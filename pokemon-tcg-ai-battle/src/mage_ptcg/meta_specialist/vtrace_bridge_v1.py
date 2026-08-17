"""Turn collected trajectories into a trainable V-trace loss.

`actor_pool_v1` writes `ActorTrajectoryTransitionV1` records and `vtrace_v1`
computes V-trace targets, but nothing joined them: collected data had no
consumer.  This module is that join.

Two invariants carry through from `trajectory_v1` and must not be softened
here:

* **One complete action is one rollout step.**  A multi-select decision decodes
  through several semantic prefixes, but reward and discount live only on the
  transition, so the rollout sees exactly one step for it.
* **The importance ratio corrects subject behavior lag only.**  The target
  log-probability is recomputed for the *same* stored complete action under the
  current model, never for a different action and never against an opponent
  mixture.

`vs` and `pg_advantage` are regression targets and advantage weights, so this
module detaches them before they reach the loss.  Leaving them attached would
let the policy move the target it is being scored against.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Callable, Mapping, Sequence

import torch

from mage_ptcg.meta_specialist.neural_batch_v1 import require_finite_update_v1
from mage_ptcg.meta_specialist.vtrace_v1 import (
    VTraceRolloutStepV1,
    VTraceRolloutV1,
    evaluate_vtrace_v1_torch,
    trajectory_within_pool_age_window_v1,
)


VTRACE_BRIDGE_SCHEMA_V1 = "specialist-vtrace-bridge-v1"

#: Recompute the target log-probability of one stored complete action.
#: Takes the transition payload, returns a differentiable scalar tensor.
TargetLogProbabilityFn = Callable[[Mapping[str, Any]], torch.Tensor]


class VTraceBridgeV1Error(ValueError):
    """Raised when a trajectory cannot be turned into a rollout safely."""


@dataclass(frozen=True, slots=True)
class RolloutAdmissionV1:
    """What a composition step admitted and what it dropped, with reasons."""

    admitted: int
    dropped: int
    drop_reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class VTraceLossV1:
    """Unnormalized loss parts plus the weight they must eventually divide by."""

    policy_loss_sum: torch.Tensor
    value_loss_sum: torch.Tensor
    entropy_sum: torch.Tensor
    weight_sum: torch.Tensor
    steps: int
    bc_loss_sum: torch.Tensor | None = None
    # Detached first and second moments of the *raw* (pre-normalization)
    # advantage.  Held as unnormalized sums for the same reason every other part
    # is: microbatching has to stay exact under a single final division.  The
    # caller turns them into the shift/scale that normalizes the *next* step.
    advantage_sum: torch.Tensor | None = None
    advantage_square_sum: torch.Tensor | None = None

    def total(
        self,
        *,
        value_coefficient: float,
        entropy_coefficient: float,
        bc_coefficient: float = 0.0,
    ) -> torch.Tensor:
        """Combine the parts; the caller divides by `weight_sum` exactly once.

        ``bc_coefficient`` weights the behavior-cloning anchor -- the negative
        log-likelihood of the action the actor actually took.  The design's
        recipe is "Policy/value/entropy/BC losses", and this is the BC term.

        It matters most in exactly the situation this bridge is usually run in:
        a *fixed* corpus.  ``-(advantage * log_pi)`` is unbounded below whenever
        the advantage is negative, so with no anchor the learner reduces its
        loss forever by driving the collected actions' log-probabilities toward
        negative infinity.  The importance ratios then collapse to zero, V-trace
        scales the gradient to nothing, and the run stops having learned only to
        avoid what the data did (measured:
        ``docs/evidence/vtrace-degenerate-collapse-20260804.md``).  V-trace's
        own premise -- "importance ratios correct subject behavior lag only" --
        assumes the policy stays near the behavior that produced the data; the
        BC term is what keeps that true when the pool cannot refresh itself.

        Defaults to ``0.0`` so a caller that has not opted in gets exactly the
        previous combination.
        """
        total = (
            self.policy_loss_sum
            + value_coefficient * self.value_loss_sum
            - entropy_coefficient * self.entropy_sum
        )
        if bc_coefficient == 0.0:
            return total
        if self.bc_loss_sum is None:
            raise VTraceBridgeV1Error(
                "a nonzero bc_coefficient needs a bc_loss_sum; this loss was built "
                "without one"
            )
        return total + bc_coefficient * self.bc_loss_sum


def _require_float(value: object, *, field: str) -> float:
    if type(value) is bool or type(value) not in (int, float) or not math.isfinite(float(value)):
        raise VTraceBridgeV1Error(f"{field} must be a finite number")
    return float(value)


def compose_vtrace_rollout_v1(
    transitions: Sequence[Mapping[str, Any]],
    *,
    bootstrap_value: float,
    target_log_probabilities: Sequence[float] | None = None,
) -> VTraceRolloutV1:
    """Compose time-ordered transitions into one rollout, one step per transition."""
    if not transitions:
        raise VTraceBridgeV1Error("a rollout needs at least one transition")
    if target_log_probabilities is None:
        # Composition is also used as a pure shape contract check; in that
        # mode the behavior value stands in so the step type stays satisfied.
        target_log_probabilities = [
            _require_float(
                item.get("behavior_log_probability"),
                field=f"transitions[{index}].behavior_log_probability",
            )
            for index, item in enumerate(transitions)
        ]
    if len(target_log_probabilities) != len(transitions):
        raise VTraceBridgeV1Error("one target log-probability per transition is required")
    steps: list[VTraceRolloutStepV1] = []
    for index, transition in enumerate(transitions):
        if not isinstance(transition, Mapping):
            raise VTraceBridgeV1Error(f"transitions[{index}] must be a mapping")
        terminal = transition.get("terminal")
        if type(terminal) is not bool:
            raise VTraceBridgeV1Error(f"transitions[{index}].terminal must be a bool")
        discount = _require_float(transition.get("discount"), field=f"transitions[{index}].discount")
        if terminal and discount != 0.0:
            # trajectory_v1 already enforces this; re-check so a hand-built
            # payload cannot bootstrap past an episode boundary.
            raise VTraceBridgeV1Error("a terminal transition must carry discount 0.0")
        if index < len(transitions) - 1 and terminal:
            raise VTraceBridgeV1Error("a terminal transition must end the rollout window")
        steps.append(
            VTraceRolloutStepV1(
                behavior_log_probability=_require_float(
                    transition.get("behavior_log_probability"),
                    field=f"transitions[{index}].behavior_log_probability",
                ),
                reward=_require_float(
                    transition.get("reward"), field=f"transitions[{index}].reward"
                ),
                discount=discount,
                value=_require_float(
                    transition.get("value"), field=f"transitions[{index}].value"
                ),
                target_log_probability=_require_float(
                    target_log_probabilities[index],
                    field=f"target_log_probabilities[{index}]",
                ),
                terminal=terminal,
            )
        )
    return VTraceRolloutV1(
        steps=tuple(steps),
        bootstrap_value=_require_float(bootstrap_value, field="bootstrap_value"),
    )


def admit_trajectories_v1(
    records: Sequence[Mapping[str, Any]],
    *,
    current_pool_epoch: int,
    recipe_max_age: int,
) -> tuple[tuple[Mapping[str, Any], ...], RolloutAdmissionV1]:
    """Keep only trajectories inside the recipe's pool-epoch age window."""
    kept: list[Mapping[str, Any]] = []
    reasons: list[str] = []
    for index, record in enumerate(records):
        transitions = record.get("transitions")
        if not isinstance(transitions, list) or not transitions:
            reasons.append(f"records[{index}]: no transitions")
            continue
        epoch = transitions[0].get("pool_epoch")
        if type(epoch) is not int:
            reasons.append(f"records[{index}]: pool_epoch is not an int")
            continue
        if not trajectory_within_pool_age_window_v1(
            current_pool_epoch=current_pool_epoch,
            trajectory_pool_epoch=epoch,
            recipe_max_age=recipe_max_age,
        ):
            reasons.append(f"records[{index}]: pool_epoch {epoch} outside the age window")
            continue
        kept.append(record)
    return tuple(kept), RolloutAdmissionV1(
        admitted=len(kept), dropped=len(records) - len(kept), drop_reasons=tuple(reasons)
    )


def evaluate_trajectory_loss_v1(
    transitions: Sequence[Mapping[str, Any]],
    *,
    target_log_probability: TargetLogProbabilityFn,
    bootstrap_value: float,
    rho_bar: float,
    c_bar: float,
    entropy: Callable[[Mapping[str, Any]], torch.Tensor] | None = None,
    state_value: Callable[[Mapping[str, Any]], torch.Tensor] | None = None,
    advantage_shift: float = 0.0,
    advantage_scale: float = 1.0,
) -> VTraceLossV1:
    """Return unnormalized V-trace policy/value/entropy sums for one trajectory.

    ``advantage_shift`` / ``advantage_scale`` recenter and rescale the advantage
    to ``(pg_advantage - shift) / scale``.  They default to the identity so an
    existing caller gets exactly the previous update.

    Why the option exists: the reward here is terminal-only (measured: 192 of
    11,151 transitions carry a nonzero reward, i.e. 1.7%), so the advantage is
    almost entirely the critic's bootstrap, and on a losing-heavy corpus its
    mean sits below zero.  ``-(advantage * log_pi)`` then applies a net downward
    push to *every* collected action rather than discriminating between them:
    the policy spreads probability mass off what it did instead of choosing
    better, which shows up as rising entropy with an unchanged argmax
    (measured over 6 rounds: entropy +18%~+58% per lane, held-out score flat at
    0.381 -> 0.381).  Subtracting the batch mean removes that common component.
    Dividing by the batch standard deviation additionally stops the effective
    step size from shrinking with the signal -- the raw spread fell 0.333 ->
    0.229 (-31%) between round 1 and round 6.

    **Scaling changes the balance against the other terms.**  With a standard
    deviation near 0.23 the divided advantage is roughly 4x its raw magnitude,
    so a ``bc_coefficient`` chosen against the raw scale anchors ~4x more
    weakly.  ``mean_log_probability_shift`` is the guard rail to watch; the BC
    anchor exists because an unanchored negative advantage drives it without
    bound (``docs/evidence/vtrace-degenerate-collapse-20260804.md``).

    ``state_value`` supplies ``V(x_t)`` from the *current learner*, which is what
    the V-trace recursion requires.  Without it the recursion falls back to the
    ``value`` field stored in the trajectory -- the actor's estimate at
    collection time, a constant.  That fallback exists only so callers written
    before the value head keep working; it makes the value loss gradient-inert
    and, far worse, leaves the policy gradient with no baseline.  On a
    losing-heavy corpus that drives every observed action's log-probability
    down without bound until the importance ratios collapse to zero and
    learning stops (measured; see
    ``docs/evidence/vtrace-degenerate-collapse-20260804.md``).  Real training
    passes ``state_value``.
    """
    target = torch.stack([
        target_log_probability(transition).to(torch.float64) for transition in transitions
    ])
    require_finite_update_v1(target, field="target log-probability")
    rollout = compose_vtrace_rollout_v1(
        transitions,
        bootstrap_value=bootstrap_value,
        target_log_probabilities=[float(value) for value in target.detach()],
    )

    behavior = torch.tensor(
        [step.behavior_log_probability for step in rollout.steps], dtype=torch.float64
    )
    reward = torch.tensor([step.reward for step in rollout.steps], dtype=torch.float64)
    discount = torch.tensor([step.discount for step in rollout.steps], dtype=torch.float64)
    # The stored value is the behavior-time estimate; the differentiable value
    # comes from the current model via `target_log_probability`'s companion.
    if state_value is None:
        value = torch.tensor([step.value for step in rollout.steps], dtype=torch.float64)
    else:
        value = torch.stack([
            state_value(transition).to(torch.float64) for transition in transitions
        ])
        if value.shape != target.shape:
            raise VTraceBridgeV1Error("state_value must yield one scalar per transition")
        require_finite_update_v1(value, field="state value")

    if target.shape != behavior.shape:
        raise VTraceBridgeV1Error("target log-probability must yield one scalar per transition")

    result = evaluate_vtrace_v1_torch(
        behavior_log_probability=behavior,
        target_log_probability=target,
        reward=reward,
        discount=discount,
        value=value,
        bootstrap_value=torch.tensor(rollout.bootstrap_value, dtype=torch.float64),
        rho_bar=rho_bar,
        c_bar=c_bar,
    )

    # vs and pg_advantage are fixed targets/weights: detach so the policy cannot
    # move the thing it is being scored against.
    advantage = result.pg_advantage.detach()
    value_target = result.vs.detach()
    require_finite_update_v1(advantage, field="pg_advantage")
    require_finite_update_v1(value_target, field="vs")

    # Report the *raw* moments: the caller estimates the next step's shift/scale
    # from them, so folding this step's normalization back in would compound.
    advantage_sum = advantage.sum()
    advantage_square_sum = (advantage * advantage).sum()
    if advantage_scale <= 0.0 or not math.isfinite(advantage_scale):
        raise VTraceBridgeV1Error("advantage_scale must be a positive finite float")
    if not math.isfinite(advantage_shift):
        raise VTraceBridgeV1Error("advantage_shift must be a finite float")
    if advantage_shift != 0.0 or advantage_scale != 1.0:
        advantage = (advantage - advantage_shift) / advantage_scale

    policy_loss_sum = -(advantage * target).sum()
    # Regress the learner's own V(x) toward the (fixed) vs target. With a
    # model-supplied `state_value` this term carries gradient into the value
    # head; with the stored-value fallback it is a constant and does not.
    value_loss_sum = ((value - value_target) ** 2).sum()
    if entropy is None:
        entropy_sum = torch.zeros((), dtype=torch.float64)
    else:
        entropy_sum = torch.stack([
            entropy(transition).to(torch.float64) for transition in transitions
        ]).sum()
    require_finite_update_v1(policy_loss_sum, field="policy loss")

    return VTraceLossV1(
        policy_loss_sum=policy_loss_sum,
        value_loss_sum=value_loss_sum,
        entropy_sum=entropy_sum,
        weight_sum=torch.tensor(float(len(rollout.steps)), dtype=torch.float64),
        steps=len(rollout.steps),
        # The BC anchor is the negative log-likelihood of the stored action --
        # exactly the quantity already computed above, so it costs no extra
        # forward pass. Always produced; whether it is used is the caller's
        # coefficient.
        bc_loss_sum=-target.sum(),
        advantage_sum=advantage_sum,
        advantage_square_sum=advantage_square_sum,
    )


def accumulate_trajectory_losses_v1(losses: Sequence[VTraceLossV1]) -> VTraceLossV1:
    """Sum unnormalized parts so microbatching stays exact under one division."""
    if not losses:
        raise VTraceBridgeV1Error("nothing to accumulate")
    bc_parts = [item.bc_loss_sum for item in losses]
    adv_parts = [item.advantage_sum for item in losses]
    adv_sq_parts = [item.advantage_square_sum for item in losses]
    return VTraceLossV1(
        policy_loss_sum=sum(item.policy_loss_sum for item in losses),
        value_loss_sum=sum(item.value_loss_sum for item in losses),
        entropy_sum=sum(item.entropy_sum for item in losses),
        weight_sum=sum(item.weight_sum for item in losses),
        steps=sum(item.steps for item in losses),
        # Only summable when every part has one; a mixed accumulation would
        # silently under-weight the anchor for the trajectories that lack it.
        bc_loss_sum=None if any(part is None for part in bc_parts) else sum(bc_parts),
        # Same rule: a partial sum would bias the moments the next step's
        # shift/scale are estimated from.
        advantage_sum=None if any(part is None for part in adv_parts) else sum(adv_parts),
        advantage_square_sum=(
            None if any(part is None for part in adv_sq_parts) else sum(adv_sq_parts)
        ),
    )


__all__ = [
    "VTRACE_BRIDGE_SCHEMA_V1", "RolloutAdmissionV1", "TargetLogProbabilityFn",
    "VTraceBridgeV1Error", "VTraceLossV1", "accumulate_trajectory_losses_v1",
    "admit_trajectories_v1", "compose_vtrace_rollout_v1",
    "evaluate_trajectory_loss_v1",
]
