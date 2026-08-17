# R2D3 Performance Redesign Implementation Plan

> **For agentic workers:** Execute this plan inline, task-by-task, with a red-green test cycle for each task. Do not start a learning experiment.

**Goal:** 偏りのない評価、多様な Replay/PSRO、品質済み直接模倣、actor-visible shaping、Replay 再利用上限を備えた新規 R2D3 実験系列を作る。

**Architecture:** Performance runner に評価 schedule、教師 calibration、Replay recipe、PSRO quota を追加する。R2D3 core は transition の reward と demonstration、source-aware replay sampler、BC 損失を受け、全ての新しい構成を checkpoint identity と manifest に含める。

**Tech Stack:** Python 3.12、pytest、PyTorch、既存 `mage_ptcg.policy_learning.r2d3`。

## Global Constraints

- Kaggle 提出、commit、push、外部データ取得、学習実験の起動を行わない。
- `main.py`、`deck.csv`、`agents/rule_agent.py` は変更しない。
- 報酬と教師入力は actor-visible な public state とローカル submitted asset のみに限定する。
- validation/deck_holdout/final_holdout asset は Replay と教師へ入れない。
- stage/checkpoint/replay identity は recipe 変更時に旧 artifact を再利用しない。

---

### Task 1: Seat-balanced validation schedule and summaries

**Files:**
- Modify: `scripts/policy_learning/run_r2d3_multiseed_psro_performance.py`
- Modify: `tests/test_submitted_opponents_r2d3.py`

**Interfaces:**
- Produce `validation_schedule(assets, games, *, seed_namespace) -> list[dict[str, int | str]]`.
- Produce `summarize_evaluation(rows) -> dict[str, object]`.

- [ ] Write a failing test that two assets and eight games create two games for every asset×seat cell, and a non-divisible game count raises `ValueError`.
- [ ] Run `PYTHONPATH=.:src .venv/bin/python -m pytest -q tests/test_submitted_opponents_r2d3.py -k validation_schedule` and confirm failure.
- [ ] Implement deterministic Cartesian scheduling, separate stable seed namespaces, and Wilson summaries.
- [ ] Update parallel and serial validation jobs to consume explicit asset/seat/seed schedule entries; write `evaluation_summary.json` beside each evaluation CSV.
- [ ] Re-run the focused tests and then the R2D3 test module.

### Task 2: Public potential rewards and quality-only demonstrations

**Files:**
- Modify: `src/mage_ptcg/policy_learning/r2d3/sequence.py`
- Modify: `scripts/policy_learning/run_submitted_r2d3_e2e.py`
- Modify: `scripts/policy_learning/run_r2d3_multiseed_psro_performance.py`
- Modify: `tests/test_submitted_opponents_r2d3.py`

**Interfaces:**
- Produce `public_prize_potential(public_state) -> float` and `shape_episode_rewards(traces, outcome, gamma) -> list[float]`.
- Extend trace records with `potential`; never preserve a raw observation or hidden state.

- [ ] Write failing tests proving a prize gain yields positive intermediate shaping, the terminal transition uses `outcome - potential`, and a forbidden hidden key raises before trace persistence.
- [ ] Run focused tests and confirm they fail because the helpers do not exist.
- [ ] Implement the helpers from the design: `0.10 * (opponent prizes - own prizes) / 6`, terminal potential zero, and shaped reward insertion during replay construction.
- [ ] Mark only winning, calibration-qualified submitted trajectories as demonstrations; retain other legal trajectories for TD learning without BC labels.
- [ ] Expand the model support through `R2D3ModelConfig` and include reward recipe in replay/checkpoint identity.
- [ ] Re-run focused tests and the R2D3 test module.

### Task 3: BC learner objective and source-balanced replay

**Files:**
- Modify: `src/mage_ptcg/policy_learning/r2d3/learner.py`
- Modify: `src/mage_ptcg/policy_learning/r2d3/replay.py`
- Create: `src/mage_ptcg/policy_learning/r2d3/source_balanced_replay.py`
- Modify: `scripts/policy_learning/run_submitted_r2d3_e2e.py`
- Modify: `scripts/policy_learning/run_r2d3_multiseed_psro_performance.py`
- Modify: `tests/test_submitted_opponents_r2d3.py`

**Interfaces:**
- `SourceBalancedReplayPartitions.from_replay(replay) -> SourceBalancedReplayPartitions`.
- `sample(batch_size, beta, demonstration_ratio, seed, episode_first) -> ReplaySample` and priority/checkpoint methods matching existing replay use.
- Learner metric includes `bc_loss`.

- [ ] Write failing tests for a nonzero masked BC loss on a demonstration, zero BC loss without demonstrations, deterministic balanced source sampling, and priority-state round trip.
- [ ] Run focused tests and confirm failure.
- [ ] Implement masked selected-action cross entropy weighted by `LearnerConfig.bc_weight` while preserving TD, conservative, margin, and priority terms.
- [ ] Implement source partitions for the six named sources; sample all non-empty sources evenly, reserve the configured demonstration fraction, and return global priority indices.
- [ ] Make replay construction persist source counts and make training manifests persist source draw counts.
- [ ] Re-run focused tests and the R2D3 test module.

### Task 4: Teacher calibration, replay budget, and PSRO diversity

**Files:**
- Modify: `scripts/policy_learning/run_r2d3_multiseed_psro_performance.py`
- Modify: `src/mage_ptcg/policy_learning/r2d3/online_collection.py`
- Modify: `tests/test_submitted_opponents_r2d3.py`

**Interfaces:**
- Add `teacher_calibration` before `replay_collection`.
- Produce `balanced_mixture_quotas(mixture, games, floor_probability) -> list[MixtureMember]`.
- Add profile draw caps and PSRO floor probability to training identity/manifests.

- [ ] Write failing tests for calibration’s balanced seat gate, update capping from replay windows and batch size, and four-member PSRO quotas with a 0.15 floor.
- [ ] Run focused tests and confirm failure.
- [ ] Add calibration stage: local Rule v0 games in both seats, fault-free balanced win rate ≥0.5, teacher registry manifest, and fail-closed zero-teacher behavior.
- [ ] Replace reference-update scaling with per-stage nominal draw/window caps and record actual caps in manifests.
- [ ] Transform solver mixture with its probability floor and construct a deterministic quota schedule. Verify every member’s games and sequences before best response.
- [ ] Re-run focused tests and the R2D3 test module.

### Task 5: Integration contracts and non-execution handoff

**Files:**
- Modify: `tests/test_submitted_opponents_r2d3.py`
- Modify: `docs/status/current_status.md`
- Modify: `docs/status/handoff.md`

- [ ] Write a contract test that the new profile has all stages, uses no automatic submission command, rejects old Replay identities, and leaves holdout consumption rules unchanged.
- [ ] Run it to confirm it fails before the final runner integration is complete.
- [ ] Update the status documents with implemented-but-not-run status and the exact future command placeholder only after code is verified.
- [ ] Run `PYTHONPATH=.:src .venv/bin/python -m pytest -q tests/test_submitted_opponents_r2d3.py`, `python scripts/docs/validate_docs.py`, and `git diff --check`.
- [ ] Do not commit; report every remaining uncommitted change separately.
