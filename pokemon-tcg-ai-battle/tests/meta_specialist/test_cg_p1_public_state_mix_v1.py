from __future__ import annotations

import ast

import pytest

from mage_ptcg.meta_specialist.cg_p1_public_state_mix_v1 import (
    BASE_SOURCE_SHA256,
    PARAMETER_BOUNDS,
    PublicStateMixConfig,
    candidate_id_for_config,
    materialize_public_state_mix_package,
    render_public_state_mix_source,
)
from mage_ptcg.meta_specialist.self_owned_cg_package_v1 import verify_self_owned_cg_package_v1

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
P1_PACKAGE = (
    ROOT
    / "runs/final-sprint-autonomous/cg-policy-screen-v1-retry-safe4-20260814"
    / "candidates/cg-lethal-target-v1/package"
)
DECK_PACKAGE = ROOT / "runs/cg-self-owned-deck-generation-v2-20260816-00/package"


def test_default_config_is_bounded_and_hash_stable() -> None:
    config = PublicStateMixConfig.default()
    config.validate()
    assert len(config.as_dict()) == 8
    assert config.config_sha256() == PublicStateMixConfig.default().config_sha256()
    assert len(BASE_SOURCE_SHA256) == 64
    for name, (lower, upper) in PARAMETER_BOUNDS.items():
        assert lower <= config.as_dict()[name] <= upper, name


def test_config_rejects_unknown_or_non_integer_values() -> None:
    with pytest.raises(ValueError, match="unknown"):
        PublicStateMixConfig.from_mapping({"not_a_knob": 1})
    with pytest.raises(ValueError, match="integer"):
        PublicStateMixConfig.from_mapping({"behind_attack_bonus": 1.5})


def test_rendered_source_is_compilable_public_only_and_keeps_agent_last() -> None:
    source = render_public_state_mix_source(
        PublicStateMixConfig.default(), candidate_id="cg-p1-public-state-mix-test"
    )
    tree = ast.parse(source)
    callables = [
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    assert callables[-1] == "agent"
    compile(source, "cg_p1_public_state_mix_main.py", "exec")
    assert "CG_PSM_PARAMETERS" in source
    assert "opponent.prize" not in source
    assert "opponent.hand" not in source
    assert "opponent.deck" not in source


def test_candidate_id_binds_config_and_coordinates() -> None:
    config = PublicStateMixConfig.default()
    identity = candidate_id_for_config(config, generation=4, index=3)
    assert identity.startswith("cg-p1-public-state-mix-g04-c03-")
    assert config.config_sha256()[:12] in identity


def test_materializer_binds_public_policy_to_self_owned_deck(tmp_path: Path) -> None:
    output = tmp_path / "candidate"
    manifest = materialize_public_state_mix_package(
        source_package=P1_PACKAGE,
        self_owned_deck_package=DECK_PACKAGE,
        output_package=output,
        config=PublicStateMixConfig.from_mapping({"behind_attack_bonus": 12000}),
        candidate_id="cg-p1-public-state-mix-materializer-test",
    )
    verified = verify_self_owned_cg_package_v1(output)
    assert manifest["candidate_id"] == verified["candidate_id"]
    assert verified["parent_policy_sha256"] == BASE_SOURCE_SHA256
    assert verified["policy_sha256"] != BASE_SOURCE_SHA256
    assert '"behind_attack_bonus":12000' in (output / "main.py").read_text(encoding="utf-8")
    assert (output / "cg_p1_public_state_mix_manifest.json").is_file()
