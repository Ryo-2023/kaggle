# Biohub Cell Tracking — ChatGPT報告用・全結果統合版

初版作成日: 2026-08-21（JST）
最終更新日: 2026-08-22（JST）
対象: Kaggle **Biohub – Cell Tracking During Development**
現在の性能改善ブランチ: `codex/biohub-095-performance`
履歴上のraceブランチ: `codex/biohub-multi-method-race`
本レポート更新前に確認した0.95 campaignのremote commit: `976e87c`
Task1実装完了時のコードHEAD: `17135f0`
本レポートが対象とするvalidation receipt実装commit: `fbfbf26`
実験artifactに記録されたrace実装commit: `ac2ece5`

この文書は、Strong Baseline v1、Multi-Method Benchmark Race、追加性能改善実験、公開手法の実行可能性調査、検証結果を、ChatGPTへそのまま渡せるように1ファイルへ統合したものである。

## 1. 結論

- 公式 TemporalUNet3D + SimpleNodeTransformer + ILP pipelineを実データで完走した。
- 同一sample・同一公式metricで、追加3 lane（`blob_lap`、`cc_flow`、`motion_lap`）を推論からprediction GEFF、公式評価まで完走した。
- 旧development単一sampleの全比較Best Methodは `harmonic v1`（Final Score `0.9211200215044129`）。
- detector-fixed raceでは同一TemporalUNet3D detector cacheを固定し、development、`44b6_0b24845f`、`44b6_0c582fdc`、`44b6_0db75fae`、`44b6_12dfb391`の5 sampleで4 association方式を公式metricまで完走した。各sampleのBestはharmonic v1である。
- 0dbのBestは`harmonic_v1`（Final Score `0.8249556959559359`）で、official ILP `0.8150423866970982`を`+0.0099133092588377`上回った。harmonicはEdge TP/FP/FN `134/8/17`、Division TP/FP/FN `0/1/0`で、division false positiveが1件ある。
- 0dbの公式ILPはprediction `18,325 nodes / 16,060 edges`、Edge TP/FP/FN `133/9/18`、Adjusted/Final `0.8150423866970982`を取得した。GTは評価phase以外に使っていない。
- `44b6_0c582fdc`も同じdetector固定条件で4方式を完走し、0cのBestは`harmonic_v1`（Final Score `0.8022386963904503`）。0cのofficial ILPは`0.738499713856499`で、harmonic差は`+0.0637389825339513`だった。
- detector-fixed 5 sampleのunweighted macro Final Scoreはofficial `0.7688958987642377`、harmonic `0.7944143977140719`、mutual `0.7467735686449968`、motion `0.7187007022873142`。harmonicのofficial差は `+0.025518498949834156` で、5/5 sampleでofficial ILPを上回った。
- `44b6_12dfb391` はdivisionを含むsampleだが、cache、4方式のprediction GEFF、manifest検証、公式metricまで完走した。divisionは全方式でTP `0` / FN `1`、harmonicのみFP `3`であり、division対応は未解決である。
- 新規race laneでは `blob_lap`（Final Score `0.9140773262846648`）が最良だった。
- 追加のNMS仮説（3.0→3.5 µm）は `0.9172062183593925` を得て、固定blob lane比 `+0.0031288920747277`。ただし単一sampleでharmonic v1未達のため、複数sample検証前の昇格候補として扱う。
- `cc_flow` は detector の node recall が低く不採用、`motion_lap` は blob単独より悪化した。
- HOCT、Trackastra、Ultrack、Linajea、DeepCenterは、入力契約・依存・checkpoint・source確認の不足により、今回の公式スコア比較には含めていない。

### 1.1 0.95 Performance Goalの現在地

2026-08-22、固定5サンプルのvendored RoyerLab由来公式Final Score macroを `0.95` 以上にする目標を開始した。GTはprediction GEFFとmanifestを永続化・hash検証した後の公式評価とerror analysisに使用でき、完了済みの評価・error analysisは次の独立したmethod/model family選択に使用できる。ただし、推論、feature生成、cache生成、candidate生成、association input、parameter fitting、current-run branch調整へGTを戻すことは禁止する。Kaggleへの外部submissionはこのcampaignに含めない。

| 項目 | 現在値 / 状態 |
|---|---|
| 固定panel | `0113`、`0b`、`0c`、`0db`、divisionを含む`12df`の5件。除外しない |
| macro target | `0.95` |
| Current BestKnown（既存実測best） | `harmonic_v1` macro `0.7944143977140719` |
| targetとの差 | `+0.1555856022859281` |
| 第一候補 | 公開 `Recipe C` dual-seed、D4 TTA、ILP、gap/safe-division/track repair |
| 一次source | `https://github.com/asapacsin/biohub-cell-tracking`、commit `843a47fdd531bdf7e6377673135519c54b69ae28`、Apache-2.0 |
| 固定config | `recipe_c_motion_off_edge_0_40_det0_96875.yaml`、SHA-256 `0e5758f3ea76ba015fb71c35bc749e136c009237e093d544a89a4b03a8c66ced` |
| source側5件参考macro | `0.9560058787896148`（`official-spec-lite` recordsの算術平均。こちらの公式metricでは未再現） |
| 本repoの0.95判定 | **未評価・未達成扱い**。実prediction GEFFとvendored official receiptが揃うまで合格としない |
| 現在の作業 | Task1（source/config/checkpoint契約）は完了。次はTask2（protocol/selection lock） |

source側参考値は次のとおりである。0bのAdjusted値が1を超えることも含め、source recordをそのまま参照値として記録し、本repoの公式実測と混ぜない。

| sample | source側 `adj_edge_jaccard` |
|---|---:|
| `44b6_0113de3b` | `0.9613642399534071` |
| `44b6_0b24845f` | `1.0139018143009606` |
| `44b6_0c582fdc` | `0.9062409250942050` |
| `44b6_0db75fae` | `0.9638150186669892` |
| `44b6_12dfb391` | `0.9347073959325117` |
| **unweighted macro** | **`0.9560058787896148`** |

Recipe Cは次の2つのKaggle assetを必要とする。primary packだけではsecondary seedが欠けるため実行しない。

| asset | 内容 | 期待SHA-256 |
|---|---|---|
| `pilkwang/biohub-tracking-support-pack-50ep-v1` | predictor repo + primary `split_0` checkpoint、v10 / CC0 | predictor `c44e771ba5980b820f93091e03a303c25dfe8f3232e501f54dc9565731c234b`、primary checkpoint `12f6881ee3620a831697ca098ff8f48e687a24225f4e048b538deec3562fe771` |
| `pilkwang/biohub-temporal-unet3d-seed314159-v1` | secondary seed checkpoint、v2 / CC0。run-localで`seed_314159` pathへstage | `9bac2fa0dadc4a6fc1899e0caf187f4b553e0a7cd90ba1261a68b35ffe9e305f` |
| 展開後の2 asset合計 | primary + secondary | 約711 MB |

一次sourceの`run_prediction()`はCUDA未検出時に強制停止する。性能configや一次sourceを変えずにCPU/MPS互換を得るため、同sourceの`build_predict_command()`が生成したargvをadapterから実行し、copy済みrun-local predictorのdevice選択だけを `CUDA → MPS → CPU` にする。現在のLinux DockerはPyTorch CPU wheelのためCPU、NVIDIA desktopでは同じ`auto`指定でCUDA、macOS nativeの対応実行系ではMPSを優先する。ILP、GEFF I/O、公式metricはCPUのままとする。

### 1.2 Task1完了と適応ループ

Task1（Recipe Cのsource・config・checkpoint契約固定）は完了し、独立したLuna reviewは `APPROVED` だった。Task1の実装履歴は `2a60cc0`、`87cf762`、`6887576`、`17135f0` である。対象テストは `82 passed`、全リポジトリの確認は `416 passed, 9 skipped, 2 warnings`、Task1対象Ruffはpassだった。これは契約・provenance検証の完了であり、Recipe Cの5 sample公式評価や0.95到達を意味しない。

今後の適応ループは、(1) 実験前にTask2のprotocol/selection lockへpanel、source、config、checkpoint、code commit、仮説、control、採否基準を固定し、(2) GT-freeで推論してprediction GEFFとmanifestを永続化・hash検証し、(3) その後に公式評価とerror analysisを行い、(4) 結果を次に検証する独立したmethod/model familyの選択へだけ使い、(5) 5 sampleの結果をappend-only ledgerへ記録する、という順序で進める。GTは現runの推論、feature、cache、candidate、association input、parameter fitting、current-run branch調整には使用しない。同一method familyで5回連続してBestKnownを更新できなければmicrotuningを停止してfailure analysisへ戻り、全family通算10実験以上でmeaningful improvementがなければarchitecture reviewと公開手法の再調査へ切り替える。

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

GT GEFFは、推論・feature生成・cache・candidate生成・association input・parameter fitting・current-run branch調整には使用せず、prediction GEFFとmanifestを永続化・hash検証した後の公式評価で開いた。公式評価後のerror analysisと、次に検証する独立したmethod/model familyの選択には使用できるが、同じrunへ戻さない。

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

harmonic v1では、image、detector、checkpoint、candidate construction、ILP costを変更せず、forward/reverse scoreのharmonic結合と再標準化を追加した。保存されたreceiptを根拠に結果を報告している。後続Lane F監査ではforward-only temperature variantでも同等以上のTPを回収したため、改善原因をreverse passそのものとは断定せず、再標準化に伴うtemperature/sharpening効果を含むものと解釈する。保持されたnotebook JSONからsource cellを独立監査するfixtureは不足しており、その監査はBLOCKEDである。source textを推測・捏造していない。

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

downloadしたconfigに `pool_kernel_um=5.0` が含まれる一方、upstream run receiptは `3.0` µmを報告する。ただし現在の5 sample scaleでは両設定とも実効kernel `(3,3,3)` となるため、観測結果を変えた重大欠陥ではなく設定差として記録する。

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
| 0c detector cache | GT-free nodes `34,910`、candidate edges `12,459,009`、hash `2bd90bee3abf0afb07abdc971bfb45235a33bb931feaf6bfb3b884759682f748`、sidecar約0.77 GiB |
| 0b adapter source SHA-256 | `e914af35a2b68f2509027429efaa6ab29670be822212ae7c8628985f42a4ac72` |
| 0b image SHA-256 | `7f7809f8948ce7f6c5c7cfb03d5b6fb8f140c725d16f0d63653d59620845d33a` |
| 0c image SHA-256 | `8143958530532e2701edc7e9c12b296167eeae1d672d709c495e0fdf137fb2d3` |
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

- campaign前のdetector-fixed full pytestは `199 passed, 2 warnings`（2026-08-22 JST）。warningはdivisionなしsplitでdivision termをdropする既知の公式metric警告2件である。
- Task1の最終full pytestは `416 passed, 9 skipped, 2 warnings`。Task1対象テストは `82 passed`、Task1対象Ruffはpassだった。
- validation receipt実装はcommit `fbfbf26`。初回レビューの2件を修正し、fresh re-reviewは `APPROVED`。5 sample×4方式の実データ集約も完了した。
- 既存のreport＋validation receipt限定テストは `25 passed`、対象Ruffは `All checks passed!`（2026-08-22 JST）。
- detector-fixed関連確認: `12 passed in 3.83s`、対象Ruff `All checks passed!`
- race対象Ruff: `All checks passed!`
- report対象pytest: `4 passed`
- campaign前のfull repository Ruffには24件の既存問題（`src/biohub/official_metrics/metrics.py`、`src/biohub/visualizer/*`）が残るという履歴がある。Task1の最終Ruff確認はpassであり、今回のTask1契約変更による失敗はない。

## 11. Commit・push・成果物

### Git

- v1 branch: `feat/strong-baseline-v1`、commit `9edb7e1`、push済み
- historical race branch: `codex/biohub-multi-method-race`
- current performance branch: `codex/biohub-095-performance`
- 0.95 campaignの初期設計・計画commit: `de582ef`
- 本レポート更新前に確認したremote commit: `976e87c`
- Task1実装完了時のコードHEAD: `17135f0`
- Task1完了履歴: `2a60cc0`、`87cf762`、`6887576`、`17135f0`
- validation receipt実装commit: `fbfbf26`
- current remote: `origin/codex/biohub-095-performance`
- PR作成URL: <https://github.com/Ryo-2023/kaggle/pull/new/codex/biohub-095-performance>

### report

- 本統合版: `docs/results/chatgpt_submission_report_ja.md`
- race詳細: `docs/results/multi_method_benchmark_race.md`
- v1詳細: `docs/results/strong_baseline_v1.md`
- feasibility詳細: `docs/results/multi_method_feasibility_ja.md`
- 0.95設計: `docs/superpowers/specs/2026-08-22-biohub-095-performance-design.md`
- 0.95実装計画: `docs/superpowers/plans/2026-08-22-biohub-095-performance.md`

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
- `artifacts/detector_fixed_race/panel_auto/cache/44b6_0c582fdc/`（canonical NPZ + `candidate_edges.mmap/`）
- `artifacts/detector_fixed_race/panel_runs_0c_official/44b6_0c582fdc/`
- `artifacts/detector_fixed_race/panel_runs_0c_harmonic/44b6_0c582fdc/`
- `artifacts/detector_fixed_race/panel_runs_0c_mutual/44b6_0c582fdc/`
- `artifacts/detector_fixed_race/panel_runs_0c_motion/44b6_0c582fdc/`
- `artifacts/detector_fixed_race/harmonic_sweep/`（development/0b/0cの0.10/0.20/0.30、0db/12dfの0.10 variantを含む）
- `artifacts/detector_fixed_race/panel_auto/cache/44b6_0db75fae/`（canonical NPZ + `candidate_edges.mmap/`）
- `artifacts/detector_fixed_race/panel_runs_0db_official/44b6_0db75fae/`
- `artifacts/detector_fixed_race/panel_runs_0db_harmonic/44b6_0db75fae/`
- `artifacts/detector_fixed_race/panel_runs_0db_mutual/44b6_0db75fae/`
- `artifacts/detector_fixed_race/panel_runs_0db_motion/44b6_0db75fae/`
- `artifacts/detector_fixed_race/panel_auto/cache/44b6_12dfb391/`（canonical NPZ + `candidate_edges.mmap/`）
- `artifacts/detector_fixed_race/panel_data/train/44b6_12dfb391.geff`（評価専用GT）
- `artifacts/detector_fixed_race/panel_runs_12df_{official,harmonic,mutual,motion}/44b6_12dfb391/`
- `artifacts/detector_fixed_race/panel_runs_dev_{official,harmonic,mutual,motion}/44b6_0113de3b/`（development個別再生・manifest修復済み）
- `artifacts/detector_fixed_race/validation_receipt.json`
- `artifacts/detector_fixed_race/panel.json`

Kaggleへの外部提出は実施していない。prediction生成・local official evaluationまでであり、ユーザー承認なしのKaggle submissionは行っていない。

## 12. 既知の問題・未解決事項・次の一手

### 既知の問題

1. 旧blob raceの比較は単一Kaggle train sampleのみ。detector-fixed raceはdevelopment＋0b＋0c＋0db＋12dfの5 sampleであり、leaderboard性能やdense-truth性能を意味しない。
2. 12dfb391ではdivision GTを含むが、全方式でDivision TP/FN=`0/1`、harmonicのみFP=`3`だった。division対応は未解決であり、5 sample平均をdivision一般性能へ外挿しない。
3. harmonicのsource-cell独立監査は保持notebook JSON不足でBLOCKED。
4. 旧official receiptにはraw candidate count/digestがなく、detector driftを完全には比較できない。detector-fixed cacheはcandidate digestとcache hashを保存している。
5. official upstreamのconfigにpool kernel `5.0`とrun報告`3.0`の設定差があるが、現panelでは実効kernelが同じである。
6. high-level viewerの`matched_node_id` / `match_node_id`不一致でGUI表示は未完了。ただしheadless overlay evidenceは保存済み。
7. 現行Dockerの全laneはCPU-only。detector-fixedはCUDA→MPS→CPUの自動fallbackを実装済みだが、GPU実測性能はまだ主張していない。
8. 公開Recipe Cはsource側参考macro `0.9560058787896148` があるが、本repoのvendored official metricは未取得である。HOCT/Trackastra/Ultrack/Linajea/DeepCenterの性能数値もない。
9. Kaggle competitionはnotebook-only submissionである。ローカルprediction生成は外部提出許可を意味せず、GPU runtimeとoffline packagingは未検証である。

### 次の一手

1. 次の作業はTask2のprotocol/selection lockを実装し、PANEL_V1、source/config/checkpoint、code commit、仮説、control、採否基準を実験前に固定する。
2. GT-free 2-frame smokeを通し、prediction GEFFとmanifestの永続化・hash検証後にだけGTを開く公式評価境界を確認する。
3. selection lockを変更せず5 sampleを除外なしで逐次推論・公式評価する。macro `>=0.95` の実receiptが得られるまで達成扱いにしない。
4. 未達時だけRAM-safe診断でnode/candidate/oracle上限を分解する。診断結果はpersisted prediction後のerror analysisとして次の独立したmethod/model family選択に使用できるが、GTを推論、feature、cache、candidate、association input、parameter fitting、current-run branch調整へ戻さない。
5. `reverse_weight=0.10`、division weight `.6`、Lane F temperature候補は既存panel結果から採用しない。

現時点での採用判断は、**旧race全体Bestはharmonic v1、旧race新規Bestはblob_lap、detector-fixedの5 sampleではharmonic_v1が5/5でofficial ILPを上回った**である。Task1の契約固定は完了したがRecipe Cの本repo公式評価は未実施で、0.95は未達成扱いである。division対応も未解決である。

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

修正後の4フレーム実データsmokeはcache公開まで完走し、node `897`、候補edge `151,830`、feature衝突観測 `453`を記録した。2フレーム `auto` smokeでは `requested_device=auto`、実選択 `cpu` を確認した。全100フレームのdevelopment、0b、0c、0db、12dfのcache生成と公式metric、4方式比較が完了した。

再現コマンド（GPU環境ではautoでGPUを選択）:

```bash
docker compose exec -T biohub sh -lc 'cd /workspace/biohub-cell-tracking-during-development/scratch/strong-baseline-v1/biohub-cell-tracking-during-development && PYTHONPATH=/workspace/biohub-cell-tracking-during-development/scratch/strong-baseline-v1/biohub-cell-tracking-during-development/src uv run python scripts/run_detector_fixed_race.py materialize --sample 44b6_0113de3b --train-root /workspace/biohub-cell-tracking-during-development/data/train --upstream-root artifacts/strong_baseline_v1/upstream --checkpoint artifacts/strong_baseline_v1/inputs/cellmot-baseline-artifacts/weights/unet_transformer/split_0/edge_predictor_best.pth --output artifacts/detector_fixed_race/full_auto'
```

関連commit: `830ccab`（accelerator-first device fallback、contextual feature衝突の記録）および `eb6e472`（dense cacheのedge memmap sidecar、chunked validation、pair-contiguous grouping）を含む実装を履歴へ保持している。validation receipt evidence強化commitは `fbfbf26`、現在の0.95 campaign設計・計画commitは `de582ef` である。

NVIDIAデスクトップ移行用に `docker-compose.nvidia.yml` も追加した。通常Composeは現MacBookのCPU環境を維持し、移行先では公式CUDA wheel indexを `BIOHUB_TORCH_INDEX_URL` に指定して `gpus: all` でbuildする。Dockerfile側はCPU indexを既定にしつつ、override時だけ `uv sync --no-install-package torch` 後に指定indexのPyTorchを導入する。これによりCPU-onlyの現環境を壊さず、移行先では `--device auto` がCUDAを選べる。

## 14. Detector-Fixed Association Race 実データ結果（2026-08-21追記）

development sample `44b6_0113de3b` の100フレームを、公式TemporalUNet3D + SimpleNodeTransformerで一度だけ処理した。GT-free cacheは `artifacts/detector_fixed_race/full_auto/cache/44b6_0113de3b/` に保存され、cache hashは `0bc38739fa40d5dc38db99ec52a7ea5891849a6520d95ecbeed9bc126c6a62a8`、node `26,887`、candidate edge `7,240,938`、detector elapsed `4,841.270636372006 s`、requested/actual deviceは `auto/cpu` だった。

同一cacheから4方式を再生し、prediction GEFF生成後にRoyerLab公式metricで評価した。prediction writerは孤立detector nodeを除外し、既存canonical baselineとGEFF semanticsを一致させた。

| 手法 | prediction nodes / edges | Edge TP/FP/FN | Division TP/FP/FN | Division Jaccard | Edge Jaccard | Adjusted Edge Jaccard | Final Score | 公式baselineとの差 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `official_ilp` | `25,994 / 23,536` | `46/2/4` | `0/0/0` | `null` | `0.8846153846153846` | `0.8837944835207503` | `0.8837944835207503` | `+0` |
| `harmonic_v1` | `26,301 / 24,205` | `48/2/2` | `0/0/0` | `null` | `0.9230769230769231` | `0.9211200215044129` | `0.9211200215044129` | `+0.0373255379836626` |
| `mutual_confidence` | `25,806 / 22,727` | `43/0/7` | `0/0/0` | `null` | `0.86` | `0.859829702970297` | `0.859829702970297` | `-0.0239647805504533` |
| `motion_gated` | `25,143 / 21,799` | `42/2/8` | `0/0/0` | `null` | `0.8076923076923077` | `0.8096115765422697` | `0.8096115765422697` | `-0.0741829069784806` |

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

prediction GEFF、GT、receipt、cache sidecarは `docs/results/detector_fixed_association_race.md` に一覧化した。validation panelはdevelopment＋0b＋0c＋0db＋12dfb391の5 sample・4方式まで完了し、全runでCPU detector cacheと公式metricを取得した。

### 14.2 追加panel sample `44b6_0c582fdc`

0cも同じGT-free detector cache固定で完走した。cache hashは `2bd90bee3abf0afb07abdc971bfb45235a33bb931feaf6bfb3b884759682f748`、nodes `34,910`、candidate edges `12,459,009`、detector elapsed `5,447.649957480986 s`、requested/actual deviceは `auto/cpu`。GT GEFFは `artifacts/detector_fixed_race/panel_data/train/44b6_0c582fdc.geff` である。

| 手法 | prediction nodes / edges | Edge TP/FP/FN | Division TP/FP/FN | Division Jaccard | Edge Jaccard | Adjusted / Final | runtime [s] | officialとの差 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `official_ilp` | `32,245 / 28,388` | `57/6/13` | `0/0/0` | `null` | `0.75` | `0.738499713856499 / 0.738499713856499` | `59.321964` | `+0` |
| `harmonic_v1` | `32,602 / 29,176` | `62/6/8` | `0/0/0` | `null` | `0.8157894736842105` | `0.8022386963904503 / 0.8022386963904503` | `58.106748` | `+0.0637389825339513` |
| `mutual_confidence` | `31,638 / 27,174` | `55/5/15` | `0/0/0` | `null` | `0.7333333333333333` | `0.7236807592340891 / 0.7236807592340891` | `57.710211` | `-0.0148189546224099` |
| `motion_gated` | `31,072 / 26,243` | `50/6/20` | `0/0/0` | `null` | `0.6578947368421053` | `0.65056701593744 / 0.65056701593744` | `58.797033` | `-0.0879326979190590` |

0cのprediction manifestも4方式すべてGTを開く前に検証した。candidate→selectedはofficial `30,140→28,388`、harmonic `31,164→29,176`、mutual `28,526→27,174`、motion `27,154→26,243`。0cのdivisionなしのためDivision Jaccardは`null`である。これによりdetector-fixed raceはdevelopment＋0b＋0c＋0db＋12dfb391の5 sample panelを完了した。

## 15. 2026-08-21 panel完了状況

`44b6_0c582fdc`のGT-free materialize、edge memmap化、official/harmonic/mutual/motionのGEFF生成・公式metric評価が完了した。0db75faeとdivisionを含む12dfb391も同じ手順で完走し、事前固定panelはdevelopment＋0b＋0c＋0db＋12dfb391の5 sampleを完了した。全materialize/replayで`oom_kill`は既存値7から増加せず、GPU fallback設定は`auto→cpu`としてreceiptへ保存された。

## 16. Harmonic reverse weight性能改善

detector再計算なしで、同一GT-free cache上に`harmonic_v1`の`reverse_weight=0.10/0.20/0.30`を個別再生した。0dbと12dfでは`0.10`の追加variantも完走し、5 sampleの`0.10`平均を再集計できる状態になった。

| reverse_weight | 集計範囲 | 平均Final Score | `0.20`との差 | 判定 |
|---:|---|---:|---:|---|
| `0.10` | 5 sample | `0.7931993011556243` | `-0.0012150965584476` | 既定値に不採用 |
| `0.20` | 5 sample（canonical） | `0.7944143977140719` | `+0` | 公開Strong Baseline v1の既定値として維持 |
| `0.30` | 3 sampleのみ | `0.7777614914000653` | — | 5 sample比較は未完了のため採用判断に使わない |

追加variantの実測値は次のとおりである。

| sample | Final Score | Edge TP/FP/FN | Division TP/FP/FN | prediction / receipt |
|---|---:|---|---|---|
| `44b6_0db75fae` (`rw=0.10`) | `0.8138281708509945` | `133/9/18` | `0/1/0` | `artifacts/detector_fixed_race/harmonic_sweep/44b6_0db75fae_rw_0p10/44b6_0db75fae/` |
| `44b6_12dfb391` (`rw=0.10`) | `0.8012113309947029` | `689/85/84` | `0/3/1` | `artifacts/detector_fixed_race/harmonic_sweep/44b6_12dfb391_rw_0p10/44b6_12dfb391/` |

12df単体では`rw=0.10`がcanonical `rw=0.20`（`0.7962869753878102`）を上回るが、0dbでは`rw=0.20`（`0.8249556959559359`）を下回る。5 sampleの生スコアを10進数で再集計すると、`rw=0.10`は`0.20`より`-0.0012150965584476`であるため、既定値は`0.20`から変更しない。variant GEFF・manifest・receipt・runtimeは `artifacts/detector_fixed_race/harmonic_sweep/` に保存し、CLIは`--harmonic-reverse-weight`で再現できる。

## 17. 追加panel `44b6_0db75fae` 完了結果（2026-08-21）

0dbは画像とGTを固定したうえで、GT-free detector cacheのmaterialize、edge sidecar化、4方式の個別再生、prediction manifest検証、GTを評価時だけ開く公式metricまで完走した。GT GEFFは `artifacts/detector_fixed_race/panel_data/train/44b6_0db75fae.geff` である。

cacheは `artifacts/detector_fixed_race/panel_auto/cache/44b6_0db75fae/` に保存した。cache hashは `bdaa6c60fd1ccc14abe0bcc0fde1a0efe8330692e10b9926e898c909ee89a3e9`、nodes `19,599`、candidate edges `4,346,571`、detector calls `100`、forward/reverse edge calls `99/99`、detector elapsed `4839.955556327011 s`、requested/actual deviceは `auto/cpu` である。checkpoint SHA-256は `347915de9c33883cb2ee69832a8e4552c88b1ec692d0fbfe956422467d3d4235`、upstream source commitは `075fc5f5a52d11077f9dc2b074644618f26939e2`、image SHA-256は `c16d44a2dc0b08ab6dd47401c5bf6b9e6e52ebcb5b638decee88dcdc0203eb73`、adapter SHA-256は `e914af35a2b68f2509027429efaa6ab29670be822212ae7c8628985f42a4ac72`。manifestは `ground_truth_included=false` を記録する。sidecarは `artifacts/detector_fixed_race/panel_auto/cache/44b6_0db75fae/candidate_edges.mmap/`、schemaは `detector_fixed.cache_mmap.v1`、source cache hashはcanonical manifestと一致し、cacheとsidecarの合計は約405 MBである。

| 手法 | prediction nodes / edges | Edge TP/FP/FN | Division TP/FP/FN | Division Jaccard | Edge Jaccard | Adjusted / Final | wall time [s] | 公式ILPとの差 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `official_ilp` | `18,325 / 16,060` | `133/9/18` | `0/0/0` | `null` | `0.83125` | `0.8150423866970982 / 0.8150423866970982` | `22.204889` | `+0` |
| `harmonic_v1` | `18,576 / 16,523` | `134/8/17` | `0/1/0` | `0.0` | `0.8427672955974843` | `0.8249556959559359 / 0.8249556959559359` | `21.593063` | `+0.0099133092588377` |
| `mutual_confidence` | `18,124 / 15,474` | `124/4/27` | `0/0/0` | `null` | `0.8` | `0.7854502771437888 / 0.7854502771437888` | `22.003911` | `-0.0295921095533094` |
| `motion_gated` | `17,496 / 14,606` | `125/4/26` | `0/0/0` | `null` | `0.8064516129032258` | `0.795087139897136 / 0.795087139897136` | `19.994326` | `-0.0199552467999622` |

candidate→selectedはofficial `16,889→16,060`、harmonic `17,469→16,523`、mutual `16,111→15,474`、motion `14,966→14,606`。harmonicはofficialよりEdge TPが1件増、FPが1件減、FNが1件減った一方、division FPを1件生成した。

prediction GEFF、receipt、wall timeは次のとおりである。各 `race_receipt.json` には同じcache hashと、GTを開く前にprediction manifestを検証した記録がある。

- official: `artifacts/detector_fixed_race/panel_runs_0db_official/44b6_0db75fae/official_ilp.geff`、同ディレクトリの `race_receipt.json`、`wall_time.txt`
- harmonic: `artifacts/detector_fixed_race/panel_runs_0db_harmonic/44b6_0db75fae/harmonic_v1.geff`、同ディレクトリの `race_receipt.json`、`wall_time.txt`
- mutual: `artifacts/detector_fixed_race/panel_runs_0db_mutual/44b6_0db75fae/mutual_confidence.geff`、同ディレクトリの `race_receipt.json`、`wall_time.txt`
- motion: `artifacts/detector_fixed_race/panel_runs_0db_motion/44b6_0db75fae/motion_gated.geff`、同ディレクトリの `race_receipt.json`、`wall_time.txt`

コンテナに`/usr/bin/time`が存在しなかったため、wall timeは同じ単独プロセスを`time.monotonic()`で外側から測定した。4方式ともreturn code `0`、Gurobi licenseなしによるSCIP fallbackのみで、cgroup `oom_kill=7`はmaterialize開始前から増加しなかった。

## 18. 追加panel `44b6_12dfb391` 完了結果（2026-08-22更新）

12dfb391は画像とdivisionを含むGTを固定し、GT-free detector cacheのmaterialize、edge sidecar化、4方式の個別再生、prediction manifest検証、GTを評価時だけ開く公式metricまで完走した。GT GEFFは `artifacts/detector_fixed_race/panel_data/train/44b6_12dfb391.geff` である。

cacheは `artifacts/detector_fixed_race/panel_auto/cache/44b6_12dfb391/` に保存した。manifestの実値は次のとおりである。

| 項目 | 値 |
|---|---:|
| cache hash | `3fefd2f62dba07f0e2c7266a3fa7b0ee97f9a3ff6bb652598c35622bdfc75a40` |
| node数 / candidate edge数 | `62,219 / 38,940,536` |
| detector / forward / reverse calls | `100 / 99 / 99` |
| feature conflict observations | `60,980` |
| detector elapsed | `5,878.221322058991 s`（約98.0分） |
| requested / actual device | `auto / cpu` |
| checkpoint SHA-256 | `347915de9c33883cb2ee69832a8e4552c88b1ec692d0fbfe956422467d3d4235` |
| source repository / commit | `https://github.com/royerlab/kaggle-cell-tracking-competition.git` / `075fc5f5a52d11077f9dc2b074644618f26939e2` |
| image SHA-256 | `94622f407ef6959ee4be8c126174216bee404fb1a26cf310f840377f41bbbc81` |
| adapter source SHA-256 | `e914af35a2b68f2509027429efaa6ab29670be822212ae7c8628985f42a4ac72` |
| GT-free manifest | `ground_truth_included=false` |

edge sidecarは `artifacts/detector_fixed_race/panel_auto/cache/44b6_12dfb391/candidate_edges.mmap/`、schemaは `detector_fixed.cache_mmap.v1`、`edge_count=38,940,536`、`source_cache_hash`はcanonical cache hashと一致する。`du`はcache root全体が約`3.4 GiB`、sidecarが約`2.4 GiB`、canonical `candidate_edges.npz`が約`1.0 GiB`だった。sidecarとchunked validationを使った4方式のreplayはすべてreturn code `0`で、実行監視の`memory.events`では既存の`oom_kill=7`（0b初回の過去失敗を含む）から増加せず、追加OOM killはなかった。

| 手法 | prediction nodes / edges | candidate→selected | Edge TP/FP/FN | Division TP/FP/FN | Division Jaccard | Edge Jaccard | Adjusted / Final | node recall / total node ratio | wall time [s] |
|---|---:|---:|---|---|---:|---:|---:|---:|---:|
| `official_ilp` | `59,632 / 54,744` | `56,455→54,744` | `668/81/105` | `0/0/1` | `0.0` | `0.7822014051522248` | `0.7809215555664836 / 0.7809215555664836` | `0.9822335025380711 / 0.01636214889555495` | `162.105704` |
| `harmonic_v1` | `60,037 / 55,707` | `57,654→55,707` | `688/89/85` | `0/3/1` | `0.0` | `0.7981438515081206` | `0.7962869753878102 / 0.7962869753878102` | `0.9885786802030457 / 0.023264930460867195` | `147.6` |
| `mutual_confidence` | `59,135 / 52,882` | `54,181→52,882` | `648/84/125` | `0/0/1` | `0.0` | `0.7561260210035006` | `0.7555293371547744 / 0.7555293371547744` | `0.9847715736040609 / 0.007891328061085355` | `164.1` |
| `motion_gated` | `58,618 / 52,214` | `53,093→52,214` | `644/78/129` | `0/0/1` | `0.0` | `0.7567567567567568` | `0.7568264064446228 / 0.7568264064446228` | `0.9771573604060914 / -0.0009203708753749659` | `149.6` |

12dfでは全方式がDivision TP=`0` / FN=`1`で、harmonicのみDivision FP=`3`となった。harmonicのFinal Scoreはofficial ILPを`+0.015365419821326665`上回るが、division false positiveを含むため、division対応は未解決である。4方式すべてで同じcache hashを使い、prediction manifestをGTを開く前に検証した。

prediction GEFF、manifest、receipt、runtime、GT、cacheは次の場所にある。

- official: `artifacts/detector_fixed_race/panel_runs_12df_official/44b6_12dfb391/official_ilp.geff`、同ディレクトリの `prediction_manifest.json`、`race_receipt.json`、`wall_time.txt`
- harmonic: `artifacts/detector_fixed_race/panel_runs_12df_harmonic/44b6_12dfb391/harmonic_v1.geff`、同ディレクトリの `prediction_manifest.json`、`race_receipt.json`、`wall_time.txt`
- mutual: `artifacts/detector_fixed_race/panel_runs_12df_mutual/44b6_12dfb391/mutual_confidence.geff`、同ディレクトリの `prediction_manifest.json`、`race_receipt.json`、`wall_time.txt`
- motion: `artifacts/detector_fixed_race/panel_runs_12df_motion/44b6_12dfb391/motion_gated.geff`、同ディレクトリの `prediction_manifest.json`、`race_receipt.json`、`wall_time.txt`
- GT: `artifacts/detector_fixed_race/panel_data/train/44b6_12dfb391.geff`
- cache / sidecar: `artifacts/detector_fixed_race/panel_auto/cache/44b6_12dfb391/` / `artifacts/detector_fixed_race/panel_auto/cache/44b6_12dfb391/candidate_edges.mmap/`

## 19. detector-fixed 5 sample集約とvalidation receipt

`artifacts/detector_fixed_race/validation_receipt.json`を直接読み、development、0b、0c、0db、12dfb391の20 records（5 sample × 4方式）を再集計した。`failed_samples=[]`、`ground_truth_usage="official metric evaluation only"`、panel SHA-256は `d6621f43f3308b4e6e52f00f2a1bf9c4747ee12e03b073c7c3907c0f1eef6de7` である。

| association | 5 sample平均 Final Score | officialとの差 | n | officialを上回ったsample数 |
|---|---:|---:|---:|---:|
| `official_ilp` | `0.7688958987642377` | `+0` | `5` | `0` |
| `harmonic_v1` | `0.7944143977140719` | `+0.025518498949834156` | `5` | `5` |
| `mutual_confidence` | `0.7467735686449968` | `-0.02212233011924092` | `5` | `0` |
| `motion_gated` | `0.7187007022873142` | `-0.0501951964769235` | `5` | `0` |

harmonicは5/5 sampleでofficial ILPを上回った。developmentの旧runは4方式が単一出力ディレクトリを共有し、最後の方式が`prediction_manifest.json`を上書きしていたため、validation receiptの初回集約で`prediction_path mismatch`を検出した。detector cacheの再計算は行わず、`panel_runs_dev_official/`、`panel_runs_dev_harmonic/`、`panel_runs_dev_mutual/`、`panel_runs_dev_motion/`へ個別再生してmanifestを修復した。修復後の各`race_receipt.json`は方式固有のprediction path・cache hashを持ち、validation receiptの該当recordが`prediction_manifest_validated_before_gt=true`と評価metricを記録する。最終`validation_receipt.json`は`failed_samples=[]`となった。

最終full pytestは `199 passed, 2 warnings`、report＋validation receipt限定テストは `25 passed`、対象Ruffは `All checks passed!` だった。full repository Ruffには既存問題が残る。
