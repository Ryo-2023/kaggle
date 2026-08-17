#!/usr/bin/env python3
"""Collect bounded, privacy-safe V4 action traces for selected shadow opponents.

This is a research diagnostic only.  It does not change the shadow evaluator,
promotion authority, runtime agent, checkpoint, or submission package.  The
trace projection is deliberately reused from the fixed-six evaluator so raw
observations, private fields, physical indices, and opaque payloads are never
written to the artifact.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from mage_ptcg.meta_specialist.collect_teacher_records_v1 import seed_agent_randomness_v1  # noqa: E402
from mage_ptcg.meta_specialist.opponent_pool_v1 import (  # noqa: E402
    build_opponent_agent_factory_v1,
    default_pool_root_v1,
    load_opponent_pool_v1,
    resolve_opponent_v1,
)
from scripts.measure_v4_checkpoint_strength import (  # noqa: E402
    _checkpoint_provenance,
    _public_trace_row_v1,
    _redacted_trace_row_v1,
    _v4_subject_factory,
)
from scripts.measure_v4_checkpoint_strength_shadow import _load_shadow_pool_manifest  # noqa: E402
from scripts.test_sim import run_match  # noqa: E402


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _atomic_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.tmp.", dir=path.parent)
    try:
        with open(descriptor, "w", encoding="utf-8", closefd=True) as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
        Path(temporary).replace(path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--subject-deck-csv", type=Path, required=True)
    parser.add_argument("--subject-archetype-id", required=True)
    parser.add_argument("--shadow-manifest", type=Path, required=True)
    parser.add_argument("--shadow-manifest-sha256", required=True)
    parser.add_argument("--opponent-id", action="append", required=True)
    parser.add_argument("--games-per-seat", type=int, default=4)
    parser.add_argument("--base-seed", type=int, default=10_100_000)
    parser.add_argument("--max-steps", type=int, default=2_000)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--trace-max-rows", type=int, default=50_000)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.games_per_seat <= 0 or args.base_seed < 0 or args.max_steps <= 0 or args.trace_max_rows <= 0:
        raise ValueError("games-per-seat, base-seed, max-steps, and trace-max-rows must be positive")
    if not args.checkpoint.is_file() or not args.subject_deck_csv.is_file() or not args.shadow_manifest.is_file():
        raise ValueError("checkpoint, subject deck, and shadow manifest must exist")
    provenance = _checkpoint_provenance(args.checkpoint)
    pool = load_opponent_pool_v1(default_pool_root_v1(ROOT))
    candidates = _load_shadow_pool_manifest(args.shadow_manifest, args.shadow_manifest_sha256, pool=pool)
    by_id = {str(item["opponent_id"]): item for item in candidates}
    if any(opponent_id not in by_id for opponent_id in args.opponent_id):
        missing = sorted(set(args.opponent_id) - set(by_id))
        raise ValueError(f"opponent IDs are not in the frozen shadow manifest: {missing}")

    runtime_sinks: list[object] = []
    subject_factory = _v4_subject_factory(
        checkpoint_path=args.checkpoint,
        file_sha256=provenance["file_sha256"],
        tensor_state_sha256=provenance["tensor_state_sha256"],
        subject_deck_csv=args.subject_deck_csv,
        subject_archetype_id=args.subject_archetype_id,
        trace_sinks=runtime_sinks,
    )
    trace_rows: list[dict[str, object]] = []
    outcomes: dict[str, dict[str, int]] = defaultdict(lambda: {"w": 0, "d": 0, "l": 0, "f": 0})
    fault_reasons: Counter[str] = Counter()
    trace_redacted = 0
    trace_overflow = 0
    match_root = args.output.parent / f"{args.output.stem}-matches"
    started = time.time()
    for opponent_id in args.opponent_id:
        opponent = resolve_opponent_v1(pool, opponent_id, subject_deck_csv_path="x")
        opponent_factory = build_opponent_agent_factory_v1(opponent)
        for seat in (0, 1):
            for game_index in range(args.games_per_seat):
                seed = args.base_seed + game_index
                subject_first = seat == 0
                key = f"{opponent_id}|seat={seat}"
                seed_agent_randomness_v1(seed)
                sink_start = len(runtime_sinks)
                try:
                    result = run_match(
                        deck_a_path=str(args.subject_deck_csv) if subject_first else opponent.deck_csv_path,
                        deck_b_path=opponent.deck_csv_path if subject_first else str(args.subject_deck_csv),
                        agent_a_name="a", agent_b_name="b", seed=seed, max_steps=args.max_steps,
                        output_dir=str(match_root / f"{opponent_id}-{seat}-{game_index}"),
                        save_html=False, save_result=False,
                        agent_a_factory=subject_factory if subject_first else opponent_factory,
                        agent_b_factory=opponent_factory if subject_first else subject_factory,
                    )
                    if result.get("status") != "DONE":
                        raise RuntimeError(f"run_match status={result.get('status')!r}")
                except Exception as exc:
                    outcomes[key]["f"] += 1
                    fault_reasons[f"{type(exc).__name__}: {exc}"] += 1
                    continue
                winner = result.get("winner")
                outcomes[key]["d" if winner == 2 else ("w" if winner == seat else "l")] += 1
                for decision_index, runtime in enumerate(runtime_sinks[sink_start:]):
                    for local_index, trace in enumerate(getattr(runtime, "traces", ())):
                        if len(trace_rows) >= args.trace_max_rows:
                            trace_overflow += 1
                            continue
                        payload = trace.to_payload()
                        try:
                            row = _public_trace_row_v1(
                                payload, opponent_id=opponent_id, seat=seat,
                                game_index=game_index, seed=seed, decision_index=local_index,
                            )
                        except ValueError:
                            row = _redacted_trace_row_v1(
                                payload, opponent_id=opponent_id, seat=seat,
                                game_index=game_index, seed=seed, decision_index=local_index,
                            )
                            trace_redacted += 1
                        row["outcome_key"] = key
                        trace_rows.append(row)

    trace_path = args.output.with_suffix(".jsonl")
    raw_trace = b"".join(
        (json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        for row in trace_rows
    )
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{trace_path.name}.tmp.", dir=trace_path.parent)
    try:
        with open(descriptor, "wb", closefd=True) as handle:
            handle.write(raw_trace)
            handle.flush()
        Path(temporary).replace(trace_path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise

    action_counts: Counter[str] = Counter()
    by_outcome_action: dict[str, Counter[str]] = defaultdict(Counter)
    for row in trace_rows:
        outcome_key = str(row["outcome_key"])
        for action_type in row.get("action_types", []):
            action_counts[str(action_type)] += 1
            by_outcome_action[outcome_key][str(action_type)] += 1
    payload: dict[str, Any] = {
        "schema": "meta-specialist-v4-shadow-matchup-trace-research-v1",
        "promotion_authority": False,
        "checkpoint": provenance,
        "subject_archetype_id": args.subject_archetype_id,
        "subject_deck_csv": str(args.subject_deck_csv.resolve()),
        "subject_deck_file_sha256": _sha256(args.subject_deck_csv),
        "shadow_manifest": str(args.shadow_manifest.resolve()),
        "shadow_manifest_file_sha256": _sha256(args.shadow_manifest),
        "opponent_ids": list(args.opponent_id),
        "games_per_seat": args.games_per_seat,
        "base_seed": args.base_seed,
        "max_steps": args.max_steps,
        "outcomes": {key: dict(value) for key, value in sorted(outcomes.items())},
        "fault_reasons": dict(fault_reasons),
        "trace": {
            "path": str(trace_path.resolve()),
            "sha256": hashlib.sha256(raw_trace).hexdigest(),
            "rows": len(trace_rows),
            "redacted_rows": trace_redacted,
            "overflow_rows": trace_overflow,
            "by_action_type": dict(sorted(action_counts.items())),
            "by_outcome_action": {
                key: dict(sorted(value.items())) for key, value in sorted(by_outcome_action.items())
            },
        },
        "elapsed_seconds": round(time.time() - started, 3),
    }
    _atomic_json(args.output, payload)
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
