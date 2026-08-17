---
project: MAGE-PTCG
document_status: decision-log
---

# Decision Log

## DEC-001｜Git Markdownを正典にする

- 日付：2026-07-14
- `docs/plan/`配下のMarkdownを正典、Notionをミラーとする。ページ対応は[../notion/page_map.yaml](../notion/page_map.yaml)。
- Notion変更は差分提案としてGitへ取り込み、commit後に正式同期する。

## DEC-002｜Rule Agent v0をChampionとして維持

- 日付：2026-07-14
- 旧報告のRule v1対Rule v0 105–95は **UNVERIFIED HISTORICAL REPORT — ORIGINAL ARTIFACT NOT RECOVERED** とし、昇格根拠に使わない。
- 2026-07-15の新規実装評価は400試合205–195だが、engine seed非対応かつ既定runtimeで両者の選択が同一であるため、性能向上を示さない。
- Rule Agent v0をChampion、Rule Agent v1をChallengerとして維持する。

## DEC-003｜CompetitionをOptional Evidence Planeとする

- 日付：2026-07-14
- Replay取得をcritical pathにしない。
- 2026-07-17までにmodeを確定。

## DEC-004｜ES-MCCFRを既定経路から外す

- 日付：2026-07-14
- まずbounded search（S1）。
- Teacher品質／engine-callで優位な場合のみ昇格。

## DEC-005｜Student v0に期限付きGO／NO-GO

- 日付：2026-07-14
- 期限は2026-07-30。
- Rule v0非劣性未達なら提出critical pathから除外。

## DEC-006｜Submission Factoryを全期間継続

- 日付：2026-07-14
- P0でTier D／Eを常時build。

## DEC-007｜Safetyを上側信頼限界で評価

- 日付：2026-07-14
- 0件を真の発生率0としない。
- 10kをhard target、100kをoptional。

## DEC-008｜C1を公開情報限定の新規実装として確定

- 日付：2026-07-15
- 回収不能な旧patchを前提にせず、`ActorInformationView`、Stable `ActionKey`、`DecisionState`、`PublicBelief`、Rule v1 lifecycleを新規実装する。
- PublicBeliefはactor viewのみを入力とし、permitted priorがない場合は`unknown`へ正規化してRule v0へ決定的にfallbackする。
- C1 `DecisionState`のpersisted traceにはactor自身の手札も含めず、公開状態、公開履歴digest、redacted ActionKey、belief summaryだけを許可する。
- C1完了判定と制約は[Evidence](../evidence/public-belief-c1-new-implementation-2026-07-15.md)を正とする。

## DEC-009｜未検証のhand source indexをActionKeyに保持

- 日付：2026-07-15
- cabtの`option.area`／`option.index`の意味は未検証であるため、own-hand card IDはActionKeyの補助enrichmentに限る。canonical payloadから`index`を削除せず、同一カードIDでも異なるindexの合法選択肢を別identityとして扱う。

## DEC-010｜C2aの未検証card roleはFLEX・低supportとして保存

- 日付：2026-07-15
- 現リポジトリに公式card dataがないため、`deck.csv`由来の9 card IDは意味を推測してCORE／ENGINE／TECHへ分類しない。全entryを`FLEX`、`validity=1.0`、`support=0.0`、`freshness=0.0`として明示する。
- card pool ID／versionも`competition-card-pool-unverified`／`unverified`と明示する。C2a runtimeは同値との一致だけを確認し、公式card data入手後の再分類は新snapshotとして行う。

## DEC-011｜C3 runtime searchはpublic forward契約まで無効化

- 日付：2026-07-15
- `kaggle-environments==1.32.0`の公開`Environment.clone()`／`step()`は、外部評価器が所有する現在のEnvironmentを前進できるが、submission `agent(obs)`へEnvironment handleまたは任意観測からの再構成APIを提供しない。
- opaqueな`search_begin_input`の意味・安定性・復元APIは公開specificationで確認できない。private binary、非公開関数、opaque tokenへ依存するadapterはversion／ABI破損、submission差、hidden-state混入のリスクがあるため追加しない。
- C3は`EngineAdapter` protocol、deterministic fake adapter評価、adapter未指定時のRule v0 fallbackまでを実装する。実cabt paired評価と性能改善は未確認と明記し、documentedかつprivacy-safeなforward契約が得られるまでRule v0をruntime Championとして維持する。

## DEC-012｜C5はactual evidenceをattestできない入力を昇格根拠にしない

- 日付：2026-07-15
- C5 canonical decision recordはpublic ActionKey payload/digestだけを保存し、C4 Rule BCのprivate ActionKey core digestを再保存しない。
- C4 Rule BC v1はactual cabt sourceをattestできないため、C5 CLIは`--actual-cabt`による再ラベルを拒否する。fixture、fake adapter、0-game Leagueはpromotion inputとして`NO_DECISION`に分離する。
- actual trace adapterとdocumented public League runnerが追加されるまで、Rule Agent v0をChampionとして維持する。

## DEC-013｜Competition Intelligence Sidecar（O1）はCompetition Probeと別packageにし、Slice 0–2のみ実装する

- 日付：2026-07-18
- 新規`src/mage_ptcg/competition_intelligence/`を、既存C2b `mage_ptcg.competition`（Capability Probe／raw archive／redaction／schema fingerprint）とは別packageとして追加する。既存C2b機能はimportして再利用し、重複実装しない（[実装計画書§23.1](../plan/implementation/04_kaggle_competition_intelligence_and_joint_optimization_implementation_plan.md#231-モジュール配置と既存機能の再利用)）。
- 本セッションではO1-0（Guardrails）とO1-1（Foundation：contracts／permissions／provenance／archive／runstate／catalog／config／CLI 3コマンド）のみを実装する。O1-2（Replay正規化）以降のO1-6までは未着手とし、継続計画を[実装計画書§23.3](../plan/implementation/04_kaggle_competition_intelligence_and_joint_optimization_implementation_plan.md#233-未実装範囲継続計画)に記録する。
- `mage_ptcg.competition_intelligence`は`main.py`から到達不能とし、`tests/test_competition_intelligence_runtime_isolation.py`で検証する。Champion／submission defaultはRule Agent v0、Promotionは`NO_DECISION`のまま変更しない。
- 詳細はEvidence（[o1-competition-intelligence-sidecar-slice0-1.md](../evidence/o1-competition-intelligence-sidecar-slice0-1.md)）を正とする。

## DEC-014｜O1-2〜O1-4はoffline_trainingのコードを変更せず、選択専用adapterとして実装する

- 日付：2026-07-18
- O1-2（Replay正規化）、O1-3（Knowledge Registry）、O1-4（Immutable SnapshotとOffline Training Adapter）を実装した。既存`mage_ptcg.offline_training`／`mage_ptcg.student`のコードは一切変更しない。
- Offline Training Adapterは、選択されたepisodeへ元の`rule-bc-v1.jsonl`行をfilterした新しいJSONLファイルを生成し、既存の`build_dataset()`へそのまま渡す方式（selection-only）とする。これにより、Snapshotを使わない既存パイプラインの挙動は変更前と完全に同一のまま保たれる。
- `mage_ptcg.competition_intelligence`は`mage_ptcg.student`／`mage_ptcg.offline_training.dataset`をimportしない（両者は`mage_ptcg.student`パッケージの`__init__.py`経由でStudent runtimeを間接的に読み込むため）。代わりに`rule-bc-v1.jsonl`の行形式を直接、寛容にparseする独自reader（`offline_reader.py`）を実装する。
- 自己レビューで`group_split.py`の複合キーに`episode_id`（常に一意）が含まれ、group-aware splitのleakage防止が機能していなかった実装バグを発見し修正した（[実装計画書§24.2](../plan/implementation/04_kaggle_competition_intelligence_and_joint_optimization_implementation_plan.md#242-発見した実装バグとその修正自己レビュー)）。
- 新規focused test 258件（O1-0/O1-1の127件＋O1-2〜O1-4の131件）PASS、repository full suite 1277 passed。CLI subprocess経由の完全パイプラインを2回実行し、Knowledge/Intelligence Snapshot and 正規化済みEpisode/Decisionが決定的に同一hashになることを確認した。
- Champion／submission defaultはRule Agent v0、Promotionは`NO_DECISION`のまま変更しない。詳細はEvidence（[o1-competition-intelligence-sidecar-slice2-4.md](../evidence/o1-competition-intelligence-sidecar-slice2-4.md)）を正とする。

## DEC-015｜O1-6は非権威的・統計的sidecar baselineに限定する

- 日付：2026-07-18
- Meta Posteriorはpermission/cutoffを満たすWeighted Strategy Observationのみを集計し、unknown massを既知archetypeへ再配分しない。観測Kaggle dataからdeck/policy効果を因果分離したとは主張しない。
- Opponent Surrogateはactor-visible contextだけを用いるsmoothed empirical policyであり、Student teacher／dataset／runtimeに接続しない。unseen contextは決定的fallbackへ落とす。
- benchmark manifestとPromotion Reportは非権威的な分析artifactである。report decisionは`NO_DECISION`、`REVIEW_REQUIRED`、`INSUFFICIENT_EVIDENCE`だけを許可し、Champion/default/Promotion Gate/Kaggle submissionを変更しない。

## DEC-016｜Population Actor-Criticを方策改善の主実装経路にする

- 日付：2026-07-26
- **問題**：Rule v0近傍の手作業変更と単一教師BCだけでは、終局勝敗・相性・学習方策が訪れる局面を直接最適化できない。
- **採用**：`policy_learning`をcandidate-only層として実装し、ActorInformationView、visible public history digest、CABTの合法ActionKeyだけからRecurrent Legal-Action Actor-Criticを学習する。offline AWR、Targeted DAgger、PSRO、PPO/V-traceを順に使う。
- **棄却**：生observationや相手private stateを再帰履歴へ保存する案、自由形式のaction生成、snapshot/restore不在の反実仮想枝分岐を採用しない。いずれも情報境界またはCABT capabilityを満たさない。
- **検証**：padding mask、AWR/PPO/V-traceの有限値、offline学習・runtime legal selection、public-history連結、PSRO正規化、DAgger queryをfocused testで確認した。長時間CABTとpaired promotionは未実行である。
- **残リスク**：実CABTのwinner表現、長時間actor throughput、official scoreとの校正、複数教師の実効品質は未検証である。各候補は新しいfixed Populationとholdoutで評価し、Rule v0 Championを自動変更しない。

## DEC-017｜Population Actor-Criticのonline trajectory契約をfail-closedに固定する

- 日付：2026-07-26
- actor recordはbehavior log-prob、terminal mask、actor policy version、exact deck fingerprint、vocabulary hashを必須とする。PPOは単一actor policy versionのみ、V-traceはepisode末尾だけterminalとし、burn-inはhistory復元に使いlossから除外する。
- checkpointはoptimizer、scheduler、Python／Torch／DataLoader RNG state、dataset hash、vocabulary hashを含む。いずれかの契約が不一致・欠損ならresumeを拒否する。
- policy runtimeのNaN／Infはhard-failする。一方で通常のruntime非対応はRule v0 fallbackを許可し、理由を保存してactor-critic targetから除外する。fallbackをsilentな性能証拠やChampion変更に使わない。

## DEC-018｜AWR優位性をPPO safety pilotの開始条件にしない

- 日付：2026-07-27
- **観測**：Gate 4の同一256局CABTで、BC recurrentは100勝、AWR recurrentは88勝、AWR feedforwardは103勝、AWR＋Rule proposalは102勝だった。AWR feedforwardとBC recurrentの差は3勝（+1.172ポイント）であり、優位性を支持しない。全候補はRule v0に50%未満である。
- **採用**：AWRをChampion又はpromotion候補にしない。一方、BC recurrentの合法・fault-free checkpointを初期値にし、Value-only warm-up、on-policy legal categorical sampling、GAE、BC KL guard、checkpoint/snapshot/evaluation stopを備えるPPO Gate 5a safety pilotを開始可能とする。
- **境界**：Gate 5aの目的は10万decisionでonline経路・数値安定性・resume・安全停止を確認することであり、性能改善の確認ではない。現在の固定Rule populationを「50% strong opponent」と解釈しない。PPO結果だけでChampion、Rule v0、default Deck、Kaggle提出を変更しない。

## DEC-019｜Gate 5aのmulti-selectを合法実行とPPO対象分離で扱う

- 日付：2026-07-27
- **観測**：Gate 5a初回rolloutは`HARD_TIMEOUT` 1件に加え、single-action限定runtimeにより201 episode／244 decisionのRule v0 fallbackを含んだ。全fallbackは`selection_type=1`のmin=max=2〜7 multi-selectであり、candidateのcategorical behaviorではない。
- **採用**：runtimeは各legal optionをscoreして決定的top-kを返す。multi-selectは`ppo_eligible=false`、`actor_action_mode=multi_topk_ranking`、`behavior_log_probability_kind=NOT_PPO_ACTION_SET`として保存する。fallbackまたはPPO非適格decisionを一つでも含むrecurrent episodeはPPO updateから丸ごと除外する。
- **境界**：このtop-kはmulti-action set policyの確率モデルではない。Gate 5aでmulti-selectをPPOへ混入させず、V-trace混合behaviorやmulti-action distributionの設計判断を先送りする。timeoutのseat原因は未解決のため、actor並列度を増加させず固定slot再現と64局stressがPASSするまでPPOを再開しない。

## DEC-020｜DAgger安定化checkpointをPPO pilot専用初期値に固定

- 日付：2026-07-27
- **観測**：CUDA `NVIDIA RTX PRO 5000 Blackwell`で、targeted DAgger 1 epoch、fresh 64局smoke、fresh 256局cleanが全てfault-freeに完走した。cleanは256/256 terminal・legal、fallback 0、PPO利用可能201 episode／2,551 decisionだった。Gate 4 validation forced-action除外 top-1はDAgger後0.802182、元BC recurrentの0.794182を下回らなかった。
- **採用**：`runs/policy-learning-gate5a/scale-readiness-gpu/dagger/bc-recurrent-dagger-stabilized/`を新規artifact rootのGate 5a PPO pilotの初期値に固定する。過去のtimeout/fallback混在PPO artifactはresumeしない。
- **境界**：これはonline PPO経路を開始できる安全性判断であり、ゲーム強度、AWR優位性、Champion昇格、Rule v0置換の判断ではない。PPO/V-traceのlossを同一runで混在させない。

## DEC-021｜行動選択モードを明示契約とし、Gate 5a-AのBC改善判定を差し戻す

- 日付：2026-07-28
- **観測**：`load_runtime_policy`は`stochastic_actions`をcheckpoint schemaから推論していた。`prepare_gate5a_round3_branch.sh`はBC（schema `policy-learning-offline-awr-v2`）とround 3／round 4（schema `policy-learning-ppo-pilot-v1`）を同一branchへ並べるため、fixed recheckはBCをargmax、PPO候補をcategorical samplingで実行していた。加えて`snapshot-22794`の`updates`は3であり、AdamW lr 1e-5の3ステップで勝率9ptは説明できない。round 4は1ステップ多いのに41.31%へ後退している。
- **採用**：行動選択モードを`load_runtime_policy(action_mode=...)`とpopulation entryの`provenance.action_mode`で明示する。既定は`argmax`（提出可能な方策）とし、schemaからの推論経路は削除する。PPO rolloutは`sample`を明示指定し、evaluationは`argmax`とする。Gate 5a-Aの判定を`GATE5A_BC_IMPROVEMENT_CONFOUNDED_PENDING_RECHECK`へ差し戻す。
- **境界**：これは性能退行の主張ではなく、原因帰属が不能であることの明示である。再判定は、BC×round 3のargmax／sample完全2×2（同一1,024局schedule、計4,096局）で`action_mode_effect_on_bc`、`parameter_effect_at_argmax`、`parameter_effect_at_sample`、`action_mode_effect_on_round3`を分離してから行う。CABTは`engine_seed_supported=false`のため、これらはpaired差ではなくmatched schedule差として報告する。

## DEC-022｜PPO更新を multi-epoch minibatch とし、trust regionをbehavior policy基準にする

- 日付：2026-07-28
- **観測**：`ppo_update_episodes`は全episodeを1バッチにまとめ、rolloutごとに`optimizer.step()`を1回だけ実行していた。損失計算時点で`current == behavior`が厳密に成立するため、importance ratioは恒等的に1、clipは一度も発火せず、round 1の`kl_to_bc`は厳密に0だった。目標10万decision（約13 round、rollout 800局＋evaluation 256局）に対する総勾配ステップは約13回である。また安全停止は`optimizer.step()`前のdetach値を検査しており、KL逸脱の検出が1 round遅れていた。
- **採用**：`epochs`×`minibatch_episodes`（既定4×64）でcurrent log-probを再計算する。behavior log-probは収集値で固定し、advantage正規化はrollout全体で1回だけ行う。安全指標は`optimizer.step()`後に再計測し、`kl_to_behavior_post`をhard gate（既定0.02、逸脱時は`pre-update-backups/round-N`から復元）、`kl_to_bc_anchor_post`を累積漂流の監視、`entropy_post`をhard gateとする。epoch途中でも閾値超過なら早期停止する。`summary.ppo.updates`は勾配ステップ数、`rollouts`はrollout数として分離する。
- **境界**：1 stepから約40 stepへ変わるため、既存の`1e-5`をそのまま本番投入しない。`--learning-rate`で`1e-6`／`3e-6`／`1e-5`を各1 roundずつ比較してから継続する。`kl_to_bc_anchor`はBCアンカー正則化であってPPOの近接性制約ではないため、rollback判定に使わない。

## DEC-023｜任意選択promptの辞退をfallbackと数えず、非PPO decisionもepisodeへ残す

- 日付：2026-07-28
- **観測**：`policy_learning/runtime.py`は`minCount==0`かつ`maxCount>=1`のpromptで空リストを選択後に`selected_indices[0]`へ添字アクセスし`IndexError`を送出していた。これは`TEACHER_DECISION_FAILURE`へ包まれ、`RULE_V0_POLICY_RUNTIME:list index out of range`として黙ってRule v0委譲されていた。さらに`capture()`は`minCount==0`の空選択でtelemetry行を残さないため、当該委譲は`fallback_decisions`に一切現れなかった。到達性は`student/runtime.py:62`、`offline_training/neural_runtime.py:58`、`candidate_runtime.py:62`が同prompt種を明示処理していることによる。Gate 4 dataset 80,133 recordsに`min_count==0`が0件であることは、発生の不在ではなく計測の不在である。
- **採用**：非MAINの任意promptは`actor_action_mode=optional_declined`、`ppo_eligible=false`、`fallback_used=false`として辞退する（Rule v0／Student v0と同一規約）。MAINで`minCount==0`の場合は1件だけ選ぶ。`optional_prompt_count`／`optional_declined_count`／`captured_decision_count`／`uncaptured_fallback_count`／`actual_fallback_decisions`を分離保存し、preflight gateは後二者が0であることを要求する（`optional_declined_count`は0でなくてよい）。PPO非適格decisionはepisodeから削除せず、policy lossだけmaskしてvalue／GAE連鎖を維持する。
- **境界**：episodeを保持する根拠はGAEのcredit assignment連続性であり、hidden-state汚染ではない。`collate`は各decisionを独立行として構成しGRUをゼロ初期状態から走らせるため、decision間で隠れ状態を持ち越していない。同じ理由で`--burn-in`は現構成で効果がなく、CLIから削除した。CABTの`step`は実測で候補decisionごとに一意（2,431 episode／40,000 decisionで重複0）だが、同一環境step内の追随promptを検出できるよう`environment_step_id`／`decision_substep`／`reward_boundary`を保存し、同一step内はdiscount 1.0とする。

## DEC-024｜Gate 5 CABT childはCABTだけを登録し、16 workerを採用する

- 日付：2026-07-28
- **観測**：各CABT gameを隔離するspawn childが`kaggle_environments`の全bundled environmentをeager importし、CABTと無関係なLiteLLM環境のremote cost-map初期化まで実行していた。cold importは全登録で22.34秒、local cost map指定でも7.09秒、CABTだけの登録では1.27秒だった。最適化後の同一64局sweepは12/14/16/20/24 workerでwall-clock 2.049/1.982/2.791/2.511/2.248 game/s、全条件legal 64/64・candidate fault 0だった。
- **採用**：`scripts/test_sim.py`の`kaggle_environments`初期化中だけenv directory列挙を`cabt`へ限定する。公式`make`とCABT pluginは変更しない。Gate 5 runnerはOMP／MKL／OpenBLAS／NumExprを1 thread、LiteLLM cost mapをlocal、worker recycleを32局/worker、rollout／evaluation／preflightの既定を16 workerとする。
- **棄却**：20/24 workerは16 workerより10.0%／19.5%遅く、物理14 coreを超えたSMT、memory bandwidth、spawn競合で実効速度が低下するため採用しない。actorの数ミリ秒単位の単件推論を各spawn processからGPUへ移す案も、IPCとCUDA contextの方が大きくなるためGate 5では採用しない。
- **境界**：CABT game単位のprocess隔離はfault containmentのため維持する。GPUはPPO learnerのbatched updateへ使い、CABT engineはCPUへ残す。永続CABT workerやbatched inference serverは追加高速化候補だが、native state漏えい・game間再現性・timeout隔離を別gateで実証するまで本runへ入れない。

## DEC-025｜Gate 5aは10万decisionで学習を止め、checkpoint選抜と未知方策holdoutを分離する

- 日付：2026-07-28
- **観測**：round 15は累積106,158 decisionへ到達し、数値安全gateを通過した。一方、過去にround 3がround 4を上回っており、PPOの最終checkpointが最良とは限らない。また旧recheckはBCをargmax、PPOをsampleで実行したため、性能差の原因帰属に使えない。
- **採用**：追加学習を止め、primary selectionではBC recurrent、frozen round 3、30,793／53,590／76,937 decision snapshot、round 15 finalをargmax・Rule v0 current deck・各1,024局・balanced side・同一worker・matched scheduleで比較する。選抜bestだけをBCとともに、trainingで未使用かつopponentとして実行可能なPolicy 6 entry／4 distinct hash＋unseen Rule v0 deck 2種へ各128局、合計1,024局ずつ評価する。population上AVAILABLEでもopponent loaderが未承認のStudent entryは理由付きで除外記録し、unknown holdout通過へ数えない。
- **判定**：BC差の独立二標本95%区間、Rule v0 50% point targetとWilson下限、side別差、BC勝率で定義するhardest opponent quartile、unknown policy hash集計、fault／illegal／timeout 0を別々に保存する。round 15がfrozen round 3を上回り、かつBC改善が確認された場合だけ`GATE5A_PPO_IMPROVEMENT_CONFIRMED`とする。best checkpointのvalidationとChampion promotionは別であり、自動昇格しない。
- **境界**：CABTは`engine_seed_supported=false`なので、同じseed列でもpaired inferenceとは呼ばない。比較はmatched scheduleの独立二標本として扱う。sample modeは行動選択診断へ分離し、主評価へ混在させない。

## DEC-026｜Submitted runtimeを資格実行commitへ固定し、中央GPU推論はspawn actor＋IPCで行う

- 日付：2026-07-28
- **観測**：旧ledgerの`source_commit`は公式提出lineageを表す一方、proxy runtime資格確認は`branch_tip`のbyteへ対して行われていた。両者を同一視すると、検証していないbyteへruntime evidenceを流用する。CABT native engineを同一processのthreadで並行実行するとallocator corruptionを再現した。spawn process actorから親GPU queueへIPCした128局では、fault／timeout 0、batch平均2.63／最大7、同一semantic入力のCPU/GPU action一致100%だった。
- **採用**：実行snapshotは資格確認に使った`branch_tip` commitを`git archive`し、Policy／Deck hashを照合する。公式`source_commit`はsubmission lineageとしてsplit隔離に保持する。中央推論はCABT gameをspawn processへ隔離し、semantic state／legal actionだけを親processのGPU microbatch queueへ送る。
- **境界**：floating refはdrift診断専用でruntimeへ使わない。Gate3 2000局sourceは3 faultのため`BLOCKED`を維持し、1997 valid trajectoryだけを使う。未対応multi-select decisionはhidden sequenceを跨がない境界として除外する。R2D3／PSRO smokeはChampion昇格またはPopulation追加の性能証拠にしない。

## DEC-027｜R2D3 performance protocolは全Replay sourceのruntime資格を開始条件とする

- 日付：2026-07-28
- **観測**：初回preflightはcheckpoint単体を見てPPO／BC／Familyを未資格と誤判定した。既存のimmutable population entryを用いると、PPO frozen-round-3、BC recurrent、Family Alakazamはpolicy hash、deck fingerprint、source lineageを照合したadapter `prepare()`を通過し、各1局の対Rule v0実CABTも`DONE`／legal／candidate fault 0だった。PSRO meta-mixtureはこの資格済みsource群から派生できる。
- **採用**：Replay sourceは既存immutable population entryを正とし、checkpoint単体で可否を判定しない。sourceをRule v0、submitted asset、または過去Replayへ無断置換しない。以後の停止条件はsource資格ではなく、20,000局Replay品質Gate、CABT scaling、6構成screen、多seed、holdout、SP-PSROの各実行Gateとする。性能実験未実行時は`NO_PROMOTION_RECOMMENDED`とし、`R2D3_IMPROVEMENT_NOT_REPRODUCIBLE`や`R2D3_RULE_V0_NOT_REACHED`を性能結果として発行しない。
- **境界**：CUDA GRU/LRU 128-request smokeは実行経路の確認だけである。CABT 128局schedule、callback p95、CPU/GPU action一致、actor数／batch／delay選定を代替しない。final holdoutは0 asset使用のまま保つ。

## DEC-028｜final holdoutは上流gateの合格を条件とし、消費前に予約マーカーを書く

- 日付：2026-07-30
- `conditional_holdout`の実行条件がdevelopment validation勝率のみで、`final_holdout`も同条件を共有していた。`STAGES`順序によりdeck holdoutとPSROは実行済みになるが、その結果は参照されていなかったため、deck holdoutが閾値未達でもfinal holdoutを消費しうる状態だった。
- `HOLDOUT_PREREQUISITE_STAGES`を正とし、`final_holdout`はdevelopment validation・deck holdout（使用済みかつ勝率≥閾値）・psro_payoff・psro_best_responseの全PASSを要求する。未達時は`NOT_USED`を記録し消費しない。
- holdout消費前に`RESERVED`マーカーを書き、成功後に`USED`へ更新する。途中クラッシュを「未使用」と誤認して再実行することを防ぐ。one-time splitの再実行は拒否する。
- `run_promotion`は`holdout_used`だけでなく両holdoutの勝率を閾値と比較する。判断根拠は`promotion_decision.md`へ記録する。
- 根拠と検証：[experiments/2026-07-30-r2d3-learner-throughput-and-final-holdout-gate.md](../../experiments/2026-07-30-r2d3-learner-throughput-and-final-holdout-gate.md)

## DEC-029｜R2D3 learnerの高速化は意味論不変の範囲に限り、batch sizeは変更しない

- 日付：2026-07-30
- 1 learner stepの実測内訳は`replay.sample()` 48.8 ms／`_learner_batch()` 14.7 ms／`learner.update()` 26.9 msで、GPU計算ではなくPython側の逐次処理が律速だった。
- 採用した最適化はサンプル列・重み・tensor値が不変であることをテストで担保したものに限る（sampler cache、NumPy batch構築、validation CABT並列化、PPO updateのバッチ化、offline collateのNumPy化）。合計で1 stepは91 ms→45.5 ms。
- `learner.update()`はbatch sizeにほぼ非依存（128–8192で29.6–33.7 ms）だが、batch拡大は最適化の意味論を変え、実行済みarchitecture screenとの比較可能性を壊すため既定128を維持する。変更する場合は学習率の再調整とscreenの再実行を伴う。
- CABT永続worker（`--worker-reuse-games`）は既定`1`のまま無効とする。CABT engineがschedule seedを受け取らないため局単位の再現性が元から無く、reuse有無の同一性を24局規模では分離できなかった。有効化には数百局規模の統計的A/Bを前提とする。
- 根拠と検証：[experiments/2026-07-30-r2d3-learner-throughput-and-final-holdout-gate.md](../../experiments/2026-07-30-r2d3-learner-throughput-and-final-holdout-gate.md)

## DEC-030｜R2D3 v2はtrue sequence／closed-loop PSROと実測resource scalingを新規runで採用する

- 日付：2026-07-30
- **観測**：旧実装はreplay上のburn-in 8／unroll 20に対しmodel forwardが`T=1`、priorityはbatch平均の複製、PSRO best-responseはfrozen replayだけの再学習だった。state/actionもhash sketchが主で、overlap windowはepisode数の多いsourceを過重sampleした。
- **採用**：burn-in 8、trainable unroll 20、5-step bootstrap lookahead、全step distributional Double-Q、CQL、item-wise PER、episode-first samplingを一体契約とする。PSROはfrozen meta-mixtureを局開始時にsampleし、online trajectoryを完全provenance付きで収集してoffline／online partitionを交互に学習する。
- **表現と容量**：明示的な公開state/action座標＋hash residual、LayerNorm付き二層encoder、categorical auxiliary lossを採用する。production hidden sizeは128から256へ上げる。Kaggle Replay actionをexpert labelにせず、公開exact deckへRule v0をbindして環境多様化にだけ使う。
- **resource scaling**：固定worker／batchを廃止し、同じcontroller内の実CABT process-poolと実CUDA updateで選ぶ。production batch候補は64／128／256／512／1024／2048／3072。batch 128基準の総sequence budgetを保存してupdate数を換算し、target更新間隔、PER β、平方根learning-rate補正も同時に変える。hidden 256／batch 2048の200-update soakはVRAM 32,184 MiBで完走した。CPU収集actorとCUDA評価workerはDEC-032で分離する。
- **DEC-029との関係**：DEC-029は進行中の旧architecture screenとの比較可能性を守るためbatch 128を固定した判断として有効。v2はfeature/model/replayを新規にし、screenを全て再実行するため、その「batchを変更しない」境界だけをsupersedeする。旧artifactへrebaseline resumeしない。
- **反証と境界**：split修正後の最終smokeは15 stage、128 replay game、PSRO online 4 gameをfault／illegal／timeout 0で完走したが、development 2/8、best-response 0/4で性能証拠ではない。production multi-seedと独立holdout通過までChampion、default、Promotion、Kaggle提出を変更しない。
- 根拠と検証：[experiments/2026-07-30-r2d3-sequence-psro-v2-readiness.md](../../experiments/2026-07-30-r2d3-sequence-psro-v2-readiness.md)

## DEC-031｜長時間R2D3の再開単位へPER状態と全入力identityを含める

- 日付：2026-07-30
- **観測**：旧checkpointはmodel／target／optimizer／RNGだけを保存し、学習で更新されたPER priorityを保存していなかった。同じstep seedでもWSL再起動後はsampling分布が初期値へ戻る。また単一Replay objectをarchitecture／seed間で共有し、実験順序によって開始priorityが変わっていた。
- **採用**：checkpoint schema v2へ全priority、replay index identity、training identity hashを含める。PSRO best-responseはoffline／online partition別にpriorityを保存する。各training runはimmutable transition payloadだけを共有するfresh replay forkから開始し、resume時だけ当該runのpriorityを復元する。
- **durability／fallback**：Replay chunk、Replay本体、PSRO replay／state pair、learner checkpointは一時fileへのwrite、`fsync`、atomic replace、directory `fsync`で確定する。最新learner checkpoint又はPSRO stateが破損していれば、identity／SHA／step／連続game IDを検証し、直前の完全checkpointへ戻す。全候補が不正なら初期化して続けずfail-closed停止する。
- **identity境界**：profile、HEAD、controller／R2D3 source bytes、protected `main.py`／`deck.csv`／Rule v0、semantic feature version、submitted registry、population、deck pool、source artifactをrun／collection identityへ束縛する。demoは現在のtraining splitだけを復元可能とし、旧semantic Replay又はsplit変更後のchunkを混ぜない。
- **資源／データ判断**：production actor候補は4〜28、CUDA batch候補は64〜3,072とする。3,584以上を含む境界探索中にWSL停止を観測したため、GPU memoryを使い切ること自体より連続稼働を優先して上限を3,072へ戻す。旧20,000局はsemantic不一致で再利用せず、現semanticの5,000局（うち上位deck 1,000局）へ置換する。これは動作検証回数の削減であり、smoke結果を性能証拠へ昇格する判断ではない。
- **検証**：連続12 updateと6+resume+6 updateのmodel／target／priority／次sampleが完全一致した。hidden 256／batch 2,048の200-update soakは40 checkpoint、VRAM 32,184 MiB、NaN／Inf 0で完走し、100 updateのfresh reloadと完了後の別process reuseを通過した。
- 根拠：[experiments/2026-07-30-r2d3-sequence-psro-v2-readiness.md](../../experiments/2026-07-30-r2d3-sequence-psro-v2-readiness.md)

## DEC-032｜holdoutはdeck連結成分で隔離し、source変更後の計算済みstage再利用を禁止する

- 日付：2026-07-30
- **反例**：独立監査で、旧submitted splitのtraining `dev/ozawa_crustle_v2`／`agents/ozawa-crustle-rule+RL`とdeck holdout `agents/ozawa-crustle-rule`が同一deck hashを共有していた。lineageだけのgroup化では「未見deck」評価にならない。またdeck holdoutは中断後に再実行可能で、campaign identityからCABT／split／runtime依存sourceが欠けていた。
- **採用**：training eligible assetはpolicy hash、source lineage、deck hashのいずれかを共有する連結成分へunionし、成分単位でsplitする。`assert_no_leakage`は3 identity全てを検査する。旧Gate 5のRule deck holdoutもtraining deck fingerprintと一致する候補を除外し、manifestへ`unknown_deck_hash`を保存する。
- **一回限り境界**：deck／final holdoutはともに最初のCABT前に`RESERVED`をfile／directory `fsync`付きで確定し、`RESERVED`／`USED`のどちらでも再実行を拒否する。上流gate未達で一度も予約しなかったsplitだけを未使用と扱う。
- **source／checkpoint境界**：campaign identityは`main.py`、`deck.csv`、`agents/**/*.py`、`src/mage_ptcg/**/*.py`、policy-learning scripts、依存定義を含むsource closureとsource artifactを束縛する。source rebaselineはstage開始前だけ許可し、計算済み／部分stageを新sourceへ持ち越さない。full modelはcheckpoint SHA、training identity、stepを現在のcampaignと再照合する。
- **資源と再開**：CPU収集の4〜28 actor sweepとCUDA validationを分離し、独立CUDA model/contextは最大4 processとする。PSRO payoffは決定的scheduleを局単位でdurable保存し、WSL停止後は正常prefixの次局から続ける。NaNだけでなく±Infのmetric／PER priorityも即時拒否する。
- **検証**：現ledgerはtraining 12／validation 2／deck holdout 1／final holdout 1で全split間deck hash非交差。短縮128局Replayのv3 smokeは15 stage、PSRO payoff 12局、online 4局をfault／illegal／timeout 0で完走し、別process `--resume`も再計算なしでPASSした。旧v2 smokeはこのsplit修正前なので性能・holdout証拠に使わない。
- 根拠：[experiments/2026-07-30-r2d3-sequence-psro-v2-readiness.md](../../experiments/2026-07-30-r2d3-sequence-psro-v2-readiness.md)
