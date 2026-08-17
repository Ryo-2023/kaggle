# Continuous Learner GPU高速化の検証（2026-07-31）

## 結論

42,084系列の実Sealed Replayを用いた学習ループ測定では、batch 512のGPU常駐方式が旧経路の約2.62倍の系列処理速度を示した。一方、WSL長時間実行ではGPU常駐ReplayがWindows host commitを枯渇させ、Pythonがabortした。標準の長時間設定は、旧経路の約2.02倍となるprepack＋pinned arena、batch 512を採用する。

これは学習処理速度と再開可能性の証拠であり、対戦勝率やChampion昇格の証拠ではない。

## 実行条件

| 項目 | 値 |
|---|---|
| 実行日 | 2026-07-31 |
| base commit | `f352050dd96e7cea8942794250d19b209dee0587` |
| branch | `feature/belief-guided-search` |
| GPU | NVIDIA RTX PRO 5000、48 GB |
| PyTorch | `2.11.0+cu128` |
| Replay ID | `ea98677fcc681e06520f844d2f8d1dfdbe8ae98580694304e8c4c58928997098` |
| Population epoch | `a757828c1c9873cadfd1174229eafbbe91aed668458d1f4c1f4940c309a3fa2d` |
| 系列数 | 42,084 |
| model | GRU、hidden size 256、51 atoms |
| precision | BF16 |
| optimizer | fused AdamW |

## 学習ループ測定

各方式は同じReplay、model形状、batch size 512を使用した。`total`はPER抽選、batch作成またはGPU gather、learner updateを含む。

| 方式 | sample | batch／gather | update | total | 処理速度 | peak reserved |
|---|---:|---:|---:|---:|---:|---:|
| 旧経路 | 7.15 ms | 96.37 ms | 59.84 ms | 164.27 ms | 3,116.8系列/s | 7,886 MB |
| prepack＋pinned arena | 5.41 ms | 20.19 ms | 54.82 ms | 81.34 ms | 6,294.7系列/s | 未記録 |
| GPU常駐Replay | 5.47 ms | 1.78 ms | 54.47 ms | 62.63 ms | 8,175.6系列/s | 24,974 MB |

prepackは初回に約6.8秒を要する。GPU常駐化は約1.4秒で高速だが、WSLではGPU allocationがWindows host commitへ重なり、長時間実行の標準経路には使えない。

## WSL常駐Replayの実行停止

2026-07-31の無制限学習では、step 25まで有限のlossで進んだ直後にPythonが`SIGABRT`で終了した。WindowsのResource Exhaustion記録は同時刻の`vmmemWSL`を約59.2 GBと報告し、WSL crash dumpには`CUDA error: unknown error`が含まれる。GPU常駐Replayの約25 GB VRAM、展開済みReplay、prepack、pinned領域が同時にWindows host commitへ計上されたことが原因である。

したがって、`gru256_cuda_fast.yaml`は`resident_replay: false`へ変更した。これは学習identityに含めない実行設定なので、既存のGPU常駐checkpointから同じReplayとPopulation epochへstrict resumeできる。再開後はprepack＋pinned転送へ切り替わる。

## batch上限の反証

| batch size | 結果 | 判断 |
|---:|---|---|
| 512 | 20 updateで安定、最大total 124.8 ms | 採用 |
| 768 | 10 update中に約9.6秒の停止、平均約740系列/s | 不採用 |
| 1,024 | target forward中にCUDA out-of-memory | 不採用 |

最大batchを採るのではなく、連続updateで停止時間がなく、Replayを含むVRAM使用量に余裕があるbatch 512を採用した。

## 正しさと再開

次を自動テストで確認した。

- packed batchが従来batchとtensor単位で一致する。
- GPU常駐batchが参照batchとtensor単位で一致する。
- GRUとLRUの一括burn-inがstep反復実装と一致する。
- NumPy化したPER抽選がpriority更新をまたいで従来Python oracleと一致する。
- prepack、GPU常駐、pinned arenaの単独変更は既存学習identityを変えない。

実Replayを使った高速設定の短時間学習はstep 3まで完了し、そのcheckpointから厳密resumeしてstep 4へ進んだ。

| 確認項目 | 結果 |
|---|---|
| checkpoint schema | `r2d3-checkpoint-v3` |
| 再開後step | 4 |
| loss | 4.599186897277832、有限 |
| gradient norm | 0.517095148563385、有限 |
| PER priority | 42,084件を保存 |
| RNG | Python、NumPy、Torch CPU、Torch CUDAを保存 |
| optimizer／scheduler | 保存・復元あり |
| 進捗出力 | 生の優先度配列を含まず、集計値だけ |

再開後checkpoint:

`runs/continuous-league-bootstrap-v2/learner-cuda-fast-v1/checkpoints/r2d3-step-000000000004.pt`

さらにstep 4から20 updateを連続実行し、target networkの同期間隔16をまたいでstep 24まで完了した。

| 確認項目 | 結果 |
|---|---|
| status | `COMPLETED` |
| update数 | 20 |
| 経過時間 | 47.60秒。CUDA初回実行のwarm-upを含む |
| 最終loss | 1.9176479578018188、有限 |
| 最終gradient norm | 0.36033251881599426、有限 |
| target更新 | step 4からtarget tensor 31個中28個が変化 |
| model／target | 全tensorが有限 |
| 最終checkpoint | `r2d3-step-000000000024.pt` |

## 再現コマンド

```bash
.venv/bin/python scripts/benchmark_continuous_learner.py \
  --replay-manifest runs/continuous-league-bootstrap-v2/learner/replay_inputs/ea98677fcc681e06520f844d2f8d1dfdbe8ae98580694304e8c4c58928997098/manifest.json \
  --device cuda:0 \
  --batch-sizes 512 \
  --updates 20 \
  --warmup-updates 3 \
  --hidden-size 256 \
  --prepack \
  --pin-memory \
  --bf16 \
  --fused-optimizer \
  --matmul-precision high \
  --output /tmp/continuous-learner-prepack.json
```
