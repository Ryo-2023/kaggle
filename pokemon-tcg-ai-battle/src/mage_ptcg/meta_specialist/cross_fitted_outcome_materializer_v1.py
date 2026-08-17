"""Research-only hash-bound join for signed outcome residual targets.

This module aligns a sealed screen trajectory with a separately sealed
cross-fitted outcome manifest.  It never launches learning or CABT, and its
``RecurrentBCSequenceV4`` carriers have zero supervision weights so they
cannot silently be handed to the ordinary hard/soft BC trainer.  A later
signed-residual trainer must consume ``AlignedSignedResidualPrefixV1``.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Sequence

from mage_ptcg.meta_specialist.cross_fitted_outcome_residual_v1 import (
    CrossFittedOutcomeManifestV1,
    CrossFittedOutcomeResidualError,
    OutcomeEpisodeV1,
    build_cross_fitted_outcome_manifest_v1,
    load_cross_fitted_outcome_manifest_v1,
)
from mage_ptcg.meta_specialist.dagger_v4 import parse_transition_payload_v4
from mage_ptcg.meta_specialist.frozen_residual_preflight_v1 import SeedKnownDomainManifestV1
from mage_ptcg.meta_specialist.recurrent_dataset_v4 import RecurrentBCSequenceV4, RecurrentBCStepV4
from mage_ptcg.meta_specialist.representation_v4 import representation_v4_from_step_input_v1
from mage_ptcg.meta_specialist.trajectory_v1 import ActorTrajectoryTransitionV1


TARGET_KIND_V1 = "signed_behavior_log_probability"
_SCREEN_SCHEMA_V1 = "meta-specialist-v4-dagger-transition-v1"
_SCREEN_KEYS = {
    "component_id", "env_seed", "episode_group", "game_id", "opponent_id", "partition",
    "schema", "seat", "transition", "transition_index",
}
_HEX64 = frozenset("0123456789abcdef")


class CrossFittedOutcomeMaterializerError(ValueError):
    """Raised when screen rows and signed outcome targets do not seal together."""


def _sha(value: object, *, field: str) -> str:
    if type(value) is not str or len(value) != 64 or any(char not in _HEX64 for char in value):
        raise CrossFittedOutcomeMaterializerError(f"{field} must be a lowercase SHA-256")
    return value


def _file_sha(path: Path) -> str:
    if not path.is_file() or path.is_symlink():
        raise CrossFittedOutcomeMaterializerError("sealed source must be a regular non-symlink file")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_train_episodes(path: Path) -> tuple[OutcomeEpisodeV1, ...]:
    active_key: tuple[str, str, int] | None = None
    active_rows: list[ActorTrajectoryTransitionV1] = []
    seen_keys: set[tuple[str, str, int]] = set()
    seen_game_ids: set[str] = set()
    episodes: list[OutcomeEpisodeV1] = []

    def close_active() -> None:
        nonlocal active_key, active_rows
        if active_key is None:
            return
        game_id, _opaque_opponent, _opaque_seat = active_key
        episodes.append(OutcomeEpisodeV1(episode_id=game_id, transitions=tuple(active_rows)))
        seen_keys.add(active_key)
        seen_game_ids.add(game_id)
        active_key, active_rows = None, []

    with path.open("rb") as handle:
        for line_number, raw in enumerate(handle, start=1):
            try:
                row = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise CrossFittedOutcomeMaterializerError(f"screen line {line_number} is invalid JSON") from exc
            if type(row) is not dict or set(row) != _SCREEN_KEYS or row.get("schema") != _SCREEN_SCHEMA_V1:
                raise CrossFittedOutcomeMaterializerError(f"screen line {line_number} has an open schema")
            if row["partition"] != "train":
                continue
            game_id = _sha(row["game_id"], field="screen game_id")
            if _sha(row["component_id"], field="screen component_id") != game_id or _sha(row["episode_group"], field="screen episode_group") != game_id:
                raise CrossFittedOutcomeMaterializerError(f"screen line {line_number} game/component topology differs")
            opaque_opponent = row["opponent_id"]
            opaque_seat = row["seat"]
            index = row["transition_index"]
            if type(opaque_opponent) is not str or not opaque_opponent or type(opaque_seat) is not int or opaque_seat not in {0, 1}:
                raise CrossFittedOutcomeMaterializerError(f"screen line {line_number} grouping provenance is invalid")
            if type(index) is not int or index < 0:
                raise CrossFittedOutcomeMaterializerError(f"screen line {line_number} transition index is invalid")
            key = (game_id, opaque_opponent, opaque_seat)
            if key != active_key:
                close_active()
                if key in seen_keys or game_id in seen_game_ids:
                    raise CrossFittedOutcomeMaterializerError(f"screen line {line_number} has game reentry")
                active_key = key
            if index != len(active_rows):
                raise CrossFittedOutcomeMaterializerError(f"screen line {line_number} transition order is not contiguous")
            try:
                active_rows.append(parse_transition_payload_v4(row["transition"]))
            except (AttributeError, KeyError, TypeError, ValueError) as exc:
                raise CrossFittedOutcomeMaterializerError(f"screen line {line_number} transition is invalid") from exc
    close_active()
    if not episodes:
        raise CrossFittedOutcomeMaterializerError("screen has no train episodes")
    return tuple(episodes)


@dataclass(frozen=True, slots=True)
class AlignedSignedResidualPrefixV1:
    """One signed chosen-action input for a later residual-only loss."""

    sequence_index: int
    sequence_step_index: int
    episode_id: str
    transition_index: int
    transition_sha256: str
    prefix_index: int
    target_index: int
    signed_weight: float
    target_kind: str = TARGET_KIND_V1

    def __post_init__(self) -> None:
        if any(type(getattr(self, field)) is not int or getattr(self, field) < 0 for field in ("sequence_index", "sequence_step_index", "transition_index", "prefix_index", "target_index")):
            raise CrossFittedOutcomeMaterializerError("aligned target indices are invalid")
        _sha(self.episode_id, field="aligned episode_id")
        _sha(self.transition_sha256, field="aligned transition_sha256")
        if type(self.signed_weight) not in (int, float) or type(self.signed_weight) is bool or not -1.0 <= float(self.signed_weight) <= 1.0:
            raise CrossFittedOutcomeMaterializerError("aligned signed weight is invalid")
        if self.target_kind != TARGET_KIND_V1:
            raise CrossFittedOutcomeMaterializerError("aligned target kind must remain signed behavior")


@dataclass(frozen=True, slots=True)
class SignedOutcomeMaterializationV1:
    """In-memory research materialization; not a train/eval authorization."""

    seed: int
    target_manifest_file_sha256: str
    source_transitions_file_sha256: str
    source_episode_sha256: str
    sequences: tuple[RecurrentBCSequenceV4, ...]
    prefix_targets: tuple[AlignedSignedResidualPrefixV1, ...]
    training_permitted: bool = False
    promotion_authority: bool = False
    longrun_allowed: bool = False

    def __post_init__(self) -> None:
        if type(self.seed) is not int or self.seed not in {0, 1}:
            raise CrossFittedOutcomeMaterializerError("materialization seed is invalid")
        for field in ("target_manifest_file_sha256", "source_transitions_file_sha256", "source_episode_sha256"):
            _sha(getattr(self, field), field=field)
        if type(self.sequences) is not tuple or not self.sequences or any(type(item) is not RecurrentBCSequenceV4 or not item.research_only for item in self.sequences):
            raise CrossFittedOutcomeMaterializerError("materialization sequences are invalid")
        if type(self.prefix_targets) is not tuple or not self.prefix_targets or any(type(item) is not AlignedSignedResidualPrefixV1 for item in self.prefix_targets):
            raise CrossFittedOutcomeMaterializerError("materialization prefix targets are invalid")
        if any(getattr(self, field) is not False for field in ("training_permitted", "promotion_authority", "longrun_allowed")):
            raise CrossFittedOutcomeMaterializerError("materialization grants authority")

    @property
    def context_only_rows(self) -> int:
        return sum(len(sequence.steps) for sequence in self.sequences)

    def to_summary_dict(self) -> dict[str, object]:
        return {
            "schema_version": "specialist-cross-fitted-mc-residual-materialization-v1",
            "seed": self.seed,
            "target_manifest_file_sha256": self.target_manifest_file_sha256,
            "source_transitions_file_sha256": self.source_transitions_file_sha256,
            "source_episode_sha256": self.source_episode_sha256,
            "sequence_count": len(self.sequences),
            "prefix_target_count": len(self.prefix_targets),
            "context_only_rows": self.context_only_rows,
            "target_kind": TARGET_KIND_V1,
            "training_permitted": False,
            "promotion_authority": False,
            "longrun_allowed": False,
        }


def _load_manifest(path: Path, *, expected_sha256: str) -> CrossFittedOutcomeManifestV1:
    actual = _file_sha(path)
    if actual != _sha(expected_sha256, field="expected target manifest SHA-256"):
        raise CrossFittedOutcomeMaterializerError("target manifest SHA-256 differs from the expected binding")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return load_cross_fitted_outcome_manifest_v1(payload)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, CrossFittedOutcomeResidualError, TypeError, ValueError) as exc:
        raise CrossFittedOutcomeMaterializerError("target manifest is not a closed signed-outcome manifest") from exc


def _recomputed_manifest(
    episodes: Sequence[OutcomeEpisodeV1], manifest: CrossFittedOutcomeManifestV1,
) -> CrossFittedOutcomeManifestV1:
    try:
        recomputed = build_cross_fitted_outcome_manifest_v1(
            episodes, fold_count=manifest.fold_count, advantage_clip=manifest.advantage_clip,
        )
    except CrossFittedOutcomeResidualError as exc:
        raise CrossFittedOutcomeMaterializerError("sealed screen cannot reproduce outcome targets") from exc
    if recomputed.source_episode_sha256 != manifest.source_episode_sha256:
        raise CrossFittedOutcomeMaterializerError("source episode SHA-256 differs from outcome manifest")
    if recomputed.to_dict() != manifest.to_dict():
        raise CrossFittedOutcomeMaterializerError("outcome manifest target values differ from sealed screen")
    return recomputed


def materialize_signed_outcome_targets_v1(
    target_manifest_path: Path,
    *,
    expected_manifest_sha256: str,
    known_domain: SeedKnownDomainManifestV1,
    max_episodes: int | None = None,
) -> SignedOutcomeMaterializationV1:
    """Hash-join one seed's sealed screen with a signed outcome target manifest.

    The returned V4 sequences carry context only (`supervision_weight=0`).
    Their one-hot target fields exist solely because the V4 sequence schema
    requires a legal target; signed objective consumers must use the separate
    aligned target rows and cannot invoke ordinary BC loss by accident.
    """
    if type(known_domain) is not SeedKnownDomainManifestV1:
        raise CrossFittedOutcomeMaterializerError("known domain must be exact seed manifest")
    if max_episodes is not None and (type(max_episodes) is not int or max_episodes < 1):
        raise CrossFittedOutcomeMaterializerError("max_episodes must be a positive int or None")
    source_path = Path(known_domain.provenance.transitions_path)
    actual_source_sha = _file_sha(source_path)
    if actual_source_sha != known_domain.provenance.transitions_file_sha256:
        raise CrossFittedOutcomeMaterializerError("sealed screen source SHA-256 differs from seed provenance")
    manifest = _load_manifest(target_manifest_path, expected_sha256=expected_manifest_sha256)
    episodes = _read_train_episodes(source_path)
    _recomputed_manifest(episodes, manifest)
    targets_by_episode = {item.episode_id: item for item in manifest.episodes}
    if set(targets_by_episode) != {item.episode_id for item in episodes}:
        raise CrossFittedOutcomeMaterializerError("outcome manifest episode set differs from sealed screen")
    sequences: list[RecurrentBCSequenceV4] = []
    aligned: list[AlignedSignedResidualPrefixV1] = []
    selected_episodes = tuple(sorted(episodes, key=lambda item: item.episode_id))
    if max_episodes is not None:
        selected_episodes = selected_episodes[:max_episodes]
    for sequence_index, episode in enumerate(selected_episodes):
        target_episode = targets_by_episode[episode.episode_id]
        if len(target_episode.targets) != len(episode.transitions):
            raise CrossFittedOutcomeMaterializerError("episode transition count differs from outcome targets")
        steps: list[RecurrentBCStepV4] = []
        for transition_index, (transition, transition_target, transition_sha) in enumerate(zip(
            episode.transitions, target_episode.targets, episode.source_transition_sha256, strict=True,
        )):
            if transition_target.transition_index != transition_index or transition_target.transition_sha256 != transition_sha:
                raise CrossFittedOutcomeMaterializerError("transition SHA/index differs from outcome target")
            if transition_target.target_kind != TARGET_KIND_V1:
                raise CrossFittedOutcomeMaterializerError("outcome target kind is not signed behavior")
            if len(transition_target.target_indices) != len(transition.prefix_steps):
                raise CrossFittedOutcomeMaterializerError("prefix count differs from outcome target")
            for prefix_index, (prefix, target_index) in enumerate(zip(
                transition.prefix_steps, transition_target.target_indices, strict=True,
            )):
                domain_size = len(prefix.step_input.allowed_semantic_classes) + int(prefix.step_input.stop_available)
                if not 0 <= target_index < domain_size:
                    raise CrossFittedOutcomeMaterializerError("outcome target index is outside the sealed prefix domain")
                if prefix.chosen_is_stop != (target_index == len(prefix.step_input.allowed_semantic_classes)):
                    raise CrossFittedOutcomeMaterializerError("outcome target STOP/semantic alignment differs")
                state = representation_v4_from_step_input_v1(
                    transition.model_input, prefix.step_input, allow_unbound_selected=True,
                )
                masses = tuple(float(index == target_index) for index in range(domain_size))
                step_index = len(steps)
                steps.append(RecurrentBCStepV4(
                    state=state,
                    target_index=target_index,
                    episode_group=episode.episode_id,
                    quality_weight=1.0,
                    model_input=transition.model_input,
                    step_input=prefix.step_input,
                    target_masses=masses,
                    reach_mass=1.0,
                    episode_start=(transition_index == 0 and prefix_index == 0),
                    component_id=episode.episode_id,
                    partition="train",
                    record_id=transition_sha,
                    content_hash=transition_sha,
                    research_only=True,
                    supervision_weight=0.0,
                ))
                aligned.append(AlignedSignedResidualPrefixV1(
                    sequence_index=sequence_index,
                    sequence_step_index=step_index,
                    episode_id=episode.episode_id,
                    transition_index=transition_index,
                    transition_sha256=transition_sha,
                    prefix_index=prefix_index,
                    target_index=target_index,
                    signed_weight=transition_target.signed_weight,
                ))
        sequences.append(RecurrentBCSequenceV4(
            lane=f"frozen-residual-outcome-seed-{known_domain.provenance.seed}",
            episode_group=episode.episode_id,
            component_id=episode.episode_id,
            partition="train",
            steps=tuple(steps),
            burn_in=0,
            research_only=True,
        ))
    return SignedOutcomeMaterializationV1(
        seed=known_domain.provenance.seed,
        target_manifest_file_sha256=_sha(expected_manifest_sha256, field="expected target manifest SHA-256"),
        source_transitions_file_sha256=actual_source_sha,
        source_episode_sha256=manifest.source_episode_sha256,
        sequences=tuple(sequences),
        prefix_targets=tuple(aligned),
    )


__all__ = [
    "TARGET_KIND_V1",
    "CrossFittedOutcomeMaterializerError",
    "AlignedSignedResidualPrefixV1",
    "SignedOutcomeMaterializationV1",
    "materialize_signed_outcome_targets_v1",
]
