# Rocket Specialist Route Meta v1 — Design

## 目的

`rocket_theta_behavior_meta_v2` は5つのtheta tableの数値変換を探索したが、screen上位が独立再評価で反転した。次のsource-generation familyでは、数値を再探索せず、受理済みRocket sourceに既に実装されている `_SPECIALIST_THETA` の family-to-theta routing だけを bounded に組み替える。

狙いは、公開状態からの archetype commit 自体は固定したまま、specialist theta の miscommit／family-specific overfit を軽減できる composition を得ることである。勝率改善は仮説であり、このgeneratorの成功はsourceの安全なmaterializeと独立評価に限定する。

## 入力sourceと不変条件

- base: `runs/cg-fresh-internal-meta-intake-20260815-f/internal_ozawa-rocket-rule_de797c3646e9`
- source commit: `de797c3646e935157618be3edea17615430ccfec`
- source policyとdeckはsealed noteのSHA-256で検証する。
- `_SPECIALIST_THETA` の辞書キーは厳密に `A01`, `A09`, `A07`, `A11`。
- 値は厳密に `_THETA_GENERAL`, `_THETA_LUCMIX`, `_THETA_A09_MERGED`, `_THETA_A07_MERGED`, `_THETA_ABOMASNOW_R2` のいずれかのName参照。
- 変更対象は `_SPECIALIST_THETA` の値Name tokenだけ。`_TIER_A_TO_GROUP`、commit条件、観測抽出、環境変数、deck、import、`_apply_theta`、その他のruntimeはbyte-for-byte保持する。
- 全candidateは`local_eval_only`、authority全false、提出bundleへは入れない。

## 生成surface

各variantは現在のrouteを一つ以上変更し、ASTのsource spanで値参照だけを置換する。文字列置換やPython再serializeは使わない。次の12 variantを固定する。

| variant | route変更 | 仮説 |
|---|---|---|
| `A01_GENERAL` | A01だけGENERAL | Lucario系のspecialist miscommitを抑える |
| `A09_GENERAL` | A09だけGENERAL | Grimmsnarl系のspecialist varianceを抑える |
| `A07_GENERAL` | A07だけGENERAL | Alakazam系のspecialist varianceを抑える |
| `A11_GENERAL` | A11だけGENERAL | Abomasnow系のspecialist varianceを抑える |
| `A01_A09_GENERAL` | A01/A09をGENERAL | 悪／闘側の過適合を同時に抑える |
| `A07_A11_GENERAL` | A07/A11をGENERAL | 水／鋼側の過適合を同時に抑える |
| `GENERAL_ONLY` | 全familyをGENERAL | dispatcherのfamily theta効果を除去する対照 |
| `A01_A07_LUCMIX` | A01/A07をLUCMIX | setup/board類似familyで共有する |
| `A09_A11_A09MERGED` | A09/A11をA09_MERGED | supporter/guard類似familyで共有する |
| `SWAP_A01_A09` | A01とA09を相互交換 | specialist表のfamily対応を反転する hard-negative候補 |
| `SWAP_A07_A11` | A07とA11を相互交換 | specialist表のfamily対応を反転する hard-negative候補 |
| `ROTATE_A01_A09_A07_A11` | A01→A09→A07→A11→A01 | 全routeの循環交換 hard-negative候補 |

現行routeそのものはcandidateとして含めない。各variantのpolicy SHAは相互に一意で、current poolおよび設定したartifact scan rootに未出現であることを要求する。

## splitと評価ゲート

- `META_TRAIN`: 8 variant。TRAIN-only fault smokeとP1-control CEMに限定する。
- `META_DEV`: 2 variant。TRAINの独立positive、fault 0、seat gap ≤5%、opponent×seat lower-tail gateを満たした場合だけ使用する。
- `META_FINAL`: 2 variant。DEV transfer gate後に一度だけ使用し、選抜・CEM更新には使わない。
- smokeはsplit manifestから明示的にTRAIN IDだけを渡す。全pool指定は禁止する。
- CEM screen上位のdeltaだけでは昇格しない。独立re-evaluationでpositive lower-tail、seat-safe、opponent×seat-safeを同時に満たす必要がある。
- generation 0が独立gate不合格ならgeneration 1、DEV、FINALを起動せず、このsource epochを停止する。

## freshnessと停止条件

source commitが過去と同じでも、route変換後のpolicy SHAが新規であることをfreshnessの最低条件とする。ただし同一Rocket source由来のlocal proxyであり、native/public metaの代替とは主張しない。

次のいずれかでfail-closedする。

- `_SPECIALIST_THETA` の辞書構造、キー、値Name、出現回数が想定と異なる。
- route変換がno-op、未知variant、重複variant、または静的安全検査／60枚deck検査に失敗する。
- current poolまたはartifactにpolicy identity hitがある。
- TRAIN smokeにfault／illegalが出る。
- 独立CEMでpositive lower-tail、seat-safe、opponent×seat-safeを満たすcandidateが0件。

## 成功基準

このfamilyの成功は、(1) hash-boundな12件のsealed source、(2) TRAIN-only fault-free smoke、(3) 独立CEMでの再現性付きpositive candidate、の順に判定する。CEM gateを満たさない場合、BestKnown、P1、deck、Champion、submission、`cg_bestknown_loop_v1.py`の状態は変更しない。
