"""Gate 4 dataset materialization with explicit policy and deck holdouts.

This exporter is deliberately separate from the Student-v1 split contract.
Gate 4 learns from one behavior policy and reserves a *different* candidate
policy's complete episodes for the teacher-policy holdout; it never relabels
or fabricates that cohort from the same policy version.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
from typing import Any, Mapping

from mage_ptcg.offline_scaleup.pipeline import (
    DATASET_SCHEMA, ContractError, _atomic_json, _digest, _population_entries_by_id,
    _read_json, _teacher_dataset_record, _valid_terminal_games, _write_jsonl_once,
)
from mage_ptcg.offline_scaleup.progress import ProgressReporter


GATE4_SPLITS = ("train", "validation", "test", "opponent_holdout", "deck_holdout", "teacher_policy_holdout")


def _assign_remainder(cells: Mapping[tuple[str, int], list[str]]) -> dict[str, str]:
    assignment: dict[str, str] = {}
    for cell, episodes in sorted(cells.items()):
        ranked = sorted(episodes, key=lambda value: _digest((cell, value), "gate4-split"))
        validation = int(len(ranked) * .1); train = int(len(ranked) * .8)
        for episode in ranked[:train]: assignment[episode] = "train"
        for episode in ranked[train:train + validation]: assignment[episode] = "validation"
        for episode in ranked[train + validation:]: assignment[episode] = "test"
    return assignment


def _records_for_run(run_dir: Path, population: Mapping[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    summary = _read_json(run_dir / "run_summary.json")
    if summary.get("gate") != "PASS":
        raise ContractError(f"Gate 4 source run must PASS: {run_dir}")
    schedule = _read_json(run_dir / "schedule.json")
    if schedule.get("population_digest") != population.get("semantic_population_digest"):
        raise ContractError(f"population snapshot does not match source run: {run_dir}")
    games = _valid_terminal_games(run_dir)
    if len(games) != summary.get("completed"):
        raise ContractError(f"source has invalid or non-terminal episodes: {run_dir}")
    records: list[dict[str, Any]] = []
    for game in games:
        for sample in game.get("teacher_samples", []):
            records.append(_teacher_dataset_record(game, sample, schedule["population_digest"]))
    if not records:
        raise ContractError(f"source has no candidate decisions: {run_dir}")
    return games, records


def export_gate4_dataset(*, run_dir: Path, teacher_holdout_run_dir: Path, population_path: Path, output: Path,
                         progress: bool | None = None, progress_interval_seconds: float | None = None) -> dict[str, Any]:
    """Write one immutable six-cohort JSONL, including a real policy holdout."""
    if output.exists():
        raise ContractError("Gate 4 dataset output already exists")
    population = _read_json(population_path); entries = _population_entries_by_id(population)
    games, records = _records_for_run(run_dir, population)
    teacher_games, teacher_records = _records_for_run(teacher_holdout_run_dir, population)
    games_by_id = {str(game["game_id"]): game for game in games}
    teacher_games_by_id = {str(game["game_id"]): game for game in teacher_games}
    primary_candidate = {str(game["candidate"]) for game in games}
    holdout_candidate = {str(game["candidate"]) for game in teacher_games}
    if len(primary_candidate) != 1 or len(holdout_candidate) != 1 or primary_candidate == holdout_candidate:
        raise ContractError("teacher-policy holdout requires a distinct single candidate policy run")
    present = {str(game["opponent"]) for game in games}
    rule_ids = sorted(opponent_id for opponent_id in present if entries.get(opponent_id, {}).get("opponent_type") == "RULE_V0_DECK")
    if len(rule_ids) < 3:
        raise ContractError("Gate 4 requires at least three Rule-v0 opponents for train/opponent/deck cohorts")
    opponent_holdout_id = min(rule_ids, key=lambda value: _digest(value, "gate4-opponent-holdout"))
    remaining_rule_ids = [value for value in rule_ids if value != opponent_holdout_id]
    deck_holdout_id = min(remaining_rule_ids, key=lambda value: _digest(entries[value]["deck_fingerprint"], "gate4-deck-holdout"))
    deck_holdout_fingerprint = str(entries[deck_holdout_id]["deck_fingerprint"])
    assignment: dict[str, str] = {}
    cells: dict[tuple[str, int], list[str]] = defaultdict(list)
    for game in games:
        episode, opponent = str(game["game_id"]), str(game["opponent"])
        if opponent == opponent_holdout_id:
            assignment[episode] = "opponent_holdout"
        elif str(entries[opponent]["deck_fingerprint"]) == deck_holdout_fingerprint:
            assignment[episode] = "deck_holdout"
        else:
            cells[(opponent, int(game["candidate_side"]))].append(episode)
    assignment.update(_assign_remainder(cells))
    if set(assignment) != {str(game["game_id"]) for game in games}:
        raise ContractError("Gate 4 episode assignment is incomplete")
    reporter = ProgressReporter(phase="gate4-export", total=len(records) + len(teacher_records), run_id=output.stem,
                                unit="record", progress=progress, interval_seconds=progress_interval_seconds)
    try:
        for record in records:
            episode = str(record["episode_id"]); opponent = games_by_id[episode]["opponent"]
            entry = entries[str(opponent)]
            record.update({"split": assignment[episode], "opponent_id": opponent, "opponent_type": entry["opponent_type"],
                           "opponent_deck_fingerprint": entry["deck_fingerprint"], "family_id": entry.get("family_id"),
                           "teacher_policy_id": next(iter(primary_candidate))})
            _write_jsonl_once(output, record); reporter.update(1)
        for record in teacher_records:
            episode = str(record["episode_id"]); opponent = teacher_games_by_id[episode]["opponent"]
            entry = entries[str(opponent)]
            record.update({"split": "teacher_policy_holdout", "opponent_id": opponent, "opponent_type": entry["opponent_type"],
                           "opponent_deck_fingerprint": entry["deck_fingerprint"], "family_id": entry.get("family_id"),
                           "teacher_policy_id": next(iter(holdout_candidate))})
            _write_jsonl_once(output, record); reporter.update(1)
    finally:
        reporter.close()
    rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines() if line.strip()]
    episode_splits: dict[str, set[str]] = defaultdict(set)
    for row in rows: episode_splits[str(row["episode_id"])].add(str(row["split"]))
    counts = Counter(str(row["split"]) for row in rows)
    episodes = Counter(str(row["split"]) for row in {f"{row['teacher_policy_id']}:{row['episode_id']}": row for row in rows}.values())
    usable = [row for row in rows if row.get("rule_bc_example", {}).get("min_count") == row.get("rule_bc_example", {}).get("max_count") == 1
              and not row.get("rule_bc_example", {}).get("fallback_used")]
    report = {"schema": "policy-learning-gate4-dataset-v1", "dataset": str(output), "records": len(rows),
              "records_by_split": dict(sorted(counts.items())), "episodes_by_split": dict(sorted(episodes.items())),
              "primary_candidate": next(iter(primary_candidate)), "teacher_policy_holdout_candidate": next(iter(holdout_candidate)),
              "opponent_holdout_id": opponent_holdout_id, "deck_holdout_fingerprint": deck_holdout_fingerprint,
              "episode_split_leakage": sum(len(value) != 1 for value in episode_splits.values()),
              "rule_proposal_coverage": sum(row.get("rule_proposal_digests") is not None for row in rows) / len(rows),
              "trainable_single_action_records": len(usable),
              "trainable_rule_proposal_coverage": (sum(row.get("rule_proposal_digests") is not None for row in usable) / len(usable)) if usable else 0.0,
              "gate": "PASS" if not any(len(value) != 1 for value in episode_splits.values()) else "BLOCKED"}
    _atomic_json(output.with_suffix(".manifest.json"), report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mage-policy-gate4-export")
    parser.add_argument("--run-dir", type=Path, required=True); parser.add_argument("--teacher-holdout-run-dir", type=Path, required=True)
    parser.add_argument("--population", type=Path, required=True); parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--progress", action="store_true"); parser.add_argument("--progress-interval-seconds", type=float, default=None)
    args = parser.parse_args(argv)
    try:
        print(json.dumps(export_gate4_dataset(run_dir=args.run_dir, teacher_holdout_run_dir=args.teacher_holdout_run_dir,
                                               population_path=args.population, output=args.output, progress=args.progress,
                                               progress_interval_seconds=args.progress_interval_seconds), ensure_ascii=False, sort_keys=True))
        return 0
    except (ContractError, OSError, ValueError) as exc:
        print(json.dumps({"error": type(exc).__name__, "message": str(exc)}, ensure_ascii=False), flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
