"""正典 §16 の取得状態機械を回し、§2.3 の seal と §2.4 のレポートまで繋ぐ。

`census_fetch_v1` は 8 状態の SQLite 状態機械、pacing、circuit breaker、resume を
持つが、それを回す側が無かった。`census_v1` は Gold 100% / 全体 98% の seal 判定を
持つが、判定する対象を作る経路が無かった。この module がその間を埋める。

## 責務の境界

- **transport は注入する。** HTTP、認証、Kaggle API の形をここへ持ち込まない。
  ``CensusTransportV1`` は ``(status, payload)`` を返すだけであり、実行時は
  ``scripts/run_census_fetch.py`` が本物を渡し、テストは既知の応答を渡す。
- **tier はここで決めない。** Gold / Silver / Bronze は leaderboard の medal band
  であり、順位から推測する閾値をこの module が持つと、census の意味が実装依存に
  なる。``tier_of_rank`` として呼び出し側から渡す。
- **待ち時間は注入する。** ``sleep`` を直接呼ばないので、pacing の検証に実時間を
  消費しない。

## fail-closed

- 状態機械が知らない状態や、stage が返した payload に必要な field が無い場合は、
  その row を ``terminal_failure`` にして理由を残す。推測で埋めない。
- transient 失敗は ``retry_wait`` へ落とし、``MAX_TRANSIENT_ATTEMPTS_V1`` を超えたら
  ``terminal_failure`` にする。無限に再試行しない。
- seal は ``verify_census_seal_v1`` の判定をそのまま返す。閾値を緩めない。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping, Sequence

from mage_ptcg.meta_specialist.census_fetch_v1 import (
    MAX_TRANSIENT_ATTEMPTS_V1,
    CensusFetchV1Error,
    CensusPacerV1,
    CensusStateStoreV1,
    CensusTransportV1,
    classify_status_v1,
    select_episode_v1,
    select_submission_v1,
)
from mage_ptcg.meta_specialist.census_v1 import (
    CensusRecordV1,
    CensusSealReportV1,
    verify_census_seal_v1,
)


CENSUS_PIPELINE_SCHEMA_V1 = "meta-specialist-census-pipeline-v1"

# 状態 -> (transport へ渡す stage 名, 成功時の次状態, payload から取り出す field)
_STAGE_PLAN_V1: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "pending": ("submission", "submission_fixed", ("submission_id",)),
    "submission_fixed": ("episode", "episode_fixed", ("episode_id", "player_index")),
    "episode_fixed": ("replay", "replay_fetched", ("replay_sha256",)),
    "replay_fetched": ("deck", "deck_extracted", ("deck_sha256",)),
}

# `deck_extracted -> qualified` is a local check, not a fetch: every field the
# census needs is already in the row.  Issuing a request for it spends quota on
# nothing and, against any transport that has no such resource, turns a fully
# collected row into a terminal failure at the last step.
_LOCAL_TERMINAL_STATE_V1 = "deck_extracted"

# `retry_wait` is a waiting state, not a position in the pipeline: a row can be
# parked there from any stage.  Resuming it always at `submission` would discard
# every field already fetched and re-request them, so the effective position is
# recovered from which fields the row actually carries.
_RESUME_ORDER_V1: tuple[tuple[str, str], ...] = (
    ("submission_id", "pending"),
    ("episode_id", "submission_fixed"),
    ("replay_sha256", "episode_fixed"),
    ("deck_sha256", "replay_fetched"),
)

# row が完全とみなされるための field。欠けたものが missing_fields になる。
_REQUIRED_ROW_FIELDS_V1: tuple[str, ...] = (
    "submission_id", "episode_id", "replay_sha256", "deck_sha256",
)


class CensusPipelineV1Error(ValueError):
    """Raised when the pipeline is asked for something it cannot do honestly."""


@dataclass(frozen=True, slots=True)
class CensusFetchProgressV1:
    """What one pass of the fetch loop did."""

    requests: int
    advanced: int
    rate_limited: int
    transient_failures: int
    terminal_failures: int
    state_counts: Mapping[str, int]
    stopped_reason: str

    def to_dict(self) -> dict[str, object]:
        return {
            "requests": self.requests,
            "advanced": self.advanced,
            "rate_limited": self.rate_limited,
            "transient_failures": self.transient_failures,
            "terminal_failures": self.terminal_failures,
            "state_counts": dict(self.state_counts),
            "stopped_reason": self.stopped_reason,
        }


def _utc_now_text() -> str:
    return datetime.now(timezone.utc).isoformat()


def _retry_at(now_utc: str, seconds: float) -> str:
    try:
        moment = datetime.fromisoformat(now_utc)
    except ValueError as exc:
        raise CensusPipelineV1Error(f"now_utc {now_utc!r} is not ISO-8601") from exc
    return (moment + timedelta(seconds=max(0.0, seconds))).isoformat()


def _retry_after_seconds_v1(payload: Any) -> float | None:
    """A ``Retry-After`` the server actually sent, or ``None`` to use the ladder."""
    if isinstance(payload, Mapping):
        value = payload.get("retry_after_seconds", payload.get("Retry-After"))
        if isinstance(value, (int, float)) and value >= 0:
            return float(value)
    return None


def _effective_state_v1(row: Mapping[str, Any]) -> str:
    """The stage a row should resume at.

    For every state but ``retry_wait`` this is the stored state.  A parked row
    resumes where its fetched fields say it got to, so a rate limit at the deck
    stage does not silently re-run the submission and episode stages -- which
    would burn quota and, worse, re-select a submission if the leaderboard moved.
    """
    state = str(row["state"])
    if state != "retry_wait":
        return state
    for field_name, resume_state in _RESUME_ORDER_V1:
        if not row.get(field_name):
            return resume_state
    return "deck_extracted"


def _extract_fields_v1(
    stage: str, payload: Any, wanted: Sequence[str]
) -> dict[str, Any]:
    """Pull the fields a stage promises, refusing a payload that lacks them.

    ``submission`` and ``episode`` payloads are candidate lists and go through the
    canon's deterministic tie-breaks, so a re-fetch selects the same row and the
    census stays reproducible.
    """
    if stage == "submission":
        if not isinstance(payload, list) or not payload:
            raise CensusPipelineV1Error("submission stage returned no candidate list")
        chosen = select_submission_v1(payload)
    elif stage == "episode":
        if not isinstance(payload, list) or not payload:
            raise CensusPipelineV1Error("episode stage returned no candidate list")
        chosen = select_episode_v1(payload)
    elif isinstance(payload, Mapping):
        chosen = payload
    else:
        raise CensusPipelineV1Error(f"{stage} stage returned {type(payload).__name__}, not a mapping")

    extracted: dict[str, Any] = {}
    for name in wanted:
        if name not in chosen or chosen[name] in (None, ""):
            raise CensusPipelineV1Error(
                f"{stage} stage payload has no {name!r}; the row is left unqualified "
                "rather than filled with a guess"
            )
        extracted[name] = chosen[name]
    return extracted


def run_census_fetch_pass_v1(
    store: CensusStateStoreV1,
    *,
    transport: CensusTransportV1,
    pacer: CensusPacerV1,
    max_requests: int,
    sleep: Callable[[float], None] = lambda _seconds: None,
    now_utc: Callable[[], str] = _utc_now_text,
    on_progress: Callable[[Mapping[str, Any]], None] | None = None,
) -> CensusFetchProgressV1:
    """Drive the §16 state machine until nothing is due or the budget is spent.

    Returns rather than loops forever: the caller owns how long a census run may
    take, and a resume picks up exactly where this left off because every
    transition is committed to the store before the next request.
    """
    if type(max_requests) is not int or max_requests < 1:
        raise CensusPipelineV1Error("max_requests must be a positive int")
    requests = advanced = rate_limited = transient = terminal = 0
    stopped = "budget_exhausted"

    while requests < max_requests:
        row = store.claim_next(now_utc=now_utc())
        if row is None:
            stopped = "nothing_due"
            break
        state = _effective_state_v1(row)
        if state == _LOCAL_TERMINAL_STATE_V1:
            missing = [name for name in _REQUIRED_ROW_FIELDS_V1 if not row.get(name)]
            store.advance(
                rank=int(row["rank"]), team_id=str(row["team_id"]),
                state="terminal_failure" if missing else "qualified",
                detail=f"missing {missing}" if missing else None,
            )
            if missing:
                terminal += 1
            else:
                advanced += 1
            continue
        plan = _STAGE_PLAN_V1.get(state)
        if plan is None:
            store.advance(
                rank=int(row["rank"]), team_id=str(row["team_id"]),
                state="terminal_failure", detail=f"no stage plan for state {state!r}",
            )
            terminal += 1
            continue
        stage, next_state, wanted = plan

        decision = pacer.before_request()
        if decision.sleep_seconds > 0:
            sleep(decision.sleep_seconds)
        if decision.breaker_open and not decision.probe:
            stopped = f"circuit_breaker_open: {decision.reason}"
            break

        status, payload = transport(stage, row)
        requests += 1
        kind = classify_status_v1(int(status))

        if kind == "success":
            pacer.after_success()
            try:
                fields = _extract_fields_v1(stage, payload, wanted)
            except (CensusPipelineV1Error, CensusFetchV1Error) as exc:
                # The canon's selectors reject a candidate set they cannot resolve
                # deterministically.  That is one row's problem, not the pass's:
                # letting it propagate would abandon every remaining row and lose
                # the pacing state along with it.
                store.advance(
                    rank=int(row["rank"]), team_id=str(row["team_id"]),
                    state="terminal_failure", last_status=int(status), detail=str(exc),
                )
                terminal += 1
                continue
            store.advance(
                rank=int(row["rank"]), team_id=str(row["team_id"]),
                state=next_state, fields=fields, attempt=0, last_status=int(status),
            )
            advanced += 1
        elif kind == "rate_limited":
            # Obey a server-supplied Retry-After exactly when the payload carries
            # one; guessing a shorter wait is how a breaker becomes decorative.
            pacer.after_rate_limit(
                retry_after_seconds=_retry_after_seconds_v1(payload)
            )
            rate_limited += 1
            store.advance(
                rank=int(row["rank"]), team_id=str(row["team_id"]), state="retry_wait",
                attempt=int(row["attempt"]),
                not_before_utc=_retry_at(now_utc(), pacer.interval_seconds),
                last_status=int(status), detail="rate limited",
            )
        elif kind == "transient":
            pacer.after_transient()
            attempt = int(row["attempt"]) + 1
            transient += 1
            if attempt >= MAX_TRANSIENT_ATTEMPTS_V1:
                store.advance(
                    rank=int(row["rank"]), team_id=str(row["team_id"]),
                    state="terminal_failure", attempt=attempt, last_status=int(status),
                    detail=f"gave up after {attempt} transient failures",
                )
                terminal += 1
            else:
                store.advance(
                    rank=int(row["rank"]), team_id=str(row["team_id"]), state="retry_wait",
                    attempt=attempt,
                    not_before_utc=_retry_at(now_utc(), pacer.interval_seconds * attempt),
                    last_status=int(status), detail="transient failure",
                )
        else:
            pacer.after_transient()
            terminal += 1
            store.advance(
                rank=int(row["rank"]), team_id=str(row["team_id"]),
                state="terminal_failure", last_status=int(status),
                detail=f"terminal status {status}",
            )

        if on_progress is not None:
            on_progress({
                "requests": requests, "advanced": advanced,
                "rate_limited": rate_limited, "state": next_state,
            })

    return CensusFetchProgressV1(
        requests=requests, advanced=advanced, rate_limited=rate_limited,
        transient_failures=transient, terminal_failures=terminal,
        state_counts=store.state_counts(), stopped_reason=stopped,
    )


def census_records_from_store_v1(
    store: CensusStateStoreV1, *, tier_of_rank: Callable[[int], str],
) -> tuple[CensusRecordV1, ...]:
    """Project the fetch store onto the records the seal check is defined over.

    A row counts as complete only when it reached ``qualified`` *and* carries
    every required field.  A row that reached ``qualified`` with a missing hash
    would otherwise be counted toward coverage it did not earn.
    """
    records: list[CensusRecordV1] = []
    for row in store.rows():
        rank = int(row["rank"])
        missing = tuple(
            name for name in _REQUIRED_ROW_FIELDS_V1 if not row.get(name)
        )
        records.append(CensusRecordV1(
            record_id=f"{rank}:{row['team_id']}",
            tier=tier_of_rank(rank),
            is_complete=str(row["state"]) == "qualified" and not missing,
            missing_fields=missing,
            deck_hash=str(row.get("deck_sha256") or ""),
            replay_hash=str(row.get("replay_sha256") or ""),
        ))
    if not records:
        raise CensusPipelineV1Error(
            "the census store has no row; enqueue the leaderboard before sealing"
        )
    return tuple(records)


def seal_census_from_store_v1(
    store: CensusStateStoreV1, *, tier_of_rank: Callable[[int], str],
) -> CensusSealReportV1:
    """Run the §2.3 seal check over whatever the fetch has actually collected."""
    return verify_census_seal_v1(census_records_from_store_v1(store, tier_of_rank=tier_of_rank))


def require_sealed_census_v1(report: CensusSealReportV1) -> CensusSealReportV1:
    """Fail closed when the census is not sealed, naming what is short.

    Downstream (§2.4 report, seed qualification) treats census numbers as current
    fact.  Letting an unsealed census through would make every number after it
    provisional without saying so.
    """
    if report.is_sealed:
        return report
    raise CensusPipelineV1Error(
        "census is not sealed: Gold coverage "
        f"{report.gold_coverage_rate:.4f} (needs 1.0), total coverage "
        f"{report.total_coverage_rate:.4f} (needs >= 0.98). "
        f"missing-field sensitivity: {report.missing_sensitivity}"
    )


__all__ = [
    "CENSUS_PIPELINE_SCHEMA_V1",
    "CensusFetchProgressV1",
    "CensusPipelineV1Error",
    "census_records_from_store_v1",
    "require_sealed_census_v1",
    "run_census_fetch_pass_v1",
    "seal_census_from_store_v1",
]
