---
project: MAGE-PTCG
slice: C5
status: offline-wiring-validated
as_of: 2026-07-16
---

# C5 Curated Knowledge Wiring

## 結論

`artifacts/team-knowledge-curated`をC5 offline buildへopt-inで接続した。authoritativeなteacher sourceは`executable_teacher_registry.jsonl`だけであり、22件を重複なく読む。`canonical_rules.jsonl`の`ACCEPT_TEACHER_RULE`はprovenance参照だけで、teacherとして追加読込しない。Championとsubmission defaultはRule Agent v0のままである。

## 実装と安全境界

- `src/mage_ptcg/distillation/knowledge.py`はcurated schema、teacher ID、canonical/macro provenance、scope、observable condition、score/priority、hard constraint IDをfail closedで検証する。
- C5 buildの`--curated-knowledge-dir`はoffline provenanceをsummaryへ出力する。C4 Rule BCには規則ごとのobservable-condition attestationとprivate coreを含まないため、自然言語規則を推測実行しない。明示的なpublic ActionKey adapterがないbuildではteacher適用は0、skipとして記録する。
- `apply_priors`はcabt相当のselection contract、全候補の一意なActionKey mapping、range、重複、min/maxを先に検査する。registration、unknown type、空候補、ambiguous mapping、parse failureではRule v0 labelへfallbackする。
- hard constraintはsoft scoreより先であり、search priorは`ACCEPT_SEARCH_PRIOR`だけを読む。`HOLD_FOR_EXPERIMENT`と未解決contradiction 3件をpolicyへ入れない。
- evaluation weightは`(wins + 0.5 * draws) / games`をW-L-Dから再計算する。source `win_rate`とKaggle scoreは使用せず、weightはoffline soft用途だけでpromotion moduleへ渡さない。

## Curated pack 実測値

| 項目 | 値 |
|---|---:|
| teacher registry rows / distinct loaded | 22 / 22 |
| canonical teacher double-count | 0 |
| hard constraints | 5 (`HC-000001`〜`HC-000005`) |
| search priors loaded | 66 |
| evaluation records loaded | 138 |
| evaluation weights created | 37 |
| non-accepted evaluation records excluded | 96 |
| accepted but W-L-D不足のrecords excluded | 5 |

fixture C5 buildでは3 episodes、3 decisions、3 labelsを生成し、同一入力・configでcanonical dataset bytesとmanifest summaryが一致した。これはwiringのdeterminism確認だけであり、actual cabt evidenceではない。unknown/ambiguous mappingは0件、hard constraint rejectionは0件、fallbackは0件だった（adapterなしのためteacher rulesは適用0・skip 22）。

## 検証

```bash
python -m pytest tests/test_targeted_distillation_v0.py tests/test_student_v0.py tests/test_curate_team_knowledge.py tests/test_bounded_search_v0.py tests/test_knowledge_wiring.py -q
# 71 passed

python scripts/curate_team_knowledge.py --check
# 全 integrity check true

python scripts/build_submission.py --output-dir /tmp/rule-v0-c5-wiring
python scripts/build_submission.py --verify-dir /tmp/rule-v0-c5-wiring
# clean-room pass、Rule v0 runtime filesだけ
```

full suiteは環境の単一プロセス実行上限で途中終了したため、全524 nodeの一括結果は未確定である。root test bundleは185 passed / 5 skipped、C5/C4/Rule/belief bundleは117 passed / 2 skippedまで確認した。actual cabtは`NOT_RUN`（`CAPABILITY_UNAVAILABLE`）、promotionは`NO_DECISION`である。

## 残リスクと次のコマンド

public observationからobservable conditionとActionKeyを明示的に結ぶadapterは未実装である。追加する場合もsubmission defaultを変えず、adapter contract testとactual provenanceを先に追加する。

```bash
python -m pytest -q
```
