# Autonomous Strong Asset に対する R2D3／PSRO／Continuous League 経路監査（2026-08-13）

## 結論

**現時点の判定は `NO-GO: FIRST_PATH`、`CONDITIONAL_GO: FALLBACK_ROUTE` である。**

既存 R2D3／PSRO／Continuous League は、actor-visible public-state、single-action legality、immutable mixture、per-game durable collection、sealed replay、strict checkpoint resume、runtime policy publicationという、長時間 online value learning に必要な部品を既に持つ。Strong Asset の native BestKnown を起点にした将来の fallback として再利用する価値はある。

ただし現行 Autonomous Strong Asset 本線には、そのまま接続できない。理由は性能仮説ではなく、一次artifactで確認できる閉路不足である。

1. Autonomous manifestは102 pair全体について `behavior_allowed=false`、`submission_allowed=false` であり、top-level authorityもtraining/promotion/submissionをfalseに固定する。個別にはtomato/Luciferの2 rowだけが `training_allowed=true` だが、behavior permissionにはならず、schedule上もLuciferは`META_FINAL`である。よってR2D3が要求する実行可能 opponent catalog／behavior collectionを現在のmanifestだけから作れない。
2. 旧 R2D3 production の replay、checkpoint、source artifact、continuous-league output は現ホスト上に存在しない。記録された checkpoint hash や途中進捗は履歴証拠であってresume入力ではない。
3. R2D3 の学習対象は `trainable_single_action=true` のみで、multi-select decision は sequence の境界として除外される。runtime は top-k index を返せるが、Strong Asset 全分布に対する multi-select order／combination の学習・CABT長期検証がない。
4. R2D3 runtime policy は model-only local runtime bundle であり、現在の Kaggle submission entrypoint へ閉じる bundle builder・clean-room entrypoint・CPU time/size closure がない。
5. 既存の評価器は R2D3 candidate を評価できる一方、現行 `EvaluationBestKnown`（tomatomato native）の24-opponent common arenaと同一のnative対照を作る adapter/manifest API が未実装である。

従って、Lucifer hard BC の再実行や旧R2D3 artifactの推測resumeは行わない。まず Strong Asset の permission/behavior/pair-closure を満たす2〜3 archetypeを確定し、その後に下記の最小 bridge を実装・検証できた場合だけ、R2D3を「V4 outcome/AWR が改善を示さない場合の value-learning fallback」として開始する。

## 監査範囲と方法

read-only で以下を確認した。学習、CABT、artifact復旧、external fetch、checkpoint作成、package作成、commit、push、Kaggle提出は行っていない。

- `scripts/policy_learning/run_r2d3_multiseed_psro_performance.py`
- `scripts/policy_learning/run_submitted_r2d3_e2e.py`
- `src/mage_ptcg/policy_learning/r2d3/`
- `src/mage_ptcg/continuous_league/`
- `configs/meta_specialist/autonomous_meta_distribution_v1.json`
- `configs/meta_specialist/opponent_schedule_v1.json`
- `runs/final-sprint-autonomous/meta-distribution-v1/{manifest.json,meta_schedule.json}`
- 現存するtests、過去実験記録、continuous-league evidence

コード関係の一次探索には既存 `graphify-out/graph.json` を使用し、`run_r2d3_multiseed_psro_performance.py → MixtureManifest/Replay → R2D3CandidatePolicy → ContinuousLeague` の呼出関係を確認した。以下の現行ファイルSHA-256は監査時点のbyte identityである。

| artifact | SHA-256 |
|---|---|
| `scripts/policy_learning/run_r2d3_multiseed_psro_performance.py` | `1e1496b01de52bc880f3e23242c2389583a9f879667e9a1e5c6f5767cd27911f` |
| `scripts/policy_learning/run_submitted_r2d3_e2e.py` | `ec87a4a6020b2793c12d84e10b116611e04fc2fecdd2d5cc0f2d8eeb74ed924b` |
| `src/mage_ptcg/policy_learning/r2d3/candidate.py` | `9cc7f8847cfc7e800d95dc9e8de76ffca818783c4c77d83f41c90ff7a3a4bd9b` |
| `src/mage_ptcg/policy_learning/r2d3/semantic_state.py` | `4567cf63636ab441af527dd8962653275600913d55d4e3d0d56277a51e80d3cd` |
| `src/mage_ptcg/policy_learning/r2d3/online_collection.py` | `23de5658938a2f64b68d91dc8a59efd81be9f0974418619cef8e25ca822cd418` |
| `src/mage_ptcg/policy_learning/r2d3/checkpoint.py` | `bf606b4da9ad1c4bea4928c3913d3a914a3e331546d7cd205dcb108e9813c76d` |
| `src/mage_ptcg/continuous_league/collector.py` | `211b6c8d42a00346258f82b9cf09b143b8a2fefb9b9000b41f7b5db529cff2f4` |
| `src/mage_ptcg/continuous_league/cli.py` | `f8404bc7337a733b88e33e7786b424699c3037056690ca255968e1a92e3e9fc7` |
| `src/mage_ptcg/continuous_league/candidate_runtime.py` | `6530e7cc9f42f95c56288f054b7c21bda54c57e7033a6272f98739c60444dcaa` |
| `src/mage_ptcg/continuous_league/learner_service.py` | `7ca75d2e3bfa13fcafe2a4f333be330df365246834e86816d2145f7fce95d86e` |
| autonomous meta config | `222a32772a640c5362399d1839cc6ada743481670497784da849f8415ab12fde` |
| autonomous schedule config | `5584587236a808b1c1184adb92dfae08538f2de71b03c4fbd39c8ed410a6db0a` |
| actual autonomous manifest | `e430f1284e587e7f301f9e29abe377faad79ff5120a39c42b7b2f6a5223dd2ae` |
| actual autonomous schedule | `9db6e1d9ea3a6913f2080ac5ca4f08b748ed9fa1469cfd0a7a74d0253cb16b6a` |
| 3 external + 3 internal teacher derivation decision | `e64cc3f3e74bf5b96932438b4718af3079f56d1c7da64bc27524d02432e3a6fc` |

## 1. 現存checkpoint、replay、progress、性能証拠

### 現ホストで実在を確認できたもの

現ワークスペースには、R2D3 production/continuous league の実体は存在しない。次のパスはいずれも `MISSING` だった。

```text
/home/bfe-lab-ono/kaggle/handoff-artifacts/submitted-opponents-r2d3-psro-v1-20260728_180801
/home/bfe-lab-ono/kaggle/handoff-artifacts/submitted-r2d3-e2e-v1-20260728_184333
/home/bfe-lab-ono/kaggle/handoff-artifacts/r2d3-sequence-psro-production-v15
runs/continuous-league-external-v1
runs/policy-learning-gate5a
```

従って、旧model、optimizer、target、PER priority、RNG state、catalog snapshot、runtime snapshot、replay payloadをhash検証して再開することは不可能である。`load_checkpoint()` は population hash、replay manifest hash、strict state時はschema v3、training identity、replay priority stateを照合するため、別入力への"resume"は仕様上も拒否される。

### 履歴として確認できるもの（現在の再開入力ではない）

過去evidenceには以下が記録されている。

| 記録 | 観測事実 | 性能解釈 |
|---|---|---|
| `continuous-league-local-implementation-20260730.md` | CPU learner 200 updateの完了、checkpoint `r2d3-step-000000000202.pt`、SHA `e011df8b...`, RuntimePolicy ID `c39db72d...` | replayは4 sequenceだけ。学習進行smokeであり勝率根拠ではない。 |
| 同evidence | 旧productionは WSL再起動で消失し、durable progressは `psro-best-response-seed2` の1,875/6,250、fault 0 | 完走前に停止。自動再開なし。実体も現ホストにない。 |
| `continuous-replay-opponent-audit-20260731.md` | V15 frozen replayは5,000 games/33,810 sequences、SHA `ea07b3a5...`; 過去full trainingは37,500 updateに到達 | その後PSRO online collectionは未生成。最終性能比較ではない。 |
| 同audit | 補完後replayは42,084 sequence、SHA `49c83ee6...`、1 updateが有限 | 再生可能な実体なし。有限lossは強さを意味しない。 |
| `2026-07-30-r2d3-sequence-psro-v2-readiness.md` | 15-stage smokeはfault/illegal/timeout 0 | development 2/8=25%、PSRO BR 0/4。昇格水準ではなく、deck/final holdout未使用。 |

旧 R2D3 に「BestKnownを超えた最終勝率」「native tomato pairに対する共通arena勝率」「submission clean-room pass」を示す一次artifactは見つからなかった。最終性能は **未測定** と扱うべきである。

## 2. 停止原因とresume可能性

### 停止原因

記録されたR2D3 productionの停止は、性能による棄却ではなくWSL再起動によるprocess/input消失である。過去のv15ではfull checkpointが37,500 updateまで作られたが、controllerはdevelopment validation中に停止した。別のprogress記録はPSRO best response seed2の途中（1,875/6,250）を示す。これは「R2D3が失敗した」ではなく、「全ランのcheckpoint・input・holdout gateを現在再開できない」である。

### resumeの実装能力

R2D3には次の正しいresume能力がある。

- schema v3 checkpointがmodel、target、optimizer、scheduler、PyTorch/Python/NumPy RNG、PER priority、population/replay/training identityを保存する。
- replay inputはlearner output配下へcontent-verified copyされる。途中collectionのstaging game recordも同一requestなら未完了局だけを実行する。
- `run_r2d3_multiseed_psro_performance.py --resume` はstage artifactを再読込し、続行前にhash/identityを検証する。
- PSRO online collectionはcheckpointed replay/state pairを検証し、連続prefixだけを回復する。

ただし、この能力の前提artifactがすべて欠ける。したがって現時点の実際の選択肢は **strict resumeではなく、permission済みStrong Assetを入力とする新規epochの開始だけ** である。旧artifactをdoc上のSHAだけで再構成してはいけない。

## 3. actor-visible、legality、hidden state、multi-select契約

### actor-visible / hidden state

`encode_public_state()` は `build_decision_state(...).actor_view.public_state` を入力にし、`opponent_hand`、`opponent_deck`、`opponent_prizes`、deck order、RNG、hidden keyを再帰的に拒否する。state=128、legal action=64であり、actionにはStable ActionKey由来のdigest、type、card/zone/phase/orderの公開semantic featureを使う。この境界はStrong Asset本線と整合する。

`R2D3CandidatePolicy` はゲーム開始時にhidden stateを`None`へresetし、各合法decisionのQ出力から次hidden stateを保持する。RuntimePolicy factoryは各CABT game/seatにつき新規candidateを作るため、ゲーム間hidden stateを共有しない。これは必要条件を満たす。

### legal action

candidateは、その時点の `state.legal_actions` だけをencodeし、legal maskは全候補でtrue、greedy tie breakは最小indexで決定する。範囲外indexを選ぶ経路はない。Single-action traceは `selected_action` とlegal actionsを保存し、online collectionはそのtraceをR2D3 transitionへ変換する。

### multi-selectの未閉路

この経路はmulti-selectを完全には学習しない。

- runtimeは `maxCount > 1` の場合、上位Qの`maxCount`個を返し得る。`minCount==0` または`maxCount==0`は空配列を返す。
- traceには`trainable_single_action = (count == 1)`が記録される。
- Continuous League collectorとPSRO online collectorは`trainable_single_action=false`を境界としてsegmentを分割し、datasetへ入れない。
- したがってmulti-select decisionの順序・組合せ・後続dependencyについてQを学習していない。top-k各要素が個別に合法でも、組合せ/順序のfull legalityをStrong Asset metaで証明する試験はない。

この制約は致命的というより、R2D3を本線にする前に解くべき明示的なNO-GO条件である。multi-select率、skipped decision率、candidate fault/illegal/timeoutを各96→384→768→1536 blockで記録し、single-action-onlyルートの性能改善と分けなければならない。

## 4. Autonomous meta manifest/scheduleとの接続

### 現在は接続不能な理由

`autonomous_meta_distribution_v1.json` は明示的に `research_only=true`、`training_authority=false`、`promotion_authority=false`、`submission_authority=false`、`longrun_allowed=false` とする。actual manifestは102 rowsで、manifest policy自体が「training permissionがある場合だけcollection」と定義している。個別rowの`training_allowed=true`はtomato/Luciferの2件だけで、`behavior_allowed=true`と`submission_allowed=true`はいずれも0件である。Luciferは`META_FINAL`であり、permission-filtered scheduleの実行対象にもできない。すなわち現manifestは、実験上のlocal rankingには使えるが、R2D3 behavior/on-policy curriculumの直接入力ではない。

一方でR2D3 `MixtureManifest` は、各memberに `opponent_policy_id`, probability, policy hash, source lineage, family, kind を要求し、Continuous League collectorは`opponent_policy_id`がcatalogの実行可能な`opponent_instance_id`であることを要求する。現meta manifestの`opponent_id`はlocal evaluation pool IDであり、直接にはcatalog instance IDでも実行permissionでもない。

### 最小 bridge API（未実装）

以下は実装候補であり、現在使えるAPIではない。橋を足すなら、この一つのcontent-addressed adapterに限定する。

```text
build_r2d3_training_population_from_autonomous_manifest_v1(
  autonomous_manifest_path,
  schedule_path,
  catalog_snapshot,
  allowed_opponent_ids,
  candidate_pair_identity,
  split="META_TRAIN_PERMISSION_FILTERED",
) -> {
  population_epoch.json,
  r2d3_mixture_manifest.json,
  quota_map.json,
  permission_audit.json,
}
```

必須fail-closed条件は次の通り。

1. `allowed_opponent_ids`はcandidate自身、META_DEV、META_FINALを含まない。META_FINALは最終評価まで読まない。
2. 各rowで`training_allowed && behavior_allowed`がtrueであり、catalog snapshotに同一 `(policy SHA, deck SHA, source lineage)` の`TRAINING_ACTIVE/RESERVE` instanceがある。
3. action/deck runtimeをhash検証して実行でき、source permissionとpackage利用権限を別々に証明する。
4. schedule countを両seat偶数quotaへ変換し、probabilityとquotaが同じfrozen manifestにbindされる。
5. candidate pairは`(deck SHA, policy SHA, runtime config SHA)`としてbindされ、deck mutation後は新epoch/replayを作る。旧optimizer/replayを暗黙に流用しない。

今は条件2を満たす **behavior-permission済み** rowがゼロ件なので、このAPIを作っても空mixtureでfail-closedになる。`training_allowed=true` をbehavior permissionと読み替えたり、research-only manifestを無断で`TRAINING_ACTIVE`へ昇格してはならない。

### 旧artifact再開ではない最短のsource/θ0経路

ユーザー判断の `docs/decisions/2026-08-05-archaludon-teacher-derivation.md` は、外部3 teacher（tomato/Lucifer/plamen）とチーム内製3 teacher（Grimmsnarl/Rocket/Alakazam）の**挙動から導出された自前weight**を、native codeを同梱しない提出候補の初期値 θ0 として扱うことを許可している。これはR2D3 routeでも利用可能なprovenance上の出発点である。ただし許可対象は導出weightであり、teacher native source/deck、pool全体のbehavior、または外部deckの提出利用ではない。

したがって最短路は次のいずれかであり、旧production artifactの再開ではない。

| route | 出発物 | 利点 | 必須の新規evidence |
|---|---|---|---|
| A. current source artifact rebuild + R2D3 cold start | permission済みcatalog、self-owned/bundle-allowed deck、空model | 旧checkpoint/replayの欠落に依存しない | 新しいcatalog/mixture、on-policy collection権限、new replay/epoch、96局common control。 |
| B. current source artifact rebuild + R2D3-compatible θ0 | 許可済みexternal3+internal3のpublic-state/action recordsを用いて、R2D3 model schemaに蒸留した**自前**bootstrap checkpoint | 強teacher行動を初期表現へ反映できる | teacher dataset/permission manifest、decision ref、R2D3 architecture/state/action schema parity、teacher source非同梱のpackage scan。 |
| C. θ0-only student route（R2D3なし） | 既存/新規のself-trained portable student | 提出bundleの技術閉路が最も短い可能性 | native BestKnown対照の性能、self-owned deck、clean-room package。 |

`ContinuousLearner --bootstrap-checkpoint` はR2D3 model config/action schema/deck hashが一致するBootstrap bundleを要求する。V4 weightや任意teacher modelをそのままR2D3へloadするAPIはないため、Bでは重みの無根拠なtensorコピーではなくR2D3-compatible distillation/bootstrapを新規に行う。A/Bとも、candidate自身が収集したon-policy actionはexternal teacher actionではないが、相手poolを実行して学習データを作る権限と、current manifestを超えるlongrun authorityは別途manifestで明示する必要がある。

## 5. submission bundle closure

R2D3にはlocal runtime closureの一部がある。`publish_checkpoint()` はtraining checkpointからmodel weights、model config、60-card deck、semantic encoder version、legal mask/recurrent/tie break契約をcontent-addressed `RuntimePolicy` directoryへ公開する。`load_runtime_policy()` はweights hash、deck hash、config、greedy Q contractを検証する。

しかしこれは提出bundleではない。

- current root `main.py` はR2D3 weights/runtime manifestを探さず、R2D3 actorを構成しない。
- `scripts/build_performance_submission_bundle_v1.py` が包むcurrent submission assetとR2D3 RuntimePolicyとの結合は存在しない。
- 現R2D3 runtimeはPyTorchをimportしてCPU inferenceする。Kaggle submission image上のtorch availability、weights/package size、cold-start、per-decision latency、main entrypoint import closureを測ったartifactがない。
- existing `run_submitted_r2d3_e2e.py` はGPU E2E/PSRO/legality gateでありsubmission package builderではない。script名の`submitted`はsubmitted-opponent populationを指し、候補をKaggle形式へbundleすることを証明しない。

よって `SubmissionEligibleBestKnown` をR2D3へ置換する根拠はない。R2D3は現状 `local runtime candidate only` であり、submission closureの前にChampion/default/main.pyを変更してはいけない。ただしexternal3+internal3から導出した**自前**R2D3-compatible θ0/weight自体は、判断記録上はsubmission candidateに進められる。必要なのはteacher code/deckを含めないself-owned policy/deck/runtime/packageの閉路である。

## 6. native BestKnown 対照へ繋ぐ評価計画

### 現在使える基準

common 24-opponent arenaで確認されたnative controlは、tomatomato_archaludon 1107/1536 = 72.0703%、fault 0である。これはR2D3の過去Rule-v0/old submitted-split validationと同じ基準ではない。R2D3候補がこの値を超えるかを言うには、同一pool、同一席数、同一block数、同一fault集計を使う必要がある。

### bridge完成後の具体的な評価コマンド

以下は**現状では実行不可**のreproducible target commandである。`build-r2d3-autonomous-bridge` と`run-r2d3-common-protocol`は新規bridgeにより提供されるべきcommand名で、既存CLIを装ってはいない。

```bash
# 0. permission/candidate/package closureを検証して初めて新epochを構築する。
PYTHONPATH=.:src .venv/bin/python scripts/build_r2d3_autonomous_bridge_v1.py \
  --meta-manifest runs/final-sprint-autonomous/meta-distribution-v1/manifest.json \
  --meta-schedule runs/final-sprint-autonomous/meta-distribution-v1/meta_schedule.json \
  --candidate-pair <native-bestknown-deck-policy-runtime-manifest> \
  --catalog <qualified-training-catalog.json> \
  --allowed-opponent <permission-qualified-META_TRAIN-only> \
  --output runs/final-sprint-autonomous/r2d3-bridge-a

# 1. existing Continuous League APIでon-policy dataをbalanced two-seat quotaで収集する。
PYTHONPATH=.:src .venv/bin/python scripts/continuous_league.py collect \
  --runtime <published-native-or-r2d3-runtime> \
  --catalog runs/final-sprint-autonomous/r2d3-bridge-a/catalog.json \
  --mixture runs/final-sprint-autonomous/r2d3-bridge-a/r2d3_mixture_manifest.json \
  --deck <candidate-deck.csv> \
  --population-epoch-id <bridge-population-epoch-id> \
  --subject-deck-id <candidate-deck-id> \
  --opponent-episodes <each-qualified-opponent>=<even-count> \
  --execution-block policy-iteration-001 \
  --output runs/final-sprint-autonomous/r2d3-bridge-a/collection

# 2. seal → learn → publish。strict resume時は同replay/epoch/identityを必須にする。
PYTHONPATH=.:src .venv/bin/python scripts/continuous_league.py seal \
  --chunk-manifest <collection-chunk-manifest> \
  --population-epoch-id <bridge-population-epoch-id> \
  --output runs/final-sprint-autonomous/r2d3-bridge-a/replays

PYTHONPATH=.:src .venv/bin/python scripts/continuous_league.py learn \
  --replay-manifest <sealed-manifest> \
  --population-epoch-id <bridge-population-epoch-id> \
  --deck <candidate-deck.csv> \
  --config <r2d3-config.json> \
  --max-replay-passes <predeclared-budget> \
  --output runs/final-sprint-autonomous/r2d3-bridge-a/learner

PYTHONPATH=.:src .venv/bin/python scripts/continuous_league.py publish \
  --checkpoint <strict-checkpoint> --deck <candidate-deck.csv> \
  --config <r2d3-config.json> \
  --output runs/final-sprint-autonomous/r2d3-bridge-a/published

# 3. Candidateとnative tomato controlを同一common 24-opponent protocolへ投入する。
PYTHONPATH=.:src .venv/bin/python scripts/run_r2d3_common_protocol_v1.py \
  --runtime <published-runtime-dir> \
  --native-control tomatomato_archaludon \
  --reference-config configs/meta_specialist/performance_first_broad_pool_v1.json \
  --games-per-opponent-seat 2 --blocks 1 --base-seed 9800000 \
  --workers <measured-safe-workers> \
  --output runs/final-sprint-autonomous/r2d3-bridge-a/eval-096
```

最終行の新規runnerが必要な理由は、existing `scripts/run_native_policy_candidate_pilot_v1.py` はnative `main.py:agent` 専用で、R2D3 RuntimePolicy factoryをloadできないためである。Continuous League `evaluate` はR2D3 runtimeをロードできるが、現common 24-poolのlocal-eval opponent rowsをpermissioned catalogへ変換できず、同一arena対照を作れない。

### 96→384→768→1536のgate

| stage | 24 opponents × 2 seats | 目的 | 必須判定 |
|---|---:|---|---|
| 96 | 2 repetitions/opponent-seat | smokeではなく最初のblock | fault/illegal/timeout=0、multi-select skip率とseat別結果を保存。小差で昇格/棄却しない。 |
| 384 | 8 repetitions/opponent-seat | 4 independent 96-block相当 | native tomatoとcandidateを同じblock/scheduleで比較。block方向、CI、faultを確認。 |
| 768 | 16 repetitions/opponent-seat | 2段階確認 | 384の優位が再現する候補だけ。native BestKnownを対照に固定。 |
| 1536 | 32 repetitions/opponent-seat | promotion候補の確証 | tomato native 72.0703%/1536を上回り、seat/seed/block/fault基準を満たすこと。 |

過去common evaluatorの実測は個々のnative opponentの速度とworker設定に強く依存する。R2D3 pathには同一hardware・同一poolでの測定がないため、正確なETAを断定しない。過去のR2D3 CABT recordは12 workersで約0.294 games/sという一時期の値を示すが、old worker/import configurationであり現arenaの所要時間根拠に使えない。bridge完成後は96局をまず実測し、`wall_seconds / DONE games` をartifactに保存して、384/768/1536を線形推定する。収集/学習時間は別にreportする。

## 7. 過去に否定・停止したarmとの違い

R2D3 fallbackを再考する根拠は「もう一度BCを回す」ことではない。過去に止めた同型との違いを明示する。

| 既存arm | なぜ停止/不採用か | R2D3 fallbackが異なるために必要な条件 |
|---|---|---|
| Lucifer hard-label + outcome weighted BC | 384局で両seedがWave6未満。teacher actionを一様正解として重くするだけではBestKnown改善を示さなかった。 | behavior actionの模倣ではなく、on-policy transition、reward/discount、target Q、PER、online mixtureを使う。ただしpermission済みbehavior/dataが必須。 |
| V4 outcome/AWR / filtered BC | Strong Asset teacher/behavior permissionとpair closureが未成立。 | R2D3はteacher logitを要求しないが、candidate自身の合法on-policy行動を収集できるcatalogが必要。 |
| Rule v0 / Wave6対照 | 最終問いがnative BestKnownではなく旧baselineになる。 | 全評価をtomato native等の`EvaluationBestKnown`とcommon 24-poolで行う。Rule v0はsubmission fallbackのみ。 |
| 過去R2D3 smoke | 25%/0%の小sample、設計/合法性の証拠であり性能改善ではない。 | 96→384→768→1536、native control、block/seat/fault/multi-select accountingを必須にする。 |
| 旧R2D3 production resume | input artifactが失われ、semantic/split/identityの混用リスクがある。 | strict resumeは完全なcurrent checkpoint+replay+population+identityが揃う場合だけ。揃わなければ新epoch。 |

## Go/No-Go と再開条件

### 現在の判定

```text
R2D3 as Autonomous Strong Asset first fine-tuning path: NO-GO
R2D3 code/replay/PSRO as later value-learning fallback: CONDITIONAL GO
R2D3 strict resume of historical production: NO-GO
R2D3 submission candidate / SubmissionEligibleBestKnown: NO-GO
```

### Goに必要な最小evidence

1. top 2〜3 candidate archetypeについて、native pairのpolicy/deck/runtime byte、behavior利用許可、training許可、submission許可を別々に明示したpermission ledger。θ0 routeではexternal3+internal3のdecision ref、teacher policy SHA、teacher dataset permission manifestを追加する。
2. META_TRAIN onlyで、少なくとも2以上の実行可能permissioned opponentからなるhash-bound R2D3 catalog/mixture。META_DEV/META_FINALは除外する。
3. native BestKnown deckをR2D3 RuntimePolicy deckとしてbindできること、またはcandidate pairのdeck changeを新epochにするdeck-policy identity contract。
4. multi-selectについて、(a)完全action/sequence表現を実装して検証する、または(b)skip rateと単一選択のみの適用境界を十分大きい96-blockで測り、性能の解釈可能性を確認する。
5. common 24-opponent arenaでR2D3 candidateとtomatomato nativeを同一96-blockから評価できるrunner。最初の96局でfault/illegal/timeout=0。
6. published runtimeのclean-room submission bundle proof：main entrypoint、weights、dependencies、60-card deck、CPU timing、package size、local importを検証する。これができるまでSubmissionEligibleにはならない。

上記を満たした時点で初めて、R2D3はStrong Asset 本線のAWR/filtered BC失敗後に開始する価値がある。それ以前は、実在するnative BestKnownのdirect ranking、permission/package audit、common arena高速化、deck mutationの再現性確認を優先する。

## 参照一次artifact

- `docs/evidence/continuous-league-local-implementation-20260730.md` — SHA `dd1ab32c47ddc9031d39de47028676a030273c0d3b351fefa286431324b19491`
- `docs/evidence/continuous-replay-opponent-audit-20260731.md` — SHA `681dad725d2a794917695deb9608176b3122195b0ac537aa7e16453e40b1f6c9`
- `experiments/2026-07-30-r2d3-sequence-psro-v2-readiness.md` — historical implementation/readiness record
- `experiments/2026-07-30-r2d3-learner-throughput-and-final-holdout-gate.md` — historical throughput/gate record
- `runs/final-sprint-autonomous/meta-distribution-v1/manifest.json` — current autonomous permission/source boundary
- `docs/evidence/autonomous-bestknown-classification-v3-20260813.md` — current BestKnown classification

## 監査時の検証

コードは変更していない。次のfocused regressionは現作業treeで実行し、`102 passed, 1 skipped in 20.55s`だった。

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python -m pytest -q -s \
  tests/test_submitted_opponents_r2d3.py \
  tests/test_continuous_league_contracts.py \
  tests/test_continuous_league_collector.py \
  tests/test_continuous_league_learning.py \
  tests/test_continuous_league_cabt.py \
  tests/test_continuous_league_cli.py \
  tests/test_continuous_league_coverage.py \
  tests/test_continuous_league_cycle.py \
  tests/test_continuous_league_evaluation_history.py \
  tests/test_continuous_league_source_intake.py
```

通常captureを有効にした同じpytest起動は、collection前にpytestのcapture temporary fileが消えたため`FileNotFoundError`で中断した。これはR2D3/Continuous League test assertionのfailではなく、共有環境のtemporary-file raceと判断し、コード変更はしていない。captureを使わない`-s`の同一suiteは上記の通り通過した。`python scripts/docs/validate_docs.py` は `Validated 13 canonical documents.`、`git diff --check -- docs/evidence/autonomous-r2d3-submission-route-audit-20260813.md` はPASSした。
