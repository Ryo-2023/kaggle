# V4 policy drift 監査（2026-08-12）

## 判定

研究専用の sealed actor-visible replay 監査スクリプトを追加し、Wave6 seed0/seed1 と public-confidence/OOD candidate seed0/seed1 を同一の V4 replay subset へ teacher-forced 入力した。CABT 対戦、学習、longrun、Champion変更、Kaggle提出は実行していない。

今回の smoke では、public OOD candidate は Wave6 から完全に離れたモデルではないが、seed により drift の大きさが異なる。Wave6 seed0→public OOD seed0 は平均 JS 0.0426、top-1 action change 11.75%、hidden cosine 0.9542、Wave6 seed1→public OOD seed1 は平均 JS 0.0181、top-1 action change 9.25%、hidden cosine 0.9742 だった。これは「候補が全方策を破壊した」とは言えない一方、同じ masked full-model fine-tune でも seed ごとに変化量が異なることを示す診断材料である。勝率との因果や promotion 可否はこの監査からは主張しない。

Wave6 seed0 と seed1 の相互 drift は平均 JS 0.0246、top-1 action change 9.00%、hidden cosine 0 未満（平均 -0.0038）となった。両 Wave6 checkpoint 自体が別の学習軌跡であり、hidden absolute vector は初期値・学習履歴の差を含むため、hidden cosine は「seed instability の候補信号」であって単独の性能証明ではない。

## 追加成果物

- 実装: `scripts/audit_v4_policy_drift_v1.py`
- focused tests: `tests/meta_specialist/test_policy_drift_audit_v1.py`
- 入力マニフェスト: `docs/evidence/v4-policy-drift-audit-input-20260812.json`
- smoke 出力: `runs/meta-specialist-v4-policy-drift-audit-smoke-20260812.json`

入力マニフェストは Wave6 の sealed selection manifest を SHA 固定している。

| artifact | SHA-256 |
|---|---|
| selection manifest `runs/meta-specialist-two-lane-readiness/recurrent-selection/archaludon.json` | `b3044504df1192ce072377f1ddfbeeafdf071a715ef896076b5adb1471eaf0cc` |
| input manifest | `28a7b7c2cb065b20b9a777ba6696e758f291f1615f56c9ae4c646692ea14a501` |
| audit script | `6fabe013f98228e70987aa6a69aa7e87c7544941f9f00f895ea9f1234bb4bcb8` |
| focused test | `ed3cb9c833602d6d76aa106951323d13aaf4695a1504fed7d56c2ec136c7f019` |
| smoke JSON | `36a33542ebd219ce54134a8b17019ab00abe37508817c9e6b1ad53d4e90b4b17` |

## 監査対象

| label | checkpoint | tensor state SHA |
|---|---|---|
| `wave6_seed0` | `runs/meta-specialist-v4-archaludon-longrun-wave6-current/.../seed-0/best-recurrent-bc-v4.pt` | `36b6ace02e69849ea476565bd0ef283d206a6786ba2ac0c0040e82d263f3292a` |
| `wave6_seed1` | `runs/meta-specialist-v4-archaludon-longrun-wave6-current/.../seed-1/best-recurrent-bc-v4.pt` | `046968c1a295af0ae84594fedb8503a33b6f8a5dd33314e8fc4fe4ae09985e8a` |
| `public_ood_seed0` | `runs/meta-specialist-v4-public-confidence-ood-pilot-exec-rerun-20260812/seed-0/candidate/best-recurrent-bc-v4.pt` | `f08982fd812518eadf771afac61eb5a48163004e45c1073746502a7521c07002` |
| `public_ood_seed1` | `runs/meta-specialist-v4-public-confidence-ood-pilot-exec-rerun-20260812/seed-1/candidate/best-recurrent-bc-v4.pt` | `2ea9bdd6028e8b66d3c71592d732ffca3a48aa999c9790ddfdbc279ee5b249c6` |

全 checkpoint の model config は vocabulary 1267、hidden 128、embedding 64 で、V4 strict loader の file SHA と tensor-state SHA を検証してから使用した。

## Replay 範囲

- recurrence: `carry`（通常の episode carry）
- bounded materializer: `materialize_fast_research_uniform_subset_v4`
- requested `--episodes-per-partition 4`, `--components-per-partition 4`
- 実測 sequence 数: 8（train 4 / validation 4）
- 実測 source records: train 217、validation 285
- 比較可能な non-forced policy rows: 400（`--max-policy-rows 400` で deterministic prefix cap）
- max-records は当初 400 を指定したが、complete episode の境界を壊せず validation 未充足で fail-closed になった。したがって再実行では 2000 を上限にして同じ 8 complete episodes を読み込み、その後に aligned row を deterministic に 400 件へ切った。これは full corpus ではなく bounded smoke である。
- 1 row は actor-visible V4 state と legal semantic domain の logits だけで構成し、`opponent_id`、`seat` 等を row へ保存または model input へ渡していない。
- forced domain size 1 は top-1/KL/JS 比較から除外したが、episode recurrent carry は維持した。

再現コマンド:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python \
  scripts/audit_v4_policy_drift_v1.py \
  --input docs/evidence/v4-policy-drift-audit-input-20260812.json \
  --output runs/meta-specialist-v4-policy-drift-audit-smoke-20260812.json \
  --max-records 2000 --episodes-per-partition 4 \
  --components-per-partition 4 --max-policy-rows 400 --device cpu
```

## 指標定義

- `top1_action_change_rate`: baseline と candidate の同一 legal semantic domain で argmax index が変わった割合。
- `mean_js`: softmax policy の Jensen–Shannon divergence。自然対数の JS を `ln(2)` で割り、0〜1へ正規化した。
- `mean_kl_baseline_to_candidate` / reverse: 同じ domain の forward/reverse KL。masked `-inf` は probability 0 として扱う。
- `root_action_change_rate`: semantic prefix 長が 0 の row の top-1 change。
- `by_baseline_top1_action_type`: baseline top-1 を `STOP`、`ATTACK`、`END`、`RETREAT` などへ分類し、その行動種別ごとの change/JS を集計。
- `by_domain_bucket`: legal domain size 別（2〜8 は exact、9〜16 等は range）の change/JS。
- `first_divergence_positions`: sequence 内で最初に top-1 が変わった record-group index の histogram。
- `hidden`: 各 recurrent group の V4 GRU hidden state について L2 差と cosine を集計。
- `parameter_delta`: checkpoint state dict の first module path（`memory`、`candidate_mix` 等）単位で absolute/L2/relative L2 を集計。
- `pairwise_seed_js`: 同じ replay を全 checkpoint pair で比較した JS。今回の input では Wave6 seed0/1、public OOD seed0/1 を含む。

## Smoke 結果

| baseline → candidate | policy rows | top-1 change | root change | mean JS | hidden mean L2 | hidden mean cosine |
|---|---:|---:|---:|---:|---:|---:|
| Wave6 seed0 → Wave6 seed1 | 400 | 36 (9.00%) | 9.30% | 0.02459 | 13.954 | -0.0038 |
| Wave6 seed0 → public OOD seed0 | 400 | 47 (11.75%) | 12.39% | 0.04262 | 2.619 | 0.9542 |
| Wave6 seed1 → public OOD seed1 | 400 | 37 (9.25%) | 10.14% | 0.01807 | 2.157 | 0.9742 |

Domain size別では、public OOD seed0 の Wave6 seed0 比較で domain 6 が 6.67%、domain 7 が 13.64%、domain 8 が 13.33%、domain 9〜16 が 10.00%、domain 2 は 3.67%だった。public OOD seed1 では domain 6 が 20.00%、domain 7 が 22.73%、domain 8 が 26.67%、domain 9〜16 が 10.00%、domain 2 が 3.67%だった。これは候補が広い semantic domain で相対的に drift しやすい可能性を示すが、400 rows の bounded smoke であり、一般化結論ではない。

行動種別別には、各 comparison の JSON に `STOP`、`ATTACK`、`END`、`RETREAT` を含む baseline-top1 集計が保存されている。該当 action type が少ない bucket は比率の分散が大きいため、次回は全 sealed replay または 1000 行以上の固定 subset で再集計する。

## 解釈と次の判断

1. public OOD candidate の policy drift は seed0 と seed1 で方向は同じだが、seed1 の方が小さい。少なくとも「どの seed でも同じ量の大規模 drift」という単純な説明は成り立たない。
2. Wave6 seed0/1 間の hidden cosine が極端に低い一方、public OOD candidate は対応する Wave6 へ高 cosine である。これは public OOD candidate が初期 Wave6 の representation を大幅に置換したというより、同じ座標系で head/局所重みを動かした可能性と整合する。ただし hidden state の絶対比較は seed 初期値差にも依存する。
3. parameter delta の module 表はモデルがどの部位を更新したかの記述であり、性能寄与を意味しない。public OOD の主要 relative L2 は `argument_value_projection`、`candidate_mix`、`relation_projection` 周辺だが、これは候補学習設定の差分を示すだけである。
4. この監査は ChatGPT Pro レビューの「policy drift を測る」要求を閉じる最小実装であり、勝率・評価ノイズ・recurrence ablation・teacher projection round-trip を代替しない。次の優先度は、同一 checkpoint の評価反復による noise 分離、続いて frozen Wave6 residual + KL anchor の比較設計とする。

## 未解決・不足

- 全既存 candidate（strict、tomatomato 24/96、empty-selection、action-balanced、lucifer、outcome-weighted、V5）を一つの common manifest に揃えた full audit は未実施。
- 今回は同一 replay subset 上の policy drift のみで、shadow-B/C の勝率との統計的相関は未計算。
- hidden cosine は state-space 座標が完全に同一の checkpoint で解釈しやすい。独立初期化 seed 間の absolute cosine を単独の catastrophic-forgetting 指標にしない。
- `max_records=400` は complete episode 境界のため fail-closed になった。100〜400 transition の厳密な smoke を必要とする場合、episode 内を切らずに使える専用の row-level sealed projection を別途定義する必要がある。
- CABT evaluator の再現性、true pairing、recurrence reset/turn-reset ablation はこの成果物の範囲外。

## 検証

```text
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/pytest -q -s tests/meta_specialist/test_policy_drift_audit_v1.py
4 passed in 0.77s
```

本監査は `promotion_authority=false`、`runtime_evaluation=false`、`training_started=false`、`submission_started=false` を出力 JSON に固定している。
