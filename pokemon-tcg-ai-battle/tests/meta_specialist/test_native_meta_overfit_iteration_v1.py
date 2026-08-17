from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path

import pytest

from mage_ptcg.meta_specialist import native_meta_overfit_iteration_v1 as iteration
from mage_ptcg.meta_specialist.native_meta_overfit_iteration_v1 import (
    NativeMetaOverfitIterationError,
    build_native_meta_overfit_iteration_v1,
    verify_native_meta_overfit_iteration_v1,
)
from mage_ptcg.meta_specialist.native_public_advantage_v1 import (
    PublicAdvantageTableV1,
    build_public_advantage_table_v1,
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_canonical(path: Path, value: object) -> None:
    path.write_bytes(json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def _make_public_table(root: Path) -> Path:
    source = root / "meta-source.json"
    _write_canonical(source, {})
    source_sha = _sha(source)
    rows = []
    for index, (opponent_id, split) in enumerate(
        (("a1", "META_TRAIN"), ("a2", "META_TRAIN"), ("b1", "META_TRAIN"), ("dev", "META_DEV"), ("final", "META_FINAL"))
    ):
        rows.append(
            {
                "opponent_id": opponent_id,
                "pair_id": f"pair-{opponent_id}",
                "deck_sha256": hashlib.sha256(f"deck:{opponent_id}".encode()).hexdigest(),
                "policy_sha256": hashlib.sha256(f"policy:{opponent_id}".encode()).hexdigest(),
                "archetype": "A" if opponent_id.startswith("a") else "B",
                "runtime_class": "native_fast",
                "source": "fixture",
                "source_sha256": source_sha,
                "usage_boundary": "training_local",
                "evaluation_allowed": True,
                "training_allowed": True,
                "behavior_allowed": True,
                "submission_allowed": False,
                "observed_strength": 0.5,
                "observed_games": 16,
                "observed_fault_rate": 0.0,
                "frequency_proxy": 0.2,
                "hard_negative_score": 0.2,
                "diversity_contribution": 0.5,
                "top_meta_component": 0.2,
                "hard_negative_component": 0.2,
                "diversity_component": 0.2,
                "weight": 0.2,
                "split": split,
                "runtime_status": "fixture",
                "evidence_status": "fixture",
            }
        )
    manifest = {
        "schema_version": "meta-specialist-meta-distribution-v1",
        "candidate_id": "fixture",
        "sources": [{"path": str(source), "sha256": source_sha, "role": "fixture"}],
        "rows": rows,
        "component_targets": {"top_meta": 0.60, "hard_negative": 0.25, "diversity": 0.15},
        "split_ids": {
            "META_TRAIN": ["a1", "a2", "b1"],
            "META_DEV": ["dev"],
            "META_FINAL": ["final"],
        },
        "training_authority": False,
        "promotion_authority": False,
        "submission_authority": False,
        "research_only": True,
        "notes": ["fixture"],
    }
    meta = root / "meta-manifest.json"
    _write_canonical(meta, manifest)
    rows_path = root / "public-rows.jsonl"
    action0 = "0" * 64
    action1 = "1" * 64
    state = "a" * 64
    source_rows = [
        {"state_digest": state, "action_key": action0, "opponent_id": "a1", "seat": 0, "split": "META_TRAIN", "outcome": "win", "weight": 1.0},
        {"state_digest": state, "action_key": action0, "opponent_id": "a1", "seat": 1, "split": "META_TRAIN", "outcome": "loss", "weight": 2.0},
        {"state_digest": state, "action_key": action1, "opponent_id": "a2", "seat": 0, "split": "META_TRAIN", "outcome": "win", "weight": 3.0},
        {"state_digest": state, "action_key": action1, "opponent_id": "a2", "seat": 1, "split": "META_TRAIN", "outcome": "loss", "weight": 4.0},
    ]
    rows_path.write_bytes(b"".join(json.dumps(row, sort_keys=True, separators=(",", ":")).encode() + b"\n" for row in source_rows))
    table = build_public_advantage_table_v1(
        source_rows_path=rows_path,
        meta_manifest_path=meta,
        baseline_policy_sha256="f" * 64,
        iteration=0,
        min_support=2,
    )
    table_path = root / "public-advantage-table.json"
    _write_canonical(table_path, table.to_dict())
    return table_path


def _curriculum_payload(*, meta_manifest_path: Path | None = None) -> dict[str, object]:
    entries = []
    for opponent_id, family, split, weight, quota in (
        ("a1", "A", "META_TRAIN", 0.30, 4),
        ("a2", "A", "META_TRAIN", 0.30, 4),
        ("b1", "B", "META_TRAIN", 0.40, 4),
        ("dev", "D", "META_DEV", 0.0, 0),
        ("final", "F", "META_FINAL", 0.0, 0),
    ):
        entries.append(
            {
                "opponent_id": opponent_id,
                "family": family,
                "split": split,
                "weight": weight,
                "quota": quota,
                "reason": ["fixture"],
                "lineage": {"iteration": 0, "seed_tiebreak_sha256": "a" * 64},
                "statistics": {"hard_negative": 0.5, "fault_rate": 0.0},
                "training_exposure_allowed": split == "META_TRAIN",
                "teacher_behavior_allowed": split == "META_TRAIN",
            }
        )
    return {
        "schema_version": "meta-specialist-dynamic-meta-train-curriculum-v1",
        "purpose": "META_TRAIN_OPPONENT_ROLLOUT_RESEARCH_ONLY",
        "iteration": 0,
        "seed": "fixture-seed",
        "quota": 12,
        "sources": (
            [{
                "path": "meta-manifest.json",
                "file_sha256": _sha(meta_manifest_path),
                "role": "meta_distribution_manifest",
            }]
            if meta_manifest_path is not None
            else []
        ),
        "previous_iteration": None,
        "outcome_ledger": None,
        "parameters": {"max_opponent_weight": 0.45, "max_family_weight": 0.70, "min_family_quota": 1},
        "entries": entries,
        "summary": {"selected_by_split": {"META_TRAIN": 3, "META_DEV": 1, "META_FINAL": 1}},
        "consumer_contract": {"meta_dev_training_exposure": 0, "meta_final_training_exposure": 0},
        "authority": {
            "training_authority": False,
            "promotion_authority": False,
            "submission_authority": False,
            "external_execution_authority": False,
        },
        "curriculum_sha256": "fixture",
    }


def _adapter_payload(*, heldout: bool = False, authority: dict[str, object] | None = None) -> dict[str, object]:
    records = [
        {"record_index": 0, "game_id": "g-a1-0", "opponent_id": "a1", "split": "META_TRAIN", "candidate_score": 1.0, "fault": False, "seat": 0},
        {"record_index": 1, "game_id": "g-a1-1", "opponent_id": "a1", "split": "META_TRAIN", "candidate_score": 0.0, "fault": False, "seat": 1},
        {"record_index": 2, "game_id": "g-b1-0", "opponent_id": "b1", "split": "META_TRAIN", "candidate_score": 0.0, "fault": True, "seat": 0},
        {"record_index": 3, "game_id": "g-b1-1", "opponent_id": "b1", "split": "META_TRAIN", "candidate_score": 0.0, "fault": False, "seat": 0},
    ]
    if heldout:
        records.append({"record_index": 4, "game_id": "g-dev-0", "opponent_id": "dev", "split": "META_DEV", "candidate_score": 0.0, "fault": False, "seat": 0})
    return {
        "schema_version": "meta-specialist-common24-curriculum-outcome-adapter-v1",
        "purpose": "META_TRAIN_DYNAMIC_CURRICULUM_OUTCOME_RESEARCH_ONLY",
        "source_reconciliation": {"path": "reconciliation.json", "file_sha256": "a" * 64},
        "source_meta_distribution": {"path": "meta-manifest.json", "file_sha256": "b" * 64},
        "execution_closure": {
            "protocol_sha256": "c" * 64,
            "execution_closure_sha256": "c" * 64,
        },
        "arms": {},
        "blocks": [],
        "records": records,
        "excluded_heldout": {"META_DEV": {"opponent_ids": ["dev"], "rows": 0}, "META_FINAL": {"opponent_ids": ["final"], "rows": 0}},
        "output": {"path": "outcome-ledger.jsonl", "file_sha256": "d" * 64, "rows": len(records)},
        "summary": {"emitted_meta_train_rows": len(records)},
        "consumer_contract": {"meta_dev_rows_allowed": 0, "meta_final_rows_allowed": 0},
        "authority": authority or {"training_authority": False, "promotion_authority": False, "submission_authority": False, "external_execution_authority": False},
        "adapter_sha256": "fixture",
    }


def _sources(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, heldout: bool = False):
    root = tmp_path
    table_path = _make_public_table(root)
    curriculum_path = root / "curriculum.json"
    adapter_path = root / "adapter.json"
    _write_canonical(curriculum_path, _curriculum_payload(meta_manifest_path=root / "meta-manifest.json"))
    _write_canonical(adapter_path, _adapter_payload(heldout=heldout))
    policy = root / "native.py"
    policy.write_text("def agent(obs): return [0]\n", encoding="utf-8")
    deck = root / "native-deck.csv"
    deck.write_text(" ".join("1" for _ in range(60)), encoding="utf-8")
    baseline = {
        "candidate_id": "native-tomato",
        "policy_path": "native.py",
        "policy_sha256": _sha(policy),
        "deck_path": "native-deck.csv",
        "deck_sha256": _sha(deck),
        "evaluator_sha256": "e" * 64,
        "authority": {"training_authority": False, "promotion_authority": False, "submission_authority": False, "external_execution_authority": False},
        "research_only": True,
    }
    monkeypatch.setattr(
        iteration,
        "verify_dynamic_curriculum_manifest_v1",
        lambda *_args: _curriculum_payload(meta_manifest_path=root / "meta-manifest.json"),
    )
    monkeypatch.setattr(iteration, "verify_common24_curriculum_outcome_adapter_v1", lambda *_args: _adapter_payload(heldout=heldout))
    return root, curriculum_path, adapter_path, table_path, baseline


def _build(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **kwargs):
    root, curriculum, adapter, table, baseline = _sources(tmp_path, monkeypatch, heldout=kwargs.pop("heldout", False))
    output = root / "iteration-manifest.json"
    built = build_native_meta_overfit_iteration_v1(
        repo_root=root,
        curriculum_manifest_path=curriculum,
        outcome_adapter_manifest_path=adapter,
        public_advantage_table_path=table,
        native_baseline_identity=baseline,
        output_manifest_path=output,
        **kwargs,
    )
    return root, output, built


def test_build_is_meta_train_only_with_family_floor_cap_and_zero_heldout(tmp_path, monkeypatch):
    root, output, built = _build(tmp_path, monkeypatch)
    assert output.is_file()
    assert built["authority"] == {
        "training_authority": False,
        "promotion_authority": False,
        "submission_authority": False,
        "external_execution_authority": False,
    }
    assert built["ready_for_evaluation"] is False
    assert built["exposure_by_split"] == {"META_TRAIN": 4, "META_DEV": 0, "META_FINAL": 0}
    stats = built["opponent_statistics"]
    assert stats["dev"]["exposure"] == stats["final"]["exposure"] == 0
    assert stats["dev"]["weight"] == stats["final"]["weight"] == 0.0
    family = built["family_statistics"]
    assert all(row["weight"] <= 0.70 + 1e-12 for row in family.values())
    assert all(row["quota"] >= 1 for row in family.values())
    assert sum(row["weight"] for row in stats.values()) == pytest.approx(1.0)
    assert verify_native_meta_overfit_iteration_v1(output, root) == built


def test_fault_and_seat_weighting_are_bound_and_exposed(tmp_path, monkeypatch):
    _, _, built = _build(tmp_path, monkeypatch)
    b1 = built["opponent_statistics"]["b1"]
    assert b1["fault_count"] == 1
    assert b1["fault_rate"] == pytest.approx(0.5)
    assert b1["seat_exposure"] == {"0": 2, "1": 0}
    assert b1["reliability"] == pytest.approx(0.5)
    assert b1["seat_imbalance"] == pytest.approx(1.0)
    assert b1["weight"] > 0.0


def test_heldout_record_is_rejected_even_if_adapter_verifier_returns_it(tmp_path, monkeypatch):
    with pytest.raises(NativeMetaOverfitIterationError, match="held-out|META_DEV|META_FINAL"):
        _build(tmp_path, monkeypatch, heldout=True)


def test_meta_train_local_eval_only_source_is_rejected_before_weighting(tmp_path, monkeypatch):
    root, curriculum, adapter, table, baseline = _sources(tmp_path, monkeypatch)
    from mage_ptcg.meta_specialist.meta_distribution_v1 import load_meta_distribution_manifest_v1

    original = load_meta_distribution_manifest_v1(root / "meta-manifest.json", verify_sources=True)
    bad = replace(
        original,
        rows=tuple(
            replace(row, usage_boundary="local_eval_only", training_allowed=False, behavior_allowed=False)
            if row.opponent_id == "a1"
            else row
            for row in original.rows
        ),
    )
    monkeypatch.setattr(iteration, "load_meta_distribution_manifest_v1", lambda *_args, **_kwargs: bad)
    with pytest.raises(NativeMetaOverfitIterationError, match="training-local|permission"):
        build_native_meta_overfit_iteration_v1(
            repo_root=root,
            curriculum_manifest_path=curriculum,
            outcome_adapter_manifest_path=adapter,
            public_advantage_table_path=table,
            native_baseline_identity=baseline,
            output_manifest_path=root / "local-eval-fail.json",
        )


@pytest.mark.parametrize("field", ["training_exposure_allowed", "teacher_behavior_allowed"])
def test_meta_train_entry_permission_flags_are_rechecked(tmp_path, monkeypatch, field):
    root, curriculum, adapter, table, baseline = _sources(tmp_path, monkeypatch)
    payload = _curriculum_payload(meta_manifest_path=root / "meta-manifest.json")
    for entry in payload["entries"]:
        if entry["opponent_id"] == "a1":
            entry[field] = False
    monkeypatch.setattr(iteration, "verify_dynamic_curriculum_manifest_v1", lambda *_args: payload)
    with pytest.raises(NativeMetaOverfitIterationError, match="exposure/behavior permission"):
        build_native_meta_overfit_iteration_v1(
            repo_root=root,
            curriculum_manifest_path=curriculum,
            outcome_adapter_manifest_path=adapter,
            public_advantage_table_path=table,
            native_baseline_identity=baseline,
            output_manifest_path=root / f"entry-permission-{field}.json",
        )


def test_meta_distribution_authority_is_rechecked(tmp_path, monkeypatch):
    root, curriculum, adapter, table, baseline = _sources(tmp_path, monkeypatch)
    from mage_ptcg.meta_specialist.meta_distribution_v1 import load_meta_distribution_manifest_v1

    original = load_meta_distribution_manifest_v1(root / "meta-manifest.json", verify_sources=True)
    from types import SimpleNamespace

    bad = SimpleNamespace(
        research_only=True,
        training_authority=True,
        promotion_authority=False,
        submission_authority=False,
        rows=original.rows,
    )
    monkeypatch.setattr(iteration, "load_meta_distribution_manifest_v1", lambda *_args, **_kwargs: bad)
    with pytest.raises(NativeMetaOverfitIterationError, match="grants authority"):
        build_native_meta_overfit_iteration_v1(
            repo_root=root,
            curriculum_manifest_path=curriculum,
            outcome_adapter_manifest_path=adapter,
            public_advantage_table_path=table,
            native_baseline_identity=baseline,
            output_manifest_path=root / "meta-authority-fail.json",
        )


def test_meta_distribution_research_only_is_rechecked(tmp_path, monkeypatch):
    root, curriculum, adapter, table, baseline = _sources(tmp_path, monkeypatch)
    from mage_ptcg.meta_specialist.meta_distribution_v1 import load_meta_distribution_manifest_v1
    from types import SimpleNamespace

    original = load_meta_distribution_manifest_v1(root / "meta-manifest.json", verify_sources=True)
    bad = SimpleNamespace(
        research_only=False,
        training_authority=False,
        promotion_authority=False,
        submission_authority=False,
        rows=original.rows,
    )
    monkeypatch.setattr(iteration, "load_meta_distribution_manifest_v1", lambda *_args, **_kwargs: bad)
    with pytest.raises(NativeMetaOverfitIterationError, match="grants authority"):
        build_native_meta_overfit_iteration_v1(
            repo_root=root,
            curriculum_manifest_path=curriculum,
            outcome_adapter_manifest_path=adapter,
            public_advantage_table_path=table,
            native_baseline_identity=baseline,
            output_manifest_path=root / "meta-research-only-fail.json",
        )


def test_derive_weighting_rejects_structurally_forged_permission_map(tmp_path, monkeypatch):
    """A caller cannot bypass source verification by supplying a matching map."""
    root, _, _, _, _ = _sources(tmp_path, monkeypatch)
    curriculum = _curriculum_payload(meta_manifest_path=root / "meta-manifest.json")
    curriculum["_verified_permission_by_id"] = {
        opponent_id: {
            "usage_boundary": "training_local",
            "training_allowed": True,
            "behavior_allowed": True,
            "submission_allowed": False,
        }
        for opponent_id in ("a1", "a2", "b1")
    }
    # The mapping is intentionally structurally identical to the verified
    # shape.  It is still untrusted because it was not produced by
    # _verified_curriculum from the bound meta-distribution source.
    with pytest.raises(NativeMetaOverfitIterationError, match="verified|source"):
        iteration._derive_weighting(curriculum, _adapter_payload())


def test_derive_weighting_rejects_mutated_verified_permission_digest(tmp_path, monkeypatch):
    root, curriculum_path, _, _, _ = _sources(tmp_path, monkeypatch)
    verified = iteration._verified_curriculum(curriculum_path, root)
    verified._permission_digest = "0" * 64
    with pytest.raises(NativeMetaOverfitIterationError, match="proof|mutated"):
        iteration._derive_weighting(verified, _adapter_payload())


def test_atomic_new_claim_never_calls_replace_and_never_clobbers(tmp_path, monkeypatch):
    target = tmp_path / "manifest.json"
    monkeypatch.setattr(iteration.os, "replace", lambda *_args, **_kwargs: pytest.fail("clobbering replace is forbidden"))
    iteration._atomic_write_new(target, b"first")
    assert target.read_bytes() == b"first"
    with pytest.raises(FileExistsError):
        iteration._atomic_write_new(target, b"second")
    assert target.read_bytes() == b"first"


def test_builder_race_does_not_delete_winner_after_claim_failure(tmp_path, monkeypatch):
    root, curriculum, adapter, table, baseline = _sources(tmp_path, monkeypatch)
    output = root / "race-iteration.json"

    def competing_writer(path, raw):
        # Simulate another process winning the exclusive destination claim
        # between the preflight exists check and this builder's write.
        path.write_bytes(b"winner")
        raise FileExistsError(path)

    monkeypatch.setattr(iteration, "_atomic_write_new", competing_writer)
    with pytest.raises(FileExistsError):
        build_native_meta_overfit_iteration_v1(
            repo_root=root,
            curriculum_manifest_path=curriculum,
            outcome_adapter_manifest_path=adapter,
            public_advantage_table_path=table,
            native_baseline_identity=baseline,
            output_manifest_path=output,
        )
    assert output.read_bytes() == b"winner"


def test_source_sha_binding_and_deterministic_seed(tmp_path, monkeypatch):
    root, output, first = _build(tmp_path, monkeypatch)
    second_output = root / "iteration-manifest-second.json"
    curriculum = root / "curriculum.json"
    adapter = root / "adapter.json"
    table = root / "public-advantage-table.json"
    baseline = first["native_baseline"]
    second = build_native_meta_overfit_iteration_v1(
        repo_root=root,
        curriculum_manifest_path=curriculum,
        outcome_adapter_manifest_path=adapter,
        public_advantage_table_path=table,
        native_baseline_identity=baseline,
        output_manifest_path=second_output,
    )
    assert first["derived_iteration_seed_sha256"] == second["derived_iteration_seed_sha256"]
    assert first["hard_negative_weights_sha256"] == second["hard_negative_weights_sha256"]
    curriculum.write_text("tampered", encoding="utf-8")
    with pytest.raises(NativeMetaOverfitIterationError, match="source|SHA"):
        verify_native_meta_overfit_iteration_v1(output, root)


@pytest.mark.parametrize("authority_field", ["training_authority", "promotion_authority", "submission_authority", "external_execution_authority"])
def test_all_authority_false_is_enforced(tmp_path, monkeypatch, authority_field):
    root, curriculum, adapter, table, baseline = _sources(tmp_path, monkeypatch)
    baseline["authority"][authority_field] = True
    with pytest.raises(NativeMetaOverfitIterationError, match="authority"):
        build_native_meta_overfit_iteration_v1(
            repo_root=root,
            curriculum_manifest_path=curriculum,
            outcome_adapter_manifest_path=adapter,
            public_advantage_table_path=table,
            native_baseline_identity=baseline,
            output_manifest_path=root / "authority-fail.json",
        )


def test_optional_legal_candidate_identity_is_bound(tmp_path, monkeypatch):
    root, curriculum, adapter, table, baseline = _sources(tmp_path, monkeypatch)
    candidate_deck = root / "candidate-deck.csv"
    candidate_deck.write_text(" ".join("2" for _ in range(60)), encoding="utf-8")
    candidate_manifest = root / "candidate-deck-manifest.json"
    _write_canonical(
        candidate_manifest,
        {
            "schema_version": "native-meta-overfit-legal-deck-candidate-v1",
            "candidate_id": "candidate-deck",
            "deck_path": "candidate-deck.csv",
            "deck_sha256": _sha(candidate_deck),
            "legal": True,
            "research_only": True,
            "authority": {"training_authority": False, "promotion_authority": False, "submission_authority": False, "external_execution_authority": False},
        },
    )
    built = build_native_meta_overfit_iteration_v1(
        repo_root=root,
        curriculum_manifest_path=curriculum,
        outcome_adapter_manifest_path=adapter,
        public_advantage_table_path=table,
        native_baseline_identity=baseline,
        candidate_deck_manifest_path=candidate_manifest,
        output_manifest_path=root / "candidate-iteration.json",
    )
    assert built["candidate_identity"]["candidate_id"] == "candidate-deck"
    assert built["candidate_identity"]["deck_sha256"] == _sha(candidate_deck)
    assert built["ready_for_evaluation"] is False


def test_outcome_adapter_protocol_sha_is_required(tmp_path, monkeypatch):
    root, curriculum, adapter, table, baseline = _sources(tmp_path, monkeypatch)
    invalid = _adapter_payload()
    invalid["execution_closure"] = {"execution_closure_sha256": "c" * 64}
    monkeypatch.setattr(iteration, "verify_common24_curriculum_outcome_adapter_v1", lambda *_args: invalid)
    with pytest.raises(NativeMetaOverfitIterationError, match="protocol_sha256"):
        build_native_meta_overfit_iteration_v1(
            repo_root=root,
            curriculum_manifest_path=curriculum,
            outcome_adapter_manifest_path=adapter,
            public_advantage_table_path=table,
            native_baseline_identity=baseline,
            output_manifest_path=root / "protocol-fail.json",
        )


def test_native_outcome_arm_must_match_baseline(tmp_path, monkeypatch):
    root, curriculum, adapter, table, baseline = _sources(tmp_path, monkeypatch)
    invalid = _adapter_payload()
    invalid["arms"] = {
        "native": {
            "policy_sha256": "d" * 64,
            "deck_sha256": baseline["deck_sha256"],
        }
    }
    monkeypatch.setattr(iteration, "verify_common24_curriculum_outcome_adapter_v1", lambda *_args: invalid)
    with pytest.raises(NativeMetaOverfitIterationError, match="native baseline policy_sha256"):
        build_native_meta_overfit_iteration_v1(
            repo_root=root,
            curriculum_manifest_path=curriculum,
            outcome_adapter_manifest_path=adapter,
            public_advantage_table_path=table,
            native_baseline_identity=baseline,
            output_manifest_path=root / "native-arm-fail.json",
        )


def test_cli_defaults_to_dry_run_and_rejects_execute(tmp_path, monkeypatch, capsys):
    root, curriculum, adapter, table, baseline = _sources(tmp_path, monkeypatch)
    baseline_path = root / "native-baseline.json"
    _write_canonical(baseline_path, baseline)
    output = root / "cli-iteration.json"
    from scripts.build_native_meta_overfit_iteration_v1 import main

    assert main(
        [
            "--repo-root", str(root),
            "--curriculum-manifest", str(curriculum),
            "--outcome-adapter-manifest", str(adapter),
            "--public-advantage-table", str(table),
            "--native-baseline-identity", str(baseline_path),
            "--output-manifest", str(output),
        ]
    ) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["status"] == "DRY_RUN"
    assert summary["processes_launched"] is False
    assert summary["cabt_started"] is False
    assert summary["training_started"] is False
    assert summary["submission_started"] is False
    assert main(
        [
            "--repo-root", str(root),
            "--curriculum-manifest", str(curriculum),
            "--outcome-adapter-manifest", str(adapter),
            "--public-advantage-table", str(table),
            "--native-baseline-identity", str(baseline_path),
            "--output-manifest", str(root / "execute.json"),
            "--execute",
        ]
    ) == 2
    assert "--execute is disabled" in capsys.readouterr().err
