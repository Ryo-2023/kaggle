"""TDD contracts for the frozen residual learning preflight boundary."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mage_ptcg.meta_specialist.frozen_residual_preflight_v1 import (
    FrozenResidualPreflightError,
    FrozenResidualPreflightManifestV1,
    ResidualMaskRowV1,
    Wave6ProvenanceV1,
    aggregate_residual_mask_v1,
    build_frozen_residual_preflight_manifest_v1,
    build_seed_known_manifest_v1,
    load_frozen_residual_preflight_manifest_v1,
)


def _sha(char: str) -> str:
    return char * 64


def _provenance(seed: int) -> Wave6ProvenanceV1:
    return Wave6ProvenanceV1(
        seed=seed,
        checkpoint_path=f"/sealed/wave6-{seed}.pt",
        checkpoint_file_sha256=_sha(str(seed + 1)),
        checkpoint_tensor_state_sha256=_sha(str(seed + 2)),
        screen_path=f"/sealed/screen-{seed}.json",
        screen_file_sha256=_sha(str(seed + 3)),
        transitions_path=f"/sealed/transitions-{seed}.jsonl",
        transitions_file_sha256=_sha(str(seed + 4)),
        subject_deck_sha256=_sha("a"),
        partition="train",
    )


def _manifest() -> FrozenResidualPreflightManifestV1:
    seeds = tuple(
        build_seed_known_manifest_v1(
            _provenance(seed),
            context_ids=(_sha(str(seed + 5)), _sha(str(seed + 6))),
            action_keys=(_sha(str(seed + 7)),),
            transition_count=2,
            prefix_count=3,
        )
        for seed in (0, 1)
    )
    return build_frozen_residual_preflight_manifest_v1(
        seeds,
        subject_deck_sha256=_sha("a"),
    )


def test_manifest_binds_exact_two_wave6_seeds_and_round_trips() -> None:
    manifest = _manifest()
    assert manifest.status == "PREFLIGHT_ONLY_NOT_EXECUTED"
    assert tuple(item.provenance.seed for item in manifest.seeds) == (0, 1)
    payload = manifest.to_dict()
    loaded = load_frozen_residual_preflight_manifest_v1(payload)
    assert loaded.to_dict() == payload
    assert loaded.training_permitted is False
    assert loaded.longrun_allowed is False


def test_manifest_rejects_cross_seed_duplicate_or_open_schema() -> None:
    manifest = _manifest().to_dict()
    duplicate = json.loads(json.dumps(manifest))
    duplicate["seeds"][1]["provenance"]["transitions_file_sha256"] = duplicate["seeds"][0]["provenance"]["transitions_file_sha256"]
    with pytest.raises(FrozenResidualPreflightError, match="distinct|duplicate|source"):
        load_frozen_residual_preflight_manifest_v1(duplicate)

    open_schema = json.loads(json.dumps(manifest))
    open_schema["unexpected"] = True
    with pytest.raises(FrozenResidualPreflightError, match="schema|unknown|closed"):
        load_frozen_residual_preflight_manifest_v1(open_schema)


def test_mask_summary_excludes_context_only_rows_from_effective_denominator() -> None:
    rows = (
        ResidualMaskRowV1(context_id=_sha("1"), eligible=False, supervision_weight=0.0),
        ResidualMaskRowV1(context_id=_sha("2"), eligible=True, supervision_weight=0.5),
        ResidualMaskRowV1(context_id=_sha("3"), eligible=True, supervision_weight=1.0),
    )
    summary = aggregate_residual_mask_v1(rows, loss_terms=(100.0, 2.0, 3.0))
    assert summary.total_rows == 3
    assert summary.context_only_rows == 1
    assert summary.loss_bearing_rows == 2
    assert summary.effective_loss_mass == pytest.approx(1.5)
    assert summary.weighted_loss_sum == pytest.approx(4.0)


def test_mask_rejects_loss_on_context_only_or_non_recurrent_context() -> None:
    with pytest.raises(FrozenResidualPreflightError, match="context-only|weight"):
        aggregate_residual_mask_v1((
            ResidualMaskRowV1(context_id=_sha("1"), eligible=False, supervision_weight=1.0),
        ))
    with pytest.raises(FrozenResidualPreflightError, match="recurrent"):
        aggregate_residual_mask_v1((
            ResidualMaskRowV1(context_id=_sha("1"), eligible=True, supervision_weight=1.0, recurrent_context=False),
        ))


def test_manifest_rejects_training_authority_and_seed_mismatch() -> None:
    manifest = _manifest().to_dict()
    manifest["training_permitted"] = True
    with pytest.raises(FrozenResidualPreflightError, match="training|authority"):
        load_frozen_residual_preflight_manifest_v1(manifest)

    seeds = list(_manifest().seeds)
    wrong = build_seed_known_manifest_v1(
        _provenance(1), context_ids=(_sha("1"),), action_keys=(_sha("2"),),
        transition_count=1, prefix_count=1,
    )
    with pytest.raises(FrozenResidualPreflightError, match="seed|exactly"):
        build_frozen_residual_preflight_manifest_v1((wrong, seeds[1]), subject_deck_sha256=_sha("a"))
