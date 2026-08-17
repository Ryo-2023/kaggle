# Frozen Wave6 residual tiny integration — 2026-08-12

## 判定

研究専用の sidecar trainer と bounded tiny runner の接続契約を閉じ、実データの Wave6 seed 0/1 について各 64 prefix、固定 1 optimizer update を CPU 上で実行した。これは `SELF_IMITATION_INTEGRATION_ONLY` であり、性能証拠ではない。Rule teacher の relabel target が sidecar optimizer へ届くこと、base checkpoint が hash-bound のまま frozen であること、context-only row が denominator から除外されることを確認するためだけの integration probe である。

今回の変更範囲は新規 research module/script/test/evidence に限定した。既存の V4 production model、`recurrent_bc_v4`、`actor_pool_v1`、production policy、CABT evaluator、fixed-six runner、longrun runner は編集・起動していない。Kaggle 提出、Champion変更、promotion authority の付与は行っていない。

## 実行 identity

| 項目 | 値 |
|---|---|
| preflight manifest | `runs/meta-specialist-frozen-residual-preflight-20260812/known-context-action-manifest-v1.json` |
| preflight manifest SHA-256 | `7b79b57436c3d1029abf35e9895045b4f546ff1aea0db706c948c4f4aaec6689` |
| target kind | `self_imitation_rule_relabel_v1` |
| target manifest SHA | seed 0/1 対応 sealed transition JSONL SHA（下表） |
| evidence class | `SELF_IMITATION_INTEGRATION_ONLY` |
| performance evidence | `false` |
| device | `cpu` |
| max prefixes | 64（各 seed、選択した最初の sealed game 内） |
| max optimizer updates | 1 |
| learning rate | 0.01 |
| max residual | 0.25 |
| CABT / fixed-six / longrun | 起動していない |

## seed 別結果

| seed | base file SHA-256 | base tensor-state SHA-256 | target/transition SHA-256 | total rows | context-only | loss-bearing / denominator | effective mass | sidecar file SHA-256 | sidecar tensor SHA-256 |
|---:|---|---|---|---:|---:|---:|---:|---|---|
| 0 | `9eb22970fb9917d3632f415e19b943c752e60e31595efe5419960d9c27e6c8de` | `36b6ace02e69849ea476565bd0ef283d206a6786ba2ac0c0040e82d263f3292a` | `2d9892855350ac99a085eb616489e65e995415e987ce7c2470e20cc27e08b0ce` | 63 | 32 | 31 / 31 | 31.0 | `616d85d8779c8d45ffb527412626cf506e7604da6aed743ce78963e8ec6396db` | `1071699f2ba06cadb3547ba1e2cce38f8ee517cb622261bf3c41850b7a290af7` |
| 1 | `5d137fd6e6b76b993d1d7dcc4d975bcf9c43358c9e43510a8db0c9c6181dddf6` | `046968c1a295af0ae84594fedb8503a33b6f8a5dd33314e8fc4fe4ae09985e8a` | `2e5438aec5e451d70c37593971b45965cd33950822423b1540fdaf56b3f27e26` | 50 | 30 | 20 / 20 | 20.0 | `7a41c4a439cd3802208d78ae3722d34d115feadc42a8e5b9fb28137504c81595` | `f7de0e162a5068bce4d1e983bbda103ac9788bbcb7f1111199ac898a1bbfa569` |

各 report (`tiny-seed0/seed-0-tiny-report.json`、`tiny-seed1/seed-1-tiny-report.json`) に、同じ `evidence_class`、`performance_evidence=false`、`target_kind`、target manifest SHA、base file/tensor SHA、`base_checkpoint_sha256_unchanged=true`、`sidecar_base_checkpoint_binding_verified=true` を保存した。checkpoint descriptor も target kind/target manifest SHA を必須フィールドとして保存し、`training_permitted=false`、`promotion_authority=false`、`longrun_allowed=false` を維持している。

## 何を実行したか

runner は次の順序で fail-closed に動く。

1. preflight manifest を expected SHA と `verify_files=True` でロードする。
2. requested seed の sealed transition JSONL を SHA 照合し、train partition だけを読む。
3. 最初の一つの game から最大 64 prefix を切り出し、Rule teacher の relabel を研究専用 `RecurrentBCSequenceV4` へ変換する。
4. singleton/forced domain は `supervision_weight=0` の context-only row として残す。
5. 対応する Wave6 closed checkpoint を file/tensor SHA 付きで strict load し、全 base parameter を `requires_grad=False`、`eval()`、forward `torch.no_grad()` にする。
6. sidecar だけを SGD の optimizer に渡し、GRU hidden は record group 間で forward context として運ぶ。context-only row は loss denominator に入れない。
7. sidecar checkpoint と report を新規 output directory へ保存し、base file SHA を run 後に再計算して不変性を確認する。

実行コマンド（seed 0/1 は別 output directory）:

```bash
PYTHONPATH=.:src PYTHONDONTWRITEBYTECODE=1 .venv/bin/python \
  scripts/run_frozen_residual_tiny_overfit_v1.py \
  --manifest runs/meta-specialist-frozen-residual-preflight-20260812/known-context-action-manifest-v1.json \
  --manifest-sha256 7b79b57436c3d1029abf35e9895045b4f546ff1aea0db706c948c4f4aaec6689 \
  --seed 0 --max-prefixes 64 --max-updates 1 --learning-rate 0.01 \
  --device cpu \
  --output-dir runs/meta-specialist-frozen-residual-preflight-20260812/tiny-seed0 \
  --execute
```

seed 1 は `--seed 1` と `tiny-seed1` に置き換えた。同じコマンドから CABT、固定 six、shadow、longrun、Kaggle API は呼び出されない。

## signed behavior API の分離

既存の `frozen_residual_loss_v1` は hard target index による self-imitation 用である。一方、cross-fitted outcome residual は signed behavior log-probability target なので、同じ関数へ暗黙に流用しない。新規 `frozen_residual_signed_behavior_loss_v1` は次を明示的に実装した。

```text
imitation = mean(-signed_weight * log_softmax(base.detach() + residual)[target])
anchor_kl = KL(softmax(base.detach()) || softmax(base.detach() + residual))
residual_l2 = mean(residual ** 2)
total = imitation + kl_weight * anchor_kl + l2_weight * residual_l2
```

`signed_weight` は有限な浮動 tensor で `[-1, 1]` に限定し、base logits には勾配を返さない。正の weight は selected behavior を強め、負の weight は逆向きの signed target として働く。focused test で正負 weight、anchor/L2、base detach、範囲外 weight rejection を確認した。

今回の実データ tiny は `target_kind=self_imitation_rule_relabel_v1` のため、この signed performance objective は接続していない。cross-fitted manifest を使う性能 pilot は、別の target kind と target manifest SHA を渡し、別の descriptor/report として実装・検証しなければならない。

## 検証

今回の focused verification:

```text
tests/meta_specialist/test_frozen_residual_v1.py
tests/meta_specialist/test_frozen_residual_trainer_v1.py
tests/meta_specialist/test_run_frozen_residual_tiny_overfit_v1.py
12 passed
```

runner fail-closed test は explicit flag なしで exit code 2、dry-run は optimizer update 0・全 authority false・`SELF_IMITATION_INTEGRATION_ONLY` を確認する。trainer fixture は base state unchanged、全 base parameter frozen、sidecar-only optimizer、context-only denominator 除外、descriptor target kind/manifest SHA 必須を確認する。

追加確認:

- `python -m py_compile scripts/run_frozen_residual_tiny_overfit_v1.py src/mage_ptcg/meta_specialist/frozen_residual_trainer_v1.py` — pass
- preflight manifest SHA の再照合 — pass
- seed 0/1 の実データ tiny — 両方 exit 0
- base checkpoint SHA run 前後一致 — seed 0/1 とも pass
- sidecar tensor SHA は初期 zero state から update 後に変化 — seed 0/1 とも pass
- `git diff --check` — 最終確認対象

## 解釈と残リスク

今回の loss 数値や sidecar SHA は、実戦勝率・CABT改善・teacher quality・promotion の証拠ではない。特に各 seed で 1 update のみ、同じ最初の game から最大 64 prefix、Rule teacher relabel を使っているため、性能評価や一般化を推定できない。

残る主要リスク:

- trainer の self-imitation 経路は現在 `target_index` hard loss を使う。`target_masses` の soft distribution を全面的に使う設計は別途必要。
- signed performance target の transition-to-prefix alignment、cross-fit manifest SHA、effective denominator はまだ実データで接続していない。
- variable legal domain、ordered/soft action mass、STOP action の performance target は未評価。
- residual を runtime policy/CABT へ接続すると trajectory が変わるため、unknown/OOD zero pass-through、semantic decoder、STOP legality、GRU commit の統合監査が必要。
- 長時間学習、fixed-six、shadow-C、promotion、Champion変更、提出は引き続き禁止。

## 成果物

- sidecar: `src/mage_ptcg/meta_specialist/frozen_residual_v1.py`
- preflight: `src/mage_ptcg/meta_specialist/frozen_residual_preflight_v1.py`
- trainer: `src/mage_ptcg/meta_specialist/frozen_residual_trainer_v1.py`
- runner: `scripts/run_frozen_residual_tiny_overfit_v1.py`
- tests: `tests/meta_specialist/test_frozen_residual_v1.py`、`test_frozen_residual_trainer_v1.py`、`test_run_frozen_residual_tiny_overfit_v1.py`
- reports: `runs/meta-specialist-frozen-residual-preflight-20260812/tiny-seed0/seed-0-tiny-report.json`、`tiny-seed1/seed-1-tiny-report.json`

## 追補 — signed outcome targetの実data integration tiny

上記のself-imitation tinyとは別に、cross-fitted outcome targetを接続するsigned residual runnerを、性能評価ではない bounded integration として実行した。runnerは `scripts/run_signed_residual_tiny_v1.py`（SHA `4eeadc35d18f9acfa2812f71d49a115ce7a49f8d85ece7a0184f9f945f3c9bc7`）、focused test SHAは `d59b4e0dcfbde9db147eb6b9caf8327effee5ccd0394087b33ae027dfa525780` である。各seedで `--execute --max-episodes 2 --max-updates 1` を明示し、CPUのみ、CABT/production/evaluator/longrunは起動していない。未指定実行はfail-closedで拒否される。

最終正本は `runs/meta-specialist-signed-residual-tiny-20260812/seed-0/` と `seed-1/`。seed0 report SHAは `43423e6a288f24b5eb8af9aee991f9d14b9bb5c9a71ff2c9d1ecf7331c3ec9d8`、sidecar file SHAは `e512024175133257ad2a4280d0b99ca6b8f0857a96c6f821368e7066695550fc`、160 rows / positive mass 160 / negative 0 / signed loss `0.1939923994294245`。seed1 report SHAは `337da0c405ae36550ec0993278ac8632058d736a42d14cdf3d85d0155a139317`、sidecar file SHAは `1af6823337d35a4b788d0cf83b509f6f578e6810f1c4b3c38d3485a7082c0d82`、131 rows / positive 0 / negative mass 131 / signed loss `-0.10173570971138989`。

両seedともbase checkpoint file/tensor SHAは実行前後で一致し、sidecarだけが更新された。reportは `evidence_class=SELF_SIGNED_OUTCOME_INTEGRATION_ONLY`、`target_kind=signed_behavior_log_probability`、`performance_evidence=false`、`training_permitted=false`、`promotion_authority=false`、`longrun_allowed=false`を持つ。seed0/1の正負massが片側へ分かれたのは最大2 episodeのbounded選択による局所偏りであり、勝率・改善・target qualityの証拠ではない。先行出力 `runs/meta-specialist-frozen-residual-outcome-targets-20260812/tiny-seed{0,1}/` は同一入力・同一数値の退避複製で、評価時は最終正本を使用する。

検証は、signed runner / trainer / materializer / cross-fitted targetの targeted pytest 12 passed、py_compile、`git diff --check`、docs validator `Validated 13 canonical documents.`。残課題はsidecar strict loader/factory、coverage telemetry、fixed-six 24局/seed evaluatorであり、本artifact単独から性能候補やlongrun許可を導かない。
