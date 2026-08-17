# V-trace Learning Health and Representation Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Restore correct on-policy behavior probabilities, independent reproducible exploration, verifiable V-trace health, and state/critic information needed for meaningful policy improvement, then run the repaired 1–4 pipeline end-to-end.

**Architecture:** Sampling keeps Gumbel-max decoding but records unperturbed model logits for behavior probability reconstruction. Collection derives a unique deterministic seed per game. Training emits explicit importance-ratio and continuation diagnostics and stratifies critic targets by opponent. The neural encoder is upgraded to retain zone/attachment/endpoint-Pokémon information and categorical state scalars; checkpoint schema is bumped so stale checkpoints fail closed.

**Tech Stack:** Python, PyTorch, pytest, existing CABT actor pool/trajectory/V-trace scripts.

## Global Constraints

- Preserve existing user changes in the worktree; do not reset or overwrite unrelated files.
- Behavior log-probability must be computed from the same base logits that define the categorical distribution, never from Gumbel-perturbed decode logits.
- Sampling must remain deterministic for a fixed base seed and game identity, but different games must not reuse the same RNG stream.
- All new serialized fields must be backward-incompatible by schema/version validation rather than silently guessed.
- Every implementation task starts with a failing test and ends with a focused passing test.

---

### Task 1: Correct sampled behavior probabilities

**Files:**
- Modify: `src/mage_ptcg/meta_specialist/actor_pool_v1.py`
- Test: `tests/meta_specialist/test_actor_pool_v1.py`

**Interfaces:**
- Add `_NeuralSamplingSessionV1.behavior_logits()` returning the unperturbed `SpecialistStepLogitsV1` from the most recent query.
- Change `_reconstruct_prefix_steps_v1` to accept recorded `(step_input, decode_logits, behavior_logits)` triples while accepting the existing pair form for non-sampled callers.

- [ ] **Step 1: Write the failing test**

```python
def test_sampled_behavior_log_probability_uses_base_logits_not_gumbel_logits():
    # A fixed RNG makes decode logits differ from base logits.  The recorded
    # probability must still equal log-softmax(base)[selected].
    ...
```

- [ ] **Step 2: Run the focused test and verify it fails**

Run: `pytest tests/meta_specialist/test_actor_pool_v1.py -k "base_logits_not_gumbel" -q`
Expected: FAIL because reconstruction currently normalizes perturbed logits.

- [ ] **Step 3: Implement the minimal recording path**

Store the base result in `_NeuralSamplingSessionV1`, expose it through a method, have `_RecordingSessionV1` capture it immediately after `inner.logits`, and normalize the behavior logits during reconstruction. Keep decode return values Gumbel-perturbed so runtime sampling semantics are unchanged.

- [ ] **Step 4: Run focused actor tests**

Run: `pytest tests/meta_specialist/test_actor_pool_v1.py -q`
Expected: PASS, including reproducibility and existing greedy tests.

---

### Task 2: Per-game independent sampling seeds

**Files:**
- Modify: `src/mage_ptcg/meta_specialist/actor_pool_v1.py`
- Modify: `src/mage_ptcg/meta_specialist/collect_trajectories_v1.py`
- Test: `tests/meta_specialist/test_actor_pool_v1.py`
- Test: `tests/meta_specialist/test_collect_trajectories_cli.py`

**Interfaces:**
- Add `derive_game_sampling_seed_v1(base_seed, env_seed, archetype_id, opponent_kind, seat) -> int`.

- [ ] **Step 1: Write failing seed-independence tests**

Assert same inputs reproduce the seed, different `env_seed` values produce different seeds, and collection plans carry distinct per-job seeds while retaining the base seed in the run summary.

- [ ] **Step 2: Run tests and verify failure**

Run: `pytest tests/meta_specialist/test_actor_pool_v1.py tests/meta_specialist/test_collect_trajectories_cli.py -k "sampling_seed or collection_plan" -q`

- [ ] **Step 3: Derive and wire seeds**

Use a SHA-256-derived nonnegative 63-bit integer from the base seed and job identity fields. Pass the derived value into `ActorJobConfigV1` and include it in the actor job identity.

- [ ] **Step 4: Run focused tests**

Run the same command; expected PASS.

---

### Task 3: V-trace health invariants and opponent-stratified critic

**Files:**
- Modify: `src/mage_ptcg/meta_specialist/train_from_trajectories_v1.py`
- Modify: `src/mage_ptcg/meta_specialist/vtrace_bridge_v1.py`
- Modify: `src/mage_ptcg/meta_specialist/cli.py`
- Test: `tests/meta_specialist/test_train_from_trajectories.py`
- Test: `tests/meta_specialist/test_vtrace_bridge_v1.py`

**Interfaces:**
- Extend `LearningHealthV1` with `mean_importance_ratio`, `mean_continuation_c`, `mean_log_probability_shift`, and `opponent_target_means`.
- Add `assert_on_policy_health_v1(health, *, max_abs_log_shift=1e-5, min_mean_c=0.95)`.

- [ ] **Step 1: Write failing diagnostics tests**

Construct a one-step on-policy trajectory and assert ratio≈1/c≈1; construct a mismatched trajectory and assert the health check raises. Add two-opponent fixtures to assert critic targets are reported per opponent rather than only as a pooled mean.

- [ ] **Step 2: Run focused tests and verify failure**

Run: `pytest tests/meta_specialist/test_train_from_trajectories.py tests/meta_specialist/test_vtrace_bridge_v1.py -k "health or opponent" -q`

- [ ] **Step 3: Implement diagnostics and fail-closed CLI preflight**

Accumulate finite ratios and recursive c values from the existing V-trace tensors, persist them in the recipe/summary, add an optional `--assert-on-policy-health` switch, and compute opponent-stratified value residual means without changing the loss objective.

- [ ] **Step 4: Run focused tests**

Expected PASS.

---

### Task 4: Rich state and critic representation

**Files:**
- Modify: `src/mage_ptcg/meta_specialist/neural_model_v1.py`
- Modify: `src/mage_ptcg/meta_specialist/neural_policy_v1.py`
- Modify: `src/mage_ptcg/meta_specialist/actor_visible_features_v1.py` only if a serialized feature field is required
- Test: `tests/meta_specialist/test_neural_model_v1.py`

**Interfaces:**
- Bump model schema to `specialist-neural-model-v2`.
- Extend `SpecialistModelConfigV1` with the representation version and reject v1 checkpoints for v2 inference.

- [ ] **Step 1: Write failing representation tests**

Assert that changing only zone, energy-type composition, attached tool/pre-evolution IDs, or nested endpoint Pokémon changes the corresponding encoded vector; assert categorical state scalar `selection_type` is not log-transformed.

- [ ] **Step 2: Run focused tests and verify failure**

Run: `pytest tests/meta_specialist/test_neural_model_v1.py -k "zone or energy or endpoint or scalar" -q`

- [ ] **Step 3: Implement the v2 encoder**

Add zone embedding and attachment-bag projections to Pokémon features, append nested Pokémon encoding/presence to endpoint features, preserve active/bench identity with pooled mean plus count, and apply the existing feature-layer scalar semantics (continuous fields log-scaled, categorical fields embedded/raw). Keep candidate batching equivalent to sequential encoding.

- [ ] **Step 4: Update checkpoint loading and run focused tests**

Reject stale v1 checkpoints with a clear migration error; run all neural model tests and the checkpoint loader tests.

---

### Task 5: Execute repaired pipeline 1–4 and record evidence

**Files:**
- Create: `docs/evidence/vtrace-learning-health-20260808.md`
- Use: existing `scripts/collect_meta_specialist_trajectories.py`, `scripts/train_meta_specialist.py`/CLI, and evaluation scripts under `scripts/`

- [ ] **Step 1:** Run the complete focused/unit test suite.
- [ ] **Step 2:** Run a deterministic smoke collection for all four lanes and verify per-game seeds, zero step-0 probability mismatch, and mean continuation c near 1.
- [ ] **Step 3:** Run one repaired BC initialization and one V-trace training round for all four lanes with the documented t3 hyperparameters.
- [ ] **Step 4:** Run the held-out evaluation using the same matchup schedule and report per-lane deltas, confidence intervals, critic residuals, and all health gates.
- [ ] **Step 5:** Write the evidence report with exact commands, artifact paths, pass/fail status, and any remaining blocker; do not claim improvement if the statistical gate is not met.

