"""Tests for decision_eligibility.py (independent-audit finding #3 remediation).

Verifies that High-Information selection (analysis) and training eligibility
(label-quality) are separate axes, that training export defaults to a
default-deny policy, and that low-information/fallback/unverified decisions
are never silently promoted into BC training targets.
"""

from __future__ import annotations

import pytest

from mage_ptcg.competition_intelligence.contracts import (
    DECISION_RECORD_SCHEMA_VERSION,
    ContractError,
    DecisionRecord,
)
from mage_ptcg.competition_intelligence.decision_eligibility import (
    ANALYSIS_ALL_PERMITTED,
    DEFAULT_TRAINING_POLICY,
    TRAINING_HIGH_INFORMATION,
    TRAINING_HIGH_INFORMATION_VERIFIED,
    TRAINING_VERIFIED,
    VERIFICATION_BASIS_TEACHER_AGREEMENT,
    build_high_information_index,
    compute_decision_eligibility,
)
from mage_ptcg.competition_intelligence.high_info_selector import select_high_information_decisions


def _decision(**overrides) -> DecisionRecord:
    fields = dict(
        schema_version=DECISION_RECORD_SCHEMA_VERSION, episode_id="ep-1", decision_index=0, actor_seat=0,
        turn_index=1, phase="MAIN", actor_information_view=None, legal_action_keys=("a", "b"),
        chosen_action_key="a", chosen_action_raw=None, public_cards_seen=(), board_summary=None,
        latency_us=None, fallback_used=False, result_to_go=None, source_quality="offline-training-v1-rule-bc-v1",
    )
    fields.update(overrides)
    return DecisionRecord(**fields)


def _verified_view(top_key: str = "a", runner_up_key: str = "b") -> dict:
    return {"teacher_ranking": [[top_key, 100], [runner_up_key, 10]]}


class TestDefaultDeny:
    def test_default_training_policy_is_the_strictest(self) -> None:
        assert DEFAULT_TRAINING_POLICY == TRAINING_HIGH_INFORMATION_VERIFIED

    def test_unknown_policy_is_rejected(self) -> None:
        with pytest.raises(ContractError):
            compute_decision_eligibility(
                [_decision()], policy="NOT_A_REAL_POLICY", permission_granted_by_episode={"ep-1": True},
            )


class TestAnalysisPolicy:
    def test_analysis_all_permitted_includes_every_non_fallback_permitted_decision(self) -> None:
        decisions = [_decision(decision_index=i, fallback_used=False) for i in range(5)]
        records = compute_decision_eligibility(
            decisions, policy=ANALYSIS_ALL_PERMITTED, permission_granted_by_episode={"ep-1": True},
        )
        assert all(r.training_eligible for r in records)

    def test_analysis_all_permitted_still_excludes_fallback(self) -> None:
        decisions = [_decision(decision_index=0, fallback_used=True)]
        records = compute_decision_eligibility(
            decisions, policy=ANALYSIS_ALL_PERMITTED, permission_granted_by_episode={"ep-1": True},
        )
        assert records[0].training_eligible is False
        assert any("fallback_used" in reason for reason in records[0].training_eligibility_reasons)

    def test_analysis_all_permitted_excludes_when_permission_not_granted(self) -> None:
        decisions = [_decision(decision_index=0)]
        records = compute_decision_eligibility(
            decisions, policy=ANALYSIS_ALL_PERMITTED, permission_granted_by_episode={"ep-1": False},
        )
        assert records[0].training_eligible is False
        assert any("permission_not_granted" in reason for reason in records[0].training_eligibility_reasons)


class TestTrainingHighInformation:
    def test_low_information_decisions_are_excluded(self) -> None:
        decisions = [_decision(decision_index=0)]
        records = compute_decision_eligibility(
            decisions, policy=TRAINING_HIGH_INFORMATION, permission_granted_by_episode={"ep-1": True},
            high_information_selectors_by_key={},  # nothing flagged high-information
        )
        assert records[0].training_eligible is False
        assert any("not_high_information" in reason for reason in records[0].training_eligibility_reasons)

    def test_high_information_decisions_are_included(self) -> None:
        decisions = [_decision(decision_index=0)]
        records = compute_decision_eligibility(
            decisions, policy=TRAINING_HIGH_INFORMATION, permission_granted_by_episode={"ep-1": True},
            high_information_selectors_by_key={("ep-1", 0): ("SMALL_TOP2_MARGIN",)},
        )
        assert records[0].training_eligible is True
        assert records[0].is_high_information is True

    def test_fallback_decision_excluded_even_if_high_information(self) -> None:
        decisions = [_decision(decision_index=0, fallback_used=True)]
        records = compute_decision_eligibility(
            decisions, policy=TRAINING_HIGH_INFORMATION, permission_granted_by_episode={"ep-1": True},
            high_information_selectors_by_key={("ep-1", 0): ("FALLBACK_OR_ANOMALY",)},
        )
        assert records[0].training_eligible is False


class TestTrainingVerified:
    def test_unverified_decision_is_not_training_eligible(self) -> None:
        # No actor_information_view at all -> no teacher_ranking -> unverified.
        decisions = [_decision(decision_index=0, actor_information_view=None)]
        records = compute_decision_eligibility(
            decisions, policy=TRAINING_VERIFIED, permission_granted_by_episode={"ep-1": True},
        )
        assert records[0].training_eligible is False
        assert records[0].verification_basis is None
        assert any("no_verification_basis" in reason for reason in records[0].training_eligibility_reasons)

    def test_disagreement_with_teacher_ranking_is_not_verified(self) -> None:
        # chosen_action_key does NOT match the top-ranked action.
        decisions = [_decision(decision_index=0, chosen_action_key="b", actor_information_view=_verified_view(top_key="a"))]
        records = compute_decision_eligibility(
            decisions, policy=TRAINING_VERIFIED, permission_granted_by_episode={"ep-1": True},
        )
        assert records[0].training_eligible is False
        assert records[0].verification_basis is None

    def test_teacher_agreement_is_a_verification_basis(self) -> None:
        decisions = [_decision(decision_index=0, chosen_action_key="a", actor_information_view=_verified_view(top_key="a"))]
        records = compute_decision_eligibility(
            decisions, policy=TRAINING_VERIFIED, permission_granted_by_episode={"ep-1": True},
        )
        assert records[0].training_eligible is True
        assert records[0].verification_basis == VERIFICATION_BASIS_TEACHER_AGREEMENT
        assert records[0].verification_provenance is not None

    def test_executed_but_unverified_action_is_not_training_eligible(self) -> None:
        # This is the independent-audit's core concern: an action was
        # literally executed (chosen_action_key is set) but there is no
        # ranking evidence it was the teacher's actual top choice -- "it ran"
        # must not be treated as "it is a correct label".
        decisions = [_decision(decision_index=0, chosen_action_key="z", actor_information_view=None)]
        records = compute_decision_eligibility(
            decisions, policy=TRAINING_VERIFIED, permission_granted_by_episode={"ep-1": True},
        )
        assert records[0].training_eligible is False


class TestHighInformationVsVerifiedAreSeparateAxes:
    def test_high_information_but_unverified_is_not_training_eligible_under_verified_policy(self) -> None:
        decisions = [_decision(decision_index=0, chosen_action_key="b", actor_information_view=_verified_view(top_key="a"))]
        records = compute_decision_eligibility(
            decisions, policy=TRAINING_VERIFIED, permission_granted_by_episode={"ep-1": True},
            high_information_selectors_by_key={("ep-1", 0): ("SMALL_TOP2_MARGIN",)},
        )
        assert records[0].is_high_information is True
        assert records[0].training_eligible is False  # verified policy does not care about high-info

    def test_verified_but_not_high_information_is_training_eligible_under_verified_policy(self) -> None:
        decisions = [_decision(decision_index=0, chosen_action_key="a", actor_information_view=_verified_view(top_key="a"))]
        records = compute_decision_eligibility(
            decisions, policy=TRAINING_VERIFIED, permission_granted_by_episode={"ep-1": True},
            high_information_selectors_by_key={},
        )
        assert records[0].is_high_information is False
        assert records[0].training_eligible is True

    def test_high_information_verified_requires_both(self) -> None:
        both = _decision(episode_id="ep-a", decision_index=0, chosen_action_key="a", actor_information_view=_verified_view(top_key="a"))
        only_high_info = _decision(episode_id="ep-b", decision_index=0, chosen_action_key="b", actor_information_view=_verified_view(top_key="a"))
        only_verified = _decision(episode_id="ep-c", decision_index=0, chosen_action_key="a", actor_information_view=_verified_view(top_key="a"))
        selectors_by_key = {("ep-a", 0): ("SMALL_TOP2_MARGIN",), ("ep-b", 0): ("SMALL_TOP2_MARGIN",)}
        records = compute_decision_eligibility(
            [both, only_high_info, only_verified], policy=TRAINING_HIGH_INFORMATION_VERIFIED,
            permission_granted_by_episode={"ep-a": True, "ep-b": True, "ep-c": True},
            high_information_selectors_by_key=selectors_by_key,
        )
        by_episode = {r.episode_id: r for r in records}
        assert by_episode["ep-a"].training_eligible is True
        assert by_episode["ep-b"].training_eligible is False  # high-info but not verified
        assert by_episode["ep-c"].training_eligible is False  # verified but not high-info


class TestEligibilityReasonsAndProvenance:
    def test_every_decision_gets_a_reason_whether_included_or_excluded(self) -> None:
        decisions = [
            _decision(episode_id="ep-a", decision_index=0, chosen_action_key="a", actor_information_view=_verified_view(top_key="a")),
            _decision(episode_id="ep-b", decision_index=0, fallback_used=True),
        ]
        records = compute_decision_eligibility(
            decisions, policy=TRAINING_VERIFIED, permission_granted_by_episode={"ep-a": True, "ep-b": True},
        )
        assert all(len(r.training_eligibility_reasons) > 0 for r in records)

    def test_observed_is_always_true_for_replay_decisions(self) -> None:
        records = compute_decision_eligibility(
            [_decision()], policy=ANALYSIS_ALL_PERMITTED, permission_granted_by_episode={"ep-1": True},
        )
        assert records[0].observed is True

    def test_to_dict_round_trips_all_fields(self) -> None:
        records = compute_decision_eligibility(
            [_decision(chosen_action_key="a", actor_information_view=_verified_view(top_key="a"))],
            policy=TRAINING_VERIFIED, permission_granted_by_episode={"ep-1": True},
        )
        payload = records[0].to_dict()
        assert payload["episode_id"] == "ep-1"
        assert payload["training_eligible"] is True
        assert payload["verification_basis"] == VERIFICATION_BASIS_TEACHER_AGREEMENT


class TestHighInformationIndexHelper:
    def test_build_high_information_index_matches_selector_output(self) -> None:
        decisions = [
            _decision(episode_id="ep-1", decision_index=0, phase="ENDGAME"),
            _decision(episode_id="ep-1", decision_index=1, phase="MAIN"),
        ]
        selection = select_high_information_decisions(decisions)
        index = build_high_information_index(selection["selections"])
        assert ("ep-1", 0) in index
        assert "ENDGAME_OR_PRIZE_RACE" in index[("ep-1", 0)]
        assert ("ep-1", 1) not in index
