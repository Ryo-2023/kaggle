"""固定 leaderboard snapshot から deck を復元する、再開可能な census 取得器。

正典 §16 (census の取得と rate limit) に対応する。既存の `census_v1` は seal 後の
coverage / 欠損感度を扱う。この module はその**手前**、すなわち「何を、どの順で、
どのくらいの間隔で取得し、中断からどう再開するか」を担う。

## なぜ SQLite の状態機械なのか

正典 §16 は取得状態を「単一 writer の SQLite に、rank、team ID、submission ID、
episode ID、replay hash、deck hash、API stage、attempt、`not_before_utc` とともに
保存する」と定め、`pending` / `submission_fixed` / `episode_fixed` /
`replay_fetched` / `deck_extracted` / `qualified` / `retry_wait` /
`terminal_failure` の 8 状態を要求する。行ごとに状態を持つことで、数千 team の
取得が途中で落ちても「どこまで進んだか」が復元できる。process を保持したまま
長時間 sleep しない、という要件もここから来る。

## rate limit の規律 (正典 §16)

- credential ごと 1 worker、in-flight 1 件、初期間隔 2 秒
- 100 成功かつ 429 なしの window ごとに間隔を 10% だけ短縮、floor 0.5 秒
- **最初の 429 で global circuit breaker を開き全 worker を止める。** `Retry-After`
  があれば厳守、無ければ 60 秒から指数的に cooldown
- 解除時は probe request 1 件だけを許し、成功後に通常 pacing へ戻す
- 408 / 5xx / network reset は上限付き exponential retry、401 / 403 / schema error は terminal

## transport は注入する

この module は `requests` も Kaggle CLI も直接呼ばない。`CensusTransportV1` protocol
を満たす callable を受け取る。ネットワーク無しで状態機械と pacing を検証できることは
偶然ではなく設計であり、取得規律のテストが credential を要求しないようにするためである。
"""

from __future__ import annotations

from dataclasses import dataclass
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol


CENSUS_FETCH_SCHEMA_V1 = "meta-specialist-census-fetch-v1"

# 正典 §16 の 8 状態。
STATES_V1: tuple[str, ...] = (
    "pending",
    "submission_fixed",
    "episode_fixed",
    "replay_fetched",
    "deck_extracted",
    "qualified",
    "retry_wait",
    "terminal_failure",
)
_TERMINAL_STATES_V1 = frozenset({"qualified", "terminal_failure"})

INITIAL_INTERVAL_SECONDS_V1 = 2.0
MIN_INTERVAL_SECONDS_V1 = 0.5
INTERVAL_SHRINK_FACTOR_V1 = 0.9
SHRINK_WINDOW_SUCCESSES_V1 = 100
DEFAULT_COOLDOWN_SECONDS_V1 = 60.0
MAX_COOLDOWN_SECONDS_V1 = 3600.0
MAX_TRANSIENT_ATTEMPTS_V1 = 5

# 分類は config version に固定する (正典 §16)。
_TRANSIENT_STATUSES_V1 = frozenset({408, 500, 502, 503, 504})
_TERMINAL_STATUSES_V1 = frozenset({400, 401, 403, 404, 422})


class CensusFetchV1Error(ValueError):
    """Raised when the fetch state machine is asked for something inconsistent."""


class CensusTransportV1(Protocol):
    """One request.  Returns ``(status, payload)``; never raises for HTTP status."""

    def __call__(self, stage: str, row: Mapping[str, Any]) -> tuple[int, Any]: ...


@dataclass(frozen=True, slots=True)
class PacingDecisionV1:
    """What the pacer wants the caller to do before the next request."""

    sleep_seconds: float
    interval_seconds: float
    breaker_open: bool
    probe: bool
    reason: str


class CensusPacerV1:
    """正典 §16 の pacing と circuit breaker。時計は注入する。

    実時間で待たないので、100 成功後の短縮も 429 後の指数 cooldown も、秒を
    消費せずに検証できる。実行時は ``time.monotonic`` を渡す。
    """

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._interval = INITIAL_INTERVAL_SECONDS_V1
        self._successes_in_window = 0
        self._breaker_until: float | None = None
        self._cooldown = DEFAULT_COOLDOWN_SECONDS_V1
        self._probe_pending = False
        self._last_request_at: float | None = None

    @property
    def interval_seconds(self) -> float:
        return self._interval

    @property
    def breaker_open(self) -> bool:
        return self._breaker_until is not None and self._clock() < self._breaker_until

    def before_request(self) -> PacingDecisionV1:
        now = self._clock()
        if self._breaker_until is not None:
            if now < self._breaker_until:
                return PacingDecisionV1(
                    sleep_seconds=self._breaker_until - now, interval_seconds=self._interval,
                    breaker_open=True, probe=False, reason="circuit breaker open",
                )
            # Cooldown elapsed: exactly one probe is allowed through.
            self._breaker_until = None
            self._probe_pending = True
            return PacingDecisionV1(
                sleep_seconds=0.0, interval_seconds=self._interval, breaker_open=False,
                probe=True, reason="single probe after cooldown",
            )
        wait = 0.0
        if self._last_request_at is not None:
            elapsed = now - self._last_request_at
            wait = max(0.0, self._interval - elapsed)
        return PacingDecisionV1(
            sleep_seconds=wait, interval_seconds=self._interval, breaker_open=False,
            probe=self._probe_pending, reason="normal pacing",
        )

    def after_success(self) -> None:
        self._last_request_at = self._clock()
        if self._probe_pending:
            # The probe succeeded: return to normal pacing and reset the cooldown
            # ladder, but do not also credit it toward the shrink window.
            self._probe_pending = False
            self._cooldown = DEFAULT_COOLDOWN_SECONDS_V1
            self._successes_in_window = 0
            return
        self._successes_in_window += 1
        if self._successes_in_window >= SHRINK_WINDOW_SUCCESSES_V1:
            self._successes_in_window = 0
            self._interval = max(
                MIN_INTERVAL_SECONDS_V1, self._interval * INTERVAL_SHRINK_FACTOR_V1
            )

    def after_rate_limit(self, *, retry_after_seconds: float | None) -> None:
        """Open the breaker.  ``Retry-After`` is obeyed exactly when present."""
        self._last_request_at = self._clock()
        self._successes_in_window = 0
        self._probe_pending = False
        if retry_after_seconds is not None and retry_after_seconds >= 0:
            cooldown = float(retry_after_seconds)
        else:
            cooldown = self._cooldown
            self._cooldown = min(MAX_COOLDOWN_SECONDS_V1, self._cooldown * 2.0)
        self._breaker_until = self._clock() + cooldown

    def after_transient(self) -> None:
        self._last_request_at = self._clock()
        self._successes_in_window = 0


def classify_status_v1(status: int) -> str:
    """Map an HTTP status onto the census's fixed handling classes."""
    if status == 429:
        return "rate_limited"
    if 200 <= status < 300:
        return "success"
    if status in _TRANSIENT_STATUSES_V1:
        return "transient"
    if status in _TERMINAL_STATUSES_V1:
        return "terminal"
    return "transient" if 500 <= status < 600 else "terminal"


_SCHEMA_SQL_V1 = """
CREATE TABLE IF NOT EXISTS census_rows (
    rank INTEGER NOT NULL,
    team_id TEXT NOT NULL,
    leaderboard_score REAL,
    leaderboard_timestamp TEXT,
    submission_id TEXT,
    episode_id TEXT,
    player_index INTEGER,
    replay_sha256 TEXT,
    deck_sha256 TEXT,
    state TEXT NOT NULL,
    attempt INTEGER NOT NULL DEFAULT 0,
    not_before_utc TEXT,
    last_status INTEGER,
    detail TEXT,
    PRIMARY KEY (rank, team_id)
);
CREATE TABLE IF NOT EXISTS census_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS census_rows_state ON census_rows (state);
"""


class CensusStateStoreV1:
    """Single-writer SQLite store for the per-team fetch state (正典 §16)."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self._path, isolation_level=None)
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.executescript(_SCHEMA_SQL_V1)

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> "CensusStateStoreV1":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def seal_census_id(self, census_id: str) -> None:
        """Pin the leaderboard snapshot hash.  It never changes during a resume."""
        existing = self.census_id()
        if existing is not None and existing != census_id:
            raise CensusFetchV1Error(
                f"this store is already sealed to census_id {existing}; a resume cannot "
                f"switch to {census_id}"
            )
        self._connection.execute(
            "INSERT OR REPLACE INTO census_meta (key, value) VALUES ('census_id', ?)",
            (census_id,),
        )

    def census_id(self) -> str | None:
        row = self._connection.execute(
            "SELECT value FROM census_meta WHERE key='census_id'"
        ).fetchone()
        return None if row is None else str(row[0])

    def enqueue_rows(self, rows: list[Mapping[str, Any]]) -> int:
        """Insert leaderboard rows as ``pending``.  Existing rows keep their state."""
        added = 0
        for row in rows:
            cursor = self._connection.execute(
                "INSERT OR IGNORE INTO census_rows "
                "(rank, team_id, leaderboard_score, leaderboard_timestamp, state, attempt) "
                "VALUES (?, ?, ?, ?, 'pending', 0)",
                (
                    int(row["rank"]), str(row["team_id"]),
                    row.get("score"), row.get("timestamp"),
                ),
            )
            added += cursor.rowcount if cursor.rowcount > 0 else 0
        return added

    def claim_next(self, *, now_utc: str) -> dict[str, Any] | None:
        """Return the next actionable row, or ``None`` when nothing is due.

        ``retry_wait`` rows only become actionable once ``not_before_utc`` has
        passed, so a resume never re-hammers a row that is deliberately backed off.
        """
        row = self._connection.execute(
            "SELECT * FROM census_rows WHERE state NOT IN ('qualified','terminal_failure') "
            "AND (not_before_utc IS NULL OR not_before_utc <= ?) "
            "ORDER BY rank ASC LIMIT 1",
            (now_utc,),
        ).fetchone()
        if row is None:
            return None
        columns = [d[0] for d in self._connection.execute("SELECT * FROM census_rows LIMIT 0").description]
        return dict(zip(columns, row))

    def advance(
        self,
        *,
        rank: int,
        team_id: str,
        state: str,
        fields: Mapping[str, Any] | None = None,
        attempt: int | None = None,
        not_before_utc: str | None = None,
        last_status: int | None = None,
        detail: str | None = None,
    ) -> None:
        if state not in STATES_V1:
            raise CensusFetchV1Error(f"unknown state {state!r}; expected one of {STATES_V1}")
        assignments = ["state = ?"]
        values: list[Any] = [state]
        for key, value in (fields or {}).items():
            if key not in {
                "submission_id", "episode_id", "player_index", "replay_sha256", "deck_sha256",
            }:
                raise CensusFetchV1Error(f"{key!r} is not a settable census row field")
            assignments.append(f"{key} = ?")
            values.append(value)
        if attempt is not None:
            assignments.append("attempt = ?")
            values.append(int(attempt))
        assignments.append("not_before_utc = ?")
        values.append(not_before_utc)
        assignments.append("last_status = ?")
        values.append(last_status)
        assignments.append("detail = ?")
        values.append(None if detail is None else str(detail)[:500])
        values.extend([int(rank), str(team_id)])
        self._connection.execute(
            f"UPDATE census_rows SET {', '.join(assignments)} WHERE rank = ? AND team_id = ?",
            values,
        )

    def state_counts(self) -> dict[str, int]:
        counts = {state: 0 for state in STATES_V1}
        for state, count in self._connection.execute(
            "SELECT state, COUNT(*) FROM census_rows GROUP BY state"
        ):
            counts[str(state)] = int(count)
        return counts

    def rows(self) -> list[dict[str, Any]]:
        cursor = self._connection.execute("SELECT * FROM census_rows ORDER BY rank ASC")
        columns = [d[0] for d in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]


def select_submission_v1(candidates: list[Mapping[str, Any]]) -> Mapping[str, Any]:
    """正典 §16 の決定的な submission tie-break.

    「successful / scored submission の public score 最大、submitted-at 新しい順、
    submission ID 小さい順」。順序を固定するのは、再取得したときに別の submission が
    選ばれると census が再現しなくなるためである。
    """
    scored = [
        item for item in candidates
        if item.get("status") in {"complete", "successful", "scored"}
        and item.get("public_score") is not None
    ]
    if not scored:
        raise CensusFetchV1Error("no successful scored submission among the candidates")
    return sorted(
        scored,
        key=lambda item: (
            -float(item["public_score"]),
            _negated_timestamp(item.get("submitted_at")),
            str(item.get("submission_id", "")),
        ),
    )[0]


def _negated_timestamp(value: Any) -> str:
    """Sort newest-first over ISO timestamps without parsing them."""
    text = "" if value is None else str(value)
    # Invert lexicographic order by complementing each character code.
    return "".join(chr(0x10FFFD - ord(ch)) if ord(ch) < 0x10FFFD else ch for ch in text)


def select_episode_v1(candidates: list[Mapping[str, Any]]) -> Mapping[str, Any]:
    """完全な両 deck を含む replay のうち episode ID が最小のものを決定的に選ぶ."""
    complete = [item for item in candidates if item.get("has_both_decks") is True]
    if not complete:
        raise CensusFetchV1Error("no episode carries both complete decks")
    return sorted(complete, key=lambda item: str(item.get("episode_id", "")))[0]


__all__ = [
    "CENSUS_FETCH_SCHEMA_V1",
    "DEFAULT_COOLDOWN_SECONDS_V1",
    "INITIAL_INTERVAL_SECONDS_V1",
    "MAX_TRANSIENT_ATTEMPTS_V1",
    "MIN_INTERVAL_SECONDS_V1",
    "SHRINK_WINDOW_SUCCESSES_V1",
    "STATES_V1",
    "CensusFetchV1Error",
    "CensusPacerV1",
    "CensusStateStoreV1",
    "CensusTransportV1",
    "PacingDecisionV1",
    "classify_status_v1",
    "select_episode_v1",
    "select_submission_v1",
]
