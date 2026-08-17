from __future__ import annotations

from dataclasses import asdict, replace
import json
from pathlib import Path

import pytest

from mage_ptcg.meta_specialist.evaluation_protocol_v2 import (
    IndependentEvaluationRecordV2,
    IndependentEvaluationRecordV3,
    PairedEvaluationRecordV2,
    evaluation_inference_allowed_v2,
    independent_readiness_summary_v3,
    independent_stratified_summary_v2,
    validate_evidence_attestation_v2,
    paired_summary_from_records_v2,
    paired_summary_v2,
    wilson_interval_v2,
)


def test_wilson_interval_and_paired_summary_are_bounded() -> None:
    low, high = wilson_interval_v2(5, 10)
    assert 0 <= low <= 0.5 <= high <= 1
    summary = paired_summary_v2([1, 0, 1, 1], [0, 1, 0, 0], seed=3, bootstrap_samples=100)
    assert summary["candidate_wins"] == 3
    assert summary["baseline_wins"] == 1
    assert summary["paired_delta"] == 0.5
    assert summary["bootstrap_ci_low"] <= summary["paired_delta"] <= summary["bootstrap_ci_high"]


def _record(*, candidate_outcome: str, baseline_outcome: str, seat: int = 0,
            family: str = "stage2", candidate_ledger: str = "ledger-a",
            baseline_ledger: str = "ledger-a") -> PairedEvaluationRecordV2:
    return PairedEvaluationRecordV2(
        candidate_outcome=candidate_outcome, baseline_outcome=baseline_outcome,
        candidate_ledger_identity=candidate_ledger, baseline_ledger_identity=baseline_ledger,
        candidate_record_hash="a" * 64, baseline_record_hash="b" * 64,
        candidate_state_hash_sequence=("c" * 64,), baseline_state_hash_sequence=("c" * 64,),
        candidate_action_sequence=((1,),), baseline_action_sequence=((1,),),
        seat=seat, opponent_family=family,
    )


def test_paired_records_reject_mismatched_ledgers_before_bootstrap() -> None:
    with pytest.raises(ValueError, match="ledger identity"):
        paired_summary_from_records_v2([
            _record(candidate_outcome="win", baseline_outcome="loss", baseline_ledger="ledger-b"),
        ], seed=3, bootstrap_samples=10)


def test_paired_records_account_for_all_outcomes_and_bootstrap_complete_pairs_only() -> None:
    summary = paired_summary_from_records_v2([
        _record(candidate_outcome="win", baseline_outcome="loss", seat=0, family="stage2"),
        _record(candidate_outcome="loss", baseline_outcome="win", seat=1, family="basic"),
        _record(candidate_outcome="draw", baseline_outcome="draw", seat=1, family="basic"),
        _record(candidate_outcome="fault", baseline_outcome="win", seat=0, family="stage2"),
    ], seed=3, bootstrap_samples=100)

    assert summary["complete_pairs"] == 3
    assert summary["candidate"]["wins"] == 1
    assert summary["candidate"]["losses"] == 1
    assert summary["candidate"]["draws"] == 1
    assert summary["candidate"]["faults"] == 1
    assert summary["by_seat"]["0"]["candidate"]["faults"] == 1
    assert summary["by_opponent_family"]["basic"]["candidate"]["draws"] == 1
    assert summary["candidate_rate_intervals"]["fault"][0] <= 0.25 <= summary["candidate_rate_intervals"]["fault"][1]


def _independent(*, arm: str, outcome: str, seat: int, family: str,
                 capability: bool = False, verified: bool = False) -> IndependentEvaluationRecordV2:
    return IndependentEvaluationRecordV2(
        arm=arm, outcome=outcome, seat=seat, opponent_family=family,
        canonical_game_identity={
            "opponent_id": "opp", "opponent_policy_version": "b" * 64,
            "opponent_deck_fingerprint": "c" * 64, "seat": seat,
            "environment_seed": 1, "agent_sampling_seed": 2, "retry_index": 0,
        },
        record_hash="a" * 64, engine_seed_supported=capability,
        replay_verified=verified, run_attestation="run-attested", seed_attestation="seed-attested",
        fault_provenance=None if outcome != "fault" else {"exception_class": "RuntimeError"},
    )


def test_capability_gate_refuses_paired_or_promotion_inference_without_verified_engine_replay() -> None:
    with pytest.raises(ValueError, match="independent stratified"):
        evaluation_inference_allowed_v2(engine_seed_supported=False, replay_verified=False)
    assert evaluation_inference_allowed_v2(engine_seed_supported=True, replay_verified=True) == "paired"


@pytest.mark.parametrize("forged_capability", (1, "true", object()))
def test_capability_gate_requires_exact_boolean_capabilities(forged_capability: object) -> None:
    with pytest.raises(ValueError, match="JSON booleans"):
        evaluation_inference_allowed_v2(
            engine_seed_supported=forged_capability, replay_verified=True  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="JSON booleans"):
        evaluation_inference_allowed_v2(
            engine_seed_supported=True, replay_verified=forged_capability  # type: ignore[arg-type]
        )


def test_actual_cabt_runner_has_no_seed_configuration_and_keeps_paired_inference_gated(tmp_path) -> None:
    """Inspect the real CABT runner call shape; this is not a fake RNG replay claim."""
    from mage_ptcg.opponents.trajectory import ENGINE_SEED_UNSUPPORTED, determine_engine_seed_capability
    from scripts.test_sim import run_match

    observed_configurations = []
    def rejecting_make(_name, *, configuration):
        observed_configurations.append(configuration)
        raise RuntimeError("stop after observing CABT configuration")

    deck = Path(__file__).resolve().parents[2] / "deck.csv"
    with pytest.raises(Exception, match="stop after observing CABT configuration"):
        run_match(
            deck_a_path=deck, deck_b_path=deck, agent_a_name="rule", agent_b_name="rule", seed=9,
            max_steps=1, output_dir=tmp_path, save_html=False, save_result=False,
            make_environment=rejecting_make,
        )
    assert observed_configurations and "seed" not in observed_configurations[0]
    assert determine_engine_seed_capability(observed_configurations[0]) == ENGINE_SEED_UNSUPPORTED
    with pytest.raises(ValueError, match="independent stratified"):
        evaluation_inference_allowed_v2(engine_seed_supported=False, replay_verified=False)


def test_independent_stratified_summary_reports_arm_rates_intervals_and_weighted_difference() -> None:
    records = [
        _independent(arm="candidate", outcome="win", seat=0, family="basic"),
        _independent(arm="candidate", outcome="draw", seat=0, family="basic"),
        _independent(arm="baseline", outcome="loss", seat=0, family="basic"),
        _independent(arm="baseline", outcome="fault", seat=0, family="basic"),
        _independent(arm="candidate", outcome="loss", seat=1, family="stage2"),
        _independent(arm="baseline", outcome="win", seat=1, family="stage2"),
    ]
    summary = independent_stratified_summary_v2(records, seed=7, bootstrap_samples=100)

    basic = summary["strata"]["seat=0|family=basic"]
    assert basic["candidate"]["denominator"] == 2
    assert basic["candidate"]["rates"]["win"] == 0.5
    assert basic["baseline"]["rates"]["fault"] == 0.5
    assert basic["candidate"]["intervals"]["draw"][0] <= 0.5 <= basic["candidate"]["intervals"]["draw"][1]
    assert summary["fixed_weight_difference"] == -0.25


@pytest.mark.parametrize("payload", [
    {"engine_seed_supported": "false", "replay_verified": False, "run_attestation": "run-attested", "seed_attestation": "seed-attested", "evidence_kind": "measured"},
    {"engine_seed_supported": False, "replay_verified": False, "run_attestation": "synthetic", "seed_attestation": "seed-attested", "evidence_kind": "measured"},
    {"engine_seed_supported": False, "replay_verified": False, "run_attestation": "", "seed_attestation": "seed-attested", "evidence_kind": "measured"},
    {"engine_seed_supported": False, "replay_verified": False, "run_attestation": "run-attested", "seed_attestation": "seed-attested", "evidence_kind": "unit-only"},
])
def test_evidence_attestation_rejects_forged_boolean_synthetic_and_unattested_payloads(payload) -> None:
    with pytest.raises(ValueError):
        validate_evidence_attestation_v2(payload)


def test_independent_record_rejects_opaque_identity_and_string_boolean() -> None:
    with pytest.raises(ValueError):
        IndependentEvaluationRecordV2(
            arm="candidate", outcome="win", seat=0, opponent_family="basic",
            canonical_game_identity={"game_key": "x"}, record_hash="a" * 64,
            engine_seed_supported="false", replay_verified=False,
            run_attestation="run-attested", seed_attestation="seed-attested", fault_provenance=None,
        )


def test_paired_and_independent_identity_reject_boolean_seat() -> None:
    with pytest.raises(ValueError):
        PairedEvaluationRecordV2(
            candidate_outcome="win", baseline_outcome="loss", candidate_ledger_identity="x",
            baseline_ledger_identity="x", candidate_record_hash="a" * 64, baseline_record_hash="b" * 64,
            candidate_state_hash_sequence=(), baseline_state_hash_sequence=(), candidate_action_sequence=(),
            baseline_action_sequence=(), seat=True, opponent_family="basic",
        )
    with pytest.raises(ValueError):
        _independent(arm="candidate", outcome="win", seat=True, family="basic")


def test_independent_record_rejects_boolean_seat_even_when_identity_uses_integer_one() -> None:
    """A record-level boolean must not silently alias canonical seat ``1``."""
    with pytest.raises(ValueError, match="seat"):
        IndependentEvaluationRecordV2(
            arm="candidate", outcome="win", seat=True, opponent_family="basic",
            canonical_game_identity={
                "opponent_id": "opp", "opponent_policy_version": "b" * 64,
                "opponent_deck_fingerprint": "c" * 64, "seat": 1,
                "environment_seed": 1, "agent_sampling_seed": 2, "retry_index": 0,
            },
            record_hash="a" * 64, engine_seed_supported=False, replay_verified=False,
            run_attestation="run-attested", seed_attestation="seed-attested", fault_provenance=None,
        )


def _readiness_v3_records() -> list[IndependentEvaluationRecordV3]:
    """A complete pre-registered 6 opponent × 2 seat × 8 attempt ledger."""
    theta0_sha256 = "d" * 64
    candidate_sha256 = "e" * 64
    records: list[IndependentEvaluationRecordV3] = []
    for policy_role, policy_hash, outcome in (
        ("theta0", theta0_sha256, "loss"),
        ("candidate", candidate_sha256, "win"),
    ):
        for opponent_index in range(6):
            for seat in (0, 1):
                for repetition in range(8):
                    records.append(
                        IndependentEvaluationRecordV3(
                            lane_id="alakazam",
                            training_seed=7,
                            policy_role=policy_role,
                            policy_artifact_sha256=policy_hash,
                            theta0_sha256=theta0_sha256,
                            repetition=repetition,
                            outcome=outcome,
                            seat=seat,
                            opponent_family="held_out",
                            canonical_game_identity={
                                "opponent_id": f"opponent-{opponent_index}",
                                "opponent_policy_version": "b" * 64,
                                "opponent_deck_fingerprint": "c" * 64,
                                "seat": seat,
                                "environment_seed": repetition,
                                "agent_sampling_seed": repetition + 100,
                                "retry_index": 0,
                            },
                            record_hash=(f"{opponent_index:x}" * 64)[:64],
                            engine_seed_supported=False,
                            replay_verified=False,
                            run_attestation="measured-run",
                            seed_attestation="measured-seed",
                            evidence_kind="measured",
                            fault_provenance=None,
                        )
                    )
    return records


@pytest.mark.parametrize("evidence_kind", ("synthetic", "unit-only"))
def test_readiness_v3_direct_record_rejects_non_measured_evidence(
    evidence_kind: str,
) -> None:
    payload = asdict(_readiness_v3_records()[0])
    payload["evidence_kind"] = evidence_kind
    with pytest.raises(ValueError, match="synthetic/unit-only"):
        IndependentEvaluationRecordV3(**payload)


def test_readiness_v3_summary_revalidates_record_evidence_kind() -> None:
    records = _readiness_v3_records()
    object.__setattr__(records[0], "evidence_kind", "synthetic")
    with pytest.raises(ValueError, match="synthetic/unit-only"):
        independent_readiness_summary_v3(records, bootstrap_replicates=10)


@pytest.mark.parametrize("invalid_provenance", ("forged", {}, {"exception_class": ""}))
def test_independent_fault_records_require_shaped_mapping_provenance(
    invalid_provenance: object,
) -> None:
    with pytest.raises(ValueError, match="fault provenance"):
        replace(
            _independent(arm="candidate", outcome="fault", seat=0, family="basic"),
            fault_provenance=invalid_provenance,
        )
    with pytest.raises(ValueError, match="fault provenance"):
        replace(
            _readiness_v3_records()[0], outcome="fault", fault_provenance=invalid_provenance,
        )


def test_readiness_v3_requires_a_complete_unique_fixed_attempt_ledger() -> None:
    records = _readiness_v3_records()
    summary = independent_readiness_summary_v3(
        records, bootstrap_seed=20260809, bootstrap_replicates=100
    )

    assert summary["macro_delta"] == 1.0
    assert summary["one_sided_95_lower"] == 1.0
    assert summary["cell_deltas"] == {"lane=alakazam|training_seed=7": 1.0}

    with pytest.raises(ValueError, match="duplicate"):
        independent_readiness_summary_v3(records + [records[0]], bootstrap_replicates=10)
    with pytest.raises(ValueError, match="complete"):
        independent_readiness_summary_v3(records[:-1], bootstrap_replicates=10)


def test_readiness_v3_requires_theta0_hash_and_counts_fault_as_candidate_loss() -> None:
    records = _readiness_v3_records()
    candidate_fault = records[-1]
    records[-1] = IndependentEvaluationRecordV3(
        lane_id=candidate_fault.lane_id,
        training_seed=candidate_fault.training_seed,
        policy_role=candidate_fault.policy_role,
        policy_artifact_sha256=candidate_fault.policy_artifact_sha256,
        theta0_sha256=candidate_fault.theta0_sha256,
        repetition=candidate_fault.repetition,
        outcome="fault",
        seat=candidate_fault.seat,
        opponent_family=candidate_fault.opponent_family,
        canonical_game_identity=candidate_fault.canonical_game_identity,
        record_hash=candidate_fault.record_hash,
        engine_seed_supported=candidate_fault.engine_seed_supported,
        replay_verified=candidate_fault.replay_verified,
        run_attestation=candidate_fault.run_attestation,
        seed_attestation=candidate_fault.seed_attestation,
        evidence_kind="measured",
        fault_provenance={"exception_class": "RuntimeError"},
    )
    summary = independent_readiness_summary_v3(records, bootstrap_replicates=100)
    assert summary["cell_deltas"]["lane=alakazam|training_seed=7"] == pytest.approx(95 / 96)
    assert summary["cells"]["lane=alakazam|training_seed=7"]["candidate"]["counts"]["fault"] == 1

    mismatched = list(records)
    mismatched[-1] = IndependentEvaluationRecordV3(
        lane_id=candidate_fault.lane_id,
        training_seed=candidate_fault.training_seed,
        policy_role=candidate_fault.policy_role,
        policy_artifact_sha256=candidate_fault.policy_artifact_sha256,
        theta0_sha256="f" * 64,
        repetition=candidate_fault.repetition,
        outcome="fault",
        seat=candidate_fault.seat,
        opponent_family=candidate_fault.opponent_family,
        canonical_game_identity=candidate_fault.canonical_game_identity,
        record_hash=candidate_fault.record_hash,
        engine_seed_supported=candidate_fault.engine_seed_supported,
        replay_verified=candidate_fault.replay_verified,
        run_attestation=candidate_fault.run_attestation,
        seed_attestation=candidate_fault.seed_attestation,
        evidence_kind="measured",
        fault_provenance={"exception_class": "RuntimeError"},
    )
    with pytest.raises(ValueError, match="theta0"):
        independent_readiness_summary_v3(mismatched, bootstrap_replicates=10)


def test_readiness_v3_cli_writes_only_an_independent_arm_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts.run_meta_specialist_v3_eval import main

    records_path = tmp_path / "records.json"
    output_path = tmp_path / "report.json"
    raw_records = [asdict(record) for record in _readiness_v3_records()]
    for row in raw_records:
        row["evidence_kind"] = "measured"
    records_path.write_text(json.dumps(raw_records), encoding="utf-8")
    monkeypatch.setattr(
        "sys.argv",
        [
            "run_meta_specialist_v3_eval.py",
            "--records", str(records_path),
            "--output", str(output_path),
            "--bootstrap-replicates", "100",
        ],
    )

    assert main() == 0
    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["schema"] == "meta-specialist-evaluation-v3-independent-readiness"
    assert report["promotion_gate"] == "NOT_APPLICABLE_INDEPENDENT_ARMS"
    assert report["macro_delta"] == 1.0


def test_readiness_v3_cli_rejects_raw_records_without_evidence_kind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts.run_meta_specialist_v3_eval import main

    records_path = tmp_path / "records.json"
    output_path = tmp_path / "report.json"
    raw_records = [asdict(record) for record in _readiness_v3_records()]
    for row in raw_records:
        row.pop("evidence_kind")
    records_path.write_text(
        json.dumps(raw_records),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "run_meta_specialist_v3_eval.py",
            "--records", str(records_path),
            "--output", str(output_path),
        ],
    )

    with pytest.raises(ValueError, match="required attestation fields"):
        main()
