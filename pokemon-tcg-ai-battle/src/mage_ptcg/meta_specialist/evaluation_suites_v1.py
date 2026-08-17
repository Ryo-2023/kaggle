"""正典 §14.0 の `ascent_suite` / `top_band_suite` を構成し、実行し、§14.2 を測る。

同じ policy が初期 rating から上位まで固定のまま戦うため、正典 §14.0 は 2 つの
suite を分けて持つことを要求する。

- `ascent_suite`: lower -> middle -> high の順に opponent block を通過させ、各 band
  の score、fault、rating-proxy trajectory、最大 drawdown を測る。
- `top_band_suite`: high-strength proxy、historical champion、未使用 exploiter を
  中心に stationary performance を測る。

## band はここで決めない

suite の構成要素は `calibration_v1` が測定で与えた band だけである。出自、medal、
元デッキの強さは入力にしない (`calibration_v1` の module docstring)。`ambiguous`
は band 未確定の表明なので suite へ入れない。

## rating proxy の定義

正典は「rating-proxy trajectory」と「最大 drawdown」を要求するが、rating 系の定数
は定めていない。ここでは自由な定数を導入せず、**running mean score を標準 Elo
scale で差分 rating へ写したもの**を proxy とする。すなわち score rate ``s`` に対し
``400 * log10(s / (1 - s))``。score rate と単調に対応するため順序情報を歪めず、K 値
のような当てずっぽうの parameter を持ち込まない。drawdown はこの trajectory の
running peak からの最大下落幅 (Elo point) とする。

## fault の扱い

正典 §14.3 は logical fault、illegal、agent timeout を 0 条件に含め、runner /
infrastructure 由来の失敗は別分類として block 全体を同じ規則で再実行させる。
したがって前者は score へ算入せず fault として数え、後者は測定から除外したうえで
`infrastructure_failures` として報告し、再実行が必要であることを呼び出し側へ返す。
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from typing import Callable, Mapping, Sequence

from mage_ptcg.meta_specialist.calibration_v1 import BANDS_V1


EVALUATION_SUITE_SCHEMA_V1 = "meta-specialist-evaluation-suite-v1"

ASCENT_BAND_ORDER_V1: tuple[str, ...] = ("lower", "middle", "high")
SUITE_NAMES_V1: tuple[str, ...] = ("ascent_suite", "top_band_suite")

# §14.3 の 0 条件に含まれる fault。infrastructure はここに入れない。
COUNTED_FAULT_KINDS_V1: tuple[str, ...] = ("logical_fault", "illegal", "timeout")
INFRASTRUCTURE_FAULT_V1 = "infrastructure"

# Elo scale の標準値。score rate から差分 rating への写像にだけ使い、K 値のような
# 更新則の parameter は持たない。
_ELO_SCALE_V1 = 400.0
# s=0 / s=1 では Elo が発散するため、1 局分の分解能で内側へ寄せる。
_MIN_GAMES_FOR_PROXY_V1 = 1


class EvaluationSuiteV1Error(ValueError):
    """Raised when a suite cannot be built or its results cannot be measured."""


@dataclass(frozen=True, slots=True)
class SuiteBlockV1:
    """One band's opponent block, played as a unit (正典 §14.1 の完全ブロック)."""

    band: str
    opponent_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.band not in BANDS_V1 or self.band == "ambiguous":
            raise EvaluationSuiteV1Error(
                f"block band must be a decided band, got {self.band!r}"
            )
        if not self.opponent_ids:
            raise EvaluationSuiteV1Error(f"band {self.band!r} block has no opponents")
        if len(set(self.opponent_ids)) != len(self.opponent_ids):
            raise EvaluationSuiteV1Error(f"band {self.band!r} block repeats an opponent")
        if list(self.opponent_ids) != sorted(self.opponent_ids):
            raise EvaluationSuiteV1Error(f"band {self.band!r} block must be sorted")

    def to_dict(self) -> dict[str, object]:
        return {"band": self.band, "opponent_ids": list(self.opponent_ids)}


@dataclass(frozen=True, slots=True)
class EvaluationSuiteV1:
    """A sealed, ordered set of opponent blocks with a content-addressed identity."""

    suite_name: str
    blocks: tuple[SuiteBlockV1, ...]

    def __post_init__(self) -> None:
        if self.suite_name not in SUITE_NAMES_V1:
            raise EvaluationSuiteV1Error(f"unknown suite name {self.suite_name!r}")
        if not self.blocks:
            raise EvaluationSuiteV1Error(f"{self.suite_name} has no blocks")
        bands = [block.band for block in self.blocks]
        if len(set(bands)) != len(bands):
            raise EvaluationSuiteV1Error(f"{self.suite_name} repeats a band block")

    def schedule_id(self) -> str:
        """Identity of exactly what this suite will play, in order."""
        return hashlib.sha256(
            b"mage_ptcg:evaluation-suite:v1\0"
            + json.dumps(
                {
                    "suite_name": self.suite_name,
                    "blocks": [block.to_dict() for block in self.blocks],
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

    def opponent_ids(self) -> tuple[str, ...]:
        return tuple(
            opponent for block in self.blocks for opponent in block.opponent_ids
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": EVALUATION_SUITE_SCHEMA_V1,
            "suite_name": self.suite_name,
            "blocks": [block.to_dict() for block in self.blocks],
            "schedule_id": self.schedule_id(),
        }


@dataclass(frozen=True, slots=True)
class SuiteGameResultV1:
    """One completed game of a suite.

    ``score`` follows §14.2: win 1, draw 0.5, loss 0.  A game carrying a counted
    fault contributes no score -- §14.3 makes faults a hard zero condition, so
    letting a faulted game also report a win would hide it behind the average.
    """

    opponent_id: str
    opponent_version: str
    seat: int
    scenario_seed: int
    score: float
    fault: str = ""
    decision_latencies_ms: tuple[float, ...] = ()

    def __post_init__(self) -> None:
        if type(self.opponent_id) is not str or not self.opponent_id:
            raise EvaluationSuiteV1Error("opponent_id must be a nonempty string")
        if self.seat not in (0, 1):
            raise EvaluationSuiteV1Error("seat must be 0 or 1")
        if type(self.score) is not float or not (0.0 <= self.score <= 1.0):
            raise EvaluationSuiteV1Error("score must be a float in [0,1]")
        if self.fault and self.fault not in COUNTED_FAULT_KINDS_V1 + (INFRASTRUCTURE_FAULT_V1,):
            raise EvaluationSuiteV1Error(f"unknown fault kind {self.fault!r}")
        if self.fault in COUNTED_FAULT_KINDS_V1 and self.score != 0.0:
            raise EvaluationSuiteV1Error(
                "a faulted game cannot also report score; §14.3 makes faults a zero condition"
            )


@dataclass(frozen=True, slots=True)
class BandReportV1:
    """The four quantities §14.0 requires per band, plus the §14.2 metrics."""

    band: str
    games: int
    score_rate: float
    opponent_equal_score_rate: float
    worst_opponent_id: str
    worst_opponent_score_rate: float
    seat_score_rates: Mapping[str, float]
    fault_rate: float
    timeout_rate: float
    illegal_rate: float
    infrastructure_failures: int
    rating_proxy_trajectory: tuple[float, ...]
    max_drawdown: float
    latency_p50_ms: float
    latency_p95_ms: float
    latency_p99_ms: float

    def to_dict(self) -> dict[str, object]:
        return {
            "band": self.band,
            "games": self.games,
            "score_rate": self.score_rate,
            "opponent_equal_score_rate": self.opponent_equal_score_rate,
            "worst_opponent_id": self.worst_opponent_id,
            "worst_opponent_score_rate": self.worst_opponent_score_rate,
            "seat_score_rates": dict(self.seat_score_rates),
            "fault_rate": self.fault_rate,
            "timeout_rate": self.timeout_rate,
            "illegal_rate": self.illegal_rate,
            "infrastructure_failures": self.infrastructure_failures,
            "rating_proxy_trajectory": list(self.rating_proxy_trajectory),
            "max_drawdown": self.max_drawdown,
            "latency_p50_ms": self.latency_p50_ms,
            "latency_p95_ms": self.latency_p95_ms,
            "latency_p99_ms": self.latency_p99_ms,
        }


@dataclass(frozen=True, slots=True)
class SuiteReportV1:
    """One suite's result: per-band reports in the order the blocks were played."""

    schema_version: str
    suite_name: str
    schedule_id: str
    bands: tuple[BandReportV1, ...]
    games: int
    score_rate: float
    faults: int
    infrastructure_failures: int
    requires_rerun: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "suite_name": self.suite_name,
            "schedule_id": self.schedule_id,
            "bands": [band.to_dict() for band in self.bands],
            "games": self.games,
            "score_rate": self.score_rate,
            "faults": self.faults,
            "infrastructure_failures": self.infrastructure_failures,
            "requires_rerun": self.requires_rerun,
        }


def build_ascent_suite_v1(
    *, band_map: Mapping[str, str], available: Sequence[str]
) -> EvaluationSuiteV1:
    """Blocks in the §14.0 ascent order: lower -> middle -> high.

    A band with no calibrated opponent is omitted rather than filled from a
    neighbouring band: an ascent whose "lower" block is really middle-strength
    measures a different thing while still being reported as an ascent.
    """
    blocks: list[SuiteBlockV1] = []
    grouped = _grouped_by_band_v1(band_map, available)
    for band in ASCENT_BAND_ORDER_V1:
        members = grouped[band]
        if members:
            blocks.append(SuiteBlockV1(band=band, opponent_ids=tuple(members)))
    if not blocks:
        raise EvaluationSuiteV1Error(
            "no calibrated opponent carries a decided band; the ascent suite would "
            "measure nothing. Calibrate opponents against the reference panel first."
        )
    return EvaluationSuiteV1(suite_name="ascent_suite", blocks=tuple(blocks))


def build_top_band_suite_v1(
    *,
    band_map: Mapping[str, str],
    available: Sequence[str],
    historical_champions: Sequence[str] = (),
    unused_exploiters: Sequence[str] = (),
) -> EvaluationSuiteV1:
    """The §14.0 stationary-performance suite: high proxies, champions, exploiters.

    Champions and exploiters are included even when they are not in ``band_map``:
    §14.0 names them explicitly, and a historical champion's job here is to be a
    fixed reference point rather than a freshly banded proxy.  They are still
    required to be in ``available``, so the suite cannot name an opponent that
    does not exist.
    """
    grouped = _grouped_by_band_v1(band_map, available)
    present = set(available)
    members = set(grouped["high"])
    for extra, label in (
        (historical_champions, "historical champion"),
        (unused_exploiters, "unused exploiter"),
    ):
        for opponent_id in extra:
            if opponent_id not in present:
                raise EvaluationSuiteV1Error(
                    f"{label} {opponent_id!r} is not in the available pool"
                )
            members.add(opponent_id)
    if not members:
        raise EvaluationSuiteV1Error(
            "the top-band suite has no high-band proxy, historical champion, or "
            "exploiter; stationary performance near the top cannot be measured"
        )
    return EvaluationSuiteV1(
        suite_name="top_band_suite",
        blocks=(SuiteBlockV1(band="high", opponent_ids=tuple(sorted(members))),),
    )


def _grouped_by_band_v1(
    band_map: Mapping[str, str], available: Sequence[str]
) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {band: [] for band in BANDS_V1}
    for opponent_id in sorted(set(available)):
        band = band_map.get(opponent_id)
        # `ambiguous` is an explicit "not established"; it is not a band here.
        if band in grouped and band != "ambiguous":
            grouped[band].append(opponent_id)
    return grouped


def rating_proxy_v1(score_rate: float, games: int) -> float:
    """Score rate as a difference rating on the standard Elo scale.

    Clamped by one game's resolution so that a clean sweep is a large finite
    rating rather than an infinity that would make the trajectory unusable.
    """
    if games < _MIN_GAMES_FOR_PROXY_V1:
        raise EvaluationSuiteV1Error("a rating proxy needs at least one game")
    margin = 1.0 / (2.0 * games)
    bounded = min(max(score_rate, margin), 1.0 - margin)
    return _ELO_SCALE_V1 * math.log10(bounded / (1.0 - bounded))


def _trajectory_and_drawdown_v1(scores: Sequence[float]) -> tuple[tuple[float, ...], float]:
    """Running rating proxy after each game, and the largest fall from its peak."""
    trajectory: list[float] = []
    running = 0.0
    for index, score in enumerate(scores, start=1):
        running += score
        trajectory.append(rating_proxy_v1(running / index, index))
    peak = -math.inf
    drawdown = 0.0
    for value in trajectory:
        peak = max(peak, value)
        drawdown = max(drawdown, peak - value)
    return tuple(trajectory), drawdown


def _percentile_v1(values: Sequence[float], fraction: float) -> float:
    """Nearest-rank percentile; 0.0 when nothing was measured."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, math.ceil(fraction * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


def _band_report_v1(band: str, results: Sequence[SuiteGameResultV1]) -> BandReportV1:
    measured = [item for item in results if item.fault != INFRASTRUCTURE_FAULT_V1]
    infrastructure = len(results) - len(measured)
    if not measured:
        raise EvaluationSuiteV1Error(
            f"band {band!r} has no measurable game; every result was an "
            "infrastructure failure and the block must be re-run (§14.3)"
        )
    games = len(measured)
    scores = [item.score for item in measured]
    score_rate = math.fsum(scores) / games

    by_opponent: dict[str, list[float]] = {}
    for item in measured:
        by_opponent.setdefault(item.opponent_id, []).append(item.score)
    per_opponent = {
        opponent: math.fsum(values) / len(values) for opponent, values in by_opponent.items()
    }
    # Equal weight per opponent, so a heavily played matchup cannot dominate.
    opponent_equal = math.fsum(per_opponent.values()) / len(per_opponent)
    worst_opponent = min(sorted(per_opponent), key=lambda key: per_opponent[key])

    seat_rates: dict[str, float] = {}
    for seat in (0, 1):
        seat_scores = [item.score for item in measured if item.seat == seat]
        if seat_scores:
            seat_rates[str(seat)] = math.fsum(seat_scores) / len(seat_scores)

    counted = [item.fault for item in measured if item.fault in COUNTED_FAULT_KINDS_V1]
    latencies = [value for item in measured for value in item.decision_latencies_ms]
    trajectory, drawdown = _trajectory_and_drawdown_v1(scores)
    return BandReportV1(
        band=band,
        games=games,
        score_rate=score_rate,
        opponent_equal_score_rate=opponent_equal,
        worst_opponent_id=worst_opponent,
        worst_opponent_score_rate=per_opponent[worst_opponent],
        seat_score_rates=seat_rates,
        fault_rate=len(counted) / games,
        timeout_rate=sum(1 for kind in counted if kind == "timeout") / games,
        illegal_rate=sum(1 for kind in counted if kind == "illegal") / games,
        infrastructure_failures=infrastructure,
        rating_proxy_trajectory=trajectory,
        max_drawdown=drawdown,
        latency_p50_ms=_percentile_v1(latencies, 0.50),
        latency_p95_ms=_percentile_v1(latencies, 0.95),
        latency_p99_ms=_percentile_v1(latencies, 0.99),
    )


def run_evaluation_suite_v1(
    suite: EvaluationSuiteV1,
    *,
    play_block: Callable[[str, tuple[str, ...]], Sequence[SuiteGameResultV1]],
) -> SuiteReportV1:
    """Play every block in suite order and measure §14.0 / §14.2 per band.

    ``play_block(band, opponent_ids)`` plays one complete block and returns its
    games.  Injecting it keeps this module free of simulator and seat-scheduling
    concerns, and lets the measurement be tested against known results rather
    than against whatever the simulator happened to produce.

    Blocks are measured in the order the suite declares -- for ``ascent_suite``
    that is lower -> middle -> high, which is what makes the trajectory and the
    drawdown mean "as it ascends" rather than "in arbitrary order".
    """
    if type(suite) is not EvaluationSuiteV1:
        raise EvaluationSuiteV1Error("suite must be an EvaluationSuiteV1")
    reports: list[BandReportV1] = []
    total_games = 0
    total_score = 0.0
    total_faults = 0
    total_infrastructure = 0
    for block in suite.blocks:
        results = tuple(play_block(block.band, block.opponent_ids))
        if any(type(item) is not SuiteGameResultV1 for item in results):
            raise EvaluationSuiteV1Error("play_block must return SuiteGameResultV1 values")
        unexpected = sorted({item.opponent_id for item in results} - set(block.opponent_ids))
        if unexpected:
            raise EvaluationSuiteV1Error(
                f"band {block.band!r} played opponents outside its block: {unexpected}"
            )
        report = _band_report_v1(block.band, results)
        reports.append(report)
        measured = [item for item in results if item.fault != INFRASTRUCTURE_FAULT_V1]
        total_games += len(measured)
        total_score += math.fsum(item.score for item in measured)
        total_faults += sum(1 for item in measured if item.fault in COUNTED_FAULT_KINDS_V1)
        total_infrastructure += len(results) - len(measured)
    return SuiteReportV1(
        schema_version=EVALUATION_SUITE_SCHEMA_V1,
        suite_name=suite.suite_name,
        schedule_id=suite.schedule_id(),
        bands=tuple(reports),
        games=total_games,
        score_rate=total_score / total_games,
        faults=total_faults,
        infrastructure_failures=total_infrastructure,
        # §14.3: infrastructure failures re-run the block for both sides; they are
        # not a result, so the caller must not read this report as final.
        requires_rerun=total_infrastructure > 0,
    )


__all__ = [
    "ASCENT_BAND_ORDER_V1",
    "COUNTED_FAULT_KINDS_V1",
    "EVALUATION_SUITE_SCHEMA_V1",
    "INFRASTRUCTURE_FAULT_V1",
    "SUITE_NAMES_V1",
    "BandReportV1",
    "EvaluationSuiteV1",
    "EvaluationSuiteV1Error",
    "SuiteBlockV1",
    "SuiteGameResultV1",
    "SuiteReportV1",
    "build_ascent_suite_v1",
    "build_top_band_suite_v1",
    "rating_proxy_v1",
    "run_evaluation_suite_v1",
]
