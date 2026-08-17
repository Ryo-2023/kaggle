# Policy Learning Gate 5a 初回PPO Safety Pilot

## メタデータ

| 項目 | 値 |
|---|---|
| 日時 | 2026-07-27 JST |
| 担当 | human 実行、Codex 集計 |
| 種別 | local experiment / safety pilot |
| commit | `254a9f5becd9b431b3e47807634caf123024e6ee` を基点とする未コミット policy-learning worktree |
| branch | `local/offline-scaleup-v2` |
| model provenance | BC recurrent → PPO pilot、CUDA learner、actor 8 |
| simulator / data | `runs/policy-learning-gate5a/`、Gate 4 dataset、Gate 3 Rule fixed population |

## 目的と反証条件

- **問い**: BC recurrent初期化のon-policy PPO経路がfault-freeにrollout、update、evaluationまで到達するか。
- **反証条件**: illegal、candidate fault、unresolved timeout、NaN/Inf、entropy/KL gate、またはRule v0比10ポイント超の悪化。
- **固定条件**: exact deck 1種類、4 Rule opponent、各200 game、actor 8、terminal reward ±1、BC KL guard。

## 結果

| phase | 結果 |
|---|---|
| Value warm-up | CUDA、5 epoch、Huber loss 0.496258、完了 |
| rollout round 1 | 800/800 terminal、legal 799、candidate fault 0、`HARD_TIMEOUT` 1、`BLOCKED` |
| PPO update | 未実行、decision 0 |
| snapshot / Rule v0 evaluation | 未実行 |

- timeoutはcandidate side 0対`rule-v0-deck-74d86ec36fd144b9`で発生した。candidate callback errorはなく、fault attributionは`UNRESOLVED_TIMEOUT`である。
- raw legal 799 gameは369勝（46.18%）だが、201 game／244 decisionでRule v0 fallbackを含む。fallbackなし598 gameは216勝（36.12%）。fallback理由は全244 decisionで`actor-critic runtime supports only single-action prompts`、内訳は`selection_type=1`・min=max=2〜7のmulti-selectである。いずれもupdate前かつGate不通過のため、比較・promotion根拠ではない。

## 解釈と判断

- **観測事実**: GPU initializationとValue warm-upは動作したが、fault-free rollout条件を満たさず、learner更新へ到達しなかった。
- **判断**: `GATE5A_BLOCKED_UNRESOLVED_TIMEOUT_AND_MIXED_BEHAVIOR`。Gate 5b／5c、V-trace、Champion変更を開始しない。
- **次 action**: `run_gate5a_diagnostics.sh`でtimeout gameを1 actor×5とconcurrency 8×5で固定再現し、candidate／opponent／engine／workerを区別する。runtimeはmulti-selectをlegal top-kへ変更済みであり、fallbackまたはmulti-selectを含むepisodeをPPOから除外する。fresh fault-free rolloutを得た後だけPPO updateを許可する。

## Gate 5a-0／5a-1 診断追記

- 固定slotの再現はserial 5/5、concurrency 8の5/5が全て`DONE`となり、分類は`NOT_REPRODUCED`だった。従って初回の`HARD_TIMEOUT`をcandidate、opponent、engineのいずれにも帰属しない。
- serialのcandidate callback p95は12.954ms以下、concurrency 8のp95は542.635ms以下、最大665.911msであり、30秒watchdog未満だった。CABTはopponent callback／engine step／timeout直前状態を提供しないため、未記録項目を推測で補っていない。
- fallback集計は244 decision／201 episode（総11,973 decisionの2.038%）で、全件が旧single-action限定runtimeのmulti-select fallbackだった。新runtimeのlegal top-k処理は、まだfresh CABT stressで検証していない。
- 次はfresh 64-game candidate-only preflightでtimeout 0、candidate fault 0、illegal 0、fallback decision 0を確認する。Gateを通過してもPPOはまだ開始せず、必要なら一回のtargeted DAggerと256-game rollout preflightを先に行う。

## Fresh 64-game preflight（runtime修正後）

| 項目 | 結果 |
|---|---|
| terminal / legal | 64 / 64、64 / 64 |
| candidate fault / mapping / score identity | 0 / 0 / 0 |
| fallback decision | 0 / 1,074 |
| behavior log-prob | 1,074 / 1,074 finite |
| actor version / vocabulary / deck hash | 各1値 |
| multi-select top-k | 25 decision、19 episode |
| PPO利用可能 | 45 episode、597 decision |

- `runs/policy-learning-gate5a/preflight-64/league-64/`はrun gate `PASS`。multi-selectを合法に処理する修正は実CABTで確認できた。
- ただしmulti-selectはaction-set log-probを持たず、そのepisode全体をPPOから除外する。fallback 0は全decisionがon-policy PPO transitionであることを意味しない。
- 次はfresh 256局で同じ契約を確認する。PPO更新、DAgger、Gate 5b以降は未実行である。

## Fresh 256-game clean rollout（runtime修正後）

| 項目 | 結果 |
|---|---|
| worker / terminal / legal | 28 / 256 / 256 |
| candidate fault / mapping / score identity / fallback | 0 / 0 / 0 / 0 |
| decision / finite behavior log-prob | 3,748 / 3,748 |
| multi-select top-k | 85 decision、65 episode |
| PPO利用可能 | 191 episode、2,315 decision |
| game latency p50 / p95 | 42.386 / 88.737秒 |

- policy contractは`PASS`。version／vocabulary／deck hashは各1値であり、PPO対象transitionへfallbackは混入していない。
- 次工程は一回のtargeted DAggerである。Rule v0 proposalと不一致のsingle-action 1,720候補から最大1,024のみを再ラベルし、BC recurrent初期化のcheckpointを別identityで作る。PPO更新はこのcheckpointのfresh smoke／clean rolloutが通過するまで開始しない。

## GPU scale-readiness（DAgger後、PPO開始前）

| 項目 | 結果 |
|---|---|
| CUDA runtime | Torch 2.11.0+cu128 / CUDA 12.8 / RTX PRO 5000 Blackwell x1 |
| DAgger | BC recurrent明示初期化、1 epoch、validation forced-action除外 top-1 0.802182 |
| DAgger 64局smoke | 64/64 terminal・legal、candidate fault / fallback 0 / 0 |
| DAgger 256局clean | 256/256 terminal・legal、candidate fault / fallback 0 / 0 |
| clean policy contract | 3,721 finite log-prob、PPO利用可能201 episode / 2,551 decision |
| verdict | `READY_FOR_PPO_PILOT` |

- GPU device access拒否は再確認時には再現せず、CUDA learnerで全工程を完走した。DAgger後のvalidationは元BC recurrentのforced-action除外top-1 0.794182を下回らなかった。
- PPO update、snapshot、Rule v0 evaluationはまだ実行していない。次runは新しいartifact rootを使い、`bc-recurrent-dagger-stabilized`から初期化する。初回timeout/fallback混在artifactはresumeしない。

## PPO round 1 update 修復

- 新規pilotのround 1は800/800 terminal・legal、candidate fault／fallback=0で完走した。PPO対象は571 episode／7,510 decisionだった。
- 最初のGPU updateは`cudnn RNN backward can only be called in training mode`で停止した。原因はactor-time logitとの一致を保つ意図で、PPO learner全体をeval modeにしていたことだった。
- learnerをtrain mode、各Dropoutだけeval modeに固定する修正を加えた。同一roundを用いた隔離CPU copyのupdateはfinite metrics（total 0.306466、entropy 0.442919、KL 0）で通過した。GPU updateの再検証はGPUを持つ利用者環境で、同じPPO commandを再実行して行う。round 1 collectionは再実行しない。

## PPO update後のNaN evaluation 修復

- 利用者GPUでround 1 updateは完走したが、Rule v0 evaluationはcandidate 256/256が`actor-critic emitted non-finite scores`で失敗した。保存済みのPPO modelとoptimizer stateは全float tensorがNaNだった。
- `-inf` padded action logitをentropy/KLの乗算前に0へ置換し、gradient norm、各gradient、optimizer step後parameterにfinite hard-failを追加した。従来の後段`where` maskはCUDA backwardのNaN gradientを防げなかった。
- runnerはNaN modelとfailed evaluationを削除せずtimestamp付き`*-invalid-*`へ隔離し、DAgger checkpointからrebuildする。既存round 1 rolloutは再利用する。修正後のGPU evaluationは未実行であり、Gate 5aは`PPO_UPDATE_REPAIR_REQUIRED`である。

## 修復後 round 1 と round 2 winner contract

- 修復後round 1はPPO update、Rule v0 256局評価ともPASS。評価は104/256勝（40.62%）でGate 4 BC baseline 39.06%比+1.56pt、candidate fault 0だった。
- round 2 rolloutは800/800 legalだったが、winner code `2`のcompleted gameが1局あった。これはdraw／lossとして文書化されていないため、PPO terminal rewardを推測せずそのepisode全体を除外する。隔離CPU updateは594 episode／7,924 decision、finite metricsで通過した。
- runnerは保存済みPPO update数から次roundを決める。次回はround 1を再更新せず、round 2 rolloutのGPU updateから再開する。

## Round 3 signal と旧pilotの閉鎖

| checkpoint | Rule v0 | BC比 |
|---|---:|---:|
| round 2 | 113/256 (44.14%) | +5.08pt |
| round 3 | 133/256 (51.95%) | +12.89pt |
| round 4 | 107/256 (41.80%) | +2.73pt |

- round 3は暫定bestだが、256局だけではRule v0超えを確定しない。round 4との差はupdate悪化と評価分散のどちらか未分類である。
- round 5は`HARD_TIMEOUT` 2件、legal 798/800のためupdateせず、旧rootを`BLOCKED_AFTER_ROUND4`として閉じた。round 3／round 4／BCは`ppo-pilot-dagger-v1-round3-best-w16`へimmutable copyし、SHA-256をbranch manifestへ保存した。
- 24 workerはround 4/5でthroughput 0.023/0.022 game/s、p95約91秒まで悪化した。今後はrollout/evaluation workerを16/8へ分離し、worker sweepと同一1,024局recheckを先に行う。

## Fixed 1,024-game recheck

| candidate | Rule v0 | seed対応BC差（95% CI） |
|---|---:|---:|
| BC recurrent | 371/1,024 (36.23%) | 基準 |
| PPO round 3 | 463/1,024 (45.21%) | +8.98pt (+4.71〜+13.26pt) |
| PPO round 4 | 423/1,024 (41.31%) | +5.08pt (+0.89〜+9.27pt) |

- pairing digest `a006c983…`でopponent、balanced side、seedを固定し、各候補ともlegal 1,024/1,024、fault 0だった。candidate identityを含むschedule digestは候補ごとに異なるが、比較入力は共通である。
- round 3は256局時の51.95%を再現せず、Rule v0優位性は未確定。一方、BC recurrent超えは支持する。round 3−round 4は+3.91pt（95% CI −0.37〜+8.18pt）であり、round 4更新による悪化を確定しない。

## Gate 5a-A 判定と worker sweep

- Gate 5a-Aは`GATE5A_BC_IMPROVEMENT_CONFIRMED`。round 3−BCのseed対応差+8.98ptの95%区間は全て正であり、actual CABTでonline PPOが初期BCを改善したことを支持する。
- Gate 5a-Bは`RULE_V0_TARGET_NOT_MET`。round 3=45.21%の95%区間は50%未満で、現固定Rule v0条件では依然劣後である。promotion、Champion、Rule v0、default Deck、Kaggle提出は不変。
- worker sweepの端末実測wall-clock throughputはworker 8/12/16/20で0.637/0.533/0.459/0.481 game/s、全条件fault 0。8 workerを採用し、24 workerは長時間timeout実績もあるため不採用。新しいrun summaryではwall-clockと個別game durationを分離する。
