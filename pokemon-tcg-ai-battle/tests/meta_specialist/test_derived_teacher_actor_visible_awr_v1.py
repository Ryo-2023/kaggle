"""Contracts for the derived-teacher actor-visible AWR sidecar."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import math
from pathlib import Path

import pytest


def _sha(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _snapshot_example(*, metadata_label: str = "a") -> dict[str, object]:
    scalars = [0] * 41
    scalars[4:9] = [1, 22, 1, 1, 2]
    scalars[11:15] = [0, 0, 0, 0]
    scalars[23:39] = [0] * 16
    scalars[15] = 2
    bags = {
        name: {"tokens": [0, 0], "mask": [0, 0]}
        for name in (
            "own_hand", "deck_reveal", "looking_visible", "self_discard",
            "opponent_discard",
        )
    }
    bags["own_hand"] = {"tokens": [7, 9], "mask": [1, 1]}
    semantic = {
        "selection_type": 1,
        "selection_context": 22,
        "option_type": 3,
        "operation": "CARD",
        "source": {},
        "target": {},
        "host": {},
        "number": None,
        "attack_id": None,
        "special_condition": None,
        "energy_count": None,
        "skill_card_id": None,
    }
    return {
        "record_id": _sha(f"record-{metadata_label}"),
        "record_content_hash": _sha(f"content-{metadata_label}"),
        "episode_id_hash": _sha(f"episode-{metadata_label}"),
        "split": "train",
        "teacher_id": f"teacher-{metadata_label}",
        "opponent_id": f"opponent-{metadata_label}",
        "seat": metadata_label,
        "policy_sha256": _sha(f"policy-{metadata_label}"),
        "deck_sha256": _sha(f"deck-{metadata_label}"),
        "value_target": 1.0,
        "example_quality_weight": 1.0,
        "model_input": {
            "state_scalars": scalars,
            "card_bags": bags,
            "pokemon_entities": [],
        },
        "loss_rows": [{
            "semantic_prefix": [],
            "token_masses": [
                {"kind": "semantic", "semantic_action": semantic, "mass": 1.0},
                {"kind": "stop", "mass": 0.0},
            ],
        }],
    }


def _sample(
    label: str,
    *,
    fold: int,
    fold_count: int,
    target: float,
    split: str = "train",
    feature_offset: float = 0.0,
):
    from mage_ptcg.meta_specialist.derived_teacher_actor_visible_awr_v1 import (
        ActorVisibleAwrSampleV1,
        episode_fold_v1,
    )

    nonce = 0
    while True:
        episode_id = _sha(f"{label}-episode-{nonce}")
        if episode_fold_v1(episode_id, fold_count=fold_count) == fold:
            break
        nonce += 1
    features = [0.0] * 56
    features[0] = feature_offset
    features[-1] = 1.0
    return ActorVisibleAwrSampleV1(
        record_id=_sha(f"{label}-record"),
        record_content_hash=_sha(f"{label}-content"),
        episode_id=episode_id,
        split=split,
        teacher_id="teacher-a",
        action_type="selection_type=1/selection_context=22",
        features=tuple(features),
        value_target=target,
        example_quality_weight=1.0,
    )


def test_actor_visible_features_ignore_provenance_metadata() -> None:
    from mage_ptcg.meta_specialist.derived_teacher_actor_visible_awr_v1 import (
        ACTOR_VISIBLE_VALUE_FEATURE_SCHEMA_V1,
        actor_visible_value_features_v1,
    )

    left = _snapshot_example(metadata_label="left")
    right = _snapshot_example(metadata_label="right")
    assert actor_visible_value_features_v1(left) == actor_visible_value_features_v1(right)
    assert len(actor_visible_value_features_v1(left)) == 56
    assert "actor-visible" in ACTOR_VISIBLE_VALUE_FEATURE_SCHEMA_V1
    assert "public-only" not in ACTOR_VISIBLE_VALUE_FEATURE_SCHEMA_V1


def test_snapshot_example_becomes_metadata_separated_sample() -> None:
    from mage_ptcg.meta_specialist.derived_teacher_actor_visible_awr_v1 import (
        sample_from_training_snapshot_example_v1,
    )

    example = _snapshot_example(metadata_label="source")
    sample = sample_from_training_snapshot_example_v1(
        example,
        teacher_id="teacher-source",
    )
    assert sample.record_id == example["record_id"]
    assert sample.episode_id == example["episode_id_hash"]
    assert sample.split == "train"
    assert sample.teacher_id == "teacher-source"
    assert sample.action_type == "selection_type=1/selection_context=22"
    assert len(sample.features) == 56


def test_cross_fit_excludes_every_episode_in_the_scored_fold() -> None:
    from mage_ptcg.meta_specialist.derived_teacher_actor_visible_awr_v1 import (
        build_cross_fitted_actor_visible_awr_v1,
    )

    original = tuple(
        _sample(
            f"fold-{fold}-episode-{position}",
            fold=fold,
            fold_count=2,
            target=1.0 if (fold + position) % 2 == 0 else -1.0,
            feature_offset=float(position + fold),
        )
        for fold in range(2)
        for position in range(3)
    )
    changed = tuple(
        replace(sample, value_target=-sample.value_target)
        if sample.fold_index(2) == 0 else sample
        for sample in original
    )
    first = build_cross_fitted_actor_visible_awr_v1(original, fold_count=2)
    second = build_cross_fitted_actor_visible_awr_v1(changed, fold_count=2)
    first_rows = {row.record_id: row for row in first.rows}
    second_rows = {row.record_id: row for row in second.rows}
    for sample in original:
        if sample.fold_index(2) == 0:
            assert first_rows[sample.record_id].baseline_value == pytest.approx(
                second_rows[sample.record_id].baseline_value, abs=1e-12
            )
    fold_zero_first = next(row for row in first.fold_models if row["fold_index"] == 0)
    fold_zero_second = next(row for row in second.fold_models if row["fold_index"] == 0)
    assert fold_zero_first["coefficients_sha256"] == fold_zero_second["coefficients_sha256"]
    assert all(row["fit_score_episode_intersection_count"] == 0 for row in first.fold_models)


def test_development_and_explicit_holdouts_never_fit_value_models() -> None:
    from mage_ptcg.meta_specialist.derived_teacher_actor_visible_awr_v1 import (
        build_cross_fitted_actor_visible_awr_v1,
    )

    train = tuple(
        _sample(
            f"train-{fold}-{position}", fold=fold, fold_count=2,
            target=1.0 if position % 2 == 0 else -1.0,
            feature_offset=float(position),
        )
        for fold in range(2)
        for position in range(2)
    )
    heldout = (
        _sample("development", fold=0, fold_count=2, target=1.0, split="development"),
        _sample("opponent", fold=1, fold_count=2, target=-1.0, split="opponent_holdout"),
        _sample("deck", fold=0, fold_count=2, target=1.0, split="deck_holdout"),
    )
    first = build_cross_fitted_actor_visible_awr_v1((*train, *heldout), fold_count=2)
    changed = tuple(
        replace(sample, value_target=-sample.value_target)
        if sample.split != "train" else sample
        for sample in (*train, *heldout)
    )
    second = build_cross_fitted_actor_visible_awr_v1(changed, fold_count=2)
    assert [row["coefficients_sha256"] for row in first.fold_models] == [
        row["coefficients_sha256"] for row in second.fold_models
    ]
    assert first.full_train_model["coefficients_sha256"] == second.full_train_model[
        "coefficients_sha256"
    ]
    assert first.fit_splits == ("train",)
    assert all(
        row.fit_membership is (row.split == "train")
        for row in first.rows
    )


def test_zero_variance_targets_produce_finite_bounded_mean_one_weights() -> None:
    from mage_ptcg.meta_specialist.derived_teacher_actor_visible_awr_v1 import (
        build_cross_fitted_actor_visible_awr_v1,
    )

    samples = tuple(
        _sample(
            f"constant-{fold}-{position}", fold=fold, fold_count=2,
            target=1.0, feature_offset=0.0,
        )
        for fold in range(2)
        for position in range(3)
    )
    result = build_cross_fitted_actor_visible_awr_v1(
        samples, fold_count=2, beta=1.0, max_weight=20.0,
    )
    weights = [row.awr_weight for row in result.rows if row.split == "train"]
    assert all(math.isfinite(value) and 0.0 < value <= 20.0 for value in weights)
    assert math.fsum(weights) / len(weights) == pytest.approx(1.0, abs=1e-12)
    assert result.weighting["beta"] == 1.0
    assert result.weighting["normalized_training_mean"] == pytest.approx(1.0, abs=1e-12)


def test_sidecar_reader_rejects_byte_tampering(tmp_path: Path) -> None:
    from mage_ptcg.meta_specialist.derived_teacher_actor_visible_awr_v1 import (
        ActorVisibleAwrError,
        build_cross_fitted_actor_visible_awr_v1,
        read_actor_visible_awr_sidecar_v1,
        write_actor_visible_awr_sidecar_v1,
    )

    samples = tuple(
        _sample(
            f"tamper-{fold}-{position}", fold=fold, fold_count=2,
            target=1.0 if position == 0 else -1.0,
        )
        for fold in range(2)
        for position in range(2)
    )
    result = build_cross_fitted_actor_visible_awr_v1(samples, fold_count=2)
    path = tmp_path / "sidecar.jsonl"
    binding = write_actor_visible_awr_sidecar_v1(result.rows, path)
    assert len(read_actor_visible_awr_sidecar_v1(path, expected_sha256=binding["sha256"])) == 4

    lines = path.read_text(encoding="utf-8").splitlines()
    payload = json.loads(lines[0])
    payload["awr_weight"] = payload["awr_weight"] + 0.25
    lines[0] = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(ActorVisibleAwrError, match="SHA-256|tamper"):
        read_actor_visible_awr_sidecar_v1(path, expected_sha256=binding["sha256"])


def test_manifest_contract_denies_authority_and_records_effective_mass() -> None:
    from mage_ptcg.meta_specialist.derived_teacher_actor_visible_awr_v1 import (
        build_cross_fitted_actor_visible_awr_v1,
        build_derived_teacher_awr_manifest_payload_v1,
    )

    samples = tuple(
        _sample(
            f"manifest-{fold}-{position}", fold=fold, fold_count=2,
            target=1.0 if position == 0 else -1.0,
        )
        for fold in range(2)
        for position in range(2)
    )
    result = build_cross_fitted_actor_visible_awr_v1(samples, fold_count=2)
    payload = build_derived_teacher_awr_manifest_payload_v1(
        result=result,
        catalog_binding={
            "path": "catalog.json", "file_sha256": _sha("catalog-file"),
            "catalog_sha256": _sha("catalog-semantic"),
        },
        decision_binding={"path": "decision.md", "sha256": _sha("decision")},
        source_bindings=[{
            "teacher_id": "teacher-a", "archetype": "test", "policy_sha256": _sha("policy"),
            "deck_sha256": _sha("deck"), "dataset_manifest_path": "dataset.json",
            "dataset_manifest_sha256": _sha("dataset"), "snapshot_index_path": "index.json",
            "snapshot_index_sha256": _sha("index"), "dataset_snapshot_sha256": _sha("snapshot"),
            "feature_domain": "actor-visible", "feature_schema_hash": _sha("feature"),
            "record_count": 4, "shards": [],
        }],
        sidecar_binding={
            "path": "sidecar.jsonl", "sha256": _sha("sidecar"), "row_count": 4,
            "format": "canonical-jsonl-v1",
        },
    )
    assert payload["authority"] == {
        "training_authority": False,
        "promotion_authority": False,
        "submission_authority": False,
        "longrun_authority": False,
    }
    assert payload["behavior_probability_required"] is False
    assert payload["behavior_probability_used"] is False
    excluded = payload["feature_contract"]["metadata_excluded_from_value_features"]
    assert {"opponent_id", "seat", "teacher_id", "policy_sha256", "deck_sha256", "episode_id"}.issubset(excluded)
    assert payload["diagnostics"]["teacher_action_type"]
    assert payload["manifest_sha256"] != ""
