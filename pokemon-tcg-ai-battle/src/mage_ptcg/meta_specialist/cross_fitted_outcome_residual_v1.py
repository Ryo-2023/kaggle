"""Research-only cross-fitted Monte-Carlo targets for frozen residuals.

The module intentionally turns complete, sealed actor trajectories into a
*training target manifest*, not a runtime feature.  Opponent identity, seat,
policy version, and actor metadata are neither accepted as fields here nor
emitted by the manifest.  A later residual learner may consume the signed
behavior targets, but must not reinterpret them as teacher distributions.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from collections.abc import Mapping
from typing import Sequence

from mage_ptcg.meta_specialist.trajectory_v1 import (
    ActorTrajectoryTransitionV1,
    canonical_actor_trajectory_transition_bytes_v1,
)


SCHEMA_V1 = "specialist-cross-fitted-mc-residual-target-v1"
OBJECTIVE_KIND_V1 = "cross_fitted_mc_signed_behavior_residual"
TARGET_KIND_V1 = "signed_behavior_log_probability"
_DOMAIN = b"mage_ptcg:cross-fitted-mc-residual-target:v1\0"
_HEX64 = frozenset("0123456789abcdef")


class CrossFittedOutcomeResidualError(ValueError):
    """Raised when a sealed outcome target cannot be constructed honestly."""


def _sha(value: object, *, field: str) -> str:
    if type(value) is not str or len(value) != 64 or any(char not in _HEX64 for char in value):
        raise CrossFittedOutcomeResidualError(f"{field} must be a lowercase SHA-256")
    return value


def _finite(value: object, *, field: str) -> float:
    if type(value) not in (int, float) or type(value) is bool or not math.isfinite(float(value)):
        raise CrossFittedOutcomeResidualError(f"{field} must be finite")
    return float(value)


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise CrossFittedOutcomeResidualError("outcome target payload is not canonical JSON") from exc


def _closed_mapping(value: object, expected: set[str], *, field: str) -> dict[str, object]:
    if type(value) is not dict or set(value) != expected:
        raise CrossFittedOutcomeResidualError(f"{field} has an open or invalid schema")
    return value


def _target_indices(transition: ActorTrajectoryTransitionV1) -> tuple[int, ...]:
    indices: list[int] = []
    for prefix in transition.prefix_steps:
        if prefix.chosen_is_stop:
            if not prefix.step_input.stop_available:
                raise CrossFittedOutcomeResidualError("chosen STOP is outside the sealed domain")
            indices.append(len(prefix.step_input.allowed_semantic_classes))
            continue
        chosen = prefix.chosen_semantic_action
        matches = [
            index for index, candidate in enumerate(prefix.step_input.allowed_semantic_classes)
            if candidate.semantic_row == chosen
        ]
        if len(matches) != 1:
            raise CrossFittedOutcomeResidualError("chosen semantic action is not uniquely aligned to its sealed domain")
        indices.append(matches[0])
    if not indices:
        raise CrossFittedOutcomeResidualError("transition has no prefix targets")
    return tuple(indices)


@dataclass(frozen=True, slots=True)
class OutcomeEpisodeV1:
    """One ordered terminal actor episode, identified only by an opaque digest."""

    episode_id: str
    transitions: tuple[ActorTrajectoryTransitionV1, ...]

    def __post_init__(self) -> None:
        _sha(self.episode_id, field="episode_id")
        if type(self.transitions) is not tuple or not self.transitions:
            raise CrossFittedOutcomeResidualError("episode transitions must be a nonempty tuple")
        if any(type(item) is not ActorTrajectoryTransitionV1 for item in self.transitions):
            raise CrossFittedOutcomeResidualError("episode contains a non-canonical transition")
        if any(item.terminal for item in self.transitions[:-1]) or self.transitions[-1].terminal is not True:
            raise CrossFittedOutcomeResidualError("episode terminal topology is invalid")

    @property
    def source_transition_sha256(self) -> tuple[str, ...]:
        return tuple(
            hashlib.sha256(canonical_actor_trajectory_transition_bytes_v1(item)).hexdigest()
            for item in self.transitions
        )

    @property
    def returns(self) -> tuple[float, ...]:
        next_return = 0.0
        reversed_returns: list[float] = []
        for transition in reversed(self.transitions):
            next_return = float(transition.reward) + float(transition.discount) * next_return
            if not math.isfinite(next_return):
                raise CrossFittedOutcomeResidualError("episode Monte-Carlo return is nonfinite")
            reversed_returns.append(next_return)
        return tuple(reversed(reversed_returns))


@dataclass(frozen=True, slots=True)
class SignedBehaviorResidualTargetV1:
    transition_index: int
    transition_sha256: str
    target_indices: tuple[int, ...]
    return_value: float
    baseline_value: float
    advantage: float
    signed_weight: float
    target_kind: str = TARGET_KIND_V1

    def __post_init__(self) -> None:
        if type(self.transition_index) is not int or self.transition_index < 0:
            raise CrossFittedOutcomeResidualError("target transition_index is invalid")
        _sha(self.transition_sha256, field="target transition_sha256")
        if type(self.target_indices) is not tuple or not self.target_indices or any(type(value) is not int or value < 0 for value in self.target_indices):
            raise CrossFittedOutcomeResidualError("target indices are invalid")
        for field in ("return_value", "baseline_value", "advantage", "signed_weight"):
            _finite(getattr(self, field), field=field)
        if not -1.0 <= self.signed_weight <= 1.0:
            raise CrossFittedOutcomeResidualError("signed_weight must be in [-1, 1]")
        if self.target_kind != TARGET_KIND_V1:
            raise CrossFittedOutcomeResidualError("residual target cannot be reclassified as a teacher label")

    def to_dict(self) -> dict[str, object]:
        return {
            "transition_index": self.transition_index,
            "transition_sha256": self.transition_sha256,
            "target_indices": list(self.target_indices),
            "return_value": self.return_value,
            "baseline_value": self.baseline_value,
            "advantage": self.advantage,
            "signed_weight": self.signed_weight,
            "target_kind": self.target_kind,
        }


@dataclass(frozen=True, slots=True)
class OutcomeEpisodeTargetsV1:
    episode_id: str
    fold_index: int
    return_value: float
    targets: tuple[SignedBehaviorResidualTargetV1, ...]

    def __post_init__(self) -> None:
        _sha(self.episode_id, field="target episode_id")
        if type(self.fold_index) is not int or self.fold_index < 0:
            raise CrossFittedOutcomeResidualError("target fold index is invalid")
        _finite(self.return_value, field="episode return_value")
        if type(self.targets) is not tuple or not self.targets:
            raise CrossFittedOutcomeResidualError("episode target rows are invalid")
        if tuple(item.transition_index for item in self.targets) != tuple(range(len(self.targets))):
            raise CrossFittedOutcomeResidualError("episode target transition order is invalid")

    def to_dict(self) -> dict[str, object]:
        return {
            "episode_id": self.episode_id,
            "fold_index": self.fold_index,
            "return_value": self.return_value,
            "targets": [item.to_dict() for item in self.targets],
        }


@dataclass(frozen=True, slots=True)
class CrossFittedOutcomeManifestV1:
    schema_version: str
    objective_kind: str
    fold_count: int
    advantage_clip: float
    source_episode_sha256: str
    episodes: tuple[OutcomeEpisodeTargetsV1, ...]
    training_permitted: bool = False
    promotion_authority: bool = False
    longrun_allowed: bool = False

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_V1 or self.objective_kind != OBJECTIVE_KIND_V1:
            raise CrossFittedOutcomeResidualError("outcome target manifest schema/objective is invalid")
        if type(self.fold_count) is not int or self.fold_count < 2:
            raise CrossFittedOutcomeResidualError("fold_count must be at least two")
        if _finite(self.advantage_clip, field="advantage_clip") <= 0.0:
            raise CrossFittedOutcomeResidualError("advantage_clip must be positive")
        _sha(self.source_episode_sha256, field="source_episode_sha256")
        if type(self.episodes) is not tuple or len(self.episodes) <= self.fold_count:
            raise CrossFittedOutcomeResidualError("manifest needs more episodes than folds")
        if any(type(item) is not OutcomeEpisodeTargetsV1 for item in self.episodes):
            raise CrossFittedOutcomeResidualError("manifest episodes are invalid")
        ids = tuple(item.episode_id for item in self.episodes)
        if tuple(sorted(ids)) != ids or len(set(ids)) != len(ids):
            raise CrossFittedOutcomeResidualError("manifest episodes must be sorted and distinct")
        if any(item.fold_index >= self.fold_count for item in self.episodes):
            raise CrossFittedOutcomeResidualError("manifest episode fold is invalid")
        if any(getattr(self, field) is not False for field in ("training_permitted", "promotion_authority", "longrun_allowed")):
            raise CrossFittedOutcomeResidualError("outcome manifest grants authority")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "objective_kind": self.objective_kind,
            "fold_count": self.fold_count,
            "advantage_clip": self.advantage_clip,
            "source_episode_sha256": self.source_episode_sha256,
            "training_permitted": self.training_permitted,
            "promotion_authority": self.promotion_authority,
            "longrun_allowed": self.longrun_allowed,
            "episodes": [item.to_dict() for item in self.episodes],
        }


def _fold(episode_id: str, fold_count: int) -> int:
    return int(episode_id[:16], 16) % fold_count


def build_cross_fitted_outcome_manifest_v1(
    episodes: Sequence[OutcomeEpisodeV1],
    *,
    fold_count: int = 2,
    advantage_clip: float = 1.0,
) -> CrossFittedOutcomeManifestV1:
    """Return a closed signed-behavior target manifest with leave-fold-out means.

    The baseline is deliberately a fold-external global return mean.  It is
    not a runtime feature and therefore cannot carry opponent/seat leakage.
    """
    if type(episodes) not in (tuple, list) or any(type(item) is not OutcomeEpisodeV1 for item in episodes):
        raise CrossFittedOutcomeResidualError("episodes must be typed outcome episodes")
    ordered = tuple(sorted(episodes, key=lambda item: item.episode_id))
    if len({item.episode_id for item in ordered}) != len(ordered):
        raise CrossFittedOutcomeResidualError("episodes must be distinct")
    if type(fold_count) is not int or fold_count < 2 or len(ordered) <= fold_count:
        raise CrossFittedOutcomeResidualError("fold count requires more episodes than folds")
    clip = _finite(advantage_clip, field="advantage_clip")
    if clip <= 0.0:
        raise CrossFittedOutcomeResidualError("advantage_clip must be positive")
    episode_returns = {item.episode_id: item.returns[0] for item in ordered}
    folds = {item.episode_id: _fold(item.episode_id, fold_count) for item in ordered}
    rows: list[OutcomeEpisodeTargetsV1] = []
    source_payload = []
    for episode in ordered:
        fold = folds[episode.episode_id]
        external = [
            episode_returns[other.episode_id] for other in ordered
            if folds[other.episode_id] != fold
        ]
        if not external:
            raise CrossFittedOutcomeResidualError("cross-fit fold has no external baseline episodes")
        baseline = math.fsum(external) / len(external)
        targets: list[SignedBehaviorResidualTargetV1] = []
        for index, (transition, return_value, source_sha) in enumerate(zip(
            episode.transitions, episode.returns, episode.source_transition_sha256, strict=True,
        )):
            advantage = return_value - baseline
            signed_weight = max(-1.0, min(1.0, advantage / clip))
            targets.append(SignedBehaviorResidualTargetV1(
                transition_index=index,
                transition_sha256=source_sha,
                target_indices=_target_indices(transition),
                return_value=return_value,
                baseline_value=baseline,
                advantage=advantage,
                signed_weight=signed_weight,
            ))
        rows.append(OutcomeEpisodeTargetsV1(
            episode_id=episode.episode_id,
            fold_index=fold,
            return_value=episode_returns[episode.episode_id],
            targets=tuple(targets),
        ))
        source_payload.append({"episode_id": episode.episode_id, "transition_sha256": list(episode.source_transition_sha256)})
    source_sha = hashlib.sha256(_DOMAIN + _canonical(source_payload)).hexdigest()
    return CrossFittedOutcomeManifestV1(
        schema_version=SCHEMA_V1,
        objective_kind=OBJECTIVE_KIND_V1,
        fold_count=fold_count,
        advantage_clip=clip,
        source_episode_sha256=source_sha,
        episodes=tuple(rows),
    )


def load_cross_fitted_outcome_manifest_v1(
    value: Mapping[str, object],
) -> CrossFittedOutcomeManifestV1:
    """Load only the closed research manifest shape; reject metadata injection."""
    if not isinstance(value, Mapping):
        raise CrossFittedOutcomeResidualError("outcome manifest must be a mapping")
    root = _closed_mapping(
        dict(value),
        {
            "schema_version", "objective_kind", "fold_count", "advantage_clip",
            "source_episode_sha256", "training_permitted", "promotion_authority",
            "longrun_allowed", "episodes",
        },
        field="outcome manifest",
    )
    raw_episodes = root["episodes"]
    if type(raw_episodes) is not list:
        raise CrossFittedOutcomeResidualError("outcome manifest episodes must be a list")
    episodes: list[OutcomeEpisodeTargetsV1] = []
    for raw_episode in raw_episodes:
        episode = _closed_mapping(
            raw_episode,
            {"episode_id", "fold_index", "return_value", "targets"},
            field="outcome manifest episode",
        )
        raw_targets = episode["targets"]
        if type(raw_targets) is not list:
            raise CrossFittedOutcomeResidualError("outcome manifest targets must be a list")
        targets: list[SignedBehaviorResidualTargetV1] = []
        for raw_target in raw_targets:
            target = _closed_mapping(
                raw_target,
                {
                    "transition_index", "transition_sha256", "target_indices",
                    "return_value", "baseline_value", "advantage", "signed_weight",
                    "target_kind",
                },
                field="outcome manifest target",
            )
            indices = target["target_indices"]
            if type(indices) is not list:
                raise CrossFittedOutcomeResidualError("outcome manifest target indices must be a list")
            targets.append(SignedBehaviorResidualTargetV1(
                transition_index=target["transition_index"],  # type: ignore[arg-type]
                transition_sha256=target["transition_sha256"],  # type: ignore[arg-type]
                target_indices=tuple(indices),
                return_value=target["return_value"],  # type: ignore[arg-type]
                baseline_value=target["baseline_value"],  # type: ignore[arg-type]
                advantage=target["advantage"],  # type: ignore[arg-type]
                signed_weight=target["signed_weight"],  # type: ignore[arg-type]
                target_kind=target["target_kind"],  # type: ignore[arg-type]
            ))
        episodes.append(OutcomeEpisodeTargetsV1(
            episode_id=episode["episode_id"],  # type: ignore[arg-type]
            fold_index=episode["fold_index"],  # type: ignore[arg-type]
            return_value=episode["return_value"],  # type: ignore[arg-type]
            targets=tuple(targets),
        ))
    return CrossFittedOutcomeManifestV1(
        schema_version=root["schema_version"],  # type: ignore[arg-type]
        objective_kind=root["objective_kind"],  # type: ignore[arg-type]
        fold_count=root["fold_count"],  # type: ignore[arg-type]
        advantage_clip=root["advantage_clip"],  # type: ignore[arg-type]
        source_episode_sha256=root["source_episode_sha256"],  # type: ignore[arg-type]
        episodes=tuple(episodes),
        training_permitted=root["training_permitted"],  # type: ignore[arg-type]
        promotion_authority=root["promotion_authority"],  # type: ignore[arg-type]
        longrun_allowed=root["longrun_allowed"],  # type: ignore[arg-type]
    )


__all__ = [
    "SCHEMA_V1",
    "OBJECTIVE_KIND_V1",
    "TARGET_KIND_V1",
    "CrossFittedOutcomeResidualError",
    "OutcomeEpisodeV1",
    "SignedBehaviorResidualTargetV1",
    "OutcomeEpisodeTargetsV1",
    "CrossFittedOutcomeManifestV1",
    "build_cross_fitted_outcome_manifest_v1",
    "load_cross_fitted_outcome_manifest_v1",
]
