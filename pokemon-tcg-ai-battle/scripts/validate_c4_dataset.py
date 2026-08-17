"""Validate a collected C4 run: rule-bc-v1 rows, private bindings, and split.

Every check is read-only.  A non-zero exit means the run must not be treated as
a PASS: a schema violation, a row/binding mismatch, a duplicate decision, a
non-finite value, or a public-summary privacy violation.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))
if str(REPOSITORY_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from mage_ptcg.dataops import DataOpsError, validate_run  # noqa: E402
from mage_ptcg.student.dataset import DatasetValidationError  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = validate_run(args.run_dir)
    except (DataOpsError, DatasetValidationError, OSError, ValueError) as exc:
        print(f"c4 dataset validation failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
