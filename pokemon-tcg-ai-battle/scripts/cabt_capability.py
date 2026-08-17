"""Diagnose whether the active Python interpreter can load official cabt.

The report deliberately contains only normalized interpreter/package paths and
environment names. It never serializes arbitrary import exceptions or shell
environment values.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib
import importlib.metadata
import io
import json
import os
from collections.abc import Callable, Mapping
from pathlib import Path
import sys
from typing import Any


EXPECTED_PACKAGE = "kaggle-environments==1.32.0"
REQUESTED_ENVIRONMENT = "cabt"
# Official cabt does not expose an engine RNG seed control through this runner.
ENGINE_SEED_SUPPORTED = False


def _normalized_python(executable: str) -> str:
    path = Path(executable)
    if os.environ.get("VIRTUAL_ENV") or ".venv" in path.parts:
        return "<venv>/bin/" + path.name
    return str(path) if str(path).startswith("/usr/bin/") else path.name


def _normalized_package_path(module_file: object) -> str | None:
    if not isinstance(module_file, str):
        return None
    parts = Path(module_file).parts
    if "site-packages" in parts:
        index = parts.index("site-packages")
        return str(Path("<site-packages>", *parts[index + 1 :]).parent)
    return Path(module_file).name


def _report(
    *,
    status: str,
    reason_code: str,
    package_version: str | None,
    package_path: str | None,
    available: list[str] | None = None,
    import_error_type: str | None = None,
    missing: list[str] | None = None,
) -> dict[str, object]:
    return {
        "status": status,
        "reason_code": reason_code,
        "python_executable": _normalized_python(sys.executable),
        "python_version": sys.version.split()[0],
        "kaggle_environments_version": package_version,
        "kaggle_environments_path": package_path,
        "requested_environment": REQUESTED_ENVIRONMENT,
        "engine_seed_supported": ENGINE_SEED_SUPPORTED,
        "available_environments": sorted(available or []),
        "plugin_path": None,
        "asset_dir": None,
        "import_error_type": import_error_type,
        "missing_requirements": sorted(missing or []),
        "actual_execution_allowed": status == "READY",
    }


def diagnose_cabt_capability(
    *,
    module_loader: Callable[[str], Any] = importlib.import_module,
    version_loader: Callable[[str], str] = importlib.metadata.version,
) -> dict[str, object]:
    """Return a deterministic, privacy-safe capability report for cabt."""
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            module = module_loader("kaggle_environments")
    except ModuleNotFoundError:
        return _report(
            status="UNAVAILABLE",
            reason_code="PACKAGE_NOT_INSTALLED",
            package_version=None,
            package_path=None,
            missing=[EXPECTED_PACKAGE],
        )
    except BaseException as exc:
        return _report(
            status="UNAVAILABLE",
            reason_code="PACKAGE_IMPORT_FAILED",
            package_version=None,
            package_path=None,
            import_error_type=type(exc).__name__,
        )

    try:
        package_version = version_loader("kaggle-environments")
    except importlib.metadata.PackageNotFoundError:
        return _report(
            status="UNAVAILABLE",
            reason_code="PACKAGE_IMPORT_FAILED",
            package_version=None,
            package_path=_normalized_package_path(getattr(module, "__file__", None)),
            import_error_type="PackageNotFoundError",
        )
    package_path = _normalized_package_path(getattr(module, "__file__", None))
    if package_version != EXPECTED_PACKAGE.split("==", 1)[1]:
        return _report(
            status="UNAVAILABLE",
            reason_code="VERSION_INCOMPATIBLE",
            package_version=package_version,
            package_path=package_path,
        )

    environments = getattr(module, "environments", None)
    if not isinstance(environments, Mapping):
        return _report(
            status="UNAVAILABLE",
            reason_code="PLUGIN_NOT_REGISTERED",
            package_version=package_version,
            package_path=package_path,
        )
    available = [str(name) for name in environments]
    if REQUESTED_ENVIRONMENT not in environments:
        return _report(
            status="UNAVAILABLE",
            reason_code="PLUGIN_NOT_REGISTERED",
            package_version=package_version,
            package_path=package_path,
            available=available,
        )

    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            module.make(REQUESTED_ENVIRONMENT)
    except BaseException as exc:
        return _report(
            status="UNAVAILABLE",
            reason_code="COMPETITION_ASSET_MISSING",
            package_version=package_version,
            package_path=package_path,
            available=available,
            import_error_type=type(exc).__name__,
        )
    return _report(
        status="READY",
        reason_code="READY",
        package_version=package_version,
        package_path=package_path,
        available=available,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="write the JSON report to this path")
    args = parser.parse_args(argv)
    report = diagnose_cabt_capability()
    encoded = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output is None:
        print(encoded, end="")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    return 0 if report["status"] == "READY" else 3


if __name__ == "__main__":
    raise SystemExit(main())
