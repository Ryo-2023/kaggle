"""Tests for O6-AUD-002 remediation: trajectory digests and unique-sample statistics."""
from __future__ import annotations

import pytest

from mage_ptcg.opponents.core import OpponentError
from mage_ptcg.opponents.public_trajectory_projection import build_public_trajectory_events
from mage_ptcg.opponents.trajectory import (
    ENGINE_SEED_UNSUPPORTED, ENGINE_SEED_UNVERIFIED,
    aggregate_trajectory_uniqueness, compute_trajectory_digests, deduplicate_by_trajectory,
    determine_engine_seed_capability, fit_bradley_terry, load_trajectory_evidence, pair_win_rate_statistics,
    pairwise_wins_from_records, record_trajectory_evidence, wilson_ci,
)


def _player():
    return {"active": [None], "asleep": False, "bench": [None] * 5, "benchMax": 5, "burned": False,
            "confused": False, "deckCount": 52, "discard": [], "hand": [], "handCount": 0,
            "paralyzed": False, "poisoned": False, "prize": [{"id": 9}] * 6}


def _select(option_type=14, **fields):
    # Real cabt shape: select["option"] (singular) already is the candidate list, and each
    # option dict is flat (no nested "fields").
    return {"type": 0, "option": [{"type": option_type, **fields}]}


def _obs(your_index, *, overage=600, select=None, result=None):
    current = {"yourIndex": your_index, "players": [_player(), _player()], "energyAttached": False,
               "retreated": False, "stadium": None, "stadiumPlayed": False, "supporterPlayed": False}
    if result is not None:
        current["result"] = result
    return {"current": current, "logs": [], "search_begin_input": "t", "remainingOverageTime": overage,
            "select": select, "step": 1}


def _step(seat0_action, seat1_action, *, overage0=600, overage1=600, status0="ACTIVE", status1="ACTIVE",
          seat0_select=None, seat1_select=None, result=None):
    return [
        {"action": seat0_action, "observation": _obs(0, overage=overage0, select=seat0_select, result=result), "status": status0},
        {"action": seat1_action, "observation": _obs(1, overage=overage1, select=seat1_select, result=result), "status": status1},
    ]


def _raw_sample_game(*, overage_variant=0):
    # Real engine pairing: a seat's select at raw step i is answered by that seat's action at
    # raw step i + 1, not the same index.
    return [
        _step(None, None, overage0=600 - overage_variant, overage1=600, seat0_select=_select(14)),
        _step([0], None, overage0=590 - overage_variant, overage1=600, seat1_select=_select(14)),
        _step(None, [0], overage0=580, overage1=590 - overage_variant),
        _step(None, None, overage0=580, overage1=580, status0="DONE", status1="DONE", result=0),
    ]


def _sample_game(*, overage_variant=0):
    return build_public_trajectory_events(_raw_sample_game(overage_variant=overage_variant))


def test_empty_steps_fails_closed():
    with pytest.raises(OpponentError, match="empty events"):
        compute_trajectory_digests([])


def test_identical_trajectory_deduplication():
    first = compute_trajectory_digests(_sample_game())
    second = compute_trajectory_digests(_sample_game())
    assert first["complete_trajectory_digest"] == second["complete_trajectory_digest"]
    assert first["initial_observation_digest"] == second["initial_observation_digest"]
    assert first["action_trace_digest"] == second["action_trace_digest"]
    assert first["terminal_observation_digest"] == second["terminal_observation_digest"]


def test_timestamp_independent_digest():
    """remainingOverageTime differs only by wall-clock usage; digest must be identical."""
    baseline = compute_trajectory_digests(_sample_game(overage_variant=0))
    later = compute_trajectory_digests(_sample_game(overage_variant=37))
    assert baseline["complete_trajectory_digest"] == later["complete_trajectory_digest"]
    assert baseline["initial_observation_digest"] == later["initial_observation_digest"]


def test_path_independent_digest():
    """The digest function takes no filesystem path at all; recomputing from
    logically-identical in-memory data from two unrelated call sites must
    agree, which is the only meaningful 'path independence' a pure hashing
    function can be tested for."""
    events_built_one_way = _sample_game()
    events_built_another_way = build_public_trajectory_events(_raw_sample_game())
    assert compute_trajectory_digests(events_built_one_way)["complete_trajectory_digest"] == compute_trajectory_digests(events_built_another_way)["complete_trajectory_digest"]


def test_private_state_and_unrelated_fields_do_not_participate():
    """Extra keys on a per-seat step wrapper (outside observation/action/status) are
    never read by the projection builder at all, so they cannot participate."""
    steps = _raw_sample_game()
    steps_with_extra = [
        [{**seat, "reward": 999, "info": {"hidden_opponent_hand": ["should never be hashed"]}} for seat in step]
        for step in steps
    ]
    base_events = build_public_trajectory_events(steps)
    extra_events = build_public_trajectory_events(steps_with_extra)
    assert compute_trajectory_digests(base_events)["complete_trajectory_digest"] == compute_trajectory_digests(extra_events)["complete_trajectory_digest"]


def test_different_action_trace_separation():
    base = compute_trajectory_digests(_sample_game())
    different_actions = [
        _step(None, None, overage0=600, overage1=600, seat0_select=_select(13, attackId=9, count=9)),  # different action content
        _step([0], None, overage0=590, overage1=600, seat1_select=_select(14)),
        _step(None, [0], overage0=580, overage1=590),
        _step(None, None, overage0=580, overage1=580, status0="DONE", status1="DONE", result=0),
    ]
    other = compute_trajectory_digests(build_public_trajectory_events(different_actions))
    assert base["initial_observation_digest"] == other["initial_observation_digest"]
    assert base["action_trace_digest"] != other["action_trace_digest"]
    assert base["complete_trajectory_digest"] != other["complete_trajectory_digest"]


def test_different_seat_trajectory_separation():
    """Swapping which seat has which observation/action content must change the digest."""
    normal_raw = _raw_sample_game()
    swapped_raw = [[step[1], step[0]] for step in normal_raw]
    normal = compute_trajectory_digests(build_public_trajectory_events(normal_raw))
    swapped = compute_trajectory_digests(build_public_trajectory_events(swapped_raw))
    assert normal["complete_trajectory_digest"] != swapped["complete_trajectory_digest"]


def test_fault_partial_game_still_produces_digest():
    """A crashed/faulted game with only a couple of recorded steps must still
    yield usable evidence (fail-closed only on truly empty steps)."""
    partial = [_step(None, None), _step([0], None, status0="ERROR", status1="ACTIVE", seat0_select=_select(14))]
    result = compute_trajectory_digests(build_public_trajectory_events(partial))
    assert result["game_length"] == 2
    assert result["complete_trajectory_digest"]


def test_raw_execution_unique_count_multiplicity_and_effective_sample_size():
    duplicate_digest = compute_trajectory_digests(_sample_game())["complete_trajectory_digest"]
    records = []
    for _ in range(10):
        records.append({
            "pair_id": "a__vs__b", "seat_0_participant": "a",
            "initial_observation_digest": "same-initial", "action_trace_digest": "same-actions",
            "terminal_observation_digest": "same-terminal", "complete_trajectory_digest": duplicate_digest,
        })
    aggregated = aggregate_trajectory_uniqueness(records, bucket_key=lambda r: (r["pair_id"], r["seat_0_participant"]))
    bucket = aggregated[("a__vs__b", "a")]
    assert bucket["raw_executions"] == 10
    assert bucket["unique_complete_trajectories"] == 1
    assert bucket["effective_independent_sample_size"] == 1
    assert bucket["duplicate_trajectory_groups"] == {duplicate_digest: 10}
    assert bucket["max_multiplicity"] == 10


def test_diverse_trajectories_give_full_effective_sample_size():
    records = []
    for variant in range(6):
        # force distinct complete digests by varying the action content per variant
        game = [
            _step(None, None, seat0_select=_select(13, attackId=variant, count=variant)),
            _step([0], None, seat1_select=_select(14)),
            _step(None, [0]),
            _step(None, None, status0="DONE", status1="DONE", result=0),
        ]
        digests = compute_trajectory_digests(build_public_trajectory_events(game))
        records.append({
            "pair_id": "a__vs__b", "seat_0_participant": "a", **digests,
        })
    aggregated = aggregate_trajectory_uniqueness(records, bucket_key=lambda r: (r["pair_id"], r["seat_0_participant"]))
    bucket = aggregated[("a__vs__b", "a")]
    assert bucket["raw_executions"] == 6
    assert bucket["unique_complete_trajectories"] == 6
    assert bucket["effective_independent_sample_size"] == 6
    assert bucket["duplicate_trajectory_groups"] == {}


def test_engine_seed_capability_classification():
    assert determine_engine_seed_capability(["decks", "episodeSteps", "actTimeout", "runTimeout"]) == ENGINE_SEED_UNSUPPORTED
    assert determine_engine_seed_capability(["decks", "seed"]) == ENGINE_SEED_UNVERIFIED


def test_wilson_ci_basic_bounds():
    assert wilson_ci(0, 0) is None
    lo, hi = wilson_ci(5, 10)
    assert 0.0 < lo < 0.5 < hi < 1.0
    lo_all, hi_all = wilson_ci(10, 10)
    assert hi_all == pytest.approx(1.0) and lo_all > 0.5


def _win_record(winner, *, digest_value):
    return {"winner_participant": winner, "complete_trajectory_digest": digest_value}


def test_pair_statistics_winner_direction_is_explicit_and_unambiguous():
    records = [_win_record("ozawa-crustle-rule", digest_value=f"d{i}") for i in range(10)]
    stats = pair_win_rate_statistics(records, side_a="ozawa-crustle-rule", side_b="rule-agent-v0")
    assert stats["raw_execution_wins"] == {"ozawa-crustle-rule": 10, "rule-agent-v0": 0}
    assert stats["unique_trajectory_wins"] == {"ozawa-crustle-rule": 10, "rule-agent-v0": 0}
    assert stats["unique_trajectory_win_rate_a"] == 1.0


def test_wilson_ci_on_unique_sample_basis_and_insufficient_sample_suppression():
    # 10 raw executions but only 2 unique trajectories -> effective N=2, below default threshold(5)
    records = [_win_record("a", digest_value="dup") for _ in range(8)] + [_win_record("b", digest_value="dup2") for _ in range(2)]
    stats = pair_win_rate_statistics(records, side_a="a", side_b="b")
    assert stats["effective_independent_sample_size"] == 2
    assert stats["unique_trajectory_wilson_ci_status"] == "INSUFFICIENT_INDEPENDENT_SAMPLES"
    assert stats["unique_trajectory_wilson_ci_a"] is None
    assert stats["statistically_interpretable"] is False
    # raw-execution CI is still computed, but explicitly flagged descriptive-only
    assert stats["raw_execution_wilson_ci_a"] is not None
    assert stats["raw_execution_wilson_ci_is_descriptive_only"] is True

    sufficient_records = [_win_record("a", digest_value=f"unique-{i}") for i in range(6)]
    sufficient_stats = pair_win_rate_statistics(sufficient_records, side_a="a", side_b="b", min_effective_n=5)
    assert sufficient_stats["unique_trajectory_wilson_ci_status"] == "COMPUTED"
    assert sufficient_stats["unique_trajectory_wilson_ci_a"] is not None
    assert sufficient_stats["statistically_interpretable"] is True


def test_deduplicate_by_trajectory_preserves_first_seen_order():
    records = [_win_record("a", digest_value="x"), _win_record("b", digest_value="x"), _win_record("a", digest_value="y")]
    deduped = deduplicate_by_trajectory(records)
    assert [r["winner_participant"] for r in deduped] == ["a", "a"]  # second "x" (winner b) dropped, first wins


def test_bradley_terry_is_always_descriptive_and_reports_connectivity():
    wins = {("a", "b"): 5, ("b", "c"): 3, ("c", "a"): 2}
    result = fit_bradley_terry(wins, participants=["a", "b", "c"])
    assert result["descriptive_only"] is True
    assert result["statistically_supported_ranking"] is False
    assert result["graph_connected"] is True
    assert set(result["log_strength"]) == {"a", "b", "c"}
    assert result["identifiability_warning"] is None


def test_bradley_terry_disconnected_graph_flagged_not_hidden():
    wins = {("a", "b"): 3, ("c", "d"): 4}  # {a,b} and {c,d} never played each other
    result = fit_bradley_terry(wins, participants=["a", "b", "c", "d"])
    assert result["graph_connected"] is False
    assert len(result["components"]) == 2
    assert result["identifiability_warning"] is not None


def test_pairwise_wins_from_records_excludes_draws_and_unknown_winners():
    records = [
        {"participant_a": "a", "participant_b": "b", "winner_participant": "a"},
        {"participant_a": "a", "participant_b": "b", "winner_participant": "b"},
        {"participant_a": "a", "participant_b": "b", "winner_participant": "draw"},
        {"participant_a": "a", "participant_b": "b", "winner_participant": None},
    ]
    wins = pairwise_wins_from_records(records)
    assert wins == {("a", "b"): 1, ("b", "a"): 1}


def test_resume_does_not_duplicate_trajectory_evidence(tmp_path):
    evidence_path = tmp_path / "pair__trajectory.json"
    assert load_trajectory_evidence(evidence_path) == {}
    for index in range(5):
        record_trajectory_evidence(evidence_path, index, {"match_index": index, "complete_trajectory_digest": f"d{index}"})
    first_run = load_trajectory_evidence(evidence_path)
    assert sorted(int(k) for k in first_run) == [0, 1, 2, 3, 4]

    # Simulate a resumed run: only indices 5..9 are newly played (0..4 are
    # skipped by run_actual_league's own resume logic and never re-recorded).
    for index in range(5, 10):
        record_trajectory_evidence(evidence_path, index, {"match_index": index, "complete_trajectory_digest": f"d{index}"})
    final = load_trajectory_evidence(evidence_path)
    assert sorted(int(k) for k in final) == list(range(10))
    assert len(final) == 10  # exactly one entry per match_index, no duplicates
    # entries from before the resume are untouched
    for index in range(5):
        assert final[str(index)]["complete_trajectory_digest"] == f"d{index}"


def test_deterministic_rerun_of_digest_computation():
    game = _sample_game()
    results = [compute_trajectory_digests(game) for _ in range(5)]
    complete_digests = {r["complete_trajectory_digest"] for r in results}
    assert len(complete_digests) == 1


def test_schema_version_change_changes_digest():
    events = _sample_game()
    baseline = compute_trajectory_digests(events)
    bumped = [dict(e, schema_version="o6-public-trajectory-v2") for e in events]
    bumped_digests = compute_trajectory_digests(bumped)
    assert baseline["complete_trajectory_digest"] != bumped_digests["complete_trajectory_digest"]
    assert baseline["initial_observation_digest"] != bumped_digests["initial_observation_digest"]
