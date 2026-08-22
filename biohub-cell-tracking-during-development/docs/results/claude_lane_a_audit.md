# Lane A 独立監査レポート — 0.92112 は正しいか

監査日: 2026-08-21 / ブランチ `claude/a-audit` / 監査対象: `strong_baseline_v1` 2 本と
`detector_fixed_race/dev_full_auto_compact_timed` 4 手法。

> 本監査は「数値を無効化する / 提出を壊す」欠陥のみを対象とする。命名・書式・docstring は扱わない。
> すべての主張はファイル:行、または保存済み成果物に対する決定的な再計算で裏づける。
> 検証はホスト側の純 Python（`numpy` + `compression.zstd` による GEFF 直読）で行い、
> 稼働中コンテナのリソースは一切消費していない。

---

## 1. 結論（重要順）

1. **0.92112 という数値そのものは正しい。** メトリクス実装は上流とバイト一致で、
   `metrics.json` の値はカウントから厳密に再現できる。**しかし「harmonic が official より
   良い」という主張は統計的に支持されない。** 差分は GT エッジ 50 本中 2 本。最良ケース
   （harmonic が official を完全包含）の McNemar 正確検定でも **p = 0.50**。
2. **TP が同数なら harmonic は official より低い（0.88274 対 0.88379、−0.00105）。**
   harmonic の優位は「エッジ 2 本」に完全に還元される。ノード penalty では負けている。
3. **S1: 実装されている harmonic は、出典として宣言されている公開手法と一致しない。**
   `harmonic.py:84-92` の再アラインメントにより、下流の softmax が返すのは公開式の
   `p_harmonic` ではなく **`p_harmonic^s` を再正規化したもの**（`s` は列ごとのデータ依存
   指数、`clamp(0.5, 2.0)`）。代数的に厳密（残差 2.3e-15 で確認）。
4. **S1: 最良結果 `harmonic_v1` は自身の成果物から再評価できない。** 4 手法が 1 つの
   `prediction_manifest.json` を共有し、最後の `motion_gated` が上書きしている。
   いま `evaluate_prediction(harmonic_v1.geff, gt)` を呼ぶと path mismatch で例外になる。
5. **S1: division 項は構造的に取りこぼしている。** ILP の `division_weight=1.0` と
   `appearance_weight=0.1` は、分裂受理に **p > 0.9 という厳密な閾値**を課す
   （観測された 43 個の fork すべてで成立、最小 0.900084）。ローカルスコアは
   `0.1 × division_jaccard` 項を丸ごと落としているため、**ローカル値と LB 値は別スケール**。
6. **S1: 稼働中ジョブに直接効く。** `8b03cd6` で新設されたストリーミング検証の発火閾値が
   512 MiB だが、実キャッシュの `serialized_nbytes` は **460.2 MiB** で発火しない。
   対策が書かれた当のワークロードで死んでいる。閾値 1 つの修正で済む。
7. **否定できた赤旗（再調査不要）:** 検出器固定は成立、メトリクスは上流とバイト一致、
   `pool_kernel_um` 3.0 と 5.0 は**同一カーネル (3,3,3)** を生む非問題、孤立ノード削除は
   上流公式そのものの挙動、推論経路への GT 漏洩は無し。

---

## 2. 指摘（重大度順）

### S1-1 実装 harmonic ≠ 公開 harmonic（`p^s` への温度変換）

| 項目 | 内容 |
|---|---|
| 証拠 | `src/biohub/strong_baseline/harmonic.py:82-92` |
| 影響 | `strong_baseline_v1/harmonic_ilp` と `detector_fixed_race` の `harmonic_v1` 双方（= 見出しの 0.9211） |
| 確信度 | 高（代数的に厳密） |

公開式（`harmonic_ilp/source_receipt.json` の `formula_evidence`）は
`harmonic_prob = 1/((1-w)/p_f + w/p_r)` を dim=1 で正規化したところで終わる。
実装はそのあとに以下を追加している。

```python
harmonic_logits = torch.log(harmonic_prob.clamp_min(1e-8))       # harmonic.py:82
harmonic_center = harmonic_logits.mean(dim=1, keepdim=True)
harmonic_scale  = harmonic_logits.std(dim=1, keepdim=True, unbiased=False).clamp_min(1e-4)
harmonic_scale_ratio = (forward_scale / harmonic_scale).clamp(0.5, 2.0)
return (harmonic_logits - harmonic_center) * harmonic_scale_ratio + forward_center
```

上流は返り値に `torch.softmax(raw, dim=0)` を適用する
（`upstream/scripts/predict_unet_transformer.py:456`）。`center` と `forward_center` は
source 軸に沿って定数なので softmax で相殺し、残るのは倍率 `s` だけである。したがって

```
softmax( (log h − c)·s + m )  =  h^s / Σ_i h_i^s
```

つまり**下流が実際に閾値処理する分布は、公開式 `h` ではなく `h^s` の再正規化**。
`s = clamp(std(forward_logits) / std(log h), 0.5, 2.0)` は列ごとにデータで決まる。

数値確認（`scratchpad`、純 numpy、n_src=269、m=400 列の模擬ロジット）:

```
指数 s: min 1.008  median 1.042  max 1.103
max|final − h^s/Z| = 2.3e-15      <- 代数の厳密確認
max|final − h|     = 1.02e-01      <- 実装 vs 公開式の差（確率で最大 10 ポイント）
>0.5 の採否が変わる列: 19 / 400 (4.75%)
```

`log h` をそのまま返せば `softmax(log h) = h` で公開式に厳密一致する。docstring の
「upstream が期待する forward スケールに再アラインする」という根拠は成立しない
（upstream が要求するのは softmax 後の値だけ）。

**なぜ重大か:** 採否が変わる割合（模擬で約 4.75%）は、結論全体が乗っている
「エッジ 2 本」というマージンより桁で大きい。0.9211 は公開手法の再現値ではない。

補足として、実測の確率分布も歪みの痕跡と整合する（下流保存値、実データ）:

| | official_ilp | harmonic |
|---|---:|---:|
| p ≥ 0.99 のエッジ数 | 10 | 2,653 |
| p == 1.0（float64 で厳密）| 0 | 12 |
| 中央値 | 0.834219 | 0.902034 |

269 個の競合ソース上の真の softmax が float64 で厳密に 1.0 に飽和するには、
ロジット差が約 37 nat 必要であり、単独の融合では説明しにくい。

### S1-2 4 手法が 1 つの `prediction_manifest.json` を共有し、最良結果が再検証不能

| 項目 | 内容 |
|---|---|
| 証拠 | `src/biohub/strong_baseline/manifest.py:55`（`path.parent / "prediction_manifest.json"`）、`src/biohub/detector_fixed_race/panel.py:210-219`（4 手法とも `output_root/sample_id/` 直下に出力） |
| 実測 | ディスク上の manifest は `motion_gated.geff` を指し、`manifest_created_at = 19:11:38`。official/harmonic/mutual の評価時刻は 19:10:17 / 19:10:45 / 19:11:13 |
| 確信度 | 高（成果物で直接確認） |

`validate_prediction_manifest` は `Path(recorded_path).resolve() != path.resolve()` で
不一致なら例外を投げる（`manifest.py:83-86`）。したがって**いま `harmonic_v1.geff` を
再評価すると `prediction manifest path mismatch` で失敗する**。逐次実行（書き出し →
即評価）だったため当時は成功したが、監査証跡としては 4 分の 3 が失われている。

救いは `race_receipt.json` が各手法の `prediction_manifest_directory_sha256` を保持して
いる点で、本監査でホスト側から同アルゴリズムで再計算し **4 件すべて一致**を確認した:

```
official_ilp       53dd3e6ba4d2cea8  MATCH  (27 files, 333,320 B)
harmonic_v1        d5b90631a97ae0a8  MATCH  (27 files, 339,490 B)
mutual_confidence  7a502ca9c1afdac8  MATCH  (27 files, 324,747 B)
motion_gated       2875e65546d36c61  MATCH  (27 files, 314,788 B)
```

成果物自体は無傷。壊れているのは manifest ファイルと再評価経路のみ。

**修正案（小さい）:** manifest 名を `f"{output_path.name}.manifest.json"` あるいは
`prediction_manifest_{method_id}.json` にする。1 行の変更で監査証跡が 4 本とも残る。

### S1-3 手法比較には検出力が無い（2 エッジ、n=50、1 動画）

| 項目 | 内容 |
|---|---|
| 証拠 | 4 手法の `race_receipt.json`、GT = 52 ノード / 50 エッジ |
| 確信度 | 高（純粋な算術・厳密検定） |

FP がどちらも 2 なので `edge_jaccard = TP/(50+FP) = TP/52`。1 TP エッジの価値は
`(1/52)·(1 − 0.1·total_node_ratio)`。

| シナリオ | TP | J | final_score | official との差 |
|---|---:|---:|---:|---:|
| official（実測） | 46 | 0.884615 | 0.8837944835 | 0 |
| harmonic（実測） | 48 | 0.923077 | **0.9211200215** | +0.0373255 |
| harmonic が 47 TP なら | 47 | 0.903846 | 0.9019300211 | +0.0181355 |
| **harmonic が 46 TP なら（同点）** | 46 | 0.884615 | **0.8827400206** | **−0.0010545** |

- 1 TP エッジ = **0.01919** の final_score。
- 同点時に harmonic が負ける分（ノード penalty 差）= **0.00110**。
- したがって **harmonic の優位は「エッジ 2 本」に完全に還元される**。

有意性: 50 本の GT エッジについて対応ありの二値結果とみなす McNemar 正確検定。
harmonic が official のエッジを 1 本も落とさず 2 本だけ増やした**最良ケース**でも

```
b=0, c=2 → two-sided exact p = 0.5000
b=1, c=3 → p = 0.6250
b=2, c=4 → p = 0.6875
```

4 手法レース全体でも TP は 42→48、**50 本中 6 本**。これは順位づけの根拠として弱い。
`mutual_confidence` は FP=0（唯一）で TP=43 であり、精度重視の別の妥当解にも見える。

### S1-4 division 項の構造的取りこぼし（ILP コストが p > 0.9 を強制）

| 項目 | 内容 |
|---|---|
| 証拠 | `upstream/scripts/predict_unet_transformer.py:555-561`、`src/biohub/detector_fixed_race/association.py:30-35`、保存済み GEFF 4 本の実測 |
| 確信度 | 高（理論予測が 43/43 の fork で反例なし） |

**まず、パイプラインは分裂を出力できる**（「構造的に不可能」という仮説は否定）。
実測 fork（out-degree ≥ 2 のノード）数: official 3、harmonic 30、mutual 10、motion_gated **0**。

**なぜこの数なのか。** 上流は edge 確率を source 軸で softmax 正規化して 0.5 で閾値処理
するため（`predict_unet_transformer.py:456,465`）、**候補段階で 1 つの target には親が
高々 1 本**しか存在しない（実測: 4 手法すべて in-degree ヒストグラムが `{1: 全件}`）。
よって ILP が争うのは source 側の out-degree だけになる。最小化目的
`Σ(−1.0·p_e)·x_e + 0.1·appear + 0.1·disappear + 1.0·division` のもとで、

- 既に子を持つ source に 2 本目を足す差分 = `−p − 0.1 + 1.0 = 0.9 − p` → **受理条件は p > 0.9**
- 子を持たない source の 1 本目 = `−p − 0.2 < 0` → 常に受理

実測での検証（保存済み GEFF、fork の弱い方のエッジ確率）:

| 手法 | fork 数 | 2 本目確率の最小 | 最大 | 0.9 未満の件数 |
|---|---:|---:|---:|---:|
| official_ilp | 3 | 0.902766 | 0.940079 | **0** |
| harmonic_v1 | 30 | 0.902342 | 0.999924 | **0** |
| mutual_confidence | 10 | 0.900084 | 0.954442 | **0** |
| motion_gated | 0 | — | — | — |

43 個すべてが 0.9 の上側にあり、全体最小が 0.900084。予測どおり。
候補数と採択数の差（official 24,183→23,536、harmonic 25,023→24,205）も、
争っている source が約 650／約 850 あり、そのうち p>0.9 の 2 本目を持つものだけが
fork になったという解釈と整合する。

**LB への影響。** ローカルは 4 手法すべて division TP/FP/FN = 0/0/0 なので
`summarise()` が `has_divisions=False` と判定し、警告を出して division 項を落とす
（`official_metrics/metrics.py:375-377, 511-517`）。よって

- ローカル `final_score` は **エッジのみのスコア**（上限 ≈ 1.0）
- LB は `score = adj_edge_jaccard + 0.1 × division_jaccard`（上限 1.1、`metrics.md:144-149`）

**ローカルの 0.9211 と LB スコアは同じ尺度ではない。** 公開 LB と直接比較してはいけない。
分裂を 1 つも当てなければ `division_jaccard = 0/(0+FP+FN) = 0` で 0.1 を丸ごと失う。
`division_jaccard = 0.5` を取れる競合との差は 0.05 で、**harmonic vs official の差
(0.0373) より大きい**。

`--ilp-division-weight` は上流 CLI の引数として存在し（`predict_unet_transformer.py:633`）、
受理閾値は `division_weight − appearance_weight` で直接動く。0.6 にすれば閾値は p>0.5、
すなわち争っている source が全部 fork になり FP が爆発する。掃引すべきパラメータだが、
**ローカルでは検証できない**（この試料の GT 分裂は 0）。検証には `44b6_12dfb391` が要る。

### S2-1 キャッシュの `node_features` は保存済みロジットを生んだ特徴量ではない

| 項目 | 内容 |
|---|---|
| 証拠 | `src/biohub/detector_fixed_race/upstream_adapter.py:294-313`（`_assign_features`, first-observation 方針）、`upstream/scripts/predict_unet_transformer.py:355-446`（窓ごとの UNet 符号化） |
| 実測 | 100 フレームキャッシュ manifest: `node_feature_conflict_observation_count = 26397` / `node_count = 26887` = **98.2%** |
| 確信度 | 高 |

W=2、stride=1 なのでフレーム t は窓 (t−1,t) と窓 (t,t+1) の両方に現れ、TemporalUNet3D は
時間文脈が違えば違う特徴量を返す。上流はペア (t,t+1) について**常に窓 t の特徴量**を
両端に使う。一方キャッシュは**最初の観測**を正典にするため、t ≥ 1 のノードは
窓 (t−1,t) の「target スロット」特徴量で固定される。つまり **すべてのペアの source 端に
ついて、キャッシュの `node_features` は保存済み `forward_logit` を生成した入力ではない**。

算術一致: 衝突数は「フレーム 1..98 のノード数」に一致するはず。
`26887 − n(t=0) − n(t=99) ≈ 26419` に対し実測 26397（差 22 は 1e-5 以内で偶然一致した分）。
BRIEF §3.5 の「4 フレーム smoke で 453 件」も同じ機構で説明できる
（フレーム 1 と 2 のノード数の和 ≈ 452）。

**健全性の判定:**
- `forward_logit` / `reverse_logit` / `*_probability` を消費する手法（現行 4 手法すべて）
  には**影響しない**。これらは推論中にその場で捕捉されており、ペア内で整合している
  （reverse は `upstream_adapter.py:547-559` で `pair.*_features` を使って計算されている）。
  実際、キャッシュ再生の `official_ilp` は上流 CLI の結果を完全再現している（§4 参照）。
- **`node_features` から association を再計算する手法（学習ベースの re-scorer など）には
  健全でない。** その手法だけ上流と違う入力を見ることになり、「検出器固定」の前提が
  特徴量レベルで崩れる。将来レーンがここを踏む可能性があるため S2 で明記する。

**推奨:** manifest に `node_feature_slot = "target_slot_of_window(t-1,t)"` を書く、または
窓ごとの特徴量を両スロットぶん保存する（サイズは 2 倍だがノード側なので軽い）。

### S1-5 新設のストリーミング検証は 100 フレーム試料では**発火しない**（閾値が実測値の直上）

| 項目 | 内容 |
|---|---|
| 証拠 | live `8b03cd6`（+未コミット）`src/biohub/detector_fixed_race/cache.py`：`if serialized_nbytes > 512 * 1024 * 1024:` |
| 実測 | 実キャッシュの NPZ ヘッダから直接算出した `serialized_nbytes` = **482,526,472 B = 460.2 MiB** |
| 確信度 | 高（実ファイルのヘッダ実測。閾値との比較は算術） |

Codex は `19feb13` / `8b03cd6` で私の指摘どおりメモリ二重確保に対処し、
`_validate_npz_streaming` を追加した。**しかし発火条件が実ワークロードの直上にある。**

実キャッシュ `full_auto/cache/44b6_0113de3b/` の全配列を NPZ ヘッダから読み出した内訳:

```
nodes.npz            合計   4.6 MB   (node_features は (26887, 32) float32)
candidate_edges.npz  合計 477.9 MB   (7,240,938 行 × 66 B)
--------------------------------------------------------------
serialized_nbytes = 482,526,472 B = 460.2 MiB
閾値              = 536,870,912 B = 512.0 MiB
発火するか        -> False
```

したがって `else` 分岐に落ち、`_load_npz` が **455.8 MiB のエッジ配列を丸ごと 2 つ目の
コピーとして RAM に展開**し、続く `loaded_edges.validate(loaded_nodes)` が
`expected_voxel_delta` / `expected_physical_delta`（各 86.9 MB）、
`expected_voxel_distance` / `expected_physical_distance`（各 29.0 MB）の一時配列を作る
（`schema.py:240-251`）。**追加ピークは約 690 MB**、しかも 80 分の検出器実行が
終わった最後の瞬間に来る。

コーディネータ報告時点でコンテナは 7.651 GiB 中 **5.6 GiB** 使用。残り約 2.05 GiB なので
おそらく耐えるが、これは「落ちたら 80 分/試料が消える」箇所であり、
**書かれたばかりの対策がこのワークロードでは死んでいる**という状態は避けたい。

**修正は数値 1 つ:** 閾値を `128 * 1024 * 1024` 程度に下げる、または分岐をやめて常に
ストリーミング検証にする。パネル残り 3 試料に間に合えば実質ノーリスクで効く。

### S2-3 メトリクスの `scale` がハードコード既定値（上流は試料ごとに zarr から読む）

| 項目 | 内容 |
|---|---|
| 証拠 | `scripts/run_strong_baseline_v1.py:83`、`src/biohub/detector_fixed_race/prediction.py:25,87`（既定 `(1.625, 0.40625, 0.40625)`）対 `upstream/scripts/evaluate.py:40-48`（`open_dataset(...).scale`、無ければ `DEFAULT_SCALE`） |
| 確信度 | 高 |

現在のパネル 5 試料はすべて `(1.625, 0.40625, 0.40625)` なので**今は無害**。
ただしマッチングは 7 µm の物理距離閾値であり、scale が違う試料が入った瞬間に
静かに間違った µm 換算で採点される。`detector_fixed_race` の panel 経路は
`cache.manifest["scale"]`（= 画像 zarr 由来）を渡しているので健全
（`panel.py:206`）。危ないのは `run_strong_baseline_v1.py evaluate` の CLI 既定のみ。

### S2-4 `prediction_manifest_validated_before_gt` はハードコード定数

| 項目 | 内容 |
|---|---|
| 証拠 | `src/biohub/detector_fixed_race/prediction.py:211`、`src/biohub/strong_baseline/evaluation.py:114`（どちらも `True` リテラル） |
| 確信度 | 高 |

ただし**下地のチェックは本物**である。`validate_prediction_manifest` は
ディスク上のバイトから directory sha256 を**再計算**し、保存済み manifest と
`files` / `total_bytes` / `nodes` / `edges` まで突き合わせる（`manifest.py:88-94`）。
GT を開くのはその後（`prediction.py:168` → `169-170`、`evaluation.py:71` → `72-73`）。
したがって「評価呼び出しの内部で、GT を開く前に予測バイトが凍結されている」ことは
保証される。

保証**しない**こと: 別プロセス・別工程が先に GT を読んでいた可能性。フラグは
「検証分岐を通った」ことの記録であって、来歴の証明ではない。

**推奨:** `manifest_created_at < validated_at` を実際に assert する
（受領書には既に両方入っている）。加えて `_open_ground_truth` がモジュール規模の
番兵を立て、`write_prediction` / `materialize_detector_cache` がその未設定を assert する。

### S3-1 float32 と float64 の確率経路差が閾値マージンに対して無視できない

上流 CLI 経路と キャッシュ再生経路の `edge_prob` は多重集合として最大 **1.79e-07** 異なる。
一方、採択されたエッジの閾値 0.5 からの最小マージンは harmonic で **3.0e-06**
（`min p = 0.500003`）。約 17 倍しか余裕がない。今回は TP/FP が完全一致したので影響なし
だが、閾値ちょうどの候補が 1 本反転するだけで 0.0192 動く経路である。

### S3-2 パネル選定が GT の分裂数でバイアスされている

`freeze_validation_panel` の `require_division_if_available`（`panel.py:130-142`）は
GT の `division_source_count` を見て試料を差し替える。BRIEF §0.4 が許す
「GT メタデータのみのパネル設計」の範囲内だが、結果としてパネルは無作為標本ではなく
**分裂を含むよう偏っている**。パネル上の `division_jaccard` はランダム分割より楽観的に
出る。数値を読むときの注意事項として記録する。

### S3-3 キャッシュ再生 GEFF はノード ID を 0..N−1 に振り直す

上流 CLI 出力は元の検出 ID（0..26886）を保つが、`_compact_prediction_inputs`
（`prediction.py:48-50`）は 0..25993 に再採番する。座標・スコアは同一なので
メトリクスには影響しない（実測で確認）。ただし公式評価側の重複除去は
`EDGE_ID.min().over(...)` と `rank("ordinal").over(EDGE_SOURCE)` という **edge id の
タイブレーク**に依存する（`metrics.py:119-124, 140-145`）ため、原理的には
ID 順序が結果を変えうる。今回は変わっていない。

---

## 3. 否定できた赤旗（再調査不要）

### 3.1 検出器は本当に固定されている（BRIEF §3.2 は否定）

保存済み GEFF の直読による決定的証拠:

```
official node id 範囲 0..26886 / 25,994 件
harmonic node id 範囲 0..26886 / 26,301 件
共有 ID 25,923 件の (t,z,y,x) は完全一致       maxabsdiff = 0.0
両者に無い検出ノード 515 件（26,887 − 26,372）
両アーム 孤立ノード 0 件、エッジ端点が node 集合外 0 件
すべてのエッジで dt(target − source) = +1
```

ノード集合は「選択エッジに触れたノード」と厳密に一致する。よってノード数の差は
association のみの関数であり、検出の差ではない。`detector_fixed_race` はさらに強く、
4 手法が同一 `cache_hash = 0bc38739…` から走っている。

### 3.2 メトリクス実装は上流とバイト一致

```
diff upstream/src/tracking_cellmot/metrics.py           src/biohub/official_metrics/metrics.py           -> 差分なし
diff upstream/src/tracking_cellmot/division_metrics.py  src/biohub/official_metrics/division_metrics.py  -> 差分なし
git -C upstream rev-parse HEAD  -> 075fc5f5a52d11077f9dc2b074644618f26939e2
git -C upstream status --porcelain -> 空（改変なし）
```

`ADJUSTMENT_ALPHA=0.1`、`SCORE_DIVISION_WEIGHT=0.1`、`has_divisions` フォールバック、
micro 平均（division）と `w_i = TP+FP+FN` 加重平均（adj edge）、NaN 行スキップ
（`summarise:486,498`）— すべて `metrics.md:131-149` の記述と一致。

`total_node_ratio` の分母も独立に確認した。`25994/(1+0.009279751504562221) = 25755`、
`26301/(1+0.021199767035527083) = 25755`。GT GEFF の
`geff.extra.estimated_number_of_nodes = 25755` と一致（`data/train/44b6_0113de3b.geff/zarr.json`）。
両アームで分母は同一。

唯一の実装差は堅牢性方向: 上流 `evaluate.py:51-58` は `estimated_number_of_nodes` 欠損時に
NaN を返して続行するが、ローカル `_estimated_node_count` は例外を投げる。提出経路では
ローカル評価器を使わないので影響なし。

### 3.3 `pool_kernel_um` 3.0 vs 5.0 は完全な非問題

`load_model`（`predict_unet_transformer.py:162-200`）は `config.json` から
`unet_out_channels` / `unet_layers` / `downsample` / `window_size` しか読まない。
`pool_kernel_um` は `PredictConfig` の既定値 3.0 から来ており、**CLI にこれを上書きする
引数は存在しない**。よって実行されたのは 3.0。

さらに決定的なことに、**両者は同じカーネルを生む**。`downsample=(1,4,4)` を
`scale=(1.625,0.40625,0.40625)` に掛けると実効ボクセルは **等方 1.625 µm**。
`pool_kernel_from_um`（`predict_unet_transformer.py:229-251`）は偶数を +1 して奇数化するので:

```
um = 3.0 -> round(1.846) = 2 -> 奇数化 -> (3,3,3)
um = 5.0 -> round(3.077) = 3 ->        -> (3,3,3)
um ∈ [2.4375, 5.6875) はすべて (3,3,3)
```

パネル 5 試料は全部同じ scale なので、この赤旗はパネル全体で無効。閉じてよい。

### 3.4 孤立ノード削除は上流公式そのものの挙動（ADDENDUM A2 への回答）

**証拠の連鎖:**

1. `artifacts/strong_baseline_v1/official_ilp/run.json` の `command` は**未改変の上流 CLI**
   `upstream/scripts/predict_unet_transformer.py`（`return_code: 0`, `status: success`）。
   Codex のコードは 1 行も介在していない。
2. 上流 `predict()` は `graph = build_graph(coords, edges)`（全 26,887 検出ノードを追加）→
   `graph = solver.solve(graph)` → `save_graph(graph, ...)`（`predict_unet_transformer.py:554-564`）。
3. `save_graph` は `graph.to_geff(output_path)` そのもの（`upstream/src/tracking_cellmot/io.py:358`）。
   渡されたグラフをそのまま直列化するだけで、ノードの復元は一切しない。
4. その出力 GEFF を直読すると **25,994 ノード / 孤立ノード 0 件**。

つまり **上流公式ベースライン自身が孤立検出ノードを落として提出している**。
Codex の `_compact_prediction_inputs`（`prediction.py:28-55`）はこの意味論に合わせた
ものであり、逸脱ではない。

**評価器側もこれを要求も禁止もしない。** `evaluate()` は
`num_pred_nodes = graph.num_nodes()`（`metrics.py:330`）で提出 GEFF のノードを数えるだけ。
`metrics.md:47-49` の `T_pred` は「予測ノードの総数」であり、孤立検出を含める規定はない。

**スコアとしての価値（定量）:**

| 手法 | N_pred（削除後） | adj（削除後） | adj（26,887 全保持なら） | 削除の価値 |
|---|---:|---:|---:|---:|
| official_ilp | 25,994 | 0.88379448 | 0.88072727 | **+0.00306722** |
| harmonic_v1 | 26,301 | 0.92112002 | 0.91901976 | **+0.00210026** |
| mutual_confidence | 25,806 | 0.85982970 | 0.85622007 | +0.00360963 |
| motion_gated | 25,143 | 0.80961158 | 0.80414229 | +0.00546929 |

harmonic での価値は **+0.0021**、harmonic vs official の差 0.0373 の約 5.6%。
数値が水増しされているという疑いは**否定される**（上流と同じ規則で採点されている）。

**ただしここに一つのレバーが見えている。** 限界感度は

```
d(final_score)/d(N_pred) = −J·0.1/N_total = −3.584e-06  (J = 48/52)
ノード 1,000 削除 → +0.003584  (= TP エッジ 0.19 本ぶん)
ノード 5,000 削除 → +0.017920  (= TP エッジ 0.93 本ぶん)
```

harmonic 予測の連結成分（= トラック）は 2,096 本、長さ中央値 8、最大 91（100 フレーム中）。

| 長さ ≤ k のトラックを剪定 | 削除ノード数 | スコア増 |
|---:|---:|---:|
| 2 | 544 | +0.001950 |
| 3 | 1,195 | +0.004283 |
| 5 | 2,679 | +0.009602 |
| 10 | 6,010 | +0.021540 |
| 20 | 12,550 | +0.044980 |

長さ 20 以下の剪定だけで harmonic vs official の差分全体を超える。ただし剪定は
エッジも消すので TP を巻き込めば 1 本あたり −0.0192 で相殺される。
**重要な注意: 剪定閾値をローカルスコアを見て選ぶと GT へのチューニング（漏洩）になる。**
GT を見ない根拠（トラック長の事前分布、検出確率など）で決め、パネルでは検証のみ行うこと。

### 3.5 推論経路への GT 漏洩は見つからなかった

`src/biohub/**` と `scripts/**` の GEFF/zarr オープン箇所を全走査した結果:

| 箇所 | 用途 | 判定 |
|---|---|---|
| `strong_baseline/evaluation.py:73,31` | GT グラフ + `estimated_number_of_nodes` | manifest 検証（:71）の**後**。可 |
| `detector_fixed_race/prediction.py:170,181` | 同上 | manifest 検証（:168）の**後**。可 |
| `detector_fixed_race/panel.py:60,70-73` | GT のノード/エッジ/分裂数 | パネル構成のみ。BRIEF §0.4 が許す範囲（S3-2 の偏りは記録） |
| `strong_baseline/visual_check.py:183`, `visualizer/app.py:247` | 目視検査 | 推論経路外。ただし manifest 検証は行わない（S3） |
| `strong_baseline/runner.py:942`, `detector_fixed_race/cli.py:29`, `panel.py:30` | 画像 zarr のみ | GT ではない。可 |

推論側の構造的ガードも実在する: `InferenceRequest.__post_init__` は `.geff` を
image_stem/checkpoint として拒否（`runner.py:87-96`）、`materialize_detector_cache` は
GT を含むパスを拒否（`upstream_adapter.py:439-441`）、キャッシュ manifest は
`ground_truth_included=False` を不変条件として強制（`cache.py:82-92`）。

`detector_fixed_race` の reverse ロジット対応も確認した。`upstream_adapter.py:547-559` は
`(target, source)` の順で同じ 8 引数を渡し、返り値を `.T` して `(n_src, n_tgt)` に戻す。
`harmonic.py:58` の `transpose(1,2)` と同じ規約であり、**forward と reverse は同一の
順序付きノード対を指している**。すべてのエッジが `dt=+1` であることも実測済み。
座標は `p_coords * ds_arr_t`（原寸ボクセル）で上流と同一、キャッシュの
`physical_*` は `scale` を掛けた µm 系で別カラムに分離されており、混同はない。

---

## 4. キャッシュ再生の忠実性（強い肯定的所見）

`detector_fixed_race` の `official_ilp`（キャッシュ再生）は、上流 CLI 実行の結果を
**完全に再現**している。

```
ノード数 25,994 / エッジ数 23,536      両者一致
edge TP/FP/FN = 46/2/4               両者一致
final_score  = 0.8837944835207503     両者一致
node ID でソートした (t,z,y,x)         完全一致
edge_prob の多重集合                   最大差 1.79e-07（float32 対 float64）
```

ノード ID は再採番されている（0..25993 対 0..26886）が意味論は同一。
これは検出器キャッシュ・アダプタが上流を忠実に捕捉していることの強い証拠であり、
S2-1（`node_features` の正典化）が `forward_logit` を使う現行手法に影響しないことの
裏づけでもある。

---

## 5. 未検証・ブロック

- **harmonic の再アラインメント（S1-1）を外したときのスコアは未測定。** キャッシュのみで
  116 秒程度のはずだが、稼働中ジョブへの配慮で本監査では実行していない。
- **`ilp_division_weight` 掃引は測定不能。** この試料の GT 分裂が 0 なので、
  ローカルでは division_jaccard を評価できない。`44b6_12dfb391` のキャッシュ完成待ち。
- **S1-1 の「採否が 4.75% 変わる」は模擬ロジットでの数値であり、実データの実測ではない。**
  代数の部分（`final = h^s/Σh^s`、残差 2.3e-15）のみが厳密。
- **S2-2 のメモリ見積りは行あたりバイト数からの算術**であり、実プロセスの RSS 測定ではない。
- テストスイートは 1 本も実行していない（BRIEF §0.1 の禁止事項と、稼働中ジョブへの配慮）。
