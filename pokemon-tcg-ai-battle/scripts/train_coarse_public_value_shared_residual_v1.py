#!/usr/bin/env python3
"""Train one shared coarse residual table from both sealed Wave6 seeds.

This is a single predeclared research arm: both seed-specific public-state
value row artifacts are merged with seed-qualified episode/record identities,
then one bounded table is optimized.  The resulting runtime table is closed
against the common train reference bundle and can be evaluated on either
frozen base checkpoint.  It grants no promotion or long-run authority.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from mage_ptcg.meta_specialist.coarse_record_residual_trainer_v1 import (  # noqa: E402
    CoarsePrefixLogitRowV1,
    CoarseResidualTableV1,
    train_coarse_record_residual_v1,
)
from mage_ptcg.meta_specialist.frozen_residual_v1 import STOP_ACTION_KEY_V1  # noqa: E402


TABLE_SCHEMA = "specialist-coarse-public-residual-table-v1"
REPORT_SCHEMA = "specialist-coarse-public-value-shared-residual-training-report-v1"
_HEX64 = frozenset("0123456789abcdef")


def _sha(path: Path) -> str:
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"artifact must be a regular file: {path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _require_sha(value: object, field: str) -> str:
    if type(value) is not str or len(value) != 64 or any(char not in _HEX64 for char in value):
        raise ValueError(f"{field} must be lowercase SHA-256")
    return value


def _load_rows(path: Path, expected_sha256: str, seed: int) -> tuple[dict[str, Any], tuple[CoarsePrefixLogitRowV1, ...]]:
    actual = _sha(path)
    if actual != _require_sha(expected_sha256, f"seed{seed} rows SHA"):
        raise ValueError(f"seed{seed} rows SHA mismatch")
    payload = json.loads(path.read_text(encoding="utf-8"))
    expected = {
        "schema_version", "seed", "screen_file_sha256", "value_manifest_file_sha256",
        "source_episode_sha256", "checkpoint_file_sha256", "checkpoint_tensor_state_sha256",
        "episode_count", "transition_count", "prefix_row_count", "public_bucket_count",
        "public_bucket_counts", "target_baseline_source_counts", "target_kind", "rows",
        "research_only", "training_permitted", "promotion_authority", "longrun_allowed",
        "performance_evidence",
    }
    if type(payload) is not dict or set(payload) != expected:
        raise ValueError(f"seed{seed} rows artifact has an open schema")
    if payload["schema_version"] != "specialist-coarse-public-value-logit-rows-v1" or payload["target_kind"] != "signed_public_state_value_residual":
        raise ValueError(f"seed{seed} rows artifact kind is invalid")
    if payload["research_only"] is not True or any(payload[field] is not False for field in ("training_permitted", "promotion_authority", "longrun_allowed", "performance_evidence")):
        raise ValueError(f"seed{seed} rows artifact grants authority")
    result: list[CoarsePrefixLogitRowV1] = []
    for raw in payload["rows"]:
        if type(raw) is not dict or set(raw) != {"episode_id", "record_id", "prefix_index", "bucket_id", "action_keys", "base_logits", "target_index", "signed_weight"}:
            raise ValueError(f"seed{seed} row has an open schema")
        result.append(CoarsePrefixLogitRowV1(
            episode_id=f"seed{seed}:{raw['episode_id']}", record_id=f"seed{seed}:{raw['record_id']}",
            prefix_index=raw["prefix_index"], bucket_id=raw["bucket_id"], action_keys=tuple(raw["action_keys"]),
            base_logits=tuple(raw["base_logits"]), target_index=raw["target_index"], signed_weight=raw["signed_weight"],
        ))
    if len(result) != payload["prefix_row_count"]:
        raise ValueError(f"seed{seed} row count mismatch")
    return {"payload": payload, "sha256": actual}, tuple(result)


def train_shared_v1(
    rows0: Path, rows0_sha256: str, rows1: Path, rows1_sha256: str,
    *, bundle_sha256: str, source_list_sha256: str, mode: str,
    max_updates: int, learning_rate: float, max_abs_residual: float,
) -> tuple[dict[str, Any], dict[str, Any]]:
    meta0, part0 = _load_rows(rows0, rows0_sha256, 0)
    meta1, part1 = _load_rows(rows1, rows1_sha256, 1)
    rows = part0 + part1
    table = CoarseResidualTableV1.from_rows(rows, max_abs_residual=max_abs_residual)
    result = train_coarse_record_residual_v1(table, rows, mode=mode, max_updates=max_updates, learning_rate=learning_rate)
    residual = table.to_residual_table()
    semantic: dict[str, dict[str, float]] = {}
    stop: dict[str, float] = {}
    for bucket, actions in residual.items():
        for action, value in actions.items():
            if action == STOP_ACTION_KEY_V1:
                stop[bucket] = value
            else:
                semantic.setdefault(bucket, {})[action] = value
    table_payload = {
        "schema_version": TABLE_SCHEMA,
        "reference_bundle_file_sha256": _require_sha(bundle_sha256, "reference bundle SHA"),
        "reference_source_list_sha256": _require_sha(source_list_sha256, "reference source-list SHA"),
        "max_abs_residual": max_abs_residual,
        "residual_by_bucket_action": {k: dict(sorted(v.items())) for k, v in sorted(semantic.items())},
        "stop_residual_by_bucket": dict(sorted(stop.items())),
        "prefix_count": len(rows),
        "training_permitted": False, "promotion_authority": False,
        "longrun_allowed": False, "performance_evidence": False,
    }
    report = {
        "schema_version": REPORT_SCHEMA, "table": table_payload,
        "source_rows": {
            "seed0": {"file_sha256": meta0["sha256"], **{k: meta0["payload"][k] for k in ("screen_file_sha256", "value_manifest_file_sha256", "source_episode_sha256", "checkpoint_file_sha256", "checkpoint_tensor_state_sha256", "prefix_row_count")}},
            "seed1": {"file_sha256": meta1["sha256"], **{k: meta1["payload"][k] for k in ("screen_file_sha256", "value_manifest_file_sha256", "source_episode_sha256", "checkpoint_file_sha256", "checkpoint_tensor_state_sha256", "prefix_row_count")}},
        },
        "mode": mode, "max_updates": max_updates, "learning_rate": learning_rate,
        "max_abs_residual": max_abs_residual, "records": result.records,
        "prefix_rows": result.prefix_rows, "optimizer_updates": result.optimizer_updates,
        "loss_normalizer": result.loss_normalizer, "signed_complete_action_loss": result.signed_complete_action_loss,
        "anchor_kl": result.anchor_kl, "residual_l2": result.residual_l2,
        "max_abs_residual_observed": result.max_abs_residual,
        "training_permitted": False, "promotion_authority": False,
        "longrun_allowed": False, "performance_evidence": False,
    }
    return table_payload, report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows-seed0", type=Path, required=True)
    parser.add_argument("--rows-seed0-sha256", required=True)
    parser.add_argument("--rows-seed1", type=Path, required=True)
    parser.add_argument("--rows-seed1-sha256", required=True)
    parser.add_argument("--bundle-sha256", required=True)
    parser.add_argument("--source-list-sha256", required=True)
    parser.add_argument("--mode", choices=("episode_normalized",), default="episode_normalized")
    parser.add_argument("--max-updates", type=int, default=3)
    parser.add_argument("--learning-rate", type=float, default=1000.0)
    parser.add_argument("--max-abs-residual", type=float, default=0.25)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report-output", type=Path, required=True)
    args = parser.parse_args(argv)
    table, report = train_shared_v1(
        args.rows_seed0, args.rows_seed0_sha256, args.rows_seed1, args.rows_seed1_sha256,
        bundle_sha256=args.bundle_sha256, source_list_sha256=args.source_list_sha256,
        mode=args.mode, max_updates=args.max_updates, learning_rate=args.learning_rate,
        max_abs_residual=args.max_abs_residual,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.report_output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(table, ensure_ascii=True, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    args.report_output.write_text(json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("mode", "prefix_rows", "records", "optimizer_updates", "max_abs_residual_observed")}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
