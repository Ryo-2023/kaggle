"""Build and immediately hash-verify the closed derived-teacher catalog."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Sequence


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from mage_ptcg.meta_specialist.derived_teacher_catalog_v1 import (  # noqa: E402
    build_derived_teacher_catalog_v1,
    verify_derived_teacher_catalog_v1,
)


DEFAULT_OUTPUT = ROOT / "runs/final-sprint-autonomous/derived-teacher-catalog-v1/catalog.json"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--replace", action="store_true", help="replace an existing catalog only after full source verification")
    args = parser.parse_args(argv)
    payload = build_derived_teacher_catalog_v1(ROOT, output_path=args.output, replace_existing=args.replace)
    verified = verify_derived_teacher_catalog_v1(args.output, ROOT)
    if verified != payload:
        raise RuntimeError("catalog verification payload mismatch")
    print(json.dumps({
        "catalog_path": str(args.output.resolve()),
        "catalog_file_sha256": hashlib.sha256(args.output.read_bytes()).hexdigest(),
        "catalog_sha256": payload["catalog_sha256"],
        "teachers": [row["teacher_id"] for row in payload["teachers"]],
        "verification": "PASS",
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
