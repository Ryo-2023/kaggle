"""Run a bounded root-cg package contract smoke in an isolated subprocess.

The CEM coordinator must not import a candidate's native ``cg`` package.  The
native library keeps process-global state, so this helper owns the import and
exits before the coordinator creates CABT evaluation workers.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Mapping, Sequence


_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(1, str(_ROOT / "src"))

SCHEMA = "cg-static-smoke-v1"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _atomic_write_json(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.tmp-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(dict(payload), handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def run_static_smoke(
    *,
    candidate_package: Path | str,
    control_package: Path | str,
    output: Path | str,
) -> dict[str, object]:
    """Import both packages here, execute their no-selection contract, and seal a report."""
    candidate = Path(candidate_package).resolve()
    control = Path(control_package).resolve()
    destination = Path(output).resolve()
    report_path = destination / "static_smoke_report.json"
    base: dict[str, object] = {
        "schema_version": SCHEMA,
        "candidate_package": str(candidate),
        "control_package": str(control),
        "candidate_main_sha256": None,
        "control_main_sha256": None,
        "candidate_deck_sha256": None,
        "control_deck_sha256": None,
        "candidate_agent_contract": "NOT_RUN",
        "status": "FAIL",
    }
    try:
        for package in (candidate, control):
            if not (package / "main.py").is_file() or not (package / "deck.csv").is_file():
                raise ValueError(f"package is incomplete: {package}")
        base.update(
            {
                "candidate_main_sha256": _sha256(candidate / "main.py"),
                "control_main_sha256": _sha256(control / "main.py"),
                "candidate_deck_sha256": _sha256(candidate / "deck.csv"),
                "control_deck_sha256": _sha256(control / "deck.csv"),
            }
        )
        # Importing the arena loader is safe here: this process is dedicated to
        # the smoke and will exit before the coordinator starts CABT workers.
        from scripts import run_root_cg_candidate_arena_v1 as arena

        candidate_module = arena._load_candidate(candidate)
        control_module = arena._load_candidate(control)
        candidate_value = candidate_module.agent({"select": None})
        control_value = control_module.agent({"select": None})
        if candidate_value != control_value:
            raise ValueError("candidate failed the P1 deck/fallback contract")
        base["candidate_agent_contract"] = "PASS"
        base["status"] = "PASS"
    except BaseException as exc:  # noqa: BLE001 - report and fail closed
        base["error"] = f"{type(exc).__name__}: {exc}"
    _atomic_write_json(report_path, base)
    return base


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-package", type=Path, required=True)
    parser.add_argument("--control-package", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = run_static_smoke(
        candidate_package=args.candidate_package,
        control_package=args.control_package,
        output=args.output,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report.get("status") == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["SCHEMA", "main", "run_static_smoke"]
