# MAGE-PTCG Autonomous Meta Fine-Tuning 引き渡し用 Context Pack

作成日: 2026-08-13 JST  
対象: このCodexチャットで、3:56以降の作業と最新の Strong Asset / Autonomous Meta-Fine-Tuning 方針を ChatGPT に引き渡すための背景資料  
目的: 既存の強い `deck + agent` population を共通arenaで比較し、現時点のBestKnownを確定し、そのnative pairを起点に上位meta分布への適応、deck/policy交互最適化、提出候補、再開可能な長時間ループまで閉じる。

補助的な前史packとして、3:56以降の監査・V4/V5・residual・teacher・evaluation noiseの逐次履歴は `docs/status/chatgpt_context_pack_since_0356_2026-08-12.md`、Performance-First sprintの中間証跡は `docs/status/chatgpt_context_pack_final_sprint_2026-08-12.md` に保持している。本ファイルはそれらを参照したうえで、Strong Asset Census以降の最終方針、native ranking、mutation common-protocol再評価、BestKnown分類、longrun/package gateを最新値へ統合した引き渡し版である。

## 0. 最新目的と絶対条件

最終目的は Rule v0 や Wave6 を改善すること自体ではない。現在利用可能な強い `deck + agent` pair を比較し、次の区分を一次artifact/SHA付きで確定することである。

- `EvaluationBestKnown`: `local_eval_only` を含むローカル性能上限。
- `TrainingEligibleBestKnown`: 明示的 training-local permission がある pair の上限。
- `SubmissionEligibleBestKnown`: permission、package closure、runtime、legal deck、entrypointを満たす提出可能上限。
- `BestKnownArchaludon`: Archaludon系の現行上限。
- `GlobalBestKnown`: 全archetypeの現行上限。

BestKnownを起点に、以下を交互に行う。

1. 大量の opponent deck/agent pool から固定した上位meta分布を作る。
2. native policy を保持した直接調整を行う。
3. policy固定で deck mutation race、deck固定で policy candidate race を行う。
4. 96→384→768→1536局の successive-halving を行う。
5. native baselineを全blockへ含め、fault、seat、seed、runtime、coverageを記録する。
6. native BestKnownを複数独立blockで再現的に超えた場合のみ、AWR/filtered BC、public-state value、必要ならpublic-only search/Qへ進む。
7. さらに通過した場合のみ再開可能な longrun を `LONGRUN_READY` → `LONGRUN_STARTED` へ進める。

ユーザーが明示的に停止した経路・再実行禁止経路:

- Lucifer hard-label / outcome-weighted hard BCの追加sweep。
- tomatoの同型AWR、uniform legal、Rule prior、exact/coarse residual、V5 set-context head、NLL-only fine-tune。
- 24/48局だけでpromotionすること。
- Rule v0を最終目的と誤認すること。

絶対に行わない操作:

- `git commit`、`git push`、remote branch作成。
- Champion/default agentの変更。
- Kaggleへのsubmit/API外部送信。
- external assetを無許可でsubmission bundleへ混入。
- `local_eval_only` をtraining/behavior/submission permissionへ暗黙拡張。

## 1. これまでに確定したStrong Asset inventory

### 1.1 Census

102 asset の棚卸しを行った。

- `docs/evidence/strong-asset-census-20260812.json`
- `docs/evidence/strong-asset-census-20260812.md`
- pool manifest SHA: `e0013cf31b3e6e24db54591faeef6f092b9ebf85247bd0e57598eb8d447f20ca`
- 102 entries = public 71 / internal 31。
- policy unique 58、declared deck unique 77、raw deck unique 79。
- smoke true 101、smoke false 1。
- 現行 source と sealed teacher snapshot が一致して、直ちに再利用可能な training artifact は tomato と Lucifer の2件。
- census上の permission-qualified 候補は、旧 decision/evidenceも含めて tomato、Lucifer、plamen、内製 grimmsnarl/rocket/nihei を分離表示している。ただし現行 source commitに対応するsealed snapshot readyは tomato/Lucifer の2件。

### 1.2 permission境界

`tomatomato_archaludon` と `lucifer19_battlecore` の teacher manifest は `allowed_usages=["training-local"]` を持つ。一方 pool manifestの `usage_boundary` は `local_eval_only` である。この2つを混同しない。

- tomato teacher snapshot: `runs/meta-specialist-teacher-records/archaludon-teacher-tomatomato-96/teacher_dataset_manifest.json`
  - permission manifest SHA: `441a6b83373c9ff2e7af765bb1d7e926bc5af9b3967537dc5d0be8d842956ca0`
  - records: 5,146、games 96、outcome 60W/36L。
- Lucifer teacher snapshot: `runs/meta-specialist-teacher-records/archaludon-teacher-lucifer19-96-strong-20260812/teacher_dataset_manifest.json`
  - permission manifest SHA: `83074da078f50149081a73c740803476f5b548d4c795f533b3f3e800ad74a70f`
  - records: 5,102、games 96、outcome 72W/24L。

native BestKnownを behavior policy として使う長時間 on-policy collectionへ進む場合は、対象pairごとに明示的な behavior permissionと新manifestを作る必要がある。評価可能だから学習可能、学習可能だから提出可能、とはしない。

## 2. native pair直接ランキング

### 2.1 fast96

`runs/meta-specialist-asset-ranking-primary-fast96-20260812`:

- requested 9,216 = 96 asset × 96 games相当。
- DONE 9,207、fault 9、draw 8、W3,697/L5,502。
- fault 9はすべて `medal_0019_df6f7443` の STEP_LIMIT。asset-level quarantine対象。
- ranking SHA: `7ad461caebd8bc8b21b1600f1719d8107f4654c0b2236c8ddcb57996f8b94b29`
- ledger SHA: `dc68512a72d57b804589692b2603f9b7fc872a61fc336d7ab93623641e57704a`
- manifest SHA: `161f18d0367d456b5a7cf1680d1d1a1ec619e9bbb82f984c0d1e6940c1269147`

一次順位:

| pair | W/L | score |
|---|---:|---:|
| plamen06_steel | 76/20 | 79.17% |
| tomatomato_archaludon | 73/23 | 76.04% |
| lucifer19_battlecore | 70/26 | 72.92% |
| aristophanivan_multiply | 65/31 | 67.71% |
| nihei_alakazam | 65/31 | 67.71% |
| dashimaki360_crustlecounter / kojimar_lucario | 64/32 | 66.67% |

これは96局の一次screenであり、後続blockで順位が逆転したため確定順位ではない。

### 2.2 top3 384→1536

top3 (`tomato`, `plamen`, `Lucifer`) を各blockで native pairそのものとして評価した。

- block1 ranking SHA: `58df60b5c3ace39fb827ede3adf229c2d3d626e14b9dd685dda0d18506f5690b`
- block2 ranking SHA: `776f499598d771af10bfcdec0b10e8578aa347d114b122099725c5ce38dc163e`
- block3 ranking SHA: `e8ea484359d9085cdd2003c2877672f5245f9bb0fc8b1945148f141ab031acc7`
- block4 ranking SHA: `27d665871f2bad82dc9877a9dbd5fea51767caf9c5b28ad9b4804138fec01cc5`
- pooled1152 evidence: `docs/evidence/strong-asset-top3-pooled1152-20260812.md`, SHA `3a84fa84a318a7e9c27f76fae2817868939dd611a83050df88ba6790c01358dc`
- pooled1536 evidence: `docs/evidence/strong-asset-top3-pooled1536-20260812.md`, SHA `e3299aac3a666cca3d19ab80a8feb0d7dddc861be155c2479345933eb22df863`

pooled1536結果:

| native pair | W/L | score | seat0 | seat1 |
|---|---:|---:|---:|---:|
| tomatomato_archaludon | 1107/429 | 72.0703% | 561/768 | 546/768 |
| lucifer19_battlecore | 1103/433 | 71.8099% | 554/768 | 549/768 |
| plamen06_steel | 1102/434 | 71.7448% | 567/768 | 535/768 |

差は tomato-Luci +4 wins、tomato-plamen +5 wins程度で、独立binomial近似CIでは差のCIが0を含む。したがって、現在の `EvaluationBestKnown` は tomato provisionalだが、tomato/Lucifer/plamen の三者near-tie populationを保持し、単一seedで一件に固定しない。

### 2.3 R7とslow assets

- R7 `public_archaludon_cinderace_r7`: 96局診断で 68W/28L = 70.8333%、fault0。ただし `smoke_ok=false`, `local_eval_only`、canonical identity不一致があり、training/submission不可。性能参照としてのみ保持。
- slow5: `kinoshita_pimc_search`, `ozawa_metal_psychic_search`, `water_box_search`, `waterbox_search_v3`, `tientrum_alakazam_search`。search budgetとnative runtimeが極端に遅く、fail-fast diagnosticは2/240程度で終了。common rankingをブロックするruntime-infeasible/quarantine群であり、性能0と解釈しない。
- `medal_0019_df6f7443` はSTEP_LIMIT faultが全9件に集中したため、順位昇格対象外。

## 3. immutable META distribution

追加:

- `src/mage_ptcg/meta_specialist/meta_distribution_v1.py`
- `scripts/build_meta_distribution_manifest_v1.py`
- `tests/meta_specialist/test_meta_distribution_v1.py`
- `configs/meta_specialist/autonomous_meta_distribution_v1.json`
- `docs/evidence/autonomous-meta-distribution-v1-20260813.md`

実artifact:

- manifest: `runs/final-sprint-autonomous/meta-distribution-v1/manifest.json`
- manifest SHA: `e430f1284e587e7f301f9e29abe377faad79ff5120a39c42b7b2f6a5223dd2ae`
- schedule: `runs/final-sprint-autonomous/meta-distribution-v1/meta_schedule.json`
- schedule SHA: `9db6e1d9ea3a6913f2080ac5ca4f08b748ed9fa1469cfd0a7a74d0253cb16b6a`
- rows 102、split 90/6/6。
- weight formula: top_meta 0.60 / hard_negative 0.25 / archetype_diversity 0.15。
- `META_DEV`: kiyotah_lucario, sue124_alakazam, skarin_dragapult, ozawa_crustle_v2, nihei_megalopunny, yaroslav_crustleaware_lucario。
- `META_FINAL`: plamen06_steel, lucifer19_battlecore, aristophanivan_multiply, nihei_alakazam, dashimaki360_crustlecounter, ozawa_starmie。
- `META_TRAIN`: 上記以外の90行。ただし teacher/behavior collectionは明示permission falseを拒否。
- evaluation quota 512、permission-filtered training quota 256。
- `META_FINAL` はcandidate selection/trainingに渡さず最終評価用に保持。

重要な解釈: 現実装の per-row normalized weight は102行へ正規化されるため、top-meta一行が0.60になるものではない。family-level quotaが必要なら、別のmanifest revisionとして作る。既存manifestを暗黙変更しない。

## 4. native tuning surface / preserving adapter

surface audit:

- `src/mage_ptcg/meta_specialist/native_tuning_surface_v1.py`
- `scripts/audit_native_tuning_surface_v1.py`
- audit output: `runs/final-sprint-autonomous/native-surface-v1/audit-v3.json`
- audit SHA: `abd20c6c2badc7d2471fb9aeac5bc95c298c6835a02b98fc0506191202078654`

観測:

- tomato/Lucifer: `DIRECT_PARAMETER_TUNABLE`, `RULE_EDIT_TUNABLE`, `NATIVE_FALLBACK_READY`。
- plamen: 上記に加え `SEARCH_ROLLOUT_READY`。
- plamen source knob: `USE_SEARCH`, `SP_BUDGET`, `BEAM_CAND`, `BEAM_MAXD`, `BEAM_MARGIN`。
- tomato/Luciferはsearch blockを持たず score/thresholdを主に持つ。
- AST監査はnative sourceをimport/編集せず、module-scope uppercase tuning surfaceだけを抽出。

preserving adapter:

- native agentを必ず先に呼ぶ。
- bounded score biasはMAIN selectionでのみ適用。
- illegal index、duplicate、min/max違反、例外、unknown contextはnative actionへ戻る。
- module loaderはsource SHA由来の名前で隔離し、repository `main`/`agents` moduleを復元。
- adapterとpilot tests合計 13 passed。

## 5. native policy candidate pilot

追加:

- `scripts/run_native_policy_candidate_pilot_v1.py`
- `tests/meta_specialist/test_run_native_policy_candidate_pilot_v1.py`
- `docs/evidence/autonomous-native-policy-pilot-v1-20260813.md`

script SHA: `bd546642dd4fac6b3af69cab4f80b9f804e2e9d51a6bf297718eb7baf7182c72`。

### 5.1 tomato score bias

config `ATTACK=+5, EVOLVE=+2, PLAY=-5`, config SHA `bd2cf688fac3f1e67943e1f167ebf64a4405d9b32d5ce3e13e54cf6295e009ae`。

| block | candidate | same-block native | delta | fault |
|---|---:|---:|---:|---:|
| 384, seed9500000 | 275/384 = 71.6146% | 259/384 = 67.4479% | +4.1667pt | 0 |
| 384, seed9600000 | 251/384 = 65.3646% | 271/384 = 70.5729% | −5.2083pt | 0 |
| pooled768 | 526/768 = 68.4896% | 530/768 = 69.0104% | −0.5208pt | 0 |

block2 seat0=70.3125%、seat1=60.4167%、gap9.896pt。短期改善は再現せず、longrun rejected。

### 5.2 plamen USE_SEARCH=0

config SHA `210f6386da24d0e1ab015e64cb3b2a277640733df5e5e37dbc8c749072b28a55`。

| block | candidate | same-block native | delta | fault |
|---|---:|---:|---:|---:|
| 92, seed9700000 | 73/92 = 79.3478% | 68/92 = 73.9130% | +5.4348pt | 0 |
| 368, seed9800000 | 276/368 = 75.0000% | 259/368 = 70.3804% | +4.6196pt | 0 |
| 368, seed9900000 | 267/368 = 72.5543% | 267/368 = 72.5543% | 0 | 0 |
| 736, seed10000000 | 532/736 = 72.2826% | 555/736 = 75.4076% | −3.1250pt | 0 |
| pooled1472 | 1075/1472 = 73.0299% | 1081/1472 = 73.4375% | −0.4076pt | 0 |

`USE_SEARCH=0` は第一・第二blockだけなら有望に見えるが、第三blockで下落し、pooledではnative未達。native BestKnown超過候補には昇格しない。

## 6. deck mutation

Luna担当が以下を追加した。

- `src/mage_ptcg/meta_specialist/deck_mutation_v1.py`
- `tests/meta_specialist/test_deck_mutation_v1.py`
- `docs/evidence/autonomous-deck-mutation-v1-20260813.md`
- implementation SHA `01c0a74f6b122904d417958ef3413c3ab835b840387f7b164fe3364ff70becf6`
- tests SHA `50b51f2b75e0dc4a71e49efefc6dd74cf97c2198d402a125551a5e7742cb748e`
- evidence SHA `c06871062b7043b0c1b402881b21a38eaae4212cc5e6628965397a0dd69a07a0`

契約:

- core signatureを保ち、1/2/3/4 physical swapを生成。
- 60枚、正のcard ID、known_card_ids、任意legality checkerを検証。
- exact multiset identityは既存 `deck_multiset_identity_v1` と同じSHA。
- parentと同一multisetを重複排除。
- candidate-only、promotion/training/submission authorityは常にfalse。
- CABT/学習/提出は未起動。

完了: plamen親deckからmutation manifestを生成し、native policy固定で736局screenと
4 independent block（各368局）のconfirmationを実施した。首位候補は pooled1472で親nativeを
+2.0041pt上回った。続くdeck-fixed policy raceではnative/defaultと`USE_SEARCH=0`が同率で、
policy variantの昇格は見送った。tomato mutationは未実施で、候補deckのCABT legality callback、
package closure、behavior/training permission、longrun接続は未成立。

## 7. longrun契約と現在のgate

Luna担当が追加したもの:

- `src/mage_ptcg/meta_specialist/longrun_autonomous_v1.py`
- `scripts/run_autonomous_meta_finetune_longrun_v1.py`
- `tests/meta_specialist/test_longrun_autonomous_v1.py`
- `docs/evidence/autonomous-meta-finetuning-longrun-v1-20260813.md`
- latest module SHA `cfb4a798b602546697c2bb7f846ee43c094e96a80c1ee853882e1b1680302c0e`
- latest test SHA `f47ddac9f11687f7a315c8e135e64e8dcfa722da6f26f561e1e59d9de4d18df3`
- evidence SHA `a47d3aeeb524f1634180d726cd9b8ba55dcb7f85a08b8ba6d5a8a6515480d8fe`

機能:

- manifest/source SHAとMETA splitを再検証。
- native baseline deck/policy/evaluator SHAを固定。
- 2 block、2 seed、fault0、seat gap、candidate改善、package closure、rollback readyのgate。
- atomic run manifest/progress/events、checkpoint、resume、stop、rollback。
- native regression 2回で安全停止。
- dry-runはtraining/CABT/submissionを起動しない。
- CLIは明示的runnerを持たず、`--execute`でもreadyなしならfail-closed。

実際に tomato score-bias の2 blockをgateへ入力した。

- run directory: `runs/final-sprint-autonomous/longrun-tomato-native-score-a`
- config SHA: `9943e733381043e6c4fec08dcbf5ba5b4d7b638f28ca4ae7e9887fac0e19825e`
- state: `BLOCKED`
- gate reasons: `seat_balance_ok`, `dev_improvement_ok`, `package_closure`, `rollback_ready`
- `launch_longrun_v1(execute=False)` result: `launch_allowed=false`
- `LONGRUN_STARTED`にはしていない。

nativeを2独立META_DEV blockで超え、seat/fault/package/rollbackを満たす候補が出るまで、長時間学習を起動しないのが正しい状態である。

## 8. 過去に監査・停止した経路

### 8.1 Lucifer hard BC

Lucifer snapshotをteacherとしてV4へhard-label/outcome-weighted BCした。旧実装ではepisode qualityがgradientで相殺される問題があり、後続corrected trainerで再計算した。fixed-six 24局ではseed0 12/24、seed1 14/24、aggregate26/48で、seed反転・seed0悪化。後に384局/armまで評価した結果、両seedともWave6未達。NLL低下は勝率改善を意味しない。追加sweepは停止。

### 8.2 tomato AWR / value

既存AWR runnerはV4用の実trainerへ接続可能な形を持つが、以前のtomato同型AWRはnative BestKnown超過を示さなかった。今回の方針では同型再試行をしない。将来使う場合は、cross-fitted actor-visible value target、record-group mass normalization、target authority、source SHA、behavior permissionを別manifestで固定する。

### 8.3 residual / OOD / Rule / V5

既存のexact/coarse residual、Rule alpha prior、V5 set-context、public OOD sweepは、coverageが低い、semantic boundary不一致、seed/seat不安定、またはproduction contract未接続であるため、最新方針では主線から外す。残したコードは研究専用・authority false・evidence付きで、再評価の根拠にはしない。

## 9. 全体の検証状況

今回の主要focused suite:

```text
30 passed
```

対象:

- meta distribution
- native tuning surface
- native preserving adapter
- native candidate pilot
- deck mutation
- longrun autonomous contract

その他の検証:

- `PYTHONPATH=.:src .venv/bin/pytest -q -s tests/meta_specialist/test_deck_mutation_v1.py tests/meta_specialist/test_joint_optimization_v1.py` → 22 passed。
- longrun tests → 6 passed。
- docs validator → `Validated 13 canonical documents.`
- `python -m compileall` → pass。
- `git diff --check` → pass。

worktreeは過去のユーザー作業と研究用untracked artifactで大きくdirty。既存差分を削除・reset・commitしていない。

## 10. 現在のBestKnown分類

| 分類 | 現在の状態 |
|---|---|
| EvaluationBestKnown | tomato provisional; tomato/Lucifer/plamen near-tie。R7は別診断上限だがsmoke/permissionで隔離 |
| TrainingEligibleBestKnown | native性能とpermissionを別軸で扱う。sealed teacher snapshot readyはtomato/Luciferのみ |
| SubmissionEligibleBestKnown | pool external pairはas-is package禁止。root Rule v0 package anchorは別fallbackでありStrong Asset BestKnownとは別 |
| BestKnownArchaludon | tomato provisional、ただしplamen/Luciferと統計的に未分離 |
| GlobalBestKnown | 102全件のslow/R7欠測とtop3 near-tieのため未確定 |

この「未確定」は失敗ではなく、96局順位を768/1536で再検証し、native baselineを必ず含める方針の結果である。

## 11. 次に実行する順序

### Step A: deck mutation manifest

plamen06_steelまたはtomatoを親にする。archetype core signatureと合法flex poolを明示し、1/2/3/4 swapをcandidate-only manifestへ保存する。CABT legality callbackで候補をfail-closed検証し、元native policyを固定する。

### Step B: policy-fixed deck race

各deck候補をnative policyで96→384→768→1536。全blockに親native baselineを含める。winnerが2独立blockでnativeを超えない限り、policy tuningへ渡さない。

### Step C: deck-fixed policy race

native surfaceからplamen search knobまたはbounded score rule candidateを作る。tomato score-biasのような1 block上振れは採用しない。candidate config SHA、source SHA、deck SHAを固定する。

### Step D: AWR/value

permission-qualified sourceを使う必要がある場合は、teacher hard BCではなく、actor-visible state + executed action + terminal return + cross-fitted V(s)から signed advantageを作る。behavior probabilityが無いからAWR不能、とはしないが、importance ratioを捏造しない。既存public valueはown hand/card bagを含むactor-visibleであり、厳密な両者公開stateと呼ばない。

### Step E: longrun

native over 2 independent META_DEV blocks、fault0、seat gap、package closure、rollback checkpoint、resume contractが揃ったら、初めて明示的runnerを渡して `execute=True`。それまでは dry-run/BLOCKED のまま。

## 12. ChatGPTへ渡すときの要約

この作業は「Luciferを学習させれば勝てる」という仮説を否定し、「強いnative pairをまず共通arenaで測り、そのままのnative policy/deckを初期資産として、複数block・複数seat・fault0で上回る候補だけを長時間学習へ進める」方針へ移行した。現時点で実装されたのは、immutable meta distribution、native surface audit、native-preserving candidate evaluator、deck mutation generator、atomic longrun gateである。

実験結果は、tomato score-biasが pooled 768でnative未達、plamen `USE_SEARCH=0`が旧1472 pooledでnativeに0.4076pt負け、plamen mutation candidateは23-opponentの旧4-block pooled1472で親nativeを+2.0041pt上回った。一方、native rankingと同じ24-opponent common protocolの4-block/1536ではcandidate 1099/1536=71.5495%、parent 1089/1536=70.8984%（+0.6510pt）に留まり、block2/4で反転しtomato native 1107/1536=72.0703%に届かなかった。したがって研究上のbounded candidate signalは得られたが、提出候補・LONGRUN_STARTEDは未成立。次の高価値作業はcandidateのpackage/permission/legality closureと、必要ならtomato mutationの独立screenである。AWR/valueはpermissionとtarget SHAが閉じたtraining pairに限定する。

## 13. 追加済みの交互最適化接続と実deck候補

Luna laneの交互最適化契約を実meta artifactへ接続したdry-runを実施した。

- `alternating_meta_optimizer_v1` の実manifest bind: `e430f1284e587e7f301f9e29abe377faad79ff5120a39c42b7b2f6a5223dd2ae`
- schedule bind: `9db6e1d9ea3a6913f2080ac5ca4f08b748ed9fa1469cfd0a7a74d0253cb16b6a`
- phase: `POLICY_FIXED_SHORT`
- stage: `96`
- `launch_allowed=false`
- authority: execute/training/promotion/submission/longrun 全 false
- dry-run state SHA: `c4abf58c5cbc1a8278f0051cda79308555f0cc90451442e2f452af1ba2d7a972`
- optimizer journal SHA: `cbab4cf429c6208b9f139e591d9967b7dbcac23e7aed9aa28521f19252956f10`

実deck候補は plamen06_steel native pairを親に生成した。

- candidate manifest: `runs/final-sprint-autonomous/deck-mutation-plamen-v1/candidates.json`
- manifest SHA: `67f63c1caadf1b478cd3865dda8193f778e4c1fc4e5d0903776b23a0e982690b`
- parent deck raw SHA: `fbe6ab59992260b0d6774abed19469be315521b5ed0546de8c20f329607693e6`
- parent multiset SHA: `d0b36a40a383c262723a60b14a0785f99074cd7816f187a39214f0ec12cc5ae0`
- parent policy SHA: `8a40be6825612ea927f5a6fd3396bb18e78a198d554e44cbb572acbab1b74ac3`
- core lock: `{169: 4, 190: 4}`
- known card IDs: root deck + 103 opponent deck union, 254 IDs
- candidates: 1-swap 4件 + 2-swap 4件 = 8件
- all candidates: 60枚、core維持、親multiset除外、candidate-only、authority false
- each candidate has a materialized `deck.csv` and file SHA in the manifest

この候補manifestの生成・合法構造・multiset SHAは検証済みで、candidate deckのnative policy
performanceも736局screen＋4×368局confirmationまで実施した。ただし候補manifestは
`candidate_only`、authority falseであり、CABT legality callbackの独立証跡、package closure、
training/behavior permission、BestKnownの正式更新、longrun接続は未実施である。

## 14. 現在の判断と未完了工程

現在の正確な状態は「設計・証跡・dry-run・候補deck生成・plamen候補のbounded性能確認・
policy raceまで完了、AWR/value学習とlongrun開始は未成立」である。次の順序を崩さない。

1. plamen mutation candidateのCABT legality/package/permission closureを独立に確認する。
2. 必要ならtomato親deckでも同じ8候補screenを実施し、archetype別の候補を比較する。
3. permission-qualified sourceでのみ、候補deck固定のAWR/value targetを設計する。同じhard BCは再実行しない。
4. `META_DEV` 2 blockでnative超過、package closure、rollback checkpoint、resume、schedule SHA再検証を満たした場合のみ `LONGRUN_READY` とする。
5. `LONGRUN_STARTED` と提出可能pairは、上記の実artifactが存在するときだけ確定する。現時点では未確定であり、推測で埋めない。

## 15. plamen deck mutationの実評価結果

plamen06_steel native policyを固定した8候補screenを実行した。

- runner: `scripts/run_deck_mutation_native_pilot_v1.py`
- runner SHA: `7896b58e429e029b43bd60f98b9d1435e4efc008dd84b60e829b437df2306dfc`
- candidate manifest SHA: `67f63c1caadf1b478cd3865dda8193f778e4c1fc4e5d0903776b23a0e982690b`
- 8候補×92局 = 736局、DONE 736、fault 0、W543/L193
- screen summary SHA: `8bb22edf47ddffc70f763aa1969124adb0c30204a389958118906dafbc3deb37`
- screen首位: `aab824462a561b8a459fc71e1a780dc46487f8ab9ed27514a2dfff17fb40b6d9`、76/92=82.6087%

首位候補と親nativeを各368局、4 independent blocksで再評価した。

| arm | block 1 | block 2 | block 3 | block 4 | pooled1472 |
|---|---:|---:|---:|---:|---:|
| candidate | 269/368=73.0978% | 271/368=73.6413% | 278/368=75.5435% | 283/368+1D=77.0380% | 1101W/1D/370L=74.8302% |
| parent native | 255/368=69.2935% | 270/368=73.3696% | 270/368=73.3696% | 277/368=75.2717% | 1072W/0D/400L=72.8261% |

- block1 delta: +3.8043pt
- block2 delta: +0.2717pt
- block3 delta: +2.1739pt
- block4 delta: +1.7663pt
- pooled1472 delta: +2.0041pt
- all arms fault0
- candidate seat scores: block1 71.1957%/75.0000%、block2 71.1957%/76.0870%
- max observed candidate seat gap: 4.8913pt
- block1 artifact SHA: `b347542057453a78c420fba0ed70a2b3c7d6ddbcd215248cc47093959a4ec7d1`
- block2 artifact SHA: `5aeac755dfa9d069dc44f6f0e6cf8dda833022bf35557063593bb9ad96420b43`
- block3 artifact SHA: `7f17835b96625a3d5dad66058aee90e28ed8d655680e4a7543bd76c42db21c1e`
- block4 artifact SHA: `708a8884548eac424fd68617f2d90b12b28f6e20b62f1b1c55a4dbae1cbd0f79`

これはnative超過の有望なbounded confirmationである。次に、同じ候補deckを固定して
native/default と `USE_SEARCH=0` を各368局比較した policy race を実施した。

## 16. plamen deck-fixed policy race

policy race artifact は `runs/final-sprint-autonomous/deck-mutation-plamen-v1/policy-race-736/`
である。runner SHAは
`71bd0c06608c756e2c911a7e6b3ff1e2388acd89c3339abbe9262ed447101926`、summary SHAは
`e941429da0252c9dd79f95ba294c7ba68d3eb3e8e9acbe12c71a3a1426a93f65`、ledger SHAは
`9dd25ee1fbf13c3a314c83b51015c4e2f32ac6254d4806f8d20291bbfb725bf7`、evaluator SHAは
`0cbac2789e08758d14783922c5c7145f25701a47d978b3d9df9d132aec4eed84` である。

| arm | W/D/L | score | fault |
|---|---:|---:|---:|
| native/default | 271/0/97 (368) | 73.6413% | 0 |
| `USE_SEARCH=0` | 271/0/97 (368) | 73.6413% | 0 |

fresh import監査では `{}` で `USE_SEARCH=True`、`{"USE_SEARCH":"0"}` で
`USE_SEARCH=False`、いずれも `_SEARCH_OK=True` だった。したがって同率は環境変数が
無視された証拠ではなく、独立seed blockで差を観測しなかった bounded result と記録する。
policy armは同じ native source SHA、候補deck SHA、23 non-self opponents、両seat、各8局で
評価した。authorityはtraining/promotion/submission/longrun全てfalseである。

判定は `policy_no_difference_observed`。deck mutation candidateは4 block全て親nativeを
上回ったため `bounded_confirmation_positive` を維持するが、policy variantは昇格しない。
候補はparent assetのlocal-eval-only境界を継承するため、package closure、behavior permission、
rollback checkpoint、AWR/value接続、longrun gateは未完了であり、`LONGRUN_STARTED`や
提出可能pairへはまだ昇格させない。

## 17. 現時点の最終状態

- `EvaluationBestKnown`: sealed 24-opponent common protocolのpooled1536では
  `tomatomato_archaludon` nativeが暫定首位（1107/1536=72.0703%）。tomato/Lucifer/plamenは
  near-tieである。plamen mutation candidateは23-opponentのpooled1472では親nativeを
  +2.0041pt上回ったが、common protocol 4 block/1536では1099/1536=71.5495%でtomatoを
  下回るため、candidateをEvaluationBestKnownへ直接置換しない。common block2/4では
  parent nativeがcandidateを上回った。
- `TrainingEligibleBestKnown`: 現行sealed sourceとしてtomato/Luciferが再利用可能。plamen
  mutation/nativeはlocal_eval_onlyでbehavior/training permissionを持たない。
- `SubmissionEligibleBestKnown`: Strong Asset poolのas-is pairはpackage/permission closure
  未成立。root Rule v0 package anchorはfallbackであり、Strong AssetのBestKnownとは別軸。
- `BestKnownArchaludon`: tomato native provisional。plamen mutationはMetal/Psychic系であり、
  Archaludon順位を直接更新しない。
- `GlobalBestKnown`: slow search assets/R7の未完了または隔離、native top3 near-tie、submission
  permission不足のため未確定。
- `LONGRUN_STARTED`: 未成立。tomato score-bias gateはBLOCKED、mutation candidateも
  package/behavior/rollback gate未成立。高価なlongrunへ未検証armを流していない。

このpackをChatGPTへ渡す際は、上記の研究候補positiveを「提出可能な改善」と誤読せず、
一次artifact SHAとauthority falseを必ず併記する。

## 18. common-protocol mutation rerun後の更新

23-opponent mutation confirmationとnative top3のprotocol差を解消するため、
`scripts/run_deck_mutation_common_protocol_v1.py`（SHA
`82c9caa21c4401996cdc691c2e6807c37140c4041a96c349bb5a42bfbd616ace`）を新規追加した。
candidate/parentを同じ24 opponent、両seat、各8局で4 independent block評価した。

| block | candidate | parent native | delta |
|---:|---:|---:|---:|
| 1 | 277/384=72.1354% | 268/384=69.7917% | +2.3438pt |
| 2 | 274/384=71.3542% | 279/384=72.6563% | −1.3021pt |
| 3 | 288/384=75.0000% | 260/384=67.7083% | +7.2917pt |
| 4 | 260/384=67.7083% | 282/384=73.4375% | −5.7292pt |
| pooled 1536 | 1099/1536=71.5495% | 1089/1536=70.8984% | +0.6510pt |

block1 summary SHAは`86992be532a77d5d2b0396c7199ca78d49a119804b7b56932db8e65c6c626f1d`、
block2は`6a2109b1c8921cf65626da42f9e0a8295fe588fe24f3fc92e9086401f0983e87`、block3は
`c104d040da4e1205a3e6451545fb3dfdfda8d8072333eb4bc9acb21540feccc6`、block4は
`e8e3078209944540a4b3080055ddd60d765bc66c78be7aaa9ac30bec7b7a9b09`。全3,072 rowは
fault0だが、block2/4で反転し、candidateはtomato native点推定を超えない。このためmutation
candidateは「親plamenに対するbounded positive signal」のまま、Global/EvaluationBestKnown
には昇格させない。詳細分類は
`docs/evidence/autonomous-bestknown-classification-v3-20260813.md`、機械可読版は同JSON。

## 19. 最終reconciliation（2026-08-13）

このpackの最終判定は、未完了のslow5/R7を性能0として扱わず、common protocolで完了したnative top3とmutation candidateを分離して記録することである。

| 区分 | 現在の判定 | 一次根拠 |
|---|---|---|
| EvaluationBestKnown | `tomatomato_archaludon` native provisional、1107/1536=72.0703% | `docs/evidence/strong-asset-top3-pooled1536-20260812.md` SHA `e3299aac3a666cca3d19ab80a8feb0d7dddc861be155c2479345933eb22df863` |
| TrainingEligibleBestKnown | tomato primary / Lucifer control（sealed snapshot軸） | `docs/evidence/strong-asset-finetune-readiness-20260812.md`、classification v3 |
| SubmissionEligibleBestKnown | Strong Asset pool外のRule v0 + root deck package anchor | archive SHA `da4bbe9d65c6d42feae2070fc02711efde4ffbe1ca08b9f9f00bfed3d96f9b9a` |
| BestKnownArchaludon | tomato native provisional | classification v3 JSON |
| GlobalBestKnown | unresolved | slow5/R7、permission/package、protocol未完了 |

mutation common protocolの最終artifactは`docs/evidence/autonomous-deck-mutation-common-protocol-20260813.md`（runner SHA `82c9caa21c4401996cdc691c2e6807c37140c4041a96c349bb5a42bfbd616ace`）。4 blockのsummary SHAは順に `86992be532a77d5d2b0396c7199ca78d49a119804b7b56932db8e65c6c626f1d`、`6a2109b1c8921cf65626da42f9e0a8295fe588fe24f3fc92e9086401f0983e87`、`c104d040da4e1205a3e6451545fb3dfdfda8d8072333eb4bc9acb21540feccc6`、`e8e3078209944540a4b3080055ddd60d765bc66c78be7aaa9ac30bec7b7a9b09`。全3072 rowはDONE/fault0だがcandidateはtomato nativeを超えない。

交互optimizer/longrunはmanifest・schedule SHA拘束、checkpoint/resume/rollback、stop-after-regression、dry-run fail-closedまで実装済みである。ただし実training/CABT longrunを起動できるauthority、candidate package closure、clean META_DEV、behavior permission、candidate checkpoint lineageが揃っていないため、`LONGRUN_STARTED`は未成立。ChatGPTはこのpackを読む際、研究実装の完成度と提出性能・権限の完成度を分けて解釈すること。

## 20. Student v3 θ0/AWR common24 実戦ゲート（2026-08-13）

fresh formal candidate artifactを、native `tomatomato_archaludon`と同一24 opponent・両seat・各2局・base seed `13000000`・max_steps `2000`・timeout `600s`・evaluator SHA `0cbac2789e08758d14783922c5c7145f25701a47d978b3d9df9d132aec4eed84`で96局評価した。全armは`DONE=96/96`, `fault=0`, `draw=0`である。

| arm | W-D-L | score | native差 |
|---|---:|---:|---:|
| native Tomato | 66-0-30 | 68.750% | control |
| Student θ0 | 7-0-89 | 7.292% | −61.458pt / −59勝 |
| Student AWR | 3-0-93 | 3.125% | −65.625pt / −63勝 |

一次artifact:

- θ0 reconciliation: `runs/final-sprint-autonomous/student-v3-native-common24-reconcile-96-v2/reconciliation.json`, SHA `81bfda4621ec1fc6952dd781e04a569d41c5e5389e5dabe821f2ecce03fab0bf`
- AWR reconciliation: `runs/final-sprint-autonomous/student-v3-awr-native-common24-reconcile-96-v2/reconciliation.json`, SHA `6482a3f613af330985bc0d5bcb829884f744a20433cc6e612d71aae189f38b93`, semantic SHA `10fd95ea939b332c2c49ed3e1687040a5e186cb7f3bdf3dc6431f2b56518bae5`
- native ledger SHA `bd2b5b420286c6b77960009d05e31f69df3ed512251b514b6e00d046021fadf7`
- θ0 ledger SHA `67765b935239d495b424f463076288a9065244082c3aeff5688863e5b3b38707`
- AWR ledger SHA `b968276f873ad9a8755208d43f11b3c7deb643124017651ecc6215196a24cbc6`

判定は `STRONG_ASSET_STUDENT_V3_NOT_PROMOTABLE`。validation/NLLが改善していても実戦 action/runtimeへ移らず、同型 hard BC/AWRの384→1536延長とlongrunは停止する。Student v3は「実装・formal provenance・fault-free evaluationまで通過したが、native代替性能を満たさない」研究分岐として保留する。

## 21. 次の主線（native population meta-overfit）

θ0/AWRを主線から外した後は、Tomato/Lucifer/Plamen nativeを固定したbehavior diversityとし、META_TRAINの評価用分布をhard-negative・archetype diversity付きで更新する。META_DEV/FINALは重み0かつ完全holdout、teacher/behavior/training authorityは明示permissionのある行だけに限定する。Full6はGrim ordered 4 decisionsをexact quarantineし、global near-duplicate componentをsplit単位で扱う修復が完了するまで学習入力にしない。deck mutationはnative policy固定のcommon24 raceとして継続する。

次の候補ができた場合のみ、native controlとの96→384→768→1536 gateへ戻る。`LONGRUN_READY`/`LONGRUN_STARTED`、package、Champion、Kaggle submissionは未成立であり、研究artifactのpositive signalを提出可能な改善と解釈してはならない。

common24 96局の追加provenance監査は `docs/evidence/autonomous-student-v3-common24-96-reconciliation-and-evaluator-provenance-audit-20260813.md`
(SHA `88897f1a496318893e3d592f3e44c438f42c5f4c6fbb036f4b07d07b3c033f56`) に固定した。θ0/AWR/nativeの3 armはseed `13000000..13000095`、両seat×2 repetition、fault0で、timeout/runner_refは実行時に使われるが現行ledgerへ永続化されないため、reconciliationのbindingは宣言値ベースである。この制約を含めても、θ0/AWRの大差負け判定は変わらない。

## 22. Native population curriculum iteration-0（2026-08-13）

θ0/AWRのnative 96局ゲート大差負けを受け、同型の学習延長を停止し、native strong pairをbehavior diversityとして保持する動的META_TRAIN laneへ移行した。iteration-0 artifactは `runs/final-sprint-autonomous/meta-train-curriculum-v1/iteration-0000/manifest.json`、file SHA `b9034c96b5f1cde1a33eab156b7e94ef73f9c35aa292492bec2cf07b8aee6a7a`、semantic SHA `df87a1d5866e2fb9791c9b560fa6bbf8d6798eedc1652fdec527fa816b83fde4`。source bindingは meta manifest `e430f1284e587e7f301f9e29abe377faad79ff5120a39c42b7b2f6a5223dd2ae`、schedule `9db6e1d9ea3a6913f2080ac5ca4f08b748ed9fa1469cfd0a7a74d0253cb16b6a`、broad config `832273ff656280d2556c9df09a9a3db9f2564a181be78a3e658509d3b396209b`、pool `e0013cf31b3e6e24db54591faeef6f092b9ebf85247bd0e57598eb8d447f20ca`。

quota96のうちMETA_TRAIN 20 opponentのみ非zero exposure、META_DEV 0、META_FINAL 0、teacher behavior eligible 0。training/promotion/submission/external execution authorityはすべてfalse。common24 outcomeはMETA_TRAIN 20とMETA_FINAL 4が混在するため、strict adapterなしのhard-negative updateには使わない。outcome schemaのgame/seed/protocol/subject identity不足も監査で確認しており、次iterationではreconciler由来の閉じた入力だけを受理する。

Full6修復はordered pointer-head 4件をexact quarantineし、episode＋non-ubiquitous near-duplicate component単位でsplitを再割当するTDD GREEN（7 focused tests）まで完了。primary 36,684-row formal再導出はCPU-boundで中断され、修復manifest/性能readyは未成立。したがって現時点で「meta curriculum artifactは完成、longrun性能ループは未開始」と記載する。candidateがnative controlを超えたという結果はまだなく、次はcandidate common24 96→384→768→1536で判定する。

## 23. Strict common24 → META_TRAIN outcome adapter（2026-08-13）

curriculum feedbackへ渡すoutcomeを、reconciliation由来のstrict adapterへ限定した。actual manifestは `runs/final-sprint-autonomous/common24-curriculum-outcome-adapter-v1/theta0-common24-96-v2/adapter-manifest.json`、file SHA `a4c27800fa80a855c7cd5b2fadca123e7bc9db74e7d09e20555d7d8f1d066e39`、semantic SHA `f2a5547d50f1813d2637c60cee62eef9d04bec22a79a2c6d47bb7378822e6064`、ledger SHA `18f1bec6a1f5804996060be95265b68ccb6929d39a2133f4b270723ee14d47aa`。candidate source 96 rowsからMETA_TRAIN 20 opponent/80 rowsだけを出力し、META_FINAL 4 opponent/16 rowsを拒否、META_DEVは0、fault0、game_idは80 uniqueである。

manifestはreconciliation file/semantic/request SHA、candidate/native deck・policy identity、common24 protocol、seed/base_seed、seat/status/fault、evaluator/runner closure、source SHAをbindし、tamper/heldout/duplicateをfail-closedにする。authorityはtraining/promotion/submission/external executionすべてfalse。evaluator v1がrunner source SHAをledgerへ直接保存しないため、adapter生成時のpost-hoc closureという制約は残る。adapter＋curriculum＋Full6＋reconciliation統合は33 passed、docs validator/diff-check PASS。

このartifactは入力契約を閉じただけで、性能改善の証拠ではない。θ0/AWRはnativeに大差負けしているため、そのoutcomeを使ったlongrunは開始しない。次の深い判断は、native 72.07% controlを超える新しいpolicy update方式と、native behavior permission/package closureを同時に満たすかどうかである。
## 24. Strict adapter re-seal（2026-08-13）

初回strict adapter seal後に native runner sourceが更新され、旧manifestの実行closure SHAと現行sourceが不一致になった。旧targetは `runs/final-sprint-autonomous/common24-curriculum-outcome-adapter-v1/theta0-common24-96-v2-pre-reseal-20260813/` に保全し、正式targetを同じreconciliation/meta sourceからatomic re-sealした。現行manifest file SHA `0679bc79af541759c67d480fdc1fef8bd9f8f1a955f0f5dddb69890e163faa89`、semantic SHA `6ff323c8ec5cf377f8f2c9c75230416dcbafe9dfab01b801aada557ad6369454`、native runner SHA `7c559621eb960f7be0a63ad53adf615bacaf30b7058885e6b433b2a83d951a32`、execution closure SHA `b8fe183f78245bb91a34b440b0087c3bb5f17a65b1b9a1af5d97d629f2cb0de2`、ledger SHA `18f1bec6a1f5804996060be95265b68ccb6929d39a2133f4b270723ee14d47aa`。80 META_TRAIN rowsのみ、META_FINAL 16 rows除外、META_DEV 0、fault0、authority全false。actual-source verifier 4 passed。
## 25. Tomato policy × mutation deck interaction final check（2026-08-13）

Plamen mutation deck `3f64513…`へ Tomato native policyを載せたcandidateと、Tomato native deck+policy controlをcommon24・同一seed `14600000..14600383`・各384局で比較した。candidate 264/384=68.7500%、control 260/384=67.7083%、+4勝/+1.0417pt、fault0/draw0。事前の約+3pt gate未達のためsecond blockは起動せず、candidate-only bounded interaction signalとして停止。summary SHA `ba6486331ec8171fa9848cd22e792b55496726b2b7e4efd5d1ba7cf897b41e4a`、run root `runs/final-sprint-autonomous/deck-mutation-plamen-v1/common24-tomato-policy-mutant-384/`、evidence `docs/evidence/autonomous-deck-mutation-tomato-policy-interaction-20260813.md`。これはBestKnown/TrainingEligible/SubmissionEligible昇格やlongrun開始の根拠ではなく、package/permissionはfail-closedのまま。
