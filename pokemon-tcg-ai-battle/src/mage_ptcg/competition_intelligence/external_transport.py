"""Injectable transport abstraction for external (Kaggle) capability probing
and acquisition (O1-5 SS2).

Mirrors ``mage_ptcg.competition.probe``'s ``CompetitionTransport`` Protocol
pattern (a single narrow ``call`` method, a transport-neutral response type,
ordered/fixture/unavailable implementations selected by the caller) rather
than inventing an incompatible shape, but covers a wider O1-5 action set
(``own_submission_listing``, ``own_episode_listing``, ``own_logs``,
``public_logs``, ``team_submission_listing`` -- none of which C2b's probe
implements, since C2b only covers competition-level metadata/leaderboard/
files/replay actions with no episode identifier). Tests never require live
Kaggle access: ``FixtureTransport``/``RecordedResponseTransport`` give
deterministic responses, and ``UnavailableTransport`` is the default so a
caller must opt in to a live subprocess transport explicitly.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from dataclasses import dataclass, field, replace
from importlib.metadata import PackageNotFoundError, version as package_version
from pathlib import Path
from typing import Mapping, Protocol, Sequence

from mage_ptcg.competition.redaction import redact_value

# The full O1-5 external action namespace. Every action maps directly to one
# CapabilityReport availability field; see external_capability.py.
EXTERNAL_ACTIONS: tuple[str, ...] = (
    "competition_metadata",
    "own_submission_listing",
    "own_episode_listing",
    "replay",
    "own_logs",
    "public_logs",
    "public_artifacts",
    "leaderboard",
    "team_submission_listing",
)


@dataclass(frozen=True, slots=True)
class ExternalRequest:
    """Typed parameters for one documented read-only Kaggle CLI action.

    ``target`` used to overload competition, submission, episode, and team
    identifiers.  Keeping those identifiers separate prevents an episode id
    and an agent index from being smuggled through a colon-delimited string.
    ``from_legacy`` exists only for callers still using the O1 protocol.
    """

    action: str
    competition: str | None = None
    submission_id: str | None = None
    episode_id: str | None = None
    team_id: str | None = None
    agent_index: int | None = None

    def __post_init__(self) -> None:
        if self.action not in EXTERNAL_ACTIONS:
            raise TransportError(f"unknown external action {self.action!r}")
        required: dict[str, tuple[str, ...]] = {
            "own_submission_listing": ("competition",),
            "leaderboard": ("competition",),
            "public_artifacts": ("competition",),
            "own_episode_listing": ("submission_id",),
            "replay": ("episode_id",),
            "own_logs": ("episode_id", "agent_index"),
            "team_submission_listing": ("team_id",),
        }
        for field_name in required.get(self.action, ()):
            value = getattr(self, field_name)
            if value is None or (isinstance(value, str) and not value):
                raise TransportError(f"{self.action} requires {field_name}")
        if self.agent_index is not None and (type(self.agent_index) is not int or self.agent_index < 0):
            raise TransportError("agent_index must be a non-negative integer")

    @property
    def legacy_target(self) -> str:
        for value in (self.competition, self.submission_id, self.episode_id, self.team_id):
            if value:
                return value
        return ""

    @classmethod
    def from_legacy(cls, action: str, target: str) -> "ExternalRequest":
        fields = {"action": action}
        if action in {"own_submission_listing", "leaderboard", "public_artifacts", "competition_metadata"}:
            fields["competition"] = target
        elif action == "own_episode_listing":
            fields["submission_id"] = target
        elif action in {"replay", "public_logs"}:
            fields["episode_id"] = target
        elif action == "own_logs":
            # Legacy callers cannot safely represent the required agent index.
            raise TransportError("own_logs requires ExternalRequest(episode_id, agent_index); legacy target is unsupported")
        elif action == "team_submission_listing":
            fields["team_id"] = target
        return cls(**fields)

# Failure category taxonomy. A transport must classify every failure into
# exactly one of these (never leave error_type=None on failure).
FAILURE_RATE_LIMITED = "rate_limited"
FAILURE_AUTHENTICATION = "authentication_error"
FAILURE_PERMISSION_DENIED = "permission_denied"
FAILURE_NETWORK = "network_error"
FAILURE_NOT_FOUND = "not_found"
FAILURE_SCHEMA_ERROR = "schema_error"
FAILURE_TIMEOUT = "timeout"
FAILURE_DEPENDENCY_MISSING = "dependency_missing"
FAILURE_UNAVAILABLE = "unavailable"
FAILURE_UNKNOWN = "unknown_error"

RETRYABLE_FAILURES = frozenset({FAILURE_RATE_LIMITED, FAILURE_TIMEOUT})

_MAX_CAPTURED_BYTES = 1_000_000
_MAX_REPLAY_BYTES = 32_000_000


class TransportError(RuntimeError):
    """Raised for a transport-construction/usage error (not a probe failure)."""


@dataclass(frozen=True, slots=True)
class ExternalRawResponse:
    """Transport-neutral response. Must never contain raw credentials/tokens."""

    action: str
    target: str
    success: bool
    body: bytes = b""
    # stderr is retained separately from stdout/body.  It is never parsed as
    # payload data, but lets the raw archive preserve CLI warnings verbatim.
    stderr: bytes = b""
    content_type: str | None = None
    status_code: int | None = None
    return_code: int | None = None
    error_type: str | None = None
    error_message: str | None = None
    retryable: bool = False
    client_name: str = "unknown"
    client_version: str | None = None
    command: tuple[str, ...] | None = None
    rate_limit_info: Mapping[str, object] | None = None
    attempt_count: int = 1
    # Only fixture/recorded test material may establish a baseline without a
    # pre-existing live schema.  A live response is never trusted merely
    # because it arrived first.
    trusted_test_baseline: bool = False

    def __post_init__(self) -> None:
        if not self.success and self.error_type is None:
            raise TransportError(f"a failed ExternalRawResponse for action {self.action!r} must set error_type")


class ExternalTransport(Protocol):
    def call(self, action: str, *, target: str, timeout: float) -> ExternalRawResponse: ...


def call_request(transport: ExternalTransport, request: ExternalRequest, *, timeout: float) -> ExternalRawResponse:
    """Dispatch an O3 request while preserving the O1 transport protocol."""
    typed_call = getattr(transport, "call_request", None)
    if callable(typed_call):
        return typed_call(request, timeout=timeout)
    return transport.call(request.action, target=request.legacy_target, timeout=timeout)


def _sanitize(message: str | None) -> str | None:
    if message is None:
        return None
    return str(redact_value(message))[:500]


# --------------------------------------------------------------------------- #
# UnavailableTransport -- the default; live mode is opt-in only
# --------------------------------------------------------------------------- #


class UnavailableTransport:
    """Always reports every action as unavailable without attempting anything.

    This is the CLI's default transport: a caller must explicitly select
    ``--fixture``/``--recorded``/``--live`` to get any other behavior, so
    "did we actually probe" is never ambiguous.
    """

    def call(self, action: str, *, target: str, timeout: float) -> ExternalRawResponse:
        return ExternalRawResponse(
            action=action,
            target=target,
            success=False,
            error_type=FAILURE_UNAVAILABLE,
            error_message="external transport disabled (no transport selected)",
            client_name="unavailable",
        )


# --------------------------------------------------------------------------- #
# FixtureTransport -- deterministic, in-memory, no credentials required
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class FixtureTransport:
    """Deterministic canned responses keyed by action, for tests and dry-runs."""

    responses: Mapping[str, ExternalRawResponse] = field(default_factory=dict)
    client_version: str | None = "fixture-1"

    def call(self, action: str, *, target: str, timeout: float) -> ExternalRawResponse:
        response = self.responses.get(action)
        if response is not None:
            return replace(response, trusted_test_baseline=True)
        return ExternalRawResponse(
            action=action,
            target=target,
            success=False,
            error_type=FAILURE_UNAVAILABLE,
            error_message=f"fixture has no response configured for action {action!r}",
            client_name="fixture",
            client_version=self.client_version,
        )


# --------------------------------------------------------------------------- #
# RecordedResponseTransport -- replays a previously-archived probe response
# --------------------------------------------------------------------------- #


class RecordedResponseTransport:
    """Replays JSON-recorded responses from a directory of ``<action>.json`` files.

    Each file holds ``{"success": bool, "body": <json value or null>,
    "content_type": str|null, "error_type": str|null, "error_message":
    str|null}``; this is the same shape a caller can derive from an archived
    ``external_capability`` probe run, letting a prior live probe's output be
    replayed deterministically without live access.
    """

    def __init__(self, recordings_dir: str | Path) -> None:
        self._root = Path(recordings_dir)

    def call(self, action: str, *, target: str, timeout: float) -> ExternalRawResponse:
        path = self._root / f"{action}.json"
        if not path.is_file():
            return ExternalRawResponse(
                action=action,
                target=target,
                success=False,
                error_type=FAILURE_UNAVAILABLE,
                error_message=f"no recorded response for action {action!r}",
                client_name="recorded",
            )
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return ExternalRawResponse(
                action=action,
                target=target,
                success=False,
                error_type=FAILURE_SCHEMA_ERROR,
                error_message=f"recorded response is malformed: {exc}",
                client_name="recorded",
            )
        body_value = raw.get("body")
        body = json.dumps(body_value, sort_keys=True).encode("utf-8") if body_value is not None else b""
        success = bool(raw.get("success", False))
        return ExternalRawResponse(
            action=action,
            target=target,
            success=success,
            body=body,
            content_type=raw.get("content_type") or ("application/json" if body_value is not None else None),
            error_type=(raw.get("error_type") or (FAILURE_UNKNOWN if not success else None)),
            error_message=_sanitize(raw.get("error_message")),
            client_name="recorded",
            client_version=raw.get("client_version"),
            trusted_test_baseline=True,
        )


# --------------------------------------------------------------------------- #
# SubprocessKaggleTransport -- real live transport, disabled unless selected
# --------------------------------------------------------------------------- #

_RETRYABLE_RETURN_CODES = frozenset({429})
_BACKOFF_BASE_SECONDS = 0.5
_BACKOFF_MAX_SECONDS = 8.0


def _classify_cli_failure(return_code: int, stderr_text: str) -> str:
    lowered = stderr_text.lower()
    if return_code == 429 or "rate limit" in lowered or "too many requests" in lowered:
        return FAILURE_RATE_LIMITED
    if "401" in lowered or "unauthorized" in lowered or "could not find kaggle.json" in lowered:
        return FAILURE_AUTHENTICATION
    if "403" in lowered or "forbidden" in lowered or "permission denied" in lowered:
        return FAILURE_PERMISSION_DENIED
    if any(marker in lowered for marker in ("name or service not known", "temporary failure in name resolution", "connection reset", "connection refused", "network is unreachable", "max retries exceeded")):
        return FAILURE_NETWORK
    if "404" in lowered or "not found" in lowered:
        return FAILURE_NOT_FOUND
    return FAILURE_UNKNOWN


def _cli_version(executable: str) -> str | None:
    try:
        completed = subprocess.run((executable, "--version"), capture_output=True, timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    text = (completed.stdout or completed.stderr).decode("utf-8", errors="replace").strip()
    return _sanitize(text) if text else None


@dataclass(frozen=True, slots=True)
class SubprocessKaggleTransport:
    """Live transport over the official ``kaggle`` CLI. Opt-in only.

    Every invocation uses an explicit argv tuple (never a shell string), a
    hard timeout, bounded captured output, and at most ``max_retries``
    bounded-exponential-backoff retries -- and only for failures classified
    as retryable (rate limiting / timeout), never for auth or not-found
    errors, which retrying cannot fix.
    """

    max_retries: int = 2
    max_output_bytes: int = _MAX_CAPTURED_BYTES
    max_replay_bytes: int = _MAX_REPLAY_BYTES
    sdk_episode_agents: bool = False

    @staticmethod
    def _executable() -> str | None:
        configured = os.environ.get("KAGGLE_CLI_PATH")
        if configured and Path(configured).is_file() and os.access(configured, os.X_OK):
            return configured
        # Prefer a project-local client so its dependencies and auth handling
        # match the worktree; fall back to the inherited PATH for compatibility.
        project_cli = Path(__file__).resolve().parents[3] / ".venv" / "bin" / "kaggle"
        if project_cli.is_file() and os.access(project_cli, os.X_OK):
            return str(project_cli)
        return shutil.which("kaggle")

    def call(self, action: str, *, target: str, timeout: float) -> ExternalRawResponse:
        return self.call_request(ExternalRequest.from_legacy(action, target), timeout=timeout)

    def call_request(self, request: ExternalRequest, *, timeout: float) -> ExternalRawResponse:
        """Execute one O3 typed request with Kaggle CLI 2.2.3 syntax.

        Replay and log commands intentionally use a temporary output directory:
        unlike list commands they write a file rather than stdout.  The file
        bytes are returned to the existing raw-archive pipeline, never kept in
        the repository.  ``public_logs`` and ``competition_metadata`` are
        explicitly unavailable because the public CLI exposes neither a safe
        third-party-log endpoint nor the removed ``competitions view`` command.
        """
        action = request.action
        target = request.legacy_target
        if action == "own_episode_listing" and self.sdk_episode_agents:
            return self._call_sdk_episode_listing(request, timeout=timeout)
        if action in {"public_logs", "competition_metadata"}:
            return ExternalRawResponse(
                action=action, target=target, success=False, error_type=FAILURE_UNAVAILABLE,
                error_message=("NOT_SUPPORTED_BY_PUBLIC_API" if action == "public_logs" else "UNAVAILABLE_BY_KAGGLE_CLI_2_2_3"),
                client_name="kaggle-cli",
            )
        executable = self._executable()
        if executable is None:
            return ExternalRawResponse(
                action=action,
                target=target,
                success=False,
                error_type=FAILURE_DEPENDENCY_MISSING,
                error_message="official Kaggle CLI is not installed",
                client_name="kaggle-cli",
            )
        version = _cli_version(executable)
        command_parts = self._command_parts(request)
        if command_parts is None:
            return ExternalRawResponse(
                action=action, target=target, success=False, error_type=FAILURE_UNAVAILABLE,
                error_message="NOT_SUPPORTED_BY_PUBLIC_API", client_name="kaggle-cli", client_version=version,
            )

        attempt = 0
        last_response: ExternalRawResponse | None = None
        while attempt <= self.max_retries:
            attempt += 1
            response = self._run_once(request, (executable, *command_parts), timeout, version, attempt)
            if response.success or response.error_type not in RETRYABLE_FAILURES or attempt > self.max_retries:
                return response
            last_response = response
            backoff = min(_BACKOFF_MAX_SECONDS, _BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)))
            time.sleep(backoff)
        assert last_response is not None
        return last_response

    @staticmethod
    def _sdk_version() -> str | None:
        try:
            return package_version("kaggle")
        except PackageNotFoundError:
            return None

    def _call_sdk_episode_listing(self, request: ExternalRequest, *, timeout: float) -> ExternalRawResponse:
        """Read own submission episodes with the typed SDK, preserving agents.

        Kaggle CLI 2.2.3 renders only ``episode_fields`` and discards its
        nested ``episode_agent_fields``.  This deliberately narrow SDK route
        is used only for own episode listing; all other actions retain the
        bounded official CLI transport.
        """
        target = request.legacy_target
        sdk_version = self._sdk_version()
        try:
            submission_id = int(request.submission_id or "")
        except ValueError:
            return ExternalRawResponse(
                action=request.action, target=target, success=False, error_type=FAILURE_SCHEMA_ERROR,
                error_message="own episode listing requires a numeric Kaggle submission ID",
                client_name="kaggle-sdk", client_version=sdk_version,
            )

        def invoke() -> bytes:
            # Importing Kaggle authenticates using its supported credential
            # mechanisms.  No credential value is read, logged, or archived.
            from kaggle.api.kaggle_api_extended import KaggleApi
            from kagglesdk.competitions.types.competition_api_service import ApiListSubmissionEpisodesRequest

            api = KaggleApi()
            sdk_request = ApiListSubmissionEpisodesRequest()
            sdk_request.submission_id = submission_id
            with api.build_kaggle_client() as client:
                response = client.competitions.competition_api_client.list_submission_episodes(sdk_request)
            return json.dumps(response.to_dict(ignore_defaults=False), sort_keys=True, separators=(",", ":")).encode("utf-8")

        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mage-kaggle-sdk")
        future = executor.submit(invoke)
        try:
            body = future.result(timeout=timeout)
        except FuturesTimeoutError:
            future.cancel()
            executor.shutdown(wait=False, cancel_futures=True)
            return ExternalRawResponse(
                action=request.action, target=target, success=False, error_type=FAILURE_TIMEOUT,
                error_message=f"Kaggle SDK timed out after {timeout}s", retryable=True,
                client_name="kaggle-sdk", client_version=sdk_version,
                command=("kaggle-sdk", "competitions", "episodes"),
            )
        except ModuleNotFoundError:
            executor.shutdown(wait=False, cancel_futures=True)
            return ExternalRawResponse(
                action=request.action, target=target, success=False, error_type=FAILURE_DEPENDENCY_MISSING,
                error_message="official Kaggle Python SDK is not installed",
                client_name="kaggle-sdk", client_version=sdk_version,
            )
        except Exception as exc:  # SDK exception types vary across releases; retain only sanitized taxonomy.
            executor.shutdown(wait=False, cancel_futures=True)
            message = _sanitize(str(exc)) or "Kaggle SDK request failed"
            return ExternalRawResponse(
                action=request.action, target=target, success=False,
                error_type=_classify_cli_failure(1, message), error_message=message,
                client_name="kaggle-sdk", client_version=sdk_version,
                command=("kaggle-sdk", "competitions", "episodes"),
            )
        executor.shutdown(wait=True)
        if len(body) > self.max_output_bytes:
            return ExternalRawResponse(
                action=request.action, target=target, success=False, error_type=FAILURE_SCHEMA_ERROR,
                error_message="Kaggle SDK episode response exceeds configured size limit",
                client_name="kaggle-sdk", client_version=sdk_version,
            )
        return ExternalRawResponse(
            action=request.action, target=target, success=True, body=body, content_type="application/json",
            client_name="kaggle-sdk", client_version=sdk_version,
            command=("kaggle-sdk", "competitions", "episodes"),
        )

    @staticmethod
    def _command_parts(request: ExternalRequest) -> tuple[str, ...] | None:
        action = request.action
        if action == "own_submission_listing":
            return ("competitions", "submissions", request.competition or "", "--format", "json", "--quiet")
        if action == "leaderboard":
            return ("competitions", "leaderboard", "--show", request.competition or "", "--format", "json", "--quiet")
        if action == "public_artifacts":
            return ("competitions", "files", request.competition or "", "--format", "json", "--quiet")
        if action == "own_episode_listing":
            return ("competitions", "episodes", request.submission_id or "", "--format", "json", "--quiet")
        if action == "team_submission_listing":
            return ("competitions", "team-submissions", request.team_id or "", "--format", "json", "--quiet")
        if action == "replay":
            return ("competitions", "replay", request.episode_id or "", "--quiet")
        if action == "own_logs":
            return ("competitions", "logs", request.episode_id or "", str(request.agent_index), "--quiet")
        return None

    def _run_once(
        self, request: ExternalRequest, argv: Sequence[str], timeout: float, version: str | None, attempt: int
    ) -> ExternalRawResponse:
        action, target = request.action, request.legacy_target
        temp_dir: tempfile.TemporaryDirectory[str] | None = None
        invocation = list(argv)
        if action in {"replay", "own_logs"}:
            temp_dir = tempfile.TemporaryDirectory(prefix="mage-kaggle-read-")
            invocation.extend(("--path", temp_dir.name))
        try:
            completed = subprocess.run(invocation, capture_output=True, timeout=timeout, check=False, env=os.environ.copy())
        except subprocess.TimeoutExpired:
            if temp_dir is not None:
                temp_dir.cleanup()
            return ExternalRawResponse(
                action=action,
                target=target,
                success=False,
                error_type=FAILURE_TIMEOUT,
                error_message=f"kaggle CLI timed out after {timeout}s",
                retryable=True,
                client_name="kaggle-cli",
                client_version=version,
                command=tuple(invocation),
                attempt_count=attempt,
            )
        except OSError as exc:
            if temp_dir is not None:
                temp_dir.cleanup()
            return ExternalRawResponse(
                action=action,
                target=target,
                success=False,
                error_type=FAILURE_UNKNOWN,
                error_message=_sanitize(str(exc)),
                client_name="kaggle-cli",
                client_version=version,
                command=tuple(invocation),
                attempt_count=attempt,
            )
        output_limit = self.max_replay_bytes if action == "replay" else self.max_output_bytes
        stdout = completed.stdout[: output_limit]
        stderr = completed.stderr[: self.max_output_bytes]
        stderr_text = completed.stderr[: self.max_output_bytes].decode("utf-8", errors="replace")
        if completed.returncode == 0:
            if temp_dir is not None:
                files = sorted(path for path in Path(temp_dir.name).rglob("*") if path.is_file())
                if len(files) != 1:
                    temp_dir.cleanup()
                    return ExternalRawResponse(
                        action=action, target=target, success=False, return_code=completed.returncode,
                        error_type=FAILURE_SCHEMA_ERROR, error_message="CLI download did not produce exactly one file",
                        client_name="kaggle-cli", client_version=version, command=tuple(invocation), attempt_count=attempt,
                    )
                stdout = files[0].read_bytes()[: output_limit]
            content_type = "application/json" if action == "replay" else "text/plain" if action == "own_logs" else "application/json"
            if temp_dir is not None:
                temp_dir.cleanup()
            return ExternalRawResponse(
                action=action,
                target=target,
                success=True,
                body=stdout,
                stderr=stderr,
                content_type=content_type,
                return_code=0,
                client_name="kaggle-cli",
                client_version=version,
                command=tuple(invocation),
                attempt_count=attempt,
            )
        if temp_dir is not None:
            temp_dir.cleanup()
        category = _classify_cli_failure(completed.returncode, stderr_text)
        return ExternalRawResponse(
            action=action,
            target=target,
            success=False,
            return_code=completed.returncode,
            error_type=category,
            error_message=_sanitize(stderr_text),
            retryable=category in RETRYABLE_FAILURES,
            client_name="kaggle-cli",
            client_version=version,
            command=tuple(invocation),
            attempt_count=attempt,
        )


__all__ = [
    "EXTERNAL_ACTIONS",
    "FAILURE_AUTHENTICATION",
    "FAILURE_PERMISSION_DENIED",
    "FAILURE_NETWORK",
    "FAILURE_DEPENDENCY_MISSING",
    "FAILURE_NOT_FOUND",
    "FAILURE_RATE_LIMITED",
    "FAILURE_SCHEMA_ERROR",
    "FAILURE_TIMEOUT",
    "FAILURE_UNAVAILABLE",
    "FAILURE_UNKNOWN",
    "RETRYABLE_FAILURES",
    "ExternalRawResponse",
    "ExternalRequest",
    "ExternalTransport",
    "FixtureTransport",
    "RecordedResponseTransport",
    "SubprocessKaggleTransport",
    "TransportError",
    "UnavailableTransport",
    "call_request",
]
