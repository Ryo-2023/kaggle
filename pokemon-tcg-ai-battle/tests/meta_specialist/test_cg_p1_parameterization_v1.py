from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

import pytest

from mage_ptcg.meta_specialist.cg_p1_parameterization_v1 import (
    BASE_SOURCE_SHA256,
    PARAMETER_BOUNDS,
    P1ParameterConfig,
    candidate_id_for_config,
    materialize_parameterized_package,
    render_parameterized_source,
)


ROOT = Path(__file__).resolve().parents[2]
P1_PACKAGE = (
    ROOT
    / "runs/final-sprint-autonomous/cg-policy-screen-v1-retry-safe4-20260814"
    / "candidates/cg-lethal-target-v1/package"
)


def _load_agent(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.agent


def test_default_config_is_bounded_and_hash_stable() -> None:
    config = P1ParameterConfig.default()
    config.validate()
    assert len(config.as_dict()) == 15
    assert config.config_sha256() == P1ParameterConfig.default().config_sha256()
    assert len(BASE_SOURCE_SHA256) == 64
    for name, (lower, upper) in PARAMETER_BOUNDS.items():
        value = config.as_dict()[name]
        assert lower <= value <= upper, name


def test_config_rejects_non_integer_or_out_of_bounds_values() -> None:
    with pytest.raises(ValueError, match="out of bounds"):
        P1ParameterConfig.from_mapping({"lethal_bonus": 30001})
    with pytest.raises(ValueError, match="integer"):
        P1ParameterConfig.from_mapping({"lethal_bonus": 1.5})


def test_rendered_default_keeps_agent_last_and_compiles() -> None:
    source = render_parameterized_source(P1ParameterConfig.default(), candidate_id="cg-p1-cem-default")
    tree = ast.parse(source)
    callables = [
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    assert callables[-1] == "agent"
    compile(source, "cg_p1_cem_main.py", "exec")
    assert "CG_P1_CEM_PARAMETERS" in source
    assert "cg-p1-cem-default" in source


def test_default_package_preserves_p1_fallback_actions(tmp_path: Path) -> None:
    target = tmp_path / "candidate"
    result = materialize_parameterized_package(
        source_package=P1_PACKAGE,
        output_package=target,
        config=P1ParameterConfig.default(),
        candidate_id="cg-p1-cem-default",
    )
    assert result["candidate_id"] == "cg-p1-cem-default"
    assert result["parent_policy_sha256"] == BASE_SOURCE_SHA256
    base_agent = _load_agent(P1_PACKAGE / "main.py", "cg_p1_base_for_test")
    candidate_agent = _load_agent(target / "main.py", "cg_p1_candidate_for_test")
    for observation in (
        {"select": None},
        {"select": {"option": [], "minCount": 0, "maxCount": 0}},
        {"select": {"option": "malformed", "minCount": 1, "maxCount": 1}},
    ):
        assert candidate_agent(observation) == base_agent(observation)


def test_candidate_identity_contains_config_hash() -> None:
    config = P1ParameterConfig.default()
    identity = candidate_id_for_config(config, generation=2, index=7)
    assert identity.startswith("cg-p1-cem-g02-c07-")
    assert config.config_sha256()[:12] in identity
