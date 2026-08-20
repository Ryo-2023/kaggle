# Detector-Fixed Association Race — Strong Baseline v1

作成日: 2026-08-21（JST）  
ブランチ: `codex/biohub-multi-method-race`

## 1. 目的と比較条件

同一の公式 detector 出力を固定し、association だけを交換して比較する。画像、cell-center、node feature、forward/reverse raw edge logitsは一度だけ取得し、後段はcacheのみを読む。GTはcache生成・candidate生成・association選択に渡さず、公式metric評価時だけ開く。

比較予定の4方式:

| method_id | association | graph最適化 |
|---|---|---|
| `official_ilp` | 公式forward probability | RoyerLab `build_graph` + ILP（SCIP fallback） |
| `harmonic_v1` | forward/reverse harmonic fusion (`w=0.20`) | 同上 |
| `mutual_confidence` | forward/reverse mutual confidence | 同上 |
| `motion_gated` | forward confidence + physical motion gate | 同上 |

## 2. detector / checkpoint provenance

| 項目 | 値 |
|---|---|
| detector | `TemporalUNet3D + SimpleNodeTransformer` |
| source repository | `https://github.com/royerlab/kaggle-cell-tracking-competition.git` |
| pinned source commit | `075fc5f5a52d11077f9dc2b074644618f26939e2` |
| checkpoint | `artifacts/strong_baseline_v1/inputs/cellmot-baseline-artifacts/weights/unet_transformer/split_0/edge_predictor_best.pth` |
| checkpoint SHA-256 | `347915de9c33883cb2ee69832a8e4552c88b1ec692d0fbfe956422467d3d4235` |
| detector threshold | `0.99` |
| TTA | Y/X flip TTA enabled |
| pool kernel | `3.0 µm` |
| edge activation | softmax |
| edge threshold | `0.5` |
| image device | `auto` = CUDA → MPS → CPU |

## 3. validation panel

scoreを見ずに、image metadataとGT graph metadata（node/edge/divisionの有無）だけで固定した。development sampleを先頭に含め、divisionが存在するsampleを優先した。

| sample | shape | GT nodes / edges | division source | 状態 |
|---|---:|---:|---:|---|
| `44b6_0113de3b` | `(100,64,256,256)` | `52 / 50` | `0` | development / 実行中 |
| `44b6_0b24845f` | `(100,64,256,256)` | `51 / 49` | `0` | panel固定 |
| `44b6_0c582fdc` | `(100,64,256,256)` | `71 / 70` | `0` | panel固定 |
| `44b6_0db75fae` | `(100,64,256,256)` | `157 / 151` | `0` | panel固定 |
| `44b6_12dfb391` | `(100,64,256,256)` | `788 / 773` | `1` | division panel |

## 4. cache契約と実行状況

cacheはGT-free manifest、`nodes.npz`、`candidate_edges.npz`、`READY`を原子的に公開する。manifestにはimage/checkpoint/source/adapter hash、detector設定、実デバイス、node/edge数を記録する。

最初の全100フレーム試行では、sliding window間で同一nodeのcontextual featureが変わることが判明した。これはTemporalUNetの前後frame contextとwindow相対時刻による仕様である。修正後は最初の観測をcanonical featureとして保存し、衝突観測数をprovenanceへ記録する。pair単位のforward/reverse logitsはそのまま保持する。

修正後の4フレーム実データsmoke:

| 項目 | 値 |
|---|---:|
| node数 | `897` |
| candidate edge数 | `151,830` |
| feature conflict observations | `453` |
| cache hash | `e3be83ded19f3637478fae649d49de334a3e0db8f1744bcc3c16178abae6a0b` |

全100フレーム実行は `artifacts/detector_fixed_race/full_auto/` へ継続中である。完了後、cache hash、node/edge数、runtime、実デバイスをここへ追記する。

## 5. 公式metric結果

全100フレームのcache生成後に、各prediction GEFFと公式metric receiptを追記する。未取得の数値は推測で埋めない。

| sample / method | prediction GEFF | nodes / edges | Edge TP/FP/FN | Division TP/FP/FN | Edge Jaccard | Adjusted Edge Jaccard | Division Jaccard | Final Score | runtime |
|---|---|---:|---|---|---:|---:|---:|---:|---:|
| `44b6_0113de3b` / official | pending | pending | pending | pending | pending | pending | pending | pending | pending |
| `44b6_0113de3b` / harmonic | pending | pending | pending | pending | pending | pending | pending | pending | pending |
| `44b6_0113de3b` / mutual | pending | pending | pending | pending | pending | pending | pending | pending | pending |
| `44b6_0113de3b` / motion | pending | pending | pending | pending | pending | pending | pending | pending | pending |

## 6. デバイス診断とGPU移行

今回のDocker実測値は `torch 2.13.0+cpu`、`torch.version.cuda=None`、`torch.cuda.is_available=False`、CUDA device count `0`、MPS built/available `False/False`、`nvidia-smi`なしである。従ってautoがCPUを選んだ原因は環境であり、行列計算の実装不具合ではない。

NVIDIA環境では `docker-compose.nvidia.yml` と公式CUDA wheel indexを使い、`gpus: all`で起動する。source側の `--device auto` はCUDAを最優先する。MPS環境では同じautoがMPSを選ぶ。GEFF I/O、ILP/SCIP、公式metric、古典associationはCPU処理である。

## 7. 再現コマンド

```bash
docker compose exec -T biohub sh -lc 'cd /workspace/biohub-cell-tracking-during-development/scratch/strong-baseline-v1/biohub-cell-tracking-during-development && PYTHONPATH=/workspace/biohub-cell-tracking-during-development/scratch/strong-baseline-v1/biohub-cell-tracking-during-development/src uv run python scripts/run_detector_fixed_race.py materialize --sample 44b6_0113de3b --train-root /workspace/biohub-cell-tracking-during-development/data/train --upstream-root artifacts/strong_baseline_v1/upstream --checkpoint artifacts/strong_baseline_v1/inputs/cellmot-baseline-artifacts/weights/unet_transformer/split_0/edge_predictor_best.pth --output artifacts/detector_fixed_race/full_auto'
```

## 8. 既知の問題

- 現行MacBook DockerはCPU-onlyであり、GPU実測値はまだない。
- node featureはwindow context依存のため、cacheのcanonical featureは最初の観測である。association比較はpair logitsを使用する。
- panel画像はKaggleから取得済みだが、各sampleのfull detector cacheと公式metricは未完了である。
- Kaggleへの外部submissionは行わない。
