# Biohub 0.95 Performance Goal 設計仕様

**Status:** 2026-08-22 ユーザー承認済み  
**対象branch:** `codex/biohub-095-performance`  
**外部提出:** この設計にはKaggle submissionの送信を含めない。

## 1. 目的と判定基準

既存の公式 `TemporalUNet3D + Node Transformer + ILP` detector-fixed pipelineをCurrent BestKnown比較基準とし、一次配布元を固定できる公開Recipe Cを第一候補として、固定5サンプル `PANEL_V1` のRoyerLab由来公式Final Score macro平均を `0.95` 以上へ改善する。associationだけを前提にせず、persist済みpredictionのerror analysisが別のbottleneckを示した場合はdetector、feature、division、optimizer、postprocess、model familyを再設計してよい。

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

GTは、完成済みprediction GEFFとmanifestのhash整合を検証した後、公式metric評価、失敗原因の事後診断、次実験のmethod/model family選択に使用してよい。次には使用しない。

- detector推論
- cache生成
- candidate edge生成
- 同一runのthreshold・weight・parameter fitting
- detector/association/model inputまたはsampleごとの推論条件分岐
- graph repairの条件分岐
- sample除外

診断で得たGT対応edge、TP/FP/FN、oracle、per-edge rankは、次に検証する独立したmethod/model familyやsubsystem仮説の選択へ利用できる。ただしGT座標・対応edge・metric値を次runのmodel input、loss、threshold fitting、sample別parameterへ渡さない。各次実験は実行前に仮説、変更点、control、採否基準、prior evidenceをcommit済みexperiment lockへ固定する。

### 2.2 configの固定

手法と定数は次のいずれかだけから固定する。

- 一次配布元の公開default
- 公開notebook/repositoryに記録された値
- image/cacheだけから計算する単位付き統計
- 評価前にcommitされた機械可読selection lock

同一runの公式metricを目的変数にしたparameter fitting/sweep、best sample/seedだけの採用、低score sampleの除外を禁止する。一方、完了済みexperimentの全5件error analysisを根拠に、次の独立したmethod/model familyやarchitecture仮説を選ぶ逐次研究は許可する。各experimentは5件を同じ固定configで再評価し、成功・失敗を含めappend-only ledgerへ残す。

### 2.3 補助panel

既存5件がすべて `44b6_` である偏りを補うため、test IDと `PANEL_V1` を除外した `6bba_` train sampleから、sample IDのSHA-256順でscore非依存に `D_DEV` を固定する。`D_DEV` はdomain smoke/evaluationに使えるが、`PANEL_V1` の分母や0.95 gateと混ぜない。将来一般化性能を主張する場合は、別の未見 `H_HOLDOUT` を評価前に固定する。

### 2.4 自律研究ledgerとBestKnown

各experimentは実行前に `experiment_id`、method family、仮説、expected gain、cost、risk、novelty、変更点、control、固定config/hash、採否基準を記録する。評価後は5件すべてのFinal Score、macro、median、control勝率、worst-case harm、division TP/FP/FN/Jaccard、runtimeを追記する。

BestKnownへ昇格できるのは、5/5 prediction/official receiptが揃い、固定panel・公式metric・macro算術を変えず、従来BestKnownよりmacroが高いexperimentだけである。単一sample改善だけでは昇格しない。同一method familyで5 experiment連続BestKnown更新がなければ微小parameter tuningを停止してfailure analysisをやり直す。全family通算10 experiment以上meaningful improvementがなければarchitecture-level reviewと公開手法再調査を実施する。

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

Recipe Cの公開assetは1つではなく、次の2つを同時に必要とする。

- primary/support tree: Kaggle dataset `pilkwang/biohub-tracking-support-pack-50ep-v1` version 10、CC0。`repo/`とprimary `weights/unet_transformer/split_0/edge_predictor_best.pth` を含む。
- secondary seed: Kaggle dataset `pilkwang/biohub-temporal-unet3d-seed314159-v1` version 2、CC0。配布時は `weights/unet_transformer/split_0/edge_predictor_best.pth` だが、実行用stagingでは `weights/unet_transformer/seed_314159/edge_predictor_best.pth` として参照する。

期待SHA-256はpredictor `c44e771ba5980b820f93091e03a303c25dfe8f3232e501f54dc9565731c234b9`、primary checkpoint `12f6881ee3620a831697ca098ff8f48e687a24225f4e048b538deec3562fe771`、secondary checkpoint `9bac2fa0dadc4a6fc1899e0caf187f4b553e0a7cd90ba1261a68b35ffe9e305f` である。primary packだけを取得してsecondaryを欠いた状態では実行しない。

`DualSeed / Frame Retention Guard` は次候補だが、追加checkpointのsource/schema/license/training splitを固定できるまで実行候補へ昇格させない。provenanceを閉じられない `DeepCenter`、opaque notebook、非公開weightは採用しない。

## 4. アーキテクチャ

```text
OME-Zarr image
    │
    ├─ device auto: CUDA → MPS → CPU
    ▼
pinned detector / node transformer
    │ primary support + separately pinned secondary seed
    │ staged predictorだけへD4/ensemble/threshold/device互換patch
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

一次sourceの `biohub_pipeline.inference.run_prediction()` はCUDAが利用できない環境を明示的に拒否する。本campaignはその関数内のCUDA gateを改変せず、同じ一次sourceの `build_predict_command()` で生成・固定したargvをadapterから `subprocess.run(..., cwd=staged_repo, env={..., PYTHONPATH: src}, shell=False, check=True)` で実行する。device互換変更はrun-localにcopyしたsupport predictorの選択式だけへ適用し、`CUDA → MPS → CPU` とする。一次sourceと配布assetはread-onlyで保持し、patch前後hashとこのorchestration adaptationをreceiptへ残す。

`build_predict_command()` はsplit file作成とpredictor patchを伴う。特にedge-threshold patchは二回目の適用が失敗するため、各runは期待predictor hashから作る新しいpristine staged copyを一度だけ使用する。direct subprocess後は外部sourceと同じGEFF件数検証、`postprocessing.configure()`、`write_submission_from_geff()`、CSV integrity、out-degree診断を省略しない。Python 3.11で必要moduleのcompile/importは確認済みで、pandasを要求する外部fixed-8 evaluatorはこのinference経路へimportしない。

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

source/checkpoint/config契約、2つのsupport asset契約、protocol/selection-lock receipt、GT open順序guardをTDDで追加する。ここでは性能configを変更しない。

### Wave B — 公開V106/Recipe C系のGT-free修復

一次sourceを固定し、公開コードを再実装せずpinned checkoutから呼ぶadapterを作る。V106 defaultをprovenance control、Recipe Cを第一性能候補とする。primary/support packと別配布secondary seedの両checkpointが期待SHA-256と一致しない限りfull runを開始しない。synthetic graphでtopologyと座標単位を検証する。

### Wave C — 評価前固定

source/checkpoint/config/device/seed/code commitをselection lockへ固定する。image/cache-only structural checksを通した単一configだけを `D_DEV` と `PANEL_V1` へ進める。

### Wave D — 実データ評価

`D_DEV` をdomain smokeとして完走し、configは変更せず `PANEL_V1` 5件を順次実行する。巨大cacheの `0b` と `12df` は同時実行しない。全predictionを公式metricで評価し、macroと各sampleの必須値を日本語レポートへ追記する。

### Wave E — 未達時の診断と次仮説

locked Recipe Cが0.95未達、またはsource側参考値とofficial値がmaterialに不一致の場合、mmap/chunked diagnosticsを実装する。診断結果は次に検証するmethod/model familyやsubsystem仮説の選択へ使えるが、GT edge/座標やmetric値を推論input・loss・threshold fittingへ渡さない。次experimentは実行前lockを作り、同じ5件で完走する。

## 7. deviceと計算資源

PyTorch inferenceの既定device選択は `CUDA → MPS → CPU` とする。現在のmacOS DockerはLinux containerなのでApple MPSを利用できず、CUDAもpassthroughされていないためCPUへfallbackする。一次sourceのCUDA-only `run_prediction()` は呼ばず、同sourceが組み立てたcommandを互換adapterから実行する。NVIDIA desktop移行時は同じ `--device auto` でCUDAを選び、checkpoint/device identityをreceiptへ記録する。

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

- GTが推論・cache・candidate生成・model input・parameter fittingへ入る経路を検出した場合はrunを無効化する。完了済みerror analysisから次method/model familyを選ぶこと自体は許可する。
- source/checkpoint/provenanceを固定できない公開手法は採用しない。
- OOM時は同じ巨大cacheを一括展開せず、mmap/chunk/逐次sampleへ切り替える。
- official metricファイルを変更してスコアを上げない。
- 同一method familyで5回連続BestKnown更新がなければ微小tuningを止め、10 experiment以上meaningful improvementがなければarchitecture-level reviewへ切り替える。
- 0.95未達の数値を達成と表現せず、実測差と次の独立した公開候補を記録する。
