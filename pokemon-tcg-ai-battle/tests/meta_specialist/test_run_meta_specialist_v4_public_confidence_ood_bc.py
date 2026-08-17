"""Contract tests for the research-only public confidence/OOD BC overlay.

These tests intentionally use typed fixtures.  They must not start a real
training/evaluation runner: the first version only materializes a recurrent
mask while preserving the sealed row topology.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from mage_ptcg.meta_specialist.actor_visible_features_v1 import SpecialistStepLogitsV1
from mage_ptcg.meta_specialist.public_confidence_ood_v1 import PublicBucketReferenceV1
from tests.meta_specialist.test_neural_policy_v4 import _model_input_and_steps

from scripts import run_meta_specialist_v4_public_confidence_ood_bc as runner


def _manifest(*, promotion_authority: bool = False, longrun_allowed: bool = False,
              status: str = "pre_registered_diagnostic_policy_not_yet_connected_to_training") -> dict[str, object]:
    return {
        "schema_version": "meta-specialist-public-confidence-ood-policy-v1",
        "promotion_authority": promotion_authority,
        "source": {
            "reference_artifact": "runs/meta-specialist-public-confidence-ood/reference-wave6-seed0-seed1-train-bundle-v1.json",
            "reference_artifact_sha256": "a" * 64,
            "reference_source_list_sha256": "b" * 64,
            "reference_source_sha256s": ["c" * 64, "d" * 64],
            "reference_source_count": 2,
            "partition": "train",
            "bucket_schema": "meta-specialist-public-confidence-ood-v1",
        },
        "bucket_policy": {"rare_count_threshold": 2, "focus_on_ood": True},
        "confidence_policy": {"min_normalized_surprisal": 0.5, "max_top1_top2_margin": None},
        "loss_mask_semantics": {
            "forced_domain": "context_only",
            "ineligible": "context_only",
            "eligible": "loss_bearing",
            "context_rows_in_loss_denominator": False,
            "context_rows_advance_recurrent_state": True,
        },
        "privacy": {
            "runtime_uses_opponent_id": False,
            "runtime_uses_seat": False,
            "runtime_uses_policy_identity": False,
            "runtime_uses_hidden_fields": False,
            "training_component_selection_may_stratify_by_opponent": True,
        },
        "gate": {
            "fixed_six_games_per_seed": 24,
            "required_faults": 0,
            "required_seedwise_non_degradation": True,
            "required_seatwise_non_degradation": True,
            "shadow_b_only_after_fixed_six_pass": True,
            "longrun_allowed": longrun_allowed,
        },
        "status": status,
    }


def _rows() -> tuple[runner.SealedPublicTransitionV1, ...]:
    model_input, first, second = _model_input_and_steps()
    first_target = first.allowed_semantic_classes[0].semantic_row
    second_target = second.allowed_semantic_classes[0].semantic_row
    first_logits = SpecialistStepLogitsV1(
        semantic_logits=tuple(0.0 for _ in first.allowed_semantic_classes),
        stop_logit=0.0 if first.stop_available else None,
    )
    second_logits = SpecialistStepLogitsV1(
        semantic_logits=tuple(
            10.0 if index == 0 else -10.0
            for index in range(len(second.allowed_semantic_classes))
        ),
        stop_logit=0.0 if second.stop_available else None,
    )
    forced = type(second)(
        schema_version=second.schema_version,
        order_semantics=second.order_semantics,
        semantic_prefix=second.semantic_prefix,
        allowed_semantic_classes=(),
        stop_available=True,
    )
    forced_logits = SpecialistStepLogitsV1(semantic_logits=(), stop_logit=0.0)
    hidden_a = {"opaque": object()}
    hidden_b = {"opaque": object()}
    return (
        runner.SealedPublicTransitionV1(
            record_id="r" * 64, group_id="g" * 64, row_index=0, episode_start=True,
            hidden_context=hidden_a, model_input=model_input, step_input=first,
            logits=first_logits, chosen_semantic_action=first_target,
        ),
        runner.SealedPublicTransitionV1(
            record_id="r" * 64, group_id="g" * 64, row_index=1, episode_start=False,
            hidden_context=hidden_b, model_input=model_input, step_input=second,
            logits=second_logits, chosen_semantic_action=second_target,
        ),
        runner.SealedPublicTransitionV1(
            record_id="s" * 64, group_id="g" * 64, row_index=0, episode_start=False,
            hidden_context=hidden_b, model_input=model_input, step_input=forced,
            logits=forced_logits, chosen_is_stop=True,
        ),
    )


def test_mask_preserves_rows_record_groups_episode_start_and_hidden_context() -> None:
    rows = _rows()
    scores = [
        runner._score_fixture_row_v1(row, reference=None)
        for row in rows
    ]
    reference = PublicBucketReferenceV1(
        source_sha256="b" * 64,
        bucket_counts={scores[0].bucket_id: 1, scores[1].bucket_id: 10},
        rare_count_threshold=2,
    )
    manifest = _manifest()

    result = runner.build_public_ood_mask_contract_v1(
        rows, reference=reference, policy_manifest=manifest,
    )

    assert len(result.rows) == len(rows) == 3
    assert tuple(item.source for item in result.rows) == rows
    assert result.record_row_counts == {"r" * 64: 2, "s" * 64: 1}
    assert result.group_row_counts == {"g" * 64: 3}
    assert tuple(item.source.episode_start for item in result.rows) == (True, False, False)
    assert result.rows[0].source.hidden_context is rows[0].hidden_context
    assert result.rows[1].source.hidden_context is rows[1].hidden_context
    assert result.rows[2].source.hidden_context is rows[2].hidden_context
    assert result.rows[0].supervision_weight == 1.0
    assert result.rows[1].supervision_weight == 0.0
    assert result.rows[2].supervision_weight == 0.0
    assert result.loss_bearing_row_count == 1
    assert result.context_only_row_count == 2


def test_mask_contract_rejects_group_reentry_and_row_gaps() -> None:
    rows = list(_rows())
    rows[1] = runner.SealedPublicTransitionV1(
        record_id=rows[1].record_id, group_id="o" * 64, row_index=0,
        episode_start=True, hidden_context=rows[1].hidden_context,
        model_input=rows[1].model_input, step_input=rows[1].step_input,
        logits=rows[1].logits, chosen_semantic_action=rows[1].chosen_semantic_action,
    )
    reference = PublicBucketReferenceV1(source_sha256="b" * 64, bucket_counts={})
    with pytest.raises(ValueError, match="episode_start|group|row_index"):
        runner.build_public_ood_mask_contract_v1(rows, reference=reference, policy_manifest=_manifest())

    rows = list(_rows())
    rows[1] = runner.SealedPublicTransitionV1(
        record_id=rows[1].record_id, group_id=rows[1].group_id, row_index=3,
        episode_start=False, hidden_context=rows[1].hidden_context,
        model_input=rows[1].model_input, step_input=rows[1].step_input,
        logits=rows[1].logits, chosen_semantic_action=rows[1].chosen_semantic_action,
    )
    with pytest.raises(ValueError, match="row_index"):
        runner.build_public_ood_mask_contract_v1(rows, reference=reference, policy_manifest=_manifest())


@pytest.mark.parametrize(
    "changes, message",
    [
        ({"promotion_authority": True}, "promotion_authority"),
        ({"gate": {"longrun_allowed": True}}, "longrun_allowed"),
        ({"status": "ready_for_training"}, "status"),
    ],
)
def test_policy_manifest_is_strictly_diagnostic_and_never_a_training_permit(
    changes: dict[str, object], message: str,
) -> None:
    manifest = _manifest()
    if "gate" in changes:
        manifest["gate"] = {**manifest["gate"], **changes["gate"]}  # type: ignore[index]
    else:
        manifest.update(changes)
    reference = PublicBucketReferenceV1(source_sha256="b" * 64, bucket_counts={})
    with pytest.raises(ValueError, match=message):
        runner.build_public_ood_mask_contract_v1(
            _rows(), reference=reference, policy_manifest=manifest,
        )

    with pytest.raises(ValueError, match="training"):
        runner.build_public_ood_mask_contract_v1(
            _rows(), reference=reference, policy_manifest=_manifest(),
            training_requested=True,
        )


def test_common_reference_bundle_is_reported_as_diagnostic_only() -> None:
    manifest = _manifest()
    reference = PublicBucketReferenceV1(source_sha256="b" * 64, bucket_counts={})
    result = runner.build_public_ood_mask_contract_v1(
        _rows(), reference=reference, policy_manifest=manifest,
    )
    assert result.training_permitted is False
    assert result.promotion_authority is False
    assert result.longrun_allowed is False
    assert "seed0-seed1" in result.reference_artifact


def test_model_training_entrypoint_is_explicitly_rejected() -> None:
    with pytest.raises(ValueError, match="not connected|training"):
        runner.run_public_confidence_ood_bc_v1(_rows(), policy_manifest=_manifest(), train=True)


def _reference_bundle_for_rows(rows: tuple[runner.SealedPublicTransitionV1, ...]) -> dict[str, object]:
    scores = [runner._score_fixture_row_v1(row, reference=None) for row in rows]
    source_list = [
        {"ordinal": 0, "source_sha256": "c" * 64},
        {"ordinal": 1, "source_sha256": "d" * 64},
    ]
    canonical = json.dumps(
        {"partition": "train", "source_list": source_list},
        ensure_ascii=True, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    source_list_sha = hashlib.sha256(canonical).hexdigest()
    return {
        "schema_version": runner.REFERENCE_BUNDLE_SCHEMA_V1,
        "bucket_schema_version": "meta-specialist-public-confidence-ood-v1",
        "partition": "train",
        "rare_count_threshold": 2,
        "source_count": 2,
        "source_list": source_list,
        "source_list_sha256": source_list_sha,
        "source_stats": [
            {"ordinal": 0, "transition_count": 1, "prefix_count": 1, "forced_prefix_count": 0, "skipped_transition_count": 0},
            {"ordinal": 1, "transition_count": 1, "prefix_count": 1, "forced_prefix_count": 0, "skipped_transition_count": 0},
        ],
        "transition_count": 2,
        "prefix_count": 2,
        "forced_prefix_count": 0,
        "skipped_transition_count": 0,
        "bucket_count": len({score.bucket_id for score in scores}),
        "bucket_counts": {score.bucket_id: 1 for score in scores},
        "privacy": {
            "uses_opponent_id": False,
            "uses_seat": False,
            "uses_policy_identity": False,
            "uses_hidden_fields": False,
        },
        "promotion_authority": False,
    }


def test_reference_loader_requires_two_source_bundle_and_binds_source_list_identity() -> None:
    bundle = _reference_bundle_for_rows(_rows())
    reference = runner.load_public_ood_reference_bundle_v1(
        bundle,
        expected_source_list_sha256=bundle["source_list_sha256"],  # type: ignore[arg-type]
        expected_source_sha256s=("c" * 64, "d" * 64),
    )
    assert reference.source_sha256 == bundle["source_list_sha256"]
    assert reference.rare_count_threshold == 2

    single = deepcopy(bundle)
    single["schema_version"] = "meta-specialist-public-bucket-reference-v1"
    single["source_count"] = 1
    single["source_list"] = [single["source_list"][0]]  # type: ignore[index]
    with pytest.raises(ValueError, match="single-source|schema"):
        runner.load_public_ood_reference_bundle_v1(single)

    unsafe = deepcopy(bundle)
    unsafe["promotion_authority"] = True
    with pytest.raises(ValueError, match="promotion_authority"):
        runner.load_public_ood_reference_bundle_v1(unsafe)


def test_current_manifest_and_bundle_are_jointly_hash_bound() -> None:
    root = Path(__file__).resolve().parents[2]
    manifest = runner.validate_public_ood_policy_manifest_v1(
        root / "configs/meta_specialist/public_confidence_ood_policy_v1.json",
    )
    reference = runner.load_public_ood_reference_bundle_v1(
        root / manifest.reference_artifact,
        expected_artifact_sha256=manifest.reference_artifact_sha256,
        expected_source_list_sha256=manifest.reference_source_list_sha256,
        expected_source_sha256s=manifest.reference_source_sha256s,
    )
    assert reference.source_sha256 == manifest.reference_source_list_sha256
    assert reference.rare_count_threshold == manifest.rare_count_threshold


def test_mapping_fixture_has_closed_public_schema_and_no_opponent_fields() -> None:
    row = _rows()[0]
    payload = runner.sealed_public_transition_to_mapping_v1(row)
    assert "opponent_id" not in payload
    assert "seat" not in payload
    assert payload["record_id"] == row.record_id
    assert runner.sealed_public_transition_from_mapping_v1(payload) == row
    with pytest.raises(ValueError, match="closed|unknown"):
        runner.sealed_public_transition_from_mapping_v1({**payload, "seat": 1})
