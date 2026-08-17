from __future__ import annotations

import ast
from pathlib import Path

import pytest

from mage_ptcg.meta_specialist.cg_p1_margin_gated_renderer_v1 import (
    BASE_SOURCE_SHA256,
    PARAMETER_BOUNDS,
    MarginGatedConfig,
    candidate_id_for_config,
    materialize_margin_gated_package,
    render_margin_gated_source,
)
from mage_ptcg.meta_specialist.self_owned_cg_package_v1 import verify_self_owned_cg_package_v1


ROOT = Path(__file__).resolve().parents[2]
P1_PACKAGE = ROOT / "runs/final-sprint-autonomous/cg-policy-screen-v1-retry-safe4-20260814/candidates/cg-lethal-target-v1/package"
DECK_PACKAGE = ROOT / "runs/cg-self-owned-deck-generation-v2-20260816-00/package"


def test_default_config_is_bounded_and_identity_safe() -> None:
    config = MarginGatedConfig.default()
    config.validate()
    assert len(config.as_dict()) == 8
    assert config.config_sha256() == MarginGatedConfig.default().config_sha256()
    for name, (lower, upper) in PARAMETER_BOUNDS.items():
        assert lower <= config.as_dict()[name] <= upper, name


def test_config_rejects_unknown_and_non_integer_values() -> None:
    with pytest.raises(ValueError, match="unknown"):
        MarginGatedConfig.from_mapping({"not_a_knob": 1})
    with pytest.raises(ValueError, match="integer"):
        MarginGatedConfig.from_mapping({"score_margin": 1.5})


def test_rendered_source_is_compilable_and_public_only() -> None:
    source = render_margin_gated_source(
        MarginGatedConfig.from_mapping({"score_margin": 6000, "seat_bias": 2500}),
        candidate_id="cg-margin-gated-test",
    )
    tree = ast.parse(source)
    callables = [
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    assert callables[-1] == "agent"
    compile(source, "cg_margin_gated_main.py", "exec")
    assert "CG_MARGIN_GATED_PARAMETERS" in source
    assert "opponent.hand" not in source
    assert "opponent.deck" not in source
    assert "opponent.discard" not in source


def test_candidate_id_binds_config_and_coordinates() -> None:
    config = MarginGatedConfig.default()
    identity = candidate_id_for_config(config, generation=3, index=2)
    assert identity.startswith("cg-margin-gated-g03-c02-")
    assert config.config_sha256()[:12] in identity


def test_materializer_rebinds_policy_and_self_owned_manifest(tmp_path: Path) -> None:
    output = tmp_path / "candidate"
    manifest = materialize_margin_gated_package(
        source_package=P1_PACKAGE,
        self_owned_deck_package=DECK_PACKAGE,
        output_package=output,
        config=MarginGatedConfig.from_mapping({"score_margin": 6000, "lethal_bonus": 12000}),
        candidate_id="cg-margin-gated-materializer-test",
    )
    verified = verify_self_owned_cg_package_v1(output)
    assert manifest["candidate_id"] == verified["candidate_id"]
    assert verified["parent_policy_sha256"] == BASE_SOURCE_SHA256
    assert verified["policy_sha256"] != BASE_SOURCE_SHA256
    assert '"lethal_bonus":12000' in (output / "main.py").read_text(encoding="utf-8")
    assert (output / "cg_margin_gated_manifest.json").is_file()
