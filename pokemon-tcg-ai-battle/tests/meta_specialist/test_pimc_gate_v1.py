"""Tests for §11 PIMC Reproducibility Gate and Distillation Probe."""

import pytest
from mage_ptcg.meta_specialist.pimc_gate_v1 import (
    ActionLogitPairV1,
    PIMCGateV1Error,
    evaluate_pimc_reproducibility_v1,
)


def test_evaluate_pimc_reproducibility_pass():
    pairs = [
        ActionLogitPairV1("act1", 0.5, 0.48),
        ActionLogitPairV1("act2", 0.3, 0.31),
        ActionLogitPairV1("act3", 0.2, 0.21),
    ]
    res = evaluate_pimc_reproducibility_v1(pairs, max_kl_threshold=0.10)
    assert res.passed
    assert res.kl_divergence >= 0.0
    assert res.max_prob_delta == pytest.approx(0.02)


def test_evaluate_pimc_reproducibility_fails_high_kl():
    pairs = [
        ActionLogitPairV1("act1", 0.9, 0.1),
        ActionLogitPairV1("act2", 0.1, 0.9),
    ]
    res = evaluate_pimc_reproducibility_v1(pairs, max_kl_threshold=0.05)
    assert not res.passed


def test_invalid_probability_raises():
    with pytest.raises(PIMCGateV1Error):
        ActionLogitPairV1("act1", 1.5, 0.5)


def test_empty_pairs_raises():
    with pytest.raises(PIMCGateV1Error):
        evaluate_pimc_reproducibility_v1([])
