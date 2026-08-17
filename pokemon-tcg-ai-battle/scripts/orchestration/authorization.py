"""Standing authorization and prohibited-data preflight for external models."""

from __future__ import annotations

import fnmatch
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence


DEFAULT_AUTHORIZATION_PATH = Path(
    ".orchestrator/policies/external_model_authorization.json"
)


class ExternalAuthorizationError(RuntimeError):
    """Fail-closed external-model authorization error with a stable reason code."""

    def __init__(self, code: str, detail: str):
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


def canonical_policy_hash(policy: Mapping[str, Any]) -> str:
    """Hash canonical policy content excluding its self-referential hash field."""

    payload = {key: value for key, value in policy.items() if key != "policy_hash"}
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def repository_identity(repository_root: Path) -> str:
    """Return a stable identity bound to the canonical repository location."""

    canonical_root = str(repository_root.resolve())
    return f"repo-root-sha256:{hashlib.sha256(canonical_root.encode()).hexdigest()}"


@dataclass(frozen=True)
class AuthorizationSummary:
    """Non-sensitive authorization facts safe to expose through doctor."""

    authorization_id: str
    provider: str
    service: str
    policy_hash: str
    status: str
    model: str | None = None
    reasoning_effort: str | None = None

    def to_dict(self) -> dict[str, str]:
        """Return a JSON-serializable safe summary."""

        return {
            "authorization_id": self.authorization_id,
            "provider": self.provider,
            "service": self.service,
            "policy_hash": self.policy_hash,
            "status": self.status,
            **({"model": self.model} if self.model is not None else {}),
            **(
                {"reasoning_effort": self.reasoning_effort}
                if self.reasoning_effort is not None
                else {}
            ),
        }


@dataclass(frozen=True)
class ProviderCapability:
    """Trusted provider profiles bound to one repository identity."""

    repository_identity: str
    provider: str
    model: str
    reasoning_efforts: frozenset[str]

    def allows(
        self, repository: str, provider: str, model: str, reasoning_effort: str
    ) -> bool:
        return (
            self.repository_identity == repository
            and self.provider == provider
            and self.model == model
            and reasoning_effort in self.reasoning_efforts
        )


def load_authorization_policy(path: Path) -> dict[str, Any]:
    """Load a standing authorization policy as a JSON object."""

    try:
        with path.open(encoding="utf-8") as handle:
            value = json.load(handle)
    except FileNotFoundError as exc:
        raise ExternalAuthorizationError(
            "EXTERNAL_MODEL_AUTHORIZATION_REQUIRED", "authorization policy is missing"
        ) from exc
    except json.JSONDecodeError as exc:
        raise ExternalAuthorizationError(
            "EXTERNAL_MODEL_AUTHORIZATION_INVALID", "authorization policy is invalid JSON"
        ) from exc
    if not isinstance(value, dict):
        raise ExternalAuthorizationError(
            "EXTERNAL_MODEL_AUTHORIZATION_INVALID", "authorization policy must be an object"
        )
    return value


def validate_external_authorization(
    repository_root: Path,
    provider: str,
    external_model: Mapping[str, Any] | None,
    read_paths: Sequence[str],
    *,
    scan_repository_root: Path | None = None,
    model: str | None = None,
    reasoning_effort: str | None = None,
) -> AuthorizationSummary:
    """Validate standing authorization and scan data before provider startup."""

    if not external_model or external_model.get("enabled") is not True:
        raise ExternalAuthorizationError(
            "EXTERNAL_MODEL_AUTHORIZATION_REQUIRED",
            "TaskContract must explicitly enable external model use",
        )
    if external_model.get("provider") != provider:
        raise ExternalAuthorizationError(
            "EXTERNAL_MODEL_AUTHORIZATION_INVALID",
            "TaskContract external provider does not match provider routing",
        )
    policy_path_raw = external_model.get("authorization_policy_path")
    if not isinstance(policy_path_raw, str):
        raise ExternalAuthorizationError(
            "EXTERNAL_MODEL_AUTHORIZATION_REQUIRED",
            "TaskContract authorization_policy_path is required",
        )
    policy_path = _resolve_policy_path(repository_root, policy_path_raw)
    policy = load_authorization_policy(policy_path)
    _validate_policy_shape(policy)
    computed_hash = canonical_policy_hash(policy)
    if policy["policy_hash"] != computed_hash:
        raise ExternalAuthorizationError(
            "EXTERNAL_MODEL_AUTHORIZATION_INVALID", "authorization policy hash is invalid"
        )
    if external_model.get("authorization_id") != policy["authorization_id"]:
        raise ExternalAuthorizationError(
            "EXTERNAL_MODEL_AUTHORIZATION_INVALID", "authorization ID does not match"
        )
    if external_model.get("policy_hash") != policy["policy_hash"]:
        raise ExternalAuthorizationError(
            "EXTERNAL_MODEL_AUTHORIZATION_INVALID", "TaskContract policy hash does not match"
        )
    if policy["repository_root"] != str(repository_root.resolve()):
        raise ExternalAuthorizationError(
            "EXTERNAL_MODEL_AUTHORIZATION_INVALID", "repository root does not match"
        )
    if policy["repository_identity"] != repository_identity(repository_root):
        raise ExternalAuthorizationError(
            "EXTERNAL_MODEL_AUTHORIZATION_INVALID", "repository identity does not match"
        )
    if policy["allowed_provider"] != provider or policy["allowed_service"] != "openai_codex":
        raise ExternalAuthorizationError(
            "EXTERNAL_MODEL_AUTHORIZATION_INVALID", "provider or service is not authorized"
        )
    _validate_validity(policy["validity"])
    if (model is None) != (reasoning_effort is None):
        raise ExternalAuthorizationError(
            "EXTERNAL_MODEL_AUTHORIZATION_INVALID",
            "routed model and reasoning effort must be validated together",
        )
    if model is not None and reasoning_effort is not None:
        allowed_efforts = policy["allowed_models"].get(model)
        if not isinstance(allowed_efforts, list) or reasoning_effort not in allowed_efforts:
            raise ExternalAuthorizationError(
                "ROUTED_PROFILE_NOT_AUTHORIZED",
                "routed model or reasoning effort is not authorized",
            )
    declared_scope = external_model.get("read_scope")
    if not isinstance(declared_scope, list) or any(
        not isinstance(item, str) for item in declared_scope
    ):
        raise ExternalAuthorizationError(
            "EXTERNAL_MODEL_AUTHORIZATION_REQUIRED", "external model read_scope is required"
        )
    if set(declared_scope) != set(read_paths):
        raise ExternalAuthorizationError(
            "EXTERNAL_MODEL_AUTHORIZATION_INVALID",
            "external model read_scope must equal TaskContract read_paths",
        )
    _validate_read_scope(declared_scope, policy["prohibited_path_patterns"])
    _scan_repository_for_prohibited_data(
        (scan_repository_root or repository_root).resolve(), declared_scope
    )
    return AuthorizationSummary(
        authorization_id=str(policy["authorization_id"]),
        provider=str(policy["allowed_provider"]),
        service=str(policy["allowed_service"]),
        policy_hash=str(policy["policy_hash"]),
        status=str(policy["validity"]["status"]),
        model=model,
        reasoning_effort=reasoning_effort,
    )


def load_authorized_provider_capabilities(
    repository_root: Path,
) -> tuple[ProviderCapability, ...]:
    """Resolve trusted provider/model/effort profiles from standing authorization."""

    root = repository_root.resolve()
    policy = load_authorization_policy(root / DEFAULT_AUTHORIZATION_PATH)
    _validate_policy_shape(policy)
    if policy["policy_hash"] != canonical_policy_hash(policy):
        raise ExternalAuthorizationError(
            "EXTERNAL_MODEL_AUTHORIZATION_INVALID", "authorization policy hash is invalid"
        )
    identity = repository_identity(root)
    if (
        policy["repository_root"] != str(root)
        or policy["repository_identity"] != identity
    ):
        raise ExternalAuthorizationError(
            "EXTERNAL_MODEL_AUTHORIZATION_INVALID", "repository identity does not match"
        )
    if policy["allowed_provider"] != "codex" or policy["allowed_service"] != "openai_codex":
        raise ExternalAuthorizationError(
            "EXTERNAL_MODEL_AUTHORIZATION_INVALID", "provider or service is not authorized"
        )
    _validate_validity(policy["validity"])
    return tuple(
        ProviderCapability(
            repository_identity=identity,
            provider=str(policy["allowed_provider"]),
            model=str(model),
            reasoning_efforts=frozenset(str(effort) for effort in efforts),
        )
        for model, efforts in sorted(policy["allowed_models"].items())
    )


def inspect_default_authorization(repository_root: Path) -> dict[str, Any]:
    """Return a safe doctor report for the default project authorization."""

    path = repository_root / DEFAULT_AUTHORIZATION_PATH
    try:
        policy = load_authorization_policy(path)
        _validate_policy_shape(policy)
        hash_valid = policy["policy_hash"] == canonical_policy_hash(policy)
        identity_valid = (
            policy["repository_root"] == str(repository_root.resolve())
            and policy["repository_identity"] == repository_identity(repository_root)
        )
        provider_valid = (
            policy["allowed_provider"] == "codex"
            and policy["allowed_service"] == "openai_codex"
        )
        _validate_validity(policy["validity"])
        passed = hash_valid and identity_valid and provider_valid
        return {
            "passed": passed,
            "authorization_id": policy["authorization_id"],
            "provider": policy["allowed_provider"],
            "hash_valid": hash_valid,
            "repository_identity_valid": identity_valid,
            "status": policy["validity"]["status"],
        }
    except ExternalAuthorizationError as exc:
        return {"passed": False, "reason_code": exc.code}


def _resolve_policy_path(repository_root: Path, relative_path: str) -> Path:
    candidate = Path(relative_path)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ExternalAuthorizationError(
            "EXTERNAL_MODEL_AUTHORIZATION_INVALID", "authorization policy path is unsafe"
        )
    resolved = (repository_root / candidate).resolve(strict=False)
    allowed_root = (repository_root / ".orchestrator" / "policies").resolve()
    if not resolved.is_relative_to(allowed_root):
        raise ExternalAuthorizationError(
            "EXTERNAL_MODEL_AUTHORIZATION_INVALID",
            "authorization policy must be under .orchestrator/policies",
        )
    return resolved


def _validate_policy_shape(policy: Mapping[str, Any]) -> None:
    required = {
        "schema_version": int,
        "authorization_id": str,
        "repository_identity": str,
        "repository_root": str,
        "allowed_provider": str,
        "allowed_service": str,
        "allowed_models": dict,
        "allowed_data_scope": dict,
        "prohibited_path_patterns": list,
        "prohibited_secret_categories": list,
        "approved_at": str,
        "approved_by": str,
        "validity": dict,
        "policy_hash": str,
    }
    for field, expected_type in required.items():
        if not isinstance(policy.get(field), expected_type):
            raise ExternalAuthorizationError(
                "EXTERNAL_MODEL_AUTHORIZATION_INVALID",
                f"authorization policy field is invalid: {field}",
            )
    if policy["schema_version"] != 1:
        raise ExternalAuthorizationError(
            "EXTERNAL_MODEL_AUTHORIZATION_INVALID", "unsupported authorization schema version"
        )
    if not policy["allowed_models"]:
        raise ExternalAuthorizationError(
            "EXTERNAL_MODEL_AUTHORIZATION_INVALID", "allowed_models must not be empty"
        )
    for model, efforts in policy["allowed_models"].items():
        if (
            not isinstance(model, str)
            or not model
            or not isinstance(efforts, list)
            or not efforts
            or any(not isinstance(effort, str) or not effort for effort in efforts)
        ):
            raise ExternalAuthorizationError(
                "EXTERNAL_MODEL_AUTHORIZATION_INVALID", "allowed_models is invalid"
            )


def _validate_validity(validity: Mapping[str, Any]) -> None:
    if validity.get("status") != "active" or validity.get("revoked_at") is not None:
        raise ExternalAuthorizationError(
            "EXTERNAL_MODEL_AUTHORIZATION_INVALID", "authorization is inactive or revoked"
        )
    expires_at = validity.get("expires_at")
    if expires_at is not None:
        try:
            expiry = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
        except ValueError as exc:
            raise ExternalAuthorizationError(
                "EXTERNAL_MODEL_AUTHORIZATION_INVALID", "authorization expiry is invalid"
            ) from exc
        if expiry <= datetime.now(UTC):
            raise ExternalAuthorizationError(
                "EXTERNAL_MODEL_AUTHORIZATION_INVALID", "authorization has expired"
            )


def _validate_read_scope(read_scope: Sequence[str], prohibited_patterns: Sequence[Any]) -> None:
    patterns = [str(pattern) for pattern in prohibited_patterns]
    for path in read_scope:
        if any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns):
            raise ExternalAuthorizationError(
                "PROHIBITED_DATA_DETECTED", f"read scope contains prohibited path: {path}"
            )


_SECRET_NAME_PATTERNS = (
    ".env",
    ".env.*",
    "*.key",
    "kaggle.json",
    "*credential*",
    "*credentials*",
    "*private_key*",
)
_SECRET_CONTENT_PATTERNS = (
    re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(rb"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b"),
    re.compile(rb"\bAKIA[A-Z0-9]{16}\b"),
)


def _scan_repository_for_prohibited_data(repository_root: Path, read_scope: Sequence[str]) -> None:
    for path in repository_root.rglob("*"):
        relative_path = path.relative_to(repository_root)
        if any(
            excluded in relative_path.parts
            for excluded in (".git", ".orchestrator", ".venv", "__pycache__")
        ):
            continue
        relative = relative_path.as_posix()
        if any(fnmatch.fnmatchcase(path.name.lower(), pattern) for pattern in _SECRET_NAME_PATTERNS):
            raise ExternalAuthorizationError(
                "PROHIBITED_DATA_DETECTED", f"prohibited secret path detected: {relative}"
            )
    for path in _expand_read_scope(repository_root, read_scope):
        if path.is_symlink():
            raise ExternalAuthorizationError(
                "PROHIBITED_DATA_DETECTED", "symlink is not allowed in external read scope"
            )
        if not path.is_file() or path.stat().st_size > 5 * 1024 * 1024:
            continue
        sample = path.read_bytes()[: 1024 * 1024]
        if any(pattern.search(sample) for pattern in _SECRET_CONTENT_PATTERNS):
            relative = path.relative_to(repository_root).as_posix()
            raise ExternalAuthorizationError(
                "PROHIBITED_DATA_DETECTED", f"secret marker detected in read scope: {relative}"
            )


def _expand_read_scope(repository_root: Path, read_scope: Sequence[str]) -> tuple[Path, ...]:
    files: set[Path] = set()
    root = repository_root.resolve()
    for item in read_scope:
        candidates = list(repository_root.glob(item)) if any(c in item for c in "*?[") else [repository_root / item]
        if not candidates or any(not candidate.exists() for candidate in candidates):
            raise ExternalAuthorizationError(
                "EXTERNAL_MODEL_AUTHORIZATION_INVALID", f"read scope path does not exist: {item}"
            )
        for candidate in candidates:
            resolved = candidate.resolve()
            if not resolved.is_relative_to(root):
                raise ExternalAuthorizationError(
                    "PROHIBITED_DATA_DETECTED", "read scope escapes repository"
                )
            if candidate.is_dir():
                files.update(path for path in candidate.rglob("*") if path.is_file())
            else:
                files.add(candidate)
    return tuple(sorted(files))
