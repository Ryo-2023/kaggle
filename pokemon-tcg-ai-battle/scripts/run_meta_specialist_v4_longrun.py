#!/usr/bin/env python3
"""Run the restart-safe, monitored Archaludon V4 recurrent-BC long run.

The V4 trainer publishes an atomic model+Adam snapshot after every completed
epoch.  This wrapper resumes only from that explicit epoch boundary; it never
claims that an interrupted sequence/update was replayed exactly.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Mapping, NamedTuple, Sequence

import torch

from mage_ptcg.meta_specialist.opponent_pool_v1 import default_pool_root_v1, load_opponent_pool_v1, resolve_opponent_v1
from scripts.make_medal_opponents import EVAL_HELD_OUT_V1
from scripts.measure_v4_checkpoint_strength import evaluation_implementation_sha256_v1
from scripts.measure_opponent_strength import _wilson
from mage_ptcg.meta_specialist.recurrent_bc_v4 import trainer_implementation_sha256_v4
from mage_ptcg.meta_specialist.neural_model_v4 import SpecialistModelV4
from mage_ptcg.meta_specialist.progress_v1 import ProgressReporterV1


ROOT = Path(__file__).resolve().parents[1]
TRAINING_SCRIPT = ROOT / "scripts" / "run_meta_specialist_v4_bc.py"
EVALUATION_SCRIPT = ROOT / "scripts" / "measure_v4_checkpoint_strength.py"
LONGRUN_SCHEMA = "meta-specialist-v4-archaludon-longrun-v2"
TRAINING_SCHEMA = "meta-specialist-recurrent-bc-v4-research-report"
EVALUATION_SCHEMA = "meta-specialist-v4-heldout-checkpoint-strength-v1"
RESTART_CONTRACT = "epoch_boundary_optimizer_resume_only"
SEEDS = (0, 1)
OPPONENT_COUNT = 6

DEFAULT_LANE = {
    "lane": "archaludon",
    "selection_manifest": ROOT / "runs/meta-specialist-two-lane-readiness/recurrent-selection/archaludon.json",
    "selection_manifest_sha256": "b3044504df1192ce072377f1ddfbeeafdf071a715ef896076b5adb1471eaf0cc",
    "subject_deck_csv": ROOT / "opponents/public_archaludon_cinderace_r7/deck.csv",
    "subject_archetype_id": "archaludon",
}


class LongrunError(RuntimeError):
    """The sealed long-run artifact cannot safely be started or reused."""


class LongrunConfigV4(NamedTuple):
    lane: Mapping[str, object]
    output_root: Path
    python: str
    max_records: int
    episodes_per_partition: int
    components_per_partition: int
    epochs: int
    patience: int
    seeds: tuple[int, int]
    hidden_dim: int
    embedding_dim: int
    tbptt_steps: int
    games_per_seat: int
    base_seed: int
    max_steps: int
    validation_episodes_per_partition: int | None = None
    validation_components_per_partition: int | None = None
    learning_rate: float = 1e-3
    burn_in: int = 1
    subset_fraction: float = 0.05
    gradient_clip_norm: float = 1.0

    @property
    def train_episodes_per_partition(self) -> int:
        return self.episodes_per_partition

    @property
    def train_components_per_partition(self) -> int:
        return self.components_per_partition

    @property
    def effective_validation_episodes_per_partition(self) -> int:
        return self.episodes_per_partition if self.validation_episodes_per_partition is None else self.validation_episodes_per_partition

    @property
    def effective_validation_components_per_partition(self) -> int:
        return self.components_per_partition if self.validation_components_per_partition is None else self.validation_components_per_partition

    @property
    def training_output(self) -> Path:
        return self.output_root / "archaludon-training.json"

    @property
    def manifest_path(self) -> Path:
        return self.output_root / "run-manifest.json"

    @property
    def progress_path(self) -> Path:
        return self.output_root / "progress_summary.json"


def _atomic_json(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.tmp.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _json_object(path: Path, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise LongrunError(f"{label} does not exist or is not a regular file: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LongrunError(f"{label} is not readable JSON: {path}") from exc
    if not isinstance(value, dict):
        raise LongrunError(f"{label} must be a JSON object: {path}")
    return value


def _require_hex64(value: object, label: str) -> str:
    if type(value) is not str or len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise LongrunError(f"{label} must be a 64-character lowercase SHA-256")
    return value


def _config_payload(config: LongrunConfigV4) -> dict[str, object]:
    pool = load_opponent_pool_v1(default_pool_root_v1(ROOT))
    opponent_fingerprints = []
    for opponent_id in EVAL_HELD_OUT_V1:
        opponent = resolve_opponent_v1(pool, opponent_id, subject_deck_csv_path="x")
        opponent_fingerprints.append({
            "opponent_id": opponent_id, "canonical_deck_hash": opponent.canonical_deck_hash,
            "deck_file_sha256": hashlib.sha256(Path(opponent.deck_csv_path).read_bytes()).hexdigest(),
            "policy_hash": opponent.policy_hash,
        })
    return {
        "lane": str(config.lane["lane"]),
        "selection_manifest": str(Path(str(config.lane["selection_manifest"])).resolve()),
        "selection_manifest_sha256": str(config.lane["selection_manifest_sha256"]),
        "subject_deck_csv": str(Path(str(config.lane["subject_deck_csv"])).resolve()),
        "subject_deck_file_sha256": hashlib.sha256(Path(str(config.lane["subject_deck_csv"])).read_bytes()).hexdigest(),
        "subject_archetype_id": str(config.lane["subject_archetype_id"]),
        "max_records": config.max_records,
        "episodes_per_partition": config.episodes_per_partition,
        "components_per_partition": config.components_per_partition,
        "train_episodes_per_partition": config.train_episodes_per_partition,
        "validation_episodes_per_partition": config.effective_validation_episodes_per_partition,
        "train_components_per_partition": config.train_components_per_partition,
        "validation_components_per_partition": config.effective_validation_components_per_partition,
        "require_positive_stop": True,
        "epochs": config.epochs,
        "patience": config.patience,
        "seeds": list(config.seeds),
        "hidden_dim": config.hidden_dim,
        "embedding_dim": config.embedding_dim,
        "tbptt_steps": config.tbptt_steps,
        "learning_rate": config.learning_rate,
        "burn_in": config.burn_in,
        "subset_fraction": config.subset_fraction,
        "gradient_clip_norm": config.gradient_clip_norm,
        "device": "cuda:0",
        "opponent_count": OPPONENT_COUNT,
        "fixed_held_out_opponent_ids": list(EVAL_HELD_OUT_V1),
        "opponent_fingerprints": opponent_fingerprints,
        "evaluation_implementation_sha256": evaluation_implementation_sha256_v1(),
        "games_per_seat": config.games_per_seat,
        "base_seed": config.base_seed,
        "max_steps": config.max_steps,
        "restart_contract": RESTART_CONTRACT,
    }


def config_sha256_v4(config: LongrunConfigV4) -> str:
    raw = json.dumps(_config_payload(config), sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _sealed_config_identity(config: LongrunConfigV4) -> tuple[dict[str, object], str]:
    """Return the immutable initial payload; never rebaseline changed live assets."""
    live = _config_payload(config)
    live_sha = hashlib.sha256(json.dumps(live, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    if not config.manifest_path.is_file():
        return live, live_sha
    existing = _json_object(config.manifest_path, "longrun manifest")
    sealed = existing.get("config")
    sealed_sha = existing.get("config_sha256")
    if not isinstance(sealed, dict) or type(sealed_sha) is not str:
        raise LongrunError("existing longrun manifest has no sealed config identity")
    if live != sealed or live_sha != sealed_sha:
        raise LongrunError("live longrun assets/config SHA-256 differ from the initially sealed config")
    return dict(sealed), sealed_sha


def _validate_config(config: LongrunConfigV4) -> None:
    if str(config.lane.get("lane")) != "archaludon":
        raise LongrunError("V4 longrun is currently sealed to the Archaludon lane")
    if not Path(str(config.lane.get("selection_manifest", ""))).is_file():
        raise LongrunError("selection manifest does not exist or is not a regular file")
    selection_sha256 = _require_hex64(config.lane.get("selection_manifest_sha256"), "selection manifest SHA-256")
    selection_path = Path(str(config.lane["selection_manifest"]))
    if hashlib.sha256(selection_path.read_bytes()).hexdigest() != selection_sha256:
        raise LongrunError("selection manifest SHA-256 changed since this longrun was configured")
    if not Path(str(config.lane.get("subject_deck_csv", ""))).is_file():
        raise LongrunError("subject deck does not exist or is not a regular file")
    if (
        type(config.max_records) is not int or config.max_records < 4
        or type(config.episodes_per_partition) is not int or not 4 <= config.episodes_per_partition <= 512
        or type(config.components_per_partition) is not int
        or not 4 <= config.components_per_partition <= config.episodes_per_partition
        or type(config.effective_validation_episodes_per_partition) is not int
        or not 4 <= config.effective_validation_episodes_per_partition <= 512
        or type(config.effective_validation_components_per_partition) is not int
        or not 4 <= config.effective_validation_components_per_partition <= config.effective_validation_episodes_per_partition
        or type(config.epochs) is not int or config.epochs < 1
        or type(config.patience) is not int or config.patience < 0
        or config.seeds != SEEDS or type(config.hidden_dim) is not int or config.hidden_dim < 1
        or type(config.embedding_dim) is not int or config.embedding_dim < 1
        or type(config.tbptt_steps) is not int or config.tbptt_steps < 1
        or type(config.learning_rate) is not float or config.learning_rate <= 0.0
        or type(config.burn_in) is not int or config.burn_in < 0
        or type(config.subset_fraction) is not float or not 0.0 < config.subset_fraction <= 0.1
        or type(config.gradient_clip_norm) is not float or config.gradient_clip_norm <= 0.0
        or type(config.games_per_seat) is not int or config.games_per_seat < 1
        or type(config.base_seed) is not int or config.base_seed < 0
        or type(config.max_steps) is not int or config.max_steps < 1
    ):
        raise LongrunError("longrun configuration is invalid")


def _manifest(config: LongrunConfigV4, *, status: str, **extra: object) -> dict[str, object]:
    sealed, sealed_sha = _sealed_config_identity(config)
    return {
        "schema": LONGRUN_SCHEMA,
        "status": status,
        "config": sealed,
        "config_sha256": sealed_sha,
        "restart_contract": RESTART_CONTRACT,
        "updated_unix": time.time(),
        **extra,
    }


def _write_progress(config: LongrunConfigV4, *, status: str, stage: str, **extra: object) -> None:
    _sealed, sealed_sha = _sealed_config_identity(config)
    _atomic_json(config.progress_path, {
        "schema": LONGRUN_SCHEMA,
        "status": status,
        "stage": stage,
        "restart_contract": RESTART_CONTRACT,
        "config_sha256": sealed_sha,
        "updated_unix": time.time(),
        **extra,
    })


def _pid_alive(pid: int) -> bool:
    if type(pid) is not int or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _command_sha256(command: Sequence[str]) -> str:
    return hashlib.sha256(json.dumps(list(command), separators=(",", ":")).encode("utf-8")).hexdigest()


def _process_start_identity(pid: int) -> str | None:
    """Linux boot-relative start ticks + cmdline hash prevent PID-reuse aliasing."""
    try:
        stat_fields = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").split()
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return None
    if len(stat_fields) < 22 or not cmdline:
        return None
    return hashlib.sha256(stat_fields[21].encode("ascii") + b"\0" + cmdline).hexdigest()


def _training_progress(config: LongrunConfigV4) -> dict[str, object]:
    """Read only the bounded, atomic child progress fields used by the parent summary."""
    path = config.output_root / "training-progress.json"
    try:
        payload = _json_object(path, "training progress")
    except LongrunError:
        return {}
    if payload.get("schema") != "meta-specialist-recurrent-bc-v4-progress-v1":
        return {}
    allowed = {"stage", "seed", "epoch", "epochs_completed", "epochs_requested", "optimizer_updates_completed", "last_checkpoint_path", "history_row"}
    return {key: payload[key] for key in allowed if key in payload}


def _child_progress(path: Path, *, training: bool) -> dict[str, object]:
    if training:
        try:
            payload = _json_object(path, "training progress")
        except LongrunError:
            return {}
        if payload.get("schema") != "meta-specialist-recurrent-bc-v4-progress-v1":
            return {}
        allowed = {"stage", "seed", "epoch", "epochs_completed", "epochs_requested", "optimizer_updates_completed", "last_checkpoint_path", "history_row"}
        return {key: payload[key] for key in allowed if key in payload}
    try:
        payload = _json_object(path, "evaluation progress")
    except LongrunError:
        return {}
    fields = payload.get("fields")
    if not isinstance(fields, dict):
        fields = {}
    return {
        "stage": "evaluation", "completed": payload.get("completed"), "total": payload.get("total"),
        "rate_per_second": payload.get("rate_per_second"), "eta_seconds": payload.get("eta_seconds"),
        "faults": fields.get("faults"), "score_rate": fields.get("rate"),
    }


def initialize_longrun_v4(config: LongrunConfigV4) -> None:
    """Publish or verify the identity needed before an irreversible GPU child starts."""
    _validate_config(config)
    if config.manifest_path.exists():
        existing = _json_object(config.manifest_path, "longrun manifest")
        if existing.get("schema") != LONGRUN_SCHEMA:
            raise LongrunError(f"existing run manifest has unexpected schema: {config.manifest_path}")
        _sealed_config_identity(config)
        return
    config.output_root.mkdir(parents=True, exist_ok=True)
    _atomic_json(config.manifest_path, _manifest(config, status="pending"))
    _write_progress(config, status="pending", stage="training")


def _current_manifest(config: LongrunConfigV4) -> dict[str, Any]:
    initialize_longrun_v4(config)
    manifest = _json_object(config.manifest_path, "longrun manifest")
    _sealed_config_identity(config)
    return manifest


def _update_manifest(config: LongrunConfigV4, *, status: str, **extra: object) -> None:
    _atomic_json(config.manifest_path, _manifest(config, status=status, **extra))


def mark_interrupted_v4(config: LongrunConfigV4, *, stage: str, returncode: int | None) -> None:
    status = "interrupted_epoch_boundary_resumable" if stage == "training" else "interrupted_restartable"
    _update_manifest(config, status=status, failed_stage=stage, returncode=returncode)
    _write_progress(config, status=status, stage=stage, returncode=returncode)


def require_startable_v4(config: LongrunConfigV4, *, restart_interrupted: bool) -> None:
    manifest = _current_manifest(config)
    if manifest.get("status") == "running":
        pid = manifest.get("pid")
        expected_start = manifest.get("process_start_identity")
        expected_command = manifest.get("command_sha256")
        live_identity = _process_start_identity(pid) if type(pid) is int and _pid_alive(pid) else None
        if (
            live_identity is not None and live_identity == expected_start
            and expected_command == _command_sha256(manifest.get("command", ()))
        ):
            raise LongrunError(f"longrun is already running with PID {pid}")
        stage = str(manifest.get("stage", "training"))
        mark_interrupted_v4(config, stage=stage, returncode=None)
    status = _current_manifest(config).get("status")
    if status == "complete":
        return
    if status not in {"pending", "interrupted_epoch_boundary_resumable", "interrupted_restartable", "failed"}:
        raise LongrunError(f"longrun is not startable from status {status!r}")


def training_command_v4(config: LongrunConfigV4) -> list[str]:
    return [
        config.python, str(TRAINING_SCRIPT),
        "--selection-manifest", str(config.lane["selection_manifest"]),
        "--selection-manifest-sha256", str(config.lane["selection_manifest_sha256"]),
        "--fast-research-subset", "--require-positive-stop", "--device", "cuda:0",
        "--max-records", str(config.max_records),
        "--episodes-per-partition", str(config.episodes_per_partition),
        "--components-per-partition", str(config.components_per_partition),
        "--train-episodes-per-partition", str(config.train_episodes_per_partition),
        "--validation-episodes-per-partition", str(config.effective_validation_episodes_per_partition),
        "--train-components-per-partition", str(config.train_components_per_partition),
        "--validation-components-per-partition", str(config.effective_validation_components_per_partition),
        "--epochs", str(config.epochs), "--patience", str(config.patience),
        "--learning-rate", str(config.learning_rate), "--burn-in", str(config.burn_in),
        "--subset-fraction", str(config.subset_fraction), "--gradient-clip-norm", str(config.gradient_clip_norm),
        "--seeds", ",".join(str(seed) for seed in config.seeds),
        "--tbptt-steps", str(config.tbptt_steps), "--hidden-dim", str(config.hidden_dim),
        "--embedding-dim", str(config.embedding_dim), "--output", str(config.training_output),
        "--resume", "--progress-path", str(config.output_root / "training-progress.json"),
        "--external-run-config-sha256", config_sha256_v4(config),
    ]


def _checkpoint_from_training(result: Mapping[str, object], *, seed: int) -> dict[str, str]:
    try:
        checkpoint = Path(str(result["best_checkpoint_path"])).resolve(strict=True)
    except (KeyError, OSError, RuntimeError) as exc:
        raise LongrunError(f"seed {seed} result has no readable best checkpoint") from exc
    if not checkpoint.is_file():
        raise LongrunError(f"seed {seed} checkpoint is not a regular file")
    file_sha = _require_hex64(result.get("best_checkpoint_file_sha256"), "checkpoint file SHA-256")
    if hashlib.sha256(checkpoint.read_bytes()).hexdigest() != file_sha:
        raise LongrunError(f"seed {seed} checkpoint file SHA-256 changed since training report")
    return {
        "path": str(checkpoint), "file_sha256": file_sha,
        "tensor_state_sha256": _require_hex64(
            result.get("best_checkpoint_tensor_state_sha256"), "checkpoint tensor-state SHA-256",
        ),
    }


def _validate_history(result: Mapping[str, object], *, config: LongrunConfigV4, seed: int) -> None:
    completed = result.get("epochs_completed")
    if result.get("sequence_order_seed") != seed or type(completed) is not int or not 1 <= completed <= config.epochs:
        raise LongrunError(f"seed {seed} history seed/epoch identity differs")
    history = result.get("history")
    if not isinstance(history, list) or len(history) != completed:
        raise LongrunError(f"seed {seed} history length differs")
    update_sum = 0
    for epoch, row in enumerate(history):
        if not isinstance(row, dict) or row.get("epoch") != float(epoch):
            raise LongrunError(f"seed {seed} history epoch is discontinuous")
        for key in ("train_complete_action_nll", "validation_complete_action_nll", "mean_preclip_gradient_norm", "train_elapsed_seconds"):
            value = row.get(key)
            if type(value) not in (float, int) or not __import__("math").isfinite(float(value)) or float(value) < 0.0:
                raise LongrunError(f"seed {seed} history {key} is invalid")
        updates = row.get("optimizer_updates")
        if type(updates) not in (float, int) or int(updates) != updates or updates < 1:
            raise LongrunError(f"seed {seed} history update count is invalid")
        update_sum += int(updates)
    if result.get("optimizer_updates_completed") != update_sum:
        raise LongrunError(f"seed {seed} total updates differ from history")


def _validate_checkpoint_model_config(checkpoint: Mapping[str, str], *, config: LongrunConfigV4) -> Mapping[str, object]:
    try:
        payload = torch.load(checkpoint["path"], map_location="cpu", weights_only=False)
        descriptor = payload.get("descriptor") if isinstance(payload, dict) else None
        model_config = descriptor.get("model_config") if isinstance(descriptor, dict) else None
    except (OSError, RuntimeError, ValueError, TypeError) as exc:
        raise LongrunError("checkpoint model descriptor is unreadable") from exc
    expected = {"hidden_dim": config.hidden_dim, "embedding_dim": config.embedding_dim}
    if not isinstance(model_config, dict) or any(model_config.get(key) != value for key, value in expected.items()):
        raise LongrunError("checkpoint model dimensions differ from sealed longrun config")
    if descriptor.get("tensor_state_sha256") != checkpoint["tensor_state_sha256"]:
        raise LongrunError("checkpoint descriptor tensor SHA differs from training report")
    return descriptor


def _validate_last_resume_lineage(
    result: Mapping[str, object], *, seed: int, config: LongrunConfigV4, card_vocabulary_size: int,
    selected_sha: str, trainer_sha: str, external_sha: str, best_tensor_sha: str,
) -> None:
    try:
        last_path = Path(str(result["last_checkpoint_path"])).resolve(strict=True)
        expected_last = (
            config.training_output.parent / f"{config.training_output.stem}-checkpoints"
            / f"seed-{seed}" / "last-recurrent-bc-v4.pt"
        ).resolve()
        if last_path != expected_last:
            raise LongrunError(f"seed {seed} last resume path differs from sealed layout")
        payload = torch.load(last_path, map_location="cpu", weights_only=False)
        if not isinstance(payload, dict) or payload.get("schema") != "meta-specialist-recurrent-bc-v4-epoch-resume-v1":
            raise LongrunError("last resume checkpoint schema is invalid")
        expected_keys = {
            "schema", "run_config", "sequence_order_seed", "epochs", "next_epoch", "model_state",
            "optimizer_state", "history", "initial_validation_complete_action_nll",
            "best_validation_complete_action_nll", "best_epoch", "stale_epochs",
            "best_validation_by_component", "best_checkpoint_tensor_state_sha256",
            "optimizer_updates_completed", "cumulative_train_elapsed_seconds",
        }
        if set(payload) != expected_keys:
            raise LongrunError("last resume checkpoint key set is invalid")
        stable = payload["run_config"]
        user = stable["user"]
    except LongrunError:
        raise
    except (KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
        raise LongrunError("last resume checkpoint lineage is unreadable") from exc
    if (
        stable.get("selected_objective_sha256") != selected_sha
        or stable.get("trainer_implementation_sha256") != trainer_sha
        or user.get("selected_sequence_sha256") != selected_sha
        or user.get("trainer_implementation_sha256") != trainer_sha
        or user.get("external_run_config_sha256") != external_sha
    ):
        raise LongrunError("last resume checkpoint lineage differs from training report")
    if (
        payload.get("sequence_order_seed") != seed or payload.get("epochs") != config.epochs
        or payload.get("next_epoch") != result.get("epochs_completed")
        or payload.get("history") != result.get("history")
        or payload.get("optimizer_updates_completed") != result.get("optimizer_updates_completed")
        or payload.get("best_checkpoint_tensor_state_sha256") != best_tensor_sha
    ):
        raise LongrunError("last resume checkpoint state/history differs from training report")
    history = result["history"]
    assert isinstance(history, list)
    validations = [float(row["validation_complete_action_nll"]) for row in history]
    best_nll = min(validations)
    best_epoch = validations.index(best_nll)
    initial_nll = result.get("initial_validation_complete_action_nll")
    reported_best = result.get("best_validation_complete_action_nll")
    delta = result.get("validation_delta_nll")
    components = result.get("validation_by_component")
    if (
        type(initial_nll) not in (float, int) or not __import__("math").isfinite(float(initial_nll)) or float(initial_nll) < 0.0
        or type(reported_best) not in (float, int) or not __import__("math").isclose(float(reported_best), best_nll, abs_tol=1e-12)
        or result.get("best_epoch") != best_epoch
        or type(delta) not in (float, int) or not __import__("math").isclose(float(delta), best_nll - float(initial_nll), abs_tol=1e-12)
        or result.get("improved") is not (best_nll < float(initial_nll))
        or not isinstance(components, dict) or not components
        or any(type(value) not in (float, int) or not __import__("math").isfinite(float(value)) or float(value) < 0.0 for value in components.values())
        or payload.get("initial_validation_complete_action_nll") != initial_nll
        or payload.get("best_validation_complete_action_nll") != reported_best
        or payload.get("best_epoch") != best_epoch
        or payload.get("stale_epochs") != len(history) - 1 - best_epoch
        or payload.get("best_validation_by_component") != components
    ):
        raise LongrunError("last resume/report validation summary is inconsistent")
    cumulative = sum(float(row["train_elapsed_seconds"]) for row in history)
    if (
        type(payload.get("cumulative_train_elapsed_seconds")) not in (float, int)
        or not __import__("math").isclose(float(payload["cumulative_train_elapsed_seconds"]), cumulative, abs_tol=1e-9)
        or type(result.get("cumulative_train_elapsed_seconds")) not in (float, int)
        or not __import__("math").isclose(float(result["cumulative_train_elapsed_seconds"]), cumulative, abs_tol=1e-9)
    ):
        raise LongrunError("last resume/report cumulative train elapsed differs")
    model_state = payload.get("model_state")
    optimizer_state = payload.get("optimizer_state")
    if not isinstance(model_state, dict) or not isinstance(optimizer_state, dict):
        raise LongrunError("last resume checkpoint lacks model/Adam state")
    try:
        model = SpecialistModelV4(
            card_vocabulary_size=card_vocabulary_size, hidden_dim=config.hidden_dim,
            embedding_dim=config.embedding_dim, seed=seed,
        )
        model.load_state_dict(model_state, strict=True)
        optimizer = torch.optim.Adam(model.parameters(), lr=config.learning_rate)
        optimizer.load_state_dict(optimizer_state)
    except (RuntimeError, ValueError, TypeError, KeyError) as exc:
        raise LongrunError("last resume checkpoint model/Adam state is incompatible") from exc
    _validate_adam_state_v4(
        optimizer_state, model=model, optimizer_updates=int(result["optimizer_updates_completed"]),
    )


def _validate_adam_state_v4(
    optimizer_state: Mapping[str, object], *, model: SpecialistModelV4, optimizer_updates: int,
) -> None:
    state = optimizer_state.get("state")
    groups = optimizer_state.get("param_groups")
    if not isinstance(state, dict) or not isinstance(groups, list) or optimizer_updates < 1 or not state:
        raise LongrunError("completed updates require nonempty Adam state")
    parameter_ids: list[int] = []
    for group in groups:
        if not isinstance(group, dict) or not isinstance(group.get("params"), list):
            raise LongrunError("Adam parameter groups are invalid")
        parameter_ids.extend(group["params"])
    parameters = list(model.parameters())
    if len(parameter_ids) != len(parameters) or len(set(parameter_ids)) != len(parameter_ids):
        raise LongrunError("Adam parameter ID mapping differs from model")
    parameter_by_id = dict(zip(parameter_ids, parameters, strict=True))
    if not set(state).issubset(parameter_by_id):
        raise LongrunError("Adam state references a non-model parameter ID")
    for parameter_id, moments in state.items():
        if not isinstance(moments, dict):
            raise LongrunError("Adam parameter state is invalid")
        step = moments.get("step")
        step_value = float(step.item()) if isinstance(step, torch.Tensor) and step.numel() == 1 else None
        if step_value is None or not __import__("math").isfinite(step_value) or not 1.0 <= step_value <= float(optimizer_updates):
            raise LongrunError("Adam step is outside completed optimizer update bounds")
        for key in ("exp_avg", "exp_avg_sq"):
            tensor = moments.get(key)
            if not isinstance(tensor, torch.Tensor) or tensor.shape != parameter_by_id[parameter_id].shape or not torch.isfinite(tensor).all().item():
                raise LongrunError(f"Adam {key} tensor is missing, mismatched, or nonfinite")


def validate_training_report_v4(config: LongrunConfigV4) -> dict[int, dict[str, str]]:
    report = _json_object(config.training_output, "training report")
    expected_coverage = {
        "episodes_per_partition": config.episodes_per_partition,
        "components_per_partition": config.components_per_partition,
        "train_episodes_per_partition": config.train_episodes_per_partition,
        "validation_episodes_per_partition": config.effective_validation_episodes_per_partition,
        "train_components_per_partition": config.train_components_per_partition,
        "validation_components_per_partition": config.effective_validation_components_per_partition,
        "require_positive_stop": True,
    }
    if (
        report.get("schema") != TRAINING_SCHEMA or report.get("lane") != config.lane["lane"]
        or report.get("device") != "cuda:0"
        or report.get("selection_manifest_file_sha256") != config.lane["selection_manifest_sha256"]
        or report.get("coverage_target") != expected_coverage
        or report.get("external_run_config_sha256") != config_sha256_v4(config)
    ):
        raise LongrunError("training report does not match the sealed longrun contract")
    coverage = report.get("decoder_coverage_by_partition")
    if not isinstance(coverage, dict):
        raise LongrunError("training report has no decoder coverage")
    for partition in ("train", "validation"):
        row = coverage.get(partition)
        if not isinstance(row, dict) or type(row.get("positive_stop_target_rows")) is not int or row["positive_stop_target_rows"] <= 0:
            raise LongrunError(f"training report lacks positive STOP targets in {partition}")
    results = report.get("seed_results")
    if not isinstance(results, dict) or set(results) != {str(seed) for seed in config.seeds}:
        raise LongrunError("training report does not contain exactly the sealed seed pair")
    checked: dict[int, dict[str, str]] = {}
    expected_training_config = {
        "max_records": config.max_records, "subset_fraction": config.subset_fraction, "burn_in": config.burn_in,
        "epochs": config.epochs, "patience": config.patience, "learning_rate": config.learning_rate,
        "tbptt_steps": config.tbptt_steps, "gradient_clip_norm": config.gradient_clip_norm,
        "hidden_dim": config.hidden_dim, "embedding_dim": config.embedding_dim,
        "seeds": list(config.seeds), "device": "cuda:0",
    }
    training_config = report.get("training_config")
    if (
        not isinstance(training_config, dict)
        or any(training_config.get(key) != value for key, value in expected_training_config.items())
        or type(training_config.get("card_vocabulary_size")) is not int
        or training_config["card_vocabulary_size"] < 1
    ):
        raise LongrunError("training report hyperparameters differ from sealed longrun config")
    selected_sha = _require_hex64(report.get("selected_sequence_sha256"), "selected sequence SHA-256")
    trainer_sha = _require_hex64(report.get("trainer_implementation_sha256"), "trainer implementation SHA-256")
    if trainer_sha != trainer_implementation_sha256_v4():
        raise LongrunError("training report trainer implementation differs from live closure")
    training_identity = {
        "training_config": training_config, "coverage_target": report.get("coverage_target"),
        "selected_sequence_sha256": selected_sha, "trainer_implementation_sha256": trainer_sha,
        "external_run_config_sha256": report.get("external_run_config_sha256"),
        "selection_manifest_file_sha256": report.get("selection_manifest_file_sha256"),
    }
    actual_training_sha = hashlib.sha256(json.dumps(
        training_identity, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")).hexdigest()
    if report.get("training_config_sha256") != actual_training_sha:
        raise LongrunError("training report canonical config SHA-256 differs")
    for seed in config.seeds:
        result = results[str(seed)]
        if not isinstance(result, dict):
            raise LongrunError(f"seed {seed} training result is not an object")
        _validate_history(result, config=config, seed=seed)
        checked[seed] = _checkpoint_from_training(result, seed=seed)
        _validate_checkpoint_model_config(checked[seed], config=config)
        _validate_last_resume_lineage(
            result, seed=seed, config=config, card_vocabulary_size=int(training_config["card_vocabulary_size"]),
            selected_sha=selected_sha, trainer_sha=trainer_sha, external_sha=config_sha256_v4(config),
            best_tensor_sha=checked[seed]["tensor_state_sha256"],
        )
    return checked


def evaluation_command_v4(config: LongrunConfigV4, checkpoint: Mapping[str, str], output: Path) -> list[str]:
    return [
        config.python, str(EVALUATION_SCRIPT), "--checkpoint", checkpoint["path"],
        "--subject-deck-csv", str(config.lane["subject_deck_csv"]),
        "--subject-archetype-id", str(config.lane["subject_archetype_id"]),
        "--opponent-count", str(OPPONENT_COUNT), "--games-per-seat", str(config.games_per_seat),
        "--base-seed", str(config.base_seed), "--max-steps", str(config.max_steps), "--output", str(output),
        "--progress-path", str(output.with_suffix(".progress.json")),
    ]


def validate_evaluation_report_v4(config: LongrunConfigV4, path: Path, checkpoint: Mapping[str, str]) -> None:
    report = _json_object(path, "held-out evaluation report")
    recorded = report.get("checkpoint")
    if not isinstance(recorded, dict):
        raise LongrunError("held-out evaluation has no checkpoint provenance")
    expected_games = OPPONENT_COUNT * 2 * config.games_per_seat
    subject_deck = Path(str(config.lane["subject_deck_csv"])).resolve()
    expected_ids = list(EVAL_HELD_OUT_V1)
    pool = load_opponent_pool_v1(default_pool_root_v1(ROOT))
    expected_fingerprints = []
    for opponent_id in expected_ids:
        opponent = resolve_opponent_v1(pool, opponent_id, subject_deck_csv_path="x")
        expected_fingerprints.append({
            "opponent_id": opponent_id, "canonical_deck_hash": opponent.canonical_deck_hash,
            "deck_file_sha256": hashlib.sha256(Path(opponent.deck_csv_path).read_bytes()).hexdigest(),
            "policy_hash": opponent.policy_hash,
        })
    if (
        report.get("schema_version") != EVALUATION_SCHEMA
        or recorded.get("file_sha256") != checkpoint["file_sha256"]
        or recorded.get("tensor_state_sha256") != checkpoint["tensor_state_sha256"]
        or Path(str(recorded.get("path", ""))).resolve() != Path(checkpoint["path"]).resolve()
        or report.get("games_per_seat") != config.games_per_seat
        or report.get("base_seed") != config.base_seed
        or report.get("max_steps") != config.max_steps
        or report.get("subject_archetype_id") != config.lane["subject_archetype_id"]
        or Path(str(report.get("subject_deck_csv", ""))).resolve() != subject_deck
        or report.get("subject_deck_file_sha256") != hashlib.sha256(subject_deck.read_bytes()).hexdigest()
        or report.get("requested_games") != expected_games
        or report.get("fixed_held_out_opponent_ids") != expected_ids
        or report.get("opponent_ids") != expected_ids
        or report.get("opponent_fingerprints") != expected_fingerprints
        or report.get("evaluation_implementation_sha256") != evaluation_implementation_sha256_v1()
        or report.get("faults") != 0 or report.get("comparison_status") != "valid"
    ):
        raise LongrunError("held-out evaluation does not match the sealed fault-free longrun protocol")
    _validate_evaluation_aggregates_v4(report, expected_ids=expected_ids, games_per_seat=config.games_per_seat)


def _validated_wdlf_row(row: object, *, requested: int, label: str) -> dict[str, int]:
    if not isinstance(row, dict):
        raise LongrunError(f"{label} aggregate is not an object")
    values: dict[str, int] = {}
    for key in ("w", "d", "l", "f", "requested"):
        value = row.get(key)
        if type(value) is not int or value < 0:
            raise LongrunError(f"{label} {key} is not a nonnegative integer")
        values[key] = value
    if values["requested"] != requested or sum(values[key] for key in ("w", "d", "l", "f")) != requested:
        raise LongrunError(f"{label} requested/WDLF totals differ")
    expected_score = (values["w"] + 0.5 * values["d"]) / requested
    score = row.get("score_rate")
    if type(score) not in (float, int) or not __import__("math").isclose(float(score), expected_score, abs_tol=1e-12):
        raise LongrunError(f"{label} score_rate differs from WDLF")
    return values


def _validate_evaluation_aggregates_v4(
    report: Mapping[str, object], *, expected_ids: list[str], games_per_seat: int,
) -> None:
    requested = len(expected_ids) * 2 * games_per_seat
    overall = {key: report.get(name) for key, name in (("w", "wins"), ("d", "draws"), ("l", "losses"), ("f", "faults"))}
    if any(type(value) is not int or value < 0 for value in overall.values()):
        raise LongrunError("held-out overall WDLF is invalid")
    if sum(int(value) for value in overall.values()) != requested:
        raise LongrunError("held-out overall WDLF does not cover requested games")
    played = int(overall["w"]) + int(overall["d"]) + int(overall["l"])
    if report.get("games_played") != played or report.get("score_denominator_games") != requested:
        raise LongrunError("held-out played/denominator totals differ")
    expected_score = (int(overall["w"]) + 0.5 * int(overall["d"])) / requested
    score = report.get("score_rate")
    if type(score) not in (float, int) or not __import__("math").isclose(float(score), expected_score, abs_tol=1e-12):
        raise LongrunError("held-out score_rate differs from WDLF")
    expected_ci = list(_wilson(int(overall["w"]) + 0.5 * int(overall["d"]), requested))
    ci = report.get("score_ci95")
    if not isinstance(ci, list) or len(ci) != 2 or any(
        type(value) not in (float, int) or not __import__("math").isclose(float(value), expected, abs_tol=1e-12)
        for value, expected in zip(ci, expected_ci, strict=True)
    ):
        raise LongrunError("held-out Wilson interval differs from WDLF")
    seat = report.get("seat")
    per_opponent = report.get("per_opponent")
    if not isinstance(seat, dict) or set(seat) != {"0", "1"}:
        raise LongrunError("held-out seat aggregate keys differ")
    if not isinstance(per_opponent, dict) or set(per_opponent) != set(expected_ids):
        raise LongrunError("held-out opponent aggregate keys/order differ")
    seat_rows = [_validated_wdlf_row(seat[key], requested=len(expected_ids) * games_per_seat, label=f"seat {key}") for key in ("0", "1")]
    opponent_rows = [_validated_wdlf_row(per_opponent[key], requested=2 * games_per_seat, label=f"opponent {key}") for key in expected_ids]
    for key in ("w", "d", "l", "f", "requested"):
        if sum(row[key] for row in seat_rows) != (requested if key == "requested" else overall[key]):
            raise LongrunError("held-out seat aggregates do not sum to overall")
        if sum(row[key] for row in opponent_rows) != (requested if key == "requested" else overall[key]):
            raise LongrunError("held-out opponent aggregates do not sum to overall")


def _run_child(config: LongrunConfigV4, *, stage: str, command: Sequence[str], heartbeat_seconds: float) -> None:
    started = time.monotonic()
    process = subprocess.Popen(list(command), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    captured = bytearray()
    stderr_total = 0
    capture_limit = 64 * 1024
    def drain_stderr() -> None:
        nonlocal stderr_total
        assert process.stderr is not None
        for block in iter(lambda: process.stderr.read(8192), b""):
            stderr_total += len(block)
            remaining = capture_limit - len(captured)
            if remaining > 0:
                captured.extend(block[:remaining])
    stderr_thread = threading.Thread(target=drain_stderr, name=f"v4-{stage}-stderr", daemon=True)
    stderr_thread.start()
    progress_index = list(command).index("--progress-path") if "--progress-path" in command else -1
    child_progress_path = Path(command[progress_index + 1]) if progress_index >= 0 else config.output_root / "missing-progress.json"
    training_stage = stage == "training"
    progress_total = config.epochs * len(config.seeds) if training_stage else OPPONENT_COUNT * 2 * config.games_per_seat
    reporter = ProgressReporterV1(
        total=progress_total,
        desc=f"v4-longrun {stage}", snapshot_interval_seconds=heartbeat_seconds,
    )
    rendered_completed = 0
    old_handlers: dict[int, object] = {}
    def interrupt_handler(signum: int, _frame: object) -> None:
        raise KeyboardInterrupt(f"signal {signum}")
    for signum in (signal.SIGTERM, signal.SIGHUP):
        old_handlers[signum] = signal.signal(signum, interrupt_handler)
    try:
        start_identity = _process_start_identity(process.pid)
        _update_manifest(
            config, status="running", stage=stage, pid=process.pid, command=list(command),
            command_sha256=_command_sha256(command), process_start_identity=start_identity,
        )
        while process.poll() is None:
            child_progress = _child_progress(child_progress_path, training=training_stage)
            progress = {
                "pid": process.pid, "invocation_elapsed_seconds": round(time.monotonic() - started, 1),
                "child_stage": child_progress.get("stage", stage),
                **{key: value for key, value in child_progress.items() if key != "stage"},
            }
            history_row = child_progress.get("history_row")
            if isinstance(history_row, dict):
                progress["latest_train_complete_action_nll"] = history_row.get("train_complete_action_nll")
                progress["latest_validation_complete_action_nll"] = history_row.get("validation_complete_action_nll")
                progress["latest_gradient_norm"] = history_row.get("mean_preclip_gradient_norm")
            _write_progress(config, status="running", stage=stage, **progress)
            completed = 0
            if training_stage and type(child_progress.get("seed")) is int and type(child_progress.get("epochs_completed")) is int:
                completed = config.seeds.index(child_progress["seed"]) * config.epochs + child_progress["epochs_completed"]
            elif not training_stage and type(child_progress.get("completed")) is int:
                completed = int(child_progress["completed"])
            reporter.update(max(0, completed - rendered_completed), **{
                "stage": child_progress.get("stage", stage), "seed": child_progress.get("seed"), "epoch": child_progress.get("epochs_completed"),
                "update": child_progress.get("optimizer_updates_completed"),
                "train_nll": progress.get("latest_train_complete_action_nll"),
                "valid_nll": progress.get("latest_validation_complete_action_nll"),
                "grad": progress.get("latest_gradient_norm"), "faults": child_progress.get("faults"),
                "score": child_progress.get("score_rate"),
            })
            rendered_completed = max(rendered_completed, completed)
            time.sleep(heartbeat_seconds)
        process.wait()
    except BaseException as exc:
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=10.0)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        stderr_thread.join(timeout=5.0)
        diagnostic = {
            "schema": LONGRUN_SCHEMA, "stage": stage, "returncode": process.returncode,
            "exception": f"{type(exc).__name__}: {exc}",
            "stderr_excerpt": captured.decode("utf-8", errors="replace"),
            "stderr_bytes_total": stderr_total, "stderr_truncated": stderr_total > len(captured),
        }
        _atomic_json(config.output_root / f"{stage}-failure.json", diagnostic)
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            try:
                mark_interrupted_v4(config, stage=stage, returncode=process.returncode)
            except LongrunError:
                pass
            reporter.close(status="interrupted")
        else:
            try:
                _update_manifest(config, status="failed", failed_stage=stage, returncode=process.returncode, error=diagnostic["exception"])
                _write_progress(config, status="failed", stage=stage, returncode=process.returncode, error=diagnostic["exception"])
            except LongrunError:
                pass
            reporter.close(status="failed")
        raise
    finally:
        for signum, handler in old_handlers.items():
            signal.signal(signum, handler)
    stderr_thread.join(timeout=5.0)
    if process.returncode != 0:
        diagnostic = {
            "schema": LONGRUN_SCHEMA, "stage": stage, "returncode": process.returncode,
            "stderr_excerpt": captured.decode("utf-8", errors="replace"),
            "stderr_bytes_total": stderr_total, "stderr_truncated": stderr_total > len(captured),
        }
        _atomic_json(config.output_root / f"{stage}-failure.json", diagnostic)
        if process.returncode is not None and process.returncode < 0:
            try:
                mark_interrupted_v4(config, stage=stage, returncode=process.returncode)
            except LongrunError:
                pass
            reporter.close(status="interrupted")
        else:
            try:
                _update_manifest(config, status="failed", failed_stage=stage, returncode=process.returncode)
                _write_progress(config, status="failed", stage=stage, returncode=process.returncode, diagnostic=str(config.output_root / f"{stage}-failure.json"))
            except LongrunError:
                pass
            reporter.close(status="failed")
        raise LongrunError(f"{stage} child exited with return code {process.returncode}")
    reporter.update(max(0, progress_total - rendered_completed), stage=stage, status="complete")
    reporter.close(status="done")


def run_longrun_v4(config: LongrunConfigV4, *, restart_interrupted: bool, heartbeat_seconds: float) -> None:
    """Run/reuse sealed stages, resuming training only from saved epoch boundaries."""
    if heartbeat_seconds <= 0.0:
        raise LongrunError("heartbeat seconds must be positive")
    require_startable_v4(config, restart_interrupted=restart_interrupted)
    try:
        checkpoints = validate_training_report_v4(config)
    except LongrunError:
        if config.training_output.exists() and not restart_interrupted:
            raise
        _run_child(config, stage="training", command=training_command_v4(config), heartbeat_seconds=heartbeat_seconds)
        checkpoints = validate_training_report_v4(config)
    evaluations: dict[str, str] = {}
    for seed, checkpoint in checkpoints.items():
        output = config.output_root / f"archaludon-seed-{seed}-heldout-{OPPONENT_COUNT * 2 * config.games_per_seat}.json"
        try:
            validate_evaluation_report_v4(config, output, checkpoint)
        except LongrunError:
            if output.exists() and not restart_interrupted:
                raise
            _run_child(
                config, stage=f"heldout_seed_{seed}",
                command=evaluation_command_v4(config, checkpoint, output), heartbeat_seconds=heartbeat_seconds,
            )
            validate_evaluation_report_v4(config, output, checkpoint)
        evaluations[str(seed)] = str(output)
    _update_manifest(
        config, status="complete", training_report=str(config.training_output), checkpoints=checkpoints,
        heldout_evaluations=evaluations,
    )
    _write_progress(config, status="complete", stage="complete", heldout_evaluations=evaluations)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=ROOT / "runs/meta-specialist-v4-archaludon-longrun")
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--max-records", type=int, default=131072)
    parser.add_argument("--episodes-per-partition", type=int, default=512)
    parser.add_argument("--components-per-partition", type=int, default=512)
    parser.add_argument("--validation-episodes-per-partition", type=int, default=128)
    parser.add_argument("--validation-components-per-partition", type=int, default=128)
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--patience", type=int, default=2)
    parser.add_argument("--learning-rate", type=float, default=1e-3)
    parser.add_argument("--burn-in", type=int, default=1)
    parser.add_argument("--subset-fraction", type=float, default=0.05)
    parser.add_argument("--gradient-clip-norm", type=float, default=1.0)
    parser.add_argument("--games-per-seat", type=int, default=8)
    parser.add_argument("--base-seed", type=int, default=9_800_000)
    parser.add_argument("--max-steps", type=int, default=2_000)
    parser.add_argument("--heartbeat-seconds", type=float, default=10.0)
    parser.add_argument("--restart-interrupted", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    config = LongrunConfigV4(
        lane=DEFAULT_LANE, output_root=args.output_root.resolve(), python=args.python,
        max_records=args.max_records, episodes_per_partition=args.episodes_per_partition,
        components_per_partition=args.components_per_partition, epochs=args.epochs,
        patience=args.patience, seeds=SEEDS, hidden_dim=128, embedding_dim=64, tbptt_steps=8,
        games_per_seat=args.games_per_seat, base_seed=args.base_seed, max_steps=args.max_steps,
        validation_episodes_per_partition=args.validation_episodes_per_partition,
        validation_components_per_partition=args.validation_components_per_partition,
        learning_rate=args.learning_rate, burn_in=args.burn_in, subset_fraction=args.subset_fraction,
        gradient_clip_norm=args.gradient_clip_norm,
    )
    initialize_longrun_v4(config)
    run_longrun_v4(config, restart_interrupted=args.restart_interrupted, heartbeat_seconds=args.heartbeat_seconds)
    print(json.dumps(_json_object(config.manifest_path, "longrun manifest"), ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
