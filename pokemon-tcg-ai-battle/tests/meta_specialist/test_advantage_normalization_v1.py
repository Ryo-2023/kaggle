"""advantage の正規化が、意図した量だけを変えることを確かめる。

この機能を入れた理由は `docs/evidence/vtrace-no-progress-20260807.md` にある:
報酬が終端のみ (実測 1.7% の transition) で勝率が低いと advantage の平均が負へ寄り、
`-(advantage * log_pi)` が「収集した行動すべてを一律に下げる」成分を持つ。方策は
良い手を選ぶかわりに確率質量を散らし、エントロピーだけが上がる。

ここで固定する契約:

1. `none` は従来の更新をビット単位で再現する (既存 run との比較可能性)。
2. 記録される moment は**正規化前**の生の advantage である。正規化後を記録すると
   次 step の shift/scale が二重に効く。
3. `center` は平均を引き、`standardize` はさらに標準偏差で割る。
4. 分散がほぼ 0 の minibatch では、下限で割って発散を防ぐ。
"""

from __future__ import annotations

import math

import pytest
import torch

from mage_ptcg.meta_specialist.train_from_trajectories_v1 import (
    ADVANTAGE_NORMALIZATION_MODES_V1,
    AdvantageNormalizationV1,
    TrainFromTrajectoriesV1Error,
)
from mage_ptcg.meta_specialist.vtrace_bridge_v1 import (
    VTraceBridgeV1Error,
    VTraceLossV1,
    accumulate_trajectory_losses_v1,
    evaluate_trajectory_loss_v1,
)


def _transitions_v1(rewards: list[float]) -> list[dict]:
    """終端報酬だけを持つ 1 本の軌跡。実データと同じ形にする。"""
    steps = len(rewards)
    return [
        {
            "behavior_log_probability": -0.2,
            "reward": rewards[index],
            # 終端だけ discount 0.0。bridge がこの対応を検証する。
            "terminal": index == steps - 1,
            "discount": 0.0 if index == steps - 1 else 1.0,
            "value": 0.0,
            "chosen_semantic_complete_action": [{"index": 0}],
        }
        for index in range(steps)
    ]


def _loss_v1(transitions, *, shift: float = 0.0, scale: float = 1.0) -> VTraceLossV1:
    return evaluate_trajectory_loss_v1(
        transitions,
        target_log_probability=lambda _t: torch.tensor(-0.3, dtype=torch.float64),
        bootstrap_value=0.0, rho_bar=1.0, c_bar=1.0,
        state_value=lambda _t: torch.tensor(0.1, dtype=torch.float64),
        advantage_shift=shift, advantage_scale=scale,
    )


def test_identity_normalization_reproduces_the_previous_update_v1() -> None:
    """既定 (shift=0, scale=1) は正規化を入れる前と同じ policy loss を出す。"""
    transitions = _transitions_v1([0.0, 0.0, -1.0])
    baseline = _loss_v1(transitions)
    explicit = _loss_v1(transitions, shift=0.0, scale=1.0)
    assert float(baseline.policy_loss_sum) == float(explicit.policy_loss_sum)


def test_recorded_moments_are_the_raw_advantage_not_the_normalized_one_v1() -> None:
    """shift/scale を変えても記録される moment は動かない。

    ここが逆になっていると、次 step が「すでに中心化された値」からさらに平均を引き、
    正規化が step ごとに二重にかかって update が縮み続ける。
    """
    transitions = _transitions_v1([0.0, 0.0, -1.0])
    raw = _loss_v1(transitions)
    shifted = _loss_v1(transitions, shift=-0.5, scale=2.0)
    assert float(shifted.advantage_sum) == pytest.approx(float(raw.advantage_sum))
    assert float(shifted.advantage_square_sum) == pytest.approx(
        float(raw.advantage_square_sum)
    )
    # policy loss のほうは当然変わる。
    assert float(shifted.policy_loss_sum) != pytest.approx(float(raw.policy_loss_sum))


def test_centering_subtracts_the_mean_and_standardizing_also_divides_v1() -> None:
    transitions = _transitions_v1([0.0, 0.0, -1.0])
    raw = _loss_v1(transitions)
    weight = float(raw.weight_sum)
    mean = float(raw.advantage_sum) / weight
    variance = float(raw.advantage_square_sum) / weight - mean * mean
    assert variance > 0.0, "この fixture は分散を持つ必要がある"

    centered = AdvantageNormalizationV1.for_mode_v1("center").advanced_v1(raw)
    assert centered.shift == pytest.approx(mean)
    assert centered.scale == 1.0

    standardized = AdvantageNormalizationV1.for_mode_v1("standardize").advanced_v1(raw)
    assert standardized.shift == pytest.approx(mean)
    assert standardized.scale == pytest.approx(math.sqrt(variance))


def test_none_mode_never_moves_off_the_identity_v1() -> None:
    """`none` は moment を読んでも shift/scale を変えない。"""
    raw = _loss_v1(_transitions_v1([0.0, -1.0]))
    stayed = AdvantageNormalizationV1.for_mode_v1("none").advanced_v1(raw)
    assert (stayed.shift, stayed.scale) == (0.0, 1.0)


def test_degenerate_spread_uses_the_floor_instead_of_exploding_v1() -> None:
    """分散 0 の minibatch で割っても発散しない。

    順位情報を持たない batch なので、そこで increase する理由はない。
    """
    zero_spread = VTraceLossV1(
        policy_loss_sum=torch.zeros((), dtype=torch.float64),
        value_loss_sum=torch.zeros((), dtype=torch.float64),
        entropy_sum=torch.zeros((), dtype=torch.float64),
        weight_sum=torch.tensor(4.0, dtype=torch.float64),
        steps=4,
        advantage_sum=torch.tensor(2.0, dtype=torch.float64),
        advantage_square_sum=torch.tensor(1.0, dtype=torch.float64),  # 分散 0
    )
    result = AdvantageNormalizationV1.for_mode_v1("standardize").advanced_v1(zero_spread)
    assert result.scale >= 1.0e-3
    assert math.isfinite(result.scale)


def test_moments_accumulate_across_microbatches_v1() -> None:
    """microbatch に割っても moment の合計が一致する。

    合計が壊れると、microbatch 数を変えただけで正規化の強さが変わってしまう。
    """
    a = _loss_v1(_transitions_v1([0.0, 0.0, -1.0]))
    b = _loss_v1(_transitions_v1([0.0, 1.0]))
    merged = accumulate_trajectory_losses_v1([a, b])
    assert float(merged.advantage_sum) == pytest.approx(
        float(a.advantage_sum) + float(b.advantage_sum)
    )
    assert float(merged.weight_sum) == float(a.weight_sum) + float(b.weight_sum)


def test_partial_moments_are_dropped_rather_than_summed_v1() -> None:
    """一部だけ moment を持つ集約は None にする。

    部分和で次 step の shift/scale を決めると、欠けた分だけ偏った正規化になる。
    """
    complete = _loss_v1(_transitions_v1([0.0, -1.0]))
    without = VTraceLossV1(
        policy_loss_sum=torch.zeros((), dtype=torch.float64),
        value_loss_sum=torch.zeros((), dtype=torch.float64),
        entropy_sum=torch.zeros((), dtype=torch.float64),
        weight_sum=torch.tensor(1.0, dtype=torch.float64),
        steps=1,
    )
    merged = accumulate_trajectory_losses_v1([complete, without])
    assert merged.advantage_sum is None
    # moment が無ければ、正規化は前の値のまま据え置く。
    previous = AdvantageNormalizationV1(mode="standardize", shift=0.4, scale=2.0)
    assert previous.advanced_v1(merged) == previous


@pytest.mark.parametrize("scale", [0.0, -1.0, float("nan"), float("inf")])
def test_nonpositive_or_nonfinite_scale_is_refused_v1(scale: float) -> None:
    with pytest.raises(VTraceBridgeV1Error):
        _loss_v1(_transitions_v1([0.0, -1.0]), scale=scale)


def test_nonfinite_shift_is_refused_v1() -> None:
    with pytest.raises(VTraceBridgeV1Error):
        _loss_v1(_transitions_v1([0.0, -1.0]), shift=float("nan"))


def test_unknown_mode_is_refused_v1() -> None:
    with pytest.raises(TrainFromTrajectoriesV1Error):
        AdvantageNormalizationV1.for_mode_v1("zscore")


def test_declared_modes_are_the_ones_that_work_v1() -> None:
    for mode in ADVANTAGE_NORMALIZATION_MODES_V1:
        assert AdvantageNormalizationV1.for_mode_v1(mode).mode == mode
