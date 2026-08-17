#!/usr/bin/env python3
"""Apply the fixed V4 held-out/action promotion gate to sealed artifacts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from mage_ptcg.meta_specialist.v4_promotion_gate import evaluate_v4_promotion_gate  # noqa: E402


def _write_atomic(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(payload, sort_keys=True, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    with tempfile.NamedTemporaryFile("wb", dir=path.parent, prefix=f".{path.name}.", delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(raw)
        handle.flush()
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", nargs=2, required=True, metavar="JSON")
    parser.add_argument("--baseline", nargs=2, required=True, metavar="JSON")
    parser.add_argument("--imitation", required=True, metavar="JSON")
    parser.add_argument("--output", required=True, metavar="JSON")
    args = parser.parse_args()
    result = evaluate_v4_promotion_gate(args.candidate, args.baseline, imitation_path=args.imitation)
    _write_atomic(Path(args.output), result)
    print(json.dumps({"decision": result["decision"], "reasons": result["reasons"], "output": str(Path(args.output).resolve())}, ensure_ascii=False))
    return 0 if result["decision"] == "PROMOTION_READY" else 2


if __name__ == "__main__":
    raise SystemExit(main())

