"""SP-PSRO outer-loop population manager。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence

from mage_ptcg.policy_learning.r2d3.psro import meta_strategy

from .contracts import LeagueContractError, content_id
from .population_epoch import PopulationEpoch


@dataclass(frozen=True, slots=True)
class BestResponseRequest:
    parent_population_epoch_id: str
    target_mixture: tuple[tuple[str, float], ...]
    training_budget_updates: int
    validation_benchmark_id: str
    best_response_request_id: str

    @classmethod
    def build(
        cls,
        *,
        parent: PopulationEpoch,
        training_budget_updates: int,
        validation_benchmark_id: str,
    ) -> "BestResponseRequest":
        if training_budget_updates < 1:
            raise LeagueContractError("best-response budget must be positive")
        identity = {
            "parent_population_epoch_id": parent.population_epoch_id,
            "target_mixture": parent.member_probabilities,
            "training_budget_updates": training_budget_updates,
            "validation_benchmark_id": validation_benchmark_id,
        }
        return cls(
            **identity,
            best_response_request_id=content_id("best-response-request-v1", identity),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def decide_expansion(
    *,
    parent: PopulationEpoch,
    candidate_opponent_instance_id: str,
    expanded_payoff_matrix: Sequence[Sequence[float]],
    meta_improvement: float,
    validation_improvement: float,
    faults: int,
    novel: bool,
    single_opponent_overfit: bool,
) -> dict[str, Any]:
    reasons = []
    if meta_improvement <= 0:
        reasons.append("non_positive_meta_improvement")
    if validation_improvement <= 0:
        reasons.append("non_positive_validation_improvement")
    if faults:
        reasons.append("evaluation_fault")
    if not novel:
        reasons.append("not_novel")
    if single_opponent_overfit:
        reasons.append("single_opponent_overfit")
    accepted = not reasons
    strategy: list[float] | None = None
    next_epoch: PopulationEpoch | None = None
    if accepted:
        size = len(parent.member_probabilities) + 1
        if len(expanded_payoff_matrix) != size or any(
            len(row) != size for row in expanded_payoff_matrix
        ):
            raise LeagueContractError("expanded payoff matrix has wrong shape")
        strategy = meta_strategy([list(row) for row in expanded_payoff_matrix])
        member_ids = [
            member_id for member_id, _probability in parent.member_probabilities
        ] + [candidate_opponent_instance_id]
        next_epoch = PopulationEpoch.build(
            dict(zip(member_ids, strategy, strict=True)),
            parent_population_epoch_id=parent.population_epoch_id,
        )
    identity = {
        "parent_population_epoch_id": parent.population_epoch_id,
        "candidate_opponent_instance_id": candidate_opponent_instance_id,
        "meta_improvement": meta_improvement,
        "validation_improvement": validation_improvement,
        "faults": faults,
        "novel": novel,
        "single_opponent_overfit": single_opponent_overfit,
        "accepted": accepted,
        "reasons": reasons,
        "strategy": strategy,
        "next_population_epoch_id": (
            next_epoch.population_epoch_id if next_epoch else None
        ),
    }
    return {
        "population_expansion_decision_id": content_id(
            "population-expansion-decision-v1", identity
        ),
        **identity,
        "next_population": next_epoch.to_dict() if next_epoch else None,
    }
