#!/usr/bin/env python3
"""Train one bounded coarse residual table from sealed replay rows.

This is a research-only, frozen-base arm.  It consumes the detached rows
created by ``build_coarse_public_value_rows_v1.py`` and updates only the coarse
table.  ``record_normalized`` and ``episode_normalized`` are separate fixed
arms; the command never selects one from a performance result.
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


SCHEMA = "specialist-coarse-public-value-residual-artifact-v1"
TABLE_SCHEMA = "specialist-coarse-public-residual-table-v1"
_HEX64 = frozenset("0123456789abcdef")


def _sha(path: Path) -> str:
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"artifact must be a regular file: {path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _require_sha(value: object, field: str) -> str:
    if type(value) is not str or len(value) != 64 or any(char not in _HEX64 for char in value):
        raise ValueError(f"{field} must be lowercase SHA-256")
    return value


def _rows_from_payload(payload: dict[str, Any]) -> tuple[CoarsePrefixLogitRowV1, ...]:
    expected = {
        "schema_version", "seed", "screen_file_sha256", "value_manifest_file_sha256",
        "source_episode_sha256", "checkpoint_file_sha256", "checkpoint_tensor_state_sha256",
        "episode_count", "transition_count", "prefix_row_count", "public_bucket_count",
        "public_bucket_counts", "target_baseline_source_counts", "target_kind", "rows",
        "research_only", "training_permitted", "promotion_authority", "longrun_allowed",
        "performance_evidence",
    }
    if type(payload) is not dict or set(payload) != expected:
        raise ValueError("replay row artifact has an open schema")
    if payload["schema_version"] != "specialist-coarse-public-value-logit-rows-v1":
        raise ValueError("replay row artifact schema is invalid")
    if payload["target_kind"] != "signed_public_state_value_residual":
        raise ValueError("replay row target kind is invalid")
    if payload["research_only"] is not True or any(payload[field] is not False for field in ("training_permitted", "promotion_authority", "longrun_allowed", "performance_evidence")):
        raise ValueError("replay row artifact grants authority")
    rows = payload["rows"]
    if type(rows) is not list or not rows:
        raise ValueError("replay row artifact has no rows")
    result = []
    for raw in rows:
        if type(raw) is not dict or set(raw) != {
            "episode_id", "record_id", "prefix_index", "bucket_id", "action_keys",
            "base_logits", "target_index", "signed_weight",
        }:
            raise ValueError("replay row has an open schema")
        result.append(CoarsePrefixLogitRowV1(
            episode_id=raw["episode_id"], record_id=raw["record_id"], prefix_index=raw["prefix_index"],
            bucket_id=raw["bucket_id"], action_keys=tuple(raw["action_keys"]),
            base_logits=tuple(raw["base_logits"]), target_index=raw["target_index"],
            signed_weight=raw["signed_weight"],
        ))
    if len(result) != payload["prefix_row_count"]:
        raise ValueError("replay row count does not match artifact")
    return tuple(result)


def train_artifact_v1(
    rows_path: Path,
    *,
    expected_rows_sha256: str,
    reference_bundle_file_sha256: str,
    reference_source_list_sha256: str,
    mode: str,
    max_updates: int,
    learning_rate: float,
    max_abs_residual: float,
) -> tuple[dict[str, Any], dict[str, Any]]:
    actual = _sha(rows_path)
    if actual != _require_sha(expected_rows_sha256, "rows SHA"):
        raise ValueError("replay row artifact SHA mismatch")
    payload = json.loads(rows_path.read_text(encoding="utf-8"))
    rows = _rows_from_payload(payload)
    table = CoarseResidualTableV1.from_rows(rows, max_abs_residual=max_abs_residual)
    result = train_coarse_record_residual_v1(
        table, rows, mode=mode, max_updates=max_updates, learning_rate=learning_rate,
    )
    residual_table = table.to_residual_table()
    stop_table: dict[str, float] = {}
    semantic_table: dict[str, dict[str, float]] = {}
    for bucket, actions in residual_table.items():
        for action, value in actions.items():
            if action == STOP_ACTION_KEY_V1:
                stop_table[bucket] = value
            else:
                semantic_table.setdefault(bucket, {})[action] = value
    table_payload = {
        "schema_version": TABLE_SCHEMA,
        "reference_bundle_file_sha256": _require_sha(reference_bundle_file_sha256, "reference bundle SHA"),
        "reference_source_list_sha256": _require_sha(reference_source_list_sha256, "reference source-list SHA"),
        "max_abs_residual": max_abs_residual,
        "residual_by_bucket_action": {bucket: dict(sorted(actions.items())) for bucket, actions in sorted(semantic_table.items())},
        "stop_residual_by_bucket": dict(sorted(stop_table.items())),
        "prefix_count": result.prefix_rows,
        "training_permitted": False,
        "promotion_authority": False,
        "longrun_allowed": False,
        "performance_evidence": False,
    }
    report = {
        "schema_version": "specialist-coarse-public-value-residual-training-report-v1",
        "table_schema_version": TABLE_SCHEMA,
        "table": table_payload,
        "rows_file_sha256": actual,
        "seed": payload["seed"],
        "screen_file_sha256": payload["screen_file_sha256"],
        "value_manifest_file_sha256": payload["value_manifest_file_sha256"],
        "source_episode_sha256": payload["source_episode_sha256"],
        "checkpoint_file_sha256": payload["checkpoint_file_sha256"],
        "checkpoint_tensor_state_sha256": payload["checkpoint_tensor_state_sha256"],
        "target_kind": payload["target_kind"],
        "mode": mode,
        "max_updates": max_updates,
        "learning_rate": learning_rate,
        "max_abs_residual": max_abs_residual,
        "records": result.records,
        "optimizer_updates": result.optimizer_updates,
        "loss_normalizer": result.loss_normalizer,
        "signed_complete_action_loss": result.signed_complete_action_loss,
        "anchor_kl": result.anchor_kl,
        "residual_l2": result.residual_l2,
        "max_abs_residual_observed": result.max_abs_residual,
        "training_permitted": False,
        "promotion_authority": False,
        "longrun_allowed": False,
        "performance_evidence": False,
    }
    return table_payload, report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=Path, required=True)
    parser.add_argument("--rows-sha256", required=True)
    parser.add_argument("--reference-bundle-sha256", required=True)
    parser.add_argument("--reference-source-list-sha256", required=True)
    parser.add_argument("--mode", choices=("record_normalized", "episode_normalized"), required=True)
    parser.add_argument("--max-updates", type=int, required=True)
    parser.add_argument("--learning-rate", type=float, required=True)
    parser.add_argument("--max-abs-residual", type=float, default=0.25)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report-output", type=Path, required=True)
    args = parser.parse_args(argv)
    payload, report = train_artifact_v1(
        args.rows, expected_rows_sha256=args.rows_sha256,
        reference_bundle_file_sha256=args.reference_bundle_sha256,
        reference_source_list_sha256=args.reference_source_list_sha256,
        mode=args.mode,
        max_updates=args.max_updates, learning_rate=args.learning_rate,
        max_abs_residual=args.max_abs_residual,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=True, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    args.report_output.parent.mkdir(parents=True, exist_ok=True)
    args.report_output.write_text(json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("seed", "mode", "records", "optimizer_updates", "max_abs_residual_observed", "signed_complete_action_loss")}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
