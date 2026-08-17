# Meta Specialist 2レーン長時間学習 Readiness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** AlakazamとArchaludonについて、実データ・実optimizer・独立評価に基づき長時間学習の開始可否を判定し、全readiness gate通過時だけ長時間学習を開始する。

**Architecture:** `TrajectoryEpisodeV3`をcollector、representation、recurrent BC、critic、PPO/V-trace/AWR-CRR、evaluationの唯一の実データ境界とする。CABTのengine seed非対応下ではexact paired inferenceを禁止し、事前固定した独立armの層化比較を使う。

**Design:** `docs/superpowers/specs/2026-08-09-meta-specialist-two-lane-readiness-design.md`

**Tech Stack:** Python 3.12、PyTorch 2.11 CUDA 12.8、pytest、CABT/Kaggle environment、JSON/JSONL artifacts。

## Global Constraints

- 現在の`feature/belief-guided-search` checkoutで作業し、新規worktreeを作らない。
- 既存の未コミット・未追跡差分を上書き、削除、整形しない。同じファイルを複数agentが同時編集しない。
- commit、push、Kaggle提出、checkpoint promotionを行わない。
- production behaviorはRED→GREENのTDDで変更する。テストは実挙動を検査し、source textやmockの存在だけをassertしない。
- hidden information、illegal action、split leakage、schema/hash/provenance不一致はfail closedにする。
- synthetic fixtureはunit testにのみ使い、learner性能・critic calibration・promotion evidenceにしない。
- native engine replay capabilityが成立しない限りpaired performance inferenceを禁止する。
- 長時間runnerはTTYで単一progress bar、非TTYで約10秒ごとの集約snapshotを使い、`tee`へprogress streamを通さない。

### Task 1: 評価契約と現行baselineの固定

**Files:**
- Modify: `src/mage_ptcg/meta_specialist/evaluation_protocol_v2.py`
- Modify: `src/mage_ptcg/meta_specialist/decks.py`
- Modify: `scripts/run_meta_specialist_v3_eval.py`
- Modify: `tests/meta_specialist/test_evaluation_protocol_v2.py`
- Modify: `tests/meta_specialist/test_decks.py`
- Create: `docs/evidence/meta-specialist-two-lane-readiness-baseline-20260809.md`

**Interfaces:**
- Consumes: `validate_evidence_attestation_v2(payload)`, `evaluation_inference_allowed_v2(engine_seed_supported, replay_verified)`, `IndependentEvaluationRecordV2`。
- Produces: `IndependentEvaluationRecordV3`。必須fieldは`lane_id: str`、`training_seed: int`、`policy_role: Literal["theta0","candidate"]`、`policy_artifact_sha256: str`、`theta0_sha256: str`、`repetition: int`、既存v2のidentity/seat/family/outcome/fault provenance。
- Produces: `independent_readiness_summary_v3(records, *, bootstrap_seed=20260809, bootstrap_replicates=20000)`。lane×seedを等重みとするmacro delta、片側95%下限、cell deltaを返す。key `(lane_id,training_seed,policy_role,opponent_id,seat,repetition)` の重複を拒否する。
- Candidate recordの`theta0_sha256`は同lane/seedのtheta0 armの`policy_artifact_sha256`と一致しなければならない。各lane×seed×policyは6 opponent×2 seat×8 repetitionをattemptとして完全に持つ。fault/incomplete attemptは残してloss扱いとし、欠落cellはfail closedにする。

- [ ] paired inferenceがengine seeding supportとverified replayなしでは必ず拒否されることを回帰確認する。
- [ ] independent-arm評価がexact bool/type、canonical identity、seat整合、measured evidence、fault accounting、固定strataを要求することをTDDで補強する。
- [ ] fresh isolated importで`DeckAssetInput`が不要な`agents`依存を引かないよう、既存失敗をREDとして最小修正する。
- [ ] Task 1 affected suite、Meta Specialist suiteをfresh実行し、既知seed qualification未実行以外の回帰を0にする。
- [ ] baseline evidenceへGit状態、CUDA、テスト結果、engine capability、paired禁止、独立評価protocolを記録する。
- [ ] RED: `PYTHONPATH=.:src .venv/bin/python -m pytest -q tests/meta_specialist/test_decks.py::test_meta_specialist_parses_an_explicit_deck_with_poisoned_root_main tests/meta_specialist/test_evaluation_protocol_v2.py -k 'readiness_v3 or poisoned_root'`を実行し、import closureまたは未定義v3 APIで失敗することを記録する。
- [ ] GREEN: 同command、続いてTask 1 affected suiteを実行し、全件PASSを記録する。

### Task 2: 正しいrelational representationとmulti-selection

**Files:**
- Modify: `src/mage_ptcg/meta_specialist/representation_v3.py`
- Modify: `src/mage_ptcg/meta_specialist/neural_model_v3.py`
- Modify: `src/mage_ptcg/meta_specialist/actor_visible_features_v1.py`
- Modify: `src/mage_ptcg/meta_specialist/training_example_envelope_v2.py`
- Modify: `tests/meta_specialist/test_representation_v3.py`
- Modify: `tests/meta_specialist/test_actor_visible_features_v1.py`
- Modify: `tests/meta_specialist/test_training_example_envelope_v2.py`

**Interfaces:**
- Produces: `PublicEntityLocatorV3(owner_role: int, semantic_zone: str, zone_ordinal: int)`。public alignment専用でembeddingへ入れない。
- `EntityTokenV3`は`public_locator`を持ち、`ActionCandidateV3`は`source_locator`、`target_locator`、`selected_locators`、`selection_order_sensitive`を持つ。
- 旧v1 payloadはowner+zone+完全public snapshotが一意な場合だけlocatorへ移行し、複数一致は`RepresentationV3Error("ambiguous_public_locator")`で拒否する。
- `SpecialistModelV3.encode_candidate_v3()`はstable action IDをsemantic入力へ使わず、state-bound locator、selection prefix、canonical argsだけを使う。

- [ ] duplicate cardがactive/benchまたは同zoneに存在するfixtureで、owner+zone+locator endpoint解決のREDを作る。
- [ ] R3-Aの5 poolとR3-Bの192/4/2/512/0.05、全relation typeをbehavioral testで固定する。
- [ ] stable action IDだけをrename/permutationしてもsemantic logitsが変わらないREDを作る。
- [ ] unordered canonical set、selected mask、duplicate exclusion、order-sensitive selection stepのREDを作る。
- [ ] 最小実装でGREENにし、既存representation/BC/adapter testを回帰確認する。
- [ ] RED: `PYTHONPATH=.:src .venv/bin/python -m pytest -q tests/meta_specialist/test_representation_v3.py -k 'public_locator or required_topology or stable_id or multi_selection'`を実行し、各不足契約で失敗することを記録する。
- [ ] GREEN: 同file全体とactor-visible/training-envelope affected testsを実行し、全件PASSを記録する。

### Task 3: leakage-free splitとformal Gate 1 runner

**Files:**
- Modify: `src/mage_ptcg/meta_specialist/bc_trainer_v3.py`
- Modify: `src/mage_ptcg/meta_specialist/representation_benchmark_v3.py`
- Reuse/modify: `src/mage_ptcg/meta_specialist/local_dataset_v2.py`
- Modify: `scripts/run_meta_specialist_v3_ablation.py`
- Modify: `tests/meta_specialist/test_bc_trainer_v3.py`
- Modify: `tests/meta_specialist/test_representation_benchmark_v3.py`

**Interfaces:**
- Produces: `SplitManifestV3` JSON。`schema`、`source_dataset_sha256`、`ubiquitous_keys`、`assignments[{record_id,component_id,partition}]`、counts、overlap counters、`manifest_sha256`を必須とする。
- Produces: `build_split_manifest_v3(records, *, validation_fraction, ubiquitous_threshold) -> SplitManifestV3`。
- Produces: `run_gate1_v3(*, lane_roots, split_manifest_paths, seeds=(7,17,29), patience=3, min_delta=1e-4, output_dir) -> Gate1ResultV3`。
- Gate 1比較はcurrent R2、R3-A、R3-Bへ同じcomplete legal-action targetとcomponent assignmentを渡す。

- [ ] episode A→near X/Y、episode B→near Yのtransitive component fixtureで現splitをREDにする。
- [ ] ubiquitous keyを除外したshared connected-component splitを使い、episode/near overlap 0をmanifestで検証する。
- [ ] current R2 adapterとR3が同じfull legal-candidate/complete-action targetを学習する比較を実装する。
- [ ] top-1/top-3、rare-action recall、action-type NLL、p50/p95、CPU preprocessing、CUDA VRAMを出す。
- [ ] 3 deterministic training seeds、validation early stopping、equal budget、artifact-pinned two-lane inputを持つGate 1 runnerを実装する。
- [ ] bounded real-record Gate 1を実行し、選択representationと不採用理由をartifactへ固定する。
- [ ] RED: `PYTHONPATH=.:src .venv/bin/python -m pytest -q tests/meta_specialist/test_bc_trainer_v3.py -k 'connected_component or split_manifest' tests/meta_specialist/test_representation_benchmark_v3.py -k 'current_r2 or complete_action or three_seed or early_stop'`を実行する。
- [ ] GREEN: 両test file全体を実行後、Gate 1 CLIを`--dry-run`とbounded 32-record/laneで実行し、同じsplit hashを全model/seedが参照することを確認する。

### Task 4: teacher再検証とrecurrent BC

**Files:**
- Modify: `src/mage_ptcg/meta_specialist/teacher_revalidation_v3.py`
- Modify: `src/mage_ptcg/meta_specialist/bc_trainer_v3.py`
- Modify: `scripts/run_meta_specialist_v3_teacher_manifest.py`
- Modify: `scripts/run_meta_specialist_v3_bc.py`
- Modify: `tests/meta_specialist/test_teacher_revalidation_v3.py`
- Modify: `tests/meta_specialist/test_bc_trainer_v3.py`

**Interfaces:**
- Consumes: Task 3の`SplitManifestV3` path/hash。BC CLIは`--split-manifest`必須とし、独自splitを行わない。
- Produces: `TeacherManifestV3`。policy source/version、usage boundary、deck hash、current-pool games/outcomes/faults、weight inputs、derived quality tierを持つ。
- Produces: ordered `BCSequenceBatchV3` with `[B,T]` targets/padding/episode-start、burn-in length、component IDs。
- Produces: lane/seedごとのatomic best-validation BC checkpoint。Task 6のseal前は`theta0_status="bc_checkpoint_unsealed"`とする。

- [ ] policy source/version、usage boundary、deck fingerprint、current-pool results、faultを欠くteacher manifestを拒否するREDを作る。
- [ ] confidence/agreement/search/strength evidenceから1.0/0.7/0.4/0.2/0.0を導出し、stored default 1.0を拒否する。
- [ ] ordered episode sequence、境界reset、padding mask、burn-in、component-independent validationをTDDで実装する。
- [ ] Alakazam/Archaludonのteacherをcurrent validation poolで再評価し、manifestを固定する。
- [ ] 2 lane × 3 seedのbounded recurrent BCを実行し、best-by-independent-validation checkpointを保存する。
- [ ] RED: `PYTHONPATH=.:src .venv/bin/python -m pytest -q tests/meta_specialist/test_teacher_revalidation_v3.py tests/meta_specialist/test_bc_trainer_v3.py -k 'provenance or quality_tier or recurrent or padding or burn_in or supplied_split'`を実行する。
- [ ] GREEN: 同2 file全体とBC CLIの2 lane×3 seed `--dry-run`、各lane 2 episodeのbounded real integrationを実行する。

### Task 5: v3 trajectory schemaとbounded real collection

**Files:**
- Modify: `src/mage_ptcg/meta_specialist/trajectory_schema_v3.py`
- Modify: `src/mage_ptcg/meta_specialist/collect_trajectories_v1.py`
- Modify: `src/mage_ptcg/meta_specialist/cli.py`
- Modify: `tests/meta_specialist/test_trajectory_schema_v3.py`
- Add: `tests/meta_specialist/test_v3_collection_training_e2e.py`

**Interfaces:**
- Produces: `TrajectoryEpisodeV3` JSONL。episode ID、lane、ordered decisions、terminal outcome、actor version、opponent policy/deck hash、seat、environment/sampling seed、fault/retry provenance、source/split/theta checkpoint hashを必須とする。
- 各decisionはlegal action IDs、mask、base behavior logits/log-probabilities、chosen index/log-probability、reward、episode_start、recurrent state provenanceを持つ。
- Produces: `collect-v3 --lane {alakazam,archaludon} --bc-checkpoint PATH --split-manifest PATH --games N --output DIR`。schema/hash不一致はwrite前に拒否する。
- Task 5 artifactは`preseal_policy_checkpoint_sha256`としてTask 4のunsealed BC checkpoint hashを記録する。θ0またはsealedという語で偽装しない。
- Produces: `critic_examples_from_episodes_v3(episodes) -> tuple[CriticExampleV3, ...]`。terminal outcomeを各valid timestepへ伝播し、seat/family/normalized positionを保持する。

- [ ] actual collectorの1 episodeを保存・reloadし、decision順序、mask、base behavior、chosen log-prob、terminal/provenanceが一致するE2E REDを作る。
- [ ] synthetic/unit-only recordをreal evidenceとしてwriteしようとすると拒否するREDを作る。
- [ ] Alakazam/Archaludon各2 completed episodeのbounded fresh collectionを実行し、faultを含むattempted game ledgerを保存する。
- [ ] RED: `PYTHONPATH=.:src .venv/bin/python -m pytest -q tests/meta_specialist/test_trajectory_schema_v3.py tests/meta_specialist/test_v3_collection_training_e2e.py -k 'actual_collector or roundtrip or measured_evidence'`を実行する。
- [ ] GREEN: 同2 test file全体と`collect-v3` bounded commandを実行し、real episode artifactをTask 6へ渡す。

### Task 6: real outcome criticとsealed θ0

**Files:**
- Modify: `src/mage_ptcg/meta_specialist/critic_conditioning_v3.py`
- Modify: `src/mage_ptcg/meta_specialist/critic_warmup_v3.py`
- Modify: `src/mage_ptcg/meta_specialist/critic_v3.py`
- Modify: `src/mage_ptcg/meta_specialist/neural_model_v3.py`
- Modify: `src/mage_ptcg/meta_specialist/experiment_manifest_v1.py`
- Modify: `scripts/run_meta_specialist_v3_critic.py`
- Modify: `scripts/run_meta_specialist_v3_bc.py`
- Modify: `tests/meta_specialist/test_critic_conditioning_v3.py`
- Modify: `tests/meta_specialist/test_critic_warmup_v3.py`
- Modify: `tests/meta_specialist/test_critic_v3.py`
- Modify: `tests/meta_specialist/test_experiment_manifest_v1.py`

**Interfaces:**
- Consumes: Task 5のreal `TrajectoryEpisodeV3`、Task 3の`SplitManifestV3`、Task 4のBC checkpoint/teacher manifest。
- Produces: `CriticCalibrationV3` with overall/seat/opponent-family/trajectory-position CE/Brier/ECE、uniform Brier、outcome-value correlation。
- Produces: `Theta0ManifestV3`。allowlisted `source_files[{path,sha256}]`、config/data/split/teacher/model/critic hash、checkpoint hash、lane、training seed、seal statusを持つ。Task 5全episodeの`preseal_policy_checkpoint_sha256`がこのcheckpoint hashと一致しなければsealを拒否する。
- Produces: `seal_theta0_v3(checkpoint_path, manifests, source_allowlist, output_dir) -> Path`。atomic write後にreload/hash一致を検証し、不一致はsealしない。
- Source allowlistは本TaskのCLI entrypoint、import traceで到達した`src/mage_ptcg/meta_specialist/*.py`、明示config、Task 3–5 manifestsだけを許可する。

- [ ] completed real `TrajectoryEpisodeV3`のterminal outcomeを全timestepへ伝播するREDを作る。
- [ ] episode-balanced lossとoverall/seat/family/position strataのBrier、uniform比較を実装する。
- [ ] production conditioningからgame-seed modeを除去し、頻度<64をunknown、>=128だけdedicatedとする。
- [ ] checkpoint atomic save/reload/hashと、限定source/config/data/split/teacher/model/critic manifestを実装する。
- [ ] bytecode/transient/unrelated fileを除外し、未追跡source contentを含める。
- [ ] 各lane/seedでreal criticがuniform Brierを上回り、負相関がない場合だけθ0をsealする。
- [ ] RED: `PYTHONPATH=.:src .venv/bin/python -m pytest -q tests/meta_specialist/test_critic_conditioning_v3.py tests/meta_specialist/test_critic_warmup_v3.py tests/meta_specialist/test_critic_v3.py tests/meta_specialist/test_experiment_manifest_v1.py -k 'real_outcome or strata or threshold or atomic or allowlist'`を実行する。
- [ ] GREEN: 同4 test file全体と各lane/seed最低64 completed episodeのcritic calibrationを実行する。uniform非改善または負相関のcellはunsealedで停止する。

### Task 7: actual collection→3 learner E2E

**Files:**
- Modify: `src/mage_ptcg/meta_specialist/trajectory_targets_v3.py`
- Modify: `src/mage_ptcg/meta_specialist/train_from_trajectories_v1.py`
- Modify: `src/mage_ptcg/meta_specialist/cli.py`
- Modify: `src/mage_ptcg/meta_specialist/learner_common_v1.py`
- Modify: `src/mage_ptcg/meta_specialist/learner_ppo_recurrent_v1.py`
- Modify: `src/mage_ptcg/meta_specialist/learner_vtrace_online_v1.py`
- Modify: `src/mage_ptcg/meta_specialist/learner_awr_crr_v1.py`
- Modify: `scripts/run_meta_specialist_v3_rl.py`
- Modify: `tests/meta_specialist/test_trajectory_targets_v3.py`
- Modify: `tests/meta_specialist/test_learner_common_v1.py`
- Modify: `tests/meta_specialist/test_learners_v1.py`

**Interfaces:**
- Consumes: Task 6のsealed `Theta0ManifestV3`とTask 5のreal `TrajectoryEpisodeV3`だけを受理する。
- Produces: `LearnerSequenceBatchV3` with chosen `[B,T]`、behavior/target distributions `[B,T,A]`、legal/padding masks、hidden/burn-in、outcome/value target、actor version。
- Produces: `train-v3 --learner {ppo,vtrace,awr-crr} --theta0-manifest PATH --trajectory-root DIR --max-updates N --output DIR`。
- 各runはmodel/critic optimizer step、parameter delta、finite diagnostics、consume/reuse/discard counts、checkpoint/reload evaluationを保存する。

- [ ] actual collectorがv3 episodeを保存し、同じartifactを3 learnerが読むE2E REDを作る。
- [ ] `[B,T]` chosen log-probと`[B,T,A]` distribution/mask、padding、hidden、burn-inのshape contractを実装する。
- [ ] PPOのGAE、clipped policy/value、entropy、exact masked KL、gradient clipping、early stopを実optimizerへ接続する。
- [ ] V-traceのfuture-version拒否、exactly-once、actor+critic update、d40/90% horizon/position diagnosticsを実装する。
- [ ] AWR/CRRをshared replay/model/critic schema上の実optimizerにする。
- [ ] 同じsealed θ0からcollect→train→checkpoint→reload→bounded evaluateを3 learnerで完走する。
- [ ] RED: `PYTHONPATH=.:src .venv/bin/python -m pytest -q tests/meta_specialist/test_trajectory_targets_v3.py tests/meta_specialist/test_learner_common_v1.py tests/meta_specialist/test_learners_v1.py tests/meta_specialist/test_v3_collection_training_e2e.py -k 'sequence_batch or optimizer or exactly_once or checkpoint_reload'`を実行する。
- [ ] GREEN: 同4 test file全体と、同じ1つのsealed θ0/real trajectory rootから各learner 1 optimizer updateのCLI integrationを実行する。

### Task 8: 2 lane × 3 seed pilotと長時間開始判定

**Files:**
- Modify: `src/mage_ptcg/meta_specialist/promotion_gate_v1.py`
- Modify: `scripts/run_meta_specialist_v3_eval.py`
- Create: `scripts/run_meta_specialist_two_lane_pilot.py`
- Create: `scripts/run_meta_specialist_two_lane_long_training.py`
- Modify: `tests/meta_specialist/test_evaluation_protocol_v2.py`
- Modify: `tests/meta_specialist/test_promotion_gate_v1.py`
- Add: `tests/meta_specialist/test_two_lane_pilot.py`
- Create: `docs/evidence/meta-specialist-two-lane-pilot-20260809.md`
- Create run artifacts under `runs/meta-specialist-two-lane-readiness/`

**Interfaces:**
- Consumes: 6個のsealed θ0 manifest、Task 7 learner health artifact、train/validation/evaluation opponent disjointness manifest。
- Produces: `PilotManifestV1`。lanes=`[alakazam,archaludon]`、training seeds=`[7,17,29]`、6 held-out opponents、2 seats、8 repetitions、96 attempted games/policy/cell、bootstrap seed=`20260809`、replicates=`20000`、learner selection rule、全stop thresholdを固定する。
- Produces: `ReadinessDecisionV1`。cell deltas、macro delta、片側95%下限、2-seed片側90%上限、health/fault/leakage/hash gates、`START_LONG_TRAINING|DO_NOT_START`を持つ。
- `run_meta_specialist_two_lane_long_training.py`は`--readiness-decision`、`--pilot-manifest`、`--max-decisions-per-lane 100000`、`--checkpoint-every 10000`、`--health-every 5000`、`--dry-run`、`--resume`を持つ。
- 長時間runは`run_status.json`へPID、manifest hash、completed decisions、latest checkpoint、health、stop reasonをatomic更新する。readinessが`START_LONG_TRAINING`でなければexit 2で開始しない。

- [ ] pilot manifestへlane、3 training seeds、learner選択規則、θ0 hashes、held-out opponents、seat、repetition、stop rule、primary metricを結果確認前にsealする。
- [ ] 1 round health gateを実行し、fault、dead_rho、dlogp、trace product、parameter/optimizer finiteを判定する。
- [ ] health通過時だけ3 round pilotと各policy 96 attempted games/cellの独立層化評価を実行する。
- [ ] macro delta点推定+5pt、片側95%下限+2pt、6セル中5セル正、lane平均非負を機械判定する。
- [ ] 2-seed futility rule、fault/health/leakage/hash fail-closedを実装する。
- [ ] 全readiness gate通過時だけ、同じsealed inputsとlearner設定で長時間runnerを起動する。未通過なら起動しない。
- [ ] 最終報告に実行コマンド、artifact、テスト、数値、残リスク、長時間process PID/statusを記録する。
- [ ] RED: `PYTHONPATH=.:src .venv/bin/python -m pytest -q tests/meta_specialist/test_evaluation_protocol_v2.py tests/meta_specialist/test_promotion_gate_v1.py tests/meta_specialist/test_two_lane_pilot.py -k 'one_sided or six_cell or futility or long_runner'`を実行する。
- [ ] GREEN: 同3 test file全体、pilot `--dry-run`、long runnerのDO_NOT_START fixtureによるexit 2を確認後、実health/pilotを実行する。

### Task 9: 全体検証と独立レビュー

**Files:**
- Modify: `docs/evidence/meta-specialist-two-lane-pilot-20260809.md`
- Modify: `docs/status/current_status.md`
- Modify: `docs/status/handoff.md`
- Create: `docs/evidence/meta-specialist-two-lane-readiness-verification-20260809.md`

**Interfaces:**
- Consumes: Task 1–8のreports、test outputs、Gate 1、theta0、learner、pilot/long-run artifacts。
- Produces: requirementごとにcode/test/artifact/statusを対応させたverification report。未実施は`NOT_RUN`、失敗は`BLOCKED`とし、推測値を補わない。

- [ ] focused suites、Meta Specialist suite、関連full repo tests、`git diff --check`、docs validationをfresh実行する。
- [ ] actual CLIからrepresentation、critic、v3 schema、3 learner、evaluation gateへ到達することをcall-site監査する。
- [ ] synthetic evidenceがreadiness/promotion判定へ混入しないことを確認する。
- [ ] 独立reviewerのCritical/Important findingを解消する。
- [ ] `docs/status/current_status.md`と`docs/status/handoff.md`を実測結果に合わせて更新する。
- [ ] Verification commands: `PYTHONPATH=.:src .venv/bin/python -m pytest -q tests/meta_specialist`、影響したrepo-level test files、`.venv/bin/python scripts/docs/validate_docs.py`、`git diff --check`、`git status --short`をfresh実行し、exit codeと件数をreportへ記録する。
