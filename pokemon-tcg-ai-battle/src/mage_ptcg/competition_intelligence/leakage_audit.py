"""Machine-computed leakage audit for a group split (O1-4 §3).

Every count here is computed from the actual split assignment and episode
records passed in -- never hardcoded to zero. ``passed`` is derived only from
the hard invariants the O1-4 design lists as things to "enforce and test"
(episode/opponent/temporal/future-claim/duplicate leakage); deck-fingerprint,
model/submission, and source leakage are *reported* (per the design's
"leakage audit... covering" list) but are not themselves pass/fail gates,
since some overlap there (e.g. two different opponents both piloting the
same well-known deck archetype) is not necessarily a real information leak.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from .contracts import EpisodeRecord, KnowledgeClaim

LEAKAGE_AUDIT_SCHEMA_VERSION = "leakage-audit-v1"

# Leakage dimensions that must be exactly zero for a split to pass, per O1-4 §2.
_HARD_INVARIANT_FIELDS = (
    "episode_leakage_count",
    "opponent_leakage_count",
    "temporal_leakage_count",
    "future_knowledge_claim_leakage_count",
    "duplicate_leakage_count",
)


@dataclass(frozen=True, slots=True)
class LeakageAuditResult:
    schema_version: str
    episode_leakage_count: int
    opponent_leakage_count: int
    model_submission_leakage_count: int
    deck_fingerprint_leakage_count: int
    temporal_leakage_count: int
    source_leakage_count: int
    permission_violation_count: int
    future_knowledge_claim_leakage_count: int
    duplicate_leakage_count: int
    passed: bool
    details: Mapping[str, object]

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "episode_leakage_count": self.episode_leakage_count,
            "opponent_leakage_count": self.opponent_leakage_count,
            "model_submission_leakage_count": self.model_submission_leakage_count,
            "deck_fingerprint_leakage_count": self.deck_fingerprint_leakage_count,
            "temporal_leakage_count": self.temporal_leakage_count,
            "source_leakage_count": self.source_leakage_count,
            "permission_violation_count": self.permission_violation_count,
            "future_knowledge_claim_leakage_count": self.future_knowledge_claim_leakage_count,
            "duplicate_leakage_count": self.duplicate_leakage_count,
            "passed": self.passed,
            "details": dict(self.details),
        }


def _distinct_or_none(episodes_by_id: Mapping[str, EpisodeRecord], ids: Sequence[str], attribute: str) -> set[object]:
    values: set[object] = set()
    for episode_id in ids:
        episode = episodes_by_id.get(episode_id)
        if episode is None:
            continue
        value = getattr(episode, attribute)
        if value is not None:
            values.add(value)
    return values


def audit_split_leakage(
    *,
    train_ids: Sequence[str],
    validation_ids: Sequence[str],
    test_ids: Sequence[str],
    episodes_by_id: Mapping[str, EpisodeRecord],
    cutoff_time: str | None = None,
    knowledge_claims: Sequence[KnowledgeClaim] = (),
    permission_violation_count: int = 0,
) -> LeakageAuditResult:
    train_set, validation_set, test_set = set(train_ids), set(validation_ids), set(test_ids)
    holdout_set = validation_set | test_set

    episode_overlap = (train_set & validation_set) | (train_set & test_set) | (validation_set & test_set)

    train_opponents = _distinct_or_none(episodes_by_id, sorted(train_set), "agent_b")
    holdout_opponents = _distinct_or_none(episodes_by_id, sorted(holdout_set), "agent_b")
    opponent_leakage = train_opponents & holdout_opponents

    train_agents = _distinct_or_none(episodes_by_id, sorted(train_set), "agent_a")
    holdout_agents = _distinct_or_none(episodes_by_id, sorted(holdout_set), "agent_a")
    model_leakage = train_agents & holdout_agents

    train_decks = _distinct_or_none(episodes_by_id, sorted(train_set), "deck_a_reference")
    holdout_decks = _distinct_or_none(episodes_by_id, sorted(holdout_set), "deck_a_reference")
    deck_leakage = train_decks & holdout_decks

    train_sources = _distinct_or_none(episodes_by_id, sorted(train_set), "source_id")
    holdout_sources = _distinct_or_none(episodes_by_id, sorted(holdout_set), "source_id")
    source_leakage = train_sources & holdout_sources

    temporal_leakage_count = 0
    if cutoff_time is not None:
        for episode_id in train_set | holdout_set:
            episode = episodes_by_id.get(episode_id)
            if episode is not None and episode.played_at is not None and episode.played_at > cutoff_time:
                temporal_leakage_count += 1

    future_claim_leakage_count = 0
    if cutoff_time is not None:
        future_claim_leakage_count = sum(1 for claim in knowledge_claims if claim.created_at > cutoff_time)

    all_ids = list(train_ids) + list(validation_ids) + list(test_ids)
    duplicate_leakage_count = len(all_ids) - len(set(all_ids))

    result_fields = {
        "episode_leakage_count": len(episode_overlap),
        "opponent_leakage_count": len(opponent_leakage),
        "model_submission_leakage_count": len(model_leakage),
        "deck_fingerprint_leakage_count": len(deck_leakage),
        "temporal_leakage_count": temporal_leakage_count,
        "source_leakage_count": len(source_leakage),
        "permission_violation_count": permission_violation_count,
        "future_knowledge_claim_leakage_count": future_claim_leakage_count,
        "duplicate_leakage_count": duplicate_leakage_count,
    }
    passed = all(result_fields[field] == 0 for field in _HARD_INVARIANT_FIELDS) and permission_violation_count == 0

    return LeakageAuditResult(
        schema_version=LEAKAGE_AUDIT_SCHEMA_VERSION,
        **result_fields,
        passed=passed,
        details={
            "episode_overlap_ids": sorted(episode_overlap),
            "opponent_overlap": sorted(str(v) for v in opponent_leakage),
            "deck_fingerprint_overlap": sorted(str(v) for v in deck_leakage),
            "source_overlap": sorted(str(v) for v in source_leakage),
        },
    )


__all__ = ["LEAKAGE_AUDIT_SCHEMA_VERSION", "LeakageAuditResult", "audit_split_leakage"]
