#!/usr/bin/env python3
"""Materialize one native-preserving meta-overfit iteration in dry-run mode.

The command only verifies and writes a research manifest.  It never launches
CABT, training, a subprocess, a longrun, or a submission.  ``--execute`` is
accepted only to fail closed, so a caller cannot accidentally turn this
materializer into an executor.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from mage_ptcg.meta_specialist.native_meta_overfit_iteration_v1 import (
    NativeMetaOverfitIterationError,
    build_native_meta_overfit_iteration_v1,
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", required=True, type=Path)
    parser.add_argument("--curriculum-manifest", required=True, type=Path)
    parser.add_argument("--outcome-adapter-manifest", required=True, type=Path)
    parser.add_argument("--public-advantage-table", required=True, type=Path)
    parser.add_argument("--native-baseline-identity", required=True, type=Path)
    parser.add_argument("--candidate-deck-manifest", type=Path)
    parser.add_argument(
        "--output-manifest",
        type=Path,
        help="legacy direct output path (use --run-root for the Task 4 materializer)",
    )
    parser.add_argument(
        "--run-root",
        type=Path,
        help="new, repo-contained run root for a dry-run materialization",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Always rejected: this command is a dry-run materializer only.",
    )
    parser.add_argument(
        "--record-blocked",
        action="store_true",
        help="Persist a BLOCKED run root when strict input verification fails; never marks it ready.",
    )
    return parser


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def _sha_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _contained_run_root(repo_root: Path, run_root: Path) -> Path:
    root = repo_root.resolve()
    target = run_root.resolve()
    if target == root:
        raise NativeMetaOverfitIterationError("run_root must be below repo_root")
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise NativeMetaOverfitIterationError("run_root escapes repo_root") from exc
    if target.exists():
        raise FileExistsError(target)
    return target


def _write_new(path: Path, raw: bytes) -> None:
    with path.open("xb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())


def _atomic_copy_new(source: Path, destination: Path) -> None:
    """Publish a complete copy, never a partially copied destination.

    The run root is claimed exclusively before this helper is called, so the
    destination is expected to be new.  The source is copied to a sibling
    temporary file, flushed/fsynced, and only then atomically published.  A
    failed copy removes both the temporary file and any destination published
    by this invocation; callers therefore do not need to register a path that
    may only contain a partial prefix.
    """

    temporary = destination.with_name(f".{destination.name}.tmp.{os.getpid()}")
    published = False
    try:
        with source.open("rb") as source_handle, temporary.open("xb") as target_handle:
            shutil.copyfileobj(source_handle, target_handle)
            target_handle.flush()
            os.fsync(target_handle.fileno())
        # ``os.replace`` would silently clobber a destination created by a
        # competing writer after the run-root claim.  A hard-link publish is
        # an exclusive create on the same filesystem: FileExistsError leaves
        # the winner's bytes untouched while the temporary is cleaned below.
        os.link(temporary, destination)
        published = True
        temporary.unlink()
    except BaseException:
        temporary.unlink(missing_ok=True)
        if published:
            destination.unlink(missing_ok=True)
        raise


def _cleanup_owned_root(run_root: Path, owned: list[Path]) -> None:
    # Only remove files explicitly created by this call, then remove the empty
    # directory.  A pre-existing destination is never touched.
    for path in reversed(owned):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
    try:
        run_root.rmdir()
    except OSError:
        pass


def _input_inventory(repo_root: Path, paths: dict[str, Path]) -> dict[str, object]:
    """Describe requested inputs without treating missing inputs as valid."""

    inventory: dict[str, object] = {}
    for role, raw_path in sorted(paths.items()):
        path = Path(raw_path).resolve()
        item: dict[str, object] = {"path": str(path)}
        try:
            path.relative_to(repo_root.resolve())
            item["repo_contained"] = True
        except ValueError:
            item["repo_contained"] = False
        if path.is_file():
            item["exists"] = True
            item["file_sha256"] = _sha_bytes(path.read_bytes())
        else:
            item["exists"] = False
        inventory[role] = item
    return inventory


def _write_blocked_root(
    *,
    run_root: Path,
    repo_root: Path,
    inputs: dict[str, Path],
    error: BaseException,
) -> dict[str, object]:
    """Persist a non-promotable blocked dry-run without candidate artifacts."""

    run_root.mkdir(parents=True, exist_ok=False)
    authority = {
        "training_authority": False,
        "promotion_authority": False,
        "submission_authority": False,
        "external_execution_authority": False,
    }
    progress = {
        "schema_version": "native-meta-overfit-dry-run-progress-v1",
        "status": "BLOCKED",
        "stage": "INPUT_VERIFICATION",
        "completed": 0,
        "total": 1,
        "fault": 0,
        "ready_for_evaluation": False,
        "processes_launched": False,
        "cabt_started": False,
        "training_started": False,
        "submission_started": False,
        "authority": authority,
        "input_inventory": _input_inventory(repo_root, inputs),
        "block_reason": f"{type(error).__name__}: {error}",
    }
    progress_path = run_root / "progress_summary.json"
    _write_new(progress_path, _canonical_bytes(progress))
    payload = {
        "schema_version": "native-meta-overfit-dry-run-run-v1",
        "status": "BLOCKED",
        "block_reason": progress["block_reason"],
        "input_inventory": progress["input_inventory"],
        "progress_summary": {
            "path": "progress_summary.json",
            "file_sha256": _sha_bytes(progress_path.read_bytes()),
        },
        "ready_for_evaluation": False,
        "processes_launched": False,
        "cabt_started": False,
        "training_started": False,
        "submission_started": False,
        "candidate_artifacts_materialized": False,
        "authority": authority,
    }
    run_manifest_path = run_root / "run-manifest.json"
    _write_new(run_manifest_path, _canonical_bytes(payload))
    return {
        "status": "BLOCKED",
        "run_root": str(run_root),
        "block_reason": payload["block_reason"],
        "ready_for_evaluation": False,
        "candidate_artifacts_materialized": False,
        "processes_launched": False,
        "cabt_started": False,
        "training_started": False,
        "submission_started": False,
    }


def materialize_dry_run(
    *,
    repo_root: Path,
    curriculum_manifest: Path,
    outcome_adapter_manifest: Path,
    public_advantage_table: Path,
    native_baseline_identity: Path,
    run_root: Path,
    candidate_deck_manifest: Path | None = None,
    record_blocked: bool = False,
) -> dict[str, object]:
    """Create one new, repo-contained research-only dry-run root.

    This function has no executor callback and deliberately does not import or
    call an evaluator/trainer.  The run root is claimed with ``mkdir`` before
    any artifact is written; all failure cleanup is limited to files created by
    this invocation.
    """

    root = Path(repo_root).resolve()
    target = _contained_run_root(root, Path(run_root))
    target.mkdir(parents=True, exist_ok=False)
    owned: list[Path] = []
    try:
        table_source = Path(public_advantage_table).resolve()
        try:
            table_source.relative_to(root)
        except ValueError as exc:
            raise NativeMetaOverfitIterationError("public advantage table escapes repo_root") from exc
        table_copy = target / "candidate-public-advantage-table.json"
        _atomic_copy_new(table_source, table_copy)
        owned.append(table_copy)

        output_manifest = target / "iteration-manifest.json"
        manifest = build_native_meta_overfit_iteration_v1(
            repo_root=root,
            curriculum_manifest_path=curriculum_manifest,
            outcome_adapter_manifest_path=outcome_adapter_manifest,
            public_advantage_table_path=table_copy,
            native_baseline_identity=native_baseline_identity,
            candidate_deck_manifest_path=candidate_deck_manifest,
            output_manifest_path=output_manifest,
        )
        owned.append(output_manifest)

        progress = {
            "schema_version": "native-meta-overfit-dry-run-progress-v1",
            "status": "DRY_RUN",
            "stage": "MATERIALIZED",
            "completed": 1,
            "total": 1,
            "fault": 0,
            "ready_for_evaluation": False,
            "processes_launched": False,
            "cabt_started": False,
            "training_started": False,
            "submission_started": False,
            "authority": {
                "training_authority": False,
                "promotion_authority": False,
                "submission_authority": False,
                "external_execution_authority": False,
            },
        }
        progress_path = target / "progress_summary.json"
        _write_new(progress_path, _canonical_bytes(progress))
        owned.append(progress_path)

        run_payload = {
            "schema_version": "native-meta-overfit-dry-run-run-v1",
            "status": "DRY_RUN",
            "iteration_manifest": {
                "path": "iteration-manifest.json",
                "file_sha256": _sha_bytes(output_manifest.read_bytes()),
                "iteration_sha256": manifest["iteration_sha256"],
            },
            "candidate_public_advantage_table": {
                "path": "candidate-public-advantage-table.json",
                "file_sha256": _sha_bytes(table_copy.read_bytes()),
            },
            "progress_summary": {
                "path": "progress_summary.json",
                "file_sha256": _sha_bytes(progress_path.read_bytes()),
            },
            "ready_for_evaluation": False,
            "processes_launched": False,
            "cabt_started": False,
            "training_started": False,
            "submission_started": False,
            "authority": dict(progress["authority"]),
        }
        run_path = target / "run-manifest.json"
        _write_new(run_path, _canonical_bytes(run_payload))
        owned.append(run_path)
        return {
            "status": "DRY_RUN",
            "run_root": str(target),
            "manifest_path": str(output_manifest),
            "manifest_file_sha256": run_payload["iteration_manifest"]["file_sha256"],
            "iteration_sha256": manifest["iteration_sha256"],
            "candidate_table_path": str(table_copy),
            "ready_for_evaluation": False,
            "processes_launched": False,
            "cabt_started": False,
            "training_started": False,
            "submission_started": False,
        }
    except BaseException as exc:
        if record_blocked and isinstance(exc, (NativeMetaOverfitIterationError, FileNotFoundError, OSError)):
            _cleanup_owned_root(target, owned)
            return _write_blocked_root(
                run_root=target,
                repo_root=root,
                inputs={
                    "curriculum_manifest": Path(curriculum_manifest),
                    "outcome_adapter_manifest": Path(outcome_adapter_manifest),
                    "public_advantage_table": Path(public_advantage_table),
                    "native_baseline_identity": Path(native_baseline_identity),
                },
                error=exc,
            )
        _cleanup_owned_root(target, owned)
        raise


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.execute:
        print("ERROR: --execute is disabled; this materializer is DRY_RUN only", file=sys.stderr)
        return 2
    if (args.run_root is None) == (args.output_manifest is None):
        print("ERROR: provide exactly one of --run-root or --output-manifest", file=sys.stderr)
        return 2
    try:
        if args.run_root is not None:
            summary = materialize_dry_run(
                repo_root=args.repo_root,
                curriculum_manifest=args.curriculum_manifest,
                outcome_adapter_manifest=args.outcome_adapter_manifest,
                public_advantage_table=args.public_advantage_table,
                native_baseline_identity=args.native_baseline_identity,
                run_root=args.run_root,
                candidate_deck_manifest=args.candidate_deck_manifest,
                record_blocked=args.record_blocked,
            )
            print(json.dumps(summary, sort_keys=True))
            return 0
        manifest = build_native_meta_overfit_iteration_v1(
            repo_root=args.repo_root,
            curriculum_manifest_path=args.curriculum_manifest,
            outcome_adapter_manifest_path=args.outcome_adapter_manifest,
            public_advantage_table_path=args.public_advantage_table,
            native_baseline_identity=args.native_baseline_identity,
            candidate_deck_manifest_path=args.candidate_deck_manifest,
            output_manifest_path=args.output_manifest,
        )
    except (NativeMetaOverfitIterationError, FileExistsError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "status": "DRY_RUN",
                "manifest_path": str(args.output_manifest.resolve()),
                "manifest_file_sha256": _sha(args.output_manifest.resolve()),
                "iteration_sha256": manifest["iteration_sha256"],
                "ready_for_evaluation": manifest["ready_for_evaluation"],
                "processes_launched": False,
                "cabt_started": False,
                "training_started": False,
                "submission_started": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
