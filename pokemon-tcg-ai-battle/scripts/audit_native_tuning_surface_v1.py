"""Audit direct tuning and native fallback surfaces for selected native pairs."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

_ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(_ROOT), str(_ROOT / "src")]

from mage_ptcg.meta_specialist.native_tuning_surface_v1 import (  # noqa: E402
    audit_native_pair_v1,
    surface_to_dict_v1,
)


DEFAULT_IDS = ("tomatomato_archaludon", "lucifer19_battlecore", "plamen06_steel")


def audit_native_surfaces_v1(
    *,
    pool_root: Path | str = _ROOT / "opponents",
    asset_ids: tuple[str, ...] = DEFAULT_IDS,
) -> dict[str, object]:
    root = Path(pool_root)
    rows = []
    for asset_id in asset_ids:
        main = root / asset_id / "main.py"
        deck = root / asset_id / "deck.csv"
        rows.append(surface_to_dict_v1(audit_native_pair_v1(asset_id, main, deck)))
    payload = {
        "schema_version": "meta-specialist-native-tuning-surface-audit-v1",
        "research_only": True,
        "promotion_authority": False,
        "training_authority": False,
        "submission_authority": False,
        "asset_count": len(rows),
        "assets": rows,
        "notes": [
            "Source and deck bytes are read-only and SHA-bound.",
            "A classification is an implementation observation, not permission to modify or submit the upstream asset.",
            "Native fallback must remain the default for unknown, malformed, illegal, or timed-out override decisions.",
        ],
    }
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pool-root", type=Path, default=_ROOT / "opponents")
    parser.add_argument("--asset-ids", default=",".join(DEFAULT_IDS))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    asset_ids = tuple(item.strip() for item in args.asset_ids.split(",") if item.strip())
    payload = audit_native_surfaces_v1(pool_root=args.pool_root, asset_ids=asset_ids)
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.output.exists():
        raise SystemExit(f"refusing to overwrite existing output: {args.output}")
    args.output.write_text(text, encoding="utf-8")
    print(json.dumps({"output": str(args.output), "sha256": hashlib.sha256(text.encode()).hexdigest()}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

