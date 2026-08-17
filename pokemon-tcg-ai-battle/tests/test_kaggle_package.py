"""Focused contracts for the Student-only public package helpers."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

from scripts.kaggle_student_entrypoint import (
    render_student_cabt_trace,
    render_student_entrypoint,
    render_student_package_init,
    render_student_runtime_model,
)
from scripts.kaggle_student_runtime import render_student_runtime
from scripts.probe_kaggle_contract import probe


_FORBIDDEN_SUBMISSION_PATTERNS = (
    'sys.modules.pop("agents"',
    "sys.modules.pop('agents'",
    "sys.path[-1]",
    "from agents",
    "import agents",
    "rule_agent_v1",
    "RuleAgentV1",
    "mage_ptcg.knowledge",
    "mage_ptcg.solver",
    "make_bounded_search_agent",
    "actor_visible_attestation",
)


def _resolve_venv_python() -> Path:
    for entry in sys.path:
        if ".venv" in entry:
            parts = Path(entry).parts
            if "site-packages" in parts:
                idx = parts.index(".venv")
                venv_dir = (
                    Path("/").joinpath(*parts[1 : idx + 1])
                    if parts[0] == "/"
                    else Path(*parts[: idx + 1])
                )
                candidate = venv_dir / "bin" / "python"
                if candidate.exists():
                    return candidate
    fallback = Path("/home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle/.venv/bin/python")
    return fallback if fallback.exists() else Path(sys.executable)


def _clean_subprocess_env(temp_home: Path) -> dict:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env["PYTHONNOUSERSITE"] = "1"
    env["HOME"] = str(temp_home)
    return env


def test_student_entrypoint_is_package_relative_and_keeps_rule_fallback() -> None:
    source = render_student_entrypoint()
    assert "Path(_RUNTIME_MAIN_FILE).resolve().parent" in source
    assert "ACTUAL_TRAINED" in source
    assert "make_student_agent(model_path=MODEL_PATH)" in source
    assert "make_rule_agent()" in source
    assert "/home/" not in source
    repository_main = (Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8")
    assert "_DEFAULT_AGENT = make_rule_agent()" in repository_main


def test_student_runtime_surface_has_no_dataset_import() -> None:
    source = render_student_package_init()
    model = render_student_runtime_model("from .dataset import RuleBCExample\nclass X: pass\n")
    assert "dataset" not in source
    assert "TYPE_CHECKING" in model
    assert "from .dataset import RuleBCExample" in model


def test_contract_probe_distinguishes_missing_cli_and_auth_without_secrets() -> None:
    missing_cli = probe("pokemon-tcg-ai-battle", which=lambda _: None, environ={"KAGGLE_KEY": "secret"})
    assert missing_cli["status"] == "CLI_MISSING"
    assert missing_cli["credential_values_logged"] is False
    missing_auth = probe("pokemon-tcg-ai-battle", which=lambda _: "kaggle", environ={})
    assert missing_auth["status"] == "AUTH_MISSING"


def test_contract_probe_retains_unknown_when_rules_or_access_are_unavailable() -> None:
    completed = subprocess.CompletedProcess(["kaggle"], 1, stdout="", stderr="sensitive")
    result = probe(
        "pokemon-tcg-ai-battle",
        which=lambda _: "kaggle",
        environ={"KAGGLE_USERNAME": "user", "KAGGLE_KEY": "secret"},
        run=lambda *args, **kwargs: completed,
    )
    assert result["status"] == "RULES_OR_ACCESS_REQUIRED"
    assert result["submission_method"] == "UNKNOWN"


def test_repository_has_no_kaggle_submission_executor() -> None:
    root = Path(__file__).resolve().parents[1]
    assert not (root / "scripts" / "submit_kaggle_submission.py").exists()
    assert not (root / "scripts" / "submit_verified_kaggle_candidate.py").exists()


def test_student_entrypoint_callable_order() -> None:
    """AST で callable 定義順を検証。exec() は親プロセス汚染を防ぐため使わない。"""
    import ast

    source = render_student_entrypoint()
    tree = ast.parse(source)
    defined_callables = [
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    ]
    assert len(defined_callables) >= 1, "エントリポイントに callable 定義がない"
    assert defined_callables[-1] == "agent", (
        f"最後の callable は 'agent' であるべき: 実際は {defined_callables[-1]!r}"
    )


def test_student_entrypoint_exact_loader_from_real_layout(tmp_path: Path) -> None:
    import subprocess
    import sys
    import os

    package_dir = tmp_path / "package"
    package_dir.mkdir()

    main_path = package_dir / "main.py"
    source = render_student_entrypoint()
    main_path.write_text(source, encoding="utf-8")

    runtime_path = package_dir / "runtime_main.py"
    runtime_path.write_text("""
def make_rule_agent():
    return lambda obs: []
def make_student_agent(model_path):
    return lambda obs: []
""", encoding="utf-8")

    student_dir = package_dir / "src" / "mage_ptcg" / "student"
    student_dir.mkdir(parents=True)
    (package_dir / "src" / "mage_ptcg" / "__init__.py").write_text("", encoding="utf-8")
    (student_dir / "__init__.py").write_text("", encoding="utf-8")
    (student_dir / "model.py").write_text("""
class StudentV0Model:
    @classmethod
    def load(cls, path):
        pass
""", encoding="utf-8")

    venv_python = None
    for p in sys.path:
        if ".venv" in p:
            path_parts = Path(p).parts
            if "site-packages" in path_parts:
                idx = path_parts.index(".venv")
                if path_parts[0] == '/':
                    venv_dir = Path('/') / Path(*path_parts[1:idx+1])
                else:
                    venv_dir = Path(*path_parts[:idx+1])
                venv_python = venv_dir / "bin" / "python"
                break
    if not venv_python or not venv_python.exists():
        potential_venv = Path("/home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle/.venv/bin/python")
        if potential_venv.exists():
            venv_python = potential_venv
        else:
            venv_python = Path(sys.executable)

    temp_home = tmp_path / "temp_home"
    temp_home.mkdir()

    clean_env = os.environ.copy()
    clean_env.pop("PYTHONPATH", None)
    clean_env["PYTHONNOUSERSITE"] = "1"
    clean_env["HOME"] = str(temp_home)

    loader_script = f"""
from pathlib import Path
from kaggle_environments.agent import get_last_callable

main_path = Path({repr(str(main_path))})
source = main_path.read_text(encoding="utf-8")

selected = get_last_callable(
    source,
    path=str(main_path),
)

print(f"SELECTED={{selected.__name__}}")
"""
    result = subprocess.run(
        [str(venv_python), "-I", "-c", loader_script],
        cwd=tmp_path,
        env=clean_env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, f"stdout: {result.stdout}, stderr: {result.stderr}"
    assert "SELECTED=agent" in result.stdout


def test_student_entrypoint_has_no_toplevel_file_reference() -> None:
    import ast
    source = render_student_entrypoint()
    tree = ast.parse(source)

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        names = [
            child
            for child in ast.walk(node)
            if isinstance(child, ast.Name) and child.id == "__file__"
        ]
        assert not names, f"Found __file__ reference in top-level statement: {ast.dump(node)}"


def test_student_entrypoint_does_not_evict_agents_module() -> None:
    """sys.modules.pop('agents') がソースに存在しないことを保証する。"""
    source = render_student_entrypoint()
    assert "sys.modules.pop" not in source, "sys.modules.pop が存在する"
    assert 'sys.modules["agents"]' not in source, "sys.modules[\"agents\"] への代入が存在する"
    assert "sys.modules['agents']" not in source, "sys.modules['agents'] への代入が存在する"


def test_student_entrypoint_does_not_use_syspath_last() -> None:
    """sys.path[-1] ハックがソースに存在しないことを保証する。"""
    source = render_student_entrypoint()
    assert "sys.path[-1]" not in source, "sys.path[-1] が存在する"


def test_student_entrypoint_does_not_import_generic_agents() -> None:
    """提出 entrypoint が汎用名 'agents' を直接 import しないことを保証する。"""
    source = render_student_entrypoint()
    # runtime_main 内の import は別途変換されるため、
    # entrypoint 自体に 'from agents' / 'import agents' がないことだけを確認
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        assert not stripped.startswith("from agents "), (
            f"汎用名 agents を import している行: {stripped}"
        )
        assert not stripped.startswith("import agents"), (
            f"汎用名 agents を import している行: {stripped}"
        )


def test_student_entrypoint_survives_contaminated_agents(tmp_path: Path) -> None:
    """外部の agents が sys.modules に存在しても exact loader が動作し、
    かつ既存の agents モジュールを削除・置換しないことを保証する。"""
    import subprocess
    import sys
    import os

    package_dir = tmp_path / "package"
    package_dir.mkdir()

    main_path = package_dir / "main.py"
    source = render_student_entrypoint()
    main_path.write_text(source, encoding="utf-8")

    runtime_path = package_dir / "runtime_main.py"
    runtime_path.write_text("""
def make_rule_agent():
    return lambda obs: []
def make_student_agent(model_path):
    return lambda obs: []
""", encoding="utf-8")

    student_dir = package_dir / "src" / "mage_ptcg" / "student"
    student_dir.mkdir(parents=True)
    (package_dir / "src" / "mage_ptcg" / "__init__.py").write_text("", encoding="utf-8")
    (student_dir / "__init__.py").write_text("", encoding="utf-8")
    (student_dir / "model.py").write_text("""
class StudentV0Model:
    @classmethod
    def load(cls, path):
        pass
""", encoding="utf-8")

    venv_python = None
    for p in sys.path:
        if ".venv" in p:
            path_parts = Path(p).parts
            if "site-packages" in path_parts:
                idx = path_parts.index(".venv")
                if path_parts[0] == '/':
                    venv_dir = Path('/') / Path(*path_parts[1:idx+1])
                else:
                    venv_dir = Path(*path_parts[:idx+1])
                venv_python = venv_dir / "bin" / "python"
                break
    if not venv_python or not venv_python.exists():
        potential_venv = Path("/home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle/.venv/bin/python")
        if potential_venv.exists():
            venv_python = potential_venv
        else:
            venv_python = Path(sys.executable)

    temp_home = tmp_path / "temp_home"
    temp_home.mkdir()

    clean_env = os.environ.copy()
    clean_env.pop("PYTHONPATH", None)
    clean_env["PYTHONNOUSERSITE"] = "1"
    clean_env["HOME"] = str(temp_home)

    # sys.modules に汚染された agents を注入してから get_last_callable を実行
    contamination_script = f"""
import sys
import types
from pathlib import Path
from kaggle_environments.agent import get_last_callable

# 汚染された agents を注入
contaminated = types.ModuleType("agents")
contaminated.CONTAMINATION_MARKER = True
sys.modules["agents"] = contaminated

main_path = Path({repr(str(main_path))})
source = main_path.read_text(encoding="utf-8")

selected = get_last_callable(source, path=str(main_path))

# 1. 正しく agent が選択されること
assert selected.__name__ == "agent", f"Expected agent, got {{selected.__name__}}"

# 2. 汚染された agents が sys.modules に残っていること（削除されていないこと）
assert "agents" in sys.modules, "agents が sys.modules から削除された"
assert getattr(sys.modules["agents"], "CONTAMINATION_MARKER", False), (
    "agents module が別のものに置換された"
)

print("CONTAMINATION_TEST=PASS")
"""
    result = subprocess.run(
        [str(venv_python), "-I", "-c", contamination_script],
        cwd=tmp_path,
        env=clean_env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, (
        f"汚染テスト失敗: stdout={result.stdout}, stderr={result.stderr}"
    )
    assert "CONTAMINATION_TEST=PASS" in result.stdout


def test_student_runtime_has_no_forbidden_patterns() -> None:
    """4.1: render_student_runtime() の出力に禁止パターンがないことを保証する。"""
    source = render_student_runtime()
    for pattern in _FORBIDDEN_SUBMISSION_PATTERNS:
        assert pattern not in source, f"runtime_main.py に禁止パターンが検出された: {pattern}"


def test_student_entrypoint_and_runtime_share_no_forbidden_patterns() -> None:
    """4.1: 生成される main.py と runtime_main.py の両方を禁止パターンで検査する。"""
    for label, source in (
        ("main.py", render_student_entrypoint()),
        ("runtime_main.py", render_student_runtime()),
    ):
        for pattern in _FORBIDDEN_SUBMISSION_PATTERNS:
            assert pattern not in source, f"{label} に禁止パターンが検出された: {pattern}"


def test_student_runtime_required_factories_present() -> None:
    """4.2: 生成runtimeに make_rule_agent / make_student_agent が存在すること。"""
    import ast

    source = render_student_runtime()
    tree = ast.parse(source)
    top_level_defs = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}
    assert "make_rule_agent" in top_level_defs
    assert "make_student_agent" in top_level_defs


def test_student_cabt_trace_removes_attestation_dependency_and_stays_importable(
    tmp_path: Path,
) -> None:
    """Task 3: cabt_trace の提出コピーが attestation 依存を除去しつつ importable であること。"""
    import importlib.util

    repository_root = Path(__file__).resolve().parents[1]
    source = (
        repository_root / "src/mage_ptcg/observability/cabt_trace.py"
    ).read_text(encoding="utf-8")
    transformed = render_student_cabt_trace(source)

    assert "actor_visible_attestation" not in transformed
    assert "ActorVisibleAttestationWriter" not in transformed
    assert "make_traced_agent" in transformed
    assert "TraceWriter" in transformed

    module_path = tmp_path / "cabt_trace_submission_copy.py"
    module_path.write_text(transformed, encoding="utf-8")
    spec = importlib.util.spec_from_file_location(
        "_student_submission_cabt_trace_check", module_path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    for name in (
        "CARD_LIST_COUNT_FIELDS",
        "CARD_SCALAR_FIELDS",
        "OPTION_SCALAR_FIELDS",
        "OPTION_TYPE_NAMES",
        "STATUS_FLAG_FIELDS",
    ):
        assert hasattr(module, name), f"decision_state が必要とする定数が失われた: {name}"
    assert not hasattr(module, "ActorVisibleAttestationWriter")


def _tiny_student_examples() -> list:
    from mage_ptcg.student.dataset import build_rule_bc_example

    def card(card_id: int) -> dict:
        return {
            "id": card_id, "serial": 0, "playerIndex": 0, "hp": 100, "maxHp": 100,
            "appearThisTurn": False, "energies": [], "energyCards": [], "tools": [],
            "preEvolution": [],
        }

    def player(card_id: int) -> dict:
        return {
            "active": [], "asleep": False, "bench": [], "benchMax": 5, "burned": False,
            "confused": False, "deckCount": 53, "discard": [], "hand": [card(card_id)],
            "handCount": 1, "paralyzed": False, "poisoned": False,
            "prize": [object() for _ in range(6)],
        }

    def observation(options: list) -> dict:
        return {
            "current": {
                "energyAttached": False, "firstPlayer": 0,
                "players": [player(100), player(700)], "result": -1, "retreated": False,
                "stadium": [], "stadiumPlayed": False, "supporterPlayed": False,
                "turn": 2, "turnActionCount": 3, "yourIndex": 0,
            },
            "logs": ["not persisted"],
            "search_begin_input": "not persisted",
            "select": {"context": 0, "maxCount": 1, "minCount": 1, "option": options, "type": 0},
            "step": 7,
        }

    deck = [1] * 60
    return [
        build_rule_bc_example(
            observation([{"type": 14}, {"type": 13, "attackId": 1}, {"type": 7, "index": 0}]),
            deck=deck,
            source_id=f"episode-{index}",
            source_revision="test",
        )
        for index in range(12)
    ]


def test_kaggle_student_package_is_minimal_loader_correct_and_falls_back(
    tmp_path: Path,
) -> None:
    """Task 4 統合回帰: 実際の Kaggle Student package生成物で
    禁止パターン不在・必須factory・exact loader・Rule v0 fallback契約(4.6)を検証する。
    """
    from mage_ptcg.student.artifact import build_artifact
    from scripts.build_student_submission import (
        KAGGLE_STUDENT_RUNTIME_PATHS,
        build_student_submission,
    )

    repository_root = Path(__file__).resolve().parents[1]

    artifact_dir = tmp_path / "artifact"
    build_artifact(
        examples=_tiny_student_examples(),
        output_dir=artifact_dir,
        canonical_base_sha="0" * 40,
        work_commit_sha="0" * 40,
        dataset_source_type="PRIVACY_SAFE_DATASET",
        artifact_purpose="ACTUAL_TRAINED",
        epochs=20,
    )

    package_dir = tmp_path / "package"
    build_student_submission(
        artifact_dir / "student-v0.json",
        package_dir,
        runtime_paths=KAGGLE_STUDENT_RUNTIME_PATHS,
        generated_main=render_student_entrypoint().encode(),
        generated_files={
            "src/mage_ptcg/student/__init__.py": render_student_package_init().encode(),
            "src/mage_ptcg/student/model.py": render_student_runtime_model(
                (repository_root / "src/mage_ptcg/student/model.py").read_text(encoding="utf-8")
            ).encode(),
            "src/mage_ptcg/observability/cabt_trace.py": render_student_cabt_trace(
                (repository_root / "src/mage_ptcg/observability/cabt_trace.py").read_text(
                    encoding="utf-8"
                )
            ).encode(),
        },
    )

    main_source = (package_dir / "main.py").read_text(encoding="utf-8")
    runtime_source = (package_dir / "runtime_main.py").read_text(encoding="utf-8")
    for pattern in _FORBIDDEN_SUBMISSION_PATTERNS:
        assert pattern not in main_source, f"生成main.pyに禁止パターン: {pattern}"
        assert pattern not in runtime_source, f"生成runtime_main.pyに禁止パターン: {pattern}"

    import ast

    tree = ast.parse(runtime_source)
    top_level_defs = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}
    assert {"make_rule_agent", "make_student_agent"} <= top_level_defs

    venv_python = _resolve_venv_python()
    temp_home = tmp_path / "temp_home"
    temp_home.mkdir()
    clean_env = _clean_subprocess_env(temp_home)

    # 4.4: Exact Kaggle loader (独立subprocess、PYTHONPATH除去、一時HOME、手動sys.path追加なし)
    loader_script = f"""
import sys
from pathlib import Path
from kaggle_environments.agent import get_last_callable

main_path = Path({str(package_dir / "main.py")!r})
source = main_path.read_text(encoding="utf-8")
selected = get_last_callable(source, path=str(main_path))
print(f"SELECTED={{selected.__name__}}")
"""
    loader_result = subprocess.run(
        [str(venv_python), "-I", "-c", loader_script],
        cwd=tmp_path,
        env=clean_env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert loader_result.returncode == 0, (
        f"stdout={loader_result.stdout} stderr={loader_result.stderr}"
    )
    assert "SELECTED=agent" in loader_result.stdout

    # 4.6: Rule v0 fallback契約を実パッケージのruntime_main.pyで検証する。
    fallback_script = f"""
import sys
sys.path.insert(0, {str(package_dir)!r})
import runtime_main

deck = [1] * 60
rule_agent = runtime_main.make_rule_agent(deck=deck)
student_agent = runtime_main.make_student_agent(deck=deck, model_path="/nonexistent/model.json")

# select なしでは deck を返す
registration_obs = {{"select": None}}
assert rule_agent(registration_obs) == deck
assert student_agent(registration_obs) == deck

# 合法selectionでは合法indexを返す
decision_obs = {{
    "current": {{"yourIndex": 0, "players": [{{"hand": [{{"id": 1}}]}}, {{"hand": []}}]}},
    "select": {{"type": 0, "context": 0, "option": [{{"type": 14}}, {{"type": 7, "index": 0}}], "minCount": 1, "maxCount": 1}},
}}
rule_choice = rule_agent(decision_obs)
assert isinstance(rule_choice, list) and len(rule_choice) == 1
assert all(isinstance(i, int) and 0 <= i < 2 for i in rule_choice)

# Studentモデル異常時は Rule v0 fallback
assert student_agent.student_policy is None, "存在しないmodel_pathでpolicyがNoneでない"
student_choice = student_agent(decision_obs)
assert student_choice == rule_choice

# fallback は決定的
assert rule_agent(decision_obs) == rule_choice
assert student_agent(decision_obs) == student_choice

print("FALLBACK_CONTRACT=PASS")
"""
    fallback_result = subprocess.run(
        [str(venv_python), "-I", "-c", fallback_script],
        cwd=tmp_path,
        env=clean_env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert fallback_result.returncode == 0, (
        f"stdout={fallback_result.stdout} stderr={fallback_result.stderr}"
    )
    assert "FALLBACK_CONTRACT=PASS" in fallback_result.stdout
