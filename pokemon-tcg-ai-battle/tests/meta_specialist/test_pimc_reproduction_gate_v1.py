"""正典 §9.4 の PIMC 再現 gate を、既知の対局結果で確かめる。"""

from __future__ import annotations

import math

import pytest

from mage_ptcg.meta_specialist.pimc_reproduction_gate_v1 import (
    FALLBACK_ALGORITHM_ID_V1,
    FORBIDDEN_FALLBACK_LABEL_V1,
    INTERIM_GAMES_V1,
    MAX_GAMES_V1,
    PRACTICAL_MARGIN_V1,
    TOTAL_ONE_SIDED_ALPHA_V1,
    PairedGameV1,
    PimcReproductionGateV1Error,
    assert_pimc_target_usable_v1,
    evaluate_pimc_reproduction_gate_v1,
    fallback_algorithm_id_v1,
    obrien_fleming_alpha_v1,
)


def _block(
    games: int, *, edge: float, clusters: int = 32, jitter: float = 0.0
) -> list[PairedGameV1]:
    """A paired block whose mean difference is ``edge``.

    ``jitter`` moves score mass between clusters so the bootstrap sees genuine
    between-cluster variation rather than a constant.
    """
    rows: list[PairedGameV1] = []
    for index in range(games):
        cluster = index % clusters
        # Cluster-level offset, mean zero across clusters.
        offset = jitter * ((cluster - (clusters - 1) / 2) / max(1.0, (clusters - 1) / 2))
        pimc = min(1.0, max(0.0, 0.5 + edge + offset))
        rows.append(PairedGameV1(
            pair_key=f"pair-{index}", cluster_id=f"c{cluster}",
            pimc_score=pimc, baseline_score=0.5,
        ))
    return rows


def test_a_clear_reproduction_passes_both_gates() -> None:
    decision = evaluate_pimc_reproduction_gate_v1(
        _block(INTERIM_GAMES_V1, edge=0.05, jitter=0.02), schedule_id="sched-1"
    )

    assert decision.primary_gate_passed is True
    assert decision.practical_gate_passed is True
    assert decision.passed is True
    assert decision.status == "passed"
    assert decision.point_estimate == pytest.approx(0.05, abs=1e-9)
    assert decision.lower_bound > 0.0


def test_a_statistically_clear_but_tiny_edge_fails_the_practical_gate() -> None:
    """+0.5pp は「再現」ではない。正典 §9.4 は +3pp を実用性 gate に置く。"""
    decision = evaluate_pimc_reproduction_gate_v1(
        _block(MAX_GAMES_V1, edge=0.005, jitter=0.001), schedule_id="sched-1"
    )

    assert decision.lower_bound > 0.0, "統計的には 0 より上"
    assert decision.practical_gate_passed is False
    assert decision.passed is False
    assert decision.point_estimate < PRACTICAL_MARGIN_V1


def test_a_large_but_uncertain_edge_fails_the_primary_gate() -> None:
    """point estimate が大きくても lower bound が 0 を跨げば再現ではない。"""
    rows: list[PairedGameV1] = []
    # 半分の cluster で大勝、半分で大敗。平均は +4pp だが cluster 間分散が大きい。
    for index in range(INTERIM_GAMES_V1):
        cluster = index % 16
        winning = cluster < 8
        rows.append(PairedGameV1(
            pair_key=f"pair-{index}", cluster_id=f"c{cluster}",
            pimc_score=1.0 if winning else 0.0,
            baseline_score=0.0 if winning else 0.92,
        ))

    decision = evaluate_pimc_reproduction_gate_v1(rows, schedule_id="sched-1")

    assert decision.point_estimate > PRACTICAL_MARGIN_V1
    assert decision.practical_gate_passed is True
    assert decision.primary_gate_passed is False
    assert decision.passed is False


def test_an_inconclusive_first_look_asks_to_extend_within_the_same_family() -> None:
    decision = evaluate_pimc_reproduction_gate_v1(
        _block(INTERIM_GAMES_V1, edge=0.0, jitter=0.02), schedule_id="sched-1"
    )

    assert decision.passed is False
    assert decision.status == "inconclusive_extend"
    assert str(MAX_GAMES_V1) in decision.reason


def test_the_full_schedule_rejects_rather_than_asking_for_more() -> None:
    decision = evaluate_pimc_reproduction_gate_v1(
        _block(MAX_GAMES_V1, edge=0.0, jitter=0.02), schedule_id="sched-1"
    )

    assert decision.status == "rejected"
    assert "not used for training" in decision.reason


def test_alpha_spending_is_far_stricter_at_the_interim_look() -> None:
    interim = obrien_fleming_alpha_v1(INTERIM_GAMES_V1)
    final = obrien_fleming_alpha_v1(MAX_GAMES_V1)

    assert final == pytest.approx(TOTAL_ONE_SIDED_ALPHA_V1)
    assert 0.0 < interim < final / 100, (
        f"interim alpha {interim} は O'Brien-Fleming としては緩すぎる"
    )
    # 単調増加であること。
    assert obrien_fleming_alpha_v1(2048) > interim


def test_the_interim_look_is_stricter_than_the_final_on_the_same_data() -> None:
    """同じ edge でも、1,024 局時点の方が通りにくいこと。"""
    early = evaluate_pimc_reproduction_gate_v1(
        _block(INTERIM_GAMES_V1, edge=0.035, jitter=0.06), schedule_id="s"
    )
    late = evaluate_pimc_reproduction_gate_v1(
        _block(MAX_GAMES_V1, edge=0.035, jitter=0.06), schedule_id="s"
    )

    assert early.alpha_spent < late.alpha_spent
    assert early.lower_bound < late.lower_bound


def test_judging_before_the_first_look_is_refused() -> None:
    with pytest.raises(PimcReproductionGateV1Error, match="first look is defined"):
        evaluate_pimc_reproduction_gate_v1(_block(512, edge=0.1), schedule_id="s")


def test_more_than_the_declared_maximum_is_refused() -> None:
    with pytest.raises(PimcReproductionGateV1Error, match="at most"):
        evaluate_pimc_reproduction_gate_v1(
            _block(MAX_GAMES_V1 + 1, edge=0.1), schedule_id="s"
        )


def test_a_single_cluster_cannot_produce_an_interval() -> None:
    rows = [
        PairedGameV1(pair_key=f"p{index}", cluster_id="only",
                     pimc_score=1.0, baseline_score=0.0)
        for index in range(INTERIM_GAMES_V1)
    ]

    with pytest.raises(PimcReproductionGateV1Error, match="at least two clusters"):
        evaluate_pimc_reproduction_gate_v1(rows, schedule_id="s")


def test_a_repeated_pair_key_is_refused() -> None:
    rows = _block(INTERIM_GAMES_V1, edge=0.05)
    rows[1] = PairedGameV1(
        pair_key=rows[0].pair_key, cluster_id="c1", pimc_score=1.0, baseline_score=0.0
    )

    with pytest.raises(PimcReproductionGateV1Error, match="appears twice"):
        evaluate_pimc_reproduction_gate_v1(rows, schedule_id="s")


def test_a_block_without_a_sealed_schedule_id_is_refused() -> None:
    with pytest.raises(PimcReproductionGateV1Error, match="sealed schedule"):
        evaluate_pimc_reproduction_gate_v1(
            _block(INTERIM_GAMES_V1, edge=0.05), schedule_id=""
        )


def test_an_unpassed_gate_blocks_the_target_from_training() -> None:
    decision = evaluate_pimc_reproduction_gate_v1(
        _block(INTERIM_GAMES_V1, edge=0.0, jitter=0.02), schedule_id="s"
    )

    with pytest.raises(PimcReproductionGateV1Error, match="not usable for training"):
        assert_pimc_target_usable_v1(decision)


def test_an_inconclusive_result_is_not_treated_as_a_pass() -> None:
    decision = evaluate_pimc_reproduction_gate_v1(
        _block(INTERIM_GAMES_V1, edge=0.0, jitter=0.02), schedule_id="s"
    )

    assert decision.status == "inconclusive_extend"
    with pytest.raises(PimcReproductionGateV1Error):
        assert_pimc_target_usable_v1(decision)


def test_a_rejected_pimc_falls_back_to_rule_bc_vtrace_not_exit_vtrace() -> None:
    """正典 §9.4: 不採用の PIMC を `exit_vtrace` と偽って扱わない。"""
    decision = evaluate_pimc_reproduction_gate_v1(
        _block(MAX_GAMES_V1, edge=0.0, jitter=0.02), schedule_id="s"
    )

    label = fallback_algorithm_id_v1(decision)

    assert label == FALLBACK_ALGORITHM_ID_V1 == "rule_bc_vtrace"
    assert label != FORBIDDEN_FALLBACK_LABEL_V1


def test_a_passed_gate_has_no_fallback_to_name() -> None:
    decision = evaluate_pimc_reproduction_gate_v1(
        _block(INTERIM_GAMES_V1, edge=0.05, jitter=0.02), schedule_id="s"
    )

    with pytest.raises(PimcReproductionGateV1Error, match="no fallback"):
        fallback_algorithm_id_v1(decision)


def test_the_decision_is_reproducible_and_serialisable() -> None:
    rows = _block(INTERIM_GAMES_V1, edge=0.05, jitter=0.02)

    first = evaluate_pimc_reproduction_gate_v1(rows, schedule_id="s")
    second = evaluate_pimc_reproduction_gate_v1(rows, schedule_id="s")

    assert first.to_dict() == second.to_dict()
    assert math.isfinite(first.to_dict()["lower_bound"])
