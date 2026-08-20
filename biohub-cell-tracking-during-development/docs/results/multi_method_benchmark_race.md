# Biohub Multi-Method Benchmark Race

この文書は保存済みの `run.json` と `metrics.json` だけから生成した比較記録です。
推論中にGTは使用せず、未実行の手法は数値を補わず `BLOCKED` と記録します。

## 比較結果

| 指標 | 公式ベースライン | harmonic v1 | blob_lap | cc_flow | motion_lap |
|---|---:|---:|---:|---:|---:|
| `prediction_node_count` | 25994 | 26301 | 28266 | 12095 | 28266 |
| `prediction_edge_count` | 23536 | 24205 | 25562 | 352 | 25562 |
| `edge_tp` | 46 | 48 | 48 | 2 | 48 |
| `edge_fp` | 2 | 2 | 2 | 0 | 3 |
| `edge_fn` | 4 | 2 | 2 | 48 | 2 |
| `division_tp` | 0 | 0 | 0 | 0 | 0 |
| `division_fp` | 0 | 0 | 0 | 0 | 0 |
| `division_fn` | 0 | 0 | 0 | 0 | 0 |
| `edge_jaccard` | 0.884615384615385 | 0.923076923076923 | 0.923076923076923 | 0.04 | 0.905660377358491 |
| `adjusted_edge_jaccard` | 0.88379448352075 | 0.921120021504413 | 0.914077326284665 | 0.0421215298000388 | 0.896830584279294 |
| `division_jaccard` | null | null | null | null | null |
| `final_score` | 0.88379448352075 | 0.921120021504413 | 0.914077326284665 | 0.0421215298000388 | 0.896830584279294 |
| `node_recall` | 1 | 1 | 1 | 0.134615384615385 | 1 |
| `total_node_ratio` | 0.00927975150456222 | 0.0211997670355271 | 0.0974956319161328 | -0.530382450009707 | 0.0974956319161328 |

### Final Score差分 (公式ベースライン比)

| 手法 | 状態 | Final Score | 公式との差分 | 実行時間[s] | expected / actual device |
|---|---|---:|---:|---:|---|
| 公式ベースライン (`official_ilp`) | OK | 0.88379448352075 | +0 | 3967.187808434 | cpu / cpu |
| harmonic v1 (`harmonic_ilp`) | OK | 0.921120021504413 | +0.0373255379836626 | 4459.703853909 | cpu / cpu |
| blob_lap (`blob_lap`) | OK | 0.914077326284665 | +0.0302828427639145 | 33.0921396409976 | cpu / cpu |
| cc_flow (`cc_flow`) | OK | 0.0421215298000388 | -0.841672953720711 | 39.2323415179999 | cpu / cpu |
| motion_lap (`motion_lap`) | OK | 0.896830584279294 | +0.0130361007585434 | 4.7600200859888 | cpu / cpu |

## 手法ごとの状態と成果物

### `official_ilp`

- 状態: `OK`
- source_commit: `075fc5f5a52d11077f9dc2b074644618f26939e2`
- checkpoint_sha256: `347915de9c33883cb2ee69832a8e4552c88b1ec692d0fbfe956422467d3d4235`
- prediction: `artifacts/strong_baseline_v1/official_ilp/44b6_0113de3b.geff`
- manifest: `artifacts/strong_baseline_v1/official_ilp/prediction_manifest.json`
- run: `artifacts/strong_baseline_v1/official_ilp/run.json`
- metrics: `artifacts/strong_baseline_v1/official_ilp/metrics.json`
### `harmonic_ilp`

- 状態: `OK`
- source_commit: `075fc5f5a52d11077f9dc2b074644618f26939e2`
- checkpoint_sha256: `347915de9c33883cb2ee69832a8e4552c88b1ec692d0fbfe956422467d3d4235`
- prediction: `artifacts/strong_baseline_v1/harmonic_ilp/44b6_0113de3b.geff`
- manifest: `artifacts/strong_baseline_v1/harmonic_ilp/prediction_manifest.json`
- run: `artifacts/strong_baseline_v1/harmonic_ilp/run.json`
- metrics: `artifacts/strong_baseline_v1/harmonic_ilp/metrics.json`
### `blob_lap`

- 状態: `OK`
- method_family: `classical_detector_and_lap_linker`
- detector_id: `3d_gaussian_local_peak`
- linker_id: `physical_distance_hungarian_lap`
- version: `blob_lap.v1`
- source_commit: `ac2ece5`
- prediction: `artifacts/multi_method_race/methods/blob_lap/44b6_0113de3b.geff`
- manifest: `artifacts/multi_method_race/methods/blob_lap/prediction_manifest.json`
- run: `artifacts/multi_method_race/methods/blob_lap/run.json`
- metrics: `artifacts/multi_method_race/evaluation/blob_lap/metrics.json`
### `cc_flow`

- 状態: `OK`
- method_family: `classical_connected_component_and_global_flow`
- detector_id: `quantile_foreground_3d_connected_components`
- linker_id: `global_min_cost_flow`
- version: `cc_flow.v1`
- source_commit: `ac2ece5`
- checkpoint_sha256: `None`
- prediction: `artifacts/multi_method_race/methods/cc_flow/44b6_0113de3b.geff`
- manifest: `artifacts/multi_method_race/methods/cc_flow/prediction_manifest.json`
- run: `artifacts/multi_method_race/methods/cc_flow/run.json`
- metrics: `artifacts/multi_method_race/evaluation/cc_flow/metrics.json`
### `motion_lap`

- 状態: `OK`
- method_family: `classical_motion_association`
- detector_id: `blob_lap_fixed_image_only_candidates`
- linker_id: `velocity_acceleration_hungarian_lap`
- version: `motion_lap.v1`
- source_commit: `ac2ece5`
- checkpoint_sha256: `None`
- prediction: `artifacts/multi_method_race/methods/motion_lap/44b6_0113de3b.geff`
- manifest: `artifacts/multi_method_race/methods/motion_lap/prediction_manifest.json`
- run: `artifacts/multi_method_race/methods/motion_lap/run.json`
- metrics: `artifacts/multi_method_race/evaluation/motion_lap/metrics.json`

## 既知の制約

- これは単一のKaggle train sampleに対する同条件比較であり、leaderboard性能を意味しません。
- 疎なGTでは未注釈cellをfalse positiveと解釈しません。
- `division_jaccard=null` は公式summarizerのdivision項が存在しない場合の値です。
- official detectorを共有するmotion laneは今回のraceでは未実施です。
  `motion_lap`はblob候補上の古典motion associationです。

## 実験条件・判定

- sample: `44b6_0113de3b.zarr`（`(T,Z,Y,X)=(100,64,256,256)`、uint16）。
- physical scale: `(1.625, 0.40625, 0.40625)` µm、公式 evaluator `max_distance=7.0` µm。
- GT: `../../../data/train/44b6_0113de3b.geff`。GTは推論入力に渡さず、prediction manifestを検証した後の評価phaseだけで開いた。
- cache/run/prediction receiptの`ground_truth_included`は全laneで`false`。divisionは初回raceでは無効化した。
- 公式metricはリポジトリ内のRoyerLab由来vendor実装を使用し、再実装していない。

## 手法構成と最終判定

- `blob_lap`: 3D Gaussian/local-max + physical NMSの画像-only detector、Hungarian/LAP linker。新規手法ではFinal Score `0.9140773262846648`で、公式比`+0.0302828427639145`。
- `cc_flow`: quantile foreground + 3D connected components、全フレーム `networkx.network_simplex` global min-cost flow。node recall `0.1346153846153846`、Final Score `0.04212152980003883`で、候補detectorが今回のデータに適合しなかった。
- `motion_lap`: 固定blob候補に速度・加速度priorを加えたframe-local LAP。公式detector共有laneではない。Final Score `0.8968305842792937`で、blob単独より`-0.0172467420053711`となり、今回の設定では採用しない。
- Best Method（全比較）: `harmonic v1`（Final Score `0.9211200215044129`）。Best new lane: `blob_lap`（`0.9140773262846648`）。
- 次に深掘りする候補: 公式TemporalUNet3Dのcenter detector候補を固定し、harmonic bidirectional association + ILPへ接続する実験。今回の公開実装調査ではofficial detector中間cacheが無く、別laneとしては未実施。
- 相補component: blob候補のnode recallは`1.0`だったため、まずconfidence calibration/NMSと、harmonic associationの組合せを優先する。motion priorは今回のreceipt上の改善根拠がない。

## 追加改善実験（blob NMS）

- 仮説: `blob_lap`のphysical NMS距離を3.0 µmから3.5 µmへ変更し、過剰nodeを減らす。その他のdetector/linker設定、sample、metricは固定した。
- receipt: `artifacts/performance_experiments/blob_lap_nms35/metrics.json`、source_commit=`ac2ece5`、CPU、runtime `63.7277883200004` s。
- 結果: nodes `27393`、edges `25098`、Edge TP/FP/FN `48/2/2`、Division TP/FP/FN `0/0/0`、Final Score `0.9172062183593925`。
- 差分: fixed `blob_lap`（`0.9140773262846648`）比 `+0.0031288920747277`、公式ベースライン比 `+0.0334117348386422`。harmonic v1には `-0.0039138031450204`で、単一sampleの改善候補として採用し、複数sample validation後に固定laneへ昇格する。

## 失敗・未実施候補

- HOCT、Trackastra、Ultrack、Linajea、DeepCenterは、公開source/checkpointまたはsegmentation/instance-mask入力契約、依存、checkpoint schemaの不足を`docs/results/multi_method_feasibility_ja.md`に記録した。今回の3本の公式評価値には含めていない。
- `official_motion`（公式detectorを共有するmotion ablation）は、upstreamに永続detector cache APIが無く、CPU 100-frame detector再実行を避けるためdeferredとした。
- 全laneはCPU実行。`cc_flow`はsolver status `optimal`だが、detector側の低recallが支配的だった。

## 再現コマンド

```bash
docker compose exec -T -w /workspace/biohub-cell-tracking-during-development/scratch/strong-baseline-v1/biohub-cell-tracking-during-development biohub uv run --no-sync python scripts/run_benchmark_race.py infer --method blob_lap --image-stem ../../../data/train/44b6_0113de3b.zarr --cache-root artifacts/multi_method_race/cache --output-root artifacts/multi_method_race
docker compose exec -T -w /workspace/biohub-cell-tracking-during-development/scratch/strong-baseline-v1/biohub-cell-tracking-during-development biohub uv run --no-sync python scripts/run_benchmark_race.py infer --method cc_flow --image-stem ../../../data/train/44b6_0113de3b.zarr --cache-root artifacts/multi_method_race/cache --output-root artifacts/multi_method_race
docker compose exec -T -w /workspace/biohub-cell-tracking-during-development/scratch/strong-baseline-v1/biohub-cell-tracking-during-development biohub uv run --no-sync python scripts/run_benchmark_race.py infer --method motion_lap --image-stem ../../../data/train/44b6_0113de3b.zarr --cache-root artifacts/multi_method_race/cache --output-root artifacts/multi_method_race
docker compose exec -T -w /workspace/biohub-cell-tracking-during-development/scratch/strong-baseline-v1/biohub-cell-tracking-during-development biohub uv run --no-sync python scripts/run_benchmark_race.py evaluate --prediction artifacts/multi_method_race/methods/<method>/44b6_0113de3b.geff --ground-truth ../../../data/train/44b6_0113de3b.geff --metrics artifacts/multi_method_race/evaluation/<method>/metrics.json
docker compose exec -T -w /workspace/biohub-cell-tracking-during-development/scratch/strong-baseline-v1/biohub-cell-tracking-during-development biohub uv run --no-sync python scripts/run_benchmark_race.py summarize --root . --output docs/results/multi_method_benchmark_race.md --summary-json artifacts/multi_method_race/race_summary.json
```
