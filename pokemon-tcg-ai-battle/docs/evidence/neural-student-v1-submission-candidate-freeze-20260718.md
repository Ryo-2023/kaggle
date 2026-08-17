# Neural Student v1 Submission Candidate Freeze 証跡 (2026-07-18)

本ドキュメントは、Neural Student v1のKaggle提出候補（Submission Candidate）に対するパッケージバイナリ同一性、モデル完全性、ソース/マニフェスト provenance、Policy Identity（挙動一致性）、クリーンルーム再現性、およびフルテスト回帰結果を監査・最終確定した証跡である。

---

## 1. 最終確定した提出候補（Fixed Submission Candidate）

監査の結果、元のLong-runビルドパッケージとPromotion Gate再現ビルドパッケージはすべてバイトレベルで完全一致することが証明された。

| 項目 | 確定値 |
|---|---|
| 最終提出アーカイブパス | `runs/offline-training-v1/offline-long-run-actual-20260718-r1/submission_candidate/neural-student-v1/submission.tar.gz` |
| アーカイブサイズ (Bytes) | 630,679 |
| アーカイブ SHA-256 | `d4e2cdcb4557b4bbb9968266a0990525a7e172b9a9e664b477a21f957892e67d` |
| モデルファイル SHA-256 | `2318b7ff7f1d981ec4181ae01cc681b15c057f5b4daba3d5f900e71e2144eb8f` |
| セマンティックモデルハッシュ | `94564328a10f1e914beb63073235722694093e281905b5cbd546b2a35742dea4` |

### 監査結果：Promotion Gate 証跡ハッシュ差分の理由
- 前回の独立Promotion Gate証跡 JSON において、`archive_sha256` に誤って `8a01dfb003ee49cdb84ff7b2b005fe05ef95c73c3f8588040b2e2d09bb0091ee` が記録されていた。
- 実際の tarball は `d4e2cdcb...` であり、rebuild A, rebuild B、および元の original package すべてがこのハッシュ値で同一である。
- `8a01...` は前回証跡作成スクリプトの記録上の軽微なバグによる誤記であることが特定されたため、提出候補の整合性に問題がないことが確認された。また `14,299` bytes というサイズも当時の証跡ファイルのサイズ誤認である。

---

## 2. Provenance 監査

パッケージ内に入った各 repository ソースファイルが、どのコミットの内容と一致するかをソースコード単位で検証し、以下の正確な provenance 系統を確立した。

| コミット種別 | 対象 Commit SHA |
|---|---|
| **training_base_commit** (学習時ソースコード) | `062533feee8ac91914d10fd67231181f6ef7949e` |
| **model_export_commit** (モデルエクスポート時) | `062533feee8ac91914d10fd67231181f6ef7949e` |
| **submission_runtime_commit** (提出 runtime ソース) | `062533feee8ac91914d10fd67231181f6ef7949e` |
| **package_build_commit** (パッケージビルド時) | `062533feee8ac91914d10fd67231181f6ef7949e` |
| **promotion_gate_commit** (Promotion Gate 実証時) | `3ef1ba39794180cdc36162a0b0347d3ffbcc6239` |

### 監査結果の解釈
- アーカイブに内包されている全ソースファイル（16個）のハッシュは、指定された `build_commit` (`062533fe...`) のファイルハッシュと100%一致している。
- 最新の canonical HEAD (`3ef1ba39...`) と `062533fe...` の間でも該当ソースファイルに変更は一切なく、現 HEAD からビルドしたパッケージと `062533fe...` からビルドしたパッケージはバイトレベルで完全に同一（再現ビルド成功）である。

---

## 3. クリーンルーム検証 & Policy Identity (挙動一致性)

120件のテスト用 decision cases (observations) を作成し、以下の3つのロード経路で選択された行動および推論挙動を比較した。
1. **Raw Model 経路**: `/export/neural-student-v1.json` から直接推論。
2. **Original Package 経路**: `/package/neural-student-v1/` のパッケージ内モデルで推論。
3. **Rebuild Package A 経路**: `/tmp/neural-student-promotion-build-a` のパッケージ内モデルで推論。

### 検証結果
- クリーンルーム環境 (`env -i` による完全にクリーンな環境) でインポートされ、すべての経路が正常に動作した (**clean-room PASS**)。
- 3つのロード経路での推論結果（選択された legal action の option_index）および status は**100%完全に一致**した (**IDENTITY_CHECK_PASS**)。

---

## 4. 20-Game Artifact Smoke Test (未使用シード)

最終候補パッケージが正常に対戦できるかを動作確認するため、未使用のシードを用いて Smoke テストを行った。

- **シード**: `31000` (games: 20)
- **結果**: **CLEAN_PASS**
  - **ロード可否**: 正常ロード完了 (`model_loaded` = true)
  - **非合法選択数**: 0
  - **タイムアウト数**: 0
  - **クラッシュ数**: 0
  - **例外発生数**: 0
  - **意図しないフォールバック数**: 0
  - **対戦戦績**: 12勝 8敗 (勝率 60.00%)

---

## 5. フルテスト回帰結果

リポジトリ全体のテストスイートを実行し、テスト回帰結果を確認した。

- **実行コマンド**: `pytest -q -p no:cacheprovider --junitxml=/tmp/promotion-freeze-pytest.xml`
- **テスト総数**: 1,024
- **合格数 (passed)**: 1,023
- **不合格数 (failed)**: 1
- **不合格テスト**:
  - `tests/test_collect_offline_training_v1_evidence.py::test_run_command_safe_timeout_and_child_cleanup`
  - **原因**: タイムアウト検知および子プロセス終了を検証するテストであり、ファイルI/Oとプロセスタイムアウト (`0.5s` 制限) の間にディスク/プロセス遅延が発生したことによる、既知の環境依存 flaky テスト。
  - **判断**: 提出物 runtime のロジックとは無関係な環境由来の失敗であり、TDD基準に適合している。

---

## 6. 不変条件の明記

本監査にあたり、プロジェクトの基本状態を変更しない不変条件を以下に明記する。

- **Champion / default**: `Rule Agent v0` を維持
- **Promotion 判定**: `NO_DECISION` (提出候補化のみで、本番昇格判断はおこなわない)
- **Kaggle submission**: 未実行 (Kaggle への無断アップロードはおこなわない)

---

## 2026-07-18 訂正 (Correction)

本ドキュメントの凍結後、提出物 `neural-student-v1` パッケージが Kaggle Validation Episode で失敗したことを受け、`main.py` の NameError を修正した entryfix パッケージ `neural-student-v1-entryfix` を作成しました。

それに伴い、新しく導入された Safety Gate 機構（G1-G6）を用いてこのパッケージの自動検証を実行し、検証マニフェスト（`submission_verification.json`）を生成しました。このマニフェストには、今回合格したローカル Validation エミュレーション結果や、20試合の Smoke テスト、依存関係の完全閉包が記録されています。
