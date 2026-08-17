---
project: MAGE-PTCG
document_status: evidence
canonical_source: git
language: ja
title: V4 public trace META_TRAIN common24 diagnostic
---

# V4 public trace META_TRAIN common24 diagnostic

## 結論

既存 Wave4 strict-paired seed1 V4 checkpoint を、`performance_first_broad_pool_v1.json` の検証済み24 opponentへ両seat・各2局（96局）流し、V4 runtimeの公開traceと終端WDLだけを新規run rootへ保存した。96/96 `DONE`、fault 0、54勝42敗（56.25%）であり、seat0は21/48、seat1は33/48だった。

公開traceは4,678行だが、representable action rowは4行、action eventは8件（すべて`SKILL`、8勝0敗）だけだった。したがって action-conditioned signal は sparse で、`usable_signal=false`、`ready_for_candidate_screen=false`。この結果から候補screen、384局、policy update、training、submissionは起動しない。

## 入力と権限境界

- checkpoint: Wave4 strict-paired seed1、file SHA `ec08ace5fb25352758a9f950694134ef6544ec69b23c00047101e588e3d06319`、tensor SHA `17682967a16c955ccd009858e036ef69e54d3efcd32bb0de83bebb64aa7c0244`
- subject deck: `opponents/public_archaludon_cinderace_r7/deck.csv`、SHA `42165967b565dd42ec426ecccfe79bfa7d72aa8306590e149dface0ee8bd530e`
- broad config SHA `832273ff656280d2556c9df09a9a3db9f2564a181be78a3e658509d3b396209b`
- opponent pool manifest SHA `e0013cf31b3e6e24db54591faeef6f092b9ebf85247bd0e57598eb8d447f20ca`
- evaluator SHA `0cbac2789e08758d14783922c5c7145f25701a47d978b3d9df9d132aec4eed84`
- seed recipe: base `14910000`（trace runはWDL runと分離するため `14910000..14910095`）、engine seed support false
- selected assets: 全24件 `usage_boundary=local_eval_only`、`source=public`、policy identity重複なし
- `native_action_labels_saved=false`、`teacher_labels_saved=false`、`private_fields_saved=false`、全authority false

`local_eval_only` はローカル対戦の実行許可であり、teacher、training、submissionの権限を付与しない。native opponentのaction labelは保存も学習もしていない。

## 一次artifact

Run root: `runs/final-sprint-autonomous/v4-seed1-public-trace-meta-train-common24-96-serial-20260813-v1/`

| artifact | SHA-256 |
|---|---|
| `summary.json` | `df9148eb8550e0f5ecba8385335e6d02a15d4dcb86d186a6f80a1d55985a3137` |
| `ledger.jsonl` | `6d39fe80c20bc8360360396fe180fef04b3d2b3864ce55e8b6f283ee49095630` |
| `public-trace.jsonl` | `40ca755cb0706033a7a5eaff2a695458e535346a37b5e462677476742bdc1afb` |
| `public-action-table.json` | `51213c7fde74953d46bbd95091bf50095641beeae82d20b02ffd4113670849d1` |

Table semantic SHAは`978fd1d3d8975ebb0ea17d03ff29f76aba5821d7c104a3c5bab0a199d73058f8`。gate理由は`insufficient_action_examples`、`insufficient_competing_action_types`、`insufficient_mixed_sign_action_types`である。trace privacy scanは forbidden token 0件、redacted row 1,248、overflow 0だった。

## 再現コマンド

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python \
  scripts/run_v4_public_trace_meta_train_v1.py \
  --config configs/meta_specialist/performance_first_broad_pool_v1.json \
  --checkpoint runs/meta-specialist-v4-archaludon-dagger-wave4-strict-paired/bc-checkpoints/seed-1/best-recurrent-bc-v4.pt \
  --subject-deck-csv opponents/public_archaludon_cinderace_r7/deck.csv \
  --subject-archetype-id archaludon \
  --output runs/final-sprint-autonomous/v4-seed1-public-trace-meta-train-common24-96-serial-20260813-v1 \
  --games-per-seat 2 --base-seed 14910000 --max-steps 2000

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python \
  scripts/build_v4_public_trace_action_table_v1.py \
  --run-root runs/final-sprint-autonomous/v4-seed1-public-trace-meta-train-common24-96-serial-20260813-v1
```

実装SHAは trace runner `e984e5e7e58a50d3e5f461ea28b19b87abdaf64b5b9e255a1524c3ea90d51d5b`、table builder `4e979ec4edf28d848d6ae696a31279b6270576522c4c3868efb3fd1b96ff13d1`。focused testsはそれぞれ `4 passed`、`2 passed`。既存production、checkpoint、pool manifest、WDL run artifactは変更していない。

## 判断

この96局はV4自身の公開traceを使ったMETA_TRAIN入口の診断として有効だが、action supportが極端に少ないため policy updateの入力には昇格しない。次に進む条件は、同じpermission-safe sourceで状態特徴を追加し、複数action type・十分なmixed-sign supportを得て、独立screen前に`ready_for_candidate_screen=true`を満たすことである。現時点では長時間学習のGOではない。
