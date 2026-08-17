from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

import pytest

from scripts.orchestration.authorization import canonical_policy_hash, repository_identity


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    root = tmp_path / "repository"
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "test@example.invalid")
    _git(root, "config", "user.name", "Kernel Test")
    (root / ".gitignore").write_text(".orchestrator/\nworker-cache.tmp\n", encoding="utf-8")
    (root / "fixture.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "protected_test.py").write_text("ORACLE = 1\n", encoding="utf-8")
    (root / "README.md").write_text("baseline\n", encoding="utf-8")
    (root / "verify_fixture.py").write_text(
        "from pathlib import Path\nassert Path('fixture.py').read_text() == 'VALUE = 2\\n'\n",
        encoding="utf-8",
    )
    (root / "verify_new.py").write_text(
        "from pathlib import Path\nassert Path('new_module.py').read_text() == 'NEW = 1\\n'\n",
        encoding="utf-8",
    )
    (root / "verify_clean.py").write_text(
        "from pathlib import Path\n"
        "assert Path('fixture.py').read_text() == 'VALUE = 2\\n'\n"
        "assert not Path('worker-cache.tmp').exists()\n",
        encoding="utf-8",
    )
    (root / "fail_verification.py").write_text("raise SystemExit(3)\n", encoding="utf-8")
    (root / "verify_no_marker.py").write_text(
        "import os\nassert 'MAGE_ORCHESTRATOR_CHILD' not in os.environ\n",
        encoding="utf-8",
    )
    (root / "verify_default_environment.py").write_text(
        "import os\n"
        "assert os.environ['LC_ALL'] == 'C.UTF-8'\n"
        "assert os.environ['LANG'] == 'C.UTF-8'\n"
        "assert os.environ['PATH'] == '/usr/local/bin:/usr/bin:/bin'\n"
        "assert os.environ['PYTHONDONTWRITEBYTECODE'] == '1'\n"
        "assert 'PYTHONPATH' not in os.environ\n",
        encoding="utf-8",
    )
    (root / "verify_legacy_environment.py").write_text(
        "import os\nassert os.environ['TZ'] == 'Asia/Tokyo'\n", encoding="utf-8"
    )
    (root / "verify_python_environment.py").write_text(
        "import os\n"
        "assert 'PYTHONPATH' not in os.environ\n"
        "assert 'PYTHONHOME' not in os.environ\n"
        "assert 'VIRTUAL_ENV' not in os.environ\n",
        encoding="utf-8",
    )
    (root / "verify_explicit_environment.py").write_text(
        "import os\n"
        "assert os.environ['LC_ALL'] == 'C.UTF-8'\n"
        "assert os.environ['PATH'] == 'private-verification-path'\n",
        encoding="utf-8",
    )
    source_dir = root / "src"
    source_dir.mkdir()
    (source_dir / "verification_fixture.py").write_text(
        "VALUE = 'worktree'\n", encoding="utf-8"
    )
    (root / "verify_src_import.py").write_text(
        "from verification_fixture import VALUE\nassert VALUE == 'worktree'\n",
        encoding="utf-8",
    )
    _git(root, "add", ".")
    _git(root, "commit", "-m", "initial")
    return root


@pytest.fixture
def make_contract(tmp_path: Path) -> Callable[..., Path]:
    counter = 0

    def factory(
        *,
        scenario: str = "valid",
        writes: dict[str, str] | None = None,
        allowed_paths: list[str] | None = None,
        protected_paths: list[str] | None = None,
        command: list[str] | None = None,
        provider_extra: dict[str, Any] | None = None,
        read_paths: list[str] | None = None,
        external_model: dict[str, Any] | None = None,
        environment_allowlist: list[str] | None = None,
        verification_environment: dict[str, Any] | None = None,
    ) -> Path:
        nonlocal counter
        counter += 1
        provider: dict[str, Any] = {
            "type": "fake",
            "scenario": scenario,
            "writes": writes if writes is not None else {"fixture.py": "VALUE = 2\n"},
        }
        if provider_extra:
            provider.update(provider_extra)
        value = {
            "task_id": f"task-{counter}",
            "role": "implementation",
            "read_paths": read_paths or [],
            "allowed_paths": allowed_paths or ["fixture.py"],
            "forbidden_paths": ["forbidden.txt"],
            "protected_paths": protected_paths or ["protected_test.py"],
            "verification_commands": [
                command
                or [
                    sys.executable,
                    "verify_fixture.py",
                ]
            ],
            "environment_allowlist": environment_allowlist or [],
            "resource_budget": {
                "provider_timeout_seconds": 0.2,
                "verification_timeout_seconds": 5,
            },
            "provider": provider,
            "external_model": external_model or {},
        }
        if verification_environment is not None:
            value["verification_environment"] = verification_environment
        path = tmp_path / f"contract-{counter}.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    return factory


@pytest.fixture
def write_authorization_policy() -> Callable[..., dict[str, Any]]:
    def factory(
        root: Path,
        *,
        allowed_provider: str = "codex",
        identity: str | None = None,
        allowed_models: dict[str, list[str]] | None = None,
    ) -> dict[str, Any]:
        policy: dict[str, Any] = {
            "schema_version": 1,
            "authorization_id": "test-authorization-001",
            "repository_identity": identity or repository_identity(root),
            "repository_root": str(root.resolve()),
            "allowed_provider": allowed_provider,
            "allowed_service": "openai_codex",
            "allowed_models": allowed_models
            or {"test-model": ["low", "medium", "high"]},
            "allowed_data_scope": {
                "categories": ["repository_source_code", "repository_documentation"],
                "requires_explicit_task_read_scope": True,
                "root_integration_authorized": False,
            },
            "prohibited_path_patterns": [
                ".env",
                ".env.*",
                "**/.env",
                "**/.env.*",
                "data/**",
                ".orchestrator/**",
            ],
            "prohibited_secret_categories": [
                "environment_file",
                "private_key",
                "api_token",
            ],
            "approved_at": "2026-07-12T00:00:00+00:00",
            "approved_by": "test-user",
            "validity": {
                "status": "active",
                "expires_at": None,
                "revoked_at": None,
                "revocation_condition": "explicit revocation",
            },
            "policy_hash": "",
        }
        policy["policy_hash"] = canonical_policy_hash(policy)
        path = root / ".orchestrator" / "policies" / "external_model_authorization.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(policy), encoding="utf-8")
        return policy

    return factory
