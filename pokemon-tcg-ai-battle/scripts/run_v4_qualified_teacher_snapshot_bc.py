"""Run a research-only V4 recurrent BC arm from a sealed teacher snapshot.

This runner deliberately preserves the snapshot's train/development/test split
instead of rebuilding a new split from all records.  The test partition is
never used for training or validation.  It is a bounded experiment helper; it
has no promotion, Champion, or submission authority.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from typing import Any

import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from mage_ptcg.meta_specialist.neural_model_v4 import (  # noqa: E402
    SpecialistModelV4,
    load_specialist_checkpoint_v4,
)
from mage_ptcg.meta_specialist.recurrent_bc_v4 import (  # noqa: E402
    ACTION_BALANCED_WEIGHTS_V1,
    RESEARCH_ONLY_OUTCOME_WEIGHTED_V4,
    RESEARCH_ONLY_UNIFORM_WEIGHT,
    RecurrentBCSequenceV4,
    RecurrentBCStepV4,
    selected_objective_sha256_v4,
    train_recurrent_bc_v4,
    trainer_implementation_sha256_v4,
)
from mage_ptcg.meta_specialist.outcome_weighted_v4 import (  # noqa: E402
    outcome_quality_weight_v4,
    outcome_weight_summary_v4,
)
from mage_ptcg.meta_specialist.recurrent_dataset_v3 import (  # noqa: E402
    RecurrentRecordAuthorityRowV3,
)
from mage_ptcg.meta_specialist.recurrent_dataset_v4 import _project_record_steps_v4  # noqa: E402
from mage_ptcg.meta_specialist.representation_benchmark_v3 import (  # noqa: E402
    _load_production_vocabulary_v3,
)


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _atomic_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.tmp.", dir=path.parent)
    try:
        with open(descriptor, "w", encoding="utf-8", closefd=True) as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
        Path(temporary).replace(path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _load_snapshot(root: Path) -> tuple[dict[str, object], dict[str, object], list[bytes], list[dict[str, object]]]:
    index_path = root / "snapshot_index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    if index.get("schema_version") != "specialist-training-snapshot-index-v1":
        raise ValueError("sealed teacher snapshot index schema is invalid")
    chunks = index.get("shards")
    if not isinstance(chunks, list) or len(chunks) != 1 or not isinstance(chunks[0], dict):
        raise ValueError("runner requires exactly one sealed snapshot shard")
    # The index's ``shards`` entry names the derived snapshot JSON.  The
    # physical teacher records remain in the collection root's JSONL shard.
    shard_path = root / "dataset-0000.jsonl"
    raw_shard = shard_path.read_bytes()
    expected_shard_sha = chunks[0].get("sha256") or chunks[0].get("dataset_snapshot_sha256")
    if isinstance(expected_shard_sha, str) and _sha256_bytes(raw_shard) != expected_shard_sha:
        raise ValueError("sealed snapshot shard SHA-256 changed")
    raw_lines = raw_shard.splitlines(keepends=True)
    records = [json.loads(line) for line in raw_lines]
    snapshot_path = root / "snapshot-0000.json"
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    examples = snapshot.get("examples")
    if not isinstance(examples, list) or len(examples) != len(records):
        raise ValueError("sealed snapshot example count does not match its shard")
    by_id = {record.get("record_id"): (index, record, raw_lines[index]) for index, record in enumerate(records)}
    if len(by_id) != len(records):
        raise ValueError("sealed teacher snapshot has duplicate record IDs")
    for example in examples:
        record_id = example.get("record_id") if isinstance(example, dict) else None
        if record_id not in by_id:
            raise ValueError("sealed snapshot example is absent from its physical shard")
        _index, record, _line = by_id[record_id]
        if example.get("record_content_hash") != record.get("content_hash"):
            raise ValueError("sealed snapshot record content hash does not match raw record")
    return index, snapshot, raw_lines, records


def _record_outcome_weight_v4(record: dict[str, object], *, outcome_weighted: bool) -> float:
    """Return one immutable episode weight from the sealed teacher value target."""
    if not outcome_weighted:
        return 1.0
    teacher = record.get("teacher")
    if not isinstance(teacher, dict) or "value_target" not in teacher:
        raise ValueError("outcome-weighted snapshot record lacks teacher.value_target")
    return outcome_quality_weight_v4(teacher["value_target"])


def _materialize_sequences(
    root: Path, *, burn_in: int, exclude_empty_selection: bool, outcome_weighted: bool = False,
) -> tuple[tuple[RecurrentBCSequenceV4, ...], tuple[RecurrentBCSequenceV4, ...], dict[str, object]]:
    index, snapshot, raw_lines, records = _load_snapshot(root)
    raw_by_id = {record["record_id"]: (index, record, raw_lines[index]) for index, record in enumerate(records)}
    examples = [example for example in snapshot["examples"] if example["split"] in {"train", "development"}]
    examples.sort(key=lambda example: raw_by_id[example["record_id"]][0])
    vocabulary = _load_production_vocabulary_v3()
    grouped: dict[str, list[RecurrentBCStepV4]] = defaultdict(list)
    group_partition: dict[str, str] = {}
    group_component: dict[str, str] = {}
    last_episode: str | None = None
    closed: set[str] = set()
    records_by_partition = Counter()
    steps_by_partition = Counter()
    capped_by_partition = Counter()
    empty_selection_by_partition = Counter()
    empty_selection_steps_by_partition = Counter()
    outcome_targets_by_partition: dict[str, list[float]] = {"train": [], "validation": []}
    episode_outcome_targets: dict[str, float] = {}
    for example in examples:
        physical_index, record, raw_line = raw_by_id[example["record_id"]]
        episode = record.get("episode_id_hash")
        if not isinstance(episode, str) or not episode:
            raise ValueError("teacher record episode identity is invalid")
        partition = "train" if example["split"] == "train" else "validation"
        if episode in closed:
            raise ValueError("snapshot physical order revisits an episode")
        previous = last_episode == episode
        if not previous and last_episode is not None:
            closed.add(last_episode)
        last_episode = episode
        if episode in group_partition and group_partition[episode] != partition:
            raise ValueError("one episode crosses train/development split")
        group_partition[episode] = partition
        group_component[episode] = episode
        row = RecurrentRecordAuthorityRowV3(
            record=record,
            model_payload=example["model_input"],
            shard="dataset-0000.jsonl",
            line=physical_index + 1,
            record_id=str(record["record_id"]),
            content_hash=str(record["content_hash"]),
            raw_line_sha256=_sha256_bytes(raw_line),
            component_id=episode,
            partition=partition,
        )
        projected = _project_record_steps_v4(
            row, vocabulary=vocabulary, episode_start=not previous,
        )
        # The research trainer's explicit mode requires uniform loss weights.
        # Keep the sealed cap in the audit counters, but do not silently mix
        # cap-weighted and uniform objectives in one short arm.
        sealed_quality = float(example.get("example_quality_weight", 1.0))
        quality = _record_outcome_weight_v4(record, outcome_weighted=outcome_weighted)
        teacher = record.get("teacher")
        if outcome_weighted:
            assert isinstance(teacher, dict)
            target = float(teacher["value_target"])
            previous_target = episode_outcome_targets.get(episode)
            if previous_target is not None and previous_target != target:
                raise ValueError("one episode contains inconsistent teacher.value_target values")
            if previous_target is None:
                episode_outcome_targets[episode] = target
                outcome_targets_by_partition[partition].append(target)
        teacher = record.get("teacher")
        mass_rows = teacher.get("mass_rows") if isinstance(teacher, dict) else None
        empty_selection = (
            not isinstance(mass_rows, list)
            or not mass_rows
            or any(
                isinstance(mass_row, dict)
                and isinstance(mass_row.get("selection"), list)
                and not mass_row.get("selection")
                for mass_row in mass_rows
            )
        )
        if empty_selection:
            empty_selection_by_partition[partition] += 1
            empty_selection_steps_by_partition[partition] += len(projected)
        supervision_weight = 0.0 if exclude_empty_selection and empty_selection else 1.0
        for step in projected:
            grouped[episode].append(RecurrentBCStepV4(
                state=step.state,
                target_index=step.target_index,
                episode_group=step.episode_group,
                quality_weight=quality,
                model_input=step.model_input,
                step_input=step.step_input,
                target_masses=step.target_masses,
                reach_mass=step.reach_mass,
                episode_start=step.episode_start,
                component_id=step.component_id,
                partition=step.partition,
                record_id=step.record_id,
                content_hash=step.content_hash,
                research_only=True,
                supervision_weight=supervision_weight,
            ))
        records_by_partition[partition] += 1
        steps_by_partition[partition] += len(projected)
        capped_by_partition[partition] += sealed_quality < 1.0
    sequences: list[RecurrentBCSequenceV4] = []
    for episode, steps in grouped.items():
        if not steps or not steps[0].episode_start:
            raise ValueError("sequence does not start at an episode boundary")
        sequences.append(RecurrentBCSequenceV4(
            lane="qualified-teacher-snapshot-v4",
            episode_group=episode,
            component_id=group_component[episode],
            partition=group_partition[episode],
            steps=tuple(steps),
            burn_in=burn_in,
            research_only=True,
        ))
    train = tuple(sequence for sequence in sequences if sequence.partition == "train")
    validation = tuple(sequence for sequence in sequences if sequence.partition == "validation")
    if not train or not validation:
        raise ValueError("snapshot has no train or validation episodes")
    stats = {
        "snapshot_index_file_sha256": _sha256_file(root / "snapshot_index.json"),
        "snapshot_shard_file_sha256": _sha256_file(root / "snapshot-0000.json"),
        "dataset_snapshot_sha256": snapshot.get("dataset_snapshot_sha256"),
        "records_by_partition": dict(sorted(records_by_partition.items())),
        "steps_by_partition": dict(sorted(steps_by_partition.items())),
        "episodes_by_partition": {
            "train": len(train), "validation": len(validation),
        },
        "capped_records_by_partition": dict(sorted(capped_by_partition.items())),
        "empty_selection_records_by_partition": dict(sorted(empty_selection_by_partition.items())),
        "empty_selection_steps_by_partition": dict(sorted(empty_selection_steps_by_partition.items())),
        "empty_selection_policy": (
            "context_only_excluded_from_loss"
            if exclude_empty_selection else "mapped_by_snapshot_projection"
        ),
        "training_quality_weight_policy": "uniform_research_1.0; sealed_cap_reported_only",
        "outcome_weight_policy": (
            "max_normalized_win_1.0_draw_0.6666666666666666_loss_0.3333333333333333"
            if outcome_weighted else "disabled_uniform_1.0"
        ),
        "outcome_summary_by_partition": {
            partition: outcome_weight_summary_v4(values)
            for partition, values in outcome_targets_by_partition.items()
        },
        "source_examples_total": len(snapshot["examples"]),
        "source_split_counts": snapshot.get("split_counts"),
        "test_records_excluded": sum(example["split"] == "test" for example in snapshot["examples"]),
    }
    return train, validation, stats


def _load_model(checkpoint: Path, *, file_sha: str, tensor_sha: str, seed: int, device: torch.device) -> SpecialistModelV4:
    model = SpecialistModelV4(card_vocabulary_size=1267, hidden_dim=128, embedding_dim=64, seed=seed).to(device)
    load_specialist_checkpoint_v4(
        checkpoint, model, expected_file_sha256=file_sha,
        expected_tensor_state_sha256=tensor_sha,
    )
    return model


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--checkpoint-seed0", type=Path, required=True)
    parser.add_argument("--checkpoint-seed0-file-sha256", required=True)
    parser.add_argument("--checkpoint-seed0-tensor-sha256", required=True)
    parser.add_argument("--checkpoint-seed1", type=Path, required=True)
    parser.add_argument("--checkpoint-seed1-file-sha256", required=True)
    parser.add_argument("--checkpoint-seed1-tensor-sha256", required=True)
    parser.add_argument("--epochs", type=int, default=1)
    parser.add_argument("--patience", type=int, default=0)
    parser.add_argument("--learning-rate", type=float, default=1e-4)
    parser.add_argument("--tbptt-steps", type=int, default=8)
    parser.add_argument("--burn-in", type=int, default=1)
    parser.add_argument(
        "--exclude-empty-selection", action="store_true",
        help="keep empty teacher selections as recurrent context but set their supervision weight to zero",
    )
    parser.add_argument(
        "--action-balanced", action="store_true",
        help="use the pre-registered V4 macro-action loss weights; no weight sweep",
    )
    parser.add_argument(
        "--outcome-weighted", action="store_true",
        help="weight every prefix by the sealed episode teacher.value_target",
    )
    parser.add_argument("--device", default="cuda:0", choices=("cpu", "cuda:0"))
    parser.add_argument("--torch-threads", type=int, default=2)
    args = parser.parse_args()
    if args.epochs < 1 or args.patience < 0 or args.learning_rate <= 0.0 or args.burn_in < 0:
        parser.error("invalid training configuration")
    if args.device.startswith("cuda") and not torch.cuda.is_available():
        parser.error("requested CUDA device is unavailable")
    torch.set_num_threads(args.torch_threads)
    device = torch.device(args.device)
    train, validation, stats = _materialize_sequences(
        args.snapshot_root,
        burn_in=args.burn_in,
        exclude_empty_selection=args.exclude_empty_selection,
        outcome_weighted=args.outcome_weighted,
    )
    objective_sha = selected_objective_sha256_v4(tuple(train) + tuple(validation))
    trainer_sha = trainer_implementation_sha256_v4()
    action_type_weights = dict(ACTION_BALANCED_WEIGHTS_V1) if args.action_balanced else None
    mode = RESEARCH_ONLY_OUTCOME_WEIGHTED_V4 if args.outcome_weighted else RESEARCH_ONLY_UNIFORM_WEIGHT
    args.output_root.mkdir(parents=True, exist_ok=True)
    seed_args = {
        0: (args.checkpoint_seed0, args.checkpoint_seed0_file_sha256, args.checkpoint_seed0_tensor_sha256),
        1: (args.checkpoint_seed1, args.checkpoint_seed1_file_sha256, args.checkpoint_seed1_tensor_sha256),
    }
    results: dict[str, object] = {}
    for seed, (checkpoint, file_sha, tensor_sha) in seed_args.items():
        output = args.output_root / f"seed-{seed}"
        output.mkdir(parents=True, exist_ok=True)
        model = _load_model(checkpoint, file_sha=file_sha, tensor_sha=tensor_sha, seed=seed, device=device)
        run_config: dict[str, object] = {
            "source": stats,
            "objective_sha256": objective_sha,
            "trainer_implementation_sha256": trainer_sha,
            "initial_checkpoint": {"path": str(checkpoint), "file_sha256": file_sha, "tensor_state_sha256": tensor_sha},
            "seed": seed,
            "device": str(device),
            "epochs": args.epochs,
            "patience": args.patience,
            "learning_rate": args.learning_rate,
            "tbptt_steps": args.tbptt_steps,
            "burn_in": args.burn_in,
            "test_partition_used": False,
            "exclude_empty_selection": args.exclude_empty_selection,
            "action_type_weights": action_type_weights,
            "mode": mode,
            "outcome_weighted": args.outcome_weighted,
        }
        result = train_recurrent_bc_v4(
            model, train, validation,
            mode=mode,
            output_dir=output,
            sequence_order_seed=seed,
            epochs=args.epochs,
            patience=args.patience,
            learning_rate=args.learning_rate,
            tbptt_steps=args.tbptt_steps,
            gradient_clip_norm=1.0,
            run_config=run_config,
            action_type_weights=action_type_weights,
        )
        results[str(seed)] = {
            "best_epoch": result.best_epoch,
            "epochs_completed": result.epochs_completed,
            "initial_validation_complete_action_nll": result.initial_validation_complete_action_nll,
            "best_validation_complete_action_nll": result.best_validation_complete_action_nll,
            "validation_delta_nll": result.validation_delta_nll,
            "improved": result.improved,
            "optimizer_updates_completed": result.optimizer_updates_completed,
            "elapsed_seconds": result.elapsed_seconds,
            "best_checkpoint_path": str(result.best_checkpoint_path),
            "best_checkpoint_file_sha256": result.best_checkpoint_file_sha256,
            "best_checkpoint_tensor_state_sha256": result.best_checkpoint_tensor_state_sha256,
            "validation_by_component": dict(result.validation_by_component),
            "history": [dict(row) for row in result.history],
        }
    report = {
        "schema": "meta-specialist-v4-qualified-teacher-snapshot-bc-research-v1",
        "promotion_authority": False,
        "source": stats,
        "objective_sha256": objective_sha,
        "trainer_implementation_sha256": trainer_sha,
        "mode": mode,
        "training_config": {
            "device": str(device), "epochs": args.epochs, "patience": args.patience,
            "learning_rate": args.learning_rate, "tbptt_steps": args.tbptt_steps,
            "burn_in": args.burn_in, "seeds": [0, 1], "test_partition_used": False,
            "exclude_empty_selection": args.exclude_empty_selection,
            "action_type_weights": action_type_weights,
            "outcome_weighted": args.outcome_weighted,
        },
        "results": results,
    }
    _atomic_json(args.output_root / "report.json", report)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
