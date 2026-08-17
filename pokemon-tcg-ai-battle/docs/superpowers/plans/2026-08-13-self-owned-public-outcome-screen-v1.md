# Self-Owned Public Outcome Screen v1 Implementation Plan

> **For agentic workers:** TDDを守り、各タスクをRED→GREEN→検証の順で実行する。既存production/evaluatorは変更しない。

**Goal:** self-owned Rule v0の実CABT public rolloutを保存し、action-conditioned outcomeからbounded native-preserving overlayを作り、同一common24でRule v0 controlと96局screenする。

**Architecture:** 新規research-only moduleが、実ゲーム中の`env.steps`をメモリ内でstrict public projectionへ変換し、`persist_game_evidence`とhash-bound outcome rowsだけを保存する。同moduleのoverlay factoryはRule v0を先に呼び、MAINの合法option typeだけをbounded再順位付けし、失敗時は完全にRule v0へ戻る。新規scriptは`EvaluationGameV1`/`run_parallel_cabt_evaluation`へrunner_refで接続し、native poolを実 opponent として使う。

**Tech Stack:** Python 3.11、kaggle-environments CABT、既存 public trajectory projection/evidence、`parallel_cabt_evaluator_v1`、pytest。

## Global Constraints

- `main.py`、`agents/rule_agent.py`、既存evaluator、pool/artifactは編集・上書きしない。
- raw observation、private hand/deck/prize、logs、search payloadは永続化しない。
- synthetic opponent/tableは性能根拠に使わず、opponents/pool_manifest.jsonのnative local-eval-only assetsだけを共通arenaへ入れる。
- candidate/controlとも`authority=false`、`research_only=true`、native-first/fail-closedをmanifestへ固定する。
- 96局でfault、seat、override coverage、paired cell差を確認し、明確なpositiveのみ384へ進める。

### Task 1: Public rollout capture and bounded outcome table

**Files:**
- Create: `src/mage_ptcg/meta_specialist/self_owned_public_outcome_v1.py`
- Test: `tests/meta_specialist/test_self_owned_public_outcome_v1.py`

**Interfaces:**
- `capture_rule_v0_rollout_v1(...) -> dict`
- `build_bounded_action_overlay_v1(records) -> dict`
- `build_overlay_agent_v1(deck, table, baseline_sha, config_sha)`

- [ ] RED: assert public-only record projection, no forbidden fields, bounded deterministic table, and exact Rule fallback on malformed/illegal candidate.
- [ ] GREEN: run real CABT in memory, call existing public projection/evidence writer before persistence, derive action-type outcome deltas with bounded cap, and expose a hash-bound fail-closed factory.
- [ ] Verify: focused pytest and py_compile.

### Task 2: Native common24 screen bridge

**Files:**
- Create: `scripts/run_self_owned_rule_v0_public_outcome_screen_v1.py`
- Extend: `tests/meta_specialist/test_self_owned_public_outcome_v1.py`

**Interfaces:**
- `build_screen_games_v1(...) -> tuple[EvaluationGameV1, ...]`
- `run_screen_game_v1(payload) -> Mapping[str, object]`
- CLI captures 1–2 real rollouts, materializes table/manifest, then runs baseline+candidate on the same common24 seed cells.

- [ ] RED: assert baseline/candidate identity, same cell seeds, native opponent binding, no synthetic IDs, and authority false.
- [ ] GREEN: use existing pool loader/factory and parallel evaluator only through new runner_ref; workers=1 for strict evidence.
- [ ] Verify: focused tests, then one real rollout smoke and 96-game screen.

### Task 3: Evidence and gate

**Files:**
- Create: `docs/evidence/autonomous-self-owned-public-outcome-screen-v1-20260813.md`
- Create: `runs/final-sprint-autonomous/self-owned-public-outcome-screen-v1-*/` (generated, untracked)

- [ ] Record source/evaluator/pool/protocol/deck/config/table SHAs, rollout count, screen summaries, faults, seat/support/override metrics, and exact commands.
- [ ] If 96 delta is clearly positive and coverage is nonzero with fault0/seat gate, run separate 384 confirmation; otherwise record NO-GO and do not launch 384.
- [ ] Verify JSON reload, SHA checks, `git diff --check`, and preserve all pre-existing dirty files.
