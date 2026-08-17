#!/usr/bin/env python3
"""Build a bounded action-type diagnostic from a V4 public trace bundle."""

from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import tempfile
from typing import Mapping, Sequence


TABLE_SCHEMA_V1 = "meta-specialist-v4-public-trace-action-table-v1"
RUN_SCHEMA_V1 = "meta-specialist-v4-public-trace-meta-train-v1"
_FORBIDDEN = frozenset({
    "observation", "raw_observation", "private", "private_state", "hand", "prize",
    "deck", "serial", "local_action", "stable_key", "option_index", "actor_payload",
    "native_label", "teacher_label", "behavior_label",
})


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
            if normalized in _FORBIDDEN or any(
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


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def build_public_action_table_v1(
    *, summary: Mapping[str, object], ledger_rows: Sequence[Mapping[str, object]],
    trace_rows: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    """Aggregate only public V4 action types and terminal outcomes.

    The gate intentionally demands substantial support and competing action
    types.  A table with one lucky action type is a diagnostic, never a policy
    candidate.
    """
    if summary.get("schema_version") != RUN_SCHEMA_V1:
        raise ValueError("trace bundle schema is invalid")
    if summary.get("native_action_labels_saved") is not False or summary.get("private_fields_saved") is not False:
        raise ValueError("trace bundle authority/privacy flags are not fail-closed")
    requested = summary.get("requested_games")
    if type(requested) is not int or requested <= 0 or len(ledger_rows) != requested:
        raise ValueError("trace bundle ledger does not cover the requested game denominator")
    ledger_ids = {str(row.get("game_id")) for row in ledger_rows}
    if len(ledger_ids) != requested:
        raise ValueError("trace bundle ledger game IDs are not unique")
    action_outcomes: dict[str, Counter[str]] = {}
    action_examples: Counter[str] = Counter()
    trace_game_ids: set[str] = set()
    for row in trace_rows:
        if not isinstance(row, Mapping):
            raise ValueError("trace row is not an object")
        _walk_public(row)
        game_id = row.get("game_id")
        if type(game_id) is not str or game_id not in ledger_ids:
            raise ValueError("trace row is not bound to a ledger game")
        trace_game_ids.add(game_id)
        action_types = row.get("action_types", [])
        if not isinstance(action_types, list):
            raise ValueError("trace action_types must be a list")
        outcome = row.get("outcome")
        if outcome not in {"win", "draw", "loss"}:
            continue
        for action_type in action_types:
            if type(action_type) is not str or not action_type or len(action_type) > 64:
                raise ValueError("trace action type is invalid")
            action_examples[action_type] += 1
            action_outcomes.setdefault(action_type, Counter())[str(outcome)] += 1
    entries: dict[str, dict[str, object]] = {}
    for action_type in sorted(action_outcomes):
        counts = action_outcomes[action_type]
        support = sum(counts.values())
        entries[action_type] = {
            "wins": counts.get("win", 0), "draws": counts.get("draw", 0),
            "losses": counts.get("loss", 0), "support": support,
            "score_rate": (counts.get("win", 0) + 0.5 * counts.get("draw", 0)) / support if support else None,
        }
    mixed_sign = sum(1 for counts in action_outcomes.values() if counts.get("win", 0) and counts.get("loss", 0))
    reasons: list[str] = []
    if sum(action_examples.values()) < 200:
        reasons.append("insufficient_action_examples")
    if len(entries) < 2:
        reasons.append("insufficient_competing_action_types")
    if mixed_sign < 2:
        reasons.append("insufficient_mixed_sign_action_types")
    table: dict[str, object] = {
        "schema_version": TABLE_SCHEMA_V1,
        "requested_games": requested,
        "ledger_game_ids": requested,
        "trace_games_with_rows": len(trace_game_ids),
        "trace_rows": len(trace_rows),
        "action_events": sum(action_examples.values()),
        "action_types": len(entries),
        "mixed_sign_action_types": mixed_sign,
        "entries": entries,
        "reasons": reasons,
        "usable_signal": not reasons,
        "ready_for_candidate_screen": not reasons,
        "candidate_screen_started": False,
        "native_action_labels_saved": False,
        "private_fields_saved": False,
        "training_authority": False,
        "promotion_authority": False,
        "submission_authority": False,
    }
    table["table_sha256"] = hashlib.sha256(b"v4-public-trace-action-table-v1\0" + _canonical(table)).hexdigest()
    return table


def _atomic_json(path: Path, payload: Mapping[str, object]) -> str:
    raw = (json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.tmp-", dir=path.parent)
    try:
        with open(descriptor, "wb", closefd=True) as handle:
            handle.write(raw)
            handle.flush()
        Path(temporary).replace(path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return hashlib.sha256(raw).hexdigest()


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    summary_path = args.run_root / "summary.json"
    ledger_path = args.run_root / "ledger.jsonl"
    trace_path = args.run_root / "public-trace.jsonl"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    ledger = [json.loads(line) for line in ledger_path.read_text(encoding="utf-8").splitlines() if line]
    traces = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines() if line]
    table = build_public_action_table_v1(summary=summary, ledger_rows=ledger, trace_rows=traces)
    table.update({
        "source_summary_sha256": hashlib.sha256(summary_path.read_bytes()).hexdigest(),
        "source_ledger_sha256": hashlib.sha256(ledger_path.read_bytes()).hexdigest(),
        "source_trace_sha256": hashlib.sha256(trace_path.read_bytes()).hexdigest(),
    })
    output = args.output or args.run_root / "public-action-table.json"
    # The semantic table hash remains stable; callers hash the final artifact
    # bytes externally rather than recursively embedding an artifact hash.
    _atomic_json(output, table)
    print(json.dumps(table, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["TABLE_SCHEMA_V1", "build_public_action_table_v1", "main"]
