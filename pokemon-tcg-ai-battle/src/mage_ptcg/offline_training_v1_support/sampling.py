"""Priority sampling module for training data.

Selects training records using custom heuristic weights, handles zero-total
fallbacks, and formats execution manifests.
"""

from __future__ import annotations

import math
import random
from collections import Counter
from typing import Any, Sequence

from mage_ptcg.offline_training_v1_support.contracts import (
    SupportContractError,
    digest,
)


def calculate_record_weight(record: dict[str, Any], weight_config: dict[str, float]) -> float:
    """Calculate the cumulative sampling weight for a single record."""
    weight = 1.0

    # 1. Uniform baseline
    weight *= weight_config.get("uniform", 1.0)

    # 2. Disagreement
    teacher_act = record.get("teacher_action_key")
    student_act = record.get("student_action_key")
    if teacher_act and student_act and teacher_act != student_act:
        weight *= weight_config.get("disagreement", 1.0)

    # 3. Hard-state score
    priority_score = record.get("priority_score", 0.0)
    if priority_score > 0.0:
        weight *= (1.0 + priority_score * weight_config.get("hard_state_score", 1.0))

    # 4. Rare selection type
    sel_type = record.get("selection_type")
    if sel_type in ("rare_select", "special_select"):
        weight *= weight_config.get("rare_selection_type", 1.0)

    # 5. Rare context type
    ctx_type = record.get("context_type")
    if ctx_type in ("rare_context", "special_context"):
        weight *= weight_config.get("rare_context_type", 1.0)

    # 6. Teacher confidence
    conf = record.get("student_confidence")
    if conf is not None and isinstance(conf, (int, float)):
        # Low confidence student -> higher weight
        weight *= (1.0 + (1.0 - conf) * weight_config.get("teacher_confidence", 1.0))

    # 7. Held-out error
    if bool(record.get("is_error", False)):
        weight *= weight_config.get("held_out_error", 1.0)

    # 8. Runtime fallback
    if bool(record.get("fallback_used", False)):
        weight *= weight_config.get("runtime_fallback", 1.0)

    return weight


def priority_sample(
    records: Sequence[dict[str, Any]],
    weight_config: dict[str, float],
    sampled_count: int,
    replacement: bool = False,
    seed: int = 42,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Perform deterministic priority sampling for training input only."""
    # Safety Check: Guarantee this is only used for training inputs, never validation splits.
    # Exclude usage if validation or test split flags are passed in config
    if weight_config.get("is_validation_split") or weight_config.get("is_test_split"):
        raise SupportContractError(
            "Priority sampler is protected and restricted to training input only. "
            "Do NOT use for validation or test split generation."
        )

    if not records:
        raise SupportContractError("No records provided to sample.")

    if sampled_count <= 0:
        raise SupportContractError("Sampled count must be positive.")

    if not replacement and sampled_count > len(records):
        raise SupportContractError(
            f"Cannot sample {sampled_count} elements without replacement from a pool of size {len(records)}."
        )

    # Validate weight_config values
    for k, v in weight_config.items():
        if k in ("is_validation_split", "is_test_split"):
            continue
        if not isinstance(v, (int, float)) or not math.isfinite(v):
            raise SupportContractError(f"Non-finite weight configuration value for {k}: {v}")
        if v < 0:
            raise SupportContractError(f"Negative weight configuration value for {k}: {v}")

    # Calculate and validate weights
    raw_weights = []
    for r in records:
        w = calculate_record_weight(r, weight_config)
        if not isinstance(w, (int, float)) or not math.isfinite(w):
            raise SupportContractError("Non-finite weight detected during priority sampling.")
        if w < 0:
            raise SupportContractError("Negative weight detected during priority sampling.")
        raw_weights.append(w)

    sum_weights = sum(raw_weights)

    # Zero-total fallback
    if sum_weights <= 0.0:
        raw_weights = [1.0] * len(records)
        sum_weights = float(len(records))

    rng = random.Random(seed)
    sampled_indices = []

    if replacement:
        # Weighted random choices with replacement
        sampled_indices = rng.choices(range(len(records)), weights=raw_weights, k=sampled_count)
    else:
        # Weighted random sampling without replacement using Efraimidis & Spirakis key method
        keys = []
        for idx, w in enumerate(raw_weights):
            u = rng.random()
            # Handle elements with 0 weight (lowest priority)
            key = u ** (1.0 / w) if w > 0.0 else -1.0
            keys.append((key, idx))
        keys.sort(reverse=True, key=lambda x: x[0])
        sampled_indices = [idx for _, idx in keys[:sampled_count]]

    sampled_records = [records[idx].copy() for idx in sampled_indices]

    # Gather distributions for manifest
    reasons = Counter()
    selection_types = Counter()
    context_types = Counter()

    for idx in sampled_indices:
        r = records[idx]
        sel_type = r.get("selection_type", "unknown")
        ctx_type = r.get("context_type", "unknown")
        selection_types[str(sel_type)] += 1
        context_types[str(ctx_type)] += 1

        # Approximate reasons
        teacher_act = r.get("teacher_action_key")
        student_act = r.get("student_action_key")
        if teacher_act and student_act and teacher_act != student_act:
            reasons["disagreement"] += 1
        if bool(r.get("is_error", False)):
            reasons["error"] += 1
        if bool(r.get("fallback_used", False)):
            reasons["fallback"] += 1

    source_hash = digest(records, domain="sampler-source")
    sample_hash = digest(sampled_records, domain="sampler-output")

    manifest = {
        "schema_version": "support-sampling-manifest-v1",
        "input_count": len(records),
        "eligible_count": len(records),
        "sampled_count": len(sampled_records),
        "reason_distribution": dict(reasons),
        "selection_type_distribution": dict(selection_types),
        "context_type_distribution": dict(context_types),
        "source_hash": source_hash,
        "sample_hash": sample_hash,
        "seed": seed,
        "weights": weight_config,
    }

    return sampled_records, manifest
