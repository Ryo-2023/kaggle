from __future__ import annotations

import ast
from pathlib import Path

import pytest

from mage_ptcg.meta_specialist.cg_p1_seat_conditioned_renderer_v1 import (
    BASE_SOURCE_SHA256,
    PARAMETER_BOUNDS,
    SeatConditionedConfig,
    candidate_id_for_config,
    materialize_seat_conditioned_package,
    render_seat_conditioned_source,
)
from mage_ptcg.meta_specialist.self_owned_cg_package_v1 import verify_self_owned_cg_package_v1


ROOT = Path(__file__).resolve().parents[2]
P1_PACKAGE = ROOT / "runs/final-sprint-autonomous/cg-policy-screen-v1-retry-safe4-20260814/candidates/cg-lethal-target-v1/package"
DECK_PACKAGE = ROOT / "runs/cg-self-owned-deck-generation-v2-20260816-00/package"


def test_default_config_is_identity_and_bounded() -> None:
    config = SeatConditionedConfig.default()
    config.validate()
    assert len(config.as_dict()) == 8
    assert config.config_sha256() == SeatConditionedConfig.default().config_sha256()
    for name, (lower, upper) in PARAMETER_BOUNDS.items():
        assert lower <= config.as_dict()[name] <= upper, name


def test_config_rejects_unknown_and_non_integer_values() -> None:
    with pytest.raises(ValueError, match="unknown"):
        SeatConditionedConfig.from_mapping({"not_a_knob": 1})
    with pytest.raises(ValueError, match="integer"):
        SeatConditionedConfig.from_mapping({"seat0_attack_bonus": 1.5})


def test_rendered_source_is_compilable_and_uses_public_seat_only() -> None:
    source = render_seat_conditioned_source(
        SeatConditionedConfig.from_mapping({"seat0_attack_bonus": 12000}),
        candidate_id="cg-seat-conditioned-test",
    )
    tree = ast.parse(source)
    callables = [
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    assert callables[-1] == "agent"
    compile(source, "cg_seat_conditioned_main.py", "exec")
    assert "CG_SEAT_CONDITIONED_PARAMETERS" in source
    assert "yourIndex" in source
    assert "opponent.hand" not in source
    assert "opponent.deck" not in source
    assert "opponent.discard" not in source


def test_candidate_id_binds_config_and_coordinates() -> None:
    config = SeatConditionedConfig.default()
    identity = candidate_id_for_config(config, generation=3, index=2)
    assert identity.startswith("cg-seat-conditioned-g03-c02-")
    assert config.config_sha256()[:12] in identity


def test_materializer_rebinds_policy_and_self_owned_manifest(tmp_path: Path) -> None:
    output = tmp_path / "candidate"
    manifest = materialize_seat_conditioned_package(
        source_package=P1_PACKAGE,
        self_owned_deck_package=DECK_PACKAGE,
        output_package=output,
        config=SeatConditionedConfig.from_mapping({"seat1_retreat_bonus": 9000}),
        candidate_id="cg-seat-conditioned-materializer-test",
    )
    verified = verify_self_owned_cg_package_v1(output)
    assert manifest["candidate_id"] == verified["candidate_id"]
    assert verified["parent_policy_sha256"] == BASE_SOURCE_SHA256
    assert verified["policy_sha256"] != BASE_SOURCE_SHA256
    assert '"seat1_retreat_bonus":9000' in (output / "main.py").read_text(encoding="utf-8")
    assert (output / "cg_seat_conditioned_manifest.json").is_file()
