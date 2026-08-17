"""Run-level, resumable OWN_KAGGLE Replay normalization.

This reads an already-acquired source run and writes only normalized,
privacy-redacted derivatives to a separate output run.  Raw Replay blobs are
never copied.  The source run is read-only throughout.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from . import archive
from .atomic_io import atomic_write_bytes, atomic_write_json
from .canonical import canonical_json_bytes, digest
from .contracts import DeckObservation, SourceKind
from .kaggle_replay_normalize import VerifiedEpisodeAgentMapping, normalize_kaggle_replay
from .live_payloads import normalize_live_payload, normalized_episode_records, normalized_submission_ids
from .participant_resolver import OwnSubmissionBootstrap, resolve_episode_agent_mapping
from .provenance import envelope_from_manifest_payload, write_source_manifest
from .runstate import RunPaths


class KaggleReplayRunError(ValueError):
    """The archived own-data sources cannot produce a verified replay run."""


def _load_envelopes(root: Path) -> list[object]:
    envelopes = []
    for path in sorted(RunPaths(root).source_manifests.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        envelopes.append(envelope_from_manifest_payload(payload))
    return envelopes


def _raw(root: Path, envelope: object) -> Any:
    return json.loads(archive.read_raw(root, envelope.raw_sha256).decode("utf-8"))


def _hash_identity(agent: Mapping[str, Any]) -> str:
    value = {
        "submission_id": agent.get("submission_id"),
        "team_id": agent.get("team_id"),
        "team_name": agent.get("team_name"),
        "agent_index": agent.get("agent_index"),
    }
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _episode_time(row: Mapping[str, Any]) -> str | None:
    for key in ("endTime", "EndTime", "createTime", "CreateTime"):
        value = row.get(key)
        if isinstance(value, str) and value and ("+" in value or value.endswith("Z")):
            return value
    return None


def _episode_mappings(source_root: Path, envelopes: list[object]) -> tuple[dict[str, list[VerifiedEpisodeAgentMapping]], dict[str, int]]:
    submission_ids: set[str] = set()
    listing_hashes: dict[str, str] = {}
    for envelope in envelopes:
        if envelope.source_kind is SourceKind.OWN_KAGGLE and envelope.metadata.get("action") == "own_submission_listing":
            payload = normalize_live_payload("own_submission_listing", archive.read_raw(source_root, envelope.raw_sha256)).payload
            for submission_id in normalized_submission_ids(payload):
                submission_ids.add(submission_id)
                listing_hashes[submission_id] = envelope.raw_sha256
    if not submission_ids:
        raise KaggleReplayRunError("SUBMISSION_MAPPING_MISSING: authenticated own submission listing unavailable")

    result: dict[str, list[VerifiedEpisodeAgentMapping]] = {}
    stats = {"episode_rows": 0, "mapping_verified": 0, "mapping_ambiguous_or_missing": 0}
    for envelope in envelopes:
        if envelope.source_kind is not SourceKind.OWN_KAGGLE or envelope.metadata.get("action") != "own_episode_listing":
            continue
        payload = normalize_live_payload("own_episode_listing", archive.read_raw(source_root, envelope.raw_sha256)).payload
        for row in normalized_episode_records(payload):
            stats["episode_rows"] += 1
            episode_id = row.get("_normalized_id")
            if not isinstance(episode_id, str):
                stats["mapping_ambiguous_or_missing"] += 1
                continue
            agents = row.get("_normalized_agents")
            if not isinstance(agents, list):
                stats["mapping_ambiguous_or_missing"] += 1
                continue
            candidates = []
            for submission_id in sorted(submission_ids):
                resolved = resolve_episode_agent_mapping(
                    row, OwnSubmissionBootstrap(submission_id, listing_hashes[submission_id])
                )
                if resolved.reason != "episode_submission_side_verified" or len(resolved.agent_indices) != 1 or resolved.identity is None:
                    continue
                own_index = resolved.agent_indices[0]
                if own_index not in (0, 1) or len(agents) != 2 or any(not isinstance(agent, Mapping) for agent in agents):
                    continue
                mapping_payload = {
                    "episode_id": episode_id,
                    "submission_id": submission_id,
                    "own_agent_index": own_index,
                    "episode_source_hash": envelope.raw_sha256,
                    "agents": [
                        {"submission_id": agent.get("submission_id"), "agent_index": agent.get("agent_index"),
                         "team_id": agent.get("team_id"), "team_name": agent.get("team_name"), "reward": agent.get("reward"), "state": agent.get("state")}
                        for agent in agents
                    ],
                }
                candidates.append(VerifiedEpisodeAgentMapping(
                    episode_id=episode_id,
                    submission_id=submission_id,
                    own_agent_index=own_index,
                    identity_hash=resolved.identity_hash or "0" * 64,
                    episode_mapping_hash=digest(mapping_payload, domain="o4-official-episode-agent-mapping-v1"),
                    played_at=_episode_time(row),
                    agent_identity_hashes=(_hash_identity(agents[0]), _hash_identity(agents[1])),
                ))
            unique = {item.episode_mapping_hash: item for item in candidates}
            if len(unique) == 1:
                result.setdefault(episode_id, []).append(next(iter(unique.values())))
                stats["mapping_verified"] += 1
            elif candidates:
                stats["mapping_ambiguous_or_missing"] += 1
    return result, stats


def _deduplicated_mapping(candidates: list[VerifiedEpisodeAgentMapping]) -> VerifiedEpisodeAgentMapping | None:
    unique = {item.episode_mapping_hash: item for item in candidates}
    return next(iter(unique.values())) if len(unique) == 1 else None


def _jsonl(path: Path, rows: list[Mapping[str, Any]]) -> None:
    atomic_write_bytes(path, b"".join(canonical_json_bytes(dict(row)) + b"\n" for row in rows))


def _deck_payload(item: DeckObservation) -> dict[str, Any]:
    payload = item.content_payload()
    payload["content_hash"] = item.content_hash()
    return payload


def run_normalize_live_own_replays(*, source_run_root: str | Path, output_run_root: str | Path) -> dict[str, Any]:
    """Normalize every verified OWN_KAGGLE Replay from a read-only source run."""
    source_root = Path(source_run_root)
    output_root = Path(output_run_root)
    source_paths = RunPaths(source_root)
    if not source_paths.source_manifests.exists():
        raise KaggleReplayRunError(f"source manifests missing under {source_root}")
    output_paths = RunPaths(output_root)
    output_paths.ensure_layout()
    envelopes = _load_envelopes(source_root)
    mappings_by_episode, mapping_stats = _episode_mappings(source_root, envelopes)
    replay_envelopes = [
        envelope for envelope in envelopes
        if envelope.source_kind is SourceKind.OWN_KAGGLE and envelope.metadata.get("action") == "replay"
    ]
    episodes = []
    decisions = []
    decks = []
    examples = []
    exclusions: list[Mapping[str, object]] = []
    quarantine: list[Mapping[str, object]] = []
    schema_variants: dict[str, int] = {}
    seen_content_by_episode: dict[str, str] = {}
    for envelope in replay_envelopes:
        try:
            raw_replay = _raw(source_root, envelope)
        except (archive.ArchiveError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            quarantine.append({"source_id": envelope.source_id, "reason": "SCHEMA_DRIFT", "detail": type(exc).__name__})
            continue
        if not isinstance(raw_replay, Mapping):
            quarantine.append({"source_id": envelope.source_id, "reason": "SCHEMA_DRIFT", "detail": "replay_not_object"})
            continue
        raw_episode_id = raw_replay.get("info", {}).get("EpisodeId") if isinstance(raw_replay.get("info"), Mapping) else None
        episode_id = str(raw_episode_id) if isinstance(raw_episode_id, (str, int)) else None
        if episode_id is None:
            quarantine.append({"source_id": envelope.source_id, "reason": "SCHEMA_DRIFT", "detail": "episode_id_missing"})
            continue
        previous_hash = seen_content_by_episode.get(episode_id)
        if previous_hash is not None and previous_hash != envelope.raw_sha256:
            quarantine.append({"source_id": envelope.source_id, "episode_id": episode_id, "reason": "REPLAY_CONTENT_CONFLICT"})
            continue
        seen_content_by_episode[episode_id] = envelope.raw_sha256
        mapping = _deduplicated_mapping(mappings_by_episode.get(episode_id, []))
        if mapping is None:
            quarantine.append({"source_id": envelope.source_id, "episode_id": episode_id, "reason": "SUBMISSION_MAPPING_MISSING"})
            continue
        result = normalize_kaggle_replay(raw_replay, mapping, envelope)
        schema_variants[result.schema_fingerprint] = schema_variants.get(result.schema_fingerprint, 0) + 1
        if result.quarantine_reason:
            quarantine.append({"source_id": envelope.source_id, "episode_id": episode_id, "reason": result.quarantine_reason})
            continue
        if result.episode is not None:
            episodes.append(result.episode)
            write_source_manifest(output_root, envelope)
        decisions.extend(result.decisions)
        decks.extend(result.deck_observations)
        examples.extend(result.training_examples)
        exclusions.extend({"episode_id": episode_id, **item} for item in result.excluded_decisions)

    _jsonl(output_paths.normalized / "episodes.jsonl", [{**item.content_payload(), "content_hash": item.content_hash()} for item in episodes])
    _jsonl(output_paths.normalized / "decisions.jsonl", [{**item.content_payload(), "content_hash": item.content_hash()} for item in decisions])
    _jsonl(output_paths.normalized / "deck_observations.jsonl", [_deck_payload(item) for item in decks])
    _jsonl(output_paths.normalized / "rule_bc_examples.jsonl", [item.to_dict() for item in examples])
    report = {
        "schema_version": "o4-live-own-replay-normalization-report-v1",
        "normalizer_version": "competition-intelligence-kaggle-replay-normalizer-v1",
        "source_run_hash": digest(
            sorted(envelope.content_hash() for envelope in replay_envelopes),
            domain="o4-own-kaggle-replay-source-set-v1",
        ),
        "replay_count": len(replay_envelopes), "episode_count": len(episodes),
        "decision_count": len(decisions), "training_permitted_decision_count": len(examples),
        "excluded_decision_count": len(exclusions), "deck_observation_count": len(decks),
        "schema_variants": dict(sorted(schema_variants.items())),
        "privacy_violation_count": sum(item.get("reason") == "OPPONENT_PRIVATE_HAND_EXPOSED" for item in exclusions),
        "actor_visibility_violation_count": sum(item.get("reason") in {"ACTOR_INDEX_MISMATCH", "MISSING_OWN_OBSERVATION"} for item in exclusions),
        "mapping": mapping_stats, "quarantine_count": len(quarantine),
        "raw_replay_copied": False,
    }
    atomic_write_json(output_paths.reports / "o4_replay_normalization_report.json", report)
    atomic_write_json(output_paths.quarantine / "o4_replay_normalization_quarantine.json", {
        "schema_version": "o4-replay-normalization-quarantine-v1", "entries": quarantine,
        "decision_exclusions": exclusions,
    })
    atomic_write_json(output_paths.manifest, {
        "schema_version": "o4-final-own-data-run-v1", "source_run": str(source_root),
        "raw_replay_copied": False, "report_hash": digest(report, domain="o4-live-own-replay-report-v1"),
    })
    return report


__all__ = ["KaggleReplayRunError", "run_normalize_live_own_replays"]
