from __future__ import annotations

from pathlib import Path

import pytest

from mage_ptcg.meta_specialist.cg_p2_context_surface_v1 import (
    P2ContextConfig,
    candidate_id_for_config,
    render_context_source,
)


ROOT = Path(__file__).resolve().parents[2]
P2_SOURCE = (
    ROOT
    / "runs/final-sprint-autonomous/cg-p1-robust-g01-submission-package-v1"
    / "package/main.py"
)


def test_default_config_is_the_identity_context_overlay() -> None:
    config = P2ContextConfig.default()
    assert config.as_dict() == {
        "near_lethal_attack_bonus": 0,
        "threat_energy_attack_bonus": 0,
        "full_bench_attack_bonus": 0,
        "damaged_active_threat_attack_bonus": 0,
    }
    assert len(config.config_sha256()) == 64
    assert candidate_id_for_config(config, generation=0, index=0).startswith(
        "cg-p2-context-g00-c00-"
    )


def test_context_config_rejects_unknown_and_out_of_bound_values() -> None:
    with pytest.raises(ValueError, match="unknown parameter"):
        P2ContextConfig.from_mapping({"private_hand_bonus": 1})
    with pytest.raises(ValueError, match="out of bounds"):
        P2ContextConfig.from_mapping({"near_lethal_attack_bonus": 30001})
    with pytest.raises(ValueError, match="must be an integer"):
        P2ContextConfig.from_mapping({"full_bench_attack_bonus": True})


def test_render_context_source_is_bound_to_the_exact_p2_parent() -> None:
    rendered = render_context_source(
        P2ContextConfig(near_lethal_attack_bonus=12000),
        candidate_id="cg-p2-context-test",
        source_path=P2_SOURCE,
    )
    assert "RESEARCH_PARAMETERIZATION: cg-p2-context-v1" in rendered
    assert "_CG_P2_CONTEXT_BASE_MAIN_SCORE = _main_score" in rendered
    assert "_opponent(obs)" in rendered
    assert "_mine(obs)" in rendered
    assert "_CG_P2_CONTEXT_CANDIDATE_ID = \"cg-p2-context-test\"" in rendered
