# V4 frozen Wave6 residual sidecar preflight — 2026-08-12

## 判定

ChatGPT Pro レビューが提案した `frozen Wave6 + zero-init bounded residual` の最小契約を、研究専用 sidecar として実装し、focused test で監査した。今回の作業は**学習、CABT 評価、長時間実行、production V4 への接続を行っていない**。したがって、残差の実戦性能、学習安定性、または promotion 可否を示す証拠ではない。

実装は次の2ファイルだけで閉じている。

- [sidecar module](/home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle/src/mage_ptcg/meta_specialist/frozen_residual_v1.py)
- [focused contract tests](/home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle/tests/meta_specialist/test_frozen_residual_v1.py)

既存の `neural_model_v4.py`、`neural_policy_v4.py`、`actor_pool_v1.py`、trainer、CABT runtime の production 実装は変更していない。

## 監査した契約

### Base logits は frozen

`FrozenResidualSidecarV1.adjust_logits()` は入力 base semantic/STOP tensor を `detach()` してから残差を加える。損失関数 `frozen_residual_loss_v1()` も base logits を detach して `base + residual` を作るため、optimizer が Wave6 backbone/GRU/head へ勾配を返す経路はない。sidecar 自体は base model を所有せず、将来 runner が既存 Wave6 factory を外側から渡す設計である。

### Zero-init と bounded magnitude

残差 MLP の最終 `Linear` の weight/bias を construction 時に全ゼロへ初期化する。初期時点では `alpha=0` で、最終出力は `max_abs_residual * tanh(raw)` により `[-max_abs_residual, +max_abs_residual]` に閉じる。`max_abs_residual` は `(0, 1]` に限定し、pilot では例えば `0.25` を manifest へ事前固定できる。

### Anchor KL と residual L2

`frozen_residual_loss_v1(base, residual, target)` は次を返す。

```text
adjusted = detach(base) + residual
imitation = CrossEntropy(adjusted, target)
anchor_kl = KL(softmax(detach(base)) || softmax(adjusted))
residual_l2 = mean(residual ** 2)
total = imitation + kl_weight * anchor_kl + l2_weight * residual_l2
```

実装上の `F.kl_div(log_softmax(adjusted), softmax(base), reduction="batchmean")` は上記向きの anchor である。target domain は semantic classes + legal STOP を含む単一の padded でない domain を想定し、variable-domain batching は将来 runner の責務とする。

### OOD / malformed は fail-closed

sidecar は、学習 manifest から渡された `known_context_ids` と `known_action_keys` の両方に一致する場合だけ対象行へ残差を出す。未知 context なら semantic/STOP 全行がゼロ、既知 context でも未知 action key は当該行だけゼロになる。`None`、型違い、feature width 不一致、STOP availability と base STOP tensor の矛盾などは、`adjust_logits()` で base logits の detached exact pass-through へ戻る。したがって malformed/OOD は意図せず residual を適用しない。

context は actor-visible V1 の serial-free `SpecialistModelInputV1` と `SpecialistStepInputV1` から作り、state scalars、semantic canonical bytes、selection prefix だけを使う。`opponent_id`、seat、policy identity、physical serial、local action ID、private field は追加しない。action key は semantic canonical bytes の SHA-256、context ID は canonical model input + step input の SHA-256 である。

### Semantic / STOP / GRU commit

`FrozenResidualPolicyV1` は既存 policy の外側に置く research adapter であり、次を sidecar が行わないことをテストした。

- semantic class の個数を変えない
- STOP が不合法な prefix に STOP logit を生成しない
- semantic decoder、alias dispatcher、legality を再実装しない
- GRU hidden state を sidecar が保持・更新しない
- complete semantic action の `commit(outcome)` を base session へ一度だけ委譲する

従って future runner では、`base_session.logits(...) → sidecar.adjust_step(...) → 既存 CABT decoder → base_session.commit(...)` の順序だけを追加し、GRU の token は Wave6 base session が従来どおり commit 後に更新する。

## 既存 Wave6 への接続点（未接続）

現在の Archaludon Wave6 closed checkpoint は次の2つである。以下は既存 `run-manifest.json` の provenance であり、今回 sidecar へロードしていない。

| training seed | checkpoint | file SHA-256 | tensor-state SHA-256 |
|---|---|---|---|
| 0 | `runs/meta-specialist-v4-archaludon-longrun-wave6-current/archaludon-training-checkpoints/seed-0/best-recurrent-bc-v4.pt` | `9eb22970fb9917d3632f415e19b943c752e60e31595efe5419960d9c27e6c8de` | `36b6ace02e69849ea476565bd0ef283d206a6786ba2ac0c0040e82d263f3292a` |
| 1 | `runs/meta-specialist-v4-archaludon-longrun-wave6-current/archaludon-training-checkpoints/seed-1/best-recurrent-bc-v4.pt` | `5d137fd6e6b76b993d1d7dcc4d975bcf9c43358c9e43510a8db0c9c6181dddf6` | `046968c1a295af0ae84594fedb8503a33b6f8a5dd33314e8fc4fe4ae09985e8a` |

`FrozenResidualSidecarV1.descriptor()` はこの file/tensor SHA を**ペアで**保存できる。片方だけを指定すると constructor が拒否する。sidecar manifest には少なくとも次を固定する必要がある。

1. base checkpoint file/tensor SHA と closed V4 implementation/evaluator SHA
2. subject deck SHA、opponent pool/protocol SHA、training seed
3. `known_context_ids` の source JSONL SHA と partition
4. `known_action_keys` の source/action-schema SHA
5. state/action feature width、`max_abs_residual`、KL/L2 weights
6. `promotion_authority=false`、`longrun_allowed=false`（pilot 完了まで）

### 必要な data / manifest

実際に学習する前に、Wave6 seed0 と seed1 を混ぜずに、対応する on-policy train material から次を作る。

- seed0/seed1 各々の public `model_input + step_input` から context ID 集合
- semantic action canonical bytes から action key 集合
- target index と complete-action grouping（prefix 内の GRU context は通すが、residual loss の effective row は明示）
- base checkpoint identity、data source SHA、deck/opponent/protocol SHA
- fixed residual max、anchor KL、L2、optimizer budget、seed

既知集合は勝率を見て後付け変更してはならない。unknown context/action はゼロ残差で評価し、known coverage と zero-pass-through rate を ledger へ記録する。

## 実行結果

以下を実行し、5 tests が pass した。

```bash
PYTHONDONTWRITEBYTECODE=1 TMPDIR=.tmp-test PYTHONPATH=.:src \
  .venv/bin/pytest -q -s tests/meta_specialist/test_frozen_residual_v1.py
```

結果: `5 passed`（約1.6秒）。確認内容は zero-init、bound、OOD/malformed zero pass-through、base no-grad、anchor KL/L2、semantic/STOP arity、base GRU commit、bad topology/weight rejection である。

今回未実施:

- sidecar 学習
- existing Wave6 checkpoint load/evaluator 接続
- fixed-six / shadow-C CABT
- recurrence/reset ablation
- longrun、Champion変更、Kaggle提出

## 次の安全な接続順序と時間見積もり

1. 研究専用 trainer が `frozen_residual_loss_v1` の effective denominator と known/OOD mask を unit test（0.5〜1日）。
2. Wave6 seed0/seed1 対応 context manifest を固定し、sidecar-only の tiny overfit（各 seed、数十 update、0.5日）。
3. 同じ base checkpoint の 24 局 smoke と 96 局 noise-aware block、seed/seat/fault/action metrics を記録（半日〜1日）。
4. noise floor を超える bounded pilot が出た場合のみ shadow-C で external comparison（1日以上）。

最短でも学習系接続から 1〜3日程度であり、現時点で長時間学習を開始できる状態ではない。sidecar の module/test は「接続して壊れにくい最小契約」の完成であって、「性能改善候補」の完成ではない。

## 残リスク

- hash-bound context/action coverage が狭いと、大半が zero residual となり学習効果が出ない。
- coverage を広げると public state distribution への過適応が起こり得る。shadow-C は sidecar の context/action source を見た後に untouched ではなくなるため、新しい external pool を先に freeze する。
- additive logit の小さな差でも recurrent trajectory を変える可能性がある。same-checkpoint noise（seed0 SD 約2.62pt、seed1 SD 約7.51pt）を上回る外部証拠が必要である。
- semantic canonical byte は legal/decoder identity と整合するが、ordered/soft-action mass、teacher correctness、outcome value を保証しない。
- CPU/GPU dtype/device、variable legal domain padding、complete-action grouping、checkpoint descriptor loader との接続は未実装である。

