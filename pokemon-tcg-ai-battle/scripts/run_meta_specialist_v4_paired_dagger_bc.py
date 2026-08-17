#!/usr/bin/env python3
"""Run a seed-matched V4 DAgger arm from a sealed paired-input manifest.

The existing DAgger runner intentionally accepts one screen/checkpoint pair
for two model seeds.  This wrapper is the opt-in path for causal comparisons:
each seed gets its own actor screen and warm-start checkpoint, while both
seeds share the same sealed base selection and training hyperparameters.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
import hashlib
import json
from pathlib import Path
import sys
import time

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
    positive_stop_target_metrics_v4,
    selected_objective_sha256_v4,
    train_recurrent_bc_v4,
    trainer_implementation_sha256_v4,
)
from mage_ptcg.meta_specialist.v4_imitation_metrics import (  # noqa: E402
    evaluate_recurrent_imitation_v4,
)

from scripts import run_meta_specialist_v4_dagger_bc as base_runner  # noqa: E402


REPORT_SCHEMA = "meta-specialist-v4-dagger-paired-bc-report-v1"


def _parse_seeds(value: str) -> tuple[int, ...]:
    try:
        seeds = tuple(int(item.strip()) for item in value.split(",") if item.strip())
    except ValueError as exc:
        raise argparse.ArgumentTypeError("seeds must be comma-separated integers") from exc
    if len(seeds) != 2 or len(set(seeds)) != 2:
        raise argparse.ArgumentTypeError("exactly two distinct seeds are required")
    return seeds


def _resolve_device(value: str) -> torch.device:
    device = torch.device(value)
    if device.type == "cuda" and not torch.cuda.is_available():
        raise ValueError(f"requested CUDA device is unavailable: {value}")
    return device


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection-manifest", type=Path, required=True)
    parser.add_argument("--selection-manifest-sha256", required=True)
    parser.add_argument("--paired-seed-manifest", type=Path, required=True)
    parser.add_argument("--paired-seed-manifest-sha256", required=True)
    parser.add_argument("--lane", choices=("alakazam", "archaludon"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--progress-path", type=Path)
    parser.add_argument("--seeds", type=_parse_seeds, default=(0, 1))
    parser.add_argument("--max-records", type=int, default=65536)
    parser.add_argument("--subset-fraction", type=float, default=0.05)
    parser.add_argument("--burn-in", type=int, default=1)
    parser.add_argument("--episodes-per-partition", type=int, default=512)
    parser.add_argument("--components-per-partition", type=int, default=512)
    parser.add_argument("--train-episodes-per-partition", type=int, default=512)
    parser.add_argument("--validation-episodes-per-partition", type=int, default=128)
    parser.add_argument("--train-components-per-partition", type=int, default=512)
    parser.add_argument("--validation-components-per-partition", type=int, default=128)
    parser.add_argument("--require-positive-stop", action="store_true")
    parser.add_argument("--dagger-fraction", type=float, default=1.0 / 3.0)
    parser.add_argument("--focus-opponents", type=base_runner._parse_focus_names, required=True)
    parser.add_argument(
        "--focus-seats",
        type=lambda value: base_runner._parse_focus_ints(value, field="focus_seats", minimum=0, maximum=1),
        required=True,
    )
    parser.add_argument("--strict-focus-targets", action="store_true")
    parser.add_argument(
        "--focus-action-types",
        type=lambda value: base_runner._parse_focus_ints(value, field="focus_action_types", minimum=0, maximum=16),
        default=(9, 13, 14),
    )
    parser.add_argument("--action-type-weights", type=base_runner._parse_action_type_weights, default=None)
    parser.add_argument("--hidden-dim", type=int, default=128)
    parser.add_argument("--embedding-dim", type=int, default=64)
    parser.add_argument("--tbptt-steps", type=int, default=8)
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--patience", type=int, default=1)
    parser.add_argument("--learning-rate", type=float, default=3e-4)
    parser.add_argument("--gradient-clip-norm", type=float, default=1.0)
    parser.add_argument("--device", choices=("cpu", "cuda:0"), default="cpu")
    return parser


def _read_seed_inputs(
    binding: Mapping[str, object], *, lane: str, focus_opponents: Sequence[str], focus_seats: Sequence[int],
    strict_focus_targets: bool,
) -> tuple[dict[str, object], tuple[dict[str, object], ...], tuple[dict[str, object], ...] | None, tuple[object, ...]]:
    screen_path = Path(str(binding["screen_path"]))
    screen = base_runner._read_hashed_json(
        screen_path, str(binding["screen_file_sha256"]), field="paired_screen",
    )
    subject_identity = base_runner._validate_screen_subject_identity(screen, lane=lane)
    base_runner._validate_dagger_seed_checkpoint_binding_v4(screen, binding=binding)
    transitions_path = Path(str(binding["transitions_path"]))
    if screen.get("transitions_path") is None or Path(str(screen["transitions_path"])).resolve() != transitions_path.resolve():
        raise ValueError("paired screen transitions path differs from its seed binding")
    rows = base_runner._read_transition_rows(
        transitions_path,
        expected_sha=str(binding["transitions_file_sha256"]),
        expected_screen=screen,
    )
    selected_rows, target_metadata = base_runner._select_dagger_transition_rows_v4(
        rows,
        screen=screen,
        strict_focus_targets=strict_focus_targets,
        focus_opponents=focus_opponents,
        focus_seats=focus_seats,
    )
    focus_ids = base_runner.prioritized_dagger_component_ids_v4(
        selected_rows,
        focus_opponents=focus_opponents,
        focus_seats=focus_seats,
        focus_action_types=(),
    )
    if not focus_ids:
        raise ValueError("paired seed screen has no complete DAgger focus component")
    dagger = base_runner.build_dagger_sequences_v4(selected_rows, lane=lane)
    if target_metadata is None:
        strict_report = None
    else:
        strict_report = {"available_metadata": target_metadata, "focus_ids": focus_ids}
    return subject_identity, tuple(selected_rows), strict_report, tuple(dagger)


def _training_run_config(
    *, args: argparse.Namespace, subset: object, subject_identity: Mapping[str, str], binding: Mapping[str, object],
    paired_identity: Mapping[str, str], base: Sequence[object], dagger: Sequence[object], mixed: Sequence[object],
    strict_report: Mapping[str, object] | None,
) -> dict[str, object]:
    run_config: dict[str, object] = {
        "lane": args.lane,
        "subject_archetype_id": subject_identity["archetype_id"],
        "subject_deck_csv_path": subject_identity["deck_csv_path"],
        "subject_deck_file_sha256": subject_identity["deck_file_sha256"],
        "selection_manifest_file_sha256": subset.selection_manifest_file_sha256,
        "paired_seed_manifest": dict(paired_identity),
        "seed": int(binding["seed"]),
        "screen_path": binding["screen_path"],
        "screen_file_sha256": binding["screen_file_sha256"],
        "transitions_path": binding["transitions_path"],
        "transitions_file_sha256": binding["transitions_file_sha256"],
        "init_checkpoint_path": binding["init_checkpoint_path"],
        "init_checkpoint_file_sha256": binding["init_checkpoint_file_sha256"],
        "init_checkpoint_tensor_state_sha256": binding["init_checkpoint_tensor_state_sha256"],
        "base_selected_sequence_sha256": selected_objective_sha256_v4(base),
        "dagger_sequence_sha256": hashlib.sha256(
            "".join(base_runner.dagger_record_sha256_v4(row) for row in dagger).encode("ascii")
        ).hexdigest(),
        "selected_sequence_sha256": selected_objective_sha256_v4(mixed),
        "dagger_fraction": float(args.dagger_fraction),
        "focus_opponents": list(args.focus_opponents),
        "focus_seats": list(args.focus_seats),
        "focus_action_types": list(args.focus_action_types),
        "action_type_weights": args.action_type_weights,
        "burn_in": args.burn_in,
        "max_records": args.max_records,
        "coverage_target": {
            "train_episodes_per_partition": args.train_episodes_per_partition,
            "validation_episodes_per_partition": args.validation_episodes_per_partition,
            "train_components_per_partition": args.train_components_per_partition,
            "validation_components_per_partition": args.validation_components_per_partition,
            "require_positive_stop": args.require_positive_stop,
        },
        "model": {
            "card_vocabulary_size": subset.card_vocabulary_size,
            "hidden_dim": args.hidden_dim,
            "embedding_dim": args.embedding_dim,
        },
        "trainer": {
            "epochs": args.epochs,
            "patience": args.patience,
            "learning_rate": args.learning_rate,
            "tbptt_steps": args.tbptt_steps,
            "gradient_clip_norm": args.gradient_clip_norm,
        },
        "trainer_implementation_sha256": trainer_implementation_sha256_v4(),
    }
    if strict_report is not None:
        # _strict_target_sequence_report_v4 already returns the closed,
        # selected-vs-available report.  Preserve that exact schema rather
        # than rebuilding it from an intermediate materialization object.
        run_config["strict_target_selection"] = dict(strict_report)
    return run_config


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    try:
        device = _resolve_device(args.device)
        bindings, paired_identity = base_runner._resolve_paired_seed_provenance_v4(
            seeds=args.seeds, lane=args.lane, manifest_path=args.paired_seed_manifest,
            manifest_file_sha256=args.paired_seed_manifest_sha256,
        )
        if not args.strict_focus_targets:
            raise ValueError("paired DAgger requires --strict-focus-targets")
        if not 0.0 <= args.dagger_fraction < 1.0:
            raise ValueError("dagger_fraction must be in [0, 1)")
        base_runner._write_progress(args.progress_path, {
            "status": "running", "stage": "selection_materialize", "device": str(device),
            "seeds_total": len(bindings),
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
            require_positive_stop=args.require_positive_stop,
            mode=RESEARCH_ONLY_UNIFORM_WEIGHT,
        )
        base = tuple(subset.sequences)
        seed_inputs: list[dict[str, object]] = []
        common_subject: dict[str, str] | None = None
        for binding in bindings:
            subject, _selected_rows, strict_info, dagger = _read_seed_inputs(
                binding,
                lane=args.lane,
                focus_opponents=args.focus_opponents,
                focus_seats=args.focus_seats,
                strict_focus_targets=True,
            )
            if common_subject is None:
                common_subject = subject
            elif subject != common_subject:
                raise ValueError("paired seed screens disagree on subject deck identity")
            focus_ids = tuple(strict_info["focus_ids"]) if strict_info is not None else ()
            mixed = base_runner.mix_dagger_sequences_v4(
                base, dagger, dagger_fraction=args.dagger_fraction,
                seed=int(binding["seed"]), priority_component_ids=focus_ids,
            )
            strict_selection = base_runner._strict_target_sequence_report_v4(
                focus_opponents=args.focus_opponents,
                focus_seats=args.focus_seats,
                available_metadata=strict_info["available_metadata"],
                base=base,
                dagger=dagger,
                mixed=mixed,
            )
            seed_inputs.append({
                "binding": binding,
                "subject_identity": subject,
                "dagger": dagger,
                "mixed": tuple(mixed),
                "strict_selection": strict_selection,
                "selected_sequence_sha256": selected_objective_sha256_v4(mixed),
                "dagger_sequence_sha256": hashlib.sha256(
                    "".join(base_runner.dagger_record_sha256_v4(row) for row in dagger).encode("ascii")
                ).hexdigest(),
            })
        if common_subject is None:
            raise ValueError("paired seed screens produced no subject identity")
        checkpoint_root = args.output.parent / f"{args.output.stem}-checkpoints"
        checkpoint_root.mkdir(parents=True, exist_ok=True)
        started = time.monotonic()
        seed_results: dict[str, dict[str, object]] = {}
        try:
            from tqdm import tqdm
            iterator = tqdm(seed_inputs, total=len(seed_inputs), desc="v4-paired-dagger-bc", unit="seed", dynamic_ncols=True)
        except ImportError:  # pragma: no cover
            iterator = seed_inputs
        for seed_index, item in enumerate(iterator, start=1):
            binding = item["binding"]
            seed = int(binding["seed"])
            mixed = tuple(item["mixed"])
            train = tuple(row for row in mixed if row.partition == "train")
            validation = tuple(row for row in mixed if row.partition == "validation")
            model = SpecialistModelV4(
                card_vocabulary_size=subset.card_vocabulary_size,
                hidden_dim=args.hidden_dim, embedding_dim=args.embedding_dim, seed=seed,
            ).to(device)
            load_specialist_checkpoint_v4(
                Path(str(binding["init_checkpoint_path"])), model,
                expected_file_sha256=str(binding["init_checkpoint_file_sha256"]),
                expected_tensor_state_sha256=str(binding["init_checkpoint_tensor_state_sha256"]),
            )
            base_runner._write_progress(args.progress_path, {
                "status": "running", "stage": "training", "seed": seed,
                "seed_index": seed_index, "seeds_total": len(seed_inputs),
            })
            run_config = _training_run_config(
                args=args, subset=subset, subject_identity=common_subject, binding=binding,
                paired_identity=paired_identity, base=base, dagger=item["dagger"], mixed=mixed,
                strict_report=item["strict_selection"],
            )
            result = train_recurrent_bc_v4(
                model, train, validation, mode=RESEARCH_ONLY_UNIFORM_WEIGHT,
                output_dir=checkpoint_root / f"seed-{seed}", sequence_order_seed=seed,
                epochs=args.epochs, patience=args.patience, learning_rate=args.learning_rate,
                tbptt_steps=args.tbptt_steps, gradient_clip_norm=args.gradient_clip_norm,
                action_type_weights=args.action_type_weights, run_config=run_config,
                resume=False,
                epoch_callback=lambda payload, current_seed=seed: base_runner._write_progress(
                    args.progress_path, {"status": "running", "stage": "training", "seed": current_seed, **payload}
                ),
                train_progress_callback=base_runner._make_training_progress_callback(
                    args.progress_path, seed=seed, epochs=args.epochs, started=started,
                ),
            )
            stop_metrics = positive_stop_target_metrics_v4(model, validation, mode=RESEARCH_ONLY_UNIFORM_WEIGHT)
            imitation_metrics = evaluate_recurrent_imitation_v4(model, validation, partition="validation", recurrence="carry")
            seed_results[str(seed)] = {
                "sequence_order_seed": seed,
                "screen_path": binding["screen_path"],
                "screen_file_sha256": binding["screen_file_sha256"],
                "transitions_path": binding["transitions_path"],
                "transitions_file_sha256": binding["transitions_file_sha256"],
                "init_checkpoint_path": binding["init_checkpoint_path"],
                "init_checkpoint_file_sha256": binding["init_checkpoint_file_sha256"],
                "init_checkpoint_tensor_state_sha256": binding["init_checkpoint_tensor_state_sha256"],
                "selected_sequence_sha256": item["selected_sequence_sha256"],
                "dagger_sequence_sha256": item["dagger_sequence_sha256"],
                "strict_target_selection": item["strict_selection"],
                "best_epoch": result.best_epoch,
                "epochs_completed": result.epochs_completed,
                "initial_validation_complete_action_nll": result.initial_validation_complete_action_nll,
                "best_validation_complete_action_nll": result.best_validation_complete_action_nll,
                "validation_delta_nll": result.validation_delta_nll,
                "improved": result.improved,
                "validation_by_component": dict(result.validation_by_component),
                "history": [dict(row) for row in result.history],
                "optimizer_updates_completed": result.optimizer_updates_completed,
                "elapsed_seconds": result.elapsed_seconds,
                "best_checkpoint_path": str(result.best_checkpoint_path),
                "best_checkpoint_file_sha256": result.best_checkpoint_file_sha256,
                "best_checkpoint_tensor_state_sha256": result.best_checkpoint_tensor_state_sha256,
                "validation_positive_stop_target_metrics": dict(stop_metrics),
                "validation_imitation_metrics": imitation_metrics,
            }
        report = {
            "schema": REPORT_SCHEMA,
            "mode": RESEARCH_ONLY_UNIFORM_WEIGHT,
            "promotion_authority": False,
            "status": "RESEARCH_ONLY_COMPLETE",
            "device": str(device),
            "elapsed_seconds": time.monotonic() - started,
            "paired_seed_manifest": dict(paired_identity),
            "selection_manifest": str(subset.selection_manifest_path),
            "selection_manifest_file_sha256": subset.selection_manifest_file_sha256,
            "lane": subset.lane,
            "subject_archetype_id": common_subject["archetype_id"],
            "subject_deck_csv_path": common_subject["deck_csv_path"],
            "subject_deck_file_sha256": common_subject["deck_file_sha256"],
            "base_records_by_partition": dict(subset.records_by_partition),
            "base_sequences_by_partition": {part: sum(row.partition == part for row in base) for part in ("train", "validation")},
            "seeds": list(args.seeds),
            "training_config": {
                "max_records": args.max_records, "subset_fraction": args.subset_fraction,
                "burn_in": args.burn_in, "dagger_fraction": args.dagger_fraction,
                "epochs": args.epochs, "patience": args.patience, "learning_rate": args.learning_rate,
                "tbptt_steps": args.tbptt_steps, "gradient_clip_norm": args.gradient_clip_norm,
                "action_type_weights": args.action_type_weights,
                "hidden_dim": args.hidden_dim, "embedding_dim": args.embedding_dim,
                "seeds": list(args.seeds), "device": str(device),
                "focus_opponents": list(args.focus_opponents), "focus_seats": list(args.focus_seats),
                "focus_action_types": list(args.focus_action_types), "strict_focus_targets": True,
            },
            "trainer_implementation_sha256": trainer_implementation_sha256_v4(),
            "seed_results": seed_results,
        }
        records = [
            {
                "seed": int(item["binding"]["seed"]),
                "screen_path": item["binding"]["screen_path"],
                "screen_file_sha256": item["binding"]["screen_file_sha256"],
                "transitions_path": item["binding"]["transitions_path"],
                "transitions_file_sha256": item["binding"]["transitions_file_sha256"],
                "init_checkpoint_path": item["binding"]["init_checkpoint_path"],
                "init_checkpoint_file_sha256": item["binding"]["init_checkpoint_file_sha256"],
                "init_checkpoint_tensor_state_sha256": item["binding"]["init_checkpoint_tensor_state_sha256"],
                "selected_sequence_sha256": item["selected_sequence_sha256"],
                "dagger_sequence_sha256": item["dagger_sequence_sha256"],
            }
            for item in seed_inputs
        ]
        report["paired_selected_sequence_sha256"] = base_runner._paired_selected_sequence_identity_v4(
            records, paired_manifest_identity=paired_identity,
        )
        base_runner._atomic_json(args.output, report)
        base_runner._write_progress(args.progress_path, {
            "status": "complete", "stage": "complete", "output": str(args.output),
            "seeds_completed": len(seed_results), "elapsed_seconds": report["elapsed_seconds"],
        })
        print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
        return 0
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
        parser.error(str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
