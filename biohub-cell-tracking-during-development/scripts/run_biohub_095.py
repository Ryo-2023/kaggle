#!/usr/bin/env python3
"""Freeze the immutable Biohub 0.95 Recipe C selection lock.

Only the pre-registration ``freeze`` command belongs in this task.  Inference
and evaluation deliberately live in later task modules, so this CLI cannot
accidentally open ground truth or select a panel dynamically.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
CANONICAL_CONFIG_PATH = PROJECT_ROOT / "configs" / "biohub_095_recipe_c.yaml"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from biohub.recipe_c.protocol import (  # noqa: E402
    ExperimentSpec,
    build_selection_lock,
    write_selection_lock,
)
from biohub.recipe_c.source import (  # noqa: E402
    validate_source_checkout,
    validate_support_artifacts,
)


def _git_commit_and_clean() -> str:
    common = ["git", "-C", str(PROJECT_ROOT)]
    environment = os.environ.copy()
    environment["GIT_OPTIONAL_LOCKS"] = "0"
    head = subprocess.run(
        [*common, "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    if head.returncode != 0 or not head.stdout.strip():
        raise ValueError("the campaign checkout HEAD could not be read")
    status = subprocess.run(
        [*common, "status", "--porcelain=v1", "--untracked-files=all", "--ignored=no"],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    if status.returncode != 0:
        raise ValueError("the campaign checkout status could not be read")
    if status.stdout.strip():
        raise ValueError("the campaign checkout must be clean before freeze")
    index = subprocess.run(
        [*common, "ls-files", "-v", "-z"],
        check=False,
        capture_output=True,
        env=environment,
    )
    if index.returncode != 0:
        raise ValueError("the campaign checkout index could not be read")
    for entry in index.stdout.split(b"\0"):
        if entry and chr(entry[0]) != "H":
            relative = entry[2:].decode(errors="replace")
            raise ValueError(f"the campaign checkout index has hidden flags: {relative}")
    return head.stdout.strip()


def _resolve_project_path(path: Path) -> Path:
    return path if path.is_absolute() else PROJECT_ROOT / path


def _resolve_config_path(path: Path) -> Path:
    """Require the one pinned project config before building any lock."""

    candidate = path if path.is_absolute() else PROJECT_ROOT / path
    canonical = CANONICAL_CONFIG_PATH
    if ".." in candidate.parts or candidate.absolute() != canonical.absolute():
        raise ValueError(f"--config must be the canonical project config: {canonical}")
    return canonical


def _experiment_from_args(args: argparse.Namespace) -> ExperimentSpec:
    return ExperimentSpec(
        experiment_id=args.experiment_id,
        method_family=args.method_family,
        hypothesis=args.hypothesis,
        expected_gain=args.expected_gain,
        cost=args.cost,
        risk=args.risk,
        novelty=args.novelty,
        changes=args.changes,
        control_id=args.control_id,
        acceptance_criteria=args.acceptance_criteria,
        prior_evidence_receipt_hash=args.prior_evidence_receipt_hash,
    )


def _freeze(args: argparse.Namespace) -> int:
    code_commit = _git_commit_and_clean()
    source_path = _resolve_project_path(args.source)
    primary_support_path = _resolve_project_path(args.primary_support)
    secondary_support_path = _resolve_project_path(args.secondary_support)
    config_path = _resolve_config_path(args.config)
    output_path = _resolve_project_path(args.output)
    prior_receipts = [_resolve_project_path(path) for path in args.prior_evaluation_receipt]
    if prior_receipts and args.prior_evidence_receipt_hash is None:
        raise ValueError("--prior-evidence-receipt-hash is required with prior receipts")
    source_receipt = validate_source_checkout(source_path)
    support_receipt = validate_support_artifacts(primary_support_path, secondary_support_path)
    # Both validators emit the same direct contract field names.  Merging only
    # those receipts keeps the protocol independent of artifact filesystem paths.
    merged_receipt = dict(source_receipt)
    for key in (
        "predictor_relative_path",
        "predictor_sha256",
        "primary_checkpoint_relative_path",
        "primary_checkpoint_sha256",
        "secondary_checkpoint_relative_path",
        "secondary_checkpoint_sha256",
        "secondary_staging_relative_path",
        "primary_dataset",
        "primary_dataset_version",
        "primary_dataset_license",
        "secondary_dataset",
        "secondary_dataset_version",
        "secondary_dataset_license",
    ):
        if key in support_receipt:
            merged_receipt[key] = support_receipt[key]
    payload = build_selection_lock(
        merged_receipt,
        config_path,
        code_commit,
        args.requested_device,
        _experiment_from_args(args),
        prior_receipts,
    )
    output = write_selection_lock(output_path, payload)
    print(
        json.dumps(
            {"selection_lock_id": payload["selection_lock_id"], "output": output.name},
            sort_keys=True,
        ),
    )
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Freeze the Biohub 0.95 Recipe C selection lock.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    freeze = subparsers.add_parser("freeze", help="validate pinned assets and create selection_lock.json once")
    freeze.add_argument("--source", type=Path, required=True)
    freeze.add_argument("--primary-support", type=Path, required=True)
    freeze.add_argument("--secondary-support", type=Path, required=True)
    freeze.add_argument(
        "--config",
        type=Path,
        default=PROJECT_ROOT / "configs" / "biohub_095_recipe_c.yaml",
    )
    freeze.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "artifacts" / "biohub_095" / "selection_lock.json",
    )
    freeze.add_argument("--device", "--requested-device", dest="requested_device", default="auto")
    freeze.add_argument("--prior-evaluation-receipt", type=Path, action="append", default=[])
    freeze.add_argument("--prior-evidence-receipt-hash")
    freeze.add_argument("--experiment-id", required=True)
    freeze.add_argument("--method-family", required=True)
    freeze.add_argument("--hypothesis", required=True)
    freeze.add_argument("--expected-gain", type=float, required=True)
    freeze.add_argument("--cost", required=True)
    freeze.add_argument("--risk", required=True)
    freeze.add_argument("--novelty", required=True)
    freeze.add_argument("--changes", required=True)
    freeze.add_argument("--control-id", required=True)
    freeze.add_argument("--acceptance-criteria", required=True)
    freeze.set_defaults(handler=_freeze)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        return args.handler(args)
    except (FileNotFoundError, OSError, ValueError) as exc:
        print(f"freeze failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
