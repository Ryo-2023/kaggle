# Parallel CABT evaluator v1（2026-08-12）

## 結論

研究専用の並列 CABT evaluator と game-level ledger を追加し、focused test 4件と実 CABT 4局 smoke を完了した。既存の `main.py`、production actor、既存 evaluator は変更していない。smoke は 4/4 `DONE`、fault 0、3勝1敗（score 0.75）で、worker は `spawn`、2 worker、2 game/worker recycle で動作した。

この成果物は評価速度と証跡を閉じるための Phase 2 基盤であり、候補 policy の promotion や Kaggle 提出を意味しない。CABT engine の seed setter は未提供なので、同一 seed 列を使っても game-level paired evidence とは呼ばない。

## 既存実装の監査

DEC-024（2026-07-28）は、CABT child の eager environment import を `cabt` へ限定し、OMP/MKL/OpenBLAS/NumExpr を 1 thread、worker recycle 32局/worker、既定 16 worker とする方針を採用している。

確認した既存経路は次の通りである。

- `src/mage_ptcg/meta_specialist/actor_pool_v1.py` は通常経路が spawn child 1局/process で、`persistent_worker=True` は opt-in だが recycle 契約を持たない。
- `scripts/run_batch_eval.py` は逐次 `run_match` であり、game-level atomic ledger、policy/deck/opponent SHA の完全な row provenance、fault-inclusive denominator は持たない。
- `scripts/measure_v4_checkpoint_strength.py` も V4 固有の逐次 evaluator であり、今回の研究用汎用 pool へ直接改変しなかった。

## 実装範囲

追加ファイルは以下の2つである。

- `scripts/parallel_cabt_evaluator_v1.py`
- `tests/test_parallel_cabt_evaluator_v1.py`

主な契約は次の通りである。

| 項目 | 実装 |
|---|---|
| worker | `ProcessPoolExecutor` + `multiprocessing.get_context("spawn")` |
| 既定並列度 | 16 worker |
| recycle | `max_tasks_per_child=32`（DEC-024既定。smoke/testでは短縮可能） |
| thread cap | `OMP_NUM_THREADS`、`MKL_NUM_THREADS`、`OPENBLAS_NUM_THREADS`、`NUMEXPR_NUM_THREADS`、`BLIS_NUM_THREADS`、`VECLIB_MAXIMUM_THREADS` を `1` |
| PyTorch | worker initializer で intra-op / inter-op を 1 へ要求。観測値も row に保存 |
| timeout | worker-local `SIGALRM`、親 watchdog、いずれも fault row へ変換 |
| ledger | gameごとの JSON を temporary file + `fsync` + `os.replace` で atomic 公開。全行の `ledger.jsonl` と `summary.json` も atomic |
| fault | exception、timeout、worker crash、非 `DONE`、missing winner を `outcome=fault` として requested games 分母へ残す |
| provenance | `policy_id`/SHA、`deck_id`/SHA、opponent identity/deck SHA、seat、block、seed、steps、runtime、evaluator SHA、worker PID/thread観測 |
| pairing | `engine_seed_supported=false`、`independent_stratified_not_game_paired` を manifest に固定 |

V4やcheckpoint固有の factory は production codeへ複製せず、`EvaluationGameV1.runner_ref` に importable `module:function` を指定して接続する。既定の `run_cabt_game_v1` は `scripts.test_sim.run_match` の named-agent 契約を使う。

## TDD / focused verification

最初に test を追加し、module 未実装による RED（`ModuleNotFoundError`）を確認してから実装した。GREEN は次の通りである。

```text
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/pytest \
  tests/test_parallel_cabt_evaluator_v1.py -q
....                                                                     [100%]
4 passed in 7.21s
```

テストは以下を固定する。

1. SHA/seat 不正を worker 起動前に拒否する。
2. 同じ fixture game set を serial（1 worker）と parallel（2 worker）で実行し、aggregate、fault-inclusive score denominator、per-game atomic file、thread/recycle manifest、provenance の schema が一致する。
3. runner exception を `FAULT` row として保存し、1局の fault でも denominator が1のまま score 0になる。
4. duplicate `game_id` は output を作る前に拒否する。

追加の静的確認:

```text
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python -m py_compile \
  scripts/parallel_cabt_evaluator_v1.py tests/test_parallel_cabt_evaluator_v1.py
git diff --check -- scripts/parallel_cabt_evaluator_v1.py \
  tests/test_parallel_cabt_evaluator_v1.py
```

## 実 CABT smoke

root deck と Rule v0 named agent を対象に、4局（両 seat、同一 `root-rule-smoke` block）を `workers=2`、`worker_recycle_games=2` で実行した。保存先は `/tmp/parallel-cabt-smoke/` である。

| 指標 | 観測値 |
|---|---:|
| requested games | 4 |
| completed (`DONE`) | 4 |
| wins / losses / draws | 3 / 1 / 0 |
| faults | 0 |
| score denominator | 4 |
| score | 0.75 |
| sum of game runtime | 2.04832 s |
| evaluator implementation SHA at smoke execution | `ee3a9e4e352006af41355bf660dd599bcabbbbb5c30666970cc890ac10ce6363` |
| start method | `spawn` |
| worker / recycle | 2 / 2 games |
| engine seed setter | unsupported (`false`) |

worker import・CABT初期化を含む起動からの壁時計は約62秒だった。したがって、今回の4局だけから本番 throughput を推定しない。DEC-024の1.27秒 CABT-only child import観測と整合し、短い smoke では worker 起動・Torch import overhead が支配的である。大きな broad arena では 16 worker、32 game recycle を使い、wall throughput を別途測る必要がある。

`/tmp/parallel-cabt-smoke/manifest.json` には `max_workers=2`、`worker_recycle_games=2`、全 thread cap、`faults=0`、`pool_failure_observed=false` が保存され、各 `games/*.json` には policy/deck/opponent/seat/block/SHA/steps/runtime が存在する。

その後、長い pool の先頭 game の timeout で後続 game を誤って一括 fault にしないよう、親 watchdog の基準を pool 開始時刻から各 future の submit 時刻へ小修正した。修正後の現在 source SHA は `cb15090f41dcf54072c717621d65935a2747e0b529db38aa82fdca069b4081bc` である。smoke artifact は修正前 SHAへ正しくbindされているため、修正後 source と同一SHAの再smokeは未実施として扱う。

## 制約と次の接続点

- 実 CABT の serial-vs-parallel を同一ゲーム乱数で paired 比較する機能はない。serial smoke は schema/aggregate smoke として扱い、勝敗差の統計的 attribution には使わない。
- V4 checkpoint factory、opponent pool の policy factory、archive-only submission runtime は今回接続していない。次段で `runner_ref` へ hash-bound な research runner を渡す。
- parent watchdog が native engine の hard hang を完全に kill するには、既存 `ActorPoolV1` の per-game process-group kill 相当が必要である。現版は worker-local alarm、親 fault row、fail-closed shutdown を採用しており、大規模投入前に hard-kill soak を追加確認する。
- したがって本 artifact だけで longrun、Champion変更、Kaggle提出を開始してはならない。
