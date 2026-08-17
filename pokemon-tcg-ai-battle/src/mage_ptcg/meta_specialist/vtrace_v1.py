"""Clipped IMPALA V-trace: a pure-stdlib numerical oracle and a PyTorch mirror.

`docs/superpowers/plans/2026-08-02-meta-specialist-learning-orchestration-v1.md`,
"Slice L5: actor trajectories and V-trace", requires clipped V-trace with an
explicit tail bootstrap, explicit ``rho_bar``/``c_bar`` clip thresholds (never
hardcoded), and importance ratios that "correct subject behavior lag only,
not an opponent mixture change."

Ratio scope
-----------
Every function here takes ``behavior_log_probability`` and
``target_log_probability`` as *only* the subject's own log-probability of the
action it actually took, under the behavior policy that acted and the
current/target policy respectively -- exactly the two fields
``trajectory_v1.ActorTrajectoryTransitionV1`` records
(``behavior_log_probability``) and a fresh forward pass recomputes
(``target_log_probability``).  There is no opponent-identity, opponent
version, or opponent-mixture parameter anywhere in this module's signatures;
an opponent-mixture change is therefore structurally impossible to fold into
the importance ratio here, matching
``docs/superpowers/specs/2026-08-01-meta-deck-specialist-finetuning-design.md``:
"V-trace ratio が補正するのは subject behavior と learner policy の差だけで
あり、opponent 分布差を補正したとはみなさない。" ``opponent_instance_id``/
``opponent_version``/``pool_epoch`` remain pure bookkeeping on the trajectory
record and only ever gate *admission* of a whole trajectory into a training
window (:func:`trajectory_within_pool_age_window_v1`), never the per-step
ratio math.

Recursion and the tail bootstrap
---------------------------------
For a rollout of ``T`` steps ``t = 0 .. T-1`` with per-step reward/discount/
value and a caller-supplied ``bootstrap_value`` standing in for ``V(x_T)``
(the state immediately after the window's last transition)::

    ratio_t  = exp(target_log_probability_t - behavior_log_probability_t)
    rho_t    = min(rho_bar, ratio_t)
    c_t      = min(c_bar,   ratio_t)
    delta_t  = rho_t * (reward_t + discount_t * V(x_{t+1}) - V(x_t))
    vs_t     = V(x_t) + delta_t + discount_t * c_t * (vs_{t+1} - V(x_{t+1}))
    pg_t     = rho_t * (reward_t + discount_t * vs_{t+1} - V(x_t))

computed backward from ``t = T-1`` down to ``0``, seeded with
``vs_T := bootstrap_value`` and ``V(x_T) := bootstrap_value``.  A terminal
step carries ``discount_t == 0.0`` (enforced by
``VTraceRolloutStepV1``/``trajectory_v1.ActorTrajectoryTransitionV1`` alike),
which zeroes both the ``V(x_{t+1})`` term in ``delta_t`` and the whole
recursive ``vs_{t+1}`` term -- so a terminal step, and everything before it in
the same backward pass, never bootstraps past the episode's true end,
regardless of what ``bootstrap_value`` is.

CPU only: this module never touches CUDA and never selects a device other
than ``"cpu"``.
"""

from __future__ import annotations

from dataclasses import dataclass
import math

import torch


MAX_VTRACE_ROLLOUT_STEPS_V1 = 4_096
_LOG_PROBABILITY_TOLERANCE_V1 = 1.0e-9


class VtraceV1Error(ValueError):
    """Raised when a V-trace rollout, its coefficients, or its outputs are invalid."""


def _fail(message: str) -> None:
    raise VtraceV1Error(message)


def _require_bool(value: object, name: str) -> bool:
    if type(value) is not bool:
        _fail(f"{name} must be an exact bool")
    return value


def _require_int(value: object, name: str, *, minimum: int) -> int:
    if type(value) is not int or value < minimum:
        _fail(f"{name} must be an exact int >= {minimum}")
    return value


def _require_finite_float(value: object, name: str) -> float:
    if type(value) is not float:
        _fail(f"{name} must be an exact float")
    if not math.isfinite(value):
        _fail(f"{name} must be finite")
    return value


def _require_positive_finite(value: object, name: str) -> float:
    result = _require_finite_float(value, name)
    if result <= 0.0:
        _fail(f"{name} must be positive")
    return result


def _require_log_probability(value: object, name: str) -> float:
    result = _require_finite_float(value, name)
    if result > _LOG_PROBABILITY_TOLERANCE_V1:
        _fail(f"{name} cannot be positive")
    return result


@dataclass(frozen=True, slots=True)
class VTraceRolloutStepV1:
    """One rollout step's inputs to the clipped V-trace recursion.

    ``behavior_log_probability``/``target_log_probability`` are the subject's
    own complete-action log-probabilities only -- see the module docstring's
    "Ratio scope" section.  ``value`` is ``V(x_t)`` (the value estimate at
    this step's decision point); ``reward``/``discount`` are the transition's
    single reward/discount, matching
    ``trajectory_v1.ActorTrajectoryTransitionV1`` one-per-transition shape.
    """

    behavior_log_probability: float
    target_log_probability: float
    reward: float
    discount: float
    value: float
    terminal: bool

    def __post_init__(self) -> None:
        _require_log_probability(self.behavior_log_probability, "behavior_log_probability")
        _require_log_probability(self.target_log_probability, "target_log_probability")
        _require_finite_float(self.reward, "reward")
        _require_finite_float(self.value, "value")
        discount = _require_finite_float(self.discount, "discount")
        if discount < 0.0 or discount > 1.0:
            _fail("discount must be in [0, 1]")
        terminal = _require_bool(self.terminal, "terminal")
        if terminal and discount != 0.0:
            _fail("a terminal step must carry discount exactly 0.0")
        if not terminal and discount == 0.0:
            _fail("a non-terminal step cannot carry discount 0.0")


@dataclass(frozen=True, slots=True)
class VTraceRolloutV1:
    """One time-ordered rollout window plus its explicit tail bootstrap value.

    ``bootstrap_value`` stands in for ``V(x_T)``, the value of the state
    immediately after ``steps[-1]``.  It is caller-supplied, never
    re-derived, so a rollout that intentionally ends the window before the
    episode ends (truncation) can still receive a real bootstrap while a
    rollout whose last step is terminal can supply any finite placeholder --
    the recursion multiplies it by that step's ``discount == 0.0`` and it
    never reaches ``vs``/``pg_advantage``.
    """

    steps: tuple[VTraceRolloutStepV1, ...]
    bootstrap_value: float

    def __post_init__(self) -> None:
        if type(self.steps) is not tuple or not self.steps:
            _fail("steps must be a nonempty exact tuple")
        if len(self.steps) > MAX_VTRACE_ROLLOUT_STEPS_V1:
            _fail("steps exceeds the bounded rollout length")
        for position, step in enumerate(self.steps):
            if type(step) is not VTraceRolloutStepV1:
                _fail(f"steps[{position}] must be an exact VTraceRolloutStepV1")
            VTraceRolloutStepV1.__post_init__(step)
        _require_finite_float(self.bootstrap_value, "bootstrap_value")


@dataclass(frozen=True, slots=True)
class VTraceStepResultV1:
    ratio: float
    rho: float
    c: float
    delta: float
    vs: float
    pg_advantage: float


@dataclass(frozen=True, slots=True)
class VTraceResultV1:
    steps: tuple[VTraceStepResultV1, ...]


def evaluate_vtrace_v1(
    rollout: VTraceRolloutV1, *, rho_bar: float, c_bar: float,
) -> VTraceResultV1:
    """Pure-Python clipped V-trace; the authority for PyTorch value/gradient parity.

    ``rho_bar``/``c_bar`` are required keyword-only arguments with no
    default, so a caller can never silently inherit a hardcoded clip
    threshold.
    """
    if type(rollout) is not VTraceRolloutV1:
        _fail("rollout must be an exact VTraceRolloutV1")
    VTraceRolloutV1.__post_init__(rollout)
    rho_bar_value = _require_positive_finite(rho_bar, "rho_bar")
    c_bar_value = _require_positive_finite(c_bar, "c_bar")

    steps = rollout.steps
    count = len(steps)
    ratios = [0.0] * count
    rhos = [0.0] * count
    cs = [0.0] * count
    deltas = [0.0] * count
    vs = [0.0] * count
    pg = [0.0] * count

    next_vs = rollout.bootstrap_value
    next_value = rollout.bootstrap_value
    for t in range(count - 1, -1, -1):
        step = steps[t]
        log_ratio = step.target_log_probability - step.behavior_log_probability
        try:
            ratio = math.exp(log_ratio)
        except OverflowError:
            _fail(f"v-trace importance ratio at step {t} overflowed")
        if not math.isfinite(ratio):
            _fail(f"v-trace importance ratio at step {t} is not finite")
        rho = min(rho_bar_value, ratio)
        c = min(c_bar_value, ratio)
        delta = rho * (step.reward + step.discount * next_value - step.value)
        vs_t = step.value + delta + step.discount * c * (next_vs - next_value)
        pg_t = rho * (step.reward + step.discount * next_vs - step.value)
        for name, value in (("delta", delta), ("vs", vs_t), ("pg_advantage", pg_t)):
            if not math.isfinite(value):
                _fail(f"v-trace {name} at step {t} is not finite")
        ratios[t], rhos[t], cs[t], deltas[t], vs[t], pg[t] = ratio, rho, c, delta, vs_t, pg_t
        next_vs = vs_t
        next_value = step.value

    return VTraceResultV1(
        steps=tuple(
            VTraceStepResultV1(
                ratio=ratios[t], rho=rhos[t], c=cs[t], delta=deltas[t], vs=vs[t], pg_advantage=pg[t],
            )
            for t in range(count)
        ),
    )


@dataclass(frozen=True, slots=True)
class VTraceTorchResultV1:
    """Differentiable per-step tensors from :func:`evaluate_vtrace_v1_torch`.

    No tensor here has been ``.detach()``-ed: gradients flow end-to-end
    through the whole recursion.  Whether an eventual V-trace training loss
    should detach ``vs``/``pg_advantage``/``rho``/``c`` before using them as
    fixed regression targets/advantage weights is a policy-loss decision left
    to that later call site, not to this pure V-trace math module.
    """

    ratio: torch.Tensor
    rho: torch.Tensor
    c: torch.Tensor
    vs: torch.Tensor
    pg_advantage: torch.Tensor


def _require_step_tensor(tensor: object, name: str, *, expected_shape: tuple[int, ...] | None) -> torch.Tensor:
    if not torch.is_tensor(tensor):
        _fail(f"{name} must be a torch.Tensor")
    assert isinstance(tensor, torch.Tensor)
    if tensor.dtype != torch.float64:
        _fail(f"{name} must be float64")
    if tensor.device.type != "cpu":
        _fail(f"{name} must be a CPU tensor")
    if expected_shape is None:
        if tensor.dim() != 1 or tensor.shape[0] == 0:
            _fail(f"{name} must be a nonempty 1-D tensor")
    elif tuple(tensor.shape) != expected_shape:
        _fail(f"{name} must have shape {expected_shape}")
    if not torch.isfinite(tensor.detach()).all():
        _fail(f"{name} must be finite")
    return tensor


def evaluate_vtrace_v1_torch(
    *,
    behavior_log_probability: torch.Tensor,
    target_log_probability: torch.Tensor,
    reward: torch.Tensor,
    discount: torch.Tensor,
    value: torch.Tensor,
    bootstrap_value: torch.Tensor,
    rho_bar: float,
    c_bar: float,
) -> VTraceTorchResultV1:
    """Differentiable torch mirror of :func:`evaluate_vtrace_v1`.

    All step tensors must be 1-D, float64, CPU, identically shaped, and share
    the rollout's time order; ``bootstrap_value`` is a 0-D float64 CPU
    tensor.  ``rho_bar``/``c_bar`` are plain Python floats -- explicit,
    non-differentiable clip thresholds, matching the oracle exactly.
    """
    behavior_log_probability = _require_step_tensor(
        behavior_log_probability, "behavior_log_probability", expected_shape=None,
    )
    shape = tuple(behavior_log_probability.shape)
    target_log_probability = _require_step_tensor(
        target_log_probability, "target_log_probability", expected_shape=shape,
    )
    reward = _require_step_tensor(reward, "reward", expected_shape=shape)
    discount = _require_step_tensor(discount, "discount", expected_shape=shape)
    value = _require_step_tensor(value, "value", expected_shape=shape)
    if not torch.is_tensor(bootstrap_value):
        _fail("bootstrap_value must be a torch.Tensor")
    assert isinstance(bootstrap_value, torch.Tensor)
    if bootstrap_value.dtype != torch.float64 or bootstrap_value.device.type != "cpu":
        _fail("bootstrap_value must be a CPU float64 tensor")
    if bootstrap_value.dim() != 0:
        _fail("bootstrap_value must be a 0-D tensor")
    if not torch.isfinite(bootstrap_value.detach()).all():
        _fail("bootstrap_value must be finite")

    rho_bar_value = _require_positive_finite(rho_bar, "rho_bar")
    c_bar_value = _require_positive_finite(c_bar, "c_bar")

    if bool((discount.detach() < 0.0).any()) or bool((discount.detach() > 1.0).any()):
        _fail("discount must be in [0, 1]")
    if bool((behavior_log_probability.detach() > _LOG_PROBABILITY_TOLERANCE_V1).any()):
        _fail("behavior_log_probability cannot be positive")
    if bool((target_log_probability.detach() > _LOG_PROBABILITY_TOLERANCE_V1).any()):
        _fail("target_log_probability cannot be positive")

    count = shape[0]
    if count > MAX_VTRACE_ROLLOUT_STEPS_V1:
        _fail("steps exceeds the bounded rollout length")

    log_ratio = target_log_probability - behavior_log_probability
    ratio = torch.exp(log_ratio)
    if not torch.isfinite(ratio.detach()).all():
        _fail("v-trace importance ratio is not finite")
    rho = torch.clamp(ratio, max=rho_bar_value)
    c = torch.clamp(ratio, max=c_bar_value)

    vs_steps: list[torch.Tensor] = [torch.zeros((), dtype=torch.float64)] * count
    pg_steps: list[torch.Tensor] = [torch.zeros((), dtype=torch.float64)] * count
    next_vs = bootstrap_value
    next_value = bootstrap_value
    for t in range(count - 1, -1, -1):
        delta_t = rho[t] * (reward[t] + discount[t] * next_value - value[t])
        vs_t = value[t] + delta_t + discount[t] * c[t] * (next_vs - next_value)
        pg_t = rho[t] * (reward[t] + discount[t] * next_vs - value[t])
        vs_steps[t] = vs_t
        pg_steps[t] = pg_t
        next_vs = vs_t
        next_value = value[t]

    vs = torch.stack(vs_steps)
    pg_advantage = torch.stack(pg_steps)
    if not torch.isfinite(vs.detach()).all() or not torch.isfinite(pg_advantage.detach()).all():
        _fail("v-trace produced a non-finite output")

    return VTraceTorchResultV1(ratio=ratio, rho=rho, c=c, vs=vs, pg_advantage=pg_advantage)


def trajectory_within_pool_age_window_v1(
    *, current_pool_epoch: int, trajectory_pool_epoch: int, recipe_max_age: int,
) -> bool:
    """Admit a trajectory only if its pool epoch is within the recipe's fixed age window.

    ``age = current_pool_epoch - trajectory_pool_epoch``.  Admitted iff
    ``0 <= age <= recipe_max_age``.  A trajectory recorded at a pool epoch
    *ahead of* ``current_pool_epoch`` cannot occur in a well-formed run --
    epochs only advance -- and is rejected by the same inequality, with no
    special case: its negative age is never within ``[0, recipe_max_age]``.
    """
    current = _require_int(current_pool_epoch, "current_pool_epoch", minimum=0)
    trajectory = _require_int(trajectory_pool_epoch, "trajectory_pool_epoch", minimum=0)
    max_age = _require_int(recipe_max_age, "recipe_max_age", minimum=0)
    age = current - trajectory
    return 0 <= age <= max_age


__all__ = [
    "MAX_VTRACE_ROLLOUT_STEPS_V1",
    "VTraceResultV1",
    "VTraceRolloutStepV1",
    "VTraceRolloutV1",
    "VTraceStepResultV1",
    "VTraceTorchResultV1",
    "VtraceV1Error",
    "evaluate_vtrace_v1",
    "evaluate_vtrace_v1_torch",
    "trajectory_within_pool_age_window_v1",
]
