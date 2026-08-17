from __future__ import annotations

from mage_ptcg.bootstrap_champion.tournament import (
    BootstrapScore,
    build_candidate_schedule,
    rank_candidates,
    summarize_candidate,
)
from mage_ptcg.continuous_league.contracts import content_id


def _sha(character: str) -> str:
    return character * 64


def test_schedule_is_balanced_and_keeps_common_random_numbers_between_candidates() -> None:
    first = build_candidate_schedule(
        candidate_ids=[_sha("a"), _sha("b")],
        opponent_instance_ids=[_sha("1"), _sha("2")],
        games_per_candidate=8,
        seed_namespace="bootstrap-screen-v1",
    )
    reversed_input = build_candidate_schedule(
        candidate_ids=[_sha("b"), _sha("a")],
        opponent_instance_ids=[_sha("2"), _sha("1")],
        games_per_candidate=8,
        seed_namespace="bootstrap-screen-v1",
    )

    assert first == reversed_input
    per_candidate = [item for item in first if item.candidate_id == _sha("a")]
    assert len(per_candidate) == 8
    assert {item.seat for item in per_candidate} == {"subject_first", "subject_second"}
    common_cells = {}
    for item in first:
        common_cells.setdefault((item.opponent_instance_id, item.seat, item.repetition_index), set()).add(item.env_seed)
    assert all(len(seeds) == 1 for seeds in common_cells.values())


def test_score_summary_rejects_faults_and_computes_equal_opponent_scores() -> None:
    rows = [
        {"candidate_id": _sha("a"), "opponent_instance_id": _sha("1"), "outcome": "win", "seat": "subject_first", "duration_seconds": 0.1},
        {"candidate_id": _sha("a"), "opponent_instance_id": _sha("1"), "outcome": "loss", "seat": "subject_second", "duration_seconds": 0.2},
        {"candidate_id": _sha("a"), "opponent_instance_id": _sha("2"), "outcome": "win", "seat": "subject_first", "duration_seconds": 0.3},
        {"candidate_id": _sha("a"), "opponent_instance_id": _sha("2"), "outcome": "win", "seat": "subject_second", "duration_seconds": 0.4},
    ]

    score = summarize_candidate(rows)

    assert score.opponent_equal_score_rate == 0.75
    assert score.worst_opponent_score_rate == 0.5
    assert score.fault_count == 0
    assert score.seat_score_rates == {"subject_first": 1.0, "subject_second": 0.5}


def test_ranking_uses_one_point_shortlist_then_worst_opponent() -> None:
    strongest = BootstrapScore(_sha("a"), 0.600, 0.1, 0.50, 0.40, 1.0, {"subject_first": 0.6}, 0)
    close_but_robust = BootstrapScore(_sha("b"), 0.595, 0.4, 0.55, 0.50, 1.0, {"subject_first": 0.6}, 0)
    outside_shortlist = BootstrapScore(_sha("c"), 0.580, 0.9, 0.90, 0.50, 1.0, {"subject_first": 0.6}, 0)

    assert [score.candidate_id for score in rank_candidates([strongest, close_but_robust, outside_shortlist])] == [
        _sha("b"),
        _sha("a"),
        _sha("c"),
    ]
