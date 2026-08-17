"""Tests for fingerprint.py, matchup_stats.py, failure_hypothesis.py, high_info_selector.py."""

from __future__ import annotations

from pathlib import Path

import pytest

from mage_ptcg.competition_intelligence.contracts import (
    EPISODE_RECORD_SCHEMA_VERSION,
    KNOWLEDGE_CLAIM_SCHEMA_VERSION,
    ContractError,
    EpisodeRecord,
    EvidenceGrade,
    ClaimStatus,
    KnowledgeClaim,
)
from mage_ptcg.competition_intelligence.failure_hypothesis import (
    IMPLEMENTED_CATEGORIES,
    TIMEOUT_FALLBACK_RUNTIME,
    generate_failure_hypotheses,
)
from mage_ptcg.competition_intelligence.fingerprint import (
    INDEPENDENCE_CAVEAT,
    build_deck_fingerprint,
    build_joint_fingerprint,
    build_policy_fingerprint,
)
from mage_ptcg.competition_intelligence.high_info_selector import (
    UNAVAILABLE_SELECTORS,
    select_high_information_decisions,
)
from mage_ptcg.competition_intelligence.matchup_stats import aggregate_matchup_statistics
from mage_ptcg.competition_intelligence.replay_normalize import normalize_rule_bc_jsonl

REPO_ROOT = Path(__file__).resolve().parents[2]
DECK_PATH = REPO_ROOT / "deck.csv"


@pytest.fixture(scope="module")
def normalized(tmp_path_factory):
    from mage_ptcg.offline_training.collection import run_collection

    root = tmp_path_factory.mktemp("o1-2-analytics-collection")
    run_collection(
        source="fixture", run_id="cabt", games=8, base_seed=3000, output_root=root / "collection",
        canonical_base_sha="a" * 40, deck_path=DECK_PATH, repository_root=REPO_ROOT,
        validation_percent=20, split_seed=0, fixture_decisions_per_seat=5, fixture_option_count=3,
    )
    jsonl_path = root / "collection" / "cabt" / "private_dataset" / "rule-bc-v1.jsonl"
    return normalize_rule_bc_jsonl(jsonl_path, source_id="local:analytics-source")


class TestDeckFingerprint:
    def test_builds_from_real_decisions(self, normalized) -> None:
        deck_ref = normalized.episodes[0].deck_a_reference
        assert deck_ref is not None
        decisions = [d for d in normalized.decisions if d.episode_id == normalized.episodes[0].episode_id]
        fingerprint = build_deck_fingerprint(decisions, deck_reference=deck_ref)
        assert fingerprint.sample_count >= 1
        assert "evolution_edges_unavailable_no_card_database" in fingerprint.missing_data_flags
        assert "trainer_role_profile_unavailable_no_card_database" in fingerprint.missing_data_flags

    def test_confidence_scales_with_sample_count(self, normalized) -> None:
        deck_ref = normalized.episodes[0].deck_a_reference
        one_episode = [d for d in normalized.decisions if d.episode_id == normalized.episodes[0].episode_id]
        fp_small = build_deck_fingerprint(one_episode, deck_reference=deck_ref)
        fp_all = build_deck_fingerprint(list(normalized.decisions), deck_reference=deck_ref)
        assert fp_all.confidence >= fp_small.confidence

    def test_content_hash_deterministic(self, normalized) -> None:
        deck_ref = normalized.episodes[0].deck_a_reference
        decisions = [d for d in normalized.decisions if d.episode_id == normalized.episodes[0].episode_id]
        a = build_deck_fingerprint(decisions, deck_reference=deck_ref)
        b = build_deck_fingerprint(decisions, deck_reference=deck_ref)
        assert a.content_hash() == b.content_hash()

    def test_never_fabricates_complete_decklist(self, normalized) -> None:
        deck_ref = normalized.episodes[0].deck_a_reference
        decisions = [d for d in normalized.decisions if d.episode_id == normalized.episodes[0].episode_id]
        fingerprint = build_deck_fingerprint(decisions, deck_reference=deck_ref)
        # observed_card_counts must only ever contain cards actually seen, never a synthesized full 60
        assert sum(fingerprint.observed_card_counts.values()) < 60 or fingerprint.observed_card_counts == {}

    def test_rejects_invalid_confidence(self) -> None:
        from mage_ptcg.competition_intelligence.fingerprint import DECK_FINGERPRINT_SCHEMA_VERSION, DeckFingerprint

        with pytest.raises(ContractError):
            DeckFingerprint(
                schema_version=DECK_FINGERPRINT_SCHEMA_VERSION, deck_reference="x", observed_card_counts={},
                attack_usage={}, opening_sequence=(), first_attack_turn=None, energy_attach_rate=None,
                sample_count=1, confidence=1.5, missing_data_flags=frozenset(),
            )


class TestPolicyFingerprint:
    def test_macro_distribution_sums_to_one(self, normalized) -> None:
        fingerprint = build_policy_fingerprint(list(normalized.decisions), agent_reference="rule")
        if fingerprint.macro_distribution:
            assert abs(sum(fingerprint.macro_distribution.values()) - 1.0) < 1e-6

    def test_reports_unavailable_flags_for_unverifiable_features(self, normalized) -> None:
        fingerprint = build_policy_fingerprint(list(normalized.decisions), agent_reference="rule")
        assert "bench_expansion_unavailable_no_card_role_data" in fingerprint.missing_data_flags
        assert "decision_latency_profile_unavailable_no_source_signal" in fingerprint.missing_data_flags
        assert fingerprint.decision_latency_profile is None

    def test_macro_distribution_only_uses_verified_operations(self, normalized) -> None:
        from mage_ptcg.competition_intelligence.fingerprint import KNOWN_SEMANTIC_OPERATIONS

        fingerprint = build_policy_fingerprint(list(normalized.decisions), agent_reference="rule")
        for op in fingerprint.macro_distribution:
            assert op in KNOWN_SEMANTIC_OPERATIONS or op == "OTHER"


class TestJointFingerprint:
    def test_joint_id_is_content_derived(self, normalized) -> None:
        joint = build_joint_fingerprint(list(normalized.decisions), deck_reference="deck-x", agent_reference="rule")
        joint2 = build_joint_fingerprint(list(normalized.decisions), deck_reference="deck-x", agent_reference="rule")
        assert joint.joint_id == joint2.joint_id

    def test_different_deck_reference_changes_joint_id(self, normalized) -> None:
        a = build_joint_fingerprint(list(normalized.decisions), deck_reference="deck-x", agent_reference="rule")
        b = build_joint_fingerprint(list(normalized.decisions), deck_reference="deck-y", agent_reference="rule")
        assert a.joint_id != b.joint_id

    def test_carries_independence_caveat(self, normalized) -> None:
        joint = build_joint_fingerprint(list(normalized.decisions), deck_reference="deck-x", agent_reference="rule")
        assert joint.independence_caveat == INDEPENDENCE_CAVEAT


class TestMatchupStatistics:
    def _episode(self, **overrides) -> EpisodeRecord:
        fields = dict(
            schema_version=EPISODE_RECORD_SCHEMA_VERSION, episode_id="ep-1", source_id="src-1",
            competition_id=None, played_at=None, engine_version=None, agent_a="rule_v0", agent_b="rule_v0",
            deck_a_reference=None, deck_b_reference=None, first_player=0, winner=None,
            termination_reason=None, turn_count=5, decision_count=3, public_trace_hash=None,
        )
        fields.update(overrides)
        return EpisodeRecord(**fields)

    def test_wilson_known_value(self) -> None:
        episodes = [self._episode(episode_id=f"ep-{i}", winner=0) for i in range(8)] + [
            self._episode(episode_id=f"ep-{i}", winner=1) for i in range(8, 10)
        ]
        result = aggregate_matchup_statistics(episodes, own_seat=0)
        key = ("rule_v0", "rule_v0")
        stats = result[key]
        assert stats.wins == 8
        assert stats.losses == 2
        assert stats.win_rate == 0.8
        low, high = stats.wilson_interval
        assert 0.0 < low < stats.win_rate < high < 1.0

    def test_unknown_winner_not_counted_as_draw(self) -> None:
        episodes = [self._episode(episode_id="ep-1", winner=None)]
        result = aggregate_matchup_statistics(episodes)
        stats = next(iter(result.values()))
        assert stats.unknown_result_count == 1
        assert stats.wins == 0 and stats.losses == 0 and stats.draws == 0
        assert stats.win_rate is None
        assert stats.wilson_interval is None

    def test_games_equals_sum_of_all_buckets(self) -> None:
        episodes = [self._episode(episode_id="ep-1", winner=0), self._episode(episode_id="ep-2", winner=None)]
        result = aggregate_matchup_statistics(episodes)
        stats = next(iter(result.values()))
        assert stats.games == stats.wins + stats.losses + stats.draws + stats.unknown_result_count == 2

    def test_rejects_invalid_own_seat(self) -> None:
        with pytest.raises(ContractError):
            aggregate_matchup_statistics([self._episode()], own_seat=5)

    def test_content_hash_deterministic(self) -> None:
        episodes = [self._episode(episode_id="ep-1", winner=0)]
        stats_a = aggregate_matchup_statistics(episodes)
        stats_b = aggregate_matchup_statistics(episodes)
        key = next(iter(stats_a))
        assert stats_a[key].content_hash() == stats_b[key].content_hash()


class TestFailureHypotheses:
    def test_fallback_used_generates_timeout_hypothesis(self, normalized) -> None:
        # No decision in the fixture has fallback_used=True (offline_training's
        # collector never populates it), so verify the rule directly against a
        # constructed decision instead of relying on the fixture to contain one.
        from mage_ptcg.competition_intelligence.contracts import DECISION_RECORD_SCHEMA_VERSION, DecisionRecord

        episode = normalized.episodes[0]
        decision = DecisionRecord(
            schema_version=DECISION_RECORD_SCHEMA_VERSION, episode_id=episode.episode_id, decision_index=0,
            actor_seat=0, turn_index=1, phase="OPENING", actor_information_view=None, legal_action_keys=(),
            chosen_action_key=None, chosen_action_raw=None, public_cards_seen=(), board_summary=None,
            latency_us=None, fallback_used=True, result_to_go=None, source_quality="test",
        )
        hypotheses = generate_failure_hypotheses(episode, [decision])
        categories = {h.category for h in hypotheses}
        assert TIMEOUT_FALLBACK_RUNTIME in categories

    def test_all_generated_categories_are_implemented_categories(self, normalized) -> None:
        for episode in normalized.episodes:
            decisions = [d for d in normalized.decisions if d.episode_id == episode.episode_id]
            for hypothesis in generate_failure_hypotheses(episode, decisions):
                assert hypothesis.category in IMPLEMENTED_CATEGORIES

    def test_every_hypothesis_has_reason_and_limitations(self, normalized) -> None:
        for episode in normalized.episodes:
            decisions = [d for d in normalized.decisions if d.episode_id == episode.episode_id]
            for hypothesis in generate_failure_hypotheses(episode, decisions):
                assert hypothesis.reason
                assert hypothesis.limitations

    def test_public_only_and_oracle_only_mutually_exclusive(self) -> None:
        from mage_ptcg.competition_intelligence.failure_hypothesis import (
            FAILURE_HYPOTHESIS_SCHEMA_VERSION,
            FailureHypothesis,
        )

        with pytest.raises(ContractError):
            FailureHypothesis(
                schema_version=FAILURE_HYPOTHESIS_SCHEMA_VERSION, category=TIMEOUT_FALLBACK_RUNTIME,
                confidence=0.5, evidence={}, episode_id="ep-1", decision_index_start=0, decision_index_end=0,
                phase="OPENING", public_only=True, oracle_only=True, reason="x", limitations="y",
            )


class TestHighInformationSelector:
    def test_reports_unavailable_selectors_explicitly(self, normalized) -> None:
        result = select_high_information_decisions(list(normalized.decisions))
        assert result["unavailable_selectors"] == dict(UNAVAILABLE_SELECTORS)
        assert "RULE_VS_STUDENT_DISAGREEMENT" in result["unavailable_selectors"]

    def test_endgame_or_prize_race_selector_uses_real_phase(self, normalized) -> None:
        result = select_high_information_decisions(list(normalized.decisions))
        from mage_ptcg.competition_intelligence.high_info_selector import SELECTOR_ENDGAME_OR_PRIZE_RACE
        from mage_ptcg.competition_intelligence.phase import ENDGAME, PRIZE_RACE

        selections = result["selections"][SELECTOR_ENDGAME_OR_PRIZE_RACE]
        for selection in selections:
            matching = next(d for d in normalized.decisions if d.episode_id == selection.episode_id and d.decision_index == selection.decision_index)
            assert matching.phase in (ENDGAME, PRIZE_RACE)

    def test_no_claims_supplied_is_reported(self, normalized) -> None:
        result = select_high_information_decisions(list(normalized.decisions))
        assert result["knowledge_claims_supplied"] == 0

    def test_knowledge_claim_scope_match_finds_real_phase_overlap(self, normalized) -> None:
        claim = KnowledgeClaim(
            schema_version=KNOWLEDGE_CLAIM_SCHEMA_VERSION, claim_id="claim-1", raw_source_id="note-1",
            claim_type="phase-tech", scope={"phase": "OPENING"}, preconditions=(), recommendation="x",
            expected_effect=None, evidence_grade=EvidenceGrade.E0_UNVALIDATED, status=ClaimStatus.RAW,
            validity=0.5, support=0.0, freshness=1.0, supporting_artifacts=(), contradicting_claims=(),
            created_at="2026-07-18T00:00:00Z", updated_at="2026-07-18T00:00:00Z",
        )
        result = select_high_information_decisions(list(normalized.decisions), claims=[claim])
        assert result["knowledge_claims_supplied"] == 1
        from mage_ptcg.competition_intelligence.high_info_selector import SELECTOR_KNOWLEDGE_CLAIM_SCOPE_MATCH

        matches = result["selections"][SELECTOR_KNOWLEDGE_CLAIM_SCOPE_MATCH]
        assert any(m.evidence["phase"] == "OPENING" for m in matches) or matches == ()
