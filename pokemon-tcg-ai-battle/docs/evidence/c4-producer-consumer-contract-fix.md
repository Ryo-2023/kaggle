---
project: MAGE-PTCG
evidence_type: c4-producer-consumer-contract-fix
as_of: 2026-07-16
---

# C4 producer / consumer canonical bundle contract

## 結論

既存 4-game actual cabt smoke を read-only source として canonical C4 bundle へ export し、consumer `--validate-only` が PASS した。bundle は `TEST_FIXTURE` / `performance_eligible=false` であり、学習、`ACTUAL_TRAINED` build、actual Gate A/B はこの作業では実行していない。

## Canonical contract

- dataset manifest: `c4-actual-training-bundle-v1`
- split manifest: `c4-actual-episode-split-v1`
- root files: `rule-bc-v1.jsonl`、`dataset_manifest.json`、`split_manifest.json`、`public_summary.json`
- split の唯一の正典: `split_manifest.assignments`。actual bundle経路の trainer/evaluator/builder は `--split-manifest` を渡し、内部 hash split を再実行しない。
- private candidate binding は bundle root にコピーせず、`path_role`、raw binding SHA-256、record count、`trainer_input=false` の descriptor だけを manifest に載せる。

## 4-game smoke export

source は `pokemon-tcg-ai-battle-c4-data-ops-v0/.local_artifacts/c4_runs/smoke-4g/` を read-only で使用した。source の 171 decision のうち、Rule target が空の optional prompt 19 件は BC supervision に使えないため、export dataset からのみ除外した。source row/binding は変更していない。

|項目|値|
|---|---:|
|bundle purpose / eligibility|`TEST_FIXTURE` / false|
|episode / supervised decision / candidate|4 / 152 / 875|
|split|`episode_group_hash_v0`、train 3 / validation 1、overlap 0|
|dataset hash|`4e22d03c02087ec281b951d30753b54dab05adf9b756a58f80377f6fc72c20ff`|
|dataset manifest hash|`9f85d166ab15e1d9c283e23629f66abd7b2b9b2243e8ba83846ed74eb5000a15`|
|split hash|`e670002e1aa073ddfdf5da5311408cf024663fbcc1d47f6da8407513f3a186c7`|
|split manifest hash|`4db5cc8b15c87c90f990ede32d79db9718e9b90cfc35c18c14cd72fdf1ccbb45`|
|privacy scan / violation|true / 0|

`dataset_hash` は canonical JSON row list、`dataset_file_sha256` は export JSONL bytes を対象に別計算する。dataset/split manifest はそれぞれ self `manifest_hash` を持ち、public summary は両hashと split hash を継承する。feature schema hash は consumer の `feature_schema()` を唯一の方式として使用した。

## 検証と安全性

- exporter → consumer `--validate-only`: PASS
- `TEST_FIXTURE` の `--train`: fail-closed rejection
- public root に private binding、candidate payload、raw observation、absolute path はない
- Champion / submission default: Rule Agent v0
- Promotion: `NO_DECISION`

生成物は Git 管理外の `.local_artifacts/c4_contract_fix/smoke-4g-bundle/` にある。

## Actual-training follow-up

このcontractは32-game actual collector runに対しても適用した。exporterはCLIからpurposeやeligibilityを変更せず、collectorのengineering gate、validator、hash、private binding count、target count、external split assignment、privacyを再検証してからだけ `ACTUAL_TRAINING` を出力する。条件未達sourceに`--require-actual-training`を指定すると拒否し、`TEST_FIXTURE`をactual modelへ昇格させない。

actual bundleからのtrain/evaluate/buildとGate A/Bの結果は[actual-trained evidence](c4-actual-trained-v0.md)を正とする。
