"""Official-client capability probe and fail-closed competition mode classifier."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from .archive import archive_probe
from .fingerprint import fingerprint_document
from .redaction import redact_value

PROBE_SCHEMA_VERSION = 1
PROBE_VERSION = "competition-probe-v0"
CLASSIFICATION_VERSION = 1


class CompetitionMode(StrEnum):
    FULL_REPLAY = "FULL_REPLAY"
    REPLAY_WITHOUT_LEGAL_OPTIONS = "REPLAY_WITHOUT_LEGAL_OPTIONS"
    PUBLIC_ARTIFACTS_ONLY = "PUBLIC_ARTIFACTS_ONLY"
    LOCAL_ONLY = "LOCAL_ONLY"


@dataclass(frozen=True)
class RawResponse:
    """Transport-neutral official response.  It must never contain request secrets."""

    action: str
    requested_capability: str
    official_action: str
    success: bool
    body: bytes = b""
    content_type: str | None = None
    status_code: int | None = None
    return_code: int | None = None
    error_type: str | None = None
    error_message: str | None = None
    retryable: bool = False
    client_name: str = "unknown"
    client_version: str | None = None
    command: tuple[str, ...] | None = None


class CompetitionTransport(Protocol):
    def probe(self, action: str, competition: str, timeout: float) -> RawResponse: ...


_ACTIONS: tuple[tuple[str, str], ...] = (
    ("metadata", "competition_metadata"),
    ("public_files", "public_files_or_artifacts"),
    ("leaderboard", "public_leaderboard"),
    ("submissions", "submission_visibility"),
    ("replay", "replay_or_episode_artifact"),
)


def _package_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def _sanitized_error(message: str | None) -> str | None:
    if message is None:
        return None
    return str(redact_value(message))[:500]


def detect_authentication() -> tuple[bool, str]:
    """Report the available credential *shape* without reading secret values.

    Presence is only a source hint.  Callers must still perform a harmless
    capability probe before treating the client as authenticated.
    """
    if os.environ.get("KAGGLE_API_TOKEN") or os.environ.get("KAGGLE_ACCESS_TOKEN"):
        return True, "environment_access_token"
    if os.environ.get("KAGGLE_USERNAME") and os.environ.get("KAGGLE_KEY"):
        return True, "environment_legacy"
    config_dir = Path(os.environ.get("KAGGLE_CONFIG_DIR", Path.home() / ".kaggle"))
    if (config_dir / "credentials.json").is_file():
        return True, "oauth_credentials_file"
    if (config_dir / "kaggle.json").is_file():
        return True, "legacy_config_file"
    return False, "none"


def _parse_json(body: bytes, content_type: str | None) -> tuple[Any | None, str | None]:
    if not body:
        return None, "zero_byte_response"
    likely_json = (content_type and "json" in content_type.lower()) or body.lstrip().startswith((b"{", b"["))
    if not likely_json:
        return None, "non_json_response"
    try:
        return json.loads(body.decode("utf-8")), None
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, "malformed_json"


def _field_paths(value: Any, prefix: str = "$") -> list[str]:
    paths: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            path = f"{prefix}.{key}"
            paths.append(path)
            paths.extend(_field_paths(child, path))
    elif isinstance(value, list):
        for child in value:
            paths.extend(_field_paths(child, f"{prefix}[]"))
    return paths


def detect_replay_fields(value: Any) -> tuple[list[str], list[str], bool]:
    """Detect evidence conservatively; a field name alone never proves full replay."""
    paths = _field_paths(value)
    legal: list[str] = []
    progression: list[str] = []
    for path in paths:
        final = path.rsplit(".", 1)[-1].lower()
        normalized = re.sub(r"[^a-z]", "", final)
        parent = path.rsplit(".", 2)[-2].lower() if path.count(".") >= 2 else ""
        if normalized in {"legaloptions", "legaloption", "legalmoves", "legalactions"}:
            legal.append(path)
        elif normalized in {"option", "options"} and any(word in parent for word in ("select", "decision", "action")):
            legal.append(path)
        if normalized in {"steps", "events", "actions", "states", "turns", "decisions"}:
            progression.append(path)
    return sorted(set(legal)), sorted(set(progression)), bool(progression)


def classify_mode(
    *,
    replay_retrieved: bool,
    replay_has_progression: bool,
    legal_option_fields: tuple[str, ...] | list[str],
    schema_fingerprint: str | None,
    public_artifacts_retrieved: bool,
) -> tuple[CompetitionMode, list[str]]:
    """Pure, fail-closed classification for the four C2b capability modes."""
    reasons: list[str] = []
    if replay_retrieved and replay_has_progression:
        if legal_option_fields and schema_fingerprint:
            return CompetitionMode.FULL_REPLAY, [
                "replay_or_episode_bytes_retrieved",
                "state_or_action_progression_detected",
                "legal_option_fields_detected",
                "schema_fingerprint_recorded",
            ]
        reasons.extend(["replay_or_episode_bytes_retrieved", "state_or_action_progression_detected"])
        if not legal_option_fields:
            reasons.append("complete_legal_option_set_not_proven")
        if not schema_fingerprint:
            reasons.append("schema_fingerprint_not_recorded")
        return CompetitionMode.REPLAY_WITHOUT_LEGAL_OPTIONS, reasons
    if replay_retrieved:
        reasons.append("replay_bytes_lacked_proven_state_or_action_progression")
    if public_artifacts_retrieved:
        reasons.append("public_competition_artifact_retrieved")
        return CompetitionMode.PUBLIC_ARTIFACTS_ONLY, reasons
    reasons.append("no_remote_competition_capability_proven")
    return CompetitionMode.LOCAL_ONLY, reasons


class OfficialKaggleCliTransport:
    """Use only documented CLI actions discovered from the installed CLI help."""

    _COMMANDS: dict[str, tuple[str, ...]] = {
        "public_files": ("competitions", "files"),
        "leaderboard": ("competitions", "leaderboard"),
        "submissions": ("competitions", "submissions"),
    }

    def _unavailable(self, action: str, capability: str, error_type: str, message: str) -> RawResponse:
        return RawResponse(
            action=action,
            requested_capability=capability,
            official_action="kaggle_cli",
            success=False,
            error_type=error_type,
            error_message=message,
            client_name="kaggle-cli",
        )

    def probe(self, action: str, competition: str, timeout: float) -> RawResponse:
        capability = dict(_ACTIONS)[action]
        executable = shutil.which("kaggle")
        if executable is None:
            return self._unavailable(action, capability, "dependency_missing", "official Kaggle CLI is not installed")
        command_parts = self._COMMANDS.get(action)
        if command_parts is None:
            return self._unavailable(
                action, capability, "official_action_unavailable", "no documented CLI action is available without an episode identifier"
            )
        command = (executable, *command_parts, competition, "--json")
        try:
            completed = subprocess.run(command, capture_output=True, timeout=timeout, check=False)
        except subprocess.TimeoutExpired:
            return self._unavailable(action, capability, "timeout", "official Kaggle CLI timed out")
        body = completed.stdout or completed.stderr
        error = None if completed.returncode == 0 else _sanitized_error(completed.stderr.decode("utf-8", errors="replace"))
        return RawResponse(
            action=action,
            requested_capability=capability,
            official_action=" ".join(command_parts),
            success=completed.returncode == 0,
            body=body,
            content_type="application/json" if completed.returncode == 0 else "text/plain",
            return_code=completed.returncode,
            error_type=None if completed.returncode == 0 else "cli_error",
            error_message=error,
            retryable=completed.returncode in {429, 75},
            client_name="kaggle-cli",
            client_version=_cli_version(executable),
            command=("kaggle", *command_parts, competition, "--json"),
        )


def _cli_version(executable: str) -> str | None:
    try:
        completed = subprocess.run((executable, "--version"), capture_output=True, timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    text = (completed.stdout or completed.stderr).decode("utf-8", errors="replace").strip()
    return _sanitized_error(text)


class OfficialKagglePythonTransport:
    """Small adapter for the installed official Python client's documented methods.

    Method availability is inspected before invocation so an SDK version that
    lacks a capability is reported as such rather than treated as a replay.
    """

    _METHODS: dict[str, tuple[str, ...]] = {
        "metadata": ("competitions_list",),
        "public_files": ("competition_list_files",),
        "leaderboard": ("competition_leaderboard_view",),
        "submissions": ("competition_list_submissions", "competitions_submissions_list"),
    }

    def probe(self, action: str, competition: str, timeout: float) -> RawResponse:
        capability = dict(_ACTIONS)[action]
        try:
            from kaggle.api.kaggle_api_extended import KaggleApi
        except ModuleNotFoundError:
            return RawResponse(
                action=action,
                requested_capability=capability,
                official_action="kaggle_python_client",
                success=False,
                error_type="dependency_missing",
                error_message="official Kaggle Python client is not installed",
                client_name="kaggle-python",
            )
        methods = self._METHODS.get(action)
        if not methods:
            return RawResponse(
                action=action,
                requested_capability=capability,
                official_action="kaggle_python_client",
                success=False,
                error_type="official_action_unavailable",
                error_message="no official Python action is available without an episode identifier",
                client_name="kaggle-python",
                client_version=_package_version("kaggle"),
            )
        try:
            client = KaggleApi()
            client.authenticate()
        except (OSError, ValueError, TypeError, RuntimeError) as exc:
            return RawResponse(
                action=action,
                requested_capability=capability,
                official_action="kaggle_python_client.authenticate",
                success=False,
                error_type="authentication_failure",
                error_message=_sanitized_error(str(exc)),
                client_name="kaggle-python",
                client_version=_package_version("kaggle"),
            )
        for method_name in methods:
            method = getattr(client, method_name, None)
            if callable(method):
                try:
                    value = (
                        method(search=competition, page_size=20)
                        if method_name == "competitions_list"
                        else method(competition)
                    )
                    body = json.dumps(value, default=_json_default, sort_keys=True).encode("utf-8")
                except (OSError, ValueError, TypeError, RuntimeError) as exc:
                    return RawResponse(
                        action=action,
                        requested_capability=capability,
                        official_action=f"KaggleApi.{method_name}",
                        success=False,
                        error_type="official_client_error",
                        error_message=_sanitized_error(str(exc)),
                        client_name="kaggle-python",
                        client_version=_package_version("kaggle"),
                    )
                return RawResponse(
                    action=action,
                    requested_capability=capability,
                    official_action=f"KaggleApi.{method_name}",
                    success=True,
                    body=body,
                    content_type="application/json",
                    client_name="kaggle-python",
                    client_version=_package_version("kaggle"),
                )
        return RawResponse(
            action=action,
            requested_capability=capability,
            official_action="kaggle_python_client",
            success=False,
            error_type="official_action_unavailable",
            error_message="installed official Python client does not expose this action",
            client_name="kaggle-python",
            client_version=_package_version("kaggle"),
        )


def _json_default(value: Any) -> Any:
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return to_dict()
    if hasattr(value, "__dict__"):
        return vars(value)
    return str(value)


class OrderedOfficialTransport:
    """Try CLI first, then the official Python client only when CLI is absent."""

    def __init__(self) -> None:
        self.cli = OfficialKaggleCliTransport()
        self.python = OfficialKagglePythonTransport()

    def probe(self, action: str, competition: str, timeout: float) -> RawResponse:
        cli_response = self.cli.probe(action, competition, timeout)
        if cli_response.error_type not in {
            "dependency_missing",
            "official_action_unavailable",
        }:
            return cli_response
        return self.python.probe(action, competition, timeout)


class ProbeRunner:
    """Run ordered, injectable probes and archive each outcome independently."""

    def __init__(self, transport: CompetitionTransport | None = None) -> None:
        self.transport = transport or OrderedOfficialTransport()

    def run(
        self,
        *,
        competition: str,
        output_dir: str | Path,
        timeout: float = 20.0,
        metadata_only: bool = False,
        offline: bool = False,
        force: bool = False,
        probe_id_prefix: str | None = None,
    ) -> dict[str, Any]:
        if not competition or any(character.isspace() for character in competition):
            raise ValueError("competition must be a non-empty slug without whitespace")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        started = datetime.now(UTC)
        id_prefix = probe_id_prefix or started.strftime("%Y%m%dT%H%M%SZ")
        auth_detected, auth_source = detect_authentication()
        selected = _ACTIONS[:1] if metadata_only else _ACTIONS
        results: list[dict[str, Any]] = []
        public_retrieved = False
        replay_retrieved = False
        replay_progression = False
        legal_fields: list[str] = []
        replay_fingerprint: str | None = None
        for index, (action, capability) in enumerate(selected):
            if offline:
                response = RawResponse(
                    action=action,
                    requested_capability=capability,
                    official_action="offline",
                    success=False,
                    error_type="offline",
                    error_message="remote probe disabled by --offline",
                    client_name="offline",
                )
            else:
                response = self.transport.probe(action, competition, timeout)
            parsed, parse_failure = _parse_json(response.body, response.content_type)
            detected_legal, detected_progression, has_progression = detect_replay_fields(parsed) if parsed is not None else ([], [], False)
            fingerprint = fingerprint_document(parsed)["sha256"] if parsed is not None else None
            content_hash = hashlib.sha256(response.body).hexdigest()
            completed = datetime.now(UTC)
            entry = {
                "schema_version": PROBE_SCHEMA_VERSION,
                "probe_version": PROBE_VERSION,
                "started_at": started.isoformat(),
                "completed_at": completed.isoformat(),
                "source_head": _source_head(),
                "action": action,
                "requested_capability": response.requested_capability,
                "official_action_identifier": response.official_action,
                "command": list(response.command) if response.command else None,
                "client_name": response.client_name,
                "client_version": response.client_version,
                "python_version": platform.python_version(),
                "kaggle_package_version": _package_version("kaggle"),
                "kaggle_environments_version": _package_version("kaggle-environments"),
                "authentication_detected": auth_detected,
                "authentication_source_type": auth_source,
                "success": response.success,
                "http_status": response.status_code,
                "process_return_code": response.return_code,
                "response_content_type": response.content_type,
                "response_byte_size": len(response.body),
                "response_sha256": content_hash,
                "error_type": response.error_type,
                "sanitized_error_message": _sanitized_error(response.error_message),
                "retryable": response.retryable,
                "parse_failure": parse_failure,
                "detected_replay_fields": detected_progression,
                "detected_legal_option_fields": detected_legal,
                "schema_fingerprint": fingerprint,
                "redaction_version": 1,
            }
            probe_id = f"{id_prefix}-{index:02d}-{action}"
            archive_probe(
                output_dir=output_dir,
                probe_id=probe_id,
                manifest={"competition": competition, "probe_id": probe_id, "action": action},
                summary=entry,
                response=response.body if response.success or response.body else None,
                response_json=parsed,
                error=None
                if response.success
                else {
                    "error_type": response.error_type,
                    "message": _sanitized_error(response.error_message),
                    "retryable": response.retryable,
                },
                force=force,
            )
            results.append(entry)
            if action in {"metadata", "public_files", "leaderboard", "submissions"} and response.success:
                public_retrieved = True
            if action == "replay" and response.success:
                replay_retrieved = True
                replay_progression = has_progression
                legal_fields.extend(detected_legal)
                replay_fingerprint = str(fingerprint) if fingerprint else None
        mode, reasons = classify_mode(
            replay_retrieved=replay_retrieved,
            replay_has_progression=replay_progression,
            legal_option_fields=legal_fields,
            schema_fingerprint=replay_fingerprint,
            public_artifacts_retrieved=public_retrieved,
        )
        completed = datetime.now(UTC)
        report = {
            "schema_version": PROBE_SCHEMA_VERSION,
            "probe_version": PROBE_VERSION,
            "classification_version": CLASSIFICATION_VERSION,
            "competition": competition,
            "started_at": started.isoformat(),
            "completed_at": completed.isoformat(),
            "source_head": _source_head(),
            "authentication_detected": auth_detected,
            "authentication_source_type": auth_source,
            "classified_mode": mode.value,
            "mode_classification_reasons": reasons,
            "limitations": _limitations(results),
            "actions": results,
        }
        report_id = f"{id_prefix}-report"
        report_path = archive_probe(
            output_dir=output_dir,
            probe_id=report_id,
            manifest={"competition": competition, "probe_id": report_id, "action": "probe_report"},
            summary={
                "schema_version": PROBE_SCHEMA_VERSION,
                "probe_version": PROBE_VERSION,
                "classified_mode": mode.value,
                "mode_classification_reasons": reasons,
                "response_content_type": "application/json",
            },
            response=json.dumps(report, ensure_ascii=False, sort_keys=True).encode("utf-8"),
            response_json=report,
            error=None,
            force=force,
        )
        # Keep stdout/report safe to share: never expose the caller's absolute
        # local path merely to identify an archive already rooted at output_dir.
        report["archive_summary_path"] = report_path.name
        return report


def _source_head() -> str | None:
    root = Path(__file__).resolve().parents[3]
    try:
        completed = subprocess.run(
            ("git", "-C", str(root), "rev-parse", "HEAD"), capture_output=True, timeout=5, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return completed.stdout.decode("ascii", errors="ignore").strip() if completed.returncode == 0 else None


def _limitations(results: list[dict[str, Any]]) -> list[str]:
    limitations: list[str] = []
    for entry in results:
        if not entry["success"]:
            limitations.append(f"{entry['action']}:{entry['error_type'] or 'unavailable'}")
        elif entry["parse_failure"]:
            limitations.append(f"{entry['action']}:{entry['parse_failure']}")
    return sorted(set(limitations))
