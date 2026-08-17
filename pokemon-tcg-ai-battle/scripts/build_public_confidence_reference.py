#!/usr/bin/env python3
"""Build a frozen public-structure bucket reference from sealed transitions.

The builder intentionally uses only the canonical actor-visible transition
payload.  Envelope fields such as opponent identity and seat are used neither
for grouping nor for output.  The resulting histogram is a diagnostic input
to ``PublicBucketReferenceV1``; it is not a training dataset or an evaluator.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
from mage_ptcg.meta_specialist.dagger_v4 import parse_transition_payload_v4
from mage_ptcg.meta_specialist.public_confidence_ood_v1 import (
    PUBLIC_CONFIDENCE_OOD_SCHEMA_V1,
    _bucket_id,
)


REFERENCE_SCHEMA_V1 = "meta-specialist-public-bucket-reference-v1"
_VALID_PARTITIONS = frozenset({"train", "validation"})


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_args(path: Path, partition: str, rare_count_threshold: int) -> None:
    if not path.is_file():
        raise ValueError(f"transition source must be a regular file: {path}")
    if partition not in _VALID_PARTITIONS:
        raise ValueError(f"partition must be one of {sorted(_VALID_PARTITIONS)}")
    if type(rare_count_threshold) is not int or rare_count_threshold < 0:
        raise ValueError("rare_count_threshold must be a nonnegative int")


def build_public_bucket_reference(
    transitions_path: str | os.PathLike[str],
    *,
    partition: str = "train",
    rare_count_threshold: int = 2,
) -> dict[str, object]:
    """Return a deterministic public bucket histogram for one partition.

    The output contains no opponent, seat, policy, game, or component
    identity.  ``source_sha256`` binds the reference to the exact JSONL bytes.
    """

    path = Path(transitions_path)
    _validate_args(path, partition, rare_count_threshold)
    source_sha256 = _sha256_file(path)
    bucket_counts: dict[str, int] = {}
    transition_count = 0
    prefix_count = 0
    forced_prefix_count = 0
    skipped_transition_count = 0

    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid transition JSON at line {line_number}") from exc
            if type(row) is not dict:
                raise ValueError(f"transition row must be an object at line {line_number}")
            row_partition = row.get("partition")
            if row_partition != partition:
                skipped_transition_count += 1
                continue
            if row.get("schema") != "meta-specialist-v4-dagger-transition-v1":
                raise ValueError(f"unexpected transition schema at line {line_number}")
            raw_transition = row.get("transition")
            try:
                transition = parse_transition_payload_v4(raw_transition)
            except (ValueError, TypeError, KeyError, AttributeError) as exc:
                raise ValueError(f"invalid canonical transition at line {line_number}") from exc
            transition_count += 1
            for prefix in transition.prefix_steps:
                step_input = prefix.step_input
                effective_domain = len(step_input.allowed_semantic_classes) + int(step_input.stop_available)
                if effective_domain < 1:
                    raise ValueError(f"empty public legal domain at line {line_number}")
                bucket = _bucket_id(transition.model_input, step_input, effective_domain)
                bucket_counts[bucket] = bucket_counts.get(bucket, 0) + 1
                prefix_count += 1
                forced_prefix_count += int(effective_domain == 1)

    if transition_count == 0 or prefix_count == 0:
        raise ValueError("selected partition contains no canonical transitions")
    if any(not math.isfinite(float(value)) for value in bucket_counts.values()):
        raise ValueError("bucket counts must be finite")
    return {
        "schema_version": REFERENCE_SCHEMA_V1,
        "bucket_schema_version": PUBLIC_CONFIDENCE_OOD_SCHEMA_V1,
        "source_sha256": source_sha256,
        "partition": partition,
        "rare_count_threshold": rare_count_threshold,
        "transition_count": transition_count,
        "prefix_count": prefix_count,
        "forced_prefix_count": forced_prefix_count,
        "skipped_transition_count": skipped_transition_count,
        "bucket_count": len(bucket_counts),
        "bucket_counts": dict(sorted(bucket_counts.items())),
        "privacy": {
            "uses_opponent_id": False,
            "uses_seat": False,
            "uses_policy_identity": False,
            "uses_hidden_fields": False,
        },
    }


def _write_json_atomic(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=True, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--transitions", required=True, type=Path)
    parser.add_argument("--partition", default="train")
    parser.add_argument("--rare-count-threshold", type=int, default=2)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    payload = build_public_bucket_reference(
        args.transitions,
        partition=args.partition,
        rare_count_threshold=args.rare_count_threshold,
    )
    _write_json_atomic(args.output, payload)
    print(json.dumps({key: payload[key] for key in ("source_sha256", "partition", "transition_count", "prefix_count", "bucket_count")}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
