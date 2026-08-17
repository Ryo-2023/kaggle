"""External acquisition adapters (O1-5 SS3): own submissions, own submission
episodes/Replay, own logs, public artifacts, leaderboard snapshots, and team
submission metadata.

Every adapter goes through the same pipeline: call the injected transport for
one action -> secret-scan -> schema-drift-check against a persisted,
value-free trusted baseline (``external_schema.py``) -> archive raw bytes by
content hash (reusing ``archive.py``, exactly as ``local_ingest.py`` already
does) -> write a validated ``SourceEnvelope`` (reusing ``provenance.py``).
A feature the transport reports as unavailable, or a response that fails the
schema-drift or secret-scan check, never silently becomes an empty
"successful" archive entry -- it is returned as a distinct, typed outcome
(``UNAVAILABLE`` / ``QUARANTINED``) instead.

Schema baselines are persisted per ``(source_kind, action)`` under
``state/external_schema_baselines/``. They contain structural shapes only,
never response values. Unknown live structured input is quarantined. A
fixture/recording may explicitly establish a trusted test baseline; live TOFU
requires an explicit opt-in argument after action validation succeeds.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from . import archive
from .canonical import sha256_hex
from .contracts import AcquisitionMode, ContractError, SourceEnvelope, SourceKind
from .external_capability import CapabilityReport
from .external_schema import (
    COMPATIBLE,
    COMPATIBLE_WITH_ADDITIONS,
    UNKNOWN_SCHEMA,
    SchemaDriftReport,
    build_schema_drift_report,
    schema_fingerprint_of,
    schema_shape_of,
)
from .external_transport import ExternalRawResponse, ExternalRequest, ExternalTransport, call_request
from .o5_payload import PayloadExtractionError, extract_structured_payload
from .provenance import build_source_envelope, write_source_manifest
from .runstate import RunPaths, load_or_create, run_lock

PARSER_VERSION = "competition-intelligence-external-acquisition-v1"
REDACTION_VERSION = "competition-intelligence-redaction-v1"

STATUS_ARCHIVED = "ARCHIVED"
STATUS_UNAVAILABLE = "UNAVAILABLE"
STATUS_QUARANTINED = "QUARANTINED"


class AcquisitionError(RuntimeError):
    """Raised for a caller-error (bad arguments), not a normal unavailable/quarantine outcome."""


def _timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


@dataclass(frozen=True, slots=True)
class AcquisitionOutcome:
    status: str
    action: str
    target: str
    source_id: str | None
    raw_sha256: str | None
    content_hash: str | None
    manifest_path: str | None
    capability_report_id: str
    schema_compatibility: str | None
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "action": self.action,
            "target": self.target,
            "source_id": self.source_id,
            "raw_sha256": self.raw_sha256,
            "content_hash": self.content_hash,
            "manifest_path": self.manifest_path,
            "capability_report_id": self.capability_report_id,
            "schema_compatibility": self.schema_compatibility,
            "detail": self.detail,
        }


def _baseline_key(source_kind: SourceKind, action: str) -> str:
    return f"{source_kind.value}:{action}"


def _baseline_path(run_root: Path, source_kind: SourceKind, action: str) -> Path:
    key = _baseline_key(source_kind, action)
    return RunPaths(run_root).state / "external_schema_baselines" / (sha256_hex(key.encode("utf-8")) + ".json")


BASELINE_SCHEMA_VERSION = "external-schema-baseline-v2"


def _load_baseline(run_root: Path, source_kind: SourceKind, action: str) -> dict[str, Any] | None:
    path = _baseline_path(run_root, source_kind, action)
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or value.get("schema_version") != BASELINE_SCHEMA_VERSION:
            return None
        if not isinstance(value.get("shape"), dict) or not isinstance(value.get("fingerprint"), str):
            return None
        return value
    except (OSError, json.JSONDecodeError):
        return None


def _store_baseline(run_root: Path, source_kind: SourceKind, action: str, value: Any, *, trust: str) -> None:
    from .atomic_io import atomic_write_json

    atomic_write_json(_baseline_path(run_root, source_kind, action), {
        "schema_version": BASELINE_SCHEMA_VERSION,
        "source_kind": source_kind.value,
        "action": action,
        "fingerprint": schema_fingerprint_of(value),
        "shape": schema_shape_of(value),
        "trust": trust,
    })


def _validate_action_payload(action: str, value: Any) -> str | None:
    """Action-specific minimum schema gate; it never fabricates missing data."""
    if isinstance(value, Mapping) and value.get("schema_version") == "kaggle-cli-live-payload-v1":
        if value.get("action") != action:
            return "normalized action does not match requested action"
        if action == "replay":
            return None if isinstance(value.get("info"), Mapping) and isinstance(value.get("progression_field"), str) else "normalized replay is incomplete"
        if action == "own_logs":
            return None if isinstance(value.get("byte_length"), int) and value["byte_length"] > 0 else "normalized logs are empty"
        return None if isinstance(value.get("records"), list) else "normalized payload lacks records"
    if action == "replay":
        if not isinstance(value, dict):
            return "replay must be a JSON object"
        if not any(key in value for key in ("events", "steps", "actions", "decisions", "turns")):
            return "replay lacks a progression/action field"
        return None
    if action in {
        "competition_metadata", "own_submission_listing", "own_episode_listing", "own_logs", "public_logs",
        "public_artifacts", "leaderboard", "team_submission_listing",
    } and isinstance(value, (dict, list)):
        return None
    return f"{action} must be a JSON object or array"


def _safe_detail(value: str | None) -> str:
    from mage_ptcg.competition.redaction import redact_value

    return str(redact_value(value or ""))[:500]


def _parse_body(response: ExternalRawResponse) -> tuple[Any | None, bool]:
    """Returns (parsed_value_or_None, looked_like_structured_data)."""
    if not response.body:
        return None, False
    text = response.body
    likely = (response.content_type and "json" in response.content_type.lower()) or text.lstrip(b"\xef\xbb\xbf \t\r\n").startswith((b"{", b"["))
    if not likely:
        return None, False
    try:
        candidate = extract_structured_payload(text, response.stderr)
        # Multiple independent JSON values are not a schema.  Callers must
        # select one through a typed adapter rather than guessing.
        return (candidate.payload if candidate.envelope_kind == "SINGLE_JSON" else None), True
    except PayloadExtractionError:
        return None, True


def acquire_external_artifact(
    run_root: str | Path,
    transport: ExternalTransport,
    *,
    action: str,
    target: str,
    capability_report: CapabilityReport,
    source_kind: SourceKind | str,
    allowed_uses: Iterable[str],
    owner_scope: str = "self",
    visibility: str = "private",
    config_hash: str = "unset",
    timeout: float = 20.0,
    source_id: str | None = None,
    allow_live_schema_tofu: bool = False,
    request: ExternalRequest | None = None,
    response: ExternalRawResponse | None = None,
    source_kind_resolver: Callable[[Any], SourceKind | None] | None = None,
    payload_parser: Callable[[str, bytes], Mapping[str, Any]] | None = None,
) -> AcquisitionOutcome:
    """Acquire one external artifact for ``action`` and archive it with provenance.

    Never raises for a normal "the capability is unavailable" or "the
    response was quarantined" outcome -- both are represented as a typed
    ``AcquisitionOutcome``, not an empty success or a swallowed exception.
    """
    kind = source_kind if isinstance(source_kind, SourceKind) else SourceKind(source_kind)
    requested_allowed_uses = list(allowed_uses)
    root = Path(run_root)
    paths = RunPaths(root)
    root.mkdir(parents=True, exist_ok=True)
    # One lock spans every mutation.  An interruption can therefore leave an
    # immutable blob but cannot race a manifest/runstate update; re-running
    # content-addressed storage and record_ingested_source is idempotent.
    with run_lock(paths, root.name):
        state = load_or_create(
            root, run_id=root.name, git_commit="unknown", config_hash=config_hash, resume=paths.manifest.exists()
        )
        resolved_request = request or ExternalRequest.from_legacy(action, target)
        if resolved_request.action != action:
            raise AcquisitionError("ExternalRequest action must match acquisition action")
        response = response or call_request(transport, resolved_request, timeout=timeout)
        if not response.success:
            return AcquisitionOutcome(
                status=STATUS_UNAVAILABLE, action=action, target=target, source_id=None, raw_sha256=None,
                content_hash=None, manifest_path=None, capability_report_id=capability_report.capability_report_id,
                schema_compatibility=None,
                detail=_safe_detail(f"transport reported {response.error_type}: {response.error_message or ''}"),
            )

        data = response.body
        is_safe, labels = archive.scan_before_archive(data)
        if not is_safe:
            quarantine_hash = archive.quarantine_bytes(
                root, data, reason="secret_scan_hit", detail={"labels": list(labels), "action": action}
            )
            return AcquisitionOutcome(
                status=STATUS_QUARANTINED, action=action, target=target, source_id=None, raw_sha256=None,
                content_hash=quarantine_hash, manifest_path=None, capability_report_id=capability_report.capability_report_id,
                schema_compatibility=None, detail=f"secret_scan_hit:{','.join(labels)}",
            )

        # The byte-identical response is retained before any parser, schema,
        # or adapter has a chance to inspect it.  This is intentionally a raw
        # archive rather than a normalized DTO and makes parser quarantines
        # reproducible without accepting their schema.
        raw_sha256 = archive.store_raw(root, data)

        parsed, looked_structured = _parse_body(response)
        normalized_payload: Mapping[str, Any] | None = None
        if payload_parser is not None:
            try:
                normalized_payload = payload_parser(action, data)
            except (ValueError, TypeError) as exc:
                quarantine_hash = archive.quarantine_bytes(
                    root, data, reason="action_payload_invalid", detail={"action": action, "reason": _safe_detail(str(exc))},
                )
                return AcquisitionOutcome(
                    status=STATUS_QUARANTINED, action=action, target=target, source_id=None, raw_sha256=raw_sha256,
                    content_hash=quarantine_hash, manifest_path=None,
                    capability_report_id=capability_report.capability_report_id, schema_compatibility=None,
                    detail="action_payload_invalid",
                )
            # The adapter's normalized DTO is the schema-validation boundary;
            # raw bytes remain the archived source of record.
            parsed = dict(normalized_payload)
            looked_structured = True
        if source_kind_resolver is not None:
            resolved_kind = source_kind_resolver(parsed) if parsed is not None else None
            if resolved_kind is None:
                quarantine_hash = archive.quarantine_bytes(
                    root, data, reason="participant_resolution_quarantined", detail={"action": action},
                )
                return AcquisitionOutcome(
                    status=STATUS_QUARANTINED, action=action, target=target, source_id=None, raw_sha256=raw_sha256,
                    content_hash=quarantine_hash, manifest_path=None,
                    capability_report_id=capability_report.capability_report_id, schema_compatibility=None,
                    detail="participant_resolution_quarantined",
                )
            kind = resolved_kind
            if kind is SourceKind.PUBLIC_OTHER:
                requested_uses = {str(value) for value in requested_allowed_uses}
                if requested_uses - {"ARCHIVE"}:
                    raise AcquisitionError("PUBLIC_OTHER replay may only be archived while rules are unverified")
        schema_compatibility: str | None = None
        if looked_structured:
            baseline = _load_baseline(root, kind, action)
            validation_error = _validate_action_payload(action, parsed) if parsed is not None else "malformed_json"
            drift_report: SchemaDriftReport | None = None
            if validation_error is None:
                drift_report = build_schema_drift_report(
                    source_kind=kind.value, baseline_value=None, candidate_value=parsed,
                    baseline_fingerprint=baseline["fingerprint"] if baseline else None,
                    baseline_shape=baseline["shape"] if baseline else None,
                )
                schema_compatibility = drift_report.compatibility
            else:
                schema_compatibility = "malformed_json" if parsed is None else UNKNOWN_SCHEMA
            if schema_compatibility == UNKNOWN_SCHEMA and baseline is None and parsed is not None:
                if payload_parser is not None:
                    _store_baseline(root, kind, action, parsed, trust="action_specific_live_parser")
                    schema_compatibility = COMPATIBLE
                elif response.trusted_test_baseline or allow_live_schema_tofu:
                    _store_baseline(
                        root, kind, action, parsed,
                        trust="test_fixture" if response.trusted_test_baseline else "explicit_live_tofu",
                    )
                    schema_compatibility = COMPATIBLE
                else:
                    validation_error = validation_error or "untrusted_first_response"
            if schema_compatibility not in (COMPATIBLE, COMPATIBLE_WITH_ADDITIONS):
                detail: dict[str, Any] = {
                    "action": action, "compatibility": schema_compatibility, "validation_error": validation_error,
                }
                if drift_report is not None:
                    detail["findings"] = [finding.as_dict() for finding in drift_report.findings]
                quarantine_hash = archive.quarantine_bytes(root, data, reason="schema_unknown_or_incompatible", detail=detail)
                return AcquisitionOutcome(
                    status=STATUS_QUARANTINED, action=action, target=target, source_id=None, raw_sha256=raw_sha256,
                    content_hash=quarantine_hash, manifest_path=None,
                    capability_report_id=capability_report.capability_report_id,
                    schema_compatibility=schema_compatibility, detail="schema_unknown_or_incompatible",
                )

        resolved_source_id = source_id or (
            f"external:{kind.value}:{action}:{sha256_hex(target.encode('utf-8'))[:12]}:{raw_sha256[:16]}"
        )
        try:
            envelope = build_source_envelope(
                source_id=resolved_source_id, source_kind=kind, acquisition_mode=capability_report.capability_mode,
                acquired_at=_timestamp(), origin_reference=f"kaggle:{action}:{target}", owner_scope=owner_scope,
                visibility=visibility, allowed_uses=requested_allowed_uses, raw_sha256=raw_sha256,
                parser_version=PARSER_VERSION, redaction_version=REDACTION_VERSION,
                metadata={"action": action, "capability_report_id": capability_report.capability_report_id,
                          "schema_compatibility": schema_compatibility,
                          "normalized_payload_hash": sha256_hex(json.dumps(normalized_payload, sort_keys=True).encode("utf-8")) if normalized_payload is not None else None},
            )
        except ContractError as exc:
            raise AcquisitionError(f"invalid source envelope for {action}: {_safe_detail(str(exc))}") from exc

        from .provenance import source_manifest_path
        manifest_path = source_manifest_path(root, envelope.source_id)
        if not manifest_path.exists():
            write_source_manifest(root, envelope)
        state.record_ingested_source(envelope.source_id)
        return AcquisitionOutcome(
            status=STATUS_ARCHIVED, action=action, target=target, source_id=envelope.source_id,
            raw_sha256=raw_sha256, content_hash=envelope.content_hash(),
            manifest_path=manifest_path.relative_to(root).as_posix(),
            capability_report_id=capability_report.capability_report_id,
            schema_compatibility=schema_compatibility, detail="ok",
        )


# Convenience wrappers naming each of the six required adapter surfaces
# explicitly (O1-5 SS3); all delegate to the single acquisition pipeline
# above with the appropriate action/source-kind/default owner scope.


def acquire_own_submissions(run_root: str | Path, transport: ExternalTransport, *, target: str, capability_report: CapabilityReport, allowed_uses: Iterable[str], **kwargs: Any) -> AcquisitionOutcome:
    return acquire_external_artifact(
        run_root, transport, action="own_submission_listing", target=target, capability_report=capability_report,
        source_kind=SourceKind.OWN_KAGGLE, allowed_uses=allowed_uses, **kwargs,
    )


def acquire_own_submission_episodes(run_root: str | Path, transport: ExternalTransport, *, target: str, capability_report: CapabilityReport, allowed_uses: Iterable[str], **kwargs: Any) -> AcquisitionOutcome:
    return acquire_external_artifact(
        run_root, transport, action="own_episode_listing", target=target, capability_report=capability_report,
        source_kind=SourceKind.OWN_KAGGLE, allowed_uses=allowed_uses, **kwargs,
    )


def acquire_replay(run_root: str | Path, transport: ExternalTransport, *, target: str, capability_report: CapabilityReport, allowed_uses: Iterable[str], team_identity: Any | None = None, bootstrap: Any | None = None, verified_episode_agent_index: int | None = None, **kwargs: Any) -> AcquisitionOutcome:
    from .participant_resolver import TeamIdentity, bootstrap_identity_from_replay, classify_replay_participants, replay_matches_episode_agent

    def resolve(value: Any) -> SourceKind | None:
        if not isinstance(value, dict):
            return None
        identity = team_identity if isinstance(team_identity, TeamIdentity) else None
        if bootstrap is not None:
            bootstrapped = bootstrap_identity_from_replay(value, bootstrap)
            if bootstrapped.identity is None:
                return None
            if identity is not None and bootstrapped.identity != identity:
                return None
            return SourceKind.OWN_KAGGLE
        if verified_episode_agent_index is not None:
            if identity is None or not replay_matches_episode_agent(value, identity, verified_episode_agent_index):
                return None
            return SourceKind.OWN_KAGGLE
        return classify_replay_participants(value, identity).source_kind

    return acquire_external_artifact(
        run_root, transport, action="replay", target=target, capability_report=capability_report,
        source_kind=SourceKind.OWN_KAGGLE, allowed_uses=allowed_uses, source_kind_resolver=resolve, **kwargs,
    )


def acquire_own_logs(run_root: str | Path, transport: ExternalTransport, *, target: str, capability_report: CapabilityReport, allowed_uses: Iterable[str], **kwargs: Any) -> AcquisitionOutcome:
    return acquire_external_artifact(
        run_root, transport, action="own_logs", target=target, capability_report=capability_report,
        source_kind=SourceKind.OWN_KAGGLE, allowed_uses=allowed_uses, **kwargs,
    )


def acquire_public_artifacts(run_root: str | Path, transport: ExternalTransport, *, target: str, capability_report: CapabilityReport, allowed_uses: Iterable[str], **kwargs: Any) -> AcquisitionOutcome:
    return acquire_external_artifact(
        run_root, transport, action="public_artifacts", target=target, capability_report=capability_report,
        source_kind=SourceKind.PUBLIC_OTHER, allowed_uses=allowed_uses, **kwargs,
    )


def acquire_public_logs(run_root: str | Path, transport: ExternalTransport, *, target: str, capability_report: CapabilityReport, allowed_uses: Iterable[str], **kwargs: Any) -> AcquisitionOutcome:
    return acquire_external_artifact(
        run_root, transport, action="public_logs", target=target, capability_report=capability_report,
        source_kind=SourceKind.PUBLIC_OTHER, allowed_uses=allowed_uses, **kwargs,
    )


def acquire_leaderboard_snapshot(run_root: str | Path, transport: ExternalTransport, *, target: str, capability_report: CapabilityReport, allowed_uses: Iterable[str], **kwargs: Any) -> AcquisitionOutcome:
    return acquire_external_artifact(
        run_root, transport, action="leaderboard", target=target, capability_report=capability_report,
        source_kind=SourceKind.PUBLIC_OTHER, allowed_uses=allowed_uses, **kwargs,
    )


def acquire_team_submission_metadata(run_root: str | Path, transport: ExternalTransport, *, target: str, capability_report: CapabilityReport, allowed_uses: Iterable[str], **kwargs: Any) -> AcquisitionOutcome:
    return acquire_external_artifact(
        run_root, transport, action="team_submission_listing", target=target, capability_report=capability_report,
        source_kind=SourceKind.TEAM_SHARED, allowed_uses=allowed_uses, **kwargs,
    )


__all__ = [
    "PARSER_VERSION",
    "REDACTION_VERSION",
    "STATUS_ARCHIVED",
    "STATUS_QUARANTINED",
    "STATUS_UNAVAILABLE",
    "AcquisitionError",
    "AcquisitionOutcome",
    "acquire_external_artifact",
    "acquire_leaderboard_snapshot",
    "acquire_own_logs",
    "acquire_own_submission_episodes",
    "acquire_own_submissions",
    "acquire_public_artifacts",
    "acquire_public_logs",
    "acquire_replay",
    "acquire_team_submission_metadata",
]
