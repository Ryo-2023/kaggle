# Checkpoint Benchmark Terminal Summary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** checkpoint benchmark controller のTTY表示を、局ごとのログではなく進捗バーとcheckpoint成績表へ集約する。

**Architecture:** controller は評価履歴の正本 `evaluation_summary.json` と durable scheduler の状態だけを読み、表示専用の renderer へ渡す。評価 worker は進捗バーだけを表示し、完了時の大きな JSON は controller 経由では出さない。

**Tech Stack:** Python 3、既存 `tqdm`、JSON artifact、pytest。

## Global Constraints

- TTYでは1本の評価progress barだけを更新し、局ごとの行ログを出さない。
- 表は評価taskの開始時と終了時だけ再描画し、既存の`evaluation_summary.json`を正本とする。
- faultまたは未完了の結果をscoreとして表示しない。
- 非TTYの集約出力契約は変更しない。

---

### Task 1: 表示rendererの契約を追加する

**Files:**
- Modify: `src/mage_ptcg/continuous_league/controller.py`
- Test: `tests/test_continuous_league_contracts.py`

- [x] rendererが最新10件のstep、score rate、95%区間、最弱相手、fault、前回差分とキュー件数を1つの文字列へ整形する失敗テストを書く。
- [x] TTY以外ではrendererが何も出力しないことを失敗テストへ含める。
- [x] `evaluation_summary.json`とscheduler状態を受け取る最小rendererを実装する。
- [x] 対象テストを実行する。

### Task 2: controller lifecycleへrendererを接続する

**Files:**
- Modify: `src/mage_ptcg/continuous_league/controller.py`
- Modify: `src/mage_ptcg/continuous_league/cli.py`
- Test: `tests/test_continuous_league_cli.py`

- [x] controllerがtask開始前と終了後にrendererを呼ぶ失敗テストを書く。
- [x] `--checkpoint-history`でhistory rootを明示し、TTY時だけrendererを有効にする最小実装を書く。
- [x] task workerの完了JSONをTTYへ流さない設定を追加する。
- [x] 対象テストを実行する。

### Task 3: 運用文書と回帰検証

**Files:**
- Modify: `docs/runbooks/continuous-league.md`
- Test: `tests/test_continuous_league_contracts.py`, `tests/test_continuous_league_cli.py`, `tests/test_continuous_league_evaluation_history.py`

- [x] controller起動例へ`--checkpoint-history`を追加する。
- [x] 進捗バーと表の表示規則を文書化する。
- [x] 関連pytest、文書検証、`git diff --check`を実行する。
