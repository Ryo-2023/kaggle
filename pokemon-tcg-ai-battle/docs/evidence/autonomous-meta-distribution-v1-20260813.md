---
title: Autonomous meta distribution v1
date: 2026-08-13
status: research-only
promotion_authority: false
---

# 結論

既存の Strong Asset Census 102件と、完了済み native ranking artifact を入力に、`META_TRAIN` 90件、`META_DEV` 6件、`META_FINAL` 6件を持つ hash-bound manifest を生成した。評価用スケジュールと teacher collection 用スケジュールを分離し、現行の明示的 training-local permission がある `tomatomato_archaludon` と `lucifer19_battlecore` 以外は teacher/behavior source に入らない。

これは Kaggle の実際の prevalence を観測したものではなく、既存ローカル pool の strength・candidate regret・archetype diversity を使った `observed_pool_strength_distribution` である。したがって、candidate 選抜用の分布は固定できたが、外部 asset の permission を拡張したものではない。

## 実行した変更

| 種類 | パス | SHA-256 |
|---|---|---|
| 実装 | `src/mage_ptcg/meta_specialist/meta_distribution_v1.py` | `0bbb44161f89e9eaac6065e23166db17563ee7c66a0d1494f8819b9aa4f9c941` |
| builder | `scripts/build_meta_distribution_manifest_v1.py` | `041053cdec0f92fea36a02f99195562f474a7e563d5fc018c693c3f247de849e` |
| test | `tests/meta_specialist/test_meta_distribution_v1.py` | `504210c9d5d2f45262230bb5db15ff1160a6fcfa6a370d086901c85bec8df5da` |
| spec | `docs/superpowers/specs/2026-08-13-autonomous-meta-finetuning-design.md` | `87554a35d0c0a4d2e65fe2b6b3bf5f1e0aee35a0b634dc2c1c990ca0e5417199` |
| plan | `docs/superpowers/plans/2026-08-13-autonomous-meta-finetuning.md` | `de1d72e0b6f648bfd82dea6e3ae3b93cf9357f0a81910cc9bdf94306fa454ce5` |

## Primary artifact

- manifest: `runs/final-sprint-autonomous/meta-distribution-v1/manifest.json`
  - SHA-256: `e430f1284e587e7f301f9e29abe377faad79ff5120a39c42b7b2f6a5223dd2ae`
  - schema: `meta-specialist-meta-distribution-v1`
  - rows: 102
  - split: `META_TRAIN=90`, `META_DEV=6`, `META_FINAL=6`
  - candidate: `tomatomato_archaludon`
  - `training_authority=false`, `promotion_authority=false`, `submission_authority=false`, `research_only=true`
- schedule: `runs/final-sprint-autonomous/meta-distribution-v1/meta_schedule.json`
  - SHA-256: `9db6e1d9ea3a6913f2080ac5ca4f08b748ed9fa1469cfd0a7a74d0253cb16b6a`
  - evaluation curriculum quota: 512
  - permission-filtered teacher quota: 256

## Source artifacts

The manifest byte-binds:

- `docs/evidence/strong-asset-census-20260812.json`
- `runs/meta-specialist-asset-ranking-primary-fast96-20260812/asset_ranking.json`
- `runs/meta-specialist-asset-ranking-top3-confirm384-20260812/asset_ranking.json`
- `runs/meta-specialist-asset-ranking-top3-confirm384-block2-20260812/asset_ranking.json`
- `runs/meta-specialist-asset-ranking-top3-confirm384-block3-20260812/asset_ranking.json`
- `runs/meta-specialist-asset-ranking-top3-confirm384-block4-20260812/asset_ranking.json`
- `runs/meta-specialist-asset-ranking-r7-diagnostic-20260812/asset_ranking.json`

The builder aggregates score by completed games, ignores runtime-fault-only evidence as a zero-strength claim, and retains unmeasured rows with `evidence_status=unmeasured_or_runtime_infeasible`.

## Weight construction

Each row stores the raw observed strength, candidate hard-negative score, frequency proxy, diversity contribution, and normalized components. The final weight is:

```text
weight = 0.60 * top_meta_component
       + 0.25 * hard_negative_component
       + 0.15 * diversity_component
```

- `top_meta_component`: observed strength rank blended with archetype prevalence proxy.
- `hard_negative_component`: current candidate's observed loss rate against that opponent when available; otherwise conservative `1 - observed_strength`.
- `diversity_component`: inverse archetype count, normalized.

The three component masses each sum to one, and final row weights sum to one. The formula deliberately stays within the requested 50–65% top-meta, 20–35% hard-negative, 10–20% diversity bands.

## Permission boundary

The current census has only two `training_usable=yes_*` rows:

- `tomatomato_archaludon`
- `lucifer19_battlecore`

`plamen06_steel` is a strong native evaluation member but is not training-local in the current manifest. `local_eval_only` rows remain useful for evaluation schedule and hard-negative diagnostics, but cannot supply teacher labels, behavior probabilities, or derivative submission bytes. The permission-filtered schedule therefore contains only the two authorized rows and is not a claim that the full 102-pair pool may be used for training.

## Split policy

`META_DEV` is the known fixed-six evaluation set:

```text
kiyotah_lucario
sue124_alakazam
skarin_dragapult
ozawa_crustle_v2
nihei_megalopunny
yaroslav_crustleaware_lucario
```

`META_FINAL` is held out from candidate selection until the final gate:

```text
plamen06_steel
lucifer19_battlecore
aristophanivan_multiply
nihei_alakazam
dashimaki360_crustlecounter
ozawa_starmie
```

All other pool rows are structurally in `META_TRAIN`; actual teacher collection still requires `training_allowed=true`. No final row is emitted in either schedule output.

## Verification

```text
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/pytest -q -s tests/meta_specialist/test_meta_distribution_v1.py
5 passed in 0.51s
```

The real builder command was:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src \
  .venv/bin/python scripts/build_meta_distribution_manifest_v1.py \
  --output runs/final-sprint-autonomous/meta-distribution-v1/manifest.json \
  --eval-quota 512 --train-quota 256
```

`json.tool`, source verification, and the loader's SHA check pass. No native `main.py`, production evaluator, deck, Champion, package, CABT submission, or longrun process was changed or started by this step.

## Next decision

The manifest is now the only permitted input for the next workstream. The next high-value operation is a read-only native tuning-surface audit for tomato/Lucifer/plamen, followed by a native-preserving bounded override candidate. Repeating hard-label BC or AWR is explicitly out of scope. A training pilot may use only the permission-filtered two-row schedule; a full-pool policy selection race remains evaluation-only until permission is separately evidenced.

