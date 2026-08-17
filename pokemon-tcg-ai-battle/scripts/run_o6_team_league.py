"""Run the initial O6 Team League: Rule Agent v0 + the 3 VALIDATED Native Team Agents.

Every pair (round-robin over the 4 participants) is played through the
existing resumable, side-swapped ``mage_ptcg.league.actual_runner`` engine.
Native Team Agents are only ever invoked through a hash-verified runtime
bundle extracted from the durable Population Snapshot (never through a git
checkout of the source branch), and each game gets a brand-new isolated
subprocess per native participant (no state carried between games).

O6-AUD-002 remediation: in addition to the O5-shared match-level bookkeeping
``run_actual_league`` already persists, this script maintains a *separate*
per-pair trajectory-evidence side file (never touching the shared
``mage_ptcg.league.actual_runner`` schema or its O5 regression tests) with a
timestamp/path-independent digest of every game's initial state, action
trace, and terminal state, plus a live-verified cabt seed-capability
classification.

O6-AUD-002 *final* remediation (public projection evidence + integrity
chain): this script now persists only strict allow-list public trajectory
projections per game under ``--evidence-root`` (see
``mage_ptcg.opponents.public_trajectory_evidence`` -- raw observations are
never written to disk), and after every pair has finished, independently
re-verifies every recorded digest in a *separate subprocess*
(``python -m mage_ptcg.opponents.independent_trajectory_verifier``) before
computing any final uniqueness/statistics. If any game's evidence is
missing, malformed, privacy-violating, schema-violating, or its digest does
not independently recompute, this script aborts (``SystemExit``) without
writing a "final" ``league_summary.json`` -- a shortfall must not be
silently reported as success. After the trajectory-mode pass, this script
also builds the multi-level integrity chain (``run_manifest.json``,
``run_summary.json``, ``run_root.sha256``), registers an externally-anchored
trusted root in ``docs/evidence/o6-trusted-league-roots.json``, and runs a
final full-chain independent re-verification against that anchor before
declaring success. Final uniqueness and Wilson/Bradley-Terry statistics are
computed from the *independently verified* digests, not the runtime-recorded
ones, so the reported unique-trajectory counts are grounded in something a
third party can reproduce.
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))
SRC_ROOT = REPOSITORY_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from main import make_rule_agent, read_deck_csv  # noqa: E402
from mage_ptcg.competition_intelligence.canonical import sha256_hex  # noqa: E402
from mage_ptcg.league.actual_runner import ActualLeagueConfig, run_actual_league  # noqa: E402
from mage_ptcg.opponents.core import LocalArtifactStore, OpponentError  # noqa: E402
from mage_ptcg.opponents.league_integrity_chain import build_run_manifest, compute_run_root_sha256, write_trusted_root_entry  # noqa: E402
from mage_ptcg.opponents.league_runtime import NativeAgentWorker, cleanup_native_participant, play_game, prepare_native_participant  # noqa: E402
from mage_ptcg.opponents.public_trajectory_evidence import compute_checksums_file, persist_game_evidence, write_immutable_json  # noqa: E402
from mage_ptcg.opponents.trajectory import (  # noqa: E402
    aggregate_trajectory_uniqueness, deduplicate_by_trajectory, fit_bradley_terry, load_trajectory_evidence,
    pair_win_rate_statistics, pairwise_wins_from_records, record_trajectory_evidence,
)

ACTUAL_STUDENT_NOTE = {
    "status": "NOT_CONNECTED_TO_POPULATION_FACTORY",
    "merge_blocker": False,
    "reason": ("this runner's _load_participants() only constructs rule-agent-v0 plus VALIDATED native Population specs; "
               "no actual-Student adapter/runtime participant is wired into this script, so the League establishes "
               "Team-Agent-vs-Rule-Agent comparisons only, never Student fairness or strength"),
}

LEAGUE_SUMMARY_SCHEMA_VERSION = "o6-team-league-summary-v3"
TRUSTED_ROOT_REGISTRY_PATH = REPOSITORY_ROOT / "docs/evidence/o6-trusted-league-roots.json"


def _load_participants(*, population_dir: Path, deck_path: Path) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    manifest = json.loads((population_dir / "population_manifest.json").read_text(encoding="utf-8"))
    specs = json.loads((population_dir / "opponent_specs.json").read_text(encoding="utf-8"))
    decks = {row["deck_id"]: row for row in json.loads((population_dir / "deck_registry.json").read_text(encoding="utf-8"))}
    participants: dict[str, dict[str, Any]] = {
        "rule-agent-v0": {"kind": "rule", "deck": read_deck_csv(deck_path), "label": "rule-agent-v0"}
    }
    for spec in specs:
        if spec.get("permission_status") != "VALIDATED":
            continue
        deck = decks[spec["deck_id"]]["normalized_card_multiset"]
        participants[spec["agent_id"]] = {"kind": "native", "deck": list(deck), "agent_id": spec["agent_id"],
                                           "opponent_id": spec["opponent_id"], "label": spec["agent_id"][:12]}
    return participants, manifest


def _make_callable(participant: dict[str, Any], *, deck_path: Path, scratch_root: Path, population_dir: Path, decision_timeout_seconds: float):
    if participant["kind"] == "rule":
        return make_rule_agent(deck_path=deck_path), None
    prepared = prepare_native_participant(population_dir, participant["agent_id"], scratch_root=scratch_root)
    worker = NativeAgentWorker(prepared["source_root"], prepared["entrypoint"], decision_timeout_seconds=decision_timeout_seconds)
    return worker, (worker, prepared)


def _game_dir_id(pair_id: str, match_index: int) -> str:
    return f"{pair_id}__match{match_index}"


def run_pair(*, name_a: str, name_b: str, participants: dict[str, Any], games: int, base_seed: int,
             output_dir: Path, deck_path: Path, scratch_root: Path, population_dir: Path,
             environment_version: str, decision_timeout_seconds: float, max_steps: int,
             evidence_root: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    pa, pb = participants[name_a], participants[name_b]
    pair_id = f"{name_a}__vs__{name_b}"
    config = ActualLeagueConfig(champion=name_a, challenger=name_b, games=games, base_seed=base_seed,
                                 deck_fingerprint=f"{name_a}|{name_b}", environment_version=environment_version, max_steps=max_steps)
    trajectory_path = output_dir / f"{pair_id}__trajectory.json"

    def play(schedule: dict[str, object]) -> dict[str, object]:
        champion_seat = int(schedule["champion_player_index"])
        match_index = int(schedule["match_index"])
        seat_0_participant, seat_1_participant = (name_a, name_b) if champion_seat == 0 else (name_b, name_a)
        call_champion, cleanup_champion = _make_callable(pa, deck_path=deck_path, scratch_root=scratch_root, population_dir=population_dir, decision_timeout_seconds=decision_timeout_seconds)
        call_challenger, cleanup_challenger = _make_callable(pb, deck_path=deck_path, scratch_root=scratch_root, population_dir=population_dir, decision_timeout_seconds=decision_timeout_seconds)
        try:
            deck_a, deck_b = (pa["deck"], pb["deck"]) if champion_seat == 0 else (pb["deck"], pa["deck"])
            call_a, call_b = (call_champion, call_challenger) if champion_seat == 0 else (call_challenger, call_champion)
            raw = play_game(deck_a=deck_a, deck_b=deck_b, call_a=call_a, call_b=call_b, max_steps=max_steps)
        finally:
            if cleanup_champion is not None:
                cleanup_champion[0].close(); cleanup_native_participant(cleanup_champion[1])
            if cleanup_challenger is not None:
                cleanup_challenger[0].close(); cleanup_native_participant(cleanup_challenger[1])
        winner = raw.get("winner")
        winner_agent = None
        winner_participant = None
        if raw.get("status") == "DONE":
            winner_agent = "draw" if winner == 2 else ("champion" if winner == champion_seat else "challenger")
            winner_participant = "draw" if winner == 2 else (seat_0_participant if winner == 0 else seat_1_participant if winner == 1 else None)
        status = str(raw.get("status"))
        trajectory = raw.get("trajectory") or {}
        evidence_record = {
            "schema_version": "o6-league-trajectory-evidence-v1",
            "game_id": f"{pair_id}#{match_index}", "pair_id": pair_id, "match_index": match_index,
            "participant_a": name_a, "participant_b": name_b,
            "seat_0_participant": seat_0_participant, "seat_1_participant": seat_1_participant,
            "requested_seed": schedule.get("seed"), "engine_seed_support_status": raw.get("engine_seed_support"),
            "winner_participant": winner_participant, "status": status,
            "invalid_action": status == "AGENT_INVALID", "crash": status in {"ERROR", "AGENT_ERROR"}, "timeout": status in {"AGENT_TIMEOUT", "STEP_LIMIT"},
            "fallback_count": 0, "latency_seconds": raw.get("elapsed_seconds"),
            "initial_observation_digest": trajectory.get("initial_observation_digest"),
            "action_trace_digest": trajectory.get("action_trace_digest"),
            "terminal_observation_digest": trajectory.get("terminal_observation_digest"),
            "complete_trajectory_digest": trajectory.get("complete_trajectory_digest"),
            "game_length": trajectory.get("game_length"), "raw_action_count": trajectory.get("raw_action_count"),
        }
        record_trajectory_evidence(trajectory_path, match_index, evidence_record)

        canonical_steps = raw.get("canonical_steps")
        if canonical_steps:
            game_metadata = {
                "schema_version": "o6-raw-game-metadata-v1",
                "game_id": evidence_record["game_id"], "pair_id": pair_id,
                "participant_a": name_a, "participant_b": name_b,
                "seat_0_participant": seat_0_participant, "seat_1_participant": seat_1_participant,
                "execution_index": match_index, "requested_seed": schedule.get("seed"),
                "engine_seed_capability": raw.get("engine_seed_support"),
                "runtime_duration_seconds": raw.get("elapsed_seconds"), "latency_seconds": raw.get("elapsed_seconds"),
                "fault": status != "DONE", "timeout": status in {"AGENT_TIMEOUT", "STEP_LIMIT"},
                "crash": status in {"ERROR", "AGENT_ERROR"}, "fallback_usage": 0,
                "winner": winner, "winner_participant": winner_participant, "status": status,
            }
            persist_game_evidence(
                evidence_root, _game_dir_id(pair_id, match_index), canonical_steps=canonical_steps,
                runtime_digests={key: trajectory.get(key) for key in (
                    "initial_observation_digest", "action_trace_digest", "terminal_observation_digest", "complete_trajectory_digest")},
                metadata=game_metadata,
            )
        return {"status": raw.get("status"), "winner_agent": winner_agent, "elapsed_seconds": raw.get("elapsed_seconds"),
                "fallback_count": 0, "champion_status": raw.get("agent_status", [None, None])[champion_seat] if raw.get("agent_status") else "NOT_OBSERVABLE",
                "challenger_status": raw.get("agent_status", [None, None])[1 - champion_seat] if raw.get("agent_status") else "NOT_OBSERVABLE"}

    output_path = output_dir / f"{pair_id}.json"
    result = run_actual_league(config, output_path=output_path, run_match=play)
    stored_evidence = load_trajectory_evidence(trajectory_path)
    trajectory_records = [stored_evidence[str(i)] for i in range(games) if str(i) in stored_evidence]
    return result, trajectory_records


def _run_verifier_subprocess(evidence_root: Path, *, mode: str, extra_args: list[str] | None = None) -> dict[str, Any]:
    """Invoke the independent verifier in its own subprocess (never imported into this process)."""
    completed = subprocess.run(
        [sys.executable, "-m", "mage_ptcg.opponents.independent_trajectory_verifier", "--evidence", str(evidence_root),
         "--json", "--mode", mode, *(extra_args or [])],
        capture_output=True, text=True, cwd=REPOSITORY_ROOT,
        env={**os.environ, "PYTHONPATH": str(SRC_ROOT), "PYTHONDONTWRITEBYTECODE": "1"},
    )
    if completed.returncode not in (0, 1):
        raise SystemExit(f"independent verifier crashed: {completed.stderr[-2000:]}")
    return json.loads(completed.stdout)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-store", required=True)
    parser.add_argument("--population", required=True)
    parser.add_argument("--cache-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--evidence-root", type=Path, default=REPOSITORY_ROOT / "docs/evidence/o6-opponent-intelligence-v4",
                         help="public trajectory projection evidence root (O6-AUD-002 final remediation); "
                              "games-per-pair/base-seed/output-dir may point inside it (a 'league/' subdir), "
                              "but this is the root the integrity chain covers")
    parser.add_argument("--games-per-pair", type=int, default=10)
    parser.add_argument("--base-seed", type=int, default=71000)
    parser.add_argument("--decision-timeout-seconds", type=float, default=8.0)
    parser.add_argument("--max-steps", type=int, default=10_000)
    parser.add_argument("--min-effective-independent-sample-size", type=int, default=5)
    parser.add_argument("--deck", type=Path, default=REPOSITORY_ROOT / "deck.csv")
    args = parser.parse_args(argv)

    cache_dir = Path(args.cache_dir); output_dir = Path(args.output_dir); output_dir.mkdir(parents=True, exist_ok=True)
    evidence_root = Path(args.evidence_root); evidence_root.mkdir(parents=True, exist_ok=True)
    store = LocalArtifactStore(args.artifact_store)
    population_dir = store.fetch_to_cache(args.population, cache_dir, verify_hashes=True)
    participants, manifest = _load_participants(population_dir=population_dir, deck_path=args.deck)
    scratch_root = output_dir / "league-scratch"; scratch_root.mkdir(parents=True, exist_ok=True)

    names = sorted(participants)
    pairs = list(itertools.combinations(names, 2))
    environment_version = f"cabt/{manifest.get('cabt_version', 'unknown')}"

    pair_results: dict[str, Any] = {}
    pair_participants: dict[str, tuple[str, str]] = {}
    all_trajectory_records: list[dict[str, Any]] = []
    for index, (a, b) in enumerate(pairs):
        print(f"[league] pair {index + 1}/{len(pairs)}: {a} vs {b}", file=sys.stderr, flush=True)
        result, trajectory_records = run_pair(name_a=a, name_b=b, participants=participants, games=args.games_per_pair,
                                               base_seed=args.base_seed + index * 1000, output_dir=output_dir, deck_path=args.deck,
                                               scratch_root=scratch_root, population_dir=population_dir, environment_version=environment_version,
                                               decision_timeout_seconds=args.decision_timeout_seconds, max_steps=args.max_steps,
                                               evidence_root=evidence_root)
        pair_id = f"{a}__vs__{b}"
        pair_results[pair_id] = result
        pair_participants[pair_id] = (a, b)
        all_trajectory_records.extend(trajectory_records)
        print(f"[league] {a} vs {b}: raw executions={len(trajectory_records)} "
              f"(invalid={result['invalid_actions']} crash={result['crashes']} timeout={result['timeouts']})", file=sys.stderr, flush=True)

    print("[league] all pairs finished; independently re-verifying public trajectory evidence...", file=sys.stderr, flush=True)
    verify_result = _run_verifier_subprocess(evidence_root, mode="trajectory")
    if (verify_result["digest_mismatches"] or verify_result["malformed_trajectories"] or verify_result["privacy_violations"]
            or verify_result.get("schema_violations", 0)):
        raise SystemExit(
            f"independent verification failed, refusing to write a final league summary: "
            f"digest_mismatches={verify_result['digest_mismatches']} "
            f"malformed_trajectories={verify_result['malformed_trajectories']} "
            f"privacy_violations={verify_result['privacy_violations']} "
            f"schema_violations={verify_result.get('schema_violations', 0)}"
        )
    if verify_result["game_count"] != len(all_trajectory_records):
        raise SystemExit(
            f"public evidence game_count ({verify_result['game_count']}) does not match recorded trajectory "
            f"records ({len(all_trajectory_records)}); refusing to write a final league summary"
        )

    per_game = verify_result["per_game"]
    verified_records: list[dict[str, Any]] = []
    game_manifest_hashes: dict[str, str] = {}
    for record in all_trajectory_records:
        game_dir_id = _game_dir_id(record["pair_id"], record["match_index"])
        game_result = per_game.get(game_dir_id)
        if game_result is None:
            raise SystemExit(f"no independent verification result for {game_dir_id}; refusing to write a final league summary")
        verified = dict(record)
        verified.update(game_result["independent_digests"])
        verified_records.append(verified)
        game_dir = evidence_root / "games" / game_dir_id
        independent_digest_text = json.dumps(game_result["independent_digests"], sort_keys=True) + "\n"
        (game_dir / "independent_digest.txt").write_text(independent_digest_text, encoding="utf-8")
        hashes = json.loads((game_dir / "hashes.json").read_text(encoding="utf-8"))
        hashes["files"]["independent_digest.txt"] = sha256_hex((game_dir / "independent_digest.txt").read_bytes())
        (game_dir / "hashes.json").write_text(json.dumps(hashes, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        game_manifest_hashes[game_dir_id] = sha256_hex((game_dir / "trajectory_manifest.json").read_bytes())

    pair_statistics: dict[str, Any] = {}
    for pair_id, (a, b) in pair_participants.items():
        records_for_pair = [r for r in verified_records if r["pair_id"] == pair_id]
        pair_statistics[pair_id] = {
            **pair_win_rate_statistics(records_for_pair, side_a=a, side_b=b, min_effective_n=args.min_effective_independent_sample_size),
            "uniqueness_by_seat_direction": {
                ("seat_0=" + str(bucket_key[1])): value
                for bucket_key, value in aggregate_trajectory_uniqueness(records_for_pair, bucket_key=lambda r: (r["pair_id"], r["seat_0_participant"])).items()
            },
        }
        stats = pair_statistics[pair_id]
        print(f"[league] {a} vs {b}: unique_trajectory_wins={stats['unique_trajectory_wins']} "
              f"effective_n={stats['effective_independent_sample_size']}", file=sys.stderr, flush=True)

    engine_seed_support_values = sorted({record.get("engine_seed_support_status") for record in verified_records if record.get("engine_seed_support_status")})
    engine_seed_support_status = engine_seed_support_values[0] if len(engine_seed_support_values) == 1 else engine_seed_support_values
    raw_bt = fit_bradley_terry(pairwise_wins_from_records(verified_records), participants=names)
    dedup_records = deduplicate_by_trajectory(verified_records)
    dedup_bt = fit_bradley_terry(pairwise_wins_from_records(dedup_records), participants=names)

    league_run_id = f"o6-team-league-{(manifest.get('population_identity_hash') or 'unknown')[:12]}-public-v2"

    summary = {
        "schema_version": LEAGUE_SUMMARY_SCHEMA_VERSION, "league_run_id": league_run_id,
        "population_id": args.population, "population_identity_hash": manifest.get("population_identity_hash"),
        "participants": sorted(participants), "pairs": pair_results,
        "digest_basis": "independently_verified",
        "trajectory_statistics": {
            "schema_version": "o6-team-league-trajectory-statistics-v2",
            "engine_seed_support_status": engine_seed_support_status,
            "raw_executions_total": len(verified_records),
            "unique_complete_trajectories_total": len({r["complete_trajectory_digest"] for r in verified_records if r.get("complete_trajectory_digest")}),
            "effective_independent_sample_size_total": len(dedup_records),
            "min_effective_independent_sample_size_threshold": args.min_effective_independent_sample_size,
            "per_pair": pair_statistics,
            "bradley_terry_raw_execution": raw_bt,
            "bradley_terry_deduplicated_trajectory": dedup_bt,
            "actual_student": ACTUAL_STUDENT_NOTE,
        },
        "independent_verification": {key: value for key, value in verify_result.items() if key != "per_game"},
    }
    (output_dir / "league_summary.json").write_text(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    run_summary_bytes = (json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    (evidence_root / "run_summary.json").write_bytes(run_summary_bytes)

    team_bundle_hashes = {pid: p.get("opponent_id", "unknown") for pid, p in participants.items() if p["kind"] == "native"}
    run_manifest = build_run_manifest(
        run_id=league_run_id, sorted_game_ids=sorted(game_manifest_hashes), game_manifest_hashes=game_manifest_hashes,
        summary_hash=sha256_hex(run_summary_bytes), participant_ids=sorted(participants), population_id=args.population,
        team_bundle_hashes=team_bundle_hashes, ruleset_version=manifest.get("ruleset_version", "unknown"),
        cabt_version=manifest.get("cabt_version", "unknown"), evidence_format_version="o6-evidence-format-v1",
    )
    write_immutable_json(evidence_root / "run_manifest.json", run_manifest)

    compute_checksums_file(evidence_root, evidence_root / "checksums.sha256")
    run_root_sha256 = compute_run_root_sha256(evidence_root, exclude={"run_root.sha256"})
    (evidence_root / "run_root.sha256").write_text(run_root_sha256 + "\n", encoding="utf-8")

    source_commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPOSITORY_ROOT, capture_output=True, text=True).stdout.strip()
    write_trusted_root_entry(
        TRUSTED_ROOT_REGISTRY_PATH, run_id=league_run_id, run_root_sha256=run_root_sha256, source_commit=source_commit,
        population_id=args.population, evidence_schema="o6-public-trajectory-v1",
    )

    print("[league] final full-chain independent verification...", file=sys.stderr, flush=True)
    final_check = _run_verifier_subprocess(evidence_root, mode="full", extra_args=["--trusted-root-registry", str(TRUSTED_ROOT_REGISTRY_PATH)])
    if final_check["status"] != "PASS":
        raise SystemExit(f"final full-chain verification failed: {final_check}")

    print(json.dumps({"pairs_played": len(pairs), "output_dir": str(output_dir), "evidence_root": str(evidence_root),
                       "league_run_id": league_run_id, "run_root_sha256": run_root_sha256,
                       "independently_verified_count": verify_result["independently_verified_count"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
