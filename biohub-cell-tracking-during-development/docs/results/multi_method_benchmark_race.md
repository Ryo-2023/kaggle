# Biohub Multi-Method Benchmark Race

この文書は保存済みの `run.json` と `metrics.json` だけから生成した比較記録です。
推論中にGTは使用せず、未実行の手法は数値を補わず `BLOCKED` と記録します。

## 比較結果

| 指標 | 公式ベースライン | harmonic v1 | blob_lap | cc_flow | motion_lap |
|---|---:|---:|---:|---:|---:|
| `prediction_node_count` | 25994 | 26301 | BLOCKED | BLOCKED | BLOCKED |
| `prediction_edge_count` | 23536 | 24205 | BLOCKED | BLOCKED | BLOCKED |
| `edge_tp` | 46 | 48 | BLOCKED | BLOCKED | BLOCKED |
| `edge_fp` | 2 | 2 | BLOCKED | BLOCKED | BLOCKED |
| `edge_fn` | 4 | 2 | BLOCKED | BLOCKED | BLOCKED |
| `division_tp` | 0 | 0 | BLOCKED | BLOCKED | BLOCKED |
| `division_fp` | 0 | 0 | BLOCKED | BLOCKED | BLOCKED |
| `division_fn` | 0 | 0 | BLOCKED | BLOCKED | BLOCKED |
| `edge_jaccard` | 0.884615384615385 | 0.923076923076923 | BLOCKED | BLOCKED | BLOCKED |
| `adjusted_edge_jaccard` | 0.88379448352075 | 0.921120021504413 | BLOCKED | BLOCKED | BLOCKED |
| `division_jaccard` | null | null | BLOCKED | BLOCKED | BLOCKED |
| `final_score` | 0.88379448352075 | 0.921120021504413 | BLOCKED | BLOCKED | BLOCKED |
| `node_recall` | 1 | 1 | BLOCKED | BLOCKED | BLOCKED |
| `total_node_ratio` | 0.00927975150456222 | 0.0211997670355271 | BLOCKED | BLOCKED | BLOCKED |

### Final Score差分 (公式ベースライン比)

| 手法 | 状態 | Final Score | 公式との差分 | 実行時間[s] | expected / actual device |
|---|---|---:|---:|---:|---|
| 公式ベースライン (`official_ilp`) | OK | 0.88379448352075 | +0 | 3967.187808434 | cpu / cpu |
| harmonic v1 (`harmonic_ilp`) | OK | 0.921120021504413 | +0.0373255379836626 | 4459.703853909 | cpu / cpu |
| blob_lap (`blob_lap`) | BLOCKED | BLOCKED | — | 不明 | 不明 / 不明 |
| cc_flow (`cc_flow`) | BLOCKED | BLOCKED | — | 不明 | 不明 / 不明 |
| motion_lap (`motion_lap`) | BLOCKED | BLOCKED | — | 不明 | 不明 / 不明 |

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

- 状態: `BLOCKED` (未実行またはreceipt不足: run.json, metrics.json)
### `cc_flow`

- 状態: `BLOCKED` (未実行またはreceipt不足: run.json, metrics.json)
### `motion_lap`

- 状態: `BLOCKED` (未実行またはreceipt不足: run.json, metrics.json)

## 既知の制約

- これは単一のKaggle train sampleに対する同条件比較であり、leaderboard性能を意味しません。
- 疎なGTでは未注釈cellをfalse positiveと解釈しません。
- `division_jaccard=null` は公式summarizerのdivision項が存在しない場合の値です。
- official detectorを共有するmotion laneは今回のraceでは未実施です。
  `motion_lap`はblob候補上の古典motion associationです。
