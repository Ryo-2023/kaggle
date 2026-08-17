from __future__ import annotations

import ast
from pathlib import Path

import pytest

from mage_ptcg.meta_specialist.cg_p1_public_failure_candidates_v1 import (
    BASE_SOURCE_SHA256,
    CANDIDATE_IDS,
    materialize_public_failure_candidate_v1,
    render_public_failure_candidate_v1,
)


ROOT = Path(__file__).resolve().parents[2]
P1_PACKAGE = ROOT / "runs/final-sprint-autonomous/cg-policy-screen-v1-retry-safe4-20260814/candidates/cg-lethal-target-v1/package"


def test_candidates_are_bounded_and_public_active_only() -> None:
    assert len(CANDIDATE_IDS) == 3
    for candidate_id in CANDIDATE_IDS:
        source = render_public_failure_candidate_v1(candidate_id)
        assert candidate_id in source
        assert "_CG_P1_PUBLIC_BASE_MAIN_SOURCE_SHA256" in source
        patch = source.split("# RESEARCH_VARIANT:", 1)[1]
        assert "opponent.active" in patch
        assert "hand" not in patch and "prize" not in patch and "deck_count" not in patch
        tree = ast.parse(source)
        callables = [node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
        assert callables[-1] == "agent"


def test_source_identity_is_fixed() -> None:
    assert BASE_SOURCE_SHA256 == "1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9"


def test_materialize_is_no_clobbering(tmp_path: Path) -> None:
    target = tmp_path / "package"
    manifest = materialize_public_failure_candidate_v1(
        source_package=P1_PACKAGE,
        output_package=target,
        candidate_id=CANDIDATE_IDS[0],
    )
    assert manifest["candidate_id"] == CANDIDATE_IDS[0]
    assert manifest["base_policy_sha256"] == BASE_SOURCE_SHA256
    assert (target / "deck.csv").read_bytes() == (P1_PACKAGE / "deck.csv").read_bytes()
    with pytest.raises(FileExistsError):
        materialize_public_failure_candidate_v1(
            source_package=P1_PACKAGE,
            output_package=target,
            candidate_id=CANDIDATE_IDS[0],
        )

