---
project: MAGE-PTCG
document_status: evidence
as_of: 2026-08-12
scope: workstream-e-strong-asset-fine-tuning-readiness
source_commit: 30cade0e5d349d6ea545f019fc411e9d53288f16
authority: research-readiness-only
---

# Strong Asset Fine-Tuning readiness 監査

## 結論

現時点で最短かつ再利用性の高い主線は、**qualified な strong deck + agent pair を固定し、
その pair を実際に挙動させた actor-visible teacher snapshot を起点に、V4 topology の
outcome/quality-weighted BC を短期 pilot として実行し、その後に candidate 自身の
on-policy return と public-state value を使った filtered BC / AWR へ進む**順序である。

既存コードは、(a) 外部 strong agent の hard-action teacher collection、(b) neural/student の
actor-pool trajectory と reward/discount、(c) outcome/public-state の cross-fitted target、
(d) V4 DAgger の public-state relabel、(e) deck mutation/race の部品をそれぞれ持っている。
ただし、それらを一つの Strong Asset loop として接続した production-ready trainer はまだない。

最重要の実装上の境界は次の三つである。

1. 外部 strong agent は `opponents/pool_manifest.json` の **opponent** として解決できるが、
   `ActorPoolV1` の subject `behavior_kind` には入っていない。外部 agent をそのまま
   strong behavior policy として `ActorPoolV1` の subject on-policy path へ流す adapter は未実装。
2. 外部 agent の公開 API は `agent(obs)` の hard action だけで、logit/probability がない。
   したがって `dagger_v4.py` の `StepLogitPolicyFactory` relabel や、外部 agent 自身の
   behavior log-probability を必要とする AWR へ直接接続できない。
3. 既存の outcome residual / public-state value manifest と search target は、厳格な
   `research-only` target artifact であり、trainer、runtime policy、promotion authority を
   付与しない。deck search も既存部品は deck-only で、policy identity を持つ pair-aware
   loop ではない。

従って、UniformLegal、Rule v0-only、同一 teacher の full-model BC sweep、threshold/epoch
sweepを追加することは、Strong Asset へ移るための最短経路ではない。この監査では学習、
CABT、package、production runtime の変更を行わず、下記の再利用可能性と blocker だけを固定した。

## 監査範囲と非実施事項

対象は次の設計目標である。

```text
(BestKnown deck, BestKnown agent)
    -> strong behavior/on-policy trajectories
    -> outcome / return / public value / disagreement targets
    -> policy improvement
    -> fixed-policy deck mutation/local search
    -> broad meta arena
    -> BestKnown update only on a win
```

この文書でいう「strong agent」は、Rule v0 の安全 fallback ではなく、Strong Asset Census
と共通 arena で qualified された deck + agent pair である。deck と agent を別々に選択して
合成せず、性能 identity は常に `(deck SHA, policy SHA, archetype, source/permission)` の
組として扱う。

今回行ったのは既存ファイルの read-only 監査と既存 artifact の確認である。以下は行っていない。

- 新規学習、AWR/filtered BC 実装、DAgger adapter 実装
- CABT ゲーム、broad arena、submission package の実行
- Rule v0、UniformLegal、strict-disagreement、exact-hash residual の追加 sweep
- 既存 dirty file の整形、削除、巻き戻し、commit、push、Kaggle 提出

## 入力 identity と既存 strong-teacher artifact

監査時点の HEAD は `30cade0e5d349d6ea545f019fc411e9d53288f16`。worktree は既存差分で
dirty であり、今回の追加ファイル以外は変更していない。既存の permission と artifact から
確認できる範囲は次の通りである。これは Strong Asset Census の最終ランキングではなく、
Fine-Tuning Workstream E が再利用可能性を判断するための入力 inventory である。

| asset | 観測済み artifact | 監査上の意味 |
|---|---|---|
| `tomatomato_archaludon` | `runs/meta-specialist-teacher-records/archaludon-teacher-tomatomato-96/teacher_dataset_manifest.json` | 96/96 games、fault 0、5,146 records、16 opponents、seat 48/48、outcome 60 win / 36 loss。`training-local` permission があり、strong hard-teacher snapshot の既存入力になる |
| `lucifer19_battlecore` | `runs/meta-specialist-teacher-records/archaludon-teacher-lucifer19-48/teacher_dataset_manifest.json` | 48/48 games、fault 0、2,790 records、16 opponents、seat 32/16。`training-local` permission はあるが、seat imbalance を補う追加 collection が必要 |
| `plamen06_steel` | `docs/decisions/2026-08-05-archaludon-teacher-derivation.md` に derivation-qualified として記録 | teacher 候補としての permission 判断はあるが、この監査対象の sealed trajectory artifact は見つけていない |
| `public_archaludon_cinderace_r7` | `runs/meta-specialist-strength/teacher-archaludon-r7-fixed6-seed9700000-96.json` | 強度診断の候補だが、現 pool row は `local_eval_only` かつ `smoke_ok=false`。明示許諾・smoke 修復なしに teacher training へ使わない |
| 内製 `ozawa_*`, `nihei_alakazam` 等 | 上記 decision の追記 | 内製 derivation-qualified でも `opponents/` の agent code は submission bundle に入れない。pair の policy/deck identity と usage boundary を別々に保つ |

`tomatomato` と `lucifer19` の permission manifest は `source_kind=pooled_external_submission_agent`、
`allowed_usages=["training-local"]`、issuer は
`docs/decisions/2026-08-05-archaludon-teacher-derivation.md` である。これは「ローカル学習へ
使える」という狭い許可であり、外部 agent code の提出 bundle への同梱を意味しない。

既存の `tomatomato` 系結果については、V1 foundation BC の 2 seed が fixed-six で各 29/96、
一方、同じ teacher snapshot を V4 へ in-memory 変換した短期 prototype は fixed-six 合算
106/192、shadow-B 合算 51/96（Wave6 56/96）だったという記録がある。前者は V4 candidate
の初期値として topology が一致せず、後者は shadow-B で再現しなかった。従って、これらは
「collection と V4 変換が実行可能」という証拠であって、同じ teacher の full-model BC を
長時間 sweep する根拠ではない。

## 既存コードの再利用性

### 1. Strong external agent の behavior / teacher collection

`src/mage_ptcg/meta_specialist/collect_teacher_records_v1.py` が最も再利用しやすい。

- `build_teacher_permission_manifest_v1()` は `training-local` が明示され、decision reference
  がある場合だけ収集を許可する。permission をコードが推測して補完しない。
- `_TeacherRecordingAgentV1` は teacher の action を変更せず engine へ渡す read-only side
  channel で、actor-visible decision state、semantic selection、policy SHA、permission ID を
  record する。従って「teacher が実際に訪れた state distribution」を保存できる。
- episode 完了時に `win=1.0`、`draw=0.0`、`loss=-1.0` を `teacher.value_target` へ付ける。
  fault、unknown winner、複数選択を記録できない行は勝手に draw や有効 target へ変換せず、
  omission/unlabelled として分離する。
- matchup cap の `quality_weight` があり、単一相手が全 dataset を占有することを抑える。

これは外部 strong pair の first-stage on-policy collection / hard teacher として再利用可能で
ある。ただし、外部 teacher は `behavior.status=unavailable` になる。これは teacher action が
記録できないという意味ではなく、teacher の確率分布が公開されず behavior log-probability が
取れないという意味である。AWR の behavior probability と混同してはならない。

### 2. Neural/student の actor-pool on-policy trajectory

`src/mage_ptcg/meta_specialist/actor_pool_v1.py` と
`collect_trajectories_v1.py` は candidate 自身の on-policy collection に再利用できる。

- subject `behavior_kind` は現在 `rule_agent`、`neural_specialist`、
  `neural_specialist_v4` の三種類だけで、V4 checkpoint は file SHA と tensor-state SHA を
  両方固定する。
- opponent は registry-driven で、deck/policy hash、usage boundary、source provenance を
  fail-closed に検証する。external opponent は実際の `agent(obs)` を opponent factory として
 使う。
- completed fault-free game の各 transition は actor-visible model input、semantic prefix、
  behavior log-probability、opponent provenance を残し、terminal transition にのみ reward
  (`+1/0/-1`)、non-terminal に reward `0` と discount を付ける。現行 actor pool は critic
  value を持たず、`value` は placeholder `0.0` なので、return は reward/discount から再計算する。
- unknown outcome、engine fault、agent fault は usable trajectory にせず `faulted` として
  fail-closed になる。

従って「strong external agent が直接 subject」と「candidate が strong external agent を
相手にする」は現状別経路である。前者は teacher-record collector、後者は actor pool であり、
両者を同じ behavior-policy schema と呼ばない。

### 3. Outcome / return と AWR / filtered BC

return の最小部品は二系統ある。

1. `actor_pool_v1` の reward/discount と
   `src/mage_ptcg/policy_learning/r2d3/sequence.py` の `n_step_returns` は、candidate の
   実訪問 trajectory から Monte-Carlo / n-step return を作る土台になる。
2. `src/mage_ptcg/meta_specialist/cross_fitted_outcome_residual_v1.py` は
   `ActorTrajectoryTransitionV1` の episode return から、leave-fold-out global baseline、
   advantage、clipped signed weight を作る。opponent/seat を public model input に入れない。

しかし `cross_fitted_outcome_residual_v1` の manifest は明示的に
`training_permitted=false`、`promotion_authority=false`、`longrun_allowed=false` であり、
そのまま learner へ渡す trainer はない。外部 hard teacher record は
`ActorTrajectoryTransitionV1` ではなく behavior log-probability もないため、この residual
builder へ直接投入できない。

`src/mage_ptcg/meta_specialist/learner_awr_crr_v1.py` の `awr_weights_v1()` と
`crr_weights_v1()` は、finite advantage を指数重みまたは positive-only indicator へ変換する
小さな replay helper に過ぎない。critic、advantage fitting、V4 recurrent loss との wiring、
dataset filter、checkpoint identity は実装されていない。

一方 `recurrent_bc_v4.py` は complete-action NLL、GRU episode continuity、
`quality_weight`、研究用 outcome weighting を持つ。`scripts/run_v4_qualified_teacher_snapshot_bc.py`
には sealed teacher snapshot の train/development/test split と `--outcome-weighted` があり、
最初の短期 pilot の土台として最も近い。ただし既存 outcome weight は固定の win/draw/loss
重みであり、AWR ではない。最短順は次のようになる。

```text
sealed strong hard-teacher snapshot
  -> V4 actor-visible sequence conversion
  -> existing quality/outcome-weighted BC (bounded pilot)
  -> candidate own actor-pool trajectories with behavior log-probability
  -> cross-fitted public baseline / return
  -> positive-advantage filtered BC or AWR wiring
```

### 4. Strong-agent student-state DAgger

`src/mage_ptcg/meta_specialist/dagger_v4.py` は研究用として十分な骨格を持つ。

- recorded `ActorTrajectoryTransitionV1` の exact public `model_input` / `step_input` のみを
  teacher へ渡し、private engine state を再構成しない。
- `relabel_transition_v4()` は `StepLogitPolicyFactory` の `new_policy()` と semantic/STOP
  logits を要求し、teacher distribution、top-1 target、target mass、episode boundaryを
  `RecurrentBCSequenceV4` へ保存する。
- `strict_disagreement_metadata_v4()` は既に訪問した prefix chainだけを比較し、teacherが
  最初に違う actionを取った後の counterfactual stateを捏造しない。
- episode-level mix は deterministic で component collision を避ける。

ただし external submission agent の `opponent_pool_v1.load_opponent_agent_callable_v1()` は
hard `agent(obs)` actionしか返さず、logit/probabilityも `StepLogitPolicyFactory`も持たない。
従って `tomatomato` 等をそのまま DAgger teacher に渡せない。短期の選択肢は、(a) V4 neural
strong policy の logit factory がある場合にそれを teacher として使う、(b) raw actor-visible
observationを許可された範囲で保持して external hard-label relabel adapter を作る、の二つ。
(b) は state/action identity、複数選択、permission、episode splitを新規に契約化するため、
first pilot の blocker である。

### 5. Public-state value / search target

`src/mage_ptcg/meta_specialist/cross_fitted_public_state_value_v1.py` は actor-visible の
public bucketまたは固定56 scalar featureによる ridge baselineを fold外で fitし、return、
baseline、advantage、signed weightを記録する。opponent ID、seat、private payloadを
featureとして受け付けない点は Strong Asset の境界に合う。

既存 manifest は `training_permitted=false`、`promotion_authority=false`、
`longrun_allowed=false`。既存の public-state-model artifact は 74/69 episode、各約3.7k/3.9k
transitionだが、V4/student screen由来の research diagnosticであり、strong-teacherの
性能証明でも trainer入力でもない。candidate own on-policy trajectoryから再生成し、
OOD/fallback/faultを固定した後に AWR/filtered BCへ接続するのが安全である。

`src/mage_ptcg/meta_specialist/search_teacher_v1.py` の `SearchTargetV1` は、既に得られた
action values、standard errors、current policyを softmax/blendして target distributionを
作る純粋関数である。合法 action後の公開 state遷移、determinization、rollout engine、
visit/value ledgerは持たない。従って public-only shallow rollout/action-value は third
priorityだが、現状の「再利用」だけでは targetを生成できず、別実装が必要である。CABT native
searchは private/opaque payloadと過去のblock/segfaultリスクがあるため、first pilotへは
入れない。

### 6. Deck-policy alternating search

`src/mage_ptcg/meta_specialist/joint_optimization_v1.py` は次の再利用部品を持つ。

- `CoreSignatureV1` と `generate_core_preserving_mutation_v1()` による core-preserving deck
  mutation
- `RaceConditionsV1`、`DeckEntrantV1`、`run_deck_policy_race_v1()` による同一 conditions の
  deck race
- `run_successive_halving_tournament_v1()` による bounded candidate selection
- `seal_race_winner_v1()` / `branch_from_sealed_lock_v1()` による winner lineage の固定

ただし `DeckEntrantV1` は deck identity、archetype、arm、score、gamesを持つだけで policy
checkpoint SHAを持たない。実際の CABT search loopもなく、pair-aware invariantは
orchestration側で保持する必要がある。Strong Asset では mutationごとに同一 BestKnown policy
を固定し、`(deck SHA, policy SHA)` を一つの entrant identityとして broad arenaへ渡す。
winner deckを次の policy fine-tuneへ使った後、再び deck searchへ戻る二時間尺度を採る。
既存 `R2D3` の `MixtureManifest`、`AlternatingReplayPartitions`、PSRO runnerにも online
mixture/replay部品はあるが、V4 representation と strong pair identityがつながっていない。
R2D3/PPO/V-trace の大規模再開は first path ではなく、V4 outcome/AWRが失敗した後の fallback とする。

## Blocker と解決条件

| blocker | 現状 | 最短の解決条件 | 放置した場合 |
|---|---|---|---|
| Strong pairの固定不足 | Census/arenaの最終 BestKnown は別 workstreamで進行中 | deck SHA、policy SHA、archetype、source、permission、runtime、smokeをpair単位でfreeze | Rule v0や任意のArchaludonを誤ってbaselineにする |
| external strong subject未接続 | ActorPool subject enumはrule/neural/V4のみ | 初回は `collect_teacher_records_v1` を使い、外部hard teacherをteacher snapshotとして扱う。直接 subject behavior adapterは後段 | behavior policyとteacher recordを混同し、on-policy分布を再現できない |
| behavior probability欠落 | external teacher `behavior.status=unavailable` | 初回は hard/outcome BC。AWRはcandidate own behavior log-probability + public baselineから計算する。外部 teacherへの偽probabilityは禁止 | teacher actionに対するAWR/CRRが数学的に未定義 |
| V4 teacher snapshot変換がad-hoc | 既存 prototypeはin-memory conversion、公開converterなし | snapshot SHA、episode split、semantic ActionKey/STOP、empty selectionを固定した converter contractを作る | split leakage、STOP捏造、alias不一致で candidate比較不能 |
| outcome targetの小標本/偏り | 96局 artifactは存在するが pair/archetype被覆とmeta代表性は未確定 | BestKnown pairごとに両seat、16+ qualified opponents、cap付き96→192 games。unknown/faultは除外 | loss/winだけの単純BCを強さと誤認 |
| AWR trainer未接続 | `learner_awr_crr_v1.py` は重み関数のみ | V4 loss denominator、episode continuity、zero/positive filter、weight clipping、checkpoint provenanceを含む小さな trainer adapter + unit tests | `awr_weights_v1` を呼ぶだけで学習が変わらない、または過重み付け |
| external DAgger logit不足 | `dagger_v4` は StepLogitPolicyFactory 必須 | logitを提供できる内製/V4 strong teacherを使うか、hard-label raw observation adapterを別契約で実装 | external teacherを誤ってsoft targetとして扱う |
| public value/searchの権限 | value manifestは全て research-only、search targetはconstructorのみ | candidate own on-policyからpublic-only feature/returnを再生成し、training integrationを別に検証。searchはstate transition ledgerを先に作る | diagnostic targetをpromotion根拠へ昇格してしまう |
| deck-policy identity欠落 | joint optimizationはdeck-only entrant | orchestration manifestに固定 policy SHAを追加し、deck mutationごとに同一 policy/arena protocolを記録 | deckだけ勝ったか、policyだけ勝ったか分からない |
| broad evaluation未接続 | fixed-six/24/48は診断規模 | common 24–32+ opponents、384→768→1536 games、seat/opponent/deck/policy/fault層別、meta-weightを固定 | 小標本のseed反転でBestKnownを更新する |

## 最短実行順（実験条件付き）

### Step 0 — Strong Asset Census と BestKnown freeze

各 pair を分離せず一覧化する。最低限、`agent_id`、`deck_id`、archetype、policy SHA、deck
SHA、source/permission、runtime smoke、training-local/eval-only、既存評価局数を記録する。
common arenaで archetype BestKnown と GlobalBestKnownを決め、Rule v0は legality/fallback
baselineだけにする。Archaludonを自動優先しない。

### Step 1 — permission と snapshot の seal

まず既存 `tomatomato-96` artifact の manifest、policy SHA、pool manifest SHA、permission
manifest、records、episode splitを検証して再利用する。座席不足や不十分な opponent 被覆の
pairは `collect_teacher_records_v1` で追加収集する。目安は pairあたり 96→192 games、両seat、
16以上の qualified opponent、matchup cap、fault 0、unknown outcome 0 である。

teacher snapshot は episode単位で train/development/testを分離し、同一 episodeを跨がせない。
teacher target、empty selection、multi-select omission、semantic alias、public feature schemaを
artifactへ固定する。external agent codeやその `main.py` は submission bundleへコピーしない。

### Step 2 — V4 outcome/quality-weighted BC bounded pilot

sealed strong hard-teacher snapshotをV4 sequenceへ変換する。既存
`run_v4_qualified_teacher_snapshot_bc.py` / `recurrent_bc_v4.py` の topology、GRU continuity、
quality weight、fixed outcome weightingを再利用し、まず2 seed・固定epoch/updateで pilotする。
same-teacher full-model sweepではなく、「BestKnown pairから candidate policyを作れるか」を確認する。

必須ログ:

- source pair / teacher policy SHA / deck SHA / permission ID / snapshot SHA
- train/dev/test episode counts、records/steps、empty/omission、alias collision
- policy loss denominator、quality/outcome weight、seed、init checkpoint、checkpoint SHA
- fault、CPU/GPU runtime、candidateのbehavior identity

### Step 3 — broad arenaで直接比較

candidateを対応archetype BestKnownとGlobalBestKnownへ直接比較する。まず 384 games、改善方向
が両seat/複数opponentで揃った場合のみ768、最終確認で1536へ進む。primaryは
meta-weighted expected win/rating proxy、補助はmacro、opponent/archetype/seat別の worst-case、
fault 0、package closure、CPU latencyである。24/48局の数勝差だけで選ばない。

candidateがBestKnownを越えなければ、その arm を長時間化せず停止する。Rule v0より強いだけでは
昇格条件を満たさない。

### Step 4 — candidate own on-policy return と public value

Step 2の candidate を `ActorPoolV1` の V4 subject として、qualified strong poolを含む broad
mixtureへ投入する。ここで初めて behavior log-probability、reward/discount、episode returnを
正しく持つ `ActorTrajectoryTransitionV1` を得る。fault/unknownはfail-closedで除外する。

candidate own trajectoryから cross-fitted global/public-state baselineを作り、public feature、
OOD、fallback、fold、source SHAを固定する。opponent ID/seat/policy hashは samplingと報告だけに
使い、checkpoint inputへ入れない。

### Step 5 — filtered BC / AWR の最小接続

`advantage = cross_fitted_return - public_baseline` を fold外で作り、positive-only filtered BC
（CRR相当）または clipped `exp(advantage / temperature)`（AWR）を V4 complete-action lossへ
接続する。weight clipping、loss denominator、empty/forced STOP、episode hidden continuityを
テストで固定する。外部 teacherの unavailable behavior log-probabilityを使わない。

最初の pilotは temperature/max weightを一組だけ事前登録し、2 seed、同じ initial candidate、
同じ data snapshot、同じ optimizer budgetで比較する。勝率を見ながら threshold/temperatureを
反復しない。

### Step 6 — strong-agent student-state DAgger（条件付き）

teacherが V4 `StepLogitPolicyFactory` を提供できる場合だけ、candidate visited statesを
`relabel_transition_v4` で再ラベルし、episode-level mixへ進む。external hard-only teacherを
使う場合は別の raw observation/hard-label adapterを先に仕様化し、permission、ActionKey、
multi-select、STOP、splitを検証する。この adapterなしに external teacherを DAgger logits
teacher と呼ばない。

### Step 7 — deck search と policy fine-tune の交互更新

policy checkpoint SHAを固定したまま `generate_core_preserving_mutation_v1` で deck候補を作り、
同一 conditions の broad arenaで successive-halvingする。entrant identityは
`(deck SHA, policy SHA, arm, conditions)` とし、deckとagentを分離して勝者を解釈しない。
best deckを固定して Step 4–6の policy improvementへ戻り、再び deck searchへ進む。winnerを
`seal_race_winner_v1` で固定し、変更は新しい branchとして扱う。

### Step 8 — public-only shallow search（後段）と longrun gate

公開状態から合法 action後の state、bounded rollout、value/visit provenanceを生成する部品が
揃った場合だけ `search_teacher_v1` へ接続する。これがない現状では実 targetを捏造しない。
Step 2–7の短期 candidateが対応/GlobalBestKnownを broad/meta-weightedで越え、fault 0、
package可能、CPU制約内である場合だけ longrunを許可する。そうでなければ `LONGRUN_NOT_STARTED`。

## 必須 evaluator / data 条件

### Pair identity

```text
pair_id = sha256(archetype, deck_raw_sha256, policy_sha256,
                 source_lineage, permission_manifest_id)
```

上記は監査用の概念 identityであり、既存 schemaを変更したものではない。実装時は existing
manifest conventionを正にし、raw deck SHA、policy file/tensor SHA、source permissionを別欄で
残す。同一 deckで別 policy、同一 policyで別 deckを一つの pairと合算しない。

### Arena

- 24–32以上の qualified opponents（policy SHAとdeck SHAの重複を記録）
- 384 → 768 → 1536 games の段階評価
- 両 seat、training seed、opponent/archetype層を固定
- fault、illegal、timeout、package/runtimeを勝率と別に gate
- primary: meta-weighted expected win/rating proxy; macro averageは補助
- BestKnown更新は candidateが対応 BestKnown と GlobalBestKnownの比較で勝った場合だけ

### Training

- strong teacher collection: 96→192 games/pair、両 seat、16+ opponent、fault 0
- train/dev/testは episode disjoint。testは学習/threshold選択に使わない
- `quality_weight`、outcome target、behavior status、permission IDを分離
- external teacherの behavior distribution unavailable を確定値として保存し、偽の logprob を
  作らない
- AWR/CRRは candidate own behavior logprob と fold外 public baselineから計算
- loss-bearing transitionとcontext-only/forced STOPを denominatorで区別

## 推定見積り

以下は既存 runner の速度、既存 artifact の大きさ、GPU復旧済みという前提での作業見積りであり、
この監査で実行した時間ではない。

| 作業 | 目安 | 前提 / 変動要因 |
|---|---:|---|
| Census/permission/BestKnown manifest freeze | 0.5–2時間 | 対象 pair数、missing SHA、smoke再確認 |
| strong teacher collection 96–192 games/pair | 15–60分/pair | game length、CPU worker、opponent数、fault再試行 |
| snapshot seal / episode split / V4 conversion | 10–40分/pair | record count、alias/empty selection検証 |
| V4 outcome/quality-weighted 2-seed pilot | 20–90分 | GPU、updates、TBPTT、checkpoint I/O |
| broad arena 384 games | 数十分〜数時間 | opponent数、parallel workers、engine latency |
| filtered BC/AWR trainer wiring + tests | 2–8時間 | loss denominator、V4 sequence adapter、provenance契約 |
| AWR 2-seed bounded pilot | 20–90分 | candidate trajectory量、value baseline fit |
| V4-logit DAgger adapter/overlay | 1–4時間 | teacherがStepLogitを既に持つ場合 |
| external hard-label DAgger adapter | 0.5–2日以上 | raw observation capture、permission、ActionKey、multi-select |
| deck mutation + arena short round | 0.5–3時間/round | mutation数、broad games、successive halving |
| public-only rollout/search target | 数日以上 | legal state transition、determinization、rollout ledger未実装 |

最大の時間リスクはGPUではなく、BestKnown pairの権限/identity、外部 hard-only teacher と
V4 logit DAggerのschema差、broad arenaの評価局数である。これらを解かずに長時間学習を
開始しても、結果をStrong Asset超えの証拠へ変換できない。

## Fail-closed 条件と promotion 非許可

次のいずれかが起きたら、その candidate/armは次へ進めない。

- pairの deck/policy/source/permission SHAが一致しない
- `training-local` が明示されない external asset、または `local_eval_only` を submissionへ入れようとする
- episode split混在、unknown/fault outcomeの draw化、empty selectionの暗黙STOP化
- private opponent information、seat/opponent IDが model inputへ漏れる
- external hard teacherに存在しない probability/logitを補う
- forced/context-only transitionが loss denominatorへ入る
- fault、illegal、timeout、CPU runtime/package gateの失敗
- candidateが対応 BestKnown/GlobalBestKnownを broad/meta-weightedで越えない
- seed間の反転を aggregateだけで隠す、または24/48局の小標本だけでBestKnownを更新する

この監査の artifact と既存 research-only value/DAgger targetは promotion、Champion変更、
longrun、Kaggle提出の authorityを持たない。最終判断には別 workstream の Strong Asset Census、
common arena、package closure、現行 root submission pair の監査を併用する。

## 再現用 read-only 参照

```bash
git rev-parse HEAD
git status --short

# qualified teacher manifest と permission
sed -n '1,260p' runs/meta-specialist-teacher-records/archaludon-teacher-tomatomato-96/teacher_dataset_manifest.json
sed -n '1,260p' runs/meta-specialist-teacher-records/archaludon-teacher-lucifer19-48/teacher_dataset_manifest.json
sed -n '1,240p' docs/decisions/2026-08-05-archaludon-teacher-derivation.md

# 主な reusable boundary
rg -n "_BEHAVIOR_KINDS_V1|run_one_actor_game_v1|reward=|discount=" \
  src/mage_ptcg/meta_specialist/actor_pool_v1.py
rg -n "training-local|value_target|behavior.*unavailable|training_eligible" \
  src/mage_ptcg/meta_specialist/collect_teacher_records_v1.py
rg -n "awr_weights_v1|crr_weights_v1" \
  src/mage_ptcg/meta_specialist/learner_awr_crr_v1.py
rg -n "StepLogitPolicyFactory|relabel_transition_v4|research_only" \
  src/mage_ptcg/meta_specialist/dagger_v4.py
rg -n "training_permitted|promotion_authority|longrun_allowed" \
  src/mage_ptcg/meta_specialist/cross_fitted_*v1.py
rg -n "DeckEntrantV1|generate_core_preserving_mutation_v1|run_deck_policy_race_v1" \
  src/mage_ptcg/meta_specialist/joint_optimization_v1.py
```

## Workstream E handoff

### 直ちに使えるもの

1. permission済み external strong pair の hard teacher collection と sealed snapshot
2. candidate V4 actor-pool の actor-visible transition、reward/discount、behavior log-probability
3. existing V4 recurrent BC の episode continuity / quality / fixed outcome weighting
4. cross-fitted public-state value target の public-only feature/return計算
5. V4-logit teacherがある場合の student-state DAgger relabelと episode mix
6. core-preserving deck mutation、successive-halving、sealed winner lineage

### 最重要 blocker

最重要 blocker は「strong external agentを hard teacherとして collectionする path」と
「candidate own trajectoryから AWR/DAgger/valueを作る path」が、permission・behavior
probability・V4 sequence identityの三点でまだ統合されていないこと。したがって、まず
BestKnown pairの freezeと既存 permission済み snapshotのV4 bounded pilotを行い、broad arenaで
直接BestKnownを越えるかを確認する。その後に candidate own on-policy return/public valueを
使って filtered BC/AWRを接続するのが最短である。

### 次の判断

新規作業を開始する前に、親 workstream が確定した GlobalBestKnown / ArchetypeBestKnown と
その pair manifest を入力として、Step 1–3を実行する。candidateがBestKnownを越えない場合は
同じ teacher、threshold、epochを増やさず停止し、teacher quality、pair selection、deck mutation
または public-value targetの次の分岐へ移る。
