"""critic (value head) が BC でも RL でも実際に学習されることを固定する。

## 経緯（自分の誤診の記録）

このファイルの初版は「value head は存在するが一度も学習・参照されない」「したがって
走っているのは V-trace ではない」と主張していた。**これは誤りだった。**
`train_from_trajectories_v1` は `state_value` を `evaluate_trajectory_loss_v1` へ
渡しており、その先は `trajectory_target_v1.value` →
`model.state_value_from_state` → value head である。`value_head` という literal を
grep しただけで `state_value_from_state` 経由の参照を見落とした。

実際の欠落はもっと狭かった: **BC が value head を学習していなかった**ため、
θ0 が乱数初期化の critic を RL へ渡していた。それは実装済みで、本ファイルは
その状態を固定する。
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import torch

from mage_ptcg.meta_specialist import train_from_trajectories_v1
from mage_ptcg.meta_specialist.neural_adapter_v1 import make_specialist_state_values_v1
from mage_ptcg.meta_specialist.neural_learner_v1 import NeuralLearnerV1Error, training_step_v1
from mage_ptcg.meta_specialist.neural_model_v1 import (
    SpecialistModelConfigV1,
    build_specialist_policy_model_v1,
)


_SRC = Path(train_from_trajectories_v1.__file__).parent


def _model():
    return build_specialist_policy_model_v1(
        SpecialistModelConfigV1(card_vocabulary_size=64), seed=0
    )


def test_the_value_head_exists_in_the_model() -> None:
    assert [name for name, _ in _model().named_parameters() if "value_head" in name]


def test_rl_feeds_vtrace_the_learners_own_value_not_the_stored_placeholder() -> None:
    """RL 側で critic が生きていること（初版の誤診の再発防止）。"""
    source = (_SRC / "train_from_trajectories_v1.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    passes_state_value = [
        keyword.arg for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        for keyword in node.keywords
        if keyword.arg == "state_value"
    ]
    assert passes_state_value, (
        "V-trace へ state_value を渡さなくなった。渡さないと保存済みの定数 0.0 が "
        "使われ、policy gradient の baseline が消える"
    )

    target_source = (_SRC / "trajectory_target_v1.py").read_text(encoding="utf-8")
    assert "state_value_from_state" in target_source, (
        "scorer が value head を読まなくなった"
    )


def test_bc_trains_the_critic_by_default() -> None:
    """θ0 が乱数初期化の critic を RL へ渡さないこと。"""
    runner = (Path(_SRC).parents[2] / "scripts" / "run_bc_distillation.py")
    source = runner.read_text(encoding="utf-8")

    assert "--value-coefficient" in source
    assert "make_specialist_state_values_v1" in source
    assert "value_coefficient=args.value_coefficient" in source
    # 既定で有効であること（0 を既定にすると欠落が復活する）。
    assert 'default=0.5' in source


def test_a_positive_value_coefficient_without_state_values_is_refused() -> None:
    """value 項を報告しながら勾配が流れない、という状態を作らせない。"""
    model = _model()
    optimizer = torch.optim.SGD(model.parameters(), lr=0.0)

    try:
        training_step_v1(
            [], model=model, optimizer=optimizer,
            row_logits=lambda examples: torch.zeros((0, 1)),
            state_values=None, value_coefficient=0.5,
        )
    except NeuralLearnerV1Error as exc:
        assert "state_values" in str(exc)
    else:  # pragma: no cover - the guard must fire
        raise AssertionError("positive value_coefficient without state_values was accepted")


def test_the_state_values_adapter_returns_one_scalar_per_example() -> None:
    model = _model()
    values = make_specialist_state_values_v1(model)

    assert callable(values)
    assert values([]).shape == (0,)


def test_value_targets_and_the_collection_discount_must_stay_aligned() -> None:
    """BC の value_target は割引なし ±1。collection の割引も 1.0 でなければずれる。

    割引を下げると V-trace の目標は割引後 return になるが、BC で当てた critic は
    割引なし return を予測したままになり、baseline が系統的にずれる。既定が揃って
    いることを固定し、変えるときは両方同時に変える必要があることを示す。
    """
    cli_source = (_SRC / "cli.py").read_text(encoding="utf-8")

    assert '"--non-terminal-discount", type=float, default=1.0' in cli_source, (
        "collection の割引既定が変わった。BC の value_target は割引なしなので、"
        "critic を割引後 return に合わせて作り直す必要がある"
    )


def test_the_module_docstring_no_longer_claims_the_critic_is_dead() -> None:
    docstring = inspect.getdoc(train_from_trajectories_v1) or ""

    assert "BC does not train the value" in docstring or "critic exists and is live" in docstring
    assert "wired and on by default at 0.01" in docstring


def test_the_bootstrap_value_is_a_declared_constant() -> None:
    assert train_from_trajectories_v1._BOOTSTRAP_VALUE_V1 == 0.0
