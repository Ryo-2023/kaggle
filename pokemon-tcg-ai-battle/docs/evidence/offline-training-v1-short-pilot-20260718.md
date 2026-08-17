# Offline Training v1 — 実データ Short Pilot Summary (2026-07-18)

本文書は `docs/evidence/offline-training-v1-short-pilot-20260718.json`（machine-readable 証跡）の要約である。目的は新規アルゴリズムの実装ではなく、既存の Offline Training v1（collection → audit → training → export → package → verify → evaluation）経路を実データで一度通し、long-running offline training へ進めるか判断できる証跡を作ることである。

## メタデータ

| 項目 | 値 |
|---|---|
| 実行日 | 2026-07-18 |
| worktree | `pokemon-tcg-ai-battle-short-pilot`（`git worktree add` で新規作成） |
| branch | `feature/offline-training-short-pilot` |
| base commit | `e69ef63f9024c0c877fd9630f45036ff385867ea`（正典 `feature/belief-guided-search` HEAD） |
| Champion/default | Rule Agent v0（不変） |
| Promotion | `NO_DECISION`（不変） |
| pilot run ID | `offline-short-pilot-actual-20260718-r1` |
| smoke run ID | `offline-short-pilot-smoke-actual-20260718-r1` |

## 結論

**Decision: `GO_LONG_RUN`**（Champion 昇格判定ではない）。

collection・dataset audit・split・training・resume・export・package・clean-room verify・100 match preflight は全て安全条件を満たした。Rule Agent v0 との real actual-cabt paired evaluation（200 games、2 seed、seat-swap）では pilot Student の勝率が 40.5%（95% Wilson CI [33.9%, 47.4%]）と Rule Agent v0 に劣後するが、致命的崩壊ではなく、fallback は 200 game中 0 回（常に実推論）だった。offline top-1 fidelity は 93.9%（linear baseline 91.1% を +2.8pt 上回る）であり、学習が機能した合理的な兆候がある。以上より long-running training へ進む価値があると判断する。ただし本 pilot の勝率差は long run が埋めるべき目標であり、Promotion 判断ではない。

## 1. Capability 確認

`scripts/cabt_capability.py` により `status: READY`、`actual_execution_allowed: true` を確認した。

## 2. Smoke collection（actual, 2 episodes）

`configs/offline_training_v1/short_pilot_smoke_actual.json`（新規、既存 `smoke.json` は fixture 専用のため actual 版を追加）を用いて `collect` のみ実行。

- episodes=2、decisions=52、candidates=218
- `actual_cabt: ACTUAL_CABT_RUN`（fixture ではなく実環境実行の証拠）
- privacy_violations=0、`validate_run()` standalone 検証も `valid: true`
- 選択 action が legal candidate 集合に含まれることをダイジェスト照合で確認

## 3. Pilot collection（actual, 128 episodes）

既存 `pilot.json`（`collection.source=actual`, `games=128`）をそのまま使用（target 100〜300、下限 100 を満たす正式 preset のため新規 config は作成しなかった）。

- games_requested=128, games_committed=128（crash/incomplete episode なし）
- decisions=5864, candidates=37642
- `performance_eligible: true`, `dataset_status: ACTUAL_TRAINING`
- privacy_violations=0, duplicate_decision_count=0
- collector 側 split（80/20）: train=102 episodes, validation=26 episodes, split_overlap_count=0
- 収集 wall-clock: 38.1 秒

## 4. Dataset audit / episode-level split

`build-dataset`（pilot.json の train/val/test=0.8/0.1/0.1, split_seed=12345）で以下を確認。

- record_count=5727（5864 decisions 中 137 件は decision-level cross-split 重複としてquarantine、破棄ではなく明示的除外）
- split_episode_counts: train=102, validation=13, test=13
- split_decision_counts: train=4274, validation=562, test=891
- episode 単位 split のため同一 episode の train/validation 跨りは構造上発生しない
- **再現性**: `build-dataset --force` で再実行し、`manifest_hash` が完全一致することを確認済み

## 5. 短時間学習

pilot.json の training 設定（epochs=20, lr=3e-4, wd=1e-4, patience=5, seed=7, hidden_dims=[128,64] compact, max_batch_decisions=512）で学習。

- device 解決: `cuda`（BF16 autocast）
- train_loss: 1.5017 → 0.5073（単調減少）
- val_nll: 1.4217 → 0.4544（単調改善）
- val_top1: 0.6495 → 0.9377
- early stop: 未発動（20 epoch 完走、patience=5 を一度も超過せず）
- model_hash: `9ed2268e09a9bf52d84b508ddc552d88514c50fae0f1894caa3337d2615d4a70`
- model_purpose: `NEURAL_ACTUAL_TRAINED`

### interruption / resume 検証

固定 sleep ではなく、`run_manifest.json` の `phase_statuses.train` が `RUNNING` になったことをポーリングで確認してから（最大20秒、0.05秒間隔）SIGINT を送信した。

- 送信後: `phase_statuses.train: INTERRUPTED`, `error_summary: "signal 2"`, exit code 130
- `resume --run-dir ...` を実行し、train 残り epoch → export → evaluate → screen → package → verify までを同一呼び出しで完走
- resume_count（最終）= 4

## 6. Export / Evaluate / Package / Clean-room verify

- offline evaluate（test split）: neural top1=0.9394, linear baseline top1=0.9113（neural が +0.0281）, neural_nll=0.6029
- screen（fixture harness、実装上 win/loss を測定しない設計。legality/fallback のみ）: legal_action_rate=1.0, fallback_rate=0.0, verdict=`INSUFFICIENT_EVIDENCE`（想定通り）
- package: archive_sha256=`0aab9654dd55b4b3fc412d3a82f55df40fd0f0575625e95388e9ed13d4528997`, members=18
- clean-room verify: verified=true, executed_cases=8, legal_cases=8, illegal_cases=0, exception_cases=0, fallback_cases=2（missing-model fallback レーンを意図的に含む負のケース）, legal_action_rate=1.0

**既知の注意点**: `dist/kaggle/neural-student-v1/` は `cli.py` にハードコードされた単一の共有 publish 先であり、この worktree で offline-training pipeline を（テストスイート経由も含め）再実行するたびに上書きされる。実際、本 pilot 完了後に full regression（`tests/test_offline_training_v1.py::test_signal_interruption_and_resume` 経由）を実行した結果、`dist/` は smoke_long fixture run の内容で上書きされていた。よって `dist/` は安定した pilot 証跡ではなく、run-scoped な `runs/offline-training-v1/<run-id>/package/` を正とする。本文書の全ハッシュは run-scoped path から取得し、full regression 実行後も内容が不変であることを確認済み。

## 7. Rule Agent v0 との Paired Evaluation（新規 glue code）

### 7.1 既存実装の欠落

調査の結果、実 actual-cabt で Rule Agent v0 と `neural-student-v1` を直接対戦させるツールは存在しなかった。

- pipeline 内蔵の `screen` は fixture-only harness であり、実装上 win/loss を測定しない（`docs/offline-training-v1.md` に明記された既知の制約）
- `scripts/run_actual_agent_viability.py` の既存 `student` challenger は旧世代 linear C4（`StudentV0Model`）artifact 専用であり、`neural-student-v1` の export 形式とは非互換

### 7.2 追加した最小限の接続コード

`src/mage_ptcg/evaluation/actual_agents.py` と `scripts/run_actual_agent_viability.py` に `neural_student` challenger を追加した。既存の real actual-cabt 対戦harness（seat-swap、seed制御、privacy scan、fail-closed inventory）をそのまま再利用し、`NeuralRuntimePolicy` を Rule Agent v0 fallback 付きでロードするだけの薄い factory を足しただけである。

- **安全境界を維持**: `main.py`（Kaggle 提出 entrypoint）は一切変更していない。新規 factory は `mage_ptcg.evaluation.actual_agents`（"main.py から import されない" と明記された evaluation 専用モジュール）にのみ存在し、提出面の default agent から neural Student へは到達不能なまま。
- TDD で実装（先に5件の失敗するテストを書き、RED を確認してから実装）。`tests/test_actual_agent_viability.py` 26/26 pass、`tests/test_offline_training_v1.py` と合わせて 56/56 pass。

### 7.3 100 match preflight（seed=7000）

| 項目 | 値 |
|---|---|
| attempted/completed | 100 / 100 |
| champion(Rule v0) win / challenger win / draw | 50 / 50 / 0 |
| invalid / crash / timeout | 0 / 0 / 0 |
| challenger unexpected fallback | 0 |
| legal_action_rate（両者） | 1.0 |
| gate_status | `CLEAN_PASS` |
| match latency (p50/p95/max, 秒) | 0.140 / 0.715 / 0.867 |
| challenger decision latency (p50/p95/max, ms) | 3.11 / 10.67 / 92.8 |

### 7.4 Paired evaluation（seed=8000, 追加バッチ）

champion(Rule v0) win=69, challenger win=31, draw=0。invalid/crash/timeout はいずれも 0、gate_status=`CLEAN_PASS`。

### 7.5 合算結果（200 games, 2 seed, seat-swap）

| 指標 | 値 |
|---|---|
| champion(Rule v0) win | 119 |
| challenger(neural Student) win | 81 |
| draw | 0 |
| challenger win rate | 0.405 |
| 95% Wilson CI | [0.339, 0.474] |
| champion seat0 win rate | 0.600 |
| champion seat1 win rate | 0.590 |
| challenger fallback（200 game 合計） | 0 |

**解釈**: seed 間でばらつきがある（seed7000: 50/100、seed8000: 31/100）が、これは n=100 の二項分布として許容範囲内のばらつきであり、単一 seed 評価が誤解を招きうることを示す好例でもある。seat0/seat1 の champion 勝率がほぼ同一（60.0%/59.0%）であることから、座席由来のバイアスではなく、pilot 規模（128 games 収集・compact model・20 epoch）の behavior cloning に内在する再現性の限界（教師の決定的方策を模倣する際の compounding error）である可能性が高いと解釈する。offline top-1 fidelity（93.9%）は高い一方、live match での勝率転移が不完全であることは、この規模の pilot として想定内の所見であり、pipeline 自体の欠陥を示すものではない。

## 8. Tests

- 新規 focused tests: `tests/test_actual_agent_viability.py` 26/26 pass
- 関連 focused tests 合算: 56/56 pass（`test_actual_agent_viability.py` + `test_offline_training_v1.py`）
- `git diff --check`: clean
- full regression: `PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 /usr/bin/python3 -m pytest -q -p no:cacheprovider` → **1022 passed, 1 failed**, 5 warnings, 159.02s
  - 失敗: `tests/test_collect_offline_training_v1_evidence.py::test_run_command_safe_timeout_and_child_cleanup`
  - **本 pilot の変更とは無関係と確認済み**: 同一テストが、変更を一切加えていない元の worktree（`~/kaggle/pokemon-tcg-ai-battle`, `git status --short` 差分なし）でも同じ base commit で同一の assertion で失敗することを確認した（3/3 再現）。本 pilot の diff は `scripts/collect_offline_training_v1_evidence.py` にもそのテストファイルにも一切触れていない。0.5 秒という timeout に対して、このサンドボックスでの子プロセス起動 latency が間に合わないことが原因と推定される、環境依存の pre-existing 事象と判断し、修正は本タスクの範囲外として行わなかった（隠さず本文書に記録）。
  - `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` を付けない場合、ROS `launch_testing` プラグインが自動ロードされ、無関係なモジュール（`mage_ptcg.belief`）の import エラーで collection 自体が失敗する（`docs/evidence/offline-training-v1.md:36` に既知事象として明記済み）。

## 9. Git 状態

| 項目 | 値 |
|---|---|
| branch | `feature/offline-training-short-pilot` |
| base commit | `e69ef63f9024c0c877fd9630f45036ff385867ea` |
| 変更ファイル | `scripts/run_actual_agent_viability.py`, `src/mage_ptcg/evaluation/actual_agents.py`, `tests/test_actual_agent_viability.py`, `configs/offline_training_v1/short_pilot_smoke_actual.json`（新規） |
| `runs/`, `dist/` | git-ignored、commit 対象外 |
| 実データ・checkpoint・archive | Git へ一切追加していない |

## 10. 未実施事項の確認

- Kaggle submission: 未実施
- long-running 本学習（production.json、2048 games）: 未実施
- Champion promotion: 未実施（Promotion は `NO_DECISION` のまま）
- Student のdefault化: 未実施
- Rule Agent v1 / HOLD 中 Gemini Support 機能への接続: 未実施
