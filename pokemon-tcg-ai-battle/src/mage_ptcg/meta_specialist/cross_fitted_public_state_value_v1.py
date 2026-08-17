"""Research-only cross-fitted public-state value targets.

The earlier signed residual target uses an external global episode-return mean.
This module adds a deliberately small, actor-visible state baseline: the
first-prefix public structural bucket of each physical record.  For every
episode, the bucket mean is fit only on episodes outside that episode's fold;
when no external episode contains the bucket, the external global transition
mean is used and the fallback is recorded explicitly.  The result is a target
manifest, not a runtime feature or a performance authorization.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from collections.abc import Mapping, Sequence

import torch

from mage_ptcg.meta_specialist.cross_fitted_outcome_residual_v1 import OutcomeEpisodeV1
from mage_ptcg.meta_specialist.public_confidence_ood_v1 import _bucket_id
from mage_ptcg.meta_specialist.trajectory_v1 import (
    ActorTrajectoryTransitionV1,
    canonical_actor_trajectory_transition_bytes_v1,
)


SCHEMA_V1 = "specialist-cross-fitted-public-state-value-v1"
PUBLIC_STATE_VALUE_OBJECTIVE_V1 = "cross_fitted_public_bucket_value_advantage"
PUBLIC_STATE_MODEL_VALUE_OBJECTIVE_V1 = "cross_fitted_public_state_model_value_advantage"
PUBLIC_STATE_VALUE_TARGET_KIND_V1 = "signed_public_state_value_residual"
PUBLIC_STATE_MODEL_FEATURE_SCHEMA_V1 = "public-state-value-features-41-scalars-step-structure-v1"
_DOMAIN = b"mage-ptcg:cross-fitted-public-state-value:v1\0"
_HEX64 = frozenset("0123456789abcdef")


class PublicStateValueError(ValueError):
    """Raised when a public-state value target cannot be sealed."""


def _sha(value: object, *, field: str) -> str:
    if type(value) is not str or len(value) != 64 or any(char not in _HEX64 for char in value):
        raise PublicStateValueError(f"{field} must be a lowercase SHA-256")
    return value


def _finite(value: object, *, field: str) -> float:
    if type(value) not in (int, float) or type(value) is bool or not math.isfinite(float(value)):
        raise PublicStateValueError(f"{field} must be finite")
    return float(value)


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise PublicStateValueError("value manifest is not canonical JSON") from exc


def _closed(value: object, fields: set[str], *, field: str) -> dict[str, object]:
    if type(value) is not dict or set(value) != fields:
        raise PublicStateValueError(f"{field} has an open or invalid schema")
    return value


def _fold(episode_id: str, fold_count: int) -> int:
    return int(episode_id[:16], 16) % fold_count


def _public_bucket(transition: ActorTrajectoryTransitionV1) -> str:
    if type(transition) is not ActorTrajectoryTransitionV1 or not transition.prefix_steps:
        raise PublicStateValueError("transition must have an actor-visible prefix")
    step = transition.prefix_steps[0].step_input
    effective_domain = len(step.allowed_semantic_classes) + int(step.stop_available)
    if effective_domain < 1:
        raise PublicStateValueError("public state has no legal effective domain")
    try:
        bucket = _bucket_id(transition.model_input, step, effective_domain)
    except (AttributeError, IndexError, TypeError, ValueError) as exc:
        raise PublicStateValueError("public state bucket could not be derived") from exc
    _sha(bucket, field="public_bucket_id")
    return bucket


def _public_state_features(transition: ActorTrajectoryTransitionV1) -> tuple[float, ...]:
    """Return a fixed actor-visible feature vector for the value baseline.

    This is deliberately a small, non-neural value model.  It uses only the
    serialized public state and the first legal semantic domain summary of a
    physical record; opponent/seat/policy/private payloads are not accepted.
    Fixed divisors keep the ridge solve deterministic without fitting any
    normalization statistics on the held-out fold.
    """

    if type(transition) is not ActorTrajectoryTransitionV1 or not transition.prefix_steps:
        raise PublicStateValueError("transition must contain an actor-visible prefix")
    model_input = transition.model_input
    step_input = transition.prefix_steps[0].step_input
    state_scales = (
        2.0, 256.0, 128.0, 128.0, 10.0, 48.0, 64.0, 64.0, 64.0,
        256.0, 256.0, 1.0, 1.0, 1.0, 1.0,
        64.0, 64.0, 64.0, 64.0, 64.0, 64.0, 64.0, 64.0,
        1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0,
            1.0, 1.0, 64.0, 1.0, 1.0, 1.0, 64.0, 64.0,
    )
    if len(model_input.state_scalars) != len(state_scales):
        raise PublicStateValueError("state scalar feature shape is not closed")
    features = [
        min(1.0, float(value) / scale) for value, scale in zip(
            model_input.state_scalars, state_scales, strict=True
        )
    ]
    effective_domain = len(step_input.allowed_semantic_classes) + int(step_input.stop_available)
    if effective_domain < 1:
        raise PublicStateValueError("public state has no legal effective domain")
    option_types = {item.semantic_row.option_type for item in step_input.allowed_semantic_classes}
    features.extend((
        min(1.0, effective_domain / 64.0),
        float(step_input.stop_available),
        min(1.0, len(step_input.semantic_prefix) / 64.0),
        min(1.0, len(model_input.pokemon_entities) / 122.0),
        min(1.0, sum(sum(int(value) for value in bag.mask) for bag in model_input.card_bags.values()) / 256.0),
    ))
    features.extend(float(option_type in option_types) for option_type in range(7, 16))
    features.append(1.0)  # fixed bias term
    if len(features) != 56 or any(not math.isfinite(value) for value in features):
        raise PublicStateValueError("public state feature vector is invalid")
    return tuple(features)


def _fit_ridge_value_model(
    transitions: Sequence[tuple[ActorTrajectoryTransitionV1, float]],
    *,
    ridge_lambda: float,
) -> tuple[float, ...]:
    if not transitions:
        raise PublicStateValueError("value model fold has no external transitions")
    if type(ridge_lambda) not in (int, float) or type(ridge_lambda) is bool or not math.isfinite(float(ridge_lambda)) or float(ridge_lambda) <= 0.0:
        raise PublicStateValueError("ridge_lambda must be finite and positive")
    x = torch.tensor([_public_state_features(transition) for transition, _ in transitions], dtype=torch.float64)
    y = torch.tensor([float(value) for _, value in transitions], dtype=torch.float64)
    identity = torch.eye(x.shape[1], dtype=torch.float64)
    coefficients = torch.linalg.solve(x.T @ x + float(ridge_lambda) * identity, x.T @ y)
    values = tuple(float(value) for value in coefficients.tolist())
    if len(values) != 56 or any(not math.isfinite(value) for value in values):
        raise PublicStateValueError("value model coefficients are invalid")
    return values


@dataclass(frozen=True, slots=True)
class PublicStateValueTargetV1:
    transition_index: int
    transition_sha256: str
    public_bucket_id: str
    return_value: float
    baseline_value: float
    advantage: float
    signed_weight: float
    baseline_source: str
    target_kind: str = PUBLIC_STATE_VALUE_TARGET_KIND_V1

    def __post_init__(self) -> None:
        if type(self.transition_index) is not int or self.transition_index < 0:
            raise PublicStateValueError("transition_index is invalid")
        _sha(self.transition_sha256, field="transition_sha256")
        _sha(self.public_bucket_id, field="public_bucket_id")
        for field in ("return_value", "baseline_value", "advantage", "signed_weight"):
            _finite(getattr(self, field), field=field)
        if not -1.0 <= self.signed_weight <= 1.0:
            raise PublicStateValueError("signed_weight must be in [-1, 1]")
        if self.baseline_source not in {"public_bucket", "public_state_model", "external_global_fallback"}:
            raise PublicStateValueError("baseline_source is invalid")
        if self.target_kind != PUBLIC_STATE_VALUE_TARGET_KIND_V1:
            raise PublicStateValueError("public value target cannot be reclassified")

    def to_dict(self) -> dict[str, object]:
        return {
            "transition_index": self.transition_index,
            "transition_sha256": self.transition_sha256,
            "public_bucket_id": self.public_bucket_id,
            "return_value": self.return_value,
            "baseline_value": self.baseline_value,
            "advantage": self.advantage,
            "signed_weight": self.signed_weight,
            "baseline_source": self.baseline_source,
            "target_kind": self.target_kind,
        }


@dataclass(frozen=True, slots=True)
class PublicStateValueEpisodeV1:
    episode_id: str
    fold_index: int
    return_value: float
    targets: tuple[PublicStateValueTargetV1, ...]

    def __post_init__(self) -> None:
        _sha(self.episode_id, field="episode_id")
        if type(self.fold_index) is not int or self.fold_index < 0:
            raise PublicStateValueError("fold_index is invalid")
        _finite(self.return_value, field="episode return_value")
        if type(self.targets) is not tuple or not self.targets:
            raise PublicStateValueError("episode targets must be nonempty")
        if any(type(target) is not PublicStateValueTargetV1 for target in self.targets):
            raise PublicStateValueError("episode has a non-canonical target")
        if tuple(target.transition_index for target in self.targets) != tuple(range(len(self.targets))):
            raise PublicStateValueError("episode target order is not contiguous")

    def to_dict(self) -> dict[str, object]:
        return {
            "episode_id": self.episode_id,
            "fold_index": self.fold_index,
            "return_value": self.return_value,
            "targets": [target.to_dict() for target in self.targets],
        }


@dataclass(frozen=True, slots=True)
class CrossFittedPublicStateValueManifestV1:
    schema_version: str
    objective_kind: str
    fold_count: int
    advantage_clip: float
    source_episode_sha256: str
    fallback_target_count: int
    episodes: tuple[PublicStateValueEpisodeV1, ...]
    value_feature_schema: str | None = None
    value_model_sha256: str | None = None
    ridge_lambda: float | None = None
    training_permitted: bool = False
    promotion_authority: bool = False
    longrun_allowed: bool = False

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_V1 or self.objective_kind not in {PUBLIC_STATE_VALUE_OBJECTIVE_V1, PUBLIC_STATE_MODEL_VALUE_OBJECTIVE_V1}:
            raise PublicStateValueError("manifest schema/objective is invalid")
        if type(self.fold_count) is not int or self.fold_count < 2:
            raise PublicStateValueError("fold_count must be at least two")
        if _finite(self.advantage_clip, field="advantage_clip") <= 0.0:
            raise PublicStateValueError("advantage_clip must be positive")
        _sha(self.source_episode_sha256, field="source_episode_sha256")
        if type(self.fallback_target_count) is not int or self.fallback_target_count < 0:
            raise PublicStateValueError("fallback_target_count is invalid")
        if type(self.episodes) is not tuple or len(self.episodes) <= self.fold_count:
            raise PublicStateValueError("manifest needs more episodes than folds")
        if any(type(episode) is not PublicStateValueEpisodeV1 for episode in self.episodes):
            raise PublicStateValueError("manifest episode is not canonical")
        ids = tuple(episode.episode_id for episode in self.episodes)
        if ids != tuple(sorted(ids)) or len(set(ids)) != len(ids):
            raise PublicStateValueError("manifest episodes must be sorted and distinct")
        if any(episode.fold_index >= self.fold_count for episode in self.episodes):
            raise PublicStateValueError("manifest fold index is invalid")
        if self.objective_kind == PUBLIC_STATE_MODEL_VALUE_OBJECTIVE_V1:
            if self.value_feature_schema != PUBLIC_STATE_MODEL_FEATURE_SCHEMA_V1:
                raise PublicStateValueError("state-model feature schema is invalid")
            _sha(self.value_model_sha256, field="value_model_sha256")
            if self.ridge_lambda is None or _finite(self.ridge_lambda, field="ridge_lambda") <= 0.0:
                raise PublicStateValueError("state-model ridge_lambda is invalid")
        elif any(value is not None for value in (self.value_feature_schema, self.value_model_sha256, self.ridge_lambda)):
            raise PublicStateValueError("bucket manifest cannot carry state-model metadata")
        if any(getattr(self, field) is not False for field in ("training_permitted", "promotion_authority", "longrun_allowed")):
            raise PublicStateValueError("manifest grants forbidden authority")
        actual_fallbacks = sum(
            target.baseline_source == "external_global_fallback"
            for episode in self.episodes for target in episode.targets
        )
        if actual_fallbacks != self.fallback_target_count:
            raise PublicStateValueError("fallback_target_count does not match target rows")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "objective_kind": self.objective_kind,
            "fold_count": self.fold_count,
            "advantage_clip": self.advantage_clip,
            "source_episode_sha256": self.source_episode_sha256,
            "fallback_target_count": self.fallback_target_count,
            **({
                "value_feature_schema": self.value_feature_schema,
                "value_model_sha256": self.value_model_sha256,
                "ridge_lambda": self.ridge_lambda,
            } if self.objective_kind == PUBLIC_STATE_MODEL_VALUE_OBJECTIVE_V1 else {}),
            "training_permitted": False,
            "promotion_authority": False,
            "longrun_allowed": False,
            "episodes": [episode.to_dict() for episode in self.episodes],
        }


def _episode_source_sha(episodes: Sequence[OutcomeEpisodeV1]) -> str:
    source = []
    for episode in sorted(episodes, key=lambda item: item.episode_id):
        source.append({
            "episode_id": episode.episode_id,
            "transition_sha256": [
                hashlib.sha256(canonical_actor_trajectory_transition_bytes_v1(transition)).hexdigest()
                for transition in episode.transitions
            ],
        })
    return hashlib.sha256(_DOMAIN + _canonical(source)).hexdigest()


def build_cross_fitted_public_state_value_manifest_v1(
    episodes: Sequence[OutcomeEpisodeV1],
    *,
    fold_count: int = 2,
    advantage_clip: float = 1.0,
) -> CrossFittedPublicStateValueManifestV1:
    """Build a bucket-conditioned, leave-fold-out public value manifest."""

    if type(episodes) not in (tuple, list) or any(type(item) is not OutcomeEpisodeV1 for item in episodes):
        raise PublicStateValueError("episodes must contain exact OutcomeEpisodeV1 values")
    if type(fold_count) is not int or fold_count < 2 or len(episodes) <= fold_count:
        raise PublicStateValueError("fold_count requires more episodes than folds")
    clip = _finite(advantage_clip, field="advantage_clip")
    if clip <= 0.0:
        raise PublicStateValueError("advantage_clip must be positive")
    ordered = tuple(sorted(episodes, key=lambda item: item.episode_id))
    if len({episode.episode_id for episode in ordered}) != len(ordered):
        raise PublicStateValueError("episodes must be distinct")
    folds = {episode.episode_id: _fold(episode.episode_id, fold_count) for episode in ordered}
    episode_returns: dict[str, tuple[float, ...]] = {}
    for episode in ordered:
        returns = episode.returns
        episode_returns[episode.episode_id] = returns
    manifest_episodes: list[PublicStateValueEpisodeV1] = []
    fallback_count = 0
    for episode in ordered:
        fold = folds[episode.episode_id]
        external_episodes = tuple(other for other in ordered if folds[other.episode_id] != fold)
        external_global = [
            return_value
            for other in external_episodes
            for return_value in episode_returns[other.episode_id]
        ]
        if not external_global:
            raise PublicStateValueError("fold has no external transitions")
        external_by_bucket: dict[str, list[float]] = {}
        for other in external_episodes:
            for transition, return_value in zip(other.transitions, episode_returns[other.episode_id], strict=True):
                external_by_bucket.setdefault(_public_bucket(transition), []).append(return_value)
        global_baseline = math.fsum(external_global) / len(external_global)
        targets: list[PublicStateValueTargetV1] = []
        for index, (transition, return_value) in enumerate(zip(episode.transitions, episode_returns[episode.episode_id], strict=True)):
            bucket = _public_bucket(transition)
            values = external_by_bucket.get(bucket)
            if values:
                baseline = math.fsum(values) / len(values)
                source = "public_bucket"
            else:
                baseline = global_baseline
                source = "external_global_fallback"
                fallback_count += 1
            advantage = float(return_value) - baseline
            signed_weight = max(-1.0, min(1.0, advantage / clip))
            targets.append(PublicStateValueTargetV1(
                transition_index=index,
                transition_sha256=hashlib.sha256(canonical_actor_trajectory_transition_bytes_v1(transition)).hexdigest(),
                public_bucket_id=bucket,
                return_value=return_value,
                baseline_value=baseline,
                advantage=advantage,
                signed_weight=signed_weight,
                baseline_source=source,
            ))
        manifest_episodes.append(PublicStateValueEpisodeV1(
            episode_id=episode.episode_id,
            fold_index=fold,
            return_value=episode_returns[episode.episode_id][0],
            targets=tuple(targets),
        ))
    return CrossFittedPublicStateValueManifestV1(
        schema_version=SCHEMA_V1,
        objective_kind=PUBLIC_STATE_VALUE_OBJECTIVE_V1,
        fold_count=fold_count,
        advantage_clip=clip,
        source_episode_sha256=_episode_source_sha(ordered),
        fallback_target_count=fallback_count,
        episodes=tuple(manifest_episodes),
    )


def build_cross_fitted_public_state_model_value_manifest_v1(
    episodes: Sequence[OutcomeEpisodeV1],
    *,
    fold_count: int = 2,
    advantage_clip: float = 1.0,
    ridge_lambda: float = 1.0,
) -> CrossFittedPublicStateValueManifestV1:
    """Build a leave-fold-out value baseline from actor-visible state features.

    The model is a fixed 56-feature ridge regressor with no runtime identity
    or opponent metadata.  Each held-out episode gets coefficients fit only
    on transitions from other folds.  Coefficients are used to materialize
    the target manifest, so the future residual trainer never receives the
    held-out outcome while estimating its baseline.
    """

    if type(episodes) not in (tuple, list) or any(type(item) is not OutcomeEpisodeV1 for item in episodes):
        raise PublicStateValueError("episodes must contain exact OutcomeEpisodeV1 values")
    ordered = tuple(sorted(episodes, key=lambda item: item.episode_id))
    if len({episode.episode_id for episode in ordered}) != len(ordered):
        raise PublicStateValueError("episodes must be distinct")
    if type(fold_count) is not int or fold_count < 2 or len(ordered) <= fold_count:
        raise PublicStateValueError("fold_count requires more episodes than folds")
    clip = _finite(advantage_clip, field="advantage_clip")
    if clip <= 0.0:
        raise PublicStateValueError("advantage_clip must be positive")
    if type(ridge_lambda) not in (int, float) or type(ridge_lambda) is bool or not math.isfinite(float(ridge_lambda)) or float(ridge_lambda) <= 0.0:
        raise PublicStateValueError("ridge_lambda must be finite and positive")
    folds = {episode.episode_id: _fold(episode.episode_id, fold_count) for episode in ordered}
    returns = {episode.episode_id: episode.returns for episode in ordered}
    fold_models: dict[int, tuple[float, ...]] = {}
    model_payload: list[dict[str, object]] = []
    for fold in range(fold_count):
        external = [
            (transition, return_value)
            for episode in ordered if folds[episode.episode_id] != fold
            for transition, return_value in zip(episode.transitions, returns[episode.episode_id], strict=True)
        ]
        coefficients = _fit_ridge_value_model(external, ridge_lambda=float(ridge_lambda))
        fold_models[fold] = coefficients
        model_payload.append({"fold": fold, "coefficients": list(coefficients)})
    value_model_sha = hashlib.sha256(_DOMAIN + _canonical({
        "feature_schema": PUBLIC_STATE_MODEL_FEATURE_SCHEMA_V1,
        "ridge_lambda": float(ridge_lambda),
        "fold_models": model_payload,
    })).hexdigest()
    manifest_episodes: list[PublicStateValueEpisodeV1] = []
    fallback_count = 0
    for episode in ordered:
        fold = folds[episode.episode_id]
        coefficients = fold_models[fold]
        targets: list[PublicStateValueTargetV1] = []
        for index, (transition, return_value) in enumerate(zip(
            episode.transitions, returns[episode.episode_id], strict=True,
        )):
            features = _public_state_features(transition)
            baseline = math.fsum(value * coefficient for value, coefficient in zip(features, coefficients, strict=True))
            if not math.isfinite(baseline):
                raise PublicStateValueError("state-model baseline is nonfinite")
            bucket = _public_bucket(transition)
            advantage = float(return_value) - baseline
            signed_weight = max(-1.0, min(1.0, advantage / clip))
            targets.append(PublicStateValueTargetV1(
                transition_index=index,
                transition_sha256=hashlib.sha256(canonical_actor_trajectory_transition_bytes_v1(transition)).hexdigest(),
                public_bucket_id=bucket,
                return_value=return_value,
                baseline_value=baseline,
                advantage=advantage,
                signed_weight=signed_weight,
                baseline_source="public_state_model",
            ))
        manifest_episodes.append(PublicStateValueEpisodeV1(
            episode_id=episode.episode_id,
            fold_index=fold,
            return_value=returns[episode.episode_id][0],
            targets=tuple(targets),
        ))
    return CrossFittedPublicStateValueManifestV1(
        schema_version=SCHEMA_V1,
        objective_kind=PUBLIC_STATE_MODEL_VALUE_OBJECTIVE_V1,
        fold_count=fold_count,
        advantage_clip=clip,
        source_episode_sha256=_episode_source_sha(ordered),
        fallback_target_count=fallback_count,
        episodes=tuple(manifest_episodes),
        value_feature_schema=PUBLIC_STATE_MODEL_FEATURE_SCHEMA_V1,
        value_model_sha256=value_model_sha,
        ridge_lambda=float(ridge_lambda),
    )


def load_cross_fitted_public_state_value_manifest_v1(
    value: Mapping[str, object],
) -> CrossFittedPublicStateValueManifestV1:
    """Load only the exact value-target manifest shape."""

    raw_value = dict(value)
    allowed_fields = {
        "schema_version", "objective_kind", "fold_count", "advantage_clip",
        "source_episode_sha256", "fallback_target_count", "training_permitted",
        "promotion_authority", "longrun_allowed", "episodes",
    }
    if raw_value.get("objective_kind") == PUBLIC_STATE_MODEL_VALUE_OBJECTIVE_V1:
        allowed_fields |= {"value_feature_schema", "value_model_sha256", "ridge_lambda"}
    root = _closed(raw_value, allowed_fields, field="public value manifest")
    raw_episodes = root["episodes"]
    if type(raw_episodes) is not list:
        raise PublicStateValueError("manifest episodes must be a list")
    episodes: list[PublicStateValueEpisodeV1] = []
    for raw_episode in raw_episodes:
        episode = _closed(raw_episode, {"episode_id", "fold_index", "return_value", "targets"}, field="public value episode")
        raw_targets = episode["targets"]
        if type(raw_targets) is not list:
            raise PublicStateValueError("manifest targets must be a list")
        targets: list[PublicStateValueTargetV1] = []
        for raw_target in raw_targets:
            target = _closed(raw_target, {
                "transition_index", "transition_sha256", "public_bucket_id", "return_value",
                "baseline_value", "advantage", "signed_weight", "baseline_source", "target_kind",
            }, field="public value target")
            targets.append(PublicStateValueTargetV1(
                transition_index=target["transition_index"],  # type: ignore[arg-type]
                transition_sha256=target["transition_sha256"],  # type: ignore[arg-type]
                public_bucket_id=target["public_bucket_id"],  # type: ignore[arg-type]
                return_value=target["return_value"],  # type: ignore[arg-type]
                baseline_value=target["baseline_value"],  # type: ignore[arg-type]
                advantage=target["advantage"],  # type: ignore[arg-type]
                signed_weight=target["signed_weight"],  # type: ignore[arg-type]
                baseline_source=target["baseline_source"],  # type: ignore[arg-type]
                target_kind=target["target_kind"],  # type: ignore[arg-type]
            ))
        episodes.append(PublicStateValueEpisodeV1(
            episode_id=episode["episode_id"],  # type: ignore[arg-type]
            fold_index=episode["fold_index"],  # type: ignore[arg-type]
            return_value=episode["return_value"],  # type: ignore[arg-type]
            targets=tuple(targets),
        ))
    return CrossFittedPublicStateValueManifestV1(
        schema_version=root["schema_version"],  # type: ignore[arg-type]
        objective_kind=root["objective_kind"],  # type: ignore[arg-type]
        fold_count=root["fold_count"],  # type: ignore[arg-type]
        advantage_clip=root["advantage_clip"],  # type: ignore[arg-type]
        source_episode_sha256=root["source_episode_sha256"],  # type: ignore[arg-type]
        fallback_target_count=root["fallback_target_count"],  # type: ignore[arg-type]
        episodes=tuple(episodes),
        value_feature_schema=root.get("value_feature_schema"),  # type: ignore[arg-type]
        value_model_sha256=root.get("value_model_sha256"),  # type: ignore[arg-type]
        ridge_lambda=root.get("ridge_lambda"),  # type: ignore[arg-type]
        training_permitted=root["training_permitted"],  # type: ignore[arg-type]
        promotion_authority=root["promotion_authority"],  # type: ignore[arg-type]
        longrun_allowed=root["longrun_allowed"],  # type: ignore[arg-type]
    )


__all__ = [
    "SCHEMA_V1",
    "PUBLIC_STATE_VALUE_OBJECTIVE_V1",
    "PUBLIC_STATE_MODEL_VALUE_OBJECTIVE_V1",
    "PUBLIC_STATE_MODEL_FEATURE_SCHEMA_V1",
    "PUBLIC_STATE_VALUE_TARGET_KIND_V1",
    "PublicStateValueError",
    "PublicStateValueTargetV1",
    "PublicStateValueEpisodeV1",
    "CrossFittedPublicStateValueManifestV1",
    "build_cross_fitted_public_state_value_manifest_v1",
    "build_cross_fitted_public_state_model_value_manifest_v1",
    "load_cross_fitted_public_state_value_manifest_v1",
]
