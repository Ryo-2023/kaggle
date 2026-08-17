"""Unit checks for the approval gate's trusted static helpers.

The tests deliberately do not materialize or import any recovered candidate.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path


def _gate_module():
    path = Path(__file__).parents[1] / "scripts" / "gate_recovered_family_assets.py"
    spec = importlib.util.spec_from_file_location("recovered_family_gate", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_static_scan_marks_unpinned_cg_dependency(tmp_path: Path) -> None:
    source = tmp_path / "bundle"
    source.mkdir()
    (source / "main.py").write_text("from cg.api import Observation\n", encoding="utf-8")

    assert _gate_module().static_findings(source)["cg_dependency"] is True


def test_bundle_digest_binds_relative_path_and_content(tmp_path: Path) -> None:
    source = tmp_path / "bundle"
    source.mkdir()
    (source / "main.py").write_text("x = 1\n", encoding="utf-8")
    gate = _gate_module()
    first = gate.tree_sha(source)
    (source / "deck.csv").write_text("1\n", encoding="utf-8")

    assert gate.tree_sha(source) != first
