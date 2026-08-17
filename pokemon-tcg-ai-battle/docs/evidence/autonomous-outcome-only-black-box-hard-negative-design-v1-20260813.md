# Outcome-only black-box / hard-negative reweighting 設計 v1

## 結論

V4 seed1 の common24 96局は `54W/42L/0F`（56.25%）だったが、公開 action として再構成できたのは8件（全て `SKILL`）だけである。したがって native action label、teacher behavior、hard BC、AWR の教師入力としては不十分であり、現在のV4 checkpointから直接 policy update を開始してはならない。

次の最小実装は、終端 WDL だけを使う二段構成とする。

1. `META_TRAIN` の terminal outcome を opponent ごとの hard-negative **スケジュール重み**へ変換する research-only adapter。
2. その重みで評価対象を選び、public state と合法 action のみを読む bounded な self-owned black-box policy candidate を同一 common24 evaluator で比較する。

1 は既存 artifact から read-only に作成可能だが、policy を更新しない。2 は現行V4 checkpointの内部に公開された tunable parameter surface がないため、V4 checkpointへ直接接続するものではない。最短の候補経路は、既存 `PolicyParameters` / `ProposalMixtureController` の Rule v0 fallback を native pool runnerへ接続する研究専用 adapter である。V4 seed1/seed0 は control/diversity として保持し、候補の採用基準は常に frozen BestKnown deck+agent pair とする。

本設計では性能 run、CABT、training、submission、Champion変更を起動していない。合成 evaluator、native action label、private state、`local_eval_only` asset の teacher/behavior 利用は許可しない。

## 現在の一次証拠

| 項目 | 値 |
|---|---|
| V4 run root | `runs/final-sprint-autonomous/v4-seed1-public-trace-meta-train-common24-96-serial-20260813-v1/` |
| protocol | broad pool 24 IDs × 両seat × repetition 2 = 96 |
| result | 54W / 42L / 0F、seat0 21/48、seat1 33/48 |
| summary SHA | `df9148eb8550e0f5ecba8385335e6d02a15d4dcb86d186a6f80a1d55985a3137` |
| ledger SHA | `6d39fe80c20bc8360360396fe180fef04b3d2b3864ce55e8b6f283ee49095630` |
| public trace SHA | `40ca755cb0706033a7a5eaff2a695458e535346a37b5e462677476742bdc1afb` |
| representable action | 8 events、全て `SKILL`、8W/0L |
| trace boundary | private-token scan 0、native/teacher label 保存なし、authority 全て false |
| subject checkpoint | `runs/meta-specialist-v4-archaludon-dagger-wave4-strict-paired/bc-checkpoints/seed-1/best-recurrent-bc-v4.pt` |
| checkpoint file / tensor SHA | `ec08ace5fb25352758a9f950694134ef6544ec69b23c00047101e588e3d06319` / `17682967a16c955ccd009858e036ef69e54d3efcd32bb0de83bebb64aa7c0244` |
| evaluator SHA | `0cbac2789e08758d14783922c5c7145f25701a47d978b3d9df9d132aec4eed84` |
| broad config / pool SHA | `832273ff656280d2556c9df09a9a3db9f2564a181be78a3e658509d3b396209b` / `e0013cf31b3e6e24db54591faeef6f092b9ebf85247bd0e57598eb8d447f20ca` |

この broad 24 は meta distribution 上 `META_TRAIN` 20件 + `META_FINAL` 4件である。全 row は `local_eval_only` で、`teacher_behavior_allowed=false` である。従って、V4 の96局 ledgerをそのまま META_TRAIN 学習入力とせず、splitを再検証して held-out 4件を除外する必要がある。

## 再利用できる既存 API

| 責務 | 既存 API / source SHA | 再利用方法 | 境界 |
|---|---|---|---|
| WDL実行・同一 strata | `scripts/run_performance_first_arena_v1.py` (`build_wave6_arena_games`, `run_wave6_pool_game_v1`) / `bf1d325cae93874aa0878a6b4c3f1abadbcd4e4143ca077692e4e9fef42f08c6` | `EvaluationGameV1` に candidate/control の policy/deck/pool/evaluator/seat/seed を bindし、既存 pool を相手として使用 | production `main.py`、`agents/rule_agent.py`、既存 evaluator は編集しない。現ファイルは dirty なので実装前に再hashする |
| fault-safe ledger | `scripts/parallel_cabt_evaluator_v1.py` / `b633fa02c910353f7aedd40dbf974b451976fe9bd39d7eb505a5f15b6c8999ba` | `run_parallel_cabt_evaluation` と `aggregate_ledger_v1` を実運用入口にする | faultをdropせず分母へ残し、`DONE`/fault0/ゲーム一意性を確認する |
| outcome集計・paired比較 | `src/mage_ptcg/continuous_league/evaluation.py` (`aggregate_records`, `compare_evaluations`) / `c68d1b206586fa234c7bc41832a81ab01b6008801fe6b5ace50602b8ae9a66cb` | opponent equal score、seat、block bootstrap、paired delta の再計算に使う | 入力 schema を既存 ledgerへ明示変換し、summary の値を盲信しない |
| split/weight gate | `src/mage_ptcg/meta_specialist/dynamic_meta_train_curriculum_v1.py` / `49c770525b43f4e1c39a694dff243ba4f90f6b616c0f2b0702640f169843ad83` | `META_TRAIN` だけに hard-negative weight/quotaを配分、family floor/cap、fault/seat/reliabilityを継承 | これは opponent sampling metadata。teacher label、behavior permission、training authorityを付与しない |
| alternating state/rollback | `src/mage_ptcg/meta_specialist/alternating_meta_optimizer_v1.py` / `15687de5f271e3323297464b33add70a7c250308812eaccff1050b70384b1d47` | `POLICY_FIXED_SHORT` と `DECK_FIXED_LONG`、native baseline、96→384→768→1536、checkpoint SHA、rollback を束ねる | 現 API は research-only dry-run。`execute/training/promotion/submission/longrun` は常に false |
| candidate parameter surface | `src/mage_ptcg/optimization/outcome.py` (`PolicyParameters`, `ProposalMixtureController`) / `a992842f6b6d4ea5007c29e93e9da37eb138c540325abd16614b862fa96a54c3` | Rule v0を合法 fallbackとし、source/action-type/phase/threshold の有限パラメータを candidate factory へ渡す | 同ファイルの synthetic opponent helper は使用しない。native pool runnerを別の research-only bridgeで注入する |
| priority storage (将来) | `src/mage_ptcg/policy_learning/r2d3/online_collection.py` / `23de5658938a2f64b68d91dc8a59efd81be9f0974418619cef8e25ca822cd418`、`replay.py` / `450ba379acf9e4f5291755adeb3d93f864897031dc42f8655c1915d1637a4a7d` | `MixtureManifest` と `AlternatingReplayPartitions` / `update_priorities` を self-owned outcome rollout の priority sidecarへ再利用可能 | 現V4 traceは action support 8件なので R2D3 transition/学習へ直結しない。直ちに learner を起動しない |
| identity/package | `src/mage_ptcg/bootstrap_champion/contracts.py` (`DeckAsset`, `PolicyAsset`, `JointCandidate`) | policy/deck/simulator identityと候補IDの計算に形を合わせる | local_eval_only opponent を提出 candidate の policy asset として扱わない。teacher APIは呼ばない |

## A/B 案の比較

### A: outcome-only hard-negative schedule reweight

**入力**は fault-free terminal WDL、`opponent_id`、seat、seed、family、candidate/control identityだけとする。action label、public traceの action row、private field は入力しない。

各 `META_TRAIN` opponent (o) の score を

```text
s_o = (W_o + 0.5 D_o) / N_o
h_o = 1 - s_o
```

から作り、未観測・低supportは `h_o` だけで過剰に増幅しない。次の deterministic mixture を採用する。

```text
raw_o = reliability_o * (0.70 * h_o + 0.15 * underexposure_o + 0.15 * diversity_o)
w_o   = capped_normalize(raw_o, opponent_cap=0.35, family_cap=0.55)
quota = family_floor + largest_remainder(w_o)
```

`reliability_o = max(0.10, 1 - fault_rate_o)`、seat imbalance は statistics として保存する。現在の dynamic curriculum の floor/cap と held-out zero exposure を再利用し、式と全定数を manifestへ固定する。`META_DEV`/`META_FINAL` は weight=0、quota=0、ledgerから除外する。

A の出力は以下を持つ immutable sidecarである。

- schema、iteration、seed、split、source run root、ledger/summary/manifest/protocol/evaluator SHA
- candidate/control policy SHA、deck SHA、pool/meta manifest SHA
- opponent/family、W/D/L/fault、seat別分母、`s_o`、`h_o`、underexposure、diversity、raw、capped weight、quota
- formula version、cap/floor、normalization、excluded heldout IDs
- `research_only=true` と全 authority false

A は現行V4 96局から**read-onlyに派生可能**だが、V4が56.25%で native BestKnown未満のため、これを改善対象の target/label として扱わない。Aだけでは candidate policyは生成されず、したがって単独で「fine-tune完了」や「longrun GO」にはならない。

### B: outcome-only black-box policy search

B は terminal WDL を objective にする derivative-free search である。candidateは public observation の合法 option type、phase、既存 Rule v0 proposalだけを読み、未知・不正・multi-select は baselineへ fail-closed fallbackする。native actionを正解扱いせず、teacher labelsも作らない。

最短の実装面は既存 `PolicyParameters` であり、bounded scopeは次に限定する。

- `source_weights`: `rule/family/primitive`、各 `[-4,4]`
- `action_type_weights` と `phase_weights`: finite な小範囲（初回は事前登録した2〜3軸のみ）
- `confidence_threshold`、`minimum_score_margin`、`rule_delegation_threshold`
- tie-break は `RULE_THEN_SOURCE_THEN_INDEX`、fallback は Rule v0 固定

候補 factoryは `ProposalMixtureController` を使うが、同モジュールの synthetic opponent helperは使わず、`opponent_pool_v1` 解決結果を `EvaluationGameV1` に注入する。候補の identityは policy config SHA + root policy/deck SHA + pool/config/evaluator SHAで閉じる。Aの weightは「次の opponent quota」を決めるだけで、action targetへ変換しない。

**V4 checkpointへ直接Bを適用する案は現時点で未実装・非即時**である。checkpoint bytesに安全な public parameter surfaceがなく、trace action supportも8件しかないため、V4 logitsへ後付けの action label/heuristicを注入しない。V4 seed0/seed1は control/diversity として残し、B candidateは Rule v0の研究専用 overlayとして別 policy identityで評価する。V4系を本当に black-box search対象にする場合は、別途 public legal-score adapterを設計し、その adapter自体を96局 gateへ通す。

### 推奨順序

1. Aの split-safe manifest builderを先に作る（実行せず、既存96から検証可能な read-only artifactを作る）。
2. Bの native-pool bridgeを TDD で作る。candidate factory、manifest、game identity、fallback、authority falseを先に閉じる。
3. frozen `BestKnown` control pair + B baseline + 2候補を同一 common24 96局で比較する。V4は診断 controlとして別armに置けるが、promotion比較の control ではない。
4. 96 gateを通った1候補だけ384へ進める。Aの weightsは次 blockの META_TRAIN quotaを更新するが、held-out exposureは常に0にする。

## 96 → 384 gate

### 96局 screen（昇格判定ではなく候補選別）

次を全て満たさない arm は `SCREEN_INVALID` または `NOT_PROMOTABLE` とする。

- baseline、candidate、frozen BestKnown controlが同一 `(opponent_id, seat, repetition, seed, evaluator_sha)` strataを共有する
- 96/96 `DONE`、fault 0、重複/欠落 game_id 0、faultを分母から隠さない
- policy/deck/pool/config/evaluator/runner SHA が manifest、ledger、summaryで一致する
- candidate override/support/fallback、seat 0/1、opponent familyの分母が記録される
- `META_DEV`/`META_FINAL` exposure=0、`local_eval_only` rowは opponent evaluationとしてだけ使う
- raw ledgerから paired loss→win / win→loss を再導出できる
- candidate scoreが **現在の frozen BestKnown pair** を明確に上回る。V4 56.25%単独を基準にしない

96局で差が小さい場合は「方法を棄却」せず `SCREEN_ONLY` とし、ただし384へ自動継続しない。action supportの少なさはBの label failureではなく、black-box objectiveの variance/coverage不足として報告する。

### 384局 confirmation

96 gateを通った候補だけ、seed-disjoint 4×96（または同一厳密 protocolの384）を fresh run rootで評価する。

- aggregate candidate - BestKnown が事前登録 `+3.0pt` 以上
- 両seatで BestKnown 比の悪化が `-5.0pt` を超えない
- 4 blockすべて fault 0、support floorを満たす
- hard-negative weightsが1 opponent/familyへ集中せず cap/floorを守る
- 2 block連続 regression、candidate identity/SHA mismatch、held-out exposureがあれば即停止して直前BestKnownへ rollback

未達時は `NOT_PROMOTABLE` とし、768/1536、longrun、Champion変更、submissionへ進めない。384通過後も768/1536は別 gateであり、この設計書だけでは起動権限を与えない。

## deck-policy alternating への接続

交互最適化は `CandidateStateV1` 相当の状態に、次を追加した新規 sidecarで束ねる。

```text
iteration_id
phase = POLICY_FIXED_SHORT | DECK_FIXED_LONG
stage_games = 96 | 384 | 768 | 1536
candidate_id / parent_candidate_id
policy_config_sha256 / deck_sha256
best_known_pair_id + native/control policy/deck SHA
hard_negative_manifest_sha256
meta_manifest_sha256 / schedule_sha256
evaluator_sha256 / protocol_sha256 / seed_universe_sha256
candidate_score / control_score / fault_count / seat_summary
regression_journal / rollback_descriptor
authority = all false, research_only = true
```

- `POLICY_FIXED_SHORT`: deck mutationだけを変え、policy config SHAは不変。Aの重みと同一 common24で deck candidateをscreenする。
- `DECK_FIXED_LONG`: deck SHAは不変、Bの bounded policy parameterだけを変える。policy candidateは前段BestKnownに対して比較する。
- 各 phaseは `EvaluationGameV1` の exact strataと source SHAを再検証し、checkpoint/manifestの canonical SHAを再計算する。
- 2回連続の native regression、fault、seat collapse、SHA mismatchで最後のBestKnownへ rollbackする。promotion/submission authorityは別の明示判断まで false。

この接続は「deck mutationが先に良いか、policy parameterが先に良いか」を同じ outcome objectiveで比較できるが、B candidateの public adapterがGREENになるまで実行しない。

## 即時実行可否と最短の次実装

| 作業 | 現artifactで即時可能か | 判定 |
|---|---:|---|
| V4 96局の WDL を再集計し、META_TRAIN/heldoutを検出 | yes（read-only） | A の入力検証に使える |
| deterministic hard-negative weight sidecarを作る | yes（新規read-only builderのみ） | teacher/training inputではない |
| V4 checkpointを直接outcome-only policy update | no | tunable public parameter surfaceがない |
| existing `PolicyParameters` をnative poolへ接続 | no（bridge実装が必要） | 最短のB実装候補 |
| R2D3/AWRを現V4 traceから開始 | no | 8 action eventsでsupport不足、native labels禁止 |
| 96→384を現V4結果だけで開始 | no | BestKnown比較ではなく、candidate signalも成立していない |

次の実装受入条件は、(a) A sidecarがheldoutを排除し source SHAを再計算、(b) B factoryが合法 fallback・candidate identity・authority falseを閉じ、(c) synthetic opponentを呼ばず既存 native poolへ接続、(d) mocked evaluatorを用いた TDD で game strata と paired ledger を検証、の4点である。これらを満たすまで性能 runは開始しない。

## 残課題 / リスク

- 現 broad 24 の `local_eval_only` は評価用であり、training/behavior permissionを自動で意味しない。Aの outcome sidecarを「training dataset」と呼ばない。
- V4 checkpointの action support不足を heuristic target で埋めると、今回停止した hard BC と同型になる。Bは outcome objectiveによる black-box比較に限定する。
- `engine_seed_supported=false` のため、seedは再現可能な stratum identityであって完全な RNG制御の保証ではない。blockとseat supportを残す。
- working treeは多数の既存dirty差分を含む。上表の source SHAは本設計時点の bytesであり、実装開始時に再hashし、SHA変更をmanifestへ記録する。

## Context pack 更新候補

外部 pack へは次だけを追記すればよい（生ログは転載しない）。

1. V4 seed1 96局 `54W/42L/0F` と public action 8件のため action supervision/AWR を起動しない。
2. broad 24 は META_TRAIN 20 + META_FINAL 4、全 local_eval_only。heldout除外なしの outcomeを curriculumへ流さない。
3. A（hard-negative schedule）とB（Rule v0 public black-box）の責務を分離し、A単独は policy updateではない。
4. 96 gateの比較対象は Rule v0 ではなく frozen BestKnown deck+agent pair。384は `+3pt / fault0 / seat非崩壊 / cap・floor` が必要。
5. 現時点の実行状態は `DESIGN_ONLY / PERFORMANCE_NOT_STARTED`。次に必要なのは新規 research-only adapterのTDDであり、V4再学習やlongrunではない。

## 検証履歴

- graphify fast-path queryで既存 runner、outcome aggregation、dynamic curriculum、R2D3 replay、alternating optimizerの依存関係を確認した。
- 本設計ターンでは性能/CABT/training/submissionを起動していない。
- 新規コード、既存production、既存performance artifactは変更していない。
