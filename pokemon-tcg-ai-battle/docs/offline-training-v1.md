# Offline Training v1 — 利用ガイド

結論: `scripts/run_offline_training_v1.py` は、actual cabt 収集から neural Student 学習、pure-Python export、独立 Kaggle package、clean-room 検証までを再開可能な統一 CLI で実行する。現在の Champion は **Rule Agent v0** のままで、本 package は default agent を変更しない。Promotion は **NO_DECISION**。

## 前提

- 依存が揃った interpreter で実行する。研究室 PC の正典は `/usr/bin/python3`（numpy + torch + pytest 同梱）。`uv run --active` は本 worktree では numpy/torch を欠くため使用しない。
- GPU は任意。CUDA があれば BF16 autocast + FP32 loss 蓄積で学習し、無ければ CPU で学習する。
- actual cabt が使えない環境では collection を `fixture` source で実行し、`actual_cabt = ACTUAL_CABT_NOT_RUN` と記録する。fixture 結果を actual として扱わない。

## コマンド

すべて `python scripts/run_offline_training_v1.py <command>`（相対・絶対 path 両対応、cwd 非依存）。

| command | 役割 |
|---|---|
| `doctor` | CPU/RAM/GPU/VRAM/disk と resource policy を出力 |
| `collect` | 再開可能 collection（fixture / actual） |
| `build-dataset` | gzip shard 化 + episode 決定的 split + train-only 正規化 |
| `train` | candidate-wise neural Student 学習（checkpoint/resume） |
| `evaluate` | held-out 評価（neural v1 と linear v0 baseline 比較） |
| `screen` | tiny paired screening（seat balanced） |
| `export` | pure-Python 安全 export |
| `package` | 独立 Kaggle package を `dist/kaggle/neural-student-v1/` に生成 |
| `verify` | tar.gz のみを clean-room 展開して検証 |
| `pipeline` | 上記全 phase を順に実行 |
| `resume` | 中断した run を再開（完了 phase は skip） |
| `status` | run manifest の phase 状態を出力 |

## 代表的な実行

```bash
# 環境確認
python scripts/run_offline_training_v1.py doctor --config configs/offline_training_v1/smoke.json

# smoke（今回検証した規模）
python scripts/run_offline_training_v1.py pipeline --config configs/offline_training_v1/smoke.json

# 中断からの再開
python scripts/run_offline_training_v1.py resume --run-dir runs/offline-training-v1/<run-id>

# 状態確認
python scripts/run_offline_training_v1.py status --run-dir runs/offline-training-v1/<run-id>
```

`--dry-run` は解決した run-dir / config hash / resource policy を出して終了する。`--force` は完了 phase を再実行する。

## GPU interpreter の指定

CUDA 対応 Python が別 path にある場合は `--gpu-python <path>` か環境変数 `POKEMON_TCG_GPU_PYTHON` を渡す（doctor へ informational に反映）。巨大依存の install、PyTorch/CUDA 再 install、driver 変更は行わない。

## GPU が無い場合（`GPU_ENV_BLOCKED`）

CPU で fixture 学習・pipeline・export・package・clean-room まで完走できる。GPU 固有の BF16 forward/backward smoke だけを `GPU_ENV_BLOCKED` として保留し、再実行コマンドを evidence に残す。GPU 環境構築のためだけにタスクを止めない。

## profile

| profile | 用途 | 実行可否（本タスク） |
|---|---|---|
| `smoke` | 2〜4 games、1〜2 epochs、4 screening games | 実行する |
| `pilot` | 128 games、10〜20 epochs、20 screening games | 実行しない |
| `production` | 2048 games、30〜50 epochs、100 screening games | 実行しない |

pilot / production の長時間 collection、正式 100-game screening、Kaggle 提出、Champion promotion は本タスクで実行しない。

## screening と評価の限界

actual cabt が無い環境の screening は勝率を測れない。`verdict = INSUFFICIENT_EVIDENCE` とし、`wins/losses/win_rate` は `null`、legality と fallback だけを測る。正式 screening（100 games、seat 50/50、事前登録 seed、固定 hash、optional stopping 禁止）は未実行。自動 promotion は行わない。

## 安全境界

- 独立 package の `main.py` だけが neural Student を選ぶ。model 欠落・破損・hash 不一致・schema 不一致・次元不一致・非有限 score・例外時は **Rule Agent v0** へ fallback し、必ず合法手を返す。
- package に torch、学習コード、optimizer、checkpoint、raw dataset、private trace、絶対 path、秘密情報を含めない。
- run directory（checkpoint、private binding、export、archive）は git 管理外。
