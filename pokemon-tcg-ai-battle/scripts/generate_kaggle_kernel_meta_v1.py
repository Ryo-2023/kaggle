#!/usr/bin/env python3
"""Seal locally downloaded Kaggle kernel outputs for research-only CABT."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from mage_ptcg.opponent_ingest.kaggle_kernel_meta_v1 import (  # noqa: E402
    KernelSourceSpec,
    seal_kaggle_kernel_meta_v1,
    validate_kernel_specs,
)


def _path(value: object, *, base: Path) -> Path:
    path = Path(str(value))
    return (base / path if not path.is_absolute() else path).resolve()


def _load_config(path: Path) -> tuple[dict[str, object], list[KernelSourceSpec]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"config is unreadable: {path}") from exc
    if not isinstance(raw, dict):
        raise ValueError("config root must be an object")
    sources = raw.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ValueError("config.sources must be a non-empty list")
    specs = [KernelSourceSpec.from_mapping(item, base_dir=ROOT) for item in sources if isinstance(item, dict)]
    if len(specs) != len(sources):
        raise ValueError("every config.sources entry must be an object")
    return raw, specs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, help="override config.output_root")
    parser.add_argument("--dry-run", action="store_true", help="verify local tar hashes without writing or importing")
    args = parser.parse_args(argv)

    config_path = args.config.resolve()
    raw, specs = _load_config(config_path)
    if args.dry_run:
        report = validate_kernel_specs(specs)
        report["config_path"] = str(config_path)
        print(json.dumps(report, ensure_ascii=False, sort_keys=True, default=str))
        return 0

    pool_manifest = raw.get("pool_manifest")
    output_root = args.output if args.output is not None else raw.get("output_root")
    if not pool_manifest or not output_root:
        raise ValueError("config.pool_manifest and config.output_root are required")
    scan_roots_raw = raw.get("scan_roots", [])
    if not isinstance(scan_roots_raw, list):
        raise ValueError("config.scan_roots must be a list")
    report = seal_kaggle_kernel_meta_v1(
        specs=specs,
        pool_manifest_path=_path(pool_manifest, base=ROOT),
        output_root=_path(output_root, base=ROOT),
        source_epoch=str(raw.get("source_epoch", "")),
        seed_namespace=str(raw.get("seed_namespace", "")),
        scan_roots=[_path(value, base=ROOT) for value in scan_roots_raw],
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, RuntimeError, FileExistsError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)
