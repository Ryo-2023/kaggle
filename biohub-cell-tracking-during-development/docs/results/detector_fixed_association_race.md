# Detector-Fixed Association Race — Strong Baseline v1

作成日: 2026-08-21（JST）  
更新日: 2026-08-22（JST）
ブランチ: `codex/biohub-multi-method-race`

## 0. 結論

同一detector出力を固定したassociation比較として、development・0b・0c・0dbの4 sampleで4方式の公式metricを取得した。divisionを含む12dfb391は実行中で、scoreは未取得である。GT-free実行契約は `ground_truth_included=false` で、cache生成・candidate生成・association選択ではGTを開かず、公式metric評価時だけ参照する。machine-readableな選定・実行成果物は `artifacts/detector_fixed_race/panel.json`、各runの `race_receipt.json`、`prediction_manifest.json` を参照する。

## 1. 目的と比較条件

同一の公式 detector 出力を固定し、association だけを交換して比較する。画像、cell-center、node feature、forward/reverse raw edge logitsは一度だけ取得し、後段はcacheのみを読む。GTはcache生成・candidate生成・association選択に渡さず、公式metric評価時だけ開く。

比較対象の4方式（development・0b・0c・0dbは完了、12dfb391は実行中）:

| method_id | association | graph最適化 |
|---|---|---|
| `official_ilp` | 公式forward probability | RoyerLab `build_graph` + ILP（SCIP fallback） |
| `harmonic_v1` | forward/reverse harmonic fusion (`w=0.20`) | 同上 |
| `mutual_confidence` | forward/reverse mutual confidence | 同上 |
| `motion_gated` | forward confidence + physical motion gate | 同上 |

## 2. detector / checkpoint provenance

| 項目 | 値 |
|---|---|
| detector | `TemporalUNet3D + SimpleNodeTransformer` |
| source repository | `https://github.com/royerlab/kaggle-cell-tracking-competition.git` |
| pinned source commit | `075fc5f5a52d11077f9dc2b074644618f26939e2` |
| checkpoint | `artifacts/strong_baseline_v1/inputs/cellmot-baseline-artifacts/weights/unet_transformer/split_0/edge_predictor_best.pth` |
| checkpoint SHA-256 | `347915de9c33883cb2ee69832a8e4552c88b1ec692d0fbfe956422467d3d4235` |
| detector threshold | `0.99` |
| TTA | Y/X flip TTA enabled |
| pool kernel | `3.0 µm` |
| edge activation | softmax |
| edge threshold | `0.5` |
| image device | `auto` = CUDA → MPS → CPU |

## 3. validation panel

scoreを見ずに、image metadataとGT graph metadata（node/edge/divisionの有無）だけで固定した。development sampleを先頭に含め、divisionが存在するsampleを優先した。

| sample | shape | GT nodes / edges | division source | 状態 |
|---|---:|---:|---:|---|
| `44b6_0113de3b` | `(100,64,256,256)` | `52 / 50` | `0` | development / 完了 |
| `44b6_0b24845f` | `(100,64,256,256)` | `51 / 49` | `0` | 4方式完了 |
| `44b6_0c582fdc` | `(100,64,256,256)` | `71 / 70` | `0` | 4方式完了 |
| `44b6_0db75fae` | `(100,64,256,256)` | `157 / 151` | `0` | 4方式完了 |
| `44b6_12dfb391` | `(100,64,256,256)` | `788 / 773` | `1` | 実行中、score未取得 |

## 4. cache契約と実行状況

cacheはGT-free manifest、`nodes.npz`、`candidate_edges.npz`、`READY`を原子的に公開する。manifestにはimage/checkpoint/source/adapter hash、detector設定、実デバイス、node/edge数を記録する。0bではcanonicalなdigest検証済みNPZを壊さず、association再生用の派生 `candidate_edges.mmap/`（schema `detector_fixed.cache_mmap.v1`、`source_cache_hash`一致）を追加した。sidecarはGT-freeで、必要なedge列だけを`numpy.memmap`として読む。

最初の全100フレーム試行では、sliding window間で同一nodeのcontextual featureが変わることが判明した。これはTemporalUNetの前後frame contextとwindow相対時刻による仕様である。修正後は最初の観測をcanonical featureとして保存し、衝突観測数をprovenanceへ記録する。pair単位のforward/reverse logitsはそのまま保持する。

修正後の4フレーム実データsmoke:

| 項目 | 値 |
|---|---:|
| node数 | `897` |
| candidate edge数 | `151,830` |
| feature conflict observations | `453` |
| cache hash | `e3be83ded19f3637478fae649d49de334a3e0db8f1744bcc3c16178abae6a0b` |

全100フレーム実行は完了した。cacheは `artifacts/detector_fixed_race/full_auto/cache/44b6_0113de3b/` にあり、`READY`・manifest・NPZのround-trip検証を通過した。

| 項目 | 値 |
|---|---:|
| cache hash | `0bc38739fa40d5dc38db99ec52a7ea5891849a6520d95ecbeed9bc126c6a62a8` |
| node数 | `26,887` |
| candidate edge数 | `7,240,938` |
| detector / forward / reverse calls | `100 / 99 / 99` |
| feature conflict observations | `26,397` |
| detector elapsed | `4,841.270636372006 s`（約80.69分） |
| requested / actual device | `auto / cpu` |
| cache artifacts | `nodes.npz` 3,564,450 bytes、`candidate_edges.npz` 197,971,252 bytes |

0b sample `44b6_0b24845f` の全100フレームcacheも完了した。GTはcache生成・candidate生成・associationには渡していない。

| 項目 | 値 |
|---|---:|
| cache hash | `50739a79bf081799d37987bbdd800ee2f95c5246ce07adead21812a3599a3b65` |
| node数 | `66,845` |
| candidate edge数 | `45,354,474` |
| detector / forward / reverse calls | `100 / 99 / 99` |
| feature conflict observations | `65,356` |
| detector elapsed | `5,476.415639576997 s`（約91.27分） |
| requested / actual device | `auto / cpu` |
| adapter source SHA-256 | `e914af35a2b68f2509027429efaa6ab29670be822212ae7c8628985f42a4ac72` |
| image SHA-256 | `7f7809f8948ce7f6c5c7cfb03d5b6fb8f140c725d16f0d63653d59620845d33a` |
| edge NPZ / mmap sidecar | `1,225,881,332` bytes / `2,993,397,988` bytes（sidecar `du` 約2.8 GiB） |

0bの最初のassociation再生では、圧縮NPZを全列展開したためコンテナのOOM killとなった。その後、pair単位のdisk capture、chunked memmap edge列、chunked validation、pair-contiguous grouping、`candidate_edges.mmap` sidecarを導入した。最終の0b公式ILP・追加3方式はOOM killを増やさず完走した。実装変更は `eb6e472` にcommit・push済みである。

0c sample `44b6_0c582fdc` の全100フレームcacheも完了した。GTはcache生成・candidate生成・associationには渡していない。

| 項目 | 値 |
|---|---:|
| cache hash | `2bd90bee3abf0afb07abdc971bfb45235a33bb931feaf6bfb3b884759682f748` |
| node数 | `34,910` |
| candidate edge数 | `12,459,009` |
| detector / forward / reverse calls | `100 / 99 / 99` |
| feature conflict observations | `34,236` |
| detector elapsed | `5,447.649957480986 s`（約90.79分） |
| requested / actual device | `auto / cpu` |
| adapter source SHA-256 | `e914af35a2b68f2509027429efaa6ab29670be822212ae7c8628985f42a4ac72` |
| image SHA-256 | `8143958530532e2701edc7e9c12b296167eeae1d672d709c495e0fdf137fb2d3` |
| edge NPZ / mmap sidecar | `338,666,747` bytes / `822,297,298` bytes（sidecar `du` 約0.77 GiB） |

0db sample `44b6_0db75fae` の全100フレームcache、edge sidecar、4方式の個別再生、prediction manifest検証、公式metric評価も完了した。GTはcache生成・candidate生成・associationには渡していない。

| 項目 | 値 |
|---|---:|
| cache hash | `bdaa6c60fd1ccc14abe0bcc0fde1a0efe8330692e10b9926e898c909ee89a3e9` |
| node数 | `19,599` |
| candidate edge数 | `4,346,571` |
| detector / forward / reverse calls | `100 / 99 / 99` |
| feature conflict observations | `19,233` |
| detector elapsed | `4,839.955556327011 s`（約80.67分） |
| requested / actual device | `auto / cpu` |
| checkpoint SHA-256 | `347915de9c33883cb2ee69832a8e4552c88b1ec692d0fbfe956422467d3d4235` |
| source repository / commit | `https://github.com/royerlab/kaggle-cell-tracking-competition.git` / `075fc5f5a52d11077f9dc2b074644618f26939e2` |
| image stem / SHA-256 | `44b6_0db75fae.zarr` / `c16d44a2dc0b08ab6dd47401c5bf6b9e6e52ebcb5b638decee88dcdc0203eb73` |
| adapter source SHA-256 | `e914af35a2b68f2509027429efaa6ab29670be822212ae7c8628985f42a4ac72` |
| GT-free manifest | `ground_truth_included=false` |
| sidecar | `candidate_edges.mmap/`、schema `detector_fixed.cache_mmap.v1`、`source_cache_hash` はcanonical cache hashと一致 |
| cache root total / sidecar portion `du` | `404M` / `274M`（canonical NPZ等は約130M） |

0dbのmaterializeと4方式の再生ではcgroup `memory.events` の `oom_kill=7` が開始前後で増加せず、追加OOM killはなかった。0bの初回全列展開で発生したOOMに対して導入したpair単位disk capture、chunked memmap、chunked validation、pair-contiguous grouping、sidecarを0dbでも再利用して完走した。

## 5. 公式metric結果

development、0b、0c、0dbのcache生成後に、prediction GEFFと公式metric receiptを取得した。12dfb391は実行中で、prediction GEFF・公式metric・scoreは未取得である。未取得の数値は推測で埋めない。

| sample / method | prediction GEFF | nodes / edges | candidate → selected | Edge TP/FP/FN | Division TP/FP/FN | Edge Jaccard | Adjusted Edge Jaccard | Division Jaccard | Final Score | runtime |
|---|---|---:|---:|---|---|---:|---:|---:|---:|---:|
| `44b6_0113de3b` / official | `dev_full_auto_compact_timed/44b6_0113de3b/official_ilp.geff` | `25,994 / 23,536` | `24,183 → 23,536` | `46/2/4` | `0/0/0` | `0.8846153846153846` | `0.8837944835207503` | `null` | `0.8837944835207503` | race合計116.29 s* |
| `44b6_0113de3b` / harmonic | `dev_full_auto_compact_timed/44b6_0113de3b/harmonic_v1.geff` | `26,301 / 24,205` | `25,023 → 24,205` | `48/2/2` | `0/0/0` | `0.9230769230769231` | `0.9211200215044129` | `null` | `0.9211200215044129` | race合計116.29 s* |
| `44b6_0113de3b` / mutual | `dev_full_auto_compact_timed/44b6_0113de3b/mutual_confidence.geff` | `25,806 / 22,727` | `23,257 → 22,727` | `43/0/7` | `0/0/0` | `0.86` | `0.859829702970297` | `null` | `0.859829702970297` | race合計116.29 s* |
| `44b6_0113de3b` / motion | `dev_full_auto_compact_timed/44b6_0113de3b/motion_gated.geff` | `25,143 / 21,799` | `22,032 → 21,799` | `42/2/8` | `0/0/0` | `0.8076923076923077` | `0.8096115765422697` | `null` | `0.8096115765422697` | race合計116.29 s* |
| `44b6_0b24845f` / official | `panel_runs/44b6_0b24845f/official_ilp.geff` | `55,324 / 44,335` | `48,068 → 44,335` | `39/9/10` | `0/0/0` | `0.6724137931034483` | `0.6262213541803576` | `null` | `0.6262213541803576` | 157.345064 s |
| `44b6_0b24845f` / harmonic | `panel_runs_0b_harmonic/44b6_0b24845f/harmonic_v1.geff` | `57,221 / 47,043` | `51,874 → 47,043` | `40/10/9` | `0/0/0` | `0.6779661016949152` | `0.6274705993317501` | `null` | `0.6274705993317501` | 161.112689 s |
| `44b6_0b24845f` / mutual | `panel_runs_0b_mutual/44b6_0b24845f/mutual_confidence.geff` | `52,875 / 40,639` | `43,362 → 40,639` | `37/8/12` | `0/0/0` | `0.6491228070175439` | `0.6093777667220346` | `null` | `0.6093777667220346` | 147.215804 s |
| `44b6_0b24845f` / motion | `panel_runs_0b_motion/44b6_0b24845f/motion_gated.geff` | `50,219 / 37,723` | `39,345 → 37,723` | `35/8/14` | `0/0/0` | `0.6140350877192983` | `0.5814113726151023` | `null` | `0.5814113726151023` | 145.915102 s |
| `44b6_0c582fdc` / official | `panel_runs_0c_official/44b6_0c582fdc/official_ilp.geff` | `32,245 / 28,388` | `30,140 → 28,388` | `57/6/13` | `0/0/0` | `0.75` | `0.738499713856499` | `null` | `0.738499713856499` | 59.321964 s |
| `44b6_0c582fdc` / harmonic | `panel_runs_0c_harmonic/44b6_0c582fdc/harmonic_v1.geff` | `32,602 / 29,176` | `31,164 → 29,176` | `62/6/8` | `0/0/0` | `0.8157894736842105` | `0.8022386963904503` | `null` | `0.8022386963904503` | 58.106748 s |
| `44b6_0c582fdc` / mutual | `panel_runs_0c_mutual/44b6_0c582fdc/mutual_confidence.geff` | `31,638 / 27,174` | `28,526 → 27,174` | `55/5/15` | `0/0/0` | `0.7333333333333333` | `0.7236807592340891` | `null` | `0.7236807592340891` | 57.710211 s |
| `44b6_0c582fdc` / motion | `panel_runs_0c_motion/44b6_0c582fdc/motion_gated.geff` | `31,072 / 26,243` | `27,154 → 26,243` | `50/6/20` | `0/0/0` | `0.6578947368421053` | `0.65056701593744` | `null` | `0.65056701593744` | 58.797033 s |
| `44b6_0db75fae` / official | `panel_runs_0db_official/44b6_0db75fae/official_ilp.geff` | `18,325 / 16,060` | `16,889 → 16,060` | `133/9/18` | `0/0/0` | `0.83125` | `0.8150423866970982` | `null` | `0.8150423866970982` | 22.204889 s |
| `44b6_0db75fae` / harmonic | `panel_runs_0db_harmonic/44b6_0db75fae/harmonic_v1.geff` | `18,576 / 16,523` | `17,469 → 16,523` | `134/8/17` | `0/1/0` | `0.8427672955974843` | `0.8249556959559359` | `0.0` | `0.8249556959559359` | 21.593063 s |
| `44b6_0db75fae` / mutual | `panel_runs_0db_mutual/44b6_0db75fae/mutual_confidence.geff` | `18,124 / 15,474` | `16,111 → 15,474` | `124/4/27` | `0/0/0` | `0.8` | `0.7854502771437888` | `null` | `0.7854502771437888` | 22.003911 s |
| `44b6_0db75fae` / motion | `panel_runs_0db_motion/44b6_0db75fae/motion_gated.geff` | `17,496 / 14,606` | `14,966 → 14,606` | `125/4/26` | `0/0/0` | `0.8064516129032258` | `0.795087139897136` | `null` | `0.795087139897136` | 19.994326 s |

`*` developmentのcache-only association、GEFF生成、公式metricを4方式で実行したwall time。0bのruntimeは各方式をsidecarから単独再生し、外部Python wrapperで計測した（各predictionディレクトリの`wall_time.txt`）。developmentの公式baselineとの差は、official `+0`、harmonic `+0.0373255379836626`、mutual `-0.0239647805504533`、motion `-0.0741829069784806`。0bのofficialとの差は、harmonic `+0.0012492451513925`、mutual `-0.0168435874583230`、motion `-0.0448099815652553`。

0bのcandidate→selected edge数は、official `48,068→44,335`、harmonic `51,874→47,043`、mutual `43,362→40,639`、motion `39,345→37,723`。officialのnode recallは`0.9803921568627451`、total node ratioは`0.6869644762921177`である。4方式ともprediction manifestをGTを開く前に検証した。

0cのcandidate→selected edge数は、official `30,140→28,388`、harmonic `31,164→29,176`、mutual `28,526→27,174`、motion `27,154→26,243`。officialのnode recallは`0.971830985915493`、total node ratioは`0.15333714858001288`である。4方式ともprediction manifestをGTを開く前に検証した。0c officialを基準にしたFinal Score差は、harmonic `+0.0637389825339513`、mutual `-0.0148189546224099`、motion `-0.0879326979190590`。

0dbのcandidate→selected edge数は、official `16,889→16,060`、harmonic `17,469→16,523`、mutual `16,111→15,474`、motion `14,966→14,606`。officialのnode recallは`1.0`、total node ratioは`0.19497880665145093`である。4方式ともprediction manifestをGTを開く前に検証した。harmonicのみDivision FPが`1`で、Division Jaccardは`0.0`になった。

development、0b、0cではDivision TP/FP/FNは`0/0/0`でdivision Jaccardは`null`だった。GTにdivisionがないため、公式summarizerはdivision termをdropした。0dbのharmonicだけはDivision TP/FP/FN=`0/1/0`を取得した。

developmentのofficial/harmonicのnode/edge数、Edge TP/FP/FN、Final Scoreは既存canonical Strong Baseline v1と一致した。最初の評価では孤立detector nodeをGEFFへ残したためFinalが低下したが、upstreamと同じく選択edgeに参加するnodeだけを保存するwriter修正後に一致した。0bは別sampleのためこの一致主張の対象外である。

prediction GEFFとmanifest/receipt:

- `artifacts/detector_fixed_race/dev_full_auto_compact_timed/44b6_0113de3b/official_ilp.geff`
- `artifacts/detector_fixed_race/dev_full_auto_compact_timed/44b6_0113de3b/harmonic_v1.geff`
- `artifacts/detector_fixed_race/dev_full_auto_compact_timed/44b6_0113de3b/mutual_confidence.geff`
- `artifacts/detector_fixed_race/dev_full_auto_compact_timed/44b6_0113de3b/motion_gated.geff`
- `artifacts/detector_fixed_race/dev_full_auto_compact_timed/44b6_0113de3b/race_receipt.json`
- GT: `artifacts/detector_fixed_race/panel_data/train/44b6_0b24845f.geff`（development GTは `/workspace/biohub-cell-tracking-during-development/data/train/44b6_0113de3b.geff`）
- `artifacts/detector_fixed_race/panel_runs/44b6_0b24845f/official_ilp.geff`
- `artifacts/detector_fixed_race/panel_runs_0b_harmonic/44b6_0b24845f/harmonic_v1.geff`
- `artifacts/detector_fixed_race/panel_runs_0b_mutual/44b6_0b24845f/mutual_confidence.geff`
- `artifacts/detector_fixed_race/panel_runs_0b_motion/44b6_0b24845f/motion_gated.geff`
- 0b各方式のreceipt/manifest/runtime: 各predictionディレクトリの `race_receipt.json`、`prediction_manifest.json`、`wall_time.txt`
- GT: `artifacts/detector_fixed_race/panel_data/train/44b6_0c582fdc.geff`
- `artifacts/detector_fixed_race/panel_runs_0c_official/44b6_0c582fdc/official_ilp.geff`
- `artifacts/detector_fixed_race/panel_runs_0c_harmonic/44b6_0c582fdc/harmonic_v1.geff`
- `artifacts/detector_fixed_race/panel_runs_0c_mutual/44b6_0c582fdc/mutual_confidence.geff`
- `artifacts/detector_fixed_race/panel_runs_0c_motion/44b6_0c582fdc/motion_gated.geff`
- 0c各方式のreceipt/manifest/runtime: 各predictionディレクトリの `race_receipt.json`、`prediction_manifest.json`、`wall_time.txt`
- GT: `artifacts/detector_fixed_race/panel_data/train/44b6_0db75fae.geff`
- `artifacts/detector_fixed_race/panel_runs_0db_official/44b6_0db75fae/official_ilp.geff`
- `artifacts/detector_fixed_race/panel_runs_0db_harmonic/44b6_0db75fae/harmonic_v1.geff`
- `artifacts/detector_fixed_race/panel_runs_0db_mutual/44b6_0db75fae/mutual_confidence.geff`
- `artifacts/detector_fixed_race/panel_runs_0db_motion/44b6_0db75fae/motion_gated.geff`
- 0db各方式のreceipt/manifest/runtime: 各predictionディレクトリの `race_receipt.json`、`prediction_manifest.json`、`wall_time.txt`

## 6. デバイス診断とGPU移行

今回のDocker実測値は `torch 2.13.0+cpu`、`torch.version.cuda=None`、`torch.cuda.is_available=False`、CUDA device count `0`、MPS built/available `False/False`、`nvidia-smi`なしである。development・0b・0c・0dbのmaterialize receiptも`requested_device=auto`、`device=cpu`を記録した。従ってautoがCPUを選んだ原因は環境であり、行列計算の実装不具合ではない。

NVIDIA環境では `docker-compose.nvidia.yml` と公式CUDA wheel indexを使い、`gpus: all`で起動する。source側の `--device auto` はCUDAを最優先する。MPS環境では同じautoがMPSを選ぶ。GEFF I/O、ILP/SCIP、公式metric、古典associationはCPU処理である。

## 7. 再現コマンド

```bash
docker compose exec -T biohub sh -lc 'cd /workspace/biohub-cell-tracking-during-development/scratch/strong-baseline-v1/biohub-cell-tracking-during-development && PYTHONPATH=/workspace/biohub-cell-tracking-during-development/scratch/strong-baseline-v1/biohub-cell-tracking-during-development/src uv run python scripts/run_detector_fixed_race.py materialize --sample 44b6_0113de3b --train-root /workspace/biohub-cell-tracking-during-development/data/train --upstream-root artifacts/strong_baseline_v1/upstream --checkpoint artifacts/strong_baseline_v1/inputs/cellmot-baseline-artifacts/weights/unet_transformer/split_0/edge_predictor_best.pth --output artifacts/detector_fixed_race/full_auto'

docker compose exec -T biohub sh -lc 'cd /workspace/biohub-cell-tracking-during-development/scratch/strong-baseline-v1/biohub-cell-tracking-during-development && PYTHONPATH=/workspace/biohub-cell-tracking-during-development/scratch/strong-baseline-v1/biohub-cell-tracking-during-development/src uv run python scripts/run_detector_fixed_race.py materialize --sample 44b6_0b24845f --train-root artifacts/detector_fixed_race/panel_data/train --upstream-root artifacts/strong_baseline_v1/upstream --checkpoint artifacts/strong_baseline_v1/inputs/cellmot-baseline-artifacts/weights/unet_transformer/split_0/edge_predictor_best.pth --output artifacts/detector_fixed_race/panel_auto'
docker compose exec -T biohub sh -lc 'cd /workspace/biohub-cell-tracking-during-development/scratch/strong-baseline-v1/biohub-cell-tracking-during-development && PYTHONPATH=/workspace/biohub-cell-tracking-during-development/scratch/strong-baseline-v1/biohub-cell-tracking-during-development/src uv run python scripts/build_detector_cache_mmap.py artifacts/detector_fixed_race/panel_auto/cache/44b6_0b24845f'
docker compose exec -T biohub sh -lc 'cd /workspace/biohub-cell-tracking-during-development/scratch/strong-baseline-v1/biohub-cell-tracking-during-development && PYTHONPATH=/workspace/biohub-cell-tracking-during-development/scratch/strong-baseline-v1/biohub-cell-tracking-during-development/src uv run python scripts/run_detector_fixed_race.py dev-race --sample 44b6_0b24845f --cache artifacts/detector_fixed_race/panel_auto/cache/44b6_0b24845f --output artifacts/detector_fixed_race/panel_runs --ground-truth artifacts/detector_fixed_race/panel_data/train/44b6_0b24845f.geff --upstream-root artifacts/strong_baseline_v1/upstream --methods official_ilp'
docker compose exec -T biohub sh -lc 'cd /workspace/biohub-cell-tracking-during-development/scratch/strong-baseline-v1/biohub-cell-tracking-during-development && PYTHONPATH=/workspace/biohub-cell-tracking-during-development/scratch/strong-baseline-v1/biohub-cell-tracking-during-development/src uv run python scripts/run_detector_fixed_race.py dev-race --sample 44b6_0b24845f --cache artifacts/detector_fixed_race/panel_auto/cache/44b6_0b24845f --output artifacts/detector_fixed_race/panel_runs_0b_harmonic --ground-truth artifacts/detector_fixed_race/panel_data/train/44b6_0b24845f.geff --upstream-root artifacts/strong_baseline_v1/upstream --methods harmonic_v1'
docker compose exec -T biohub sh -lc 'cd /workspace/biohub-cell-tracking-during-development/scratch/strong-baseline-v1/biohub-cell-tracking-during-development && PYTHONPATH=/workspace/biohub-cell-tracking-during-development/scratch/strong-baseline-v1/biohub-cell-tracking-during-development/src uv run python scripts/run_detector_fixed_race.py dev-race --sample 44b6_0b24845f --cache artifacts/detector_fixed_race/panel_auto/cache/44b6_0b24845f --output artifacts/detector_fixed_race/panel_runs_0b_mutual --ground-truth artifacts/detector_fixed_race/panel_data/train/44b6_0b24845f.geff --upstream-root artifacts/strong_baseline_v1/upstream --methods mutual_confidence'
docker compose exec -T biohub sh -lc 'cd /workspace/biohub-cell-tracking-during-development/scratch/strong-baseline-v1/biohub-cell-tracking-during-development && PYTHONPATH=/workspace/biohub-cell-tracking-during-development/scratch/strong-baseline-v1/biohub-cell-tracking-during-development/src uv run python scripts/run_detector_fixed_race.py dev-race --sample 44b6_0b24845f --cache artifacts/detector_fixed_race/panel_auto/cache/44b6_0b24845f --output artifacts/detector_fixed_race/panel_runs_0b_motion --ground-truth artifacts/detector_fixed_race/panel_data/train/44b6_0b24845f.geff --upstream-root artifacts/strong_baseline_v1/upstream --methods motion_gated'

docker compose exec -T biohub sh -lc 'cd /workspace/biohub-cell-tracking-during-development/scratch/strong-baseline-v1/biohub-cell-tracking-during-development && PYTHONPATH=/workspace/biohub-cell-tracking-during-development/scratch/strong-baseline-v1/biohub-cell-tracking-during-development/src uv run python scripts/run_detector_fixed_race.py materialize --sample 44b6_0c582fdc --train-root artifacts/detector_fixed_race/panel_data/train --upstream-root artifacts/strong_baseline_v1/upstream --checkpoint artifacts/strong_baseline_v1/inputs/cellmot-baseline-artifacts/weights/unet_transformer/split_0/edge_predictor_best.pth --output artifacts/detector_fixed_race/panel_auto --device auto'
docker compose exec -T biohub sh -lc 'cd /workspace/biohub-cell-tracking-during-development/scratch/strong-baseline-v1/biohub-cell-tracking-during-development && PYTHONPATH=/workspace/biohub-cell-tracking-during-development/scratch/strong-baseline-v1/biohub-cell-tracking-during-development/src uv run python scripts/build_detector_cache_mmap.py artifacts/detector_fixed_race/panel_auto/cache/44b6_0c582fdc'
docker compose exec -T biohub sh -lc 'cd /workspace/biohub-cell-tracking-during-development/scratch/strong-baseline-v1/biohub-cell-tracking-during-development && PYTHONPATH=/workspace/biohub-cell-tracking-during-development/scratch/strong-baseline-v1/biohub-cell-tracking-during-development/src uv run python scripts/run_detector_fixed_race.py dev-race --sample 44b6_0c582fdc --cache artifacts/detector_fixed_race/panel_auto/cache/44b6_0c582fdc --output artifacts/detector_fixed_race/panel_runs_0c_official --ground-truth artifacts/detector_fixed_race/panel_data/train/44b6_0c582fdc.geff --upstream-root artifacts/strong_baseline_v1/upstream --methods official_ilp'
docker compose exec -T biohub sh -lc 'cd /workspace/biohub-cell-tracking-during-development/scratch/strong-baseline-v1/biohub-cell-tracking-during-development && PYTHONPATH=/workspace/biohub-cell-tracking-during-development/scratch/strong-baseline-v1/biohub-cell-tracking-during-development/src uv run python scripts/run_detector_fixed_race.py dev-race --sample 44b6_0c582fdc --cache artifacts/detector_fixed_race/panel_auto/cache/44b6_0c582fdc --output artifacts/detector_fixed_race/panel_runs_0c_harmonic --ground-truth artifacts/detector_fixed_race/panel_data/train/44b6_0c582fdc.geff --upstream-root artifacts/strong_baseline_v1/upstream --methods harmonic_v1'
docker compose exec -T biohub sh -lc 'cd /workspace/biohub-cell-tracking-during-development/scratch/strong-baseline-v1/biohub-cell-tracking-during-development && PYTHONPATH=/workspace/biohub-cell-tracking-during-development/scratch/strong-baseline-v1/biohub-cell-tracking-during-development/src uv run python scripts/run_detector_fixed_race.py dev-race --sample 44b6_0c582fdc --cache artifacts/detector_fixed_race/panel_auto/cache/44b6_0c582fdc --output artifacts/detector_fixed_race/panel_runs_0c_mutual --ground-truth artifacts/detector_fixed_race/panel_data/train/44b6_0c582fdc.geff --upstream-root artifacts/strong_baseline_v1/upstream --methods mutual_confidence'
docker compose exec -T biohub sh -lc 'cd /workspace/biohub-cell-tracking-during-development/scratch/strong-baseline-v1/biohub-cell-tracking-during-development && PYTHONPATH=/workspace/biohub-cell-tracking-during-development/scratch/strong-baseline-v1/biohub-cell-tracking-during-development/src uv run python scripts/run_detector_fixed_race.py dev-race --sample 44b6_0c582fdc --cache artifacts/detector_fixed_race/panel_auto/cache/44b6_0c582fdc --output artifacts/detector_fixed_race/panel_runs_0c_motion --ground-truth artifacts/detector_fixed_race/panel_data/train/44b6_0c582fdc.geff --upstream-root artifacts/strong_baseline_v1/upstream --methods motion_gated'
```

harmonic weight sweepは、上記の`--cache`を固定し、出力rootをvariantごとに変えて次の引数を追加する。`--harmonic-reverse-weight 0.10`（または`0.20`、`0.30`）を指定すると、prediction名は`harmonic_v1_rw_0p10.geff`等になる。

```bash
docker compose exec -T biohub sh -lc 'cd /workspace/biohub-cell-tracking-during-development/scratch/strong-baseline-v1/biohub-cell-tracking-during-development && PYTHONPATH=/workspace/biohub-cell-tracking-during-development/scratch/strong-baseline-v1/biohub-cell-tracking-during-development/src uv run python scripts/run_detector_fixed_race.py dev-race --sample 44b6_0c582fdc --cache artifacts/detector_fixed_race/panel_auto/cache/44b6_0c582fdc --output artifacts/detector_fixed_race/harmonic_sweep/44b6_0c582fdc_rw_0p10 --ground-truth artifacts/detector_fixed_race/panel_data/train/44b6_0c582fdc.geff --upstream-root artifacts/strong_baseline_v1/upstream --methods harmonic_v1 --harmonic-reverse-weight 0.10'
```

## 8. 既知の問題

- 現行MacBook DockerはCPU-onlyであり、GPU実測値はまだない。
- node featureはwindow context依存のため、cacheのcanonical featureは最初の観測である。association比較はpair logitsを使用する。
- panel画像・GTは取得済み。development・0b・0c・0dbのdetector cacheと4方式公式metricは完了した。divisionを含む12dfb391は実行中で、prediction GEFF・公式metric・scoreは未取得である。
- Kaggleへの外部submissionは行わない。

## 9. panel実験の完了状況

`44b6_0c582fdc`は0bと同じGT-free detector materializeを完了し、cache hash `2bd90bee3abf0afb07abdc971bfb45235a33bb931feaf6bfb3b884759682f748`、nodes `34,910`、candidate edges `12,459,009`、detector elapsed `5,447.649957480986 s`、`auto/cpu`を記録した。sidecar作成後、official/harmonic/mutual/motionのprediction GEFFと公式metricを取得した。0dbも同じ手順で完走し、development＋0b＋0c＋0dbの4 sampleで4方式比較が完了した。divisionを含む12dfb391は実行中で、scoreは未取得である。

## 10. Harmonic reverse weight性能実験

detectorを再計算せず、同じGT-free cache・同じILP・同じ公式metricで`harmonic_v1`の`reverse_weight`だけを`0.10 / 0.20 / 0.30`へ変更した。各runは別プロセス・別output rootで実行し、0bの45M候補edgeでもOOMを再発させていない。`0.20`は既存harmonic v1の再現値である。CLI引数とvariant命名は`f405c00`で追加し、全pytest `174 passed, 2 warnings`、対象Ruff `All checks passed!`を確認した。

| sample | weight | Final Score | Edge TP/FP/FN | wall time [s] |
|---|---:|---:|---:|---:|
| `44b6_0113de3b` | `0.10` | `0.9212347117064648` | `48/2/2` | `33.001343` |
| `44b6_0113de3b` | `0.20` | `0.9211200215044129` | `48/2/2` | `31.805268` |
| `44b6_0113de3b` | `0.30` | `0.9210734286098294` | `48/2/2` | `32.610888` |
| `44b6_0b24845f` | `0.10` | `0.627410648067993` | `40/10/9` | `170.736890` |
| `44b6_0b24845f` | `0.20` | `0.6274705993317501` | `40/10/9` | `166.122310` |
| `44b6_0b24845f` | `0.30` | `0.622819815888671` | `39/9/10` | `172.885813` |
| `44b6_0c582fdc` | `0.10` | `0.8023116441579663` | `62/6/8` | `60.830931` |
| `44b6_0c582fdc` | `0.20` | `0.8022386963904503` | `62/6/8` | `58.467485` |
| `44b6_0c582fdc` | `0.30` | `0.7893912297016954` | `61/6/9` | `58.315125` |

| weight | 3 sample平均 Final Score | 0.20との差 |
|---:|---:|---:|
| `0.10` | `0.7836523346441413` | `+0.0000425622352703` |
| `0.20` | `0.7836097724088710` | `+0` |
| `0.30` | `0.7777614914000653` | `-0.0058482810088057` |

`0.10`が3 sample平均では最大だが、改善幅は`4.26e-5`と小さく、0bでは`0.20`が僅かに上回る。したがって公開Strong Baseline v1の既定値は`0.20`のまま保持し、`0.10`を追加性能候補としてdivision sample検証待ちにする。全variantのprediction GEFF・receipt・wall timeは `artifacts/detector_fixed_race/harmonic_sweep/` 以下に保存した。

## 11. 追加panel `44b6_0db75fae` 完了結果（2026-08-21）

0dbは画像とGTを固定したうえで、GT-free detector cacheのmaterialize、edge sidecar化、4方式の個別再生、prediction manifest検証、GTを評価時だけ開く公式metricまで完走した。GT GEFFは `artifacts/detector_fixed_race/panel_data/train/44b6_0db75fae.geff` である。

cacheは `artifacts/detector_fixed_race/panel_auto/cache/44b6_0db75fae/` に保存した。cache hashは `bdaa6c60fd1ccc14abe0bcc0fde1a0efe8330692e10b9926e898c909ee89a3e9`、nodes `19,599`、candidate edges `4,346,571`、detector calls `100`、forward/reverse edge calls `99/99`、detector elapsed `4,839.955556327011 s`、requested/actual deviceは `auto/cpu` である。feature conflict observationsは `19,233`、checkpoint SHA-256は `347915de9c33883cb2ee69832a8e4552c88b1ec692d0fbfe956422467d3d4235`、upstream source commitは `075fc5f5a52d11077f9dc2b074644618f26939e2`、image SHA-256は `c16d44a2dc0b08ab6dd47401c5bf6b9e6e52ebcb5b638decee88dcdc0203eb73`、adapter SHA-256は `e914af35a2b68f2509027429efaa6ab29670be822212ae7c8628985f42a4ac72` である。manifestは `ground_truth_included=false` を記録する。

sidecarは `artifacts/detector_fixed_race/panel_auto/cache/44b6_0db75fae/candidate_edges.mmap/`、schemaは `detector_fixed.cache_mmap.v1`、`source_cache_hash`はcanonical manifestのcache hashと一致する。`du`はcache root全体 `404M`、そのうちsidecar `274M`、canonical NPZ等は約`130M`だった。materializeと4方式の再生でcgroup `oom_kill=7` は開始前から増加せず、追加OOM killはなかった。

| 手法 | candidate→selected | prediction nodes / edges | Edge TP/FP/FN | Division TP/FP/FN | Edge Jaccard | Adjusted / Final | Division Jaccard | node recall / total node ratio | wall time [s] |
|---|---:|---:|---|---|---:|---:|---:|---:|---:|
| `official_ilp` | `16,889→16,060` | `18,325 / 16,060` | `133/9/18` | `0/0/0` | `0.83125` | `0.8150423866970982 / 0.8150423866970982` | `null` | `1.0 / 0.19497880665145093` | `22.204889` |
| `harmonic_v1` | `17,469→16,523` | `18,576 / 16,523` | `134/8/17` | `0/1/0` | `0.8427672955974843` | `0.8249556959559359 / 0.8249556959559359` | `0.0` | `1.0 / 0.21134659276165635` | `21.593063` |
| `mutual_confidence` | `16,111→15,474` | `18,124 / 15,474` | `124/4/27` | `0/0/0` | `0.8` | `0.7854502771437888 / 0.7854502771437888` | `null` | `0.9872611464968153 / 0.18187153570264103` | `22.003911` |
| `motion_gated` | `14,966→14,606` | `17,496 / 14,606` | `125/4/26` | `0/0/0` | `0.8064516129032258` | `0.795087139897136 / 0.795087139897136` | `null` | `0.9745222929936306 / 0.14091946527551352` | `19.994326` |

harmonicはofficialよりEdge TPが1件増、FPが1件減、FNが1件減った一方、division FPを1件生成した。4方式ともreturn code `0`で、prediction manifestはGTを開く前に検証済みである。prediction GEFF、prediction manifest、receipt、wall time、GTは次の場所にある。

- official: `artifacts/detector_fixed_race/panel_runs_0db_official/44b6_0db75fae/official_ilp.geff`、同ディレクトリの `prediction_manifest.json`、`race_receipt.json`、`wall_time.txt`
- harmonic: `artifacts/detector_fixed_race/panel_runs_0db_harmonic/44b6_0db75fae/harmonic_v1.geff`、同ディレクトリの `prediction_manifest.json`、`race_receipt.json`、`wall_time.txt`
- mutual: `artifacts/detector_fixed_race/panel_runs_0db_mutual/44b6_0db75fae/mutual_confidence.geff`、同ディレクトリの `prediction_manifest.json`、`race_receipt.json`、`wall_time.txt`
- motion: `artifacts/detector_fixed_race/panel_runs_0db_motion/44b6_0db75fae/motion_gated.geff`、同ディレクトリの `prediction_manifest.json`、`race_receipt.json`、`wall_time.txt`
- GT: `artifacts/detector_fixed_race/panel_data/train/44b6_0db75fae.geff`

## 12. 独立 blob NMS 比較（detector-fixed lane ではない）

これは同一detector出力を固定したassociation raceとは別の、独立したblob NMS実験であり、detector-fixed lane ではない。NMSの半径変更だけを比較し、結果は次のとおりである。

この比較では、metrics.jsonを公式metric receiptとして扱う。DeltaはNMS 3.5の`final_score`からNMS 3.0の`final_score`を引いた差分である。

| variant | official metric receipt | field/value |
|---|---|---|
| NMS 3.0 | `artifacts/multi_method_race/evaluation/blob_lap/metrics.json` | `final_score=0.9140773262846648` |
| NMS 3.5 | `artifacts/performance_experiments/blob_lap_nms35/metrics.json` | `final_score=0.9172062183593925` |
| Delta (NMS 3.5 - NMS 3.0) | NMS 3.5 - NMS 3.0 | `+0.0031288920747277` |

測定値のartifactは `artifacts/performance_experiments/blob_lap_nms35/metrics.json` に保存されている。

## 13. detector-fixed 4 sample集約（0db追加後）

development、0b、0cの3 sample平均と、0dbを追加した4 sample平均を、各sampleの `race_receipt.json` の `metrics.final_score` から再集計した。12dfb391は実行中で、prediction GEFF・公式metric・scoreは未取得であるため、平均には含めていない。

| association | 3 sample平均 Final Score | 4 sample平均 Final Score | 4 sampleでのofficial差 |
|---|---:|---:|---:|
| `official_ilp` | `0.7495051838525356` | `0.7658894845636763` | `+0` |
| `harmonic_v1` | `0.783609772408871` | `0.7939462532956372` | `+0.0280567687319609` |
| `mutual_confidence` | `0.7309627429754736` | `0.7445846265175524` | `-0.0213048580461239` |
| `motion_gated` | `0.6805299883649374` | `0.7091692762479871` | `-0.0567202083156892` |

0dbを加えても、harmonicは4 sampleすべてでofficialを上回った。divisionを含む12dfb391の結果はまだ未取得であり、この傾向をdivision性能へ外挿しない。

## 14. validation receipt のfresh re-reviewと4 sample smoke

validation receipt実装の対象commitは `fbfbf26` で、fresh re-reviewは `APPROVED` だった。4 sampleの実artifact集約smokeでは、legacyなdevelopment旧4方式runが単一出力ディレクトリを共有し、最後の方式が `prediction_manifest.json` を上書きしていたため、集約器は `prediction_path mismatch for ('44b6_0113de3b', 'official_ilp')` を返して不整合を正しく拒否した。

detector cacheの再計算は不要であり、developmentの4方式を別output directoryへ個別再生して、方式ごとの `prediction_manifest.json` を再生成する必要がある。12dfb391は現時点で「進行中、未取得」である。
