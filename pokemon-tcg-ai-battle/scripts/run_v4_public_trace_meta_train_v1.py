#!/usr/bin/env python3
"""Collect public-only V4 traces over the verified local META_TRAIN pool.

This runner is deliberately separate from the broad score runner.  It keeps
only terminal WDL and the V4 runtime's already-redacted public decision trace;
native opponent action labels, raw observations, and private fields never
cross the artifact boundary.  It is a research diagnostic and grants no
training, promotion, submission, or long-run authority.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile
import time
from typing import Any, Mapping, Sequence

_ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(_ROOT), str(_ROOT / "src")]

from mage_ptcg.meta_specialist.collect_teacher_records_v1 import seed_agent_randomness_v1  # noqa: E402
from mage_ptcg.meta_specialist.opponent_pool_v1 import (  # noqa: E402
    OpponentInstanceV1,
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
from scripts.test_sim import run_match  # noqa: E402


SCHEMA_V1 = "meta-specialist-v4-public-trace-meta-train-v1"
CONFIG_SCHEMA_V1 = "meta-specialist-performance-first-broad-pool-v1"
EXPECTED_POOL_SIZE_V1 = 24
_SHA_HEX = frozenset("0123456789abcdef")
_FORBIDDEN_KEY_FRAGMENTS = frozenset({
    "observation", "raw_observation", "private", "private_state", "hand", "prize",
    "deck", "serial", "local_action", "stable_key", "option_index", "actor_payload",
    "native_label", "teacher_label", "behavior_label",
})


def _sha256(path: Path | str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _require_sha(value: object, field: str) -> str:
    if type(value) is not str or len(value) != 64 or any(char not in _SHA_HEX for char in value):
        raise ValueError(f"{field} must be a lowercase SHA-256 string")
    return value


def _walk_public(value: object, *, depth: int = 0) -> None:
    if depth > 8:
        raise ValueError("public trace is too deeply nested")
    if isinstance(value, Mapping):
        if len(value) > 64:
            raise ValueError("public trace mapping is oversized")
        for key, child in value.items():
            if type(key) is not str:
                raise ValueError("public trace keys must be strings")
            normalized = re.sub(r"[^a-z0-9]+", "_", key.lower()).strip("_")
            if normalized in _FORBIDDEN_KEY_FRAGMENTS or any(
                fragment in normalized for fragment in ("private", "secret", "hidden")
            ):
                raise ValueError("public trace contains a forbidden field")
            _walk_public(child, depth=depth + 1)
        return
    if isinstance(value, list):
        if len(value) > 64:
            raise ValueError("public trace list is oversized")
        for child in value:
            _walk_public(child, depth=depth + 1)
        return
    if value is None or type(value) in (bool, int, float, str):
        return
    raise ValueError("public trace contains a non-JSON value")


def validate_meta_train_config_v1(
    config: Mapping[str, object], *, config_sha256: str, pool_manifest_sha256: str,
) -> tuple[str, ...]:
    """Validate the immutable 24-ID, local-evaluation-only pool declaration."""
    if not isinstance(config, Mapping) or config.get("schema_version") != CONFIG_SCHEMA_V1:
        raise ValueError("META_TRAIN config schema is invalid")
    _require_sha(config_sha256, "config_sha256")
    supplied_pool_sha = _require_sha(pool_manifest_sha256, "pool_manifest_sha256")
    if config.get("pool_manifest_sha256") != supplied_pool_sha:
        raise ValueError("META_TRAIN config pool manifest SHA does not match supplied SHA")
    if config.get("local_eval_only") is not True:
        raise ValueError("META_TRAIN config must be local_eval_only=true")
    if config.get("promotion_authority") is not False:
        raise ValueError("META_TRAIN config promotion authority must be false")
    if config.get("selection_policy") != "public_smoke_ok_one_policy_instance_per_policy_hash":
        raise ValueError("META_TRAIN config selection policy is not the verified broad pool")
    raw_ids = config.get("opponent_ids")
    if not isinstance(raw_ids, list) or len(raw_ids) != EXPECTED_POOL_SIZE_V1:
        raise ValueError("META_TRAIN config must contain exactly 24 opponent IDs")
    if any(type(item) is not str or not item for item in raw_ids):
        raise ValueError("META_TRAIN opponent IDs must be non-empty strings")
    ids = tuple(raw_ids)
    if len(set(ids)) != len(ids):
        raise ValueError("META_TRAIN opponent IDs must be unique")
    return ids


def verify_meta_train_pool_entries_v1(
    opponent_ids: Sequence[str], pool: Mapping[str, OpponentInstanceV1],
) -> tuple[dict[str, object], ...]:
    """Recheck each selected pool identity and deny non-local assets."""
    seen_policy: set[str] = set()
    verified: list[dict[str, object]] = []
    for opponent_id in opponent_ids:
        instance = pool.get(opponent_id)
        if not isinstance(instance, OpponentInstanceV1):
            raise ValueError(f"META_TRAIN opponent is not a verified pool entry: {opponent_id}")
        if instance.usage_boundary != "local_eval_only":
            raise ValueError(f"META_TRAIN opponent {opponent_id} is not local_eval_only")
        policy_sha = _require_sha(instance.policy_hash, f"{opponent_id}.policy_hash")
        deck_sha = _require_sha(instance.canonical_deck_hash, f"{opponent_id}.canonical_deck_hash")
        if policy_sha in seen_policy:
            raise ValueError(f"META_TRAIN has duplicate policy identity: {opponent_id}")
        seen_policy.add(policy_sha)
        # ``load_opponent_pool_v1`` already verifies that both files exist and
        # that policy bytes match the manifest.  Keep this helper focused on
        # the selection/permission identity so it remains unit-testable with
        # immutable fixture objects.
        if Path(instance.deck_csv_path).is_file() and _sha256(instance.deck_csv_path) != deck_sha:
            # The manifest's canonical deck hash is not the raw deck-file hash
            # for every historical asset.  Keep the canonical identity binding,
            # and record the raw hash separately for auditability.
            raw_deck_sha = _sha256(instance.deck_csv_path)
        else:
            raw_deck_sha = _sha256(instance.deck_csv_path) if Path(instance.deck_csv_path).is_file() else deck_sha
        if Path(instance.policy_path).is_file() and _sha256(instance.policy_path) != policy_sha:
            raise ValueError(f"META_TRAIN policy bytes do not match manifest: {opponent_id}")
        verified.append({
            "opponent_id": opponent_id,
            "canonical_deck_sha256": deck_sha,
            "deck_file_sha256": raw_deck_sha,
            "policy_sha256": policy_sha,
            "usage_boundary": instance.usage_boundary,
            "source": instance.source,
        })
    return tuple(verified)


def bind_public_trace_outcome_v1(
    trace_row: Mapping[str, object], *, game_id: str, outcome: str, winner: int | None,
) -> dict[str, object]:
    """Bind one already-redacted V4 trace row to a terminal WDL outcome."""
    if not isinstance(trace_row, Mapping) or type(game_id) is not str or not game_id:
        raise ValueError("trace row/game identity is invalid")
    if outcome not in {"win", "draw", "loss", "fault"}:
        raise ValueError("trace outcome is invalid")
    if winner is not None and (type(winner) is not int or winner not in (0, 1, 2)):
        raise ValueError("trace winner is invalid")
    allowed = (
        "opponent_id", "seat", "seed", "decision_index", "selection_type",
        "selection_context", "min_count", "max_count", "order_semantics",
        "selected_count", "action_types", "complete_action_log_probability", "trace_variant",
    )
    row = {key: trace_row[key] for key in allowed if key in trace_row}
    row.update({"game_id": game_id, "outcome": outcome, "winner": winner})
    _walk_public(row)
    return row


def aggregate_meta_train_rows_v1(rows: Sequence[Mapping[str, object]]) -> dict[str, object]:
    requested = len(rows)
    outcomes = Counter(str(row.get("outcome", "fault")) for row in rows)
    wins, draws, losses, faults = (outcomes.get(key, 0) for key in ("win", "draw", "loss", "fault"))
    by_seat: dict[str, Counter[str]] = defaultdict(Counter)
    by_opponent: dict[str, Counter[str]] = defaultdict(Counter)
    for row in rows:
        outcome = str(row.get("outcome", "fault"))
        by_seat[str(row.get("seat"))][outcome] += 1
        by_opponent[str(row.get("opponent_id"))][outcome] += 1

    def _cell(counter: Counter[str]) -> dict[str, object]:
        total = sum(counter.values())
        return {
            "wins": counter.get("win", 0), "draws": counter.get("draw", 0),
            "losses": counter.get("loss", 0), "faults": counter.get("fault", 0),
            "requested_games": total,
            "score_rate": (counter.get("win", 0) + 0.5 * counter.get("draw", 0)) / total if total else None,
        }

    return {
        "requested_games": requested,
        "completed_games": wins + draws + losses,
        "wins": wins, "draws": draws, "losses": losses, "faults": faults,
        "fault_rate": faults / requested if requested else None,
        "score_rate": (wins + 0.5 * draws) / requested if requested else None,
        "score_denominator_games": requested,
        "seat": {key: _cell(value) for key, value in sorted(by_seat.items())},
        "opponent": {key: _cell(value) for key, value in sorted(by_opponent.items())},
        "outcome_distribution": dict(sorted(outcomes.items())),
    }


def _atomic_bytes(path: Path, raw: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(raw).hexdigest()
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.tmp-", dir=path.parent)
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


def _atomic_json(path: Path, payload: Mapping[str, object]) -> str:
    raw = (json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    return _atomic_bytes(path, raw)


def _load_config(path: Path) -> tuple[dict[str, object], str, tuple[str, ...]]:
    raw = path.read_bytes()
    config = json.loads(raw.decode("utf-8"))
    if not isinstance(config, dict):
        raise ValueError("META_TRAIN config must be a JSON object")
    config_sha = hashlib.sha256(raw).hexdigest()
    ids = validate_meta_train_config_v1(
        config,
        config_sha256=config_sha,
        pool_manifest_sha256=config.get("pool_manifest_sha256"),
    )
    return config, config_sha, ids


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--subject-deck-csv", type=Path, required=True)
    parser.add_argument("--subject-archetype-id", default="archaludon")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--games-per-seat", type=int, default=2)
    parser.add_argument("--base-seed", type=int, default=14910000)
    parser.add_argument("--max-steps", type=int, default=2000)
    parser.add_argument("--trace-max-rows", type=int, default=200000)
    return parser


def _validate_args(args: argparse.Namespace) -> None:
    if args.games_per_seat <= 0 or args.base_seed < 0 or args.max_steps <= 0 or args.trace_max_rows <= 0:
        raise ValueError("games-per-seat, base-seed, max-steps, and trace-max-rows must be positive")
    for path, label in (
        (args.config, "config"), (args.checkpoint, "checkpoint"), (args.subject_deck_csv, "subject deck"),
    ):
        if not path.is_file():
            raise ValueError(f"{label} does not exist: {path}")
    if args.output.exists() and any(args.output.iterdir()):
        raise FileExistsError(f"output must be a fresh directory: {args.output}")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    _validate_args(args)
    config, config_sha, opponent_ids = _load_config(args.config)
    pool_manifest_path = _ROOT / str(config["pool_manifest_path"])
    pool_manifest_sha = _sha256(pool_manifest_path)
    if pool_manifest_sha != str(config["pool_manifest_sha256"]):
        raise ValueError("pool manifest bytes do not match the META_TRAIN config")
    pool = load_opponent_pool_v1(default_pool_root_v1(_ROOT))
    verified_pool = verify_meta_train_pool_entries_v1(opponent_ids, pool)
    provenance = _checkpoint_provenance(args.checkpoint)
    subject_deck_sha = _sha256(args.subject_deck_csv)
    runtime_sinks: list[object] = []
    subject_factory = _v4_subject_factory(
        checkpoint_path=args.checkpoint,
        file_sha256=provenance["file_sha256"],
        tensor_state_sha256=provenance["tensor_state_sha256"],
        subject_deck_csv=args.subject_deck_csv,
        subject_archetype_id=args.subject_archetype_id,
        trace_sinks=runtime_sinks,
    )
    rows: list[dict[str, object]] = []
    trace_rows: list[dict[str, object]] = []
    fault_reasons: Counter[str] = Counter()
    trace_redacted = 0
    trace_overflow = 0
    started = time.time()
    ordinal = 0
    for opponent_id in opponent_ids:
        opponent = resolve_opponent_v1(pool, opponent_id, subject_deck_csv_path=str(args.subject_deck_csv))
        opponent_factory = build_opponent_agent_factory_v1(opponent)
        for seat in (0, 1):
            for repetition in range(args.games_per_seat):
                game_id = f"v4-public-meta-train-seed1-{opponent_id}-seat{seat}-g{repetition:04d}"
                seed = args.base_seed + ordinal
                ordinal += 1
                sink_start = len(runtime_sinks)
                seed_agent_randomness_v1(seed)
                subject_first = seat == 0
                result: Mapping[str, object] | None = None
                fault_kind: str | None = None
                try:
                    result = run_match(
                        deck_a_path=str(args.subject_deck_csv) if subject_first else opponent.deck_csv_path,
                        deck_b_path=opponent.deck_csv_path if subject_first else str(args.subject_deck_csv),
                        agent_a_name="v4-seed1" if subject_first else opponent_id,
                        agent_b_name=opponent_id if subject_first else "v4-seed1",
                        seed=seed,
                        max_steps=args.max_steps,
                        output_dir=str(args.output.parent / f"{args.output.name}-matches" / game_id),
                        save_html=False,
                        save_result=False,
                        agent_a_factory=subject_factory if subject_first else opponent_factory,
                        agent_b_factory=opponent_factory if subject_first else subject_factory,
                    )
                    if result.get("status") != "DONE":
                        fault_kind = "non_done"
                except Exception as exc:  # noqa: BLE001 - preserve fault denominator
                    fault_kind = type(exc).__name__
                    fault_reasons[fault_kind] += 1
                if fault_kind is None and result is not None:
                    winner = result.get("winner")
                    outcome = "draw" if winner == 2 else ("win" if winner == seat else "loss")
                    status = "DONE"
                    terminal_reason = result.get("terminal_reason")
                    steps = result.get("steps") if type(result.get("steps")) is int else None
                else:
                    winner = None
                    outcome = "fault"
                    status = "FAULT"
                    terminal_reason = None
                    steps = None
                rows.append({
                    "schema_version": SCHEMA_V1,
                    "game_id": game_id,
                    "opponent_id": opponent_id,
                    "opponent_identity": {
                        "canonical_deck_sha256": opponent.canonical_deck_hash,
                        "deck_file_sha256": _sha256(opponent.deck_csv_path),
                        "policy_sha256": opponent.policy_hash,
                        "usage_boundary": opponent.usage_boundary,
                        "source": opponent.source,
                    },
                    "seat": seat,
                    "repetition": repetition,
                    "seed": seed,
                    "outcome": outcome,
                    "winner": winner,
                    "status": status,
                    "fault_kind": fault_kind,
                    "steps": steps,
                    "terminal_reason": terminal_reason,
                    "policy_sha256": provenance["file_sha256"],
                    "checkpoint_tensor_sha256": provenance["tensor_state_sha256"],
                    "deck_sha256": subject_deck_sha,
                })
                if outcome != "fault":
                    for runtime in runtime_sinks[sink_start:]:
                        for decision_index, trace in enumerate(getattr(runtime, "traces", ())):
                            if len(trace_rows) >= args.trace_max_rows:
                                trace_overflow += 1
                                continue
                            payload = trace.to_payload()
                            try:
                                projected = _public_trace_row_v1(
                                    payload,
                                    opponent_id=opponent_id,
                                    seat=seat,
                                    game_index=repetition,
                                    seed=seed,
                                    decision_index=decision_index,
                                )
                            except ValueError:
                                projected = _redacted_trace_row_v1(
                                    payload,
                                    opponent_id=opponent_id,
                                    seat=seat,
                                    game_index=repetition,
                                    seed=seed,
                                    decision_index=decision_index,
                                )
                                trace_redacted += 1
                            trace_rows.append(bind_public_trace_outcome_v1(
                                projected, game_id=game_id, outcome=outcome,
                                winner=winner if type(winner) is int else None,
                            ))
    rows_raw = b"".join((json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8") for row in rows)
    trace_raw = b"".join((json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8") for row in trace_rows)
    ledger_sha = _atomic_bytes(args.output / "ledger.jsonl", rows_raw)
    trace_sha = _atomic_bytes(args.output / "public-trace.jsonl", trace_raw)
    summary = aggregate_meta_train_rows_v1(rows)
    summary.update({
        "schema_version": SCHEMA_V1,
        "config_path": str(args.config.resolve()),
        "config_sha256": config_sha,
        "pool_manifest_path": str(pool_manifest_path.resolve()),
        "pool_manifest_sha256": pool_manifest_sha,
        "opponent_ids": list(opponent_ids),
        "verified_pool": list(verified_pool),
        "checkpoint": provenance,
        "subject_deck_csv": str(args.subject_deck_csv.resolve()),
        "subject_deck_file_sha256": subject_deck_sha,
        "base_seed": args.base_seed,
        "games_per_seat": args.games_per_seat,
        "max_steps": args.max_steps,
        "engine_seed_supported": False,
        "fault_reasons": dict(sorted(fault_reasons.items())),
        "ledger_sha256": ledger_sha,
        "trace": {
            "path": str((args.output / "public-trace.jsonl").resolve()),
            "sha256": trace_sha,
            "rows": len(trace_rows),
            "redacted_rows": trace_redacted,
            "overflow_rows": trace_overflow,
        },
        "training_authority": False,
        "behavior_authority": False,
        "promotion_authority": False,
        "submission_authority": False,
        "longrun_allowed": False,
        "native_action_labels_saved": False,
        "teacher_labels_saved": False,
        "private_fields_saved": False,
        "elapsed_seconds": round(time.time() - started, 3),
    })
    _atomic_json(args.output / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "SCHEMA_V1", "aggregate_meta_train_rows_v1", "bind_public_trace_outcome_v1",
    "main", "validate_meta_train_config_v1", "verify_meta_train_pool_entries_v1",
]
