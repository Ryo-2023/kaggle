# Rule v0 MAIN `ABILITY+120` telemetry smoke

既存 weighted48 evaluation root は再実行せず、同 root の games sidecarから candidate/control 各2局を新規 smoke rootへ切り出し、新しい research-only runnerを直接呼び出した。4局すべて `DONE` で、candidate agentには `rule-v0-main-ability-telemetry-v1` の bounded counters が返り、controlは baseline なので `available=false` と明示された。

candidate smokeの実測は2局とも `eligible_main_observations=0`、`override_applied=0` だった。これは telemetry 経路が取れることを確認する smoke であり、ABILITY optionの実戦coverageを示すものではない。focused unitでは合法な public MAIN observation に対し `eligible=1`, `override_attempts=1`, `override_applied=1` を確認している。

判定は `telemetry_available=true` だが `screen_remeasurement=NOT_YET`。旧48局は新runnerで測っていないため、coverage/fallbackを後付けできない。再測定を行う場合も、別 fresh rootで明示承認された screen-only run とし、common24やpromotionへ自動昇格しない。

## 再現と artifact

```bash
TMPDIR=/tmp/luna-ability-telemetry-pytest-20260814-2 \
  PYTHONPATH=.:src .venv/bin/python -m pytest -q \
  tests/meta_specialist/test_rule_v0_main_ability_weighted48_v1.py
```

- focused pytest: `5 passed`
- smoke root: `runs/final-sprint-autonomous/nonmain-ability-plus-120-telemetry-smoke-20260814-v1/`
- smoke artifact SHA (`results.json`): `7091cb23908020fcaa046800d8bef07fe1e200167b991281bad3ce7ab60fa756`
- source sidecar SHA: `8856b40bb24caf3e3a5c7ca40ac11bb53a28ea8626d2b3da75f5f116d79a6016`

本smokeは public observation/action option type のみを処理し、private state、teacher/native action label、training/promotion/submission/longrun authorityを持たない。

