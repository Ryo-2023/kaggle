# Offline Training v1 — 検証 evidence

作成日: 2026-07-17 / branch: `integration/offline-training-v1` / base HEAD: `a4f8d54`

結論: smoke 規模で collection → dataset → neural 学習 → export → 評価 → screening → package → clean-room の全 phase を統一 CLI で完走し、focused test と full regression を通過した。actual cabt はこの環境で UNAVAILABLE のため fixture で検証し、`ACTUAL_CABT_NOT_RUN` と記録する。Champion は Rule Agent v0 のまま、Promotion は NO_DECISION。

## 環境（doctor 実測）

- GPU: NVIDIA RTX PRO 5000 Blackwell（cap (12,0), BF16 supported, VRAM 48 GiB）
- CPU 28 / RAM 31 GiB / Disk free 405 GiB（soft stop 100 GiB 超）
- interpreter: `/usr/bin/python3` 3.12.3、numpy 2.4.6、torch 2.11.0+cu128、pytest 7.4.4
- resource policy: collection_workers=12、training_device=cuda、RAM 予約 ≥6 GiB
- actual cabt: `diagnose_cabt_capability()` = UNAVAILABLE

## 実行した検証

| 項目 | 結果 | 根拠 |
|---|---|---|
| doctor | PASS | `doctor` が CUDA/CPU/disk と policy を出力 |
| CPU fixture 学習 | PASS | best_metric 有限、device=cpu |
| GPU BF16 学習 + checkpoint 保存/再読込/resume | PASS | device=cuda, bf16=True, reload+resume 成功 |
| pipeline smoke | PASS | 8 phase すべて COMPLETE、clean-room verified |
| pipeline resume | PASS | 完了 run は全 phase SKIPPED、部分 run は残 phase 継続 |
| collection resume 冪等性 | PASS | 完了 run 再実行で JSONL 不変 |
| export parity（torch vs pure-Python） | PASS | max score diff 2.0e-08、top-1 不一致 0、順序 shuffle 不変 |
| Rule v0 fallback | PASS | 破損 export で runtime が None を返し合法手へ fallback |
| 独立 package + clean-room | PASS | 18 members、torch/checkpoint/optimizer 非同梱、legal rate 1.0、privacy 0 |
| focused tests | PASS | `tests/test_offline_training_v1.py` 30 passed |
| full regression | PASS | 650 passed, 7 skipped, 0 failed |

再現コマンド:

```bash
/usr/bin/python3 scripts/run_offline_training_v1.py doctor --config configs/offline_training_v1/smoke.json
/usr/bin/python3 scripts/run_offline_training_v1.py pipeline --config configs/offline_training_v1/smoke.json
PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 /usr/bin/python3 -m pytest -q -p no:cacheprovider
```

## full regression の skip について

7 件の skip はすべて既存テストの `kaggle-environments with cabt is not installed` 条件付き skip（actual cabt 不在に依存）であり、本変更に起因しない。base の「641 passed」は cabt 導入環境での計測値で、本環境では同数のテストが collect され、うち 7 件が skip、30 件の新規テストが追加された（合計 657 = 650 passed + 7 skipped）。

## smoke 品質に関する注記（過学習主張の回避）

smoke は fixture 由来かつ極小（train 12 decisions、1〜2 epochs）であり、neural top-1 が linear baseline を下回る（例: neural 0.5 vs linear 0.83）。これは pipeline の健全性を示すための最小構成であり、competition 性能の根拠ではない。性能主張には pilot/production 規模と actual cabt collection が必要で、本タスクでは未実行。

## 未実行（意図的）

- pilot / production collection、正式 100-game screening、Kaggle 提出、Champion promotion。
- actual cabt smoke: 環境が UNAVAILABLE のため `ACTUAL_CABT_NOT_RUN`。actual 環境が整えば `pipeline --config` の collection.source を `actual` にして再実行する。

## security / privacy scan

- 新規 source・docs・config に秘密情報・絶対 home path・conflict marker なし。唯一の一致 `deck_order` は privacy 拒否を検証する意図的 test fixture（false positive）。
- package archive: secret 0、絶対 path 0、torch/optimizer/checkpoint 非同梱、root に `main.py` と `deck.csv`。

## Integration 修正に関するエビデンス (2026-07-17)

`integration/offline-training-v1` ブランチを `origin/feature/belief-guided-search` 上で動作可能にするため、以下の依存関係の欠落を修正しました。

### 復元したファイル
- `src/mage_ptcg/dataops/__init__.py`
- `src/mage_ptcg/dataops/collector.py`
- `src/mage_ptcg/student/artifact.py`
- `src/mage_ptcg/student/dataset.py` （`split_examples_from_assignments` の追加差分を復元）
- `src/mage_ptcg/student/runtime.py` （`last_decision_trace` の `"student"` キーの追加差分を復元）
- `scripts/accept_c4_actual_training_bundle.py`
- `scripts/export_c4_actual_training_bundle.py`
- `scripts/build_student_actual_artifact.py`
- `tests/test_c4_data_ops.py`
- `tests/test_c4_actual_training_bundle.py`

### 検証結果
- **Import closure 検証**: PASS (`errors: []`)
- **Focused tests**: PASS
  - `tests/test_offline_training_v1.py`: `30 passed`
  - `tests/test_c4_data_ops.py`: `31 passed`
  - `tests/test_c4_actual_training_bundle.py`: `16 passed`
  - `tests/test_actual_agent_viability.py`: `13 passed`
- **Full regression**: PASS (`650 passed, 7 skipped`)
- **Pipeline smoke**: PASS (`collect` から `verify` までの全8フェーズが `COMPLETE`)
- **Package verification**: PASS (Kaggle 提出用アーカイブ内に torch 等が含まれず、Rule v0 fallback の構成が揃っていることを確認)
- **Security / Privacy scan**: PASS (秘密情報、conflict marker、絶対パスの混入なし)
- **Champion / Promotion**: `Rule Agent v0` を Champion およびデフォルトに維持。Promotion は `NO_DECISION`。
- **Actual cabt**: 本環境では `UNAVAILABLE` のため `ACTUAL_CABT_NOT_RUN`。
