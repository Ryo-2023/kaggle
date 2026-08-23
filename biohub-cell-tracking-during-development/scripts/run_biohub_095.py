#!/usr/bin/env python3
"""Freeze and run the immutable Biohub 0.95 Recipe C protocol."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
CANONICAL_CONFIG_PATH = PROJECT_ROOT / "configs" / "biohub_095_recipe_c.yaml"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from biohub.recipe_c.protocol import (  # noqa: E402
    PANEL_V1,
    ExperimentSpec,
    build_selection_lock,
    write_selection_lock,
)
from biohub.recipe_c.runner import RECIPE_C_SMOKE_FRAMES  # noqa: E402
from biohub.recipe_c.source import (  # noqa: E402
    validate_source_checkout,
    validate_support_artifacts,
)
from biohub.recipe_c.staging import stage_recipe_c_runtime  # noqa: E402


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


def _dry_run(args: argparse.Namespace) -> int:
    stage = stage_recipe_c_runtime(
        _resolve_project_path(args.source),
        _resolve_project_path(args.primary_support),
        _resolve_project_path(args.secondary_support),
        _resolve_project_path(args.destination),
        _resolve_project_path(args.selection_lock),
    )
    try:
        print(json.dumps(stage.receipt, sort_keys=True, separators=(",", ":")))
    finally:
        stage.close()
    return 0


def _infer(args: argparse.Namespace) -> int:
    """Run the GT-free Recipe C adapter against a fresh runtime stage."""

    from dataclasses import asdict

    from biohub.recipe_c.runner import run_recipe_c_inference

    stage = stage_recipe_c_runtime(
        _resolve_project_path(args.source),
        _resolve_project_path(args.primary_support),
        _resolve_project_path(args.secondary_support),
        _resolve_project_path(args.stage_destination),
        _resolve_project_path(args.selection_lock),
    )
    try:
        receipt = run_recipe_c_inference(
            _resolve_project_path(args.image_root),
            tuple(args.sample_id),
            stage,
            _resolve_project_path(args.selection_lock),
            _resolve_project_path(args.output_root),
            args.max_frames,
        )
        print(json.dumps(asdict(receipt), sort_keys=True))
        return 0
    finally:
        # The runner owns the live lease through READY/FAILED finalization;
        # close is idempotent for the preflight-error path as well.
        stage.close()


def _require_fresh_target(path: Path, *, label: str) -> Path:
    target = Path(path)
    if target.exists() or target.is_symlink():
        raise ValueError(f"{label} must be fresh: {target}")
    return target


def _reject_symlinked_parent(path: Path, *, label: str) -> None:
    absolute = Path(path).absolute()
    current = Path(absolute.anchor)
    for part in absolute.parts[1:-1]:
        current /= part
        if current.is_symlink():
            raise ValueError(f"{label} has a symlinked parent: {current}")


def _infer_panel(args: argparse.Namespace) -> int:
    """Run one fixed, full-length, GT-free PANEL_V1 inference."""

    from biohub.recipe_c.runner import run_recipe_c_inference

    stage_destination = _resolve_project_path(args.stage_destination)
    output_root = _resolve_project_path(args.output_root)
    _reject_symlinked_parent(stage_destination, label="stage destination")
    _reject_symlinked_parent(output_root, label="output root")
    _require_fresh_target(stage_destination, label="stage destination")
    _require_fresh_target(output_root, label="output root")
    stage_absolute = stage_destination.absolute()
    output_absolute = output_root.absolute()
    if (
        stage_absolute == output_absolute
        or stage_absolute in output_absolute.parents
        or output_absolute in stage_absolute.parents
    ):
        raise ValueError("stage destination and output root must not overlap")
    stage = stage_recipe_c_runtime(
        _resolve_project_path(args.source),
        _resolve_project_path(args.primary_support),
        _resolve_project_path(args.secondary_support),
        stage_destination,
        _resolve_project_path(args.selection_lock),
    )
    try:
        receipt = run_recipe_c_inference(
            _resolve_project_path(args.image_root),
            PANEL_V1,
            stage,
            _resolve_project_path(args.selection_lock),
            output_root,
            None,
        )
        print(json.dumps(asdict(receipt), sort_keys=True))
        return 0
    finally:
        stage.close()


def _evaluate_panel(args: argparse.Namespace) -> int:
    """Evaluate the locked panel after GT-free prediction handoff."""

    from biohub.recipe_c import evaluation

    output = _resolve_project_path(args.output)
    gt_root = _resolve_project_path(args.gt_root)
    ground_truth_map = {sample: gt_root / f"{sample}.geff" for sample in PANEL_V1}
    output_targets = [output, *(output.parent / f"{sample}.metric_receipt.json" for sample in PANEL_V1)]
    if len({target.absolute() for target in output_targets}) != len(output_targets):
        raise ValueError("panel receipt and sample receipt targets must be distinct")
    for target in output_targets:
        safe_target = evaluation._safe_write_target(target)
        _require_fresh_target(safe_target, label="evaluation output")

    result = evaluation.evaluate_panel(
        selection_lock=_resolve_project_path(args.selection_lock),
        prediction_root=_resolve_project_path(args.prediction_root),
        output=output,
        inference_receipt=_resolve_project_path(args.inference_receipt),
        ground_truth_map=ground_truth_map,
        reproduction_command=args.reproduction_command,
    )
    if not isinstance(result, Mapping):
        raise ValueError("evaluate_panel must return a mapping")
    try:
        encoded = json.dumps(result, sort_keys=True)
    except TypeError as exc:
        raise ValueError("evaluate_panel returned a non-JSON result") from exc
    print(encoded)
    return 0 if result.get("status") == "READY" and result.get("panel_status") == "READY" else 2


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
    dry_run = subparsers.add_parser("dry-run", help="stage a validated runtime without inference or evaluation")
    dry_run.add_argument("--source", type=Path, required=True)
    dry_run.add_argument("--primary-support", type=Path, required=True)
    dry_run.add_argument("--secondary-support", type=Path, required=True)
    dry_run.add_argument("--selection-lock", type=Path, required=True)
    dry_run.add_argument("--destination", type=Path, required=True)
    dry_run.set_defaults(handler=_dry_run)
    infer = subparsers.add_parser("infer", help="run GT-free Recipe C inference and persist GEFF predictions")
    infer.add_argument("--source", type=Path, required=True)
    infer.add_argument("--primary-support", type=Path, required=True)
    infer.add_argument("--secondary-support", type=Path, required=True)
    infer.add_argument("--selection-lock", type=Path, required=True)
    infer.add_argument("--stage-destination", type=Path, required=True)
    infer.add_argument("--image-root", type=Path, required=True)
    infer.add_argument("--output-root", type=Path, required=True)
    infer.add_argument("--sample-id", action="append", default=[])
    infer.add_argument(
        "--max-frames",
        type=int,
        default=None,
        choices=(RECIPE_C_SMOKE_FRAMES,),
        help=f"fixed GT-free smoke horizon ({RECIPE_C_SMOKE_FRAMES} frames); omit for full inference",
    )
    infer.set_defaults(handler=_infer)
    infer_panel = subparsers.add_parser(
        "infer-panel", help="run one fixed full-length GT-free PANEL_V1 inference"
    )
    infer_panel.add_argument("--source", type=Path, required=True)
    infer_panel.add_argument("--primary-support", type=Path, required=True)
    infer_panel.add_argument("--secondary-support", type=Path, required=True)
    infer_panel.add_argument("--selection-lock", type=Path, required=True)
    infer_panel.add_argument("--stage-destination", type=Path, required=True)
    infer_panel.add_argument("--image-root", type=Path, required=True)
    infer_panel.add_argument("--output-root", type=Path, required=True)
    infer_panel.set_defaults(handler=_infer_panel)
    evaluate_panel = subparsers.add_parser(
        "evaluate-panel", help="evaluate a fixed PANEL_V1 prediction handoff"
    )
    evaluate_panel.add_argument("--selection-lock", type=Path, required=True)
    evaluate_panel.add_argument("--prediction-root", type=Path, required=True)
    evaluate_panel.add_argument("--inference-receipt", type=Path, required=True)
    evaluate_panel.add_argument("--gt-root", type=Path, required=True)
    evaluate_panel.add_argument("--output", type=Path, required=True)
    evaluate_panel.add_argument("--reproduction-command", default="")
    evaluate_panel.set_defaults(handler=_evaluate_panel)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        return args.handler(args)
    except (
        FileNotFoundError,
        OSError,
        ValueError,
        RuntimeError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
    ) as exc:
        print(f"{args.command} failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
