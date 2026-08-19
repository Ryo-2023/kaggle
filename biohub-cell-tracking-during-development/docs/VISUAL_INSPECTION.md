# Biohub Visual Inspector

## 目的

集計ログやスコアだけではなく、**モデルに入る顕微鏡画像と、モデルが出した細胞検出・追跡グラフを同じ画面で確認する**ためのローカルWebビューア。

このコンペの出力は通常のセグメンテーション画像ではなく、各時刻の細胞中心を表す node と、時刻間の対応を表す directed edge である。そのため右側の出力表示では、予測点と `t → t+1` の追跡線を元画像へ重ねる。

## 画面

- **左: Input image** — 指定した OME-Zarr の生画像スライス
- **右: Prediction / metric overlay** — 同じ画像へ予測結果と評価結果を重ねた表示
- time slider — 時刻 `t` を移動
- z slider — Z断面を移動
- Play — 時系列を連続再生
- z radius — 選択断面の前後何voxelまで点・線を表示するか
- layer toggles — 予測点、GT点、TP/FP/FN、未評価予測を個別に表示・非表示

色の意味:

| 色 | 意味 |
|---|---|
| 緑 | 公式metric上の TP edge |
| 赤 | 公式metric上の FP edge |
| 青 | GTに存在するが回収できなかった FN edge |
| 水色 | sparse GTのため公式metricでTP/FPのどちらにも数えない予測edge |
| 黄 | ground-truth node |
| 白 | prediction node |

未評価予測を消さずに水色で残すのは重要である。sparse ground truthでは、GTに無い予測を直ちに誤りとは判断できないため。

## 起動

`biohub-dev` コンテナ内で実行する。

```bash
python -m biohub.visualizer \
  --image data/train/<dataset>.zarr \
  --prediction <prediction>.geff \
  --ground-truth data/train/<dataset>.geff \
  --no-browser
```

Mac側のブラウザで次を開く。

```text
http://localhost:8765
```

Docker Composeはこのポートを `127.0.0.1` にだけ公開する。LANへ公開しない。

### 入力画像だけを見る

```bash
python -m biohub.visualizer \
  --image data/train/<dataset>.zarr \
  --no-browser
```

### 予測だけを重ねる

```bash
python -m biohub.visualizer \
  --image data/train/<dataset>.zarr \
  --prediction <prediction>.geff \
  --no-browser
```

この場合、追跡線はすべて未評価予測として水色で表示される。

### 予測とGTを比較する

`--prediction` と `--ground-truth` の両方を渡すと、取り込んだ公式metricでnode matchingとedge評価を実行し、TP/FP/FNへ分類して表示する。

```bash
python -m biohub.visualizer \
  --image data/train/<dataset>.zarr \
  --prediction predictions/<method>/<dataset>.geff \
  --ground-truth data/train/<dataset>.geff \
  --scale 1.625 0.40625 0.40625 \
  --max-distance 7.0 \
  --no-browser
```

既定値は公式baselineに合わせているが、データのmetadataが正本である。異なるvoxel scaleのdatasetでは必ず値を合わせる。

## 公式metricの配置

公式Royer Lab実装を次へ固定コピーしている。

```text
src/biohub/official_metrics/
├── __init__.py
├── metrics.py
├── division_metrics.py
└── LICENSE
```

`__init__.py` に取得元repository、upstream commit、元blob SHAを記録している。`metrics.py` と `division_metrics.py` は改変せず、ビューア固有処理は `src/biohub/visualizer/` に分離する。

## 表示とスコアの関係

- TP/FP edgeは公式 `_evaluate_matched_graph` の出力から作る。
- FN edgeは、TP predictionが覆っていないGT edgeとして表示する。
- edge/division TP・FP・FNとJaccardは画面上部にも併記する。
- adjusted edge Jaccardと最終scoreには、datasetごとの推定総node数を含むrun-level集計が必要である。この単一sampleビューアの主目的は画像診断であり、最終提出scoreの正本には既存のofficial evaluation pipelineを使う。

## 実装上の性質

- 4D画像全体を一括でPNG化せず、選択中の `(t, z)` だけをZarrから読む。
- `(T,Z,Y,X)` と `(T,1,Z,Y,X)` を扱う。
- ブラウザUIはPython標準HTTP serverとCanvasで動き、Gradioやnapariを追加依存にしない。
- 入力画像は左、出力overlayは右に常に並べるため、前処理や追跡の異常を数値だけでなく目視できる。
