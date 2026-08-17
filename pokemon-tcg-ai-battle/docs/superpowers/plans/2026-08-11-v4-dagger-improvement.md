# V4 DAgger 実戦改善 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 現行V4 checkpointのlearner-stateをteacherで再ラベルし、既存BCと混合した短期screenで実戦性能の改善を検証する。

**Architecture:** `run_one_actor_game_v1` が返す検証済み `ActorTrajectoryTransitionV1` を入力に、teacher logitsを同じmodel/step inputへ再適用する独立のV4 DAgger変換層を追加する。変換層は既存 `RecurrentBCSequenceV4` を生成し、trainer/runtimeの意味を変更しない。新CLIは収集、再ラベル、混合学習、metrics出力を一つのsealed research artifactへまとめる。

**Tech Stack:** Python 3、PyTorch、既存 actor_pool/runtime、既存 `representation_v4`、既存 recurrent BC trainer、pytest、SHA-256 canonical JSON。

## Global Constraints

- `promotion_authority=false`、research-only artifactとして扱う。
- private/hidden state、serial locator、deck orderを保存しない。
- checkpoint、deck、opponent、engine、source closureのSHAをartifactへ記録する。
- 既存Wave6 artifactは上書きしない。
- train/validationはepisode/component単位で分離する。
- fault、non-DONE、invalid legal domainは学習データへ入れない。
- commit、push、Kaggle提出はユーザーが別途明示するまで行わない。

### Task 1: V4 DAgger record schema and teacher relabel helper

**Files:**
- Create: `src/mage_ptcg/meta_specialist/dagger_v4.py`
- Create: `tests/meta_specialist/test_dagger_v4.py`

**Interfaces:**
- `relabel_transition_v4(transition: ActorTrajectoryTransitionV1, *, teacher_factory: StepLogitPolicyFactory, policy_version: str, lane: str, episode_group: str, component_id: str, partition: str) -> RecurrentBCSequenceV4`
- `mix_dagger_sequences_v4(base: Sequence[RecurrentBCSequenceV4], dagger: Sequence[RecurrentBCSequenceV4], *, dagger_fraction: float, seed: int) -> tuple[RecurrentBCSequenceV4, ...]`
- `dagger_record_sha256_v4(sequence: RecurrentBCSequenceV4) -> str`

- [ ] **Step 1: Write failing tests** for softmax normalization, STOP alignment, empty domain rejection, private-field rejection, deterministic record hash, and train/validation component split.
- [ ] **Step 2: Run the focused tests** and confirm the new module/API is absent or fails with the intended validation error.
- [ ] **Step 3: Implement teacher relabeling** using the recorded `model_input` and `prefix_steps`; call the teacher session for every recorded prefix; append STOP when available; normalize logits with a stable log-sum-exp.
- [ ] **Step 4: Convert each relabeled prefix to `RecurrentBCStepV4`** using `representation_v4_from_step_input_v1`, SHA-based game component identity, and `research_only=True`.
- [ ] **Step 5: Implement deterministic episode-level mixing** with no row-level episode split and no base artifact mutation.
- [ ] **Step 6: Run `pytest -q tests/meta_specialist/test_dagger_v4.py`** and `git diff --check`.

### Task 2: Runtime collection and sealed DAgger screen CLI

**Files:**
- Create: `scripts/run_meta_specialist_v4_dagger_screen.py`
- Create: `tests/meta_specialist/test_run_meta_specialist_v4_dagger_screen.py`
- Modify: none in the existing trainer/model/runtime files.

**Interfaces:**
- CLI accepts `--checkpoint`, `--checkpoint-file-sha256`, `--checkpoint-tensor-state-sha256`, `--subject-deck-csv`, `--subject-archetype-id`, `--opponent-count`, `--games-per-seat`, `--base-seed`, `--output`, and `--device`.
- Output has `schema`, `promotion_authority`, `checkpoint`, `engine`, `games`, `faults`, `dagger_records`, `sequence_sha256`, and `coverage`.

- [ ] **Step 1: Write failing tests** for checkpoint identity binding, fixed opponent order, seed/seat coverage, fault exclusion, atomic output, and no private fields in serialized records.
- [ ] **Step 2: Run focused tests** to capture the RED result.
- [ ] **Step 3: Build fixed-six jobs** with `ActorJobConfigV1` and call `run_one_actor_game_v1`; reject non-completed games before relabeling.
- [ ] **Step 4: Relabel returned transitions** with the Rule teacher factory and create deterministic train/validation component partitions.
- [ ] **Step 5: Atomically write JSON provenance and DAgger sequence sidecar** without overwriting existing output.
- [ ] **Step 6: Run focused tests, py_compile, and docs validation**.

### Task 3: Mixed V4 training adapter and metrics gate

**Files:**
- Create: `scripts/run_meta_specialist_v4_dagger_bc.py`
- Create: `tests/meta_specialist/test_run_meta_specialist_v4_dagger_bc.py`

**Interfaces:**
- CLI consumes the DAgger screen artifact and the same sealed base selection manifest as the current V4 runner.
- It writes per-seed checkpoint descriptors, training history, imitation metrics, action-type metrics, and `promotion_authority=false`.

- [ ] **Step 1: Write failing tests** for base/DAgger component overlap, mixed sequence fraction, checkpoint strict reload, and preservation of the base selected-sequence hash.
- [ ] **Step 2: Run focused tests** and confirm the adapter is missing.
- [ ] **Step 3: Materialize the base subset once**, append DAgger episodes at the configured fraction, and call `train_recurrent_bc_v4` with the existing objective.
- [ ] **Step 4: Save initial/best/last reports** including action-type and carry/reset metrics; reject finite/identity drift.
- [ ] **Step 5: Run the focused and existing V4 regression suites**.

### Task 4: Short performance gate and next-run decision

**Files:**
- Create: `docs/evidence/v4-dagger-screen-20260811.md`
- Modify: `docs/status/current_status.md`
- Modify: `docs/status/handoff.md`

- [ ] **Step 1: Run two-seed DAgger screen** on the host GPU with fixed-six and both seats.
- [ ] **Step 2: Compare against Wave6** using complete-action top-1, END/EVOLVE/ATTACK, carry/reset, fault rate, seat, and opponent breakdown.
- [ ] **Step 3: Run 96-game confirmation only if the 24-game screen has no fault and no seat collapse.**
- [ ] **Step 4: Promote to longer training only if pooled performance exceeds Wave6 and at least four of six opponents are non-regressive.**
- [ ] **Step 5: Record the decision and leave failed arms intact for audit.**
