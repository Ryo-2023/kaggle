# 証跡: neural-student-v1-kaggle-entryfix-20260718

## 1. 概要

Kaggleへの提出リファレンス `54796979` が、KaggleのValidation Episodeにおいてエラーとなり失格となりました。原因は提出した `main.py` のモジュールスコープ内におけるグローバル変数 `__file__` の使用にありました。
Kaggle Environments 1.32.0 では、エージェントソースを `exec(code_object, env)` で実行するため、`env` グローバル空間に `__file__` が定義されていません。このためエントリポイントロード時に `NameError: name '__file__' is not defined` を起こしていました。

本対応では、パッケージ生成元の `_MAIN_TEMPLATE` を修正し、`__file__` が存在しない場合には `Path.cwd()` に動的フォールバックし、かつ想定ファイル群の存在検証（fail-fast）を行う堅牢なパス解決ロジックへと修正しました。

## 2. 障害解析と根本原因

* **対象提出リファレンス**: `54796979`
* **エラーステージ**: `Validation Episode` (Step 0, 約 0.004 秒で失格)
* **トレースバック**:
  ```text
  File "/kaggle_simulations/agent/main.py", line 13, in <module>
      _ROOT = Path(__file__).resolve().parent
  NameError: name '__file__' is not defined
  ```
* **旧検証（clean-room）で検出できなかった理由**:
  従来の検証スクリプトおよびテストでは、パッケージされた `main.py` を通常の python import を使ってロード・検証しており、この経路では Python ランタイムが `__file__` を自動定義するため、不具合が顕在化しませんでした。

## 3. 修正差分

[src/mage_ptcg/offline_training/package.py](file:///home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle/src/mage_ptcg/offline_training/package.py) 内の `_MAIN_TEMPLATE` を以下のように修正しました。

```diff
-_ROOT = Path(__file__).resolve().parent
+_ROOT = (
+    Path(__file__).resolve().parent
+    if "__file__" in globals()
+    else Path.cwd().resolve()
+)
+_REQUIRED = (
+    "runtime_main.py",
+    "deck.csv",
+    "models/neural-student-v1.json",
+)
+if not all((_ROOT / item).exists() for item in _REQUIRED):
+    raise RuntimeError(
+        f"submission package root could not be resolved: {_ROOT}"
+    )
```

## 4. 追加した回帰テスト

[tests/test_offline_training_v1.py](file:///home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle/tests/test_offline_training_v1.py) の末尾に以下の2つのテストを追加しました。

1. `test_raw_exec_without_file`:
   `__file__` グローバルを除外した環境で、`compile` および `exec(code, env)` による raw 実行がエラーなく成功し、`agent` 呼び出し可能オブジェクトが正しく globals から取得できることを検証します。
2. `test_kaggle_get_last_callable`:
   Kaggle の実パーサーである `kaggle_environments.agent.get_last_callable` を使用して `main.py` がロード可能であり、初期ステップ 0 の observation に対し正しい deck (60枚) を登録することを検証します。

これらは修正前において狙い通り `NameError` で RED（失敗）となり、修正後は GREEN（成功）となることを確認しました。

## 5. 新旧アーティファクト比較

* **新 candidate 出力先**: `runs/offline-training-v1/offline-long-run-actual-20260718-r1/submission_candidate/neural-student-v1-entryfix/`
* **新 candidate SHA-256**: `dd33517fb7758fc671b27cfe672a0367835761061f2121747430e084895178d8`
* **旧 candidate SHA-256**: `d4e2cdcb4557b4bbb9968266a0990525a7e172b9a9e664b477a21f957892e67d`
* **差分内訳**: `main.py` の修正されたコードのみ（モデルバイナリを含む他全ファイルのハッシュ値は完全一致）。
* **モデル SHA-256（不変確認済）**: `2318b7ff7f1d981ec4181ae01cc681b15c057f5b4daba3d5f900e71e2144eb8f` (旧候補モデルと完全一致)
* **意味的モデルハッシュ**: `94564328a10f1e914beb63073235722694093e281905b5cbd546b2a35742dea4`

## 6. 検証結果

1. **Kaggle Validation 模擬対戦**:
   `kaggle_environments.make` を使用したローカル Validation Episode エミュレーションを実施。Agent 0、Agent 1 双方に新パッケージの `main.py` を割り当て、全 215 ステップをエラーなく正常に完走しました (`Agent 0 status: DONE`, `Agent 1 status: DONE`)。
2. **20-Game Smoke Test**:
   未使用シード `32000` で 20 試合の viability smoke 評価を実行。crashes/timeouts は 0 件、gate_status は `"CLEAN_PASS"` となり、正常に推論モデルが機能することを確認しました。
3. **Full Regression pytest スイート**:
   1025件合格、1件不合格。不合格テストは既知の flaky テスト `tests/test_collect_offline_training_v1_evidence.py::test_run_command_safe_timeout_and_child_cleanup` であり、本修正による回帰バグではありません。

## 7. 不変条件の遵守

* **Champion / default**: `Rule Agent v0` （変更なし）
* **Promotion**: `NO_DECISION`
* **Kaggle再提出**: 未実施

---

## 2026-07-18 訂正 (Correction)

本ドキュメントの凍結後、再提出は行わない方針のもと、Safety Gate 自動検証機能（`verify_kaggle_submission_candidate.py`）を導入し、`neural-student-v1-entryfix` アーカイブ（SHA-256: `dd33517f...`）に対して実行しました。
検証の結果、G1からG6まですべて PASS し、検証済みの証跡である `submission_verification.json` を出力しました。
