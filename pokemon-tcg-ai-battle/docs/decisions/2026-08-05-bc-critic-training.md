# θ0 で critic (value head) を学習させる

- 日付: 2026-08-05
- 対象: `scripts/run_bc_distillation.py`、`neural_learner_v1`、`neural_adapter_v1`、`neural_batch_v1`
- 状態: 実装済み・実測確認済み

## 決定

BC 蒸留に value 損失を追加し、既定 (`--value-coefficient 0.5`) で value head を
学習する。θ0 が較正済みの critic を持った状態で RL へ渡るようにする。

## 経緯: 私のレビューは 2 回誤った

正確な記録として残す。

1. 旧 docstring は「codebase に value head は存在しない」と書いていた。**誤り。**
   `neural_model_v1.SpecialistPolicyModelV1` に `value_head` が実在する。
2. 私の 1 回目の訂正は「value head は学習も参照もされない」「走っているのは
   V-trace ではない」と書いた。**これも誤りで、より有害だった。**
   `train_from_trajectories_v1` は `evaluate_trajectory_loss_v1` へ
   `state_value` を渡しており、その先は `trajectory_target_v1.value` →
   `model.state_value_from_state` → value head である。RL では critic は生きており、
   value 損失に勾配があり、policy gradient に baseline がある。

   誤診の原因は、`value_head` という literal だけを grep し、
   `state_value_from_state` 経由の参照を見落としたこと。**名前の一致で存在を判定し、
   呼び出し経路を辿らなかった。**

## 実際の欠落（これは本物だった）

**BC が value head を学習していなかった。** その結果 θ0 は乱数初期化の critic を
RL へ渡し、V-trace は方策改善と同時に baseline を一から学ぶことになる。学び終える
までの間 baseline は雑音であり、これは
`docs/evidence/vtrace-degenerate-collapse-20260804.md` が記録している崩壊レジームと
同じ条件である。既定の entropy 0.01 はその対症療法だった。

## 実装

| 変更 | 内容 |
|---|---|
| `neural_adapter_v1.make_specialist_state_values_v1` | model を `examples -> V(x)` の callable に束ねる。`make_specialist_row_logits_v1` の value 版 |
| `neural_batch_v1.weighted_value_loss_v1` | quality weight 付き二乗誤差を非正規化で返す。policy 項と同じ母集団で平均されるよう、学習対象外は除外せず重み 0 とする |
| `neural_learner_v1.training_step_v1` | `state_values` / `value_coefficient` を追加。**両方とも既定は off** なので既存呼び出しの挙動は不変。`value_coefficient > 0` なのに `state_values` が無い場合は拒否する（勾配の流れない項を損失として報告させない） |
| `run_bc_distillation.py` | `--value-coefficient` (既定 0.5) を追加し、`value_loss` を進捗と履歴へ出す |

新規のデータ収集は不要。snapshot は既に全 example に `value_target` を持つ。

## 実測

12 step の smoke（archaludon snapshot、10,343 train examples）:

```
value_loss: 0.3513 → 0.3486 → 0.2558 → 0.2386 → 0.1596 → ... → 0.1226 / 0.2215
value_head 出力層 bias = 0.0522   (乱数初期化なら厳密に 0)
```

critic が実際に学習されていることを、損失の低下と重みの移動の両方で確認した。

## 割引の整合（重要な拘束）

`value_target` は**割引なし**の終局結果 ±1 である。collection の既定
`--non-terminal-discount` は 1.0 なので、**既定では整合している。**

割引を 1.0 未満へ下げる場合、V-trace の目標は割引後 return になる一方、BC で当てた
critic は割引なし return を予測したままになり、baseline が系統的にずれる。両方を
同時に変更すること。`test_value_head_gap_v1` がこの対応を固定している。

## 再発防止

`tests/meta_specialist/test_value_head_gap_v1.py`（8 件）:

- RL が `state_value` を V-trace へ渡し続けること（1 回目の誤診の再発防止）
- BC が既定で critic を学習すること（`--value-coefficient` 既定 0.5）
- `value_coefficient > 0` かつ `state_values` 無しが拒否されること
- collection の割引既定と `value_target` の割引が揃っていること
