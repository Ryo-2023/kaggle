"""Research-only alternating deck/policy optimizer contract tests."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from mage_ptcg.meta_specialist.alternating_meta_optimizer_v1 import (
    ALTERNATING_META_OPTIMIZER_SCHEMA_V1,
    SUCCESSIVE_HALVING_GAMES_V1,
    AlternatingMetaOptimizerError,
    CandidateStateV1,
    NativeBaselineArmV1,
    ResearchAuthorityV1,
    advance_candidate_state_v1,
    checkpoint_alternating_meta_optimizer_v1,
    execute_alternating_meta_optimizer_v1,
    initialize_alternating_meta_optimizer_v1,
    load_alternating_meta_optimizer_v1,
    promote_successive_halving_v1,
    rollback_alternating_meta_optimizer_v1,
    resume_alternating_meta_optimizer_v1,
)
from mage_ptcg.meta_specialist.meta_distribution_v1 import (
    MetaDistributionManifestV1,
    MetaDistributionRowV1,
    MetaSourceArtifactV1,
    save_meta_distribution_manifest_v1,
)


NATIVE = NativeBaselineArmV1(
    pair_id="native-best-known",
    deck_sha256="a" * 64,
    policy_sha256="b" * 64,
    evaluator_sha256="c" * 64,
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sources(tmp_path: Path) -> tuple[Path, Path]:
    manifest = tmp_path / "meta-manifest.json"
    schedule = tmp_path / "meta-schedule.json"
    source = tmp_path / "source.json"
    source.write_text(json.dumps({"fixture": True}), encoding="utf-8")
    source_sha = _sha(source)
    manifest_value = MetaDistributionManifestV1(
        schema_version="meta-specialist-meta-distribution-v1",
        candidate_id="candidate-a",
        sources=(MetaSourceArtifactV1(str(source), source_sha, "fixture"),),
        rows=(MetaDistributionRowV1(
            opponent_id="opponent-a",
            pair_id="pair-a",
            deck_sha256="1" * 64,
            policy_sha256="2" * 64,
            archetype="Fixture",
            runtime_class="native_fast",
            source="fixture",
            source_sha256=source_sha,
            usage_boundary="training_local",
            evaluation_allowed=True,
            training_allowed=True,
            behavior_allowed=True,
            submission_allowed=False,
            observed_strength=0.6,
            observed_games=96,
            observed_fault_rate=0.0,
            frequency_proxy=1.0,
            hard_negative_score=1.0,
            diversity_contribution=1.0,
            top_meta_component=1.0,
            hard_negative_component=1.0,
            diversity_component=1.0,
            weight=1.0,
            split="META_TRAIN",
            runtime_status="fixture",
            evidence_status="observed",
        ),),
        component_targets={"top_meta": 0.60, "hard_negative": 0.25, "diversity": 0.15},
        split_ids={"META_TRAIN": ("opponent-a",), "META_DEV": (), "META_FINAL": ()},
        training_authority=False,
        promotion_authority=False,
        submission_authority=False,
        research_only=True,
        notes=("fixture",),
    )
    save_meta_distribution_manifest_v1(manifest_value, manifest)
    schedule.write_text(json.dumps({"split": "META_TRAIN", "quota": 96}), encoding="utf-8")
    return manifest, schedule


def _state(*, phase: str = "POLICY_FIXED_SHORT", stage_games: int = 96) -> CandidateStateV1:
    return CandidateStateV1(
        schema_version=ALTERNATING_META_OPTIMIZER_SCHEMA_V1,
        candidate_id="candidate-a",
        parent_candidate_id=None,
        phase=phase,
        deck_sha256="d" * 64,
        policy_config_sha256="e" * 64,
        meta_manifest_sha256="f" * 64,
        meta_schedule_sha256="0" * 64,
        stage_games=stage_games,
        native_baseline=NATIVE,
        authority=ResearchAuthorityV1(),
    )


def test_candidate_state_has_native_baseline_and_two_fixed_timescales() -> None:
    state = _state()
    assert state.policy_fixed is True
    assert state.deck_fixed is False
    switched = advance_candidate_state_v1(
        state,
        phase="DECK_FIXED_LONG",
        policy_config_sha256="1" * 64,
    )
    assert switched.policy_fixed is False
    assert switched.deck_fixed is True
    assert switched.deck_sha256 == state.deck_sha256
    with pytest.raises(AlternatingMetaOptimizerError, match="policy-fixed"):
        advance_candidate_state_v1(state, policy_config_sha256="1" * 64)


def test_successive_halving_requires_exact_96_to_1536_sequence_and_keeps_native_arm() -> None:
    assert SUCCESSIVE_HALVING_GAMES_V1 == (96, 384, 768, 1536)
    states = tuple(
        CandidateStateV1(
            schema_version=ALTERNATING_META_OPTIMIZER_SCHEMA_V1,
            candidate_id=f"candidate-{index}",
            parent_candidate_id=None,
            phase="POLICY_FIXED_SHORT",
            deck_sha256=(str(index) * 64)[:64],
            policy_config_sha256="e" * 64,
            meta_manifest_sha256="f" * 64,
            meta_schedule_sha256="0" * 64,
            stage_games=96,
            native_baseline=NATIVE,
            authority=ResearchAuthorityV1(),
        )
        for index in (1, 2, 3, 4)
    )
    promoted = promote_successive_halving_v1(
        states,
        {state.candidate_id: 0.60 + 0.01 * index for index, state in enumerate(states)},
        native_baseline_score=0.59,
        next_stage_games=384,
    )
    assert len(promoted) == 2
    assert all(state.stage_games == 384 for state in promoted)
    assert all(state.native_baseline.pair_id == NATIVE.pair_id for state in promoted)
    with pytest.raises(AlternatingMetaOptimizerError, match="next successive-halving"):
        promote_successive_halving_v1(
            states,
            {state.candidate_id: 0.7 for state in states},
            native_baseline_score=0.59,
            next_stage_games=768,
        )


def test_initialize_checkpoint_resume_and_rollback_are_hash_bound_and_atomic(tmp_path: Path) -> None:
    manifest, schedule = _sources(tmp_path)
    run_dir = tmp_path / "run"
    initialized = initialize_alternating_meta_optimizer_v1(
        run_dir=run_dir,
        candidate_id="candidate-a",
        deck_sha256="d" * 64,
        policy_config_sha256="e" * 64,
        meta_manifest_path=manifest,
        meta_schedule_path=schedule,
        native_baseline=NATIVE,
        execute=False,
    )
    assert initialized["status"] == "DRY_RUN"
    assert initialized["authority"]["execute_allowed"] is False
    state = load_alternating_meta_optimizer_v1(run_dir)
    artifact = tmp_path / "candidate.ckpt"
    artifact.write_bytes(b"candidate checkpoint")
    checkpointed = checkpoint_alternating_meta_optimizer_v1(
        run_dir,
        state=state,
        checkpoint_path=artifact,
        stage="policy-fixed-short",
        metrics={"native_score": 0.59},
    )
    assert checkpointed["status"] == "CHECKPOINTED"
    resumed = resume_alternating_meta_optimizer_v1(run_dir, execute=False)
    assert resumed["resumed"] is True
    assert resumed["active_checkpoint_sha256"]
    rolled_back = rollback_alternating_meta_optimizer_v1(run_dir)
    assert rolled_back["status"] == "ROLLED_BACK"
    assert rolled_back["active_checkpoint_sha256"] == resumed["active_checkpoint_sha256"]

    manifest.write_text('{"tampered": true}', encoding="utf-8")
    with pytest.raises(AlternatingMetaOptimizerError, match="manifest SHA"):
        resume_alternating_meta_optimizer_v1(run_dir, execute=False)


def test_authority_and_execute_are_fail_closed(tmp_path: Path) -> None:
    manifest, schedule = _sources(tmp_path)
    with pytest.raises(AlternatingMetaOptimizerError, match="authority"):
        ResearchAuthorityV1(execute_allowed=True)
    with pytest.raises(AlternatingMetaOptimizerError, match="execute"):
        initialize_alternating_meta_optimizer_v1(
            run_dir=tmp_path / "run",
            candidate_id="candidate-a",
            deck_sha256="d" * 64,
            policy_config_sha256="e" * 64,
            meta_manifest_path=manifest,
            meta_schedule_path=schedule,
            native_baseline=NATIVE,
            execute=True,
        )
    called = False

    def runner() -> None:
        nonlocal called
        called = True

    with pytest.raises(AlternatingMetaOptimizerError, match="execute"):
        execute_alternating_meta_optimizer_v1(
            tmp_path / "run",
            execute=True,
            runner=runner,
        )
    assert called is False
