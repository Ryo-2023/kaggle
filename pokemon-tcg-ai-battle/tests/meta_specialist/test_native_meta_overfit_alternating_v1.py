"""Contract tests for the native-preserving alternating state bridge."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import mage_ptcg.meta_specialist.native_meta_overfit_alternating_v1 as alternating

from mage_ptcg.meta_specialist.alternating_meta_optimizer_v1 import (
    CandidateStateV1,
    NativeBaselineArmV1,
    POLICY_FIXED_SHORT_V1,
    DECK_FIXED_LONG_V1,
    ResearchAuthorityV1,
    advance_candidate_state_v1,
)
from mage_ptcg.meta_specialist.deck_mutation_v1 import generate_deck_mutation_candidates_v1
from mage_ptcg.meta_specialist.joint_optimization_v1 import CoreSignatureV1
from mage_ptcg.meta_specialist.native_meta_overfit_alternating_v1 import (
    EXACT_STAGE_GAMES_V1,
    EvaluationSummaryV1,
    NativeRegressionJournalV1,
    NativeMetaOverfitAlternatingError,
    build_native_meta_overfit_state_v1,
    build_rollback_descriptor_v1,
    evaluate_native_meta_overfit_stage_v1,
    promote_native_meta_overfit_successive_halving_v1,
    advance_native_meta_overfit_state_v1,
    verify_rollback_descriptor_v1,
    build_native_regression_journal_v1,
)


AUTHORITY_FALSE = {
    "training_authority": False,
    "promotion_authority": False,
    "submission_authority": False,
    "external_execution_authority": False,
}

NATIVE = NativeBaselineArmV1(
    pair_id="native-tomato",
    deck_sha256="a" * 64,
    policy_sha256="b" * 64,
    evaluator_sha256="c" * 64,
)


def test_public_exports_include_regression_journal() -> None:
    assert {
        "NativeRegressionJournalV1",
        "build_native_regression_journal_v1",
    }.issubset(set(alternating.__all__))


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_canonical(path: Path, value: object) -> None:
    path.write_bytes(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())


def _iteration_manifest_payload() -> dict[str, object]:
    return {
        "schema_version": "meta-specialist-native-meta-overfit-iteration-v1",
        "purpose": "NATIVE_PRESERVING_META_OVERFIT_RESEARCH_ONLY",
        "iteration": 1,
        "seed": "fixture-seed",
        "sources": [],
        "outcome_adapter_identity": {
            "protocol_sha256": "d" * 64,
            "execution_closure_sha256": "e" * 64,
        },
        "public_advantage_identity": {
            "table_sha256": "f" * 64,
            "file_sha256": "1" * 64,
        },
        "native_baseline": {
            "candidate_id": NATIVE.pair_id,
            "policy_sha256": NATIVE.policy_sha256,
            "deck_sha256": NATIVE.deck_sha256,
            "evaluator_sha256": NATIVE.evaluator_sha256,
            "authority": AUTHORITY_FALSE,
            "research_only": True,
        },
        "gate_status": {
            "curriculum_verified": True,
            "outcome_adapter_verified": True,
            "public_advantage_table_verified": True,
            "native_control_bound": True,
            "meta_train_only": True,
            "heldout_zero_exposure": True,
            "authority_false": True,
            "package_closure": False,
            "evaluator_closure": False,
            "performance_gate": False,
        },
        "authority": AUTHORITY_FALSE,
        "ready_for_evaluation": False,
        "iteration_sha256": "0" * 64,
    }


def _sources(tmp_path: Path) -> tuple[Path, Path]:
    iteration = tmp_path / "iteration.json"
    payload = _iteration_manifest_payload()
    fixture_source = tmp_path / "fixture-source.json"
    _write_canonical(fixture_source, {"fixture": True})
    payload["sources"] = [
        {
            "path": fixture_source.name,
            "file_sha256": _sha(fixture_source),
            "role": "fixture-source",
        }
    ]
    semantic_body = {key: value for key, value in payload.items() if key != "iteration_sha256"}
    payload["iteration_sha256"] = hashlib.sha256(
        payload["schema_version"].encode("ascii")
        + b"\0"
        + json.dumps(semantic_body, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    _write_canonical(iteration, payload)
    schedule = tmp_path / "schedule.json"
    _write_canonical(schedule, {"schema_version": "common24", "protocol_sha256": "d" * 64})
    return iteration, schedule


@pytest.fixture(autouse=True)
def _stub_task2_verifier(monkeypatch):
    """The unit fixture is not a full Task2 materialization; production still calls the verifier."""
    monkeypatch.setattr(
        alternating,
        "verify_native_meta_overfit_iteration_v1",
        lambda path, _root: json.loads(Path(path).read_text()),
    )


def _state(tmp_path: Path, *, phase: str = POLICY_FIXED_SHORT_V1, deck_sha: str = "1" * 64) -> tuple[Path, Path, CandidateStateV1]:
    iteration, schedule = _sources(tmp_path)
    state = build_native_meta_overfit_state_v1(
        iteration_manifest_path=iteration,
        meta_schedule_path=schedule,
        candidate_id="candidate-a",
        deck_sha256=deck_sha,
        policy_config_sha256="2" * 64,
        native_baseline=NATIVE,
        phase=phase,
    )
    return iteration, schedule, state


def _summary(
    candidate_id: str = "candidate-a",
    *,
    stage_games: int = 96,
    candidate_score: float = 0.62,
    native_score: float = 0.60,
    candidate_fault_count: int = 0,
    native_fault_count: int = 0,
    candidate_seat0_score: float = 0.62,
    candidate_seat1_score: float = 0.62,
    native_seat0_score: float = 0.60,
    native_seat1_score: float = 0.60,
    native_pair_id: str = NATIVE.pair_id,
    protocol_sha256: str = "d" * 64,
) -> EvaluationSummaryV1:
    if candidate_seat0_score == 0.62 and candidate_seat1_score == 0.62:
        candidate_seat0_score = candidate_seat1_score = candidate_score
    if native_seat0_score == 0.60 and native_seat1_score == 0.60:
        native_seat0_score = native_seat1_score = native_score
    candidate_deck = "1" * 64 if candidate_id == "candidate-a" else "4" * 64
    def _records(prefix: str, seat0_target: float, seat1_target: float, faults: int) -> tuple[dict[str, object], ...]:
        records: list[dict[str, object]] = []
        for seat, target, count in ((0, seat0_target, stage_games // 2), (1, seat1_target, stage_games - stage_games // 2)):
            wins = max(0, min(count, int(round(target * count))))
            for index in range(count):
                game_number = seat * 10000 + index
                records.append(
                    {
                        "game_id": f"g-{stage_games}-{game_number}",
                        "seed": f"seed-{stage_games}-{game_number}",
                        "opponent_id": f"opp-{index % 4}",
                        "family": f"family-{index % 2}",
                        "seat": seat,
                        "outcome": "win" if index < wins else "loss",
                        "fault": bool(faults > 0 and len(records) < faults),
                    }
                )
        return tuple(records)

    candidate_records = _records("candidate", candidate_seat0_score, candidate_seat1_score, candidate_fault_count)
    native_records = _records("native", native_seat0_score, native_seat1_score, native_fault_count)
    def _score(records):
        return sum({"win": 1.0, "draw": 0.5, "loss": 0.0}[row["outcome"]] for row in records) / len(records)
    def _seat_score(records, seat):
        subset = [row for row in records if row["seat"] == seat]
        return _score(subset)
    metadata = {
        "game_ids": sorted(row["game_id"] for row in candidate_records),
        "seeds": sorted(row["seed"] for row in candidate_records),
        "strata": [
            {key: row[key] for key in ("game_id", "seed", "opponent_id", "family", "seat")}
            for row in sorted(candidate_records, key=lambda row: row["game_id"])
        ],
    }
    def _semantic(schema, value):
        return hashlib.sha256(schema.encode() + b"\0" + json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    native_score_value = _score(native_records)
    native_control_artifact_sha256 = alternating.derive_native_control_artifact_sha256(
        stage_games=stage_games,
        native_pair_id=native_pair_id,
        native_policy_sha256=NATIVE.policy_sha256,
        native_deck_sha256=NATIVE.deck_sha256,
        native_evaluator_sha256=NATIVE.evaluator_sha256,
        protocol_sha256=protocol_sha256,
        game_id_universe_sha256=_semantic("mage-ptcg:common24-game-id-universe:v1", {"game_ids": metadata["game_ids"]}),
        seed_universe_sha256=_semantic("mage-ptcg:common24-seed-universe:v1", {"seeds": metadata["seeds"]}),
        strata_sha256=_semantic("mage-ptcg:common24-strata:v1", {"strata": metadata["strata"]}),
        native_game_records=native_records,
    )
    native_control_block_sha256 = alternating.derive_native_control_block_sha256(
        stage_games=stage_games,
        native_score=native_score_value,
        native_control_artifact_sha256=native_control_artifact_sha256,
        protocol_sha256=protocol_sha256,
        game_id_universe_sha256=_semantic("mage-ptcg:common24-game-id-universe:v1", {"game_ids": metadata["game_ids"]}),
        seed_universe_sha256=_semantic("mage-ptcg:common24-seed-universe:v1", {"seeds": metadata["seeds"]}),
        strata_sha256=_semantic("mage-ptcg:common24-strata:v1", {"strata": metadata["strata"]}),
    )
    return EvaluationSummaryV1(
        candidate_id=candidate_id,
        native_pair_id=native_pair_id,
        stage_games=stage_games,
        protocol_sha256=protocol_sha256,
        candidate_score=_score(candidate_records),
        native_score=native_score_value,
        candidate_fault_count=candidate_fault_count,
        native_fault_count=native_fault_count,
        candidate_seat0_games=stage_games // 2,
        candidate_seat1_games=stage_games - stage_games // 2,
        candidate_seat0_score=_seat_score(candidate_records, 0),
        candidate_seat1_score=_seat_score(candidate_records, 1),
        native_seat0_games=stage_games // 2,
        native_seat1_games=stage_games - stage_games // 2,
        native_seat0_score=_seat_score(native_records, 0),
        native_seat1_score=_seat_score(native_records, 1),
        candidate_policy_sha256="2" * 64,
        candidate_deck_sha256=candidate_deck,
        candidate_evaluator_sha256=NATIVE.evaluator_sha256,
        native_policy_sha256=NATIVE.policy_sha256,
        native_deck_sha256=NATIVE.deck_sha256,
        native_evaluator_sha256=NATIVE.evaluator_sha256,
        native_control_artifact_sha256=native_control_artifact_sha256,
        native_control_block_sha256=native_control_block_sha256,
        candidate_game_records=candidate_records,
        native_game_records=native_records,
        game_id_universe_sha256=_semantic("mage-ptcg:common24-game-id-universe:v1", {"game_ids": metadata["game_ids"]}),
        seed_universe_sha256=_semantic("mage-ptcg:common24-seed-universe:v1", {"seeds": metadata["seeds"]}),
        strata_sha256=_semantic("mage-ptcg:common24-strata:v1", {"strata": metadata["strata"]}),
    )


def test_phase_invariants_keep_the_fixed_dimension_immutable(tmp_path: Path) -> None:
    iteration, schedule, state = _state(tmp_path)
    journal = build_native_regression_journal_v1(state)
    switched = advance_native_meta_overfit_state_v1(
        state,
        iteration_manifest_path=iteration,
        meta_schedule_path=schedule,
        phase=DECK_FIXED_LONG_V1,
        policy_config_sha256="3" * 64,
        regression_journal=journal,
    )
    assert switched.phase == DECK_FIXED_LONG_V1
    assert switched.deck_sha256 == state.deck_sha256
    with pytest.raises(NativeMetaOverfitAlternatingError, match="policy-fixed"):
        advance_native_meta_overfit_state_v1(
            state,
            iteration_manifest_path=iteration,
            meta_schedule_path=schedule,
            policy_config_sha256="3" * 64,
            regression_journal=build_native_regression_journal_v1(state),
        )
    with pytest.raises(NativeMetaOverfitAlternatingError, match="deck-fixed"):
        advance_native_meta_overfit_state_v1(
            switched,
            iteration_manifest_path=iteration,
            meta_schedule_path=schedule,
            deck_sha256="4" * 64,
            regression_journal=build_native_regression_journal_v1(switched),
        )


def test_exact_stage_sequence_requires_candidate_and_native_summary(tmp_path: Path) -> None:
    iteration, schedule, state = _state(tmp_path)
    assert EXACT_STAGE_GAMES_V1 == (96, 384, 768, 1536)
    journal = build_native_regression_journal_v1(state)
    next_state = advance_native_meta_overfit_state_v1(
        state,
        iteration_manifest_path=iteration,
        meta_schedule_path=schedule,
        evaluation_summary=_summary(),
        next_stage_games=384,
        regression_journal=journal,
    )
    assert next_state.stage_games == 384
    with pytest.raises(NativeMetaOverfitAlternatingError, match="next successive-halving"):
        advance_native_meta_overfit_state_v1(
            state,
            iteration_manifest_path=iteration,
            meta_schedule_path=schedule,
            evaluation_summary=_summary(),
            next_stage_games=768,
            regression_journal=build_native_regression_journal_v1(state),
        )
    with pytest.raises(NativeMetaOverfitAlternatingError, match="native_pair_id"):
        evaluate_native_meta_overfit_stage_v1(
            _summary(native_pair_id="other-native"),
            state,
            iteration_manifest_path=iteration,
        )


def test_seat_and_fault_gate_is_fail_closed(tmp_path: Path) -> None:
    iteration, _, state = _state(tmp_path)
    with pytest.raises(NativeMetaOverfitAlternatingError, match="fault"):
        evaluate_native_meta_overfit_stage_v1(
            _summary(candidate_fault_count=1), state, iteration_manifest_path=iteration
        )
    with pytest.raises(NativeMetaOverfitAlternatingError, match="seat"):
        evaluate_native_meta_overfit_stage_v1(
            _summary(candidate_seat0_score=0.90, candidate_seat1_score=0.20),
            state,
            iteration_manifest_path=iteration,
            max_seat_gap=0.05,
        )
    with pytest.raises(NativeMetaOverfitAlternatingError, match="fault count"):
        _summary(candidate_fault_count=97)


def test_protocol_and_iteration_sha_mismatch_are_rejected(tmp_path: Path) -> None:
    iteration, _, state = _state(tmp_path)
    bad_protocol = _summary(protocol_sha256="9" * 64)
    with pytest.raises(NativeMetaOverfitAlternatingError, match="protocol SHA"):
        evaluate_native_meta_overfit_stage_v1(
            bad_protocol, state, iteration_manifest_path=iteration
        )
    payload = json.loads(iteration.read_text())
    payload["seed"] = "tampered-seed"
    _write_canonical(iteration, payload)
    with pytest.raises(NativeMetaOverfitAlternatingError, match="semantic SHA"):
        evaluate_native_meta_overfit_stage_v1(
            _summary(), state, iteration_manifest_path=iteration
        )


def test_successive_halving_requires_all_candidate_native_pairs(tmp_path: Path) -> None:
    iteration, schedule, state = _state(tmp_path)
    sibling = build_native_meta_overfit_state_v1(
        iteration_manifest_path=iteration,
        meta_schedule_path=schedule,
        candidate_id="candidate-b",
        deck_sha256="4" * 64,
        policy_config_sha256="2" * 64,
        native_baseline=NATIVE,
    )
    promoted = promote_native_meta_overfit_successive_halving_v1(
        (state, sibling),
        {"candidate-a": _summary(candidate_score=0.70), "candidate-b": _summary(candidate_id="candidate-b", candidate_score=0.61)},
        iteration_manifest_path=iteration,
        next_stage_games=384,
        regression_journals={
            "candidate-a": build_native_regression_journal_v1(state),
            "candidate-b": build_native_regression_journal_v1(sibling),
        },
    )
    assert [item.candidate_id for item in promoted] == ["candidate-a"]
    assert promoted[0].stage_games == 384
    with pytest.raises(NativeMetaOverfitAlternatingError, match="summary"):
        promote_native_meta_overfit_successive_halving_v1(
            (state, sibling),
            {"candidate-a": _summary()},
            iteration_manifest_path=iteration,
            next_stage_games=384,
            regression_journals={
                "candidate-a": build_native_regression_journal_v1(state),
                "candidate-b": build_native_regression_journal_v1(sibling),
            },
        )


def test_successive_halving_rejects_native_control_score_or_artifact_mismatch(tmp_path: Path) -> None:
    iteration, _, state = _state(tmp_path)
    sibling = build_native_meta_overfit_state_v1(
        iteration_manifest_path=iteration,
        meta_schedule_path=tmp_path / "schedule.json",
        candidate_id="candidate-b",
        deck_sha256="4" * 64,
        policy_config_sha256="2" * 64,
        native_baseline=NATIVE,
    )
    with pytest.raises(NativeMetaOverfitAlternatingError, match="native control"):
        promote_native_meta_overfit_successive_halving_v1(
            (state, sibling),
            {
                "candidate-a": _summary(candidate_score=0.70),
                "candidate-b": _summary(
                    candidate_id="candidate-b",
                    candidate_score=0.61,
                    native_score=0.90,
                    native_seat0_score=0.90,
                    native_seat1_score=0.90,
                ),
            },
            iteration_manifest_path=iteration,
            next_stage_games=384,
            regression_journals={
                "candidate-a": build_native_regression_journal_v1(state),
                "candidate-b": build_native_regression_journal_v1(sibling),
            },
        )


def test_summary_rejects_forged_native_control_block_sha(tmp_path: Path) -> None:
    payload = _summary().to_dict()
    payload["native_control_block_sha256"] = "9" * 64
    with pytest.raises(NativeMetaOverfitAlternatingError, match="control block"):
        EvaluationSummaryV1.from_dict(payload)


def test_summary_rejects_forged_native_control_artifact_sha(tmp_path: Path) -> None:
    payload = _summary().to_dict()
    payload["native_control_artifact_sha256"] = "8" * 64
    with pytest.raises(NativeMetaOverfitAlternatingError, match="control artifact"):
        EvaluationSummaryV1.from_dict(payload)


def test_unproven_native_baseline_is_rejected_before_state_binding(tmp_path: Path) -> None:
    iteration, schedule, _ = _state(tmp_path)
    unproven = NativeBaselineArmV1(
        pair_id=NATIVE.pair_id,
        deck_sha256=NATIVE.deck_sha256,
        policy_sha256=NATIVE.policy_sha256,
        evaluator_sha256=NATIVE.evaluator_sha256,
        status="UNPROVEN",
    )
    with pytest.raises(NativeMetaOverfitAlternatingError, match="PROVEN"):
        build_native_meta_overfit_state_v1(
            iteration_manifest_path=iteration,
            meta_schedule_path=schedule,
            candidate_id="candidate-a",
            deck_sha256="1" * 64,
            policy_config_sha256="2" * 64,
            native_baseline=unproven,
        )


def test_native_regression_stops_after_two_consecutive_results(tmp_path: Path) -> None:
    iteration, _, state = _state(tmp_path)
    first = evaluate_native_meta_overfit_stage_v1(
        _summary(candidate_score=0.50, native_score=0.60),
        state,
        iteration_manifest_path=iteration,
        previous_native_regressions=0,
    )
    assert first["consecutive_native_regressions"] == 1
    assert first["stop_after_two"] is False
    second = evaluate_native_meta_overfit_stage_v1(
        _summary(candidate_score=0.49, native_score=0.60),
        state,
        iteration_manifest_path=iteration,
        previous_native_regressions=first["consecutive_native_regressions"],
    )
    assert second["consecutive_native_regressions"] == 2
    assert second["stop_after_two"] is True
    assert second["rollback_required"] is True


def test_checkpoint_sha_mismatch_rejects_rollback_descriptor(tmp_path: Path) -> None:
    iteration, _, state = _state(tmp_path)
    checkpoint = tmp_path / "candidate.ckpt"
    checkpoint.write_bytes(b"checkpoint-v1")
    descriptor = build_rollback_descriptor_v1(
        state=state,
        checkpoint_path=checkpoint,
        iteration_manifest_path=iteration,
        reason="two native regressions",
        consecutive_native_regressions=2,
    )
    assert verify_rollback_descriptor_v1(
        descriptor, state=state, checkpoint_path=checkpoint, iteration_manifest_path=iteration
    )["rollback_required"] is True
    checkpoint.write_bytes(b"tampered")
    with pytest.raises(NativeMetaOverfitAlternatingError, match="checkpoint SHA"):
        verify_rollback_descriptor_v1(
            descriptor, state=state, checkpoint_path=checkpoint, iteration_manifest_path=iteration
        )
    assert json.loads(json.dumps(descriptor, sort_keys=True))["research_only"] is True


def test_rollback_descriptor_requires_two_regressions_and_canonical_reason(tmp_path: Path) -> None:
    iteration, _, state = _state(tmp_path)
    checkpoint = tmp_path / "candidate.ckpt"
    checkpoint.write_bytes(b"checkpoint-v1")
    base = build_rollback_descriptor_v1(
        state=state,
        checkpoint_path=checkpoint,
        iteration_manifest_path=iteration,
        reason="two native regressions",
        consecutive_native_regressions=2,
    )
    with pytest.raises(NativeMetaOverfitAlternatingError, match="regression"):
        alternating.RollbackDescriptorV1(
            **{**base, "reason": "arbitrary", "consecutive_native_regressions": 0}
        )


def test_mutated_rollback_descriptor_authority_is_rejected_on_verify(tmp_path: Path) -> None:
    iteration, _, state = _state(tmp_path)
    checkpoint = tmp_path / "candidate.ckpt"
    checkpoint.write_bytes(b"checkpoint-v1")
    base = build_rollback_descriptor_v1(
        state=state,
        checkpoint_path=checkpoint,
        iteration_manifest_path=iteration,
        reason="two native regressions",
        consecutive_native_regressions=2,
    )
    descriptor = alternating.RollbackDescriptorV1(**base)
    dict.__setitem__(descriptor.authority, "execute_allowed", True)
    with pytest.raises(NativeMetaOverfitAlternatingError, match="authority"):
        verify_rollback_descriptor_v1(
            descriptor,
            state=state,
            checkpoint_path=checkpoint,
            iteration_manifest_path=iteration,
        )


def test_iteration_and_public_table_authority_are_bound(tmp_path: Path) -> None:
    iteration, schedule, _ = _state(tmp_path)
    payload = json.loads(iteration.read_text())
    payload["authority"]["promotion_authority"] = True
    _write_canonical(iteration, payload)
    with pytest.raises(NativeMetaOverfitAlternatingError, match="authority"):
        build_native_meta_overfit_state_v1(
            iteration_manifest_path=iteration,
            meta_schedule_path=schedule,
            candidate_id="candidate-a",
            deck_sha256="1" * 64,
            policy_config_sha256="2" * 64,
            native_baseline=NATIVE,
        )


def test_iteration_gate_status_cannot_be_promoted_or_skip_verification(tmp_path: Path) -> None:
    iteration, schedule, _ = _state(tmp_path)
    payload = json.loads(iteration.read_text())
    payload["gate_status"]["performance_gate"] = True
    payload["iteration_sha256"] = hashlib.sha256(
        payload["schema_version"].encode("ascii")
        + b"\0"
        + json.dumps(
            {key: value for key, value in payload.items() if key != "iteration_sha256"},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    _write_canonical(iteration, payload)
    with pytest.raises(NativeMetaOverfitAlternatingError, match="must remain false"):
        build_native_meta_overfit_state_v1(
            iteration_manifest_path=iteration,
            meta_schedule_path=schedule,
            candidate_id="candidate-a",
            deck_sha256="1" * 64,
            policy_config_sha256="2" * 64,
            native_baseline=NATIVE,
        )


def test_deck_mutation_candidate_must_match_policy_fixed_state(tmp_path: Path) -> None:
    iteration, schedule, state = _state(tmp_path, deck_sha="9" * 64)
    base = tuple([101] * 4 + [102] * 2 + [200] * 4 + list(range(300, 350)))
    signature = CoreSignatureV1(archetype_id="archaludon", required_counts={101: 4, 102: 2})
    candidate = generate_deck_mutation_candidates_v1(
        base_cards=base, signature=signature, replacement_pool=tuple(range(400, 420)), swap_counts=(1,), candidates_per_swap=1, seed=17
    )[0]
    mismatched = build_native_meta_overfit_state_v1(
        iteration_manifest_path=iteration,
        meta_schedule_path=schedule,
        candidate_id="candidate-b",
        deck_sha256=candidate.deck_multiset_sha256,
        policy_config_sha256="2" * 64,
        native_baseline=NATIVE,
        deck_mutation_candidate=candidate,
    )
    assert mismatched.deck_sha256 == candidate.deck_multiset_sha256
    with pytest.raises(NativeMetaOverfitAlternatingError, match="deck mutation"):
        build_native_meta_overfit_state_v1(
            iteration_manifest_path=iteration,
            meta_schedule_path=schedule,
            candidate_id="candidate-c",
            deck_sha256="9" * 64,
            policy_config_sha256="2" * 64,
            native_baseline=NATIVE,
            deck_mutation_candidate=candidate,
        )


def test_advance_carries_native_regression_count_in_bound_journal(tmp_path: Path) -> None:
    iteration, schedule, state = _state(tmp_path)
    journal = build_native_regression_journal_v1(state)
    first = advance_native_meta_overfit_state_v1(
        state,
        iteration_manifest_path=iteration,
        meta_schedule_path=schedule,
        evaluation_summary=_summary(candidate_score=0.50, native_score=0.60),
        next_stage_games=384,
        regression_journal=journal,
    )
    assert journal.consecutive_native_regressions == 1
    second_summary = _summary(stage_games=384, candidate_score=0.49, native_score=0.60)
    with pytest.raises(NativeMetaOverfitAlternatingError, match="stop-after-two"):
        advance_native_meta_overfit_state_v1(
            first,
            iteration_manifest_path=iteration,
            meta_schedule_path=schedule,
            evaluation_summary=second_summary,
            next_stage_games=768,
            regression_journal=journal,
        )
    assert journal.consecutive_native_regressions == 2
    assert journal.state_sha256 == hashlib.sha256(
        b"mage-ptcg:alternating-candidate-state:v1\0"
        + json.dumps(first.to_dict(), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def test_advance_rejects_missing_regression_journal(tmp_path: Path) -> None:
    iteration, schedule, state = _state(tmp_path)
    with pytest.raises(NativeMetaOverfitAlternatingError, match="regression journal is required"):
        advance_native_meta_overfit_state_v1(
            state,
            iteration_manifest_path=iteration,
            meta_schedule_path=schedule,
            evaluation_summary=_summary(candidate_score=0.50, native_score=0.60),
            next_stage_games=384,
            regression_journal=None,
        )


def test_regression_journal_roundtrip_preserves_candidate_and_lineage(tmp_path: Path) -> None:
    _, _, state = _state(tmp_path)
    journal = build_native_regression_journal_v1(state)
    restored = NativeRegressionJournalV1.from_dict(journal.to_dict())
    assert restored.to_dict() == journal.to_dict()
    assert restored.candidate_id == state.candidate_id
    assert restored.state_sha256 == journal.state_sha256
    assert restored.content_sha256 == journal.content_sha256


def test_mutated_regression_journal_authority_or_count_is_rejected(tmp_path: Path) -> None:
    iteration, schedule, state = _state(tmp_path)
    journal = build_native_regression_journal_v1(state)
    with pytest.raises(TypeError, match="immutable"):
        journal.authority["execute_allowed"] = True

    journal = build_native_regression_journal_v1(state)
    with pytest.raises(AttributeError):
        journal.consecutive_native_regressions = 1

    journal = build_native_regression_journal_v1(state)
    first = advance_native_meta_overfit_state_v1(
        state,
        iteration_manifest_path=iteration,
        meta_schedule_path=schedule,
        evaluation_summary=_summary(candidate_score=0.50, native_score=0.60),
        next_stage_games=384,
        regression_journal=journal,
    )
    object.__setattr__(journal, "consecutive_native_regressions", 0)
    with pytest.raises(NativeMetaOverfitAlternatingError, match="seal"):
        advance_native_meta_overfit_state_v1(
            first,
            iteration_manifest_path=iteration,
            meta_schedule_path=schedule,
            regression_journal=journal,
        )


def test_regression_journal_bind_rejects_forged_count_reset(tmp_path: Path) -> None:
    iteration, _, state = _state(tmp_path)
    journal = build_native_regression_journal_v1(state)
    summary = _summary(candidate_score=0.50, native_score=0.60)
    decision = evaluate_native_meta_overfit_stage_v1(
        summary,
        state,
        iteration_manifest_path=iteration,
    )
    decision["consecutive_native_regressions"] = 0
    decision["stop_after_two"] = False
    decision["rollback_required"] = False
    with pytest.raises(NativeMetaOverfitAlternatingError, match="regression count"):
        journal._bind(state=state, summary=summary, decision=decision)


def test_regression_journal_bind_rejects_cross_candidate_summary(tmp_path: Path) -> None:
    iteration, _, state = _state(tmp_path)
    journal = build_native_regression_journal_v1(state)
    summary = _summary(candidate_id="candidate-b")
    decision = evaluate_native_meta_overfit_stage_v1(
        _summary(), state, iteration_manifest_path=iteration
    )
    with pytest.raises(NativeMetaOverfitAlternatingError, match="candidate_id"):
        journal._bind(state=state, summary=summary, decision=decision)


def test_regression_journal_bind_rejects_cross_stage_summary(tmp_path: Path) -> None:
    iteration, schedule, state = _state(tmp_path)
    journal = build_native_regression_journal_v1(state)
    later = advance_candidate_state_v1(
        state, next_stage_games=384, candidate_score=0.62, native_score=0.60
    )
    summary = _summary(stage_games=384)
    decision = evaluate_native_meta_overfit_stage_v1(
        summary, later, iteration_manifest_path=iteration
    )
    with pytest.raises(NativeMetaOverfitAlternatingError, match="stage_games"):
        journal._bind(state=state, summary=summary, decision=decision)


def test_public_journal_bind_is_not_an_authority_surface(tmp_path: Path) -> None:
    iteration, _, state = _state(tmp_path)
    journal = build_native_regression_journal_v1(state)
    assert not hasattr(journal, "bind")
    with pytest.raises(AttributeError):
        journal.bind(
            state=state,
            summary=_summary(protocol_sha256="9" * 64),
            decision={"consecutive_native_regressions": 0},
        )


def test_regression_journal_rebind_rejects_arbitrary_later_state(tmp_path: Path) -> None:
    iteration, _, state = _state(tmp_path)
    journal = build_native_regression_journal_v1(state)
    forged = advance_candidate_state_v1(
        state, next_stage_games=384, candidate_score=0.62, native_score=0.60
    )
    forged = advance_candidate_state_v1(
        forged, next_stage_games=768, candidate_score=0.62, native_score=0.60
    )
    with pytest.raises(NativeMetaOverfitAlternatingError, match="revision|state"):
        journal.rebind_state(state=forged)


def test_successive_halving_rejects_missing_regression_journals(tmp_path: Path) -> None:
    iteration, schedule, state = _state(tmp_path)
    sibling = build_native_meta_overfit_state_v1(
        iteration_manifest_path=iteration,
        meta_schedule_path=schedule,
        candidate_id="candidate-b",
        deck_sha256="4" * 64,
        policy_config_sha256="2" * 64,
        native_baseline=NATIVE,
    )
    with pytest.raises(NativeMetaOverfitAlternatingError, match="regression journals are required"):
        promote_native_meta_overfit_successive_halving_v1(
            (state, sibling),
            {
                "candidate-a": _summary(candidate_score=0.70),
                "candidate-b": _summary(candidate_id="candidate-b", candidate_score=0.61),
            },
            iteration_manifest_path=iteration,
            next_stage_games=384,
            regression_journals=None,
        )


def test_task2_verifier_is_required_before_iteration_binding(tmp_path: Path, monkeypatch) -> None:
    iteration, schedule, _ = _state(tmp_path)
    called = {"value": False}

    def strict_fail(*_args, **_kwargs):
        called["value"] = True
        raise NativeMetaOverfitAlternatingError("strict Task2 verifier was not bypassable")

    monkeypatch.setattr(
        "mage_ptcg.meta_specialist.native_meta_overfit_alternating_v1.verify_native_meta_overfit_iteration_v1",
        strict_fail,
        raising=False,
    )
    with pytest.raises(NativeMetaOverfitAlternatingError, match="strict Task2 verifier"):
        build_native_meta_overfit_state_v1(
            iteration_manifest_path=iteration,
            meta_schedule_path=schedule,
            candidate_id="candidate-a",
            deck_sha256="1" * 64,
            policy_config_sha256="2" * 64,
            native_baseline=NATIVE,
        )
    assert called["value"] is True


def test_iteration_source_rehash_rejects_tampered_bound_source(tmp_path: Path) -> None:
    iteration, schedule, _ = _state(tmp_path)
    source = tmp_path / "fixture-source.json"
    source.write_text("tampered", encoding="utf-8")
    with pytest.raises(NativeMetaOverfitAlternatingError, match="source SHA"):
        build_native_meta_overfit_state_v1(
            iteration_manifest_path=iteration,
            meta_schedule_path=schedule,
            candidate_id="candidate-a",
            deck_sha256="1" * 64,
            policy_config_sha256="2" * 64,
            native_baseline=NATIVE,
        )


def test_summary_rejects_forged_aggregate_even_when_seat_scores_are_unchanged(tmp_path: Path) -> None:
    summary = _summary()
    payload = summary.to_dict()
    payload["candidate_score"] = 1.0
    payload["candidate_delta"] = 1.0 - summary.native_score
    with pytest.raises(NativeMetaOverfitAlternatingError, match="aggregate|derived|record"):
        EvaluationSummaryV1.from_dict(payload)


def test_summary_rejects_candidate_native_strata_mismatch(tmp_path: Path) -> None:
    summary = _summary()
    native_records = list(summary.native_game_records)
    native_records[0] = dict(native_records[0])
    native_records[0]["family"] = "forged-family"
    with pytest.raises(NativeMetaOverfitAlternatingError, match="strata"):
        EvaluationSummaryV1(
            **{
                **{field: getattr(summary, field) for field in summary.__dataclass_fields__},
                "native_game_records": tuple(native_records),
            }
        )


def test_summary_record_mutation_is_rejected_at_evaluation_entry(tmp_path: Path) -> None:
    iteration, _, state = _state(tmp_path)
    summary = _summary()
    # Bypass a future immutable mapping only to model an adversarial in-memory
    # mutation of a record that was already stored and hashed by the summary.
    dict.__setitem__(summary.candidate_game_records[0], "fault", True)
    with pytest.raises(NativeMetaOverfitAlternatingError, match="derived|record|control"):
        evaluate_native_meta_overfit_stage_v1(
            summary,
            state,
            iteration_manifest_path=iteration,
        )
