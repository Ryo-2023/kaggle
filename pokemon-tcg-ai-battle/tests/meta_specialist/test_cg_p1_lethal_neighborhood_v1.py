from __future__ import annotations

import ast
from pathlib import Path

import pytest

from mage_ptcg.meta_specialist.cg_p1_lethal_neighborhood_v1 import (
    BASE_SOURCE_SHA256,
    VARIANT_IDS,
    materialize_p1_lethal_variant_package_v1,
    render_p1_lethal_variant_source_v1,
)


ROOT = Path(__file__).resolve().parents[2]
P1_PACKAGE = ROOT / (
    "runs/final-sprint-autonomous/"
    "cg-policy-screen-v1-retry-safe4-20260814/candidates/"
    "cg-lethal-target-v1/package"
)


def test_observed_lethal_neighborhood_has_three_bounded_variants() -> None:
    assert VARIANT_IDS == (
        "cg-lethal-lock-v1",
        "cg-lethal-setup-lock-v1",
        "cg-lethal-resource-first-v1",
    )
    for candidate_id in VARIANT_IDS:
        source = render_p1_lethal_variant_source_v1(candidate_id)
        assert f"RESEARCH_VARIANT: {candidate_id}" in source
        assert "OBSERVED_FAILURE" in source
        tree = ast.parse(source)
        functions = [node.name for node in tree.body if isinstance(node, ast.FunctionDef)]
        assert functions[-1] == "agent"


def test_base_source_identity_is_fixed() -> None:
    assert BASE_SOURCE_SHA256 == "1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9"


def test_materialize_is_no_clobbering_and_keeps_deck(tmp_path: Path) -> None:
    target = tmp_path / "package"
    manifest = materialize_p1_lethal_variant_package_v1(
        source_package=P1_PACKAGE,
        output_package=target,
        candidate_id="cg-lethal-lock-v1",
    )
    assert manifest["candidate_id"] == "cg-lethal-lock-v1"
    assert manifest["base_policy_sha256"] == BASE_SOURCE_SHA256
    assert (target / "deck.csv").read_bytes() == (P1_PACKAGE / "deck.csv").read_bytes()
    with pytest.raises(FileExistsError):
        materialize_p1_lethal_variant_package_v1(
            source_package=P1_PACKAGE,
            output_package=target,
            candidate_id="cg-lethal-lock-v1",
        )


def test_runner_requires_p1_control_and_sealed_workers() -> None:
    from scripts.run_cg_p1_lethal_neighborhood_v1 import (
        CgP1LethalNeighborhoodError,
        validate_screen_contract_v1,
    )

    with pytest.raises(CgP1LethalNeighborhoodError):
        validate_screen_contract_v1(workers=1, worker_recycle_games=16, stage_games=48)
    validate_screen_contract_v1(workers=12, worker_recycle_games=16, stage_games=48)
