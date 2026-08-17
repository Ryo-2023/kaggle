#!/usr/bin/env python3
"""Diagnose whether V4 can memorize a sealed tiny teacher-forced slice.

This is deliberately a *diagnostic-only* capacity/projection check.  It is
not a promotion gate, a held-out strength evaluation, or a CABT experiment.
The tiny train/validation episodes are materialized through the same sealed
fast-research reader as V4 BC, but an apparent fit is not performance evidence.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import time
from typing import Any, Mapping, Sequence

import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from mage_ptcg.meta_specialist.neural_model_v4 import (  # noqa: E402
    SpecialistModelV4,
    load_specialist_checkpoint_v4,
    save_specialist_checkpoint_v4,
)
from mage_ptcg.meta_specialist.recurrent_bc_v4 import (  # noqa: E402
    ACTION_BALANCED_WEIGHTS_V1,
    RESEARCH_ONLY_UNIFORM_WEIGHT,
    _shuffled_train_sequences_v4,
    _train_epoch,
    materialize_fast_research_uniform_subset_v4,
    selected_objective_sha256_v4,
    trainer_implementation_sha256_v4,
)
from mage_ptcg.meta_specialist.recurrent_dataset_v4 import RecurrentBCSequenceV4  # noqa: E402
from mage_ptcg.meta_specialist.v4_imitation_metrics import (  # noqa: E402
    V4_IMITATION_METRICS_SCHEMA_V1,
    evaluate_recurrent_imitation_v4,
)


TINY_OVERFIT_PROBE_SCHEMA_V1 = "meta-specialist-v4-tiny-overfit-probe-v1"
_MIN_EPISODES = 4
_MAX_EPISODES = 8
_MIN_EPOCHS = 20
_MAX_EPOCHS = 50
_FIT_EXACT_TOP1 = 0.95


@dataclass(frozen=True, slots=True)
class TinyOverfitProbeConfigV4:
    """Configuration for a non-promotable tiny-fit diagnostic.

    ``run_tiny_overfit_probe_v4`` permits a lower epoch count for lightweight
    contract tests.  The CLI deliberately limits real diagnostic runs to
    20--50 epochs, preventing a tiny test from being mistaken for long-run BC.
    """

    selection_manifest: Path
    selection_manifest_sha256: str
    output: Path
    progress_path: Path | None = None
    seed: int = 0
    epochs: int = 30
    hidden_dim: int = 128
    embedding_dim: int = 64
    device: str = "cpu"
    max_records: int = 4096
    episodes_per_partition: int = 4
    components_per_partition: int = 4
    learning_rate: float = 1e-3
    tbptt_steps: int = 8
    gradient_clip_norm: float = 1.0
    burn_in: int = 1
    subset_fraction: float = 0.05
    action_balanced: bool = False


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


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_probe_progress(
    path: Path | None, *, status: str, completed: int, total: int,
    started: float, fields: Mapping[str, object] | None = None,
) -> None:
    """Publish one atomic snapshot consumed by ``watch_v4_progress.py``."""
    if path is None:
        return
    elapsed = max(0.0, time.monotonic() - started)
    rate = completed / elapsed if elapsed > 0.0 else 0.0
    eta = (total - completed) / rate if rate > 0.0 and completed < total else 0.0
    _atomic_json(path, {
        "desc": "v4-tiny-overfit",
        "status": status,
        "completed": completed,
        "total": total,
        "elapsed_seconds": elapsed,
        "rate_per_second": rate,
        "eta_seconds": eta,
        "fields": dict(fields or {}),
        "updated_unix": time.time(),
    })


def _resolve_device(value: str) -> torch.device:
    device = torch.device(value)
    if device.type == "cuda" and not torch.cuda.is_available():
        raise ValueError(f"requested CUDA device is unavailable: {value}")
    if device.type not in {"cpu", "cuda"}:
        raise ValueError("tiny-overfit probe supports only CPU or CUDA devices")
    return device


def _require_sha256(value: object, *, name: str) -> str:
    if type(value) is not str or len(value) != 64 or any(letter not in "0123456789abcdef" for letter in value):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return value


def _validate_config(config: TinyOverfitProbeConfigV4) -> torch.device:
    if type(config) is not TinyOverfitProbeConfigV4:
        raise ValueError("tiny-overfit probe configuration is invalid")
    _require_sha256(config.selection_manifest_sha256, name="selection manifest SHA-256")
    if not config.selection_manifest.is_file():
        raise ValueError("tiny-overfit probe selection manifest is not a regular file")
    if _file_sha256(config.selection_manifest) != config.selection_manifest_sha256:
        raise ValueError("tiny-overfit probe selection manifest SHA-256 changed")
    if (
        type(config.seed) is not int or config.seed < 0
        or type(config.epochs) is not int or config.epochs < 1
        or type(config.hidden_dim) is not int or config.hidden_dim < 1
        or type(config.embedding_dim) is not int or config.embedding_dim < 1
        or type(config.max_records) is not int or config.max_records < 4
        or type(config.episodes_per_partition) is not int or not _MIN_EPISODES <= config.episodes_per_partition <= _MAX_EPISODES
        or type(config.components_per_partition) is not int or not _MIN_EPISODES <= config.components_per_partition <= config.episodes_per_partition
        or not math.isfinite(config.learning_rate) or config.learning_rate <= 0.0
        or type(config.tbptt_steps) is not int or config.tbptt_steps < 1
        or not math.isfinite(config.gradient_clip_norm) or config.gradient_clip_norm <= 0.0
        or type(config.burn_in) is not int or config.burn_in < 0
        or type(config.action_balanced) is not bool
        or not math.isfinite(config.subset_fraction) or not 0.0 < config.subset_fraction <= 0.1
    ):
        raise ValueError("tiny-overfit probe configuration is outside its sealed bounds")
    return _resolve_device(config.device)


def _partition_sequences(
    sequences: Sequence[RecurrentBCSequenceV4], *, partition: str,
) -> tuple[RecurrentBCSequenceV4, ...]:
    selected = tuple(sequence for sequence in sequences if sequence.partition == partition)
    if not selected:
        raise ValueError(f"tiny-overfit probe materialized no {partition} sequences")
    return selected


def _positive_stop_rows(sequences: Sequence[RecurrentBCSequenceV4]) -> int:
    return sum(
        bool(getattr(step.step_input, "stop_available", False)) and bool(step.target_masses) and step.target_masses[-1] > 0.0
        for sequence in sequences for step in sequence.steps
    )


def _coverage_report(
    sequences: Sequence[RecurrentBCSequenceV4], *, partition: str,
    expected_episodes: int, expected_components: int,
) -> dict[str, int]:
    partition_sequences = _partition_sequences(sequences, partition=partition)
    report = {
        "complete_episodes": len(partition_sequences),
        "components": len({sequence.component_id for sequence in partition_sequences}),
        "positive_stop_target_rows": _positive_stop_rows(partition_sequences),
    }
    if report["complete_episodes"] != expected_episodes or report["components"] != expected_components:
        raise ValueError(f"tiny-overfit probe {partition} coverage differs from its sealed target")
    if report["positive_stop_target_rows"] < 1:
        raise ValueError(f"tiny-overfit probe {partition} lacks a positive STOP target")
    return report


def _metric_top1(metrics: Mapping[str, object]) -> float:
    complete = metrics.get("complete_action")
    if type(complete) is not dict or type(complete.get("top1")) is not float:
        raise ValueError("tiny-overfit probe imitation metrics lack finite top-1")
    value = float(complete["top1"])
    if not math.isfinite(value):
        raise ValueError("tiny-overfit probe imitation top-1 is non-finite")
    return value


def _overfit_assessment(epoch_metrics: Sequence[Mapping[str, object]]) -> dict[str, object]:
    if not epoch_metrics:
        raise ValueError("tiny-overfit probe has no completed epochs")
    reaches = next(
        (
            int(row["epoch"])
            for row in epoch_metrics
            if _metric_top1(row["train"]) >= _FIT_EXACT_TOP1  # type: ignore[arg-type]
        ),
        None,
    )
    final = epoch_metrics[-1]
    train_top1 = _metric_top1(final["train"])  # type: ignore[arg-type]
    validation_top1 = _metric_top1(final["validation"])  # type: ignore[arg-type]
    return {
        "fit_exact_top1_threshold": _FIT_EXACT_TOP1,
        "train_exact_top1_reaches_95_epoch": reaches,
        "can_fit_tiny_train": reaches is not None,
        "final_train_exact_top1": train_top1,
        "final_validation_exact_top1": validation_top1,
        "final_train_minus_validation_top1": train_top1 - validation_top1,
        "verdict": "TINY_TRAIN_FIT_CONFIRMED" if reaches is not None else "TINY_TRAIN_FIT_NOT_REACHED",
    }


def _config_report(config: TinyOverfitProbeConfigV4, *, device: torch.device, card_vocabulary_size: int) -> dict[str, object]:
    raw = asdict(config)
    raw["selection_manifest"] = str(config.selection_manifest.resolve())
    raw["output"] = str(config.output.resolve())
    raw["progress_path"] = str(config.progress_path.resolve()) if config.progress_path is not None else None
    raw["device"] = str(device)
    raw["card_vocabulary_size"] = card_vocabulary_size
    raw["mode"] = RESEARCH_ONLY_UNIFORM_WEIGHT
    raw["epochs_cli_constraint"] = [_MIN_EPOCHS, _MAX_EPOCHS]
    return raw


def run_tiny_overfit_probe_v4(config: TinyOverfitProbeConfigV4) -> dict[str, object]:
    """Run one sealed tiny-set fit diagnostic and atomically save its report.

    The result uses carried recurrent state and the canonical imitation metric.
    It excludes forced one-choice rows from all reported NLL/ranking values.
    """
    device = _validate_config(config)
    subset = materialize_fast_research_uniform_subset_v4(
        config.selection_manifest,
        expected_selection_manifest_file_sha256=config.selection_manifest_sha256,
        max_records=config.max_records,
        subset_fraction=config.subset_fraction,
        burn_in=config.burn_in,
        episodes_per_partition=config.episodes_per_partition,
        components_per_partition=config.components_per_partition,
        require_positive_stop=True,
        mode=RESEARCH_ONLY_UNIFORM_WEIGHT,
    )
    train = _partition_sequences(subset.sequences, partition="train")
    validation = _partition_sequences(subset.sequences, partition="validation")
    coverage = {
        "train": _coverage_report(
            subset.sequences, partition="train", expected_episodes=config.episodes_per_partition,
            expected_components=config.components_per_partition,
        ),
        "validation": _coverage_report(
            subset.sequences, partition="validation", expected_episodes=config.episodes_per_partition,
            expected_components=config.components_per_partition,
        ),
    }
    model = SpecialistModelV4(
        card_vocabulary_size=subset.card_vocabulary_size,
        hidden_dim=config.hidden_dim, embedding_dim=config.embedding_dim, seed=config.seed,
    ).to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=config.learning_rate)
    action_type_weights = dict(ACTION_BALANCED_WEIGHTS_V1) if config.action_balanced else None
    cuda_index: int | None = None
    if device.type == "cuda":
        cuda_index = device.index if device.index is not None else torch.cuda.current_device()
        torch.cuda.set_device(cuda_index)
        torch.cuda.reset_peak_memory_stats(cuda_index)
    started = time.monotonic()
    _write_probe_progress(
        config.progress_path, status="running", completed=0, total=config.epochs,
        started=started,
        fields={
            "stage": "materialize_complete",
            "seed": config.seed,
            "train_sequences": len(train),
            "validation_sequences": len(validation),
        },
    )
    epoch_metrics: list[dict[str, object]] = []
    updates_completed = 0
    for epoch in range(1, config.epochs + 1):
        telemetry: dict[str, float] = {}
        train_update_nll = _train_epoch(
            model,
            _shuffled_train_sequences_v4(train, sequence_order_seed=config.seed, epoch=epoch - 1),
            optimizer=optimizer, tbptt_steps=config.tbptt_steps,
            gradient_clip_norm=config.gradient_clip_norm, mode=RESEARCH_ONLY_UNIFORM_WEIGHT,
            telemetry=telemetry,
            action_type_weights=action_type_weights,
        )
        train_metrics = evaluate_recurrent_imitation_v4(model, train, partition="train", recurrence="carry")
        validation_metrics = evaluate_recurrent_imitation_v4(
            model, validation, partition="validation", recurrence="carry",
        )
        if train_metrics.get("schema") != V4_IMITATION_METRICS_SCHEMA_V1 or validation_metrics.get("schema") != V4_IMITATION_METRICS_SCHEMA_V1:
            raise RuntimeError("tiny-overfit probe received an unexpected imitation metric schema")
        updates_completed += int(telemetry.get("optimizer_updates", 0.0))
        epoch_metrics.append({
            "epoch": epoch,
            "train_update_complete_action_nll": train_update_nll,
            "optimizer_updates": int(telemetry.get("optimizer_updates", 0.0)),
            "optimizer_updates_completed": updates_completed,
            "mean_preclip_gradient_norm": float(telemetry.get("mean_preclip_gradient_norm", 0.0)),
            "train_elapsed_seconds": float(telemetry.get("train_elapsed_seconds", 0.0)),
            "train": train_metrics,
            "validation": validation_metrics,
        })
        _write_probe_progress(
            config.progress_path, status="running", completed=epoch, total=config.epochs,
            started=started,
            fields={
                "stage": "evaluate",
                "seed": config.seed,
                "epoch": epoch,
                "partition": "validation",
                "complete_action_nll": validation_metrics["complete_action"].get("complete_action_nll"),
                "complete_action_top1": validation_metrics["complete_action"].get("top1"),
                "train_top1": train_metrics["complete_action"].get("top1"),
                "optimizer_updates": int(telemetry.get("optimizer_updates", 0.0)),
            },
        )
    config.output.parent.mkdir(parents=True, exist_ok=True)
    checkpoint_path = config.output.with_suffix(".pt")
    descriptor = save_specialist_checkpoint_v4(checkpoint_path, model)
    checkpoint_sha = _file_sha256(checkpoint_path)
    reloaded = SpecialistModelV4(
        card_vocabulary_size=subset.card_vocabulary_size,
        hidden_dim=config.hidden_dim, embedding_dim=config.embedding_dim, seed=config.seed,
    ).to(device)
    loaded_descriptor = load_specialist_checkpoint_v4(
        checkpoint_path, reloaded, expected_file_sha256=checkpoint_sha,
        expected_tensor_state_sha256=str(descriptor["tensor_state_sha256"]),
    )
    if loaded_descriptor != descriptor:
        raise RuntimeError("tiny-overfit probe strict checkpoint reload descriptor drifted")
    if device.type == "cuda":
        assert cuda_index is not None
        torch.cuda.synchronize(cuda_index)
        cuda_peak_memory_bytes: int | None = int(torch.cuda.max_memory_allocated(cuda_index))
    else:
        cuda_peak_memory_bytes = None
    report: dict[str, object] = {
        "schema": TINY_OVERFIT_PROBE_SCHEMA_V1,
        "diagnostic_only": True,
        "promotion_authority": False,
        "warning": "Tiny teacher-forced fit is a pipeline-capacity diagnostic, not held-out strength evidence.",
        "lane": subset.lane,
        "selection_manifest": str(config.selection_manifest.resolve()),
        "selection_manifest_file_sha256": subset.selection_manifest_file_sha256,
        "selected_sequence_sha256": selected_objective_sha256_v4(subset.sequences),
        "trainer_implementation_sha256": trainer_implementation_sha256_v4(),
        "coverage": coverage,
        "config": _config_report(config, device=device, card_vocabulary_size=subset.card_vocabulary_size),
        "epoch_metrics": epoch_metrics,
        "overfit_assessment": _overfit_assessment(epoch_metrics),
        "checkpoint": {
            "path": str(checkpoint_path.resolve()),
            "file_sha256": checkpoint_sha,
            "tensor_state_sha256": str(descriptor["tensor_state_sha256"]),
            "strict_reload_verified": True,
        },
        "optimizer_updates_completed": updates_completed,
        "elapsed_seconds": time.monotonic() - started,
        "cuda_peak_memory_bytes": cuda_peak_memory_bytes,
    }
    _atomic_json(config.output, report)
    _write_probe_progress(
        config.progress_path, status="done", completed=config.epochs, total=config.epochs,
        started=started,
        fields={
            "stage": "done",
            "seed": config.seed,
            "complete_action_nll": epoch_metrics[-1]["validation"]["complete_action"].get("complete_action_nll"),
            "complete_action_top1": epoch_metrics[-1]["validation"]["complete_action"].get("top1"),
            "overfit_verdict": report["overfit_assessment"]["verdict"],
        },
    )
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection-manifest", type=Path, required=True)
    parser.add_argument("--selection-manifest-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--progress-path", type=Path)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--epochs", type=int, default=30)
    parser.add_argument("--hidden-dim", type=int, default=128)
    parser.add_argument("--embedding-dim", type=int, default=64)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--max-records", type=int, default=4096)
    parser.add_argument("--episodes-per-partition", type=int, default=4)
    parser.add_argument("--components-per-partition", type=int, default=4)
    parser.add_argument("--learning-rate", type=float, default=1e-3)
    parser.add_argument("--tbptt-steps", type=int, default=8)
    parser.add_argument("--gradient-clip-norm", type=float, default=1.0)
    parser.add_argument("--burn-in", type=int, default=1)
    parser.add_argument("--subset-fraction", type=float, default=0.05)
    parser.add_argument("--action-balanced", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if not _MIN_EPOCHS <= args.epochs <= _MAX_EPOCHS:
        parser.error(f"--epochs must be in {_MIN_EPOCHS}..{_MAX_EPOCHS} for a real tiny-overfit diagnostic")
    config = TinyOverfitProbeConfigV4(**vars(args))
    try:
        report = run_tiny_overfit_probe_v4(config)
    except (ValueError, RuntimeError) as exc:
        parser.error(str(exc))
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
