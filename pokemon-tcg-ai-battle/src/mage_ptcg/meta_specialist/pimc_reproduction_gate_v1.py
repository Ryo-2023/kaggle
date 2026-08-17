"""正典 §9.4 の PIMC 再現 gate。

過去の +4.94 percentage points は **採用根拠ではなく再現対象の仮説**である
(正典 §9.4)。この module はその再現を、対局結果の paired block で判定する。

`pimc_gate_v1` は distilled policy と PIMC target の logit 分布間 KL を測る
distillation probe であり、別物である。分布が近いことは、その target を使うと
実際に勝てることを意味しない。正典 §22 条項11 は両方を要求する。

## 判定 (正典 §9.4)

最初の 1,024 局を paired block で行い、次の **両方**を要求する。

- primary gate: score difference の片側 97.5% cluster-bootstrap lower bound > 0
- 実用性 gate: point estimate が +3 percentage points 以上

1,024 局で不確定なら、事前登録した alpha-spending の下で同じ schedule family の
まま最大 4,096 局へ拡張する。gate を通らない限り PIMC target を本学習へ使わない。

## なぜ cluster bootstrap か

同じ scenario block 内の対局は共通の初期条件と相手を共有するため独立ではない。
局を独立標本として resample すると区間が実際より狭くなり、再現していないものを
再現したと判定する。したがって resample 単位は cluster とする。

## alpha-spending

look を 2 回持つので、名目 97.5% をそのまま 2 回使うと第一種過誤が膨らむ。
O'Brien-Fleming 型の spending 関数 ``alpha(t) = 2 * (1 - Phi(z_alpha / sqrt(t)))``
を情報比 ``t = games / 4096`` に適用する。定数は total alpha だけであり、look ごと
の閾値を後から選ぶ余地を残さない。
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import random
from statistics import NormalDist
from typing import Mapping, Sequence


PIMC_REPRODUCTION_GATE_SCHEMA_V1 = "meta-specialist-pimc-reproduction-gate-v1"

# 正典 §9.4 の数値。
INTERIM_GAMES_V1 = 1_024
MAX_GAMES_V1 = 4_096
PRACTICAL_MARGIN_V1 = 0.03  # +3 percentage points
TOTAL_ONE_SIDED_ALPHA_V1 = 0.025  # 片側 97.5%

BOOTSTRAP_RESAMPLES_V1 = 10_000
BOOTSTRAP_SEED_V1 = 20260804

# PIMC が不採用のときの fallback (正典 §9.4)。`exit_vtrace` と偽らない。
FALLBACK_ALGORITHM_ID_V1 = "rule_bc_vtrace"
FORBIDDEN_FALLBACK_LABEL_V1 = "exit_vtrace"


class PimcReproductionGateV1Error(ValueError):
    """Raised when the gate cannot be evaluated as specified."""


@dataclass(frozen=True, slots=True)
class PairedGameV1:
    """One paired game: the PIMC arm and the baseline arm on identical conditions.

    ``pair_key`` is the design's ``(opponent_id, opponent_version, seat,
    scenario_seed, replicate)``; ``cluster_id`` is the scenario block the pair
    belongs to and is the unit the bootstrap resamples.
    """

    pair_key: str
    cluster_id: str
    pimc_score: float
    baseline_score: float

    def __post_init__(self) -> None:
        if type(self.pair_key) is not str or not self.pair_key:
            raise PimcReproductionGateV1Error("pair_key must be a nonempty string")
        if type(self.cluster_id) is not str or not self.cluster_id:
            raise PimcReproductionGateV1Error("cluster_id must be a nonempty string")
        for name in ("pimc_score", "baseline_score"):
            value = getattr(self, name)
            if type(value) is not float or not (0.0 <= value <= 1.0):
                raise PimcReproductionGateV1Error(
                    f"{name} must be a float in [0,1] (win 1, draw 0.5, loss 0)"
                )

    @property
    def difference(self) -> float:
        return self.pimc_score - self.baseline_score


@dataclass(frozen=True, slots=True)
class PimcGateDecisionV1:
    """The gate's verdict, and everything needed to check it."""

    schema_version: str
    schedule_id: str
    games: int
    clusters: int
    point_estimate: float
    lower_bound: float
    alpha_spent: float
    primary_gate_passed: bool
    practical_gate_passed: bool
    passed: bool
    status: str  # "passed" | "rejected" | "inconclusive_extend"
    reason: str

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "schedule_id": self.schedule_id,
            "games": self.games,
            "clusters": self.clusters,
            "point_estimate": self.point_estimate,
            "lower_bound": self.lower_bound,
            "alpha_spent": self.alpha_spent,
            "primary_gate_passed": self.primary_gate_passed,
            "practical_gate_passed": self.practical_gate_passed,
            "passed": self.passed,
            "status": self.status,
            "reason": self.reason,
        }


def obrien_fleming_alpha_v1(games: int) -> float:
    """Cumulative one-sided alpha spendable after ``games`` of the 4,096 schedule.

    O'Brien-Fleming spends almost nothing at an early look, which is what keeps a
    lucky first 1,024 games from promoting a PIMC target that the full schedule
    would not support.
    """
    if type(games) is not int or games < 1:
        raise PimcReproductionGateV1Error("games must be a positive int")
    fraction = min(1.0, games / MAX_GAMES_V1)
    if fraction >= 1.0:
        return TOTAL_ONE_SIDED_ALPHA_V1
    z = NormalDist().inv_cdf(1.0 - TOTAL_ONE_SIDED_ALPHA_V1)
    return 2.0 * (1.0 - NormalDist().cdf(z / math.sqrt(fraction)))


def _cluster_bootstrap_lower_bound_v1(
    differences_by_cluster: Mapping[str, Sequence[float]], *, alpha: float, seed: int
) -> float:
    """One-sided lower bound on the mean paired difference, resampling clusters."""
    clusters = sorted(differences_by_cluster)
    if len(clusters) < 2:
        raise PimcReproductionGateV1Error(
            "a cluster bootstrap needs at least two clusters; with one scenario block "
            "the interval would describe that block, not the schedule"
        )
    rng = random.Random(seed)
    totals = {key: math.fsum(differences_by_cluster[key]) for key in clusters}
    sizes = {key: len(differences_by_cluster[key]) for key in clusters}
    means: list[float] = []
    count = len(clusters)
    for _draw in range(BOOTSTRAP_RESAMPLES_V1):
        total = 0.0
        games = 0
        for _pick in range(count):
            key = clusters[rng.randrange(count)]
            total += totals[key]
            games += sizes[key]
        means.append(total / games)
    means.sort()
    index = int(math.floor(alpha * len(means)))
    return means[min(max(index, 0), len(means) - 1)]


def evaluate_pimc_reproduction_gate_v1(
    games: Sequence[PairedGameV1],
    *,
    schedule_id: str,
    seed: int = BOOTSTRAP_SEED_V1,
) -> PimcGateDecisionV1:
    """Judge the §9.4 gate on a sealed paired block.

    Both gates are required.  A large point estimate whose lower bound touches 0
    is not a reproduction, and a tiny but statistically clear edge is not the
    +4.94pp hypothesis being reproduced either.
    """
    if type(schedule_id) is not str or not schedule_id:
        raise PimcReproductionGateV1Error(
            "schedule_id must name the sealed schedule this block was played under"
        )
    rows = list(games)
    if any(type(item) is not PairedGameV1 for item in rows):
        raise PimcReproductionGateV1Error("every game must be a PairedGameV1")
    if len(rows) < INTERIM_GAMES_V1:
        raise PimcReproductionGateV1Error(
            f"the first look is defined at {INTERIM_GAMES_V1} games; got {len(rows)}. "
            "Judging earlier is not the pre-registered schedule."
        )
    if len(rows) > MAX_GAMES_V1:
        raise PimcReproductionGateV1Error(
            f"the schedule extends to at most {MAX_GAMES_V1} games; got {len(rows)}"
        )
    keys = [item.pair_key for item in rows]
    if len(set(keys)) != len(keys):
        raise PimcReproductionGateV1Error(
            "a pair_key appears twice; a paired block compares each condition once"
        )

    by_cluster: dict[str, list[float]] = {}
    for item in rows:
        by_cluster.setdefault(item.cluster_id, []).append(item.difference)

    point = math.fsum(item.difference for item in rows) / len(rows)
    alpha = obrien_fleming_alpha_v1(len(rows))
    lower = _cluster_bootstrap_lower_bound_v1(by_cluster, alpha=alpha, seed=seed)

    primary = lower > 0.0
    practical = point >= PRACTICAL_MARGIN_V1
    passed = primary and practical
    if passed:
        status = "passed"
        reason = (
            f"lower bound {lower:+.4f} > 0 and point estimate {point:+.4f} "
            f">= {PRACTICAL_MARGIN_V1:+.2f}"
        )
    elif len(rows) < MAX_GAMES_V1:
        status = "inconclusive_extend"
        reason = (
            f"not established at {len(rows)} games (lower bound {lower:+.4f}, point "
            f"{point:+.4f}); extend within the same schedule family to at most "
            f"{MAX_GAMES_V1} games under the pre-registered alpha-spending"
        )
    else:
        status = "rejected"
        reason = (
            f"the full {MAX_GAMES_V1}-game schedule did not reproduce the hypothesis "
            f"(lower bound {lower:+.4f}, point {point:+.4f}); the PIMC target is not "
            "used for training"
        )
    return PimcGateDecisionV1(
        schema_version=PIMC_REPRODUCTION_GATE_SCHEMA_V1,
        schedule_id=schedule_id,
        games=len(rows),
        clusters=len(by_cluster),
        point_estimate=point,
        lower_bound=lower,
        alpha_spent=alpha,
        primary_gate_passed=primary,
        practical_gate_passed=practical,
        passed=passed,
        status=status,
        reason=reason,
    )


def assert_pimc_target_usable_v1(decision: PimcGateDecisionV1) -> PimcGateDecisionV1:
    """Refuse to hand a PIMC target to training unless the gate passed.

    正典 §9.4: 「gate を通らない限り PIMC target を本学習へ使用しない」。
    ``inconclusive_extend`` は未判定であって合格ではない。
    """
    if type(decision) is not PimcGateDecisionV1:
        raise PimcReproductionGateV1Error("decision must be a PimcGateDecisionV1")
    if decision.passed:
        return decision
    raise PimcReproductionGateV1Error(
        f"PIMC target is not usable for training: status={decision.status}. "
        f"{decision.reason}"
    )


def fallback_algorithm_id_v1(decision: PimcGateDecisionV1) -> str:
    """The label training must use when PIMC is not adopted.

    正典 §9.4 は、不採用の PIMC を ``exit_vtrace`` と偽って扱わず、rule BC で
    初期化した ``rule_bc_vtrace`` を fallback とすることを求める。実験記録上、
    search teacher があった run と無かった run が同じ名前になるのを防ぐ。
    """
    if type(decision) is not PimcGateDecisionV1:
        raise PimcReproductionGateV1Error("decision must be a PimcGateDecisionV1")
    if decision.passed:
        raise PimcReproductionGateV1Error(
            "the gate passed; there is no fallback to name. Use the PIMC target."
        )
    return FALLBACK_ALGORITHM_ID_V1


__all__ = [
    "BOOTSTRAP_RESAMPLES_V1",
    "FALLBACK_ALGORITHM_ID_V1",
    "FORBIDDEN_FALLBACK_LABEL_V1",
    "INTERIM_GAMES_V1",
    "MAX_GAMES_V1",
    "PIMC_REPRODUCTION_GATE_SCHEMA_V1",
    "PRACTICAL_MARGIN_V1",
    "TOTAL_ONE_SIDED_ALPHA_V1",
    "PairedGameV1",
    "PimcGateDecisionV1",
    "PimcReproductionGateV1Error",
    "assert_pimc_target_usable_v1",
    "evaluate_pimc_reproduction_gate_v1",
    "fallback_algorithm_id_v1",
    "obrien_fleming_alpha_v1",
]
