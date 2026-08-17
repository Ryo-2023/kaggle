#!/usr/bin/env python3
"""Measure one V2 checkpoint against the immutable six-opponent held-out pool.

This runner is deliberately separate from ``measure_opponent_strength.py``.
It records the complete fixed evaluation protocol needed for an apples-to-
apples comparison with ``measure_v4_checkpoint_strength.py``: held-out order,
both seats, game seed schedule, max steps, requested-game denominator, and
fault invalidation.  The V2 subject is loaded through the production
actor-pool strict checkpoint loader rather than a payload-only convenience
path.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(_ROOT), str(_ROOT / "src")]

from mage_ptcg.meta_specialist.actor_pool_v1 import (  # noqa: E402
    ActorJobConfigV1,
    _build_actor_pool_deck_binding_v1,
    _build_neural_agent_policy_factory_v1,
)
from mage_ptcg.meta_specialist.collect_teacher_records_v1 import seed_agent_randomness_v1  # noqa: E402
from mage_ptcg.meta_specialist.heldout_protocol_v1 import heldout_protocol_sha256_v1  # noqa: E402
from mage_ptcg.meta_specialist.opponent_pool_v1 import (  # noqa: E402
    build_opponent_agent_factory_v1,
    default_pool_root_v1,
    load_opponent_pool_v1,
    resolve_opponent_v1,
)
from mage_ptcg.meta_specialist.progress_v1 import ProgressReporterV1  # noqa: E402
from mage_ptcg.meta_specialist.runtime import RuntimeConstraintManifest, make_agent  # noqa: E402
from scripts.make_medal_opponents import EVAL_HELD_OUT_V1  # noqa: E402
from scripts.measure_opponent_strength import _wilson  # noqa: E402
from scripts.test_sim import run_match  # noqa: E402


V2_FIXED_HELDOUT_CHECKPOINT_STRENGTH_SCHEMA_V1 = "meta-specialist-v2-fixed-heldout-checkpoint-strength-v1"


def _checkpoint_provenance(checkpoint: Path) -> dict[str, str]:
    """Hash the exact V2 checkpoint bytes before strict actor-pool loading."""
    if not checkpoint.is_file():
        raise ValueError(f"checkpoint does not exist or is not a regular file: {checkpoint}")
    return {
        "path": str(checkpoint.resolve()),
        "file_sha256": hashlib.sha256(checkpoint.read_bytes()).hexdigest(),
    }


def _v2_subject_factory(
    *, checkpoint_path: Path, file_sha256: str,
    subject_deck_csv: Path, subject_archetype_id: str,
):
    """Bind V2 via the strict actor-pool loader and the real runtime path."""
    qualified, deck_lock, vocabulary = _build_actor_pool_deck_binding_v1(
        archetype_id=subject_archetype_id,
        deck_csv_path=subject_deck_csv,
        source_commit="0" * 40,
    )
    job = ActorJobConfigV1(
        job_id=f"v2-fixed-heldout-{file_sha256[:16]}",
        archetype_id=subject_archetype_id,
        deck_csv_path=str(subject_deck_csv),
        source_commit="0" * 40,
        env_seed=0,
        seat=0,
        behavior_kind="neural_specialist",
        behavior_identity=file_sha256,
        neural_checkpoint_path=str(checkpoint_path),
        opponent_kind="held_out_evaluation",
    )
    policy_factory, identity = _build_neural_agent_policy_factory_v1(job, deck_lock=deck_lock)
    if identity != file_sha256:
        raise ValueError("V2 actor-pool factory returned a different checkpoint identity")
    constraints = RuntimeConstraintManifest.frozen_v1()

    def factory(_deck: object, _seed: int):
        return make_agent(
            deck_asset=qualified,
            deck_lock=deck_lock,
            vocabulary=vocabulary,
            policy_factory=policy_factory,
            expected_policy_identity=file_sha256,
            constraints=constraints,
        ).agent

    return factory


def _new_row() -> dict[str, int]:
    return {"w": 0, "d": 0, "l": 0, "f": 0, "requested": 0}


def _score(row: dict[str, int]) -> float | None:
    requested = row["requested"]
    return (row["w"] + 0.5 * row["d"]) / requested if requested else None


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--subject-deck-csv", type=Path, required=True)
    parser.add_argument("--subject-archetype-id", required=True)
    parser.add_argument("--games-per-seat", type=int, default=4)
    parser.add_argument(
        "--opponent-count", type=int, default=len(EVAL_HELD_OUT_V1),
        help="固定 held-out pool の先頭から使う相手数（1–6）。任意の ID 指定は許可しない。",
    )
    parser.add_argument("--base-seed", type=int, default=9_100_000)
    parser.add_argument("--max-steps", type=int, default=2_000)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def _validate_args(args: argparse.Namespace) -> None:
    if args.games_per_seat <= 0:
        raise ValueError("--games-per-seat must be positive")
    if not 1 <= args.opponent_count <= len(EVAL_HELD_OUT_V1):
        raise ValueError(f"--opponent-count must be between 1 and {len(EVAL_HELD_OUT_V1)}")
    if args.base_seed < 0:
        raise ValueError("--base-seed must be nonnegative")
    if args.max_steps <= 0:
        raise ValueError("--max-steps must be positive")
    if not args.subject_deck_csv.is_file():
        raise ValueError(f"--subject-deck-csv does not exist or is not a regular file: {args.subject_deck_csv}")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    _validate_args(args)
    provenance = _checkpoint_provenance(args.checkpoint)
    subject_factory = _v2_subject_factory(
        checkpoint_path=args.checkpoint,
        file_sha256=provenance["file_sha256"],
        subject_deck_csv=args.subject_deck_csv,
        subject_archetype_id=args.subject_archetype_id,
    )
    subject_deck_path = str(args.subject_deck_csv)
    pool = load_opponent_pool_v1(default_pool_root_v1(_ROOT))
    opponent_ids = EVAL_HELD_OUT_V1[:args.opponent_count]
    opponent_fingerprints = []
    for opponent_id in opponent_ids:
        opponent = resolve_opponent_v1(pool, opponent_id, subject_deck_csv_path="x")
        opponent_fingerprints.append({
            "opponent_id": opponent_id,
            "canonical_deck_hash": opponent.canonical_deck_hash,
            "deck_file_sha256": hashlib.sha256(Path(opponent.deck_csv_path).read_bytes()).hexdigest(),
            "policy_hash": opponent.policy_hash,
        })
    requested_games = len(opponent_ids) * 2 * args.games_per_seat
    reporter = ProgressReporterV1(total=requested_games, desc=f"v2-heldout {provenance['file_sha256'][:12]}")
    reporter.note(
        f"[v2-heldout] checkpoint={provenance['file_sha256'][:12]} opponents={len(opponent_ids)} "
        f"games={requested_games}"
    )
    overall = _new_row()
    per_seat = {seat: _new_row() for seat in (0, 1)}
    per_opponent = {opponent_id: _new_row() for opponent_id in opponent_ids}
    fault_reasons: dict[str, int] = {}
    started = time.time()
    output_root = Path("runs/meta-specialist-strength") / f"v2-heldout-{provenance['file_sha256'][:12]}"

    for opponent_id in opponent_ids:
        opponent = resolve_opponent_v1(pool, opponent_id, subject_deck_csv_path="x")
        opponent_factory = build_opponent_agent_factory_v1(opponent)
        for seat in (0, 1):
            for game_index in range(args.games_per_seat):
                seed = args.base_seed + game_index
                first = seat == 0
                for row in (overall, per_seat[seat], per_opponent[opponent_id]):
                    row["requested"] += 1
                seed_agent_randomness_v1(seed)
                try:
                    result = run_match(
                        deck_a_path=subject_deck_path if first else opponent.deck_csv_path,
                        deck_b_path=opponent.deck_csv_path if first else subject_deck_path,
                        agent_a_name="a", agent_b_name="b",
                        seed=seed,
                        max_steps=args.max_steps,
                        output_dir=str(output_root / f"{opponent_id}-{seat}-{game_index}"),
                        save_html=False,
                        save_result=False,
                        agent_a_factory=subject_factory if first else opponent_factory,
                        agent_b_factory=opponent_factory if first else subject_factory,
                    )
                    if result.get("status") != "DONE":
                        raise RuntimeError(f"run_match status={result.get('status')!r}")
                except Exception as exc:
                    reason = f"{type(exc).__name__}: {exc}"
                    fault_reasons[reason] = fault_reasons.get(reason, 0) + 1
                    for row in (overall, per_seat[seat], per_opponent[opponent_id]):
                        row["f"] += 1
                    reporter.update(1, faults=overall["f"], rate=_score(overall) or 0.0)
                    continue
                winner = result.get("winner")
                if winner == 2:
                    key = "d"
                elif winner == seat:
                    key = "w"
                else:
                    key = "l"
                for row in (overall, per_seat[seat], per_opponent[opponent_id]):
                    row[key] += 1
                reporter.update(
                    1, win=overall["w"], loss=overall["l"], draw=overall["d"],
                    faults=overall["f"], rate=_score(overall) or 0.0,
                )
    reporter.close()

    score = _score(overall)
    payload: dict[str, Any] = {
        "schema_version": V2_FIXED_HELDOUT_CHECKPOINT_STRENGTH_SCHEMA_V1,
        "checkpoint": provenance,
        "subject_archetype_id": args.subject_archetype_id,
        "subject_deck_csv": str(args.subject_deck_csv.resolve()),
        "subject_deck_file_sha256": hashlib.sha256(args.subject_deck_csv.read_bytes()).hexdigest(),
        "fixed_held_out_opponent_ids": list(EVAL_HELD_OUT_V1),
        "opponent_ids": list(opponent_ids),
        "opponent_fingerprints": opponent_fingerprints,
        "evaluation_protocol_sha256": heldout_protocol_sha256_v1(),
        "games_per_seat": args.games_per_seat,
        "base_seed": args.base_seed,
        "max_steps": args.max_steps,
        "requested_games": overall["requested"],
        "games_played": overall["w"] + overall["d"] + overall["l"],
        "faults": overall["f"],
        "fault_reasons": dict(sorted(fault_reasons.items(), key=lambda item: (-item[1], item[0]))),
        "wins": overall["w"],
        "draws": overall["d"],
        "losses": overall["l"],
        "score_rate": score,
        "score_denominator_games": overall["requested"],
        "score_ci95": list(_wilson(overall["w"] + 0.5 * overall["d"], overall["requested"])),
        "comparison_status": "invalid_faults" if overall["f"] else "valid",
        "seat": {str(seat): {**per_seat[seat], "score_rate": _score(per_seat[seat])} for seat in (0, 1)},
        "per_opponent": {opponent_id: {**row, "score_rate": _score(row)} for opponent_id, row in per_opponent.items()},
        "elapsed_seconds": round(time.time() - started, 1),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in payload.items() if key != "per_opponent"}, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
