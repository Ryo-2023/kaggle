#!/usr/bin/env python3
"""Build a strict candidate-terminal-WDL META_TRAIN iteration-1 schedule."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

_ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(_ROOT), str(_ROOT / "src")]

from mage_ptcg.meta_specialist.outcome_only_iteration1_schedule_v1 import (  # noqa: E402
    build_outcome_only_iteration1_schedule_v1,
)


def _write_new(path: Path, value: object) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temp.open("xb") as handle:
            handle.write(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temp, path)
        except FileExistsError as exc:
            raise FileExistsError(f"refusing to replace existing schedule: {path}") from exc
    finally:
        temp.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=_ROOT)
    parser.add_argument("--candidate-ledger", type=Path, required=True)
    parser.add_argument("--confirmation", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    artifact = build_outcome_only_iteration1_schedule_v1(
        repo_root=args.repo_root,
        candidate_ledger_path=args.candidate_ledger,
        confirmation_path=args.confirmation,
        quota=96,
    )
    _write_new(args.output, artifact["manifest"])
    print(json.dumps({
        "output": str(args.output.resolve()),
        "schedule_sha256": artifact["manifest"]["schedule_sha256"],
        "candidate_rows": artifact["manifest"]["summary"]["candidate_rows"],
        "quota": artifact["manifest"]["summary"]["quota_sum"],
        "heldout_exposure": artifact["manifest"]["summary"]["heldout_exposure"],
        "ready_for_evaluation": artifact["manifest"]["ready_for_evaluation"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
