"""Run-directory orchestration for the O1-2..O1-4 pipeline commands.

Each function here operates on a run directory (``RunPaths``) and is the
business logic behind a CLI subcommand (``cli.py`` stays a thin argparse
wrapper around these, matching the ``local_ingest.py``/``cli.py`` split
already established in O1-0/O1-1). Persists canonical JSONL/JSON artifacts
under ``normalized/``, ``derived/``, ``snapshots/``, ``reports/`` using the
existing atomic-write primitives; nothing here mutates a source run or an
already-built snapshot.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .atomic_io import atomic_write_bytes, atomic_write_json
from .canonical import canonical_json_bytes, digest
from .claim_bundle import import_claim_bundle
from .contracts import (
    AllowedUse,
    ClaimStatus,
    ContractError,
    DecisionRecord,
    EpisodeRecord,
    EvidenceGrade,
    KnowledgeClaim,
)
from .contradiction import detect_contradictions
from .external_acquisition import acquire_external_artifact
from .external_capability import CapabilityReport, probe_capability
from .external_schema import build_schema_drift_report
from .external_transport import (
    EXTERNAL_ACTIONS,
    ExternalRawResponse,
    ExternalTransport,
    FixtureTransport,
    RecordedResponseTransport,
    SubprocessKaggleTransport,
    UnavailableTransport,
)
from .failure_hypothesis import generate_failure_hypotheses
from .fingerprint import build_deck_fingerprint, build_joint_fingerprint, build_policy_fingerprint
from .high_info_selector import select_high_information_decisions
from .knowledge_registry import iter_claim_versions, latest_claims
from .knowledge_snapshot import build_knowledge_snapshot
from .matchup_stats import aggregate_matchup_statistics
from .meta import ANALYSIS_GRANTED, WeightedStrategyObservation, build_meta_snapshot, detect_drift
from .offline_adapter import enforce_training_permission, export_selected_rows
from .offline_reader import discover_offline_training_run
from .permissions import has_permission
from .provenance import envelope_from_manifest_payload, read_source_manifest
from .raw_notes import archive_raw_note
from .replay_normalize import NORMALIZER_VERSION, normalize_rule_bc_jsonl
from .kaggle_replay_run import KaggleReplayRunError, run_normalize_live_own_replays
from .runstate import RunPaths
from .snapshot_builder import build_snapshot as _build_intelligence_snapshot
from .team_bundle import import_team_bundle
from .surrogate import SurrogateObservation, build_opponent_surrogate, evaluate_surrogate
from .benchmark import build_benchmark_manifest
from .promotion_report import build_promotion_report

REPORT_SCHEMA_VERSION = "competition-intelligence-report-v1"


class PipelineError(ValueError):
    """Raised for a pipeline-level failure (corrupt persisted artifact, missing input)."""


# --------------------------------------------------------------------------- #
# Episode/Decision (de)serialization
# --------------------------------------------------------------------------- #


def _episode_to_payload(episode: EpisodeRecord) -> dict[str, Any]:
    payload = episode.content_payload()
    payload["content_hash"] = episode.content_hash()
    return payload


def _episode_from_payload(payload: Mapping[str, Any]) -> EpisodeRecord:
    fields = {key: value for key, value in payload.items() if key != "content_hash"}
    try:
        episode = EpisodeRecord(
            schema_version=fields["schema_version"], episode_id=fields["episode_id"], source_id=fields["source_id"],
            competition_id=fields.get("competition_id"), played_at=fields.get("played_at"),
            engine_version=fields.get("engine_version"), agent_a=fields.get("agent_a"), agent_b=fields.get("agent_b"),
            deck_a_reference=fields.get("deck_a_reference"), deck_b_reference=fields.get("deck_b_reference"),
            first_player=fields.get("first_player"), winner=fields.get("winner"),
            termination_reason=fields.get("termination_reason"), turn_count=fields["turn_count"],
            decision_count=fields["decision_count"], public_trace_hash=fields.get("public_trace_hash"),
            quality_flags=frozenset(fields.get("quality_flags", ())),
        )
    except (KeyError, ContractError) as exc:
        raise PipelineError(f"corrupt normalized episode record: {exc}") from exc
    expected = payload.get("content_hash")
    if expected is not None and expected != episode.content_hash():
        raise PipelineError(f"episode {episode.episode_id!r} content_hash mismatch on load")
    return episode


def _decision_to_payload(decision: DecisionRecord) -> dict[str, Any]:
    payload = decision.content_payload()
    payload["content_hash"] = decision.content_hash()
    return payload


def _decision_from_payload(payload: Mapping[str, Any]) -> DecisionRecord:
    fields = {key: value for key, value in payload.items() if key != "content_hash"}
    try:
        decision = DecisionRecord(
            schema_version=fields["schema_version"], episode_id=fields["episode_id"],
            decision_index=fields["decision_index"], actor_seat=fields["actor_seat"], turn_index=fields["turn_index"],
            phase=fields["phase"], actor_information_view=fields.get("actor_information_view"),
            legal_action_keys=tuple(fields["legal_action_keys"]) if fields.get("legal_action_keys") is not None else None,
            chosen_action_key=fields.get("chosen_action_key"), chosen_action_raw=fields.get("chosen_action_raw"),
            public_cards_seen=tuple(fields.get("public_cards_seen", ())), board_summary=fields.get("board_summary"),
            latency_us=fields.get("latency_us"), fallback_used=fields["fallback_used"],
            result_to_go=fields.get("result_to_go"), source_quality=fields["source_quality"],
        )
    except (KeyError, ContractError) as exc:
        raise PipelineError(f"corrupt normalized decision record: {exc}") from exc
    expected = payload.get("content_hash")
    if expected is not None and expected != decision.content_hash():
        raise PipelineError(f"decision ({decision.episode_id}, {decision.decision_index}) content_hash mismatch on load")
    return decision


def _write_jsonl(path: Path, payloads: Iterable[Mapping[str, Any]]) -> None:
    lines = b"".join(canonical_json_bytes(dict(payload)) + b"\n" for payload in payloads)
    atomic_write_bytes(path, lines)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    results = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if stripped:
                results.append(json.loads(stripped))
    return results


# --------------------------------------------------------------------------- #
# normalize
# --------------------------------------------------------------------------- #


def run_normalize(run_root: str | Path, *, offline_training_run: str | Path, source_id: str) -> dict[str, Any]:
    """Discover + normalize an existing Offline Training run into this run's ``normalized/``."""
    paths = RunPaths(Path(run_root))
    discovered = discover_offline_training_run(offline_training_run)
    if not discovered.is_usable():
        raise PipelineError(f"no usable Offline Training collection found under {offline_training_run}")
    result = normalize_rule_bc_jsonl(discovered.collection_jsonl_path, source_id=source_id)

    _write_jsonl(paths.normalized / "episodes.jsonl", (_episode_to_payload(e) for e in result.episodes))
    _write_jsonl(paths.normalized / "decisions.jsonl", (_decision_to_payload(d) for d in result.decisions))
    atomic_write_json(
        paths.quarantine / "normalize_quarantine.json",
        {"schema_version": "normalize-quarantine-v1", "quarantined_rows": list(result.quarantined_rows)},
    )
    return {
        "source_row_count": result.source_row_count,
        "valid_row_count": result.valid_row_count,
        "quarantined_count": len(result.quarantined_rows),
        "episode_count": len(result.episodes),
        "decision_count": len(result.decisions),
        "normalizer_version": NORMALIZER_VERSION,
    }


def run_normalize_live_own(run_root: str | Path, *, source_run_root: str | Path) -> dict[str, Any]:
    """Normalize archived OWN_KAGGLE Replays into this separate derivative run.

    The source run remains read-only and raw Replay bytes are deliberately not
    copied.  Only actor-visible records, Rule-v0 relabelled examples, and
    existing SourceEnvelope manifests are written to ``run_root``.
    """
    try:
        return run_normalize_live_own_replays(source_run_root=source_run_root, output_run_root=run_root)
    except (KaggleReplayRunError, OSError, ValueError) as exc:
        raise PipelineError(str(exc)) from exc


def load_normalized_episodes(run_root: str | Path) -> list[EpisodeRecord]:
    paths = RunPaths(Path(run_root))
    return [_episode_from_payload(payload) for payload in _read_jsonl(paths.normalized / "episodes.jsonl")]


def load_normalized_decisions(run_root: str | Path) -> list[DecisionRecord]:
    paths = RunPaths(Path(run_root))
    return [_decision_from_payload(payload) for payload in _read_jsonl(paths.normalized / "decisions.jsonl")]


# --------------------------------------------------------------------------- #
# analyze
# --------------------------------------------------------------------------- #


def run_analyze(run_root: str | Path) -> dict[str, Any]:
    """Compute deck/policy/joint fingerprints, matchup stats, failure hypotheses, high-info selections."""
    paths = RunPaths(Path(run_root))
    episodes = load_normalized_episodes(run_root)
    decisions = load_normalized_decisions(run_root)
    if not episodes:
        raise PipelineError("no normalized episodes found; run `normalize` first")

    decisions_by_episode: dict[str, list[DecisionRecord]] = {}
    for decision in decisions:
        decisions_by_episode.setdefault(decision.episode_id, []).append(decision)

    deck_refs = sorted({e.deck_a_reference for e in episodes if e.deck_a_reference})
    deck_fingerprints = []
    for deck_ref in deck_refs:
        deck_decisions = [d for e in episodes if e.deck_a_reference == deck_ref for d in decisions_by_episode.get(e.episode_id, ())]
        fingerprint = build_deck_fingerprint(deck_decisions, deck_reference=deck_ref)
        payload = {
            "deck_reference": fingerprint.deck_reference, "observed_card_counts": {str(k): v for k, v in fingerprint.observed_card_counts.items()},
            "attack_usage": dict(fingerprint.attack_usage), "opening_sequence": list(fingerprint.opening_sequence),
            "first_attack_turn": fingerprint.first_attack_turn, "energy_attach_rate": fingerprint.energy_attach_rate,
            "sample_count": fingerprint.sample_count, "confidence": fingerprint.confidence,
            "missing_data_flags": sorted(fingerprint.missing_data_flags), "content_hash": fingerprint.content_hash(),
        }
        deck_fingerprints.append(payload)
    _write_jsonl(paths.derived / "deck_fingerprints.jsonl", deck_fingerprints)

    agent_refs = sorted({e.agent_a for e in episodes if e.agent_a})
    policy_fingerprints = []
    joint_fingerprints = []
    for agent_ref in agent_refs:
        agent_decisions = [d for e in episodes if e.agent_a == agent_ref for d in decisions_by_episode.get(e.episode_id, ())]
        policy = build_policy_fingerprint(agent_decisions, agent_reference=agent_ref)
        policy_fingerprints.append({
            "agent_reference": policy.agent_reference, "macro_distribution": dict(policy.macro_distribution),
            "attack_usage": dict(policy.attack_usage), "first_attack_turn_mean": policy.first_attack_turn_mean,
            "sample_count": policy.sample_count, "confidence": policy.confidence,
            "missing_data_flags": sorted(policy.missing_data_flags), "content_hash": policy.content_hash(),
        })
        for deck_ref in deck_refs:
            pair_decisions = [
                d for e in episodes if e.agent_a == agent_ref and e.deck_a_reference == deck_ref
                for d in decisions_by_episode.get(e.episode_id, ())
            ]
            if not pair_decisions:
                continue
            joint = build_joint_fingerprint(pair_decisions, deck_reference=deck_ref, agent_reference=agent_ref)
            joint_fingerprints.append({
                "joint_id": joint.joint_id, "deck_reference": joint.deck_reference, "agent_reference": joint.agent_reference,
                "macro_distribution": dict(joint.macro_distribution), "attack_usage": dict(joint.attack_usage),
                "sample_count": joint.sample_count, "confidence": joint.confidence,
                "missing_data_flags": sorted(joint.missing_data_flags), "independence_caveat": joint.independence_caveat,
            })
    _write_jsonl(paths.derived / "policy_fingerprints.jsonl", policy_fingerprints)
    _write_jsonl(paths.derived / "joint_fingerprints.jsonl", joint_fingerprints)

    matchup_results = aggregate_matchup_statistics(episodes)
    atomic_write_json(
        paths.derived / "matchup_statistics.json",
        {
            "schema_version": "matchup-statistics-collection-v1",
            "groups": [
                {"own_agent": key[0], "opponent_agent": key[1],
                 "games": stats.games, "wins": stats.wins, "losses": stats.losses, "draws": stats.draws,
                 "unknown_result_count": stats.unknown_result_count, "win_rate": stats.win_rate,
                 "wilson_interval": list(stats.wilson_interval) if stats.wilson_interval else None,
                 "effective_sample_size": stats.effective_sample_size, "confidence": stats.confidence,
                 "content_hash": stats.content_hash()}
                for key, stats in matchup_results.items()
            ],
        },
    )

    failure_hypotheses = []
    for episode in episodes:
        for hypothesis in generate_failure_hypotheses(episode, decisions_by_episode.get(episode.episode_id, ())):
            failure_hypotheses.append({
                "category": hypothesis.category, "confidence": hypothesis.confidence, "evidence": dict(hypothesis.evidence),
                "episode_id": hypothesis.episode_id, "decision_index_start": hypothesis.decision_index_start,
                "decision_index_end": hypothesis.decision_index_end, "phase": hypothesis.phase,
                "public_only": hypothesis.public_only, "oracle_only": hypothesis.oracle_only,
                "reason": hypothesis.reason, "limitations": hypothesis.limitations, "content_hash": hypothesis.content_hash(),
            })
    _write_jsonl(paths.derived / "failure_hypotheses.jsonl", failure_hypotheses)

    claims = list(latest_claims(run_root).values())
    selection_result = select_high_information_decisions(decisions, claims=claims)
    atomic_write_json(
        paths.derived / "high_information_selections.json",
        {
            "schema_version": "high-information-selections-v1",
            "unavailable_selectors": selection_result["unavailable_selectors"],
            "knowledge_claims_supplied": selection_result["knowledge_claims_supplied"],
            "selections": {
                selector: [
                    {"episode_id": s.episode_id, "decision_index": s.decision_index, "reason": s.reason, "evidence": dict(s.evidence)}
                    for s in selections
                ]
                for selector, selections in selection_result["selections"].items()
            },
        },
    )

    return {
        "deck_fingerprint_count": len(deck_fingerprints), "policy_fingerprint_count": len(policy_fingerprints),
        "joint_fingerprint_count": len(joint_fingerprints), "matchup_group_count": len(matchup_results),
        "failure_hypothesis_count": len(failure_hypotheses),
        "high_information_selector_count": len(selection_result["selections"]),
    }


# --------------------------------------------------------------------------- #
# import-knowledge / build-knowledge-snapshot
# --------------------------------------------------------------------------- #


def run_archive_note(
    run_root: str | Path, *, text: str, source_id: str, acquired_at: str, origin_reference: str,
    allowed_uses: Sequence[str] = ("ARCHIVE", "ANALYSIS", "REPORTING"),
) -> dict[str, Any]:
    envelope = archive_raw_note(
        run_root, text, source_id=source_id, acquired_at=acquired_at, origin_reference=origin_reference,
        allowed_uses=allowed_uses,
    )
    return {"source_id": envelope.source_id, "raw_sha256": envelope.raw_sha256, "allowed_uses": sorted(u.value for u in envelope.allowed_uses)}


def run_import_knowledge(run_root: str | Path, *, bundle_path: str | Path, raw_source_id: str, created_at: str) -> dict[str, Any]:
    from .knowledge_registry import import_claims

    paths = RunPaths(Path(run_root))
    try:
        envelope = read_source_manifest(paths.root, raw_source_id)
    except OSError as exc:
        raise PipelineError(
            f"no archived source manifest found for raw_source_id {raw_source_id!r}; "
            "archive it first (e.g. `archive-note`) before importing claims derived from it"
        ) from exc
    if not has_permission(envelope, AllowedUse.ANALYSIS):
        raise PipelineError(f"source {raw_source_id!r} does not grant ANALYSIS; cannot import knowledge claims derived from it")

    claims = import_claim_bundle(bundle_path, raw_source_id=raw_source_id, created_at=created_at)
    result = import_claims(run_root, claims)
    return {
        "imported_claim_count": len(result.appended_claim_ids),
        "duplicate_skipped_count": len(result.duplicate_skipped_claim_ids),
        "duplicate_skipped_claim_ids": list(result.duplicate_skipped_claim_ids),
        "claim_ids": list(result.claim_ids),
    }


def run_build_knowledge_snapshot(run_root: str | Path, *, cutoff_time: str, created_at: str) -> dict[str, Any]:
    paths = RunPaths(Path(run_root))
    current = latest_claims(run_root)
    included: list[str] = []
    excluded: dict[str, str] = {}
    for claim_id, claim in current.items():
        if claim.created_at > cutoff_time:
            excluded[claim_id] = "claim_created_after_cutoff"
        else:
            included.append(claim_id)

    included_claims = [current[claim_id] for claim_id in included]
    contradictions = detect_contradictions(included_claims)

    lifecycle_summary: dict[str, int] = {}
    evidence_summary: dict[str, int] = {}
    evidence_basis_summary: dict[str, int] = {}
    source_hashes: dict[str, str] = {}
    for claim in included_claims:
        lifecycle_summary[claim.status.value] = lifecycle_summary.get(claim.status.value, 0) + 1
        evidence_summary[claim.evidence_grade.value] = evidence_summary.get(claim.evidence_grade.value, 0) + 1
        evidence_basis_summary[claim.evidence_basis.value] = evidence_basis_summary.get(claim.evidence_basis.value, 0) + 1
        source_hashes.setdefault(claim.raw_source_id, claim.content_hash())

    # Permission summary: how many distinct raw_source_ids referenced by the
    # included claims grant each AllowedUse, per their archived SourceEnvelope
    # (re-derived here, not cached on the claim itself, so a source's actual
    # current permission is always what gates it, never a stale copy).
    permissions_summary: dict[str, int] = {}
    for source_id in sorted({claim.raw_source_id for claim in included_claims}):
        try:
            source_envelope = read_source_manifest(paths.root, source_id)
        except OSError:
            permissions_summary["_source_manifest_missing"] = permissions_summary.get("_source_manifest_missing", 0) + 1
            continue
        for use in source_envelope.allowed_uses:
            permissions_summary[use.value] = permissions_summary.get(use.value, 0) + 1

    snapshot = build_knowledge_snapshot(
        created_at=created_at, cutoff_time=cutoff_time, included_claim_ids=tuple(sorted(included)),
        excluded_claims=excluded, source_hashes=source_hashes,
        permissions_summary=permissions_summary, lifecycle_summary=lifecycle_summary, evidence_grade_summary=evidence_summary,
        evidence_basis_summary=evidence_basis_summary,
        contradiction_count=len(contradictions), normalizer_versions={"claim_bundle": "claim-bundle-v1"},
    )
    snapshot_dir = paths.snapshots / snapshot.snapshot_id
    atomic_write_json(
        snapshot_dir / "manifest.json",
        {**snapshot.content_payload(), "snapshot_id": snapshot.snapshot_id, "snapshot_sha256": snapshot.snapshot_sha256},
    )
    atomic_write_json(
        snapshot_dir / "contradictions.json",
        {"contradictions": [
            {"contradiction_id": c.contradiction_id, "claim_id_a": c.claim_id_a, "claim_id_b": c.claim_id_b,
             "overlap_reason": c.overlap_reason, "scope_overlap": dict(c.scope_overlap),
             "confidence": c.confidence, "content_hash": c.content_hash()}
            for c in contradictions
        ]},
    )
    return {"snapshot_id": snapshot.snapshot_id, "snapshot_sha256": snapshot.snapshot_sha256,
            "included_claim_count": len(included), "excluded_claim_count": len(excluded),
            "contradiction_count": len(contradictions)}


# --------------------------------------------------------------------------- #
# build-snapshot
# --------------------------------------------------------------------------- #


def run_build_snapshot(
    run_root: str | Path, *, cutoff_time: str, created_at: str, base_commit: str, seed: int = 0,
    require_cutoff: bool = False, knowledge_snapshot_hash: str | None = None,
    temporal_buckets: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Build an Intelligence Snapshot from this run's normalized episodes/decisions.

    ``temporal_buckets`` (``episode_id -> bucket label``) is passed straight
    through to the composite group split. It is never auto-derived here: a
    caller with real acquisition timestamps should compute real buckets from
    them; fabricating a fake "temporal" signal from e.g. episode insertion
    order would misrepresent what the grouping is actually based on. Without
    it, sources whose opponent/deck/agent identity is constant across every
    episode (e.g. pure self-play) may have too few distinct composite groups
    to split at all -- ``build_snapshot`` then reports that via
    ``split_policy`` rather than silently leaking groups.
    """
    paths = RunPaths(Path(run_root))
    episodes = load_normalized_episodes(run_root)
    decisions = load_normalized_decisions(run_root)
    if not episodes:
        raise PipelineError("no normalized episodes found; run `normalize` first")

    source_ids = sorted({e.source_id for e in episodes})
    envelopes = []
    for source_id in source_ids:
        try:
            envelopes.append(read_source_manifest(paths.root, source_id))
        except OSError as exc:
            raise PipelineError(f"missing source manifest for {source_id!r}: {exc}") from exc

    result = _build_intelligence_snapshot(
        episodes=episodes, decisions=decisions, source_envelopes=envelopes, cutoff_time=cutoff_time,
        base_commit=base_commit, created_at=created_at, seed=seed, require_cutoff=require_cutoff,
        knowledge_snapshot_hash=knowledge_snapshot_hash, temporal_buckets=temporal_buckets,
        normalizer_versions={"replay": NORMALIZER_VERSION}, analysis_versions={},
    )
    snapshot_dir = paths.snapshots / result.snapshot.snapshot_id
    atomic_write_json(
        snapshot_dir / "manifest.json",
        {**result.snapshot.content_payload(), "snapshot_id": result.snapshot.snapshot_id, "snapshot_sha256": result.snapshot.snapshot_sha256},
    )
    if result.split is not None:
        atomic_write_json(snapshot_dir / "split_assignment.json", {
            "train_episode_ids": list(result.split.train_episode_ids),
            "validation_episode_ids": list(result.split.validation_episode_ids),
            "test_episode_ids": list(result.split.test_episode_ids),
            "manifest": dict(result.split.manifest),
        })
    if result.leakage_audit is not None:
        atomic_write_json(snapshot_dir / "leakage_audit.json", result.leakage_audit.to_dict())
    atomic_write_json(snapshot_dir / "excluded_episodes.json", dict(result.excluded_episode_reasons))
    atomic_write_json(snapshot_dir / "component_diagnostics.json", dict(result.component_diagnostics))
    # O4 keeps DeckObservation references in a companion artifact because the
    # frozen IntelligenceSnapshot contract intentionally has no deck field.
    # The companion contains only content hashes/summary counts, never deck
    # ordering or raw Replay data, and therefore cannot change snapshot
    # identity or become a policy input.
    deck_rows = _read_jsonl(paths.normalized / "deck_observations.jsonl")
    o4_report_path = paths.reports / "o4_replay_normalization_report.json"
    if deck_rows or o4_report_path.exists():
        o4_report = json.loads(o4_report_path.read_text(encoding="utf-8")) if o4_report_path.exists() else {}
        atomic_write_json(snapshot_dir / "o4_own_data_companion.json", {
            "schema_version": "o4-own-data-snapshot-companion-v1",
            "deck_observation_content_hashes": sorted(
                str(row["content_hash"]) for row in deck_rows if isinstance(row.get("content_hash"), str)
            ),
            "deck_observation_count": len(deck_rows),
            "identity_unresolved_included": 0,
            "public_other_training_count": 0,
            "privacy_violation_count": o4_report.get("privacy_violation_count"),
            "actor_visibility_violation_count": o4_report.get("actor_visibility_violation_count"),
            "schema_variants": o4_report.get("schema_variants", {}),
            "normalization_report_hash": digest(o4_report, domain="o4-normalization-report-reference-v1") if o4_report else None,
        })

    return {
        "snapshot_id": result.snapshot.snapshot_id, "snapshot_sha256": result.snapshot.snapshot_sha256,
        "episode_count": result.snapshot.episode_count, "decision_count": result.snapshot.decision_count,
        "excluded_episode_count": len(result.excluded_episode_reasons),
        "leakage_audit_passed": result.leakage_audit.passed if result.leakage_audit else None,
        "component_diagnostics": dict(result.component_diagnostics),
    }


# --------------------------------------------------------------------------- #
# export-offline-dataset
# --------------------------------------------------------------------------- #


def run_export_offline_dataset(
    run_root: str | Path, *, snapshot_id: str, offline_training_run: str | Path, output_path: str | Path, split: str = "train",
) -> dict[str, Any]:
    paths = RunPaths(Path(run_root))
    split_path = paths.snapshots / snapshot_id / "split_assignment.json"
    if not split_path.exists():
        raise PipelineError(f"no split_assignment.json found for snapshot {snapshot_id!r}")
    split_assignment = json.loads(split_path.read_text(encoding="utf-8"))
    key = f"{split}_episode_ids"
    if key not in split_assignment:
        raise PipelineError(f"unknown split {split!r}; expected one of train/validation/test")
    selected_ids = split_assignment[key]

    episodes = {e.episode_id: e for e in load_normalized_episodes(run_root)}
    selected_source_ids = sorted({episodes[eid].source_id for eid in selected_ids if eid in episodes})
    envelopes = [read_source_manifest(paths.root, source_id) for source_id in selected_source_ids]
    enforce_training_permission(envelopes)

    discovered = discover_offline_training_run(offline_training_run)
    if not discovered.is_usable():
        raise PipelineError(f"no usable Offline Training collection found under {offline_training_run}")
    counts = export_selected_rows(discovered.collection_jsonl_path, output_path, selected_episode_ids=selected_ids)
    return {"snapshot_id": snapshot_id, "split": split, "selected_episode_count": len(selected_ids), **counts}


# --------------------------------------------------------------------------- #
# materialize-dataset
# --------------------------------------------------------------------------- #


def run_materialize_dataset(
    run_root: str | Path,
    *,
    offline_training_run: str | Path,
    created_at: str,
    sources: str = "both",
    baseline: bool = False,
    snapshot_id: str | None = None,
    split: str = "train",
    knowledge_snapshot_id: str | None = None,
    training_policy: str | None = None,
) -> dict[str, Any]:
    from .dataset_materialization import DEFAULT_TRAINING_POLICY, DatasetMaterializationError, materialize_dataset
    from .knowledge_registry import latest_claims

    effective_training_policy = training_policy or DEFAULT_TRAINING_POLICY

    if baseline and sources != "replay":
        raise DatasetMaterializationError("baseline=True reproduces the pre-O1 replay-only dataset; sources must be 'replay'")

    paths = RunPaths(Path(run_root))
    needs_replay = sources in ("replay", "both") and not baseline
    episodes = load_normalized_episodes(run_root) if needs_replay else []
    decisions = load_normalized_decisions(run_root) if needs_replay else []

    knowledge_claims: list[KnowledgeClaim] = []
    if sources in ("knowledge", "both"):
        if not knowledge_snapshot_id:
            raise PipelineError("knowledge_snapshot_id is required when sources includes 'knowledge'")
        manifest_path = paths.snapshots / knowledge_snapshot_id / "manifest.json"
        if not manifest_path.exists():
            raise PipelineError(f"no Knowledge Snapshot manifest found for {knowledge_snapshot_id!r}")
        knowledge_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        included_ids = frozenset(knowledge_manifest["included_claim_ids"])
        knowledge_claims = [claim for claim_id, claim in latest_claims(run_root).items() if claim_id in included_ids]

    result = materialize_dataset(
        run_root, offline_training_run=offline_training_run, created_at=created_at,
        episodes=episodes, decisions=decisions, knowledge_claims=knowledge_claims,
        sources=sources, baseline=baseline, snapshot_id=snapshot_id, split=split,
        knowledge_snapshot_id=knowledge_snapshot_id, training_policy=effective_training_policy,
    )
    return {
        "dataset_id": result.dataset_id, "dataset_hash": result.dataset_hash, "output_dir": str(result.output_dir),
        "audit_report": dict(result.audit_report), "statistics_report": dict(result.statistics_report),
    }


# --------------------------------------------------------------------------- #
# probe-external / ingest-kaggle / ingest-team / ingest-public / schema-report
# --------------------------------------------------------------------------- #

CAPABILITY_REPORTS_DIRNAME = "capability_reports"


def _build_transport(
    mode: str, *, fixture_path: str | Path | None = None, recordings_dir: str | Path | None = None
) -> ExternalTransport:
    """Select an ``ExternalTransport`` by name. ``"live"`` is opt-in only;
    every other mode requires zero credentials and touches no network.
    """
    if mode == "unavailable":
        return UnavailableTransport()
    if mode == "fixture":
        if fixture_path is None:
            raise PipelineError("mode=fixture requires --fixture-file")
        raw = json.loads(Path(fixture_path).read_text(encoding="utf-8"))
        if not isinstance(raw, Mapping):
            raise PipelineError("fixture file must be a JSON object keyed by action")
        responses: dict[str, ExternalRawResponse] = {}
        for action, entry in raw.items():
            if not isinstance(entry, Mapping):
                raise PipelineError(f"fixture entry for action {action!r} must be an object")
            body_value = entry.get("body")
            body = json.dumps(body_value, sort_keys=True).encode("utf-8") if body_value is not None else b""
            success = bool(entry.get("success", False))
            responses[action] = ExternalRawResponse(
                action=action, target="fixture", success=success, body=body,
                content_type=entry.get("content_type") or ("application/json" if body_value is not None else None),
                error_type=entry.get("error_type") or (None if success else "unknown_error"),
                error_message=entry.get("error_message"), client_name="fixture-file",
                client_version=entry.get("client_version"),
            )
        return FixtureTransport(responses=responses)
    if mode == "recorded":
        if recordings_dir is None:
            raise PipelineError("mode=recorded requires --recordings-dir")
        return RecordedResponseTransport(recordings_dir)
    if mode == "live":
        return SubprocessKaggleTransport()
    raise PipelineError(f"unknown transport mode {mode!r}; expected unavailable/fixture/recorded/live")


def _capability_report_path(run_root: Path, report_id: str) -> Path:
    return RunPaths(run_root).derived / CAPABILITY_REPORTS_DIRNAME / f"{report_id}.json"


def _persist_capability_report(run_root: Path, report: CapabilityReport) -> Path:
    path = _capability_report_path(run_root, report.capability_report_id)
    atomic_write_json(path, {**report.content_payload(), "capability_report_id": report.capability_report_id})
    return path


def run_probe_external(
    run_root: str | Path, *, target: str, mode: str = "unavailable",
    fixture_path: str | Path | None = None, recordings_dir: str | Path | None = None,
    timeout: float = 20.0, tested_at: str | None = None,
) -> dict[str, Any]:
    """Run the full O1-5 capability probe against ``target`` and persist the report.

    ``mode="unavailable"`` (the default) never attempts any real probing, so
    a caller must explicitly opt into ``fixture``/``recorded``/``live`` --
    live mode stays disabled unless requested.
    """
    root = Path(run_root)
    transport = _build_transport(mode, fixture_path=fixture_path, recordings_dir=recordings_dir)
    report = probe_capability(transport, target=target, timeout=timeout, tested_at=tested_at)
    path = _persist_capability_report(root, report)
    return {
        "capability_report_id": report.capability_report_id,
        "capability_mode": report.capability_mode.value,
        "mode_classification_reasons": list(report.mode_classification_reasons),
        "failure_categories": list(report.failure_categories),
        "authentication_available": report.authentication_available,
        "capability_detail": {
            "can_list_leaderboard": report.leaderboard_available,
            "can_list_public_submissions": None,
            "can_list_own_submissions": report.own_submission_listing_available,
            "can_list_own_episodes": report.episode_listing_available,
            "can_resolve_public_episode": None,
            "can_download_public_replay": None,
            "can_download_own_replay": report.replay_available,
            "authentication_status": report.authentication_available,
            "structured_request_requirement": "replay requires episode_id; own_logs requires episode_id and agent_index",
            "rate_limit_status": dict(report.rate_limit_info) if report.rate_limit_info else None,
        },
        "report_path": path.relative_to(root).as_posix(),
    }


def _ingest_external(
    run_root: str | Path, *, action: str, target: str, source_kind: str, allowed_uses: Iterable[str],
    mode: str, fixture_path: str | Path | None, recordings_dir: str | Path | None, timeout: float,
    owner_scope: str = "self", visibility: str = "private",
) -> dict[str, Any]:
    root = Path(run_root)
    transport = _build_transport(mode, fixture_path=fixture_path, recordings_dir=recordings_dir)
    report = probe_capability(transport, target=target, timeout=timeout, actions=(action,))
    _persist_capability_report(root, report)
    outcome = acquire_external_artifact(
        root, transport, action=action, target=target, capability_report=report, source_kind=source_kind,
        allowed_uses=allowed_uses, owner_scope=owner_scope, visibility=visibility,
    )
    return {**outcome.as_dict(), "capability_mode": report.capability_mode.value}


def run_ingest_kaggle(
    run_root: str | Path, *, action: str, target: str, allowed_uses: Iterable[str],
    mode: str = "unavailable", fixture_path: str | Path | None = None, recordings_dir: str | Path | None = None,
    timeout: float = 20.0,
) -> dict[str, Any]:
    """Ingest one own-Kaggle artifact (submissions/episodes/replay/logs)."""
    if action not in EXTERNAL_ACTIONS:
        raise PipelineError(f"unknown action {action!r}; expected one of {sorted(EXTERNAL_ACTIONS)}")
    return _ingest_external(
        run_root, action=action, target=target, source_kind="OWN_KAGGLE", allowed_uses=allowed_uses,
        mode=mode, fixture_path=fixture_path, recordings_dir=recordings_dir, timeout=timeout,
    )


def run_ingest_public(
    run_root: str | Path, *, action: str, target: str, allowed_uses: Iterable[str],
    mode: str = "unavailable", fixture_path: str | Path | None = None, recordings_dir: str | Path | None = None,
    timeout: float = 20.0,
) -> dict[str, Any]:
    """Ingest one PUBLIC_OTHER artifact (public files/logs/leaderboard).

    ``allowed_uses`` is validated the same way any ``SourceEnvelope`` is:
    ``PUBLIC_OTHER`` can never be granted ``TRAINING``/``REDISTRIBUTION``
    regardless of what the caller passes here (enforced unconditionally in
    ``contracts.SourceEnvelope.__post_init__``, not re-implemented here).
    """
    if action not in EXTERNAL_ACTIONS:
        raise PipelineError(f"unknown action {action!r}; expected one of {sorted(EXTERNAL_ACTIONS)}")
    return _ingest_external(
        run_root, action=action, target=target, source_kind="PUBLIC_OTHER",
        allowed_uses=allowed_uses, mode=mode, fixture_path=fixture_path, recordings_dir=recordings_dir,
        timeout=timeout, visibility="public",
    )


def run_ingest_team(
    run_root: str | Path, *, bundle_root: str | Path, cli_requested_uses: Iterable[str] | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    outcome = import_team_bundle(bundle_root, run_root, cli_requested_uses=cli_requested_uses, created_at=created_at)
    return outcome.as_dict()


def run_schema_report(run_root: str | Path, *, source_kind: str, action: str) -> dict[str, Any]:
    """Report a value-free trusted schema baseline without new acquisition."""
    from .external_acquisition import _baseline_path  # local: internal path helper, not part of the public API

    root = Path(run_root)
    from .contracts import SourceKind

    kind = SourceKind(source_kind)
    path = _baseline_path(root, kind, action)
    has_baseline = path.is_file()
    baseline = json.loads(path.read_text(encoding="utf-8")) if has_baseline else None
    fingerprint = baseline.get("fingerprint") if isinstance(baseline, Mapping) else None
    return {
        "source_kind": kind.value,
        "action": action,
        "has_recorded_baseline": has_baseline,
        "baseline_fingerprint": fingerprint,
        "baseline_trust": baseline.get("trust") if isinstance(baseline, Mapping) else None,
    }


# --------------------------------------------------------------------------- #
# O1-6 meta / surrogate / benchmark / non-authoritative report
# --------------------------------------------------------------------------- #


def _episode_observations(run_root: str | Path, *, cutoff_time: str) -> list[WeightedStrategyObservation]:
    paths = RunPaths(Path(run_root))
    observations: list[WeightedStrategyObservation] = []
    for episode in load_normalized_episodes(run_root):
        envelope = read_source_manifest(paths.root, episode.source_id)
        permission = ANALYSIS_GRANTED if any(use.value == "ANALYSIS" for use in envelope.allowed_uses) else "ANALYSIS_DENIED"
        # agent_b is an observed label in normalized local records, not a
        # causal deck/policy decomposition. Missing labels remain unknown.
        posterior = {episode.agent_b: 1.0} if episode.agent_b else {}
        observations.append(WeightedStrategyObservation(
            observation_id=f"observation:{episode.episode_id}", source_id=episode.source_id,
            source_kind=envelope.source_kind.value, episode_id=episode.episode_id, joint_fingerprint_id=None,
            archetype_posterior=posterior, unknown_mass=0.0 if posterior else 1.0,
            timestamp=episode.played_at or cutoff_time, source_weight=1.0, freshness_weight=1.0,
            duplicate_discount=1.0, confidence=1.0, population_bucket=None, lineage_version_group=episode.engine_version,
            permission_status=permission, analysis_version="o1-6-v1",
        ))
    return observations


def run_build_meta_snapshot(run_root: str | Path, *, cutoff_time: str, prior: Mapping[str, float] | None = None) -> dict[str, Any]:
    paths = RunPaths(Path(run_root))
    snapshot = build_meta_snapshot(_episode_observations(run_root, cutoff_time=cutoff_time), cutoff_time=cutoff_time, prior=prior)
    atomic_write_json(paths.snapshots / snapshot.meta_snapshot_id / "meta_manifest.json", {
        **snapshot.content_payload(), "meta_snapshot_id": snapshot.meta_snapshot_id, "meta_snapshot_sha256": snapshot.meta_snapshot_sha256,
    })
    return {"meta_snapshot_id": snapshot.meta_snapshot_id, "meta_snapshot_sha256": snapshot.meta_snapshot_sha256,
            "effective_sample_size": snapshot.effective_sample_size, "included_count": len(snapshot.included_observation_ids),
            "excluded_count": len(snapshot.excluded_observation_ids)}


def _read_meta_snapshot(run_root: str | Path, meta_snapshot_id: str) -> dict[str, Any]:
    path = RunPaths(Path(run_root)).snapshots / meta_snapshot_id / "meta_manifest.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise PipelineError(f"missing meta snapshot {meta_snapshot_id!r}") from exc


def run_drift_report(run_root: str | Path, *, previous_meta_snapshot_id: str, current_meta_snapshot_id: str) -> dict[str, Any]:
    from .meta import MetaSnapshot
    def load(snapshot_id: str) -> MetaSnapshot:
        payload = _read_meta_snapshot(run_root, snapshot_id)
        return MetaSnapshot(cutoff_time=payload["cutoff_time"], prior=payload["prior"], posterior_mean=payload["posterior_mean"],
                            intervals={key: tuple(value) for key, value in payload["intervals"].items()}, scenarios=payload["scenarios"],
                            effective_sample_size=payload["effective_sample_size"], source_composition=payload["source_composition"],
                            included_observation_ids=tuple(payload["included_observation_ids"]), excluded_observation_ids=payload["excluded_observation_ids"],
                            meta_snapshot_id=payload["meta_snapshot_id"], meta_snapshot_sha256=payload["meta_snapshot_sha256"])
    report = detect_drift(load(previous_meta_snapshot_id), load(current_meta_snapshot_id))
    report["content_hash"] = digest(report, domain="meta-drift-report")
    atomic_write_json(RunPaths(Path(run_root)).reports / f"drift-{current_meta_snapshot_id}.json", report)
    return report


def run_build_surrogate(run_root: str | Path, *, cutoff_time: str) -> dict[str, Any]:
    paths = RunPaths(Path(run_root))
    episodes = {episode.episode_id: episode for episode in load_normalized_episodes(run_root)}
    observations: list[SurrogateObservation] = []
    for decision in load_normalized_decisions(run_root):
        if not decision.chosen_action_key or decision.episode_id not in episodes:
            continue
        episode = episodes[decision.episode_id]
        envelope = read_source_manifest(paths.root, episode.source_id)
        observations.append(SurrogateObservation(
            source_id=episode.source_id, timestamp=episode.played_at or cutoff_time, action_key=decision.chosen_action_key,
            context={"phase": decision.phase, "action_category": decision.chosen_action_key.split(":", 1)[0],
                     "seat": str(decision.actor_seat), "matchup_bucket": episode.agent_b or "unknown"},
            permission_status=ANALYSIS_GRANTED if any(use.value == "ANALYSIS" for use in envelope.allowed_uses) else "ANALYSIS_DENIED",
            actor_visible=True,
        ))
    surrogate = build_opponent_surrogate(observations, cutoff_time=cutoff_time)
    artifact = {"schema_version": "opponent-surrogate-artifact-v1", "artifact_id": surrogate.artifact_id,
                "content_hash": surrogate.content_hash, "cutoff_time": cutoff_time, "actions": list(surrogate.actions),
                "source_ids": list(surrogate.source_ids), "minimum_support": surrogate.minimum_support,
                "laplace": surrogate.laplace, "entropy_floor": surrogate.entropy_floor, "training_route": "forbidden"}
    atomic_write_json(paths.derived / f"{surrogate.artifact_id}.json", artifact)
    return {"artifact_id": surrogate.artifact_id, "content_hash": surrogate.content_hash, "observation_count": len(observations),
            "action_count": len(surrogate.actions), "student_training_route": "forbidden"}


def run_build_benchmarks(run_root: str | Path, *, meta_snapshot_hash: str, surrogate_version: str, seeds: Sequence[int] = (0, 1)) -> dict[str, Any]:
    paths = RunPaths(Path(run_root))
    episodes = load_normalized_episodes(run_root)
    manifest = build_benchmark_manifest("fixed", snapshot_hashes=[meta_snapshot_hash], episode_ids=[e.episode_id for e in episodes],
                                        opponents=[e.agent_b or "unknown" for e in episodes], seeds=seeds,
                                        evaluation_config={"mode": "fixture_only", "auto_train": False, "auto_submit": False},
                                        unknown_meta_allocation=0.0, surrogate_versions=[surrogate_version])
    rolling = build_benchmark_manifest("rolling", snapshot_hashes=[meta_snapshot_hash], episode_ids=[e.episode_id for e in episodes],
                                       opponents=[e.agent_b or "unknown" for e in episodes], seeds=seeds,
                                       evaluation_config={"mode": "fixture_only", "auto_train": False, "auto_submit": False},
                                       unknown_meta_allocation=0.0, surrogate_versions=[surrogate_version])
    for value in (manifest, rolling):
        atomic_write_json(paths.derived / f"{value.manifest_id}.json", {**value.payload(), "manifest_id": value.manifest_id, "content_hash": value.content_hash})
    return {"fixed_manifest_id": manifest.manifest_id, "fixed_manifest_hash": manifest.content_hash,
            "rolling_manifest_id": rolling.manifest_id, "rolling_manifest_hash": rolling.content_hash}


def run_intelligence_cycle(run_root: str | Path, *, offline_training_run: str | Path, source_id: str, cutoff_time: str,
                           created_at: str, base_commit: str, seed: int = 0) -> dict[str, Any]:
    """One-shot, resumable O1-6 cycle. It never trains, promotes, or submits."""
    paths = RunPaths(Path(run_root))
    state_path = paths.state / "intelligence_cycle.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {"stages": {}}
    def stage(name: str, callback: Any) -> Any:
        if name in state["stages"]:
            return state["stages"][name]
        value = callback()
        state["stages"][name] = value
        atomic_write_json(state_path, state)
        return value
    stage("normalize", lambda: run_normalize(run_root, offline_training_run=offline_training_run, source_id=source_id))
    stage("analyze", lambda: run_analyze(run_root))
    intelligence = stage("intelligence_snapshot", lambda: run_build_snapshot(run_root, cutoff_time=cutoff_time, created_at=created_at,
        base_commit=base_commit, seed=seed, require_cutoff=False))
    meta = stage("meta_snapshot", lambda: run_build_meta_snapshot(run_root, cutoff_time=cutoff_time))
    stage("drift", lambda: run_drift_report(
        run_root, previous_meta_snapshot_id=meta["meta_snapshot_id"], current_meta_snapshot_id=meta["meta_snapshot_id"]
    ))
    surrogate = stage("surrogate", lambda: run_build_surrogate(run_root, cutoff_time=cutoff_time))
    benchmarks = stage("benchmarks", lambda: run_build_benchmarks(run_root, meta_snapshot_hash=meta["meta_snapshot_sha256"], surrogate_version=surrogate["content_hash"]))
    def build_report() -> dict[str, Any]:
        report = build_promotion_report(decision="INSUFFICIENT_EVIDENCE", meta_snapshot_hash=meta["meta_snapshot_sha256"],
            benchmark_hashes=[benchmarks["fixed_manifest_hash"], benchmarks["rolling_manifest_hash"]],
            evidence={"intelligence_snapshot_id": intelligence["snapshot_id"], "reason": "O1-6 report is non-authoritative"})
        atomic_write_json(paths.reports / f"{report['report_id']}.json", report)
        return report
    stage("promotion_report", build_report)
    return {"stages": state["stages"], "auto_training": False, "auto_promotion": False, "auto_submit": False}


# --------------------------------------------------------------------------- #
# report
# --------------------------------------------------------------------------- #


def run_report(run_root: str | Path) -> dict[str, Any]:
    paths = RunPaths(Path(run_root))
    manifest_path = paths.manifest
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else None

    source_count = len(list(paths.source_manifests.glob("*.json"))) if paths.source_manifests.exists() else 0
    episodes = _read_jsonl(paths.normalized / "episodes.jsonl")
    decisions = _read_jsonl(paths.normalized / "decisions.jsonl")
    claim_versions = list(iter_claim_versions(run_root))
    latest = latest_claims(run_root)
    snapshot_dirs = sorted(p.name for p in paths.snapshots.glob("*") if p.is_dir()) if paths.snapshots.exists() else []

    report = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "run_id": manifest.get("run_id") if manifest else None,
        "source_count": source_count,
        "episode_count": len(episodes),
        "decision_count": len(decisions),
        "knowledge_claim_version_count": len(claim_versions),
        "knowledge_claim_latest_count": len(latest),
        "knowledge_claim_status_summary": _status_summary(latest.values()),
        "snapshot_ids": snapshot_dirs,
    }
    atomic_write_json(paths.reports / "report.json", report)
    return report


def _status_summary(claims: Iterable[KnowledgeClaim]) -> dict[str, int]:
    summary: dict[str, int] = {}
    for claim in claims:
        summary[claim.status.value] = summary.get(claim.status.value, 0) + 1
    return summary


__all__ = [
    "REPORT_SCHEMA_VERSION",
    "PipelineError",
    "load_normalized_decisions",
    "load_normalized_episodes",
    "run_analyze",
    "run_archive_note",
    "run_build_benchmarks",
    "run_build_knowledge_snapshot",
    "run_build_meta_snapshot",
    "run_build_snapshot",
    "run_build_surrogate",
    "run_drift_report",
    "run_export_offline_dataset",
    "run_import_knowledge",
    "run_ingest_kaggle",
    "run_ingest_public",
    "run_ingest_team",
    "run_intelligence_cycle",
    "run_materialize_dataset",
    "run_normalize",
    "run_probe_external",
    "run_report",
    "run_schema_report",
]
