from pathlib import Path

import pytest

from mage_ptcg.optimization.core import (ActionKeyVNext, AdvantageRecord, DisagreementRootBuffer,
                                         OptimizationContractError, OpponentPublicPosterior,
                                         ResidualRanker, Root, RuleOverlay, robust_rank)


def _key() -> ActionKeyVNext:
    return ActionKeyVNext(2, "0", "main", "PLAY", 2, 0, 0, 722, "[2,0,null,1]", "[0,0,0]", None, None, (), (), (), "7", "legal")


def test_actionkey_vnext_round_trip_and_instance_identity() -> None:
    first = _key(); second = ActionKeyVNext(**{**first.payload(), "card_instance_id": "[2,1,null,1]"})
    assert first.key != second.key
    assert ActionKeyVNext.deserialize(first.payload()) == first
    with pytest.raises(OptimizationContractError): ActionKeyVNext.deserialize({"schema_version": 2})


def test_public_posterior_is_deterministic_and_keeps_unknown() -> None:
    first = OpponentPublicPosterior(); second = OpponentPublicPosterior()
    for posterior in (first, second): posterior.update(public_cards=[722], public_actions=["PLAY"], family_anchors={"A": [722], "B": [999]})
    assert first.payload() == second.payload()
    assert first.payload()["unknown_probability"] > 0
    assert first.payload()["confidence"] > 0


def test_public_posterior_without_public_evidence_remains_zero_confidence() -> None:
    posterior = OpponentPublicPosterior()
    posterior.update(public_cards=[], public_actions=[], family_anchors={"A": [722]})
    assert posterior.payload()["families"] == {"UNKNOWN": 1.0}
    assert posterior.payload()["confidence"] == 0


def test_public_posterior_payload_is_an_immutable_historical_snapshot() -> None:
    posterior = OpponentPublicPosterior(); first = posterior.payload()
    posterior.update(public_cards=[722], family_anchors={"A": [722]})
    assert first["families"] == {"UNKNOWN": 1.0}


def test_root_buffer_dedup_and_privacy(tmp_path: Path) -> None:
    root = Root("root", "state", {"public_state": {}}, [], "rule", [], {}, "deck", "game", 0, 1., 1., 1.)
    buffer = DisagreementRootBuffer(tmp_path / "roots.jsonl")
    assert buffer.add(root) and not buffer.add(root)
    bad = Root("bad", "state", {"opponent_hand": [1]}, [], "rule", [], {}, "deck", "game", 0, 1., 1., 1.)
    with pytest.raises(OptimizationContractError): buffer.add(bad)


def test_residual_and_overlay_fail_closed_without_eligible_evidence() -> None:
    row = AdvantageRecord("r", "s", "rule", "alt", .5, .1, .3, 4, "TRUE_STATE_CONDITIONAL_ONLY", "INCONCLUSIVE", False, "g", "p")
    ranker = ResidualRanker(); assert ranker.fit([row])["eligible_examples"] == 0
    assert ranker.choose(rule_action="rule", candidates=["alt"]) == ("rule", "PLANNED_RULE_DELEGATION")
    assert RuleOverlay.compile([row]).status == "NO_RULE_MET_EVIDENCE_THRESHOLD"


def test_robust_rank_prefers_worst_group_safe_candidate() -> None:
    rows = robust_rank([{"deck_id": "a", "policy_id": "a", "group_returns": [.9, -.2]}, {"deck_id": "b", "policy_id": "b", "group_returns": [.2, .2]}])
    assert rows[0]["deck_id"] == "b"
