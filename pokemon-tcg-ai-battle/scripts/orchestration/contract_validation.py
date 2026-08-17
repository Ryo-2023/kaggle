"""Public TaskContract preflight shared by the Kernel and Overnight runner."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .authorization import AuthorizationSummary, validate_external_authorization
from .policy import normalize_relative, validate_command
from .provider import CodexProvider
from .schemas import TaskContract


@dataclass(frozen=True)
class ContractValidationResult:
    """Non-sensitive facts produced by a successful contract preflight."""

    authorization: AuthorizationSummary | None
    provider_security_valid: bool


def validate_task_contract(
    repository_root: Path,
    contract: TaskContract,
    *,
    validate_external: bool = True,
    validate_provider_security: bool = True,
    read_scope_root: Path | None = None,
) -> ContractValidationResult:
    """Validate paths, commands, external authorization, and provider security."""

    for path in (
        *contract.read_paths,
        *contract.allowed_paths,
        *contract.forbidden_paths,
        *contract.protected_paths,
    ):
        normalize_relative(path)
    for command in contract.verification_commands:
        validate_command(command)

    provider_type = contract.provider.get("type", "fake")
    authorization: AuthorizationSummary | None = None
    security_valid = True
    if provider_type == "codex":
        if validate_external:
            authorization = validate_external_authorization(
                repository_root.resolve(),
                "codex",
                contract.external_model,
                contract.read_paths,
                scan_repository_root=read_scope_root,
                model=(
                    str(contract.provider["model"])
                    if isinstance(contract.provider.get("model"), str)
                    else None
                ),
                reasoning_effort=(
                    str(contract.provider["effort"])
                    if isinstance(contract.provider.get("effort"), str)
                    else None
                ),
            )
        if validate_provider_security:
            expected = {
                "approval_policy": "never",
                "sandbox_mode": "workspace-write",
                "web_search": "disabled",
                "allow_login_shell": False,
                "network_access": False,
                "ignore_user_config": True,
                "dangerous_flags": False,
            }
            security_valid = CodexProvider.security_configuration() == expected
            if not security_valid:
                raise RuntimeError("codex provider security configuration is invalid")
    return ContractValidationResult(authorization, security_valid)
