"""External capability discovery and fail-closed classification (O1-5 SS1).

Builds a ``CapabilityReport`` by running the full ``EXTERNAL_ACTIONS`` set
against an injected ``ExternalTransport`` and classifying the result with the
same conservative philosophy as ``mage_ptcg.competition.probe.classify_mode``
(reused directly, not reimplemented): a capability mode is never inferred
from partial success, and every availability field is tri-state (``True`` /
``False`` / ``None`` for "not tested") so "unknown" is never silently folded
into "unavailable".
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Mapping

from mage_ptcg.competition.fingerprint import fingerprint_document
from mage_ptcg.competition.probe import detect_authentication, detect_replay_fields

from .canonical import digest
from .contracts import AcquisitionMode, ContractError
from .external_transport import (
    FAILURE_UNAVAILABLE,
    EXTERNAL_ACTIONS,
    ExternalRawResponse,
    ExternalTransport,
    TransportError,
)

CAPABILITY_REPORT_SCHEMA_VERSION = "external-capability-report-v1"
CAPABILITY_PROBE_VERSION = "competition-intelligence-external-probe-v1"

_ACTION_TO_FIELD: dict[str, str] = {
    "competition_metadata": "competition_access_status",
    "own_submission_listing": "own_submission_listing_available",
    "own_episode_listing": "episode_listing_available",
    "replay": "replay_available",
    "own_logs": "own_log_available",
    "public_logs": "public_log_available",
    "public_artifacts": "public_artifacts_available",
    "leaderboard": "leaderboard_available",
    "team_submission_listing": "team_submission_available",
}


def _timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _parse_json_body(response: ExternalRawResponse) -> object | None:
    if not response.body:
        return None
    import json

    try:
        return json.loads(response.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


@dataclass(frozen=True, slots=True)
class CapabilityReport:
    schema_version: str
    capability_report_id: str
    target: str
    capability_mode: AcquisitionMode
    mode_classification_reasons: tuple[str, ...]
    commands_tested: tuple[str, ...]
    cli_version: str | None
    authentication_available: bool
    authentication_source_type: str
    competition_access_status: bool | None
    own_submission_listing_available: bool | None
    episode_listing_available: bool | None
    replay_available: bool | None
    own_log_available: bool | None
    public_log_available: bool | None
    public_artifacts_available: bool | None
    leaderboard_available: bool | None
    team_submission_available: bool | None
    schema_fingerprint: str | None
    rate_limit_info: Mapping[str, object] | None
    tested_at: str
    failure_categories: tuple[str, ...]
    redacted_diagnostic_summary: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.schema_version != CAPABILITY_REPORT_SCHEMA_VERSION:
            raise ContractError(f"unsupported CapabilityReport schema_version {self.schema_version!r}")
        if not isinstance(self.capability_mode, AcquisitionMode):
            raise ContractError("capability_mode must be an AcquisitionMode")

    def content_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "target": self.target,
            "capability_mode": self.capability_mode.value,
            "mode_classification_reasons": list(self.mode_classification_reasons),
            "commands_tested": list(self.commands_tested),
            "cli_version": self.cli_version,
            "authentication_available": self.authentication_available,
            "authentication_source_type": self.authentication_source_type,
            "competition_access_status": self.competition_access_status,
            "own_submission_listing_available": self.own_submission_listing_available,
            "episode_listing_available": self.episode_listing_available,
            "replay_available": self.replay_available,
            "own_log_available": self.own_log_available,
            "public_log_available": self.public_log_available,
            "public_artifacts_available": self.public_artifacts_available,
            "leaderboard_available": self.leaderboard_available,
            "team_submission_available": self.team_submission_available,
            "schema_fingerprint": self.schema_fingerprint,
            "rate_limit_info": dict(self.rate_limit_info) if self.rate_limit_info else None,
            "tested_at": self.tested_at,
            "failure_categories": sorted(self.failure_categories),
            "redacted_diagnostic_summary": list(self.redacted_diagnostic_summary),
        }

    def content_hash(self) -> str:
        return digest(self.content_payload(), domain="external-capability-report")


def classify_capability_mode(results: Mapping[str, ExternalRawResponse]) -> tuple[AcquisitionMode, list[str]]:
    """Fail-closed classification mirroring ``mage_ptcg.competition.probe.classify_mode``.

    ``UNAVAILABLE`` means every single action came back ``FAILURE_UNAVAILABLE``
    (nothing was actually attempted, e.g. the default ``UnavailableTransport``
    or an explicit ``--offline`` run). ``LOCAL_ONLY`` means at least one action
    was genuinely attempted but no remote artifact capability was proven.
    ``FULL_REPLAY`` requires replay bytes *and* proven progression *and*
    detected legal-option fields *and* a recorded schema fingerprint --
    partial success (e.g. replay bytes with no progression evidence) can only
    ever produce ``REPLAY_WITHOUT_LEGAL_OPTIONS`` or lower, never be upgraded.
    """
    reasons: list[str] = []
    if results and all(response.error_type == FAILURE_UNAVAILABLE for response in results.values()):
        return AcquisitionMode.UNAVAILABLE, ["no_action_attempted_transport_declined"]

    replay_response = results.get("replay")
    replay_retrieved = bool(replay_response and replay_response.success)
    parsed_replay = _parse_json_body(replay_response) if replay_retrieved else None
    legal_fields, progression_fields, has_progression = (
        detect_replay_fields(parsed_replay) if parsed_replay is not None else ([], [], False)
    )
    schema_fp = fingerprint_document(parsed_replay)["sha256"] if parsed_replay is not None else None

    any_public_success = any(
        results.get(action) is not None and results[action].success
        for action in ("competition_metadata", "public_artifacts", "leaderboard", "own_submission_listing")
    )

    if replay_retrieved and has_progression:
        if legal_fields and schema_fp:
            reasons.extend([
                "replay_bytes_retrieved",
                "state_or_action_progression_detected",
                "legal_option_fields_detected",
                "schema_fingerprint_recorded",
            ])
            return AcquisitionMode.FULL_REPLAY, reasons
        reasons.extend(["replay_bytes_retrieved", "state_or_action_progression_detected"])
        if not legal_fields:
            reasons.append("complete_legal_option_set_not_proven")
        if not schema_fp:
            reasons.append("schema_fingerprint_not_recorded")
        return AcquisitionMode.REPLAY_WITHOUT_LEGAL_OPTIONS, reasons
    if replay_retrieved:
        reasons.append("replay_bytes_lacked_proven_progression")
    if any_public_success:
        reasons.append("public_artifact_or_metadata_retrieved")
        return AcquisitionMode.PUBLIC_ARTIFACTS_ONLY, reasons
    reasons.append("no_remote_capability_proven")
    return AcquisitionMode.LOCAL_ONLY, reasons


def _build_capability_probe(
    transport: ExternalTransport,
    *,
    target: str,
    timeout: float = 20.0,
    actions: tuple[str, ...] = EXTERNAL_ACTIONS,
    tested_at: str | None = None,
) -> tuple[CapabilityReport, dict[str, ExternalRawResponse]]:
    """Run every requested action through ``transport`` and classify the result.

    Never raises on an individual action failure (that is the expected,
    reported outcome); only a malformed ``transport`` implementation would
    propagate an exception here.
    """
    if not target:
        raise ContractError("target must be a non-empty string")
    results: dict[str, ExternalRawResponse] = {}
    for action in actions:
        try:
            results[action] = transport.call(action, target=target, timeout=timeout)
        except TransportError:
            # Generic probes have only a competition target.  Actions such as
            # own_logs require an episode id plus agent index, and must remain
            # "not tested" rather than aborting the whole capability report.
            results[action] = ExternalRawResponse(
                action=action, target=target, success=False, error_type=FAILURE_UNAVAILABLE,
                error_message="structured request required", client_name="capability-probe",
            )

    mode, reasons = classify_capability_mode(results)

    auth_hint_available, auth_source = detect_authentication()
    auth_response = results.get("own_submission_listing")
    if auth_response is not None and auth_response.client_name == "kaggle-cli":
        # Credential-file presence is not authentication.  A successful
        # own-submission capability probe is the only live proof.
        auth_available = bool(auth_response.success)
        if not auth_response.success:
            auth_source = "none"
        elif not auth_hint_available:
            auth_source = "capability_probe"
    else:
        auth_available = auth_hint_available

    field_values: dict[str, bool | None] = {field_name: None for field_name in _ACTION_TO_FIELD.values()}
    failure_categories: set[str] = set()
    diagnostics: list[str] = []
    commands_tested: list[str] = []
    cli_version: str | None = None
    rate_limit_info: dict[str, object] | None = None

    for action, response in results.items():
        field_name = _ACTION_TO_FIELD[action]
        if response.error_type == FAILURE_UNAVAILABLE:
            field_values[field_name] = None  # not tested (transport declined), not "unavailable"
        else:
            field_values[field_name] = response.success
        if response.command:
            commands_tested.append(" ".join(response.command))
        else:
            commands_tested.append(action)
        if response.client_version and cli_version is None:
            cli_version = response.client_version
        if response.rate_limit_info:
            rate_limit_info = dict(response.rate_limit_info)
        if not response.success:
            failure_categories.add(response.error_type or "unknown_error")
            diagnostics.append(f"{action}:{response.error_type or 'unknown_error'}")

    replay_response = results.get("replay")
    parsed_replay = _parse_json_body(replay_response) if replay_response and replay_response.success else None
    schema_fp = fingerprint_document(parsed_replay)["sha256"] if parsed_replay is not None else None

    payload = {
        "schema_version": CAPABILITY_REPORT_SCHEMA_VERSION,
        "target": target,
        "capability_mode": mode.value,
        "mode_classification_reasons": list(reasons),
        "commands_tested": sorted(set(commands_tested)),
        "cli_version": cli_version,
        "authentication_available": auth_available,
        "authentication_source_type": auth_source,
        "competition_access_status": field_values["competition_access_status"],
        "own_submission_listing_available": field_values["own_submission_listing_available"],
        "episode_listing_available": field_values["episode_listing_available"],
        "replay_available": field_values["replay_available"],
        "own_log_available": field_values["own_log_available"],
        "public_log_available": field_values["public_log_available"],
        "public_artifacts_available": field_values["public_artifacts_available"],
        "leaderboard_available": field_values["leaderboard_available"],
        "team_submission_available": field_values["team_submission_available"],
        "schema_fingerprint": schema_fp,
        "rate_limit_info": rate_limit_info,
        "tested_at": tested_at or _timestamp(),
        "failure_categories": sorted(failure_categories),
        "redacted_diagnostic_summary": sorted(diagnostics),
    }
    report_id = "capability-report-" + digest(payload, domain="external-capability-report")[:20]
    fields = dict(payload)
    fields["capability_mode"] = mode
    fields["rate_limit_info"] = dict(rate_limit_info) if rate_limit_info else None
    fields["mode_classification_reasons"] = tuple(reasons)
    fields["commands_tested"] = tuple(sorted(set(commands_tested)))
    fields["failure_categories"] = tuple(sorted(failure_categories))
    fields["redacted_diagnostic_summary"] = tuple(sorted(diagnostics))
    return CapabilityReport(capability_report_id=report_id, **fields), results  # type: ignore[arg-type]


def probe_capability_with_results(
    transport: ExternalTransport,
    *,
    target: str,
    timeout: float = 20.0,
    actions: tuple[str, ...] = EXTERNAL_ACTIONS,
    tested_at: str | None = None,
) -> tuple[CapabilityReport, Mapping[str, ExternalRawResponse]]:
    """Probe once and return the exact responses for safe downstream reuse."""
    return _build_capability_probe(
        transport, target=target, timeout=timeout, actions=actions, tested_at=tested_at
    )


def probe_capability(
    transport: ExternalTransport,
    *,
    target: str,
    timeout: float = 20.0,
    actions: tuple[str, ...] = EXTERNAL_ACTIONS,
    tested_at: str | None = None,
) -> CapabilityReport:
    report, _ = probe_capability_with_results(
        transport, target=target, timeout=timeout, actions=actions, tested_at=tested_at
    )
    return report


__all__ = [
    "CAPABILITY_PROBE_VERSION",
    "CAPABILITY_REPORT_SCHEMA_VERSION",
    "CapabilityReport",
    "classify_capability_mode",
    "probe_capability",
    "probe_capability_with_results",
]
