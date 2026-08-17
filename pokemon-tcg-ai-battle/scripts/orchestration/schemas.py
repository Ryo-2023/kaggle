"""Validated data contracts used by the Bootstrap Kernel."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping


class SchemaError(ValueError):
    """Raised when an orchestration document violates its schema."""


def _strings(value: Any, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise SchemaError(f"{field_name} must be a list of strings")
    return tuple(value)


def _mapping(value: Any, field_name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise SchemaError(f"{field_name} must be an object")
    return dict(value)


_VERIFICATION_ENVIRONMENT_KEYS = frozenset(
    {"LC_ALL", "LANG", "PATH", "PYTHONDONTWRITEBYTECODE", "PYTHONPATH"}
)


def _verification_environment(value: Any) -> dict[str, str]:
    """Validate explicit environment values for authoritative verification."""

    environment = _mapping(value, "verification_environment")
    for name, item in environment.items():
        if name not in _VERIFICATION_ENVIRONMENT_KEYS:
            raise SchemaError(f"verification_environment key is not allowed: {name}")
        if not isinstance(item, str):
            raise SchemaError(f"verification_environment.{name} must be a string")
        if "\x00" in item:
            raise SchemaError(f"verification_environment.{name} must not contain NUL")
    return environment


@dataclass(frozen=True)
class TaskContract:
    """Explicit, validated description of one isolated implementation task."""

    task_id: str
    role: str
    base_snapshot_id: str | None
    read_paths: tuple[str, ...]
    allowed_paths: tuple[str, ...]
    forbidden_paths: tuple[str, ...]
    protected_paths: tuple[str, ...]
    expected_outputs: dict[str, Any]
    verification_commands: tuple[tuple[str, ...], ...]
    acceptance_digest: str
    command_policy: dict[str, Any]
    environment_allowlist: tuple[str, ...]
    resource_budget: dict[str, Any]
    provider: dict[str, Any]
    external_model: dict[str, Any]
    verification_environment: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "TaskContract":
        """Parse and validate a contract loaded from JSON."""

        task_id = raw.get("task_id")
        if not isinstance(task_id, str) or not task_id.strip():
            raise SchemaError("task_id must be a non-empty string")
        allowed_paths = _strings(raw.get("allowed_paths"), "allowed_paths")
        if not allowed_paths:
            raise SchemaError("allowed_paths must not be empty")
        commands_raw = raw.get("verification_commands", [])
        if not isinstance(commands_raw, list):
            raise SchemaError("verification_commands must be a list")
        commands: list[tuple[str, ...]] = []
        for command in commands_raw:
            if not isinstance(command, list) or not command or any(
                not isinstance(part, str) or not part for part in command
            ):
                raise SchemaError("each verification command must be a non-empty argv list")
            commands.append(tuple(command))
        provider = _mapping(raw.get("provider"), "provider")
        if provider.get("type", "fake") not in {"fake", "codex"}:
            raise SchemaError("provider.type must be fake or codex")
        if provider.get("type") == "codex" and not isinstance(provider.get("prompt"), str):
            raise SchemaError("codex provider requires a prompt string")
        role = raw.get("role", "implementation")
        if not isinstance(role, str) or not role:
            raise SchemaError("role must be a non-empty string")
        base_snapshot_id = raw.get("base_snapshot_id")
        if base_snapshot_id is not None and not isinstance(base_snapshot_id, str):
            raise SchemaError("base_snapshot_id must be a string or null")
        acceptance_digest = raw.get("acceptance_digest", "")
        if not isinstance(acceptance_digest, str):
            raise SchemaError("acceptance_digest must be a string")
        return cls(
            task_id=task_id,
            role=role,
            base_snapshot_id=base_snapshot_id,
            read_paths=_strings(raw.get("read_paths"), "read_paths"),
            allowed_paths=allowed_paths,
            forbidden_paths=_strings(raw.get("forbidden_paths"), "forbidden_paths"),
            protected_paths=_strings(raw.get("protected_paths"), "protected_paths"),
            expected_outputs=_mapping(raw.get("expected_outputs"), "expected_outputs"),
            verification_commands=tuple(commands),
            acceptance_digest=acceptance_digest,
            command_policy=_mapping(raw.get("command_policy"), "command_policy"),
            environment_allowlist=_strings(
                raw.get("environment_allowlist"), "environment_allowlist"
            ),
            resource_budget=_mapping(raw.get("resource_budget"), "resource_budget"),
            provider=provider,
            external_model=_mapping(raw.get("external_model"), "external_model"),
            verification_environment=_verification_environment(
                raw.get("verification_environment")
            ),
        )

    @classmethod
    def from_json_file(cls, path: Path) -> "TaskContract":
        """Load a UTF-8 JSON contract from *path*."""

        import json

        with path.open(encoding="utf-8") as handle:
            raw = json.load(handle)
        if not isinstance(raw, Mapping):
            raise SchemaError("contract root must be an object")
        return cls.from_dict(raw)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""

        return asdict(self)


@dataclass(frozen=True)
class RunManifest:
    """Stable metadata describing one orchestration run."""

    run_id: str
    request: str
    state: str
    snapshot_id: str
    task_contract_ref: str
    risk_level: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class StageResult:
    """Structured outcome of one worker or verification stage."""

    stage: str
    status: str
    artifacts: tuple[str, ...] = ()
    reported_evidence: tuple[dict[str, Any], ...] = ()
    authoritative_evidence: tuple[dict[str, Any], ...] = ()
    findings: tuple[str, ...] = ()
    patch_ref: str | None = None
    model_provenance: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ApprovalRecord:
    """Human approval or rejection bound to an immutable subject digest."""

    run_id: str
    gate: str
    decision: str
    subject_digest: str
    reason: str | None
    recorded_at: str


@dataclass(frozen=True)
class ProviderInvocation:
    """Audit metadata for a provider process invocation."""

    provider: str
    exact_model_id: str | None
    effort: str | None
    cli_version: str | None
    started_at: str
    ended_at: str
    exit_code: int | None
    input_tokens: int | None
    output_tokens: int | None
    usage_source: str | None
    stdout_ref: str
    stderr_ref: str
    timed_out: bool = False
