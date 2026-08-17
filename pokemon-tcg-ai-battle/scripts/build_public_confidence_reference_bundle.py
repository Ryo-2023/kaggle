#!/usr/bin/env python3
"""Build a hash-bound public bucket reference from multiple transition sources.

This is the multi-source companion to ``build_public_confidence_reference.py``.
It deliberately preserves the caller-provided source order (for example,
seed0 then seed1) while aggregating only public bucket histograms.  Source
paths, opponent identities, seats, policy identities, game IDs, and component
IDs are never emitted.  Each source's full-file SHA256 is emitted and the
ordered source list has a second SHA256 binding, so a downstream consumer can
verify both membership and order without receiving private envelope fields.

The result is a diagnostic reference only.  It is not a training dataset,
teacher artifact, evaluator, or promotion authority.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Sequence

from scripts.build_public_confidence_reference import build_public_bucket_reference


REFERENCE_BUNDLE_SCHEMA_V1 = "meta-specialist-public-bucket-reference-bundle-v1"


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _source_list_sha256(partition: str, source_list: list[dict[str, object]]) -> str:
    """Hash only the public, ordered source manifest.

    The manifest contains ordinals and source hashes, but no paths or
    transition envelope metadata.  Keeping this helper public-by-behaviour
    (rather than exposing it as a CLI detail) makes verification deterministic
    for tests and downstream readers.
    """

    manifest = {"partition": partition, "source_list": source_list}
    return hashlib.sha256(_canonical_json_bytes(manifest)).hexdigest()


def _validate_source_paths(transitions_paths: Sequence[str | os.PathLike[str]]) -> list[Path]:
    if isinstance(transitions_paths, (str, bytes, os.PathLike)):
        raise ValueError("transitions_paths must be a sequence containing at least two sources")
    paths = [Path(path) for path in transitions_paths]
    if len(paths) < 2:
        raise ValueError("bundle requires at least two transition sources")
    for path in paths:
        if not path.is_file():
            raise ValueError(f"transition source must be a regular file: {path}")
    return paths


def build_public_bucket_reference_bundle(
    transitions_paths: Sequence[str | os.PathLike[str]],
    *,
    partition: str = "train",
    rare_count_threshold: int = 2,
) -> dict[str, object]:
    """Return a deterministic public bucket histogram over ordered sources.

    ``transitions_paths`` is intentionally not sorted.  Its order is part of
    the source binding and should be fixed by the experiment manifest (for
    example ``[seed0_train, seed1_train]``).  Each source is parsed by the
    existing single-source builder, preserving its fail-closed validation.
    Duplicate source bytes are rejected so a nominal two-seed bundle cannot
    silently collapse to one dataset.
    """

    paths = _validate_source_paths(transitions_paths)
    source_results: list[dict[str, object]] = []
    seen_source_sha256: set[str] = set()
    for path in paths:
        result = build_public_bucket_reference(
            path,
            partition=partition,
            rare_count_threshold=rare_count_threshold,
        )
        source_sha256 = result["source_sha256"]
        if not isinstance(source_sha256, str):  # pragma: no cover - guarded by source builder
            raise ValueError("single-source reference did not return a source SHA256")
        if source_sha256 in seen_source_sha256:
            raise ValueError("bundle sources must be distinct by full-file SHA256")
        seen_source_sha256.add(source_sha256)
        source_results.append(result)

    source_list = [
        {"ordinal": ordinal, "source_sha256": result["source_sha256"]}
        for ordinal, result in enumerate(source_results)
    ]
    bucket_counts: dict[str, int] = {}
    for result in source_results:
        for bucket, count in result["bucket_counts"].items():
            bucket_counts[bucket] = bucket_counts.get(bucket, 0) + int(count)

    source_stats = [
        {
            "ordinal": ordinal,
            "transition_count": result["transition_count"],
            "prefix_count": result["prefix_count"],
            "forced_prefix_count": result["forced_prefix_count"],
            "skipped_transition_count": result["skipped_transition_count"],
        }
        for ordinal, result in enumerate(source_results)
    ]
    return {
        "schema_version": REFERENCE_BUNDLE_SCHEMA_V1,
        "bucket_schema_version": source_results[0]["bucket_schema_version"],
        "partition": partition,
        "rare_count_threshold": rare_count_threshold,
        "source_count": len(source_results),
        "source_list": source_list,
        "source_list_sha256": _source_list_sha256(partition, source_list),
        "source_stats": source_stats,
        "transition_count": sum(int(result["transition_count"]) for result in source_results),
        "prefix_count": sum(int(result["prefix_count"]) for result in source_results),
        "forced_prefix_count": sum(int(result["forced_prefix_count"]) for result in source_results),
        "skipped_transition_count": sum(int(result["skipped_transition_count"]) for result in source_results),
        "bucket_count": len(bucket_counts),
        "bucket_counts": dict(sorted(bucket_counts.items())),
        "privacy": {
            "uses_opponent_id": False,
            "uses_seat": False,
            "uses_policy_identity": False,
            "uses_hidden_fields": False,
        },
        "promotion_authority": False,
    }


def _write_json_atomic(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(_canonical_json_bytes(payload) + b"\n")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--transitions",
        action="append",
        required=True,
        help="transition JSONL source; repeat in fixed order (seed0, then seed1)",
    )
    parser.add_argument("--partition", default="train")
    parser.add_argument("--rare-count-threshold", type=int, default=2)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    payload = build_public_bucket_reference_bundle(
        args.transitions,
        partition=args.partition,
        rare_count_threshold=args.rare_count_threshold,
    )
    _write_json_atomic(args.output, payload)
    print(
        json.dumps(
            {
                key: payload[key]
                for key in (
                    "source_list_sha256",
                    "source_count",
                    "transition_count",
                    "prefix_count",
                    "bucket_count",
                )
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
