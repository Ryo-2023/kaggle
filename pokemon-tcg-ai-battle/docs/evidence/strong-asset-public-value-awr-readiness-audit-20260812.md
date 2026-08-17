---
project: MAGE-PTCG
document_status: evidence
as_of: 2026-08-12
scope: strong-asset-public-state-value-awr-readiness
authority: research-readiness-only
---

# Strong Asset後の public-state value + AWR/filtered BC 実装監査

## 結論

Strong Asset Census の共通 arena ランキングと native pair の BestKnown freeze が先であり、
この文書では CABT、収集、学習を起動していない。現在のリポジトリには AWR の名前を持つ部品は
複数あるが、**BestKnown を起点とした V4 candidate の public-state value + AWR/filtered BC を
一つの runner で実行する閉じた経路はまだ存在しない**。

追加の hard-label/outcome-weighted BC sweep は本監査の対象外とし、停止する。実装可能な最小経路は
次の通りである。

```text
native BestKnown pair を 384 局以上で freeze
  -> V4 candidate checkpoint を確定（外部 pair の場合は既存 hard snapshot BC を一度だけ利用）
  -> candidate 自身を sampled decoding で on-policy collection
  -> actor-visible state の cross-fitted V_hat(s)
  -> positive-advantage filtered BC（第一候補）または固定 beta の clipped AWR（第二候補）
  -> candidate checkpoint を fresh-process reload
  -> BestKnown pairとの 96 -> 384 -> 768 -> 1536 局比較
  -> BestKnown を越えた場合だけ deck mutation/local search と longrun gate
```

ここで external native agent の hard action を AWR の behavior probability として扱っては
ならない。external pair は V4 hard snapshot の初期化にのみ使い、AWR の return/behavior は
candidate 自身の actor-pool trajectory から作る。

## 監査範囲と実行していないこと

以下を read-only で確認した。

- `learner_awr_crr_v1.py`、public-state value / outcome target、V4 recurrent trainer、actor pool、
  legacy trajectory trainer と関連テスト
- 既存の `cross_fitted_outcome_materializer_v1.py`、signed residual trainer、coarse public-value
  rows の責務と authority
- BestKnown freeze 後に再利用できる collection / checkpoint / evaluation 境界

以下は行っていない。

- CABT、Strong Asset ranking、on-policy collection、GPU学習、AWR/filtered BC、deck search
- 既存 dirty worktree の巻き戻し、commit、push、Champion変更、Kaggle提出

## 現行コードの責務監査

### AWR primitive は重み関数だけ

`src/mage_ptcg/meta_specialist/learner_awr_crr_v1.py` は次の二関数だけを提供する。

- `awr_weights_v1(advantages, temperature=1.0, max_weight=20.0)`：有限な advantage を指数化し、
  上限を適用する
- `crr_weights_v1(advantages)`：positive advantage の indicator を返す

critic fitting、target join、V4 recurrent loss、optimizer、checkpoint、provenance、評価はない。
従って、この関数を呼ぶだけでは学習経路は完成しない。監査時 SHA-256 は
`9260a1870305c07a6d8c9f714360ad36f0c2a16c36931a60c984088a69df6528`。

### `policy_learning/training.py` は実 optimizer だが V4 経路ではない

`src/mage_ptcg/policy_learning/training.py` には `train_offline()` があり、`objective="bc"` または
`objective="awr"` を選び、`PolicyLearningExample.terminal_return - model.value` を advantage として
重み付けし、policy/value/family の損失を実際に optimizer へ渡す。これは実装上の有用な参照例で
あるが、次の理由で Strong Asset V4 runnerへそのまま転用できない。

- 入力は `RuleBCExample` 由来の `PolicyLearningExample`（単一選択 prompt）であり、V4 の
  `RelationalStateV4`、semantic prefix、STOP、complete-action group を受けない
- feature/model は `mage_ptcg.student.features` と legacy actor-critic であり、V4 checkpoint
  の topology / file SHA / tensor-state SHA と互換性がない
- `behavior_log_probability` はデータ列の provenance で、AWR ratioを計算する実装ではない
- multi-select / ordered selection を明示的に除外するため、V4で必要な prefix continuity を
  失う

このコードは「AWRの損失をどう書くか」の参照にはなるが、Strong Asset candidate trainerには
使わない。監査時 SHA-256 は `d9bcc23a11f43e33e27e524b9a9635c00aa35c097eb4a2bc11c1968959aad9ca`。

### `recurrent_bc_v4.py` は V4 optimizer だが AWR ではない

`src/mage_ptcg/meta_specialist/recurrent_bc_v4.py` は `SpecialistModelV4` に対する実 optimizer、
GRU の record grouping、complete-action NLL、STOP、checkpoint reload を持つ。入力は
`RecurrentBCSequenceV4` / `RecurrentBCStepV4` で、V4の本線に最も近い。

ただし、研究用 mode は次の二つだけである。

- `RESEARCH_ONLY_UNIFORM_WEIGHT`
- `RESEARCH_ONLY_OUTCOME_WEIGHTED_V4`

後者は `win=1.0 / draw=2/3 / loss=1/3` の固定 episode weight であり、`A=G-V_hat(s)` を
計算しない。`_train_epoch()` の通常経路では sequence/record weight と
`supervision_weight` の扱いが分離しているため、任意の per-step AWR mass をそのまま渡しても
分母が意図した mass にならない。既存 trainerを無理に再利用するなら分母、record grouping、
weight detach、positive-only context-only のテストを先に修正する必要がある。最小リスクは、
V4 `forward_record_group_v4()` と `_complete_action_nll_from_output()` の責務だけを再利用した
専用 AWR/filtered runnerを作ることである。

監査時 SHA-256 は `c9371f7463915ecfac37ea04094a33595d40a6e2438828b6d9059c907ed6ec54`。

### public-state value target は既にあるが authority は研究専用

`src/mage_ptcg/meta_specialist/cross_fitted_public_state_value_v1.py` は、
`ActorTrajectoryTransitionV1` から actor-visible な特徴だけを抽出する。

- public structural bucket baseline（旧経路）
- 56 scalar の固定 feature schema + leave-fold-out ridge value model（推奨経路）
- `value_model_sha256`、feature schema、ridge lambda、fallback count を保存

state model は opponent、seat、policy、private payload を外部メタデータとしては含めない。
ただし `SpecialistModelInputV1` の `card_bags` は `own_hand`、`deck_reveal`、
`looking_visible`、`self_discard`を含むため、これは厳密な「両者に公開された public state」では
なく、**actor-visible state value** と呼ぶのが正確である。厳密 public-only value が必要なら、
private card-bagを除いた別 feature schema と別 SHAを先に作る。既存 state model は AWR の
actor-visible `V_hat(s)` を作る良い基礎である。一方、manifest の
`training_permitted=false`、`promotion_authority=false`、`longrun_allowed=false` は設計上の
固定値であり、現状のまま production/training authority を持たない。

監査時 SHA-256 は `bbd8fe878188bfb5424e18fd8738e12ccde48abba9ad09936cc76da41f0ec5f0`。

### 旧 outcome residual は AWR の value baseline ではない

`cross_fitted_outcome_residual_v1.py` の baseline は fold外 episode return の global mean で、
状態に依存しない。これを `V_hat(s)` と呼んで AWRへ接続してはならない。監査時 SHA-256 は
`43544ab459c4e770885511e3c9bc47143a084c0cb6db27f6ab01bec9b663828b`。

### 既存 materializer / signed residual は「接続の部品」であって AWR runner ではない

`cross_fitted_outcome_materializer_v1.py` は sealed actor trajectory と outcome manifest を
hash-joinし、V4 sequenceを作る。ただし sequences の `supervision_weight` はすべて `0.0`で、
signed target は別の `AlignedSignedResidualPrefixV1` に置かれる。これは普通のBCへ誤投入しない
ための良い fail-closed 境界だが、AWRの学習実装ではない。

`signed_residual_trainer_v1.py` は Wave6 base modelを凍結し、residual sidecarだけを更新する。
base policyを更新して BestKnown超えを目指す経路ではない。今回の主線には流用しない。

監査時 SHA-256 は materializer `f6854af5a8d795770826751260ff58fba158f08bead1768ebeb3d54bab7b05c5`。

### actor pool は candidate own on-policy の正しい入口

`ActorPoolV1` は `behavior_kind="neural_specialist_v4"` を受け、V4 checkpoint の file SHA と
tensor-state SHAを検証する。`decoding_mode="sample"` なら fresh gameごとの seeded samplingを
使い、実際の prefix logits から behavior log-probabilityを記録する。各 transition は
actor-visible model input、semantic prefix、reward/discount、terminal、opponent provenanceを
持つ。

重要な区別は次の通りである。

- external native BestKnown：hard actionのみ。behavior probabilityなし。AWRのbehavior ratioへ
  接続しない
- V4 candidate own rollout：sampled logits、behavior log-probability、reward/discountあり。
  public-state value + AWR/filtered BC の入力にする
- greedy V4 rollout：log-probabilityは再評価値であり、sampling分布から実行したとは限らない。
  AWRを測る collection は `decoding_mode="sample"` を明示する

監査時 SHA-256 は `b60ab5be3fede6b13b26533b65b96b6762e9f84f317e9103abc97ae8ac11092a`。

### legacy V1 trainer は実学習だが candidate topologyが違う

`train_from_trajectories_v1.py` は V1 actor modelの V-trace、value head、BC anchor、checkpoint
resumeを実装している。しかし V4 checkpointをロードできず、V4の relational candidate scorer、
GRU record group、STOP semanticsを共有しない。Strong Asset主線で使うのは、実装パターンと
provenance設計の参照に限定する。監査時 SHA-256 は
`0c7d08ad49ae020d0e5f9fd3a185e2f6753d1a7cad3187a8b1afe5b8e8b41721`。

## 最小実装案（BestKnown freeze後）

### 新規 runner

新規専用ファイルを `scripts/run_public_state_awr_v1.py` とする。既存 hard BC runnerや residual
sidecarへ機能を混ぜない。CLIは最低限次を持つ。

```text
--best-known-pair-manifest
--initial-v4-checkpoint
--collection-run-dir       # candidate own actor-pool record root
--value-target-manifest    # cross-fitted public-state model value
--objective {filtered_bc,awr}
--temperature              # AWR時に1つだけ固定
--max-weight               # AWR時に1つだけ固定
--epochs / --max-updates / --seed
--output-dir
--execute                  # 明示しない限り target/train を起動しない
```

runnerの処理は次の順番に固定する。

1. BestKnown pair manifest、V4初期checkpoint、deck SHA、collection summary、engine/protocol SHA
   を hash-bindする。initial checkpointがない native external pairは、既存の qualified teacher
   snapshot BC を一度だけ通して V4 checkpointを作る。外部pairをAWR入力として直接扱わない。
2. actor-pool game recordを読み、completed/fault-free/known terminal outcomeだけを採用する。
   unknown outcome、fault、重複game、同一episodeのtrain/validation跨ぎ、source SHA不一致は拒否する。
3. `build_cross_fitted_public_state_model_value_manifest_v1()` を用い、episode fold外だけで
   `V_hat(s)` をfitする。bucket/global residualはAWRの主baselineにしない。
4. 各 transition の prefix target index を sealed semantic/STOP domainへ照合する。必要なら
   `representation_v4_from_step_input_v1()` で V4 stateを作る。record groupごとに hidden stateを
   一度だけ進め、prefix continuationを切らない。
5. `filtered_bc` は `advantage > 0` の prefixだけ loss-bearing にし、非採用prefixは context-only
   とする。`awr` は `w=exp(clip(A/beta, log(eps), log(max_weight)))` を detachし、recordごとの
   weight mass（または pre-registered mean）で分母を正規化する。beta/max_weightを勝率後に
   sweepしない。
6. complete-action NLL（semantic + STOP）をV4 modelへ適用し、gradient clip、seed、optimizer、
   update count、weight statistics、positive/zero/negative rowsを記録する。
7. source checkpointは変更せず、candidate checkpointをatomic publishし、fresh processで
   V4 runtime loaderを再読込して file/tensor SHAを検証する。

### target authority artifact

既存 value manifest（全 authority false）を直接書き換えず、runnerが一度だけ
`strong-asset-public-awr-target-v1` wrapperを生成する。wrapperの必須フィールドは以下。

- `best_known_pair_id`、agent/policy SHA、deck SHA、archetype、source/permission/usage boundary
- initial V4 checkpoint file SHA / tensor-state SHA（external native pairの場合は teacher snapshot
  SHAと変換元を含む）
- on-policy collection manifest SHA、record/game count、fault/unknown count、opponent schedule SHA
- public-state value manifest SHA、source episode SHA、fold count、feature schema、ridge lambda、
  value-model SHA、fallback count
- `objective_kind`、`temperature`、`max_weight`、positive filter、record normalization、clip policy
- transition/prefix counts、positive/zero/negative effective mass、train/validation episode ids
- `training_permitted=true` はこの wrapperが全 hash/permission/closed-schema checksを通過した
  場合だけ付ける。`promotion_authority=false`、`longrun_allowed=false` は常に固定する

つまり diagnostic public-value manifestを、そのまま promotion artifactへ昇格させない。target
authorityは candidate policy/deck identity と collection provenanceを束ねる別artifactである。

### 既存コードを再利用する箇所と新規実装する箇所

| 責務 | 再利用 | 新規 |
|---|---|---|
| V4 checkpoint load/save | `neural_model_v4.py` | runnerのBestKnown/checkpoint binding |
| own on-policy collection | `ActorPoolV1`, `collect_trajectories_v1.py` | BestKnown pair manifestを入力する job planner |
| public `V_hat(s)` | `cross_fitted_public_state_value_v1.py` の state-model builder | authority wrapper |
| V4 state/target projection | `representation_v4_from_step_input_v1`, `trajectory_v1` | actor record -> train/valid sequence join |
| policy loss | `recurrent_bc_v4.py` の forward/group/NLL | per-step detached AWR/filtered mass と正しい分母 |
| metrics/progress | ProgressReporter、既存 checkpoint descriptor | target/weight/advantage統計と fail-closed summary |
| evaluation | bounded parallel CABT evaluator、asset ranking runner | candidate vs BestKnown identityの評価 manifest |

## 必須テスト

最低限、次を先にTDDで閉じる。AWR/filtered BC trainerと同じファイルで直接CABTを実行しない。

1. **Authority / identity**：pair、deck、checkpoint file/tensor、collection、value model、protocol
   の SHA mismatch、permission不足、unknown/fault、重複game、split leakageを拒否する。
2. **Public-only**：target artifactとvalue featuresに opponent、seat、private payload、local alias、
   raw observationが混入したら拒否する。
3. **Cross-fit leakage**：held-out foldのbaseline fittingにheld-out episodeが入らない。state-model
   feature schema、ridge lambda、model SHAを再計算できる。
4. **Prefix/STOP alignment**：semantic target、multi-select ordered/unordered、forced STOP、
   legal domain arityを検証する。prefixは一つの record groupとして hidden continuityを維持する。
5. **Weight math**：AWR weightは有限・非負・clip内・detach済み。filtered BCは advantage<=0を
   lossから除き、context-only rowを denominatorへ入れない。positive/zero/negative massを報告する。
6. **Gradient/checkpoint**：tiny fixtureで正のweightに対してV4 actor parameterが動く。source
   checkpointのtensor SHAは不変で、candidate checkpointのfresh reload SHAが一致する。
7. **Missing behavior probability**：external hard teacherの probability を捏造しない。candidate
   own sampled rolloutでは stored behavior log-probabilityを診断値として整合検証するが、AWR
   targetの計算に未取得の外部 ratioを要求しない。
8. **Evaluator gate**：candidateがBestKnownを越えたかを Rule v0/Wave6との差で置換しない。96→384→
   768→1536の段階、両seat、opponent/archetype層、fault denominator、deck/policy SHAを固定する。

## 必須 artifact と命名案

```text
runs/strong-asset-awr/
  best_known_pair_manifest.json
  candidate_onpolicy_collection_manifest.json
  public_state_value_manifest.json
  public_state_value_manifest.sha256
  awr_target_authority.json
  train_summary.json
  progress_summary.json
  candidate-best.pt
  candidate-best.pt.sha256
  candidate-eval-096/ledger.json
  candidate-eval-384/ledger.json
  candidate-eval-768/ledger.json
  candidate-eval-1536/ledger.json
```

空白を含むパスは実装時に避け、上記の `strong-asset-awr` を使用する。各評価 ledgerには
`best_known_pair_id`、candidate policy/deck SHA、opponent pool/protocol SHA、seed、seat、fault、
ゲーム数、集計 score、per-opponent/seat breakdown、実装 SHAを含める。

## 時間見積り（ランキング完了後）

以下は現在の実装部品を再利用する前提の目安であり、実測 throughput を最初の8局で取得して
更新する。GPU/CABTの環境差を確定値として扱わない。

| 作業 | 目安 | 完了条件 |
|---|---:|---|
| BestKnown pair manifest / permission / native ranking結果のfreeze | 30–90分 | pair SHA、category別BestKnown、pool/protocol SHAが閉じる |
| target authority + V4 AWR/filtered trainer + focused tests | 3–6時間 | synthetic/real-schema tinyで上記7テストを通過 |
| candidate own sampled collection（初回96–384局） | 30分–4時間 | fault 0、両seat、known terminal、record SHA sealed |
| public state-model value fit / target materialize | 10–60分 | fold外fit、model SHA、target countsが一致 |
| 2 seed fixed-budget training | 20分–3時間 | candidate checkpoint fresh reload、weight/gradient統計 |
| candidate vs BestKnown 96局 screen | 15–60分 | runner throughputとfault denominator確認 |
| 384局 confirmation | 1–4時間 | 両seat・複数opponentで差方向確認 |
| 768局 / 1536局 | 各2–12時間 | pre-registered gate通過時のみ継続 |

長時間学習開始までの最短は、BestKnown freeze済み・existing V4 initial checkpointあり・GPUが
安定している場合で**半日〜1日**。external native pairからV4初期checkpointを新規作成する
必要がある場合は、qualified hard snapshot収集/変換分としてさらに**数時間〜半日**を見込む。
ランキングがBestKnownを安定確定できない場合は、AWRへ進まず停止する。

## GO / NO-GO 判定

### AWR/filtered BC の pilot開始条件

- `TrainingEligibleBestKnown` と `SubmissionEligibleBestKnown` が共通 arena で別々に freeze済み
- candidateの初期V4 checkpointと deck/policy identityが閉じている
- own sampled collectionが両seat、fault 0、known outcome、opponent schedule固定
- public-state value targetの source/value/protocol SHAと cross-fit reportが一致
- `training_permitted=true` の target authority wrapperが生成済み

### broad 継続条件

- 96局は診断のみ。candidateがBestKnownを下回っても即断しない
- 384局で candidate aggregate がBestKnown以上、かつ両seat・主要archetypeで致命的悪化なし
- 768局で差の方向が維持され、fault 0、runtime/package closureが通る
- 1536局で BestKnown に対する事前固定の改善基準（score差、seat worst-case、meta weighted
  score）を同時に満たす

### longrun GO

1536局で candidate が元の BestKnown を明確に上回り、両seat/主要archetypeの悪化、fault、
package/runtime違反がなく、deck/policy SHAが同一評価条件で再現できる場合だけ GO とする。
Rule v0やWave6を上回っただけではGOにしない。どれか一つでも未達なら `LONGRUN_NO_GO` とし、
同じ AWR beta/filter/epoch の盲目的 sweepは行わない。

## 監査時の検証

次を実行し、既存部品の read-only integrity を確認した。

```text
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/pytest -q -s \
  tests/meta_specialist/test_learners_v1.py \
  tests/meta_specialist/test_cross_fitted_public_state_value_v1.py \
  tests/meta_specialist/test_cross_fitted_outcome_materializer_v1.py \
  tests/meta_specialist/test_signed_residual_trainer_v1.py
```

結果：**13 passed in 9.03s**。

また次を実行した。

```text
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python -m py_compile \
  src/mage_ptcg/meta_specialist/learner_awr_crr_v1.py \
  src/mage_ptcg/meta_specialist/cross_fitted_public_state_value_v1.py \
  src/mage_ptcg/meta_specialist/cross_fitted_outcome_residual_v1.py \
  src/mage_ptcg/meta_specialist/cross_fitted_outcome_materializer_v1.py \
  src/mage_ptcg/meta_specialist/recurrent_bc_v4.py \
  src/mage_ptcg/meta_specialist/actor_pool_v1.py \
  src/mage_ptcg/meta_specialist/train_from_trajectories_v1.py
```

結果：**成功**。これは性能改善やAWR実行の証拠ではなく、既存部品の構文/integrity確認である。

## 最終判断

現時点でAWRを「既に実行可能」と報告するのは不正確である。正確な分類は次の通り。

| 項目 | 判定 |
|---|---|
| public-state ridge value target | 実装済み、research-only、AWR targetの基礎として再利用可 |
| external hard teacher collection | 実装済み、behavior probabilityなし |
| candidate own V4 sampled trajectory | 実装済み、BestKnown由来のV4 checkpoint後に利用可 |
| V4 complete-action optimizer | 実装済み、uniform/fixed outcome weightのみ |
| `learner_awr_crr_v1.py` | 重み関数のみ |
| public-state value + V4 AWR/filtered BC runner | **未接続** |
| BestKnownを越えた fine-tune結果 | 未測定 |
| deck optimization | BestKnown freeze後に未実施 |
| longrun / Champion / submission | **NO-GO / 未実施** |

次の担当は、まず直接ランキングの結果から pair identity を受け取り、その後この文書の
authority/runner/test契約を一度だけ実装する。ランキング未確定のまま AWR学習を始めない。
