"""sealed replay を継続消費する R2D3 learner service。"""

from __future__ import annotations

from datetime import datetime, timezone
import math
import os
import signal
import socket
import shutil
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Sequence

from mage_ptcg.policy_learning.r2d3.checkpoint import (
    load_checkpoint,
    save_checkpoint,
)
from mage_ptcg.policy_learning.r2d3.learner import LearnerConfig, R2D3Learner
from mage_ptcg.policy_learning.r2d3.model import (
    R2D3ModelConfig,
    RecurrentDistributionalQ,
)
from mage_ptcg.observability.cabt_trace import canonical_deck_sha256
from mage_ptcg.bootstrap_champion.initializer import (
    load_bootstrap_manifest,
    load_bootstrap_weights,
)

from .batching import PackedReplayBatcher, learner_batch
from .checkpoint_stream import publish_checkpoint
from .contracts import (
    LeagueContractError,
    atomic_write_json,
    content_id,
    file_sha256,
    utc_now,
)
from .replay_sealer import load_sealed_replay


@dataclass(frozen=True, slots=True)
class ContinuousLearnerConfig:
    batch_size: int = 32
    learning_rate: float = 1e-4
    demonstration_ratio: float = 1.0 / 32.0
    beta_start: float = 0.4
    beta_steps: int = 200_000
    checkpoint_interval: int = 1_000
    progress_interval_seconds: float = 10.0
    seed: int = 71_000
    device: str = "cpu"
    prepack_replay: bool = False
    resident_replay: bool = False
    pin_memory: bool = False
    mixed_precision: str = "disabled"
    fused_optimizer: bool = False
    matmul_precision: str = "highest"
    learning_rate_schedule: str = "constant"
    warmup_replay_passes: float = 0.0
    cosine_decay_replay_passes: float = 0.0
    minimum_learning_rate_scale: float = 0.1

    def validate(self) -> None:
        if (
            self.batch_size < 1
            or self.learning_rate <= 0
            or not 0 <= self.demonstration_ratio <= 1
            or not 0 <= self.beta_start <= 1
            or self.beta_steps < 1
            or self.checkpoint_interval < 1
            or self.progress_interval_seconds <= 0
            or self.mixed_precision not in {"disabled", "bf16"}
            or self.matmul_precision not in {"highest", "high", "medium"}
            or self.learning_rate_schedule not in {"constant", "cosine"}
            or self.warmup_replay_passes < 0
            or self.cosine_decay_replay_passes < 0
            or not 0 < self.minimum_learning_rate_scale <= 1
        ):
            raise LeagueContractError("invalid continuous learner configuration")

    def training_identity_payload(self) -> dict[str, Any]:
        """Preserve the v1 identity for execution-only batching improvements."""

        payload = {
            key: getattr(self, key)
            for key in (
                "batch_size",
                "learning_rate",
                "demonstration_ratio",
                "beta_start",
                "beta_steps",
                "checkpoint_interval",
                "progress_interval_seconds",
                "seed",
                "device",
            )
        }
        if self.mixed_precision != "disabled":
            payload["mixed_precision"] = self.mixed_precision
        if self.fused_optimizer:
            payload["fused_optimizer"] = True
        if self.matmul_precision != "highest":
            payload["matmul_precision"] = self.matmul_precision
        if self.learning_rate_schedule != "constant":
            payload.update(
                {
                    "learning_rate_schedule": self.learning_rate_schedule,
                    "warmup_replay_passes": self.warmup_replay_passes,
                    "cosine_decay_replay_passes": self.cosine_decay_replay_passes,
                    "minimum_learning_rate_scale": self.minimum_learning_rate_scale,
                }
            )
        return payload


def updates_for_replay_passes(
    *, sequence_count: int, batch_size: int, replay_passes: float
) -> int:
    """指定 sequence 周回数を超えない整数 update 上限を返す。"""

    if sequence_count < 1 or batch_size < 1 or replay_passes <= 0:
        raise LeagueContractError("replay pass budget must be positive")
    updates = math.floor(
        sequence_count * replay_passes / min(batch_size, sequence_count)
    )
    if updates < 1:
        raise LeagueContractError(
            "replay pass budget is smaller than one learner batch"
        )
    return updates


def learner_progress_status(
    payload: dict[str, Any], *, stale_after_seconds: float, now: datetime | None = None
) -> dict[str, Any]:
    """heartbeat が古い RUNNING 表示を observer 側で STALE として扱う。"""

    if stale_after_seconds <= 0:
        raise LeagueContractError("stale threshold must be positive")
    status = str(payload.get("status", "UNKNOWN"))
    updated_at = payload.get("updated_at")
    if status not in {"RUNNING", "STOPPING"} or not isinstance(updated_at, str):
        return {"status": status, "heartbeat_age_seconds": None, "is_stale": False}
    try:
        heartbeat = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise LeagueContractError("learner progress has an invalid updated_at") from exc
    current = now or datetime.now(timezone.utc)
    if heartbeat.tzinfo is None:
        raise LeagueContractError("learner progress updated_at must include timezone")
    age = max(0.0, (current - heartbeat).total_seconds())
    stale = age > stale_after_seconds
    return {
        "status": "STALE" if stale else status,
        "heartbeat_age_seconds": round(age, 3),
        "is_stale": stale,
    }


class ContinuousLearner:
    def __init__(
        self,
        *,
        replay_manifest_path: Path,
        population_epoch_id: str,
        output_root: Path,
        deck: Sequence[int],
        model_config: R2D3ModelConfig = R2D3ModelConfig(),
        learner_config: LearnerConfig = LearnerConfig(),
        service_config: ContinuousLearnerConfig = ContinuousLearnerConfig(),
        resume_checkpoint: Path | None = None,
        resume_training_identity_hash: str | None = None,
        bootstrap_checkpoint: Path | None = None,
    ) -> None:
        import torch

        service_config.validate()
        learner_config.validate()
        if resume_checkpoint is not None and bootstrap_checkpoint is not None:
            raise LeagueContractError("resume checkpoint and Bootstrap checkpoint are mutually exclusive")
        self.device = torch.device(service_config.device)
        if self.device.type == "cuda" and not torch.cuda.is_available():
            raise LeagueContractError(
                "continuous learner requested CUDA but it is unavailable"
            )
        if service_config.pin_memory and self.device.type != "cuda":
            raise LeagueContractError("pin_memory requires a CUDA learner")
        if service_config.resident_replay and (
            not service_config.prepack_replay or self.device.type != "cuda"
        ):
            raise LeagueContractError(
                "resident_replay requires prepack_replay on CUDA"
            )
        if service_config.mixed_precision == "bf16" and (
            self.device.type != "cuda" or not torch.cuda.is_bf16_supported()
        ):
            raise LeagueContractError("bf16 requires a supported CUDA learner")
        if service_config.fused_optimizer and self.device.type != "cuda":
            raise LeagueContractError("fused optimizer requires a CUDA learner")
        torch.set_float32_matmul_precision(service_config.matmul_precision)
        from .contracts import load_json

        self.output_root = Path(output_root)
        self.replay_manifest_path = self._materialize_replay_input(
            Path(replay_manifest_path)
        )
        replay_manifest = load_json(self.replay_manifest_path)
        self.replay_dataset_version_id = str(
            replay_manifest["replay_dataset_version_id"]
        )
        if replay_manifest.get("population_epoch_id") != population_epoch_id:
            raise LeagueContractError("replay and population epoch mismatch")
        self.replay = load_sealed_replay(self.replay_manifest_path)
        self.population_epoch_id = population_epoch_id
        self.deck = list(deck)
        self.model_config = model_config
        self.learner_config = learner_config
        self.service_config = service_config
        torch.manual_seed(service_config.seed)
        self.model = RecurrentDistributionalQ(model_config).to(self.device)
        self.optimizer = torch.optim.AdamW(
            self.model.parameters(),
            lr=service_config.learning_rate,
            fused=service_config.fused_optimizer,
        )
        updates_per_pass = len(self.replay) / min(service_config.batch_size, len(self.replay))
        warmup_updates = math.ceil(service_config.warmup_replay_passes * updates_per_pass)
        decay_updates = math.ceil(service_config.cosine_decay_replay_passes * updates_per_pass)

        def learning_rate_scale(step: int) -> float:
            if service_config.learning_rate_schedule == "constant":
                return 1.0
            if warmup_updates and step < warmup_updates:
                return (step + 1) / warmup_updates
            if not decay_updates:
                return 1.0
            progress = min(1.0, max(0.0, (step - warmup_updates) / decay_updates))
            cosine = 0.5 * (1.0 + math.cos(math.pi * progress))
            return service_config.minimum_learning_rate_scale + (
                1.0 - service_config.minimum_learning_rate_scale
            ) * cosine

        self.scheduler = torch.optim.lr_scheduler.LambdaLR(
            self.optimizer, lr_lambda=learning_rate_scale
        )
        self.learner = R2D3Learner(
            self.model, self.optimizer, config=learner_config
        )
        self.bootstrap_checkpoint_id: str | None = None
        self.bootstrap_champion_id: str | None = None
        if bootstrap_checkpoint is not None:
            bootstrap_manifest = load_bootstrap_manifest(Path(bootstrap_checkpoint))
            model_config_hash = content_id(
                "bootstrap-model-config-v1", asdict(model_config)
            )
            action_schema_hash = content_id(
                "bootstrap-action-schema-v1",
                {
                    "state_encoder_version": "semantic-public-state-v1",
                    "action_encoder_version": "semantic-legal-action-v1",
                    "state_size": model_config.state_size,
                    "action_size": model_config.action_size,
                },
            )
            if bootstrap_manifest.model_config_hash != model_config_hash:
                raise LeagueContractError("Bootstrap model configuration differs from learner")
            if bootstrap_manifest.action_schema_hash != action_schema_hash:
                raise LeagueContractError("Bootstrap action schema differs from learner")
            if bootstrap_manifest.deck_hash != canonical_deck_sha256(self.deck):
                raise LeagueContractError("Bootstrap deck differs from learner deck")
            load_bootstrap_weights(
                Path(bootstrap_checkpoint),
                model=self.model,
                target=self.learner.target,
                expected_manifest=bootstrap_manifest,
            )
            self.bootstrap_checkpoint_id = bootstrap_manifest.bootstrap_checkpoint_id
            self.bootstrap_champion_id = bootstrap_manifest.bootstrap_champion_id
        self.batcher = (
            PackedReplayBatcher(
                self.replay,
                n_step=learner_config.n_step,
                opponent_classes=model_config.opponent_classes,
                deck_family_classes=model_config.deck_family_classes,
                action_type_classes=model_config.action_type_classes,
                eager=True,
                show_progress=True,
                progress_interval_seconds=service_config.progress_interval_seconds,
            )
            if service_config.prepack_replay
            else None
        )
        if self.batcher is not None and (
            service_config.pin_memory or service_config.resident_replay
        ):
            self.batcher.reserve_pinned(service_config.batch_size)
        if self.batcher is not None and service_config.resident_replay:
            self.batcher.materialize_resident(
                self.device,
                chunk_size=service_config.batch_size,
                show_progress=True,
                progress_interval_seconds=service_config.progress_interval_seconds,
            )
        self.use_bf16 = service_config.mixed_precision == "bf16"
        self.training_identity_hash = content_id(
            "continuous-training-identity-v1",
            {
                "population_epoch_id": population_epoch_id,
                "replay_dataset_version_id": self.replay_dataset_version_id,
                "model_config": asdict(model_config),
                "learner_config": asdict(learner_config),
                "service_config": service_config.training_identity_payload(),
                "bootstrap_checkpoint_id": self.bootstrap_checkpoint_id,
                "bootstrap_champion_id": self.bootstrap_champion_id,
            },
        )
        if resume_checkpoint is not None:
            self.learner.steps = load_checkpoint(
                resume_checkpoint,
                model=self.model,
                target=self.learner.target,
                optimizer=self.optimizer,
                scheduler=self.scheduler,
                replay=self.replay,
                expected_population_hash=population_epoch_id,
                expected_replay_manifest_hash=self.replay_dataset_version_id,
                expected_training_identity_hash=(
                    resume_training_identity_hash or self.training_identity_hash
                ),
                map_location=self.device,
                strict_state=True,
            )
        self.stop_requested = False
        self.last_metrics: dict[str, Any] | None = None
        self.last_checkpoint: dict[str, Any] | None = None
        self.requested_max_replay_passes: float | None = None

    def _materialize_replay_input(self, source_manifest_path: Path) -> Path:
        """Resume可能なlearner outputへsealed replayを不変コピーする。"""

        from .contracts import load_json

        source_manifest_path = source_manifest_path.resolve()
        source_manifest = load_json(source_manifest_path)
        replay_dataset_version_id = str(source_manifest["replay_dataset_version_id"])
        source_replay_path = source_manifest_path.parent / str(
            source_manifest["replay_file"]
        )
        if not source_replay_path.is_file():
            raise LeagueContractError(
                f"sealed replay file is missing: {source_replay_path}"
            )
        destination_root = (
            self.output_root / "replay_inputs" / replay_dataset_version_id
        )
        destination_manifest_path = destination_root / "manifest.json"
        destination_replay_path = destination_root / source_replay_path.name
        if source_manifest_path == destination_manifest_path.resolve():
            return destination_manifest_path
        if destination_manifest_path.exists() or destination_replay_path.exists():
            if not (
                destination_manifest_path.is_file()
                and destination_replay_path.is_file()
                and destination_manifest_path.read_bytes()
                == source_manifest_path.read_bytes()
                and file_sha256(destination_replay_path)
                == source_manifest.get("replay_sha256")
            ):
                raise LeagueContractError(
                    "learner output already has a different replay input snapshot"
                )
            return destination_manifest_path
        destination_root.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_manifest_path, destination_manifest_path)
        shutil.copy2(source_replay_path, destination_replay_path)
        return destination_manifest_path

    def request_stop(self, *_args: object) -> None:
        self.stop_requested = True

    def update_once(self) -> dict[str, Any]:
        step = self.learner.steps + 1
        beta = min(
            1.0,
            self.service_config.beta_start
            + (1.0 - self.service_config.beta_start)
            * step
            / self.service_config.beta_steps,
        )
        sample = self.replay.sample(
            min(self.service_config.batch_size, len(self.replay)),
            beta=beta,
            demonstration_ratio=self.service_config.demonstration_ratio,
            seed=self.service_config.seed + step,
            episode_first=True,
        )
        if self.batcher is not None and self.service_config.resident_replay:
            batch = self.batcher.resident_batch(sample, self.device)
        elif self.batcher is None:
            batch = learner_batch(
                sample,
                self.device,
                n_step=self.learner_config.n_step,
                opponent_classes=self.model_config.opponent_classes,
                deck_family_classes=self.model_config.deck_family_classes,
                action_type_classes=self.model_config.action_type_classes,
            )
        else:
            batch = self.batcher.learner_batch(
                sample,
                self.device,
                pin_memory=self.service_config.pin_memory,
            )
        import torch

        with torch.autocast(
            device_type=self.device.type,
            dtype=torch.bfloat16,
            enabled=self.use_bf16,
        ):
            metrics = self.learner.update(**batch)
        if not all(
            math.isfinite(float(metrics[key]))
            for key in ("loss", "td_error_mean", "gradient_norm")
        ):
            raise FloatingPointError("continuous learner produced non-finite metrics")
        sequence_priorities = metrics.pop("sequence_priorities")
        self.replay.update_priorities(
            sample.indices,
            sequence_priorities,
            importance=sample.weights,
        )
        self.scheduler.step()
        self.last_metrics = {
            **metrics,
            "step": self.learner.steps,
            "beta": beta,
            "learning_rate": self.optimizer.param_groups[0]["lr"],
        }
        return self.last_metrics

    def checkpoint(self) -> dict[str, Any]:
        checkpoint_dir = self.output_root / "checkpoints"
        checkpoint_path = (
            checkpoint_dir / f"r2d3-step-{self.learner.steps:012d}.pt"
        )
        metadata = save_checkpoint(
            checkpoint_path,
            model=self.model,
            target=self.learner.target,
            optimizer=self.optimizer,
            scheduler=self.scheduler,
            replay=self.replay,
            population_hash=self.population_epoch_id,
            replay_manifest_hash=self.replay_dataset_version_id,
            training_identity_hash=self.training_identity_hash,
            step=self.learner.steps,
            strict_state=True,
        )
        published: dict[str, Any] | None = None
        if self.model_config.state_size == 128 and self.model_config.action_size == 64:
            published = publish_checkpoint(
                checkpoint_path=checkpoint_path,
                output_root=self.output_root / "stream",
                model_config=self.model_config,
                deck=self.deck,
            )
        self.last_checkpoint = {
            **metadata,
            "checkpoint_path": str(checkpoint_path),
            "published": published,
        }
        return self.last_checkpoint

    def _parameter_l2_norm(self) -> float:
        import torch

        with torch.no_grad():
            squared = sum(
                float(torch.sum(parameter.detach().float().square()).item())
                for parameter in self.model.parameters()
            )
        return math.sqrt(squared)

    def _progress(self, started: float, *, status: str | None = None, completed: int = 0) -> None:
        payload = {
            "schema_version": 2,
            "status": status or ("STOPPING" if self.stop_requested else "RUNNING"),
            "population_epoch_id": self.population_epoch_id,
            "replay_dataset_version_id": self.replay_dataset_version_id,
            "replay_manifest_path": str(self.replay_manifest_path),
            "step": self.learner.steps,
            "process_id": os.getpid(),
            "host": socket.gethostname(),
            "elapsed_seconds": time.monotonic() - started,
            "updates_this_invocation": completed,
            "nominal_replay_passes_this_invocation": (
                completed * min(self.service_config.batch_size, len(self.replay)) / len(self.replay)
            ),
            "requested_max_replay_passes": self.requested_max_replay_passes,
            "model_parameter_l2_norm": self._parameter_l2_norm(),
            "bootstrap_checkpoint_id": self.bootstrap_checkpoint_id,
            "bootstrap_champion_id": self.bootstrap_champion_id,
            "last_metrics": self.last_metrics,
            "last_checkpoint": self.last_checkpoint,
            "updated_at": utc_now(),
        }
        atomic_write_json(self.output_root / "progress_summary.json", payload)

    def run(
        self, *, max_updates: int | None = None, requested_max_replay_passes: float | None = None
    ) -> dict[str, Any]:
        import torch

        if max_updates is not None and max_updates < 0:
            raise LeagueContractError("max_updates must be non-negative")
        self.requested_max_replay_passes = requested_max_replay_passes
        for current_signal in (signal.SIGINT, signal.SIGTERM):
            signal.signal(current_signal, self.request_stop)
        started = time.monotonic()
        last_progress = started
        completed = 0
        progress_bar: Any | None = None
        if sys.stderr.isatty():
            try:
                from tqdm import tqdm

                progress_bar = tqdm(
                    total=max_updates,
                    initial=0,
                    unit="update",
                    dynamic_ncols=True,
                    desc="continuous-r2d3",
                )
            except ImportError:
                progress_bar = None
        while not self.stop_requested and (
            max_updates is None or completed < max_updates
        ):
            metrics = self.update_once()
            completed += 1
            if progress_bar is not None:
                progress_bar.update(1)
                elapsed = max(time.monotonic() - started, 1e-9)
                sequences_per_second = (
                    completed
                    * min(self.service_config.batch_size, len(self.replay))
                    / elapsed
                )
                gpu_memory_mb = (
                    torch.cuda.memory_allocated(self.device) / 2**20
                    if self.device.type == "cuda"
                    else 0.0
                )
                progress_bar.set_postfix(
                    loss=f"{metrics['loss']:.4f}",
                    step=self.learner.steps,
                    seq_s=f"{sequences_per_second:.0f}",
                    gpu_mb=f"{gpu_memory_mb:.0f}",
                    fault=0,
                )
            if self.learner.steps % self.service_config.checkpoint_interval == 0:
                self.checkpoint()
            now = time.monotonic()
            if now - last_progress >= self.service_config.progress_interval_seconds:
                self._progress(started, completed=completed)
                if progress_bar is None:
                    elapsed = max(now - started, 1e-9)
                    sequence_rate = (
                        completed
                        * min(self.service_config.batch_size, len(self.replay))
                        / elapsed
                    )
                    print(
                        f"stage=train completed={completed} "
                        f"rate={completed / elapsed:.2f}/s "
                        f"sequences_per_second={sequence_rate:.0f} fault=0",
                        flush=True,
                    )
                last_progress = now
        if progress_bar is not None:
            progress_bar.close()
        if self.last_checkpoint is None or self.last_checkpoint.get("step") != (
            self.learner.steps
        ):
            self.checkpoint()
        final_status = "STOPPED" if self.stop_requested else "COMPLETED"
        self._progress(started, status=final_status, completed=completed)
        return {
            "status": final_status,
            "updates": completed,
            "step": self.learner.steps,
            "last_metrics": self.last_metrics,
            "last_checkpoint": self.last_checkpoint,
            "nominal_replay_passes": (
                completed * min(self.service_config.batch_size, len(self.replay)) / len(self.replay)
            ),
        }
