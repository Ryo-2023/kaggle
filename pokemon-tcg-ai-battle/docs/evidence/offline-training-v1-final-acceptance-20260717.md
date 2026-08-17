# Offline Training v1 — Final Acceptance Summary (2026-07-17)

本文書は `docs/evidence/offline-training-v1-final-acceptance-20260717.json`（19項目 real acceptance の machine-readable 証跡）の要約である。今回の実行のみを対象とし、過去の実績（例: 旧`docs/evidence/offline-training-v1-final-acceptance.md`）とは区別する。

## メタデータ

| 項目 | 値 |
|---|---|
| 実行日 | 2026-07-17 |
| canonical branch | `feature/belief-guided-search` |
| checkpoint commit | `1bad9701f956ef755530ea4e81b2c70703facd51`（tag `checkpoint/canonical-before-final-acceptance-20260717`） |
| review限定回収 commit | `e6dca872b94d9f2d04da04686aead2cf8b2fd141` |
| tooling fix commit 1 | `9a69c2d61eb95771a7857ad5d4c5d1b4bd2b3334`（isolate/redact） |
| tooling fix commit 2 | `d6921c0d7da708a59ffa57af57bf3c67e71fc5a3`（package builder wiring） |
| acceptance実行 commit（git.commit_sha） | `d6921c0d7da708a59ffa57af57bf3c67e71fc5a3` |
| Champion/default | Rule Agent v0（不変） |
| Promotion | `NO_DECISION`（不変） |

## R1（初回 real acceptance）: FAIL

- run ID: `canonical-final-acceptance-20260717`
- overall verdict: `FAIL`（14 PASS / 5 FAIL / 0 SKIP）
- evidence SHA-256: `f61aa847f6643f82c62df31d99b5440ac4354cbd316311c043d6cecfc519f91c`（repository へは未commit、`/tmp` に保持）
- FAIL: `export_parity`, `package_build`, `clean_room`, `privacy_scan`, `absolute_path_scan`
- 原因: いずれも Offline Training v1 本体ではなく、Evidence Collector（`scripts/collect_offline_training_v1_evidence.py`）自身の4種類のツール欠陥
  1. `export_parity`: `export_c4_actual_training_bundle.py` が期待する C4 data-ops 形式の run-root ではなく、training_smoke の offline_training_v1 パイプライン run-root を誤って渡していた。
  2. `package_build`: `training_smoke` のパイプライン package phase と共有 `dist/kaggle/neural-student-v1/` を奪い合い、後着側が "must be new or empty" で失敗していた。
  3. `clean_room`: `run_command_safe()` が常に付与する `--- STDOUT ---` / `--- STDERR ---` の装飾行を `git status --short` の出力そのものと誤読し、常に FAIL していた。
  4. `privacy_scan` / `absolute_path_scan`: `inspect_tarball()` の `path` フィールドが redaction を経由せず、実絶対パスとusernameがそのままevidence JSONへ漏洩していた（self-scanが正しく検出）。

## R2（1回目の修正後 real acceptance）: FAIL（新たな欠陥を検出）

- run ID: `canonical-final-acceptance-20260717-r2`
- overall verdict: `FAIL`（17 PASS / 2 FAIL / 0 SKIP）
- evidence SHA-256: `0d39c66fa44d0ce7ae3fe9ff301e9b70a9b338513e0927be1067e82975246cc1`（repository へは未commit、`/tmp` に保持）
- FAIL: `package_build`, `package_verify`
- 原因: 4種類の修正（tooling fix commit 1）により directory 衝突が解消された結果、それまで隠れていた5件目の欠陥が露出した。`package_build` が呼んでいた `scripts/build_student_submission.py` は、旧世代の別artifact系統（"C4 Student v0" 線形モデル、`StudentV0Model` 経由）専用であり、training_smoke が実際に生成する `neural-student-v1` モデルとはスキーマ非互換（`unsupported model schema or feature version`）だった。R1では directory 衝突が先に発生していたため、この不整合はこれまで一度も露見していなかった。
- 対応: tooling fix commit 2 で `package_build` を `mage_ptcg.offline_training.package.build_package()`（`phase_package` が使う実際の production packager）を直接呼ぶ実装へ変更。

## R2（修正後、最終）: PASS

- run ID: `canonical-final-acceptance-20260717-r2b`
- overall verdict: **`PASS`**
- exit code: `0`
- required checks: 19 / 19 PASS、FAIL 0、SKIP 0、TIMEOUT 0、duplicate なし
- evidence JSON: `docs/evidence/offline-training-v1-final-acceptance-20260717.json`
- evidence JSON SHA-256: `68d5c4f93e129aab32f6373e008a829f05d80e0a5a2ecfae264f972c7a2e8293`
- evidence JSON size: 43,365 bytes（2 MiB 未満）

### 19 check 一覧

| # | check ID | status | 所要時間(s) |
|---|---|---|---|
| 1 | git_diff_check | PASS | 0.011 |
| 2 | import_closure | PASS | 0.230 |
| 3 | focused_core_tests | PASS | 12.816 |
| 4 | review_adversarial_tests | PASS | 3.446 |
| 5 | gemini_support_tests | PASS | 0.224 |
| 6 | full_regression | PASS | 152.912 |
| 7 | collection_smoke | PASS | 4.306 |
| 8 | training_smoke | PASS | 6.205 |
| 9 | resume_determinism | PASS | 4.250 |
| 10 | export_parity | PASS | 0.046 |
| 11 | package_build | PASS | 0.178 |
| 12 | package_verify | PASS | 0.025 |
| 13 | clean_room | PASS | 0.012 |
| 14 | privacy_scan | PASS | 0.001 |
| 15 | secret_scan | PASS | 0.001 |
| 16 | absolute_path_scan | PASS | 0.000 |
| 17 | artifact_hash | PASS | 0.000 |
| 18 | fallback_invariant | PASS | 1.816 |
| 19 | champion_invariant | PASS | 0.000 |

### test totals

- full_regression（repository全体 pytest）: PASS — `1018 passed, 5 warnings in 151.77s`（bounded_excerptに記録。開始時点の既知実績 1003 passed から、本seriesで追加した Evidence Collector 回帰テスト15件の増分を含む）
- focused_core_tests（`tests/test_offline_training_v1.py`）: PASS
- review_adversarial_tests（`tests/test_c4_data_ops.py`）: PASS
- gemini_support_tests（`tests/test_c4_actual_training_bundle.py`）: PASS
- fallback_invariant（`tests/test_offline_training_v1.py -k fallback`）: 1 passed, 29 deselected
- tooling fix 検証（このセッション内、acceptance run外で個別実行）:
  - `tests/test_collect_offline_training_v1_evidence.py`: 53 passed
  - `tests/test_offline_training_v1.py` + `tests/review/` + `tests/offline_training_v1_support/` + `tests/test_kaggle_package.py` + `tests/test_verify_kaggle_submission.py` + `tests/test_c4_data_ops.py` + `tests/test_c4_actual_training_bundle.py`: 376 passed

### artifact SHA

- submission tarball: `runs/offline-training-v1/_acceptance_scratch/canonical-final-acceptance-20260717-r2b/package_build/submission.tar.gz`
- sha256: `cfee30fca4d97fb32eded53dca80d24b52a2d4d4f1671c80060e1f08a1dd31b8`
- size: 215,711 bytes
- package_candidate_eligibility: `true`

### Champion / Promotion / HOLD

- Champion/default: `Rule Agent v0`（`champion_invariant` PASS で確認、main.py 静的監査）
- Promotion: `NO_DECISION`（不変）
- Gemini P0 / P1 / HOLD: `19 / 34 / 4`
- HOLD quarantine: `hold_modules_quarantined = true`、違反 0 件

### fallback invariant

PASS（`tests/test_offline_training_v1.py -k fallback`: 1 passed）。モデル破損・推論例外時に `Rule Agent v0` へ確実にフォールバックする契約を確認。

### privacy / secret / absolute path

- `privacy_scan`: PASS（self-scan violation_count=0）
- `secret_scan`: PASS（self-scan violation_count=0）
- `absolute_path_scan`: PASS（self-scan violation_count=0）
- 追加のportable監査（本セッションでPythonにより再帰的にJSON全stringを走査）: `/home/`、`/tmp/`、実username（`bfe-lab-ono`）、`file:///home/`、secret/token/credential らしき文字列、いずれも 0 件
- `repository_root_resolved`: `[REDACTED_REPO_ROOT]`
- `submission_package` artifact の `portable_relative_path` はrepo-relative文字列のみ（絶対パスなし）

## review branch 処理結果

- 対象: `origin/review/offline-training-v1-integration-audit`（確定HEAD `b8ec686d3a134616708800c2a5a4e8f24f552416`。当初指示書に記載の `b8ec68677c7f07cb67d5be2a7ea47c617192fa94` は転記ミスであり、ユーザーが訂正・承認済み）
- 判定: branch全体はmergeせず、独自の最終監査文書1件のみ限定回収
- 回収ファイル: `docs/evidence/offline-training-v1-final-independent-audit.md`（target `d73f89a`、verdict `ACCEPT`。review branchにのみ存在し、現行正典と矛盾しない）
- 回収しなかったもの: 同branch上の中間監査文書11件（前段ターゲット `4cfa1c4`/`cb502ef` に対する `APPROVE_WITH_REQUIRED_FIXES` 判定。指摘事項は最終ターゲット `d73f89a` で解消済みとreview自身が記録しているため、staleとして回収対象外）。`tests/review/*.py` はcanonicalへ既にbyte単位で同一のものが存在するため再回収不要（Category A）
- commit: `e6dca872b94d9f2d04da04686aead2cf8b2fd141`

## 未実施事項の確認

- Kaggle submission: 未実施
- long-running本学習（実データでの offline training）: 未実施
- Champion promotion: 未実施（Promotion は `NO_DECISION` のまま）
- Student のdefault化: 未実施

## Offline training 開始可否

Offline Training v1 の acceptance tooling / final acceptance は本run（`canonical-final-acceptance-20260717-r2b`）で 19/19 PASS に到達し、正典上で最終acceptance済みとなった。次段階として、実データを用いた long-running offline training を開始する技術的前提（成果物の合法性・再現性・隔離性・監査ログの整合）は整った。ただし実際の長時間学習開始・実データ投入・Champion昇格は、別途ユーザーの承認と計算リソース確保を要する（本作業では未実施）。
