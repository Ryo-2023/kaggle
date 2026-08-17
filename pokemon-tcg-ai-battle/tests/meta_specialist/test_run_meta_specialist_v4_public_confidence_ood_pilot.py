"""TDD contracts for the non-executing two-seed public OOD pilot harness."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from mage_ptcg.meta_specialist.public_confidence_ood_v1 import PublicBucketReferenceV1
from scripts import run_meta_specialist_v4_public_confidence_ood_bc as contract
from scripts import run_meta_specialist_v4_public_confidence_ood_pilot as pilot
from tests.meta_specialist.test_run_meta_specialist_v4_public_confidence_ood_bc import (
    _manifest,
    _reference_bundle_for_rows,
    _rows,
)


def _binding(seed: int, *, screen: str, transitions: str, checkpoint: str, tensor: str) -> pilot.Wave6SeedBindingV1:
    return pilot.Wave6SeedBindingV1(
        seed=seed,
        screen_path=f"/sealed/screen-{seed}.json",
        screen_file_sha256=screen * 64,
        transitions_path=f"/sealed/transitions-{seed}.jsonl",
        transitions_file_sha256=transitions * 64,
        init_checkpoint_path=f"/sealed/wave6-{seed}.pt",
        init_checkpoint_file_sha256=checkpoint * 64,
        init_checkpoint_tensor_state_sha256=tensor * 64,
    )


def _pilot_inputs(tmp_path: Path):
    rows = _rows()
    bundle = _reference_bundle_for_rows(rows)
    manifest = _manifest()
    manifest["source"] = {
        **manifest["source"],
        "reference_source_list_sha256": bundle["source_list_sha256"],
    }
    bindings = (
        _binding(0, screen="a", transitions="c", checkpoint="e", tensor="1"),
        _binding(1, screen="b", transitions="d", checkpoint="f", tensor="2"),
    )
    return rows, bundle, bindings, manifest


def test_plan_binds_common_bundle_masks_both_seeds_and_keeps_fixed_checkpoint_roles(tmp_path: Path) -> None:
    rows, bundle, bindings, manifest = _pilot_inputs(tmp_path)
    plan = pilot.build_public_ood_pilot_plan_v1(
        seed_bindings=bindings,
        common_reference=bundle,
        policy_manifest=manifest,
        records_by_seed={0: rows, 1: rows},
        base_sequence_sha256="0" * 64,
        control_sequence_sha256="1" * 64,
        output_root=tmp_path / "pilot",
    )

    assert plan.schema_version == pilot.PILOT_PLAN_SCHEMA_V1
    assert plan.status == "PLAN_ONLY_NOT_EXECUTED"
    assert plan.promotion_authority is False
    assert plan.longrun_allowed is False
    assert plan.training_permitted is False
    assert plan.common_reference_source_list_sha256 == bundle["source_list_sha256"]
    assert tuple(seed.seed for seed in plan.seeds) == (0, 1)
    assert plan.base_sequence_sha256 == "0" * 64
    assert plan.control_sequence_sha256 == "1" * 64
    for seed_plan in plan.seeds:
        assert seed_plan.wave6.screen_file_sha256 in {"a" * 64, "b" * 64}
        assert seed_plan.mask.transition_row_count == 3
        assert seed_plan.mask.eligible_row_count + seed_plan.mask.context_only_row_count == 3
        assert seed_plan.mask.effective_loss_mass == pytest.approx(seed_plan.mask.eligible_row_count)
        assert seed_plan.candidate_sequence_sha256 != seed_plan.control_sequence_sha256
        assert seed_plan.trainer.execution == "NOT_STARTED"
        assert seed_plan.trainer.best_checkpoint_selection is False
        assert seed_plan.trainer.trainer_entrypoint == pilot.V4_TRAINER_ENTRYPOINT_V1
        assert seed_plan.trainer.connected is False
        assert seed_plan.trainer.final_checkpoint_path.endswith("final-recurrent-bc-v4.pt")
        assert seed_plan.trainer.last_checkpoint_path.endswith("last-recurrent-bc-v4.pt")
        assert "best_epoch" not in seed_plan.trainer.to_dict()


def test_plan_rejects_transition_source_order_or_identity_mismatch(tmp_path: Path) -> None:
    rows, bundle, bindings, manifest = _pilot_inputs(tmp_path)
    wrong = list(bindings)
    wrong[1] = _binding(1, screen="b", transitions="c", checkpoint="f", tensor="2")
    with pytest.raises(ValueError, match="source|transition|seed"):
        pilot.build_public_ood_pilot_plan_v1(
            seed_bindings=tuple(wrong), common_reference=bundle, policy_manifest=manifest,
            records_by_seed={0: rows, 1: rows}, base_sequence_sha256="0" * 64,
            control_sequence_sha256="1" * 64, output_root=tmp_path / "pilot",
        )


def test_plan_rejects_non_two_seed_or_cross_seed_duplicate_provenance(tmp_path: Path) -> None:
    rows, bundle, bindings, manifest = _pilot_inputs(tmp_path)
    with pytest.raises(ValueError, match="exactly two|seed"):
        pilot.build_public_ood_pilot_plan_v1(
            seed_bindings=bindings[:1], common_reference=bundle, policy_manifest=manifest,
            records_by_seed={0: rows}, base_sequence_sha256="0" * 64,
            control_sequence_sha256="1" * 64, output_root=tmp_path / "pilot",
        )

    duplicate = (bindings[0], _binding(1, screen="a", transitions="d", checkpoint="f", tensor="2"))
    with pytest.raises(ValueError, match="distinct|duplicate"):
        pilot.build_public_ood_pilot_plan_v1(
            seed_bindings=duplicate, common_reference=bundle, policy_manifest=manifest,
            records_by_seed={0: rows, 1: rows}, base_sequence_sha256="0" * 64,
            control_sequence_sha256="1" * 64, output_root=tmp_path / "pilot",
        )


def test_execution_entrypoint_is_explicitly_refused(tmp_path: Path) -> None:
    rows, bundle, bindings, manifest = _pilot_inputs(tmp_path)
    with pytest.raises(ValueError, match="not executed|training|eval"):
        pilot.run_public_ood_pilot_v1(
            seed_bindings=bindings, common_reference=bundle, policy_manifest=manifest,
            records_by_seed={0: rows, 1: rows}, base_sequence_sha256="0" * 64,
            control_sequence_sha256="1" * 64, output_root=tmp_path / "pilot", execute=True,
        )
