from __future__ import annotations

from mage_ptcg.meta_specialist.cg_p2_context_surface_v1 import P2ContextConfig, render_context_source
from scripts.run_cg_p2_tempo_sweep_v1 import build_tempo_configs


def test_tempo_bonus_is_a_new_public_state_axis() -> None:
    config = P2ContextConfig(damaged_active_threat_attack_bonus=12_000)
    assert config.as_dict()["damaged_active_threat_attack_bonus"] == 12_000
    rendered = render_context_source(config, candidate_id="cg-p2-tempo-test")
    assert "damaged_active_threat_attack_bonus" in rendered
    assert "_energy_count(_CG_P2_CONTEXT_TEMPO_OPPONENT_ACTIVE)" in rendered


def test_tempo_sweep_has_only_new_axis_and_unique_strengths() -> None:
    configs = build_tempo_configs((6_000, 12_000, 24_000))
    assert [config.damaged_active_threat_attack_bonus for config in configs] == [6_000, 12_000, 24_000]
    assert all(config.near_lethal_attack_bonus == 0 for config in configs)
    assert all(config.threat_energy_attack_bonus == 0 for config in configs)
    assert all(config.full_bench_attack_bonus == 0 for config in configs)


def test_tempo_sweep_allows_a_signed_follow_up_surface() -> None:
    configs = build_tempo_configs((-6_000, -12_000, -24_000))
    assert [config.damaged_active_threat_attack_bonus for config in configs] == [-6_000, -12_000, -24_000]
