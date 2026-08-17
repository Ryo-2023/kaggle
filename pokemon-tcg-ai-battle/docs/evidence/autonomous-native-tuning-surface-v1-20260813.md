---
title: Native tuning surface audit v1
date: 2026-08-13
status: research-only
promotion_authority: false
---

# 結論

native sourceをimport・編集せずに、tomato/Lucifer/plamenの直接tuning surfaceをASTとdeck SHAから監査した。3件とも `score_*` と `agent` の native fallback があり、tomato/Luciferは rule score/threshold の直接調整候補、plamenはそれに加えて optional search の `CAND/MAXD/MARGIN/BUDGET` が外出しされている。

これは候補パラメータの存在を示すだけで、勝率改善や変更 permission を意味しない。次の pilot は source の研究コピーまたは環境変数で候補を作り、未知局面・不正 action・timeout は native fallback へ戻す必要がある。

## Artifact

- audit: `runs/final-sprint-autonomous/native-surface-v1/audit-v3.json`
- audit SHA-256: `abd20c6c2badc7d2471fb9aeac5bc95c298c6835a02b98fc0506191202078654`
- source implementation: `src/mage_ptcg/meta_specialist/native_tuning_surface_v1.py`
- source implementation SHA-256: `f4012a0b7afcd1ef622d5099492a17449f450036401e3836f1885bffcbc7440f`
- CLI: `scripts/audit_native_tuning_surface_v1.py`
- tests: `tests/meta_specialist/test_native_tuning_surface_v1.py`

## Observed surfaces

| pair | classifications | tunable surface | fallback |
|---|---|---|---|
| `tomatomato_archaludon` | `DIRECT_PARAMETER_TUNABLE`, `RULE_EDIT_TUNABLE`, `NATIVE_FALLBACK_READY` | `_SETUP_ACTIVE_PRIORITY`, `_ICE_CREAM_HP_THRESHOLD`、`score_*`/`apply_overrides` | agent exception fallbackあり |
| `lucifer19_battlecore` | `DIRECT_PARAMETER_TUNABLE`, `RULE_EDIT_TUNABLE`, `NATIVE_FALLBACK_READY` | `_SETUP_ACTIVE_PRIORITY`, `_ICE_CREAM_HP_THRESHOLD`、`score_*`/`apply_overrides` | agent exception fallbackあり |
| `plamen06_steel` | 上記 + `SEARCH_ROLLOUT_READY` | `_SETUP_ACTIVE_PRIORITY`, `_ICE_CREAM_HP_THRESHOLD`, `ENABLE_SEARCH`, `CAND`, `MAXD`, `MARGIN`, `BUDGET` | heuristic fallbackあり |

`plamen06_steel` の search は `cg.api` の high-level search に限定され、失敗時に heuristicへ戻る実装を持つ。source上の optional search は local evaluatorでの runtime cost が大きくなり得るため、通常queueへ slow candidateを混ぜず、pilotでは budget cap と hard kill を固定する。

## Boundary

- 3 pairの `main.py`/`deck.csv` bytesは変更していない。
- `local_eval_only` の permissionを直接変更・拡張していない。
- `classifications` は実装観測であり、training/submission許可ではない。
- native baseline SHAは後続 candidate descriptorへ必ず保存する。
- adapterは opponent IDをruntime observationへ渡さず、schedule側だけでmeta weightingを適用する。

## Verification

```text
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src \
  .venv/bin/pytest -q -s tests/meta_specialist/test_native_tuning_surface_v1.py
4 passed in 0.21s
```

`py_compile` と `git diff --check` も pass。次の実装は、sourceを変更せずに candidate copyを生成し、parameter overrideが native fallbackを壊さないことを focused test で固定する。
