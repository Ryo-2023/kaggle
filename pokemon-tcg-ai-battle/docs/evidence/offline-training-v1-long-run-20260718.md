# Offline Training v1 — 実データ Long Run Summary (2026-07-18)

本文書は `docs/evidence/offline-training-v1-long-run-20260718.json`（machine-readable 証跡）の要約である。目的は 2048 試合の収集データを用いてニューラル Student モデルの production-scale 学習および評価を行い、結果を詳細な証跡として残すことである。

## メタデータ

| 項目 | 値 |
|---|---|
| 実行日 | 2026-07-18 |
| worktree | `pokemon-tcg-ai-battle` (main workspace) |
| branch | `feature/belief-guided-search` |
| base commit | `062533feee8ac91914d10fd67231181f6ef7949e` (Short Pilotマージ後の最新HEAD) |
| Champion/default | Rule Agent v0（不変） |
| Promotion | `NO_DECISION`（不変） |
| production run ID | `offline-long-run-actual-20260718-r1` |

## 結論

**Decision: `PROMOTION_CANDIDATE_NO_DECISION_MAINTAINED`** （性能的には昇格可能だが、最重要規則に基づき Champion および default は変更せず `NO_DECISION` を維持する）。

ニューラル Student v1 は 2048 試合の production 収集データ（計 96,530 decisions）を用いた 40 エポック学習（early stopping により 26 エポックで完了）により、offline top-1 一致率 **94.33%** を記録し、線形ベースライン（90.96%）を **+3.37pt** 上回った。
さらに、Rule Agent v0 との実 actual-cabt paired evaluation（計 400 試合、4 seed、座席入れ替え）において勝率 **57.75%** （95% Wilson 信頼区間 **[52.86%, 62.50%]**）を記録した。信頼区間の下限が 50% を有意に上回っており、Rule Agent v0 に対する性能優位が統計的に証明された。
また、100 試合の safety preflight および 400 試合の paired evaluation において、Challenger の推論における不正手、クラッシュ、タイムアウト、および Rule Agent v0 への fallback はすべて **0 回** （合法手選択率 100%）であり、完全な runtime 動作の安全性を実証した。

---

## 1. Capability 監査
[scripts/cabt_capability.py](file:///home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle/scripts/cabt_capability.py) により `status: READY`、`actual_execution_allowed: true` であることを事前に確認した。

## 2. Production Collection (2048 games)
[configs/offline_training_v1/production.json](file:///home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle/configs/offline_training_v1/production.json)（`collection.source=actual`, `games=2048`）を用いて実行。

- games_requested = 2048, games_committed = 2048 (クラッシュや未完了の試合なし)
- decisions = 96,530, candidates = 628,632
- privacy_violations = 0, duplicate_decision_count = 0
- 収集 wall-clock: 424.0 秒

## 3. Dataset build / split
`build-dataset` により、重複とプライバシーのスキャンを経て episode-level split を実行。

- record_count = 92,902 (96,530 decisions 中 3,628 件の重複/コンフリクトを明示的除外)
- split_episode_counts: train=1638, validation=205, test=205
- split_decision_counts: train=74449, validation=7574, test=10879
- manifest_hash: `5f405f0ec798aa329fc79a605d25e1fdfbc27373b4e9565eccd5bc215b68afee`
- `--force` 再実行による split の決定的再現性を確認済み。

## 4. Production Neural Student Training
`production.json` 設定（epochs=40, lr=3e-4, patience=5, hidden_dims=[256,128,64]）で GPU (RTX 5000 Blackwell) 上で実行。

- early stopping: エポック 21 で最良 NLL 0.154983 を記録した後、5 エポック改善が無かったため、エポック 26 で正常に早期終了。
- model_hash: `94564328a10f1e914beb63073235722694093e281905b5cbd546b2a35742dea4`
- model_purpose: `NEURAL_ACTUAL_TRAINED`

## 5. Export / Evaluate / Package / Clean-room verify
- **Held-out Evaluate (test split)**:
  - neural_student_v1 top-1: **94.33%** (NLL: 0.1720)
  - linear_student_v0 top-1: **90.96%** (NLL: 1.1266)
  - neural - linear top-1 delta: **+3.37pt**
- **Screening (fixture harness)**: legal_action_rate=1.0, fallback_rate=0.0, verdict=`INSUFFICIENT_EVIDENCE` (仕様通り)
- **Package**: package_archive_sha256 = `d4e2cdcb4557b4bbb9968266a0990525a7e172b9a9e664b477a21f957892e67d`, members=18, package path = `runs/offline-training-v1/offline-long-run-actual-20260718-r1/package/neural-student-v1/`
- **Clean-room Verify**: verified=true, legal_action_rate=1.0, exceptions=0, unexpected fallback=0.

## 6. Safety Preflight (100 games)
パッケージした Student モデルを用い、Rule Agent v0 相手の 100 試合安全性検証 (seed=9000) を実施。

- Challenger 勝率: 47勝53敗
- Challenger 合法手選択率: 100% (2456/2456 decisions)
- Challenger 不正手、クラッシュ、タイムアウト、例外、unexpected fallback: すべて **0 件**
- gate_status: `CLEAN_PASS`

## 7. Paired Evaluation against Rule Agent v0 (400 games)
4つの事前登録シード (10000, 11000, 12000, 13000) で各 100 試合、座席入れ替え対戦を実施。

| シード | Challenger勝敗 | 勝率 |
|---|---|---|
| seed 10000 | 67勝 33敗 | 67.0% |
| seed 11000 | 56勝 44敗 | 56.0% |
| seed 12000 | 53勝 47敗 | 53.0% |
| seed 13000 | 55勝 45敗 | 55.0% |
| **合計** | **231勝 169敗** | **57.75%** |

- **95% Wilson 信頼区間**: **[52.86%, 62.50%]** (下限が 50.0% を上回る)
- Challenger 合法手選択率: 100%
- Challenger 不正手、クラッシュ、タイムアウト、例外、unexpected fallback: すべて **0 件**
- **解釈**: Short Pilot での 40.5% の勝率劣後から、収集エピソード数を 128 から 2048 へ増やし、モデル表現能力 (hidden_dims=[256,128,64]) と学習エポック数を引き上げたことで、模倣学習における compounding error が大幅に軽減された。Rule Agent v0 を有意に上回る対戦勝率 57.75% が実証された。

## 8. Tests
- focused tests: 26/26 passed (`tests/test_actual_agent_viability.py`)
- related focused tests: 56/56 passed
- full regression tests: `1022 passed, 1 failed, 5 warnings` (1件の失敗は、本作業とは無関係な環境依存の flaky test `test_run_command_safe_timeout_and_child_cleanup` であり、 Short Pilot 以前の pristine master HEAD でも同様に再現することを確認済みであるため、リグレッションではないと分類)。

## 9. Git 状態
- branch: `feature/belief-guided-search` (直接 push 予定)
- 変更ファイル: ドキュメントおよび証跡ファイルのみ
- `runs/`, `dist/`: git-ignored であり commit 対象外。実データや checkpoint は一切 Git に追加していない。
