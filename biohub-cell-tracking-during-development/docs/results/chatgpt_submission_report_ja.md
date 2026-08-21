# Biohub Cell Tracking — ChatGPT報告用・全結果統合版

作成日: 2026-08-21（JST）
対象: Kaggle **Biohub – Cell Tracking During Development**
作業ブランチ: `codex/biohub-multi-method-race`
最新push: `origin/codex/biohub-multi-method-race`（直前に確認済みレポートcommit: `da59280`、実装commit: `eb6e472`）
実験artifactに記録されたrace実装commit: `ac2ece5`

この文書は、Strong Baseline v1、Multi-Method Benchmark Race、追加性能改善実験、公開手法の実行可能性調査、検証結果を、ChatGPTへそのまま渡せるように1ファイルへ統合したものである。

## 1. 結論

- 公式 TemporalUNet3D + SimpleNodeTransformer + ILP pipelineを実データで完走した。
- 同一sample・同一公式metricで、追加3 lane（`blob_lap`、`cc_flow`、`motion_lap`）を推論からprediction GEFF、公式評価まで完走した。
- 全比較のBest Methodは `harmonic v1`（Final Score `0.9211200215044129`）。
- detector-fixed raceでは同一TemporalUNet3D detector cacheを固定し、developmentと`44b6_0b24845f`で4 association方式を公式metricまで完走した。0bのBestは`harmonic_v1`（Final Score `0.6274705993317501`）で、official ILP `0.6262213541803576`を`+0.0012492451513925`上回った。
- 0bの公式ILPはprediction `55,324 nodes / 44,335 edges`、Edge TP/FP/FN `39/9/10`、Adjusted/Final `0.6262213541803576`を取得した。GTは評価phase以外に使っていない。
- 新規race laneでは `blob_lap`（Final Score `0.9140773262846648`）が最良だった。
- 追加のNMS仮説（3.0→3.5 µm）は `0.9172062183593925` を得て、固定blob lane比 `+0.0031288920747277`。ただし単一sampleでharmonic v1未達のため、複数sample検証前の昇格候補として扱う。
- `cc_flow` は detector の node recall が低く不採用、`motion_lap` は blob単独より悪化した。
- HOCT、Trackastra、Ultrack、Linajea、DeepCenterは、入力契約・依存・checkpoint・source確認の不足により、今回の公式スコア比較には含めていない。

## 2. Done条件と実験範囲

### Done条件

以下を実際に確認した。

```text
Kaggle train image (.zarr)
  -> image-only detection
  -> tracking / graph optimization
  -> prediction .geff
  -> persisted prediction manifest validation
  -> GTを開く
  -> RoyerLab由来公式metric
```

GT GEFFは、推論・cache・candidate生成・threshold決定には使用せず、評価コマンドのmetric phaseだけで開いた。

### 対象sample

| 項目 | 値 |
|---|---|
| image | `44b6_0113de3b.zarr` |
| container内image path | `/workspace/biohub-cell-tracking-during-development/data/train/44b6_0113de3b.zarr` |
| GT | `/workspace/biohub-cell-tracking-during-development/data/train/44b6_0113de3b.geff` |
| shape / axes | `(T,Z,Y,X)=(100,64,256,256)` |
| dtype | `uint16` |
| physical scale | `(1.625, 0.40625, 0.40625)` µm/voxel in `(Z,Y,X)` |
| image quantiles | `q0.001=26.222222222222225`, `q0.999=2145.000000039654` |
| official evaluator | `max_distance=7.0` µm |
| GT annotation | annotated node 52、edge 50、metadata推定total node 25,755 |
| execution environment | existing `biohub-dev` / Ubuntu 24.04 / Python 3.11 / CPU-only |

GTは疎である。未注釈・未マッチのpredictionを自動的にfalse positiveと解釈してはいけない。

## 3. Source・checkpoint・version provenance

### 3.1 公式 Strong Baseline v1 / harmonic v1

| 構成要素 | provenance |
|---|---|
| official source | [`royerlab/kaggle-cell-tracking-competition`](https://github.com/royerlab/kaggle-cell-tracking-competition) |
| official source commit | `075fc5f5a52d11077f9dc2b074644618f26939e2` |
| official source license | BSD-3-Clause |
| public checkpoint dataset | `thibautgoldsborough/cellmot-baseline-artifacts`, version 1、License Unknown |
| checkpoint path | `artifacts/strong_baseline_v1/inputs/cellmot-baseline-artifacts/weights/unet_transformer/split_0/edge_predictor_best.pth` |
| checkpoint SHA-256 | `347915de9c33883cb2ee69832a8e4552c88b1ec692d0fbfe956422467d3d4235` |
| public baseline notebook | Kaggle notebook version `331429261` |
| harmonic source | Yusuke Togashi、notebook v18、`scriptVersionId=338569479`、declared Apache-2.0 |
| harmonic notebook JSON SHA-256 | `dd3819cff82851b491d9cbeb6f5f0fc36e8da3c5e9ca90a8b0d5284785a250d` |
| harmonic setting | reverse harmonic weight `w=0.20` |

harmonic v1では、image、detector、checkpoint、candidate construction、ILP costを変更せず、双方向reverse-logit associationを追加した。保存されたreceiptを根拠に結果を報告している。保持されたnotebook JSONからsource cellを独立監査するfixtureは不足しており、その監査はBLOCKEDである。source textを推測・捏造していない。

公式推論の固定設定:

| 設定 | 値 |
|---|---:|
| detector threshold | `0.99` |
| U-Net batch size | `1` |
| use ILP | `true` |
| ILP edge weight | `-1.0` |
| ILP appearance / disappearance | `0.1` / `0.1` |
| ILP division weight | `1.0` |
| reported window | `2` |
| reported pool kernel | `3.0` µm |
| evaluator max distance | `7.0` µm |

downloadしたconfigに `pool_kernel_um=5.0` が含まれる一方、upstream run receiptは `3.0` µmを報告する。この差はlocalで変更せず、upstream由来の再現性課題として記録した。

### 3.2 Multi-Method Raceの実装 provenance

| lane | method family | detector | association / optimization | source commit | checkpoint |
|---|---|---|---|---|---|
| `blob_lap` | classical detector + LAP | 3D Gaussian/local peak + physical NMS | physical-distance Hungarian/LAP | `ac2ece5` | なし |
| `cc_flow` | classical connected component + global flow | quantile foreground + 3D components | `networkx.network_simplex` global min-cost flow | `ac2ece5` | なし |
| `motion_lap` | classical motion association | fixed `blob_lap` candidate cache | velocity/acceleration prior + `scipy.optimize.linear_sum_assignment` | `ac2ece5` | なし |

Race branchの主なcommit:

| commit | 内容 |
|---|---|
| `a2ea84f` | race contracts / cache manifest |
| `0180008` | GT leakage hardening |
| `7bf3fe3` | blob detector + LAP lane |
| `99417f7` | connected-component global-flow lane |
| `dc57d64` | motion-aware LAP lane |
| `3afd3c2` | evaluate / summarize CLI |
| `c4ea814` | Zarr frame streaming for blob detector |
| `62a1d8d` | connected-component streaming / cache provenance |
| `ac2ece5` | provenance、full-shape、streaming quantile hardening |
| `1fa235b` | NMS改善実験の記録 |
| `6100101` | 再現コマンドのsource revision固定 |

### 3.3 Detector-Fixed Association Race

| 項目 | 値 |
|---|---|
| detector | `TemporalUNet3D + SimpleNodeTransformer`、source commit `075fc5f5a52d11077f9dc2b074644618f26939e2` |
| checkpoint | `edge_predictor_best.pth`、SHA-256 `347915de9c33883cb2ee69832a8e4552c88b1ec692d0fbfe956422467d3d4235` |
| detector cache | GT-free `nodes.npz` + `candidate_edges.npz`、0b hash `50739a79bf081799d37987bbdd800ee2f95c5246ce07adead21812a3599a3b65` |
| edge replay sidecar | `candidate_edges.mmap/`、schema `detector_fixed.cache_mmap.v1`、source cache hash一致、約2.8 GiB |
| 0b adapter source SHA-256 | `e914af35a2b68f2509027429efaa6ab29670be822212ae7c8628985f42a4ac72` |
| 0b image SHA-256 | `7f7809f8948ce7f6c5c7cfb03d5b6fb8f140c725d16f0d63653d59620845d33a` |
| code commit | `eb6e472`（edge memmap sidecar、chunked validation、pair-contiguous grouping） |
| device | `auto`→`cpu`（DockerのPyTorch CPU wheel。CUDA→MPS→CPU fallback実装済み） |

## 4. Method configuration

### 4.1 `blob_lap`

- `q_low=0.001`, `q_high=0.999`
- Gaussian sigma: `(1,1,1)`
- local-max size: `(3,3,3)`
- peak threshold: `0.25`
- physical NMS distance: `3.0` µm
- max link distance: `7.0` µm
- division: disabled
- inference: frame-streaming、全movieの`np.asarray` materializationなし

### 4.2 `cc_flow`

- quantile foreground: `q_low=0.001`, `q_high=0.999`
- threshold: `0.25`
- minimum component voxels: `3`
- maximum component voxels: `250000`
- max link distance: `7.0` µm
- link cost / gap cost: `1.0` / `8.0`
- association: all-frame `networkx.network_simplex`
- division: disabled
- quantile metadataが欠落した場合もframe/Z-chunk streamingで計算し、full movieをmaterializeしない

### 4.3 `motion_lap`

- candidateは固定persisted `blob_lap` cacheから読む
- velocity + acceleration priorを追加
- frame-local one-to-one LAP
- solver: `scipy.optimize.linear_sum_assignment`
- `official_detector_shared=false`
- `official_detector_motion=deferred`（旧blob race laneの設定）
- したがって、これは公式TemporalUNet3D detectorのmotion ablationではなく、blob候補上の古典motion associationである。detector-fixed raceの`motion_gated`は同じ公式cacheを読み、節14で別に評価した。

## 5. 旧Multi-Method Race（blob detector系）の公式metric結果

全laneでDivision TP/FP/FNは `0/0/0`、Division Jaccardは `null`。公式summarizerがdivision termを落とすため、Final ScoreはこのsampleではAdjusted Edge Jaccardと一致する。

この節の`official baseline`は旧blob detector raceの基準である。公式TemporalUNet3Dを固定したdetector-fixed raceの結果は、節14に別表で記録する。同じsample・公式metricでもdetectorが異なるため、両表のスコアを直接同一手法の改良差として混ぜない。

| 手法 | Final Score | Adjusted Edge Jaccard | Edge Jaccard | nodes / edges | Edge TP/FP/FN | Division TP/FP/FN | node recall | total node ratio | runtime [s] | delta vs official |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| official baseline | `0.8837944835207503` | `0.8837944835207503` | `0.8846153846153846` | `25994 / 23536` | `46/2/4` | `0/0/0` | `1.0` | `0.009279751504562221` | `3967.1878084339987` | `+0` |
| harmonic v1 | `0.9211200215044129` | `0.9211200215044129` | `0.9230769230769231` | `26301 / 24205` | `48/2/2` | `0/0/0` | `1.0` | `0.021199767035527083` | `4459.703853908999` | `+0.0373255379836626` |
| `blob_lap` | `0.9140773262846648` | `0.9140773262846648` | `0.9230769230769231` | `28266 / 25562` | `48/2/2` | `0/0/0` | `1.0` | `0.0974956319161328` | `33.09213964099763` | `+0.0302828427639145` |
| `cc_flow` | `0.04212152980003883` | `0.04212152980003883` | `0.04` | `12095 / 352` | `2/0/48` | `0/0/0` | `0.1346153846153846` | `-0.5303824500097069` | `39.232341517999885` | `-0.841672953720711` |
| `motion_lap` | `0.8968305842792937` | `0.8968305842792937` | `0.9056603773584906` | `28266 / 25562` | `48/3/2` | `0/0/0` | `1.0` | `0.0974956319161328` | `4.760020085988799` | `+0.0130361007585434` |

### 解釈

- **全比較Best:** `harmonic v1`。公式baseline比 `+0.0373255379836626`。
- **新規lane Best:** `blob_lap`。node recallは1.0だが、過剰nodeが多く、harmonicには未達。
- **`cc_flow`:** node recall `0.1346153846153846`が支配的な失敗要因。global solver statusは`optimal`でもdetector mismatchを救えなかった。
- **`motion_lap`:** blob単独比 `-0.0172467420053711`。今回の設定ではmotion priorに改善根拠がない。

## 6. 追加性能改善実験

### NMS距離 3.0 → 3.5 µm

仮説: blob detectorの過剰nodeをphysical NMSで減らす。その他設定、sample、metricを固定した。

| 項目 | 値 |
|---|---:|
| source commit | `ac2ece5` |
| device | CPU |
| runtime | `63.7277883200004` s |
| config change | `nms_distance_um: 3.0 -> 3.5` |
| nodes / edges | `27393 / 25098` |
| Edge TP/FP/FN | `48/2/2` |
| Division TP/FP/FN | `0/0/0` |
| Edge Jaccard | `0.9230769230769231` |
| Adjusted / Final | `0.9172062183593925` |
| node recall / total node ratio | `1.0 / 0.06359930110658124` |
| delta vs fixed blob | `+0.0031288920747277` |
| delta vs harmonic v1 | `-0.0039138031450204` |

判定: 単一sample上の改善候補として記録し、固定laneへの昇格は複数sample validation後とする。

artifact: `artifacts/performance_experiments/blob_lap_nms35/`（metrics: `metrics.json`）。

## 7. Prediction GEFF・receipt・評価完全性

### Canonical race artifacts

| lane | prediction GEFF | prediction manifest | metrics |
|---|---|---|---|
| official | `artifacts/strong_baseline_v1/official_ilp/44b6_0113de3b.geff` | `artifacts/strong_baseline_v1/official_ilp/prediction_manifest.json` | `artifacts/strong_baseline_v1/official_ilp/metrics.json` |
| harmonic | `artifacts/strong_baseline_v1/harmonic_ilp/44b6_0113de3b.geff` | `artifacts/strong_baseline_v1/harmonic_ilp/prediction_manifest.json` | `artifacts/strong_baseline_v1/harmonic_ilp/metrics.json` |
| blob | `artifacts/multi_method_race/methods/blob_lap/44b6_0113de3b.geff` | `artifacts/multi_method_race/methods/blob_lap/prediction_manifest.json` | `artifacts/multi_method_race/evaluation/blob_lap/metrics.json` |
| cc | `artifacts/multi_method_race/methods/cc_flow/44b6_0113de3b.geff` | `artifacts/multi_method_race/methods/cc_flow/prediction_manifest.json` | `artifacts/multi_method_race/evaluation/cc_flow/metrics.json` |
| motion | `artifacts/multi_method_race/methods/motion_lap/44b6_0113de3b.geff` | `artifacts/multi_method_race/methods/motion_lap/prediction_manifest.json` | `artifacts/multi_method_race/evaluation/motion_lap/metrics.json` |

### Integrity receipt

canonical final raceの各evaluation receiptで以下を確認した。

```json
{
  "ground_truth_included": false,
  "prediction_manifest_validated_before_gt": true,
  "prediction_manifest_validation_action": "validated persisted prediction manifest before opening ground truth"
}
```

GEFFは構造的reload後にmanifestを生成し、node数、edge数、file数、bytes、directory SHA-256を記録した。evaluationはmanifest検証後にGTを開いた。

## 8. Strong Baseline v1の追加健全性確認

official/harmonicのheadless visual sanity checkも実施した。

- raw OME-Zarr、harmonic GEFF、GTをscale `(1.625,0.40625,0.40625)`、max distance `7.0` µmで読み込んだ。
- image shapeは `(100,64,256,256)`。
- harmonic overlay totals: TP edge `48`、FP edge `2`、FN edge `2`、unscored prediction edge `24155`。
- raw `(t=0,z=0)` sliceは39,490 bytes、SHA-256 `69b6c5d2c322f092c8538f94c3aa2fffc672a1425857c9677597dcbb1a5b84e4`。
- matched window: `t=0,z=62,z_radius=0.75`、node `219` `(0,62,224,248)` → node `441` `(1,62,228,248)`、TP edge。
- error window: node `11624` `(47,31,108,120)` → node `11886` `(48,28,108,116)`、FP edge。
- sparse/unmatched context: node `0` `(0,1,8,52)` → node `225` `(1,1,16,52)`。これはunscored contextであり、false positiveの証拠ではない。
- high-level viewerは `matched_node_id` と `match_node_id` のattribute mismatchで`KeyError`となるため、rendered GUI/browser成功は主張していない。下位loader/state/overlayのheadless evidenceのみ保存した。

## 9. 外部手法の実行可能性調査

外部手法は、名前だけを模倣した実装や未確認checkpointで代用していない。

| 候補 | 一次配布元 / version | 確認したsource・checkpoint | Biohub入力との不一致 | 判定 |
|---|---|---|---|---|
| HOCT | [`royerlab/hoct`](https://github.com/royerlab/hoct)、commit `cabe8fd4bd1ccc3a18edc2b82b1e6501e396f357` | MIT、`general_v0.pt`、SHA-256 `024c2e4606275c96667907abfc9e0c27487b543480caf99d9ebd1d267cef8e4a` | 同shape整数label segmentationが必須。point GEFFを直接受けない。`uvx` helpは30秒timeout | 条件付きfeasible / 今回BLOCKED |
| Trackastra | [`weigertlab/trackastra`](https://github.com/weigertlab/trackastra)、release `0.5.5` | BSD-3-Clause、3D `ctc.zip` checkpoint（metadata dimensionality `[2,3]`） | `imgs`と同shape instance masksが必須。pseudo-mask adapterとphysical scale処理が必要 | 条件付きfeasible / 今回未実行 |
| Ultrack | [`royerlab/ultrack`](https://github.com/royerlab/ultrack)、release `0.8.0` | BSD-3-Clause、tracker checkpoint不要 | integer labelsまたはforeground+contoursが必須。raw image / point direct detectorではない | 条件付きfeasible / 今回BLOCKED |
| Linajea | [`funkelab/linajea`](https://github.com/funkelab/linajea)、version `1.5` | MIT、generic pretrained checkpointを確認できず | training-first、旧来conda/gunpowder/daisy/MongoDB等の依存。現Python/Zarr stackとの互換性不明 | BLOCKED |
| DeepCenter / center-prior | 主催者/Kaggle公開名を調査 | 公式source、checkpoint schema、license、version、推論entrypointを固定できず | 名前・notebook記載だけでは再現不能 | BLOCKED |

### official detector + motion

公式upstreamには `UNetNodeTransformer.encode`、`detect`、`predict_edges`等はあるが、detector/featureの永続cache APIは確認できない。official detectorの100 frame CPU推論は約3,967秒（約66分）であり、各association laneごとに再実行するのは今回のrace目的と合わない。そのため `official_motion` はdeferredとし、`motion_lap`を公式detector共有laneと誤って扱わない。

外部候補で公式metric scoreを取得していない。従って、外部候補のSOTA性・改善性は主張しない。

## 10. 再現コマンド

以下はscratch project rootで実行する。`../../../data`は共有Kaggle data rootを指す。

### official / harmonic

```bash
docker compose exec -T biohub sh -lc 'cd /workspace/biohub-cell-tracking-during-development/scratch/strong-baseline-v1/biohub-cell-tracking-during-development && uv run python scripts/run_strong_baseline_v1.py infer-official --upstream-root artifacts/strong_baseline_v1/upstream --image-stem /workspace/biohub-cell-tracking-during-development/data/train/44b6_0113de3b --checkpoint artifacts/strong_baseline_v1/inputs/cellmot-baseline-artifacts/weights/unet_transformer/split_0/edge_predictor_best.pth --output-dir artifacts/strong_baseline_v1/official_ilp --expected-device cpu'

docker compose exec -T biohub sh -lc 'cd /workspace/biohub-cell-tracking-during-development/scratch/strong-baseline-v1/biohub-cell-tracking-during-development && uv run python scripts/run_strong_baseline_v1.py infer-harmonic --upstream-root artifacts/strong_baseline_v1/upstream --image-stem /workspace/biohub-cell-tracking-during-development/data/train/44b6_0113de3b --checkpoint artifacts/strong_baseline_v1/inputs/cellmot-baseline-artifacts/weights/unet_transformer/split_0/edge_predictor_best.pth --output-dir artifacts/strong_baseline_v1/harmonic_ilp --expected-device cpu'

docker compose exec -T biohub sh -lc 'cd /workspace/biohub-cell-tracking-during-development/scratch/strong-baseline-v1/biohub-cell-tracking-during-development && uv run python scripts/run_strong_baseline_v1.py evaluate --prediction artifacts/strong_baseline_v1/official_ilp/44b6_0113de3b.geff --ground-truth /workspace/biohub-cell-tracking-during-development/data/train/44b6_0113de3b.geff --metrics artifacts/strong_baseline_v1/official_ilp/metrics.json'
docker compose exec -T biohub sh -lc 'cd /workspace/biohub-cell-tracking-during-development/scratch/strong-baseline-v1/biohub-cell-tracking-during-development && uv run python scripts/run_strong_baseline_v1.py evaluate --prediction artifacts/strong_baseline_v1/harmonic_ilp/44b6_0113de3b.geff --ground-truth /workspace/biohub-cell-tracking-during-development/data/train/44b6_0113de3b.geff --metrics artifacts/strong_baseline_v1/harmonic_ilp/metrics.json'
```

### race lanes

```bash
docker compose exec -T -e BIOHUB_BENCHMARK_RACE_SOURCE_REVISION=ac2ece5 -w /workspace/biohub-cell-tracking-during-development/scratch/strong-baseline-v1/biohub-cell-tracking-during-development biohub uv run --no-sync python scripts/run_benchmark_race.py infer --method blob_lap --image-stem ../../../data/train/44b6_0113de3b.zarr --cache-root artifacts/multi_method_race/cache --output-root artifacts/multi_method_race
docker compose exec -T -e BIOHUB_BENCHMARK_RACE_SOURCE_REVISION=ac2ece5 -w /workspace/biohub-cell-tracking-during-development/scratch/strong-baseline-v1/biohub-cell-tracking-during-development biohub uv run --no-sync python scripts/run_benchmark_race.py infer --method cc_flow --image-stem ../../../data/train/44b6_0113de3b.zarr --cache-root artifacts/multi_method_race/cache --output-root artifacts/multi_method_race
docker compose exec -T -e BIOHUB_BENCHMARK_RACE_SOURCE_REVISION=ac2ece5 -w /workspace/biohub-cell-tracking-during-development/scratch/strong-baseline-v1/biohub-cell-tracking-during-development biohub uv run --no-sync python scripts/run_benchmark_race.py infer --method motion_lap --image-stem ../../../data/train/44b6_0113de3b.zarr --cache-root artifacts/multi_method_race/cache --output-root artifacts/multi_method_race
docker compose exec -T -w /workspace/biohub-cell-tracking-during-development/scratch/strong-baseline-v1/biohub-cell-tracking-during-development biohub uv run --no-sync python scripts/run_benchmark_race.py evaluate --prediction artifacts/multi_method_race/methods/<method>/44b6_0113de3b.geff --ground-truth ../../../data/train/44b6_0113de3b.geff --metrics artifacts/multi_method_race/evaluation/<method>/metrics.json
docker compose exec -T -w /workspace/biohub-cell-tracking-during-development/scratch/strong-baseline-v1/biohub-cell-tracking-during-development biohub uv run --no-sync python scripts/run_benchmark_race.py summarize --root . --output docs/results/multi_method_benchmark_race.md --summary-json artifacts/multi_method_race/race_summary.json
```

### detector-fixed race（0b再現）

```bash
docker compose exec -T biohub sh -lc 'cd /workspace/biohub-cell-tracking-during-development/scratch/strong-baseline-v1/biohub-cell-tracking-during-development && PYTHONPATH=/workspace/biohub-cell-tracking-during-development/scratch/strong-baseline-v1/biohub-cell-tracking-during-development/src uv run python scripts/run_detector_fixed_race.py materialize --sample 44b6_0b24845f --train-root artifacts/detector_fixed_race/panel_data/train --upstream-root artifacts/strong_baseline_v1/upstream --checkpoint artifacts/strong_baseline_v1/inputs/cellmot-baseline-artifacts/weights/unet_transformer/split_0/edge_predictor_best.pth --output artifacts/detector_fixed_race/panel_auto --device auto'
docker compose exec -T biohub sh -lc 'cd /workspace/biohub-cell-tracking-during-development/scratch/strong-baseline-v1/biohub-cell-tracking-during-development && PYTHONPATH=/workspace/biohub-cell-tracking-during-development/scratch/strong-baseline-v1/biohub-cell-tracking-during-development/src uv run python scripts/build_detector_cache_mmap.py artifacts/detector_fixed_race/panel_auto/cache/44b6_0b24845f'
docker compose exec -T biohub sh -lc 'cd /workspace/biohub-cell-tracking-during-development/scratch/strong-baseline-v1/biohub-cell-tracking-during-development && PYTHONPATH=/workspace/biohub-cell-tracking-during-development/scratch/strong-baseline-v1/biohub-cell-tracking-during-development/src uv run python scripts/run_detector_fixed_race.py dev-race --sample 44b6_0b24845f --cache artifacts/detector_fixed_race/panel_auto/cache/44b6_0b24845f --output artifacts/detector_fixed_race/panel_runs --ground-truth artifacts/detector_fixed_race/panel_data/train/44b6_0b24845f.geff --upstream-root artifacts/strong_baseline_v1/upstream --methods official_ilp'
```

`harmonic_v1`、`mutual_confidence`、`motion_gated`は、同じ`--cache`に対して`--methods`だけをそれぞれ置き換え、OOMを避けるため個別に実行した。

### NMS改善実験

追加実験の固定変更は `BlobLapConfig(nms_distance_um=3.5)` のみ。実験receiptは `artifacts/performance_experiments/blob_lap_nms35/` にある。canonical fixed laneへはまだ昇格していない。

### 検証

```bash
docker compose exec -T -e PYTHONPATH=/workspace/biohub-cell-tracking-during-development/scratch/strong-baseline-v1/biohub-cell-tracking-during-development/src -w /workspace/biohub-cell-tracking-during-development/scratch/strong-baseline-v1/biohub-cell-tracking-during-development biohub uv run --no-sync pytest -q
docker compose exec -T -w /workspace/biohub-cell-tracking-during-development/scratch/strong-baseline-v1/biohub-cell-tracking-during-development biohub uv run --no-sync ruff check src/biohub/benchmark_race scripts/run_benchmark_race.py tests/test_benchmark_race_blob_lap.py tests/test_benchmark_race_cc_flow.py tests/test_benchmark_race_report.py --output-format concise
```

実測結果:

- full pytest: `148 passed, 1 warning in 196.99s`（`eb6e472`以前の記録。追加されたdetector-fixed関連テストを含む最新full pytestは0c実行中のため未実行）
- `eb6e472`後のdetector-fixed関連確認: `11 passed in 4.74s`、Ruff `All checks passed!`
- race対象Ruff: `All checks passed!`
- report対象pytest: `4 passed`
- full repository Ruff: 24件の既存問題（`src/biohub/official_metrics/metrics.py`、`src/biohub/visualizer/*`）が残る。今回の変更対象外のため修正していない。

## 11. Commit・push・成果物

### Git

- v1 branch: `feat/strong-baseline-v1`、commit `9edb7e1`、push済み
- race branch: `codex/biohub-multi-method-race`
- race latest implementation commit: `eb6e472`、直前レポートcommit: `da59280`
- remote: `origin/codex/biohub-multi-method-race`
- PR作成URL: <https://github.com/Ryo-2023/kaggle/pull/new/codex/biohub-multi-method-race>

### report

- 本統合版: `docs/results/chatgpt_submission_report_ja.md`
- race詳細: `docs/results/multi_method_benchmark_race.md`
- v1詳細: `docs/results/strong_baseline_v1.md`
- feasibility詳細: `docs/results/multi_method_feasibility_ja.md`

### artifact一覧

- `artifacts/strong_baseline_v1/inputs/source_receipt.json`
- `artifacts/strong_baseline_v1/official_ilp/{run.json,prediction_manifest.json,metrics.json,inference.log,44b6_0113de3b.geff}`
- `artifacts/strong_baseline_v1/harmonic_ilp/{source_receipt.json,run.json,prediction_manifest.json,metrics.json,inference.log,44b6_0113de3b.geff}`
- `artifacts/multi_method_race/cache/`
- `artifacts/multi_method_race/methods/{blob_lap,cc_flow,motion_lap}/`
- `artifacts/multi_method_race/evaluation/{blob_lap,cc_flow,motion_lap}/metrics.json`
- `artifacts/multi_method_race/race_summary.json`
- `artifacts/performance_experiments/blob_lap_nms35/`
- `docs/results/detector_fixed_association_race.md`
- `artifacts/detector_fixed_race/full_auto/cache/44b6_0113de3b/`
- `artifacts/detector_fixed_race/panel_auto/cache/44b6_0b24845f/`（canonical NPZ + `candidate_edges.mmap/`）
- `artifacts/detector_fixed_race/panel_runs/44b6_0b24845f/`（official ILP）
- `artifacts/detector_fixed_race/panel_runs_0b_harmonic/44b6_0b24845f/`
- `artifacts/detector_fixed_race/panel_runs_0b_mutual/44b6_0b24845f/`
- `artifacts/detector_fixed_race/panel_runs_0b_motion/44b6_0b24845f/`

Kaggleへの外部提出は実施していない。prediction生成・local official evaluationまでであり、ユーザー承認なしのKaggle submissionは行っていない。

## 12. 既知の問題・未解決事項・次の一手

### 既知の問題

1. 旧blob raceの比較は単一Kaggle train sampleのみ。detector-fixed raceはdevelopment＋0bの2 sampleまでで、leaderboard性能やdense-truth性能を意味しない。
2. このsampleにはdivisionがなく、division termは評価上dropされている。
3. harmonicのsource-cell独立監査は保持notebook JSON不足でBLOCKED。
4. 旧official receiptにはraw candidate count/digestがなく、detector driftを完全には比較できない。detector-fixed cacheはcandidate digestとcache hashを保存している。
5. official upstreamのconfigにpool kernel `5.0`とrun報告`3.0`の不一致がある。
6. high-level viewerの`matched_node_id` / `match_node_id`不一致でGUI表示は未完了。ただしheadless overlay evidenceは保存済み。
7. 現行Dockerの全laneはCPU-only。detector-fixedはCUDA→MPS→CPUの自動fallbackを実装済みだが、GPU実測性能はまだ主張していない。
8. 外部候補は公式metric未取得。HOCT/Trackastra/Ultrack/Linajea/DeepCenterの性能数値はない。

### 次の一手

1. `44b6_0c582fdc`を次の実データpanelとしてmaterializeし、sidecar作成後にofficial→harmonic→mutual→motionを個別評価する。
2. `44b6_0c582fdc`で安定性を確認後、0db75faeとdivisionを含む`12dfb391`へ拡張する。
3. 0bでharmonicがofficialを僅かに上回った仮説を、複数sample・divisionありsampleで検証する。
4. NMS 3.5 µm候補は旧blob laneとして、detector-fixed panelとは別条件で評価する。
5. viewer属性互換性を修正する場合は、metric/evaluatorの挙動を変えずに別commitで行う。

現時点での採用判断は、**旧race全体Bestはharmonic v1、旧race新規Bestはblob_lap、detector-fixedの0bではharmonic_v1がofficial ILPを僅かに上回った**である。次は0c以降でdetector-fixed harmonicの再現性を検証する。

## 13. 2026-08-21 追補 — detector-fixed race とGPU自動選択

### デバイス選択仕様

detector-fixed raceのmaterialize CLIは `--device auto` が既定値であり、次の順にPyTorch deviceを解決する。

```text
1. torch.cuda.is_available()       -> cuda（NVIDIA GPU）
2. torch.backends.mps.is_available() -> mps（Apple Silicon GPU）
3. cpu
```

明示的に `--device cuda` または `--device mps` を指定した場合、利用不能ならCPUへ黙って変更せずエラーにする。実行receiptには `requested_device` と実際の `device` を保存する。

今回のDocker実測値は次のとおりで、CPU帰着はコードの不具合ではなく、環境がCPU-onlyであることが原因である。

| 項目 | 実測値 |
|---|---|
| platform | Linux aarch64 container |
| PyTorch | `2.13.0+cpu` |
| `torch.version.cuda` | `None` |
| `torch.cuda.is_available()` | `False` |
| CUDA device count | `0` |
| MPS built / available | `False / False` |
| `nvidia-smi` | executableなし |
| CPU threads | `8` |

したがって現在のDockerではGPUを使用できない。CUDA対応PyTorch、NVIDIA Container ToolkitでGPUをコンテナへ公開したLinux環境、またはmacOS上のMPS対応PyTorchへ移行すれば、同じコマンドで自動的にGPUを使用する。`torch`のCPU wheelを使う現Docker定義を無理に置き換えると、現行aarch64環境や再現性を壊すため、環境側のGPU対応構築は別作業として残している。

### GPUが効く範囲と効かない範囲

- TemporalUNet3Dのencode、cell-center detector、Node Transformerのforward/reverse logitsはPyTorch tensorとしてdeviceへ移るため、CUDA/MPS環境ではGPU対象になる。
- GEFF読込・cacheのNPZ圧縮、公式metric、ILP/SCIP等のgraph optimization、古典association laneは現実装ではCPU処理である。
- Apple MPSではupstream依存演算の未対応が起きた場合に自動でCPUへ途中切替する設計にはしていない。明示的MPS指定で失敗を見える化し、問題を隠さない。

### 実データ実行状況

feature cacheの最初の全100フレーム実行では、sliding window間で同一nodeのcontextual featureが変わることを検出した。これはTemporalUNetの窓相対時刻・前後frame contextに由来する仕様であり、誤った完全一致検証を修正した。最初の観測をcanonical node featureとして保存し、衝突観測数をprovenanceへ記録する。forward/reverse raw logitsはpair単位で保持するため、association比較の入力は失われない。

修正後の4フレーム実データsmokeはcache公開まで完走し、node `897`、候補edge `151,830`、feature衝突観測 `453`を記録した。2フレーム `auto` smokeでは `requested_device=auto`、実選択 `cpu` を確認した。全100フレームのdevelopment cache生成と公式metricは完了し、追加で0b sampleのcache生成・公式ILP・harmonic・mutual・motionも完了した。0c/0db/12dfb391は未完了である。

再現コマンド（GPU環境ではautoでGPUを選択）:

```bash
docker compose exec -T biohub sh -lc 'cd /workspace/biohub-cell-tracking-during-development/scratch/strong-baseline-v1/biohub-cell-tracking-during-development && PYTHONPATH=/workspace/biohub-cell-tracking-during-development/scratch/strong-baseline-v1/biohub-cell-tracking-during-development/src uv run python scripts/run_detector_fixed_race.py materialize --sample 44b6_0113de3b --train-root /workspace/biohub-cell-tracking-during-development/data/train --upstream-root artifacts/strong_baseline_v1/upstream --checkpoint artifacts/strong_baseline_v1/inputs/cellmot-baseline-artifacts/weights/unet_transformer/split_0/edge_predictor_best.pth --output artifacts/detector_fixed_race/full_auto'
```

関連commit: `830ccab`（accelerator-first device fallback、contextual feature衝突の記録）および `eb6e472`（dense cacheのedge memmap sidecar、chunked validation、pair-contiguous grouping）を `codex/biohub-multi-method-race`へpush済み。

NVIDIAデスクトップ移行用に `docker-compose.nvidia.yml` も追加した。通常Composeは現MacBookのCPU環境を維持し、移行先では公式CUDA wheel indexを `BIOHUB_TORCH_INDEX_URL` に指定して `gpus: all` でbuildする。Dockerfile側はCPU indexを既定にしつつ、override時だけ `uv sync --no-install-package torch` 後に指定indexのPyTorchを導入する。これによりCPU-onlyの現環境を壊さず、移行先では `--device auto` がCUDAを選べる。

## 14. Detector-Fixed Association Race 実データ結果（2026-08-21追記）

development sample `44b6_0113de3b` の100フレームを、公式TemporalUNet3D + SimpleNodeTransformerで一度だけ処理した。GT-free cacheは `artifacts/detector_fixed_race/full_auto/cache/44b6_0113de3b/` に保存され、cache hashは `0bc38739fa40d5dc38db99ec52a7ea5891849a6520d95ecbeed9bc126c6a62a8`、node `26,887`、candidate edge `7,240,938`、detector elapsed `4,841.270636372006 s`、requested/actual deviceは `auto/cpu` だった。

同一cacheから4方式を再生し、prediction GEFF生成後にRoyerLab公式metricで評価した。prediction writerは孤立detector nodeを除外し、既存canonical baselineとGEFF semanticsを一致させた。

| 手法 | prediction nodes / edges | Edge TP/FP/FN | Division TP/FP/FN | Edge Jaccard | Adjusted Edge Jaccard | Final Score | 公式baselineとの差 |
|---|---:|---:|---:|---:|---:|---:|---:|
| `official_ilp` | `25,994 / 23,536` | `46/2/4` | `0/0/0` | `0.8846153846153846` | `0.8837944835207503` | `0.8837944835207503` | `+0` |
| `harmonic_v1` | `26,301 / 24,205` | `48/2/2` | `0/0/0` | `0.9230769230769231` | `0.9211200215044129` | `0.9211200215044129` | `+0.0373255379836626` |
| `mutual_confidence` | `25,806 / 22,727` | `43/0/7` | `0/0/0` | `0.86` | `0.859829702970297` | `0.859829702970297` | `-0.0239647805504533` |
| `motion_gated` | `25,143 / 21,799` | `42/2/8` | `0/0/0` | `0.8076923076923077` | `0.8096115765422697` | `0.8096115765422697` | `-0.0741829069784806` |

4方式のcache-only association、GEFF生成、公式metricのwall timeは `116.29477067900007 s`。Gurobi licenseなしのためILPはSCIP fallbackで、official/harmonicの結果は既存canonical Strong Baseline v1とnode/edge数・metricが一致した。divisionのないsampleのためDivision Jaccardは`null`、公式summarizerはdivision termをdropした。

### 14.1 追加panel sample `44b6_0b24845f`

同じGT-free detector cache（cache hash `50739a79bf081799d37987bbdd800ee2f95c5246ce07adead21812a3599a3b65`、nodes `66,845`、candidate edges `45,354,474`、detector elapsed `5,476.415639576997 s`、`auto/cpu`）を固定し、associationだけを交換した。GT GEFFは `artifacts/detector_fixed_race/panel_data/train/44b6_0b24845f.geff` である。

| 手法 | prediction nodes / edges | Edge TP/FP/FN | Division TP/FP/FN | Division Jaccard | Edge Jaccard | Adjusted / Final | runtime [s] | 公式ILPとの差 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `official_ilp` | `55,324 / 44,335` | `39/9/10` | `0/0/0` | `null` | `0.6724137931034483` | `0.6262213541803576 / 0.6262213541803576` | `157.345064` | `+0` |
| `harmonic_v1` | `57,221 / 47,043` | `40/10/9` | `0/0/0` | `null` | `0.6779661016949152` | `0.6274705993317501 / 0.6274705993317501` | `161.112689` | `+0.0012492451513925` |
| `mutual_confidence` | `52,875 / 40,639` | `37/8/12` | `0/0/0` | `null` | `0.6491228070175439` | `0.6093777667220346 / 0.6093777667220346` | `147.215804` | `-0.0168435874583230` |
| `motion_gated` | `50,219 / 37,723` | `35/8/14` | `0/0/0` | `null` | `0.6140350877192983` | `0.5814113726151023 / 0.5814113726151023` | `145.915102` | `-0.0448099815652553` |

0bの公式ILPはcandidate `48,068`→selected `44,335`、node recall `0.9803921568627451`、total node ratio `0.6869644762921177`。4方式ともprediction manifestをGTを開く前に検証し、Gurobi不可のためSCIPへfallbackした。Division JaccardはGTにdivisionがないため`null`である。runtimeは各方式を同じsidecarから単独再生し、predictionディレクトリの`wall_time.txt`へ外部Python wrapperで保存した。

0bの最初の全方式再生では圧縮NPZの全列展開がOOM killとなった（`memory.events oom_kill`は過去失敗を含め7）。pair単位disk capture、chunked memmap、chunked validation、pair-contiguous grouping、edge sidecarを導入後、0bの4方式は追加OOMなしで完走した。

prediction GEFF、GT、receipt、cache sidecarは `docs/results/detector_fixed_association_race.md` に一覧化した。validation panelはdevelopment＋0bの2 sample・4方式まで完了し、0c/0db/12dfb391は画像とGTを固定済みだが、CPU detector cacheと公式metricが未完了である。

## 15. 2026-08-21 継続状況

最小3 sample panel達成に向け、`44b6_0c582fdc`のGT-free detector materializeを継続している。2026-08-21 05:20 UTC（14:20 JST）の監視時点でpair cacheは`44/99`、実処理PIDは生存、CPU約617%、RSS約0.8 GB、cgroup使用量約`5.84/7.65 GiB`、`oom_kill=7`（開始前から増加なし）、`READY`未生成である。0cのprediction GEFFと公式metricは未取得であり、完了後にこの統合版と詳細版を更新する。既存の0b結果・コード・レポートは変更せず、materializeだけを継続している。
