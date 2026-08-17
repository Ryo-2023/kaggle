"""Tests for sequential evaluation, robust statistics, sensitivity, and stratified paradox detection."""

from __future__ import annotations
import pytest
from mage_ptcg.offline_training_v1_support.sequential_evaluation import run_sprt_check
from mage_ptcg.offline_training_v1_support.robust_statistics import (
    compute_trimmed_mean,
    compute_median_absolute_deviation,
    exact_binomial_test,
    holm_bonferroni_correction
)
from mage_ptcg.offline_training_v1_support.sensitivity import analyze_winrate_sensitivity
from mage_ptcg.offline_training_v1_support.stratified_analysis import detect_simpsons_paradox

def test_sequential_evaluation_sprt():
    # p0 = 0.5 vs p1 = 0.6
    # H0 rejected (evidence for alternative) if wins are significantly high
    res_alt = run_sprt_check(wins=45, losses=5, p0=0.5, p1=0.6, min_games=10)
    assert res_alt["status"] == "EVIDENCE_FOR_ALTERNATIVE"

    # H0 supported (evidence for null) if wins are low
    res_null = run_sprt_check(wins=5, losses=45, p0=0.5, p1=0.6, min_games=10)
    assert res_null["status"] == "EVIDENCE_FOR_NULL"

def test_robust_statistics():
    vals = [1.0, 2.0, 3.0, 4.0, 5.0, 100.0] # 100.0 is an outlier
    # Trimmed mean removes 10% highest/lowest
    trim_mean = compute_trimmed_mean(vals, trim_ratio=0.2)
    assert trim_mean == 3.5  # middle is [2.0, 3.0, 4.0, 5.0] -> avg = 3.5

    # MAD of [1, 2, 3, 4, 5] -> median is 3.
    # deviations from 3: [2, 1, 0, 1, 2] -> sorted deviations: [0, 1, 1, 2, 2] -> median deviation is 1
    mad = compute_median_absolute_deviation([1.0, 2.0, 3.0, 4.0, 5.0])
    assert mad == 1.0

    # Binomial p-value
    p_val = exact_binomial_test(wins=9, total=10, p0=0.5)
    # two-sided p-value for 9/10: P(9) + P(10) + P(0) + P(1) = (10+1+1+10)/1024 = 22/1024 = 0.02148
    assert abs(p_val - 0.02148) < 1e-4

    # Holm correction
    p_vals = [0.01, 0.04, 0.03]
    adjs = holm_bonferroni_correction(p_vals)
    # Sorted: 0.01 (n=3, adj=0.03), 0.03 (n=2, adj=0.06), 0.04 (n=1, adj=0.04 -> monotonic force to 0.06)
    assert adjs[0] == 0.03
    assert adjs[2] == 0.06

def test_sensitivity_analysis():
    games = [
        {"game_id": f"g-{i}", "winner": "candidate", "candidate_seat": 0} for i in range(10)
    ]
    res = analyze_winrate_sensitivity(games)
    assert res["baseline_win_rate"] == 1.0
    assert "conf_0.95" in res["confidence_variance"]

def test_stratified_simpsons_paradox():
    # Simpson's Paradox scenario:
    # Stratum A (seat 0): cand wins 1/10 (10%)
    # Stratum B (seat 1): cand wins 4/10 (40%)
    # Both strata are < 50%
    # Overall candidate wins: 5/20 (25%) -> No paradox (overall also < 50%)
    games_no = (
        [{"game_id": f"ga-{i}", "candidate_seat": 0, "winner": "candidate" if i == 0 else "opponent"} for i in range(10)] +
        [{"game_id": f"gb-{i}", "candidate_seat": 1, "winner": "candidate" if i < 4 else "opponent"} for i in range(10)]
    )
    res_no = detect_simpsons_paradox(games_no)
    assert res_no["paradox_detected"] is False

    # Paradox scenario:
    # Seat 0: cand wins 9/10 (90%)
    # Seat 1: cand wins 9/10 (90%)
    # Both strata are >= 50%
    # Overall win rate >= 50% (no paradox)
    games_yes = (
        [{"game_id": f"ga-{i}", "candidate_seat": 0, "winner": "candidate" if i < 9 else "opponent"} for i in range(10)] +
        [{"game_id": f"gb-{i}", "candidate_seat": 1, "winner": "candidate" if i < 9 else "opponent"} for i in range(10)]
    )
    res_yes = detect_simpsons_paradox(games_yes)
    assert res_yes["paradox_detected"] is False
