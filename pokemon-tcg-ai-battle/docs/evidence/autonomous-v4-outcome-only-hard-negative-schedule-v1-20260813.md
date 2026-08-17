# V4 outcome-only hard-negative schedule v1

## 判定

V4 seed1 common24 96局の終端WDLだけから、`META_TRAIN` 20 opponent / 80局を対象にした deterministic hard-negative schedule sidecarを生成した。`META_FINAL` 4 opponent / 16局は weight=0・quota=0 として明示除外し、scheduleへは投入していない。faultは0、全opponentが両seat×rep2を満たす。

これは opponent **評価スケジュール metadata** であり、teacher dataset、behavior policy、action supervision、private-state dataset、policy update、training authorityではない。既存 V4 runner、production、既存 run root、Champion、submissionは変更していない。性能/CABT/training/longrunは起動していない。

## 実装

- module: `src/mage_ptcg/meta_specialist/outcome_only_hard_negative_v1.py`（closed row/identity allowlistを含む）
- CLI: `scripts/build_outcome_only_hard_negative_schedule_v1.py`
- tests: `tests/meta_specialist/test_outcome_only_hard_negative_v1.py`
- formula: `reliability*(0.70*hard_negative+0.15*underexposure+0.15*diversity)`
- caps: opponent `0.35`、family `0.55`
- floor: family quota `1`、largest-remainder quota `96`
- atomicity: temporary file + fsync + exclusive `os.link`、既存winnerをclobberしない
- output schema: `meta-specialist-outcome-only-hard-negative-v1`
- authority: training/promotion/submission/external-execution/longrun 全て false

入力から使ったのは terminal `win/draw/loss`、seed、seat、repetition、opponent ID、meta family、policy/deck/source identity、source SHAだけである。ledgerの action/teacher/private/trace/trajectory/legal/hand/prize等のキー、および既知のWDL/seed/seat/opponent/identity/SHA閉包外の未知キーは fail-closed で拒否し、public traceファイルは一切読み込んでいない。

## 一次入力

| source | path | SHA-256 |
|---|---|---|
| V4 ledger | `runs/final-sprint-autonomous/v4-seed1-public-trace-meta-train-common24-96-serial-20260813-v1/ledger.jsonl` | `6d39fe80c20bc8360360396fe180fef04b3d2b3864ce55e8b6f283ee49095630` |
| V4 summary | `runs/final-sprint-autonomous/v4-seed1-public-trace-meta-train-common24-96-serial-20260813-v1/summary.json` | `df9148eb8550e0f5ecba8385335e6d02a15d4dcb86d186a6f80a1d55985a3137` |
| meta distribution | `runs/final-sprint-autonomous/meta-distribution-v1/manifest.json` | `e430f1284e587e7f301f9e29abe377faad79ff5120a39c42b7b2f6a5223dd2ae` |
| broad pool | `configs/meta_specialist/performance_first_broad_pool_v1.json` | `832273ff656280d2556c9df09a9a3db9f2564a181be78a3e658509d3b396209b` |
| opponent pool | `opponents/pool_manifest.json` | `e0013cf31b3e6e24db54591faeef6f092b9ebf85247bd0e57598eb8d447f20ca` |
| subject checkpoint | `runs/meta-specialist-v4-archaludon-dagger-wave4-strict-paired/bc-checkpoints/seed-1/best-recurrent-bc-v4.pt` | `ec08ace5fb25352758a9f950694134ef6544ec69b23c00047101e588e3d06319` |
| subject deck | `opponents/public_archaludon_cinderace_r7/deck.csv` | `42165967b565dd42ec426ecccfe79bfa7d72aa8306590e149dface0ee8bd530e` |

Source summary gate: 96/96 complete、fault0、seed base `14910000`、games/seat `2`、subject V4 policy/deck identity一致、native action label=false、teacher label=false、private field=false、全 authority=false。Broad 24の splitは `META_TRAIN=20`、`META_FINAL=4` であり、全 row の `usage_boundary=local_eval_only` を保持した。

## 生成 artifact

新規 run root:

`runs/final-sprint-autonomous/v4-seed1-outcome-only-hard-negative-schedule-v1-20260813/`

| artifact | file SHA-256 | semantic SHA-256 |
|---|---|---|
| `schedule.json` | `df9397e5e07f995ed41b000b8170a26b71f16ed429e9cfade57e36e949b4d3e9` | `f8bec57883ce60e50bb33de0b01939f85d0bceda9a7f09d021d411f82d07570b` |

集計:

- source: 96局、included: 80局 / 20 opponent、excluded: 16局 / 4 opponent
- included WDL: 48W / 0D / 32L、score rate `48/80 = 60.00%`
- weights sum `1.0`、quota sum `96`
- family count `12`、fault `0`
- hard-negative最大 weight: `aman_crustleaware_fighting` 0.0777027、`kokinnwakashuu_lucario_search` / `pilkwang_lucario_alakazam` 0.0760135
- `META_FINAL` excluded IDs: `aristophanivan_multiply`, `dashimaki360_crustlecounter`, `lucifer19_battlecore`, `plamen06_steel`

quotaは family floor を満たすため、一部の弱い opponentへ集中しすぎない cap/floor配分である。個別 opponentの quota=0 は「heldout」ではなく、family floorと deterministic largest-remainderの結果であり、weight自体は全20 META_TRAIN rowへ保持している。

## 再現・検証

再現（同一 output path は immutable のため、再生成時は新しい run rootを使用する）。

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python scripts/build_outcome_only_hard_negative_schedule_v1.py \
  --repo-root . \
  --ledger runs/final-sprint-autonomous/v4-seed1-public-trace-meta-train-common24-96-serial-20260813-v1/ledger.jsonl \
  --summary runs/final-sprint-autonomous/v4-seed1-public-trace-meta-train-common24-96-serial-20260813-v1/summary.json \
  --meta-manifest runs/final-sprint-autonomous/meta-distribution-v1/manifest.json \
  --pool-manifest opponents/pool_manifest.json \
  --output runs/final-sprint-autonomous/v4-seed1-outcome-only-hard-negative-schedule-v1-20260813/schedule.json \
  --seed v4-seed1-common24-96
```

検証コマンド:

```bash
tmpd=$(mktemp -d /tmp/luna-hardneg-final.XXXXXX)
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/pytest \
  --basetemp="$tmpd/base" --capture=no -q \
  tests/meta_specialist/test_outcome_only_hard_negative_v1.py
# 7 passed

python -m py_compile \
  src/mage_ptcg/meta_specialist/outcome_only_hard_negative_v1.py \
  scripts/build_outcome_only_hard_negative_schedule_v1.py
python scripts/docs/validate_docs.py
git diff --check
```

focused suiteは `7 passed`、`py_compile`、docs validator（13 canonical documents）、`git diff --check` がPASSした。`verify_outcome_only_hard_negative_schedule_v1` による実artifactのstrict reloadもPASSした。evaluator implementation SHAを現行 `parallel_cabt_evaluator_v1` から再計算し、source pathはrepo root内だけを受理する。atomic tempは一意なmkstemp + exclusive linkでwinnerをclobberしない。

## 解釈と次段ゲート

このartifactは次 iterationの META_TRAIN opponent quotaを決める入力としては利用可能だが、V4の56.25%をBestKnownとみなす根拠ではない。`local_eval_only` rowは評価対象としてのみ扱い、teacher/behavior/trainingへ昇格しない。次の候補実装はこのsidecarを scheduler metadata として読み、Rule v0 public black-box candidateまたは別の自己所有 outcome policyを frozen BestKnown と同一 common24で screenする research-only bridgeである。

96→384へ進む条件は別途事前登録する。少なくとも frozen BestKnown比較、同一seed/seat/opponent strata、fault0、両seat support、heldout exposure=0、candidate identity SHA一致を満たし、384 aggregateで約+3ptが再現しない限り longrunを起動しない。
