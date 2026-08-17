# Offline Training v1 — 再利用インベントリ

作成日: 2026-07-17 / branch: `integration/offline-training-v1` / base HEAD: `a4f8d54`

目的: P0 本体（`scripts/run_offline_training_v1.py` 統一 CLI）を新設するにあたり、既存実装のうち再利用する責務と、新規に追加する薄い接続層を確定する。同じ責務のモジュールを重複実装しない。

## 環境の観測事実（doctor 実測、2026-07-17）

| 項目 | 実測値 |
|---|---|
| GPU | NVIDIA RTX PRO 5000 Blackwell（sm_120, cap (12,0)）, BF16 supported |
| VRAM | 48 GiB（実測 48935 MiB） |
| CPU | 28 logical cores |
| RAM | 31 GiB total / 27 GiB available |
| Disk | 405 GiB free（soft stop 100 GiB を上回る） |
| 正典 interpreter | `/usr/bin/python3` 3.12.3 + numpy 2.4.6 + torch 2.11.0+cu128 + pytest 7.4.4 |
| `uv run --active` | uv 管理 3.11、numpy/torch 不在。テスト不能 |
| actual cabt | `diagnose_cabt_capability()` = `UNAVAILABLE`（`actual_execution_allowed=false`） |

判断: 依存が揃う唯一の interpreter は `/usr/bin/python3`。641 tests を収集でき既知 baseline と一致する。したがって実行・テストは `/usr/bin/python3` を正典とし、CLI は interpreter 非依存に実装する。actual cabt が UNAVAILABLE のため collection は fixture mode で pipeline を検証し、actual 実行は `ACTUAL_CABT_NOT_RUN` と記録する。

## 再利用する既存コンポーネント

| 責務 | file | 主要 symbol | 再利用方針 | 拡張点 | リスク |
|---|---|---|---|---|---|
| 再開可能 actual cabt 収集 | `src/mage_ptcg/dataops/collector.py` | `collect_actual_dataset`, `DecisionCaptureAgent`, `split_by_episode_group`, `validate_run`, `scan_public_artifact` | CLI `collect` から呼び出す。per-game 再開・episode split・privacy scan・atomic write を完備 | fixture `match_runner` と `capability_report` を注入して actual 不在時も pipeline を検証 | actual cabt 実行環境が無い |
| 候補特徴抽出（train/runtime 共用） | `src/mage_ptcg/student/features.py` | `state_features`, `action_features`, `combined_features`, `FEATURE_VERSION` | neural の学習と runtime の両方で共用（32+64=96 次元） | 変更しない | 特徴変更で schema hash 不一致 |
| 決定単位データ契約 | `src/mage_ptcg/student/dataset.py` | `RuleBCExample`, `load_dataset`, `split_by_episode_group` 経由の episode 分割 | canonical shard の record 本体として `RuleBCExample.to_dict()` を採用 | gzip shard 化・shard hash は新規層で付与 | schema version 固定 |
| 線形 Student（baseline） | `src/mage_ptcg/student/model.py` | `StudentV0Model`, `train_model`, `example_matrix` | 比較 baseline として保持（無変更） | 変更しない | — |
| 線形 runtime（fail-closed） | `src/mage_ptcg/student/runtime.py` | `RuntimeStudentPolicy` | tie-break 契約 `(-score, digest, option_index)` を neural runtime でも踏襲 | 変更しない | — |
| オフライン評価指標 | `src/mage_ptcg/student/evaluation.py` | `evaluate_model` | 線形 baseline の評価に使用。neural は同指標を新規実装 | top-1/top-3/NLL/legal rate | — |
| provenance artifact | `src/mage_ptcg/student/artifact.py` | `build_artifact`, `feature_schema`, `load_validated_artifact` | manifest 語彙・feature_schema_hash 計算を参照 | neural 用 export は新規 | — |
| Kaggle package + clean-room | `scripts/build_student_submission.py` | `build_student_submission`, `verify_student_submission`, `_write_tar`, `_extract`, `_safe_path` | 別 package builder の設計原型（tar 正準化・safe path・subprocess `-I` 検証・secret scan） | neural 用 `dist/kaggle/neural-student-v1/` を新規に別実装 | 既存 package を上書きしない |
| privacy / secret scan | `src/mage_ptcg/competition/redaction.py`, `src/mage_ptcg/observability/cabt_trace.py` | `secret_scan`, `find_forbidden_keys`, `FORBIDDEN_OBSERVATION_KEYS` | dataset・export・package・trace の privacy 検査で共用 | 変更しない | — |
| atomic write / digest | `src/mage_ptcg/distillation/contracts.py` | `atomic_write_json`, `digest`, `canonical_json` | digest/canonical_json を共用 | manifest は fsync 付き atomic を新規実装（強い耐障害性） | 既存 atomic は fsync 無し |
| Rule Agent v0（Champion / fallback） | `main.py`, `agents/rule_agent.py` | `make_rule_agent`, `choose_rule_indices` | neural runtime の fallback 先 | 変更しない | Champion 不変 |

## 新規追加する薄い接続層（`src/mage_ptcg/offline_training/`）

同責務の重複を避け、以下だけを新規実装する。

- `config.py`: preset 検証と resolved config。
- `environment.py`: doctor と動的 resource policy（workers/RAM/VRAM/disk）。
- `runstate.py`: run directory、fsync 付き atomic manifest、lock（PID + process start marker）、events.jsonl、phase status、signal 処理。
- `dataset.py`: gzip JSONL shard の producer/consumer、shard/dataset hash、episode 決定的 split、derived numpy cache。
- `neural.py`: candidate-wise PyTorch MLP、masked softmax CE、checkpoint/resume、OOM 回復（import guard 付き）。
- `export.py` / `neural_runtime.py`: pure-Python 安全 export と torch 非依存 runtime、Rule v0 fallback、parity。
- `evaluate.py`: neural 用オフライン評価と tiny screening。
- `package.py`: `dist/kaggle/neural-student-v1/` 独立 package builder と clean-room verifier。
- `cli.py`: 統一 CLI dispatch。

Champion は Rule Agent v0 のまま。Promotion は NO_DECISION。既存の線形 Student・C4 package・default agent は変更しない。
