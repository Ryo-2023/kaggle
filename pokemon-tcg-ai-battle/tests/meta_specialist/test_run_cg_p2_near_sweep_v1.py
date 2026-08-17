from __future__ import annotations

from scripts.run_cg_p2_near_sweep_v1 import build_near_lethal_configs


def test_near_sweep_is_a_new_strength_grid_and_excludes_the_confirmed_point() -> None:
    configs = build_near_lethal_configs((4000, 8000, 16000, 20000, 24000))
    assert [config.near_lethal_attack_bonus for config in configs] == [4000, 8000, 16000, 20000, 24000]
    assert all(config.threat_energy_attack_bonus == 0 for config in configs)
    assert all(config.full_bench_attack_bonus == 0 for config in configs)
    assert 12000 not in [config.near_lethal_attack_bonus for config in configs]

