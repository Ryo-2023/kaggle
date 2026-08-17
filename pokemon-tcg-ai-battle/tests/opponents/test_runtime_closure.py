"""Tests for the allow-list Runtime Closure Builder (O6-AUD-001 remediation).

Two fixture styles are used:

* A synthetic, self-contained source tree (``_write_fixture_source``) that
  exercises every inclusion/exclusion signal in isolation without depending
  on any real team agent branch.
* The three real ``origin/agents/*`` VALIDATED Native Team Agent branches,
  to prove the closure actually minimizes the audited 315-file/17.5MiB
  baseline down to a small, deterministic, agent-specific set.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mage_ptcg.opponents.core import OpponentError, TeamBranchCollector
from mage_ptcg.opponents.runtime_closure import build_runtime_closure


def _write_fixture_source(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "main.py").write_text(
        "import ctypes, importlib, json, os\n"
        "from helper_pkg.util import helper_fn\n"
        "import tests.helper as _forbidden_but_reachable\n"
        "try:\n"
        "    import totally_not_a_real_package_xyz\n"
        "except ImportError:\n"
        "    pass\n"
        "\n"
        "def agent(observation, configuration=None):\n"
        "    helper_fn()\n"
        "    _forbidden_but_reachable.noop()\n"
        "    here = os.path.dirname(os.path.abspath(__file__))\n"
        "    with open(os.path.join(here, 'config.json'), encoding='utf-8') as fh:\n"
        "        json.load(fh)\n"
        "    dyn = importlib.import_module('helper_pkg.dynamic_only')\n"
        "    dyn.noop()\n"
        "    try:\n"
        "        ctypes.CDLL(os.path.join(here, 'lib', 'stub.so'))\n"
        "    except OSError:\n"
        "        pass\n"
        "    return [0]\n",
        encoding="utf-8",
    )
    (root / "deck.csv").write_text("\n".join(str(n) for n in range(1, 61)) + "\n", encoding="utf-8")
    (root / "config.json").write_text(json.dumps({"ok": True}), encoding="utf-8")

    helper_pkg = root / "helper_pkg"
    helper_pkg.mkdir()
    (helper_pkg / "__init__.py").write_text("", encoding="utf-8")
    (helper_pkg / "util.py").write_text("def helper_fn():\n    return 1\n", encoding="utf-8")
    (helper_pkg / "unused.py").write_text("def never_called():\n    return 2\n", encoding="utf-8")
    (helper_pkg / "dynamic_only.py").write_text("def noop():\n    return 3\n", encoding="utf-8")

    lib_dir = root / "lib"
    lib_dir.mkdir()
    (lib_dir / "stub.so").write_bytes(b"not a real ELF file, just bytes to hash")
    (lib_dir / "other_arch.so").write_bytes(b"unreferenced native binary for a different arch")

    tests_dir = root / "tests"
    tests_dir.mkdir()
    (tests_dir / "__init__.py").write_text("", encoding="utf-8")
    (tests_dir / "helper.py").write_text("def noop():\n    return 4\n", encoding="utf-8")
    (tests_dir / "test_something.py").write_text("def test_x():\n    assert True\n", encoding="utf-8")

    (root / "docs").mkdir()
    (root / "docs" / "notes.md").write_text("internal notes", encoding="utf-8")
    (root / "report").mkdir()
    (root / "report" / "output.json").write_text("{}", encoding="utf-8")
    (root / "experiments").mkdir()
    (root / "experiments" / "plan.md").write_text("experiment plan", encoding="utf-8")
    (root / "data").mkdir()
    (root / "data" / "big.jsonl").write_text('{"row": 1}\n', encoding="utf-8")
    (root / "README.md").write_text("# fixture agent", encoding="utf-8")
    pycache = root / "helper_pkg" / "__pycache__"
    pycache.mkdir()
    (pycache / "util.cpython-312.pyc").write_bytes(b"\x00\x01")


@pytest.fixture(scope="module")
def fixture_closure(tmp_path_factory) -> dict:
    root = tmp_path_factory.mktemp("closure-fixture-src")
    _write_fixture_source(root)
    scratch = tmp_path_factory.mktemp("closure-fixture-scratch")
    result = build_runtime_closure(source_root=root, entrypoint="main.py:agent", agent_id="fixture-agent", scratch_root=scratch, timeout_seconds=30.0)
    return {"root": root, "result": result}


def test_required_module_and_dynamic_import_and_config_included(fixture_closure):
    report = fixture_closure["result"]["report"]
    files = fixture_closure["result"]["files"]
    assert "main.py" in files
    assert "helper_pkg/util.py" in files  # static import closure
    assert "helper_pkg/dynamic_only.py" in files  # dynamic-only: importlib.import_module, not a static import
    assert "config.json" in files  # opened at runtime, not python
    assert "deck.csv" in files  # declared deck artifact rule
    assert "helper_pkg/__init__.py" in files  # package __init__ chain


def test_unused_module_excluded(fixture_closure):
    files = fixture_closure["result"]["files"]
    report = fixture_closure["result"]["report"]
    assert "helper_pkg/unused.py" not in files
    categories = {entry["path"]: entry["category"] for entry in report["excluded"]}
    assert categories["helper_pkg/unused.py"] == "not_in_runtime_closure"


def test_forbidden_categories_excluded(fixture_closure):
    files = fixture_closure["result"]["files"]
    report = fixture_closure["result"]["report"]
    categories = {entry["path"]: entry["category"] for entry in report["excluded"]}
    # cache: the fixture's *pre-existing* dummy .pyc is legitimately re-opened
    # by Python's own import machinery during the dynamic trace (freshness
    # check / recompile), so the deny-list catches it via the "blocked"
    # (required-but-forbidden) path rather than the plain "excluded" path --
    # either way it must never end up in the bundled files.
    all_category_entries = {entry["path"]: entry["category"] for entry in [*report["excluded"], *report["blocked"]]}
    assert "docs/notes.md" not in files and categories["docs/notes.md"] == "docs"
    assert "report/output.json" not in files and categories["report/output.json"] == "report"
    assert "experiments/plan.md" not in files and categories["experiments/plan.md"] == "experiments"
    assert "data/big.jsonl" not in files and categories["data/big.jsonl"] == "data"
    assert "README.md" not in files and categories["README.md"] == "readme"
    assert "helper_pkg/__pycache__/util.cpython-312.pyc" not in files
    assert all_category_entries["helper_pkg/__pycache__/util.cpython-312.pyc"] == "cache"
    assert "tests/test_something.py" not in files and categories["tests/test_something.py"] == "tests"


def test_import_reachable_but_forbidden_file_is_blocked_not_included(fixture_closure):
    """main.py does ``import tests.helper`` at module scope: it is both
    statically and dynamically reachable, but tests/ is a hard deny-list
    category. It must show up as ``blocked``, never in ``files``."""
    files = fixture_closure["result"]["files"]
    report = fixture_closure["result"]["report"]
    assert "tests/helper.py" not in files
    blocked_paths = {entry["path"] for entry in report["blocked"]}
    assert "tests/helper.py" in blocked_paths
    blocked_entry = next(e for e in report["blocked"] if e["path"] == "tests/helper.py")
    assert blocked_entry["category"] == "tests"


def test_undeclared_host_dependency_detected(fixture_closure):
    report = fixture_closure["result"]["report"]
    assert "totally_not_a_real_package_xyz" in report["unresolved_imports"]["unknown_third_party"]
    assert "os" in report["unresolved_imports"]["stdlib"]


def test_native_binary_included_only_when_dlopened_and_foreign_binary_excluded(fixture_closure):
    files = fixture_closure["result"]["files"]
    report = fixture_closure["result"]["report"]
    assert "lib/stub.so" in files
    included_paths = {entry["path"] for entry in report["native_binaries"]["included"]}
    assert included_paths == {"lib/stub.so"}
    assert "lib/other_arch.so" not in files
    excluded_native = {entry["path"] for entry in report["native_binaries"]["excluded_not_used_on_build_host"]}
    assert "lib/other_arch.so" in excluded_native


def test_bundle_size_regression_and_no_full_source_copy(fixture_closure):
    report = fixture_closure["result"]["report"]
    assert report["file_count_before"] > report["file_count_after"]
    assert report["file_count_after"] <= 10
    forbidden_relpaths = {"docs/notes.md", "report/output.json", "experiments/plan.md", "data/big.jsonl", "README.md", "tests/test_something.py", "tests/helper.py"}
    assert forbidden_relpaths.isdisjoint(fixture_closure["result"]["files"].keys())


def test_deterministic_same_content_same_closure(tmp_path_factory):
    root = tmp_path_factory.mktemp("closure-determinism-src")
    _write_fixture_source(root)
    scratch_a = tmp_path_factory.mktemp("closure-determinism-scratch-a")
    scratch_b = tmp_path_factory.mktemp("closure-determinism-scratch-b")
    first = build_runtime_closure(source_root=root, entrypoint="main.py:agent", agent_id="fixture-agent", scratch_root=scratch_a, timeout_seconds=30.0)
    second = build_runtime_closure(source_root=root, entrypoint="main.py:agent", agent_id="fixture-agent", scratch_root=scratch_b, timeout_seconds=30.0)
    assert sorted(first["files"]) == sorted(second["files"])
    assert first["files"] == second["files"]
    assert first["report"]["required"] == second["report"]["required"]


def test_missing_entrypoint_fails_closed(tmp_path):
    root = tmp_path / "src"
    root.mkdir()
    (root / "main.py").write_text("def other(observation):\n    return [0]\n", encoding="utf-8")
    with pytest.raises(OpponentError, match="entrypoint must be"):
        build_runtime_closure(source_root=root, entrypoint="main.py", agent_id="broken", scratch_root=tmp_path / "scratch")
    with pytest.raises(OpponentError, match="entrypoint file not found"):
        build_runtime_closure(source_root=root, entrypoint="does_not_exist.py:agent", agent_id="broken", scratch_root=tmp_path / "scratch2")


def test_incompatible_or_broken_entrypoint_rejected_not_silently_empty(tmp_path):
    """A harness/import failure during the closure trace must fail closed
    (raise) rather than silently returning an incomplete/wrong closure --
    this is the operationally relevant form of 'incompatible host dependency
    rejected' for a build-time tool that cannot fabricate a missing host
    package."""
    root = tmp_path / "src"
    root.mkdir()
    (root / "main.py").write_text(
        "def agent(observation, configuration=None):\n"
        "    raise RuntimeError('boom')\n",
        encoding="utf-8",
    )
    with pytest.raises(OpponentError, match="closure trace did not run to completion"):
        build_runtime_closure(source_root=root, entrypoint="main.py:agent", agent_id="broken", scratch_root=tmp_path / "scratch", timeout_seconds=15.0)


@pytest.mark.parametrize("branch,commit", [
    ("origin/agents/ozawa-crustle-rule", "9c9a7766a3ec0bc3638eeda045a8c13719678aac"),
    ("origin/agents/nihei-alakazam", "26372f0fa4cf6c98ea42cd2d78f838064a978ca0"),
    ("origin/agents/nihei-festival-lead", "158f435cc22c7b2a274b7e9d52167aba8c399e7a"),
])
def test_real_team_agent_branches_minimize_to_small_deterministic_closure(tmp_path, branch, commit):
    collector = TeamBranchCollector(".")
    src_dir = tmp_path / "src"
    collector.materialize(commit, src_dir)
    result = build_runtime_closure(source_root=src_dir, entrypoint="main.py:agent", agent_id=branch.replace("/", "-"),
                                    scratch_root=tmp_path / "trace-scratch", timeout_seconds=120.0)
    report = result["report"]
    files = result["files"]
    assert report["file_count_after"] <= 8
    assert report["file_count_before"] > report["file_count_after"]
    assert set(files) == {"main.py", "deck.csv", "cg/__init__.py", "cg/api.py", "cg/sim.py", "cg/utils.py", "cg/libcg.so"}
    assert report["unresolved_imports"]["unknown_third_party"] == []
    assert report["blocked"] == []
    native_included = {entry["path"] for entry in report["native_binaries"]["included"]}
    assert native_included == {"cg/libcg.so"}
    foreign = {"cg/cg.dll", "cg/libcg.dylib", "cg/libcg-arm64.so"}
    assert foreign.isdisjoint(files.keys())
    assert "cg/game.py" not in files  # not imported by main.py -> cg.api -> cg.sim/cg.utils
    assert "seed" not in report["dynamic_trace"]["cabt_configuration_keys"]
