# Detector-Fixed Association Race 設計仕様（案）

## 1. 目的と完了条件

公式 TemporalUNet3D 検出器を各 sample で一度だけ実行し、その出力を永続化する。同じ検出器出力を読み込む association 実装を複数比較することで、detector 差と linker 差を分離する。

完了条件は次の全てである。

- 開発 sample `44b6_0113de3b` で、GTを推論入力に含めず、検出器出力cacheを生成する。
- 同じ `cache_hash` を参照する公式+ILP、harmonic v1、および追加associationを最低2本について、prediction GEFFを生成する。
- 全predictionをcompetition公式metricで評価する。
- スコアを見る前に決めた3–5 sampleのvalidation panel（最低3 sample）で同じ比較を行う。division sampleは利用可能なら含める。
- 実行条件、出典、checkpoint、cache hash、metric、失敗例を日本語レポートへ記録する。

Kaggleへの外部提出は行わない。GTはmetric評価時にだけ開く。

## 2. 設計上の不変条件

### 2.1 detector-fixed

detector実行は `materialize` コマンドの一回だけとする。associationコマンドはcache以外の画像・GT・detector checkpointを開かない。cache manifestには、入力sample、画像digest、モデル・checkpoint・設定、upstream source commit、schema versionを含める。

### 2.2 GT-free cache

cacheの作成と検証はGTのパス・ファイル名・digestを受け付けない。manifestの `ground_truth_included` は常に `false` とし、node/edge featureの値にもGT由来の列を追加しない。疑わしいGTキーや `.geff` を検出した場合は削除して続行せず失敗させる。

### 2.3 座標と候補の意味

- node座標は `(t, z, y, x)` のvoxel座標と `(t, z, y, x)` のphysical座標を明示的に分ける。
- candidate edgeは `source_node_id < target_node_id`、`target_t > source_t` を検証する。
- edge feature/logitは同じnode indexに対して再現可能な順序で保存する。
- division候補を通常edgeへ暗黙変換しない。GEFF writerが保持できるdivision情報をそのまま出力する。

## 3. コンポーネント境界

```text
official detector (one run)
        │
        ▼
cache materializer ──► detector cache + manifest + hash
        │
        ├── official_ilp association
        ├── harmonic_v1 association
        ├── mutual_confidence association
        └── motion_gated association
                    │
                    ▼
              GEFF writer
                    │
                    ▼
          official metric (GT opened here only)
```

upstreamコードを直接大きく改変せず、次のadapter境界を設ける。

- `DetectorAdapter`: raw OME-Zarr sampleからupstream detectorのnodeとlinking入力を得る。中間オブジェクトを公開APIで取り出せる場合はそれを優先する。
- `CacheStore`: numpy配列とJSON manifestをatomicに保存し、shape/dtype/order/hashを再検証する。
- `AssociationAdapter`: cacheを読み、edge scoreと最適化結果を返す。画像・GTへの参照は禁止する。
- `PredictionWriter`: node、edge、divisionをGEFFへ変換し、prediction manifestを先に検証する。
- `MetricAdapter`: prediction manifestの検証後にだけGTを開き、competition公式実装を呼ぶ。

## 4. cache schema

sampleごとに `artifacts/detector_fixed_race/cache/<sample_id>/` を作る。

```text
manifest.json
nodes.npz                 # node_id, t, z, y, x, physical_zyx, node_features
candidate_edges.npz       # source, target, delta_t, edge_features, edge_logits
provenance.json           # source/checkpoint/config/package/device/runtime
READY                      # 全ファイルのdigest検証後に作成
```

manifest必須フィールド:

- `schema_version`, `sample_id`, `image_stem`, `shape`, `scale`, `image_sha256`
- `detector_id`, `detector_config`, `source_repo`, `source_commit`
- `checkpoint_uri`, `checkpoint_sha256`（無い場合は明示的にnull）
- `nodes_file`, `candidate_edges_file`, `array_schema`
- 各artifactのsha256、`cache_hash`、`ground_truth_included=false`

`cache_hash`は、GTを除くmanifestと全artifact digestをcanonical JSON化してSHA-256化する。partial cacheやdigest不一致はassociationの入力にしない。

## 5. association候補

controlは既存実装をcache入力に合わせた公式+ILPとharmonic v1である。追加候補は以下から最低2本を実装する。

1. `mutual_confidence`: forward/reverse edge confidenceの幾何平均（または両方向min）を採用し、片方向だけ高いedgeを除外してから既存ILPへ渡す。
2. `motion_gated`: physical座標の速度・変位をcache内のedge featureから計算し、距離ゲートで不可能なedgeを除外し、残りをconfidence + motion costでILP最適化する。
3. `gap_aware`（候補edgeに `delta_t > 1` が存在する場合のみ）: gap長に応じてappearance・disappearance・edge costを正規化する。schemaが対応しない場合は未実装理由を記録する。
4. `alternative_cost`（上記を実装できない場合の代替）:同一候補集合・同一ILP制約で、edge/appearance/division係数だけを変更する。係数はdev sampleのGTスコアを見て選ばず、事前固定する。

全候補は同一cache hash、同一GEFF変換、同一metric設定で評価する。採用する「winner」はdevだけで選び、validation panelの結果は別に報告する。

## 6. validation panelの固定規則

panelはmetricを計算する前に、train sampleのデータ可用性だけで決める。決定ログに sample ID、zarrのshape、GT graphの存在、division annotationの有無の判定根拠を記録する。スコア順のsample選択・除外は行わない。

最低3 sample、上限5 sampleとし、開発sampleをpanelへ含める場合も選択規則を先に固定する。GPU/CPU時間の制約で実行不能なsampleは「除外」ではなく、失敗理由と代替選択を記録する。

## 7. TDDと検証

実装前に次の失敗テストを追加する。

- GTキー、GT path、`.geff`を含むcache manifestを拒否する。
- node/candidate edgeのshape・dtype・座標順序・時間向きを拒否/受理する。
- atomic cacheのpartial状態、digest不一致、cache hash不一致を拒否する。
- association adapterが画像・GT引数を受け取らないことを検証する。
- 同一cacheから複数associationを実行してdetector call countが1になることを検証する。
- prediction manifestをGTを開く前に検証する。

その後、dev実データのmaterialize → 各association → GEFF → 公式metricを実行し、最後にvalidation panelを実行する。

## 8. フォールバックと停止条件

upstreamが中間出力を公開しない場合は、source patchを最小限にしたinstrumented wrapperで一回のdetector実行中に必要配列を捕捉する。公開checkpointが読み込めない、node feature/logitが再現不能、またはGEFF semanticsを保持できない場合、dummy/proxy outputへ置換せず、該当associationをblockedとして報告する。少なくとも公式+ILP、harmonic v1、成立した追加associationの結果を残す。

## 9. 受け入れ基準

日本語レポート `docs/results/detector_fixed_association_race.md` に、以下を表で掲載する。

- sample、cache hash、detector/checkpoint/source commit
- association方式、optimizer、prediction GEFF、node/edge数
- Edge/Division TP・FP・FN、各Jaccard、Final Score、control差
- 実行時間、device、失敗・未実装理由
- 再現commandと既知の問題

cache・prediction・metricsのreceiptが相互のhashを参照し、`ground_truth_included=false` と公式metricのGT使用境界を機械的に確認できることを完了条件とする。
