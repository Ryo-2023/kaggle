"""census 取得の状態機械・pacing・resume の契約 (正典 §16)。

ネットワークも credential も使わない。transport と時計を注入する設計なので、
取得規律そのものを秒を消費せずに検証できる。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mage_ptcg.meta_specialist.census_fetch_v1 import (
    DEFAULT_COOLDOWN_SECONDS_V1,
    INITIAL_INTERVAL_SECONDS_V1,
    MIN_INTERVAL_SECONDS_V1,
    SHRINK_WINDOW_SUCCESSES_V1,
    STATES_V1,
    CensusFetchV1Error,
    CensusPacerV1,
    CensusStateStoreV1,
    classify_status_v1,
    select_episode_v1,
    select_submission_v1,
)


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


# ---------------------------------------------------------------------------
# pacing / circuit breaker
# ---------------------------------------------------------------------------


def test_the_first_rate_limit_opens_the_breaker_and_obeys_retry_after() -> None:
    """正典 §16: 最初の 429 で circuit breaker を開き、`Retry-After` を厳守する."""
    clock = _Clock()
    pacer = CensusPacerV1(clock=clock)
    pacer.after_rate_limit(retry_after_seconds=120.0)

    assert pacer.breaker_open
    decision = pacer.before_request()
    assert decision.breaker_open and decision.sleep_seconds == pytest.approx(120.0)

    clock.advance(119.0)
    assert pacer.before_request().breaker_open
    clock.advance(2.0)
    assert not pacer.before_request().breaker_open


def test_without_retry_after_the_cooldown_grows_exponentially() -> None:
    """header が無い場合は 60 秒から指数的に伸ばす."""
    clock = _Clock()
    pacer = CensusPacerV1(clock=clock)

    pacer.after_rate_limit(retry_after_seconds=None)
    first = pacer.before_request().sleep_seconds
    assert first == pytest.approx(DEFAULT_COOLDOWN_SECONDS_V1)

    clock.advance(first + 1.0)
    pacer.before_request()  # consume the probe slot
    pacer.after_rate_limit(retry_after_seconds=None)
    second = pacer.before_request().sleep_seconds
    assert second == pytest.approx(DEFAULT_COOLDOWN_SECONDS_V1 * 2)


def test_exactly_one_probe_is_allowed_when_the_cooldown_elapses() -> None:
    """解除時に許されるのは probe 1 件だけ."""
    clock = _Clock()
    pacer = CensusPacerV1(clock=clock)
    pacer.after_rate_limit(retry_after_seconds=10.0)
    clock.advance(11.0)

    first = pacer.before_request()
    assert first.probe and not first.breaker_open

    pacer.after_success()
    assert not pacer.before_request().probe, "the probe slot must not persist after it succeeds"


def test_the_interval_shrinks_only_after_a_full_clean_window() -> None:
    """100 成功かつ 429 なしの window ごとに 10% だけ短縮する."""
    clock = _Clock()
    pacer = CensusPacerV1(clock=clock)
    assert pacer.interval_seconds == pytest.approx(INITIAL_INTERVAL_SECONDS_V1)

    for _ in range(SHRINK_WINDOW_SUCCESSES_V1 - 1):
        pacer.after_success()
    assert pacer.interval_seconds == pytest.approx(INITIAL_INTERVAL_SECONDS_V1)

    pacer.after_success()
    assert pacer.interval_seconds == pytest.approx(INITIAL_INTERVAL_SECONDS_V1 * 0.9)


def test_a_rate_limit_resets_the_shrink_window() -> None:
    """429 を挟んだら window を数え直すこと."""
    clock = _Clock()
    pacer = CensusPacerV1(clock=clock)
    for _ in range(SHRINK_WINDOW_SUCCESSES_V1 - 1):
        pacer.after_success()
    pacer.after_rate_limit(retry_after_seconds=1.0)
    clock.advance(2.0)
    pacer.before_request()
    pacer.after_success()
    assert pacer.interval_seconds == pytest.approx(INITIAL_INTERVAL_SECONDS_V1)


def test_the_interval_never_falls_below_the_floor() -> None:
    pacer = CensusPacerV1(clock=_Clock())
    for _ in range(SHRINK_WINDOW_SUCCESSES_V1 * 60):
        pacer.after_success()
    assert pacer.interval_seconds >= MIN_INTERVAL_SECONDS_V1


@pytest.mark.parametrize(
    "status,expected",
    [
        (200, "success"), (429, "rate_limited"),
        (408, "transient"), (500, "transient"), (503, "transient"),
        (401, "terminal"), (403, "terminal"), (404, "terminal"),
    ],
)
def test_status_classes_are_fixed(status: int, expected: str) -> None:
    """分類は config version に固定する (正典 §16)."""
    assert classify_status_v1(status) == expected


# ---------------------------------------------------------------------------
# state store / resume
# ---------------------------------------------------------------------------


def test_the_store_survives_a_process_restart(tmp_path: Path) -> None:
    """中断からの再開: 進捗が失われないこと."""
    path = tmp_path / "census.sqlite"
    with CensusStateStoreV1(path) as store:
        store.seal_census_id("c" * 64)
        store.enqueue_rows([{"rank": 1, "team_id": "t1"}, {"rank": 2, "team_id": "t2"}])
        store.advance(rank=1, team_id="t1", state="deck_extracted",
                      fields={"deck_sha256": "d" * 64})

    with CensusStateStoreV1(path) as reopened:
        assert reopened.census_id() == "c" * 64
        counts = reopened.state_counts()
        assert counts["deck_extracted"] == 1 and counts["pending"] == 1


def test_a_resume_cannot_switch_the_sealed_snapshot(tmp_path: Path) -> None:
    """census_id は resume 中に変えられない (正典 §16)."""
    with CensusStateStoreV1(tmp_path / "c.sqlite") as store:
        store.seal_census_id("a" * 64)
        with pytest.raises(CensusFetchV1Error):
            store.seal_census_id("b" * 64)


def test_claim_next_skips_rows_that_are_backing_off(tmp_path: Path) -> None:
    """`retry_wait` は `not_before_utc` を過ぎるまで再取得しないこと."""
    with CensusStateStoreV1(tmp_path / "c.sqlite") as store:
        store.enqueue_rows([{"rank": 1, "team_id": "t1"}, {"rank": 2, "team_id": "t2"}])
        store.advance(rank=1, team_id="t1", state="retry_wait",
                      not_before_utc="2999-01-01T00:00:00Z", attempt=1)
        claimed = store.claim_next(now_utc="2026-08-05T00:00:00Z")
        assert claimed is not None and claimed["team_id"] == "t2"


def test_terminal_rows_are_never_reclaimed(tmp_path: Path) -> None:
    with CensusStateStoreV1(tmp_path / "c.sqlite") as store:
        store.enqueue_rows([{"rank": 1, "team_id": "t1"}])
        store.advance(rank=1, team_id="t1", state="terminal_failure", detail="403")
        assert store.claim_next(now_utc="2026-08-05T00:00:00Z") is None


def test_enqueue_is_idempotent_so_a_resume_does_not_reset_progress(tmp_path: Path) -> None:
    with CensusStateStoreV1(tmp_path / "c.sqlite") as store:
        rows = [{"rank": 1, "team_id": "t1"}]
        assert store.enqueue_rows(rows) == 1
        store.advance(rank=1, team_id="t1", state="qualified")
        assert store.enqueue_rows(rows) == 0
        assert store.state_counts()["qualified"] == 1


def test_an_unknown_state_is_refused(tmp_path: Path) -> None:
    with CensusStateStoreV1(tmp_path / "c.sqlite") as store:
        store.enqueue_rows([{"rank": 1, "team_id": "t1"}])
        with pytest.raises(CensusFetchV1Error):
            store.advance(rank=1, team_id="t1", state="almost_done")


def test_all_eight_canonical_states_exist() -> None:
    assert set(STATES_V1) == {
        "pending", "submission_fixed", "episode_fixed", "replay_fetched",
        "deck_extracted", "qualified", "retry_wait", "terminal_failure",
    }


# ---------------------------------------------------------------------------
# deterministic selection
# ---------------------------------------------------------------------------


def test_submission_selection_follows_the_fixed_tie_break() -> None:
    """public score 最大 → submitted-at 新しい順 → submission ID 小さい順."""
    chosen = select_submission_v1([
        {"submission_id": "b", "status": "complete", "public_score": 800.0,
         "submitted_at": "2026-08-01T00:00:00Z"},
        {"submission_id": "a", "status": "complete", "public_score": 900.0,
         "submitted_at": "2026-07-01T00:00:00Z"},
        {"submission_id": "c", "status": "complete", "public_score": 900.0,
         "submitted_at": "2026-08-02T00:00:00Z"},
    ])
    assert chosen["submission_id"] == "c", "newest wins among equal top scores"


def test_an_unscored_submission_is_never_selected() -> None:
    with pytest.raises(CensusFetchV1Error):
        select_submission_v1([
            {"submission_id": "a", "status": "error", "public_score": 900.0},
            {"submission_id": "b", "status": "complete", "public_score": None},
        ])


def test_episode_selection_requires_both_decks_and_is_deterministic() -> None:
    chosen = select_episode_v1([
        {"episode_id": "9", "has_both_decks": True},
        {"episode_id": "2", "has_both_decks": False},
        {"episode_id": "3", "has_both_decks": True},
    ])
    assert chosen["episode_id"] == "3"

    with pytest.raises(CensusFetchV1Error):
        select_episode_v1([{"episode_id": "1", "has_both_decks": False}])
