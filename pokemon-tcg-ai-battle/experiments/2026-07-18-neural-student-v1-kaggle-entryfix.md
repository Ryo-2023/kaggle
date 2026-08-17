# Neural Student v1 Kaggle Entrypoint Fix

## メタデータ

| 項目 | 値 |
|---|---|
| 日時 | 2026-07-18 11:55 JST |
| 担当 | Antigravity (agent) |
| 種別 | local experiment |
| commit | `c21ebed5445ee77b521ed2cd1f309b3726712766` |
| branch | `feature/belief-guided-search` |
| model provenance | neural student model export |
| simulator / data | Kaggle Environments 1.32.0 / `runs/offline-training-v1/offline-long-run-actual-20260718-r1/export/neural-student-v1.json` |

## 目的と反証条件

- **問い**: Kaggleの `exec` 実行環境で発生した `NameError: name '__file__' is not defined` を回避し、正常に初期動作（deck登録）および対戦シミュレーションを完走させることができるか。
- **仮説**: `__file__ in globals()` のチェックを行い、存在しない場合に `Path.cwd()` に動的フォールバックし、かつ主要ファイルの存在確認を行うことで、Kaggle 環境とローカル環境の両方で正常に起動できる。
- **反証条件**: 回帰テストが失敗するか、ローカルの Validation Episode 模擬環境（`kaggle_environments.make`）での実行時にエラーやクラッシュが確認された場合。
- **変更点**: エントリポイントテンプレート `main.py` 内の `_ROOT` 変数の解決ロジックを、グローバルスコープ内の `__file__` の有無に応じて `Path.cwd().resolve()` にフォールバックするよう変更。主要な3ファイル（`runtime_main.py`, `deck.csv`, `models/neural-student-v1.json`）の存在検証コードを追加。
- **固定条件**: 使用モデルは `runs/offline-training-v1/offline-long-run-actual-20260718-r1/export/neural-student-v1.json`（不変）。対戦相手はデフォルトエージェント（Rule Agent v0）。

## 再現

```bash
# 1. パッケージの再ビルド
PYTHONPATH=src /usr/bin/python3 -c "
from mage_ptcg.offline_training import package
package.build_package(
    export_path='runs/offline-training-v1/offline-long-run-actual-20260718-r1/export/neural-student-v1.json',
    output_dir='runs/offline-training-v1/offline-long-run-actual-20260718-r1/submission_candidate/neural-student-v1-entryfix/',
    repository_root='.',
    build_commit='$(git rev-parse HEAD)'
)
"

# 2. 回帰テストの実行
PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 /usr/bin/python3 -m pytest tests/test_offline_training_v1.py -k "test_raw_exec_without_file or test_kaggle_get_last_callable"

# 3. Kaggle Validation Episode のローカルシミュレーション
/usr/bin/python3 /home/bfe-lab-ono/.gemini/antigravity-ide/brain/47fa22fb-9d36-4320-9906-d0f106dd7d09/scratch/verify_episode.py

# 4. 20試合の viability smoke 評価
PYTHONPATH=src /usr/bin/python3 scripts/run_actual_agent_viability.py \
  --challenger neural_student_package \
  --games 20 \
  --base-seed 32000 \
  --canonical-base c21ebed5445ee77b521ed2cd1f309b3726712766 \
  --package-path runs/offline-training-v1/offline-long-run-actual-20260718-r1/submission_candidate/neural-student-v1-entryfix/ \
  --output runs/offline-training-v1/offline-long-run-actual-20260718-r1/submission_candidate/neural-student-v1-entryfix/smoke_run_result.json
```

生成された新しいパッケージは `runs/offline-training-v1/offline-long-run-actual-20260718-r1/submission_candidate/neural-student-v1-entryfix/` に配置されている。

## 結果

| condition | seeds | games | win rate | timeout | illegal action | runtime | 備考 |
|---|---:|---:|---:|---:|---:|---:|---|
| baseline (Rule v0) | 32000 | 20 | 0.550 | 0 | 0 | 正常 | 11 wins / 9 losses (vs Challenger) |
| proposed (Entryfix package) | 32000 | 20 | 0.450 | 0 | 0 | 正常 | 9 wins / 11 losses (vs Champion) |

- **sanity check**: テスト 2 件合格、Validation Episode は 215 ステップを正常終了 (`Agent 0/1 status: DONE`)。
- **負の所見**: なし。
- **不確実性**: なし。

## 解釈と判断

- **観測事実**:
  - `__file__` なしの raw exec 回帰テストが PASS した。
  - `kaggle_environments.make` による Step 0 から Step 215 までのエミュレーションがクラッシュなしで完走した。
  - 20試合の smoke テストで timeouts=0, crashes=0, gate_status="CLEAN_PASS" となり、正常に機能することを確認。
- **解釈**: `__file__` がグローバル空間に存在しない場合のカレントディレクトリ解決フォールバックおよびファイル存在検証による fail-fast コードが、Kaggle 環境における NameError を回避することに成功した。
- **判断**: 採用（新 candidate パッケージ `neural-student-v1-entryfix` を再提出候補として承認）。
- **次 action**: ユーザーによる確認後、本パッケージを用いた Kaggle への再提出を実行する。

## Kaggle 提出（該当時）

| 項目 | 値 |
|---|---|
| submission name | 未提出 |
| submitted at | 未提出 |
| source commit | |
| local verification | SUCCESS |
| Public LB | 未提出 |
| Private LB | 未提出 |
| Kaggle URL / ID | |
| 備考 | 今回は検証完了までとし、再提出自体はユーザー承認後に別プロセスで行う。 |

---

## 2026-07-18 追記 (Correction)

本ドキュメントの凍結後、再提出は行わない方針のもと、Safety Gate 自動検証機能（`verify_kaggle_submission_candidate.py`）を導入し、`neural-student-v1-entryfix` アーカイブ（SHA-256: `dd33517f...`）に対して実行しました。
検証の結果、G1からG6まですべて PASS し、検証済みの証跡である `submission_verification.json` を出力しました。
