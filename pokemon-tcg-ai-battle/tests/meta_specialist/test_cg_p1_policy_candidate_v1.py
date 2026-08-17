from __future__ import annotations

import ast
from pathlib import Path

import pytest

from mage_ptcg.meta_specialist.cg_p1_policy_candidate_v1 import (
    BASE_SOURCE_SHA256,
    VARIANT_IDS,
    materialize_p1_variant_package_v1,
    render_p1_variant_source_v1,
)
from scripts.run_cg_p1_variant_screen_v1 import _aggregate


ROOT = Path(__file__).resolve().parents[2]
P1_PACKAGE = ROOT / "runs/final-sprint-autonomous/cg-policy-screen-v1-retry-safe4-20260814/candidates/cg-lethal-target-v1/package"


def test_p1_variants_are_bounded_and_keep_agent_last() -> None:
    assert VARIANT_IDS == (
        "cg-lethal-retreat-damage-v2",
        "cg-lethal-attach-threshold-v2",
        "cg-lethal-overkill-conservation-v2",
    )
    sources = [render_p1_variant_source_v1(candidate_id) for candidate_id in VARIANT_IDS]
    assert len(set(sources)) == 3
    for candidate_id, source in zip(VARIANT_IDS, sources):
        assert "RESEARCH_VARIANT:" in source
        assert candidate_id in source
        assert "_CG_P1_BASE_MAIN_SOURCE_SHA256" in source
        tree = ast.parse(source)
        callables = [
            node.name
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        assert callables[-1] == "agent"


def test_p1_base_source_identity_is_fixed() -> None:
    assert BASE_SOURCE_SHA256 == "1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9"


def test_materialize_p1_variant_package_is_no_clobbering(tmp_path: Path) -> None:
    target = tmp_path / "package"
    manifest = materialize_p1_variant_package_v1(
        source_package=P1_PACKAGE,
        output_package=target,
        candidate_id="cg-lethal-retreat-damage-v2",
    )
    assert manifest["candidate_id"] == "cg-lethal-retreat-damage-v2"
    assert manifest["base_policy_sha256"] == BASE_SOURCE_SHA256
    assert (target / "deck.csv").read_bytes() == (P1_PACKAGE / "deck.csv").read_bytes()
    assert "RESEARCH_VARIANT: cg-lethal-retreat-damage-v2" in (target / "main.py").read_text(encoding="utf-8")
    with pytest.raises(FileExistsError):
        materialize_p1_variant_package_v1(
            source_package=P1_PACKAGE,
            output_package=target,
            candidate_id="cg-lethal-retreat-damage-v2",
        )


def test_variant_screen_aggregate_separates_seats_without_recursion() -> None:
    rows = [
        {"outcome": "win", "seat": 0},
        {"outcome": "loss", "seat": 1},
    ]
    summary = _aggregate(rows)
    assert summary["wins"] == 1
    assert summary["seat"]["0"]["wins"] == 1
    assert summary["seat"]["1"]["losses"] == 1
