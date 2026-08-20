# Biohub Multi-Method Benchmark Race 設計

## 目的

既存の公式 `TemporalUNet3D + SimpleNodeTransformer + ILP` と
`harmonic v1 (w=0.20)` を固定基準にし、同じ Kaggle train sample、同じ
RoyerLab 公式 evaluator、同じ GEFF 契約で複数の tracking family を比較する。
推論は常に image-only とし、GT は prediction GEFF の manifest 検証後の
evaluation phase でのみ開く。

## 初回 race の候補

| method id | detector | association / tracking | 比較上の位置づけ |
|---|---|---|---|
| `blob_lap` | quantile-normalized raw image の 3D local peak / LoG | physical-distance Hungarian/LAP | 独立した古典 detector + linker |
| `cc_flow` | quantile foreground + 3D connected components centroid | 全フレーム flow/ILP | 独立した古典 segmentation-like detector + global linker |
| `motion_lap` | `blob_lap` の固定 image-only candidates | velocity/acceleration prior を含む linker | classical motion-association family。official detector shared lane は deferred |

公式 upstream detector の中間 cache 抽出、HOCT、Trackastra、Ultrack、Linajea、DeepCenter は feasibility lane として
調査し、checkpoint・segmentation入力・依存が揃わないものは BLOCKED と記録する。
名前だけを模した実装は候補に数えない。公開実装と checkpoint が揃った場合だけ
後続 lane として追加する。

## 共通データ契約

対象 sample は `44b6_0113de3b.zarr`（`(T,Z,Y,X)=(100,64,256,256)`）に固定する。
physical scale は `(1.625, 0.40625, 0.40625)` µm、公式 evaluator の
`max_distance=7.0` µm を全 method で共有する。現在対応する GT は一つだけで、
未注釈 cell は negative と扱わない。

race の image-only request に ground-truth / `.geff` path を持たせない。
各 method は prediction GEFF を保存し、`tracksdata` reload と deterministic
manifest を完了してから artifact を返す。evaluator は manifest を検証してから
初めて GT を開く。

## cache と artifact

大幅な重複計算を避けるため、image metadata、normalization、detector candidates
を cache として分離する。ただし linker 間で意味が異なる edge score は共有しない。

```text
artifacts/multi_method_race/<run_id>/
  run_manifest.json
  cache/<cache_key>/
    cache_manifest.json
    detections.npz
    optional_features/
  methods/<method_id>/
    44b6_0113de3b.geff
    prediction_manifest.json
    run.json
    inference.log
  evaluation/<method_id>/metrics.json
  race_summary.json
```

cache key は image digest、shape/scale、quantile、detector config、source commit、
checkpoint SHA、schema version を含む。cache manifest は
`ground_truth_included=false` を必須にし、GT path/digest を保存しない。

## method adapter

各 adapter は次の責務を持つ。

1. image-only input と cache を検証する。
2. detector と linker の provenance/config を run receipt に保存する。
3. `(t,z,y,x)` の axis order と physical coordinate を保持して prediction GEFF を作る。
4. edge は通常 `t -> t+1`、division は親1→子2の意味を壊さない。
5. structural reload、manifest、node/edge count、runtime、device を保存する。

`blob_lap` は scipy の Gaussian/local-max と Hungarian を使い、division は
明示的に disabled と記録する。`cc_flow` は scipy connected components の
centroid を使い、候補を全 frame で global linking する。`official_motion` は
公開 official detector を変更せず、同じ candidate / feature path の linker
cost に velocity prior を加える。最初は bounded smoke で adapter 契約だけを検証し、
full run は smoke が通った lane だけにする。

## 評価と判定

各 method について以下を保存する。

- Final Score、Adjusted Edge Jaccard、raw Edge Jaccard
- Edge TP / FP / FN、Division TP / FP / FN、Division Jaccard
- prediction node / edge 数、node recall
- runtime、expected/actual device、solver/failure mode
- detector、association、graph optimization、source/checkpoint provenance
- baseline official / harmonic との差

single sample の score は leaderboard 性能と同一視しない。最終判定では score だけで
なく、安定性、実行時間、実装複雑度、将来性、GT分離の強さを併記する。
