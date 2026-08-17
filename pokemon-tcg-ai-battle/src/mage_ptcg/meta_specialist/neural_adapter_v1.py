"""Adapter that lets :class:`SpecialistPolicyModelV1` serve as the L3 ``row_logits``.

``training_step_v1`` (``neural_learner_v1.py``) is deliberately generic over how one
chunk of training-snapshot examples becomes a padded ``(rows, max_tokens)`` logit
tensor; it never reconstructs :class:`SpecialistModelInputV1` /
:class:`SpecialistStepInputV1` itself, and nothing wired a real model into that
contract before this module.  This is the one place that does the reconstruction,
so it must stay bijective with :func:`build_ragged_step_batch_v1`
(``neural_batch_v1.py``): the same example/row iteration order, and the same
STOP-last column convention.  Because that batcher builder is itself the frozen
reference for row layout, this module calls it once per chunk to obtain both the
padded shape and the per-row token-identity keys it independently derives from the
same ``token_masses`` payloads, and cross-checks its own reconstruction against
those keys before ever calling the model.

Order-semantics assumption
---------------------------
A training-snapshot loss row carries no local action IDs -- they are private,
per AGENTS.md's "ActorInformationView に相手の非公開情報を含めない" and the
envelope's own forbidden-field list -- so this module cannot call
``build_specialist_step_input_v1``: that needs an
``ExtractedSpecialistModelInputV1`` with a live local-ID lookup that the
leakage-safe snapshot deliberately drops. Instead ``order_semantics`` is derived
from data already present in the rebuilt model input: ``state_scalars[4:6]`` is
exactly ``(selection_type, selection_context)``, and
:func:`is_ordered_selection` (``cabt_json_contract_v1.py``) is the single frozen
source of truth C1 itself uses for that same pair -- the identical derivation
``build_specialist_step_input_v1`` performs, so the two can never disagree.  A
row's ``stop_available`` is similarly cross-checked, not merely trusted: it must
equal ``len(row.semantic_prefix) >= state_scalars[6]`` (``min_count``), the same
formula ``build_specialist_step_input_v1`` uses to set it.

``allowed_alias_count`` on a rebuilt :class:`SemanticActionClassV1` is set to ``1``
for every row.  It is structurally required by the dataclass but is never read by
:meth:`SpecialistPolicyModelV1.step_logits` (only ``semantic_row`` is), so any
positive placeholder is functionally inert; the true alias multiplicity already
went into the row's target masses upstream and is not recoverable from a loss row
alone (see ``local_dataset_v2.semantic_loss_rows_from_record_v2``, which sums
alias mass before this projection).
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

import torch

from mage_ptcg.meta_specialist.actor_visible_features_v1 import (
    STEP_INPUT_SCHEMA_V1,
    SemanticActionClassV1,
    SpecialistStepInputV1,
)
from mage_ptcg.meta_specialist.cabt_json_contract_v1 import is_ordered_selection
from mage_ptcg.meta_specialist.local_dataset_v2 import canonical_json_bytes_v2
from mage_ptcg.meta_specialist.neural_batch_v1 import build_ragged_step_batch_v1
from mage_ptcg.meta_specialist.neural_learner_v1 import RowLogitsFn
from mage_ptcg.meta_specialist.neural_model_v1 import SpecialistPolicyModelV1
from mage_ptcg.meta_specialist.training_example_envelope_v2 import (
    semantic_action_from_training_payload_v2,
    specialist_model_input_from_training_payload_v2,
)


NEURAL_ADAPTER_SCHEMA_V1 = "specialist-neural-adapter-v1"
_STOP_TOKEN_KEY_V1 = b"\x00STOP"
_MIN_COUNT_SCALAR_INDEX_V1 = 6  # STATE_SCALAR_NAMES_V1[6] == "min_count"
_SELECTION_SCHEMA_SCALAR_INDICES_V1 = (4, 5)  # (selection_type, selection_context)


class NeuralAdapterV1Error(ValueError):
    """Raised when a training-snapshot row cannot be safely replayed through the model."""


def _row_semantic_prefix(row: Mapping[str, Any], *, field: str) -> tuple:
    prefix = row["semantic_prefix"]
    if type(prefix) is not list:
        raise NeuralAdapterV1Error(f"{field}.semantic_prefix must be a list")
    return tuple(
        semantic_action_from_training_payload_v2(item, field=f"{field}.semantic_prefix[{index}]")
        for index, item in enumerate(prefix)
    )


def _row_tokens(row: Mapping[str, Any], *, field: str):
    """Parse ``token_masses`` into ordered classes/keys, mirroring the batcher.

    Returns ``(classes, stop_available, keys)`` where ``keys`` is the exact
    per-token identity the batcher independently computes, in the same order.
    """
    tokens = row["token_masses"]
    if type(tokens) is not list or not tokens:
        raise NeuralAdapterV1Error(f"{field}.token_masses must be a nonempty list")
    classes: list[SemanticActionClassV1] = []
    keys: list[bytes] = []
    stop_available = False
    for position, token in enumerate(tokens):
        kind = token.get("kind") if isinstance(token, Mapping) else None
        if kind == "semantic":
            if stop_available:
                raise NeuralAdapterV1Error(f"{field}.token_masses[{position}] follows STOP")
            action = semantic_action_from_training_payload_v2(
                token["semantic_action"], field=f"{field}.token_masses[{position}].semantic_action",
            )
            classes.append(SemanticActionClassV1(semantic_row=action, allowed_alias_count=1))
            keys.append(canonical_json_bytes_v2(action.to_dict()))
        elif kind == "stop":
            if stop_available or position != len(tokens) - 1:
                raise NeuralAdapterV1Error(f"{field}.token_masses STOP must appear at most once, last")
            stop_available = True
            keys.append(_STOP_TOKEN_KEY_V1)
        else:
            raise NeuralAdapterV1Error(f"{field}.token_masses[{position}] has an unknown kind")
    return classes, stop_available, tuple(keys)


def _row_step_input(
    row: Mapping[str, Any], *, order_semantics: str, min_count: int, field: str,
) -> tuple[SpecialistStepInputV1, tuple[bytes, ...]]:
    classes, stop_available, keys = _row_tokens(row, field=field)
    prefix = _row_semantic_prefix(row, field=field)

    # Cross-check STOP availability against the same formula
    # build_specialist_step_input_v1 uses, rather than only trusting the token
    # domain that was parsed above.
    expected_stop_available = len(prefix) >= min_count
    if stop_available != expected_stop_available:
        raise NeuralAdapterV1Error(
            f"{field} STOP availability ({stop_available}) does not match its "
            f"prefix length against min_count ({expected_stop_available})"
        )

    try:
        step_input = SpecialistStepInputV1(
            schema_version=STEP_INPUT_SCHEMA_V1,
            order_semantics=order_semantics,
            semantic_prefix=prefix,
            allowed_semantic_classes=tuple(classes),
            stop_available=stop_available,
        )
    except ValueError as exc:  # SpecialistFeatureError subclasses ValueError.
        raise NeuralAdapterV1Error(
            f"{field} rebuilt allowed-class set does not match its semantic tokens: {exc}"
        ) from exc
    return step_input, keys


def make_specialist_state_values_v1(model: SpecialistPolicyModelV1):
    """Bind one model to a ``examples -> V(x) per example`` callable.

    BC's counterpart to :func:`make_specialist_row_logits_v1`.  The design's
    recipe is "Policy/value/entropy/BC losses"; without this the value head is
    only ever trained during V-trace, so a θ0 hands the RL loop a randomly
    initialised critic and the policy gradient runs on a noise baseline until
    the critic catches up.  The snapshots already carry ``value_target``, so
    fitting the head here costs one extra forward pass per example and no new
    data collection.
    """
    if type(model) is not SpecialistPolicyModelV1:
        raise NeuralAdapterV1Error("model must be a SpecialistPolicyModelV1")

    def state_values(examples: Sequence[Mapping[str, Any]]) -> torch.Tensor:
        if not examples:
            # `torch.stack([])` raises; an empty chunk legitimately contributes
            # no value loss, so return an empty column rather than failing.
            return torch.zeros((0,), dtype=torch.float32)
        values = []
        for index, example in enumerate(examples):
            try:
                model_input = specialist_model_input_from_training_payload_v2(
                    example["model_input"]
                )
            except (KeyError, TypeError) as exc:
                raise NeuralAdapterV1Error(
                    f"examples[{index}] has no usable model_input for the value head"
                ) from exc
            values.append(model.state_value(model_input))
        return torch.stack(values)

    return state_values


def make_specialist_row_logits_v1(model: SpecialistPolicyModelV1) -> RowLogitsFn:
    """Bind one live :class:`SpecialistPolicyModelV1` to the ``row_logits`` contract.

    The returned callable matches ``neural_learner_v1.RowLogitsFn``: given one chunk
    of training-snapshot examples, it returns their padded ``(rows, max_tokens)``
    logit tensor, attached to ``model``'s autograd graph so
    :func:`training_step_v1` can call ``.backward()`` on the resulting loss.
    """
    if type(model) is not SpecialistPolicyModelV1:
        raise NeuralAdapterV1Error("model must be a SpecialistPolicyModelV1")

    def row_logits(examples: Sequence[Mapping[str, Any]]) -> torch.Tensor:
        batch = build_ragged_step_batch_v1(examples)
        row_tensors: list[torch.Tensor] = []
        row_index = 0
        for example_index, example in enumerate(examples):
            model_input = specialist_model_input_from_training_payload_v2(example["model_input"])
            selection_type = model_input.state_scalars[_SELECTION_SCHEMA_SCALAR_INDICES_V1[0]]
            selection_context = model_input.state_scalars[_SELECTION_SCHEMA_SCALAR_INDICES_V1[1]]
            min_count = model_input.state_scalars[_MIN_COUNT_SCALAR_INDEX_V1]
            try:
                ordered = is_ordered_selection(selection_type, selection_context)
            except ValueError as exc:
                raise NeuralAdapterV1Error(
                    f"examples[{example_index}].model_input has an unrecognized selection schema"
                ) from exc
            order_semantics = "ordered_sequence" if ordered else "unordered_set"

            rows = example["loss_rows"]
            if type(rows) is not list:
                raise NeuralAdapterV1Error(f"examples[{example_index}].loss_rows must be a list")
            for local_row_index, row in enumerate(rows):
                field = f"examples[{example_index}].loss_rows[{local_row_index}]"
                step_input, keys = _row_step_input(
                    row, order_semantics=order_semantics, min_count=min_count, field=field,
                )
                expected_keys = batch.row_token_keys[row_index]
                if keys != expected_keys:
                    raise NeuralAdapterV1Error(
                        f"{field} rebuilt allowed-class set does not match the row's "
                        "semantic tokens as independently keyed by the batcher"
                    )

                semantic_logits, stop_logit = model.step_logits(model_input, step_input)
                if tuple(semantic_logits.shape) != (len(step_input.allowed_semantic_classes),):
                    raise NeuralAdapterV1Error(f"{field} model returned the wrong semantic logit arity")
                if step_input.stop_available:
                    if stop_logit is None:
                        raise NeuralAdapterV1Error(
                            f"{field} model omitted the STOP logit while STOP is legal"
                        )
                    row_values = torch.cat([semantic_logits, stop_logit.reshape(1)])
                else:
                    if stop_logit is not None:
                        raise NeuralAdapterV1Error(
                            f"{field} model produced a STOP logit while STOP is illegal"
                        )
                    row_values = semantic_logits
                width = len(keys)
                if row_values.shape != (width,):
                    raise NeuralAdapterV1Error(f"{field} produced the wrong row width")
                if not torch.isfinite(row_values).all():
                    raise NeuralAdapterV1Error(f"{field} produced a non-finite logit")

                pad_width = batch.max_tokens - width
                if pad_width:
                    # A padding constant with no gradient: the loss masks it, but it
                    # must still never be NaN/Inf.
                    padding = torch.zeros(pad_width, dtype=row_values.dtype)
                    row_values = torch.cat([row_values, padding])
                row_tensors.append(row_values)
                row_index += 1

        if row_index != batch.rows:
            raise NeuralAdapterV1Error("row_logits produced a different row count than the batch")
        logits = torch.stack(row_tensors)
        if logits.shape != batch.token_mask.shape:
            raise NeuralAdapterV1Error("row_logits produced the wrong padded shape")
        return logits

    return row_logits


__all__ = [
    "NEURAL_ADAPTER_SCHEMA_V1", "NeuralAdapterV1Error", "make_specialist_row_logits_v1",
]
