---
project: MAGE-PTCG
evidence_type: c4-actual-trained-viability
as_of: 2026-07-16
---

# C4 actual-trained Student v0

## 結論

actual cabt Rule Agent v0 self-play の32 game収集を、collectorの実測engineering gateで `ACTUAL_TRAINING` と判定した。canonical bundleをconsumerが受理し、CPUで `ACTUAL_TRAINED` modelを生成した後、actual Gate Aと20-game Gate Bを完走した。これは Rule Agent v0 の模倣とviabilityの証跡であり、Rule v0を上回ること、Champion／default変更、Promotionを意味しない。

## Collection と bundle

|項目|値|
|---|---:|
|run id / requested / completed|`actual-train-rule-bc-v0` / 32 / 32|
|episodes / supervised decisions / candidates|32 / 1,628 / 10,278|
|private bindings / chosen targets / teacher targets|1,628 / 1,628 / 1,628|
|teacher / objective / quality|Rule Agent v0 / `RULE_IMITATION` / `RULE_ONLY`|
|expected ceiling|`RULE_LEVEL`|
|split|train 26 / validation 6 / overlap 0|
|duplicate episodes / decisions|0 / 0|
|invalid target / non-finite|0 / 0|
|privacy scan / violations|true / 0|
|eligibility|`ACTUAL_TRAINING` / true|

collector sourceは `rule-bc-v1` compatibleで、validatorは row/binding 1,628、candidate 10,278、duplicate 0、privacy violation 0 を再確認した。完了gameはresume stateに記録され、今回の実行では再実行していない。

canonical bundleは `.local_artifacts/c4_actual_training/bundle/` にのみ置く。public rootは `rule-bc-v1.jsonl`、dataset/split manifest、public summaryだけであり、private binding ledgerはsource private directoryに保持し、hash/count/path roleだけを公開manifestへ継承する。

|bundle field|値|
|---|---|
|purpose / performance eligible|`ACTUAL_TRAINING` / true|
|source kind|`ACTUAL_CABT_RULE_BC`|
|dataset hash|`dd16e662d2bf4f981bcff5fe6ded6a78e483ae869e9cac67714ab7996f980d7a`|
|dataset manifest hash|`ad84b83fe173896fa443562aadc093b09f1668eb1d517fcd28d06fdc14d154a2`|
|split hash|`e08b61f63c2fb49ead0cf1038784501847b0fd3bff4e102564e6ebf9b523ec81`|
|split manifest hash|`2b0cb5e9b67468c66e18b02b0e2dd214c204ca698a9d75a22476df91171aca36`|
|feature schema hash|`552d3bf4c4792d84fc509bfa51c322e23e84dd6c04697f0dab8dca80ea864484`|

consumer `--validate-only` は accepted=true、actual purpose/eligible、privacy 0、non-empty train/validation、overlap 0 を確認した。`split_manifest.assignments`を唯一の分割正典としてtrainer、evaluator、builderへ渡し、内部hash splitは再実行していない。

## Training

|項目|値|
|---|---|
|backend / device / seed|`python-float-full-batch` / CPU / `NOT_APPLICABLE`|
|train / validation examples|1,364 / 264|
|train top-1 / top-3 fidelity|0.8959 / 1.0|
|validation top-1 / top-3 fidelity|0.8182 / 1.0|
|validation legal action rate / fallback rate|1.0 / 0.0|
|validation holdout loss|0.90729|
|model hash / size|`10d54013935982ef9c9342e2b833c307f9a36babaaf7764db94943a882b15a1a` / 1,748 bytes|
|reload|PASS（model hash、size、schema、purpose、privacy、bundle provenanceを検証）|

model manifestは `artifact_purpose=ACTUAL_TRAINED`、`performance_eligible=true` と、dataset／bundle split／source split hash、teacher由来のbundle provenance、privacy statusを継承した。source collection canonical baseは `4590a85d6f78a0bd413c41ad945747f59e221a5e`、model build work revisionは `cec13c83d742860348fcb55d307f7ff92215825b` である。

## Actual gates

|項目|Gate A|Gate B|
|---|---:|---:|
|status|`CLEAN_PASS`|`CLEAN_PASS`|
|games completed|1 / 1|20 / 20|
|Student W-L-D|1-0-0|13-7-0|
|side split（Student seat 1 / 0）|1-0-0 / 0-0-0|5-5-0 / 8-2-0|
|inference requested / completed|15 / 15|508 / 508|
|Student selections / fallback|15 / 0|508 / 0|
|legal action rate|1.0|1.0|
|privacy violations / invalid / crash / timeout|0 / 0 / 0 / 0|0 / 0 / 0 / 0|
|Student latency p50 / p95 / max (ms)|0.593 / 0.646 / 0.651|0.561 / 1.356 / 88.822|
|resume duplicate execution|false|false|
|artifact hash|`523fe798d4137a891bed13119f28cae5740f611f9a4d6c72da1386bb6795ff21`|`50c7681e675f4935ed21f2f1cda561b19ad778f526f6fb165d82b74f77bf606a`|

Gate Bはside-swap済みの20-game viability smokeである。engine seedはunsupportedでoutcomeは非決定的であり、20 gameの13-7-0を性能優位、non-inferiority、Promotion evidenceへ拡張しない。

## 安全性と残リスク

- Championとsubmission defaultは Rule Agent v0、Promotionは `NO_DECISION` のままである。`main.py`と`deck.csv`は変更していない。
- exporterはCLI引数からeligibilityを設定しない。collector manifest、validator、hash、split、privacy、target/binding数の全一致があるsourceだけを `ACTUAL_TRAINING` としてexportする。`--require-actual-training`は条件未達sourceを拒否するだけで、昇格しない。
- 本modelはRule imitationであり、Rule v0超えを保証しない。より大きい評価、独立seed、paired confidence interval、Promotion Gateは本タスクの範囲外である。

## 検証

- focused: `tests/test_c4_data_ops.py`、`tests/test_c4_actual_training_bundle.py`、`tests/test_actual_agent_viability.py` → 68 passed。
- full regression: `tests/test_*.py` を45 fileのmanifestで各1回実行 → 636 passed、0 failed、0 skipped、0 xfailed（252秒）。
- curation: `python scripts/curate_team_knowledge.py --check` の全項目がtrue。
- docs: `python scripts/docs/validate_docs.py` → 12 canonical documents validated。
- submission: Rule Agent v0 artifact build/verifyとtarball-only clean-roomをPASS。clean-roomはdeck 60枚、mandatory unknown selection `[0, 1]`を確認した。
