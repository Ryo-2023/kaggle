from __future__ import annotations

import ast
from pathlib import Path

import pytest

from mage_ptcg.meta_specialist.cg_p1_turn_planner_v1 import (
    BASE_SOURCE_SHA256,
    PARAMETER_BOUNDS,
    TurnPlannerConfig,
    candidate_id_for_config,
    render_turn_planner_source,
)


def test_default_config_is_bounded_and_hash_stable() -> None:
    config = TurnPlannerConfig.default()
    config.validate()
    assert len(config.as_dict()) == 6
    assert config.config_sha256() == TurnPlannerConfig.default().config_sha256()
    for name, (lower, upper) in PARAMETER_BOUNDS.items():
        assert lower <= config.as_dict()[name] <= upper


def test_config_rejects_unknown_and_non_integer_values() -> None:
    with pytest.raises(ValueError, match="unknown"):
        TurnPlannerConfig.from_mapping({"not_a_knob": 1})
    with pytest.raises(ValueError, match="integer"):
        TurnPlannerConfig.from_mapping({"ready_attach_bonus": 1.5})


def test_rendered_source_is_compilable_and_keeps_agent_last() -> None:
    source = render_turn_planner_source(
        TurnPlannerConfig.default(), candidate_id="cg-p1-turn-planner-test"
    )
    tree = ast.parse(source)
    callables = [
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    assert callables[-1] == "agent"
    compile(source, "cg_p1_turn_planner_main.py", "exec")
    assert "CG_TP_PARAMETERS" in source
    assert len(BASE_SOURCE_SHA256) == 64


def test_candidate_id_binds_config_and_coordinates() -> None:
    config = TurnPlannerConfig.default()
    identity = candidate_id_for_config(config, generation=3, index=2)
    assert identity.startswith("cg-p1-turn-planner-g03-c02-")
    assert config.config_sha256()[:12] in identity
