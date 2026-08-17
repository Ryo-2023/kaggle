"""Immutable Intelligence Snapshot builder (O1-4 §1).

Orchestrates, in order: cutoff enforcement, allowed-use enforcement, source
caps, duplicate handling, the composite group split (``group_split.py``),
and the leakage audit (``leakage_audit.py``), then constructs a
self-verifying ``IntelligenceSnapshot`` (``contracts.build_intelligence_snapshot``)
whose content hash pins every decision made along the way. Configuration
changes (a different cutoff, a different seed, a different source allowlist)
always produce a *new* snapshot; nothing here mutates an existing one.

``IntelligenceSnapshot`` (frozen in O1-1, not modified here) has no field for
the actual train/validation/test episode-id lists -- that assignment is
content-derived from ``(seed, episode set)`` via ``group_split.py`` and is
returned alongside the snapshot as a companion, reproducible artifact
(``SnapshotBuildResult.split``), with its own ``split_hash`` folded into
``IntelligenceSnapshot.split_policy`` as a descriptive, verifiable string.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from .contracts import (
    AllowedUse,
    ContractError,
    DecisionRecord,
    EpisodeRecord,
    IntelligenceSnapshot,
    KnowledgeClaim,
    SourceEnvelope,
    build_intelligence_snapshot,
)
from .group_split import (
    HARD_CONNECTIVITY_DIMENSIONS,
    MINIMUM_GROUPS_FOR_SPLIT,
    GroupSplitError,
    SplitResult,
    build_hard_identity_components,
    split_by_composite_group,
)
from .leakage_audit import LeakageAuditResult, audit_split_leakage
from .permissions import has_permission

NORMALIZER_VERSION_KEY = "offline_training_replay_normalizer"


class SnapshotBuildError(ValueError):
    """Raised when a snapshot cannot be safely built from the given inputs."""


@dataclass(frozen=True, slots=True)
class SnapshotBuildResult:
    snapshot: IntelligenceSnapshot
    split: SplitResult | None
    leakage_audit: LeakageAuditResult | None
    excluded_episode_reasons: Mapping[str, str]
    component_diagnostics: Mapping[str, object]


def _filter_episodes(
    episodes: Sequence[EpisodeRecord],
    envelopes_by_source_id: Mapping[str, SourceEnvelope],
    *,
    cutoff_time: str,
    require_cutoff: bool,
    source_allowlist: frozenset[str] | None,
    source_denylist: frozenset[str],
    source_cap: int | None,
) -> tuple[list[EpisodeRecord], dict[str, str]]:
    excluded: dict[str, str] = {}
    kept: list[EpisodeRecord] = []
    seen_ids: set[str] = set()
    per_source_count: dict[str, int] = {}

    for episode in episodes:
        if episode.episode_id in seen_ids:
            excluded[episode.episode_id] = "duplicate_episode_id"
            continue
        if source_allowlist is not None and episode.source_id not in source_allowlist:
            excluded[episode.episode_id] = "source_not_in_allowlist"
            continue
        if episode.source_id in source_denylist:
            excluded[episode.episode_id] = "source_in_denylist"
            continue
        envelope = envelopes_by_source_id.get(episode.source_id)
        if envelope is None:
            excluded[episode.episode_id] = "source_envelope_not_found"
            continue
        if not has_permission(envelope, AllowedUse.ANALYSIS):
            excluded[episode.episode_id] = "source_does_not_grant_analysis"
            continue
        if require_cutoff:
            if episode.played_at is None:
                excluded[episode.episode_id] = "played_at_unknown_cannot_verify_cutoff"
                continue
            if episode.played_at > cutoff_time:
                excluded[episode.episode_id] = "played_at_after_cutoff"
                continue
        if source_cap is not None:
            count = per_source_count.get(episode.source_id, 0)
            if count >= source_cap:
                excluded[episode.episode_id] = "source_cap_exceeded"
                continue
            per_source_count[episode.source_id] = count + 1
        seen_ids.add(episode.episode_id)
        kept.append(episode)
    return kept, excluded


def build_snapshot(
    *,
    episodes: Sequence[EpisodeRecord],
    decisions: Sequence[DecisionRecord],
    source_envelopes: Sequence[SourceEnvelope],
    cutoff_time: str,
    base_commit: str,
    created_at: str,
    seed: int = 0,
    require_cutoff: bool = True,
    source_allowlist: Sequence[str] | None = None,
    source_denylist: Sequence[str] = (),
    source_cap: int | None = None,
    source_weights: Mapping[str, float] | None = None,
    knowledge_snapshot_hash: str | None = None,
    meta_snapshot_hash: str | None = None,
    knowledge_claims: Sequence[KnowledgeClaim] = (),
    normalizer_versions: Mapping[str, str] | None = None,
    analysis_versions: Mapping[str, str] | None = None,
    validation_fraction: float = 0.15,
    test_fraction: float = 0.15,
    temporal_buckets: Mapping[str, str] | None = None,
) -> SnapshotBuildResult:
    """Build one immutable ``IntelligenceSnapshot`` from normalized inputs.

    Raises ``SnapshotBuildError`` if every episode is excluded (nothing to
    snapshot) rather than silently producing an empty-but-"valid" snapshot.
    """
    envelopes_by_source_id = {envelope.source_id: envelope for envelope in source_envelopes}
    allowlist_set = frozenset(source_allowlist) if source_allowlist is not None else None
    denylist_set = frozenset(source_denylist)

    kept_episodes, excluded_reasons = _filter_episodes(
        episodes, envelopes_by_source_id,
        cutoff_time=cutoff_time, require_cutoff=require_cutoff,
        source_allowlist=allowlist_set, source_denylist=denylist_set, source_cap=source_cap,
    )
    if not kept_episodes:
        raise SnapshotBuildError("every episode was excluded; nothing to snapshot")

    kept_ids = {episode.episode_id for episode in kept_episodes}
    kept_decisions = [decision for decision in decisions if decision.episode_id in kept_ids]

    # Hard-identity component diagnostics (independent-audit finding #1):
    # always computed, whether or not a split ultimately succeeds, so an
    # unsplittable dataset's component count/sizes/blocking component are
    # visible in the snapshot's own record rather than only in a raised
    # exception's message.
    component_assignment = build_hard_identity_components(kept_episodes)
    component_diagnostics: dict[str, object] = {
        "component_count": component_assignment.component_count,
        "component_sizes": list(component_assignment.component_sizes),
        "largest_component_id": component_assignment.largest_component_id,
        "hard_connectivity_dimensions": list(HARD_CONNECTIVITY_DIMENSIONS),
        "splittable": component_assignment.component_count >= MINIMUM_GROUPS_FOR_SPLIT,
    }

    split_result: SplitResult | None = None
    leakage_result: LeakageAuditResult | None = None
    split_policy_descriptor = "no_split_requested"
    try:
        split_result = split_by_composite_group(
            kept_episodes, seed=seed, validation_fraction=validation_fraction, test_fraction=test_fraction,
            temporal_buckets=temporal_buckets,
        )
        split_policy_descriptor = f"{split_result.manifest['split_method']}:{split_result.manifest['split_hash']}"
        episodes_by_id = {episode.episode_id: episode for episode in kept_episodes}
        leakage_result = audit_split_leakage(
            train_ids=split_result.train_episode_ids,
            validation_ids=split_result.validation_episode_ids,
            test_ids=split_result.test_episode_ids,
            episodes_by_id=episodes_by_id,
            cutoff_time=cutoff_time if require_cutoff else None,
            knowledge_claims=knowledge_claims,
        )
        if not leakage_result.passed:
            raise SnapshotBuildError(f"leakage audit failed: {leakage_result.to_dict()}")
    except GroupSplitError as exc:
        split_policy_descriptor = f"split_unavailable:{exc}"
        component_diagnostics["unsplittable_reason"] = str(exc)

    effective_weights = dict(source_weights) if source_weights is not None else {
        envelope.source_id: 1.0 for envelope in source_envelopes if envelope.source_id in {e.source_id for e in kept_episodes}
    }

    # Permission summary: how many of the snapshot's *contributing* sources
    # grant each AllowedUse -- computed from the same envelopes already
    # resolved above, never a convenience default.
    kept_source_ids = sorted({episode.source_id for episode in kept_episodes})
    permission_summary: dict[str, int] = {}
    for source_id in kept_source_ids:
        for use in envelopes_by_source_id[source_id].allowed_uses:
            permission_summary[use.value] = permission_summary.get(use.value, 0) + 1

    try:
        snapshot = build_intelligence_snapshot(
            created_at=created_at,
            cutoff_time=cutoff_time,
            base_commit=base_commit,
            input_source_ids=tuple(kept_source_ids),
            input_hashes=tuple(envelopes_by_source_id[source_id].content_hash() for source_id in kept_source_ids),
            normalizer_versions=dict(normalizer_versions or {}),
            analysis_versions=dict(analysis_versions or {}),
            permission_summary=permission_summary,
            knowledge_snapshot_hash=knowledge_snapshot_hash,
            meta_snapshot_hash=meta_snapshot_hash,
            selection_policy="all_permitted_episodes_within_cutoff",
            source_weights=effective_weights,
            split_policy=split_policy_descriptor,
            excluded_records=tuple(sorted(excluded_reasons)),
            episode_count=len(kept_episodes),
            decision_count=len(kept_decisions),
        )
    except ContractError as exc:
        raise SnapshotBuildError(str(exc)) from exc

    return SnapshotBuildResult(
        snapshot=snapshot, split=split_result, leakage_audit=leakage_result, excluded_episode_reasons=excluded_reasons,
        component_diagnostics=component_diagnostics,
    )


__all__ = ["NORMALIZER_VERSION_KEY", "SnapshotBuildError", "SnapshotBuildResult", "build_snapshot"]
