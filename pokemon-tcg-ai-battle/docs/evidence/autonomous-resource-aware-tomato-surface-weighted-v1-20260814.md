# Tomato親デッキ exact surface weighted48（2026-08-14）

## 結論

Tomato native親（`1244→1123 Switch`、`1244→1252 Gravity Mountain`）を同一META_TRAIN weighted48で比較した。両候補とも全144局でfault 0、DONE 144/144、seat/seed/game-id/paired-strata gateを満たしたが、親を下回った。したがって両候補は `candidate_only` のまま停止し、common24・384・longrun・submissionは起動しない。

| arm | deck mutation | W-D-L | score | weighted meta score | parent差 |
|---|---|---:|---:|---:|---:|
| Tomato native parent | — | 33-0-15 / 48 | 68.750% | 0.686881594 | — |
| surface-1244-to-1123 | 1244→1123 Switch | 28-0-20 / 48 | 58.333% | 0.587203759 | −9.9678pt |
| surface-1244-to-1252 | 1244→1252 Gravity Mountain | 27-0-21 / 48 | 56.250% | 0.566681594 | −12.0200pt |

## 固定条件と候補監査

- 親 deck SHA: `42165967b565dd42ec426ecccfe79bfa7d72aa8306590e149dface0ee8bd530e`
- 親 policy SHA: `8908af5caad296820a6ce5a9c8d388f04869eb499b308ac446142d9dcdaced9e`
- `1244→1123` multiset SHA: `5e357db8390cfdbbae8e0a44fa828b75bca4892e62f01afdb01d5cb278d7ffc4`; materialized deck SHA: `bd6d35fa1919113670fc65d0f1846782688f44607b6e452b32aa0eea4aad2635`
- `1244→1252` multiset SHA: `8410b1033c8aad43836d7f5046726566cb4e6a89f8b197b4c013510d3b0053d1`; materialized deck SHA: `1f9b124c1b0feea445a98ad90eef6b60d859f36d516f108a4e9885ef90dc5f4b`
- `validate_deck`、`validate_mutation_v1`、既存 `opponents/**` と prior final-sprint `deck.csv` のmultiset novelty scanを通過。
- META_TRAIN weighted subset SHA: `09176f164b0f7719de70c903195e6b11b00dc3895ee8a98a154263fd8cbd72ed`
- evaluator SHA: `0cbac2789e08758d14783922c5c7145f25701a47d978b3d9df9d132aec4eed84`
- pool manifest SHA: `e0013cf31b3e6e24db54591faeef6f092b9ebf85247bd0e57598eb8d447f20ca`
- authority: `research_only=true`、execution/training/promotion/submission/longrun authorityはすべてfalse。

## プロトコル・資源

各armは同じ12 META_TRAIN opponents、2 seat、各seat/repetition 2局の48局。候補と親で opponent/seat/repetition key と seed scheduleを一致させた。arm内 game-id と seed は一意で、全144 recordsが `DONE`、fault 0、seat 24/24、各opponent 4局だった。

ResourceGovernor warmup rampは workers `[1,2,4,8,12]` を順に実行し、各4局・fault 0。weighted48は workers=12、requested/completed=144、wall 8.453秒、throughput 17.035 games/s、worker recycle=16、restarts=0。warmup telemetryでは無関係processをkillしていない。

## 一次artifact

Root: `runs/final-sprint-autonomous/resource-aware-tomato-surface-weighted-v1-20260814/`

- `candidate_manifest.json` SHA `a34365af14236b52b7375abdaea9a8e6448b849ed4372ada6f5cb12eb3a09803`
- `warmup_telemetry.json` SHA `9ce24ed952b7e982cac53ba1f6b1627283e329aa54b93960f6c649e3eb760d8d`
- `weighted48_summary.json` SHA `6e854dc47186f5d00ddcb3a63e2b950b48448cd17163fc3ecd234fb289e58157`
- `weighted48_summary.md` SHA `9be658d08f02bea5fc82c3593f9f86d797e99f08a255010a864cc0993b698020`
- `final_summary.json` SHA `9cf88811b623f55cecd61ec8f079411708cd6ed332f54647f4a00b042dbdb868`
- weighted ledger: `weighted48/evaluation/ledger.jsonl`
- research-only runner SHA: `9b186b7df504650b5d58a19d5703328be9104cae787aa3829becdb0c8284d329`
- focused test SHA: `585e6c7a7be04acd8506cd1e7166b8f2a0e1d78716406e16617c12a2a0b9f691`（2 passed）

再現コマンド:

```bash
PYTHONPATH=.:src .venv/bin/python \
  scripts/run_resource_aware_tomato_surface_weighted_v1.py \
  --output runs/final-sprint-autonomous/resource-aware-tomato-surface-weighted-v1-20260814
```

このrunnerはfresh rootへのno-clobber materializeのみを行い、production runner、`main.py`、既存artifactを変更せず、common24/384/longrun/submitを自動起動しない。
