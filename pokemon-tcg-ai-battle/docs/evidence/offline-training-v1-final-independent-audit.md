# Offline Training v1 最終統合 独立監査報告書 (Final Independent Audit Report)

* **監査実施日**: 2026-07-17
* **監査者**: 独立レビュー（Gemini 3.5 Flash）
* **最終判定 (Verdict)**: **ACCEPT**

---

## 回収に関する注記 (2026-07-17 限定回収)

本文書は独立review branch `origin/review/offline-training-v1-integration-audit`（確定HEAD
`b8ec686d3a134616708800c2a5a4e8f24f552416`）から、正典へ文書単位で限定回収したものである。
review branch全体はmergeしていない。内容は回収時点のまま保持し、事実の書き換えは行っていない。
現行正典と併読する際は次に注意すること。

* 本監査の対象 (target) は Offline Training v1 完成版 `d73f89ae8342150a7c91d36385fbed34a1df41dc` であり、
  同commitは現行正典（`feature/belief-guided-search`）へ統合済みである。
* 本文書の「テスト実行記録」にある **77 passed** は、リポジトリ全体の full regression ではなく、
  review実行時点の **production regression subset**（`tests/test_offline_training_v1.py` など）の件数である。
  現行正典の最新 full regression baseline（**1003 passed**）と混同しないこと。
* `tests/review/` 配下のテストは、target branch（`d73f89a` 系列）から review branch へ同期されたものを含み、
  review branchのみで独立作成された網羅的な新規テスト一式ではない。同テストは現行正典（HEAD）に
  byte単位で同一の内容が既に存在する（本回収でのtest再取り込みは行っていない）。
* 同じreview branch上には、より前段のターゲット（`4cfa1c4` / `cb502ef`）に対する中間監査記録
  （`offline-training-v1-independent-integration-verdict.md` 等、判定 `APPROVE_WITH_REQUIRED_FIXES`）が
  存在するが、それらの指摘事項は本文書が示す最終ターゲット `d73f89a` で解消済みと本文書自身が記録している
  （本文セクション4参照）。当該中間文書は stale として今回は回収していない。
* Champion/default: **Rule Agent v0**（不変）。Promotion: **NO_DECISION**（不変）。

---

## 1. ターゲット識別情報 (Target Identity)

* **対象ブランチ (Target Branch)**: `origin/integration/offline-training-v1`
* **対象コミット SHA (Target Commit)**: `d73f89ae8342150a7c91d36385fbed34a1df41dc`
* **ツリーハッシュ (Tree Hash)**: `41784a0800dec9c4e3fd92c149bb139cf6681234`
* **作業ツリー dirty 状態 (Target Dirty State)**: clean (未変更・未追跡ファイルなし)
* **監査ブランチ HEAD (Audit HEAD)**: `41a344305c731df4c8509d917fa9a3649397f49f`
* **Python バージョン**: `Python 3.12.3`
* **依存関係構成ハッシュ (requirements.txt MD5)**: `963e848032fe3d52cd035a4eafd60671`

---

## 2. 監査項目検証結果

### 2.1 勾配重み付けの数学的一致 (REV-E1)
* **検証結果**: **PASS** (許容誤差: 完全に bitwise 一致 / fp32 キャスト誤差 1e-7 以下)
* **詳細**:
  `src/mage_ptcg/offline_training/neural.py` の `_train_batch_once` において、不均一な microbatch や教師なし決定を含む microbatch が混在する場合でも、各 microbatch の average loss に supervised 決定数に基づく重み付けを行い、バッチ全体の supervised 決定数で正規化する処理（`scaled = loss * (micro_supervised / total_supervised)`）が導入されました。
  これにより、microbatch 分割サイズや OOM による動的な microbatch 縮小にかかわらず、累積勾配（gradient accumulation）が数学的にフルバッチ平均と完全に一致することが、独立 reference 検証テスト（`test_unequal_microbatch_gradient_matches_full_batch` / `test_microbatch_gradient_matches_reference_mean`）で実証されました。

### 2.2 チェックポイント保存とレジューム決定性 (REV-F1, F2, F3)
* **検証結果**: **PASS**
* **詳細**:
  * **REV-F1 (早期終了時のレジューム決定性)**: `neural.py` 内で、検証損失（NLL）に基づく patience 判定と decrement を行った後にチェックポイントの保存を行うように評価順序が修正されました。これにより、チェックポイントが pre-decrement ではなく post-decrement の正しい patience 状態を保存するようになり、連続実行とレジューム実行での early-stop のタイミングおよび最終パラメータが完全に一致することを確認しました。
  * **REV-F2 (RNG状態保存)**: RNG状態が JSON 内に正しく記録されていることを確認。現行モデルでは epoch ごとに固定シードでシャッフルされるため、モデルパラメータ発散のリスクがないことを確認しました。
  * **REV-F3 (チェックポイント破損と型付きエラー)**: テンソルファイルが欠落している場合、または読み込み時に破損している場合、型付きエラー `CheckpointValidationError`（`NeuralError` のサブクラス）が適切な `reason` コード（`tensor_file_missing` / `tensor_deserialization_failed`）と共に送出されることが実証されました。エラーメッセージ内からホームディレクトリや絶対パスなどのプライベート情報が秘匿されていることも確認しました。

### 2.3 パッケージ実測検証 (REV-I1)
* **検証結果**: **PASS**
* **詳細**:
  `src/mage_ptcg/offline_training/package.py` のクリーンルーム検証において、定数による合法手率のハードコード（`1.0`）が廃止され、実際に agent と fallback agent を用いて 8 ケースの意思決定を実行し、合法手率・例外・フォールバック発生件数を測定して返す `measure_legality` 関数が実装されました。
  クリーンルームのテストで、実測値が `CLEAN_ROOM_RESULT` から正しくパースされ、改ざんや定数偽装なしで検証されることが証明されました。

### 2.4 Privacy / Quarantine
* **検証結果**: **PASS**
* **詳細**:
  * データセット構築時の `build_dataset` が重複決定を検出し、同一 decision hash の split を跨いだ流入を `split-leakage quarantine` で確実に隔離することを確認しました。
  * symlink によるリポジトリ外参照の拒否、MANIFESTとPAYLOADの同時検査、エラー発生時のプライベート絶対パスの秘匿が正常に機能していることを確認しました。

### 2.5 フォールバックと Champion Invariant の維持
* **検証結果**: **PASS**
* **詳細**:
  モデル破損時や推論時の例外発生において、システムは確実に `Rule Agent v0` へフォールバックし、非合法手を出さないことを確認しました。
  `main.py` および `student/runtime.py` の静的監査により、Champion / default agent の設定が `Rule Agent v0` のまま維持され、Promotion が `NO_DECISION` になっていることを確認しました。

---

## 3. テスト実行記録 (Evidence)

リポジトリ外に作成した完全に隔離された temporary directory 環境（`PYTHONPATH` による隔離）において、以下のテストを実行しすべて PASS しました。

| テストカテゴリ | 実行コマンド | テスト件数 | 結果 |
|---|---|---|---|
| **プロダクション回帰テスト** | `pytest tests/test_offline_training_v1.py tests/test_c4_...` | 77 | **77 PASSED** (GREEN) |
| **独立監査テスト (tests/review)** | `pytest tests/review/ -v` | 67 | **67 PASSED** (GREEN) |
| **パイプライン一括検証** | `scripts/run_offline_training_v1.py pipeline` (smoke) | 8 phase | **COMPLETE / verified: true** |
| **インポートクロージャ検証** | `scripts/check_offline_training_import_closure.py` | — | **SUCCESS** (Internal closure COMPLETE) |

---

## 4. 差分監査 (cb502ef → d73f89a)

旧ターゲット `cb502ef631aa5541476e86a9d3a9ead497b24c37` から最新ターゲット `d73f89ae8342150a7c91d36385fbed34a1df41dc` の間で行われた主な変更は、前回の指摘事項（required fixes）の解消に完全に一致しています。

| ココミット / ファイル | 解消された Finding | テストによる確認 | 新たな回帰リスク |
|---|---|---|---|
| `neural.py` (`_train_batch_once`) | **REV-E1** (microbatch 勾配集計乖離) | `test_unequal_microbatch_gradient_matches_full_batch` | なし (数学的に正規化) |
| `neural.py` (保存タイミング修正) | **REV-F1** (早期終了レジューム決定性不一致) | `test_early_stop_resume_parity_with_continuous` | なし |
| `neural.py` (`CheckpointValidationError`) | **REV-F3** (破損時の型なし例外漏洩) | `test_truncated_checkpoint_tensor_file_raises_typed_error` | なし |
| `package.py` (`measure_legality`) | **REV-I1** (クリーンルーム検証の定数偽装) | `test_clean_room_report_is_measured_not_hardcoded` | なし |
| `tests/review/` 配下の追加 | **REV-M1 / REV-M2** (監査テストの取り込み) | review tests 全件 PASS | なし (保護の強化) |

未解決の指摘および新規に発生した問題はありません。

---

## 5. 最終判定 (Verdict)

**Verdict**: **ACCEPT**

* **理由**:
  前回の監査で指摘されたブロッキングなバグ（REV-E1, F1, F3, I1, M1, M2）はすべて完璧に修正され、統合ブランチに取り込まれました。
  隔離された temporary 環境下で、プロダクションテスト（77件）および監査テスト（67件）の計 144 件がすべて正常に動作し、GREEN であることを確認しました。
  絶対パスの漏洩やプライバシー違反はなく、clean-room 要件も完全にクリアしており、本リポジトリへの最終統合を受け入れ可能と判定します。
