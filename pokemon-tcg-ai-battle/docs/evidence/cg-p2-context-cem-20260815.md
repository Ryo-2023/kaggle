# P2 context CEM loop — 2026-08-15

## 結論

P2 robust g01（policy SHA `4261870c855d68abfbb96df029b5e66c6f019f398471701ceaac03f72f2b03c4`、root deck SHA `2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19`）を固定したまま、4軸の公開状態のみのcontext surfaceへ、再利用可能なCEM coreとresearch-only runnerを追加した。populationはcenterを先頭に保持し、`faults=0`、`candidate_seat_safe=true`、candidate-control `delta_objective>0` の行だけをelite候補にする。規定数のpositive eliteが不足した世代はcenter/scalesを保持するfail-closed契約である。

実CABT診断は3 campaign、合計840局（screen＋shared control）を実行し、全局 `DONE` / fault 0 だった。1 repetition診断ではseat粒度のためsafe eliteが0件、2 repetitionのidentity-center診断ではsafe positiveが1件のみ、−6000 signed tempo parent周辺ではsafe positiveがcenter 1件のみだった。いずれもelite_count=2を満たさずcenterを更新していない。これはCEMの更新失敗ではなく、低分散・複数elite gateが意図どおり新たなcenter採用を止めた結果である。

全campaignは既存META_TRAINの再利用であり、local poolにfresh/unused public metaがないため `BLOCKED_NO_LOCAL_UNUSED_META`。P2/P3、BestKnown、Champion、production、root deck、submission packageは不変で、promotion/training/submission authorityもfalseのままである。

## CEM設計

- `sample_population` は `random.Random(seed + generation * 1_000_003)` を使い、centerを必ず第0候補に置く。各軸は宣言済み`[-30000, 30000]`へclampし、同一configを発行しない。
- `rank_valid_results` はconfig、有限なdelta、fault 0、seat-safe、正差を検証し、delta降順→config SHA→candidate IDで決定的に並べる。
- `update_distribution` はelite meanへcenterを移し、母標準偏差に各軸span/64のfloorを適用する。
- `CemState` とno-clobber checkpointでgenerationごとのcenter、scale、結果、campaign identityを保存する。
- runnerは既存のhash-bound P2 screenと共有controlを再利用し、generationごとに別seed blockとartifactを作る。CEMは候補を提案するだけで、Champion変更・提出・学習権限を持たない。

## 診断結果

| artifact | population / repetition | 局数 | positiveかつsafe | 更新 | 要点 |
|---|---:|---:|---:|---|---|
| `runs/final-sprint-autonomous/cg-p2-context-cem-diagnostic-v1-20260815/` | 6 / 1 | 168 | 0 | center保持 | positive deltaは最大`+12.73pt`だったがseat gap `8.33%`でgate外 |
| `runs/final-sprint-autonomous/cg-p2-context-cem-diagnostic-r2-v1-20260815/` | 6 / 2 | 336 | 1 | center保持 | identity center `+1.7841pt`、safeだがelite_count=2不足。その他はnegativeまたはseat-unsafe |
| `runs/final-sprint-autonomous/cg-p2-context-cem-near-parent-v1-20260815/` | 6 / 2 | 336 | 1 | center保持 | signed `−6000` parent `+10.910pt`、safeだがreused META_TRAIN。周辺候補はsafe gate外 |

各artifactの summary / complete manifest SHAは次のとおり。

- identity r1: `baa0e7f6349886b7be719845d64caf1b99462f0682e97de6f81ffda6e6c210e7` / `0f45ff2b34df1c1719858c11cc7eab6d7328eefd45b52d9b39def53b0a180589`
- identity r2: `aea28ae514308f78ff575b19ea56444d34556add2afbfac681bf7e020ba4a05d` / `e5a41027ac284442c5951388254de6f7be63b695317f2f6597d84ea7577bb6c1`
- signed parent: `55581d041e6cd1ece2814d24713d8a7aed4463102d25a838c497bcde56d28b2e` / `71f982cc746e3e7b4c625a1564785471fda1be9fcb4f60d2b56155687e319c13`

全campaignのevaluator summaryは `requested_games == completed_games`、`faults == 0`、status distribution `DONE` だった。positive deltaだけを見てP2を更新せず、seat-safeかつ複数eliteという事前gateを優先した。

## 実装と検証

- CEM core: `src/mage_ptcg/meta_specialist/cg_p2_context_cem_v1.py` SHA `e39fb2e3269523dd16b6ce10d2289c5f9505ec91af671b946847e4ac3b702ca1`
- CEM runner: `scripts/run_cg_p2_context_cem_v1.py` SHA `546dae23602e5bad798bd02bf1ada87f170e9fef336d228d7ffe5da21b4360e4`
- P2 screen generation-id extension: `scripts/run_cg_p2_context_screen_v1.py` SHA `344aa037fcf67708f0aa5b2f3b586efb30ca75c26ddbbdaa43d778910c017b77`
- core tests: SHA `6774eeb0c1a66148d4877afb96052d26a3f42d31a1436653afd90ce5320df4ea`
- runner tests: SHA `520a7388b248b918652d7f836d0f7472c287924ef7df28fa6381047269ee61ee`

検証結果は次のとおり。

- P2関連 focused suite: `15 passed`
- `python -m py_compile`（core / runner / screen）: PASS
- `python scripts/docs/validate_docs.py`: `Validated 13 canonical documents.`
- `git diff --check`: PASS
- heavy process終了後の`pgrep`確認: active CABTなし

## 再開条件

1. fresh・unused・smoke-ready meta sourceを追加し、split SHAと使用履歴を固定する。
2. P2 parentを固定した新しいCEM campaignで、candidate/controlの両seat、fault 0、複数elite、独立seedを満たす。
3. reused META_TRAINのpositiveは探索分布の診断にのみ使い、P3/BestKnown/Championの根拠にはしない。
4. packageのsubmission-ready判定、commit、push、Kaggle提出は別途明示許可がある場合だけ行う。
