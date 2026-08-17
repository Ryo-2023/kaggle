"""Focused O1-6 deterministic baseline tests."""

from __future__ import annotations

import pytest
from pathlib import Path

from mage_ptcg.competition_intelligence.benchmark import build_benchmark_manifest
from mage_ptcg.competition_intelligence.contracts import ContractError
from mage_ptcg.competition_intelligence.meta import ANALYSIS_GRANTED, WeightedStrategyObservation, build_meta_snapshot, detect_drift
from mage_ptcg.competition_intelligence.promotion_report import build_promotion_report
from mage_ptcg.competition_intelligence.surrogate import SurrogateObservation, build_opponent_surrogate, evaluate_surrogate


def _observation(identifier: str, posterior: dict[str, float], unknown: float, **kwargs: object) -> WeightedStrategyObservation:
    return WeightedStrategyObservation(observation_id=identifier, source_id="source", source_kind="LOCAL_SELFPLAY", episode_id=identifier,
        joint_fingerprint_id="joint", archetype_posterior=posterior, unknown_mass=unknown, timestamp="2026-07-18T00:00:00Z",
        source_weight=float(kwargs.get("source_weight", 1)), freshness_weight=float(kwargs.get("freshness_weight", 1)),
        duplicate_discount=float(kwargs.get("duplicate_discount", 1)), confidence=float(kwargs.get("confidence", 1)),
        population_bucket=None, lineage_version_group=None, permission_status=str(kwargs.get("permission_status", ANALYSIS_GRANTED)), analysis_version="v1")


class TestMeta:
    def test_prior_unknown_weight_and_determinism(self) -> None:
        rows = [_observation("a", {"A": 0.75}, 0.25), _observation("b", {"B": 1.0}, 0.0, confidence=0.5)]
        first = build_meta_snapshot(rows, cutoff_time="2026-07-18T01:00:00Z", prior={"A": 1, "B": 1, "unknown": 1})
        second = build_meta_snapshot(reversed(rows), cutoff_time="2026-07-18T01:00:00Z", prior={"A": 1, "B": 1, "unknown": 1})
        assert first.meta_snapshot_sha256 == second.meta_snapshot_sha256
        assert abs(sum(first.posterior_mean.values()) - 1) < 1e-9
        assert first.posterior_mean["unknown"] > 0

    def test_cutoff_permission_and_weights(self) -> None:
        future = _observation("future", {"A": 1}, 0)
        object.__setattr__(future, "timestamp", "2026-07-19T00:00:00Z")
        denied = _observation("denied", {"B": 1}, 0, permission_status="ANALYSIS_DENIED")
        result = build_meta_snapshot([future, denied, _observation("kept", {"A": 1}, 0, duplicate_discount=0.25)], cutoff_time="2026-07-18T12:00:00Z")
        assert set(result.excluded_observation_ids.values()) == {"after_cutoff", "analysis_permission_not_granted"}
        assert result.effective_sample_size == 1

    def test_invalid_probability_is_rejected(self) -> None:
        with pytest.raises(ContractError):
            _observation("bad", {"A": 1.0}, 0.5)

    def test_drift_verdicts(self) -> None:
        old = build_meta_snapshot([_observation("old", {"A": 1}, 0)], cutoff_time="z")
        same = build_meta_snapshot([_observation("same", {"A": 1}, 0)], cutoff_time="z")
        changed = build_meta_snapshot([_observation("new", {"B": 1}, 0)], cutoff_time="z")
        assert detect_drift(old, same)["verdict"] == "NO_SIGNIFICANT_DRIFT"
        assert detect_drift(old, changed)["verdict"] == "BENCHMARK_REFRESH_RECOMMENDED"


class TestSurrogate:
    def test_smoothing_fallback_and_evaluation(self) -> None:
        rows = [SurrogateObservation("s", "2026-07-18T00:00:00Z", "attack", {"phase": "OPENING", "action_category": "attack"}),
                SurrogateObservation("s", "2026-07-18T00:00:00Z", "retreat", {"phase": "OPENING", "action_category": "attack"})]
        model = build_opponent_surrogate(rows, cutoff_time="2026-07-18T01:00:00Z", minimum_support=1)
        prediction = model.predict({"phase": "OPENING", "action_category": "attack"})
        assert all(value > 0 for value in prediction["distribution"].values())
        assert abs(sum(prediction["distribution"].values()) - 1) < 1e-9
        evaluation = evaluate_surrogate(model, rows)
        assert evaluation["negative_log_likelihood"] is not None

    def test_hidden_input_is_rejected(self) -> None:
        with pytest.raises(ContractError):
            SurrogateObservation("s", "t", "a", {}, actor_visible=False)


class TestBenchmarkAndReport:
    def test_benchmark_determinism_and_restricted_promotion(self) -> None:
        one = build_benchmark_manifest("fixed", snapshot_hashes=["x"], episode_ids=["b", "a"], opponents=["o"], seeds=[2, 1], evaluation_config={}, unknown_meta_allocation=0, surrogate_versions=["s"])
        two = build_benchmark_manifest("fixed", snapshot_hashes=["x"], episode_ids=["a", "b"], opponents=["o"], seeds=[1, 2], evaluation_config={}, unknown_meta_allocation=0, surrogate_versions=["s"])
        assert one.content_hash == two.content_hash
        assert build_promotion_report(decision="NO_DECISION", meta_snapshot_hash="m", benchmark_hashes=[one.content_hash], evidence={})["authority"] == "non_authoritative"
        with pytest.raises(ContractError):
            build_promotion_report(decision="PROMOTED", meta_snapshot_hash="m", benchmark_hashes=[], evidence={})


def test_one_shot_cycle_is_resumable_and_never_enables_automation(tmp_path: Path) -> None:
    from mage_ptcg.competition_intelligence.local_ingest import ingest_local_file
    from mage_ptcg.competition_intelligence.offline_reader import discover_offline_training_run
    from mage_ptcg.competition_intelligence.pipeline import run_intelligence_cycle
    from mage_ptcg.offline_training.collection import run_collection

    repository_root = Path(__file__).resolve().parents[2]
    offline_root = tmp_path / "offline"
    run_collection(source="fixture", run_id="cycle", games=2, base_seed=11, output_root=offline_root,
                   canonical_base_sha="a" * 40, deck_path=repository_root / "deck.csv", repository_root=repository_root,
                   validation_percent=20, split_seed=0, fixture_decisions_per_seat=2, fixture_option_count=2)
    discovered = discover_offline_training_run(offline_root)
    ci_root = tmp_path / "ci"
    ingest_local_file(ci_root, discovered.collection_jsonl_path, source_id="local:cycle", acquired_at="2026-07-18T00:00:00Z")
    kwargs = {"offline_training_run": offline_root, "source_id": "local:cycle", "cutoff_time": "2026-07-18T00:00:00Z",
              "created_at": "2026-07-18T00:00:00Z", "base_commit": "a" * 40}
    first = run_intelligence_cycle(ci_root, **kwargs)
    second = run_intelligence_cycle(ci_root, **kwargs)
    assert first == second
    assert first["stages"]["drift"]["verdict"] == "NO_SIGNIFICANT_DRIFT"
    assert (ci_root / "reports" / f"{first['stages']['promotion_report']['report_id']}.json").is_file()
    assert not first["auto_training"] and not first["auto_promotion"] and not first["auto_submit"]
