from __future__ import annotations

import ast
from pathlib import Path

import pytest

from mage_ptcg.meta_specialist.cg_policy_candidate_v1 import (
    BASE_SOURCE_SHA256,
    VARIANT_IDS,
    render_variant_source,
)


def test_policy_candidate_variants_are_bounded_and_distinct() -> None:
    assert VARIANT_IDS == (
        "cg-lethal-target-v1",
        "cg-retreat-damage-v1",
        "cg-attach-threshold-v1",
        "cg-overkill-conservation-v1",
    )
    sources = [render_variant_source(candidate_id) for candidate_id in VARIANT_IDS]
    assert len(set(sources)) == len(VARIANT_IDS)
    for source in sources:
        assert "_CG_POLICY_BASE_MAIN_SOURCE_SHA256" in source
        assert "def agent(" in source
        assert "from cg.api" in source
        assert 'getattr(option, "type", None)' in source
        assert 'getattr(obs, "select", None)' in source
        assert "_CG_POLICY_BASE_SCORE" in source
        assert "_CG_POLICY_BASE_AGENT = agent" in source


@pytest.mark.parametrize(
    ("candidate_id", "required_marker"),
    (
        ("cg-attach-threshold-v1", "RESEARCH_VARIANT: cg-attach-threshold-v1"),
        ("cg-overkill-conservation-v1", "RESEARCH_VARIANT: cg-overkill-conservation-v1"),
    ),
)
def test_new_policy_surfaces_are_public_and_bounded(candidate_id: str, required_marker: str) -> None:
    source = render_variant_source(candidate_id)
    assert required_marker in source
    assert "_CG_POLICY_BASE_MAIN_SCORE" in source
    assert "12000" in source
    assert "_opponent(obs)" in source or "_available_attack_damage" in source


def test_unknown_variant_fails_closed() -> None:
    with pytest.raises(ValueError, match="unknown cg policy candidate"):
        render_variant_source("cg-unknown-v1")


def test_base_source_identity_is_a_real_hash() -> None:
    assert len(BASE_SOURCE_SHA256) == 64
    assert all(char in "0123456789abcdef" for char in BASE_SOURCE_SHA256)


def test_rendered_variant_keeps_agent_as_last_source_callable() -> None:
    for candidate_id in VARIANT_IDS:
        tree = ast.parse(render_variant_source(candidate_id))
        callables = [
            node.name
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        assert callables[-1] == "agent"
