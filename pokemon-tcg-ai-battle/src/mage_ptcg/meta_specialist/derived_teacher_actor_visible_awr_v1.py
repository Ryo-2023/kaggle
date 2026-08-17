"""Cross-fitted actor-visible value and AWR weights for derived teachers.

The source snapshots contain an actor's legal information view.  That view is
not strict public information: it includes own-private fields such as the
actor's hand.  This module therefore uses the exact term ``actor-visible`` and
never grants a ``public-only`` interpretation to its value features.

Teacher, opponent, seat, deck, policy, and episode identities are provenance or
split metadata only.  They never enter the fixed 56-dimensional ridge feature
vector.  Behaviour probabilities are not required for the replay-only AWR and
strict-positive filtered-BC weights materialised here.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
from typing import Any


SCHEMA_V1 = "meta-specialist-derived-teacher-actor-visible-awr-sidecar-v1"
SIDECAR_ROW_SCHEMA_V1 = "meta-specialist-derived-teacher-actor-visible-awr-row-v1"
ACTOR_VISIBLE_VALUE_FEATURE_SCHEMA_V1 = (
    "actor-visible-specialist-state-41-scalars-step-structure-56-v1"
)
DEFAULT_FOLD_SEED_V1 = "derived-teacher-actor-visible-awr-folds-v1-20260813"
FOLD_ALGORITHM_V1 = "sha256-domain-seed-episode-mod-v1"
NORMALIZATION_ALGORITHM_V1 = "train-mean-one-upper-bounded-waterfill-v1"
FILTER_RULE_V1 = "advantage-strictly-positive-v1"
FIT_SPLIT_V1 = "train"
HELDOUT_SPLITS_V1 = frozenset({
    "development", "validation", "test", "opponent_holdout", "deck_holdout",
})
ALLOWED_SPLITS_V1 = frozenset({FIT_SPLIT_V1, *HELDOUT_SPLITS_V1})
_FEATURE_DIMENSION = 56
_EXPONENT_LOWER_CLIP = -50.0
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_FOLD_DOMAIN = b"mage-ptcg:derived-teacher-actor-visible-awr-fold:v1\0"
_COEFFICIENT_DOMAIN = b"mage-ptcg:derived-teacher-actor-visible-awr-coefficients:v1\0"
_MANIFEST_DOMAIN = b"mage-ptcg:derived-teacher-actor-visible-awr-manifest:v1\0"


class ActorVisibleAwrError(ValueError):
    """Raised when an AWR sidecar or one of its source contracts is invalid."""


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(
            value, ensure_ascii=False, allow_nan=False, sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ActorVisibleAwrError("value is not finite canonical JSON") from exc


def _digest(value: object, *, domain: bytes) -> str:
    return hashlib.sha256(domain + _canonical(value)).hexdigest()


def _sha(value: object, *, field: str) -> str:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        raise ActorVisibleAwrError(f"{field} must be a lowercase SHA-256")
    return value


def _finite(value: object, *, field: str) -> float:
    if (
        type(value) not in (int, float)
        or type(value) is bool
        or not math.isfinite(float(value))
    ):
        raise ActorVisibleAwrError(f"{field} must be finite")
    return float(value)


def _nonempty_text(value: object, *, field: str) -> str:
    if type(value) is not str or not value:
        raise ActorVisibleAwrError(f"{field} must be a non-empty string")
    return value


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def episode_fold_v1(
    episode_id: str,
    *,
    fold_count: int,
    fold_seed: str = DEFAULT_FOLD_SEED_V1,
) -> int:
    """Return the precommitted episode-atomic fold without using identity features."""

    _sha(episode_id, field="episode_id")
    if type(fold_count) is not int or fold_count < 2:
        raise ActorVisibleAwrError("fold_count must be at least two")
    _nonempty_text(fold_seed, field="fold_seed")
    digest = hashlib.sha256(
        _FOLD_DOMAIN + fold_seed.encode("utf-8") + b"\0" + episode_id.encode("ascii")
    ).digest()
    return int.from_bytes(digest[:8], "big") % fold_count


def actor_visible_value_features_v1(example: Mapping[str, object]) -> tuple[float, ...]:
    """Extract the fixed value vector while ignoring all provenance metadata.

    The snapshot itself is validated by ``training_snapshot_v1`` before this
    extractor is used in production.  This narrow function intentionally reads
    only ``model_input`` and the first canonical ``loss_rows`` step.
    """

    if not isinstance(example, Mapping):
        raise ActorVisibleAwrError("snapshot example must be a mapping")
    model = example.get("model_input")
    rows = example.get("loss_rows")
    if not isinstance(model, Mapping) or type(rows) is not list or not rows:
        raise ActorVisibleAwrError("snapshot example lacks model_input/loss_rows")
    scalars = model.get("state_scalars")
    if (
        type(scalars) is not list
        or len(scalars) != 41
        or any(type(value) is not int or value < 0 for value in scalars)
    ):
        raise ActorVisibleAwrError("actor-visible state scalar schema is invalid")
    state_scales = (
        2.0, 256.0, 128.0, 128.0, 10.0, 48.0, 64.0, 64.0, 64.0,
        256.0, 256.0, 1.0, 1.0, 1.0, 1.0,
        64.0, 64.0, 64.0, 64.0, 64.0, 64.0, 64.0, 64.0,
        1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0,
        1.0, 1.0, 64.0, 1.0, 1.0, 1.0, 64.0, 64.0,
    )
    features = [
        min(1.0, float(value) / scale)
        for value, scale in zip(scalars, state_scales, strict=True)
    ]

    first = rows[0]
    if not isinstance(first, Mapping):
        raise ActorVisibleAwrError("first actor-visible loss row is invalid")
    prefix = first.get("semantic_prefix")
    tokens = first.get("token_masses")
    if type(prefix) is not list or type(tokens) is not list or not tokens:
        raise ActorVisibleAwrError("first actor-visible step structure is invalid")
    semantic_tokens: list[Mapping[str, object]] = []
    stop_available = False
    option_types: set[int] = set()
    for token in tokens:
        if not isinstance(token, Mapping) or token.get("kind") not in {"semantic", "stop"}:
            raise ActorVisibleAwrError("first step token has an invalid kind")
        if token["kind"] == "stop":
            if stop_available:
                raise ActorVisibleAwrError("first step contains duplicate STOP")
            stop_available = True
            continue
        semantic = token.get("semantic_action")
        if not isinstance(semantic, Mapping):
            raise ActorVisibleAwrError("first step semantic token is invalid")
        option_type = semantic.get("option_type")
        if type(option_type) is not int or option_type < 0:
            raise ActorVisibleAwrError("first step option_type is invalid")
        semantic_tokens.append(semantic)
        option_types.add(option_type)
    effective_domain = len(semantic_tokens) + int(stop_available)
    if effective_domain < 1:
        raise ActorVisibleAwrError("first actor-visible step has no effective domain")

    entities = model.get("pokemon_entities")
    bags = model.get("card_bags")
    if type(entities) is not list or not isinstance(bags, Mapping):
        raise ActorVisibleAwrError("actor-visible entities/card bags are invalid")
    expected_bags = {
        "own_hand", "deck_reveal", "looking_visible", "self_discard", "opponent_discard",
    }
    if set(bags) != expected_bags:
        raise ActorVisibleAwrError("actor-visible card bag schema is open or incomplete")
    visible_card_count = 0
    for name in sorted(expected_bags):
        bag = bags[name]
        if not isinstance(bag, Mapping) or type(bag.get("mask")) is not list:
            raise ActorVisibleAwrError("actor-visible card bag mask is invalid")
        mask = bag["mask"]
        if any(type(value) is not int or value not in (0, 1) for value in mask):
            raise ActorVisibleAwrError("actor-visible card bag mask is not binary")
        visible_card_count += sum(mask)
    features.extend((
        min(1.0, effective_domain / 64.0),
        float(stop_available),
        min(1.0, len(prefix) / 64.0),
        min(1.0, len(entities) / 122.0),
        min(1.0, visible_card_count / 256.0),
    ))
    features.extend(float(option_type in option_types) for option_type in range(7, 16))
    features.append(1.0)
    if len(features) != _FEATURE_DIMENSION or any(not math.isfinite(value) for value in features):
        raise ActorVisibleAwrError("actor-visible value feature vector is invalid")
    return tuple(features)


@dataclass(frozen=True, slots=True)
class ActorVisibleAwrSampleV1:
    record_id: str
    record_content_hash: str
    episode_id: str
    split: str
    teacher_id: str
    action_type: str
    features: tuple[float, ...]
    value_target: float
    example_quality_weight: float

    def __post_init__(self) -> None:
        _sha(self.record_id, field="record_id")
        _sha(self.record_content_hash, field="record_content_hash")
        _sha(self.episode_id, field="episode_id")
        if self.split not in ALLOWED_SPLITS_V1:
            raise ActorVisibleAwrError("sample split is outside the closed split set")
        _nonempty_text(self.teacher_id, field="teacher_id")
        _nonempty_text(self.action_type, field="action_type")
        if (
            type(self.features) is not tuple
            or len(self.features) != _FEATURE_DIMENSION
            or any(
                type(value) not in (int, float)
                or type(value) is bool
                or not math.isfinite(float(value))
                for value in self.features
            )
        ):
            raise ActorVisibleAwrError("sample features must be the finite 56-tuple")
        target = _finite(self.value_target, field="value_target")
        if not -1.0 <= target <= 1.0:
            raise ActorVisibleAwrError("value_target must be in [-1, 1]")
        quality = _finite(self.example_quality_weight, field="example_quality_weight")
        if not 0.0 < quality <= 1.0:
            raise ActorVisibleAwrError("example_quality_weight must be in (0, 1]")

    def fold_index(
        self,
        fold_count: int,
        fold_seed: str = DEFAULT_FOLD_SEED_V1,
    ) -> int:
        return episode_fold_v1(
            self.episode_id, fold_count=fold_count, fold_seed=fold_seed,
        )


def sample_from_training_snapshot_example_v1(
    example: Mapping[str, object], *, teacher_id: str,
) -> ActorVisibleAwrSampleV1:
    """Project one already-validated snapshot row into value input + metadata."""

    _nonempty_text(teacher_id, field="teacher_id")
    if not isinstance(example, Mapping):
        raise ActorVisibleAwrError("training snapshot example must be a mapping")
    model = example.get("model_input")
    if not isinstance(model, Mapping):
        raise ActorVisibleAwrError("training snapshot example lacks model_input")
    scalars = model.get("state_scalars")
    if type(scalars) is not list or len(scalars) != 41:
        raise ActorVisibleAwrError("training snapshot state scalars are invalid")
    selection_type = scalars[4]
    selection_context = scalars[5]
    if type(selection_type) is not int or type(selection_context) is not int:
        raise ActorVisibleAwrError("training snapshot selection schema is invalid")
    return ActorVisibleAwrSampleV1(
        record_id=example.get("record_id"),  # type: ignore[arg-type]
        record_content_hash=example.get("record_content_hash"),  # type: ignore[arg-type]
        episode_id=example.get("episode_id_hash"),  # type: ignore[arg-type]
        split=example.get("split"),  # type: ignore[arg-type]
        teacher_id=teacher_id,
        action_type=(
            f"selection_type={selection_type}/selection_context={selection_context}"
        ),
        features=actor_visible_value_features_v1(example),
        value_target=example.get("value_target"),  # type: ignore[arg-type]
        example_quality_weight=example.get("example_quality_weight"),  # type: ignore[arg-type]
    )


@dataclass(frozen=True, slots=True)
class ActorVisibleAwrRowV1:
    record_id: str
    record_content_hash: str
    episode_id: str
    split: str
    teacher_id: str
    action_type: str
    fold_index: int | None
    fit_membership: bool
    value_estimation: str
    value_target: float
    baseline_value: float
    advantage: float
    awr_weight: float
    example_quality_weight: float
    effective_weight: float
    filtered_bc_eligible: bool
    schema_version: str = SIDECAR_ROW_SCHEMA_V1

    def __post_init__(self) -> None:
        _sha(self.record_id, field="row.record_id")
        _sha(self.record_content_hash, field="row.record_content_hash")
        _sha(self.episode_id, field="row.episode_id")
        if self.split not in ALLOWED_SPLITS_V1:
            raise ActorVisibleAwrError("row split is outside the closed split set")
        _nonempty_text(self.teacher_id, field="row.teacher_id")
        _nonempty_text(self.action_type, field="row.action_type")
        if type(self.fit_membership) is not bool or self.fit_membership != (self.split == FIT_SPLIT_V1):
            raise ActorVisibleAwrError("row fit membership must equal split=train")
        if self.fit_membership:
            if type(self.fold_index) is not int or self.fold_index < 0:
                raise ActorVisibleAwrError("train row needs a nonnegative fold")
            if self.value_estimation != "cross_fitted_train":
                raise ActorVisibleAwrError("train row value estimation is not cross-fitted")
        elif self.fold_index is not None or self.value_estimation != "full_train_heldout":
            raise ActorVisibleAwrError("heldout row may only use the full-train model")
        target = _finite(self.value_target, field="row.value_target")
        baseline = _finite(self.baseline_value, field="row.baseline_value")
        advantage = _finite(self.advantage, field="row.advantage")
        if not math.isclose(target - baseline, advantage, rel_tol=0.0, abs_tol=1e-12):
            raise ActorVisibleAwrError("row advantage does not equal G - V(s)")
        weight = _finite(self.awr_weight, field="row.awr_weight")
        quality = _finite(self.example_quality_weight, field="row.example_quality_weight")
        effective = _finite(self.effective_weight, field="row.effective_weight")
        if weight <= 0.0 or not 0.0 < quality <= 1.0:
            raise ActorVisibleAwrError("row AWR/quality weight is outside its domain")
        if not math.isclose(weight * quality, effective, rel_tol=1e-12, abs_tol=1e-12):
            raise ActorVisibleAwrError("row effective weight is not quality * AWR")
        if type(self.filtered_bc_eligible) is not bool or self.filtered_bc_eligible != (advantage > 0.0):
            raise ActorVisibleAwrError("row filtered-BC eligibility is not strict-positive advantage")
        if self.schema_version != SIDECAR_ROW_SCHEMA_V1:
            raise ActorVisibleAwrError("sidecar row cannot be reclassified")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "record_id": self.record_id,
            "record_content_hash": self.record_content_hash,
            "episode_id": self.episode_id,
            "split": self.split,
            "teacher_id": self.teacher_id,
            "action_type": self.action_type,
            "fold_index": self.fold_index,
            "fit_membership": self.fit_membership,
            "value_estimation": self.value_estimation,
            "value_target": self.value_target,
            "baseline_value": self.baseline_value,
            "advantage": self.advantage,
            "awr_weight": self.awr_weight,
            "example_quality_weight": self.example_quality_weight,
            "effective_weight": self.effective_weight,
            "filtered_bc_eligible": self.filtered_bc_eligible,
        }


@dataclass(frozen=True, slots=True)
class ActorVisibleAwrBuildResultV1:
    rows: tuple[ActorVisibleAwrRowV1, ...]
    fold_models: tuple[dict[str, object], ...]
    full_train_model: dict[str, object]
    weighting: dict[str, object]
    fold_assignment_sha256: str
    fit_splits: tuple[str, ...] = (FIT_SPLIT_V1,)

    def __post_init__(self) -> None:
        if not self.rows or any(type(row) is not ActorVisibleAwrRowV1 for row in self.rows):
            raise ActorVisibleAwrError("build result needs canonical sidecar rows")
        if tuple(row.record_id for row in self.rows) != tuple(sorted(row.record_id for row in self.rows)):
            raise ActorVisibleAwrError("build result rows must be sorted by record_id")
        _sha(self.fold_assignment_sha256, field="fold_assignment_sha256")
        if self.fit_splits != (FIT_SPLIT_V1,):
            raise ActorVisibleAwrError("only train may be a fit split")


def _fit_ridge(samples: Sequence[ActorVisibleAwrSampleV1], *, ridge_lambda: float) -> tuple[float, ...]:
    if not samples:
        raise ActorVisibleAwrError("ridge fit has no samples")
    ridge = _finite(ridge_lambda, field="ridge_lambda")
    if ridge <= 0.0:
        raise ActorVisibleAwrError("ridge_lambda must be positive")
    import torch

    x = torch.tensor([sample.features for sample in samples], dtype=torch.float64)
    y = torch.tensor([sample.value_target for sample in samples], dtype=torch.float64)
    identity = torch.eye(_FEATURE_DIMENSION, dtype=torch.float64)
    try:
        coefficients = torch.linalg.solve(x.T @ x + ridge * identity, x.T @ y)
    except RuntimeError as exc:
        raise ActorVisibleAwrError("actor-visible ridge solve failed") from exc
    result = tuple(float(value) for value in coefficients.tolist())
    if len(result) != _FEATURE_DIMENSION or any(not math.isfinite(value) for value in result):
        raise ActorVisibleAwrError("ridge coefficients are invalid")
    return result


def _predict(features: Sequence[float], coefficients: Sequence[float]) -> float:
    value = math.fsum(
        float(feature) * float(coefficient)
        for feature, coefficient in zip(features, coefficients, strict=True)
    )
    if not math.isfinite(value):
        raise ActorVisibleAwrError("value prediction is non-finite")
    return value


def _coefficient_sha(coefficients: Sequence[float]) -> str:
    return _digest(
        {"feature_schema": ACTOR_VISIBLE_VALUE_FEATURE_SCHEMA_V1, "coefficients": list(coefficients)},
        domain=_COEFFICIENT_DOMAIN,
    )


def _set_sha(values: Sequence[str], *, label: str) -> str:
    return _digest({"label": label, "values": sorted(values)}, domain=_FOLD_DOMAIN)


def _raw_awr_weight(advantage: float, *, beta: float, max_weight: float) -> float:
    exponent = advantage / beta
    upper = math.log(max_weight)
    if exponent >= upper:
        return max_weight
    return math.exp(max(_EXPONENT_LOWER_CLIP, exponent))


def _mean_one_bounded_scale(raw_weights: Sequence[float], *, max_weight: float) -> float:
    if not raw_weights or any(not math.isfinite(value) or value <= 0.0 for value in raw_weights):
        raise ActorVisibleAwrError("normalization needs positive finite training weights")
    if max_weight < 1.0:
        raise ActorVisibleAwrError("max_weight must be at least one for mean-one normalization")

    def mean_for(scale: float) -> float:
        return math.fsum(min(max_weight, scale * value) for value in raw_weights) / len(raw_weights)

    low = 0.0
    high = 1.0
    while mean_for(high) < 1.0:
        high *= 2.0
        if not math.isfinite(high):
            raise ActorVisibleAwrError("AWR normalization scale overflowed")
    for _ in range(160):
        middle = (low + high) / 2.0
        if mean_for(middle) < 1.0:
            low = middle
        else:
            high = middle
    return (low + high) / 2.0


def build_cross_fitted_actor_visible_awr_v1(
    samples: Sequence[ActorVisibleAwrSampleV1],
    *,
    fold_count: int = 5,
    fold_seed: str = DEFAULT_FOLD_SEED_V1,
    ridge_lambda: float = 1.0,
    beta: float = 1.0,
    max_weight: float = 20.0,
) -> ActorVisibleAwrBuildResultV1:
    """Fit episode-cross-fitted train values and full-train heldout values."""

    if isinstance(samples, (str, bytes)) or not samples or any(
        type(sample) is not ActorVisibleAwrSampleV1 for sample in samples
    ):
        raise ActorVisibleAwrError("samples must be nonempty canonical AWR samples")
    if type(fold_count) is not int or fold_count < 2:
        raise ActorVisibleAwrError("fold_count must be at least two")
    _nonempty_text(fold_seed, field="fold_seed")
    ridge = _finite(ridge_lambda, field="ridge_lambda")
    beta_value = _finite(beta, field="beta")
    cap = _finite(max_weight, field="max_weight")
    if ridge <= 0.0 or beta_value <= 0.0 or cap < 1.0:
        raise ActorVisibleAwrError("ridge/beta/max_weight configuration is invalid")
    ordered = tuple(sorted(samples, key=lambda sample: sample.record_id))
    record_ids = [sample.record_id for sample in ordered]
    if len(record_ids) != len(set(record_ids)):
        raise ActorVisibleAwrError("record IDs must be unique")

    episode_contract: dict[str, tuple[str, str, float]] = {}
    for sample in ordered:
        contract = (sample.split, sample.teacher_id, sample.value_target)
        prior = episode_contract.setdefault(sample.episode_id, contract)
        if prior != contract:
            raise ActorVisibleAwrError("one episode crosses split/teacher/outcome boundaries")
    train = tuple(sample for sample in ordered if sample.split == FIT_SPLIT_V1)
    train_episodes = sorted({sample.episode_id for sample in train})
    if len(train_episodes) <= fold_count:
        raise ActorVisibleAwrError("train needs more episodes than folds")
    assignments = {
        episode: episode_fold_v1(episode, fold_count=fold_count, fold_seed=fold_seed)
        for episode in train_episodes
    }
    if set(assignments.values()) != set(range(fold_count)):
        raise ActorVisibleAwrError("precommitted folds leave an empty train score fold")

    fold_coefficients: dict[int, tuple[float, ...]] = {}
    fold_models: list[dict[str, object]] = []
    for fold in range(fold_count):
        fit_episodes = sorted(episode for episode, value in assignments.items() if value != fold)
        score_episodes = sorted(episode for episode, value in assignments.items() if value == fold)
        intersection = set(fit_episodes) & set(score_episodes)
        if intersection:
            raise ActorVisibleAwrError("cross-fit fold leaks score episodes into fit")
        fit_samples = tuple(sample for sample in train if sample.episode_id in set(fit_episodes))
        coefficients = _fit_ridge(fit_samples, ridge_lambda=ridge)
        fold_coefficients[fold] = coefficients
        fold_models.append({
            "fold_index": fold,
            "fit_split": FIT_SPLIT_V1,
            "fit_episode_count": len(fit_episodes),
            "score_episode_count": len(score_episodes),
            "fit_record_count": len(fit_samples),
            "score_record_count": sum(sample.episode_id in set(score_episodes) for sample in train),
            "fit_episode_set_sha256": _set_sha(fit_episodes, label=f"fold-{fold}-fit"),
            "score_episode_set_sha256": _set_sha(score_episodes, label=f"fold-{fold}-score"),
            "fit_score_episode_intersection_count": len(intersection),
            "coefficients_sha256": _coefficient_sha(coefficients),
        })
    full_coefficients = _fit_ridge(train, ridge_lambda=ridge)
    full_train_model = {
        "fit_split": FIT_SPLIT_V1,
        "fit_episode_count": len(train_episodes),
        "fit_record_count": len(train),
        "fit_episode_set_sha256": _set_sha(train_episodes, label="full-train-fit"),
        "coefficients_sha256": _coefficient_sha(full_coefficients),
    }

    baselines: dict[str, float] = {}
    advantages: dict[str, float] = {}
    raw_weights: dict[str, float] = {}
    for sample in ordered:
        if sample.split == FIT_SPLIT_V1:
            coefficients = fold_coefficients[assignments[sample.episode_id]]
        else:
            coefficients = full_coefficients
        baseline = _predict(sample.features, coefficients)
        advantage = sample.value_target - baseline
        if not math.isfinite(advantage):
            raise ActorVisibleAwrError("AWR advantage is non-finite")
        baselines[sample.record_id] = baseline
        advantages[sample.record_id] = advantage
        raw_weights[sample.record_id] = _raw_awr_weight(
            advantage, beta=beta_value, max_weight=cap,
        )
    train_raw = [raw_weights[sample.record_id] for sample in train]
    normalization_scale = _mean_one_bounded_scale(train_raw, max_weight=cap)

    rows: list[ActorVisibleAwrRowV1] = []
    for sample in ordered:
        advantage = advantages[sample.record_id]
        weight = min(cap, normalization_scale * raw_weights[sample.record_id])
        rows.append(ActorVisibleAwrRowV1(
            record_id=sample.record_id,
            record_content_hash=sample.record_content_hash,
            episode_id=sample.episode_id,
            split=sample.split,
            teacher_id=sample.teacher_id,
            action_type=sample.action_type,
            fold_index=assignments[sample.episode_id] if sample.split == FIT_SPLIT_V1 else None,
            fit_membership=sample.split == FIT_SPLIT_V1,
            value_estimation=(
                "cross_fitted_train" if sample.split == FIT_SPLIT_V1 else "full_train_heldout"
            ),
            value_target=sample.value_target,
            baseline_value=baselines[sample.record_id],
            advantage=advantage,
            awr_weight=weight,
            example_quality_weight=sample.example_quality_weight,
            effective_weight=weight * sample.example_quality_weight,
            filtered_bc_eligible=advantage > 0.0,
        ))
    normalized_train = [row.awr_weight for row in rows if row.split == FIT_SPLIT_V1]
    normalized_mean = math.fsum(normalized_train) / len(normalized_train)
    if not math.isclose(normalized_mean, 1.0, rel_tol=0.0, abs_tol=1e-12):
        raise ActorVisibleAwrError("training AWR weights did not normalize to mean one")
    weighting = {
        "advantage_definition": "terminal_value_target_minus_actor_visible_value",
        "beta": beta_value,
        "raw_exponential_upper_clip": cap,
        "exponent_lower_clip": _EXPONENT_LOWER_CLIP,
        "normalization_algorithm": NORMALIZATION_ALGORITHM_V1,
        "normalization_fit_split": FIT_SPLIT_V1,
        "normalization_scale": normalization_scale,
        "normalized_upper_bound": cap,
        "normalized_training_mean": normalized_mean,
        "filtered_bc_rule": FILTER_RULE_V1,
        "behavior_probability_required": False,
        "behavior_probability_used": False,
    }
    fold_assignment_sha = _digest({
        "algorithm": FOLD_ALGORITHM_V1,
        "fold_seed": fold_seed,
        "fold_count": fold_count,
        "assignments": [
            {"episode_id": episode, "fold_index": assignments[episode]}
            for episode in train_episodes
        ],
    }, domain=_FOLD_DOMAIN)
    return ActorVisibleAwrBuildResultV1(
        rows=tuple(rows),
        fold_models=tuple(fold_models),
        full_train_model=full_train_model,
        weighting=weighting,
        fold_assignment_sha256=fold_assignment_sha,
    )


def _metrics(rows: Sequence[ActorVisibleAwrRowV1]) -> dict[str, object]:
    if not rows:
        raise ActorVisibleAwrError("diagnostic group cannot be empty")
    effective = [row.effective_weight for row in rows]
    total = math.fsum(effective)
    square_total = math.fsum(value * value for value in effective)
    signs: dict[str, dict[str, object]] = {}
    for name, predicate in (
        ("positive", lambda value: value > 0.0),
        ("zero", lambda value: value == 0.0),
        ("negative", lambda value: value < 0.0),
    ):
        selected = [row for row in rows if predicate(row.advantage)]
        signs[name] = {
            "count": len(selected),
            "effective_mass": math.fsum(row.effective_weight for row in selected),
        }
    return {
        "row_count": len(rows),
        "quality_mass": math.fsum(row.example_quality_weight for row in rows),
        "awr_mass": math.fsum(row.awr_weight for row in rows),
        "effective_mass": total,
        "effective_sample_size": (total * total / square_total) if square_total else 0.0,
        "filtered_bc_eligible_count": sum(row.filtered_bc_eligible for row in rows),
        "advantage_mean": math.fsum(row.advantage for row in rows) / len(rows),
        "advantage_min": min(row.advantage for row in rows),
        "advantage_max": max(row.advantage for row in rows),
        "advantage_sign": signs,
    }


def actor_visible_awr_diagnostics_v1(
    rows: Sequence[ActorVisibleAwrRowV1],
) -> dict[str, object]:
    if not rows:
        raise ActorVisibleAwrError("sidecar diagnostics need rows")
    by_teacher: dict[str, list[ActorVisibleAwrRowV1]] = defaultdict(list)
    by_action: dict[str, list[ActorVisibleAwrRowV1]] = defaultdict(list)
    by_teacher_action: dict[tuple[str, str], list[ActorVisibleAwrRowV1]] = defaultdict(list)
    by_split: dict[str, list[ActorVisibleAwrRowV1]] = defaultdict(list)
    for row in rows:
        by_teacher[row.teacher_id].append(row)
        by_action[row.action_type].append(row)
        by_teacher_action[(row.teacher_id, row.action_type)].append(row)
        by_split[row.split].append(row)
    return {
        "overall": _metrics(rows),
        "split": [
            {"split": key, **_metrics(value)} for key, value in sorted(by_split.items())
        ],
        "teacher": [
            {"teacher_id": key, **_metrics(value)} for key, value in sorted(by_teacher.items())
        ],
        "action_type": [
            {"action_type": key, **_metrics(value)} for key, value in sorted(by_action.items())
        ],
        "teacher_action_type": [
            {"teacher_id": key[0], "action_type": key[1], **_metrics(value)}
            for key, value in sorted(by_teacher_action.items())
        ],
    }


def build_derived_teacher_awr_manifest_payload_v1(
    *,
    result: ActorVisibleAwrBuildResultV1,
    catalog_binding: Mapping[str, object],
    decision_binding: Mapping[str, object],
    source_bindings: Sequence[Mapping[str, object]],
    sidecar_binding: Mapping[str, object],
) -> dict[str, object]:
    """Build a self-hashed manifest without conferring execution authority."""

    if type(result) is not ActorVisibleAwrBuildResultV1:
        raise ActorVisibleAwrError("result is not a canonical AWR build")
    if not source_bindings:
        raise ActorVisibleAwrError("manifest needs source bindings")
    payload: dict[str, object] = {
        "schema_version": SCHEMA_V1,
        "catalog": dict(catalog_binding),
        "decision": dict(decision_binding),
        "sources": [dict(row) for row in source_bindings],
        "feature_contract": {
            "feature_schema": ACTOR_VISIBLE_VALUE_FEATURE_SCHEMA_V1,
            "feature_dimension": _FEATURE_DIMENSION,
            "information_boundary": "actor-visible-including-own-private-state",
            "strict_public_only": False,
            "metadata_excluded_from_value_features": [
                "opponent_id", "seat", "teacher_id", "policy_sha256", "deck_sha256",
                "episode_id",
            ],
        },
        "cross_fitting": {
            "fit_splits": list(result.fit_splits),
            "fit_forbidden_splits": sorted(HELDOUT_SPLITS_V1),
            "fold_algorithm": FOLD_ALGORITHM_V1,
            "fold_assignment_sha256": result.fold_assignment_sha256,
            "fold_models": list(result.fold_models),
            "full_train_model": dict(result.full_train_model),
        },
        "weighting": dict(result.weighting),
        "behavior_probability_required": False,
        "behavior_probability_used": False,
        "counts": {
            "rows": len(result.rows),
            "episodes": len({row.episode_id for row in result.rows}),
            "train_rows": sum(row.split == FIT_SPLIT_V1 for row in result.rows),
            "heldout_rows": sum(row.split != FIT_SPLIT_V1 for row in result.rows),
            "teachers": len({row.teacher_id for row in result.rows}),
        },
        "diagnostics": actor_visible_awr_diagnostics_v1(result.rows),
        "sidecar": dict(sidecar_binding),
        "authority": {
            "training_authority": False,
            "promotion_authority": False,
            "submission_authority": False,
            "longrun_authority": False,
        },
        "manifest_sha256": "",
    }
    payload["manifest_sha256"] = _digest(
        {key: value for key, value in payload.items() if key != "manifest_sha256"},
        domain=_MANIFEST_DOMAIN,
    )
    return payload


def _atomic_write_new(path: Path, body: bytes) -> None:
    if path.exists():
        raise FileExistsError(f"refusing to overwrite output: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor: int | None = None
    temporary: Path | None = None
    try:
        for nonce in range(1024):
            candidate = path.with_name(f".{path.name}.tmp-{os.getpid()}-{nonce}")
            try:
                descriptor = os.open(candidate, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                continue
            temporary = candidate
            break
        if descriptor is None or temporary is None:
            raise ActorVisibleAwrError("could not reserve atomic output")
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = None
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def write_actor_visible_awr_sidecar_v1(
    rows: Sequence[ActorVisibleAwrRowV1], path: str | Path,
) -> dict[str, object]:
    ordered = tuple(sorted(rows, key=lambda row: row.record_id))
    if not ordered or any(type(row) is not ActorVisibleAwrRowV1 for row in ordered):
        raise ActorVisibleAwrError("sidecar writer needs canonical rows")
    if len({row.record_id for row in ordered}) != len(ordered):
        raise ActorVisibleAwrError("sidecar rows contain duplicate record IDs")
    body = b"".join(_canonical(row.to_dict()) + b"\n" for row in ordered)
    destination = Path(path).resolve()
    _atomic_write_new(destination, body)
    return {
        "path": str(destination),
        "sha256": _file_sha256(destination),
        "row_count": len(ordered),
        "format": "canonical-jsonl-v1",
    }


_ROW_KEYS = frozenset({
    "schema_version", "record_id", "record_content_hash", "episode_id", "split",
    "teacher_id", "action_type", "fold_index", "fit_membership", "value_estimation",
    "value_target", "baseline_value", "advantage", "awr_weight",
    "example_quality_weight", "effective_weight", "filtered_bc_eligible",
})


def _row_from_payload(payload: object) -> ActorVisibleAwrRowV1:
    if type(payload) is not dict or set(payload) != _ROW_KEYS:
        raise ActorVisibleAwrError("sidecar row has an open or invalid schema")
    return ActorVisibleAwrRowV1(
        record_id=payload["record_id"],  # type: ignore[arg-type]
        record_content_hash=payload["record_content_hash"],  # type: ignore[arg-type]
        episode_id=payload["episode_id"],  # type: ignore[arg-type]
        split=payload["split"],  # type: ignore[arg-type]
        teacher_id=payload["teacher_id"],  # type: ignore[arg-type]
        action_type=payload["action_type"],  # type: ignore[arg-type]
        fold_index=payload["fold_index"],  # type: ignore[arg-type]
        fit_membership=payload["fit_membership"],  # type: ignore[arg-type]
        value_estimation=payload["value_estimation"],  # type: ignore[arg-type]
        value_target=payload["value_target"],  # type: ignore[arg-type]
        baseline_value=payload["baseline_value"],  # type: ignore[arg-type]
        advantage=payload["advantage"],  # type: ignore[arg-type]
        awr_weight=payload["awr_weight"],  # type: ignore[arg-type]
        example_quality_weight=payload["example_quality_weight"],  # type: ignore[arg-type]
        effective_weight=payload["effective_weight"],  # type: ignore[arg-type]
        filtered_bc_eligible=payload["filtered_bc_eligible"],  # type: ignore[arg-type]
        schema_version=payload["schema_version"],  # type: ignore[arg-type]
    )


def read_actor_visible_awr_sidecar_v1(
    path: str | Path, *, expected_sha256: str,
) -> tuple[ActorVisibleAwrRowV1, ...]:
    expected = _sha(expected_sha256, field="expected sidecar SHA-256")
    source = Path(path).resolve()
    if not source.is_file() or _file_sha256(source) != expected:
        raise ActorVisibleAwrError("sidecar SHA-256 mismatch; source may be tampered")
    body = source.read_bytes()
    if not body or not body.endswith(b"\n"):
        raise ActorVisibleAwrError("sidecar must be nonempty newline-framed JSONL")
    rows: list[ActorVisibleAwrRowV1] = []
    for line_number, line in enumerate(body.splitlines(), 1):
        try:
            payload = json.loads(
                line.decode("utf-8"),
                parse_constant=lambda value: (_ for _ in ()).throw(
                    ActorVisibleAwrError(f"nonfinite JSON value: {value}")
                ),
            )
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ActorVisibleAwrError(f"sidecar line {line_number} is invalid JSON") from exc
        if _canonical(payload) != line:
            raise ActorVisibleAwrError("sidecar line is not canonical JSON")
        rows.append(_row_from_payload(payload))
    ids = tuple(row.record_id for row in rows)
    if ids != tuple(sorted(ids)) or len(ids) != len(set(ids)):
        raise ActorVisibleAwrError("sidecar rows are not sorted and unique")
    return tuple(rows)


__all__ = [
    "SCHEMA_V1",
    "SIDECAR_ROW_SCHEMA_V1",
    "ACTOR_VISIBLE_VALUE_FEATURE_SCHEMA_V1",
    "DEFAULT_FOLD_SEED_V1",
    "FIT_SPLIT_V1",
    "HELDOUT_SPLITS_V1",
    "ActorVisibleAwrError",
    "ActorVisibleAwrSampleV1",
    "ActorVisibleAwrRowV1",
    "ActorVisibleAwrBuildResultV1",
    "episode_fold_v1",
    "actor_visible_value_features_v1",
    "sample_from_training_snapshot_example_v1",
    "build_cross_fitted_actor_visible_awr_v1",
    "actor_visible_awr_diagnostics_v1",
    "build_derived_teacher_awr_manifest_payload_v1",
    "write_actor_visible_awr_sidecar_v1",
    "read_actor_visible_awr_sidecar_v1",
]
