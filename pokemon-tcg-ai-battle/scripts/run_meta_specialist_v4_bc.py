"""Run a bounded two-seed, research-only recurrent V4 BC comparison on CPU."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import time

import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from mage_ptcg.meta_specialist.neural_model_v4 import SpecialistModelV4  # noqa: E402
from mage_ptcg.meta_specialist.recurrent_bc_v4 import (  # noqa: E402
    ACTION_BALANCED_WEIGHTS_V1,
    RESEARCH_ONLY_UNIFORM_WEIGHT,
    SHORT_PILOT_MAJOR_REGRESSION_NLL,
    SHORT_PILOT_MIN_MEAN_DELTA_NLL,
    materialize_fast_research_uniform_subset_v4,
    materialize_research_uniform_subset_v4,
    positive_stop_target_metrics_v4,
    short_pilot_selection_status_v4,
    train_recurrent_bc_v4,
    trainer_implementation_sha256_v4,
    selected_objective_sha256_v4,
)


def _seeds(value: str) -> tuple[int, ...]:
    try:
        seeds = tuple(int(item) for item in value.split(",") if item)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("seeds must be comma-separated integers") from exc
    if len(seeds) != 2 or len(set(seeds)) != 2:
        raise argparse.ArgumentTypeError("exactly two distinct seeds are required for the short comparison")
    return seeds


def _resolve_device(value: str) -> torch.device:
    device = torch.device(value)
    if device.type == "cuda" and not torch.cuda.is_available():
        raise ValueError(f"requested CUDA device is unavailable: {value}")
    return device


def _prepare_cuda_stats(device: torch.device) -> int:
    """Initialize CUDA and use the concrete index for delayed stats calls.

    The Windows-hosted CUDA 12.8 runtime can reject a ``torch.device`` object
    when the first memory-stats call happens only after a long CPU
    materialization pass.  Binding and initializing the concrete index keeps
    that diagnostic call separate from model semantics.
    """
    if device.type != "cuda":
        raise ValueError("CUDA stats require a CUDA device")
    index = device.index if device.index is not None else torch.cuda.current_device()
    torch.cuda.set_device(index)
    torch.cuda.init()
    torch.cuda.reset_peak_memory_stats(index)
    torch.cuda.synchronize(index)
    return index


def _atomic_json(path: Path, payload: dict[str, object]) -> None:
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


def _config_sha256(payload: dict[str, object]) -> str:
    raw = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return __import__("hashlib").sha256(raw).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection-manifest", type=Path, required=True)
    parser.add_argument("--selection-manifest-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seeds", type=_seeds, default=(0, 1))
    parser.add_argument("--max-records", type=int, default=1024)
    parser.add_argument("--episodes-per-partition", type=int, default=4)
    parser.add_argument("--components-per-partition", type=int)
    parser.add_argument("--train-episodes-per-partition", type=int)
    parser.add_argument("--validation-episodes-per-partition", type=int)
    parser.add_argument("--train-components-per-partition", type=int)
    parser.add_argument("--validation-components-per-partition", type=int)
    parser.add_argument("--require-positive-stop", action="store_true")
    parser.add_argument("--subset-fraction", type=float, default=0.05)
    parser.add_argument("--burn-in", type=int, default=1)
    parser.add_argument("--tbptt-steps", type=int, default=8)
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--patience", type=int, default=1)
    parser.add_argument("--learning-rate", type=float, default=1e-3)
    parser.add_argument("--gradient-clip-norm", type=float, default=1.0)
    parser.add_argument("--hidden-dim", type=int, default=64)
    parser.add_argument("--embedding-dim", type=int, default=32)
    parser.add_argument("--device", choices=("cpu", "cuda:0"), default="cpu")
    parser.add_argument("--card-vocabulary-size", type=int)
    parser.add_argument("--fast-research-subset", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--progress-path", type=Path)
    parser.add_argument("--external-run-config-sha256")
    parser.add_argument("--mode", choices=(RESEARCH_ONLY_UNIFORM_WEIGHT,), default=RESEARCH_ONLY_UNIFORM_WEIGHT)
    parser.add_argument(
        "--action-balanced", action="store_true",
        help="research-only action-type loss weighting; records the mapping in run identity",
    )
    args = parser.parse_args()

    try:
        device = _resolve_device(args.device)
    except ValueError as exc:
        parser.error(str(exc))
    if not args.fast_research_subset:
        parser.error("--fast-research-subset is required; the full authority reader is not a short pilot")
    if args.progress_path is not None:
        _atomic_json(args.progress_path, {
            "schema": "meta-specialist-recurrent-bc-v4-progress-v1", "status": "running",
            "stage": "materialize", "updated_unix": time.time(),
        })
    subset = materialize_fast_research_uniform_subset_v4(
        args.selection_manifest,
        expected_selection_manifest_file_sha256=args.selection_manifest_sha256,
        max_records=args.max_records, subset_fraction=args.subset_fraction,
        burn_in=args.burn_in, episodes_per_partition=args.episodes_per_partition,
        components_per_partition=args.components_per_partition,
        train_episodes_per_partition=args.train_episodes_per_partition,
        validation_episodes_per_partition=args.validation_episodes_per_partition,
        train_components_per_partition=args.train_components_per_partition,
        validation_components_per_partition=args.validation_components_per_partition,
        require_positive_stop=args.require_positive_stop, mode=args.mode,
    )
    train = tuple(item for item in subset.sequences if item.partition == "train")
    validation = tuple(item for item in subset.sequences if item.partition == "validation")
    if args.card_vocabulary_size is not None and args.card_vocabulary_size != subset.card_vocabulary_size:
        parser.error(
            "--card-vocabulary-size must exactly match the sealed selection vocabulary "
            f"size {subset.card_vocabulary_size}",
        )
    card_vocabulary_size = subset.card_vocabulary_size
    selected_sequence_sha256 = selected_objective_sha256_v4(tuple(subset.sequences))
    trainer_sha256 = trainer_implementation_sha256_v4()
    action_type_weights = dict(ACTION_BALANCED_WEIGHTS_V1) if args.action_balanced else None
    seed_results: dict[str, dict[str, object]] = {}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    checkpoint_root = args.output.parent / f"{args.output.stem}-checkpoints"
    checkpoint_root.mkdir(parents=True, exist_ok=True)
    cuda_index = _prepare_cuda_stats(device) if device.type == "cuda" else None
    started = time.monotonic()
    for seed in args.seeds:
        if args.progress_path is not None:
            _atomic_json(args.progress_path, {
                "schema": "meta-specialist-recurrent-bc-v4-progress-v1", "status": "running",
                "stage": "training", "seed": seed, "epochs_completed": 0,
                "epochs_requested": args.epochs, "optimizer_updates_completed": 0,
                "updated_unix": time.time(),
            })
        model = SpecialistModelV4(
            card_vocabulary_size=card_vocabulary_size, hidden_dim=args.hidden_dim,
            embedding_dim=args.embedding_dim, seed=seed,
        ).to(device)
        run_config = {
            "lane": subset.lane,
            "selection_manifest_file_sha256": subset.selection_manifest_file_sha256,
            "records_by_partition": dict(subset.records_by_partition),
            "target_records_by_partition": dict(subset.target_records_by_partition),
            "selected_sequence_sha256": selected_sequence_sha256,
            "trainer_implementation_sha256": trainer_sha256,
            "subset_fraction": args.subset_fraction,
            "max_records": args.max_records,
            "burn_in": args.burn_in,
            "coverage_target": {
                "train_episodes_per_partition": subset.train_episodes_per_partition,
                "validation_episodes_per_partition": subset.validation_episodes_per_partition,
                "train_components_per_partition": subset.train_components_per_partition,
                "validation_components_per_partition": subset.validation_components_per_partition,
                "require_positive_stop": subset.require_positive_stop,
            },
            "model": {"card_vocabulary_size": card_vocabulary_size, "hidden_dim": args.hidden_dim, "embedding_dim": args.embedding_dim},
            "trainer": {"epochs": args.epochs, "patience": args.patience, "learning_rate": args.learning_rate,
                        "tbptt_steps": args.tbptt_steps, "gradient_clip_norm": args.gradient_clip_norm,
                        "action_type_weights": action_type_weights},
            "external_run_config_sha256": args.external_run_config_sha256,
        }
        def report_epoch(payload: dict[str, object], *, current_seed: int = seed) -> None:
            if args.progress_path is not None:
                _atomic_json(args.progress_path, {
                    "schema": "meta-specialist-recurrent-bc-v4-progress-v1", "status": "running",
                    "stage": "training", "seed": current_seed, "updated_unix": time.time(), **payload,
                })
        result = train_recurrent_bc_v4(
            model, train, validation, mode=args.mode, output_dir=checkpoint_root / f"seed-{seed}",
            epochs=args.epochs, patience=args.patience, learning_rate=args.learning_rate,
            tbptt_steps=args.tbptt_steps, sequence_order_seed=seed, run_config=run_config,
            resume=args.resume, epoch_callback=report_epoch, gradient_clip_norm=args.gradient_clip_norm,
            action_type_weights=action_type_weights,
        )
        stop_metrics = positive_stop_target_metrics_v4(model, validation, mode=args.mode)
        seed_results[str(seed)] = {
            "sequence_order_seed": seed,
            "best_epoch": result.best_epoch, "epochs_completed": result.epochs_completed,
            "initial_validation_complete_action_nll": result.initial_validation_complete_action_nll,
            "best_validation_complete_action_nll": result.best_validation_complete_action_nll,
            "validation_delta_nll": result.validation_delta_nll, "improved": result.improved,
            "validation_by_component": dict(result.validation_by_component),
            "history": [dict(row) for row in result.history],
            "optimizer_updates_completed": result.optimizer_updates_completed,
            "elapsed_seconds": result.elapsed_seconds,
            "invocation_elapsed_seconds": result.invocation_elapsed_seconds,
            "cumulative_train_elapsed_seconds": result.cumulative_train_elapsed_seconds,
            "last_checkpoint_path": str(result.last_checkpoint_path) if result.last_checkpoint_path else None,
            "best_checkpoint_path": str(result.best_checkpoint_path),
            "best_checkpoint_file_sha256": result.best_checkpoint_file_sha256,
            "best_checkpoint_tensor_state_sha256": result.best_checkpoint_tensor_state_sha256,
            "validation_positive_stop_target_metrics": dict(stop_metrics),
        }
    deltas = tuple(float(item["validation_delta_nll"]) for item in seed_results.values())
    if device.type == "cuda":
        assert cuda_index is not None
        torch.cuda.synchronize(cuda_index)
        cuda_peak_memory_bytes: int | None = int(torch.cuda.max_memory_allocated(cuda_index))
    else:
        cuda_peak_memory_bytes = None
    report = {
        "schema": "meta-specialist-recurrent-bc-v4-research-report",
        "mode": args.mode,
        "promotion_authority": False,
        "selection_status": short_pilot_selection_status_v4(deltas, epochs=args.epochs),
        "short_pilot_major_regression_nll": SHORT_PILOT_MAJOR_REGRESSION_NLL,
        "short_pilot_min_mean_delta_nll": SHORT_PILOT_MIN_MEAN_DELTA_NLL,
        "device": str(device),
        "elapsed_seconds": time.monotonic() - started,
        "selected_sequence_sha256": selected_sequence_sha256,
        "trainer_implementation_sha256": trainer_sha256,
        "action_type_weights": action_type_weights,
        "external_run_config_sha256": args.external_run_config_sha256,
        "cuda_peak_memory_bytes": cuda_peak_memory_bytes,
        "selection_manifest": str(subset.selection_manifest_path),
        "selection_manifest_file_sha256": subset.selection_manifest_file_sha256,
        "lane": subset.lane,
        "records_by_partition": dict(subset.records_by_partition),
        "target_records_by_partition": dict(subset.target_records_by_partition),
        "coverage_target": {
            "episodes_per_partition": subset.episodes_per_partition,
            "components_per_partition": subset.components_per_partition,
            "train_episodes_per_partition": subset.train_episodes_per_partition,
            "validation_episodes_per_partition": subset.validation_episodes_per_partition,
            "train_components_per_partition": subset.train_components_per_partition,
            "validation_components_per_partition": subset.validation_components_per_partition,
            "require_positive_stop": subset.require_positive_stop,
        },
        "actual_sequences_by_partition": {
            partition: sum(sequence.partition == partition for sequence in subset.sequences)
            for partition in ("train", "validation")
        },
        "actual_components_by_partition": {
            partition: len({sequence.component_id for sequence in subset.sequences if sequence.partition == partition})
            for partition in ("train", "validation")
        },
        "decoder_coverage_by_partition": {
            partition: {
                "stop_available_rows": sum(
                    bool(getattr(step.step_input, "stop_available", False))
                    for sequence in subset.sequences if sequence.partition == partition
                    for step in sequence.steps
                ),
                "positive_stop_target_rows": sum(
                    bool(getattr(step.step_input, "stop_available", False)) and step.target_masses[-1] > 0.0
                    for sequence in subset.sequences if sequence.partition == partition
                    for step in sequence.steps
                ),
                "ordered_prefix_rows": sum(
                    getattr(step.step_input, "order_semantics", None) == "ordered_sequence"
                    and bool(getattr(step.step_input, "semantic_prefix", ()))
                    for sequence in subset.sequences if sequence.partition == partition
                    for step in sequence.steps
                ),
            }
            for partition in ("train", "validation")
        },
        "card_vocabulary_size": card_vocabulary_size,
        "card_vocabulary_card_id_count": subset.card_vocabulary_card_id_count,
        "training_config": {
            "max_records": args.max_records, "subset_fraction": args.subset_fraction, "burn_in": args.burn_in,
            "epochs": args.epochs, "patience": args.patience, "learning_rate": args.learning_rate,
            "tbptt_steps": args.tbptt_steps, "gradient_clip_norm": args.gradient_clip_norm,
            "hidden_dim": args.hidden_dim, "embedding_dim": args.embedding_dim,
            "card_vocabulary_size": card_vocabulary_size, "seeds": list(args.seeds), "device": str(device),
            "action_type_weights": action_type_weights,
        },
        "seed_results": seed_results,
    }
    report["training_config_sha256"] = _config_sha256({
        "training_config": report["training_config"], "coverage_target": report["coverage_target"],
        "selected_sequence_sha256": selected_sequence_sha256, "trainer_implementation_sha256": trainer_sha256,
        "external_run_config_sha256": args.external_run_config_sha256,
        "selection_manifest_file_sha256": subset.selection_manifest_file_sha256,
    })
    _atomic_json(args.output, report)
    if args.progress_path is not None:
        _atomic_json(args.progress_path, {
            "schema": "meta-specialist-recurrent-bc-v4-progress-v1", "status": "complete",
            "stage": "training", "updated_unix": time.time(), "output": str(args.output),
        })
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
