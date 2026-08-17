#!/usr/bin/env python3
"""Build a zero-init coarse public residual table from sealed train screens.

The output is an identity/coverage artifact only.  It enumerates public
bucket/action keys from the fixed train sources and assigns exact zero
residuals, so a downstream runtime smoke can measure coarse gate coverage
without pretending to have learned a performance candidate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from mage_ptcg.meta_specialist.coarse_public_residual_gate_v1 import semantic_action_key_v1
from mage_ptcg.meta_specialist.dagger_v4 import parse_transition_payload_v4
from mage_ptcg.meta_specialist.public_confidence_ood_v1 import _bucket_id


SCHEMA = "specialist-coarse-public-residual-table-v1"
_KEYS = {
    "component_id", "env_seed", "episode_group", "game_id", "opponent_id", "partition",
    "schema", "seat", "transition", "transition_index",
}


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def build_zero_table_v1(sources: tuple[Path, ...], *, bundle_sha256: str, source_list_sha256: str) -> dict[str, object]:
    if len(sources) < 2 or len({ _sha(path) for path in sources }) != len(sources):
        raise ValueError("zero table requires at least two distinct source files")
    table: dict[str, dict[str, float]] = {}
    prefix_count = 0
    for source in sources:
        with source.open("rb") as handle:
            for line_number, raw in enumerate(handle, start=1):
                if not raw.strip():
                    continue
                row = json.loads(raw.decode("utf-8"))
                if type(row) is not dict or set(row) != _KEYS or row.get("schema") != "meta-specialist-v4-dagger-transition-v1":
                    raise ValueError(f"source line {line_number} has an open schema")
                if row.get("partition") != "train":
                    continue
                transition = parse_transition_payload_v4(row["transition"])
                for prefix in transition.prefix_steps:
                    effective_domain = len(prefix.step_input.allowed_semantic_classes) + int(prefix.step_input.stop_available)
                    if effective_domain < 1:
                        raise ValueError("source has an empty effective domain")
                    bucket = _bucket_id(transition.model_input, prefix.step_input, effective_domain)
                    actions = table.setdefault(bucket, {})
                    for candidate in prefix.step_input.allowed_semantic_classes:
                        actions[semantic_action_key_v1(candidate.semantic_row)] = 0.0
                    prefix_count += 1
    if not table or prefix_count < 1:
        raise ValueError("sources contain no train prefix rows")
    return {
        "schema_version": SCHEMA,
        "reference_bundle_file_sha256": bundle_sha256,
        "reference_source_list_sha256": source_list_sha256,
        "max_abs_residual": 0.25,
        "residual_by_bucket_action": {
            bucket: dict(sorted(actions.items())) for bucket, actions in sorted(table.items())
        },
        "stop_residual_by_bucket": {},
        "prefix_count": prefix_count,
        "training_permitted": False,
        "promotion_authority": False,
        "longrun_allowed": False,
        "performance_evidence": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", action="append", required=True)
    parser.add_argument("--bundle-sha256", required=True)
    parser.add_argument("--source-list-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    payload = build_zero_table_v1(
        tuple(Path(path) for path in args.source),
        bundle_sha256=args.bundle_sha256,
        source_list_sha256=args.source_list_sha256,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=True, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: payload[key] for key in ("schema_version", "prefix_count", "training_permitted", "performance_evidence")}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
