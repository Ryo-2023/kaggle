# Tomato親 1152 overlay（Night Stretcher / Hero's Cape）weighted48

## 結論

全既存119 deck multisetと過去Tomato/Rule v0/Student loopの候補を除外し、Tomato親の明示的な救済・装備ロジックに接続する未評価候補を2件screenした。`1152→1097 Night Stretcher` は weighted `+2.5259pt`、`1152→1159 Hero's Cape` は `+0.2476pt` の局所positiveだった。両候補とも candidate-only とし、common24/384/longrun/submissionは自動起動しない。小差のHero's Capeは追加確認へ進めず、Night Stretcherは親とのguardrail確認候補として記録する。

| arm | mutation | W-D-L / 48 | weighted score | parent差 |
|---|---|---:|---:|---:|
| Tomato native parent | — | 34-0-14 | 0.705577956 | — |
| surface-1152-to-1097 | Poke Pad→Night Stretcher | 35-0-13 | 0.730836537 | +2.5259pt |
| surface-1152-to-1159 | Poke Pad→Hero's Cape | 34-0-14 | 0.708053937 | +0.2476pt |

## 静的候補監査

- Tomato親 deck SHA `42165967b565dd42ec426ecccfe79bfa7d72aa8306590e149dface0ee8bd530e`、policy SHA `8908af5caad296820a6ce5a9c8d388f04869eb499b308ac446142d9dcdaced9e`。
- `1152→1097` multiset SHA `46abcda0f1fb64f72c38734f3829db577ca8b7ef169581a7d6b073240063d82a`、deck file SHA `b49944fa5400daa03c4f7ed14eccc2cf388bb268349fe6cb8549e6834abbae57`。
- `1152→1159` multiset SHA `4c2f9826b5b53743ef30ac6e4e7165a248093c2ca0adb98e23b5549dff2e818b`、deck file SHA `a31c7f7a624c2d58200c5d3d2b8cbdc784f6e9a8e47107cddf6b6e5a6789aaea`。
- `validate_deck`、`validate_mutation_v1`、既存opponentsと過去final-sprint deckのmultiset noveltyを通過。エネルギー/親のlineと結びつかない上位頻出候補はfail-closedで除外した。

## Protocol / resource gates

各armは同一META_TRAIN 12 opponent、両seat、各seat/repetition 2局の48局。全144 recordが`DONE`/fault0、各arm seat24/24、各opponent4局。arm内GID/seedは一意で、候補と親の`(opponent, seat, repetition)` keyおよびseed scheduleは一致した。

ResourceGovernor warmup ramp `[1,2,4,8,12]` は各4局fault0。weighted runはworkers12/recycle16、144/144 DONE、8.075秒、17.833 games/s、restart0、kill0。authorityはresearch-onlyで、execution/training/promotion/submission/longrunはすべてfalse。

## 一次artifact

Root: `runs/final-sprint-autonomous/resource-aware-tomato-overlay-1097-1159-weighted-v1-20260814/`

- manifest `0cc44dbe6cc144e4943842d6911ebd7083feb8728345aa716034f76aa60024a2`
- warmup telemetry `8461262f635f826a1bac0ddcde8e636df97e925303453a048657f14a858838e5`
- weighted summary JSON `59222a445c251f573bda2c001f81b423c9187ed620327bc6133b477aceafe54f`
- weighted summary MD `ad0132321675fa850b878fa1aa740e6d901ae7b140880f2708298721e38f4e1b`
- final summary `4f768657e4089d4b65f267446a8ae3cc7009cefa2ce2e6c9c7e85b050ff6325d`
- wrapper SHA（research-only）: `61cfe496b48c88cd5953dc0b950b045f219ba757493c41d6a0ffd1d2c1976124`

再現コマンド:

```bash
PYTHONPATH=.:src .venv/bin/python \
  scripts/run_resource_aware_tomato_overlay_1097_1159_weighted_v1.py \
  --output runs/final-sprint-autonomous/resource-aware-tomato-overlay-1097-1159-weighted-v1-20260814
```

common24/384はrunnerから自動起動されず、既存production/evaluator/artifactも変更していない。
