"""Resumable, read-only O3 acquisition runner built from existing O1 adapters."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from . import archive
from .atomic_io import atomic_write_json
from .external_acquisition import AcquisitionOutcome, acquire_own_logs, acquire_own_submission_episodes, acquire_own_submissions, acquire_replay
from .external_capability import CapabilityReport, probe_capability_with_results
from .external_transport import (
    FAILURE_AUTHENTICATION, FAILURE_DEPENDENCY_MISSING, FAILURE_PERMISSION_DENIED,
    ExternalRequest, ExternalTransport, FixtureTransport, SubprocessKaggleTransport,
)
from .live_payloads import identity_from_submission_payload, normalize_live_payload, normalized_episode_records, normalized_submission_ids
from .participant_resolver import (
    OwnSubmissionBootstrap, TeamIdentity, identities_compatible, resolve_episode_agent_mapping,
)
from .rules_attestation import RulesAttestation, load_rules_attestation
from .runstate import RunPaths


LIVE_ACQUISITION_SCHEMA_VERSION = "o3-live-acquisition-v1"


@dataclass(frozen=True, slots=True)
class LiveAcquisitionConfig:
    competition: str
    timeout_seconds: float = 20.0
    maximum_replays: int = 10
    maximum_submissions: int = 10
    team_id_env: str = "O3_TEAM_ID"
    team_name_env: str = "O3_TEAM_NAME"

    @classmethod
    def load(cls, path: str | Path) -> "LiveAcquisitionConfig":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(raw, Mapping) or raw.get("schema_version") != LIVE_ACQUISITION_SCHEMA_VERSION:
            raise ValueError("unsupported live acquisition config")
        allowed = {"schema_version", "competition", "timeout_seconds", "maximum_replays", "maximum_submissions", "team_id_env", "team_name_env"}
        unknown = set(raw) - allowed
        if unknown:
            raise ValueError(f"unknown live acquisition config keys: {sorted(unknown)}")
        config = cls(**{key: value for key, value in raw.items() if key != "schema_version"})
        if not config.competition or config.timeout_seconds <= 0 or config.maximum_replays < 0 or config.maximum_submissions < 0:
            raise ValueError("invalid live acquisition limits")
        return config


def _identity(config: LiveAcquisitionConfig, *, team_id: str | None = None, team_name: str | None = None, identity_config: str | Path | None = None) -> TeamIdentity | None:
    """Resolve explicit CLI values, then gitignored local config, then env."""
    if identity_config is not None:
        raw = json.loads(Path(identity_config).read_text(encoding="utf-8"))
        if not isinstance(raw, Mapping) or set(raw) - {"team_id", "team_name"}:
            raise ValueError("identity config must contain only team_id/team_name")
        team_id = team_id or (str(raw["team_id"]) if raw.get("team_id") else None)
        team_name = team_name or (str(raw["team_name"]) if raw.get("team_name") else None)
    team_id = team_id or os.environ.get(config.team_id_env) or None
    team_name = team_name or os.environ.get(config.team_name_env) or None
    return TeamIdentity(team_id=team_id, team_name=team_name) if (team_id or team_name) else None


def _json_from_outcome(run_root: Path, outcome: AcquisitionOutcome) -> Any:
    if outcome.status != "ARCHIVED" or not outcome.raw_sha256:
        return None
    try:
        return json.loads(archive.read_raw(run_root, outcome.raw_sha256).decode("utf-8"))
    except (archive.ArchiveError, UnicodeDecodeError, json.JSONDecodeError):
        return None


def _normalized_from_outcome(run_root: Path, outcome: AcquisitionOutcome) -> Mapping[str, Any] | None:
    value = _json_from_outcome(run_root, outcome)
    if value is None:
        return None
    try:
        return normalize_live_payload(outcome.action, archive.read_raw(run_root, outcome.raw_sha256 or "")).payload
    except (ValueError, archive.ArchiveError):
        return None


def _outcome(outcome: AcquisitionOutcome) -> dict[str, Any]:
    return outcome.as_dict()


def _submission_candidates(payload: Mapping[str, Any], maximum: int) -> tuple[str, ...]:
    records = payload.get("records")
    if not isinstance(records, list):
        return ()
    rows = [row for row in records if isinstance(row, Mapping) and isinstance(row.get("_normalized_id"), str)]
    rows.sort(key=lambda row: str(row.get("date", row.get("createTime", ""))), reverse=True)
    return tuple(dict.fromkeys(str(row["_normalized_id"]) for row in rows))[:maximum]


def _write_identity_cache(
    root: Path, config: LiveAcquisitionConfig, identity: TeamIdentity, bootstrap: OwnSubmissionBootstrap,
    episode_sha256: str, agent_index: int,
) -> None:
    identity_value = f"{identity.team_id or ''}\x00{identity.team_name or ''}"
    cache = {
        "schema_version": "o4-identity-cache-v2",
        "competition": config.competition,
        "identity_hash": hashlib.sha256(identity_value.encode("utf-8")).hexdigest(),
        "source_submission_id_hash": hashlib.sha256(bootstrap.submission_id.encode("utf-8")).hexdigest(),
        "source_submission_listing_hash": bootstrap.submission_source_hash,
        "source_episode_hash": episode_sha256,
        "agent_index": agent_index,
        "verified_at": __import__("time").strftime("%Y-%m-%dT%H:%M:%SZ", __import__("time").gmtime()),
    }
    atomic_write_json(RunPaths(root).state / "o4_identity_cache.json", cache)


def _identity_cache_status(root: Path, config: LiveAcquisitionConfig) -> str:
    path = RunPaths(root).state / "o4_identity_cache.json"
    if not path.exists():
        return "ABSENT"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "INVALID_REVERIFY"
    required = ("identity_hash", "source_submission_id_hash", "source_submission_listing_hash", "source_episode_hash")
    if not isinstance(value, Mapping) or value.get("schema_version") != "o4-identity-cache-v2" or value.get("competition") != config.competition:
        return "INVALID_REVERIFY"
    if any(not isinstance(value.get(key), str) or len(value[key]) != 64 for key in required):
        return "INVALID_REVERIFY"
    if type(value.get("agent_index")) is not int or value["agent_index"] < 0:
        return "INVALID_REVERIFY"
    return "VALID_REVERIFY_REQUIRED"


def run_live_acquisition(
    *, run_root: str | Path, config: LiveAcquisitionConfig, transport: ExternalTransport,
    rules_attestation: RulesAttestation | None = None,
    team_id: str | None = None, team_name: str | None = None, identity_config: str | Path | None = None,
) -> dict[str, Any]:
    """Acquire only provenance-proven own data; never schedule public episodes."""
    root = Path(run_root)
    # Capability probing deliberately avoids actions that need dynamic IDs;
    # those are issued below only after a submission/episode id was observed.
    capability, probe_results = probe_capability_with_results(
        transport, target=config.competition, timeout=config.timeout_seconds,
        actions=("own_submission_listing",),
    )
    paths = RunPaths(root)
    paths.ensure_layout()
    identity_cache_status = _identity_cache_status(root, config)
    atomic_write_json(paths.reports / "live_capability_report.json", capability.content_payload())
    own_uses = ("ARCHIVE", "ANALYSIS", "TRAINING", "REPORTING")
    outcomes: list[dict[str, Any]] = []
    resolved_identity = _identity(config, team_id=team_id, team_name=team_name, identity_config=identity_config)
    # A public listing can be returned without credentials.  It must never be
    # promoted to OWN_KAGGLE merely because the command name says "submissions".
    probe_submission = probe_results.get("own_submission_listing")
    if (
        isinstance(transport, SubprocessKaggleTransport)
        and probe_submission is not None
        and probe_submission.error_type in {
            FAILURE_AUTHENTICATION, FAILURE_PERMISSION_DENIED, FAILURE_DEPENDENCY_MISSING,
        }
    ):
        outcomes.append({"action": "own_submission_listing", "status": "SKIPPED_AUTHENTICATION_REQUIRED", "detail": "own-data chain not started"})
        manifest = {
            "schema_version": LIVE_ACQUISITION_SCHEMA_VERSION, "competition": config.competition,
            "capability_report_id": capability.capability_report_id, "outcomes": outcomes,
            "submission_count": 0, "episode_count": 0, "replay_count": 0, "log_count": 0,
            "identity_status": "AUTHENTICATION_REQUIRED", "public_other_collection_enabled": False,
            "public_other_status": "RULES_UNVERIFIED_ARCHIVE_ONLY",
        }
        atomic_write_json(paths.reports / "live_acquisition_manifest.json", manifest)
        return manifest
    submissions = acquire_own_submissions(
        root, transport, target=config.competition, capability_report=capability, allowed_uses=own_uses,
        request=ExternalRequest("own_submission_listing", competition=config.competition), timeout=config.timeout_seconds,
        response=probe_submission,
        payload_parser=lambda action, body: normalize_live_payload(action, body).payload,
    )
    outcomes.append(_outcome(submissions))
    submissions_payload = _normalized_from_outcome(root, submissions)
    if resolved_identity is None and submissions_payload is not None:
        inferred_id, inferred_name = identity_from_submission_payload(submissions_payload)
        resolved_identity = TeamIdentity(team_id=inferred_id, team_name=inferred_name) if (inferred_id or inferred_name) else None
    submission_bootstrap_hash = submissions.raw_sha256
    bootstrap_ready = submissions.status == "ARCHIVED" and isinstance(submission_bootstrap_hash, str)
    submission_candidates = _submission_candidates(submissions_payload or {}, config.maximum_submissions) if bootstrap_ready else ()
    if not submission_candidates:
        outcomes.append({"action": "own_episode_listing", "status": "SKIPPED_OWN_SUBMISSION_BOOTSTRAP_UNAVAILABLE", "detail": "authenticated own listing was not archived"})
    replay_count = 0
    replay_attempts = 0
    episode_count = 0
    episode_mapping_verified = 0
    episode_mapping_quarantined = 0
    log_count = 0
    for submission_id in submission_candidates:
        bootstrap = OwnSubmissionBootstrap(
            submission_id=submission_id, submission_source_hash=submission_bootstrap_hash or "0" * 64,
        )
        episodes = acquire_own_submission_episodes(
            root, transport, target=submission_id, capability_report=capability, allowed_uses=("ARCHIVE",),
            request=ExternalRequest("own_episode_listing", submission_id=submission_id), timeout=config.timeout_seconds,
            payload_parser=lambda action, body: normalize_live_payload(action, body).payload,
        )
        outcomes.append(_outcome(episodes))
        episode_records = normalized_episode_records(_normalized_from_outcome(root, episodes) or {})
        episode_count += len(episode_records)
        for episode in episode_records:
            episode_id = str(episode["_normalized_id"])
            episode_mapping = resolve_episode_agent_mapping(episode, bootstrap)
            if episode_mapping.identity is None or len(episode_mapping.agent_indices) != 1:
                episode_mapping_quarantined += 1
                outcomes.append({
                    "action": "episode_agent_mapping", "status": "QUARANTINED_SUBMISSION_MAPPING",
                    "detail": episode_mapping.reason,
                })
                continue
            mapped_identity = episode_mapping.identity
            if resolved_identity is not None and not identities_compatible(resolved_identity, mapped_identity):
                episode_mapping_quarantined += 1
                outcomes.append({
                    "action": "episode_agent_mapping", "status": "QUARANTINED_IDENTITY_MISMATCH",
                    "detail": "explicit_identity_disagrees_with_episode_agent",
                })
                continue
            if resolved_identity is None:
                resolved_identity = mapped_identity
                _write_identity_cache(
                    root, config, resolved_identity, bootstrap, episodes.raw_sha256 or "0" * 64,
                    episode_mapping.agent_indices[0],
                )
                identity_cache_status = "VALID_REVERIFY_REQUIRED"
            episode_mapping_verified += 1
            if replay_attempts >= config.maximum_replays:
                break
            replay_attempts += 1
            replay = acquire_replay(
                root, transport, target=episode_id, capability_report=capability, allowed_uses=own_uses,
                team_identity=resolved_identity, verified_episode_agent_index=episode_mapping.agent_indices[0],
                request=ExternalRequest("replay", episode_id=episode_id),
                timeout=config.timeout_seconds,
                payload_parser=lambda action, body: normalize_live_payload(action, body).payload,
            )
            outcomes.append(_outcome(replay))
            replay_count += int(replay.status == "ARCHIVED")
            if replay.status != "ARCHIVED":
                continue
            agent_index = episode_mapping.agent_indices[0]
            logs = acquire_own_logs(
                root, transport, target=episode_id, capability_report=capability, allowed_uses=("ARCHIVE",),
                request=ExternalRequest("own_logs", episode_id=episode_id, agent_index=agent_index),
                timeout=config.timeout_seconds, payload_parser=lambda action, body: normalize_live_payload(action, body).payload,
            )
            outcomes.append(_outcome(logs))
            log_count += int(logs.status == "ARCHIVED")
    public_other_enabled = bool(rules_attestation and rules_attestation.permits_public_other_collection())
    manifest = {
        "schema_version": LIVE_ACQUISITION_SCHEMA_VERSION,
        "competition": config.competition,
        "capability_report_id": capability.capability_report_id,
        "outcomes": outcomes,
        "submission_count": len(normalized_submission_ids(submissions_payload or {})),
        "episode_count": episode_count,
        "episode_mapping_verified_count": episode_mapping_verified,
        "episode_mapping_quarantined_count": episode_mapping_quarantined,
        "replay_count": replay_count,
        "log_count": log_count,
        "identity_status": "RESOLVED" if resolved_identity is not None else "UNRESOLVED",
        "identity_cache_status": identity_cache_status,
        "public_other_collection_enabled": public_other_enabled,
        "public_other_status": "RULES_UNVERIFIED_ARCHIVE_ONLY" if not public_other_enabled else "ATTESTED_NOT_SCHEDULED",
    }
    atomic_write_json(paths.reports / "live_acquisition_manifest.json", manifest)
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mage-ptcg-live-acquisition")
    parser.add_argument("--config", required=True)
    parser.add_argument("--run-root", required=True)
    parser.add_argument("--rules-attestation")
    parser.add_argument("--fixture")
    parser.add_argument("--team-id")
    parser.add_argument("--team-name")
    parser.add_argument("--identity-config", help="gitignored local JSON containing team_id/team_name")
    args = parser.parse_args(argv)
    config = LiveAcquisitionConfig.load(args.config)
    if args.fixture:
        raw = json.loads(Path(args.fixture).read_text(encoding="utf-8"))
        if not isinstance(raw, Mapping):
            parser.error("fixture must be an object")
        responses = {}
        from .external_transport import ExternalRawResponse
        for action, item in raw.items():
            if isinstance(item, Mapping):
                responses[action] = ExternalRawResponse(
                    action=action, target="fixture", success=bool(item.get("success", True)),
                    body=json.dumps(item.get("body", {}), sort_keys=True).encode(), content_type="application/json",
                    error_type=None if item.get("success", True) else "unavailable", client_name="fixture",
                )
        transport: ExternalTransport = FixtureTransport(responses)
    else:
        transport = SubprocessKaggleTransport(sdk_episode_agents=True)
    attestation = load_rules_attestation(args.rules_attestation) if args.rules_attestation else None
    print(json.dumps(run_live_acquisition(run_root=args.run_root, config=config, transport=transport, rules_attestation=attestation,
                                          team_id=args.team_id, team_name=args.team_name, identity_config=args.identity_config), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
