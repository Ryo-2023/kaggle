# Kaggle Submission Safety Gate: cwd 分離再検証（2026-07-18）

## 結論

旧 candidate `dd33517…` は Kaggle Validation Episode で失敗したため `REJECTED_KAGGLE_VALIDATION` である。後継 candidate `neural-student-v1-entryfix-cwddecoupled` の当時のローカルG1〜G6はPASSしたが、Python 3.11の強制、正確なKaggle directory layout、module originのfail-closed監査が不足していた。このため当時の`LOCAL_SUBMISSION_READY`判定を無効化し、独立レビューEvidenceへ置き換える。Kaggleへの追加提出は実行していない。

## 修正

- `__file__` 不在時は `sys._getframe().f_code.co_filename` から source root を解決する。
- `/kaggle_simulations/agent` と cwd は、必須 archive member が存在する場合だけ fallback 候補にする。
- G3/G4/G5/G6 は extracted agent root と別の working directory で実行する。
- submission 専用 helper を `mage_submission_agents` へ名前空間化し、Kaggle environment の `agents` module cache と衝突させない。
- verifier と wrapper は `cwd_decoupled_verification: true` を要求する。

## Candidate と結果

- archive: `runs/offline-training-v1/offline-long-run-actual-20260718-r1/submission_candidate/neural-student-v1-entryfix-cwddecoupled/submission.tar.gz`
- archive SHA-256: `e90546ab4e0aac32d0cf3996ddf80c2802779e2525c17b6f0c6e2dd55dc680e5`
- model SHA-256: `2318b7ff7f1d981ec4181ae01cc681b15c057f5b4daba3d5f900e71e2144eb8f`（不変）
- semantic model hash: `94564328a10f1e914beb63073235722694093e281905b5cbd546b2a35742dea4`
- G5: 52 steps、`DONE/DONE`。
- G6: seed `35000`、20 games、9–11–0、crash / invalid / timeout 0、`external_files_read: []`、fallback telemetry `AVAILABLE`、selected 919、fallback reasons 空。
- focused tests: Safety Gate 40 passed、offline training 32 passed。

full regression は既知 flaky `tests/test_collect_offline_training_v1_evidence.py::test_run_command_safe_timeout_and_child_cleanup` のため PASS と扱わない。Champion/default は Rule Agent v0、Promotion は `NO_DECISION`、追加学習なし、model bytes 不変である。

本Evidenceは履歴として保持するが、現在の提出可否判断には使用しない。後継は[独立レビュー](kaggle-submission-validation-v2-independent-review-20260718.md)とその[JSON](kaggle-submission-validation-v2-independent-review-20260718.json)を正とする。
