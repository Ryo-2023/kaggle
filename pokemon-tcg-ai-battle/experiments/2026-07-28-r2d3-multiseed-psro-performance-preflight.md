# R2D3 multi-seed／PSRO performance preflight

## メタデータ

| 項目 | 値 |
|---|---|
| 日時 | 2026-07-28 22:36 JST |
| 担当 | Codex |
| 種別 | local preflight / CUDA smoke |
| commit | `254a9f5becd9b431b3e47807634caf123024e6ee`（dirty working tree） |
| branch | `local/offline-scaleup-v2` |
| simulator / data | prior submitted-opponent R2D3 E2E artifact / CUDA 12.8 |

## 目的と反証条件

- **問い**: 長時間R2D3性能学習を始めるためのReplay sourceとCUDA経路が固定・資格化されているか。
- **反証条件**: 要求したPolicy sourceのいずれかがpin済みCABT runtimeを持たない、またはCUDA smokeがfault／timeoutを出す。
- **固定条件**: Rule v0、Champion、`main.py`、`deck.csv`、default Deck、`agents/*`・`dev/*` ref、final holdoutは変更／使用しない。

## 再現

```bash
cd /home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle
R2D3_PERFORMANCE_PROFILE=smoke \
PYTHON_BIN="$PWD/.venv-gpu/bin/python" \
bash scripts/policy_learning/run_r2d3_multiseed_psro_performance.sh \
  /home/bfe-lab-ono/kaggle/handoff-artifacts \
  "$PWD/runs/r2d3-multiseed-psro-performance" 0
```

生成物は`/home/bfe-lab-ono/kaggle/handoff-artifacts/r2d3-multiseed-psro-performance-v1-20260728_225913`。source patch SHA-256は`a7352bb81d2296550984673fe40a6e02f2fe0b35890e6aa0d054c8cd71106fc5`、checksumは37 filesで検証済み。

## 結果

| condition | requests | fault / timeout | 結果 |
|---|---:|---:|---|
| GRU CPU synthetic inference | 128 | 0 / 0 | smoke pass |
| GRU CUDA synthetic inference | 128 | 0 / 0 | smoke pass |
| LRU CPU synthetic inference | 128 | 0 / 0 | smoke pass |
| LRU CUDA synthetic inference | 128 | 0 / 0 | smoke pass |

- CUDAは`NVIDIA RTX PRO 5000 Blackwell`、PyTorch `2.11.0+cu128`、CUDA `12.8`、BF16対応だった。
- submitted training assets 9、Rule v0/v1、initial R2D3、PPO frozen-round-3、BC recurrent、Family Alakazam、PSRO meta-mixtureはREADYだった。PPO／BC／Familyはimmutable population entryのadapter `prepare()`と対Rule v0 1局実CABTを通過し、いずれも`DONE`／legal／candidate fault 0だった。
- このsmokeはCABT 128局のscale benchmarkではない。callback latency、CPU/GPU action一致、actor／batch／delayの選定値、games/sを決定していない。

## 解釈と判断

- **観測事実**: CUDA R2D3実行経路と全Replay source qualificationは動作する。ただし、長時間性能プロトコルは未実行である。
- **判断**: `NO_PROMOTION_RECOMMENDED`。20,000局Replay、architecture screen、multi-seed、deck/final holdout、SP-PSROは未実行である。
- **言わないこと**: R2D3がRule v0、PPO best、BCを上回る、または再現不能であるとは結論しない。
- **次 action**: 資格済みsourceを固定し、full protocolで20,000局Replayから開始する。

## Kaggle 提出

該当なし。commit、push、Kaggle提出はいずれも実施していない。

## Controller smoke（2026-07-29 JST）

- `run_r2d3_multiseed_psro_performance.py`を13 stageのresumable fail-closed controllerへ置換した。smoke profileは同一stage実装でscale 16局/config、Replay 256局/128局Gate、6 screen×20 CUDA update、top-2×3 seed×20 update、full 40 update、PSRO 4 policy×8局/pair、BR 20 updateを実行する。
- GPU smoke artifact `/tmp/r2d3-performance-smoke-final-v2` は全13 stage `PASS`、Replay freeze save/reload、checkpoint resume、checksumを確認した。development threshold未達のためdeck/final holdoutは`NOT_USED`で、final dataは開いていない。`NO_PROMOTION_RECOMMENDED`は未評価保留であり性能棄却ではない。
- production profileは20,000局／5,000局品質Gate／100,000 sequence minimum／screen 10,000 update／top-2×3 seed各50,000 update／full 150,000 updateに接続済みだが、長時間production run自体は未実行。

## 並列collector最適化（2026-07-29 JST）

- production collectorは16 spawn process、smokeは4 processへ変更した。native CABTのthread共有は行わず、各gameのruntime scratch／match outputを固有パスへ隔離し、親processでgame index順にReplayを統合する。
- `/tmp/r2d3-parallel-smoke-spawn-v2`で4 process、256局Replay collection、全品質Gate、fault 0を実行確認した。CUDA contextを継承する`fork`はworker停止を再現したため採用せず、`spawn`のみを許可する。
- production learner batchを128へ上げ、実ReplayのCUDA BF16 forward/backward 128 sequence updateを確認した（loss 4.7024、gradient norm 0.2055、NaNなし）。
- PPO collector workerはfrozen CPU modelをprocess内で一度だけロードし、毎局`reset_episode()`とgame/seat由来seedを適用して再利用する。policy state・traceは各局で必ず初期化し、worker境界を越えて共有しない。productionの150,000 updateでは全updateを実行したうえで、training curveを100 updateごとの集計値にし、Python object／CSV I/Oを抑える。
- `/tmp/r2d3-parallel-cache-smoke-v3`で現行code pathのsmoke replay collectionを再実行した。256局、PPO submitted/Rule 128・BC 64・Family 64、quality gate 128／192／256、fault／timeout／hidden leak／split leakage／corrupt sequenceはすべて0で`PASS`。source countがこの実局数と一致しない場合はstageを失敗させる。これは縮小Replay経路の確認であり、productionの20,000局結果ではない。

## Production Replay recovery（2026-07-29 JST）

- production v6 はsource freeze、768局scale、20,000局Replay collection（PPO/Rule 10,000、BC 5,000、Family 5,000、quality gate 5,000ごと）をfault 0で完了したが、non-overlap 20-step sequence化では31,420 sequenceとなり、100,000 minimumを満たさずReplay freezeでfail-closedした。後段stageは未開始であり、20,000局成果物は保持した。
- production既定をburn-in=8、unroll=20、stride=4の重複R2D3 windowへ変更した。windowは既存の実 transitionのみを参照し、terminalをburn-inへ跨がない。compact `window_refs` storageはbase sequenceを一度だけ保存し、sample時にwindowを実体化する。
- `/tmp/r2d3-production-replay-recovery-v8-smoke`でv6のchecksum、protected source、4品質gate、source mixを検証して再利用し、106,081 sequence、save/reload/checksumを`PASS`した。compact fileはv6の1.1GBに対して+0.12%。同ReplayからRTX PRO 5000 Blackwell上でBF16 128-sequence ×20 update（loss 3.3340、gradient norm 0.0829、NaNなし）を通した。
