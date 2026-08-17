# CG P1 standard wrapper closure — 2026-08-15

## 結論

現 BestKnown の `cg-lethal-target-v1` archive を、既存の `kaggle-agent-package-v1` 外側wrapperへ接続できる状態にした。標準 verifier は sidecar を一時退避した閉じたinventory上で、CG runtime parity・canonical archive・4局clean-room CABTを `PASS` と判定した。外部 Kaggle Submit verifier／契約はリポジトリに存在しないため、結果は `PREFLIGHT_ONLY` のままであり、提出やChampion変更は行っていない。

## 対象とidentity

| 項目 | 値 |
|---|---|
| policy | `cg-lethal-target-v1` |
| source candidate | `runs/final-sprint-autonomous/cg-policy-screen-v1-retry-safe4-20260814/candidates/cg-lethal-target-v1` |
| wrapper | `runs/final-sprint-autonomous/kaggle-cg-wrapper-p1-20260815-v1/` |
| archive SHA-256 | `e724b48059ac16a43ea28030647fea8b516caf7f9c1ac1331659e04caf369f02` |
| policy/main.py SHA-256 | `1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9` |
| deck SHA-256 | `2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19` |
| outer manifest SHA-256 | `d1afa22cf956038df207a7cea77b05562b6cb6c89632af83e297575d631f66f0` |
| verifier report SHA-256 | `ae0c144db54b90e49e2940fa84a134835af0af4b79279253e17672dfe895a2c0` |

## 変更

- `agent_kind="cg"` を標準wrapper loader／dispatchへ追加した。
- 現行P1の `schema_version` と path-keyed `files` mappingを、旧いCG adapterの `artifact_schema_version`／list形式と混同せず検証できるようにした。
- CG package inventoryで、sampleのoptional runtime memberを「存在必須」ではなく「存在する場合だけ許可」とした。
- `scripts/build_cg_kaggle_submission.py` と `configs/kaggle/cg_p1.json` を追加し、既存candidateから標準wrapperを再生成できるようにした。`scripts/build_kaggle_submission.py` の `agent_kind="cg"` からも同じbuilderを呼べる。
- CGのreadinessは、local runtimeがPASSでも remote submission contractがUNKNOWNなら `PREFLIGHT_ONLY` とし、誤って提出可能と断定しない。
- `python -I` でuser-site依存を持たないよう、`kaggle_environments` を実import probeした `.venv/bin/python` をclean-room smokeへ選択するようにした。

## 実測結果

`TMPDIR=/tmp PYTHONPATH=.:src python scripts/verify_kaggle_submission.py --artifact runs/final-sprint-autonomous/kaggle-cg-wrapper-p1-20260815-v1`

- verifier process: exit `0`
- wrapper verification: `PASS`
- CG runtime parity: `PASS`
- clean-room CABT: `4/4 DONE`, `faults=0`, `illegal_actions=0`
- readiness: `PREFLIGHT_ONLY`
- blocker: `remote_contract_confirmation_required`
- contract confirmation: `CONTRACT_CONFIRMATION_REQUIRED`

検証結果JSONは `runs/final-sprint-autonomous/kaggle-cg-wrapper-p1-20260815-v1-verifier.json` に保存した。これはwrapper root外の診断artifactであり、wrapper inventoryへ混入させていない。

## 検証範囲

```text
TMPDIR=/tmp PYTHONPATH=.:src pytest -q \
  tests/test_build_cg_kaggle_submission.py \
  tests/test_verify_kaggle_submission.py \
  tests/test_verify_root_cg_submission_candidate_v1.py \
  tests/meta_specialist/test_cg_p1_active_threat_attach_v1.py \
  tests/meta_specialist/test_cg_p1_policy_candidate_v1.py \
  tests/meta_specialist/test_run_cg_p1_policy_candidate_v6_screen_v1.py
```

結果は `65 passed, 10 skipped`。対象スクリプト／テストの `py_compile` と `git diff --check` もPASSである。

## 残課題と境界

- Kaggle remote Submit verifier・実際のSubmit UI/API契約は未同梱で、外部送信可能性は未確定である。
- P1の性能、BestKnown、Champion、production、submission authorityは変更していない。
- fresh・unused・smoke-ready public metaは引き続き0件であるため、既評価metaのCABT blind retryやP2/P3昇格は行っていない。
- commit、push、Kaggle submitは行っていない。
