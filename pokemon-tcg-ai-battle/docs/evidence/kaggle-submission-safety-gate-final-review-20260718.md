# Kaggle Submission Safety Gate 最終レビュー（2026-07-18）

## 結論

この記録は **REJECTED_KAGGLE_VALIDATION** である。`neural-student-v1-entryfix` は local G1〜G6 を PASS したが、Kaggle 再提出の Validation Episode で source root `/kaggle_simulations/agent` と cwd `/kaggle/working` が分離する条件を再現できず失敗した。旧 `LOCAL_SUBMISSION_READY` と verification JSON は無効であり、後継は [cwd-decoupled evidence](kaggle-submission-safety-gate-cwd-decoupled-20260718.md) を正とする。

## Candidate と G6

- archive: `runs/offline-training-v1/offline-long-run-actual-20260718-r1/submission_candidate/neural-student-v1-entryfix/submission.tar.gz`
- archive SHA-256: `dd33517fb7758fc671b27cfe672a0367835761061f2121747430e084895178d8`
- model SHA-256: `2318b7ff7f1d981ec4181ae01cc681b15c057f5b4daba3d5f900e71e2144eb8f`
- semantic model hash: `94564328a10f1e914beb63073235722694093e281905b5cbd546b2a35742dea4`
- challenger runtime: extracted archive の `main.py`。candidate sidecar、workspace `main.py` / `src/` / `models/`、`run_actual_agent_viability.py` adapter は使用しない。
- G6: seed `34000`、20 games、15–5–0、`DONE/DONE`、crash / invalid / timeout は各 0、`external_files_read: []`。
- fallback telemetry: `AVAILABLE`、selected count 816、fallback reasons `[]`。

## 監査所見

- CRITICAL: なし。
- HIGH: 旧 G6 は sidecar `manifest.json` を展開先へコピーし、workspace viability adapter を使用していたため archive-only ではなかった。旧 `LOCAL_SUBMISSION_READY` は撤回し、G6 を extracted `main.py` の直接実行へ置換した。
- MEDIUM: wrapper の verifier commit 完全一致は docs-only descendant commit でも有効な検証を拒否する。verifier bytes SHA-256 一致と記録 commit が現 HEAD の ancestor であることへ変更した。
- LOW: repository root `main.py` には raw exec 時の `__file__` 参照が残る。entryfix archive の generated `main.py` とは別問題であり、root `main.py` を直接提出してはならない。Champion/default の Rule Agent v0 は変更していない。

## 検証

- focused: Safety Gate core 39 passed、G6 archive-only fixtures 9 passed、`tests/test_offline_training_v1.py` 32 passed（30秒実行枠のため 29 + 3 に分割）。
- full regression: 1,055 tests を収集した正式コマンドは既知の `tests/test_collect_offline_training_v1_evidence.py::test_run_command_safe_timeout_and_child_cleanup` で最初の失敗を再現した。`--maxfail=1` で 200 passed / 1 failed（18.95秒）、当該 test 単体でも 1 failed（0.67秒）。全件完走 JUnit は生成されなかったため `FULL_REGRESSION_BLOCKED` とし、PASS と扱わない。
- docs validation と UTF-8 validation: 実行結果は本 evidence の JSON に記録する。

## 不変条件

Champion/default は Rule Agent v0、Promotion は `NO_DECISION`、model bytes は不変、Kaggle resubmission は未実行である。
