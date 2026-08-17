#!/usr/bin/env python3
"""Measure one closed V4 checkpoint against the immutable six-opponent held-out pool.

This is intentionally separate from ``measure_opponent_strength.py``: V4
requires both a file and tensor-state digest and uses the actor-pool V4 runtime
binding.  A fault remains in the requested-game score denominator (as a zero
score) and makes the result invalid for comparison, so a partial run cannot be
mistaken for a clean strength measurement.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(_ROOT), str(_ROOT / "src")]

from mage_ptcg.meta_specialist.actor_pool_v1 import (  # noqa: E402
    ActorJobConfigV1,
    _build_actor_pool_deck_binding_v1,
    _build_neural_agent_policy_factory_v4,
)
from mage_ptcg.meta_specialist.collect_teacher_records_v1 import seed_agent_randomness_v1  # noqa: E402
from mage_ptcg.meta_specialist.opponent_pool_v1 import (  # noqa: E402
    build_opponent_agent_factory_v1,
    default_pool_root_v1,
    load_opponent_pool_v1,
    resolve_opponent_v1,
)
from mage_ptcg.meta_specialist.progress_v1 import ProgressReporterV1  # noqa: E402
from mage_ptcg.meta_specialist.runtime import RuntimeConstraintManifest, make_agent  # noqa: E402
from mage_ptcg.meta_specialist.heldout_protocol_v1 import heldout_protocol_sha256_v1  # noqa: E402
from scripts.make_medal_opponents import EVAL_HELD_OUT_V1  # noqa: E402
from scripts.measure_opponent_strength import _wilson  # noqa: E402
from scripts.test_sim import run_match  # noqa: E402


V4_HELDOUT_CHECKPOINT_STRENGTH_SCHEMA_V1 = "meta-specialist-v4-heldout-checkpoint-strength-v1"


def evaluation_implementation_sha256_v1() -> str:
    digest = hashlib.sha256(b"meta-specialist-v4-heldout-evaluator-v1\0")
    paths = (
        Path(__file__).resolve(),
        _ROOT / "src/mage_ptcg/meta_specialist/opponent_pool_v1.py",
        _ROOT / "src/mage_ptcg/meta_specialist/neural_policy_v4.py",
        _ROOT / "src/mage_ptcg/meta_specialist/actor_pool_v1.py",
        _ROOT / "src/mage_ptcg/meta_specialist/runtime.py",
        _ROOT / "src/mage_ptcg/meta_specialist/runtime_actions_v2.py",
        _ROOT / "src/mage_ptcg/meta_specialist/actions.py",
        _ROOT / "src/mage_ptcg/meta_specialist/actor_visible_features_v1.py",
        _ROOT / "src/mage_ptcg/meta_specialist/actor_visible_v2.py",
        _ROOT / "src/mage_ptcg/meta_specialist/decks.py",
        _ROOT / "src/mage_ptcg/meta_specialist/card_vocabulary_registry_v1.py",
        _ROOT / "src/mage_ptcg/meta_specialist/collect_teacher_records_v1.py",
        _ROOT / "scripts/test_sim.py",
        _ROOT / "scripts/make_medal_opponents.py",
    )
    for path in paths:
        raw = path.read_bytes()
        digest.update(path.name.encode("utf-8") + b"\0" + len(raw).to_bytes(8, "big") + raw)
    return digest.hexdigest()


def _require_hex64(value: object, name: str) -> str:
    if type(value) is not str or len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ValueError(f"{name} must be a 64-character lowercase hex SHA-256 string")
    return value


def _checkpoint_provenance(checkpoint: Path) -> dict[str, str]:
    """Read V4's tensor digest from a file-hash-bound, tensors-only payload."""
    if not checkpoint.is_file():
        raise ValueError(f"checkpoint does not exist or is not a regular file: {checkpoint}")
    raw = checkpoint.read_bytes()
    file_sha256 = hashlib.sha256(raw).hexdigest()
    try:
        import torch

        payload = torch.load(__import__("io").BytesIO(raw), map_location="cpu", weights_only=True)
        tensor_state_sha256 = payload["descriptor"]["tensor_state_sha256"]
    except (ImportError, KeyError, OSError, RuntimeError, TypeError, ValueError, EOFError) as exc:
        raise ValueError("checkpoint has no readable closed V4 tensor-state descriptor") from exc
    return {
        "path": str(checkpoint.resolve()),
        "file_sha256": file_sha256,
        "tensor_state_sha256": _require_hex64(tensor_state_sha256, "tensor_state_sha256"),
    }


def _v4_subject_factory(
    *, checkpoint_path: Path, file_sha256: str, tensor_state_sha256: str,
    subject_deck_csv: Path, subject_archetype_id: str,
    trace_sinks: list[object] | None = None,
):
    """Bind V4 through actor-pool's strict factory and the real runtime.make_agent path."""
    qualified, deck_lock, vocabulary = _build_actor_pool_deck_binding_v1(
        archetype_id=subject_archetype_id,
        deck_csv_path=subject_deck_csv,
        source_commit="0" * 40,
    )
    job = ActorJobConfigV1(
        job_id=f"v4-heldout-{file_sha256[:16]}",
        archetype_id=subject_archetype_id,
        deck_csv_path=str(subject_deck_csv),
        source_commit="0" * 40,
        env_seed=0,
        seat=0,
        behavior_kind="neural_specialist_v4",
        behavior_identity=file_sha256,
        neural_checkpoint_path=str(checkpoint_path),
        neural_checkpoint_file_sha256=file_sha256,
        neural_checkpoint_tensor_state_sha256=tensor_state_sha256,
        opponent_kind="held_out_evaluation",
    )
    policy_factory, identity = _build_neural_agent_policy_factory_v4(
        job, checkpoint_lineage_id=deck_lock.policy_lineage_id,
    )
    if identity != file_sha256:
        raise ValueError("V4 actor-pool factory returned a different checkpoint identity")
    constraints = RuntimeConstraintManifest.frozen_v1()

    def factory(_deck: object, _seed: int):
        binding = make_agent(
            deck_asset=qualified,
            deck_lock=deck_lock,
            vocabulary=vocabulary,
            policy_factory=policy_factory,
            expected_policy_identity=file_sha256,
            constraints=constraints,
        )
        if trace_sinks is not None:
            trace_sinks.append(binding.agent)
        return binding.agent

    return factory


def _new_row() -> dict[str, int]:
    return {"w": 0, "d": 0, "l": 0, "f": 0, "requested": 0}


def _score(row: dict[str, int]) -> float | None:
    requested = row["requested"]
    return (row["w"] + 0.5 * row["d"]) / requested if requested else None


_TRACE_PRIVATE_KEY_FRAGMENTS_V1 = frozenset({
    "observation", "raw_observation", "private", "private_state", "hand", "prize",
    "deck", "serial", "local_action", "stable_key", "option_index", "actor_payload",
})


def _reject_private_trace_tree_v1(value: object, *, depth: int = 0) -> None:
    """Reject private/opaque fields before an opt-in trace is persisted."""
    if depth > 8:
        raise ValueError("runtime trace projection is too deeply nested")
    if isinstance(value, dict):
        if len(value) > 64:
            raise ValueError("runtime trace projection mapping is oversized")
        for key, child in value.items():
            if type(key) is not str:
                raise ValueError("runtime trace projection keys must be strings")
            normalized = re.sub(r"[^a-z0-9]+", "_", key.lower()).strip("_")
            if normalized in _TRACE_PRIVATE_KEY_FRAGMENTS_V1 or any(
                fragment in normalized for fragment in ("private", "secret", "hidden")
            ):
                raise ValueError("runtime trace projection contains a private field")
            _reject_private_trace_tree_v1(child, depth=depth + 1)
    elif isinstance(value, list):
        if len(value) > 64:
            raise ValueError("runtime trace projection list is oversized")
        for child in value:
            _reject_private_trace_tree_v1(child, depth=depth + 1)
    elif value is None or type(value) in (bool, int, float, str):
        return
    else:
        raise ValueError("runtime trace projection contains a non-JSON value")


def _public_trace_row_v1(
    payload: dict[str, object], *, opponent_id: str, seat: int, game_index: int,
    seed: int, decision_index: int,
) -> dict[str, object]:
    """Reduce one runtime trace to bounded action/seat diagnostics.

    Public action semantics are retained; state/action digests, candidate
    identities, and all raw projection trees are intentionally discarded.
    """
    if type(payload) is not dict:
        raise ValueError("runtime trace payload must be an object")
    variant = payload.get("trace_variant")
    if variant != "public-v1-representable":
        if variant not in {"duplicate-public-identity", "public-v1-option-limit-exceeded"}:
            raise ValueError("runtime trace variant is unknown")
        log_probability = payload.get("complete_action_log_probability")
        if type(log_probability) not in (int, float) or isinstance(log_probability, bool):
            raise ValueError("runtime trace log probability is invalid")
        return {
            "opponent_id": opponent_id,
            "seat": seat,
            "game_index": game_index,
            "seed": seed,
            "decision_index": decision_index,
            "selection_type": payload.get("selection_type"),
            "selection_context": payload.get("selection_context"),
            "min_count": payload.get("min_count"),
            "max_count": payload.get("max_count"),
            "order_semantics": payload.get("order_semantics"),
            "selected_count": payload.get("selected_count"),
            "action_types": [],
            "complete_action_log_probability": float(log_probability),
            "trace_variant": variant,
        }
    projection = payload.get("public_projection")
    if type(projection) is not dict:
        raise ValueError("representable runtime trace lacks a public projection")
    _reject_private_trace_tree_v1(projection)
    selected = projection.get("selected_public_actions")
    if type(selected) is not list or not selected or len(selected) > 60:
        raise ValueError("runtime trace selected action list is invalid")
    action_types: list[str] = []
    for action in selected:
        if type(action) is not dict:
            raise ValueError("runtime trace selected action is not an object")
        operation = action.get("semantic_operation")
        if type(operation) is not str or not operation or len(operation) > 64:
            raise ValueError("runtime trace selected action has no bounded semantic operation")
        action_types.append(operation)
    log_probability = payload.get("complete_action_log_probability")
    if type(log_probability) not in (int, float) or isinstance(log_probability, bool):
        raise ValueError("runtime trace log probability is invalid")
    return {
        "opponent_id": opponent_id,
        "seat": seat,
        "game_index": game_index,
        "seed": seed,
        "decision_index": decision_index,
        "selection_type": projection.get("selection_type"),
        "selection_context": projection.get("selection_context"),
        "min_count": projection.get("min_count"),
        "max_count": projection.get("max_count"),
        "order_semantics": projection.get("order_semantics"),
        "selected_count": projection.get("selected_count"),
        "action_types": action_types,
        "complete_action_log_probability": float(log_probability),
        "trace_variant": payload.get("trace_variant"),
    }


def _redacted_trace_row_v1(
    payload: object, *, opponent_id: str, seat: int, game_index: int,
    seed: int, decision_index: int,
) -> dict[str, object]:
    """Return only bounded top-level metadata when a trace projection is unusable.

    A non-representable runtime projection must not abort an otherwise valid
    held-out game, and its nested payload must never be copied into the trace.
    The missing action list is explicit so downstream summaries cannot mistake
    the row for an observed action label.
    """
    source = payload if type(payload) is dict else {}
    log_probability = source.get("complete_action_log_probability")
    if type(log_probability) not in (int, float) or isinstance(log_probability, bool) or not math.isfinite(float(log_probability)):
        log_probability = None
    allowed = {
        key: source.get(key)
        for key in ("selection_type", "selection_context", "min_count", "max_count", "order_semantics", "selected_count")
    }
    return {
        "opponent_id": opponent_id,
        "seat": seat,
        "game_index": game_index,
        "seed": seed,
        "decision_index": decision_index,
        **allowed,
        "action_types": [],
        "complete_action_log_probability": log_probability,
        "trace_variant": "public-v1-redacted",
    }


def _atomic_trace_jsonl_v1(path: Path, rows: list[dict[str, object]]) -> str:
    """Publish bounded JSONL trace atomically and return its content SHA."""
    raw = b"".join(
        (json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        for row in rows
    )
    digest = hashlib.sha256(raw).hexdigest()
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.tmp.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return digest


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
    parser.add_argument("--progress-path", type=Path)
    parser.add_argument(
        "--trace-output", type=Path,
        help="optional privacy-safe JSONL action trace for the subject runtime",
    )
    parser.add_argument("--trace-max-rows", type=int, default=100_000)
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
    if args.trace_max_rows <= 0:
        raise ValueError("--trace-max-rows must be positive")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    _validate_args(args)
    provenance = _checkpoint_provenance(args.checkpoint)
    runtime_sinks: list[object] = []
    subject_factory = _v4_subject_factory(
        checkpoint_path=args.checkpoint,
        file_sha256=provenance["file_sha256"],
        tensor_state_sha256=provenance["tensor_state_sha256"],
        subject_deck_csv=args.subject_deck_csv,
        subject_archetype_id=args.subject_archetype_id,
        trace_sinks=runtime_sinks if args.trace_output is not None else None,
    )
    subject_deck_path = str(args.subject_deck_csv)
    pool = load_opponent_pool_v1(default_pool_root_v1(_ROOT))
    opponent_ids = EVAL_HELD_OUT_V1[:args.opponent_count]
    opponent_fingerprints = []
    for opponent_id in opponent_ids:
        opponent = resolve_opponent_v1(pool, opponent_id, subject_deck_csv_path="x")
        opponent_fingerprints.append({
            "opponent_id": opponent_id, "canonical_deck_hash": opponent.canonical_deck_hash,
            "deck_file_sha256": hashlib.sha256(Path(opponent.deck_csv_path).read_bytes()).hexdigest(),
            "policy_hash": opponent.policy_hash,
        })
    requested_games = len(opponent_ids) * 2 * args.games_per_seat
    reporter = ProgressReporterV1(
        total=requested_games, desc=f"v4-heldout {provenance['file_sha256'][:12]}",
        progress_path=args.progress_path,
    )
    reporter.note(
        f"[v4-heldout] checkpoint={provenance['file_sha256'][:12]} opponents={len(opponent_ids)} "
        f"games={requested_games}"
    )
    overall = _new_row()
    per_seat = {seat: _new_row() for seat in (0, 1)}
    per_opponent = {opponent_id: _new_row() for opponent_id in opponent_ids}
    fault_reasons: dict[str, int] = {}
    trace_rows: list[dict[str, object]] = []
    trace_overflow = 0
    trace_redacted = 0
    started = time.time()
    output_root = Path("runs/meta-specialist-strength") / f"v4-heldout-{provenance['file_sha256'][:12]}"

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
                sink_start = len(runtime_sinks)
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
                if args.trace_output is not None:
                    for runtime in runtime_sinks[sink_start:]:
                        for decision_index, trace in enumerate(getattr(runtime, "traces", ())):
                            if len(trace_rows) >= args.trace_max_rows:
                                trace_overflow += 1
                                continue
                            trace_payload = trace.to_payload()
                            try:
                                trace_rows.append(_public_trace_row_v1(
                                    trace_payload, opponent_id=opponent_id, seat=seat,
                                    game_index=game_index, seed=seed, decision_index=decision_index,
                                ))
                            except ValueError:
                                trace_rows.append(_redacted_trace_row_v1(
                                    trace_payload, opponent_id=opponent_id, seat=seat,
                                    game_index=game_index, seed=seed, decision_index=decision_index,
                                ))
                                trace_redacted += 1
                winner = result.get("winner")
                if winner == 2:
                    key = "d"
                elif winner == seat:
                    key = "w"
                else:
                    key = "l"
                for row in (overall, per_seat[seat], per_opponent[opponent_id]):
                    row[key] += 1
                reporter.update(1, win=overall["w"], loss=overall["l"], draw=overall["d"],
                                faults=overall["f"], rate=_score(overall) or 0.0)
    reporter.close()

    trace_summary: dict[str, object] | None = None
    if args.trace_output is not None:
        trace_sha256 = _atomic_trace_jsonl_v1(args.trace_output, trace_rows)
        by_action: dict[str, int] = {}
        by_opponent: dict[str, int] = {}
        by_seat: dict[str, int] = {}
        for row in trace_rows:
            by_opponent[str(row["opponent_id"])] = by_opponent.get(str(row["opponent_id"]), 0) + 1
            by_seat[str(row["seat"])] = by_seat.get(str(row["seat"]), 0) + 1
            for action_type in row["action_types"]:
                by_action[str(action_type)] = by_action.get(str(action_type), 0) + 1
        trace_summary = {
            "path": str(args.trace_output.resolve()),
            "sha256": trace_sha256,
            "rows": len(trace_rows),
            "overflow_rows": trace_overflow,
            "redacted_rows": trace_redacted,
            "by_action_type": dict(sorted(by_action.items())),
            "by_opponent": dict(sorted(by_opponent.items())),
            "by_seat": dict(sorted(by_seat.items())),
        }

    score = _score(overall)
    payload: dict[str, Any] = {
        "schema_version": V4_HELDOUT_CHECKPOINT_STRENGTH_SCHEMA_V1,
        "checkpoint": provenance,
        "subject_archetype_id": args.subject_archetype_id,
        "subject_deck_csv": str(args.subject_deck_csv.resolve()),
        "subject_deck_file_sha256": hashlib.sha256(args.subject_deck_csv.read_bytes()).hexdigest(),
        "fixed_held_out_opponent_ids": list(EVAL_HELD_OUT_V1),
        "opponent_ids": list(opponent_ids),
        "opponent_fingerprints": opponent_fingerprints,
        "evaluation_protocol_sha256": heldout_protocol_sha256_v1(),
        "evaluation_implementation_sha256": evaluation_implementation_sha256_v1(),
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
    if trace_summary is not None:
        payload["trace"] = trace_summary
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in payload.items() if key != "per_opponent"}, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
