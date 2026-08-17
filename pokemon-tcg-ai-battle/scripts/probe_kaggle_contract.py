"""Safely report what is known about the Kaggle competition contract.

The probe never prints credential values and never submits anything.  It does
not infer a submission method or accepted artifact type from partial results.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from typing import Callable


def probe(
    competition: str,
    *,
    which: Callable[[str], str | None] = shutil.which,
    environ: dict[str, str] | None = None,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, object]:
    environment = os.environ if environ is None else environ
    result: dict[str, object] = {
        "competition": competition,
        "submission_method": "UNKNOWN",
        "archive_type": "UNKNOWN",
        "rules_acceptance": "UNKNOWN",
        "credential_values_logged": False,
    }
    executable = which("kaggle")
    if executable is None:
        return {**result, "status": "CLI_MISSING", "competition_access": "UNKNOWN"}
    if not (environment.get("KAGGLE_USERNAME") and environment.get("KAGGLE_KEY")):
        return {**result, "status": "AUTH_MISSING", "competition_access": "UNKNOWN"}
    completed = run(
        [executable, "competitions", "files", competition, "--csv"],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    if completed.returncode == 0:
        return {**result, "status": "ACCESSIBLE", "competition_access": "CONFIRMED"}
    # Avoid returning CLI stderr because it can contain user-specific paths.
    return {**result, "status": "RULES_OR_ACCESS_REQUIRED", "competition_access": "UNKNOWN"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--competition", default="pokemon-tcg-ai-battle")
    args = parser.parse_args(argv)
    print(json.dumps(probe(args.competition), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
