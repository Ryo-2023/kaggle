from __future__ import annotations

import ast
from pathlib import Path

import pytest

from mage_ptcg.meta_specialist.cg_p1_observed_failure_v1 import (
    BASE_SOURCE_SHA256,
    VARIANT_IDS,
    materialize_observed_failure_variant_v1,
    render_observed_failure_variant_v1,
)


ROOT = Path(__file__).resolve().parents[2]
P1_PACKAGE = (
    ROOT
    / "runs/final-sprint-autonomous/cg-policy-screen-v1-retry-safe4-20260814"
    / "candidates/cg-lethal-target-v1/package"
)


def test_observed_failure_variants_are_public_and_last_callable() -> None:
    assert VARIANT_IDS == (
        "cg-p1-heavy-active-attack-v1",
        "cg-p1-very-heavy-active-attack-v1",
        "cg-p1-heavy-active-conserve-v1",
        "cg-p1-abomasnow-pressure-v1",
        "cg-p1-ursaluna-pressure-v1",
    )
    for candidate_id in VARIANT_IDS:
        source = render_observed_failure_variant_v1(candidate_id)
        assert "RESEARCH_VARIANT:" in source
        assert candidate_id in source
        assert "maxHp" in source
        assert "_CG_P1_BASE_MAIN_SOURCE_SHA256" in source
        tree = ast.parse(source)
        callables = [
            node.name
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        assert callables[-1] == "agent"


def test_observed_failure_source_identity_is_fixed() -> None:
    assert BASE_SOURCE_SHA256 == (
        "1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9"
    )


def test_materialize_observed_failure_variant_is_no_clobbering(tmp_path: Path) -> None:
    target = tmp_path / "package"
    manifest = materialize_observed_failure_variant_v1(
        source_package=P1_PACKAGE,
        output_package=target,
        candidate_id="cg-p1-heavy-active-attack-v1",
    )
    assert manifest["candidate_id"] == "cg-p1-heavy-active-attack-v1"
    assert manifest["base_policy_sha256"] == BASE_SOURCE_SHA256
    assert (target / "deck.csv").read_bytes() == (P1_PACKAGE / "deck.csv").read_bytes()
    source = (target / "main.py").read_text(encoding="utf-8")
    assert "RESEARCH_VARIANT: cg-p1-heavy-active-attack-v1" in source
    with pytest.raises(FileExistsError):
        materialize_observed_failure_variant_v1(
            source_package=P1_PACKAGE,
            output_package=target,
            candidate_id="cg-p1-heavy-active-attack-v1",
        )


def test_observed_failure_patch_does_not_read_private_opponent_fields() -> None:
    source = render_observed_failure_variant_v1("cg-p1-heavy-active-attack-v1")
    assert "hand" not in source.split("# RESEARCH_VARIANT:", 1)[1]
    assert "prize" not in source.split("# RESEARCH_VARIANT:", 1)[1]
    assert "deck_count" not in source.split("# RESEARCH_VARIANT:", 1)[1]


def test_conserve_variant_is_the_explicit_counter_hypothesis() -> None:
    source = render_observed_failure_variant_v1("cg-p1-heavy-active-conserve-v1")
    assert "return score - 12000" in source
    assert "max_hp >= 300" in source


def test_archetype_variants_use_only_visible_active_ids() -> None:
    abomasnow = render_observed_failure_variant_v1("cg-p1-abomasnow-pressure-v1")
    ursaluna = render_observed_failure_variant_v1("cg-p1-ursaluna-pressure-v1")
    assert "{721, 722, 723}" in abomasnow
    assert "{65, 135, 1073, 1074}" in ursaluna
    for source in (abomasnow, ursaluna):
        patch = source.split("# RESEARCH_VARIANT:", 1)[1]
        assert "hand" not in patch
        assert "prize" not in patch
        assert "deck_count" not in patch
