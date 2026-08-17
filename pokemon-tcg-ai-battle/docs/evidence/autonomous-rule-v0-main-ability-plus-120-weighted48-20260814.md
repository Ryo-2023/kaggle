# Rule v0 MAIN `ABILITY+120` weighted48 screen

## 結論

既評価の ATTACK / PLAY / EVOLVE / ATTACH / END overlay を再実行せず、未評価だった Rule v0 MAIN `ABILITY+120` を research-only の別 surface として測定した。candidate は 7W/0D/41L、control は 3W/0D/45L（各48局、fault 0）で、score-rate の相対差は +8.333pt（14.583% 対 6.250%）だった。paired は loss→win 6、win→loss 2、同結果39+1で net +4 wins。seat別は candidate seat0 5W/19L、seat1 2W/22L、control seat0 1W/23L、seat1 2W/22L で、candidate の seat1 collapse は見られないが、絶対性能は低く native BestKnown を超えない。

この結果は `screen-only` とし、common24-96、384、promotion、training、submission、longrun へは進めない。既存 evaluator runner が action override/fallback の実行時 telemetry を ledger row へ伝播しないため、coverage/fallback は `unknown` のまま fail-closed である。したがって相対差を「ABILITY が発火した改善」とは解釈しない。

## 契約と再現

- candidate: `rule-v0-main-ability-plus-120-v1`, `MAIN_ONLY`, legal public `ABILITY` option の score delta `+120.0`
- fallback: malformed / non-MAIN / unsupported / illegal / exception は exact Rule v0
- control: current root Rule v0, 同じ deck / opponent / seat / seed / stratum
- source: META_TRAIN 20 opponent IDs のみ。heldout 4 IDs exposure `0`
- authority: training / promotion / submission / external execution / longrun はすべて `false`
- evaluator: 既存 `scripts.parallel_cabt_evaluator_v1`、workers `12`、worker recycle `16`

再現コマンド:

```bash
PYTHONPATH=.:src .venv/bin/python scripts/build_rule_v0_main_ability_weighted48_v1.py \
  --output runs/final-sprint-autonomous/nonmain-ability-plus-120-weighted48-20260814-v1
PYTHONPATH=.:src .venv/bin/python scripts/run_rule_v0_main_ability_weighted48_v1.py \
  --screen runs/final-sprint-autonomous/nonmain-ability-plus-120-weighted48-20260814-v1 \
  --output runs/final-sprint-autonomous/nonmain-ability-plus-120-weighted48-20260814-v1/evaluation \
  --workers 12 --worker-recycle-games 16
```

## Artifact identity

| artifact | SHA-256 |
|---|---|
| bridge manifest `screen_sha256` | `028b43207941e57a7d41c26eeeaadd27287c46e307272cb231794fc87a13f33f` |
| evaluation `ledger.jsonl` | `df99222151f53bec2d40c2020f4305cdee769e25bd69d320806feb2746934951` |
| evaluation `manifest.json` | `36a9b31f81d801790398fe67f18dd1e087406d2ce89e2471d5094c4ef17ed079` |
| evaluation `summary.json` | `1a09407f488fa5c0d78b323f9263c3a677d50fc13ff668e0a5251e9ffc7457fc` |

入力 identity は materialized manifest の `root_policy_sha256`, `deck_sha256`, `pool_manifest_sha256`, `broad_config_sha256`, `evaluator_sha256`, `schedule_file_sha256` に封印した。実行 artifact は `runs/final-sprint-autonomous/nonmain-ability-plus-120-weighted48-20260814-v1/` にあり、既存 run root は上書きしていない。

## 検証と残課題

- focused pytest: `4 passed`
- `py_compile`: module / materializer / runner pass
- `git diff --check`: 実行予定（受け渡し前に再確認）
- performance: 96 requested, 96 DONE, 0 faults, candidate/control keys 48/48 exact
- promotion gate: `NO-GO`（absolute weak、telemetry unknown、96のみ）

