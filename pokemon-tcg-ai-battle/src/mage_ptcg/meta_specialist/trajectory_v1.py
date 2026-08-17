"""Actor rollout trajectories: one CABT complete action is one transition.

`docs/superpowers/plans/2026-08-02-meta-specialist-learning-orchestration-v1.md`,
"Slice L5: actor trajectories and V-trace", requires that "one CABT callback
decision is one complete action, one transition, and one recurrent commit" and
that "multi-select decisions are not dropped or converted to independent
indices."  A multi-select CABT decision (``minCount``/``maxCount`` > 1) is
still decoded through several semantic-prefix steps -- see
``EvaluatedSpecialistStepV1``/``_evaluate_runtime_step`` in
``runtime_actions_v2.py`` -- but those steps are sub-decisions of ONE
transition, never transitions of their own.

This module makes that invariant structural rather than conventional:

* :class:`TrajectoryPrefixStepV1` (one decode step) has no ``reward`` or
  ``discount`` field.  It cannot carry either even by mistake, because the
  type it would need to live on does not declare them.
* :class:`ActorTrajectoryTransitionV1` (one committed complete action) has
  exactly one ``reward`` and one ``discount``, plus the ordered tuple of
  prefix steps nested underneath it.
* The transition's ``behavior_log_probability`` is required, at construction
  time, to equal :func:`masked_behavior_log_probability_v1` of its own prefix
  steps -- the sum of each step's masked log-probability.  A caller cannot
  drift the two apart; :func:`build_actor_trajectory_transition_v1` derives
  both the summed log-probability and the reconstructed complete action from
  the steps directly so nothing needs to be computed twice by hand.

Every model/step input stored here is the same serial-free
``SpecialistModelInputV1``/``SpecialistStepInputV1`` domain the rest of L1-L4
uses (see ``actor_visible_features_v1.py``): no local action ID, card serial,
CABT index, or action-key digest ever appears.  Serialized payloads are
additionally checked with
:func:`training_example_envelope_v2.reject_forbidden_private_fields_v2`.

Consumption note for ``vtrace_v1.py``: this module records
``subject_behavior_version``, ``opponent_instance_id``, ``opponent_version``,
``pool_epoch``, and ``policy_lag`` purely as provenance/bookkeeping.  None of
them feed the V-trace importance-ratio math -- that ratio is computed only
from this transition's own ``behavior_log_probability`` versus a freshly
recomputed target log-probability, both over the *subject's* action.  See the
module docstring of ``vtrace_v1.py`` for why an opponent-mixture change is
never treated as a correctable importance-sampling lag.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
from typing import Mapping

from mage_ptcg.meta_specialist.actor_visible_features_v1 import (
    STEP_INPUT_SCHEMA_V1,
    SemanticActionClassV1,
    SemanticActionV1,
    SpecialistFeatureError,
    SpecialistModelInputV1,
    SpecialistStepInputV1,
    validate_specialist_model_input_v1,
)
from mage_ptcg.meta_specialist.local_dataset_v2 import (
    canonical_json_bytes_v2,
    parse_canonical_json_bytes_v2,
)
from mage_ptcg.meta_specialist.training_example_envelope_v2 import (
    reject_forbidden_private_fields_v2,
    semantic_action_from_training_payload_v2,
    specialist_model_input_from_training_payload_v2,
)


ACTOR_TRAJECTORY_TRANSITION_SCHEMA_V1 = "specialist-actor-trajectory-transition-v1"
MAX_TRAJECTORY_PREFIX_STEPS_V1 = 512

_CONTENT_HASH_DOMAIN_V1 = b"mage_ptcg:specialist-actor-trajectory-transition:v1\0"
_HEX64 = frozenset("0123456789abcdef")
_LOG_PROBABILITY_TOLERANCE_V1 = 1.0e-9
_ORDER_SEMANTICS_VALUES_V1 = frozenset({"ordered_sequence", "unordered_set"})

_TRANSITION_KEYS_V1 = frozenset({
    "schema_version", "content_hash", "model_input", "order_semantics",
    "prefix_steps", "chosen_semantic_complete_action", "behavior_log_probability",
    "value", "reward", "discount", "terminal", "subject_behavior_version",
    "opponent_instance_id", "opponent_version", "pool_epoch", "policy_lag",
})
_PREFIX_STEP_KEYS_V1 = frozenset({
    "step_input", "forced_stop", "chosen_token", "behavior_log_probability",
})
_STEP_INPUT_KEYS_V1 = frozenset({
    "schema_version", "order_semantics", "semantic_prefix",
    "allowed_semantic_classes", "stop_available",
})
_SEMANTIC_CLASS_KEYS_V1 = frozenset({"semantic_row", "allowed_alias_count"})


class TrajectoryV1Error(ValueError):
    """Raised when an actor trajectory transition cannot be built or verified."""


def _fail(message: str) -> None:
    raise TrajectoryV1Error(message)


def _ordered_fsum(values: list[float]) -> float:
    return math.fsum(sorted(values))


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


def _require_bounded_float(value: object, name: str, *, minimum: float, maximum: float) -> float:
    result = _require_finite_float(value, name)
    if result < minimum or result > maximum:
        _fail(f"{name} must be in [{minimum}, {maximum}]")
    return result


def _require_log_probability(value: object, name: str) -> float:
    """Validate a *realized* sampled action's own log-probability.

    Unlike ``reference_losses_v1._require_log_probability`` (which describes a
    general target-mass domain and therefore tolerates ``-inf`` for
    zero-probability classes), a trajectory step's log-probability describes
    the probability the behavior policy actually assigned to the token it
    sampled.  That can never be zero probability, so ``-inf`` is rejected here
    along with any positive value.
    """
    result = _require_finite_float(value, name)
    if result > _LOG_PROBABILITY_TOLERANCE_V1:
        _fail(f"{name} cannot be positive")
    return result


def _require_bounded_str(value: object, name: str, *, maximum_length: int = 256) -> str:
    if type(value) is not str or not value or len(value) > maximum_length:
        _fail(f"{name} must be a bounded nonempty string")
    return value


def _require_hex64(value: object, name: str) -> str:
    if type(value) is not str or len(value) != 64 or any(character not in _HEX64 for character in value):
        _fail(f"{name} must be lowercase 64-hex")
    return value


def _exact_dict(value: object, *, field: str, keys: frozenset[str]) -> dict[str, object]:
    if type(value) is not dict or set(value) != keys:
        _fail(f"{field} has the wrong closed field set")
    return value


@dataclass(frozen=True, slots=True)
class TrajectoryPrefixStepV1:
    """One CABT decode step inside a single multi-select decision.

    Mirrors ``EvaluatedSpecialistStepV1``/``_evaluate_runtime_step`` in
    ``runtime_actions_v2.py``: a committed transition is decoded through one
    or more of these, each choosing either the next semantic token to add to
    the running selection or STOP.  ``forced_stop=True`` reproduces the
    runtime's model-free convention -- STOP was the only legal continuation,
    so no policy logits were ever taken and the step's log-probability is
    fixed at exactly ``0.0`` (matching ``_class_log_probability``'s
    ``forced_stop`` branch).

    This type intentionally has **no** ``reward`` or ``discount`` field.
    """

    step_input: SpecialistStepInputV1
    forced_stop: bool
    chosen_is_stop: bool
    chosen_semantic_action: SemanticActionV1 | None
    behavior_log_probability: float

    def __post_init__(self) -> None:
        if type(self.step_input) is not SpecialistStepInputV1:
            _fail("prefix step_input must be an exact SpecialistStepInputV1")
        SpecialistStepInputV1.__post_init__(self.step_input)
        forced_stop = _require_bool(self.forced_stop, "forced_stop")
        chosen_is_stop = _require_bool(self.chosen_is_stop, "chosen_is_stop")
        if forced_stop:
            if self.step_input.allowed_semantic_classes or not self.step_input.stop_available:
                _fail("forced_stop requires a step_input whose sole legal continuation is STOP")
            if not chosen_is_stop:
                _fail("a forced STOP step must choose STOP")
        if chosen_is_stop:
            if not self.step_input.stop_available:
                _fail("STOP is illegal for this step's domain")
            if self.chosen_semantic_action is not None:
                _fail("a STOP choice cannot also carry a chosen semantic action")
        else:
            if type(self.chosen_semantic_action) is not SemanticActionV1:
                _fail("a non-STOP step must choose an exact SemanticActionV1")
            SemanticActionV1.__post_init__(self.chosen_semantic_action)
            if not any(
                self.chosen_semantic_action == item.semantic_row
                for item in self.step_input.allowed_semantic_classes
            ):
                _fail("chosen semantic action is outside this step's legal domain")
        log_probability = _require_log_probability(self.behavior_log_probability, "behavior_log_probability")
        if forced_stop and log_probability != 0.0:
            _fail("a forced STOP step's log-probability must be exactly 0.0")

    def to_dict(self) -> dict[str, object]:
        chosen_token: dict[str, object]
        if self.chosen_is_stop:
            chosen_token = {"kind": "stop"}
        else:
            assert self.chosen_semantic_action is not None
            chosen_token = {
                "kind": "semantic",
                "semantic_action": self.chosen_semantic_action.to_dict(),
            }
        return {
            "step_input": self.step_input.to_dict(),
            "forced_stop": self.forced_stop,
            "chosen_token": chosen_token,
            "behavior_log_probability": self.behavior_log_probability,
        }


def _require_prefix_steps(value: object) -> tuple[TrajectoryPrefixStepV1, ...]:
    if type(value) is not tuple or not value:
        _fail("prefix_steps must be a nonempty exact tuple")
    if len(value) > MAX_TRAJECTORY_PREFIX_STEPS_V1:
        _fail("prefix_steps exceeds the bounded step count")
    for position, step in enumerate(value):
        if type(step) is not TrajectoryPrefixStepV1:
            _fail(f"prefix_steps[{position}] must be an exact TrajectoryPrefixStepV1")
        TrajectoryPrefixStepV1.__post_init__(step)
    return value


def masked_behavior_log_probability_v1(
    prefix_steps: tuple[TrajectoryPrefixStepV1, ...],
) -> float:
    """Sum masked per-prefix behavior log-probabilities into one complete-action log-prob.

    This is the sole sanctioned way to combine per-step choices into a
    transition-level total; :class:`ActorTrajectoryTransitionV1` revalidates,
    at construction, that its stored ``behavior_log_probability`` equals this
    sum within a tight tolerance.
    """
    checked = _require_prefix_steps(prefix_steps)
    total = _ordered_fsum([step.behavior_log_probability for step in checked])
    if not math.isfinite(total):
        _fail("masked behavior log-probability sum is not finite")
    return total


@dataclass(frozen=True, slots=True)
class ActorTrajectoryTransitionV1:
    """Exactly one committed CABT complete action; exactly one environment transition.

    ``reward`` and ``discount`` live only here -- never on
    :class:`TrajectoryPrefixStepV1` -- so a multi-select decision's several
    decode steps structurally cannot each carry their own reward/discount.
    """

    schema_version: str
    model_input: SpecialistModelInputV1
    order_semantics: str
    prefix_steps: tuple[TrajectoryPrefixStepV1, ...]
    chosen_semantic_complete_action: tuple[SemanticActionV1, ...]
    behavior_log_probability: float
    value: float
    reward: float
    discount: float
    terminal: bool
    subject_behavior_version: str
    opponent_instance_id: str
    opponent_version: str
    pool_epoch: int
    policy_lag: int

    def __post_init__(self) -> None:
        if self.schema_version != ACTOR_TRAJECTORY_TRANSITION_SCHEMA_V1:
            _fail("transition schema_version is invalid")
        if type(self.model_input) is not SpecialistModelInputV1:
            _fail("model_input must be an exact SpecialistModelInputV1")
        validate_specialist_model_input_v1(self.model_input)
        if self.order_semantics not in _ORDER_SEMANTICS_VALUES_V1:
            _fail("order_semantics must be ordered_sequence or unordered_set")
        steps = _require_prefix_steps(self.prefix_steps)

        expected_prefix: tuple[SemanticActionV1, ...] = ()
        chosen: list[SemanticActionV1] = []
        for position, step in enumerate(steps):
            if step.step_input.order_semantics != self.order_semantics:
                _fail(f"prefix_steps[{position}] order_semantics does not match the transition")
            if step.step_input.semantic_prefix != expected_prefix:
                _fail(f"prefix_steps[{position}] does not chain from the prior chosen tokens")
            is_last = position == len(steps) - 1
            if step.chosen_is_stop:
                if not is_last:
                    _fail("only the final prefix step may choose STOP")
            else:
                if is_last:
                    _fail("the final prefix step must choose STOP")
                assert step.chosen_semantic_action is not None
                chosen.append(step.chosen_semantic_action)
                next_prefix = (*expected_prefix, step.chosen_semantic_action)
                expected_prefix = (
                    next_prefix if self.order_semantics == "ordered_sequence"
                    else tuple(sorted(next_prefix, key=lambda row: row.canonical_bytes))
                )

        if type(self.chosen_semantic_complete_action) is not tuple or any(
            type(row) is not SemanticActionV1 for row in self.chosen_semantic_complete_action
        ):
            _fail("chosen_semantic_complete_action must be an exact tuple of SemanticActionV1")
        for row in self.chosen_semantic_complete_action:
            SemanticActionV1.__post_init__(row)
        if self.chosen_semantic_complete_action != tuple(chosen):
            _fail("chosen_semantic_complete_action does not match the prefix steps' chosen order")

        total_log_probability = masked_behavior_log_probability_v1(steps)
        stored_log_probability = _require_log_probability(self.behavior_log_probability, "behavior_log_probability")
        if not math.isclose(stored_log_probability, total_log_probability, rel_tol=0.0, abs_tol=1.0e-9):
            _fail(
                "behavior_log_probability must equal the sum of masked per-prefix "
                "log-probabilities (masked_behavior_log_probability_v1)"
            )

        _require_bounded_float(self.value, "value", minimum=-1.0, maximum=1.0)
        _require_bounded_float(self.reward, "reward", minimum=-1.0, maximum=1.0)
        discount = _require_finite_float(self.discount, "discount")
        if discount < 0.0 or discount > 1.0:
            _fail("discount must be in [0, 1]")
        terminal = _require_bool(self.terminal, "terminal")
        # A terminal transition must zero continuation and a non-terminal one
        # must never silently zero it either -- window truncation without a
        # true episode end is handled by vtrace_v1's explicit tail bootstrap,
        # not by smuggling a zero discount onto a live transition.
        if terminal and discount != 0.0:
            _fail("a terminal transition must carry discount exactly 0.0")
        if not terminal and discount == 0.0:
            _fail("a non-terminal transition cannot carry discount 0.0")

        _require_hex64(self.subject_behavior_version, "subject_behavior_version")
        _require_hex64(self.opponent_version, "opponent_version")
        _require_bounded_str(self.opponent_instance_id, "opponent_instance_id")
        _require_int(self.pool_epoch, "pool_epoch", minimum=0)
        _require_int(self.policy_lag, "policy_lag", minimum=0)

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "schema_version": self.schema_version,
            "content_hash": "",
            "model_input": self.model_input.to_dict(),
            "order_semantics": self.order_semantics,
            "prefix_steps": [step.to_dict() for step in self.prefix_steps],
            "chosen_semantic_complete_action": [
                row.to_dict() for row in self.chosen_semantic_complete_action
            ],
            "behavior_log_probability": self.behavior_log_probability,
            "value": self.value,
            "reward": self.reward,
            "discount": self.discount,
            "terminal": self.terminal,
            "subject_behavior_version": self.subject_behavior_version,
            "opponent_instance_id": self.opponent_instance_id,
            "opponent_version": self.opponent_version,
            "pool_epoch": self.pool_epoch,
            "policy_lag": self.policy_lag,
        }
        payload["content_hash"] = _transition_content_hash_v1(payload)
        reject_forbidden_private_fields_v2(payload)
        return payload


def _transition_content_hash_v1(payload: Mapping[str, object]) -> str:
    body = {key: value for key, value in payload.items() if key != "content_hash"}
    return hashlib.sha256(_CONTENT_HASH_DOMAIN_V1 + canonical_json_bytes_v2(body)).hexdigest()


def build_actor_trajectory_transition_v1(
    *,
    model_input: SpecialistModelInputV1,
    order_semantics: str,
    prefix_steps: tuple[TrajectoryPrefixStepV1, ...],
    value: float,
    reward: float,
    discount: float,
    terminal: bool,
    subject_behavior_version: str,
    opponent_instance_id: str,
    opponent_version: str,
    pool_epoch: int,
    policy_lag: int,
) -> ActorTrajectoryTransitionV1:
    """Build one validated transition, deriving the complete action and its log-probability.

    Callers (an actor worker) supply only the ordered per-prefix decode steps;
    this is the one place that sums per-prefix log-probabilities into
    ``behavior_log_probability`` (via :func:`masked_behavior_log_probability_v1`)
    and reconstructs ``chosen_semantic_complete_action`` from the non-STOP
    steps, so no caller can let the two drift apart by hand.
    """
    checked_steps = _require_prefix_steps(prefix_steps)
    chosen = tuple(
        step.chosen_semantic_action for step in checked_steps if not step.chosen_is_stop
    )
    total_log_probability = masked_behavior_log_probability_v1(checked_steps)
    return ActorTrajectoryTransitionV1(
        schema_version=ACTOR_TRAJECTORY_TRANSITION_SCHEMA_V1,
        model_input=model_input,
        order_semantics=order_semantics,
        prefix_steps=checked_steps,
        chosen_semantic_complete_action=chosen,
        behavior_log_probability=total_log_probability,
        value=value,
        reward=reward,
        discount=discount,
        terminal=terminal,
        subject_behavior_version=subject_behavior_version,
        opponent_instance_id=opponent_instance_id,
        opponent_version=opponent_version,
        pool_epoch=pool_epoch,
        policy_lag=policy_lag,
    )


def canonical_actor_trajectory_transition_bytes_v1(transition: ActorTrajectoryTransitionV1) -> bytes:
    """Return the exact canonical bytes for one validated transition."""
    if type(transition) is not ActorTrajectoryTransitionV1:
        _fail("transition must be an exact ActorTrajectoryTransitionV1")
    return canonical_json_bytes_v2(transition.to_dict())


def _step_input_from_payload(value: object, *, field: str) -> SpecialistStepInputV1:
    payload = _exact_dict(value, field=field, keys=_STEP_INPUT_KEYS_V1)
    if payload["schema_version"] != STEP_INPUT_SCHEMA_V1:
        _fail(f"{field}.schema_version is invalid")
    order_semantics = payload["order_semantics"]
    if order_semantics not in _ORDER_SEMANTICS_VALUES_V1:
        _fail(f"{field}.order_semantics is invalid")
    prefix_raw = payload["semantic_prefix"]
    if type(prefix_raw) is not list:
        _fail(f"{field}.semantic_prefix must be a list")
    prefix = tuple(
        semantic_action_from_training_payload_v2(item, field=f"{field}.semantic_prefix[{index}]")
        for index, item in enumerate(prefix_raw)
    )
    classes_raw = payload["allowed_semantic_classes"]
    if type(classes_raw) is not list:
        _fail(f"{field}.allowed_semantic_classes must be a list")
    classes: list[SemanticActionClassV1] = []
    for index, item in enumerate(classes_raw):
        class_payload = _exact_dict(
            item, field=f"{field}.allowed_semantic_classes[{index}]", keys=_SEMANTIC_CLASS_KEYS_V1,
        )
        alias_count = class_payload["allowed_alias_count"]
        if type(alias_count) is not int or alias_count < 1:
            _fail(f"{field}.allowed_semantic_classes[{index}].allowed_alias_count must be a positive int")
        semantic_row = semantic_action_from_training_payload_v2(
            class_payload["semantic_row"],
            field=f"{field}.allowed_semantic_classes[{index}].semantic_row",
        )
        classes.append(SemanticActionClassV1(semantic_row=semantic_row, allowed_alias_count=alias_count))
    stop_available = payload["stop_available"]
    if type(stop_available) is not bool:
        _fail(f"{field}.stop_available must be a bool")
    try:
        step_input = SpecialistStepInputV1(
            schema_version=STEP_INPUT_SCHEMA_V1,
            order_semantics=order_semantics,
            semantic_prefix=prefix,
            allowed_semantic_classes=tuple(classes),
            stop_available=stop_available,
        )
    except SpecialistFeatureError as exc:
        raise TrajectoryV1Error(f"{field} is not a canonical step input: {exc}") from exc
    if step_input.to_dict() != payload:
        _fail(f"{field} is not canonical")
    return step_input


def _prefix_step_from_payload(
    value: object, *, order_semantics: str, field: str,
) -> TrajectoryPrefixStepV1:
    payload = _exact_dict(value, field=field, keys=_PREFIX_STEP_KEYS_V1)
    step_input = _step_input_from_payload(payload["step_input"], field=f"{field}.step_input")
    if step_input.order_semantics != order_semantics:
        _fail(f"{field}.step_input.order_semantics does not match the transition")
    forced_stop = payload["forced_stop"]
    if type(forced_stop) is not bool:
        _fail(f"{field}.forced_stop must be a bool")
    token = payload["chosen_token"]
    if type(token) is not dict or token.get("kind") not in {"semantic", "stop"}:
        _fail(f"{field}.chosen_token has an unknown closed kind")
    if token["kind"] == "stop":
        if set(token) != {"kind"}:
            _fail(f"{field}.chosen_token has the wrong closed field set for STOP")
        chosen_is_stop = True
        chosen_semantic_action = None
    else:
        if set(token) != {"kind", "semantic_action"}:
            _fail(f"{field}.chosen_token has the wrong closed field set for a semantic choice")
        chosen_is_stop = False
        chosen_semantic_action = semantic_action_from_training_payload_v2(
            token["semantic_action"], field=f"{field}.chosen_token.semantic_action",
        )
    log_probability = payload["behavior_log_probability"]
    if type(log_probability) is not float:
        _fail(f"{field}.behavior_log_probability must be a float")
    return TrajectoryPrefixStepV1(
        step_input=step_input,
        forced_stop=forced_stop,
        chosen_is_stop=chosen_is_stop,
        chosen_semantic_action=chosen_semantic_action,
        behavior_log_probability=log_probability,
    )


def validate_actor_trajectory_transition_payload_v1(value: object) -> dict[str, object]:
    """Revalidate one canonical transition payload's closed shape and content hash.

    This is the sole read-side entry point.  It rebuilds every live typed
    object the payload claims to represent (model input, each step input,
    each chosen semantic token), reruns every construction-time invariant on
    :class:`ActorTrajectoryTransitionV1`/:class:`TrajectoryPrefixStepV1`
    (including the one-reward-one-discount-per-transition structure and the
    behavior-log-probability sum check), rejects any forbidden private field,
    and recomputes ``content_hash`` from the exact bytes rather than trusting
    the stored value.
    """
    payload = _exact_dict(value, field="actor trajectory transition", keys=_TRANSITION_KEYS_V1)
    reject_forbidden_private_fields_v2(payload)
    if payload["schema_version"] != ACTOR_TRAJECTORY_TRANSITION_SCHEMA_V1:
        _fail("transition schema_version is invalid")
    model_input = specialist_model_input_from_training_payload_v2(payload["model_input"])
    order_semantics = payload["order_semantics"]
    if order_semantics not in _ORDER_SEMANTICS_VALUES_V1:
        _fail("order_semantics must be ordered_sequence or unordered_set")
    steps_raw = payload["prefix_steps"]
    if type(steps_raw) is not list or not steps_raw:
        _fail("prefix_steps must be a nonempty list")
    steps = tuple(
        _prefix_step_from_payload(item, order_semantics=order_semantics, field=f"prefix_steps[{index}]")
        for index, item in enumerate(steps_raw)
    )
    chosen_raw = payload["chosen_semantic_complete_action"]
    if type(chosen_raw) is not list:
        _fail("chosen_semantic_complete_action must be a list")
    chosen = tuple(
        semantic_action_from_training_payload_v2(item, field=f"chosen_semantic_complete_action[{index}]")
        for index, item in enumerate(chosen_raw)
    )
    for field in ("behavior_log_probability", "value", "reward", "discount"):
        if type(payload[field]) is not float:
            _fail(f"{field} must be a float")
    if type(payload["terminal"]) is not bool:
        _fail("terminal must be a bool")
    for field in ("pool_epoch", "policy_lag"):
        if type(payload[field]) is not int:
            _fail(f"{field} must be an exact int")
    for field in ("subject_behavior_version", "opponent_instance_id", "opponent_version"):
        if type(payload[field]) is not str:
            _fail(f"{field} must be a string")

    transition = ActorTrajectoryTransitionV1(
        schema_version=payload["schema_version"],
        model_input=model_input,
        order_semantics=order_semantics,
        prefix_steps=steps,
        chosen_semantic_complete_action=chosen,
        behavior_log_probability=payload["behavior_log_probability"],
        value=payload["value"],
        reward=payload["reward"],
        discount=payload["discount"],
        terminal=payload["terminal"],
        subject_behavior_version=payload["subject_behavior_version"],
        opponent_instance_id=payload["opponent_instance_id"],
        opponent_version=payload["opponent_version"],
        pool_epoch=payload["pool_epoch"],
        policy_lag=payload["policy_lag"],
    )
    rebuilt = transition.to_dict()
    if rebuilt != payload:
        _fail("actor trajectory transition payload is not canonical")
    return rebuilt


def parse_actor_trajectory_transition_bytes_v1(raw: bytes) -> dict[str, object]:
    """Parse and fully revalidate one canonical transition from exact bytes."""
    if type(raw) is not bytes:
        _fail("actor trajectory transition bytes must be exact bytes")
    value = parse_canonical_json_bytes_v2(raw)
    payload = validate_actor_trajectory_transition_payload_v1(value)
    if canonical_json_bytes_v2(payload) != raw:
        _fail("actor trajectory transition bytes are not canonical")
    return payload


def actor_trajectory_transition_from_payload_v1(
    value: object,
) -> ActorTrajectoryTransitionV1:
    """Return the fully typed transition after the canonical read validation.

    ``parse_actor_trajectory_transition_bytes_v1`` intentionally returns a
    plain payload for safe JSONL consumers.  Runtime DAgger consumers need the
    same validated public objects, however, and must not reconstruct them from
    ad-hoc dictionaries.  This adapter keeps that boundary in this module so
    every nested step/input still goes through the exact canonical validators.
    """
    payload = validate_actor_trajectory_transition_payload_v1(value)
    order_semantics = payload["order_semantics"]
    assert isinstance(order_semantics, str)
    steps_raw = payload["prefix_steps"]
    assert isinstance(steps_raw, list)
    steps = tuple(
        _prefix_step_from_payload(
            item, order_semantics=order_semantics, field=f"prefix_steps[{index}]",
        )
        for index, item in enumerate(steps_raw)
    )
    chosen_raw = payload["chosen_semantic_complete_action"]
    assert isinstance(chosen_raw, list)
    chosen = tuple(
        semantic_action_from_training_payload_v2(
            item, field=f"chosen_semantic_complete_action[{index}]",
        )
        for index, item in enumerate(chosen_raw)
    )
    return ActorTrajectoryTransitionV1(
        schema_version=payload["schema_version"],
        model_input=specialist_model_input_from_training_payload_v2(payload["model_input"]),
        order_semantics=order_semantics,
        prefix_steps=steps,
        chosen_semantic_complete_action=chosen,
        behavior_log_probability=payload["behavior_log_probability"],
        value=payload["value"],
        reward=payload["reward"],
        discount=payload["discount"],
        terminal=payload["terminal"],
        subject_behavior_version=payload["subject_behavior_version"],
        opponent_instance_id=payload["opponent_instance_id"],
        opponent_version=payload["opponent_version"],
        pool_epoch=payload["pool_epoch"],
        policy_lag=payload["policy_lag"],
    )


def parse_actor_trajectory_transition_object_v1(raw: bytes) -> ActorTrajectoryTransitionV1:
    """Parse canonical bytes and return the exact validated transition object."""
    payload = parse_actor_trajectory_transition_bytes_v1(raw)
    return actor_trajectory_transition_from_payload_v1(payload)


__all__ = [
    "ACTOR_TRAJECTORY_TRANSITION_SCHEMA_V1",
    "MAX_TRAJECTORY_PREFIX_STEPS_V1",
    "ActorTrajectoryTransitionV1",
    "TrajectoryPrefixStepV1",
    "TrajectoryV1Error",
    "build_actor_trajectory_transition_v1",
    "canonical_actor_trajectory_transition_bytes_v1",
    "masked_behavior_log_probability_v1",
    "parse_actor_trajectory_transition_bytes_v1",
    "parse_actor_trajectory_transition_object_v1",
    "actor_trajectory_transition_from_payload_v1",
    "validate_actor_trajectory_transition_payload_v1",
]
