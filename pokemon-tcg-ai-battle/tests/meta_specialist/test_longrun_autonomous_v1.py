"""Contracts for the research-only autonomous meta long-run.

The tests intentionally never start CABT or a trainer.  They exercise the
content-addressed preflight, fixed meta split, checkpoint/rollback journal,
and the fail-closed LONGRUN_READY gate.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from mage_ptcg.meta_specialist.longrun_autonomous_v1 import (
    BlockEvidenceV1,
    GateEvidenceV1,
    LongrunConfigV1,
    LongrunError,
    NativeBaselineV1,
    checkpoint_longrun_v1,
    config_sha256_v1,
    evaluate_longrun_gate_v1,
    initialize_longrun_v1,
    launch_longrun_v1,
    load_longrun_state_v1,
    record_gate_v1,
    record_native_regression_v1,
    resume_longrun_v1,
    rollback_longrun_v1,
    stop_longrun_v1,
)


def _sha(char: str) -> str:
    return char * 64


def _manifest(tmp_path: Path) -> tuple[Path, str, tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    source = tmp_path / "fixture-source.json"
    source.write_text("{\"source\":\"fixture\"}\n", encoding="utf-8")
    manifest = {
        "schema_version": "meta-specialist-meta-distribution-v1",
        "candidate_id": "candidate",
        "sources": [{
            "path": str(source.resolve()),
            "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "role": "fixture",
        }],
        "rows": [
            {
                "opponent_id": "train",
                "pair_id": "train::pair",
                "deck_sha256": _sha("1"),
                "policy_sha256": _sha("2"),
                "archetype": "Archaludon",
                "runtime_class": "native_fast",
                "source": "fixture",
                "source_sha256": _sha("3"),
                "usage_boundary": "training_local",
                "evaluation_allowed": True,
                "training_allowed": True,
                "behavior_allowed": True,
                "submission_allowed": False,
                "observed_strength": 0.7,
                "observed_games": 96,
                "observed_fault_rate": 0.0,
                "frequency_proxy": 0.5,
                "hard_negative_score": 0.5,
                "diversity_contribution": 1.0,
                "top_meta_component": 1.0,
                "hard_negative_component": 1.0,
                "diversity_component": 1.0,
                "weight": 0.5,
                "split": "META_TRAIN",
                "runtime_status": "fixture",
                "evidence_status": "observed",
            },
            {
                "opponent_id": "dev-a",
                "pair_id": "dev-a::pair",
                "deck_sha256": _sha("4"),
                "policy_sha256": _sha("5"),
                "archetype": "Crustle",
                "runtime_class": "native_fast",
                "source": "fixture",
                "source_sha256": _sha("6"),
                "usage_boundary": "local_eval_only",
                "evaluation_allowed": True,
                "training_allowed": False,
                "behavior_allowed": False,
                "submission_allowed": False,
                "observed_strength": 0.6,
                "observed_games": 96,
                "observed_fault_rate": 0.0,
                "frequency_proxy": 0.5,
                "hard_negative_score": 0.5,
                "diversity_contribution": 1.0,
                "top_meta_component": 0.0,
                "hard_negative_component": 0.0,
                "diversity_component": 0.0,
                "weight": 0.25,
                "split": "META_DEV",
                "runtime_status": "fixture",
                "evidence_status": "observed",
            },
            {
                "opponent_id": "final-a",
                "pair_id": "final-a::pair",
                "deck_sha256": _sha("7"),
                "policy_sha256": _sha("8"),
                "archetype": "Mew",
                "runtime_class": "native_fast",
                "source": "fixture",
                "source_sha256": _sha("9"),
                "usage_boundary": "local_eval_only",
                "evaluation_allowed": True,
                "training_allowed": False,
                "behavior_allowed": False,
                "submission_allowed": False,
                "observed_strength": 0.6,
                "observed_games": 96,
                "observed_fault_rate": 0.0,
                "frequency_proxy": 0.5,
                "hard_negative_score": 0.5,
                "diversity_contribution": 1.0,
                "top_meta_component": 0.0,
                "hard_negative_component": 0.0,
                "diversity_component": 0.0,
                "weight": 0.25,
                "split": "META_FINAL",
                "runtime_status": "fixture",
                "evidence_status": "observed",
            },
        ],
        "component_targets": {"top_meta": 0.60, "hard_negative": 0.25, "diversity": 0.15},
        "split_ids": {"META_TRAIN": ["train"], "META_DEV": ["dev-a"], "META_FINAL": ["final-a"]},
        "training_authority": False,
        "promotion_authority": False,
        "submission_authority": False,
        "research_only": True,
        "notes": ["fixture"],
    }
    path = tmp_path / "meta-manifest.json"
    path.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
    return (
        path,
        hashlib.sha256(path.read_bytes()).hexdigest(),
        ("train",),
        ("dev-a",),
        ("final-a",),
    )


def _config(tmp_path: Path) -> LongrunConfigV1:
    manifest, manifest_sha, train, dev, final = _manifest(tmp_path)
    return LongrunConfigV1(
        run_dir=tmp_path / "run",
        manifest_path=manifest,
        manifest_sha256=manifest_sha,
        native_baseline=NativeBaselineV1(
            pair_id="native::baseline",
            deck_sha256=_sha("a"),
            policy_sha256=_sha("b"),
            evaluator_sha256=_sha("c"),
            status="PROVEN",
        ),
        meta_train_ids=train,
        meta_dev_ids=dev,
        meta_final_ids=final,
    )


def _blocks() -> tuple[BlockEvidenceV1, ...]:
    return (
        BlockEvidenceV1(
            block_id="block-1", split="META_DEV", seed_id="seed-a", games=96,
            native_score=0.70, candidate_score=0.74, fault_count=0,
            seat0_games=48, seat1_games=48, seat0_candidate_score=0.75,
            seat1_candidate_score=0.73,
        ),
        BlockEvidenceV1(
            block_id="block-2", split="META_DEV", seed_id="seed-b", games=96,
            native_score=0.71, candidate_score=0.75, fault_count=0,
            seat0_games=48, seat1_games=48, seat0_candidate_score=0.76,
            seat1_candidate_score=0.74,
        ),
    )


def test_dry_run_seals_config_and_never_launches(tmp_path: Path) -> None:
    config = _config(tmp_path)
    descriptor = initialize_longrun_v1(config, execute=False)
    assert descriptor["status"] == "DRY_RUN"
    assert descriptor["launch_allowed"] is False
    assert descriptor["config_sha256"] == config_sha256_v1(config)
    assert (config.run_dir / "run-manifest.json").is_file()
    assert load_longrun_state_v1(config)["status"] == "DRY_RUN"

    called = False

    def runner(_config: LongrunConfigV1) -> object:
        nonlocal called
        called = True
        return {"started": True}

    result = launch_longrun_v1(config, execute=False, runner=runner)
    assert result["status"] == "DRY_RUN"
    assert called is False
    resumed = resume_longrun_v1(config, execute=False, runner=runner)
    assert resumed["resumed"] is True
    assert called is False


def test_execute_is_fail_closed_before_longrun_ready(tmp_path: Path) -> None:
    config = _config(tmp_path)
    initialize_longrun_v1(config, execute=False)
    with pytest.raises(LongrunError, match="LONGRUN_READY"):
        launch_longrun_v1(config, execute=True, runner=lambda _config: None)


def test_gate_requires_two_fault_free_seat_balanced_dev_blocks() -> None:
    baseline = NativeBaselineV1(
        pair_id="native::baseline", deck_sha256=_sha("a"), policy_sha256=_sha("b"),
        evaluator_sha256=_sha("c"), status="PROVEN",
    )
    gate = evaluate_longrun_gate_v1(
        baseline=baseline,
        meta_train_ids=("train",), meta_dev_ids=("dev-a",), meta_final_ids=("final-a",),
        blocks=_blocks(), package_closure=True,
    )
    assert isinstance(gate, GateEvidenceV1)
    assert gate.ready is True
    assert gate.meta_final_isolated is True

    bad = evaluate_longrun_gate_v1(
        baseline=baseline,
        meta_train_ids=("train",), meta_dev_ids=("dev-a",), meta_final_ids=("final-a",),
        blocks=(_blocks()[0], BlockEvidenceV1(
            block_id="block-1", split="META_DEV", seed_id="seed-a", games=96,
            native_score=0.70, candidate_score=0.74, fault_count=1,
            seat0_games=48, seat1_games=48, seat0_candidate_score=0.75,
            seat1_candidate_score=0.73,
        )), package_closure=True,
    )
    assert bad.ready is False
    assert bad.fault_free is False


def test_gate_manifest_is_recorded_checkpoint_stops_and_rolls_back(tmp_path: Path) -> None:
    config = _config(tmp_path)
    initialize_longrun_v1(config, execute=False)
    gate = evaluate_longrun_gate_v1(
        baseline=config.native_baseline,
        meta_train_ids=config.meta_train_ids,
        meta_dev_ids=config.meta_dev_ids,
        meta_final_ids=config.meta_final_ids,
        blocks=_blocks(), package_closure=True,
    )
    record_gate_v1(config, gate)
    assert load_longrun_state_v1(config)["status"] == "LONGRUN_READY"

    checkpoint = tmp_path / "candidate.ckpt"
    checkpoint.write_bytes(b"candidate-state")
    checkpoint_row = checkpoint_longrun_v1(config, checkpoint_path=checkpoint, stage="policy")
    assert checkpoint_row["checkpoint_sha256"] == hashlib.sha256(checkpoint.read_bytes()).hexdigest()
    assert load_longrun_state_v1(config)["latest_checkpoint_sha256"] == checkpoint_row["checkpoint_sha256"]

    stop_longrun_v1(config, reason="manual safety stop")
    assert load_longrun_state_v1(config)["status"] == "STOPPED"
    rollback_longrun_v1(config, checkpoint_path=checkpoint)
    state = load_longrun_state_v1(config)
    assert state["status"] == "ROLLED_BACK"
    assert state["active_checkpoint_sha256"] == checkpoint_row["checkpoint_sha256"]


def test_two_native_regressions_trigger_a_safety_stop(tmp_path: Path) -> None:
    config = _config(tmp_path)
    initialize_longrun_v1(config, execute=False)
    record_native_regression_v1(
        config, block_id="reg-1", candidate_score=0.60, native_score=0.70,
    )
    assert load_longrun_state_v1(config)["status"] == "DRY_RUN"
    state = record_native_regression_v1(
        config, block_id="reg-2", candidate_score=0.61, native_score=0.70,
    )
    assert state["status"] == "STOPPED"
    assert state["regression_count"] == 2


def test_split_or_manifest_drift_is_rejected(tmp_path: Path) -> None:
    config = _config(tmp_path)
    initialize_longrun_v1(config, execute=False)
    changed = LongrunConfigV1(
        run_dir=config.run_dir,
        manifest_path=config.manifest_path,
        manifest_sha256=config.manifest_sha256,
        native_baseline=config.native_baseline,
        meta_train_ids=config.meta_train_ids,
        meta_dev_ids=("unknown-dev",),
        meta_final_ids=config.meta_final_ids,
    )
    with pytest.raises(LongrunError, match="META_DEV"):
        initialize_longrun_v1(changed, execute=False)
    config.manifest_path.write_text(config.manifest_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(LongrunError, match="manifest SHA"):
        load_longrun_state_v1(config)
