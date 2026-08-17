# Outcome-only hard-negative iteration 1（2026-08-13）

## 結論

384 局 confirmation の candidate arm（`ATTACK=+120`）から terminal WDL だけを投影し、META_TRAIN 20 ID（うち quota 0 の ID も population row として保持）向け iteration-1 schedule を strict materialize した。action/private/teacher fields は schedule payload に保存しておらず、heldout exposure、training、promotion、submission、longrun authority は全て 0/false である。

## 入力と証跡

- candidate ledger: `runs/final-sprint-autonomous/v4-seed1-policy-fixed-short-attack-plus-120-confirmation-384-20260813/evaluation/ledger.jsonl`
- ledger SHA-256: `eda449d3fa071c4f0303c1421ec9fd369d51bb48e110e258a234ce839f5392df`
- sealed confirmation: `runs/final-sprint-autonomous/v4-seed1-policy-fixed-short-attack-plus-120-confirmation-384-v1-20260813/confirmation.json`
- confirmation semantic SHA: `9ccb1180b21142a74a731c2d55ce79072a82843f5a45ef0ec85554f2d10d30de`
- source projection: `game_id`, `opponent_id`, `opponent_identity`, `outcome`, `seat`, `seed` のみ。ledger metadata/action configuration は読み取り時の candidate identity gate 以外へ伝播しない。
- candidate rows は 384、`DONE`/fault 0、candidate policy SHA と confirmation identity 一致、META_TRAIN のみ。各 opponent の support は parent quota×4 と一致する。

## Artifact

新規 root: `runs/final-sprint-autonomous/v4-seed1-outcome-only-hard-negative-iteration1-20260813/`

| artifact | SHA-256 |
|---|---|
| `schedule.json` | `98cf87e942af8e2eaa57eaf4c3d6c9bc7baf842e15ba845b9eea72ae578dce6a` |
| semantic `schedule_sha256` | `5c12410d3f05419fa26f73f1e64a86b99cc9a6a7c6fb39f709b3369a54ff3def` |

summary は source/candidate 384 rows、included games 384、quota 96、weights sum 1.0、faults 0、heldout exposure 0、action/private/teacher/training false。schedule entries は 20 META_TRAIN IDs（quota 0 row を含む）で、META_FINAL 4 ID は `heldout_ids` に固定して weight/quota 0 とした。

## 再現・検証

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python \
  scripts/build_outcome_only_iteration1_schedule_v1.py \
  --repo-root . \
  --candidate-ledger runs/final-sprint-autonomous/v4-seed1-policy-fixed-short-attack-plus-120-confirmation-384-20260813/evaluation/ledger.jsonl \
  --confirmation runs/final-sprint-autonomous/v4-seed1-outcome-only-hard-negative-iteration1-20260813/confirmation.json \
  --output runs/final-sprint-autonomous/v4-seed1-outcome-only-hard-negative-iteration1-20260813/schedule.json
```

Focused suite: `tests/meta_specialist/test_outcome_only_iteration1_schedule_v1.py: 5 passed`。strict reload は confirmation/parent schedule/source SHA、candidate rows/identity、META_TRAIN support、WDL-only projection、formula/cap/floor/quota、authority を再導出して成功した。`py_compile`、docs validator、`git diff --check` は完了済み。性能/CABT/training はこの schedule laneでは起動していない。

## 次の gate

この schedule は candidate selection 用の outcome-only sidecar であり、行動ラベルや学習データではない。次はこの weight/quota を使って ATTACK+120 以外の bounded action candidate を最大2件、同じ opponent/seat/seed strata の 96局 screen として materialize する。各 candidate は fault 0・seat安定・paired support を満たす場合のみ 384 confirmation へ進める。
