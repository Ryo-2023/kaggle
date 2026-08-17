from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.orchestration.authorization import ExternalAuthorizationError
from scripts.orchestration.authorization import load_authorized_provider_capabilities
from scripts.orchestration.authorization import validate_external_authorization
from scripts.orchestration.kernel import Kernel
from scripts.orchestration.state import RunStatus


def _external_reference(policy: dict, read_scope: list[str]) -> dict:
    return {
        "enabled": True,
        "authorization_id": policy["authorization_id"],
        "authorization_policy_path": ".orchestrator/policies/external_model_authorization.json",
        "policy_hash": policy["policy_hash"],
        "provider": "codex",
        "read_scope": read_scope,
    }


def test_codex_authorization_is_required_before_provider_start(repository: Path, make_contract) -> None:
    with pytest.raises(ExternalAuthorizationError) as raised:
        Kernel(repository).start(
            make_contract(
                provider_extra={"type": "codex", "prompt": "Edit fixture."},
                read_paths=["fixture.py"],
            )
        )

    assert raised.value.code == "EXTERNAL_MODEL_AUTHORIZATION_REQUIRED"
    assert not (repository / ".orchestrator" / "runs").exists()


def test_provider_mismatch_is_rejected(
    repository: Path, make_contract, write_authorization_policy
) -> None:
    policy = write_authorization_policy(repository)
    reference = _external_reference(policy, ["fixture.py"])
    reference["provider"] = "other"

    with pytest.raises(ExternalAuthorizationError) as raised:
        Kernel(repository).start(
            make_contract(
                provider_extra={"type": "codex", "prompt": "Edit fixture."},
                read_paths=["fixture.py"],
                external_model=reference,
            )
        )
    assert raised.value.code == "EXTERNAL_MODEL_AUTHORIZATION_INVALID"


def test_repository_identity_mismatch_is_rejected(
    repository: Path, make_contract, write_authorization_policy
) -> None:
    policy = write_authorization_policy(repository, identity="repo-root-sha256:wrong")

    with pytest.raises(ExternalAuthorizationError) as raised:
        Kernel(repository).start(
            make_contract(
                provider_extra={"type": "codex", "prompt": "Edit fixture."},
                read_paths=["fixture.py"],
                external_model=_external_reference(policy, ["fixture.py"]),
            )
        )
    assert raised.value.code == "EXTERNAL_MODEL_AUTHORIZATION_INVALID"


def test_policy_hash_tampering_is_rejected(
    repository: Path, make_contract, write_authorization_policy
) -> None:
    policy = write_authorization_policy(repository)
    path = repository / ".orchestrator" / "policies" / "external_model_authorization.json"
    tampered = json.loads(path.read_text(encoding="utf-8"))
    tampered["approved_by"] = "tampered"
    path.write_text(json.dumps(tampered), encoding="utf-8")

    with pytest.raises(ExternalAuthorizationError) as raised:
        Kernel(repository).start(
            make_contract(
                provider_extra={"type": "codex", "prompt": "Edit fixture."},
                read_paths=["fixture.py"],
                external_model=_external_reference(policy, ["fixture.py"]),
            )
        )
    assert raised.value.code == "EXTERNAL_MODEL_AUTHORIZATION_INVALID"


def test_prohibited_read_scope_is_rejected(
    repository: Path, make_contract, write_authorization_policy
) -> None:
    data_file = repository / "data" / "sample.txt"
    data_file.parent.mkdir()
    data_file.write_text("not secret\n", encoding="utf-8")
    policy = write_authorization_policy(repository)

    with pytest.raises(ExternalAuthorizationError) as raised:
        Kernel(repository).start(
            make_contract(
                provider_extra={"type": "codex", "prompt": "Read data."},
                read_paths=["data/sample.txt"],
                external_model=_external_reference(policy, ["data/sample.txt"]),
            )
        )
    assert raised.value.code == "PROHIBITED_DATA_DETECTED"


def test_secret_path_is_rejected(
    repository: Path, make_contract, write_authorization_policy
) -> None:
    (repository / ".env").write_text("TOKEN=do-not-send\n", encoding="utf-8")
    policy = write_authorization_policy(repository)

    with pytest.raises(ExternalAuthorizationError) as raised:
        Kernel(repository).start(
            make_contract(
                provider_extra={"type": "codex", "prompt": "Edit fixture."},
                read_paths=["fixture.py"],
                external_model=_external_reference(policy, ["fixture.py"]),
            )
        )
    assert raised.value.code == "PROHIBITED_DATA_DETECTED"


def test_ignored_ca_certificate_is_not_misclassified_as_private_key(
    repository: Path, write_authorization_policy
) -> None:
    certificate = repository / ".venv" / "certifi" / "cacert.pem"
    certificate.parent.mkdir(parents=True)
    certificate.write_text("-----BEGIN CERTIFICATE-----\npublic-ca\n", encoding="utf-8")
    policy = write_authorization_policy(repository)

    summary = validate_external_authorization(
        repository,
        "codex",
        _external_reference(policy, ["fixture.py"]),
        ["fixture.py"],
    )

    assert summary.authorization_id == policy["authorization_id"]


def test_fake_provider_does_not_require_external_authorization(repository: Path, make_contract) -> None:
    state = Kernel(repository).start(make_contract())

    assert state.state == RunStatus.WAITING_INTEGRATION_APPROVAL
    assert (repository / "fixture.py").read_text(encoding="utf-8") == "VALUE = 1\n"


def test_standing_authorization_resolves_multiple_trusted_model_profiles(
    repository: Path, write_authorization_policy
) -> None:
    write_authorization_policy(
        repository,
        allowed_models={
            "model-a": ["low"],
            "model-b": ["medium", "high"],
        },
    )
    capabilities = load_authorized_provider_capabilities(repository)
    assert {
        (item.provider, item.model, item.reasoning_efforts)
        for item in capabilities
    } == {
        ("codex", "model-a", frozenset({"low"})),
        ("codex", "model-b", frozenset({"medium", "high"})),
    }
