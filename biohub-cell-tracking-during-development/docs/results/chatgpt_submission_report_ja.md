# Biohub Cell Tracking — ChatGPT報告用・全結果統合版

初版作成日: 2026-08-21（JST）
最終更新日: 2026-08-24（JST）
対象: Kaggle **Biohub – Cell Tracking During Development**
現在の性能改善ブランチ: `codex/biohub-095-performance`
履歴上のraceブランチ: `codex/biohub-multi-method-race`
本レポート更新時点のcode gate HEAD: `2e8ce61`（fd-backed GEFF publication fix round、二重独立review **APPROVED**、push済み）
固定6-frame smoke contract: `e713a16`（push済み。Task4実装`42f9181`/`45423b0`、predictor SHA修正`421eedf`を含む）
Task1実装完了時のコードHEAD: `17135f0`
Task2実装完了時のlocal HEAD: `e1416e4`
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
- Task4のround-4/round-5 code reviewと固定6-frame smoke contractのfresh reviewは **APPROVED**。実画像2-frame smokeはGT-freeで実施し、0-nodeの根因はILPのraw段階における2-frame smoke horizonとの数学的非互換と確定した（postprocess/bridge未到達）。stage diagnostics fix `910419a`、trace artifact path fix `257ed74b46cbc402d590640d5e477614e2b13bc6`、sparse node ID fix `b2259c9`は独立レビュー **APPROVED**で、`998fe32`までpush済みである。新規lock `d3a88a3ecbf327799a6f9ab7d2da2e38418f84cfc168878dccb4ccc48ae0eb93`の6-frame runでは、detector `1,314 nodes`、ILP/raw `1,277 nodes / 1,052 edges`、short-track filter後 `1,133 nodes / 944 edges`を得て、2-frame起因の0-node問題を解消した。約14分22秒のCPU実行後、bridgeが既存temporary directoryを`overwrite=True`でserializerへ渡したためinodeが置換され、`phase=bridge`でFAILEDとなった。GT open/metric callはともに0、OOM kill増分0、旧lock/outputは再利用不可である。CPU/CUDA監査では、upstreamのCUDA hard guard、adapterの`CUDA → MPS → CPU`選択、現DockerのCPU-only実測を確認し、`cuda_equivalence_validated=false`を維持する。Recipe Cの本repo公式スコアはまだ得ていない。
- Task5 fix round 3 commit `cedf36e` は独立レビューで **APPROVED**（3/3 addressed、open 0、新規P0/P1/P2 0）となり、code gateを完了・push済みである。root fresh関連 `281 passed`、full `805 passed, 9 skipped, 5 warnings`、Ruff/py_compile/scoped diffはpassした。ただしCLI統合、実GT/metric、固定5件、公式scoreは未実行である。現CLIはfreeze/dry-run/inferのみで、`evaluate_panel` APIは安全性確認済み、後続の`infer-panel`/`evaluate-panel`が必要である。

### 1.1 0.95 Performance Goalの現在地

2026-08-22、固定5サンプルのvendored RoyerLab由来公式Final Score macroを `0.95` 以上にする目標を開始した。GTはprediction GEFFとmanifestを永続化・hash検証した後の公式評価とerror analysisに使用でき、完了済みの評価・error analysisは次の独立したmethod/model family選択に使用できる。ただし、推論、feature生成、cache生成、candidate生成、association input、parameter fitting、current-run branch調整へGTを戻すことは禁止する。Kaggleへの外部submissionはこのcampaignに含めない。

| 項目 | 現在値 / 状態 |
|---|---|
| 固定panel | `0113`、`0b`、`0c`、`0db`、divisionを含む`12df`の5件。除外しない |
| macro target | `0.95` |
| Current BestKnown（既存実測best） | `harmonic_v1` macro `0.7944143977140719` |
| target gap（`0.95 - Current BestKnown`） | `0.1555856022859281` |
| 第一候補 | 公開 `Recipe C` dual-seed、D4 TTA、ILP、gap/safe-division/track repair |
| 一次source | `https://github.com/asapacsin/biohub-cell-tracking`、commit `843a47fdd531bdf7e6377673135519c54b69ae28`、Apache-2.0 |
| 固定config | `recipe_c_motion_off_edge_0_40_det0_96875.yaml`、SHA-256 `0e5758f3ea76ba015fb71c35bc749e136c009237e093d544a89a4b03a8c66ced` |
| Recipe C source側5件参考macro（非公式・未測定） | `0.9560058787896148`（`official-spec-lite` recordsの算術平均。本repoの公式metricでは未再現） |
| 本repoの0.95判定 | **未評価・未達成扱い**。実prediction GEFFとvendored official receiptが揃うまで合格としない |
| Task4 fixed6契約 | `e713a16`（push済み）。fresh review **APPROVED**、root targeted `138 passed`、review combined `192 passed`、agent full `740 passed, 9 skipped`（bool/float追加前）を確認済み |
| Task4 stage diagnostics | trace artifact path fix `257ed74b46cbc402d590640d5e477614e2b13bc6`とsparse node ID fix `b2259c9`は独立レビュー **APPROVED（open/new P0/P1/P2=0）**。指定 `87 passed`、root関連 `230 passed, 3 warnings`、full `813 passed, 9 skipped, 5 warnings`、Ruff/py_compile pass。`998fe32`までpush済み |
| Task4 actual 6-frame | 3本目のfresh lock `d3a88a3ecbf327799a6f9ab7d2da2e38418f84cfc168878dccb4ccc48ae0eb93`でdetector `1314`、candidate `1314/1074`、ILP/raw `1277/1052`、production `1133/944`まで到達。bridgeのtemporary directory inode置換でFAILED、GT open/metric call 0、再利用不可。最終persist/READYは未成立 |
| CPU/CUDA受入 | CPU childは完走したが最終GEFF永続化未成立のため`CPU_PORTABLE`未成立。CUDA A/B・数値同値・性能同値は未検証、`cuda_equivalence_validated=false` |
| Task5 metric boundary | `cedf36e`でfix round 3完了、独立review **APPROVED**、push済み。CLIはfreeze/dry-run/inferのみ、`evaluate_panel` API安全、`infer-panel`/`evaluate-panel`は後続実装 |
| 現在の待機 | 0-node原因とbridge code gateはclosure済み。承認・push済み`2e8ce61`から新lock/new outputをfreezeし、6-frameを再実行する。旧FAILED/lock/outputは再利用しない |

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
| `pilkwang/biohub-tracking-support-pack-50ep-v1` | predictor repo + primary `split_0` checkpoint、v10 / CC0 | predictor `c44e771ba5980b820f93091e03a303c25dfe8f3232e501f54dc9565731c234b9`、primary checkpoint `12f6881ee3620a831697ca098ff8f48e687a24225f4e048b538deec3562fe771` |
| `pilkwang/biohub-temporal-unet3d-seed314159-v1` | secondary seed checkpoint、v2 / CC0。run-localで`seed_314159` pathへstage | `9bac2fa0dadc4a6fc1899e0caf187f4b553e0a7cd90ba1261a68b35ffe9e305f` |
| ignored artifactの取得量 | primary v10 runtime 13/13 + primary checkpoint + secondary v2 checkpoint。full datasetは未取得 | support合計 約16.4 MiB、source cloneは別に約4.2 MiB |

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
| harmonic notebook JSON source receipt | `dd3819cff82851b491d9cbeb6f5f0fc36e8da3c5e9ca90a8b0d5284785a250d`（保持receiptの記載値は63桁で、SHA-256として不正長・未検証。欠落文字を推測しない） |
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
- 本レポート更新時点のcode gate HEAD: `2e8ce61`（二重独立review APPROVED、push済み）
- Task4 stage diagnostics/bridge履歴: `910419a`（scope rereview APPROVED）、`257ed74b46cbc402d590640d5e477614e2b13bc6`（独立review APPROVED）、`b2259c9`（sparse node ID fix、独立review APPROVED）、`2e8ce61`（fd-backed GEFF publication、二重独立review APPROVED）。すべてpush済み
- Task5 metric boundary commits: `dd5ecbe`（元9件closed）、`03ad7a2`（device P1 closed）、`cedf36e`（fix round 3、独立review APPROVED、push済み）
- 固定6-frame smoke contract: `e713a16`（push済み。Task4実装`42f9181`/`45423b0`、predictor SHA修正`421eedf`を含む）
- Task3.5完了時点のremote commit: `e56e561`
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
7. 現行Dockerは`torch 2.13.0+cpu`、CUDA 0台、MPS build/availableなしである。upstreamのCUDA hard guardは維持され、adapterだけが`CUDA → MPS → CPU`を選択する。6-frame CPU runではdetectorからproduction CSVまで正のgraphを生成したが、bridge後の最終GEFF永続化前に停止したため`CPU_PORTABLE`は未成立である。A/BによるCUDA output/numeric/performance同値も未検証で、`cuda_equivalence_validated=false`を記録する。
8. 公開Recipe Cはsource側参考macro `0.9560058787896148`があるが、これは公式実測ではなく未測定の参考値である。本repoのRecipe C公式metric、5 sample macro、0.95達成は未評価・未達成である。HOCT/Trackastra/Ultrack/Linajea/DeepCenterの性能数値もない。
9. Kaggle competitionはnotebook-only submissionである。ローカルprediction生成は外部提出許可を意味せず、GPU runtimeとoffline packagingは未検証である。

### 次の一手

1. GT-free direct diagnosticで、detector local peaksが`t0=217`、`t1=220`、合計`437`、candidate edge`213`であることを確認した。fixed ILP cost（edge `-p`、appearance `0`、disappearance `1.575`）では2-frameのtrack costが正となり、all-zero objectiveが最適になる。sourceの`output_min_track_len=6`とも整合せず、0-nodeの根因は2-frame horizonとILPの数学的非互換であり、postprocess/bridgeには到達していない。
2. 固定6-frame smoke contractは`e713a16`で固定済みである。trace path fix `257ed74`、sparse node ID fix `b2259c9`を経て`998fe32`までpushした。新lock `d3a88a3ecbf327799a6f9ab7d2da2e38418f84cfc168878dccb4ccc48ae0eb93`ではdetector `1314`、ILP/raw `1277/1052`、production `1133/944`をGT-freeで得たため、0-nodeは解消した。bridgeだけがtemporary directoryのinode置換を検出してFAILEDとなった。
3. bridge code gateはfd-backed pure Zarr v2 publication `2e8ce61`で二重独立review **APPROVED**、push済みとなった。次はこのclean commitから新lock/new outputをfreezeして6-frameを再実行する。旧FAILED/lock/outputは再利用しない。その後にCLI統合、固定5件のGT-free persist/hash検証、CUDA A/Bへ進む。
4. 全5件の永続化・hash検証後にだけGTを開き、vendored official metricで評価する。GTは同じrunの推論、feature、cache、candidate、association input、parameter fitting、current-run branch調整へ戻さない。
5. 本repoの実測macro `>=0.95` のreceiptが得られるまで、Recipe C参考値 `0.9560058787896148` を達成値とせず、0.95は未評価・未達成扱いにする。

現時点での採用判断は、**旧race全体Bestはharmonic v1、旧race新規Bestはblob_lap、detector-fixedの5 sampleではharmonic_v1が5/5でofficial ILPを上回った**である。Task4の2-frame 0-node原因とsparse node ID境界はclosure済みで、fresh 6-frameはproduction `1133 nodes / 944 edges`まで到達した。残る実行blockerはGEFF bridgeのtemporary inode設計であり、最終persist/READYとCUDA A/Bは未成立である。Task5 code gateは**APPROVED**だがCLI統合・実GT/metric・固定5件は未実施である。Recipe Cの本repo公式評価は未実施、BestKnown `0.7944143977140719`、gap `0.1555856022859281`、0.95は未評価・未達成扱いである。division対応も未解決である。

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

## 20. Task2完了とTask3 staging/deviceへの移行（2026-08-23更新）

Task2のimmutable protocol/selection lockは完了した。panel、source、config、checkpoint、code commit、仮説、control、採否基準を実験前に固定し、selection lockの同一性と改変不能性を検証できる状態にした。実装commitは `0449c7e`、`3b46eb1`、`e1416e4` である。

最終review roundは `APPROVED`、blocking issueは `0` だった。Task2のtargeted suiteは `143 passed`、full suiteは `536 passed, 9 skipped, 2 warnings`、Task2対象Ruffはpassした。Task1 validatorも、固定したsource/config/checkpoint契約に対して実通過している。

次の作業はTask3のstaging/deviceである。公開Recipe Cのsourceと必要assetは、コード・docsとは分離したignored artifactへ取得済みである。一次sourceはApache-2.0、commit `843a47fdd531bdf7e6377673135519c54b69ae28`、`artifacts/biohub_095/source` に保持し、support側のrepoとは混在させていない。

primary supportはKaggle version `10`を明示指定して取得した`repo/` runtime 13/13で、`artifacts/biohub_095/support/primary/repo` に配置した。predictorのcontrolled importは実推論・GT読み込みなしで通過している。predictorは26,008 bytes、SHA-256は `c44e771ba5980b820f93091e03a303c25dfe8f3232e501f54dc9565731c234b9` であり、63桁の旧記載は`421eedf`で正しい64桁へ修正済みである。primary checkpointはSHA-256 `12f6881ee3620a831697ca098ff8f48e687a24225f4e048b538deec3562fe771` である。secondaryはKaggle version `2`のcheckpointを `artifacts/biohub_095/support/secondary` に保持し、SHA-256は `9bac2fa0dadc4a6fc1899e0caf187f4b553e0a7cd90ba1261a68b35ffe9e305f` である。両Kaggle assetはCC0-1.0である。

取得量はsupport合計約16.4 MiB、source clone約4.2 MiBであり、必要なruntimeとcheckpointだけを取得した。Kaggleのlatest fallbackは使わず、primary v10・secondary v2を固定した。credential/tokenの内容やpathはreportへ出していない。

この更新時点ではreportとignored artifactの確認だけであり、GT境界、既存metric数値、0.95の判定を変更しない。Task3の最終承認と後続の現在地は次節に追記した。本repoの0.95 campaignは引き続き**未評価・未達成**である。

## 21. Task3 staging/device 最終承認（2026-08-23更新）

Task3の履歴は initial `b8b895d`、hardening `49674c4`、race修正 `afb8517`、receipt failure-atomic修正 `2724a66` / `4848075` / `66fd517` である。Task3 final review時点のremote HEADは `66fd517`。P1-1/P1-2/P1-3/P2-1のpath race、P1-4のcached fd lifetime、P1-5のREADY+FAILED、P2-2のtemp fd cleanup、P1-6のreceipt partial write/fsync/cleanup failureは、同一攻撃注入と回帰テストでclosureした。Task3 final reviewは **APPROVED** である。

最終検証は targeted `49 passed`、full `585 passed, 9 skipped, 2 warnings`、変更対象Ruff pass、`git diff --check` passだった。実assetのstaging-only smokeは **READY**。source/support digestは不変で、primary/secondary checkpointは外部元pathへのsymlinkではなく、staged tree内のregular fileへcopyされることを確認した。device候補順は `CUDA → MPS → CPU`（現Dockerの実解決はCPU）である。

このTask3検証ではGTを開かず、inferenceとmetric評価も実行していない。Task3.5の最終承認と後続の現在地は次節に記録する。0.95目標は引き続き **未評価・未達成** であり、source側参考macro `0.9560`を本repoの達成値として扱わない。

## 22. Task3.5 派生asset publish boundary 最終承認（2026-08-23更新）

Task3.5は、`02a0b7f` の fd-backed derived publisher に、同一inode・同一sizeの内容改変をpost-fsync後に再検証する `e56e561` のfixを加えた状態で完了した。Task3.5完了時点のremote HEADは `e56e561`。fresh re-reviewは P1-1 **ADDRESSED**、新規blocking issue `0`、最終判定 **APPROVED** だった。

実際のDocker `biohub-dev` と virtiofs mount上のfresh ignored stageで、派生predictorとPANEL_V1 splitsをpublishし、readback、SHA-256、receiptのdevice/inode/size/fsyncedを確認した。同一roleの再publishはno-clobberで拒否され、canonical predictor/config/checkpointとsource/support treeは不変、stage内symlinkは0、close後のfd/read/publish/cached view APIは拒否された。実assetを用いたstaging-only functional smokeも **APPROVED** である。

root fresh verificationは source/protocol/staging combined `211 passed`、同時進行中のTask4 testsを明示除外したfull相当 `604 passed, 9 skipped, 2 warnings`、Ruff/compile passだった。今回のpayloadはcompileable smoke predictorとexact PANEL_V1 splitsであり、GT、画像/Zarr、実推論、official metric、Task4 full runnerは実行していない。したがって0.95は引き続き **未評価・未達成** である。

## 23. Task4 Recipe C actual smoke 最新状態（2026-08-24更新）

Task4 round-4/round-5のcode gateはともに **APPROVED** で、対象実装を `42f9181`、data-role修正を `45423b0` としてpush済みである。predictor SHAの63桁記載は`421eedf`で正しい64桁 `c44e771ba5980b820f93091e03a303c25dfe8f3232e501f54dc9565731c234b9`へ修正済みであり、固定6-frame smoke contract `e713a16`もpush済みである。fixed6 contractのfresh reviewは **APPROVED**、承認後の日本語レポート更新commitは `3d67669` である。root targeted `138 passed`、review combined `192 passed`、agent full `740 passed, 9 skipped`（fullはbool/float追加前）で、Ruff、compile、`git diff --check`もpassした。round-4/5の旧suite結果（targeted `123 passed` / `68 passed`、combined `335 passed`、full `727`→`728 passed`）は履歴として保持する。

初回production freezeはDocker worktreeの`.git`がhostの絶対worktree pathを指すGit環境衝突でHEAD読取に失敗し、production selection lockは生成されなかった。この失敗をlockありとして扱わず、後続commitごとにwrite-once lockを新規作成した。

以下の初期2-frame lock/FAILED receiptはすべて `artifacts/biohub_095/` 配下にあり、GT内容は開かずJSONメタデータだけを確認した。表内FAILED receiptの `command_sha256` は `3752c31fe6d434db041b0b766d7bd570bd907195d87e3229bc4d7be92879b11a` である。後続6-frame failureは23.3節に別記する。

| code commit | selection lock ID | lock JSON SHA-256 | FAILED receipt（GT-free） |
|---|---|---|---|
| `42f918172a860f535b6f22abd2d9ec267612f076` | `8232fe546a0328440ec5b6b97e6866078c0a6fbb5cc3230bdf7a8e26b4614088` | `99a9072e1ae346ca7b678971d2ef7b97100451ba00243719f62472505c42dcb2` | `smoke-2frame-output/FAILED.json` と `smoke-debug1-output/FAILED.json`、receipt SHA-256 `3e1f847fc54485a424b28246311d56941602b7b212b1def4eb7c53784f975515`、`phase=subprocess` / `CalledProcessError` / `reusable=false` |
| `45423b05fdb345b0e9ca7809948d29fcd054183d` | `3cc6240acb10cf9c526145354bc6733fb7d757329b7763c8ee6034b383d1d6ab` | `42b5b6af4931018406ec9ddb75167c8245e2239f23d29b272438c928ff8b9821` | `smoke-2frame-output/FAILED.json`、receipt SHA-256 `5df918e27dda21ed5dd92b80f5d3435c4d1c4d4cc77f45904085801f909d8de2`、`phase=raw_persist` / `ValueError` / `reusable=false` |

`42f9181`の旧path smokeはchildが画像を開く前のsubprocess境界で失敗し、出力は再利用不可だった（旧path failure境界を含むround-4 evidenceは外部計測 `4.24 s`）。data-role修正後の`45423b0` smokeはchild predictorが `device=cpu` を出力して完走したが、生成結果が0-nodeとなり、外部計測約 `195.59 s` のCPU実行後にraw persistenceでFAILEDとなった。これらのwall timeはFAILED receipt内の永続fieldではない。いずれもGTを開かず、official metricも呼び出しておらず、Recipe Cのprediction score・5-panel macroは生成していない。

### 23.1 GT-free zero-node direct diagnostic（2026-08-23更新）

同一weightsを使ったGT-free・先頭2-frameのdirect diagnosticでは、locked threshold `0.96875` を超えるdetector local peaksが `t0=217`、`t1=220`、合計nodes `437`、candidate edges `213` だった。all probability maxも `t0=0.9999966621`、`t1=0.9999970198` であり、threshold、blank input、bridge消失が原因ではない。source configの`output_min_track_len`は`6`である。

exact Recipe C ILPは edge cost `-p`、appearance `0`、disappearance `1.575` である。2-frameの1-edge trackは `cost >= -1 + 1.575 = 0.575 > 0`、isolated nodeとdivisionも正コストとなるため、all-zero objective `0` が有利になる。raw GEFFが0-nodeとなる根因は、2-frame smoke horizonがILPと数学的に非互換なことであり、postprocess/bridgeには未到達だった。

同じsolver設定を使うGT-free synthetic確認でも、2-node/1-edge chainは `0 nodes / 0 edges`、6-node/5-edge chainは `6 nodes / 5 edges` となり、目的関数の説明と実挙動が一致した。

CPU/CUDA監査では、upstream notebookのCUDA hard guard（CUDA unavailableなら推論前に停止）を確認した。adapterはupstreamの計算・config・threshold・ILPを変更せず、device選択だけを`CUDA → MPS → CPU`へ拡張する。現行`biohub-dev`は`torch 2.13.0+cpu`、`torch.version.cuda=None`、CUDA 0台、MPS build/availableなしである。最新6-frame runはCPU childが約14分22秒で完走し、detectorからproduction CSVまで正のgraphを生成し、OOM kill増分も0だった。ただしbridge後の最終GEFF永続化前に失敗したため`CPU_PORTABLE`は未成立、CUDA output/numeric/performance同値も未検証で、receiptの`cuda_equivalence_validated=false`を維持する。

A/B受入の要件は、同一input/config/checkpoint、CPU/CUDA child token、detector count・candidate pair set・ILP topology・final GEFF semantic outputの一致、logit/edge scoreの許容差、repeat determinismを確認することである。CUDA実行とこのA/Bは未実施であり、数値・性能同値を主張しない。sourceの`output_min_track_len=6`を根拠とする固定6-frame smoke contractは`e713a16`で固定済みである。stage diagnostics fix `910419a`、trace path fix `257ed74`、sparse node ID fix `b2259c9`は独立review **APPROVED**で、`998fe32`までpush済みである。GT/official metricは5件すべてのprediction GEFF/manifest永続化・hash検証後だけに行う。

| 受入レベル / 項目 | 現時点の判定 |
|---|---|
| `CPU_PORTABLE` | 未成立（fresh 6-frameはCPUでproduction `1133/944`まで正常だが、bridge temporary identity failureのため最終persist/reload未成立） |
| `CUDA_OUTPUT_EQUIVALENT` | 未検証（CUDA child/A-B未実施） |
| `CUDA_NUMERIC_EQUIVALENT` | 未検証（logit/score全列・repeat未実施） |
| `cuda_equivalence_validated` | `false` |

### 23.2 Stage diagnostics実装と独立レビュー（2026-08-24更新）

`79b6da4`でstage別GT-free diagnosticsを実装し、Sol fresh検証はtargeted `87 passed`、Recipe C integration `378 passed`だった。fix commit `910419a`後のSol fresh検証はtargeted `101 passed`、Recipe C integration `369 passed`、scope rereviewは **APPROVED（9/9 addressed、open/new 0）**だった。さらに`257ed74b46cbc402d590640d5e477614e2b13bc6`でtrace artifact publication pathを修正し、独立review **APPROVED（P0/P1/P2=0）**となった。sparse node ID fix `b2259c9`は独立review `task-4-sparse-node-id-rereview.md`で **APPROVED（open/new P0/P1/P2=0）**、指定 `87 passed`、root関連 `230 passed, 3 warnings`、full `813 passed, 9 skipped, 5 warnings`、Ruff/py_compile passとなり、`998fe32`までpush済みである。fresh 6-frameで全stage diagnosticsを取得できたが、bridge後のpersist/reload snapshotはtemporary identity failureのため未取得である。CUDA A/Bも未実施である。

- exact traceの順序・件数。
- shadow traceの順序。
- source round semantics。
- detector / candidate / ILPのframe別記録。
- zero GT count。
- missing trace fallback。
- real reload。
- source before/after identity。

### 23.3 固定6-frame実行と残存failure（2026-08-24更新）

b1bf2c9の最初の6-frame lock `30f550ccf414ab5f1ab36b7e0fbf3ea7494f6c7573b80424e832f9f119734159`は、`phase=patch`でtrace parentが欠落し、GT open `0`・metric call `0`・`reusable=false`となった。`257ed74b46cbc402d590640d5e477614e2b13bc6`で`_TRACE_DERIVED`を既存scriptsへ移し、独立review **APPROVED（P0/P1/P2=0）**、full `806 passed, 9 skipped, 5 warnings`でpush済みである。

同commitの新lock `ffa14615818bc22b9a0c54ad4feb6c5a7fd35b95a3902737df383733b0c8749b`では、CPU childが外部監視で約12分で完走し、OOM kill増加なし、raw prediction `1277 nodes / 1052 edges`を生成した。wall timeとOOM差分はFAILED receipt内の永続fieldではない。frame別node数は `[208, 214, 221, 217, 212, 205]`、node IDは`0..1313`のsparse範囲で、dangling node/edgeは`0`だった。pinned sourceの`validate_graph`/`graph_rows`はPASS、`2329 rows`でnode IDを保持した。

このrunのFAILED receiptは `artifacts/biohub_095/runs/257ed74b46cbc402d590640d5e477614e2b13bc6/smoke-6frame-output/FAILED.json`、SHA-256 `b525aa09a77d305429149bffcfc9e7d662a55eb6d53067887a5924ca78d1c094`、`phase=raw_persist`、`error_type=ValueError`、`reusable=false`である。

ただしadapterは、node IDをcontiguousにする過剰制約により`raw_persist`でFAILEDとなった。GT open `0`、metric call `0`、`reusable=false`であり、official scoreは生成していない。sparse node ID fix `b2259c9`でこの境界のcode gateは完了し、独立reviewは **APPROVED（open/new P0/P1/P2=0）**、failed raw artifactのfixed `validate_prediction_geff`によるGT-free再検証は `1277 nodes / 1052 edges / forks 0 PASS`だった。修正は`998fe32`までpush済みである。

`998fe32983e8e94cbbeb8f1ab282e1deb7c91fce`から新規lock `d3a88a3ecbf327799a6f9ab7d2da2e38418f84cfc168878dccb4ccc48ae0eb93`（lock JSON SHA-256 `92fb8860c6234e9e29e491d10fd473e8f71f499e0e61098054ea8042af838c76`）をfreezeし、新規stage/outputで3本目の6-frame smokeを実行した。外部監視では約14分22秒、resolved/child deviceはCPU、OOM killは開始前後とも7で増分0だった。wall timeとOOM差分はFAILED receipt内の永続fieldではなく監視記録由来である。FAILED receiptは `artifacts/biohub_095/runs/998fe32983e8e94cbbeb8f1ab282e1deb7c91fce/smoke-6frame-output/FAILED.json`、SHA-256 `cb6f1b8f67754b04d4f60a06395451e1376a4b3e621206a06e52fbb76a1e1823`、`phase=bridge`、`error_type=OSError`、`reusable=false`である。

| stage | nodes | edges | frame別nodes (`t=0..5`) |
|---|---:|---:|---|
| combined detector | `1314` | `0` | `217/220/224/220/217/216` |
| candidate graph / ILP pre | `1314` | `1074` | `217/220/224/220/217/216` |
| ILP post / raw GEFF | `1277` | `1052` | `208/214/221/217/212/205` |
| production CSV / source postprocess final | `1133` | `944` | `189/189/189/189/189/188` |

short-track filterは36 components、144 nodes、108 edgesを除去し、conservative rescueは1 component・5 nodesを救済した。したがって6-frameではminimum-track filter後も十分なgraphが残り、0-node failureではない。bridgeはtemporary GEFF directoryを先に作成してidentityを保存した後、`tracksdata/geff`へ`overwrite=True`で渡していた。libraryは既存rootを削除・再生成するため、bind mount上のGT-free最小再現で `(st_dev, st_ino)=(45,200227)→(45,200236)` とinodeだけが変化し、所有権guardが正しくFAILEDにした。修正方針は、owned private build parent内の不存在childへ`overwrite=False`でserializeし、生成後identityを取得してno-replace publishすることである。partial failure cleanupとcompetitor保持を弱めずTDDでclosureする。今回も`ground_truth_open_count=0`、`ground_truth_opened=false`、`metric_call_count=0`、`metric_status=not_run_gt_guard`である。

初回bridge修正 `dccc949` はprivate build parentと不存在childを導入し、root検証でbind mount `1 passed`、Recipe C関連 `380 passed`、full `816 passed, 9 skipped, 5 warnings`、Ruff/compileを通した。しかし独立reviewは **NOT APPROVED** だった。serializer例外時にchild identity未取得のまま競合childを後からrestatして削除できるP1と、directory確認後にsymlinkへ置換するとsymlink finalを公開できるP1、non-dir partial残骸のP2が残ったためである。`dccc949`単独は使用不可であり、承認済みfix `2e8ce61`の祖先としてのみpushされた。新smokeのfreeze対象は`2e8ce61`以後に限定する。

fix round `2e8ce61` はtemporary GEFFをexclusive作成後に`O_NOFOLLOW|O_DIRECTORY` fdでanchorし、fd-backed `LocalStore`へexplicit pure Zarr v2・`overwrite=False`でserializeする。publicationはdirectory-fd相対`renameat2(RENAME_NOREPLACE)`、semantic readbackも保持fdから行い、serializer、callback、fsync、roundtrip各境界でfinalとoutput rootのpublic path/fd identityを再検証する。Zarr v3 LocalStoreはv2/v3 metadata混在警告を再現したため採用しない。root検証は実bind mount bridge `71 passed`、Recipe C関連 `389 passed`、full `825 passed, 9 skipped, 5 warnings`、Ruff/compile passである。

前回reviewerの再レビューは **APPROVED（P0/P1=0）**で、対象 `176 passed`、実`1133/944` sparse-ID roundtrip、成功/失敗各20回のFD leakなしを確認した。非blocking P2として、GEFF tree全体の再帰fsyncによるOS crash耐性が未証明であることと、所有権記録だけを行うcallback直前のpublic path再確認を追加できることを記録する。fresh second reviewerは **APPROVED（open/new P0/P1/P2=0）**で、bridge `71 passed`、Recipe C関連 `245 passed`、full `825 passed, 9 skipped, 5 warnings`、実raw `1277/1052` roundtripを確認した。二重承認後に`2e8ce61`をpush済みである。新smokeはまだ実行していない。

Current BestKnownは既存実測の `0.7944143977140719`、target gapは `0.1555856022859281` のままである。Recipe C source側の `0.9560058787896148` は非公式・未測定の参考値であり、本repoの0.95到達値ではない。従って本repoの0.95目標は **未評価・未達成** である。

## 24. Task5 metric boundary 最新状態（2026-08-24更新）

Task5 fix round 3 commitは `cedf36e` である。独立レビュー報告 `.superpowers/sdd/2026-08-22-biohub-095-performance/task-5-fix-round3-rereview.md` は **3/3 ADDRESSED、open 0、新規P0/P1/P2 0、APPROVED** と判定し、Task5 code gateは完了した。root fresh関連は `281 passed`、fullは `805 passed, 9 skipped, 5 warnings`、Ruff・py_compile・scoped diffはpassし、commitはpush済みである。`dd5ecbe`（元9件closed）と`03ad7a2`（device P1 closed）を経た最終fixである。

CLI監査では、現行CLIの実行入口はfreeze/dry-run/inferのみで、`evaluate_panel` APIの安全性は確認済みである。後続で`infer-panel`/`evaluate-panel`をCLIへ追加する必要がある。

ただし、実GT/metric、固定5件は未実行であり、Recipe C公式値は未測定、0.95は未達成扱いを維持する。fresh 6-frameは0-nodeを解消してproduction `1133/944`まで到達し、GEFF bridge code gateも`2e8ce61`でclosureした。次は同commitから新lock/outputで再実行する。旧FAILED/lock/outputは不変のまま保持し、再利用しない。
