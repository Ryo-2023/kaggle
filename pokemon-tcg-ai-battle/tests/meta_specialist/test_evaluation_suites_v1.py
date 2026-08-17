"""正典 §14.0 / §14.2 の suite が「実行して測れる」ことを振る舞いで確かめる。

field を持つ dataclass があることは、suite を実行できることの証拠にならない。
ここでは既知の結果を注入し、band 別の score・fault・rating-proxy trajectory・
最大 drawdown が正しい値になることを検査する。
"""

from __future__ import annotations

import math

import pytest

from mage_ptcg.meta_specialist.evaluation_suites_v1 import (
    ASCENT_BAND_ORDER_V1,
    INFRASTRUCTURE_FAULT_V1,
    EvaluationSuiteV1,
    EvaluationSuiteV1Error,
    SuiteBlockV1,
    SuiteGameResultV1,
    build_ascent_suite_v1,
    build_top_band_suite_v1,
    rating_proxy_v1,
    run_evaluation_suite_v1,
)


_BAND_MAP = {
    "weak_a": "lower",
    "weak_b": "lower",
    "mid_a": "middle",
    "strong_a": "high",
    "strong_b": "high",
    "unmeasured": "ambiguous",
}
_AVAILABLE = tuple(sorted(_BAND_MAP)) + ("champion_2025", "exploiter_x")


def _result(opponent: str, score: float, *, seat: int = 0, fault: str = "") -> SuiteGameResultV1:
    return SuiteGameResultV1(
        opponent_id=opponent, opponent_version="v1", seat=seat,
        scenario_seed=1, score=score, fault=fault, decision_latencies_ms=(10.0, 20.0),
    )


def test_ascent_suite_orders_blocks_lower_middle_high() -> None:
    suite = build_ascent_suite_v1(band_map=_BAND_MAP, available=_AVAILABLE)

    assert [block.band for block in suite.blocks] == list(ASCENT_BAND_ORDER_V1)
    assert suite.blocks[0].opponent_ids == ("weak_a", "weak_b")
    assert suite.blocks[2].opponent_ids == ("strong_a", "strong_b")


def test_an_ambiguous_opponent_never_enters_a_suite() -> None:
    """`ambiguous` states that strength is not established; it is not a band."""
    suite = build_ascent_suite_v1(band_map=_BAND_MAP, available=_AVAILABLE)

    assert "unmeasured" not in suite.opponent_ids()


def test_top_band_suite_includes_champions_and_exploiters_beside_high_proxies() -> None:
    suite = build_top_band_suite_v1(
        band_map=_BAND_MAP, available=_AVAILABLE,
        historical_champions=("champion_2025",), unused_exploiters=("exploiter_x",),
    )

    assert set(suite.opponent_ids()) == {"strong_a", "strong_b", "champion_2025", "exploiter_x"}


def test_top_band_suite_refuses_a_champion_that_is_not_in_the_pool() -> None:
    with pytest.raises(EvaluationSuiteV1Error, match="not in the available pool"):
        build_top_band_suite_v1(
            band_map=_BAND_MAP, available=_AVAILABLE, historical_champions=("ghost",),
        )


def test_a_suite_with_no_decided_band_fails_rather_than_measuring_nothing() -> None:
    with pytest.raises(EvaluationSuiteV1Error, match="would\n?\\s*measure nothing|measure nothing"):
        build_ascent_suite_v1(band_map={"x": "ambiguous"}, available=("x",))


def test_schedule_id_changes_when_the_played_set_changes() -> None:
    first = build_ascent_suite_v1(band_map=_BAND_MAP, available=_AVAILABLE)
    smaller = build_ascent_suite_v1(
        band_map=_BAND_MAP, available=tuple(x for x in _AVAILABLE if x != "weak_b")
    )

    assert first.schedule_id() != smaller.schedule_id()
    assert first.schedule_id() == build_ascent_suite_v1(
        band_map=_BAND_MAP, available=_AVAILABLE
    ).schedule_id()


def test_running_a_suite_reports_score_fault_trajectory_and_drawdown_per_band() -> None:
    suite = build_ascent_suite_v1(band_map=_BAND_MAP, available=_AVAILABLE)

    def play_block(band: str, opponents: tuple[str, ...]):
        if band == "lower":
            # Wins then losses, so the trajectory must rise and then fall back.
            return [_result(opponents[0], 1.0), _result(opponents[0], 1.0),
                    _result(opponents[1], 0.0), _result(opponents[1], 0.0)]
        if band == "middle":
            return [_result(opponents[0], 1.0), _result(opponents[0], 0.0, seat=1)]
        return [_result(opponents[0], 0.0), _result(opponents[1], 0.0)]

    report = run_evaluation_suite_v1(suite, play_block=play_block)

    assert [band.band for band in report.bands] == list(ASCENT_BAND_ORDER_V1)
    lower, middle, high = report.bands
    assert lower.score_rate == 0.5
    assert lower.games == 4
    # Peak is after the second win; the two losses pull it down. Drawdown > 0.
    assert lower.rating_proxy_trajectory[1] == max(lower.rating_proxy_trajectory)
    assert lower.max_drawdown > 0.0
    assert lower.max_drawdown == pytest.approx(
        max(lower.rating_proxy_trajectory) - lower.rating_proxy_trajectory[-1]
    )
    assert middle.seat_score_rates == {"0": 1.0, "1": 0.0}
    assert high.score_rate == 0.0
    assert report.games == 8
    assert report.requires_rerun is False


def test_a_steadily_winning_trajectory_has_no_drawdown() -> None:
    suite = EvaluationSuiteV1(
        suite_name="top_band_suite",
        blocks=(SuiteBlockV1(band="high", opponent_ids=("strong_a",)),),
    )

    report = run_evaluation_suite_v1(
        suite, play_block=lambda band, ids: [_result(ids[0], 1.0) for _ in range(5)]
    )

    assert report.bands[0].max_drawdown == 0.0
    assert all(math.isfinite(value) for value in report.bands[0].rating_proxy_trajectory)


def test_worst_opponent_and_opponent_equal_rates_are_not_the_pooled_mean() -> None:
    """A heavily played easy matchup must not hide a losing one."""
    suite = EvaluationSuiteV1(
        suite_name="top_band_suite",
        blocks=(SuiteBlockV1(band="high", opponent_ids=("strong_a", "strong_b")),),
    )

    def play_block(band, ids):
        return [_result("strong_a", 1.0) for _ in range(9)] + [_result("strong_b", 0.0)]

    band = run_evaluation_suite_v1(suite, play_block=play_block).bands[0]

    assert band.score_rate == pytest.approx(0.9)
    assert band.opponent_equal_score_rate == pytest.approx(0.5)
    assert band.worst_opponent_id == "strong_b"
    assert band.worst_opponent_score_rate == 0.0


def test_a_counted_fault_cannot_also_report_a_win() -> None:
    with pytest.raises(EvaluationSuiteV1Error, match="zero condition"):
        _result("strong_a", 1.0, fault="illegal")


def test_faults_are_rated_and_infrastructure_failures_force_a_rerun() -> None:
    suite = EvaluationSuiteV1(
        suite_name="top_band_suite",
        blocks=(SuiteBlockV1(band="high", opponent_ids=("strong_a",)),),
    )

    def play_block(band, ids):
        return [
            _result("strong_a", 1.0),
            _result("strong_a", 0.0, fault="timeout"),
            _result("strong_a", 0.0, fault="illegal"),
            _result("strong_a", 0.0, fault=INFRASTRUCTURE_FAULT_V1),
        ]

    report = run_evaluation_suite_v1(suite, play_block=play_block)
    band = report.bands[0]

    assert band.games == 3, "an infrastructure failure is not a measured game"
    assert band.fault_rate == pytest.approx(2 / 3)
    assert band.timeout_rate == pytest.approx(1 / 3)
    assert band.illegal_rate == pytest.approx(1 / 3)
    assert band.infrastructure_failures == 1
    assert report.requires_rerun is True


def test_a_block_that_plays_an_opponent_outside_its_own_block_is_refused() -> None:
    suite = EvaluationSuiteV1(
        suite_name="top_band_suite",
        blocks=(SuiteBlockV1(band="high", opponent_ids=("strong_a",)),),
    )

    with pytest.raises(EvaluationSuiteV1Error, match="outside its block"):
        run_evaluation_suite_v1(
            suite, play_block=lambda band, ids: [_result("someone_else", 1.0)]
        )


def test_rating_proxy_is_monotone_and_finite_at_the_extremes() -> None:
    assert rating_proxy_v1(0.5, 10) == pytest.approx(0.0)
    assert rating_proxy_v1(1.0, 10) > rating_proxy_v1(0.75, 10) > 0.0
    assert rating_proxy_v1(0.0, 10) < rating_proxy_v1(0.25, 10) < 0.0
    assert math.isfinite(rating_proxy_v1(1.0, 1))


def test_latency_percentiles_come_from_the_measured_games() -> None:
    suite = EvaluationSuiteV1(
        suite_name="top_band_suite",
        blocks=(SuiteBlockV1(band="high", opponent_ids=("strong_a",)),),
    )

    def play_block(band, ids):
        return [
            SuiteGameResultV1(
                opponent_id="strong_a", opponent_version="v1", seat=0, scenario_seed=1,
                score=1.0, decision_latencies_ms=tuple(float(v) for v in range(1, 101)),
            )
        ]

    band = run_evaluation_suite_v1(suite, play_block=play_block).bands[0]

    assert band.latency_p50_ms == 50.0
    assert band.latency_p95_ms == 95.0
    assert band.latency_p99_ms == 99.0
