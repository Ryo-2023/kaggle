"""Deterministic offline dataset materialization + audit (O1 dataset deliverable).

Produces a self-contained, audited training-dataset directory under
``datasets/<dataset_id>/`` from:

- an Intelligence Snapshot's split selection (``sources="replay"``), gated by
  a decision-level **training eligibility policy** (see
  ``decision_eligibility.py``) before ever reaching
  ``offline_adapter.export_selected_decision_rows`` -- independent-audit
  remediation: every decision from a permitted, selected episode used to be
  exported unconditionally as a Behavior Cloning teacher target, with no
  distinction between "this action was merely executed" and "this action is
  safe to treat as a verified label"; or
- eligible Knowledge Claims (``sources="knowledge"``), i.e. claims whose
  ``training_eligible`` was explicitly granted (see
  ``contracts.KnowledgeClaim.with_transition``); or
- both (``sources="both"``, the default).

``baseline=True`` reproduces exactly what an operator would have gotten
*without* any O1 sidecar involved: every well-formed row of the original
Offline Training collection file, unfiltered, unselected, no training-policy
gate applied, sources forced to "replay". This is the byte-for-byte pre-O1
dataset, always available, never dependent on a snapshot existing.

Nothing here mutates the source Offline Training run, the Intelligence/
Knowledge Snapshot, or the Knowledge Registry -- every artifact under
``datasets/`` is a new, independent, deterministic derivative. This module
does not import ``mage_ptcg.offline_training`` or ``mage_ptcg.student``
beyond what ``offline_adapter``/``offline_reader`` already narrowly do.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence

from .atomic_io import atomic_write_bytes, atomic_write_json
from .canonical import canonical_json_bytes, sha256_hex
from .contracts import AllowedUse, DecisionRecord, EpisodeRecord, KnowledgeClaim
from .decision_eligibility import (
    DEFAULT_TRAINING_POLICY,
    VALID_SELECTION_POLICIES,
    build_high_information_index,
    compute_decision_eligibility,
)
from .high_info_selector import select_high_information_decisions
from .offline_adapter import enforce_training_permission, export_selected_decision_rows
from .offline_reader import discover_offline_training_run, iter_rule_bc_rows
from .permissions import has_permission
from .provenance import read_source_manifest
from .runstate import RunPaths

DATASET_SCHEMA_VERSION = "competition-intelligence-dataset-v1"
DATASET_AUDIT_SCHEMA_VERSION = "competition-intelligence-dataset-audit-v1"
DATASET_STATISTICS_SCHEMA_VERSION = "competition-intelligence-dataset-statistics-v1"
_DATASET_ID_HASH_LENGTH = 20

SourceMode = Literal["replay", "knowledge", "both"]
_VALID_SOURCES = ("replay", "knowledge", "both")


class DatasetMaterializationError(ValueError):
    """Raised when a dataset cannot be safely materialized."""


@dataclass(frozen=True, slots=True)
class DatasetMaterializationResult:
    dataset_id: str
    dataset_hash: str
    output_dir: Path
    manifest: Mapping[str, Any]
    audit_report: Mapping[str, Any]
    statistics_report: Mapping[str, Any]


def _count_malformed_rows(path: Path) -> int:
    return sum(1 for _, row, _error in iter_rule_bc_rows(path) if row is None)


def _export_baseline_rows(offline_training_run: str | Path, output_path: Path) -> dict[str, int]:
    """Write every well-formed row of the original collection file, unfiltered.

    This is the literal pre-O1 behavior: no snapshot, no split, no selection
    -- every row an operator would already have gotten by calling
    ``mage_ptcg.offline_training.dataset.build_dataset`` on the original file
    directly.
    """
    discovered = discover_offline_training_run(offline_training_run)
    if not discovered.is_usable():
        raise DatasetMaterializationError(f"no usable Offline Training collection found under {offline_training_run}")
    total = 0
    kept = 0
    quarantined = 0
    lines: list[bytes] = []
    for _, row, _error in iter_rule_bc_rows(discovered.collection_jsonl_path):
        total += 1
        if row is None:
            quarantined += 1
            continue
        lines.append(canonical_json_bytes(row) + b"\n")
        kept += 1
    atomic_write_bytes(output_path, b"".join(lines))
    return {"total_source_row_count": total, "kept_row_count": kept, "quarantined_row_count": quarantined}


def _eligible_knowledge_claims(claims: Sequence[KnowledgeClaim], run_root: Path) -> tuple[list[KnowledgeClaim], dict[str, int]]:
    """Filter to claims eligible for training, re-checking source permission.

    Training eligibility on the claim itself (``training_eligible=True``,
    which already implies ``status=SUPPORTED``, see
    ``KnowledgeClaim.__post_init__``) is necessary but not sufficient: the
    claim's ``raw_source_id`` must *currently* grant ``TRAINING`` too --
    re-verified here at materialization time, not trusted from whenever
    eligibility was originally granted (mirrors ``offline_adapter.
    enforce_training_permission``'s defense-in-depth re-check for replay
    sources).
    """
    excluded_reasons: dict[str, int] = {}
    eligible: list[KnowledgeClaim] = []
    for claim in sorted(claims, key=lambda c: c.claim_id):
        if not claim.training_eligible:
            excluded_reasons["not_training_eligible"] = excluded_reasons.get("not_training_eligible", 0) + 1
            continue
        try:
            envelope = read_source_manifest(run_root, claim.raw_source_id)
        except OSError:
            excluded_reasons["source_manifest_missing"] = excluded_reasons.get("source_manifest_missing", 0) + 1
            continue
        if not has_permission(envelope, AllowedUse.TRAINING):
            excluded_reasons["source_no_longer_grants_training"] = excluded_reasons.get("source_no_longer_grants_training", 0) + 1
            continue
        eligible.append(claim)
    return eligible, excluded_reasons


def _knowledge_claim_bytes(claims: Sequence[KnowledgeClaim]) -> bytes:
    return b"".join(canonical_json_bytes(claim.content_payload()) + b"\n" for claim in claims)


def materialize_dataset(
    run_root: str | Path,
    *,
    offline_training_run: str | Path,
    created_at: str,
    episodes: Sequence[EpisodeRecord] = (),
    decisions: Sequence[DecisionRecord] = (),
    knowledge_claims: Sequence[KnowledgeClaim] = (),
    sources: SourceMode = "both",
    baseline: bool = False,
    snapshot_id: str | None = None,
    split: str = "train",
    knowledge_snapshot_id: str | None = None,
    training_policy: str = DEFAULT_TRAINING_POLICY,
) -> DatasetMaterializationResult:
    if sources not in _VALID_SOURCES:
        raise DatasetMaterializationError(f"sources must be one of {_VALID_SOURCES}, got {sources!r}")
    if baseline and sources != "replay":
        raise DatasetMaterializationError("baseline=True reproduces the pre-O1 replay-only dataset; sources must be 'replay'")
    if training_policy not in VALID_SELECTION_POLICIES:
        raise DatasetMaterializationError(f"training_policy must be one of {VALID_SELECTION_POLICIES}, got {training_policy!r}")

    paths = RunPaths(Path(run_root))
    paths.datasets.mkdir(parents=True, exist_ok=True)
    include_replay = sources in ("replay", "both")
    include_knowledge = sources in ("knowledge", "both")
    episodes_by_id = {e.episode_id: e for e in episodes}
    decisions_by_episode: dict[str, list[DecisionRecord]] = {}
    for decision in decisions:
        decisions_by_episode.setdefault(decision.episode_id, []).append(decision)

    replay_row_stats = {"total_source_row_count": 0, "kept_row_count": 0, "quarantined_row_count": 0}
    replay_bytes = b""
    selected_episode_ids: list[str] = []
    split_assignment: dict[str, list[str]] = {}
    excluded_reason_counts: dict[str, int] = {}
    permission_check: dict[str, Any] = {"replay_passed": None, "knowledge_passed": None}
    leakage_check: Mapping[str, Any] | None = None
    eligibility_records: list[Any] = []
    eligible_decision_keys: set[tuple[str, int]] = set()

    if include_replay:
        if baseline:
            tmp_path = paths.datasets / "_tmp_baseline_replay.jsonl"
            replay_row_stats = _export_baseline_rows(offline_training_run, tmp_path)
            replay_bytes = tmp_path.read_bytes()
            tmp_path.unlink()
            # baseline mode intentionally matches pre-O1 behavior: no permission
            # gate, no training-eligibility gate, applied here.
        else:
            if not snapshot_id:
                raise DatasetMaterializationError("snapshot_id is required when sources includes 'replay' and baseline=False")
            split_path = paths.snapshots / snapshot_id / "split_assignment.json"
            if not split_path.exists():
                raise DatasetMaterializationError(f"no split_assignment.json found for snapshot {snapshot_id!r}")
            split_assignment = json.loads(split_path.read_text(encoding="utf-8"))
            key = f"{split}_episode_ids"
            if key not in split_assignment:
                raise DatasetMaterializationError(f"unknown split {split!r}; expected one of train/validation/test")
            selected_episode_ids = list(split_assignment[key])
            selected_episode_id_set = frozenset(selected_episode_ids)

            selected_source_ids = sorted({episodes_by_id[eid].source_id for eid in selected_episode_ids if eid in episodes_by_id})
            envelopes = [read_source_manifest(paths.root, source_id) for source_id in selected_source_ids]
            enforce_training_permission(envelopes)  # raises DatasetExportError (fail-closed) if any denies TRAINING
            permission_check["replay_passed"] = True
            # Every selected episode's source was just proven above to grant
            # TRAINING (enforce_training_permission would have raised
            # otherwise), so permission is granted for all of them here.
            permission_granted_by_episode = {eid: True for eid in selected_episode_ids}

            selected_decisions = [d for d in decisions if d.episode_id in selected_episode_id_set]
            high_information = select_high_information_decisions(selected_decisions, claims=knowledge_claims)
            high_information_index = build_high_information_index(high_information["selections"])
            eligibility_records = compute_decision_eligibility(
                selected_decisions, policy=training_policy,
                permission_granted_by_episode=permission_granted_by_episode,
                high_information_selectors_by_key=high_information_index,
            )
            eligible_decision_keys = {record.key() for record in eligibility_records if record.training_eligible}

            # Self-verifying determinism check (mirrors the knowledge-claim
            # selection check below): recompute the pure, in-memory
            # eligibility decision a second time and require an identical
            # eligible-key set before this materialization is considered valid.
            eligibility_records_b = compute_decision_eligibility(
                selected_decisions, policy=training_policy,
                permission_granted_by_episode=permission_granted_by_episode,
                high_information_selectors_by_key=high_information_index,
            )
            eligible_decision_keys_b = {record.key() for record in eligibility_records_b if record.training_eligible}
            if eligible_decision_keys_b != eligible_decision_keys:
                raise DatasetMaterializationError("non-deterministic decision eligibility detected; refusing to materialize")

            discovered = discover_offline_training_run(offline_training_run)
            if not discovered.is_usable():
                raise DatasetMaterializationError(f"no usable Offline Training collection found under {offline_training_run}")
            tmp_path = paths.datasets / "_tmp_replay.jsonl"
            counts = export_selected_decision_rows(
                discovered.collection_jsonl_path, tmp_path, selected_decision_keys=eligible_decision_keys
            )
            replay_bytes = tmp_path.read_bytes()
            tmp_path.unlink()
            replay_row_stats = {
                "total_source_row_count": counts["total_source_rows"],
                "kept_row_count": counts["kept_rows"],
                "quarantined_row_count": _count_malformed_rows(discovered.collection_jsonl_path),
            }

            leakage_path = paths.snapshots / snapshot_id / "leakage_audit.json"
            if leakage_path.exists():
                leakage_check = json.loads(leakage_path.read_text(encoding="utf-8"))

    eligible_claims: list[KnowledgeClaim] = []
    knowledge_bytes = b""
    if include_knowledge:
        eligible_claims, claim_excluded_reasons = _eligible_knowledge_claims(knowledge_claims, paths.root)
        for reason, count in claim_excluded_reasons.items():
            excluded_reason_counts[reason] = excluded_reason_counts.get(reason, 0) + count
        permission_check["knowledge_passed"] = True
        knowledge_bytes = _knowledge_claim_bytes(eligible_claims)

        # Self-verifying determinism check: recompute the pure, in-memory
        # selection a second time from the same inputs and require
        # byte-identical output before this materialization is considered
        # valid (mirrors IntelligenceSnapshot/KnowledgeSnapshot's own
        # self-verification pattern elsewhere in this package).
        eligible_claims_b, _ = _eligible_knowledge_claims(knowledge_claims, paths.root)
        if _knowledge_claim_bytes(eligible_claims_b) != knowledge_bytes:
            raise DatasetMaterializationError("non-deterministic knowledge claim selection detected; refusing to materialize")

    dataset_hash = sha256_hex(replay_bytes + b"\0" + knowledge_bytes)
    dataset_id = f"dataset-{dataset_hash[:_DATASET_ID_HASH_LENGTH]}"
    output_dir = paths.datasets / dataset_id
    output_dir.mkdir(parents=True, exist_ok=True)

    source_inventory: dict[str, Any] = {}
    if include_replay:
        source_inventory["replay_source_ids"] = (
            ["baseline:unfiltered_collection"] if baseline
            else sorted({episodes_by_id[eid].source_id for eid in selected_episode_ids if eid in episodes_by_id})
        )
    if include_knowledge:
        source_inventory["knowledge_raw_source_ids"] = sorted({c.raw_source_id for c in eligible_claims})

    if include_replay:
        atomic_write_bytes(output_dir / "replay.jsonl", replay_bytes)
    if include_knowledge:
        atomic_write_bytes(output_dir / "knowledge_claims.jsonl", knowledge_bytes)

    manifest = {
        "schema_version": DATASET_SCHEMA_VERSION,
        "dataset_id": dataset_id,
        "dataset_hash": dataset_hash,
        "created_at": created_at,
        "sources": sources,
        "baseline": baseline,
        "snapshot_id": snapshot_id,
        "knowledge_snapshot_id": knowledge_snapshot_id,
        "split": split if (include_replay and not baseline) else None,
        "training_policy": (training_policy if (include_replay and not baseline) else None),
        "source_inventory": source_inventory,
        "shard_files": (
            (["replay.jsonl"] if include_replay else []) + (["knowledge_claims.jsonl"] if include_knowledge else [])
        ),
    }
    atomic_write_json(output_dir / "manifest.json", manifest)

    if eligibility_records:
        atomic_write_json(
            output_dir / "eligibility_manifest.json",
            {"schema_version": DATASET_SCHEMA_VERSION, "selection_policy": training_policy,
             "decisions": [record.to_dict() for record in eligibility_records]},
        )

    # --- statistics report: descriptive breakdowns over eligible, retained records ---
    by_source: dict[str, int] = {}
    by_deck: dict[str, int] = {}
    by_matchup: dict[str, int] = {}
    by_seat: dict[str, int] = {}
    by_action_type: dict[str, int] = {}
    by_split: dict[str, int] = {}
    retained_decision_count = 0

    if include_replay and not baseline:
        split_of: dict[str, str] = {}
        for name in ("train", "validation", "test"):
            for eid in split_assignment.get(f"{name}_episode_ids", []):
                split_of[eid] = name
        for episode_id in selected_episode_ids:
            episode = episodes_by_id.get(episode_id)
            if episode is None:
                continue
            for decision in decisions_by_episode.get(episode_id, []):
                if (decision.episode_id, decision.decision_index) not in eligible_decision_keys:
                    continue
                retained_decision_count += 1
                by_source[episode.source_id] = by_source.get(episode.source_id, 0) + 1
                deck_key = episode.deck_a_reference or "UNKNOWN"
                by_deck[deck_key] = by_deck.get(deck_key, 0) + 1
                matchup_key = f"{episode.deck_a_reference or 'UNKNOWN'}_vs_{episode.deck_b_reference or 'UNKNOWN'}"
                by_matchup[matchup_key] = by_matchup.get(matchup_key, 0) + 1
                by_seat[str(decision.actor_seat)] = by_seat.get(str(decision.actor_seat), 0) + 1
                by_action_type[decision.phase] = by_action_type.get(decision.phase, 0) + 1
                split_name = split_of.get(episode_id, "unassigned")
                by_split[split_name] = by_split.get(split_name, 0) + 1

    # Replay decisions are literal recorded actions from a completed game --
    # always "observed", never an inference -- so every retained decision
    # counts as observed. Knowledge claims carry their own explicit
    # evidence_basis (see contracts.EvidenceBasis).
    evidence_basis_counts: dict[str, int] = {}
    for claim in eligible_claims:
        evidence_basis_counts[claim.evidence_basis.value] = evidence_basis_counts.get(claim.evidence_basis.value, 0) + 1
    observed_count = retained_decision_count + evidence_basis_counts.get("OBSERVED", 0)
    inferred_count = evidence_basis_counts.get("INFERRED", 0)

    statistics_report = {
        "schema_version": DATASET_STATISTICS_SCHEMA_VERSION,
        "dataset_id": dataset_id,
        "retained_decision_count": retained_decision_count,
        "retained_knowledge_claim_count": len(eligible_claims),
        "observed_count": observed_count,
        "inferred_count": inferred_count,
        "by_source": dict(sorted(by_source.items())),
        "by_deck": dict(sorted(by_deck.items())),
        "by_matchup": dict(sorted(by_matchup.items())),
        "by_seat": dict(sorted(by_seat.items())),
        "by_action_type": dict(sorted(by_action_type.items())),
        "by_split": dict(sorted(by_split.items())),
        "knowledge_evidence_basis_counts": dict(sorted(evidence_basis_counts.items())),
    }
    atomic_write_json(output_dir / "statistics_report.json", statistics_report)

    # --- audit report: safety/eligibility checks ---
    total_source_row_count = replay_row_stats["total_source_row_count"]
    adopted_row_count = replay_row_stats["kept_row_count"] + len(eligible_claims)
    duplicate_count = len(selected_episode_ids) - len(set(selected_episode_ids))
    missing_provenance_count = excluded_reason_counts.get("source_manifest_missing", 0)

    def _count_reason(substring: str) -> int:
        return sum(
            1 for record in eligibility_records
            if not record.training_eligible and any(substring in reason for reason in record.training_eligibility_reasons)
        )

    decision_selection = {
        "training_policy": training_policy if eligibility_records or (include_replay and not baseline) else None,
        "analysis_decision_count": len(eligibility_records),
        "training_eligible_decision_count": sum(1 for r in eligibility_records if r.training_eligible),
        "low_information_excluded_count": _count_reason("not_high_information"),
        "fallback_excluded_count": _count_reason("fallback_used"),
        "unverified_excluded_count": _count_reason("no_verification_basis"),
        "permission_excluded_decision_count": _count_reason("permission_not_granted"),
    }

    audit_report = {
        "schema_version": DATASET_AUDIT_SCHEMA_VERSION,
        "dataset_id": dataset_id,
        "dataset_hash": dataset_hash,
        "total_source_row_count": total_source_row_count,
        "adopted_row_count": adopted_row_count,
        "excluded_row_count": max(total_source_row_count - replay_row_stats["kept_row_count"], 0),
        "quarantined_row_count": replay_row_stats["quarantined_row_count"],
        "excluded_reason_counts": dict(sorted(excluded_reason_counts.items())),
        "observed_count": observed_count,
        "inferred_count": inferred_count,
        "duplicate_count": duplicate_count,
        "leakage_check": leakage_check,
        "permission_check": permission_check,
        "missing_provenance_count": missing_provenance_count,
        "decision_selection": decision_selection,
        "determinism_verified": True,  # would have raised above otherwise
    }
    atomic_write_json(output_dir / "audit_report.json", audit_report)

    return DatasetMaterializationResult(
        dataset_id=dataset_id, dataset_hash=dataset_hash, output_dir=output_dir,
        manifest=manifest, audit_report=audit_report, statistics_report=statistics_report,
    )


__all__ = [
    "DATASET_AUDIT_SCHEMA_VERSION",
    "DATASET_SCHEMA_VERSION",
    "DATASET_STATISTICS_SCHEMA_VERSION",
    "DatasetMaterializationError",
    "DatasetMaterializationResult",
    "materialize_dataset",
]
