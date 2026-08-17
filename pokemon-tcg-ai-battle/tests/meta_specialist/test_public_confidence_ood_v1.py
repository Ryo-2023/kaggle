"""Public-only confidence/OOD contracts for the research preflight."""

from __future__ import annotations

import inspect
import math
from dataclasses import replace

import pytest

from mage_ptcg.meta_specialist.actor_visible_features_v1 import SpecialistStepLogitsV1
from tests.meta_specialist.test_neural_policy_v4 import _model_input_and_steps


def _reference(bucket_id: str, *, count: int = 0):
    from mage_ptcg.meta_specialist.public_confidence_ood_v1 import PublicBucketReferenceV1

    return PublicBucketReferenceV1(
        source_sha256="a" * 64,
        bucket_counts={bucket_id: count},
        rare_count_threshold=2,
    )


def test_forced_stop_has_effective_domain_one_and_is_context_only() -> None:
    from mage_ptcg.meta_specialist.public_confidence_ood_v1 import score_public_step_v1

    model_input, _first, forced = _model_input_and_steps()
    forced = replace(forced, allowed_semantic_classes=())
    logits = SpecialistStepLogitsV1(semantic_logits=(), stop_logit=0.0)
    score = score_public_step_v1(model_input, forced, logits, chosen_is_stop=True)

    assert score.effective_domain == 1
    assert score.forced is True
    assert score.normalized_surprisal == 0.0
    assert score.eligible is False
    assert score.reason == "forced_domain"


def test_public_score_is_finite_and_metadata_free() -> None:
    from mage_ptcg.meta_specialist.public_confidence_ood_v1 import score_public_step_v1

    model_input, first, _forced = _model_input_and_steps()
    target = first.allowed_semantic_classes[0].semantic_row
    logits = SpecialistStepLogitsV1(
        semantic_logits=tuple(float(index) for index in range(len(first.allowed_semantic_classes))),
        stop_logit=0.0,
    )
    score_a = score_public_step_v1(model_input, first, logits, chosen_semantic_action=target)
    score_b = score_public_step_v1(model_input, first, logits, chosen_semantic_action=target)

    assert score_a == score_b
    assert score_a.effective_domain == len(first.allowed_semantic_classes) + 1
    assert score_a.top1_top2_margin is not None
    assert math.isfinite(score_a.top1_top2_margin)
    assert math.isfinite(score_a.entropy)
    assert score_a.target_nll is not None and math.isfinite(score_a.target_nll)
    assert score_a.bucket_id
    assert "opponent_id" not in inspect.signature(score_public_step_v1).parameters
    assert "seat" not in inspect.signature(score_public_step_v1).parameters


def test_missing_reference_fails_closed_without_discarding_metrics() -> None:
    from mage_ptcg.meta_specialist.public_confidence_ood_v1 import score_public_step_v1

    model_input, first, _forced = _model_input_and_steps()
    target = first.allowed_semantic_classes[0].semantic_row
    logits = SpecialistStepLogitsV1(
        semantic_logits=tuple(0.0 for _ in first.allowed_semantic_classes),
        stop_logit=0.0,
    )
    score = score_public_step_v1(model_input, first, logits, chosen_semantic_action=target)

    assert score.effective_domain >= 2
    assert score.bucket_id
    assert score.ood_unseen is None
    assert score.eligible is False
    assert score.reason == "missing_reference"


def test_reference_bucket_can_mark_rare_focus_without_private_metadata() -> None:
    from mage_ptcg.meta_specialist.public_confidence_ood_v1 import score_public_step_v1

    model_input, first, _forced = _model_input_and_steps()
    target = first.allowed_semantic_classes[0].semantic_row
    logits = SpecialistStepLogitsV1(
        semantic_logits=tuple(0.0 for _ in first.allowed_semantic_classes),
        stop_logit=0.0,
    )
    probe = score_public_step_v1(model_input, first, logits, chosen_semantic_action=target)
    score = score_public_step_v1(
        model_input,
        first,
        logits,
        chosen_semantic_action=target,
        reference=_reference(probe.bucket_id, count=1),
    )

    assert score.ood_unseen is False
    assert score.ood_rare is True
    assert score.eligible is True
    assert score.reason == "rare_public_bucket"


def test_logits_domain_mismatch_is_rejected() -> None:
    from mage_ptcg.meta_specialist.public_confidence_ood_v1 import score_public_step_v1

    model_input, first, _forced = _model_input_and_steps()
    with pytest.raises(ValueError, match="logit domain"):
        score_public_step_v1(
            model_input,
            first,
            SpecialistStepLogitsV1(semantic_logits=(), stop_logit=None),
        )


def test_reference_sha_is_required_before_ood_can_be_enabled() -> None:
    from mage_ptcg.meta_specialist.public_confidence_ood_v1 import PublicBucketReferenceV1

    with pytest.raises(ValueError, match="source_sha256"):
        PublicBucketReferenceV1(source_sha256="missing", bucket_counts={})


def test_public_score_mask_is_context_only_outside_eligible_rows() -> None:
    from mage_ptcg.meta_specialist.public_confidence_ood_v1 import (
        PublicStepScoreV1,
        supervision_weight_from_public_score_v1,
    )

    base = dict(
        schema_version="meta-specialist-public-confidence-ood-v1",
        effective_domain=2,
        forced=False,
        top1_top2_margin=1.0,
        entropy=0.5,
        target_nll=0.2,
        normalized_surprisal=0.1,
        bucket_id="a" * 64,
        reference_sha256="b" * 64,
        reference_count=3,
        ood_unseen=False,
        ood_rare=False,
        reason="below_focus_threshold",
    )
    assert supervision_weight_from_public_score_v1(PublicStepScoreV1(eligible=False, **base)) == 0.0
    assert supervision_weight_from_public_score_v1(PublicStepScoreV1(eligible=True, **base)) == 1.0
    forced_base = {**base, "forced": True}
    assert supervision_weight_from_public_score_v1(PublicStepScoreV1(eligible=True, **forced_base)) == 0.0
