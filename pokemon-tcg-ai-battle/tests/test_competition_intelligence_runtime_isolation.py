"""Runtime isolation: the submission surface must never reach the sidecar.

Mirrors the existing clean-subprocess pattern in
``tests/test_public_belief_decision_loop.py``
(``test_clean_subprocess_imports_main_and_constructs_both_rule_agents``): run
a small script in a fresh interpreter with ``PYTHONPATH`` cleared, then
inspect ``sys.modules``. Per the O1 mandate (AGENTS.md / project instructions
for this session), ``main.py`` must never import
``mage_ptcg.competition_intelligence`` (this sidecar), ``mage_ptcg.dataops``,
``sqlite3``, ``pandas``, or ``sklearn`` — not at module load time, and not
through the default Rule Agent v0 path that ``agent(obs_dict)`` actually
uses.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]

_FORBIDDEN_SUBSTRINGS = (
    "mage_ptcg.competition_intelligence",
    "mage_ptcg.dataops",
    "sqlite3",
    "pandas",
    "sklearn",
)


def _clean_env() -> dict[str, str]:
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    return environment


def _sys_modules_after(script: str) -> list[str]:
    wrapped = script + "\nimport json as _json, sys as _sys\nprint(_json.dumps(sorted(_sys.modules.keys())))\n"
    result = subprocess.run(
        [sys.executable, "-c", wrapped],
        cwd=REPOSITORY_ROOT,
        env=_clean_env(),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


def _assert_none_leaked(modules: list[str], *, via: str) -> None:
    for forbidden in _FORBIDDEN_SUBSTRINGS:
        leaked = [name for name in modules if forbidden in name]
        assert not leaked, f"{forbidden!r} leaked into sys.modules via {via}: {leaked}"


class TestPlainImportMain:
    def test_import_main_alone_does_not_pull_in_forbidden_modules(self) -> None:
        modules = _sys_modules_after("import main\n")
        _assert_none_leaked(modules, via="bare `import main`")

    def test_main_py_source_never_references_the_sidecar(self) -> None:
        source = (REPOSITORY_ROOT / "main.py").read_text(encoding="utf-8")
        assert "competition_intelligence" not in source
        assert "mage_ptcg.dataops" not in source


class TestDefaultRuleAgentPathIsolation:
    def test_constructing_and_running_default_rule_agent_v0_stays_isolated(self) -> None:
        script = (
            "import main\n"
            "deck = [1] * 60\n"
            "agent = main.make_rule_agent(deck=deck)\n"
            "assert agent({'select': None}) == deck\n"
        )
        modules = _sys_modules_after(script)
        _assert_none_leaked(modules, via="main.make_rule_agent default path")

    def test_student_agent_fallback_path_stays_isolated(self) -> None:
        # make_student_agent falls back to Rule v0 when no model artifact is
        # configured, which is exactly the submission's default runtime
        # posture; it must not reach the sidecar either.
        script = (
            "import main\n"
            "deck = [1] * 60\n"
            "agent = main.make_student_agent(deck=deck)\n"
            "assert agent({'select': None}) == deck\n"
        )
        modules = _sys_modules_after(script)
        _assert_none_leaked(modules, via="main.make_student_agent fallback path")
