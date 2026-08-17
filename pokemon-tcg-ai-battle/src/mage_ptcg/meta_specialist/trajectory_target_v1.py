"""The real ``target_log_probability`` for V-trace, bound to a live policy model.

``vtrace_bridge_v1.evaluate_trajectory_loss_v1`` takes an injected
``target_log_probability(transition) -> torch.Tensor`` (see that module's
``TargetLogProbabilityFn``) but, until this module, nothing implemented it
against the real :class:`~mage_ptcg.meta_specialist.neural_model_v1.SpecialistPolicyModelV1`.
This module is that implementation: given one stored, canonical
``ActorTrajectoryTransitionV1`` payload (as written by ``actor_pool_v1`` and
read back by ``actor_pool_v1.read_actor_pool_game_record_v1``), it rebuilds
the exact ``SpecialistModelInputV1``/``SpecialistStepInputV1`` domain the
transition was recorded against and recomputes the masked log-probability of
the *same stored* complete action under the *current* model -- the sum over
decode prefixes, exactly as ``trajectory_v1.masked_behavior_log_probability_v1``
sums the recorded behavior log-probabilities.

Reconstruction reuses the same two low-level rebuild primitives
``neural_adapter_v1.py`` already uses for training-snapshot examples
(``training_example_envelope_v2.specialist_model_input_from_training_payload_v2``
and ``.semantic_action_from_training_payload_v2``) rather than re-deriving
``SemanticActionV1``/``SpecialistModelInputV1`` parsing from scratch.  What is
new here is the glue those two primitives do not provide: turning one
trajectory transition's own ``prefix_steps`` payload (a different, richer
shape than a training-snapshot ``loss_rows`` entry -- it carries the actor's
single *chosen* token per step, not a full target-mass distribution) into a
live ``SpecialistStepInputV1`` per step, then scoring that step's *stored*
chosen token under the model's own logits.

Two invariants this module enforces structurally, matching the task's hard
rules:

* **Never fabricate.** Every transition is first re-verified in full through
  ``trajectory_v1.validate_actor_trajectory_transition_payload_v1`` -- the
  same read-side authority ``actor_pool_v1.read_actor_pool_game_record_v1``
  already uses -- so a transition that fails to revalidate raises
  :class:`TrajectoryTargetV1Error` rather than being scored with a
  best-effort guess.  A rebuilt step whose stored chosen token does not
  resolve to exactly one legal class of its own rebuilt step input also
  raises, rather than silently substituting a different (e.g. argmax)
  action.
* **A forced STOP step is model-free, never re-invented as a model call.**
  ``evaluate_specialist_step_v1`` (``actor_visible_features_v1.py``) never
  queries the policy when a step's sole legal continuation is STOP -- see
  its ``not step_input.allowed_semantic_classes and step_input.stop_available``
  short-circuit -- and ``trajectory_v1`` requires such a step's recorded
  ``behavior_log_probability`` to be exactly ``0.0`` for the same reason.
  This module mirrors that exactly: a ``forced_stop`` prefix step never
  calls ``model.step_logits``; its contribution to the summed target
  log-probability is the fixed constant ``0.0``, not a value computed from
  a query the real decode process never made.

Every tensor this module produces stays attached to the model's autograd
graph -- no ``torch.no_grad()``/``torch.inference_mode()``/``.detach()``
anywhere here -- so :func:`evaluate_trajectory_loss_v1`'s ``.backward()``
reaches real model parameters.  ``neural_model_v1.SpecialistPolicyModelV1
.step_logits`` is called directly (never through
``TorchStepLogitPolicyV1``, which wraps every call in
``torch.inference_mode()`` for actor-side rollout and would silently sever
the gradient a training step needs).

Validate once, reuse across steps
----------------------------------
``train_from_trajectories_v1.py`` scores the *same* stored transitions on
every one of ``--max-steps`` optimizer steps (the model changes; the stored
data does not). Re-running the full structural validate+rebuild described
above on every step was measured to cost roughly as much as the model's own
forward pass, purely on data that had already been re-validated once at
load time (``actor_pool_v1.read_actor_pool_game_record_v1``) and is
identical, byte for byte, on every subsequent call.

:func:`prepare_trajectory_target_transition_v1` factors that one-time,
model-independent work (validate the payload, rebuild the live
``SpecialistModelInputV1``/``SpecialistStepInputV1`` objects, resolve which
legal class the stored chosen token is -- none of which depend on the
model's parameters) out of the per-call closure into a
:class:`PreparedActorTrajectoryTransitionV1` a caller builds once and reuses
across every subsequent step. ``make_trajectory_target_log_probability_v1``'s
returned callable accepts either: a raw ``dict`` payload (unchanged
behavior -- validates and rebuilds inline, exactly as before, so any
existing caller that never adopts preparation keeps working identically),
or a :class:`PreparedActorTrajectoryTransitionV1` (skips straight to
scoring). ``PreparedActorTrajectoryTransitionV1`` is itself a ``dict``
subclass holding the exact same validated field values, so every other
``Mapping``-based reader of a transition (``vtrace_bridge_v1.
compose_vtrace_rollout_v1``'s ``.get(...)`` reads, for one) is completely
unaffected by which form it receives.

The one piece of *model-dependent* work this module can still safely hoist
out of the per-step loop is ``model.encode_state(model_input)``: it is a
pure, deterministic function of the model's current parameters and the
transition's own (unchanging) ``model_input``, so computing it once per
transition-scoring call and reusing it for every one of that transition's
prefix steps produces bit-identical values to recomputing it per step --
see ``neural_model_v1.SpecialistPolicyModelV1.step_logits_from_state`` for
the entry point that accepts a precomputed ``state``, and its sibling
``encode_candidates_batch``/``candidate_cache`` for the analogous reuse
across repeated candidates within one transition's steps.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

import torch

from mage_ptcg.meta_specialist.actor_visible_features_v1 import (
    STEP_INPUT_SCHEMA_V1,
    SemanticActionClassV1,
    SemanticActionV1,
    SpecialistFeatureError,
    SpecialistModelInputV1,
    SpecialistStepInputV1,
)
from mage_ptcg.meta_specialist.local_dataset_v2 import LocalDatasetV2Error
from mage_ptcg.meta_specialist.neural_model_v1 import (
    NeuralModelV1Error,
    SpecialistPolicyModelV1,
)
from mage_ptcg.meta_specialist.training_example_envelope_v2 import (
    semantic_action_from_training_payload_v2,
    specialist_model_input_from_training_payload_v2,
)
from mage_ptcg.meta_specialist.trajectory_v1 import (
    TrajectoryV1Error,
    validate_actor_trajectory_transition_payload_v1,
)
from mage_ptcg.meta_specialist.vtrace_bridge_v1 import TargetLogProbabilityFn


TRAJECTORY_TARGET_SCHEMA_V1 = "specialist-trajectory-target-v1"

_STEP_INPUT_PAYLOAD_KEYS_V1 = frozenset({
    "schema_version", "order_semantics", "semantic_prefix",
    "allowed_semantic_classes", "stop_available",
})
_SEMANTIC_CLASS_PAYLOAD_KEYS_V1 = frozenset({"semantic_row", "allowed_alias_count"})
_PREFIX_STEP_PAYLOAD_KEYS_V1 = frozenset({
    "step_input", "forced_stop", "chosen_token", "behavior_log_probability",
})

# The rebuild-only exceptions this module treats as "the stored transition
# cannot be rebuilt" -- never a broad `except Exception`.
_REBUILD_ERRORS_V1 = (TrajectoryV1Error, LocalDatasetV2Error, SpecialistFeatureError, NeuralModelV1Error)


class TrajectoryTargetV1Error(ValueError):
    """Raised when a stored transition cannot be rebuilt or safely rescored."""


def _exact_dict(value: object, *, field: str, keys: frozenset[str]) -> dict[str, object]:
    if type(value) is not dict or set(value) != keys:
        raise TrajectoryTargetV1Error(f"{field} has the wrong closed field set")
    return value


def _rebuild_step_input(payload: Mapping[str, Any], *, field: str) -> SpecialistStepInputV1:
    """Rebuild one live ``SpecialistStepInputV1`` from its own canonical ``to_dict()`` shape.

    ``payload`` has already passed through
    ``trajectory_v1.validate_actor_trajectory_transition_payload_v1`` by the
    time this is called, so its closed shape and every cross-field invariant
    (STOP legality, canonical sort order, alias uniqueness, forced-stop
    domain) are already guaranteed; the ``SpecialistStepInputV1``
    constructor re-checks them anyway as the live object's own authority.
    """
    payload = _exact_dict(payload, field=field, keys=_STEP_INPUT_PAYLOAD_KEYS_V1)
    prefix_raw = payload["semantic_prefix"]
    if type(prefix_raw) is not list:
        raise TrajectoryTargetV1Error(f"{field}.semantic_prefix must be a list")
    prefix = tuple(
        semantic_action_from_training_payload_v2(item, field=f"{field}.semantic_prefix[{index}]")
        for index, item in enumerate(prefix_raw)
    )
    classes_raw = payload["allowed_semantic_classes"]
    if type(classes_raw) is not list:
        raise TrajectoryTargetV1Error(f"{field}.allowed_semantic_classes must be a list")
    classes: list[SemanticActionClassV1] = []
    for index, item in enumerate(classes_raw):
        item = _exact_dict(
            item, field=f"{field}.allowed_semantic_classes[{index}]",
            keys=_SEMANTIC_CLASS_PAYLOAD_KEYS_V1,
        )
        semantic_row = semantic_action_from_training_payload_v2(
            item["semantic_row"], field=f"{field}.allowed_semantic_classes[{index}].semantic_row",
        )
        classes.append(SemanticActionClassV1(
            semantic_row=semantic_row, allowed_alias_count=item["allowed_alias_count"],
        ))
    try:
        return SpecialistStepInputV1(
            schema_version=STEP_INPUT_SCHEMA_V1,
            order_semantics=payload["order_semantics"],
            semantic_prefix=prefix,
            allowed_semantic_classes=tuple(classes),
            stop_available=payload["stop_available"],
        )
    except SpecialistFeatureError as exc:
        raise TrajectoryTargetV1Error(f"{field} could not be rebuilt as a legal step input: {exc}") from exc


@dataclass(frozen=True, slots=True)
class _PreparedStepV1:
    """One prefix step reduced to exactly what scoring it still needs.

    Every field here is a pure function of the *stored* payload, so it is
    identical on every scoring call.  ``target_index`` is the position, within
    the concatenated ``[semantic_logits, stop_logit?]`` score vector, of the
    token the actor actually chose -- resolved here once, from the same
    exactly-one-legal-class rule the unprepared path applies, so a prepared
    step can never silently score a substituted action either.
    """

    field: str
    forced_stop: bool
    step_input: SpecialistStepInputV1 | None
    target_index: int | None


def _prepare_step(step_payload: Mapping[str, Any], *, field: str) -> _PreparedStepV1:
    """Do every model-independent part of scoring one stored prefix step."""
    step_payload = _exact_dict(step_payload, field=field, keys=_PREFIX_STEP_PAYLOAD_KEYS_V1)
    forced_stop = step_payload["forced_stop"]
    if type(forced_stop) is not bool:
        raise TrajectoryTargetV1Error(f"{field}.forced_stop must be a bool")

    if forced_stop:
        # Mirrors evaluate_specialist_step_v1's model-free short-circuit: the
        # real decode never queried the policy for this step, so its target
        # log-probability is the same fixed constant its behavior
        # log-probability is required to be -- never a fresh model call.
        return _PreparedStepV1(field=field, forced_stop=True, step_input=None, target_index=None)

    step_input = _rebuild_step_input(step_payload["step_input"], field=f"{field}.step_input")
    token = step_payload["chosen_token"]
    if type(token) is not dict or token.get("kind") not in {"semantic", "stop"}:
        raise TrajectoryTargetV1Error(f"{field}.chosen_token has an unknown closed kind")

    if token["kind"] == "stop":
        if set(token) != {"kind"}:
            raise TrajectoryTargetV1Error(f"{field}.chosen_token has the wrong closed field set for STOP")
        if not step_input.stop_available:
            raise TrajectoryTargetV1Error(f"{field}: STOP is illegal for this step's rebuilt domain")
        # STOP is the last row of the concatenated score vector.
        index = len(step_input.allowed_semantic_classes)
    else:
        if set(token) != {"kind", "semantic_action"}:
            raise TrajectoryTargetV1Error(
                f"{field}.chosen_token has the wrong closed field set for a semantic choice"
            )
        chosen = semantic_action_from_training_payload_v2(
            token["semantic_action"], field=f"{field}.chosen_token.semantic_action",
        )
        matches = [
            position for position, item in enumerate(step_input.allowed_semantic_classes)
            if item.semantic_row == chosen
        ]
        if len(matches) != 1:
            # Never substitute a different (e.g. argmax) action for the one the
            # actor actually took: refuse instead.
            raise TrajectoryTargetV1Error(
                f"{field}: the stored chosen semantic action is not exactly one legal class "
                "of this step's rebuilt domain -- refusing to score a substituted action"
            )
        index = matches[0]
    return _PreparedStepV1(
        field=field, forced_stop=False, step_input=step_input, target_index=index,
    )


def _score_prepared_step(
    model: SpecialistPolicyModelV1,
    state: torch.Tensor,
    prepared: _PreparedStepV1,
    *,
    candidate_cache: dict[SemanticActionV1, torch.Tensor] | None,
) -> torch.Tensor:
    """Do the model-dependent part: the masked log-probability of the stored token."""
    field = prepared.field
    if prepared.forced_stop:
        return torch.zeros((), dtype=torch.float32)

    step_input = prepared.step_input
    assert step_input is not None  # guaranteed by _prepare_step's two branches
    semantic_logits, stop_logit = model.step_logits_from_state(
        state, step_input, candidate_cache=candidate_cache,
    )
    if tuple(semantic_logits.shape) != (len(step_input.allowed_semantic_classes),):
        raise TrajectoryTargetV1Error(f"{field}: model returned the wrong semantic logit arity")
    if step_input.stop_available:
        if stop_logit is None:
            raise TrajectoryTargetV1Error(f"{field}: model omitted the STOP logit while STOP is legal")
        scores = torch.cat([semantic_logits, stop_logit.reshape(1)])
    else:
        if stop_logit is not None:
            raise TrajectoryTargetV1Error(f"{field}: model produced a STOP logit while STOP is illegal")
        scores = semantic_logits
    if not torch.isfinite(scores).all():
        raise TrajectoryTargetV1Error(f"{field}: model produced a non-finite logit")

    log_probabilities = scores - torch.logsumexp(scores, dim=0)
    return log_probabilities[prepared.target_index]


class PreparedActorTrajectoryTransitionV1(dict):
    """One validated transition carrying its reusable, model-independent decode plan.

    This *is* the transition's own payload -- a ``dict`` holding exactly the
    fields ``validate_actor_trajectory_transition_payload_v1`` accepted -- so
    every other ``Mapping`` reader of a transition (``vtrace_bridge_v1``'s
    reward/discount/behavior reads, in particular) sees no difference between
    a prepared transition and the raw payload it was built from.  What it adds
    is the rebuilt :class:`SpecialistModelInputV1` and per-step plan, which
    depend only on the stored bytes and so are correct for every step of
    training rather than only the one they were built during.
    """

    __slots__ = ("model_input", "prepared_steps")

    def __init__(
        self,
        payload: Mapping[str, Any],
        *,
        model_input: SpecialistModelInputV1,
        prepared_steps: tuple[_PreparedStepV1, ...],
    ) -> None:
        super().__init__(payload)
        self.model_input = model_input
        self.prepared_steps = prepared_steps


def prepare_trajectory_target_transition_v1(
    transition: Mapping[str, Any],
) -> PreparedActorTrajectoryTransitionV1:
    """Validate one stored transition once and keep its reusable decode plan.

    Scoring the result under a model yields exactly what scoring the raw
    payload yields; the difference is that the validation, rebuild, and
    chosen-token resolution happen here instead of on every optimizer step.
    """
    if type(transition) is not dict and not isinstance(transition, PreparedActorTrajectoryTransitionV1):
        raise TrajectoryTargetV1Error("transition must be a dict payload")
    try:
        payload = validate_actor_trajectory_transition_payload_v1(dict(transition))
        model_input = specialist_model_input_from_training_payload_v2(payload["model_input"])
    except _REBUILD_ERRORS_V1 as exc:
        raise TrajectoryTargetV1Error(f"stored transition could not be rebuilt: {exc}") from exc

    prefix_steps = payload["prefix_steps"]
    if type(prefix_steps) is not list or not prefix_steps:
        raise TrajectoryTargetV1Error("transition has no prefix steps to recompute")
    try:
        prepared_steps = tuple(
            _prepare_step(step_payload, field=f"prefix_steps[{index}]")
            for index, step_payload in enumerate(prefix_steps)
        )
    except _REBUILD_ERRORS_V1 as exc:
        raise TrajectoryTargetV1Error(f"stored transition could not be rescored: {exc}") from exc
    return PreparedActorTrajectoryTransitionV1(
        payload, model_input=model_input, prepared_steps=prepared_steps,
    )


def _step_log_probabilities(
    model: SpecialistPolicyModelV1,
    state: torch.Tensor,
    prepared: _PreparedStepV1,
    *,
    candidate_cache: dict[SemanticActionV1, torch.Tensor] | None,
) -> torch.Tensor:
    """Return the full masked log-probability vector over one step's legal domain."""
    field = prepared.field
    step_input = prepared.step_input
    assert step_input is not None
    semantic_logits, stop_logit = model.step_logits_from_state(
        state, step_input, candidate_cache=candidate_cache,
    )
    if tuple(semantic_logits.shape) != (len(step_input.allowed_semantic_classes),):
        raise TrajectoryTargetV1Error(f"{field}: model returned the wrong semantic logit arity")
    if step_input.stop_available:
        if stop_logit is None:
            raise TrajectoryTargetV1Error(f"{field}: model omitted the STOP logit while STOP is legal")
        scores = torch.cat([semantic_logits, stop_logit.reshape(1)])
    else:
        if stop_logit is not None:
            raise TrajectoryTargetV1Error(f"{field}: model produced a STOP logit while STOP is illegal")
        scores = semantic_logits
    if not torch.isfinite(scores).all():
        raise TrajectoryTargetV1Error(f"{field}: model produced a non-finite logit")
    return scores - torch.logsumexp(scores, dim=0)


class TrajectoryScorerV1:
    """Score stored transitions under one live model, sharing one backbone pass.

    A training step needs three things per transition -- the masked
    log-probability of the action the actor actually took, the model's value
    ``V(x)`` for the same decision, and the policy's entropy over that
    decision's legal domain.  All three read the *same* encoded decision state,
    so computing them from one ``encode_state`` per transition rather than one
    per quantity is a pure reuse of a deterministic value.

    The encoded states are cached by the transition's own ``content_hash``, not
    by object identity: two transitions with equal content have equal model
    inputs and therefore an identical state, so sharing it is correct, while a
    hash that differs guarantees a fresh encode.  Like the candidate cache, the
    entries are graph nodes, so one scorer belongs to exactly one backward pass.
    """

    __slots__ = ("_model", "_candidate_cache", "_states")

    def __init__(
        self, model: SpecialistPolicyModelV1, *, shared_candidate_cache: bool = False,
    ) -> None:
        if type(model) is not SpecialistPolicyModelV1:
            raise TrajectoryTargetV1Error("model must be a SpecialistPolicyModelV1")
        self._model = model
        self._candidate_cache: dict[SemanticActionV1, torch.Tensor] | None = (
            {} if shared_candidate_cache else None
        )
        self._states: dict[str, torch.Tensor] = {}

    def _prepare(self, transition: Mapping[str, Any]) -> PreparedActorTrajectoryTransitionV1:
        if isinstance(transition, PreparedActorTrajectoryTransitionV1):
            return transition
        if type(transition) is dict:
            return prepare_trajectory_target_transition_v1(transition)
        raise TrajectoryTargetV1Error("transition must be a dict payload")

    def _state(self, prepared: PreparedActorTrajectoryTransitionV1) -> torch.Tensor:
        key = prepared.get("content_hash")
        if type(key) is not str:
            return self._model.encode_state(prepared.model_input)
        cached = self._states.get(key)
        if cached is None:
            cached = self._model.encode_state(prepared.model_input)
            self._states[key] = cached
        return cached

    def _cache(self) -> dict[SemanticActionV1, torch.Tensor]:
        return {} if self._candidate_cache is None else self._candidate_cache

    def log_probability(self, transition: Mapping[str, Any]) -> torch.Tensor:
        """Masked log-probability of this transition's *stored* complete action."""
        prepared = self._prepare(transition)
        state = self._state(prepared)
        cache = self._cache()
        try:
            contributions = [
                _score_prepared_step(self._model, state, step, candidate_cache=cache)
                for step in prepared.prepared_steps
            ]
        except _REBUILD_ERRORS_V1 as exc:
            raise TrajectoryTargetV1Error(f"stored transition could not be rescored: {exc}") from exc
        result = torch.stack(contributions).sum()
        if not torch.isfinite(result):
            raise TrajectoryTargetV1Error("recomputed target log-probability is not finite")
        return result

    def value(self, transition: Mapping[str, Any]) -> torch.Tensor:
        """The model's ``V(x)`` for this transition's decision state.

        This is the learner's *current* value estimate, which is what the
        V-trace recursion requires -- never the ``value`` field stored in the
        trajectory, which is only what the actor believed at collection time.
        """
        prepared = self._prepare(transition)
        opponent = transition.get("opponent_instance_id")
        result = self._model.state_value_from_state(
            self._state(prepared),
            opponent_instance_id=opponent if isinstance(opponent, str) else None,
        )
        if not torch.isfinite(result):
            raise TrajectoryTargetV1Error("model produced a non-finite state value")
        return result

    def entropy(self, transition: Mapping[str, Any]) -> torch.Tensor:
        """Entropy of the policy over this decision's own legal domain.

        Summed over the decision's scoreable prefix steps, matching how the
        log-probability of a complete action is summed.  A forced-STOP step
        contributes zero: the real decode never consulted the policy there, so
        it has no distribution to be uncertain about.
        """
        prepared = self._prepare(transition)
        state = self._state(prepared)
        cache = self._cache()
        contributions: list[torch.Tensor] = []
        for step in prepared.prepared_steps:
            if step.forced_stop:
                contributions.append(torch.zeros((), dtype=torch.float32))
                continue
            try:
                log_probabilities = _step_log_probabilities(
                    self._model, state, step, candidate_cache=cache,
                )
            except _REBUILD_ERRORS_V1 as exc:
                raise TrajectoryTargetV1Error(
                    f"stored transition could not be rescored for entropy: {exc}"
                ) from exc
            contributions.append(-(log_probabilities.exp() * log_probabilities).sum())
        result = torch.stack(contributions).sum()
        if not torch.isfinite(result):
            raise TrajectoryTargetV1Error("recomputed policy entropy is not finite")
        return result


def make_trajectory_target_log_probability_v1(
    model: SpecialistPolicyModelV1, *, shared_candidate_cache: bool = False,
) -> TargetLogProbabilityFn:
    """Bind one live model to the ``vtrace_bridge_v1.TargetLogProbabilityFn`` contract.

    The returned callable takes one stored, canonical
    ``ActorTrajectoryTransitionV1`` payload (a plain ``dict``, e.g. one entry
    of a game record's ``"transitions"`` list) and returns a differentiable
    0-D tensor: the sum, over that transition's ordered ``prefix_steps``, of
    each step's masked log-probability of its own *stored* chosen token under
    ``model``'s current parameters -- the same combination rule
    ``trajectory_v1.masked_behavior_log_probability_v1`` uses for the
    recorded behavior log-probabilities, just recomputed live.
    """
    if type(model) is not SpecialistPolicyModelV1:
        raise TrajectoryTargetV1Error("model must be a SpecialistPolicyModelV1")

    # With ``shared_candidate_cache``, one cache spans every call this closure
    # makes, so a candidate that appears in many different decisions is encoded
    # once for all of them instead of once per decision.  Measured on a real
    # 64-game minibatch: 8,794 candidate occurrences over only 473 distinct
    # candidates -- an 18.6x reuse factor that a per-decision cache cannot see,
    # because a decision averages only 1.1 scoreable steps.
    #
    # This is safe exactly as long as every call sharing the cache feeds ONE
    # backward pass: the cached tensor is a node in that pass's graph, so
    # reusing it makes several losses depend on one node, and autograd sums
    # their gradients -- the same total a re-encode per decision produces (see
    # ``test_trajectory_target_equivalence_v1.py``).  It is therefore the
    # caller's job to build a fresh closure per backward; a cache carried into a
    # second backward references a freed graph and raises, which is why this is
    # opt-in rather than the default.
    shared: dict[SemanticActionV1, torch.Tensor] | None = (
        {} if shared_candidate_cache else None
    )

    def target_log_probability(transition: Mapping[str, Any]) -> torch.Tensor:
        if isinstance(transition, PreparedActorTrajectoryTransitionV1):
            prepared = transition
        elif type(transition) is dict:
            prepared = prepare_trajectory_target_transition_v1(transition)
        else:
            raise TrajectoryTargetV1Error("transition must be a dict payload")

        # One encode_state per decision rather than one per prefix step, and
        # one candidate encoding per distinct candidate rather than one per
        # occurrence: both are deterministic functions of this model's current
        # parameters and this transition's own stored bytes, so reusing them
        # within a single scoring call cannot change the value produced.
        state = model.encode_state(prepared.model_input)
        candidate_cache: dict[SemanticActionV1, torch.Tensor] = (
            {} if shared is None else shared
        )
        try:
            contributions = [
                _score_prepared_step(model, state, step, candidate_cache=candidate_cache)
                for step in prepared.prepared_steps
            ]
        except _REBUILD_ERRORS_V1 as exc:
            raise TrajectoryTargetV1Error(f"stored transition could not be rescored: {exc}") from exc

        result = torch.stack(contributions).sum()
        if not torch.isfinite(result):
            raise TrajectoryTargetV1Error("recomputed target log-probability is not finite")
        return result

    return target_log_probability


__all__ = [
    "TRAJECTORY_TARGET_SCHEMA_V1",
    "PreparedActorTrajectoryTransitionV1",
    "TrajectoryScorerV1",
    "TrajectoryTargetV1Error",
    "make_trajectory_target_log_probability_v1",
    "prepare_trajectory_target_transition_v1",
]
