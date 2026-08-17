from __future__ import annotations

from pathlib import Path

from mage_ptcg.league.actual_runner import ActualLeagueConfig, deterministic_schedule, run_actual_league


def test_actual_league_schedule_resume_and_summary(tmp_path: Path) -> None:
    config = ActualLeagueConfig("rule-agent-v0", "rule-v1", 20, 100, "deck", "cabt")
    assert [item["champion_player_index"] for item in deterministic_schedule(config)] == [index % 2 for index in range(20)]
    calls: list[int] = []

    def run_match(item):
        calls.append(item["match_index"])
        winner = "champion" if item["match_index"] % 3 else "draw"
        return {"status": "DONE", "winner_agent": winner, "elapsed_seconds": 0.1, "fallback_count": 0}

    output = tmp_path / "league.json"
    first = run_actual_league(config, output_path=output, run_match=run_match)
    assert first["games"] == 20
    assert first["invalid_actions"] == first["crashes"] == first["timeouts"] == 0
    assert first["match_latency_seconds"]["p95"] == 0.1
    assert first["seat_wld"]["champion_player_0"] == {"wins": 6, "losses": 0, "draws": 4}
    assert first["seat_wld"]["champion_player_1"] == {"wins": 7, "losses": 0, "draws": 3}
    assert calls == list(range(20))
    assert run_actual_league(config, output_path=output, run_match=run_match) == first
    assert calls == list(range(20))


def test_actual_league_drops_callback_private_fields(tmp_path: Path) -> None:
    config = ActualLeagueConfig("rule-agent-v0", "deterministic", 2, 0, "deck", "cabt")
    result = run_actual_league(
        config,
        output_path=tmp_path / "league.json",
        run_match=lambda _item: {
            "status": "DONE", "winner_agent": "draw", "elapsed_seconds": 0.1,
            "fallback_count": 0, "raw_observation": {"hand": [{"id": 1}]},
        },
    )
    expected = {
        "status", "winner_agent", "elapsed_seconds", "fallback_count", "match_index",
        "champion_status", "challenger_status", "champion_fallback_count", "challenger_fallback_count", "steps",
    }
    assert all(set(record) == expected for record in result["records"])
    assert "raw_observation" not in str(result)


def test_summary_attributes_faults_by_champion_and_challenger_seat(tmp_path: Path) -> None:
    # 4 games, champion seat alternates 0,1,0,1. Game 0: champion (seat 0)
    # is invalid. Game 1: challenger (seat 0 this time, since champion is
    # seat 1) crashes (ERROR). Games 2-3: both DONE cleanly.
    config = ActualLeagueConfig("candidate", "opponent", 4, 0, "deck", "cabt")

    def run_match(item):
        index = int(item["match_index"])
        if index == 0:
            return {"status": "AGENT_INVALID", "winner_agent": None, "elapsed_seconds": 0.1,
                     "champion_status": "INVALID", "challenger_status": "DONE",
                     "champion_fallback_count": 0, "challenger_fallback_count": 0}
        if index == 1:
            return {"status": "AGENT_ERROR", "winner_agent": None, "elapsed_seconds": 0.1,
                     "champion_status": "DONE", "challenger_status": "ERROR",
                     "champion_fallback_count": 2, "challenger_fallback_count": 0}
        return {"status": "DONE", "winner_agent": "champion", "elapsed_seconds": 0.1,
                 "champion_status": "DONE", "challenger_status": "DONE",
                 "champion_fallback_count": 0, "challenger_fallback_count": 0}

    result = run_actual_league(config, output_path=tmp_path / "league.json", run_match=run_match)
    assert result["attribution_available"] is True
    assert result["candidate_invalid"] == 1
    assert result["candidate_exception"] == 0
    assert result["candidate_timeout"] == 0
    assert result["opponent_invalid"] == 0
    assert result["opponent_exception"] == 1
    assert result["opponent_timeout"] == 0
    assert result["candidate_fallback_total"] == 2
    assert result["candidate_fallback_games"] == 1
    assert result["opponent_fallback_total"] == 0
    assert result["opponent_fallback_games"] == 0


def test_summary_marks_attribution_unavailable_for_legacy_run_match_without_seat_fields(tmp_path: Path) -> None:
    config = ActualLeagueConfig("candidate", "opponent", 2, 0, "deck", "cabt")

    def legacy_run_match(_item):
        return {"status": "DONE", "winner_agent": "champion", "elapsed_seconds": 0.1, "fallback_count": 0}

    result = run_actual_league(config, output_path=tmp_path / "league.json", run_match=legacy_run_match)
    assert result["attribution_available"] is False
    assert result["attribution_missing_games"] == 2
    for record in result["records"]:
        assert record["champion_status"] == "NOT_OBSERVABLE"
        assert record["challenger_status"] == "NOT_OBSERVABLE"
        assert record["champion_fallback_count"] == "NOT_OBSERVABLE"
        assert record["challenger_fallback_count"] == "NOT_OBSERVABLE"


def test_summary_partial_attribution_still_counts_observed_records(tmp_path: Path) -> None:
    config = ActualLeagueConfig("candidate", "opponent", 2, 0, "deck", "cabt")

    def mixed_run_match(item):
        if int(item["match_index"]) == 0:
            return {"status": "AGENT_INVALID", "winner_agent": None, "elapsed_seconds": 0.1,
                     "champion_status": "INVALID", "challenger_status": "DONE",
                     "champion_fallback_count": 0, "challenger_fallback_count": 0}
        return {"status": "DONE", "winner_agent": "champion", "elapsed_seconds": 0.1}

    result = run_actual_league(config, output_path=tmp_path / "league.json", run_match=mixed_run_match)
    assert result["attribution_available"] is False
    assert result["attribution_missing_games"] == 1


def test_unknown_seat_status_also_marks_attribution_unavailable(tmp_path: Path) -> None:
    # Independent-audit regression: "UNKNOWN" (a status that WAS observed
    # but could not be classified as DONE/INVALID/ERROR/TIMEOUT) must be
    # just as disqualifying for attribution_available as NOT_OBSERVABLE --
    # a previous revision only checked for NOT_OBSERVABLE, so a genuinely
    # unclassifiable status silently read as "fully attributed".
    config = ActualLeagueConfig("candidate", "opponent", 2, 0, "deck", "cabt")

    def run_match(_item):
        return {
            "status": "DONE", "winner_agent": "champion", "elapsed_seconds": 0.1,
            "champion_status": "UNKNOWN", "challenger_status": "DONE",
            "champion_fallback_count": 0, "challenger_fallback_count": 0,
        }

    result = run_actual_league(config, output_path=tmp_path / "league.json", run_match=run_match)
    assert result["attribution_available"] is False
    assert result["attribution_missing_games"] == 2
    assert result["candidate_invalid"] == 0
    assert result["candidate_exception"] == 0
    assert result["candidate_timeout"] == 0
