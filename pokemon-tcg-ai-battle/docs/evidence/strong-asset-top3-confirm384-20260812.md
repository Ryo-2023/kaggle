# Strong Asset top-3 confirmation — 384局

## 結論

96局 screen 上位3 native deck+agent pair を、同じ共通 arena で各384局（24 opponents × 2 seats × 8 repetitions）へ拡張した。全1,152局が `DONE`、fault 0 である。96局で1位だった `plamen06_steel` は3位へ下がり、`tomatomato_archaludon` が暫定1位になった。したがって、96局だけで BestKnown を固定してはならず、384局結果を現時点の暫定 BestKnown 順序として使う。

| 順位 | native asset | W/D/L/F | score rate | seat0 | seat1 |
|---:|---|---:|---:|---:|---:|
| 1 | `tomatomato_archaludon` | 279/0/105/0 | 72.65625% | 138/192 | 141/192 |
| 2 | `plamen06_steel` | 275/0/109/0 | 71.61458% | 144/192 | 131/192 |
| 3 | `lucifer19_battlecore` | 266/0/118/0 | 69.27083% | 138/192 | 128/192 |

差は tomato–plamen が4勝（1.04167pt）、plamen–Lucifer が9勝（2.34375pt）であり、なお 768局以上の confirmation が必要である。これは native pair の比較であり、Lucifer の hard-BC student の性能ではない。

## 実験契約

- schema: `meta-specialist-asset-pair-ranking-v1`
- requested/completed: 1,152/1,152
- asset_count: 3
- per asset: 384 = 24 opponents × 2 seats × 8 repetitions
- per seat: 192 games
- pairing: `independent_stratified_not_game_paired`
- evaluator: bounded spawn evaluator, implementation SHA `ae476cc72ac4efcf080dff118b1c4ef15268edf8e1d22b9b04cb432d48f9a797`
- all rows: unique `game_id`、seat-balanced、self-playなし、fault 0

## 一次 artifact と SHA-256

| artifact | path | SHA-256 |
|---|---|---|
| ranking | `runs/meta-specialist-asset-ranking-top3-confirm384-20260812/asset_ranking.json` | `58df60b5c3ace39fb827ede3adf229c2d3d626e14b9dd685dda0d18506f5690b` |
| ledger | `runs/meta-specialist-asset-ranking-top3-confirm384-20260812/ledger.jsonl` | `6ecf59ef0d0248d48f3d3fb3f37292229ceab867f2ca7158bdd73812a12f5d73` |
| summary | `runs/meta-specialist-asset-ranking-top3-confirm384-20260812/summary.json` | `eb56523d2090c81bd9b107315c49310d4cfe824f9cf77a2bae0bbcf08823417f` |
| manifest | `runs/meta-specialist-asset-ranking-top3-confirm384-20260812/manifest.json` | `127e61f54fbd8753467d07ef3d6e2fd3e6f7703768ab74f1c41b65c22562576` |

## 判断と次手

1. `tomatomato_archaludon` を暫定 `EvaluationBestKnown` として freeze する。ただし 768/1,536局で plamen/Lucifer と再確認する。
2. `plamen06_steel` は96局上振れを384局で修正されたため、単一screenのBestKnown扱いをしない。
3. training/submission permission、package closure、deck hashを eligibility audit と突合する。ランキング性能だけで TrainingEligibleBestKnown／SubmissionEligibleBestKnown とは呼ばない。
4. 384局で native pair が確定する前に、Lucifer hard-label/outcome-weighted BC の同型 sweep は再開しない。

