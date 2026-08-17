---
project: MAGE-PTCG
evidence_type: c4-student-actual-training-bundle-contract
as_of: 2026-07-16
---

# C4 actual training bundle consumer contract

## 結論

`scripts/accept_c4_actual_training_bundle.py` は、data-ops が生成した bundle を consumer 側で fail-closed に受け入れる。collector、data-ops、submission default には接続しない。`TEST_FIXTURE` は validate-only の契約試験に限り、`ACTUAL_TRAINED` model へ変換できない。

## Bundle layout

bundle root は次の regular file を必須とする。dataset manifest の `dataset_file` は root 内の `rule-bc-v1.jsonl` を指す。

```text
rule-bc-v1.jsonl
dataset_manifest.json
split_manifest.json
public_summary.json
```

dataset manifest の schema は `c4-actual-training-bundle-v1`、split manifest の schema は `c4-actual-episode-split-v1` とする。dataset manifest は `artifact_purpose`（`ACTUAL_TRAINED` または `TEST_FIXTURE`）、`performance_eligible`、`dataset_schema_version=rule-bc-v1`、`dataset_file`、dataset bytes/content hash、self `manifest_hash`、feature schema/version/dimensions、episode groups/counts、teacher source/version/quality、training objective、trace provenance hashes、privacy scan/result、canonical base SHA を必須とする。

split manifest は dataset hash、`split_method`、全 episode group をちょうど一度覆う `assignments`（`train` / `validation`）、train/validation count、overlap/duplicate count 0、assignment `split_hash`、self `manifest_hash` を持つ。public summary は dataset/dataset-manifest/split-manifest hash、split hash、purpose/eligibility と privacy result を同値で再掲する。public JSON の absolute path は拒否する。

private binding を別ファイルに置く場合、dataset manifest の `private_binding` は relative pathまたは private `path_role`、SHA-256、record count、`trainer_input` を持つ。`path_role=private_bindings` は外部 private ledger の provenance descriptor であり、raw binding を public bundle root へ複製しない。private binding は public summary へ混在させない。

## Acceptance checks

- JSON/JSONL、regular-file、hash、self manifest hash、`rule-bc-v1` schema を検証する。
- row を既存 `RuleBCExample` として再検証し、target は legal candidate 内、candidate feature matrix は既存の96次元かつ有限値とする。
- episode/decision/candidate/chosen-target counts と group split を再集計し、train/validation non-empty と overlap 0 を確認する。
- teacher source/quality、trace provenance、privacy scan executed、privacy violations 0 を要求する。
- `SMOKE_ONLY`、purpose と eligibility の不整合、public manifest の absolute path、missing/malformed/hash mismatch を拒否する。

## Training handoff

`--validate-only` は acceptance summary だけを出す。`--train --output-root ...` は、`ACTUAL_TRAINED` / `performance_eligible=true` の accepted bundle に限り、accepted `split_manifest.assignments` を渡して次の既存 CLI を順に呼ぶ。

1. `scripts/train_student_v0.py`
2. `scripts/evaluate_student_v0.py`
3. `scripts/build_student_actual_artifact.py`

builder には dataset manifest hash、split manifest hash、source split hash を渡し、生成 model manifest が input dataset/split provenance を継承したことを再検証する。model は `artifact_purpose=ACTUAL_TRAINED` かつ `performance_eligible=true` でなければ拒否する。4-game smoke は `source_kind=ACTUAL_CABT_COLLECTION_SMOKE` を保ちつつ `TEST_FIXTURE` として validate-only を許可し、train を拒否する。producer→consumer smoke の結果は[contract-fix evidence](c4-producer-consumer-contract-fix.md)を正とする。

## Test fixture boundary

unit test は canonical fixture を `TEST_FIXTURE` bundle として明示し、性能 evidence ではない。fixture の validation acceptance は、collector 由来の actual provenance、actual training、paired performance、Promotion を主張しない。

## Residual risk

consumer は Claude data-ops の実 manifest に依存する。field 名または split hash canonicalization が本契約と異なる場合、acceptance は安全側に拒否する。その場合は collector を推測修正せず、bundle schema を双方で明示合意してから更新する。
