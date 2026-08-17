"""正典 §16 の取得ループと §2.3 の seal 判定を、注入した応答で確かめる。

transport も時計も待ちも注入するため、HTTP も credential も実時間も使わずに、
状態遷移、rate limit、resume、seal 判定を検証できる。
"""

from __future__ import annotations

import itertools
from datetime import datetime, timedelta, timezone

import pytest

from mage_ptcg.meta_specialist.census_fetch_v1 import CensusPacerV1, CensusStateStoreV1
from mage_ptcg.meta_specialist.census_pipeline_v1 import (
    CensusPipelineV1Error,
    census_records_from_store_v1,
    require_sealed_census_v1,
    run_census_fetch_pass_v1,
    seal_census_from_store_v1,
)


def _store(tmp_path, rows: int = 2) -> CensusStateStoreV1:
    store = CensusStateStoreV1(tmp_path / "census.sqlite")
    store.seal_census_id("census-test")
    store.enqueue_rows([
        {"rank": index + 1, "team_id": f"team{index}", "score": 100.0 - index,
         "timestamp": "2026-08-01T00:00:00Z"}
        for index in range(rows)
    ])
    return store


def _happy_transport(stage: str, row):
    if stage == "submission":
        return 200, [{"submission_id": f"sub-{row['team_id']}", "public_score": 1.0,
                      "submitted_at": "2026-08-01T00:00:00Z", "status": "complete"}]
    if stage == "episode":
        return 200, [{"episode_id": f"ep-{row['team_id']}", "player_index": 0,
                      "created_at": "2026-08-01T00:00:00Z", "has_both_decks": True}]
    if stage == "replay":
        return 200, {"replay_sha256": "r" * 64}
    if stage == "deck":
        return 200, {"deck_sha256": "d" * 64}
    return 200, {}


def _fixed_clock():
    value = {"now": 0.0}

    def clock() -> float:
        return value["now"]

    return clock, value


def test_a_row_walks_the_whole_state_machine_to_qualified(tmp_path) -> None:
    store = _store(tmp_path, rows=1)
    clock, _ = _fixed_clock()

    progress = run_census_fetch_pass_v1(
        store, transport=_happy_transport, pacer=CensusPacerV1(clock=clock),
        max_requests=20, now_utc=lambda: "2026-08-01T00:00:00+00:00",
    )

    assert progress.state_counts["qualified"] == 1
    assert progress.stopped_reason == "nothing_due"
    row = store.rows()[0]
    assert row["submission_id"] == "sub-team0"
    assert row["episode_id"] == "ep-team0"
    assert row["replay_sha256"] == "r" * 64
    assert row["deck_sha256"] == "d" * 64
    store.close()


def test_a_payload_missing_its_promised_field_is_not_guessed(tmp_path) -> None:
    store = _store(tmp_path, rows=1)
    clock, _ = _fixed_clock()

    def transport(stage, row):
        if stage == "replay":
            return 200, {}  # 200 but no hash
        return _happy_transport(stage, row)

    progress = run_census_fetch_pass_v1(
        store, transport=transport, pacer=CensusPacerV1(clock=clock),
        max_requests=20, now_utc=lambda: "2026-08-01T00:00:00+00:00",
    )

    assert progress.state_counts["terminal_failure"] == 1
    assert "replay_sha256" in str(store.rows()[0]["detail"])
    store.close()


def test_a_rate_limited_row_is_parked_and_the_breaker_stops_the_pass(tmp_path) -> None:
    # Two rows: the first is parked by the 429 and the second is still claimable,
    # so the pass must be stopped by the breaker rather than by running dry.
    store = _store(tmp_path, rows=2)
    clock, _ = _fixed_clock()
    pacer = CensusPacerV1(clock=clock)

    progress = run_census_fetch_pass_v1(
        store, transport=lambda stage, row: (429, {"retry_after_seconds": 30}),
        pacer=pacer, max_requests=5, now_utc=lambda: "2026-08-01T00:00:00+00:00",
    )

    assert progress.rate_limited == 1
    assert progress.requests == 1, "breaker が開いたのに要求を続けている"
    assert pacer.breaker_open is True
    assert progress.stopped_reason.startswith("circuit_breaker_open")
    parked = store.rows()[0]
    assert parked["state"] == "retry_wait"
    assert parked["not_before_utc"] is not None
    store.close()


def test_a_parked_row_resumes_at_the_stage_it_reached_not_at_the_start(tmp_path) -> None:
    """Regression: `retry_wait` は待機状態であって工程上の位置ではない。

    途中で park された row を常に submission から再開すると、取得済みの field を
    捨てて再取得する。quota を無駄にするだけでなく、leaderboard が動いていた場合に
    別の submission を選び直してしまい census が再現しなくなる。
    """
    store = _store(tmp_path, rows=1)
    clock, _ = _fixed_clock()
    # replay まで進んだ row を rate limit で park する。
    calls: list[str] = []

    def transport(stage, row):
        calls.append(stage)
        if stage == "deck":
            return 429, {"retry_after_seconds": 0}
        return _happy_transport(stage, row)

    run_census_fetch_pass_v1(
        store, transport=transport, pacer=CensusPacerV1(clock=clock),
        max_requests=10, now_utc=lambda: "2026-08-01T00:00:00+00:00",
    )
    parked = store.rows()[0]
    assert parked["state"] == "retry_wait"
    assert parked["replay_sha256"] == "r" * 64
    assert calls == ["submission", "episode", "replay", "deck"]

    # 再開: 最初の要求は submission ではなく deck でなければならない。
    calls.clear()

    def resumed(stage, row):
        calls.append(stage)
        return _happy_transport(stage, row)

    run_census_fetch_pass_v1(
        store, transport=resumed, pacer=CensusPacerV1(clock=clock), max_requests=10,
        now_utc=lambda: "2026-08-01T01:00:00+00:00",
    )

    assert calls[0] == "deck", f"resume が工程を巻き戻した: {calls}"
    assert "submission" not in calls, f"取得済みの submission を再取得した: {calls}"
    assert store.rows()[0]["state"] == "qualified"
    store.close()


def test_transient_failures_give_up_after_the_declared_attempt_cap(tmp_path) -> None:
    store = _store(tmp_path, rows=1)
    clock, _ = _fixed_clock()
    # 時計は無限に進む。retry_wait の not_before_utc を追い越せないと、
    # attempt cap に到達する前に "nothing_due" で抜けてしまう。
    ticks = itertools.count()
    base = datetime(2026, 8, 1, tzinfo=timezone.utc)

    def moments() -> str:
        return (base + timedelta(hours=next(ticks))).isoformat()

    progress = run_census_fetch_pass_v1(
        store, transport=lambda stage, row: (503, {}),
        pacer=CensusPacerV1(clock=clock), max_requests=10,
        now_utc=moments,
    )

    assert progress.terminal_failures == 1
    assert store.rows()[0]["state"] == "terminal_failure"
    assert "gave up" in str(store.rows()[0]["detail"])
    store.close()


def test_seal_refuses_when_gold_is_incomplete(tmp_path) -> None:
    store = _store(tmp_path, rows=2)
    clock, _ = _fixed_clock()

    def transport(stage, row):
        # rank 1 (Gold) の deck だけ落とす。
        if stage == "deck" and int(row["rank"]) == 1:
            return 404, {}
        return _happy_transport(stage, row)

    run_census_fetch_pass_v1(
        store, transport=transport, pacer=CensusPacerV1(clock=clock),
        max_requests=40, now_utc=lambda: "2026-08-01T00:00:00+00:00",
    )
    report = seal_census_from_store_v1(store, tier_of_rank=lambda rank: "Gold" if rank == 1 else "Silver")

    assert report.gold_coverage_rate < 1.0
    assert report.is_sealed is False
    with pytest.raises(CensusPipelineV1Error, match="census is not sealed"):
        require_sealed_census_v1(report)
    store.close()


def test_qualifying_a_collected_row_costs_no_request(tmp_path) -> None:
    """Regression: `deck_extracted -> qualified` は取得ではなく手元の検査である。

    ここで要求を出すと quota を無駄にするだけでなく、その stage の resource を
    持たない transport に対しては、完全に収集し終えた row を最後の一歩で
    terminal_failure に落とす。実際に replay-dir transport で 3/3 が落ちた。
    """
    store = _store(tmp_path, rows=1)
    clock, _ = _fixed_clock()
    stages: list[str] = []

    def transport(stage, row):
        stages.append(stage)
        if stage not in ("submission", "episode", "replay", "deck"):
            return 404, {}  # qualify の resource は存在しない
        return _happy_transport(stage, row)

    progress = run_census_fetch_pass_v1(
        store, transport=transport, pacer=CensusPacerV1(clock=clock),
        max_requests=20, now_utc=lambda: "2026-08-01T00:00:00+00:00",
    )

    assert stages == ["submission", "episode", "replay", "deck"]
    assert "qualify" not in stages
    assert progress.requests == 4
    assert progress.state_counts["qualified"] == 1
    assert progress.terminal_failures == 0
    store.close()


def test_a_row_reaching_the_local_check_without_its_fields_fails_closed(tmp_path) -> None:
    store = _store(tmp_path, rows=1)
    clock, _ = _fixed_clock()
    store.advance(rank=1, team_id="team0", state="deck_extracted",
                  fields={"submission_id": "s", "episode_id": "e"})

    progress = run_census_fetch_pass_v1(
        store, transport=lambda stage, row: (200, {}),
        pacer=CensusPacerV1(clock=clock), max_requests=5,
        now_utc=lambda: "2026-08-01T00:00:00+00:00",
    )

    assert progress.state_counts["qualified"] == 0
    assert store.rows()[0]["state"] == "terminal_failure"
    assert "replay_sha256" in str(store.rows()[0]["detail"])
    store.close()


def test_a_qualified_row_missing_a_hash_does_not_count_toward_coverage(tmp_path) -> None:
    """`qualified` に到達していても field が欠けていれば complete としない。"""
    store = _store(tmp_path, rows=1)
    store.advance(rank=1, team_id="team0", state="qualified",
                  fields={"submission_id": "s", "episode_id": "e"})

    records = census_records_from_store_v1(store, tier_of_rank=lambda rank: "Gold")

    assert records[0].is_complete is False
    assert set(records[0].missing_fields) == {"replay_sha256", "deck_sha256"}
    store.close()


def test_sealing_an_empty_store_fails_rather_than_reporting_full_coverage(tmp_path) -> None:
    store = CensusStateStoreV1(tmp_path / "empty.sqlite")

    with pytest.raises(CensusPipelineV1Error, match="no row"):
        census_records_from_store_v1(store, tier_of_rank=lambda rank: "Gold")
    store.close()
