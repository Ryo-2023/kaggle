"""Small NumPy-trained candidate scorer with a standard-library runtime format."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Callable, Iterable, Mapping

from mage_ptcg.decision_state import DecisionStateError
from mage_ptcg.meta_specialist.cabt_json_contract_v1 import is_ordered_selection

from .dataset import RuleBCExample
from .features import (
    ACTION_FEATURE_DOMAINS,
    ACTION_FEATURE_DIM,
    FEATURE_VERSION,
    LEGACY_ACTIONKEY_FEATURE_DOMAIN,
    PRIVATE_ACTIONKEY_FEATURE_DOMAIN,
    STATE_FEATURE_DIM,
    serialized_action_feature_domain,
    serialized_action_features,
    state_features_payload,
)


MODEL_SCHEMA_VERSION = "student-v0-model-v2"
LEGACY_MODEL_SCHEMA_VERSION = "student-v0-model-v1"
MODEL_FEATURE_DIM = STATE_FEATURE_DIM + ACTION_FEATURE_DIM
_MODEL_V2_FIELDS = frozenset(
    {
        "bias",
        "feature_domain",
        "feature_version",
        "model_schema_version",
        "model_type",
        "weights",
    }
)
_MODEL_V1_FIELDS = _MODEL_V2_FIELDS - {"feature_domain"}


class ModelValidationError(ValueError):
    """Raised when an exported Student model is malformed or incompatible."""


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))


def _action_feature_vector(record: dict[str, object]) -> list[float]:
    payload = record.get("payload")
    digest = record.get("digest")
    if not isinstance(payload, dict) or not isinstance(digest, str):
        raise ModelValidationError("dataset action is missing payload or digest")
    try:
        return serialized_action_features(payload, digest=digest)
    except DecisionStateError as exc:
        raise ModelValidationError("dataset action payload is malformed") from exc


def _example_feature_domain(example: RuleBCExample) -> str:
    try:
        ordered = is_ordered_selection(example.selection_type, example.selection_context)
    except ValueError as exc:
        raise ModelValidationError("example has an unknown CABT selection schema") from exc
    if ordered:
        raise ModelValidationError(
            "candidate-wise Student cannot train ordered Skill labels"
        )
    domains: set[str] = set()
    for action in example.legal_actions:
        payload = action.get("payload") if isinstance(action, dict) else None
        try:
            domains.add(serialized_action_feature_domain(payload))
        except DecisionStateError as exc:
            raise ModelValidationError("dataset action payload is malformed") from exc
    if len(domains) != 1:
        raise ModelValidationError("example mixes incompatible Student feature domains")
    return domains.pop()


def training_feature_domain(examples: Iterable[RuleBCExample]) -> str:
    domains = {_example_feature_domain(example) for example in examples}
    if len(domains) != 1:
        raise ModelValidationError("training dataset has mixed incompatible Student feature domains")
    return domains.pop()


def example_matrix(example: RuleBCExample) -> tuple[list[list[float]], list[int]]:
    _example_feature_domain(example)
    state = state_features_payload(example.public_state, example.own_private_state, example.visible_history)
    matrix = [[*state, *_action_feature_vector(action)] for action in example.legal_actions]
    target = [index for index, action in enumerate(example.legal_actions) if action["digest"] in example.target_action_digests]
    if not target and example.min_count > 0:
        raise ModelValidationError("mandatory example has no teacher target")
    return matrix, target


@dataclass(frozen=True, slots=True)
class StudentV0Model:
    """Candidate-wise linear score model; each legal set receives a softmax."""

    weights: tuple[float, ...]
    bias: float = 0.0
    feature_domain: str = PRIVATE_ACTIONKEY_FEATURE_DOMAIN

    def __post_init__(self) -> None:
        if len(self.weights) != MODEL_FEATURE_DIM:
            raise ModelValidationError("unexpected model feature dimension")
        if not all(math.isfinite(value) for value in (*self.weights, self.bias)):
            raise ModelValidationError("model contains non-finite values")
        if self.feature_domain not in ACTION_FEATURE_DOMAINS:
            raise ModelValidationError("unsupported Student feature domain")

    def score_vector(self, vectors: Iterable[list[float]]) -> list[float]:
        scores: list[float] = []
        for vector in vectors:
            if len(vector) != MODEL_FEATURE_DIM:
                raise ModelValidationError("feature dimension mismatch")
            score = self.bias + sum(weight * value for weight, value in zip(self.weights, vector, strict=True))
            if not math.isfinite(score):
                raise ModelValidationError("model produced a non-finite score")
            scores.append(score)
        return scores

    def to_dict(self) -> dict[str, object]:
        return {
            "feature_version": FEATURE_VERSION,
            "model_schema_version": MODEL_SCHEMA_VERSION,
            "model_type": "linear-candidate-scorer",
            "weights": list(self.weights),
            "bias": self.bias,
            "feature_domain": self.feature_domain,
        }

    @classmethod
    def from_dict(cls, value: object) -> "StudentV0Model":
        if not isinstance(value, dict):
            raise ModelValidationError("model artifact must be a JSON object")
        schema_version = value.get("model_schema_version")
        if schema_version == MODEL_SCHEMA_VERSION:
            if set(value) != _MODEL_V2_FIELDS:
                raise ModelValidationError("model v2 has unexpected or missing fields")
            feature_domain = value.get("feature_domain")
            if type(feature_domain) is not str:
                raise ModelValidationError("Student feature domain must be a string")
        elif schema_version == LEGACY_MODEL_SCHEMA_VERSION:
            if set(value) != _MODEL_V1_FIELDS:
                raise ModelValidationError("legacy model v1 has unexpected or missing fields")
            # v1 was trained against the original ActionKey core, before the
            # v2 private locator and option-union additions.  Its schema tag
            # therefore selects the frozen legacy projection explicitly.
            feature_domain = LEGACY_ACTIONKEY_FEATURE_DOMAIN
        else:
            raise ModelValidationError("unsupported model schema or feature version")
        if value.get("feature_version") != FEATURE_VERSION:
            raise ModelValidationError("unsupported model schema or feature version")
        if value.get("model_type") != "linear-candidate-scorer":
            raise ModelValidationError("unsupported Student model type")
        weights = value.get("weights")
        bias = value.get("bias")
        if not isinstance(weights, list) or any(type(item) not in (int, float) for item in weights):
            raise ModelValidationError("model weights must be finite JSON numbers")
        if type(bias) not in (int, float):
            raise ModelValidationError("model bias must be a finite JSON number")
        return cls(
            tuple(float(item) for item in weights),
            float(bias),
            feature_domain,
        )

    def export(self, path: str | Path) -> None:
        destination = Path(path)
        with destination.open("x", encoding="utf-8") as handle:
            handle.write(_canonical_json(self.to_dict()) + "\n")

    @classmethod
    def load(cls, path: str | Path) -> "StudentV0Model":
        try:
            with Path(path).open(encoding="utf-8") as handle:
                return cls.from_dict(json.load(handle))
        except (OSError, json.JSONDecodeError, ModelValidationError) as exc:
            raise ModelValidationError(f"could not load Student v0 model: {exc}") from exc


def train_model(
    examples: Iterable[RuleBCExample],
    *,
    epochs: int = 120,
    learning_rate: float = 0.15,
    example_weights: Mapping[str, float] | None = None,
    on_epoch: "Callable[[int, int, float, tuple[float, ...], float], None] | None" = None,
) -> StudentV0Model:
    """Fit deterministic full-batch cross-entropy over each legal candidate set.

    NumPy is deliberately optional here; the compact calculation is expressed
    with Python floats so the trainer and runtime share exact feature rules.
    """
    values = list(examples)
    if not values:
        raise ModelValidationError("cannot train on an empty dataset")
    if epochs < 1 or not math.isfinite(learning_rate) or learning_rate <= 0:
        raise ValueError("epochs and learning_rate must be positive")
    feature_domain = training_feature_domain(values)
    if example_weights is not None:
        if set(example_weights) != {example.example_id for example in values}:
            raise ModelValidationError("example_weights must cover exactly the training examples")
        for example_id, weight in example_weights.items():
            if type(weight) not in (int, float) or not math.isfinite(float(weight)) or float(weight) <= 0.0:
                raise ModelValidationError(f"example_weights contains an invalid weight for {example_id}")
    weights_by_id = example_weights or {}
    weights = [0.0] * MODEL_FEATURE_DIM
    bias = 0.0
    for epoch_index in range(epochs):
        gradient = [0.0] * MODEL_FEATURE_DIM
        bias_gradient = 0.0
        useful = 0
        total_weight = 0.0
        epoch_loss = 0.0
        for example in values:
            example_weight = float(weights_by_id.get(example.example_id, 1.0))
            matrix, targets = example_matrix(example)
            if not targets:
                continue
            scores = [bias + sum(weight * feature for weight, feature in zip(weights, row, strict=True)) for row in matrix]
            maximum = max(scores)
            exp_scores = [math.exp(min(80.0, score - maximum)) for score in scores]
            normalizer = sum(exp_scores)
            target_probability = 1.0 / len(targets)
            target_set = set(targets)
            example_target_probability = sum(exp_scores[index] / normalizer for index in target_set)
            epoch_loss += example_weight * -math.log(max(example_target_probability, 1e-12))
            for index, row in enumerate(matrix):
                delta = example_weight * (exp_scores[index] / normalizer - (target_probability if index in target_set else 0.0))
                for feature_index, feature in enumerate(row):
                    gradient[feature_index] += delta * feature
                bias_gradient += delta
            useful += 1
            total_weight += example_weight
        if useful == 0 or total_weight <= 0.0:
            raise ModelValidationError("dataset has no selectable teacher targets")
        scale = learning_rate / total_weight
        weights = [weight - scale * change for weight, change in zip(weights, gradient, strict=True)]
        bias -= scale * bias_gradient
        if on_epoch is not None:
            on_epoch(epoch_index, epochs, epoch_loss / total_weight, tuple(weights), bias)
    return StudentV0Model(tuple(weights), bias, feature_domain)


__all__ = [
    "MODEL_FEATURE_DIM",
    "MODEL_SCHEMA_VERSION",
    "LEGACY_MODEL_SCHEMA_VERSION",
    "ModelValidationError",
    "StudentV0Model",
    "example_matrix",
    "training_feature_domain",
    "train_model",
]
