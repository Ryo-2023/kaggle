# V4 information-aware / nontrivial action objective 実装 brief（2026-08-10）

## 結論

wave2 の sealed validation carry（seed 1）では、forced domain size 1 が 640 rows、学習可能な complete-action row が 3,148 rows だった。一方で既存 trainer は forced row の NLL がゼロにもかかわらず、物理 record ごとの `sequence_weight` に入れる。そのため情報を持たない record が多い sequence ほど同じ勾配が薄まる。評価器は既に forced row を NLL/ranking から外しており、学習側も合わせるべきである。

同じ wave2 validation の主要操作 top-1 は PLAY=0.649 (881 rows), ATTACH=0.529 (274), EVOLVE=0.518 (110), RETREAT=0.306 (36), ATTACK=0.575 (365), END=0.237 (63) である。全 row micro 指標だけで checkpoint を選ぶと、CARD 等の高頻度行への改善がこの失敗を覆い隠せる。以下は **同じ sealed validation を変えず**、この二点だけを最小変更で扱う案である。これは研究用 objective であり promotion authority を増やさない。

## 変更境界

- 対象: `recurrent_bc_v4.py` の `_train_epoch` と epoch validation、`v4_imitation_metrics.py` の集計、BC report/history。
- 不変: selection manifest、materializer の split/order/burn-in、teacher target、reach mass、GRU architecture、CABT evaluator、validation の全 sequence digest。
- 禁止: validation label から重みを推定すること、record/step の oversampling、trajectory を分割して GRU の時間順序を変えること、単純な無上限 inverse-frequency 重み。

## 1. forced domain size 1 を学習分母から外す

各 decoder row について complete logits を一度構成し、`eligible = logits.numel() >= 2` とする。forced row でも必ず `forward_record_group_v4` を実行し、その `hidden_state` を次 record へ渡す。ただし loss と正規化質量には入れない。

```text
loss numerator += quality_weight * reach_mass * action_weight(type) * NLL   # eligible のみ
loss denominator += quality_weight * reach_mass * action_weight(type)       # eligible のみ
sequence_loss = numerator / denominator
```

`denominator == 0` の全 forced sequence は forward を完走するが optimizer update を発生させない（または materializer/trainer が明示的に reject する）。通常の sequence に forced record を後置しても、それ以前の parameter gradient と update は不変でなければならない。これは既存の物理-record 平均から「情報を持つ teacher decision の reach-mass 平均」への明示的な定義変更であるため、report の objective schema/version と `run_config` に含め、旧 checkpoint との数値比較・resume は fail-closed にする。

## 2. bounded action-type loss（最初の候補）

sampling は使わない。one optimizer step が episode sequence 単位であり、step/record を複製すると hidden-state 分布と TBPTT を変えて原因が重なるためである。

対象 type は canonical enum の PLAY=7, ATTACH=8, EVOLVE=9, RETREAT=12, ATTACK=13, END=14 とする。train partition の **eligible row の reach-mass 合計だけ**から固定重みを一度作る。STOP とその他 type は raw weight 1.0 のままにする。

```text
reference = median(train_mass[t] for t in focus_types with train_mass[t] > 0)
raw[t] = 1.0                                      # t が focus 外
raw[t] = min(2.0, 1.25 * max(1.0, sqrt(reference / train_mass[t])))  # focus
weight[t] = raw[t] / weighted_mean(raw[target_type], eligible train rows, reach_mass)
```

これにより高頻度だが低精度の PLAY/ATTACK も focus 外より相対的に 1.25 倍、稀な EVOLVE/RETREAT/END は最大 2 倍まで強調され、global weighted mean は 1.0 に固定される。raw weight は常に 1 以上なので、正規化後の最大値も 2 以下である。正規化前後の mapping、reference、各 type の train effective mass、objective SHA を report/resume config に固定する。欠けた focus type を暗黙に 0 除算しない。

開始条件は全 focus type が train effective mass >= 128、同一 sealed validation で各 type の eligible row >= 32。満たさない type は重みを作らず、まず corpus diversity を増やす。これは「極少数 example を最大重みで暗記する」失敗を防ぐ。

## 3. epoch metric と checkpoint 選抜

各 epoch の同一 validation carry pass に、既存 micro `complete_action_nll`/top-1 に加えて次を出す。いずれも forced row を除外する。

- `validation_action_type_macro_nll`: 出現する全 target action type ごとの NLL の算術平均。
- `validation_action_type_macro_top1`: 同じ type ごとの exact top-1 の算術平均。
- `validation_nontrivial_macro_nll` / `validation_nontrivial_macro_top1`: 上記 six focus type のみ。各 type の `eligible_rows`, `nll_weight`, exact count も残す。
- `validation_forced_domain_size1_rows` と `validation_nontrivial_missing_types`。coverage 不足は `None` ではなく checkpoint selection を fail-closed にする理由として記録する。

top-1 macro は row 数・reach mass で再重み付けしない（type ごとの同等重要性）。NLL は各 type 内では従来通り reach mass 加重する。既存の standalone imitation metrics と同じ定義・同じ sealed subset で一致する contract を置く。

balanced objective の multi-epoch checkpoint selection は、coverage が揃う場合だけ以下の固定 lexicographic composite を使う。

1. `selection_loss = 0.75 * validation_micro_nll + 0.25 * validation_nontrivial_macro_nll` を最小化する（改善の最小幅 `1e-4`）。
2. 同幅内では `validation_nontrivial_macro_top1` が大きい checkpoint、次に micro NLL が小さい checkpoint を選ぶ。
3. 現 best に対して micro NLL が `+0.01` を超えて悪化する checkpoint は、composite が良くても採らない。

micro だけを捨てず、少数 type だけを絶対優先にもせず、評価の主目的を明示するための基準である。objective/selection rule は report の config SHA と resume identity に含める。

## TDD の RED oracle

実装前に最低限次を失敗させる。

1. **forced denominator**: informative record の後ろへ任意数の forced-only record を足しても、同一初期値・NoOp/Adam の一 step の loss と informative parameters の gradient/update が一致する。forward-group call 数は増える。
2. **hidden advance**: forced-only record の直後の informative record に渡る `hidden_state` は `None` ではなく、forced record の output hidden と一致する。
3. **mixed group**: 同一 physical record に forced と non-forced decoder prefix が混在すると、non-forced prefix だけが numerator/denominator に入り、record の GRU transition は一回だけである。
4. **bounded weights**: synthetic train distributionで focus raw weight は `[1.0, 2.0]`、normalized weight は `(0, 2.0]`、eligible train reach-mass 加重平均は 1.0。zero-support focus type は ValueError/coverage-blocked になり、無限値を作らない。
5. **macro oracle**: 100 rows で top-1=1.0 の type と 1 row で top-1=0.0 の type の macro top-1 は 0.5（micro は 100/101）。forced row を追加しても双方不変。type 内 NLL は reach-mass 加重、type 間 NLL は等重みである。
6. **validation invariance**: weighted/baseline run が同じ `selected_sequence_sha256` と train/validation record IDs を report し、weight spec が train labels のみから得られる。validation target を変えると objective digest は不一致になる。
7. **selection**: micro NLL を 0.011 以上悪化させた macro 改善 checkpoint は reject、同じ composite tolerance 内は nontrivial macro exact の高い方を選ぶ。resume config/objective/weight mapping を変えると strict reject。

## wave3 diversity pilot 後の適用条件

- **両 seed で offline delta が正、かつ fixed held-out CABT も V2 を再現可能に上回る**: この objective は直ちに入れない。confound を増やさず現行 objective の 512/128 × 3 epoch longrun を開始する。macro は診断のみ追加してよい。
- **両 seed で offline delta は正だが CABT が V2 以下、または nontrivial macro top-1 < 0.70 / focus type が二つ以上 <= 0.60**: coverage 条件を確認後、baseline と balanced objective を同一 512/128、同一2 seed、同一3 epoch、同一 held-out protocol で比較する。この条件が本案の主対象である。
- **offline delta が片方でも non-positive、または coverage 不足**: reweight は開始しない。corpus/representation/optimizer か materialization を先に診断する。重みで失敗を隠すと、longrun の原因切り分けが不能になる。
- **offline/CABT とも改善したが macro が低い**: longrun を妨げない。baseline longrun の checkpoint を確定し、その後の別 branch research として balanced を比較する。

## 実装順

1. common eligibility/weight-spec helper と RED tests。
2. trainer loss denominator と history/report/resume schema。
3. sealed metric の macro/nontrivial 集計と trainer epoch integration。
4. CPU contract suite、同一 subset digest 比較、短い two-seed A/B。その後だけ GPU/CABT に進む。

