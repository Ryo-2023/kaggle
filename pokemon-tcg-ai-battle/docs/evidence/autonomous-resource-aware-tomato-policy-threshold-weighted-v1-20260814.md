# Autonomous resource-aware Tomato policy threshold weighted48 — 2026-08-14

## 結論

Tomato native parent の policy bytesを研究用コピーへ固定し、`_ICE_CREAM_HP_THRESHOLD` の全 matchup 値を一律に `-20` または `+20` する2候補を、同一 META_TRAIN weighted subset で比較した。parentを含む3 arm・各48局、合計144局はすべて `DONE`、`fault=0`、draw=0、seat 24/24、opponent各4局、arm内 game ID/seed unique、parentとのpaired key/seed一致だった。しかし lower は parent 比 **−12.770pt**、higher は **−9.287pt** と明確に負であり、両候補を candidate-only/NO-GO として停止した。common24、384、768、longrun、training、promotion、Champion変更、submissionは起動していない。

この結果は、現行Tomatoの ice-cream 使用閾値を一律に広げる／狭める単純な policy parameter surface が、今回の上位 META_TRAIN 分布では改善信号を持たないことを示す。threshold surfaceを同じ条件で再試行せず、結果はhard-negativeとして次の deck-policy alternating / 別の局所surface選定へ渡す。

## 固定した入力と権限境界

- parent policy: `opponents/tomatomato_archaludon/main.py`
- parent policy SHA: `8908af5caad296820a6ce5a9c8d388f04869eb499b308ac446142d9dcdaced9e`
- parent deck: `opponents/tomatomato_archaludon/deck.csv`
- parent deck SHA: `42165967b565dd42ec426ecccfe79bfa7d72aa8306590e149dface0ee8bd530e`
- candidate policy copies: source bytesへ閾値mappingだけを一度置換し、exclusive writeで生成
- candidate IDs: `ice-threshold-lower-v1`, `ice-threshold-higher-v1`
- lower values: `lucario=250, starmie=190, crustle=100, hop=200, generic=210`
- higher values: `lucario=290, starmie=230, crustle=140, hop=240, generic=250`
- META_TRAIN subset SHA: `09176f164b0f7719de70c903195e6b11b00dc3895ee8a98a154263fd8cbd72ed`
- evaluator implementation / pool / broad config / resource config はmanifestでSHA-bound
- authority: `research_only=true`、execution/training/promotion/longrun/submission はすべて `false`
- native behavior permissionは使用せず、native action/teacher label/logit/private fieldを学習入力へ流していない

## 実測結果

weighted score は各 opponent の WDL rate をMETA_TRAIN重みで平均した値である。各armは12 opponent × 2 seat × repetition 2 = 48局。

| arm | W-D-L | raw score | weighted meta score | parent差 |
|---|---:|---:|---:|---:|
| Tomato parent | 35-0-13 | 72.9167% | 0.718641367 | — |
| lower `-20` | 28-0-20 | 58.3333% | 0.590939841 | −12.7702pt |
| higher `+20` | 30-0-18 | 62.5000% | 0.625769315 | −9.2872pt |

全armで `faults=0`、`status=DONE` 48件、seat 0/1 は各24件だった。ResourceGovernorはnormal、workers=12、worker recycle=16、weighted本体144局、wall約10.36秒、throughput約13.90 games/s、restart=0、kill=0、MemAvailableは約46.09GiBから45.90GiB、RSSは約34.0MBから34.2MBだった。warm-up/ramp telemetryも別artifactへ保存し、実行後に残留processはない。

## 正典artifact

Root: `runs/final-sprint-autonomous/resource-aware-tomato-policy-threshold-weighted-v1-20260814/`

| artifact | SHA-256 |
|---|---|
| `candidate_manifest.json` | `faedacf5578ad9d2e29365eb4a1b750075b072c34260f4dd9244deaaa12b341a` |
| `warmup_telemetry.json` | `42ddd8b29a194a015fcd4e55adcbd4af8e517ce029f8ae2b3b43747e51a6954b` |
| `weighted48_summary.json` | `9cad065f2fc15dbbb6705c19e0249e02bd549403ede68eff1809fcaf4c0077ba` |
| `weighted48_summary.md` | `3cac606931691562d6e6e9be20e24f3b7368377a74e8bbe8a4b8674f422f90b7` |
| `final_summary.json` | `6ea99d48d879d77d3933f5a43a9b1f6f4a891c301883509ef5778a16dccb8557` |
| `weighted48/evaluation/manifest.json` | `24a99a843d4e0cadcf286b26f153cfd131e9c19434f280892e4ae25646798f50` |
| `weighted48/evaluation/ledger.jsonl` | `0a5d4c377b1426d3bd2c429c0697479da644d13c9a287756cb4e6abdc80768b1` |
| `weighted48/evaluation/summary.json` | `59697c6036455198b4ae4b47c4c7caa86384fae5bbe795a8cb73e18276538033` |
| `weighted48/evaluation/progress_summary.json` | `312784f5ebf72710f559a5f8d7bff7e6a676552e008266e52d28b3f130779c04` |

Implementation/test SHA:

- runner: `scripts/run_resource_aware_tomato_policy_threshold_weighted_v1.py` — `f439712cc605e8189c7805c7ad66fbbaffb5a08eb6d33d55f32321af2d96e23a`
- focused test: `tests/meta_specialist/test_resource_aware_tomato_policy_threshold_weighted_v1.py` — `f1e1a8996091a4bbaf9ed118aa69d2e85d3770dcec6c501b89db3789d12d88b7`

## 検証と未完了事項

- RED: module未実装時のcollection failureを確認
- GREEN: focused threshold tests `3 passed`
- nearby threshold + Tomato surface tests `5 passed`
- `py_compile` PASS
- docs validator: `Validated 13 canonical documents.`
- `git diff --check` PASS
- no production `main.py` / evaluator / parent deck modification
- no commit, push, Kaggle submission
- no common24/384/longrun/training/promotion

Thresholdの一律±20は負結果で停止したが、これをTomato policyの全parameter surfaceの否定とは解釈しない。局所的な matchup条件、deck-policy alternating、またはhard-negative opponentへの別のbounded policy surfaceは未評価である。ただし次のrunは同一threshold面を無目的に再試行せず、期待性能・成功確率・時間コストを比較して一つだけ選ぶ。
