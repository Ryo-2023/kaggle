"""Collect self-owned Rule v0 decisions and terminal WDL sidecars.

The subject is always the repository's own Rule v0 policy.  Opponent pool
identities are used only to construct the local-evaluation schedule; they are
not written into ``RuleBCExample`` records or used as teacher labels.  The
default schedule is the 24-ID broad pool, two seats, two repetitions, and
uses twelve independent worker processes.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, asdict
import hashlib
import json
import multiprocessing as mp
from pathlib import Path
import sys
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from main import make_rule_agent, read_deck_csv  # noqa: E402
from mage_ptcg.meta_specialist.opponent_pool_v1 import (  # noqa: E402
    build_opponent_agent_factory_v1,
    default_pool_root_v1,
    load_opponent_pool_v1,
    resolve_opponent_v1,
)
from mage_ptcg.student.dataset import (  # noqa: E402
    DatasetValidationError,
    build_rule_bc_example,
    load_dataset,
    write_dataset,
)
from mage_ptcg.student.outcome_weighting_v1 import (  # noqa: E402
    EpisodeOutcomeV1,
    OutcomeWeightingError,
    build_episode_outcome_v1,
    build_example_weight_map,
)
from scripts.test_sim import run_match  # noqa: E402


SCHEMA_V1 = "student-self-owned-rule-bc-collection-v1"
DEFAULT_CONFIG = ROOT / "configs" / "meta_specialist" / "performance_first_broad_pool_v1.json"


@dataclass(frozen=True, slots=True)
class GameSpecV1:
    game_id: str
    opponent_id: str
    subject_seat: int
    repetition: int
    seed: int
    subject_deck_path: str
    output_root: str
    source_revision: str
    max_steps: int


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_opponent_ids(config_path: Path) -> tuple[str, ...]:
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("opponent_ids"), list):
        raise ValueError("broad pool config must contain opponent_ids")
    values = tuple(item for item in payload["opponent_ids"] if isinstance(item, str) and item)
    if len(values) != len(set(values)) or not values:
        raise ValueError("broad pool config opponent_ids must be unique and non-empty")
    return values


def build_game_specs_v1(
    *,
    opponent_ids: tuple[str, ...],
    games_per_seat: int,
    base_seed: int,
    subject_deck_path: Path,
    output_root: Path,
    source_revision: str,
    max_steps: int,
) -> tuple[GameSpecV1, ...]:
    if not opponent_ids:
        raise ValueError("opponent_ids must not be empty")
    if type(games_per_seat) is not int or games_per_seat <= 0:
        raise ValueError("games_per_seat must be positive")
    if type(base_seed) is not int or base_seed < 0:
        raise ValueError("base_seed must be nonnegative")
    specs: list[GameSpecV1] = []
    ordinal = 0
    for opponent_id in opponent_ids:
        for seat in (0, 1):
            for repetition in range(games_per_seat):
                specs.append(
                    GameSpecV1(
                        game_id=f"self-owned-rule-bc-{opponent_id}-seat{seat}-r{repetition:02d}",
                        opponent_id=opponent_id,
                        subject_seat=seat,
                        repetition=repetition,
                        seed=base_seed + ordinal,
                        subject_deck_path=str(subject_deck_path.resolve()),
                        output_root=str(output_root.resolve()),
                        source_revision=source_revision,
                        max_steps=max_steps,
                    )
                )
                ordinal += 1
    return tuple(specs)


def _recording_rule_factory(
    *,
    deck: list[int],
    seed: int,
    game_id: str,
    source_revision: str,
    records: list[Any],
):
    rule_agent = make_rule_agent(deck=deck, seed=seed)

    def recording_agent(observation: dict) -> list[int]:
        select = observation.get("select") if isinstance(observation, dict) else None
        if isinstance(select, dict):
            try:
                records.append(
                    build_rule_bc_example(
                        observation,
                        deck=deck,
                        source_id=game_id,
                        source_revision=source_revision,
                    )
                )
            except DatasetValidationError:
                # Unknown/ordered/zero-candidate prompts are not fabricated
                # into training data.  Rule v0 still answers the real game.
                pass
        return rule_agent(observation)

    return recording_agent


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def run_one_game_v1(spec: GameSpecV1) -> dict[str, object]:
    """Worker entry point; it is top-level so spawn can import it."""
    output_root = Path(spec.output_root)
    episode_dir = output_root / "episodes" / spec.game_id
    episode_dir.mkdir(parents=True, exist_ok=False)
    pool = load_opponent_pool_v1(default_pool_root_v1(ROOT))
    opponent = resolve_opponent_v1(pool, spec.opponent_id, subject_deck_csv_path=spec.subject_deck_path)
    subject_deck = read_deck_csv(spec.subject_deck_path)
    opponent_factory = build_opponent_agent_factory_v1(opponent)
    records: list[Any] = []

    def subject_factory(deck: list[int], seed: int):
        return _recording_rule_factory(
            deck=deck,
            seed=seed,
            game_id=spec.game_id,
            source_revision=spec.source_revision,
            records=records,
        )

    subject_first = spec.subject_seat == 0
    result = run_match(
        deck_a_path=spec.subject_deck_path if subject_first else opponent.deck_csv_path,
        deck_b_path=opponent.deck_csv_path if subject_first else spec.subject_deck_path,
        agent_a_name="self-owned-rule-v0" if subject_first else spec.opponent_id,
        agent_b_name=spec.opponent_id if subject_first else "self-owned-rule-v0",
        seed=spec.seed,
        max_steps=spec.max_steps,
        output_dir=str(episode_dir / "match"),
        save_html=False,
        save_result=False,
        agent_a_factory=subject_factory if subject_first else opponent_factory,
        agent_b_factory=opponent_factory if subject_first else subject_factory,
    )
    status = str(result.get("status"))
    winner_value = result.get("winner")
    winner = winner_value if type(winner_value) is int and winner_value in (0, 1, 2) else None
    if status == "DONE":
        episode = build_episode_outcome_v1(
            game_id=spec.game_id,
            subject_seat=spec.subject_seat,
            status=status,
            winner=winner,
            examples=records,
        )
        write_dataset(episode_dir / "rule_bc.jsonl", records)
        _write_json(episode_dir / "outcome.json", episode.to_dict())
    else:
        episode = None
        # Keep a typed empty shard so a fault cannot be mistaken for a missing
        # game.  Faults are excluded from the training join in the parent.
        (episode_dir / "rule_bc.jsonl").write_text("", encoding="utf-8")
        _write_json(episode_dir / "outcome.json", {"schema_version": SCHEMA_V1, "game_id": spec.game_id, "status": status, "winner": winner, "example_ids": [], "outcome_weight": 0.0})
    return {
        "game_id": spec.game_id,
        "opponent_id": spec.opponent_id,
        "subject_seat": spec.subject_seat,
        "repetition": spec.repetition,
        "seed": spec.seed,
        "status": status,
        "winner": winner,
        "examples": len(records),
        "elapsed_seconds": result.get("elapsed_seconds"),
        "episode": episode.to_dict() if episode is not None else None,
    }


def collect_bundle_v1(
    *,
    output_root: Path,
    subject_deck: Path,
    config_path: Path,
    games_per_seat: int = 2,
    base_seed: int = 20260814,
    workers: int = 12,
    max_steps: int = 2000,
) -> dict[str, object]:
    if output_root.exists():
        raise FileExistsError(f"output root already exists: {output_root}")
    if type(workers) is not int or workers <= 0:
        raise ValueError("workers must be positive")
    output_root.mkdir(parents=True)
    (output_root / "episodes").mkdir()
    source_revision = _sha256(Path(__file__).resolve())
    pool_manifest = ROOT / "opponents" / "pool_manifest.json"
    opponent_ids = load_opponent_ids(config_path)
    specs = build_game_specs_v1(
        opponent_ids=opponent_ids,
        games_per_seat=games_per_seat,
        base_seed=base_seed,
        subject_deck_path=subject_deck,
        output_root=output_root,
        source_revision=source_revision,
        max_steps=max_steps,
    )
    rows: list[dict[str, object]] = []
    context = mp.get_context("spawn")
    with ProcessPoolExecutor(max_workers=workers, mp_context=context) as executor:
        futures = [executor.submit(run_one_game_v1, spec) for spec in specs]
        for future in as_completed(futures):
            try:
                rows.append(dict(future.result()))
            except Exception as exc:  # preserve a row instead of losing the whole run
                rows.append({"status": "WORKER_ERROR", "error": f"{type(exc).__name__}: {exc}"})
    rows.sort(key=lambda row: str(row.get("game_id", "")))
    completed = [row for row in rows if row.get("status") == "DONE"]
    episodes: list[EpisodeOutcomeV1] = []
    all_examples = []
    for row in completed:
        episode_payload = row.get("episode")
        if not isinstance(episode_payload, dict):
            continue
        episodes.append(
            EpisodeOutcomeV1(
                game_id=str(episode_payload["game_id"]),
                subject_seat=int(episode_payload["subject_seat"]),
                status=str(episode_payload["status"]),
                winner=episode_payload.get("winner") if type(episode_payload.get("winner")) is int else None,
                example_ids=tuple(str(item) for item in episode_payload["example_ids"]),
                outcome_weight=float(episode_payload["outcome_weight"]),
            )
        )
        shard = output_root / "episodes" / str(row["game_id"]) / "rule_bc.jsonl"
        if shard.stat().st_size:
            all_examples.extend(load_dataset(shard))
    if all_examples:
        dataset_path = output_root / "rule_bc_outcome_weighted.jsonl"
        write_dataset(dataset_path, all_examples)
        weights = build_example_weight_map(episodes, all_examples)
    else:
        dataset_path = output_root / "rule_bc_outcome_weighted.jsonl"
        dataset_path.write_text("", encoding="utf-8")
        weights = {}
    _write_json(output_root / "episodes.json", [episode.to_dict() for episode in episodes])
    _write_json(output_root / "outcome_weights.json", weights)
    manifest = {
        "schema_version": SCHEMA_V1,
        "research_only": True,
        "authority": {"training": False, "behavior": False, "submission": False, "promotion": False},
        "subject_policy": "rule-v0-self-owned",
        "subject_deck_path": str(subject_deck.resolve()),
        "subject_deck_sha256": _sha256(subject_deck),
        "broad_config_path": str(config_path.resolve()),
        "broad_config_sha256": _sha256(config_path),
        "pool_manifest_path": str(pool_manifest.resolve()),
        "pool_manifest_sha256": _sha256(pool_manifest),
        "collector_source_sha256": source_revision,
        "opponent_ids": list(opponent_ids),
        "games_requested": len(specs),
        "games_completed": len(completed),
        "games_faulted": len(specs) - len(completed),
        "examples": len(all_examples),
        "weights": {"win": 1.5, "draw": 1.0, "loss": 0.5},
        "workers": workers,
        "max_steps": max_steps,
        "dataset_path": str(dataset_path.resolve()),
        "status": "READY_FOR_WEIGHTED_TRAINING" if all_examples else "BLOCKED_NO_EXAMPLES",
    }
    _write_json(output_root / "manifest.json", manifest)
    return {"manifest": manifest, "rows": rows, "dataset": str(dataset_path), "episodes": len(episodes), "examples": len(all_examples)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--subject-deck", type=Path, default=ROOT / "deck.csv")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--games-per-seat", type=int, default=2)
    parser.add_argument("--base-seed", type=int, default=20260814)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--max-steps", type=int, default=2000)
    args = parser.parse_args(argv)
    try:
        result = collect_bundle_v1(
            output_root=args.output,
            subject_deck=args.subject_deck,
            config_path=args.config,
            games_per_seat=args.games_per_seat,
            base_seed=args.base_seed,
            workers=args.workers,
            max_steps=args.max_steps,
        )
    except (OSError, ValueError, DatasetValidationError, OutcomeWeightingError) as exc:
        print(f"self-owned collection failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"status": "DONE", "manifest": result["manifest"], "episodes": result["episodes"], "examples": result["examples"]}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["GameSpecV1", "build_game_specs_v1", "collect_bundle_v1", "run_one_game_v1"]
