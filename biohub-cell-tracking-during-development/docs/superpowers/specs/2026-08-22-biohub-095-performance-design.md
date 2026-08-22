# Biohub 0.95 Performance Goal 設計仕様

**Status:** 2026-08-22 ユーザー承認済み  
**対象branch:** `codex/biohub-095-performance`  
**外部提出:** この設計にはKaggle submissionの送信を含めない。

## 1. 目的と判定基準

既存の公式 `TemporalUNet3D + Node Transformer + ILP` detector-fixed pipelineを土台に、一次配布元を固定できる公開手法のグラフ修復をGT非依存で追加し、固定5サンプル `PANEL_V1` のRoyerLab由来公式Final Score macro平均を `0.95` 以上へ改善する。

`PANEL_V1` は次の5件から変更しない。

- `44b6_0113de3b`
- `44b6_0b24845f`
- `44b6_0c582fdc`
- `44b6_0db75fae`
- `44b6_12dfb391`

Done判定は次の全条件を必要とする。

1. 5/5 sampleを除外せず、同一の事前固定configで完走する。
2. 各sampleの実画像からprediction GEFFを生成する。
3. prediction manifestを検証した後にだけGTを開き、vendored official metricで評価する。
4. 5 sampleのunweighted macro Final Scoreが `>= 0.95` である。
5. division sample `44b6_12dfb391` を保持し、Division TP/FP/FN/Jaccardを報告する。
6. source、commit、checkpoint SHA-256、config、device、runtime、artifact hash、再現commandを日本語レポートへ残す。

現在の比較基準は `harmonic_v1` のmacro `0.7944143977140719` で、目標差は `+0.1555856022859281` である。既存5件の結果はすでに閲覧済みなので、0.95到達は「固定panel上のengineering gate」と表現し、未見データへの一般化証明とは表現しない。

## 2. 評価ファイアウォール

### 2.1 GTを利用してよい境界

GTは、完成済みprediction GEFFとmanifestのhash整合を検証した後、公式metric評価と失敗原因の事後診断でのみ開く。次には使用しない。

- detector推論
- cache生成
- candidate edge生成
- threshold・weight・checkpoint・methodの選択
- graph repairの条件分岐
- sample除外

診断で得たGT対応edge、TP/FP/FN、oracle、per-edge rankは次のconfig選択へ入力しない。診断は到達可能上限と壊れている境界の説明に限定する。

### 2.2 configの固定

手法と定数は次のいずれかだけから固定する。

- 一次配布元の公開default
- 公開notebook/repositoryに記録された値
- image/cacheだけから計算する単位付き統計
- 評価前にcommitされた機械可読selection lock

公式metricを見た後のparameter sweep、best sample/seedの採用、低score sampleの除外を禁止する。既存 `PANEL_V1` は結果閲覧済みのretrospective benchmarkとして凍結し、新しいconfigを一度だけlocked confirmationする。

### 2.3 補助panel

既存5件がすべて `44b6_` である偏りを補うため、test IDと `PANEL_V1` を除外した `6bba_` train sampleから、sample IDのSHA-256順でscore非依存に `D_DEV` を固定する。`D_DEV` のGTもmodel selectionには使わず、完成configのdomain smoke/evaluationにだけ使う。将来一般化性能を主張する場合は、別の未見 `H_HOLDOUT` を評価前に固定する。

## 3. 採用する手法

基準実装は公開 `Clean V106` 系である。

- 一次配布元候補: `https://github.com/asapacsin/biohub-cell-tracking`
- 公開notebook保存SHA-256: `5adc99aef3b61f2d8c5da5253eb1df13262986e8879bf6f630b5c1b5fa345d9d`
- 基盤: 同系統の3D UNet + Node Transformer checkpoint
- 修復: D4 TTA、motion relinking、1-frame gap repair、density-adaptive gate、safe division、isolated-node pruning、minimum component length、five-node rescue、line-fit smoothing

同じ一次repositoryのcommit `843a47fdd531bdf7e6377673135519c54b69ae28` には、その後に別fixed-8/holdout-8でpromoteされた `Recipe C` が公開されている。これは本campaignの第一性能候補とする。

- dual-seed raw-logit blend `alpha=0.5`
- detector threshold `0.96875`
- edge threshold `0.40`
- D4 TTA ON
- ILP weights `edge=-1.0, appearance=0.0, disappearance=1.575, division=1.0`
- motion relink OFF
- gap close、safe division、minimum track length 6、adaptive five-node rescue、line-fit smoothing ON

source側の記録値はfixed-8 `0.9181439782806684`、holdout-8 `0.9646726188580379` である。本campaignの5件に対応するsource側per-sample値を単純平均すると約 `0.956` になるため、0.95 gateを越える可能性がある。ただしsource側は `official-spec-lite` evaluatorであり、この数値は本repoのvendored official metricで再現するまで参考値とする。

source commit、license、notebookとrepositoryの対応、weightの取得元、checkpoint SHA-256、training splitは実行前にreceiptへ固定する。確認不能な部品は同名の自作代用品で埋めず、確認できた公開コードだけを最小adapterで再利用する。

`DualSeed / Frame Retention Guard` は次候補だが、追加checkpointのsource/schema/license/training splitを固定できるまで実行候補へ昇格させない。provenanceを閉じられない `DeepCenter`、opaque notebook、非公開weightは採用しない。

## 4. アーキテクチャ

```text
OME-Zarr image
    │
    ├─ device auto: CUDA → MPS → CPU
    ▼
pinned detector / node transformer
    │
    ▼
GT-free detector cache + mmap + provenance
    │
    ├─ public/default graph construction
    ├─ GT-free motion relinking
    ├─ one-frame gap repair
    ├─ safe division / rescue / component pruning
    └─ coordinate smoothing
    ▼
prediction GEFF + immutable manifest + selection_lock_id
    │ manifest/hash検証
    ▼
official metric boundary ── GTを初めて開く
    ▼
per-sample receipt + fixed-panel aggregation + Japanese report
```

既存 `detector_fixed_race` cache schema、mmap、prediction manifest、official metric adapterを再利用する。vendored `src/biohub/official_metrics/metrics.py` と `division_metrics.py` は変更しない。

グラフ修復はcache node、candidate score、予測graphだけを受け取り、image path、GT path、metric resultを引数に持たない。1-frame gapは既存cacheに `delta_t=2` candidateがないため、retained nodeのphysical positionとtrack velocityから新しい候補をGT-freeで生成し、生成理由とgateをreceiptへ残す。

## 5. 診断と性能上限

Recipe Cのlocked official evaluationが0.95未達、またはsource側参考値とmaterialに不一致の場合は、RAM-safe診断を追加して次を分離して測る。

- GT node detection coverage
- endpoint検出済みGT edgeに対するcandidate coverage
- true-edgeのforward/reverse/harmonic scoreとrank
- detector-fixed edge-perfect oracle
- node post-filter後のscore再正規化counterfactual

大規模cacheは `candidate_edges.mmap` をchunk readし、全edge長のPython list/辞書を作らない。診断はprediction manifestとcache hashをGT open前に検証する。oracleは通常推論結果ではなく、到達可能上限として明記する。

現在の5-sample edge-perfect oracle macroは約 `0.976665`、division-perfect換算は約 `0.996665` である。この差から、0.95はnode detectorの全面再学習より、candidate recall、graph repair、node-count adjustment、division修復を優先する。ただしoracleのGT edgeを推論へ混入させない。

## 6. 実行順序

### Wave A — 評価分離とsource契約

source/checkpoint/config契約、protocol/selection-lock receipt、GT open順序guardをTDDで追加する。ここでは性能configを変更しない。

### Wave B — 公開V106/Recipe C系のGT-free修復

一次sourceを固定し、公開コードを再実装せずpinned checkoutから呼ぶadapterを作る。V106 defaultをprovenance control、Recipe Cを第一性能候補とする。dual-seed checkpointが両方とも期待SHA-256と一致しない限りfull runを開始しない。synthetic graphでtopologyと座標単位を検証する。

### Wave C — 評価前固定

source/checkpoint/config/device/seed/code commitをselection lockへ固定する。image/cache-only structural checksを通した単一configだけを `D_DEV` と `PANEL_V1` へ進める。

### Wave D — 実データ評価

`D_DEV` をdomain smokeとして完走し、configは変更せず `PANEL_V1` 5件を順次実行する。巨大cacheの `0b` と `12df` は同時実行しない。全predictionを公式metricで評価し、macroと各sampleの必須値を日本語レポートへ追記する。

### Wave E — 未達時の診断

locked Recipe Cが0.95未達、またはsource側参考値とofficial値がmaterialに不一致の場合だけ、mmap/chunked diagnosticsを実装する。診断結果は手法選択へ還流させず、未達境界の説明と次の独立した公開候補の判断材料に限定する。

## 7. deviceと計算資源

PyTorch inferenceの既定device選択は `CUDA → MPS → CPU` とする。現在のmacOS DockerはLinux containerなのでApple MPSを利用できず、CUDAもpassthroughされていないためCPUへfallbackする。NVIDIA desktop移行時は同じ `--device auto` でCUDAを選び、checkpoint/device identityをreceiptへ記録する。

ILP、GEFF I/O、official metric、巨大mmapのstream処理はCPU処理を維持する。GPUへ転送するとI/O・solver支配部分では逆に不利なので、行列演算を含むPyTorch detector/edge modelだけをGPU対象とする。

## 8. provenanceと成果物

各runは最低限、次をappend-only receiptへ保存する。

- protocol/panel/selection lock IDとSHA-256
- sample ID、image/GT/prediction/cache hash
- source repo/commit/license、adapter source hash
- checkpoint URI/SHA-256/training split
- config hash、seed、code commit、container/device
- GT-free inference command、runtime、node/edge/fork count
- manifestがGT open前に検証された事実
- Edge/Division TP/FP/FN、各Jaccard、Final Score
- failure/statusと再現command

主要成果物は次とする。

- `docs/results/chatgpt_submission_report_ja.md`
- `docs/results/strong_baseline_v1.md`
- `docs/results/biohub_095_performance.md`
- `artifacts/biohub_095/` 以下のsource、lock、prediction、metric、receipt

## 9. 停止条件

- GTが推論・cache・candidate生成・config選択へ入る経路を検出した場合はrunを無効化する。
- source/checkpoint/provenanceを固定できない公開手法は採用しない。
- OOM時は同じ巨大cacheを一括展開せず、mmap/chunk/逐次sampleへ切り替える。
- official metricファイルを変更してスコアを上げない。
- 0.95未達の数値を達成と表現せず、実測差と次の独立した公開候補を記録する。
