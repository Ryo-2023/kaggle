# Detector-Fixed Association Race — Strong Baseline v1

作成日: 2026-08-21（JST）  
更新日: 2026-08-22（JST）
ブランチ: `codex/biohub-multi-method-race`

## 0. 結論

同一detector出力を固定したassociation比較として、development・0b・0c・0db・divisionを含む12dfb391の5 sampleで4方式の公式metricを取得した。GT-free実行契約は `ground_truth_included=false` で、cache生成・candidate生成・association選択ではGTを開かず、公式metric評価時だけ参照する。machine-readableな選定・実行成果物は `artifacts/detector_fixed_race/panel.json`、`artifacts/detector_fixed_race/validation_receipt.json`、各runの `race_receipt.json`、`prediction_manifest.json` を参照する。`validation_receipt.json` の `failed_samples=[]` を確認済みである。

5 sample平均Final Scoreは official `0.7688958987642377`、harmonic `0.7944143977140719`、mutual `0.7467735686449968`、motion `0.7187007022873142`。harmonicはofficialを `+0.025518498949834156` 上回り、5 sample中 `5/5` で勝った。

## 1. 目的と比較条件

同一の公式 detector 出力を固定し、association だけを交換して比較する。画像、cell-center、node feature、forward/reverse raw edge logitsは一度だけ取得し、後段はcacheのみを読む。GTはcache生成・candidate生成・association選択に渡さず、公式metric評価時だけ開く。

比較対象の4方式（development・0b・0c・0db・12dfb391の全5 sampleで完了）:

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
| `44b6_12dfb391` | `(100,64,256,256)` | `788 / 773` | `1` | 完了（division sample） |

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

12df sample `44b6_12dfb391` の全100フレームcache、`READY`、candidate edge sidecar、4方式の個別再生、prediction manifest検証、divisionを含む公式metric評価も完了した。GT pathは `artifacts/detector_fixed_race/panel_data/train/44b6_12dfb391.geff` で、cache生成・candidate生成・associationには渡していない。

| 項目 | 値 |
|---|---:|
| cache manifest | `artifacts/detector_fixed_race/panel_auto/cache/44b6_12dfb391/manifest.json` |
| `READY` | cache hash `3fefd2f62dba07f0e2c7266a3fa7b0ee97f9a3ff6bb652598c35622bdfc75a40` + 改行 |
| cache hash | `3fefd2f62dba07f0e2c7266a3fa7b0ee97f9a3ff6bb652598c35622bdfc75a40` |
| nodes / candidate edges | `62,219 / 38,940,536` |
| detector / forward / reverse calls | `100 / 99 / 99` |
| feature conflict observations | `60,980` |
| detector elapsed | `5,878.221322058991 s` |
| requested / actual device | `auto / cpu` |
| image SHA-256 | `94622f407ef6959ee4be8c126174216bee404fb1a26cf310f840377f41bbbc81` |
| checkpoint SHA-256 | `347915de9c33883cb2ee69832a8e4552c88b1ec692d0fbfe956422467d3d4235` |
| source repository / commit | `https://github.com/royerlab/kaggle-cell-tracking-competition.git` / `075fc5f5a52d11077f9dc2b074644618f26939e2` |
| adapter source SHA-256 | `e914af35a2b68f2509027429efaa6ab29670be822212ae7c8628985f42a4ac72` |
| GT-free manifest | `ground_truth_included=false` |
| sidecar | `artifacts/detector_fixed_race/panel_auto/cache/44b6_12dfb391/candidate_edges.mmap/` |
| sidecar schema / source cache hash | `detector_fixed.cache_mmap.v1` / canonical cache hashと一致 |
| cache root / sidecar `du` | 約`3.4 GiB / 2.4 GiB` |

12dfのsidecar manifestは `candidate_edges.mmap/manifest.json` にあり、edge count `38,940,536`、source cache hash `3fefd2f62dba07f0e2c7266a3fa7b0ee97f9a3ff6bb652598c35622bdfc75a40`、forward/reverse logit・probability、`delta_t`、voxel/physical delta・distanceの全列を記録する。4方式の `wall_time.txt` は official `162.105704 s`、harmonic `147.6 s`、mutual `164.1 s`、motion `149.6 s` で、すべて `returncode=0` だった。OOMはreceipt項目ではなく外部cgroup監視で確認し、`oom_kill=7`は開始前から増加しなかった。

## 5. 公式metric結果

development、0b、0c、0db、12dfb391のcache生成後に、prediction GEFFと公式metric receiptを取得した。全20個別runの数値は各receiptとmanifestから転記し、推測値は補っていない。

| sample / method | prediction GEFF | nodes / edges | candidate → selected | Edge TP/FP/FN | Division TP/FP/FN | Edge Jaccard | Adjusted Edge Jaccard | Division Jaccard | Final Score | runtime |
|---|---|---:|---:|---|---|---:|---:|---:|---:|---:|
| `44b6_0113de3b` / official | `panel_runs_dev_official/44b6_0113de3b/official_ilp.geff` | `25,994 / 23,536` | `24,183 → 23,536` | `46/2/4` | `0/0/0` | `0.8846153846153846` | `0.8837944835207503` | `null` | `0.8837944835207503` | 27.7 s |
| `44b6_0113de3b` / harmonic | `panel_runs_dev_harmonic/44b6_0113de3b/harmonic_v1.geff` | `26,301 / 24,205` | `25,023 → 24,205` | `48/2/2` | `0/0/0` | `0.9230769230769231` | `0.9211200215044129` | `null` | `0.9211200215044129` | 28.7 s |
| `44b6_0113de3b` / mutual | `panel_runs_dev_mutual/44b6_0113de3b/mutual_confidence.geff` | `25,806 / 22,727` | `23,257 → 22,727` | `43/0/7` | `0/0/0` | `0.86` | `0.859829702970297` | `null` | `0.859829702970297` | 26.3 s |
| `44b6_0113de3b` / motion | `panel_runs_dev_motion/44b6_0113de3b/motion_gated.geff` | `25,143 / 21,799` | `22,032 → 21,799` | `42/2/8` | `0/0/0` | `0.8076923076923077` | `0.8096115765422697` | `null` | `0.8096115765422697` | 25.7 s |
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
| `44b6_12dfb391` / official | `panel_runs_12df_official/44b6_12dfb391/official_ilp.geff` | `59,632 / 54,744` | `56,455 → 54,744` | `668/81/105` | `0/0/1` | `0.7822014051522248` | `0.7809215555664836` | `0.0` | `0.7809215555664836` | 162.105704 s |
| `44b6_12dfb391` / harmonic | `panel_runs_12df_harmonic/44b6_12dfb391/harmonic_v1.geff` | `60,037 / 55,707` | `57,654 → 55,707` | `688/89/85` | `0/3/1` | `0.7981438515081206` | `0.7962869753878102` | `0.0` | `0.7962869753878102` | 147.6 s |
| `44b6_12dfb391` / mutual | `panel_runs_12df_mutual/44b6_12dfb391/mutual_confidence.geff` | `59,135 / 52,882` | `54,181 → 52,882` | `648/84/125` | `0/0/1` | `0.7561260210035006` | `0.7555293371547744` | `0.0` | `0.7555293371547744` | 164.1 s |
| `44b6_12dfb391` / motion | `panel_runs_12df_motion/44b6_12dfb391/motion_gated.geff` | `58,618 / 52,214` | `53,093 → 52,214` | `644/78/129` | `0/0/1` | `0.7567567567567568` | `0.7568264064446228` | `0.0` | `0.7568264064446228` | 149.6 s |

developmentのruntimeは各個別再生ディレクトリの`wall_time.txt`、0b以降のruntimeも同じく方式別receiptのwall timeから転記した。developmentの公式baselineとの差は、official `+0`、harmonic `+0.0373255379836626`、mutual `-0.0239647805504533`、motion `-0.0741829069784806`。0bのofficialとの差は、harmonic `+0.0012492451513925`、mutual `-0.0168435874583230`、motion `-0.0448099815652553`。

0bのcandidate→selected edge数は、official `48,068→44,335`、harmonic `51,874→47,043`、mutual `43,362→40,639`、motion `39,345→37,723`。officialのnode recallは`0.9803921568627451`、total node ratioは`0.6869644762921177`である。4方式ともprediction manifestをGTを開く前に検証した。

0cのcandidate→selected edge数は、official `30,140→28,388`、harmonic `31,164→29,176`、mutual `28,526→27,174`、motion `27,154→26,243`。officialのnode recallは`0.971830985915493`、total node ratioは`0.15333714858001288`である。4方式ともprediction manifestをGTを開く前に検証した。0c officialを基準にしたFinal Score差は、harmonic `+0.0637389825339513`、mutual `-0.0148189546224099`、motion `-0.0879326979190590`。

0dbのcandidate→selected edge数は、official `16,889→16,060`、harmonic `17,469→16,523`、mutual `16,111→15,474`、motion `14,966→14,606`。officialのnode recallは`1.0`、total node ratioは`0.19497880665145093`である。4方式ともprediction manifestをGTを開く前に検証した。harmonicのみDivision FPが`1`で、Division Jaccardは`0.0`になった。

development、0b、0cではDivision TP/FP/FNは`0/0/0`でdivision Jaccardは`null`だった。GTにdivisionがないため、公式summarizerはdivision termをdropした。0dbのharmonicだけはDivision TP/FP/FN=`0/1/0`を取得した。

12dfでは全方式がDivision TP/FN=`0/1`で、harmonicだけDivision FP=`3`を生成した。harmonicのFinal Scoreはofficialより`+0.0153654198213266`高いが、divisionを1件も真陽性にできておらず、この部分は未解決である。

developmentのofficial/harmonicのnode/edge数、Edge TP/FP/FN、Final Scoreは既存canonical Strong Baseline v1と一致した。最初の評価では孤立detector nodeをGEFFへ残したためFinalが低下したが、upstreamと同じく選択edgeに参加するnodeだけを保存するwriter修正後に一致した。0bは別sampleのためこの一致主張の対象外である。

prediction GEFFとmanifest/receipt:

- `artifacts/detector_fixed_race/panel_runs_dev_official/44b6_0113de3b/official_ilp.geff`
- `artifacts/detector_fixed_race/panel_runs_dev_harmonic/44b6_0113de3b/harmonic_v1.geff`
- `artifacts/detector_fixed_race/panel_runs_dev_mutual/44b6_0113de3b/mutual_confidence.geff`
- `artifacts/detector_fixed_race/panel_runs_dev_motion/44b6_0113de3b/motion_gated.geff`
- development各方式の `race_receipt.json`、`prediction_manifest.json`、`wall_time.txt`: `artifacts/detector_fixed_race/panel_runs_dev_{official,harmonic,mutual,motion}/44b6_0113de3b/`
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
- GT: `artifacts/detector_fixed_race/panel_data/train/44b6_12dfb391.geff`
- `artifacts/detector_fixed_race/panel_runs_12df_official/44b6_12dfb391/official_ilp.geff`
- `artifacts/detector_fixed_race/panel_runs_12df_harmonic/44b6_12dfb391/harmonic_v1.geff`
- `artifacts/detector_fixed_race/panel_runs_12df_mutual/44b6_12dfb391/mutual_confidence.geff`
- `artifacts/detector_fixed_race/panel_runs_12df_motion/44b6_12dfb391/motion_gated.geff`
- 12df各方式のreceipt/manifest/runtime: 各predictionディレクトリの `race_receipt.json`、`prediction_manifest.json`、`wall_time.txt`

## 6. デバイス診断とGPU移行

今回のDocker実測値は `torch 2.13.0+cpu`、`torch.version.cuda=None`、`torch.cuda.is_available=False`、CUDA device count `0`、MPS built/available `False/False`、`nvidia-smi`なしである。development・0b・0c・0db・12dfの全materialize receiptも`requested_device=auto`、`device=cpu`を記録した。従ってautoがCPUを選んだ原因は環境であり、行列計算の実装不具合ではない。

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

12dfb391の完了runは次のmaterialize、sidecar、4方式個別再生で再現できる。

```bash
docker compose exec -T biohub sh -lc 'cd /workspace/biohub-cell-tracking-during-development/scratch/strong-baseline-v1/biohub-cell-tracking-during-development && PYTHONPATH=/workspace/biohub-cell-tracking-during-development/scratch/strong-baseline-v1/biohub-cell-tracking-during-development/src uv run python scripts/run_detector_fixed_race.py materialize --sample 44b6_12dfb391 --train-root artifacts/detector_fixed_race/panel_data/train --upstream-root artifacts/strong_baseline_v1/upstream --checkpoint artifacts/strong_baseline_v1/inputs/cellmot-baseline-artifacts/weights/unet_transformer/split_0/edge_predictor_best.pth --output artifacts/detector_fixed_race/panel_auto --device auto'
docker compose exec -T biohub sh -lc 'cd /workspace/biohub-cell-tracking-during-development/scratch/strong-baseline-v1/biohub-cell-tracking-during-development && PYTHONPATH=/workspace/biohub-cell-tracking-during-development/scratch/strong-baseline-v1/biohub-cell-tracking-during-development/src uv run python scripts/build_detector_cache_mmap.py artifacts/detector_fixed_race/panel_auto/cache/44b6_12dfb391'
docker compose exec -T biohub sh -lc 'cd /workspace/biohub-cell-tracking-during-development/scratch/strong-baseline-v1/biohub-cell-tracking-during-development && PYTHONPATH=/workspace/biohub-cell-tracking-during-development/scratch/strong-baseline-v1/biohub-cell-tracking-during-development/src uv run python scripts/run_detector_fixed_race.py dev-race --sample 44b6_12dfb391 --cache artifacts/detector_fixed_race/panel_auto/cache/44b6_12dfb391 --output artifacts/detector_fixed_race/panel_runs_12df_official --ground-truth artifacts/detector_fixed_race/panel_data/train/44b6_12dfb391.geff --upstream-root artifacts/strong_baseline_v1/upstream --methods official_ilp'
docker compose exec -T biohub sh -lc 'cd /workspace/biohub-cell-tracking-during-development/scratch/strong-baseline-v1/biohub-cell-tracking-during-development && PYTHONPATH=/workspace/biohub-cell-tracking-during-development/scratch/strong-baseline-v1/biohub-cell-tracking-during-development/src uv run python scripts/run_detector_fixed_race.py dev-race --sample 44b6_12dfb391 --cache artifacts/detector_fixed_race/panel_auto/cache/44b6_12dfb391 --output artifacts/detector_fixed_race/panel_runs_12df_harmonic --ground-truth artifacts/detector_fixed_race/panel_data/train/44b6_12dfb391.geff --upstream-root artifacts/strong_baseline_v1/upstream --methods harmonic_v1'
docker compose exec -T biohub sh -lc 'cd /workspace/biohub-cell-tracking-during-development/scratch/strong-baseline-v1/biohub-cell-tracking-during-development && PYTHONPATH=/workspace/biohub-cell-tracking-during-development/scratch/strong-baseline-v1/biohub-cell-tracking-during-development/src uv run python scripts/run_detector_fixed_race.py dev-race --sample 44b6_12dfb391 --cache artifacts/detector_fixed_race/panel_auto/cache/44b6_12dfb391 --output artifacts/detector_fixed_race/panel_runs_12df_mutual --ground-truth artifacts/detector_fixed_race/panel_data/train/44b6_12dfb391.geff --upstream-root artifacts/strong_baseline_v1/upstream --methods mutual_confidence'
docker compose exec -T biohub sh -lc 'cd /workspace/biohub-cell-tracking-during-development/scratch/strong-baseline-v1/biohub-cell-tracking-during-development && PYTHONPATH=/workspace/biohub-cell-tracking-during-development/scratch/strong-baseline-v1/biohub-cell-tracking-during-development/src uv run python scripts/run_detector_fixed_race.py dev-race --sample 44b6_12dfb391 --cache artifacts/detector_fixed_race/panel_auto/cache/44b6_12dfb391 --output artifacts/detector_fixed_race/panel_runs_12df_motion --ground-truth artifacts/detector_fixed_race/panel_data/train/44b6_12dfb391.geff --upstream-root artifacts/strong_baseline_v1/upstream --methods motion_gated'
```

developmentのlegacy manifestは、同じcacheから方式別outputへ個別再生して解消した。再集約は、既存receiptを再利用して次の形で行う（receipt引数は5 sample×4方式を指定する）。

```bash
cd /workspace/biohub-cell-tracking-during-development/scratch/strong-baseline-v1/biohub-cell-tracking-during-development
PYTHONPATH=/workspace/biohub-cell-tracking-during-development/scratch/strong-baseline-v1/biohub-cell-tracking-during-development/src uv run python scripts/run_detector_fixed_race.py aggregate-panel-receipts --panel artifacts/detector_fixed_race/panel.json --evidence-root . \
  --receipt artifacts/detector_fixed_race/panel_runs_dev_official/44b6_0113de3b/race_receipt.json --receipt artifacts/detector_fixed_race/panel_runs_dev_harmonic/44b6_0113de3b/race_receipt.json --receipt artifacts/detector_fixed_race/panel_runs_dev_mutual/44b6_0113de3b/race_receipt.json --receipt artifacts/detector_fixed_race/panel_runs_dev_motion/44b6_0113de3b/race_receipt.json \
  --receipt artifacts/detector_fixed_race/panel_runs/44b6_0b24845f/race_receipt.json --receipt artifacts/detector_fixed_race/panel_runs_0b_harmonic/44b6_0b24845f/race_receipt.json --receipt artifacts/detector_fixed_race/panel_runs_0b_mutual/44b6_0b24845f/race_receipt.json --receipt artifacts/detector_fixed_race/panel_runs_0b_motion/44b6_0b24845f/race_receipt.json \
  --receipt artifacts/detector_fixed_race/panel_runs_0c_official/44b6_0c582fdc/race_receipt.json --receipt artifacts/detector_fixed_race/panel_runs_0c_harmonic/44b6_0c582fdc/race_receipt.json --receipt artifacts/detector_fixed_race/panel_runs_0c_mutual/44b6_0c582fdc/race_receipt.json --receipt artifacts/detector_fixed_race/panel_runs_0c_motion/44b6_0c582fdc/race_receipt.json \
  --receipt artifacts/detector_fixed_race/panel_runs_0db_official/44b6_0db75fae/race_receipt.json --receipt artifacts/detector_fixed_race/panel_runs_0db_harmonic/44b6_0db75fae/race_receipt.json --receipt artifacts/detector_fixed_race/panel_runs_0db_mutual/44b6_0db75fae/race_receipt.json --receipt artifacts/detector_fixed_race/panel_runs_0db_motion/44b6_0db75fae/race_receipt.json \
  --receipt artifacts/detector_fixed_race/panel_runs_12df_official/44b6_12dfb391/race_receipt.json --receipt artifacts/detector_fixed_race/panel_runs_12df_harmonic/44b6_12dfb391/race_receipt.json --receipt artifacts/detector_fixed_race/panel_runs_12df_mutual/44b6_12dfb391/race_receipt.json --receipt artifacts/detector_fixed_race/panel_runs_12df_motion/44b6_12dfb391/race_receipt.json \
  --methods official_ilp,harmonic_v1,mutual_confidence,motion_gated --output artifacts/detector_fixed_race/validation_receipt.json
```

## 8. 既知の問題

- 現行MacBook DockerはCPU-onlyであり、GPU実測値はまだない。
- node featureはwindow context依存のため、cacheのcanonical featureは最初の観測である。association比較はpair logitsを使用する。
- panel画像・GT、5 sampleのdetector cache、4方式公式metricは完了した。12dfb391はdivisionを含む完了sampleである。
- Kaggleへの外部submissionは行わない。

## 9. panel実験の完了状況

`44b6_0c582fdc`は0bと同じGT-free detector materializeを完了し、cache hash `2bd90bee3abf0afb07abdc971bfb45235a33bb931feaf6bfb3b884759682f748`、nodes `34,910`、candidate edges `12,459,009`、detector elapsed `5,447.649957480986 s`、`auto/cpu`を記録した。sidecar作成後、official/harmonic/mutual/motionのprediction GEFFと公式metricを取得した。0dbと12dfb391も同じ手順で完走し、5 sampleで4方式比較が完了した。

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

| weight | 旧3 sample平均 Final Score（参考） | 0.20との差 |
|---:|---:|---:|
| `0.10` | `0.7836523346441413` | `+0.0000425622352703` |
| `0.20` | `0.7836097724088710` | `+0` |
| `0.30` | `0.7777614914000653` | `-0.0058482810088057` |

追加したdivision sampleを含む5 sample共通集合では、rw0.10と既定rw0.20の実receipt平均は次のとおりである。

| variant | 5 sample平均 Final Score | rw0.20との差 |
|---|---:|---:|
| `harmonic_v1_rw_0p10` | `0.7931993011556243` | `-0.0012150965584476` |
| `harmonic_v1`（rw0.20） | `0.7944143977140719` | `+0` |

12dfb391ではrw0.10が `0.8012113309947029`、rw0.20が `0.7962869753878102` で `+0.0049243556068927` 改善した。一方、0dbではrw0.10が `0.8138281708509945`、rw0.20が `0.8249556959559359` で `-0.0111275251049414` 悪化した。5 sample平均ではrw0.10が下回るため、公開Strong Baseline v1の既定値はrw0.20のまま維持し、rw0.10は不採用とする。全variantのprediction GEFF・receipt・wall timeは `artifacts/detector_fixed_race/harmonic_sweep/` 以下に保存した。

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

## 13. detector-fixed 5 sample集約（12df追加後）

development、0b、0c、0db、12dfb391の5 sample平均を、各sampleの `race_receipt.json` の `metrics.final_score` から再集計した。集約の正典は `artifacts/detector_fixed_race/validation_receipt.json` である。

| association | 5 sample平均 Final Score | 5 sampleでのofficial差 | officialに勝ったsample数 |
|---|---:|---:|---:|
| `official_ilp` | `0.7688958987642377` | `+0` | `0/5` |
| `harmonic_v1` | `0.7944143977140719` | `+0.025518498949834156` | `5/5` |
| `mutual_confidence` | `0.7467735686449968` | `-0.02212233011924092` | `0/5` |
| `motion_gated` | `0.7187007022873142` | `-0.0501951964769235` | `0/5` |

0dbと12dfb391を加えても、harmonicは5 sampleすべてでofficialを上回った。division sampleでのDivision TPは全方式0、harmonicのDivision FPは3、official/mutual/motionのDivision FP/FNは `0/1` である。

## 14. validation receipt のfresh re-reviewと5 sample smoke

validation receipt実装の対象commitは `fbfbf26` で、fresh re-reviewは `APPROVED` だった。旧development runは4方式が単一出力ディレクトリを共有し、最後の方式が `prediction_manifest.json` を上書きしていたため、最終evidenceでは方式ごとの個別再生に切り替えた。

detector cacheの再計算は不要であり、developmentの4方式を `panel_runs_dev_official`、`panel_runs_dev_harmonic`、`panel_runs_dev_mutual`、`panel_runs_dev_motion` へ個別再生して方式ごとの `prediction_manifest.json` を再生成した。各manifestは構造再読込に成功し、`validation_receipt.json` の20個別recordに束ねられている。

## 15. 最終検証

- full pytest: `199 passed, 2 warnings`
- report＋validation receipt限定pytest: `25 passed`
- 変更対象Ruff: `All checks passed!`
- `git diff --check`: 通過

pytestの2 warningは、divisionが存在しないtest splitで公式metricがdivision termをdropする既知の警告である。full repository Ruffは `src/biohub/official_metrics/metrics.py` と `src/biohub/visualizer/*` の既存24件を報告した。今回の変更対象外のため修正していない。
