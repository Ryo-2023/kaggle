# Tomato native parent weighted deck screen (2026-08-14)

## 結論

現行 Tomato native pair を parent として、全既存 `opponents/**` と過去 final-sprint の deck multiset SHA を除外した新規1-card mutationを2件、META_TRAIN 上位12の重み付き48局で比較した。parentは weighted score **0.6763569**。`4d4aa8…`（1182→1086）は **0.6558405 / −2.052pt**、`ae3075…`（1185→1192）は **0.7474145 / +7.106pt**。全144局が DONE/fault0 で、candidate/parentのseat・opponent・repetition・seed scheduleは一致している。従って ae3075 のみを common24-96 guardrailへ進め、4d4aa8は停止する。weighted結果から common24/384/longrun を自動起動していない。

## 一次 artifact

- run root: `runs/final-sprint-autonomous/resource-aware-tomato-weighted-deck-v1-20260814/`
- candidate manifest SHA-256: `656fbbccb0c6691332459287816ddbcb803f75409febfdd213cf08c16162e913`
- parent deck SHA-256: `42165967b565dd42ec426ecccfe79bfa7d72aa8306590e149dface0ee8bd530e`
- parent policy SHA-256: `8908af5caad296820a6ce5a9c8d388f04869eb499b308ac446142d9dcdaced9e`
- candidate `4d4aa8…` deck SHA: `96aea0c0fdfb5e8feea9140ef8d5db63aa3312c75f2e846f15c332d55391141a`
- candidate `4d4aa8…` multiset SHA: `7b7ffd73f41b90b9dafe241c891fa7413cfd1015483a14f3a4fd7702b4b1e7d9`
- candidate `ae3075…` deck SHA: `95fecbb5041f1781b197ff5ba5e81adff5bec4605301b6d82bca7beb0fe919ef`
- candidate `ae3075…` multiset SHA: `ea00c6ba7ff43066675e3f2576ab4ff70697792ad1ed6d116f8bf428e3cd7178`
- weighted subset SHA: `09176f164b0f7719de70c903195e6b11b00dc3895ee8a98a154263fd8cbd72ed`
- warmup telemetry SHA: `d97ba20745aea4d6817f3de0379d267016786b94716fd46581e69350b95b7745`
- weighted ledger SHA: `5d2a5a51df7b2809ee56e61bbb216b2ef8328f9e566e9cace5ffba44dd7604bf`
- evaluator summary SHA: `1234584a8c3f0f03bc251a4b15959a057ff0fd9ae1a9f7a809df64d173c2e5b5`
- finalized weighted summary SHA: `412990816cd54b2647ca37a67ecf7132c8152ee61587d516f3500632ad27407e`
- finalized markdown summary SHA after fault re-seal: `79807565d155458fcf7e50b3b5c00a5bc1353915ce3bfa85e80d4f32c7501d49`
- final summary SHA after metadata re-seal: `a2a55b21e30691aa2b6ab3c1e63ba24962e323c4a6488629adb17aa44ddfd22b`

## Integrity / resource gates

- 3 arms × 48 games = 144 requested/completed; status `DONE` 144、fault 0
- each arm seat 0/1 = 24/24; each of 12 selected opponents = 4 rows
- game IDs and seeds unique within each arm
- candidate/parent paired keys `(opponent_id, seat, repetition)` identical and seed schedule identical
- ResourceGovernor ramp 1/2/4/8/12: all warmup blocks 4/4, fault0、safe workers 12、GPU compute processなし、kill0、recycle16
- META_FINAL rows were excluded from weighted subset; all authority flags remain false
- Tomato opponent row is retained because this is the sealed META_TRAIN subset; it is recorded explicitly in the per-opponent ledger and is not treated as submission evidence

## Known wrapper issue and closure

The first research wrapper invocation completed and atomically sealed the evaluator ledger, then failed before summary creation due to attempting to aggregate pre-run `EvaluationGameV1` objects as mappings. No performance row was rerun or overwritten. The ledger was independently reloaded, integrity-checked, and finalized into the summary artifacts above; the wrapper was minimally corrected for future runs.

## Reproduction / next gate

```bash
PYTHONPATH=.:src .venv/bin/pytest -q -s \
  tests/meta_specialist/test_resource_aware_tomato_weighted_deck_v1.py
PYTHONPATH=.:src .venv/bin/python -m py_compile \
  scripts/run_resource_aware_tomato_weighted_deck_v1.py
```

Only `ae3075…` is eligible for the separately sealed common24-96 guardrail. No 384, longrun, submission, or Champion change is authorized by this artifact.
