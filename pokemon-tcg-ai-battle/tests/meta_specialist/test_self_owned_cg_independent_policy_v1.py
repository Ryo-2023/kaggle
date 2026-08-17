from __future__ import annotations

import ast
from pathlib import Path

import pytest

from mage_ptcg.meta_specialist.cg_independent_policy_renderer_v1 import (
    BASE_SOURCE_SHA256,
    IndependentCgParameterConfig,
    candidate_id_for_config,
    render_independent_source,
)
from mage_ptcg.meta_specialist.self_owned_cg_independent_package_v1 import (
    materialize_self_owned_cg_independent_package_v1,
    verify_self_owned_cg_independent_package_v1,
)


ROOT = Path(__file__).resolve().parents[2]
ROOT_PACKAGE = ROOT / "runs/final-sprint-autonomous/root-cg-submission-candidate-v1-20260814/package"
DECK_PACKAGE = ROOT / "runs/cg-self-owned-deck-generation-v2-20260816-00/package"


def test_independent_renderer_binds_root_source_and_new_surface() -> None:
    config = IndependentCgParameterConfig.default()
    source = render_independent_source(config, candidate_id="independent-test")
    assert BASE_SOURCE_SHA256 == "617a23c060084c8b2601800b4f729238563925165f3520628d938eab065aebef"
    assert "RESEARCH_INDEPENDENT_LINEAGE: root-cg-public-state-v1" in source
    assert "_CG_INDEPENDENT_PARAMETERS" in source
    assert "1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9" not in source
    tree = ast.parse(source)
    callables = [node.name for node in tree.body if isinstance(node, ast.FunctionDef)]
    assert callables[-1] == "agent"


def test_independent_config_is_bounded_and_hashable() -> None:
    config = IndependentCgParameterConfig.from_mapping({"lethal_bonus": 18000})
    assert config.as_dict()["lethal_bonus"] == 18000
    assert len(config.config_sha256()) == 64
    assert candidate_id_for_config(config, generation=2, index=3).startswith("cg-independent-g02-c03-")
    with pytest.raises(ValueError, match="out of bounds"):
        IndependentCgParameterConfig.from_mapping({"nonlethal_attack_penalty": 1})


def test_independent_materializer_emits_verified_self_owned_package(tmp_path: Path) -> None:
    config = IndependentCgParameterConfig.default()
    output = tmp_path / "candidate"
    materialize_self_owned_cg_independent_package_v1(
        source_package=ROOT_PACKAGE,
        self_owned_deck_package=DECK_PACKAGE,
        output_package=output,
        config=config,
        candidate_id="independent-package-test",
    )
    manifest = verify_self_owned_cg_independent_package_v1(output)
    assert manifest["parent_policy_sha256"] == BASE_SOURCE_SHA256
    assert manifest["parent_deck"] is None
    assert manifest["public_parent_read"] is False
    assert manifest["research_only"] is True
    assert manifest["authority"] == {
        "training_allowed": False,
        "promotion_allowed": False,
        "submission_allowed": False,
    }

