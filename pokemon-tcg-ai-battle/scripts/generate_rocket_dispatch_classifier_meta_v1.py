#!/usr/bin/env python3
"""Seal the bounded Rocket public-card classifier meta v1 pool."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Mapping

from mage_ptcg.opponent_ingest.rocket_dispatch_classifier_meta_v1 import (
    RocketDispatchClassifierMetaError,
    seal_rocket_dispatch_classifier_meta_v1,
)


ROOT = Path(__file__).resolve().parents[1]


def _path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--p1-package", type=Path, required=True)
    parser.add_argument("--current-pool-manifest", type=Path, default=None)
    parser.add_argument("--scan-root", action="append", type=Path, default=[])
    args = parser.parse_args(argv)

    try:
        config = json.loads(args.config.read_text(encoding="utf-8"))
        if not isinstance(config, Mapping):
            raise RocketDispatchClassifierMetaError("config must be a JSON object")
        report = seal_rocket_dispatch_classifier_meta_v1(
            base_root=_path(str(config["base_root"])),
            output_root=_path(str(config["output_root"])),
            source_epoch=str(config["source_epoch"]),
            seed_namespace=str(config["seed_namespace"]),
            p1_package=_path(args.p1_package),
            variants=tuple(str(item) for item in config["variants"]),
            split_by_variant={
                str(key): str(value)
                for key, value in dict(config["split_by_variant"]).items()
            },
            current_pool_manifest=(
                _path(args.current_pool_manifest)
                if args.current_pool_manifest is not None
                else None
            ),
            scan_roots=tuple(_path(path) for path in args.scan_root),
        )
    except (
        KeyError,
        TypeError,
        ValueError,
        OSError,
        json.JSONDecodeError,
        RocketDispatchClassifierMetaError,
    ) as exc:
        print(json.dumps({"status": "ERROR", "error": str(exc)}, ensure_ascii=False, sort_keys=True))
        return 2
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
