# Meta Specialist v3 — Luna Max計画 整合性監査・結果再分析

監査日: 2026-08-08 JST  
対象worktree: `/home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle-worktrees/meta-specialist-canonical`  
branch: `feature/meta-specialist-canonical`  
HEAD: `a4e6475255ff7ac56469f87cfd0ca6214de749af`  
監査基準: `/home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle/docs/META_SPECIALIST_V3_LUNA_MAX_IMPLEMENTATION_EXPERIMENT_PLAN.md`  
参照した独立レビュー: `/home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle/docs/plan/META_SPECIALIST_CANONICAL_IMPLEMENTATION_REPORT.md`

---

## 1. 結論

今回の作業は、計画の**設計方向**とは概ね一致している。representation v3、outcome critic、trajectory schema、PPO/V-trace/AWR-CRR、opponent schedule、評価統計、search/DAgger、manifestという予定された名前と責務に対応するコードは作られている。hidden identity禁止、base behavior probability保存、episode-balanced loss、zero-init outcome head、consume-once queueなど、個々の契約にも有用な実装がある。

しかし、計画書が要求した意味での「実装・実験・評価の完遂」には達していない。より厳密には、現在の状態は次のとおりである。

> **v3研究パイプラインの部品・unit contract・bounded smokeを作った段階であり、実CABTの収集・学習・評価経路へ統合されたv3システムではない。**

最重要の不整合は以下である。

1. Gate 0.3とGate 1が不通過なのに、原因解消より先にPhase 2–9の孤立した部品とsynthetic smokeへ進んだ。計画書の「Gate不通過のまま後段へ進まない」「Phaseを飛ばさない」と整合しない。
2. v3 model、critic、trajectory schema、learners、fault diagnostics、opponent schedule、DAggerは、実際のcollector/trainer/CLIから呼ばれていない。`rg`で確認すると、主な利用箇所は専用smoke scriptとunit testだけである。
3. representation benchmarkの`R2-negative-control`は現行v2 modelではなく、card embeddingをmean-poolする簡略モデルである。さらに教師action全体ではなく`option_type`分類を学習し、transition suffixでtrain/validationを分けている。従って計画のR2/R3比較にはなっていない。
4. BC splitは`episode_id_hash:near_duplicate_id`という文字列をgroup keyにしているため、同じepisodeの別transitionをtrain/validationへ分割する。rocket 128 usable recordsで実測すると、train 102 / validation 26の両方に同じepisodeが存在した。formal validation NLLとして使用できない。
5. PPOはrecurrent PPO learnerではなく、1次元tensorに対するloss fragmentである。V-traceはtarget関数とin-memory queue、AWR/CRRはweight関数だけで、実optimizer・sequence batching・fresh rollout・critic updateへ未接続である。
6. Alakazam fault instrumentation/retryはstandalone helperのみでcollectorへ未配線であり、fault原因も特定されていない。
7. Phase 7–9 smokeは比較実験ではない。L1/L2/L3の3名称すべてが同じ`_learner_contract_smoke`を呼び、同じ診断値を出している。O2は`mirror-heavy`と名付けられているが実体は4 lane一様分布である。DAggerはexact state hash重複除去でありnear-duplicate dedupではない。
8. 最終レポートの`DO NOT PROMOTE`判断は正しいが、「IMPLEMENTED + SMOKE」「paired protocol implemented」「fault instrumentation implemented」など、一部の表現は統合済みであるかのように読み取れ、実態より強い。

従って最終判定は次のとおりである。

```text
DO_NOT_PROMOTE_META_SPECIALIST_V3
PLAN_NOT_COMPLETE
INTEGRATION_AND_VALID_EXPERIMENTS_REQUIRED
```

GPU利用不能は大規模実験を止める理由にはなるが、episode splitの誤り、孤立モジュール、PPO lossの意味不整合、collectorへの未配線を説明する理由にはならない。これらはGPUなしでも修正・検証できる。

---

## 2. 監査方法

以下を相互照合した。

- Luna Max計画書のMaster checklist、各Task、Gate、Definition of done
- v3追加module、script、test
- v1既存経路に対する変更差分
- `runs/meta-specialist-v3`のJSON/CSV/manifest
- Phase 0、Phase 1、BC、最終レポート
- Claude独立レビューに記録された過去のdead-code/split退化問題
- import/call site検索による実経路への配線確認
- rocket teacher records 128 usable examplesを使ったsplit再現

コードの追加修正は行わず、今回は監査と結果分析だけを行った。

### 2.1 dirty worktreeの帰属

現在のworktreeには、Luna Max作業以前から存在した変更と、この作業で追加された変更が混在している。Phase 0 preflightの`runs/meta-specialist-v3/preflight/environment.txt`と当時の報告を基準に照合した結果、`deck.csv`、`opponents/pool_manifest.json`、medal opponent群、leaderboard report、`make_medal_opponents.py`等はpreflight時点ですでに存在していた。これらをLuna Max計画から逸脱して今回新たに変更したものとは認定しない。

一方、0 byteの`tests/meta_specialist/__init__.py`はpreflightのファイル一覧に存在せず、今回のtest namespace回避中に追加された計画外ファイルである。import shadowingの回避自体には意味があり得るが、計画された成果物ではなく、package discoveryへ影響し得るrepo mutationなので、正式に残すか削除するかを独立に判断すべきである。今回の監査では削除していない。

---

## 3. 計画とのTask-by-Task整合性

状態の意味:

- **PASS**: 計画の目的と実挙動が一致
- **PARTIAL**: 部品または限定検証はあるが、要求全体を満たさない
- **NOT DONE**: 計画が要求する実運用・実験経路がない
- **DEVIATION**: 実施内容が計画の比較対象・意味と異なる

| Task / Gate | 状態 | 監査結果 |
|---|---|---|
| 0.1 worktree census | PASS | branch、HEAD、dirty state、tracked diff hashを保存し、既存変更を破壊していない。 |
| 0.2 baseline focused tests | PASS | 指定3 suiteを実行し、74/37/19 passed（2 skipped）を記録。後の全suiteも1481 passed。 |
| 0.3 evaluation reproducibility | DEVIATION / NOT PASSED | 計画は2 lane各384局を2回要求。実施は各run 8局。fresh同士でも0/7一致で、再現性は確立していない。`conditional pass`としたが、計画の「再現性確立前にlearner比較へ進まない」には反する。 |
| 0.4 RNG/lifecycle audit | PARTIAL | game-local sampling seedとfresh process標準化は有用。native engine RNG、global cache、worker lifecycleの根因は未解決。 |
| 1.1 relation/invariance tests | PARTIAL | v3のactive/bench、host、owner、permutation、hidden identity、action orderは検査。計画が要求した「実v2がnegative controlで失敗する証拠」はない。candidate source/target testはstate encodingなしでlocal entity-id fallbackを使い、実forwardのrelation bindingを直接検証していない。 |
| 1.2 typed entity schema | PARTIAL | schemaとhidden-card禁止は整合。adapterのendpoint解決が同owner・同cardの複数Pokemonでzoneを十分使わず最初の一致を選ぶため、active/benchに同一cardがあるとsource/targetを誤接続し得る。 |
| 1.3 encoder candidates | PARTIAL / DEVIATION | R3-A/R3-Bは存在。R3-Aはown active/own bench/opponent active/opponent benchを別poolにせずzone 1–5でpoolする。R3-Bはhost relation以外のpairwise relation biasを持たず、FFNは計画の512でなく384。 |
| 1.4 candidate encoder | PARTIAL | action type、source、target、context、step、argsは存在。stable action ID hash embeddingは計画外で、IDはalignment keyではなく学習featureになっている。multi-selection canonicalization、selected mask、duplicate illegal除外は未実装。 |
| 1.5 recurrent memory | PARTIAL | GRU-256/1 layerとepisode resetは存在。ただしBCは全transitionで`episode_start=True`を渡すためmemoryを学習しない。burn-in、sequence order、packed recurrent batchingはない。 |
| 1.6 representation benchmark / Gate 1 | DEVIATION / NOT PASSED | R2は現行v2ではなく簡略negative control。targetはlegal action candidateでなくaction type。splitはepisode/component単位でなくtransition suffix。rare-action recall、action-type NLL、VRAM、CPU preprocess、3 seedsがない。Gate 1を判定できない。 |
| 2.1 remove seed-conditioned critic | PARTIAL |孤立したv3 criticはseed非依存。一方、実学習経路のv1 modelには`opponent_instance_id` hash embeddingが残り、v3 criticは本番経路に未接続。 |
| 2.2 outcome critic | PASS as isolated component | zero-init 3-class outcome head、bounded value、seed provenance不変、checkpoint round-trip testは実装。SpecialistModelV3/learnerへの統合は別途未完。 |
| 2.3 MC critic warm-up | PARTIAL | episode mean CEは実装。実episodeのeventual outcomeではなくrandom synthetic features/labelsでのみ実行。 |
| 2.4 calibration / Gate 2 | NOT DONE | overall CE/Brier/ECE/range/correlation関数はあるが、seat、opponent-family、trajectory-position strataがない。実データcalibrationなし。 |
| 2.5 stable conditioning ablation | PARTIAL / TOY | C0/C1/C2はあるが、64未満unknown bucket・128以上dedicatedという閾値規則がない。toy generatorがfamilyからlabelを決定するためC1改善は構成上自明。 |
| 3.1 teacher strength revalidation | NOT DONE | manifestはrecord/episode/teacher IDを数えるだけ。current-pool win rate、deck fingerprint、policy implementation/source、usage boundary、fault rateを測らない。Archaludon teacherの厳密再評価もない。 |
| 3.2 dataset split | DEVIATION / INVALID | connected componentではない。`episode:near`の組をgroupにするためepisodeもnear-duplicateも推移的に保持されない。rocket smokeでepisode leakageを実測。 |
| 3.3 teacher weighting | NOT DONE | loaderは既存`quality_weight`を読むだけで、計画の1.0/0.7/0.4/0.2/0.0 policyを生成しない。4 lane manifestのmin/maxは全て1.0。 |
| 3.4 full BC | NOT DONE / SMOKE | rocket 128例、1 seed、3 epochs、hidden 32/embedding 16のみ。top3、rare action、action type、entropy、gradient norm、throughput、VRAMなし。GRU memory未学習。 |
| 3.5 seal θ0 / Gate 3 | NOT DONE | state_dict hashは計算したがcheckpoint fileを保存していない。dataset/teacher/critic/model configを結んだformal manifestもない。 |
| 4.1 full behavior distribution | PARTIAL / UNWIRED | schemaはbase logits/log-probsとchosen behavior log-probを検証するが、actual collectorはTrajectoryDecisionV3を生成していない。 |
| 4.2 episode-balanced batching | NOT DONE | helperは「episode」と称する1 decision lossを平均するだけ。sequence/transition群をepisode単位に平均するlearner batchではない。BC splitもepisodeを保持しない。 |
| 4.3 exact policy drift | PARTIAL | forward/reverse KL、TV、flip、margin、entropyは実装。action-type KLなし、実learner runへの配線なし。 |
| 4.4 V-trace kernel | PARTIAL |積`gamma*c`列は計算。d40、terminal-to-opening、position-binned cがない。`effective_horizon_90pct`は90% mass horizonではなく単にthreshold以上の要素数。`median_w_*`も複数開始位置のmedianではない。 |
| 4.5 advantage diagnostics | PARTIAL | mean/std、median/MAD、positive、correlationはある。episode variance、within-episode autocorrelation、position binsなし。synthetic vectorのみ。 |
| 5.1 Alakazam fault instrumentation | NOT DONE | standalone capture helperはあるがactor/collectorから呼ばれない。既存actor error detail改善は有用だが、Alakazam fault reasonは特定されていない。 |
| 5.2 one retry classification | NOT DONE |2個のdiagnostic objectを分類する関数のみ。same game identity/fresh processで1回retryする収集経路がない。 |
| 5.3 paired evaluation protocol | PARTIAL | binary outcome paired bootstrapとWilson関数はあるが、ledger identityを検証せず、draw/fault/seat/opponent provenanceを扱わない。same seedがnative engineを固定しないためpaired前提も未成立。 |
| 5.4 evaluation tiers | NOT DONE | Smoke/Screening/Confirmation/Promotionのbudget enforcementやmanifest sealなし。 |
| 6.1 recurrent PPO | DEVIATION / NOT DONE | 1D loss fragmentのみ。chosen-action PPO ratioとfull-distribution exact KLを同じ1D軸で扱っており意味が混在。GAE、value loss、sequence/burn-in、fresh rollout、optimizer、adaptive KL停止なし。 |
| 6.2 consume-once V-trace | PARTIAL | pop-on-consume queueとtarget式はある。actual learner pass、actor lag publish integration、discard lifecycle、actor/critic optimizerなし。future actor versionも拒否しない。 |
| 6.3 AWR/CRR | NOT DONE | weight関数だけでreplay learnerではない。 |
| Phase 7 learner screening | NOT DONE | synthetic smokeはL1/L2/L3全て同じ関数を呼ぶ。2 lane、3 training seeds、4 rounds、fresh games、512 paired eval、winner/runner-up選択なし。 |
| Phase 8 opponent distribution | NOT DONE | adaptive確率式のunit smokeのみ。Train/Validation/Promotion splitなし。O2 `mirror-heavy`の実体は4 lane一様分布。 |
| Phase 9 DAgger/ExIt | NOT DONE | soft target関数とexact hash storeのみ。PIMC/public-belief determinizations、query-state selection、search budget、candidate Q/visit、near-duplicate dedup、4-way比較なし。 |
| Phase 10 four-lane training | NOT DONE | 実行なし。 |
| Phase 11 formal promotion | NOT DONE | sealed poolなし、4,096局なし。 |
| Phase 12 final artifacts/decision | PARTIAL | `DO NOT PROMOTE`判断とplaceholder bundleは作成。ただし正式experimentの成果物ではなく、source lineageにも問題がある。 |

---

## 4. 最も重大な意図との違い

### 4.1 「本番経路の再構築」ではなく「孤立部品の追加」になっている

計画のGoalは、既存基盤を`representation v3 + calibrated critic + full BC + fresh-rollout learner + search-guided improvement + reproducible promotion evaluation`へ**中核再構築**することだった。

しかし現在、以下のv3 symbolはactual collector/trainer/CLIから参照されない。

- `SpecialistModelV3`
- `OutcomeCriticV3`
- `TrajectoryDecisionV3` / `TrajectoryEpisodeV3`
- `ppo_recurrent_loss_v1`
- `ConsumeOnceVTraceQueueV1`
- `awr_weights_v1` / `crr_weights_v1`
- `schedule_probabilities_v2`
- `soft_search_target_v1`
- `DAggerDatasetV1`
- `capture_fault_v1` / `classify_retry_v1`

利用元は主に`run_meta_specialist_v3_*`のsynthetic/bounded scriptとunit testである。従って、既存CLIでtrajectoryを集め、v3 model/criticを学習し、checkpointを評価する一連の経路は存在しない。

Claude独立レビューは以前、「importersを作っただけ」「conformanceが文字列存在だけ」「dead codeを実装済みに数えた」問題を最重大としていた。今回の実装は未使用importでPASSにはしていないものの、**unit-call可能な孤立moduleをPhase実装済みと表現した**点で、同じ本質的失敗を繰り返している。

### 4.2 Gateで止まらず、横へ広げた

計画は以下を明記していた。

```text
Gate 不通過のまま後段へ進まない
Phase を飛ばさない
evaluation reproducibility が確立する前に learner 比較へ進まない
```

実測ではfresh vs freshが0/7一致、Gate 1も不通過だった。この時点で優先すべきだったのはnative RNG/lifecycleの独立再現と、正しいR2/R3 benchmark/splitの完成である。実際には、Gateを開いたままcritic、BC smoke、learner primitives、evaluation toy、Phase 7–9 contract smokeへ広げた。

本番learner比較を実施していないため誤ったpromotionには至らなかったが、作業順序は計画意図と異なる。研究プロジェクトとして見ると、最も情報価値の高いblocking experimentを解かず、後段のinterfaceを先に増やした状態である。

### 4.3 「full BC」の最重要前提である独立validationが壊れている

`load_bc_examples_from_teacher_records_v3`はgroup keyを次のように作る。

```python
episode_group = f"{episode_id_hash}:{near_duplicate_id}"
```

このkeyはepisodeとnear duplicateを同時に守るのではない。同じepisodeでもnear IDが違えば別groupになり、別episodeで同じnear IDでも別groupになる。計画が要求するのはepisodeとnear-duplicate edgeから作る**connected component**である。

rocketの実データを同じloader/splitterで再現した結果:

```text
usable records: 128
train: 102
validation: 26
train episode count: 3
validation episode count: 1
train/validation episode overlap: 1
```

つまりvalidation 26件は独立episodeではなく、trainingにも含まれるepisodeの別decisionである。BC smokeのvalidation NLL 1.5671、top1 0.5385はepisode generalizationを測っていない。

これはClaude reportが過去に指摘したsplit退化問題と直接関係する。既存コードにはepisode/near-duplicate connected componentとubiquity処理の知見があるのに、v3で簡略splitを新設したため、既知の問題を再導入した。

### 4.4 representation比較が計画の比較ではない

計画のR2は「current v2」、targetは実際のlegal actionである。現在のbenchmarkでは:

- R2: card embeddingをmean-poolする新規簡略control
- target: 選択actionの`option_type`
- split: 先頭80% / 末尾20%のtransition suffix
- valid size: 128件runでは26件
- training seed: 1
- epochs: 全model 3、early stoppingなし

となっている。R3のcandidate encoder、source/target relation、legal action maskingはbenchmarkで評価されない。簡単なaction-type classificationにstate encoderだけを比較している。

従って、ここでのNLL差を「v2よりv3が良い/悪い」と解釈してはいけない。言えるのは「この小さなaction-type probeではR3 encoderに明確な利得が見えず、CPU costが増えた」までである。

### 4.5 PPOのtensor意味が一貫していない

`ppo_recurrent_loss_v1`は、同じshapeの1D tensorを以下の両方として使う。

1. `new_log_probs-old_log_probs`を各sampleのchosen-action PPO ratioとして扱う
2. 同じ1D軸全体をcategorical distributionとしてnormalizeし、exact KLを計算する

PPO batch axisとaction axisは別である。legal action数もdecisionごとに変わる。計画が要求するexact legal-action KLには`[decision, action]`とmaskが必要で、chosen-action ratioには`[decision]`が必要である。現在の関数はこの2つを区別できない。

また、名前に`recurrent`とあるが、hidden state、sequence、burn-in、episode maskは入力に存在しない。これは単なる未配線ではなく、learner coreのinterface自体を再設計する必要がある。

---

## 5. 結果の詳細分析

### 5.1 Phase 0: 再現性

実測:

| 比較 | 完全一致 |
|---|---:|
| Alakazam fresh vs persistent | 0/8 |
| Archaludon fresh vs persistent | 0/8 |
| Alakazam fresh vs fresh rerun | 0/7、rerun側1 fault |

同じseedでwinnerまで反転しているため、単にaction samplingだけの差ではない。native environment RNG、rule agent state、process-global cache、またはseedが渡っていない経路が存在する可能性が高い。

fresh process標準化はpersistent state leakageを避ける点では正しい。しかしfresh同士も一致しない以上、same ledgerによるpaired evaluationは「同じ乱数条件」を保証しない。candidateとbaselineの差にenvironment randomnessが入り、paired bootstrapの分散低減前提が成立しない。

また計画の384局×2に対して8局なので、fault rateや一致率の推定精度も不足している。現状はreproducibility gateのdiagnostic failureであり、conditional passではない。

### 5.2 Phase 1: representation bounded probe

現行vectorized 128-record probe:

| lane | R3-A NLL − R2 | R3-B NLL − R2 | R3-A p95/R2 | R3-B p95/R2 | top1 (R2/A/B) |
|---|---:|---:|---:|---:|---|
| alakazam | +0.0316 | +0.0444 | 4.27x | 3.43x | .269/.269/.269 |
| archaludon | -0.0355 | -0.0413 | 3.95x | 3.49x | .500/.462/.462 |
| grimmsnarl | +0.0718 | +0.1273 | 5.22x | 4.93x | .385/.385/.385 |
| rocket | +0.2326 | +0.2883 | 5.71x | 3.82x | .423/.154/.154 |

解釈:

- 4 lane中3 laneでR3-B NLLが悪化。
- ArchaludonだけNLLが0.041改善したがtop1は3.85pp低下。
- RocketのR3-A/B top1は42.3%から15.4%へ大幅低下。
- R3-B p95はR2の3.43–4.93倍。R3-Aは3.95–5.71倍。
- validationは26 transitions程度で、同episode leakageが起こり得る。
- R2が実v2でなく、targetもaction typeなのでpolicy性能比較ではない。

従って、関係性を保持するschema/test自体には価値があるが、現modelをmainlineに選ぶ証拠はない。現在のデータはむしろ「表現容量を増やしただけでは短いoptimization budgetで学習できず、latencyも増える」というnegative resultである。

### 5.3 Phase 2: critic

64 synthetic episode、4 step、2 epochの結果:

```text
initial Brier: 0.666667
final Brier:   0.665898
absolute improvement: 0.000769
value/outcome correlation: 0.155
predicted V range: [-0.0121, +0.0022]
```

Brier改善は約0.12% relativeで、予測値はほぼ0に潰れている。bounded/uniform初期化の健全性は確認できるが、criticが有用なstate rankingを学んだ証拠ではない。

conditioning toyではC1 stableのvalidation Brier 0.4813、correlation 0.998となったが、generatorが`family-a -> win`、`family-b -> loss`を直接定義しているため、この高相関は設計上ほぼ自明である。C2 game-seedがvalidationで相関を失うnegative controlとしては使えるが、実matchup conditioningを採用する証拠にはならない。

### 5.4 Phase 3: teacher corpusとBC

各lane 512 record manifest:

| lane | records | episodes | unique near IDs | quality weight range |
|---|---:|---:|---:|---|
| alakazam | 512 | 7 | 509 | 1.0–1.0 |
| archaludon | 512 | 8 | 509 | 1.0–1.0 |
| grimmsnarl | 512 | 7 | 509 | 1.0–1.0 |
| rocket | 512 | 8 | 509 | 1.0–1.0 |

最も重要なのは、512 recordsが7–8 independent episodesしかない点である。transition数をsample countと見なしてはいけないという計画の問題意識が、そのまま残っている。さらに全weightが1.0なのでteacher confidence policyは実際には働いていない。

rocket BC smoke:

| epoch | train NLL | validation NLL | train top1 | validation top1 |
|---:|---:|---:|---:|---:|
| 0 | 1.7731 | 1.6656 | .353 | .500 |
| 1 | 1.6785 | 1.6058 | .451 | .615 |
| 2 | 1.6108 | 1.5671 | .471 | .538 |

lossは下がっているが、validation top1はepoch 1からepoch 2で低下した。validationは26 decisions、同一episode leakageあり、1 seedであるため、性能・generalization・best epochの判断材料として弱い。

さらにtrainerは各exampleに`episode_start=True`を渡すため、BC中はGRUを常にresetする。したがってこのcheckpoint stateはrecurrent policyのθ0ではない。state dictはreport生成中にhashしただけで`.pt`として保存されておらず、同一θ0からlearner比較を開始することもできない。

### 5.5 Phase 4–6 diagnostics

synthetic learner health artifactの数値（KL 4.2e-5、TV 0.00364、entropy 0.840、V-trace horizon 43）は、random logitsに0.01 noiseを加え、`c=0.9`を64個並べた構成から得たものだ。実policy updateの健康状態ではない。

特に`effective_horizon_90pct=43`は、実装上`W>=0.01`の要素数であり、90% effective horizonを計算していない。`0.9^43 ≈ 0.0108`なので43になるのは入力から解析的に決まる。旧V-traceの問題が解消された証拠ではない。

### 5.6 Phase 7–9 synthetic smoke

このartifactにはsynthetic candidate win rate 59.4%、paired delta +18.75pp、`promotion_gate=true`が入っている。しかしoutcomeはindex算術でcandidateが有利になるよう生成されている。`eligible_for_promotion=false`と併記した点は適切だが、promotion結果と同じschema/field名に`true`を残すのは混同リスクがある。

またL1/L2/L3の各3 seed結果が完全に同じなのは、3 learnerを比較したからではなく、各labelで同じ関数を呼んでいるからである。このrunからlearnerの優劣は一切判断できない。

---

## 6. 実装として価値がある部分

監査結果は「全て無価値」という意味ではない。以下は次の正しい実装へ再利用できる。

1. `EntityTokenV3` / `ActionCandidateV3`のtyped contractとhidden-card identity禁止。
2. host edge、owner、zoneを含むentity encoderの基礎。
3. legal-action order permutation、bench permutation、episode resetのbehavior test。
4. zero-init 3-class outcome headとbounded value。
5. episode mean CEの基本関数。
6. base logitsとbehavior log-probの一致をfail-closedにするtrajectory schema。
7. normalized entropy、forward/reverse KL、TV、argmax flipの基本統計。
8. consume時にqueueから削除するV-trace reuse防止の考え方。
9. adaptive opponent probabilityの基本式とsampling floor。
10. paired bootstrap、Wilson intervalの基本関数。
11. `DO NOT PROMOTE`を明示し、synthetic結果を本番性能と表現しなかった最終判断。
12. 1481 testが既存v1 pathのregressionを起こしていないこと。

これらは「production-ready v3」ではなく「production実装の材料」と位置付けるのが正確である。

---

## 7. 最終レポートで修正すべき表現

既存`meta-specialist-v3-final-report.md`は未実施を明記している点では誠実だが、以下は修正が必要である。

| 現在の表現 | 問題 | 正確な表現 |
|---|---|---|
| `4–6 IMPLEMENTED + SMOKE` | learner/fault/evalが実経路へ未配線 | `ISOLATED PRIMITIVES + SYNTHETIC UNIT SMOKE` |
| `paired protocol implemented: true` | ledger identity、draw、fault、seat/opponent strataを扱わない | `paired binary statistics helper implemented; game protocol not implemented` |
| `fault instrumentation implemented: true` | collectorから呼ばれずretryもない | `standalone fault capture/classifier only` |
| `episode/near-duplicate split` | connected componentでなくepisode leakageあり | `invalid composite-key smoke split` |
| `DAgger near-duplicate dedup` | exact state hash+policy dedupのみ | `exact identity dedup` |
| `fresh PPO` | recurrent learnerでなくloss fragment | `PPO objective prototype` |
| `source tree SHA` | 737個の`__pycache__/*.pyc`を含む全tree hash | `.py/.json/.yaml等に限定したcanonical source manifestが必要` |
| `source diff SHA` | untracked v3 filesを含まない | untracked file contentをpatch/manifestへ含める必要あり |

`evaluation_manifest.json`のstatus `sealed_promotion_not_run`も矛盾している。promotion poolはsealされていないため、`promotion_not_sealed_not_run`相当が正しい。

---

## 8. 計画に戻すための修正順序

### P0: Gate 0.3を本当に解決する

1. fresh process同士の同一gameを1 worker・greedy policy・固定opponentで再現し、最初に分岐するstate/action/RNG callを特定する。
2. Python、NumPy、Torch、engine、rule agent、deck shuffle、global cacheのseedをgame identityから明示的に設定する。
3. 8局ではなく、修正後に少なくとも計画の384局×2でrecord/action/state hash一致率とfault率を測る。
4. exact replayが不可能な外部要因なら、paired評価を同一environment randomnessで比較できる別方式へ設計変更し、計画を更新する。

### P1: 正しいrepresentation Gate 1

1. 現行v2 modelをR2として直接benchmarkする。
2. targetをaction typeでなくstable legal-action candidateにする。
3. 既存`local_dataset_v2`のepisode/near-duplicate connected-component splitを再利用する。
4. adapterのduplicate card endpoint bindingをzone/locatorで一意化する。
5. R3-A poolをown/opponent × active/benchへ分け、R3-Bのrelation定義を計画どおり明示する。
6. 3 seeds、validation early stopping、top1/top3/rare/action-type、latency、CPU preprocess、VRAMを揃える。

### P2: formal BC/critic

1. teacher strengthをcurrent validation poolで再測定し、特にArchaludon teacherの前提を確認する。
2. connected-component manifestを保存し、train/validation episode overlap=0をassertする。
3. source confidence/teacher agreement/search marginからquality weightを生成する。
4. episode sequenceを保持してGRU BCを行い、best checkpointを実ファイルとして保存する。
5. 4 lane × 3 seedsでθ0をsealする。
6. 実episode outcomesでcriticをwarm-upし、seat/opponent/position strataを含めGate 2を判定する。

### P3: actual learner integration

1. chosen-action log-prob `[B,T]`とfull legal distribution `[B,T,A] + mask`を分離したPPO batch schemaを作る。
2. recurrent sequence、burn-in、GAE/value loss、adaptive exact-KL、fresh queueを実装する。
3. V-trace/AWR-CRRも同じactual trajectory schemaとcriticへ接続する。
4. collectorがTrajectoryEpisodeV3を生成し、trainerがconsumeするend-to-end testを作る。
5. fault capture/retryをactual actor poolへ接続する。

### P4: Gate 7以降

Gate 0/1/2/3通過後にのみ、計画どおりAlakazam/Archaludonの3 seeds learner screening、O0/O1/O2、DAgger、4 lane training、4,096局promotionへ進む。

---

## 9. 最終評価

### 計画の方向との整合

**中程度。** module名・設計要素・安全契約は計画に沿う。一方、計画の中心である「実データでGateを順番に通し、同一θ0から実learnerを比較し、promotionを判断する」部分は未実施である。

### 実装完成度

**prototype / isolated primitives。** production pathへのv3統合は未完成。full BC、recurrent PPO、consume-once learner、AWR/CRR learner、DAgger pipelineという呼称は現状では強すぎる。

### 実験妥当性

**Gate判定には不足。** Phase 0は非再現、Phase 1/3はepisode leakageと比較対象不一致、Phase 2/4–9はsyntheticである。性能向上を支持する実験証拠はない。

### これまでの結果から言えること

1. 旧V-traceの性能停滞を解決したとは言えない。
2. v3 relation schemaは必要情報を表現する方向へ改善した。
3. 現encoderは小規模probeで一貫したsupervised利得を示さず、CPU latencyは3.4–5.7倍。
4. criticはbounded/uniform初期化として健全だが、有用なvalue rankingをまだ学んでいない。
5. BC loss低下は確認したが、episode generalizationではない。
6. learner間、opponent schedule間、DAgger有無、実勝率の比較結果はまだ存在しない。
7. `DO NOT PROMOTE`は正しい。

### 先の「作業完了」回答の訂正

「実行可能な実装・bounded smoke・レポート生成を完了した」という限定的説明は正しい。しかし、ユーザーが依頼した「提示した計画の作業をすべて完了」という意味では完了していない。正確には、**計画の前半prototypeと監査可能なnegative evidenceを作成し、formal training/promotionを未実施として停止した状態**である。
