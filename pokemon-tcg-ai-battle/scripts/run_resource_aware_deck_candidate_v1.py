#!/usr/bin/env python3
"""Research-only ResourceGovernor adapter for deck-candidate experiments.

This wrapper is intentionally smaller than the existing CABT runners.  It
seals one resource warm-up plan and telemetry record, then hands the chosen
worker cap to a later research runner.  It never imports or edits the
production entrypoint, starts a worker, kills a process, grants permission, or
executes a performance experiment by itself.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Mapping, Sequence

from mage_ptcg.meta_specialist.resource_governor_v1 import (
    ResourceBudget,
    ResourceGovernor,
    ResourceSnapshot,
)


ROOT = Path(__file__).resolve().parents[1]
RESOURCE_BUDGET_DEFAULT = ROOT / "configs/meta_specialist/resource_budget_v1.json"
OUTPUT_DEFAULT = ROOT / (
    "runs/final-sprint-autonomous/"
    "resource-aware-deck-candidate-v1-20260813/warmup-telemetry.json"
)
RESOURCE_AWARE_DECK_SCHEMA_V1 = "meta-specialist-resource-aware-deck-candidate-v1"
AUTHORITY_FALSE = {
    "research_only": True,
    "execution_authority": False,
    "training_authority": False,
    "promotion_authority": False,
    "submission_authority": False,
    "longrun_authority": False,
}


class ResourceAwareDeckCandidateError(ValueError):
    """Raised when the research-only adapter contract is not closed."""


def _canonical_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ResourceAwareDeckCandidateError("payload is not canonical JSON") from exc


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _write_no_clobber(path: Path, payload: Mapping[str, object]) -> str:
    if path.exists():
        raise FileExistsError(f"telemetry destination already exists: {path}")
    raw_payload = dict(payload)
    payload_sha = _sha256(_canonical_bytes(raw_payload))
    raw = (
        json.dumps(
            {**raw_payload, "payload_sha256": payload_sha},
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            indent=2,
        )
        + "\n"
    ).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.tmp-", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        # link is the publication point: it cannot overwrite a destination
        # won by a competing writer.
        os.link(temporary, path, follow_symlinks=False)
        try:
            directory_fd = os.open(path.parent, os.O_RDONLY)
        except OSError:
            directory_fd = None
        if directory_fd is not None:
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        os.unlink(temporary)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return payload_sha


def _validate_task_cap(task_cap: int) -> int:
    if type(task_cap) is not int or task_cap < 1:
        raise ResourceAwareDeckCandidateError("task_cap must be a positive integer")
    return task_cap


def build_warmup_plan(
    *,
    budget: ResourceBudget,
    task_cap: int,
    gpu_required: bool = False,
    snapshot: ResourceSnapshot | None = None,
    ramp_workers: Sequence[int] = (1, 2, 4),
) -> dict[str, object]:
    """Return a sealed worker recommendation without starting evaluation."""

    if type(budget) is not ResourceBudget:
        raise ResourceAwareDeckCandidateError("budget must be ResourceBudget")
    task_cap = _validate_task_cap(task_cap)
    if type(gpu_required) is not bool:
        raise ResourceAwareDeckCandidateError("gpu_required must be bool")
    if (
        type(ramp_workers) not in (tuple, list)
        or not ramp_workers
        or any(type(step) is not int or step < 1 for step in ramp_workers)
        or tuple(sorted(set(ramp_workers))) != tuple(ramp_workers)
    ):
        raise ResourceAwareDeckCandidateError("ramp_workers must be a sorted positive sequence")
    governor = ResourceGovernor(budget)
    decision = governor.decide(
        task_cap=task_cap,
        gpu_required=gpu_required,
        snapshot=snapshot,
    )
    safe_workers = int(decision.recommended_workers)
    requested_ramp = list(ramp_workers)
    admitted_ramp = [step for step in requested_ramp if step <= safe_workers]
    admitted = decision.state in {"normal", "warning"}
    if gpu_required and not decision.gpu_admitted:
        admitted = False
    return {
        "schema_version": RESOURCE_AWARE_DECK_SCHEMA_V1,
        "purpose": "RESOURCE_WARMUP_ONLY_BEFORE_WEIGHTED_DECK_HALVING",
        "authority": dict(AUTHORITY_FALSE),
        "task_cap": task_cap,
        "gpu_required": gpu_required,
        "safe_workers": safe_workers if admitted else 0,
        "warmup_status": "ready" if admitted and safe_workers > 0 else "blocked",
        "resource_decision": decision.to_dict(),
        "snapshot": decision.snapshot.to_dict(),
        "warmup": {
            "requested_ramp_workers": requested_ramp,
            "admitted_ramp_workers": admitted_ramp if admitted else [],
            "recycle_games": budget.recycle_games,
            "execution": "not_started",
            "throughput_samples": [],
            "faults": 0,
        },
        "no_process_kill": True,
        "performance_run_started": False,
    }


def write_warmup_telemetry(
    destination: str | Path,
    *,
    budget: ResourceBudget,
    task_cap: int,
    gpu_required: bool = False,
    snapshot: ResourceSnapshot | None = None,
    repo_root: str | Path | None = None,
) -> str:
    """Atomically publish one research-only warm-up telemetry record."""

    path = Path(destination).resolve()
    if repo_root is not None:
        root = Path(repo_root).resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise ResourceAwareDeckCandidateError(
                f"output must be contained by repo root: {path}"
            ) from exc
    return _write_no_clobber(
        path,
        build_warmup_plan(
            budget=budget,
            task_cap=task_cap,
            gpu_required=gpu_required,
            snapshot=snapshot,
        ),
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--budget", type=Path, default=RESOURCE_BUDGET_DEFAULT)
    parser.add_argument("--output", type=Path, default=OUTPUT_DEFAULT)
    parser.add_argument("--task-cap", type=int, default=12)
    parser.add_argument("--gpu-required", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    budget = ResourceBudget.from_json(args.budget)
    payload_sha = write_warmup_telemetry(
        args.output,
        budget=budget,
        task_cap=args.task_cap,
        gpu_required=args.gpu_required,
        repo_root=ROOT,
    )
    print(
        json.dumps(
            {
                "output": str(args.output.resolve()),
                "payload_sha256": payload_sha,
                "performance_run_started": False,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "AUTHORITY_FALSE",
    "RESOURCE_AWARE_DECK_SCHEMA_V1",
    "ResourceAwareDeckCandidateError",
    "build_warmup_plan",
    "write_warmup_telemetry",
]
