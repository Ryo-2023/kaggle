"""Focused tests for the resumable, multi-opponent O5 Evaluation Runner.

These tests never touch the real cabt engine: ``run_match_factory`` is a
fake so the aggregation, resumption, and population-gate reporting logic can
be verified quickly and deterministically. Real cabt execution is exercised
separately by ``scripts/run_o5_benchmark.py``.
"""

from __future__ import annotations

import pytest

from mage_ptcg.competition_intelligence.o5_benchmark import build_versioned_benchmark_manifest
from mage_ptcg.competition_intelligence.o5_evaluation import (
    EXCLUDED_BY_BENCHMARK_KIND,
    KNOWN_AGENT_FACTORIES,
    O5EvaluationError,
    run_o5_benchmark,
)


def _manifest(**overrides):
    kwargs = dict(
        benchmark_id="o5-benchmark-core-v1",
        benchmark_version="1.0.0",
        benchmark_kind="performance",
        created_at="2026-07-21T00:00:00Z",
        source_snapshot_ids=(),
        deck_registry_version="v1",
        policy_pack_version="v1",
        agent_family_versions={"rule_v0": "1"},
        ruleset_version="unknown",
        cabt_version="1.32.0",
        seed_set=(9000, 9100),
        seat_swap_policy="ALWAYS_SWAP",
        game_count=4,
        time_budget_seconds=60.0,
        candidate_artifact_id="rule_v0",
        candidate_artifact_hash="NOT_APPLICABLE",
        baseline_artifact_ids=("random_legal",),
        environment="local",
        commit="0" * 40,
        active_exact_decks=0,
        runnable_families=0,
        verified_links=0,
    )
    kwargs.update(overrides)
    return build_versioned_benchmark_manifest((), **kwargs)


def _fake_run_match_factory(champion: str, challenger: str):
    def run_match(schedule_item):
        index = int(schedule_item["match_index"])
        champion_seat = int(schedule_item["champion_player_index"])
        return {
            "status": "DONE",
            "winner_agent": "champion" if index % 2 == 0 else "challenger",
            "elapsed_seconds": 0.01,
            "fallback_count": 0,
            "champion_status": "DONE",
            "challenger_status": "DONE",
            "champion_fallback_count": 1 if champion_seat == 0 else 0,
            "challenger_fallback_count": 0,
        }

    return run_match


def test_known_agent_factories_cover_core_regression_and_safety_labels():
    manifest = _manifest(benchmark_kind="safety")
    for member_id in manifest.sets["safety"]:
        assert member_id in KNOWN_AGENT_FACTORIES
    performance_manifest = _manifest(benchmark_kind="performance")
    for member_id in performance_manifest.sets["core_regression"]:
        assert member_id in KNOWN_AGENT_FACTORIES


def test_performance_manifest_excludes_safety_set_from_execution(tmp_path):
    manifest = _manifest(benchmark_kind="performance")
    result = run_o5_benchmark(
        manifest, candidate_agent_id="rule_v0", deck_fingerprint="deck.csv",
        output_dir=tmp_path, run_match_factory=_fake_run_match_factory,
    )
    assert result["safety"]["status"] == EXCLUDED_BY_BENCHMARK_KIND
    assert result["safety"]["games"] == 0
    assert result["adversarial"]["status"] == EXCLUDED_BY_BENCHMARK_KIND
    assert result["current_meta"]["status"] == "BENCHMARK_BLOCKED_INSUFFICIENT_ACTIVE_POPULATION"
    assert result["core_regression"]["status"] == "EXECUTED"


def test_safety_manifest_excludes_performance_sets_from_execution(tmp_path):
    manifest = _manifest(benchmark_kind="safety")
    result = run_o5_benchmark(
        manifest, candidate_agent_id="rule_v0", deck_fingerprint="deck.csv",
        output_dir=tmp_path, run_match_factory=_fake_run_match_factory,
    )
    assert result["core_regression"]["status"] == EXCLUDED_BY_BENCHMARK_KIND
    assert result["current_meta"]["status"] == EXCLUDED_BY_BENCHMARK_KIND
    assert result["safety"]["status"] == "EXECUTED"
    # No fault-injection game ever contributes to a performance win rate:
    # a safety manifest structurally cannot populate core_regression.
    assert result["core_regression"]["games"] == 0


def test_run_o5_benchmark_executes_populated_sets_and_records_blocked_current_meta(tmp_path):
    manifest = _manifest(benchmark_kind="performance")
    result = run_o5_benchmark(
        manifest,
        candidate_agent_id="rule_v0",
        deck_fingerprint="deck.csv",
        output_dir=tmp_path,
        run_match_factory=_fake_run_match_factory,
    )
    assert result["current_meta"]["status"] == "BENCHMARK_BLOCKED_INSUFFICIENT_ACTIVE_POPULATION"
    assert result["current_meta"]["games"] == 0

    core_members = result["core_regression"]["members"]
    # rule_v0 is both the candidate and a listed core_regression member; a
    # candidate never plays itself, so it must be skipped, not fabricated.
    assert "rule_v0" not in core_members
    assert "random_legal" in core_members
    per_seed_games = manifest.game_count * len(manifest.seed_set)
    assert core_members["random_legal"]["games"] == per_seed_games
    assert "wilson_ci_95" in core_members["random_legal"]
    assert core_members["random_legal"]["attribution_available"] is True
    assert core_members["random_legal"]["candidate_fallback_total"] == per_seed_games // 2


def test_safety_set_execution_covers_all_fault_labels(tmp_path):
    manifest = _manifest(benchmark_kind="safety")
    result = run_o5_benchmark(
        manifest, candidate_agent_id="rule_v0", deck_fingerprint="deck.csv",
        output_dir=tmp_path, run_match_factory=_fake_run_match_factory,
    )
    safety_members = result["safety"]["members"]
    assert set(safety_members) == {"random_legal", "exception_agent", "slow_agent", "invalid_artifact", "unknown_selection"}
    per_seed_games = manifest.game_count * len(manifest.seed_set)
    for member_report in safety_members.values():
        assert member_report["games"] == per_seed_games


def test_run_o5_benchmark_is_resumable_and_idempotent(tmp_path):
    manifest = _manifest()
    first = run_o5_benchmark(manifest, candidate_agent_id="rule_v0", deck_fingerprint="deck.csv", output_dir=tmp_path, run_match_factory=_fake_run_match_factory)
    second = run_o5_benchmark(manifest, candidate_agent_id="rule_v0", deck_fingerprint="deck.csv", output_dir=tmp_path, run_match_factory=_fake_run_match_factory)
    assert first == second


def test_two_manifests_in_the_same_output_dir_never_collide(tmp_path):
    # Different candidate -> different manifest_hash -> different artifact
    # filenames, so pointing two different runs at the same output_dir must
    # never resume one candidate's results as another's.
    manifest_a = _manifest(candidate_artifact_id="rule_v0")
    manifest_b = _manifest(candidate_artifact_id="deterministic", baseline_artifact_ids=("rule_v0",))
    assert manifest_a.manifest_hash != manifest_b.manifest_hash
    result_a = run_o5_benchmark(manifest_a, candidate_agent_id="rule_v0", deck_fingerprint="deck.csv", output_dir=tmp_path, run_match_factory=_fake_run_match_factory)
    result_b = run_o5_benchmark(manifest_b, candidate_agent_id="deterministic", deck_fingerprint="deck.csv", output_dir=tmp_path, run_match_factory=_fake_run_match_factory)
    assert result_a["core_regression"]["games"] > 0
    assert result_b["core_regression"]["games"] > 0
    assert set(result_a["core_regression"]["members"]) != set(result_b["core_regression"]["members"]) or result_a != result_b


def test_unknown_candidate_agent_id_is_rejected(tmp_path):
    with pytest.raises(O5EvaluationError):
        run_o5_benchmark(_manifest(), candidate_agent_id="not_a_real_agent", deck_fingerprint="deck.csv", output_dir=tmp_path, run_match_factory=_fake_run_match_factory)


def test_neural_actual_trained_candidate_id_is_accepted_as_known(tmp_path):
    manifest = _manifest(candidate_artifact_id="neural_actual_trained", candidate_artifact_hash="fixture-hash", baseline_artifact_ids=("rule_v0",))
    result = run_o5_benchmark(
        manifest, candidate_agent_id="neural_actual_trained", deck_fingerprint="deck.csv",
        output_dir=tmp_path, run_match_factory=_fake_run_match_factory,
    )
    assert result["core_regression"]["games"] > 0


def test_requires_a_run_match_factory(tmp_path):
    with pytest.raises(O5EvaluationError):
        run_o5_benchmark(_manifest(), candidate_agent_id="rule_v0", deck_fingerprint="deck.csv", output_dir=tmp_path, run_match_factory=None)


def test_crashed_opponent_games_are_excluded_from_win_rate_denominator(tmp_path):
    # A crashed/invalid opponent produces no winner. decided_games and
    # win_rate must both be computed over real outcomes only, matching the
    # convention discovered running this against real cabt: a member whose
    # opponent always crashes must read as "no outcome decided", not as a
    # 0% loss rate.
    def crash_run_match_factory(champion, challenger):
        def run_match(schedule_item):
            return {
                "status": "AGENT_ERROR", "winner_agent": None, "elapsed_seconds": 0.01, "fallback_count": 0,
                "champion_status": "DONE", "challenger_status": "ERROR",
                "champion_fallback_count": 0, "challenger_fallback_count": 0,
            }

        return run_match

    manifest = _manifest(baseline_artifact_ids=("random_legal",))
    result = run_o5_benchmark(
        manifest, candidate_agent_id="rule_v0", deck_fingerprint="deck.csv",
        output_dir=tmp_path, run_match_factory=crash_run_match_factory,
    )
    random_legal = result["core_regression"]["members"]["random_legal"]
    assert random_legal["games"] == manifest.game_count * len(manifest.seed_set)
    assert random_legal["decided_games"] == 0
    assert random_legal["win_rate"] == 0.0
    assert random_legal["wilson_ci_95"] == [0.0, 0.0]
    assert random_legal["crashes"] == random_legal["games"]
    # The opponent (not the candidate) is the one that crashed.
    assert random_legal["opponent_exception"] == random_legal["games"]
    assert random_legal["candidate_exception"] == 0


def test_wilson_ci_bounds_win_rate_and_overall_report_present(tmp_path):
    manifest = _manifest()
    result = run_o5_benchmark(manifest, candidate_agent_id="rule_v0", deck_fingerprint="deck.csv", output_dir=tmp_path, run_match_factory=_fake_run_match_factory)
    overall = result["overall"]
    low, high = overall["wilson_ci_95"]
    assert 0.0 <= low <= overall["win_rate"] <= high <= 1.0
    assert overall["games"] > 0
    assert overall["attribution_available"] is True


def test_fallback_assisted_wins_are_never_blended_into_the_no_fallback_win_rate(tmp_path):
    # Independent-audit regression: a fallback-assisted win must not read
    # identically to a pure neural-policy win. Half the games (champion
    # seat 0, per _fake_run_match_factory) report champion_fallback_count=1
    # and always win; the other half report 0 and alternate win/loss.
    manifest = _manifest()
    result = run_o5_benchmark(manifest, candidate_agent_id="rule_v0", deck_fingerprint="deck.csv", output_dir=tmp_path, run_match_factory=_fake_run_match_factory)
    breakdown = result["core_regression"]["members"]["random_legal"]["fallback_breakdown"]
    per_seed_games = manifest.game_count * len(manifest.seed_set)
    assert breakdown["no_fallback"]["games"] + breakdown["fallback_used"]["games"] + breakdown["fallback_status_unknown_games"] == per_seed_games
    assert breakdown["fallback_used"]["games"] > 0
    # Every fallback-assisted game in the fake factory is champion_seat==0,
    # match_index even -> winner_agent="champion" (see _fake_run_match_factory).
    assert breakdown["fallback_used"]["win_rate"] == 1.0
    assert breakdown["no_fallback"]["win_rate"] != breakdown["fallback_used"]["win_rate"]


def test_fallback_breakdown_excludes_games_with_unobserved_fallback_status(tmp_path):
    def legacy_run_match_factory(champion, challenger):
        def run_match(schedule_item):
            return {"status": "DONE", "winner_agent": "champion", "elapsed_seconds": 0.01, "fallback_count": 0}

        return run_match

    manifest = _manifest(baseline_artifact_ids=("random_legal",))
    result = run_o5_benchmark(manifest, candidate_agent_id="rule_v0", deck_fingerprint="deck.csv", output_dir=tmp_path, run_match_factory=legacy_run_match_factory)
    breakdown = result["core_regression"]["members"]["random_legal"]["fallback_breakdown"]
    per_seed_games = manifest.game_count * len(manifest.seed_set)
    assert breakdown["fallback_status_unknown_games"] == per_seed_games
    assert breakdown["no_fallback"]["games"] == 0
    assert breakdown["fallback_used"]["games"] == 0
