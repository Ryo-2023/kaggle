# V4 弱 matchup・seat・action 改善計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans or superpowers:subagent-driven-development to implement this plan task-by-task. 各段階は成果物と検証結果を確認してから次へ進む。

**Goal:** Archaludon V4 の弱い相手・seat0・低頻度actionを特定し、最小のデータ/objective変更で実戦勝率を改善する。

**Architecture:** まずwave4のsealed validationへaction-type診断を追加し、次にheldout対戦の相手×seat×action traceを収集する。診断で確認した偏りだけを対象に、baseline・action-balanced・seat/root-stratifiedの同一条件比較を行い、固定heldout評価で採用候補を決める。teacher-forcingで改善してもCABTが改善しない場合だけ、student rollout stateを使うDAggerへ進む。

**Tech Stack:** Python、PyTorch、既存のV4 strict checkpoint loader、sealed recurrent materializer、既存heldout runner、pytest。

## Global Constraints

- 既存wave4 checkpoint・selection manifest・heldout protocolのSHAを変更しない。
- actor-visible情報だけを使用し、opponent IDや非公開情報をモデル入力へ追加しない。
- recurrent sequenceのepisode/component境界を壊さず、個別rowの無作為oversamplingは行わない。
- `reach_mass`、record単位正規化、forced domain size 1除外の診断契約を維持する。
- V2 baseline、6 opponent、両seat、base seed、max stepsを比較arm間で固定する。
- faultsは敗戦扱いにせず、比較無効として記録する。
- commit、push、Kaggle提出はユーザーが明示するまで行わない。

---

### Task 1: wave4 action-type offline metrics

**Files:**
- Reuse: `scripts/measure_v4_imitation_metrics.py`
- Reuse: `runs/meta-specialist-v4-archaludon-longrun-wave4/archaludon-training.json`
- Create: `runs/meta-specialist-v4-archaludon-longrun-wave4/archaludon-imitation-metrics.json`
- Test: `tests/meta_specialist/test_v4_imitation_metrics.py`

**Interfaces:**
- Consumes the wave4 training report and sealed Archaludon selection manifest.
- Produces per-seed carry/reset metrics for complete action, root/later prefix, forced-row count, STOP, and every action type.

- [ ] Run the strict batch command with seeds 0 and 1 on `cuda:0`.
- [ ] Verify `selected_sequence_sha256`, checkpoint file SHA, tensor SHA, model dimensions, and materializer arguments match the wave4 report.
- [ ] Extract forced-excluded top-1, root/later top-1, END/EVOLVE/RETREAT/ATTACK top-1, STOP NLL, and teacher-prefix survival.
- [ ] Record the results in an evidence markdown file without changing promotion authority.
- [ ] Stop before Task 2 if strict loading or sealed digest validation fails.

### Task 2: heldout opponent × seat × action trace

**Files:**
- Modify: `scripts/measure_v4_checkpoint_strength.py`
- Create: `tests/meta_specialist/test_v4_heldout_action_trace.py`
- Create: `docs/evidence/v4-weak-matchup-action-trace-20260810.md`

**Interfaces:**
- Extends the existing V4 heldout runner with an opt-in trace output.
- Every completed game records opponent id, seat, turn, prefix depth, selected action type, legal-domain size, candidate count, model latency, and fault status.
- The default evaluator output and win/loss aggregation remain backward compatible.

- [ ] Add a failing test proving trace mode emits one bounded row per decision and never includes hidden opponent information.
- [ ] Run the test to confirm the new trace option is absent.
- [ ] Implement bounded JSONL trace writing under the existing content-addressed output root.
- [ ] Add per-opponent, per-seat, and per-action aggregates; do not store full hidden state or raw private objects.
- [ ] Run the trace on wave4 seed0/seed1 for the fixed six-opponent 96-game protocol.
- [ ] Compare weak (`skarin`, `sue124`, `ozawa`) and strong matchups for root decisions, action types, legal-domain size, and timeout/latency.

### Task 3: controlled small comparison

**Files:**
- Modify: `src/mage_ptcg/meta_specialist/recurrent_bc_v4.py` only after Task 1 confirms an action imbalance.
- Modify: `src/mage_ptcg/meta_specialist/run_meta_specialist_v4_bc.py` only for explicit sampler configuration.
- Create: `tests/meta_specialist/test_v4_balanced_objective.py`
- Create: `docs/evidence/v4-balanced-action-seat-ablation-20260810.md`

**Interfaces:**
- Baseline arm uses the current objective and sequence sampler.
- Action-balanced arm applies bounded type weights to eligible rows while retaining `reach_mass` and record normalization.
- Seat/root-stratified arm selects complete episodes with deterministic minimum coverage for seat0/seat1 and root action types.

- [ ] Write RED tests for reach-weight preservation, sequence integrity, bounded type weights, and deterministic seed reproducibility.
- [ ] Run the tests and verify they fail because the explicit balanced configuration is absent.
- [ ] Implement only the smallest configured weighting/sampling hooks required by the tests.
- [ ] Run two seeds, one fixed training/validation subset, one epoch first; then expand to three epochs only if both seeds improve validation.
- [ ] Reject any arm with nonfinite loss, missing coverage, validation regression, or checkpoint reload failure.

### Task 4: same-protocol heldout reevaluation

**Files:**
- Reuse: `scripts/measure_v4_checkpoint_strength.py`
- Reuse: `scripts/measure_v2_checkpoint_strength_fixed.py`
- Create: `docs/evidence/v4-weak-matchup-action-ablation-evaluation-20260810.md`

**Interfaces:**
- Evaluates baseline and candidate checkpoints with identical deck, opponent fingerprints, seats, seeds, max steps, and game count.
- Produces overall, seat, opponent, fault, and Wilson interval summaries.

- [ ] Evaluate every surviving arm on the fixed 6-opponent × 2-seat protocol.
- [ ] Require faults=0 and valid strict checkpoint provenance.
- [ ] Compare weak matchups separately instead of using only the aggregate win rate.
- [ ] Select an arm only when action metrics improve and no seat/opponent collapse occurs.

### Task 5: DAgger only if offline-to-CABT gap remains

**Files:**
- Reuse: `src/mage_ptcg/meta_specialist/dagger_dataset_v1.py`
- Create: `scripts/run_meta_specialist_v4_dagger.py`
- Create: `tests/meta_specialist/test_v4_dagger.py`
- Create: `docs/evidence/v4-dagger-improvement-20260810.md`

**Interfaces:**
- Rolls out the selected student checkpoint using actor-visible state only.
- Queries the sealed teacher on student-visited states, emphasizing END/EVOLVE/ATTACK disagreement states.
- Appends content-addressed records while preserving the original fixed validation set.

- [ ] Begin only if Task 4 shows offline action improvement without corresponding CABT improvement.
- [ ] Add RED tests for student-state provenance, fixed validation immutability, and teacher-label authority.
- [ ] Implement a bounded 1–2 round collection, not an open-ended rollout.
- [ ] Retrain and reevaluate under the same protocol.
