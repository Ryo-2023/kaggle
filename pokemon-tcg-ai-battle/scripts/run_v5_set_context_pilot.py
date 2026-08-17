"""Run one bounded V5 SetContext transfer+BC pilot seed.

This is a research-only sidecar runner.  It reuses the sealed V4 sequence
materializer but never calls the V4 trainer, changes no V4 production code,
and writes a closed per-seed report with the exact V4 base and V5 artifact
provenance.  The caller should run seed 0 and seed 1 with the corresponding
Wave6 checkpoints and compare them against the pre-registered fixed-six gate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from typing import Mapping

import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from mage_ptcg.meta_specialist.neural_model_v4 import CHECKPOINT_SCHEMA_V4  # noqa: E402
from mage_ptcg.meta_specialist.neural_model_v5 import (  # noqa: E402
    SpecialistModelV5,
    load_specialist_checkpoint_v5,
    transfer_specialist_checkpoint_v4_to_v5,
)
from mage_ptcg.meta_specialist.recurrent_bc_v4 import (  # noqa: E402
    RESEARCH_ONLY_UNIFORM_WEIGHT,
    selected_objective_sha256_v4,
)
from mage_ptcg.meta_specialist.recurrent_bc_v5 import (  # noqa: E402
    train_recurrent_bc_v5,
)
from scripts.run_v4_qualified_teacher_snapshot_bc import _materialize_sequences  # noqa: E402


_REPORT_SCHEMA = "meta-specialist-v5-set-context-pilot-seed-report-v1"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _atomic_json(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.tmp.", dir=path.parent)
    try:
        with open(descriptor, "w", encoding="utf-8", closefd=True) as handle:
            json.dump(dict(payload), handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
        Path(temporary).replace(path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _validate_sha256(value: object, *, name: str) -> str:
    if type(value) is not str or len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ValueError(f"{name} must be a lowercase SHA-256")
    return value


def _validate_checkpoint_binding_v5(
    checkpoint: Path,
    *,
    file_sha256: str,
    tensor_state_sha256: str,
) -> dict[str, object]:
    """Verify the external V4 checkpoint identity before any transfer."""
    checkpoint = checkpoint.resolve(strict=True)
    if not checkpoint.is_file() or checkpoint.is_symlink():
        raise ValueError("checkpoint must be a regular non-symlink file")
    expected_file = _validate_sha256(file_sha256, name="file_sha256")
    expected_tensor = _validate_sha256(tensor_state_sha256, name="tensor_state_sha256")
    actual_file = _sha256_file(checkpoint)
    if actual_file != expected_file:
        raise ValueError("checkpoint file_sha256 does not match the file")
    return {
        "path": str(checkpoint),
        "file_sha256": expected_file,
        "tensor_state_sha256": expected_tensor,
        "checkpoint_schema": CHECKPOINT_SCHEMA_V4,
    }


def _load_or_transfer_v5(
    base_checkpoint: Path,
    base_provenance: Mapping[str, object],
    sidecar_path: Path,
    *,
    seed: int,
) -> tuple[SpecialistModelV5, dict[str, object], str, str]:
    """Create or verify a V5 zero-head sidecar for one V4 base checkpoint."""
    sidecar_path.parent.mkdir(parents=True, exist_ok=True)
    if not sidecar_path.is_file():
        transfer_specialist_checkpoint_v4_to_v5(
            base_checkpoint,
            sidecar_path,
            expected_base_file_sha256=str(base_provenance["file_sha256"]),
            expected_base_tensor_state_sha256=str(base_provenance["tensor_state_sha256"]),
            head_seed=seed,
        )
    file_sha = _sha256_file(sidecar_path)
    payload = torch.load(sidecar_path, map_location="cpu", weights_only=True)
    if type(payload) is not dict or type(payload.get("descriptor")) is not dict:
        raise ValueError("V5 sidecar is not a closed checkpoint")
    descriptor = dict(payload["descriptor"])
    config = descriptor.get("model_config")
    if type(config) is not dict:
        raise ValueError("V5 sidecar model_config is missing")
    model = SpecialistModelV5(**config, seed=seed)
    tensor_sha = _validate_sha256(descriptor.get("tensor_state_sha256"), name="v5 tensor_state_sha256")
    loaded = load_specialist_checkpoint_v5(
        sidecar_path,
        model,
        expected_file_sha256=file_sha,
        expected_tensor_state_sha256=tensor_sha,
    )
    if loaded.get("base_provenance") != dict(base_provenance):
        raise ValueError("V5 sidecar base provenance does not match the requested V4 checkpoint")
    return model, loaded, file_sha, tensor_sha


def run_seed(args: argparse.Namespace) -> dict[str, object]:
    if args.seed not in (0, 1):
        raise ValueError("pilot seed must be 0 or 1")
    if args.device.startswith("cuda") and not torch.cuda.is_available():
        raise ValueError("requested CUDA device is unavailable")
    torch.set_num_threads(args.torch_threads)
    device = torch.device(args.device)
    train, validation, source_stats = _materialize_sequences(
        args.snapshot_root,
        burn_in=args.burn_in,
        exclude_empty_selection=args.exclude_empty_selection,
        outcome_weighted=False,
    )
    objective_sha = selected_objective_sha256_v4(tuple(train) + tuple(validation))
    base_provenance = _validate_checkpoint_binding_v5(
        args.checkpoint,
        file_sha256=args.checkpoint_file_sha256,
        tensor_state_sha256=args.checkpoint_tensor_sha256,
    )
    seed_root = args.output_root / f"seed-{args.seed}"
    seed_root.mkdir(parents=True, exist_ok=True)
    # Keep sidecars from earlier smoke runs immutable.  The base checkpoint
    # identity is part of the filename so a different Wave6 lineage cannot be
    # accidentally reused or overwritten.
    sidecar = (
        args.output_root / "base-transfer" / f"seed-{args.seed}"
        / f"base-to-v5-{str(base_provenance['file_sha256'])[:12]}.pt"
    )
    model, descriptor, sidecar_file_sha, sidecar_tensor_sha = _load_or_transfer_v5(
        args.checkpoint, base_provenance, sidecar, seed=args.seed,
    )
    model = model.to(device)
    progress_path = args.progress_path or (seed_root / "train-progress.json")

    def epoch_callback(event: Mapping[str, object]) -> None:
        _atomic_json(progress_path, {
            "schema": "meta-specialist-v5-set-context-pilot-progress-v1",
            "seed": args.seed,
            "stage": "training",
            **dict(event),
        })

    run_config: dict[str, object] = {
        "pilot": "v5-set-context-lucifer19-48",
        "seed": args.seed,
        "base_checkpoint": dict(base_provenance),
        "initial_v5_sidecar": {
            "path": str(sidecar.resolve()),
            "file_sha256": sidecar_file_sha,
            "tensor_state_sha256": sidecar_tensor_sha,
            "descriptor": descriptor,
        },
        "source": source_stats,
        "objective_sha256": objective_sha,
        "test_partition_used": False,
        "promotion_authority": False,
        "device": str(device),
        "torch_threads": args.torch_threads,
        "epochs": args.epochs,
        "patience": args.patience,
        "learning_rate": args.learning_rate,
        "tbptt_steps": args.tbptt_steps,
        "burn_in": args.burn_in,
        "exclude_empty_selection": args.exclude_empty_selection,
        "architecture": "V4 transfer + zero-init candidate mean/count SetContext residual; STOP=base-global-v4",
    }
    result = train_recurrent_bc_v5(
        model,
        train,
        validation,
        mode=RESEARCH_ONLY_UNIFORM_WEIGHT,
        output_dir=seed_root,
        sequence_order_seed=args.seed,
        base_provenance=base_provenance,
        epochs=args.epochs,
        patience=args.patience,
        learning_rate=args.learning_rate,
        tbptt_steps=args.tbptt_steps,
        gradient_clip_norm=1.0,
        run_config=run_config,
        epoch_callback=epoch_callback,
    )
    report = {
        "schema": _REPORT_SCHEMA,
        "promotion_authority": False,
        "seed": args.seed,
        "source": source_stats,
        "objective_sha256": objective_sha,
        "test_partition_used": False,
        "base_provenance": base_provenance,
        "initial_v5_sidecar": {
            "path": str(sidecar.resolve()),
            "file_sha256": sidecar_file_sha,
            "tensor_state_sha256": sidecar_tensor_sha,
            "implementation_digest_sha256": descriptor.get("implementation_digest_sha256"),
        },
        "trainer_source_sha256": _sha256_file(ROOT / "src/mage_ptcg/meta_specialist/recurrent_bc_v5.py"),
        "runner_source_sha256": _sha256_file(Path(__file__).resolve()),
        "training_config": {
            "device": str(device),
            "torch_threads": args.torch_threads,
            "epochs": args.epochs,
            "patience": args.patience,
            "learning_rate": args.learning_rate,
            "tbptt_steps": args.tbptt_steps,
            "burn_in": args.burn_in,
            "exclude_empty_selection": args.exclude_empty_selection,
            "mode": RESEARCH_ONLY_UNIFORM_WEIGHT,
        },
        "result": {
            "best_epoch": result.best_epoch,
            "epochs_completed": result.epochs_completed,
            "initial_validation_complete_action_nll": result.initial_validation_complete_action_nll,
            "best_validation_complete_action_nll": result.best_validation_complete_action_nll,
            "validation_delta_nll": result.validation_delta_nll,
            "improved": result.improved,
            "optimizer_updates_completed": result.optimizer_updates_completed,
            "elapsed_seconds": result.elapsed_seconds,
            "best_checkpoint_path": str(result.best_checkpoint_path.resolve()),
            "best_checkpoint_file_sha256": result.best_checkpoint_file_sha256,
            "best_checkpoint_tensor_state_sha256": result.best_checkpoint_tensor_state_sha256,
            "validation_by_component": dict(result.validation_by_component),
            "history": [dict(row) for row in result.history],
        },
    }
    _atomic_json(seed_root / "report.json", report)
    _atomic_json(progress_path, {
        "schema": "meta-specialist-v5-set-context-pilot-progress-v1",
        "seed": args.seed,
        "stage": "complete",
        "report": str((seed_root / "report.json").resolve()),
    })
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--checkpoint-file-sha256", required=True)
    parser.add_argument("--checkpoint-tensor-sha256", required=True)
    parser.add_argument("--seed", type=int, required=True, choices=(0, 1))
    parser.add_argument("--epochs", type=int, default=1)
    parser.add_argument("--patience", type=int, default=0)
    parser.add_argument("--learning-rate", type=float, default=1e-4)
    parser.add_argument("--tbptt-steps", type=int, default=8)
    parser.add_argument("--burn-in", type=int, default=1)
    parser.add_argument("--exclude-empty-selection", action="store_true")
    parser.add_argument("--device", default="cuda:0", choices=("cpu", "cuda:0"))
    parser.add_argument("--torch-threads", type=int, default=2)
    parser.add_argument("--progress-path", type=Path)
    args = parser.parse_args()
    if args.epochs < 1 or args.patience < 0 or args.learning_rate <= 0 or args.burn_in < 0:
        parser.error("invalid training configuration")
    report = run_seed(args)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
