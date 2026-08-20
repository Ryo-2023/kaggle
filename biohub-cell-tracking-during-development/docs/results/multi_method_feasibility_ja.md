# Multi-Method Benchmark Race 候補実行可能性調査

調査日: 2026-08-20（JST）  
対象: `44b6_0113de3b.zarr`、既存 `biohub-dev`（Ubuntu 24.04 / Python 3.11 / CPU-only）  
目的: 公開実装を名前だけで代用せず、公式 source・checkpoint・入力契約・依存を確認し、今回の race に追加できるかを判定する。

## 結論

今回の実行条件で、追加依存なしにそのまま実データを完走できる learned 外部 tracker は確認できなかった。HOCT、Trackastra、Ultrack は公式コードまたは checkpoint が存在するものの、現在の入力が「画像 + point GEFF」であり、いずれも要求する segmentation / instance mask / foreground+contours との間に adapter が必要である。Linajea は公開 generic checkpoint と現在の Python/Zarr 環境への互換性を確認できず、DeepCenter は公開 notebook の input 名以外に checkpoint schema と公式推論コードを確認できないため、どちらも BLOCKED とした。

このため、今回の runnable lane は別途実装した `blob_lap`、`cc_flow`、`motion_lap` に限定する。`motion_lap` は `blob_lap` の image-only 候補に速度・加速度 prior を加える古典 association であり、公式 learned detector の motion lane ではない。公式 detector の中間 cache を使う `official_motion` は、upstream の `predict_video` が detector/feature/cache を永続化する API を提供していないため、今回の短い race からは延期した。

## 共通の判定条件

対象画像は OME-Zarr の `(T,Z,Y,X)=(100,64,256,256)`、dtype `uint16`、physical scale `(1.625, 0.40625, 0.40625)` µm である。推論時に GT GEFF を開くこと、GT から segmentation や threshold を作ること、未注釈細胞を負例にすることは禁止した。したがって、GT GEFF は公式 metric の evaluation phase にだけ渡せる。

現在の race の detector 入力は image-only の raw image から作る候補、またはその候補から作る point graph である。候補調査対象の多くは、次のいずれかを要求するため、point detection を直接渡せない。

| 入力の種類 | 今回の状態 |
|---|---|
| raw image `(T,Z,Y,X)` | あり（OME-Zarr） |
| non-GT integer instance segmentation | なし |
| non-GT foreground + contours | なし |
| point candidate / point GEFF | 既存 baseline で生成可能。ただし外部 tracker の直接入力契約とは別 |
| GT GEFF | あり。ただし evaluation 専用。推論入力・pseudo-mask 作成には使用不可 |

## 候補ごとの判定

### 1. HOCT

判定: **条件付き feasible。ただし今回の実データ lane は BLOCKED**。

| 項目 | 確認結果 |
|---|---|
| 公式 source | [`royerlab/hoct`](https://github.com/royerlab/hoct) |
| 固定 source revision | [`cabe8fd4bd1ccc3a18edc2b82b1e6501e396f357`](https://github.com/royerlab/hoct/commit/cabe8fd4bd1ccc3a18edc2b82b1e6501e396f357) |
| license | MIT（公式 `LICENSE` と `pyproject.toml` を確認） |
| 公式 checkpoint | `general_v0`、[`general_v0.pt`](https://github.com/royerlab/hoct/releases/download/weights-v0/general_v0.pt) |
| checkpoint SHA256 | `024c2e4606275c96667907abfc9e0c27487b543480caf99d9ebd1d267cef8e4a` |
| 入力契約 | 画像と同 shape の整数 label segmentation が必須。CLI は `hoct track <IMAGES> <SEGMENTATION> -o <OUTPUT.geff>` |
| OME-Zarr | 現行 loader は TIFF、単純 Zarr、OME-Zarr に対応。最初の multiscales dataset / channel を読む |
| 現環境 | `hoct=False`、`gurobipy=False`。`ilpy` / `pyscipopt` は存在 |
| 実行確認 | `uvx --from hoct hoct --help` は 30 秒で依存解決中に timeout。import、checkpoint load、tiny inference は未確認 |

画像 shape と OME-Zarr 軸は loader の想定に合う。一方、現ローカルにあるのは画像と GT GEFF で、推論用の non-GT segmentation はない。GT GEFF を label segmentation に流用するのはリークかつ形式違いなので採用しない。point GEFF をそのまま渡す公式経路もなく、`create_graph_from_points` は現行 source で未実装（`TODO` / `pass`）である。

最小の追加 adapter は、各 point の周囲に非重複 pseudo-instance を作って integer labels Zarr にする経路だが、regionprops の面積・形状・強度特徴を人工的にしてしまう。この経路を実データの性能改善として扱うには、pseudo-mask の定義を固定し、non-GT で 2–5 frame smoke、GEFF reload、公式 metric まで別実験として記録する必要がある。

### 2. Trackastra

判定: **公開 3D checkpoint があり条件付き feasible。ただし point-only の現条件では adapter 待ち**。

| 項目 | 確認結果 |
|---|---|
| 公式 source | [`weigertlab/trackastra`](https://github.com/weigertlab/trackastra) |
| version | [release `0.5.5`](https://github.com/weigertlab/trackastra/releases/tag/0.5.5)、候補 pin は `trackastra==0.5.5` |
| license | BSD-3-Clause（upstream README / package metadata） |
| 3D checkpoint | `ctc`、[`ctc.zip`](https://github.com/weigertlab/trackastra-models/releases/download/v0.3.0/ctc.zip)。metadata の dimensionality は `[2,3]` |
| その他 checkpoint | `general_2d`、SAM2 feature variant は 2D 専用。今回の 3D 候補には使わない |
| 入力契約 | `Trackastra.track(imgs, masks, ...)`。`imgs` と同 shape の instance-label `masks` が必須 |
| OME-Zarr | native OME-Zarr reader ではない。呼び出し側で array を選び `(T,Z,Y,X)` ndarray/Dask に揃えれば入力候補 |
| scale | pixel-coordinate API に physical scale を自動反映しない。座標・後処理側で `(1.625,0.40625,0.40625)` を保持する必要 |
| 出力 | NetworkX graph と tracked masks。公式 `write_to_geff` は付随 segmentation と graph を出力するが、既存単一 prediction GEFF layout との一致は未確認 |
| 現環境 / smoke | 未インストール、依存追加・checkpoint download・3D smoke は未実施 |

`ctc` は GT 不要で CPU device を選択できるが、Biohub の point candidate を直接入力できない。最短 adapter は各 point を小さな非重複球/楕円体にした pseudo-mask である。しかし、形状・面積・境界・anisotropy が人工的になり、3D CTC checkpoint の domain mismatch も未評価である。したがって「公開 checkpoint がある」ことだけを「強い結果が出る」と解釈しない。

依存追加を許可する場合の最小確認コマンドは次の形である。今回は実行していない。

```bash
uv pip install "trackastra==0.5.5"
```

その後、non-GT pseudo-mask を使った 2–3 frame、`mode="greedy"`、`device="cpu"`、`n_workers=0` の smoke で、graph の node/edge、座標軸、GEFF reload を確認する。`mode="ilp"` は追加 solver 依存と CPU runtime 不確実性があるため初回比較では避ける。

### 3. Ultrack

判定: **solver としては条件付き feasible。ただし raw image / point detection からの end-to-end detector は BLOCKED**。

| 項目 | 確認結果 |
|---|---|
| 公式 source | [`royerlab/ultrack`](https://github.com/royerlab/ultrack) |
| version | [release `0.8.0`](https://github.com/royerlab/ultrack/releases/tag/0.8.0)、候補 pin は `ultrack==0.8.0` |
| license | [BSD-3-Clause](https://raw.githubusercontent.com/royerlab/ultrack/main/LICENSE) |
| checkpoint | tracker checkpoint は不要。候補 segmentation を作り、linking + ILP solve を実行する方式 |
| 入力契約 | integer `labels`、または `foreground` と `contours`。raw image / point list の直接 detector API ではない |
| OME-Zarr | 呼び出し側で array を `(T,Z,Y,X)` に揃える必要。`scale` に physical scale を渡せる |
| 出力 | `to_networkx`、`to_geff` 等を提供。ただし既存 `tracksdata` 単一 GEFF layout との exact compatibility は未確認 |
| 現環境 / smoke | 未インストール、依存追加・pseudo-label smoke・solver runtime は未確認 |

point adapter は各 point を小さな binary component とした integer labels を作る経路が考えられるが、segmentation の候補生成・形状特徴を失う。追加依存も napari/Qt、numba、imagecodecs、pyarrow、database/solver 周辺まで広く、既存 lockfile への衝突リスクがある。Gurobi license がない場合の free solver path は未実測で、候補数が多い 3D+t の CPU ILP 時間も未確認である。

最小の候補確認コマンドは次の形である（未実行）。

```bash
uv pip install "ultrack==0.8.0"
```

依存と solver を隔離して、まず non-GT pseudo-label の 2–5 frame で `track(..., scale=(1.625,0.40625,0.40625))`、GEFF round-trip、division topology、solver elapsed/timeout を確認する。それを通過しても、現在の race の raw-image detector と同じ family とは数えない。

### 4. Linajea

判定: **BLOCKED**。

| 項目 | 確認結果 |
|---|---|
| 公式 source | [`funkelab/linajea`](https://github.com/funkelab/linajea) |
| version | `setup.py` の `1.5` |
| license | [MIT](https://raw.githubusercontent.com/funkelab/linajea/master/LICENSE) |
| checkpoint | 現行 repo / README から、Biohub に適用できる generic pretrained checkpoint の公式配布を確認できず |
| 入力契約 | 公式 pipeline は `01_train.py → 02_predict.py → 03_extract_edges.py → 04_solve.py`。学習・予測・edge 抽出を含む training-first 構成 |
| データ形式 | 例は Zarr と track CSV（`t z y x cell_id parent_id track_id radius name`）。native GEFF writer は今回確認できず |
| 現環境 | conda/PyTorch、gunpowder、daisy、funlib、pylp、MongoDB 周辺の旧来依存。Python 3.11 / 現行 Zarr stack との共存を未確認 |

generic checkpoint、固定 point graph を inference node として渡す簡易 API、OME-Zarr v3 の loader の三点が揃っていない。今回の範囲で依存を入れたり自前学習を始めたりするのは、短い race と「公開 checkpoint 優先」の条件から外れるため実施しない。

### 5. DeepCenter / center prior

判定: **BLOCKED（公開 notebook の名称確認まで）**。

調査で確認できたのは、公開 Kaggle notebook の input 名・説明に DeepCenter 系の名前が登場することだけである。

- [`pathik1511/biohub-metric-anatomy`](https://www.kaggle.com/code/pathik1511/biohub-metric-anatomy): `Biohub DeepCenterUNet3D Center Prior V1` を input として列挙。
- [`indarkarhana/biohub-dual-seed-frame-retention-guard-v1`](https://www.kaggle.com/code/indarkarhana/biohub-dual-seed-frame-retention-guard-v1): DeepCenter と `Biohub TemporalUNet3D Seed 314159 V1` を使用した notebook として列挙。

この情報からは、DeepCenter の公式 source repository、固定 commit/version、license、checkpoint file の SHA256、model class/shape/config、推論 entrypoint、OME-Zarr 入力契約を確定できない。ローカル `artifacts/strong_baseline_v1/upstream` にある learned weight は公式 `TemporalUNet3D + Node Transformer` の `edge_predictor_best.pth` 一式だけで、DeepCenter checkpoint、Temporal Seed 314159、Local Association Ranker は存在しない。

従って、公開 notebook の input 名を local checkpoint の存在や互換性の証拠として扱わない。実行 lane に昇格するには、ユーザーが取得・配置した checkpoint と推論コードについて、source/license/version、SHA256、state-dict schema、入力軸、非 GT smoke の全てを receipt に固定する必要がある。center prior を追加できる場合も、全候補の blind union/hard gate ではなく、marginal gap の score/veto 等の補助として別 method と記録する。

## 公式 upstream detector + motion lane の扱い

判定: **今回の race では deferred**。

公式 [`royerlab/kaggle-cell-tracking-competition`](https://github.com/royerlab/kaggle-cell-tracking-competition) upstream（既存 baseline の固定 tree、HEAD `075fc5f5a52d11077f9dc2b074644618f26939e2`）には、`UNetNodeTransformer.encode`、`detect`、`_detect_cells_pooled`、`predict_edges` といった hook は存在する。しかし、`DeepCenter`、`center-prior`、`motion`、`velocity`、`flow` の専用実装や、detector/feature の永続 cache API は確認できない。`predict_video` は normalization、encode、detect、edge prediction、graph build を一つの関数内で実行する構造である。

既存 official full inference は同じ 100 frame sample で CPU 約 3,967 秒（約 66 分）だった。公式 detector を一度だけ走らせて中間 candidate/feature cache を作るには upstream private helper の抽出または fork が必要であり、各 association lane が `predict_video` を再実行すると race の比較時間と cache reuse の目的を損なう。したがって、今回の `motion_lap` は公式 detector を使う `official_motion` ではなく、固定した `blob_lap` candidates 上の `classical_motion_association` として扱う。

将来 `official_motion` を再開する条件は、(1) upstream revision と source diff の固定、(2) detector candidate/feature cache schema と digest、(3) official detector を共有することの receipt、(4) 2–5 frame smoke、(5) CPU/GPU runtime と timeout の計測、である。条件を満たすまでは独立 learned method や SOTA 相当として数えない。

## 確認済みコマンドと未確認事項

### 実行済みの読み取り専用確認

既存調査で次を実施済みである。GT はいずれの import/help probe、依存確認、候補 feasibility 判定にも渡していない。

```bash
docker compose ps
docker compose exec -T biohub /opt/venv/bin/python --version
docker compose exec -T biohub uv --version
docker compose exec -T biohub /opt/venv/bin/python - <<'PY'
import importlib.util
for name in ['hoct', 'tracksdata', 'torch', 'zarr', 'gurobipy', 'scipy', 'polars']:
    print(name, bool(importlib.util.find_spec(name)))
PY
docker compose exec -T biohub timeout 30s uvx --from hoct hoct --help
```

確認結果は container healthy、Python 3.11.16、uv 0.12.5、`hoct=False`、`gurobipy=False`、`tracksdata/torch/zarr/scipy/polars=True`。HOCT の `uvx` probe は CUDA PyTorch/Gurobi 等の依存解決・download 中に timeout し、help 出力には到達しなかった。

### 未実行・未確認

- Trackastra / Ultrack / HOCT の追加 install、checkpoint download、import、2–5 frame smoke。
- Trackastra `ctc` の Biohub pseudo-mask adapter と 3D anisotropic coordinate の性能。
- Ultrack の free solver の実際の elapsed、candidate 数に対する ILP timeout。
- Linajea の Python 3.11/Zarr 3 共存、generic checkpoint、GEFF 変換。
- DeepCenter の source/checkpoint/schema/license/version、および local inference。
- 外部候補の公式 metric score。従って本書には外部候補の性能数値を記載しない。

## 再現時の安全な最小手順

依存変更と外部 checkpoint download はユーザー承認後に、既存 `biohub-dev` の lockfile を壊さない隔離環境で行う。各候補は次の順序に限定する。

1. source revision、version、license、checkpoint URL/SHA256 を receipt に保存する。
2. GT を使わず、2–5 frame の non-GT image crop と non-GT segmentation/pseudo-mask を用意する。
3. import/help → tiny inference → output graph reload → `(t,z,y,x)` / physical scale / division topology を確認する。
4. 失敗時は依存、入力 adapter、solver timeout、checkpoint schema のどこで止まったかを記録し、全量推論へ進まない。
5. smoke 成功後だけ、固定 100 frame sample と同じ公式 evaluator に別 phase で渡す。

この文書は feasibility 判定であり、候補の full inference、prediction GEFF、公式 metric score、Best Method の主張ではない。
