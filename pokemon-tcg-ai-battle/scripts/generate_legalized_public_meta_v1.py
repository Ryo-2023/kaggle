#!/usr/bin/env python3
"""Generate research-only public-policy sources with explicit deck repairs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from mage_ptcg.opponent_ingest.legalized_public_meta_v1 import (  # noqa: E402
    DeckRepairSpec,
    LegalizedPublicMetaError,
    seal_legalized_public_meta_v1,
)


def _path(value: object, *, base: Path) -> Path:
    path = Path(str(value))
    return (base / path if not path.is_absolute() else path).resolve()


def _load(path: Path) -> tuple[dict[str, object], tuple[DeckRepairSpec, ...]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"config is unreadable: {path}") from exc
    if not isinstance(raw, dict):
        raise ValueError("config root must be an object")
    source_rows = raw.get("sources")
    if not isinstance(source_rows, list) or not source_rows:
        raise ValueError("config.sources must be a non-empty list")
    specs: list[DeckRepairSpec] = []
    for row in source_rows:
        if not isinstance(row, dict):
            raise ValueError("every source must be an object")
        replacements = row.get("replacements")
        if not isinstance(replacements, list) or not replacements:
            raise ValueError(f"{row.get('candidate_id')}: replacements must be a non-empty list")
        specs.append(
            DeckRepairSpec(
                candidate_id=str(row.get("candidate_id", "")),
                source_root=_path(row.get("source_root", ""), base=ROOT),
                replacements=tuple(item for item in replacements if isinstance(item, dict)),
                source_ref=str(row.get("source_ref", "public_kaggle_kernel/unknown")),
                source_commit=str(row.get("source_commit", "unknown")),
                source_url=str(row.get("source_url", "")),
                entrypoint_adapter=str(row.get("entrypoint_adapter", "")),
            )
        )
    return raw, tuple(specs)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, help="override config.output_root")
    args = parser.parse_args(argv)
    config_path = args.config.resolve()
    raw, specs = _load(config_path)
    pool_manifest = raw.get("current_pool_manifest")
    output_root = args.output if args.output is not None else raw.get("output_root")
    if not pool_manifest or not output_root:
        raise ValueError("config.current_pool_manifest and config.output_root are required")
    scan_roots = raw.get("scan_roots", [])
    if not isinstance(scan_roots, list):
        raise ValueError("config.scan_roots must be a list")
    report = seal_legalized_public_meta_v1(
        specs=specs,
        current_pool_manifest=_path(pool_manifest, base=ROOT),
        output_root=_path(output_root, base=ROOT),
        source_epoch=str(raw.get("source_epoch", "")),
        seed_namespace=str(raw.get("seed_namespace", "")),
        scan_roots=tuple(_path(value, base=ROOT) for value in scan_roots),
    )
    print(json.dumps({**report, "config_path": str(config_path)}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, LegalizedPublicMetaError, FileExistsError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)
