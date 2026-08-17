#!/usr/bin/env python3
"""Measure a strict V4 checkpoint on the exact bounded teacher-forced subset."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from mage_ptcg.meta_specialist.neural_model_v4 import (  # noqa: E402
    SpecialistModelV4,
    load_specialist_checkpoint_v4,
)
from mage_ptcg.meta_specialist.recurrent_bc_v4 import (  # noqa: E402
    RESEARCH_ONLY_UNIFORM_WEIGHT,
    materialize_fast_research_uniform_subset_v4,
    selected_objective_sha256_v4,
    trainer_implementation_sha256_v4,
)
from mage_ptcg.meta_specialist.v4_imitation_metrics import (  # noqa: E402
    V4_IMITATION_METRICS_SCHEMA_V1,
    evaluate_recurrent_imitation_v4,
)
from mage_ptcg.meta_specialist.progress_v1 import ProgressReporterV1  # noqa: E402


def _hex64(value: str) -> str:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise argparse.ArgumentTypeError("must be a lowercase SHA-256 digest")
    return value


def _require_hex64(value: object, name: str) -> str:
    if type(value) is not str:
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    try:
        return _hex64(value)
    except argparse.ArgumentTypeError as exc:
        raise ValueError(f"{name} must be a lowercase SHA-256 digest") from exc


def _seed_list(value: str) -> tuple[int, ...]:
    try:
        seeds = tuple(int(item) for item in value.split(",") if item)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("seeds must be comma-separated integers") from exc
    if not seeds or len(seeds) != len(set(seeds)) or any(seed < 0 for seed in seeds):
        raise argparse.ArgumentTypeError("seeds must be distinct nonnegative integers")
    return seeds


def _canonical_sha256(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _training_report_config_sha256(report: dict[str, object]) -> str:
    """Recompute the V4 BC report's configuration binding without trusting it."""
    return _canonical_sha256({
        "training_config": report.get("training_config"), "coverage_target": report.get("coverage_target"),
        "selected_sequence_sha256": report.get("selected_sequence_sha256"),
        "trainer_implementation_sha256": report.get("trainer_implementation_sha256"),
        "external_run_config_sha256": report.get("external_run_config_sha256"),
        "selection_manifest_file_sha256": report.get("selection_manifest_file_sha256"),
    })


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


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection-manifest", type=Path, required=True)
    parser.add_argument("--selection-manifest-sha256", type=_hex64, required=True)
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--checkpoint-file-sha256", type=_hex64)
    parser.add_argument("--checkpoint-tensor-state-sha256", type=_hex64)
    parser.add_argument("--card-vocabulary-size", type=int)
    parser.add_argument("--hidden-dim", type=int)
    parser.add_argument("--embedding-dim", type=int)
    parser.add_argument("--training-report", type=Path)
    parser.add_argument("--seeds", type=_seed_list)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-records", type=int, default=16384)
    parser.add_argument("--subset-fraction", type=float, default=0.05)
    parser.add_argument("--burn-in", type=int, default=1)
    parser.add_argument("--episodes-per-partition", type=int, default=64)
    parser.add_argument("--components-per-partition", type=int)
    parser.add_argument("--train-episodes-per-partition", type=int)
    parser.add_argument("--validation-episodes-per-partition", type=int)
    parser.add_argument("--train-components-per-partition", type=int)
    parser.add_argument("--validation-components-per-partition", type=int)
    parser.add_argument("--require-positive-stop", action="store_true")
    parser.add_argument("--device", choices=("cpu", "cuda:0"), default="cpu")
    parser.add_argument(
        "--progress-path", type=Path,
        help="atomic aggregate progress JSON; defaults to <output>.progress.json",
    )
    return parser


def _validate_args(args: argparse.Namespace) -> None:
    single = (args.checkpoint, args.checkpoint_file_sha256, args.checkpoint_tensor_state_sha256,
              args.card_vocabulary_size, args.hidden_dim, args.embedding_dim)
    if args.training_report is None:
        if args.seeds is not None or any(value is None for value in single):
            raise ValueError("single-checkpoint mode requires checkpoint provenance and all model dimensions")
    elif args.seeds is None:
        raise ValueError("--training-report requires --seeds")
    elif any(value is not None for value in single):
        raise ValueError("--training-report batch mode does not accept single-checkpoint arguments")
    elif not args.training_report.is_file():
        raise ValueError("training report does not exist or is not a regular file")
    if args.training_report is None and any(int(value) < 1 for value in single[3:]):
        raise ValueError("model dimensions must be positive")
    if args.device.startswith("cuda") and not torch.cuda.is_available():
        raise ValueError(f"requested CUDA device is unavailable: {args.device}")


def _materialize(args: argparse.Namespace):
    return materialize_fast_research_uniform_subset_v4(
        args.selection_manifest,
        expected_selection_manifest_file_sha256=args.selection_manifest_sha256,
        max_records=args.max_records, subset_fraction=args.subset_fraction, burn_in=args.burn_in,
        episodes_per_partition=args.episodes_per_partition,
        components_per_partition=args.components_per_partition,
        train_episodes_per_partition=args.train_episodes_per_partition,
        validation_episodes_per_partition=args.validation_episodes_per_partition,
        train_components_per_partition=args.train_components_per_partition,
        validation_components_per_partition=args.validation_components_per_partition,
        require_positive_stop=args.require_positive_stop, mode=RESEARCH_ONLY_UNIFORM_WEIGHT,
    )


def _materialize_with_progress(args: argparse.Namespace, progress: ProgressReporterV1):
    """Keep one visible aggregate heartbeat while the sealed reader scans shards."""
    stop = threading.Event()
    started = time.monotonic()

    def heartbeat() -> None:
        while not stop.wait(5.0):
            progress.update(
                0, stage="materialize", seed="all", recurrence="-",
                phase_elapsed_seconds=round(time.monotonic() - started, 1),
            )

    worker = threading.Thread(target=heartbeat, name="v4-imitation-materialize-heartbeat", daemon=True)
    worker.start()
    try:
        return _materialize(args)
    finally:
        stop.set()
        worker.join(timeout=1.0)


def _partitions(
    model: SpecialistModelV4, subset: object, *, progress: ProgressReporterV1 | None = None,
    progress_seed: int | str = "single",
) -> dict[str, object]:
    sequences = getattr(subset, "sequences")
    result: dict[str, object] = {}
    for partition in ("train", "validation"):
        partition_sequences = tuple(item for item in sequences if item.partition == partition)
        recurrence_results: dict[str, object] = {}
        for recurrence in ("carry", "reset"):
            metrics_started = time.monotonic()
            heartbeat_stop = threading.Event()

            def metrics_heartbeat() -> None:
                while not heartbeat_stop.wait(5.0):
                    if progress is not None:
                        progress.update(
                            0, stage="evaluate", seed=progress_seed, partition=partition,
                            recurrence=recurrence,
                            phase="running",
                            phase_elapsed_seconds=round(time.monotonic() - metrics_started, 1),
                            sequence_count=len(partition_sequences),
                        )

            heartbeat = threading.Thread(
                target=metrics_heartbeat, name="v4-imitation-metrics-heartbeat", daemon=True,
            )
            heartbeat.start()
            if progress is not None:
                progress.update(
                    0, stage="evaluate", seed=progress_seed, partition=partition,
                    recurrence=recurrence, phase="started",
                    sequence_count=len(partition_sequences),
                )
            try:
                metrics = evaluate_recurrent_imitation_v4(
                    model, partition_sequences, partition=partition, recurrence=recurrence,
                )
            finally:
                heartbeat_stop.set()
                heartbeat.join(timeout=1.0)
            recurrence_results[recurrence] = metrics
            if progress is not None:
                complete = metrics.get("complete_action", {})
                progress.update(
                    1, stage="evaluate", seed=progress_seed, partition=partition,
                    recurrence=recurrence,
                    complete_action_nll=complete.get("complete_action_nll"),
                    complete_action_top1=complete.get("top1"),
                )
        result[partition] = {
            "sequence_count": len(partition_sequences),
            "selected_sequence_sha256": selected_objective_sha256_v4(partition_sequences),
            "recurrence": recurrence_results,
        }
    return result


def _checkpoint_payload(
    *, checkpoint: Path, file_sha256: str, tensor_state_sha256: str,
    card_vocabulary_size: int, hidden_dim: int, embedding_dim: int, device: torch.device,
    subset: object, progress: ProgressReporterV1 | None = None, progress_seed: int | str = "single",
) -> dict[str, object]:
    if getattr(subset, "card_vocabulary_size") != card_vocabulary_size:
        raise ValueError("checkpoint card vocabulary size does not match the sealed selection")
    model = SpecialistModelV4(
        card_vocabulary_size=card_vocabulary_size, hidden_dim=hidden_dim, embedding_dim=embedding_dim,
    ).to(device)
    descriptor = load_specialist_checkpoint_v4(
        checkpoint, model, expected_file_sha256=file_sha256,
        expected_tensor_state_sha256=tensor_state_sha256,
    )
    if str(descriptor["tensor_state_sha256"]) != tensor_state_sha256:
        raise RuntimeError("strict V4 checkpoint loader returned a different tensor identity")
    return {
        "checkpoint": {
            "path": str(checkpoint.resolve()), "file_sha256": file_sha256,
            "tensor_state_sha256": tensor_state_sha256, "descriptor": descriptor,
        },
        "partitions": _partitions(model, subset, progress=progress, progress_seed=progress_seed),
    }


def _read_batch_training_report(args: argparse.Namespace) -> tuple[dict[str, object], dict[int, dict[str, object]]]:
    assert args.training_report is not None and args.seeds is not None
    try:
        report = json.loads(args.training_report.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("training report is not readable canonical JSON") from exc
    if type(report) is not dict or report.get("schema") != "meta-specialist-recurrent-bc-v4-research-report":
        raise ValueError("training report schema is not the V4 BC research report")
    if report.get("mode") != RESEARCH_ONLY_UNIFORM_WEIGHT or report.get("promotion_authority") is not False:
        raise ValueError("training report is not the bounded research-only V4 authority")
    if report.get("selection_manifest_file_sha256") != args.selection_manifest_sha256:
        raise ValueError("training report selection manifest SHA differs from the requested materializer")
    if Path(str(report.get("selection_manifest"))).resolve() != args.selection_manifest.resolve():
        raise ValueError("training report selection manifest path differs from the requested materializer")
    if type(report.get("training_config_sha256")) is not str or report["training_config_sha256"] != _training_report_config_sha256(report):
        raise ValueError("training report configuration SHA is invalid")
    if report.get("trainer_implementation_sha256") != trainer_implementation_sha256_v4():
        raise ValueError("training report trainer/source closure differs from the live evaluator closure")
    config = report.get("training_config")
    coverage = report.get("coverage_target")
    if type(config) is not dict or type(coverage) is not dict:
        raise ValueError("training report configuration is invalid")
    expected_config = {"max_records": args.max_records, "subset_fraction": args.subset_fraction, "burn_in": args.burn_in}
    if any(config.get(key) != value for key, value in expected_config.items()):
        raise ValueError("training report materializer configuration differs from the requested arguments")
    expected_coverage = {
        "episodes_per_partition": args.episodes_per_partition,
        "components_per_partition": args.components_per_partition,
        "train_episodes_per_partition": args.train_episodes_per_partition or args.episodes_per_partition,
        "validation_episodes_per_partition": args.validation_episodes_per_partition or args.episodes_per_partition,
        "train_components_per_partition": args.train_components_per_partition or args.components_per_partition or args.episodes_per_partition,
        "validation_components_per_partition": args.validation_components_per_partition or args.components_per_partition or args.episodes_per_partition,
        "require_positive_stop": args.require_positive_stop,
    }
    if any(coverage.get(key) != value for key, value in expected_coverage.items()):
        raise ValueError("training report coverage target differs from the requested materializer arguments")
    dimensions = (config.get("card_vocabulary_size"), config.get("hidden_dim"), config.get("embedding_dim"))
    if any(type(value) is not int or value < 1 for value in dimensions):
        raise ValueError("training report model dimensions are invalid")
    trained_seeds = config.get("seeds")
    if type(trained_seeds) is not list or any(type(seed) is not int or seed < 0 for seed in trained_seeds):
        raise ValueError("training report seeds are invalid")
    if not set(args.seeds).issubset(set(trained_seeds)):
        raise ValueError("requested seed is absent from the training report")
    rows = report.get("seed_results")
    if type(rows) is not dict:
        raise ValueError("training report seed results are invalid")
    selected: dict[int, dict[str, object]] = {}
    for seed in args.seeds:
        row = rows.get(str(seed))
        if type(row) is not dict:
            raise ValueError("training report has no requested seed result")
        path = row.get("best_checkpoint_path")
        file_sha = row.get("best_checkpoint_file_sha256")
        tensor_sha = row.get("best_checkpoint_tensor_state_sha256")
        if type(path) is not str or not path:
            raise ValueError("training report checkpoint path is invalid")
        selected[seed] = {
            "checkpoint": Path(path), "file_sha256": _require_hex64(file_sha, "training report checkpoint file SHA"),
            "tensor_state_sha256": _require_hex64(tensor_sha, "training report checkpoint tensor SHA"),
            "card_vocabulary_size": dimensions[0], "hidden_dim": dimensions[1], "embedding_dim": dimensions[2],
        }
    return report, selected


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    progress: ProgressReporterV1 | None = None
    try:
        _validate_args(args)
        batch_report, batch_checkpoints = _read_batch_training_report(args) if args.training_report else (None, None)
        total_units = 1 + 4 * (len(batch_checkpoints) if batch_checkpoints is not None else 1)
        progress = ProgressReporterV1(
            total=total_units,
            desc="v4-imitation",
            progress_path=args.progress_path or Path(f"{args.output}.progress.json"),
        )
        progress.update(0, stage="materialize", seed="all", recurrence="-")
        subset = _materialize_with_progress(args, progress)
        progress.update(
            1, stage="materialize_complete", seed="all", recurrence="-",
            selected_train_sequences=sum(item.partition == "train" for item in subset.sequences),
            selected_validation_sequences=sum(item.partition == "validation" for item in subset.sequences),
            train_records=subset.records_by_partition.get("train"),
            validation_records=subset.records_by_partition.get("validation"),
        )
        materialized_sha = selected_objective_sha256_v4(subset.sequences)
        if batch_report is not None and batch_report.get("selected_sequence_sha256") != materialized_sha:
            raise ValueError("training report selected sequence SHA differs from the materialized sealed subset")
        device = torch.device(args.device)
        common: dict[str, object] = {
            "schema": V4_IMITATION_METRICS_SCHEMA_V1,
            "promotion_authority": False,
            "mode": RESEARCH_ONLY_UNIFORM_WEIGHT,
            "device": str(device),
            "selection_manifest": str(subset.selection_manifest_path),
            "selection_manifest_file_sha256": subset.selection_manifest_file_sha256,
            "lane": subset.lane,
            "selected_sequence_sha256": materialized_sha,
            "records_by_partition": dict(subset.records_by_partition),
            "actual_sequences_by_partition": {
                partition: sum(item.partition == partition for item in subset.sequences)
                for partition in ("train", "validation")
            },
            "materializer": {
                "max_records": args.max_records, "subset_fraction": args.subset_fraction,
                "burn_in": args.burn_in, "episodes_per_partition": args.episodes_per_partition,
                "components_per_partition": args.components_per_partition,
                "train_episodes_per_partition": args.train_episodes_per_partition,
                "validation_episodes_per_partition": args.validation_episodes_per_partition,
                "train_components_per_partition": args.train_components_per_partition,
                "validation_components_per_partition": args.validation_components_per_partition,
                "require_positive_stop": args.require_positive_stop,
            },
        }
        if batch_checkpoints is None:
            assert args.checkpoint is not None and args.checkpoint_file_sha256 is not None
            assert args.checkpoint_tensor_state_sha256 is not None
            assert args.card_vocabulary_size is not None and args.hidden_dim is not None and args.embedding_dim is not None
            payload = {
                **common,
                **_checkpoint_payload(
                    checkpoint=args.checkpoint, file_sha256=args.checkpoint_file_sha256,
                    tensor_state_sha256=args.checkpoint_tensor_state_sha256,
                    card_vocabulary_size=args.card_vocabulary_size, hidden_dim=args.hidden_dim,
                    embedding_dim=args.embedding_dim, device=device, subset=subset, progress=progress,
                ),
            }
        else:
            seed_results: dict[str, object] = {}
            for seed, provenance in batch_checkpoints.items():
                seed_results[str(seed)] = _checkpoint_payload(
                    device=device, subset=subset, progress=progress, progress_seed=seed, **provenance,
                )
            payload = {
                **common,
                "training_report": {
                    "path": str(args.training_report.resolve()),
                    "schema": batch_report["schema"],
                    "training_config_sha256": batch_report["training_config_sha256"],
                    "selected_sequence_sha256": batch_report["selected_sequence_sha256"],
                    "trainer_implementation_sha256": batch_report["trainer_implementation_sha256"],
                },
                "seed_results": seed_results,
            }
        _atomic_json(args.output, payload)
        progress.close(status="done")
        return 0
    except (ValueError, RuntimeError) as exc:
        if progress is not None:
            progress.close(status="failed")
        _parser().error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
