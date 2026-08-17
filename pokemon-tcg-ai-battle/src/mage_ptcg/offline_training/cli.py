"""Unified, resumable CLI for the Offline Training v1 pipeline.

Commands: doctor, collect, build-dataset, train, evaluate, screen, export,
package, verify, pipeline, resume, status.  Every phase is idempotent, records
its status in the run manifest, and is skipped on resume unless ``--force``.
Paths are resolved relative to the invocation, so the CLI is cwd-independent.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from mage_ptcg.offline_training import runstate
from mage_ptcg.offline_training.config import OfflineTrainingConfig, load_config
from mage_ptcg.offline_training.environment import (
    disk_guard_status,
    doctor,
    environment_hash,
    resolve_resource_policy,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
TEACHER_ID = "rule-agent-v0"


class CliError(RuntimeError):
    pass


def _emit(summary: dict[str, Any]) -> None:
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2))


def _git_head() -> str:
    result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, check=False)
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def _collection_plan_fingerprint(o2_plan_hashes: Any) -> str:
    """Fold a collection's per-match O2 plan_hash list into one reference hash.

    Each O2 ``MatchSpec.plan_hash`` is per-match (seat, seed, and side all
    feed its identity), so a multi-match collection has many, not one.  The
    full list stays reachable via ``source_collection_hash`` -> the
    collector's own ``dataset_manifest.json`` (``o2_plan_hashes``); this
    fingerprint is only a stable, single-value pointer to "this exact set of
    O2 matches" for the offline-training dataset manifest.
    """
    if not isinstance(o2_plan_hashes, list) or not o2_plan_hashes:
        return "NONE"
    from mage_ptcg.competition_intelligence.canonical import digest

    return digest(sorted(str(item) for item in o2_plan_hashes), domain="o2-collection-plan-fingerprint-v1")


def _run_id_for(config: OfflineTrainingConfig, explicit: str | None) -> str:
    if explicit:
        if "/" in explicit:
            raise CliError("run id must be path-safe")
        return explicit
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    return f"{config.profile}-{config.hash()[:8]}-{stamp}"


def _default_run_dir(config: OfflineTrainingConfig, run_id: str) -> Path:
    return REPO_ROOT / "runs" / config.run_id_prefix / run_id


class Pipeline:
    """Holds a loaded config + run state and executes phases against a run dir."""

    def __init__(self, config: OfflineTrainingConfig, run_dir: Path, *, gpu_python: str | None = None):
        self.config = config
        self.run_dir = run_dir
        self.paths = runstate.RunPaths(run_dir)
        self.gpu_python = gpu_python
        self.report = doctor(gpu_python=gpu_python)
        self.policy = resolve_resource_policy(self.report)
        self.state: runstate.RunState | None = None

    # -- setup -------------------------------------------------------------- #
    def open(self, *, run_id: str, resume: bool) -> runstate.RunState:
        state = runstate.load_or_create(
            self.run_dir,
            run_id=run_id,
            git_commit=_git_head(),
            config_hash=self.config.hash(),
            environment_hash=environment_hash(self.report),
            resume=resume,
        )
        self.state = state
        runstate.atomic_write_json(self.paths.config_resolved, self.config.to_dict())
        runstate.atomic_write_json(self.paths.environment, {"doctor": self.report, "resource_policy": self.policy.to_dict()})
        self._check_disk()
        return state

    def _check_disk(self) -> None:
        status = disk_guard_status(self.policy, self.policy.disk_free_bytes)
        if status == "HARD_STOP":
            raise CliError("disk hard stop: refusing to run with insufficient free space")
        if status == "SOFT_STOP" and self.state is not None:
            self.state.append_event("disk_soft_stop", free_bytes=self.policy.disk_free_bytes)

    # -- helpers ------------------------------------------------------------ #
    @property
    def _s(self) -> runstate.RunState:
        if self.state is None:
            raise CliError("run state is not open")
        return self.state

    def _collection_jsonl(self) -> Path:
        return self.paths.root / "collection" / "cabt" / "private_dataset" / "rule-bc-v1.jsonl"

    def _dataset_dir(self) -> Path:
        return self.paths.root / "dataset" / "canonical"

    def _export_path(self) -> Path:
        return self.paths.root / "export" / "neural-student-v1.json"

    def _package_dir(self) -> Path:
        return self.paths.root / "package" / "neural-student-v1"

    @staticmethod
    def _dist_root() -> Path:
        """Return the publish root; tests may isolate it outside the checkout."""
        configured = os.environ.get("MAGE_PTCG_DIST_ROOT")
        return Path(configured) if configured else REPO_ROOT / "dist"

    def _model_purpose(self) -> str:
        from mage_ptcg.offline_training.neural import MODEL_PURPOSE_ACTUAL, MODEL_PURPOSE_SMOKE

        summary_path = self.paths.root / "collection" / "summary.json"
        if summary_path.exists():
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            if summary.get("collection_source") == "actual" and summary.get("performance_eligible") is True:
                return MODEL_PURPOSE_ACTUAL
        return MODEL_PURPOSE_SMOKE

    # -- phases ------------------------------------------------------------- #
    def phase_collect(self, *, force: bool) -> dict[str, Any]:
        from mage_ptcg.offline_training.collection import CollectionUnavailableError, run_collection

        state = self._s
        if state.is_phase_done("collect") and not force:
            return {"phase": "collect", "status": "SKIPPED"}
        state.set_phase("collect", runstate.STATUS_RUNNING)
        cfg = self.config.collection
        try:
            summary = run_collection(
                source=cfg.source, run_id="cabt", games=cfg.games, base_seed=cfg.base_seed,
                output_root=self.paths.root / "collection", canonical_base_sha=_git_head(),
                deck_path=REPO_ROOT / "deck.csv", repository_root=REPO_ROOT, max_steps=cfg.max_steps,
                validation_percent=cfg.validation_percent, split_seed=cfg.split_seed,
                fixture_decisions_per_seat=cfg.fixture_decisions_per_seat,
                fixture_option_count=cfg.fixture_option_count,
            )
        except CollectionUnavailableError as exc:
            state.set_phase("collect", runstate.STATUS_FAILED_RETRYABLE, error_summary="ACTUAL_CABT_NOT_RUN")
            raise CliError(f"actual cabt unavailable: {exc}") from exc
        runstate.atomic_write_json(self.paths.root / "collection" / "summary.json", summary)
        state.set_phase("collect", runstate.STATUS_COMPLETE)
        return {"phase": "collect", "status": "COMPLETE", "source": summary.get("collection_source"),
                "actual_cabt": summary.get("actual_cabt"), "episodes": summary.get("episode_count"),
                "decisions": summary.get("decision_count"), "privacy_violations": summary.get("privacy_violations")}

    def phase_build_dataset(self, *, force: bool) -> dict[str, Any]:
        from mage_ptcg.offline_training.dataset import build_dataset, verify_shards

        state = self._s
        dataset_dir = self._dataset_dir()
        if state.is_phase_done("build-dataset") and not force and (dataset_dir / "dataset_manifest.json").exists():
            verify_shards(dataset_dir)
            return {"phase": "build-dataset", "status": "SKIPPED"}
        state.set_phase("build-dataset", runstate.STATUS_RUNNING)
        source_jsonl = self._collection_jsonl()
        if not source_jsonl.is_file():
            state.set_phase("build-dataset", runstate.STATUS_FAILED_RETRYABLE, error_summary="collection dataset missing")
            raise CliError("collection dataset is missing; run collect first")
        if dataset_dir.exists() and force:
            import shutil

            shutil.rmtree(dataset_dir)
        summary = json.loads((self.paths.root / "collection" / "summary.json").read_text(encoding="utf-8"))
        cfg = self.config.dataset
        manifest = build_dataset(
            source_jsonl=source_jsonl, output_dir=dataset_dir, shard_size=cfg.shard_size,
            split_seed=cfg.split_seed, train_fraction=cfg.train_fraction,
            validation_fraction=cfg.validation_fraction, test_fraction=cfg.test_fraction,
            teacher_id=TEACHER_ID, trainer_id="offline-training-v1",
            source_collection_hash=summary.get("dataset_hash", "NONE"),
            source_plan_hash=_collection_plan_fingerprint(summary.get("o2_plan_hashes")),
        )
        verify_shards(dataset_dir)
        state.set_phase("build-dataset", runstate.STATUS_COMPLETE,
                        dataset_hash=manifest["dataset_hash"], feature_schema_hash=manifest["feature_schema_hash"])
        return {"phase": "build-dataset", "status": "COMPLETE", "shards": manifest["shard_count"],
                "records": manifest["record_count"], "episodes": manifest["episode_count"],
                "split": manifest["split_episode_counts"], "dataset_hash": manifest["dataset_hash"]}

    def phase_train(self, *, force: bool, resume_training: bool = False) -> dict[str, Any]:
        from mage_ptcg.offline_training import neural

        state = self._s
        if state.is_phase_done("train") and not force:
            return {"phase": "train", "status": "SKIPPED"}
        state.set_phase("train", runstate.STATUS_RUNNING)
        cfg = self.config.training
        model_purpose = self._model_purpose()
        try:
            result = neural.train(
                dataset_dir=self._dataset_dir(), checkpoint_dir=self.paths.root / "checkpoints",
                hidden_dims=self.config.model.resolved_hidden_dims(), epochs=cfg.epochs,
                learning_rate=cfg.learning_rate, weight_decay=cfg.weight_decay, grad_clip=cfg.grad_clip,
                patience=cfg.patience, seed=cfg.seed, max_batch_decisions=cfg.max_batch_decisions,
                model_purpose=model_purpose, device=cfg.device,
                metrics_path=self.paths.root / "checkpoints" / "metrics.jsonl",
                resume=resume_training,
            )
        except neural.NeuralError as exc:
            state.set_phase("train", runstate.STATUS_FAILED_RETRYABLE, error_summary=str(exc))
            raise CliError(f"training failed: {exc}") from exc
        state.set_phase("train", runstate.STATUS_COMPLETE, best_checkpoint=result["best_checkpoint"],
                        last_checkpoint=result["last_checkpoint"], model_purpose=model_purpose, teacher_id=TEACHER_ID)
        return {"phase": "train", "status": "COMPLETE", "best_metric": result["best_metric"],
                "epochs_run": result["epochs_run"], "device": result["resolved"]["device"],
                "bf16": result["resolved"]["use_bf16"], "final_microbatch": result["final_microbatch"],
                "model_purpose": model_purpose}

    def phase_export(self, *, force: bool) -> dict[str, Any]:
        from mage_ptcg.offline_training import export, neural
        from mage_ptcg.student.artifact import feature_schema

        state = self._s
        export_path = self._export_path()
        if state.is_phase_done("export") and not force and export_path.exists():
            return {"phase": "export", "status": "SKIPPED"}
        state.set_phase("export", runstate.STATUS_RUNNING)
        best = self.paths.root / "checkpoints" / "best"
        module, meta, spec = neural.load_module_from_checkpoint(best, device="cpu")
        document = export.build_export(
            module=module, model_spec_dict=spec.to_dict(), normalization=meta["normalization"],
            feature_schema=feature_schema(), dataset_hash=meta["dataset_hash"],
            config_hash=self.config.hash(), teacher_id=TEACHER_ID, model_purpose=meta["model_purpose"],
        )
        export_path.parent.mkdir(parents=True, exist_ok=True)
        if export_path.exists() and force:
            export_path.unlink()
        model_hash = export.write_export(document, export_path)
        state.set_phase("export", runstate.STATUS_COMPLETE, model_hash=model_hash)
        return {"phase": "export", "status": "COMPLETE", "model_hash": model_hash,
                "model_purpose": document["model_purpose"], "export_path": str(export_path)}

    def phase_evaluate(self, *, force: bool) -> dict[str, Any]:
        from mage_ptcg.offline_training import evaluate, export

        state = self._s
        out = self.paths.root / "evaluation" / "evaluation.json"
        if state.is_phase_done("evaluate") and not force and out.exists():
            return {"phase": "evaluate", "status": "SKIPPED"}
        state.set_phase("evaluate", runstate.STATUS_RUNNING)
        document = export.load_export(self._export_path())
        comparison = evaluate.compare_models(self._dataset_dir(), document, split="test")
        out.parent.mkdir(parents=True, exist_ok=True)
        runstate.atomic_write_json(out, comparison)
        state.set_phase("evaluate", runstate.STATUS_COMPLETE)
        return {"phase": "evaluate", "status": "COMPLETE",
                "neural_top1": comparison["neural_student_v1"]["top1"],
                "linear_top1": comparison["linear_student_v0"]["top1"],
                "neural_nll": comparison["neural_student_v1"]["nll"]}

    def phase_screen(self, *, force: bool) -> dict[str, Any]:
        from mage_ptcg.offline_training import evaluate, export

        state = self._s
        out = self.paths.root / "evaluation" / "screening.json"
        if state.is_phase_done("screen") and not force and out.exists():
            return {"phase": "screen", "status": "SKIPPED"}
        state.set_phase("screen", runstate.STATUS_RUNNING)
        document = export.load_export(self._export_path())
        screening = evaluate.tiny_screening(
            export_document=document, deck=[1] * 60, games=self.config.screening.games,
            base_seed=self.config.screening.base_seed,
        )
        out.parent.mkdir(parents=True, exist_ok=True)
        runstate.atomic_write_json(out, screening)
        state.set_phase("screen", runstate.STATUS_COMPLETE)
        return {"phase": "screen", "status": "COMPLETE", "verdict": screening["verdict"],
                "legal_action_rate": screening["legal_action_rate"], "fallback_rate": screening["fallback_rate"],
                "seat_balance": screening["seat_balance"], "actual_cabt": screening["actual_cabt"]}

    def phase_package(self, *, force: bool) -> dict[str, Any]:
        from mage_ptcg.offline_training import package

        state = self._s
        package_dir = self._package_dir()
        if state.is_phase_done("package") and not force and (package_dir / package.MANIFEST_NAME).exists():
            return {"phase": "package", "status": "SKIPPED"}
        state.set_phase("package", runstate.STATUS_RUNNING)
        if package_dir.exists() and force:
            import shutil

            shutil.rmtree(package_dir)
        manifest = package.build_package(
            export_path=self._export_path(), output_dir=package_dir, repository_root=REPO_ROOT,
            build_commit=_git_head(),
        )
        # Publish a copy under the repository dist path (git-ignored).
        dist_dir = self._dist_root() / "kaggle" / "neural-student-v1"
        self._publish_dist(package_dir, dist_dir)
        state.set_phase("package", runstate.STATUS_COMPLETE, package_hash=manifest["archive_sha256"])
        return {"phase": "package", "status": "COMPLETE", "archive_sha256": manifest["archive_sha256"],
                "members": len(manifest["files"]), "dist_path": str(dist_dir / package.ARCHIVE_NAME)}

    def _publish_dist(self, package_dir: Path, dist_dir: Path) -> None:
        import shutil

        if dist_dir.exists():
            shutil.rmtree(dist_dir)
        shutil.copytree(package_dir, dist_dir)

    def phase_verify(self, *, force: bool) -> dict[str, Any]:
        from mage_ptcg.offline_training import package

        state = self._s
        if state.is_phase_done("verify") and not force:
            return {"phase": "verify", "status": "SKIPPED"}
        state.set_phase("verify", runstate.STATUS_RUNNING)
        report = package.clean_room_verify(self._package_dir())
        runstate.atomic_write_json(self.paths.root / "package" / "clean_room.json", report)
        state.set_phase("verify", runstate.STATUS_COMPLETE)
        return {"phase": "verify", "status": "COMPLETE", **report}

    def run_pipeline(self, *, force: bool, resume_training: bool = False) -> dict[str, Any]:
        results = [
            self.phase_collect(force=force),
            self.phase_build_dataset(force=force),
            self.phase_train(force=force, resume_training=resume_training),
            self.phase_export(force=force),
            self.phase_evaluate(force=force),
            self.phase_screen(force=force),
            self.phase_package(force=force),
            self.phase_verify(force=force),
        ]
        return {"pipeline": "complete", "phases": results}


# --------------------------------------------------------------------------- #
# CLI wiring
# --------------------------------------------------------------------------- #


def _install_signal_guard(pipeline: Pipeline) -> None:
    def handler(signum, _frame):
        state = pipeline.state
        if state is not None:
            current = state.manifest.get("current_phase")
            if current and state.phase_status(current) == runstate.STATUS_RUNNING:
                state.set_phase(current, runstate.STATUS_INTERRUPTED, error_summary=f"signal {signum}")
            state.append_event("signal_interrupt", signal=signum)
            resume_cmd = f"python scripts/run_offline_training_v1.py resume --run-dir {pipeline.run_dir}"
            print(json.dumps({"status": "INTERRUPTED", "resume": resume_cmd}), file=sys.stderr)
        runstate.release_lock(pipeline.paths)
        raise SystemExit(130)

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, handler)
        except (ValueError, OSError):
            pass


def _load(args) -> OfflineTrainingConfig:
    if not args.config:
        raise CliError("--config is required")
    return load_config(Path(args.config).resolve())


def _dispatch(args, phase_name: str | None) -> int:
    config = _load(args)
    run_id = _run_id_for(config, getattr(args, "run_id", None))
    run_dir = Path(args.run_dir).resolve() if getattr(args, "run_dir", None) else _default_run_dir(config, run_id)
    resume = bool(getattr(args, "resume", False))
    if run_dir.exists() and (run_dir / runstate.MANIFEST_NAME).exists() and not resume and phase_name != "status":
        # Reopen an existing run for a single-phase invocation is a resume.
        resume = True
    pipeline = Pipeline(config, run_dir, gpu_python=getattr(args, "gpu_python", None))
    if getattr(args, "dry_run", False):
        _emit({"dry_run": True, "run_dir": str(run_dir), "run_id": run_id, "config_hash": config.hash(),
               "resource_policy": pipeline.policy.to_dict(), "phase": phase_name or "pipeline"})
        return 0
    existing = (run_dir / runstate.MANIFEST_NAME).exists()
    open_run_id = run_id
    if existing:
        manifest = json.loads((run_dir / runstate.MANIFEST_NAME).read_text(encoding="utf-8"))
        open_run_id = manifest.get("run_id", run_id)
    with runstate.run_lock(runstate.RunPaths(run_dir), open_run_id):
        pipeline.open(run_id=open_run_id, resume=resume or existing)
        _install_signal_guard(pipeline)
        force = bool(getattr(args, "force", False))
        if phase_name is None:
            result = pipeline.run_pipeline(force=force, resume_training=getattr(args, "resume", False))
        else:
            method = getattr(pipeline, f"phase_{phase_name.replace('-', '_')}")
            if phase_name == "train":
                result = method(force=force, resume_training=bool(getattr(args, "resume", False)))
            else:
                result = method(force=force)
        pipeline._s.save()
    _emit(result)
    return 0


def _cmd_doctor(args) -> int:
    report = doctor(gpu_python=getattr(args, "gpu_python", None))
    policy = resolve_resource_policy(report)
    _emit({"doctor": report, "resource_policy": policy.to_dict(), "environment_hash": environment_hash(report)})
    return 0


def _cmd_status(args) -> int:
    run_dir = Path(args.run_dir).resolve()
    manifest_path = run_dir / runstate.MANIFEST_NAME
    if not manifest_path.exists():
        _emit({"status": "NO_RUN", "run_dir": str(run_dir)})
        return 1
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    _emit({
        "run_id": manifest.get("run_id"), "current_phase": manifest.get("current_phase"),
        "phase_statuses": manifest.get("phase_statuses"), "dataset_hash": manifest.get("dataset_hash"),
        "model_hash": manifest.get("model_hash"), "model_purpose": manifest.get("model_purpose"),
        "package_hash": manifest.get("package_hash"), "resume_count": manifest.get("resume_count"),
        "error_summary": manifest.get("error_summary"), "updated_at": manifest.get("updated_at"),
    })
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="run_offline_training_v1", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(p, *, needs_config=True):
        if needs_config:
            p.add_argument("--config", type=str, help="path to a config preset JSON")
        p.add_argument("--run-dir", type=str, default=None, help="explicit run directory")
        p.add_argument("--run-id", type=str, default=None, help="explicit run id")
        p.add_argument("--gpu-python", type=str, default=os.environ.get("POKEMON_TCG_GPU_PYTHON"), help="GPU interpreter path (informational)")
        p.add_argument("--dry-run", action="store_true")
        p.add_argument("--force", action="store_true")
        p.add_argument("--resume", action="store_true")

    doctor_p = sub.add_parser("doctor")
    doctor_p.add_argument("--config", type=str, default=None)
    doctor_p.add_argument("--gpu-python", type=str, default=os.environ.get("POKEMON_TCG_GPU_PYTHON"))
    doctor_p.set_defaults(func=_cmd_doctor)

    for name in ("collect", "build-dataset", "train", "evaluate", "screen", "export", "package", "verify", "pipeline"):
        p = sub.add_parser(name)
        add_common(p)
        p.set_defaults(func=lambda a, n=name: _dispatch(a, None if n == "pipeline" else n))

    resume_p = sub.add_parser("resume")
    add_common(resume_p, needs_config=False)
    resume_p.add_argument("--config", type=str, default=None)
    resume_p.set_defaults(func=_cmd_resume)

    status_p = sub.add_parser("status")
    status_p.add_argument("--run-dir", type=str, required=True)
    status_p.set_defaults(func=_cmd_status)
    return parser


def _cmd_resume(args) -> int:
    run_dir = Path(args.run_dir).resolve() if args.run_dir else None
    if run_dir is None:
        raise CliError("resume requires --run-dir")
    if not (run_dir / runstate.MANIFEST_NAME).exists():
        raise CliError("no run manifest found to resume")
    if not args.config:
        args.config = str(run_dir / "config.resolved.json")
    args.resume = True
    return _dispatch(args, None)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except (CliError, runstate.RunStateError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 2


__all__ = ["Pipeline", "build_parser", "main"]
