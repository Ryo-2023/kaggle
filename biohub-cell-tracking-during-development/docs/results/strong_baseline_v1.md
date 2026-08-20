# Strong Baseline v1 — receiptに基づく結果

## 結論

実サンプル `44b6_0113de3b` では、provenanceを固定した公式の
TemporalUNet3D + SimpleNodeTransformer + ILP経路が最終スコア
`0.8837944835207503` に到達した。同じofficial evaluatorで、公開済みの
制御された双方向harmonic association variantは `0.9211200215044129` に到達し、
実測差分は正確に `+0.03732553798366256` だった。これは疎なアノテーション上の
結果であり、密な細胞真値やleaderboard性能を主張するものではない。

Harmonic v1 の測定値は w=0.20。

推論中にGTは使用していない。画像とcheckpointだけをmodel inputとした。各推論
entrypointは、構造的なGEFF reloadの後に `prediction_manifest.json` を自動で書き出す。
別個の `evaluate` commandは、GTを開く前に永続化manifestを検証する（path、hash、
file count/bytes、node/edge counts）とともに、検証receiptを `metrics.json` に記録する。

## 手法

固定した公式pipelineは次のとおり。

```text
OME-Zarr (T,Z,Y,X)
  -> quantile normalization
  -> TemporalUNet3D detector
  -> physical-scale local maxima
  -> U-Net node features
  -> SimpleNodeTransformer association
  -> tracksdata ILP graph optimization
  -> prediction GEFF
  -> vendored official metric
```

harmonic v1 variantでは、detection、image、checkpoint、candidate construction、
ILP costsを変更していない。保持されたrun receiptは `w=0.20` の双方向reverse-logit
associationを記録している。操作の正確な内容を独立に確立できるsource cellを含む
取得済みnotebook JSONはこのcheckoutに存在しないため、harmonic v1 のソースセル独立監査は BLOCKED であり、source textを捏造していない。したがって、ここでのharmonic v1は
receiptに基づく補足結果であり、ソースセル監査の完了を意味しない。

ILP後のnode数はassociationの影響で異なり得るため、永続化されたnode数が異なる
ことだけではdetector driftとはいえない。harmonic receiptはraw candidatesを別に
記録している（`26,887`）が、official receiptにはraw candidateのdigestがないため、
raw-candidate equalityは主張しない。

## ソース、バージョン、チェックポイントの来歴

| 構成要素 | 固定したprovenance |
|---|---|
| 公式ソース | [royerlab/kaggle-cell-tracking-competition](https://github.com/royerlab/kaggle-cell-tracking-competition), commit `075fc5f5a52d11077f9dc2b074644618f26939e2`, BSD-3-Clause |
| 公開checkpoint artifact | Kaggle dataset `thibautgoldsborough/cellmot-baseline-artifacts`, version `1`, License: Unknown |
| 主催者baseline notebook | Kaggle notebook version `331429261` |
| 選択checkpoint | `artifacts/strong_baseline_v1/inputs/cellmot-baseline-artifacts/weights/unet_transformer/split_0/edge_predictor_best.pth` |
| checkpoint SHA-256 | `347915de9c33883cb2ee69832a8e4552c88b1ec692d0fbfe956422467d3d4235` |
| 選択config SHA-256 | `e9b4e396c58081bca08adf8275bd0bd1c2d3fd6eb091a1912a5116cb6de7b50a` |
| harmonic source | Yusuke Togashi, notebook v18, `scriptVersionId=338569479`, declared Apache-2.0 |
| harmonic source receipt | `artifacts/strong_baseline_v1/harmonic_ilp/source_receipt.json`, notebook JSON SHA-256 `dd3819cff82851b491d9cbeb6f5f0fc36e8da3c5e9ca90a8b0d5284785a250d` |

downloadしたcheckpointとlocal candidateはbyte-identicalだった。harmonic receiptは
public source URL、script version、declared license、記録済みnotebook digestを保持している。
source-cell fixtureは、保持されたnotebook JSONを復旧するまでblockedのままである。

## 入力サンプルと疎な正解

- 画像: `/workspace/biohub-cell-tracking-during-development/data/train/44b6_0113de3b.zarr`
- サンプルstem: `44b6_0113de3b`
- shapeと軸: `(100, 64, 256, 256)`、軸順は `(T, Z, Y, X)`
- 物理scale: `(1.625, 0.40625, 0.40625)` micrometres per `(Z, Y, X)` voxel
- upstream pathで使用した画像quantile: `0.001=26.222222222222225`, `0.999=2145.000000039654`
- 評価専用GT: `/workspace/biohub-cell-tracking-during-development/data/train/44b6_0113de3b.geff`
- GTにはannotated node 52個とedge 50本があり、metadataはtotal nodeを25,755個と推定する。

GTは疎である。annotationにマッチしないpredictionは自動的にfalse positiveではない。
特に、疎なGTに対する未マッチ検出をfalse positiveとして扱ってはならない。

## 固定した推論・評価設定

| 設定 | 値 |
|---|---:|
| method | `strong_baseline_v1_official_ilp` |
| split | `0` |
| detector threshold | `0.99` |
| U-Net batch size | `1` |
| use ILP | `true` |
| ILP edge weight | `-1.0` |
| ILP appearance weight | `0.1` |
| ILP disappearance weight | `0.1` |
| ILP division weight | `1.0` |
| harmonic reverse weight | `0.20` (harmonic v1 measured value) |
| upstream run-reported window | `2` |
| upstream run-reported pool kernel | `3.0 um` |
| evaluator scale | `(1.625, 0.40625, 0.40625)` |
| evaluator max distance | `7.0 um` |

downloadしたconfigには `pool_kernel_um=5.0` が含まれる一方、固定したupstream runは
`pool_kernel_um=3.0` を報告する。これはlocalでtuneもoverrideもしておらず、upstreamで
解消すべき再現性上の制約として残る。

## 実行コマンド

コマンドは既存の正常な `biohub-dev` containerで実行した。公式のimage-only inference
command全体は次のとおり。

```bash
docker compose exec -T biohub sh -lc 'cd /workspace/biohub-cell-tracking-during-development/scratch/strong-baseline-v1/biohub-cell-tracking-during-development && uv run python scripts/run_strong_baseline_v1.py infer-official --upstream-root artifacts/strong_baseline_v1/upstream --image-stem /workspace/biohub-cell-tracking-during-development/data/train/44b6_0113de3b --checkpoint artifacts/strong_baseline_v1/inputs/cellmot-baseline-artifacts/weights/unet_transformer/split_0/edge_predictor_best.pth --output-dir artifacts/strong_baseline_v1/official_ilp --expected-device cpu'
```

harmonicのimage-only inference command全体は次のとおり。

```bash
docker compose exec -T biohub sh -lc 'cd /workspace/biohub-cell-tracking-during-development/scratch/strong-baseline-v1/biohub-cell-tracking-during-development && uv run python scripts/run_strong_baseline_v1.py infer-harmonic --upstream-root artifacts/strong_baseline_v1/upstream --image-stem /workspace/biohub-cell-tracking-during-development/data/train/44b6_0113de3b --checkpoint artifacts/strong_baseline_v1/inputs/cellmot-baseline-artifacts/weights/unet_transformer/split_0/edge_predictor_best.pth --output-dir artifacts/strong_baseline_v1/harmonic_ilp --expected-device cpu'
```

各inference commandはpredictionの隣にmanifestを書き出す。永続化されたpredictionごとの
公式evaluation commandは、評価専用GTを開く前にそのmanifestを検証する。

```bash
docker compose exec -T biohub sh -lc 'cd /workspace/biohub-cell-tracking-during-development/scratch/strong-baseline-v1/biohub-cell-tracking-during-development && uv run python scripts/run_strong_baseline_v1.py evaluate --prediction artifacts/strong_baseline_v1/official_ilp/44b6_0113de3b.geff --ground-truth /workspace/biohub-cell-tracking-during-development/data/train/44b6_0113de3b.geff --metrics artifacts/strong_baseline_v1/official_ilp/metrics.json'
docker compose exec -T biohub sh -lc 'cd /workspace/biohub-cell-tracking-during-development/scratch/strong-baseline-v1/biohub-cell-tracking-during-development && uv run python scripts/run_strong_baseline_v1.py evaluate --prediction artifacts/strong_baseline_v1/harmonic_ilp/44b6_0113de3b.geff --ground-truth /workspace/biohub-cell-tracking-during-development/data/train/44b6_0113de3b.geff --metrics artifacts/strong_baseline_v1/harmonic_ilp/metrics.json'
```

各経路で2-frame smokeを別々に実行した。smoke artifactは比較不能であり、結果表には
含めていない。

## 実行時間とリソース

| 実行 | 状態 | 期待デバイス | 実際デバイス | run policy下の `torch.cuda.is_available()` | 経過秒数 | 永続化prediction |
|---|---|---|---|---:|---:|---|
| official baseline | success (`return_code=0`) | `cpu` | `cpu` | `false` | `3967.1878084339987` | `artifacts/strong_baseline_v1/official_ilp/44b6_0113de3b.geff` |
| harmonic v1 | success (`return_code=0`) | `cpu` | `cpu` | `false` | `4459.703853908999` | `artifacts/strong_baseline_v1/harmonic_ilp/44b6_0113de3b.geff` |

実行はUbuntu 24.04 / Python 3.11の `biohub-dev` 環境で行い、CPUのみの実行として
container CPU 8個を使用した。非侵襲的なprocess samplingで、公式predictorのRSS最大値は
`1,407,624 KiB`（約 `1.34 GiB`）だった。これはsamplingによる最大値であり、kernelが
提供するpeak値ではない。またharmonic RSSは別途取得していない。Gurobiは利用できなかった
ため、upstream solverはSCIPフォールバックをログに記録した。ILPは無効化していない。

## 予測成果物と指標

既存のexperiment manifestは構造的reloadの後、evaluationの前に永続化した。evaluationは
各manifestをGT accessの前に検証したことを記録する。現在のinference wrapperは、将来の
再現可能なrunのためにこのmanifestを自動生成する。
公式manifest: 33 files、332,170 bytes、
directory SHA-256
`294e1cbad1c2a8464a5af93c63ed3bcb49fc9559cf7ad9dba891b626cf1d3840`.
harmonic manifest: 33 files、339,949 bytes、directory SHA-256
`6163e0643d27062854a32fcf102fde24aa2ead55384a0afb965af1f589307fbc`.

下表は両方の `metrics.json` receiptから値を転記したもの。`N/A` は2つのnull値に対する
deltaであり、`null` は意図したJSON null処理である。

| 指標 | 公式ベースライン | harmonic v1 | 正確な差分（harmonic − official） |
|---|---:|---:|---:|
| `prediction_node_count` | 25994 | 26301 | +307 |
| `prediction_edge_count` | 23536 | 24205 | +669 |
| `edge_tp` | 46 | 48 | +2 |
| `edge_fp` | 2 | 2 | +0 |
| `edge_fn` | 4 | 2 | -2 |
| `division_tp` | 0 | 0 | +0 |
| `division_fp` | 0 | 0 | +0 |
| `division_fn` | 0 | 0 | +0 |
| `edge_jaccard` | 0.8846153846153846 | 0.9230769230769231 | +0.03846153846153855 |
| `adjusted_edge_jaccard` | 0.8837944835207503 | 0.9211200215044129 | +0.03732553798366256 |
| `division_jaccard` | null | null | N/A |
| `final_score` | 0.8837944835207503 | 0.9211200215044129 | +0.03732553798366256 |
| `node_recall` | 1.0 | 1.0 | +0.0 |
| `total_node_ratio` | 0.009279751504562221 | 0.021199767035527083 | +0.011920015530964861 |

division Jaccardは、official summarizerが存在しないdivision項を落とすためnullである
（このsampleにはdivisionがない）。したがって両行のfinal scoreはadjusted edge Jaccardと
一致し、合成したゼロのdivision項は代入していない。

raw detection candidateはharmonic v1についてのみ利用できる。

| パス | raw candidate node数 | ILP後node数 | ILP後edge数 |
|---|---:|---:|---:|
| `artifacts/strong_baseline_v1/official_ilp/44b6_0113de3b.geff` | official receiptに未記録 | 25994 | 23536 |
| `artifacts/strong_baseline_v1/harmonic_ilp/44b6_0113de3b.geff` | 26887 | 26301 | 24205 |

## 可視化の健全性確認

`src/biohub/visualizer/` の既存viewer codeを調査した。そのload/state/overlay pathと
同等のheadless処理で、raw OME-Zarr、最終harmonic GEFF、GTをscale
`(1.625, 0.40625, 0.40625)`、max distance `7.0 um` とともに読み込んだ。既存の
GEFF loader、official matching、`ViewerState`、`select_overlay`を使用した。raw image
slice endpoint pathはPNG bytesを生成し（例: `(t=0,z=0)` は39,490 bytes）、読み込んだ
image shapeは `(100,64,256,256)` だった。overlay classificationにはTP edge 48本、
FP edge 2本、FN edge 2本、unscored prediction edge 24,155本が含まれた。

具体的なheadless観測:

- マッチしたtrajectory window: `t=0`、`z=62`、`z_radius=0.75` では、prediction
  node `219` の `(t,z,y,x)=(0,62,224,248)` が、`(1,62,228,248)` のnode `441`へ
  TP edgeで接続した。選択したoverlayにはprediction node 3個、TP edge 1本、
  unscored prediction edge 2本が含まれた。
- エラーwindow: `t=47`、`z=31` では、prediction node `11624` の
  `(47,31,108,120)` からnode `11886` の `(48,28,108,116)` へのFP edgeが見えた。
  同じslice windowにはFP 1本、FN 1本、unscored prediction edge 3本が含まれた。
- 疎・未マッチwindow: `t=0`、`z=1` では、prediction node `0` の `(0,1,8,52)` が
  unscored prediction edgeとしてnode `225` の `(1,1,16,52)` へ接続した。このwindow
  にはunscored prediction edgeが12本含まれた。これはcontextであり、これらの検出が
  false positiveである証拠ではない。

rendered GUI/browser inspectionは主張していない。high-level viewerの `build_state`は
現在attribute key `matched_node_id`を要求する一方、永続化GEFF/evaluator
は `match_node_id` を公開するため、その呼び出しは `KeyError` で停止する。
viewerのmatched_node_id/match_node_id属性不一致がある状態で、GUI feature workは追加せず、上記の
lower-level既存loader/state/overlay pathを使用した。このviewer attribute mismatchの
修正は保留している。

このcheckはchecked-in scriptで再現できる。

```bash
docker exec -w /workspace/biohub-cell-tracking-during-development/scratch/strong-baseline-v1/biohub-cell-tracking-during-development biohub-dev uv run python scripts/check_strong_baseline_visual.py --image /workspace/biohub-cell-tracking-during-development/data/train/44b6_0113de3b.zarr --prediction artifacts/strong_baseline_v1/harmonic_ilp/44b6_0113de3b.geff --ground-truth /workspace/biohub-cell-tracking-during-development/data/train/44b6_0113de3b.geff --scale 1.625 0.40625 0.40625 --max-distance 7.0 --output-dir artifacts/strong_baseline_v1/visual_sanity
```

commandは
`artifacts/strong_baseline_v1/visual_sanity/visual_sanity.json` と
`artifacts/strong_baseline_v1/visual_sanity/visual_sanity.txt` を出力する。永続化された
receiptはimage shape `(100,64,256,256)`、overlay totals
`{"fn":2,"fp":2,"prediction":24155,"tp":48}`, raw `/api/frame` slice bytes
`39490`、SHA-256は
`69b6c5d2c322f092c8538f94c3aa2fffc672a1425857c9677597dcbb1a5b84e4`。同じmatched
`219 -> 441`、FP `11624 -> 11886`、および上記のsparse-unmatched
`0 -> 225` windowを記録する。checkerはscale、distance、sample、node/edge totals、
category counts、window IDs/coordinates、raw slice evidenceの不一致で失敗する。

## 既知の失敗と制約

- annotated GTはnode 52個で、推定total node 25,755個に対して疎である。そのため
  scoreはsparse-annotation scoreであり、未マッチpredictionはdense false positiveではない。
- official run receiptにはraw-candidate countやdigestがない。harmonicにはraw count
  （`26,887`）があるが、run間のraw digest equalityは主張していない。
- associationはglobal ILP retention decisionを変えるため、image/checkpoint/detector
  settingsを固定していても、harmonicのILP後node数（`26,301`）はofficial（`25,994`）
  と異なる。
- official receiptには、再構成可能なupstream `PredictConfig` defaultの一部（detector
  TTA、edge activation/threshold、parent/child limitsなど）がない。このreportには固定した
  wrapper settingsと、利用できるupstream stdout valuesを記録している。
- 最初のofficial smoke receiptはwrapper receipt correctionより前のもので、記録commandに
  `--max-frames 2` が含まれない。ただし `max_frames` configと2-frame GEFF time rangeが
  bounded runを証明する。Smoke metricsはここでは使用していない。
- upstreamはGurobi-to-SCIP fallbackを報告し、resource measurementはこのMac-backed
  containerでCPU-onlyである。GPU resultは主張しない。
- harmonic helperは公開互換のinternal rangeを `0.35` まで受け付けるが、実測CLI pathは
  `0.20` に固定している。
- viewerの `matched_node_id`/`match_node_id` attribute mismatchによりhigh-levelの
  rendered viewer sessionは実行できず、利用できるのは記載したheadless overlay evidence
  だけである。

## 次の実験

- viewerのmatch attribute互換性を解消し、GUI-capable environmentが利用できる場合は、
  同じfixed-window inspectionをrendered browser viewで繰り返す。
- run receiptにraw candidate counts/digestsと、すべてのupstream `PredictConfig` defaultを
  永続化する。これにより、ILP後のassociation変更とdetector provenanceをより厳密に分離する。
- receiptとviewerのgapを閉じた後、明示的にheld-outした別の疎なsampleでfixed harmonic
  associationを比較する。この単一annotated volumeからdense truth performanceを推測しない。

## 再現性と成果物一覧

この文書のすべての数値は、永続receiptを根拠とする。

- `artifacts/strong_baseline_v1/inputs/source_receipt.json`
- `artifacts/strong_baseline_v1/official_ilp/run.json`
- `artifacts/strong_baseline_v1/official_ilp/prediction_manifest.json`
- `artifacts/strong_baseline_v1/official_ilp/metrics.json`
- `artifacts/strong_baseline_v1/official_ilp/inference.log`
- `artifacts/strong_baseline_v1/official_ilp/44b6_0113de3b.geff`
- `artifacts/strong_baseline_v1/harmonic_ilp/source_receipt.json`
- `artifacts/strong_baseline_v1/harmonic_ilp/run.json`
- `artifacts/strong_baseline_v1/harmonic_ilp/prediction_manifest.json`
- `artifacts/strong_baseline_v1/harmonic_ilp/metrics.json`
- `artifacts/strong_baseline_v1/harmonic_ilp/inference.log`
- `artifacts/strong_baseline_v1/harmonic_ilp/44b6_0113de3b.geff`
- `artifacts/strong_baseline_v1/visual_sanity/visual_sanity.json`
- `artifacts/strong_baseline_v1/visual_sanity/visual_sanity.txt`

experiment manifestはdeterministic directory hashとstructural reload successを記録する。
current wrapperが生成するmanifestはcreation timeとactionも記録する。追跡対象のcompact
evidence fixture（`tests/fixtures/strong_baseline_v1/`）により、ignoreされたartifactが
なくてもreportとvisual checkをdeterministicに保てる。この歴史的実験のreview-fix passでは、
commit、push、Kaggle submissionは実施していない。source-checkout mutationも行わず、
new 100-frame inferenceも実施していない。
