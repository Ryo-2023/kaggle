#!/usr/bin/env python3
"""Research-only actor-visible value AWR / filtered-BC pilot.

The source is a sealed qualified external-teacher snapshot.  The external
agent does not expose action probabilities, so this runner deliberately does
not invent an importance ratio.  It uses the committed hard action together
with a leave-fold-out actor-visible outcome baseline and applies either
bounded AWR weights or a positive-advantage filter.  V4 is warm-started from
an immutable checkpoint; only the research checkpoint produced by the
existing V4 trainer is written.

This is not a promotion or submission runner.  It records the target,
snapshot/checkpoint hashes, and authority flags, and requires ``--execute``
for any training side effect.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
import hashlib
import json
import math
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from mage_ptcg.meta_specialist.neural_model_v4 import (  # noqa: E402
    SpecialistModelV4,
    load_specialist_checkpoint_v4,
)
from mage_ptcg.meta_specialist.recurrent_bc_v4 import (  # noqa: E402
    RESEARCH_ONLY_OUTCOME_WEIGHTED_V4,
    RecurrentBCSequenceV4,
    RecurrentBCStepV4,
    selected_objective_sha256_v4,
    train_recurrent_bc_v4,
    trainer_implementation_sha256_v4,
)
from mage_ptcg.meta_specialist.public_confidence_ood_v1 import _bucket_id  # noqa: E402

from scripts.run_v4_qualified_teacher_snapshot_bc import (  # noqa: E402
    _load_snapshot,
    _materialize_sequences,
)


SCHEMA_V1 = "meta-specialist-public-state-awr-run-v1"
TARGET_SCHEMA_V1 = "meta-specialist-public-state-awr-target-v1"
TARGET_KIND_V1 = "cross_fitted_teacher_outcome_advantage"
_HEX64 = frozenset("0123456789abcdef")


class AWRTargetError(ValueError):
    """Raised when an AWR target would be ambiguous or leaky."""


def _sha(value: object, *, field: str) -> str:
    if type(value) is not str or len(value) != 64 or any(char not in _HEX64 for char in value):
        raise AWRTargetError(f"{field} must be a lowercase SHA-256")
    return value


def _file_sha(path: Path) -> str:
    if not path.is_file() or path.is_symlink():
        raise AWRTargetError(f"{path} must be a regular non-symlink file")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise AWRTargetError("target payload is not canonical JSON") from exc


def _fold(episode_id: str, fold_count: int) -> int:
    _sha(episode_id, field="episode_id")
    return int(episode_id[:16], 16) % fold_count


@dataclass(frozen=True, slots=True)
class AWRWeightV1:
    advantage: float
    raw_weight: float
    normalized_quality: float
    supervision_weight: float


def aggregate_record_advantages_v1(
    rows: Sequence[tuple[str, str, float]],
) -> dict[tuple[str, str], float]:
    """Average prefix advantages per physical record.

    V4's recurrent trainer requires all decoder prefixes belonging to one
    committed physical record to share one quality weight.  Aggregating here
    preserves that contract while still using every actor-visible prefix in
    the frozen target calculation.
    """
    totals: dict[tuple[str, str], list[float]] = {}
    for episode_id, record_id, advantage in rows:
        _sha(episode_id, field="episode_id")
        _sha(record_id, field="record_id")
        if type(advantage) not in (int, float) or type(advantage) is bool or not math.isfinite(float(advantage)):
            raise AWRTargetError("record advantage must be finite")
        totals.setdefault((episode_id, record_id), []).append(float(advantage))
    if not totals:
        raise AWRTargetError("record advantage rows are empty")
    return {
        key: math.fsum(values) / len(values)
        for key, values in totals.items()
    }


def awr_weight_from_advantage_v1(
    advantage: float,
    *,
    temperature: float,
    max_weight: float,
    filtered: bool = False,
) -> AWRWeightV1:
    """Map one cross-fitted advantage to a bounded V4 research weight.

    V4's research dataclass accepts quality weights in ``(0, 1]``.  The raw
    AWR weight is therefore divided by the fixed configured cap.  This scale
    does not change the positive/negative ordering and is part of the target
    manifest.  Filtered BC changes only the supervision mask; negative rows
    remain in recurrent context when their sequence is retained.
    """
    if type(advantage) not in (int, float) or type(advantage) is bool or not math.isfinite(float(advantage)):
        raise AWRTargetError("advantage must be finite")
    if type(temperature) not in (int, float) or type(temperature) is bool or not math.isfinite(float(temperature)) or temperature <= 0:
        raise AWRTargetError("temperature must be finite and positive")
    if type(max_weight) not in (int, float) or type(max_weight) is bool or not math.isfinite(float(max_weight)) or max_weight <= 0:
        raise AWRTargetError("max_weight must be finite and positive")
    clipped_log_weight = min(float(advantage) / float(temperature), math.log(float(max_weight)))
    raw_weight = math.exp(clipped_log_weight)
    quality = max(1.0e-12, min(1.0, raw_weight / float(max_weight)))
    supervision = 0.0 if filtered and float(advantage) <= 0.0 else 1.0
    return AWRWeightV1(float(advantage), raw_weight, quality, supervision)


def build_cross_fitted_advantage_table_v1(
    episode_returns: Mapping[str, float],
    episode_buckets: Mapping[str, Sequence[str]],
    *,
    fold_count: int,
    advantage_clip: float,
) -> dict[str, dict[str, dict[str, object]]]:
    """Build leave-fold-out bucket baselines without opponent/seat features."""
    if type(episode_returns) is not dict or type(episode_buckets) is not dict:
        raise AWRTargetError("episode inputs must be exact mappings")
    if type(fold_count) is not int or fold_count < 2:
        raise AWRTargetError("fold_count must be at least two")
    if type(advantage_clip) not in (int, float) or type(advantage_clip) is bool or not math.isfinite(float(advantage_clip)) or advantage_clip <= 0:
        raise AWRTargetError("advantage_clip must be finite and positive")
    ids = tuple(sorted(episode_returns))
    if len(ids) <= fold_count or set(ids) != set(episode_buckets):
        raise AWRTargetError("episode returns/buckets do not form a closed split")
    for episode_id in ids:
        _sha(episode_id, field="episode_id")
        value = episode_returns[episode_id]
        if type(value) not in (int, float) or type(value) is bool or not math.isfinite(float(value)):
            raise AWRTargetError("episode return must be finite")
        if not episode_buckets[episode_id] or any(type(bucket) is not str or not bucket for bucket in episode_buckets[episode_id]):
            raise AWRTargetError("episode buckets must be nonempty strings")
    folds = {episode_id: _fold(episode_id, fold_count) for episode_id in ids}
    table: dict[str, dict[str, dict[str, object]]] = {}
    for episode_id in ids:
        own_fold = folds[episode_id]
        rows: dict[str, dict[str, object]] = {}
        for bucket in sorted(set(episode_buckets[episode_id])):
            same_bucket_external = [
                other for other in ids
                if folds[other] != own_fold and bucket in set(episode_buckets[other])
            ]
            baseline_source = "bucket_external"
            baseline_ids = same_bucket_external
            if not baseline_ids:
                baseline_ids = [other for other in ids if folds[other] != own_fold]
                baseline_source = "external_global_fallback"
            if not baseline_ids:
                raise AWRTargetError("no external baseline is available for an episode fold")
            baseline = math.fsum(float(episode_returns[other]) for other in baseline_ids) / len(baseline_ids)
            advantage = float(episode_returns[episode_id]) - baseline
            advantage = max(-float(advantage_clip), min(float(advantage_clip), advantage))
            rows[bucket] = {
                "baseline": baseline,
                "advantage": advantage,
                "baseline_source": baseline_source,
                "baseline_episode_ids": list(baseline_ids),
            }
        table[episode_id] = rows
    return table


def _snapshot_episode_returns(snapshot_root: Path) -> tuple[dict[str, float], dict[str, object]]:
    index, snapshot, raw_lines, records = _load_snapshot(snapshot_root)
    episode_returns: dict[str, float] = {}
    for record in records:
        episode = record.get("episode_id_hash")
        teacher = record.get("teacher")
        if type(episode) is not str or not isinstance(teacher, dict) or teacher.get("value_target") is None:
            raise AWRTargetError("snapshot record lacks an episode outcome target")
        value = teacher["value_target"]
        if type(value) not in (int, float) or type(value) is bool or not math.isfinite(float(value)):
            raise AWRTargetError("snapshot episode outcome is invalid")
        if episode in episode_returns and episode_returns[episode] != float(value):
            raise AWRTargetError("one snapshot episode contains inconsistent outcomes")
        episode_returns[episode] = float(value)
    if len(episode_returns) <= 2:
        raise AWRTargetError("snapshot has too few outcome episodes for cross-fitting")
    raw_shard = b"".join(raw_lines)
    provenance = {
        "snapshot_index_file_sha256": _file_sha(snapshot_root / "snapshot_index.json"),
        "snapshot_file_sha256": _file_sha(snapshot_root / "snapshot-0000.json"),
        "dataset_shard_file_sha256": hashlib.sha256(raw_shard).hexdigest(),
        "snapshot_manifest_id": snapshot.get("manifest_id"),
        "examples_total": len(snapshot.get("examples", [])),
        "episodes_total": len(episode_returns),
    }
    return episode_returns, provenance


def _step_bucket(step: RecurrentBCStepV4) -> str:
    effective_domain = len(step.step_input.allowed_semantic_classes) + int(step.step_input.stop_available)
    if effective_domain < 1:
        raise AWRTargetError("recurrent step has no effective legal domain")
    try:
        bucket = _bucket_id(step.model_input, step.step_input, effective_domain)
    except (AttributeError, IndexError, TypeError, ValueError) as exc:
        raise AWRTargetError("actor-visible bucket derivation failed") from exc
    _sha(bucket, field="public bucket")
    return bucket


def build_awr_sequences_v1(
    snapshot_root: Path,
    *,
    burn_in: int = 1,
    temperature: float = 0.25,
    max_weight: float = 4.0,
    filtered: bool = False,
) -> tuple[tuple[RecurrentBCSequenceV4, ...], tuple[RecurrentBCSequenceV4, ...], dict[str, object]]:
    """Materialize V4 sequences and attach frozen cross-fitted AWR weights."""
    if type(burn_in) is not int or burn_in < 0:
        raise AWRTargetError("burn_in must be a nonnegative integer")
    train, validation, source_stats = _materialize_sequences(
        snapshot_root, burn_in=burn_in, exclude_empty_selection=True, outcome_weighted=False,
    )
    episode_returns, provenance = _snapshot_episode_returns(snapshot_root)
    sequences = tuple(train) + tuple(validation)
    sequence_episode_ids = {sequence.episode_group for sequence in sequences}
    missing = sequence_episode_ids - set(episode_returns)
    if missing:
        raise AWRTargetError("snapshot sequence/outcome episode sets differ")
    # The sealed test partition is intentionally absent from the materialized
    # V4 sequences.  It must not enter either the training target or the
    # cross-fitted baseline, so restrict outcomes to the admitted train/dev
    # episode set before fitting the table.
    episode_returns = {
        episode: value for episode, value in episode_returns.items()
        if episode in sequence_episode_ids
    }
    episode_buckets: dict[str, tuple[str, ...]] = {}
    for sequence in sequences:
        episode_buckets[sequence.episode_group] = tuple(sorted({_step_bucket(step) for step in sequence.steps}))
    target_table = build_cross_fitted_advantage_table_v1(
        episode_returns, episode_buckets, fold_count=2, advantage_clip=1.0,
    )
    record_rows = [
        (
            sequence.episode_group,
            step.record_id,
            float(target_table[sequence.episode_group][_step_bucket(step)]["advantage"]),
        )
        for sequence in sequences for step in sequence.steps
    ]
    record_advantages = aggregate_record_advantages_v1(record_rows)
    record_weights = {
        key: awr_weight_from_advantage_v1(
            advantage, temperature=temperature, max_weight=max_weight, filtered=filtered,
        )
        for key, advantage in record_advantages.items()
    }
    transformed: list[RecurrentBCSequenceV4] = []
    rows = positive_rows = zero_rows = 0
    raw_weights: list[float] = []
    for sequence in sequences:
        new_steps: list[RecurrentBCStepV4] = []
        for step in sequence.steps:
            weighted = record_weights[(sequence.episode_group, step.record_id)]
            raw_weights.append(weighted.raw_weight)
            new_steps.append(replace(
                step,
                quality_weight=float(weighted.normalized_quality),
                supervision_weight=float(weighted.supervision_weight),
            ))
            rows += 1
            if weighted.supervision_weight > 0.0:
                positive_rows += 1
            else:
                zero_rows += 1
        if not any(step.supervision_weight > 0.0 for step in new_steps[burn_in:]):
            if filtered:
                # A filtered sequence with no positive post-burn-in action has
                # no gradient and would violate the V4 trainer contract.  It
                # is excluded explicitly and counted, never silently relabeled.
                continue
            raise AWRTargetError("unweighted sequence unexpectedly has no supervised rows")
        transformed.append(replace(sequence, steps=tuple(new_steps)))
    transformed_train = tuple(sequence for sequence in transformed if sequence.partition == "train")
    transformed_validation = tuple(sequence for sequence in transformed if sequence.partition == "validation")
    if not transformed_train or not transformed_validation:
        raise AWRTargetError("AWR transformation removed a complete partition")
    target_payload = {
        "schema_version": TARGET_SCHEMA_V1,
        "target_kind": TARGET_KIND_V1,
        "fold_count": 2,
        "temperature": float(temperature),
        "max_weight": float(max_weight),
        "filtered": bool(filtered),
        "snapshot": provenance,
        "episodes": [
            {
                "episode_id": episode,
                "return_value": episode_returns[episode],
                "fold_index": _fold(episode, 2),
                "buckets": target_table[episode],
            }
            for episode in sorted(target_table)
        ],
        "source_stats": source_stats,
        "rows": rows,
        "record_count": len(record_weights),
        "record_weighting": "mean_prefix_advantage_per_episode_record",
        "positive_rows": positive_rows,
        "zero_supervision_rows": zero_rows,
        "filtered_sequences_removed": len(sequences) - len(transformed),
        "training_permission": "qualified_teacher_local",
        "research_only": True,
        "promotion_authority": False,
        "longrun_allowed": False,
        "performance_evidence": False,
    }
    target_payload["target_manifest_sha256"] = hashlib.sha256(_canonical(target_payload)).hexdigest()
    return transformed_train, transformed_validation, target_payload


def run_pilot_v1(
    *,
    snapshot_root: Path,
    checkpoint_by_seed: Mapping[int, tuple[Path, str, str]],
    output_root: Path,
    temperature: float = 0.25,
    max_weight: float = 4.0,
    filtered: bool = False,
    epochs: int = 1,
    learning_rate: float = 1.0e-4,
    tbptt_steps: int = 8,
    burn_in: int = 1,
    device: str = "cpu",
    torch_threads: int = 2,
) -> dict[str, object]:
    if set(checkpoint_by_seed) != {0, 1}:
        raise AWRTargetError("exactly seed0 and seed1 checkpoints are required")
    if device.startswith("cuda") and not torch.cuda.is_available():
        raise AWRTargetError("requested CUDA device is unavailable")
    torch.set_num_threads(torch_threads)
    train, validation, target_payload = build_awr_sequences_v1(
        snapshot_root, burn_in=burn_in, temperature=temperature,
        max_weight=max_weight, filtered=filtered,
    )
    output_root.mkdir(parents=True, exist_ok=True)
    target_path = output_root / "awr-target-manifest.json"
    target_path.write_text(json.dumps(target_payload, ensure_ascii=True, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    target_sha = _file_sha(target_path)
    sequence_sha = selected_objective_sha256_v4(tuple(train) + tuple(validation))
    results: dict[str, object] = {}
    for seed in (0, 1):
        checkpoint, file_sha, tensor_sha = checkpoint_by_seed[seed]
        if _file_sha(checkpoint) != _sha(file_sha, field=f"seed{seed} checkpoint file"):
            raise AWRTargetError(f"seed{seed} checkpoint file SHA mismatch")
        model = SpecialistModelV4(card_vocabulary_size=1267, hidden_dim=128, embedding_dim=64, seed=seed).to(device)
        load_specialist_checkpoint_v4(
            checkpoint, model, expected_file_sha256=file_sha,
            expected_tensor_state_sha256=tensor_sha,
        )
        seed_output = output_root / f"seed-{seed}"
        run_config = {
            "schema_version": SCHEMA_V1,
            "seed": seed,
            "target_kind": TARGET_KIND_V1,
            "target_manifest_file_sha256": target_sha,
            "selected_sequence_sha256": sequence_sha,
            "initial_checkpoint_file_sha256": file_sha,
            "initial_checkpoint_tensor_state_sha256": tensor_sha,
            "temperature": temperature,
            "max_weight": max_weight,
            "filtered": filtered,
            "research_only": True,
            "promotion_authority": False,
            "longrun_allowed": False,
            "performance_evidence": False,
        }
        result = train_recurrent_bc_v4(
            model, train, validation,
            mode=RESEARCH_ONLY_OUTCOME_WEIGHTED_V4,
            output_dir=seed_output,
            sequence_order_seed=seed,
            epochs=epochs,
            patience=1,
            learning_rate=learning_rate,
            tbptt_steps=tbptt_steps,
            gradient_clip_norm=1.0,
            run_config=run_config,
        )
        results[str(seed)] = {
            "best_checkpoint_path": str(result.best_checkpoint_path),
            "best_checkpoint_file_sha256": result.best_checkpoint_file_sha256,
            "best_checkpoint_tensor_state_sha256": result.best_checkpoint_tensor_state_sha256,
            "initial_validation_complete_action_nll": result.initial_validation_complete_action_nll,
            "best_validation_complete_action_nll": result.best_validation_complete_action_nll,
            "validation_delta_nll": result.validation_delta_nll,
            "optimizer_updates_completed": result.optimizer_updates_completed,
        }
    report = {
        "schema_version": SCHEMA_V1,
        "target_manifest_file_sha256": target_sha,
        "target_kind": TARGET_KIND_V1,
        "trainer_implementation_sha256": trainer_implementation_sha256_v4(),
        "selected_sequence_sha256": sequence_sha,
        "snapshot_root": str(snapshot_root),
        "temperature": temperature,
        "max_weight": max_weight,
        "filtered": filtered,
        "epochs": epochs,
        "learning_rate": learning_rate,
        "device": device,
        "results": results,
        "research_only": True,
        "promotion_authority": False,
        "longrun_allowed": False,
        "performance_evidence": False,
    }
    report_path = output_root / "report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot-root", type=Path, required=True)
    parser.add_argument("--checkpoint-seed0", type=Path, required=True)
    parser.add_argument("--checkpoint-seed0-file-sha256", required=True)
    parser.add_argument("--checkpoint-seed0-tensor-sha256", required=True)
    parser.add_argument("--checkpoint-seed1", type=Path, required=True)
    parser.add_argument("--checkpoint-seed1-file-sha256", required=True)
    parser.add_argument("--checkpoint-seed1-tensor-sha256", required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--temperature", type=float, default=0.25)
    parser.add_argument("--max-weight", type=float, default=4.0)
    parser.add_argument("--filtered", action="store_true")
    parser.add_argument("--epochs", type=int, default=1)
    parser.add_argument("--learning-rate", type=float, default=1.0e-4)
    parser.add_argument("--tbptt-steps", type=int, default=8)
    parser.add_argument("--burn-in", type=int, default=1)
    parser.add_argument("--device", choices=("cpu", "cuda:0"), default="cpu")
    parser.add_argument("--torch-threads", type=int, default=2)
    parser.add_argument("--execute", action="store_true", help="allow research-only training side effects")
    args = parser.parse_args(argv)
    if not args.execute:
        print(json.dumps({
            "schema_version": SCHEMA_V1,
            "status": "NOT_STARTED",
            "execute_required": True,
            "research_only": True,
            "promotion_authority": False,
            "longrun_allowed": False,
        }, sort_keys=True))
        return 0
    report = run_pilot_v1(
        snapshot_root=args.snapshot_root,
        checkpoint_by_seed={
            0: (args.checkpoint_seed0, args.checkpoint_seed0_file_sha256, args.checkpoint_seed0_tensor_sha256),
            1: (args.checkpoint_seed1, args.checkpoint_seed1_file_sha256, args.checkpoint_seed1_tensor_sha256),
        },
        output_root=args.output_root,
        temperature=args.temperature,
        max_weight=args.max_weight,
        filtered=args.filtered,
        epochs=args.epochs,
        learning_rate=args.learning_rate,
        tbptt_steps=args.tbptt_steps,
        burn_in=args.burn_in,
        device=args.device,
        torch_threads=args.torch_threads,
    )
    print(json.dumps({"schema_version": report["schema_version"], "results": report["results"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
