# Tomato native `ae3075` common24-96 guardrail (2026-08-14)

## 結論

META_TRAIN weighted48で唯一 positive だった `ae3075c2e096…`（Tomato native deckの1185→1192）を、Tomato native parentと同一 common24 / seat / repetition / seed scheduleで96局ずつ確認した。candidate は **62-0-34 / 96 = 64.583%**、parent は **73-0-23 / 96 = 76.042%**、差分は **−11.458pt**。全192局が `DONE`、fault 0 であり、candidate-onlyとして停止する。384、768、longrun、submissionは起動していない。

## 一次artifact / SHA

- run root: `runs/final-sprint-autonomous/resource-aware-tomato-ae-common24-v1-20260814/`
- source weighted manifest SHA: `656fbbccb0c6691332459287816ddbcb803f75409febfdd213cf08c16162e913`
- source weighted summary SHA: `412990816cd54b2647ca37a67ecf7132c8152ee61587d516f3500632ad27407e`
- parent deck SHA: `42165967b565dd42ec426ecccfe79bfa7d72aa8306590e149dface0ee8bd530e`
- parent policy SHA: `8908af5caad296820a6ce5a9c8d388f04869eb499b308ac446142d9dcdaced9e`
- candidate deck SHA: `95fecbb5041f1781b197ff5ba5e81adff5bec4605301b6d82bca7beb0fe919ef`
- candidate multiset SHA: `ea00c6ba7ff43066675e3f2576ab4ff70697792ad1ed6d116f8bf428e3cd7178`
- common24 config SHA: `832273ff656280d2556c9df09a9a3db9f2564a181be78a3e658509d3b396209b`
- evaluator SHA: `0cbac2789e08758d14783922c5c7145f25701a47d978b3d9df9d132aec4eed84`
- evaluator summary SHA: `5dace37b4c559bfb30fc3a48d97ed9424e6c66ed5065559133f1f040d3e5a336`
- evaluator manifest SHA: `4d02a7008bd48618fefbe7610d6c958855bce51b785d923021a85b2428e41918`
- evaluator ledger SHA: `c09861a8cd4a3d2d4a6441066de01410742f63f47523bd583abd6d77f6563086`
- `common24_summary.json` SHA: `d99f1e39c65a35a0696d556e44935514ec515d5f2e20f2660d0859e95abed62b`
- `common24_summary.md` SHA after fault re-seal: `97109825ad03c7f13d29ff50b672f448cd6d93d0b64308f215ba1534810832ec`
- `final_summary.json` SHA: `23b0d8d3634f633d4fd5968f9a190b549e29e524df0b5557d3aa6d448e6e1d00`

## Integrity / resource gates

- 2 arms × 96 = 192 requested/completed; all status `DONE`; faults 0
- seat support: 48/48 per arm
- opponent support: 4 rows for each of 24 common opponents per arm
- `(opponent_id, seat, repetition)` paired keys identical; candidate and parent seeds identical by key
- all 192 game IDs unique; denominator includes all requested games
- ResourceGovernor: normal、workers 12、GPU compute process なし、kill 0、worker recycle 16
- authority: research-only; execution/training/promotion/submission/longrun all false

## Interpretation

The weighted48 +7.106pt result did not survive the full common24 guardrail. Therefore `ae3075` is not a BestKnown promotion candidate and no 384 follow-up is justified in this lane.

## Reproduction

```bash
PYTHONPATH=.:src .venv/bin/pytest -q -s \
  tests/meta_specialist/test_resource_aware_tomato_common24_v1.py
PYTHONPATH=.:src .venv/bin/python -m py_compile \
  scripts/run_resource_aware_tomato_common24_v1.py
```
