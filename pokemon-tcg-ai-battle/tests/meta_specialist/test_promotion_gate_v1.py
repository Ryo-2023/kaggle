from __future__ import annotations

from mage_ptcg.meta_specialist.promotion_gate_v1 import promotion_gate_v1


def test_promotion_gate_requires_nonnegative_paired_interval_and_fault_budget() -> None:
    assert promotion_gate_v1(
        paired_delta=0.1, ci_lower=0.01, fault_rate=0.0, seat_delta=0.0,
        training_seed_consistency=True,
    )
    assert not promotion_gate_v1(
        paired_delta=0.1, ci_lower=-0.01, fault_rate=0.0, seat_delta=0.0,
        training_seed_consistency=True,
    )
    assert not promotion_gate_v1(
        paired_delta=0.1, ci_lower=0.01, fault_rate=0.02, seat_delta=0.0,
        training_seed_consistency=True,
    )
