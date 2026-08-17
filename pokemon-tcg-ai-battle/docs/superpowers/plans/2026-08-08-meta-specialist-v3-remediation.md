# Meta Specialist v3 Plan-Conformance Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert the isolated Meta Specialist v3 prototypes into a valid, wired, recurrent collection-training-evaluation pipeline and remove every defect recorded in `docs/evidence/meta-specialist-v3-plan-conformance-audit-20260808.md` before any long training claim.

**Architecture:** Preserve the v1 production path while adding an explicit v3 path selected by CLI/config. One canonical `TrajectoryEpisodeV3` schema must connect fresh-process collection, relational recurrent BC, calibrated outcome critic, PPO/V-trace/AWR-CRR learners, opponent scheduling, DAgger, and provenance-complete evaluation. Gate tests and bounded real-data runs—not synthetic label arithmetic—decide readiness for long training.

**Tech Stack:** Python 3.12, PyTorch, pytest, CABT/Kaggle environment, JSON/JSONL run artifacts.

## Global Constraints

- Work only in `/home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle-worktrees/meta-specialist-canonical` on `feature/meta-specialist-canonical`.
- Preserve all pre-existing user modifications. Never reset, checkout, clean, delete, or overwrite unrelated work.
- Do not commit, push, create a remote branch, submit to Kaggle, or promote a checkpoint without explicit user authorization.
- Use strict TDD for every production behavior: add a failing behavioral test, run it and record the expected failure, implement minimally, then run the focused and affected suites.
- Hidden information, illegal actions, schema mismatches, provenance mismatches, and trajectory reuse fail closed.
- Do not treat transition count as independent sample count. Dataset partitions are connected components over episode and near-duplicate edges.
- Do not label synthetic smoke results as learner, evaluation, or promotion evidence.
- Preserve base behavior logits/log-probabilities separately from perturbed sampling values.
- Every run artifact records command, actual seeds, opponent/deck/policy identity, source manifest, faults, draws, seats, and metrics.
- For the repository test namespace conflict, run tests through an isolated temporary `tests` package with repo root and `src` in `PYTHONPATH`; do not rely on `tests/meta_specialist/__init__.py`.

---

### Task 1: Deterministic game identity, fault retry, and valid paired evaluation

**Files:**
- Modify: `src/mage_ptcg/meta_specialist/actor_pool_v1.py`
- Modify: `src/mage_ptcg/meta_specialist/collect_trajectories_v1.py`
- Modify: `src/mage_ptcg/meta_specialist/fault_diagnostics_v1.py`
- Modify: `src/mage_ptcg/meta_specialist/evaluation_protocol_v2.py`
- Modify: `scripts/run_meta_specialist_v3_eval.py`
- Test: `tests/meta_specialist/test_actor_pool_v1.py`
- Test: `tests/meta_specialist/test_collect_trajectories_cli.py`
- Test: `tests/meta_specialist/test_fault_diagnostics_v1.py`
- Test: `tests/meta_specialist/test_evaluation_protocol_v2.py`

**Interfaces:**
- Produce a canonical game-identity/seed bundle containing opponent ID, opponent policy version, opponent deck fingerprint, seat, environment seed, agent sampling seed, and retry index.
- Produce a fresh-process execution path that explicitly seeds Python, NumPy, Torch CPU/CUDA when available, environment construction, deck shuffle, rule-agent RNG, and agent sampling without mutating caller-global RNG state.
- Produce one automatic fresh-process retry for a fault using the same canonical game identity and a distinct recorded retry index; preserve both diagnostics and classify reproducible/transient/divergent results.
- Produce paired evaluation records that validate identical ledger identity and contain baseline/candidate outcome, draw, fault, seat, opponent family, record hash, state-hash sequence, and action sequence.

- [ ] Add behavioral tests proving identical canonical game identity reproduces the same initial observation, action sequence, terminal outcome, and record hash in two fresh processes; make the test fail on the current code.
- [ ] Add tests proving different games and retry indices cannot accidentally share mutable RNG/agent/opponent state; run them red.
- [ ] Add an actor-pool integration test in which the first attempt faults and exactly one same-game fresh retry runs, with both diagnostics persisted; run it red.
- [ ] Add evaluation tests rejecting ledger mismatches and correctly accounting for win/loss/draw/fault by seat and opponent family; run them red.
- [ ] Implement canonical local seeding and lifecycle reset at the lowest shared game-construction boundary, avoiding global caches or reused agent objects.
- [ ] Wire `capture_fault_v1` and retry classification into the actual collector/actor-pool failure path and include traceback, last valid observation/action, worker exit information, hashes, and provenance.
- [ ] Replace the binary-array-only evaluation entry point with provenance-bearing paired records and bootstrap only complete valid pairs; report faults/draws separately and Wilson intervals for each candidate rate.
- [ ] Run focused tests, then two bounded fresh-process repetitions on Alakazam and Archaludon using the same ledger. Save the first-divergence diagnostic if exact replay still fails; do not advance this task while a code-controlled divergence remains.
- [ ] Remove the ineffective unplanned `tests/meta_specialist/__init__.py` only if the isolated namespace suite proves it is unnecessary and no package consumer depends on it.

### Task 2: Correct relational representation, legal-candidate benchmark, and leakage-free splits

**Files:**
- Modify: `src/mage_ptcg/meta_specialist/representation_v3.py`
- Modify: `src/mage_ptcg/meta_specialist/neural_model_v3.py`
- Modify: `src/mage_ptcg/meta_specialist/representation_benchmark_v3.py`
- Modify: `src/mage_ptcg/meta_specialist/bc_trainer_v3.py`
- Reuse/modify as necessary: `src/mage_ptcg/meta_specialist/local_dataset_v2.py`
- Modify: `scripts/run_meta_specialist_v3_ablation.py`
- Test: `tests/meta_specialist/test_representation_v3.py`
- Test: `tests/meta_specialist/test_representation_benchmark_v3.py`
- Test: `tests/meta_specialist/test_bc_trainer_v3.py`

**Interfaces:**
- R3-A pools own active, own bench, opponent active, opponent bench, and other public entities separately.
- R3-B uses 192 dimensions, 4 heads, 2 pre-norm blocks, FFN 512, dropout 0.05, with same-owner, same-host, active, source/target, and public-evolution relations.
- Candidate logits are computed from legal action type, state-bound source/target embeddings, selection context, canonicalized arguments, numeric arguments, and recurrent/global context. Stable action ID is alignment/provenance only, never a semantic hash embedding.
- Split output is connected-component based over episode and near-duplicate edges and asserts zero episode and zero non-ubiquitous near-duplicate overlap.

- [ ] Add failing tests with duplicate copies of the same card in active and bench, proving source/target endpoint resolution uses owner+zone+locator and never first-card match.
- [ ] Add failing tests for all required R3-A pools and R3-B relation types, FFN size, action source/target binding after full state encoding, and stable-action permutation equivariance.
- [ ] Add failing multi-selection tests for canonical set order, selected-item masks, duplicate illegal exclusion, and order-sensitive selection-step behavior.
- [ ] Add failing connected-component split tests where one episode spans many near IDs and one near ID spans many episodes; assert zero leakage.
- [ ] Implement endpoint identity and relation tensors, the exact R3-A/R3-B structures, state-bound candidate encoding, and removal of stable-action-ID semantic embedding.
- [ ] Replace the simplified splitter with the existing `local_dataset_v2` connected-component/ubiquity algorithm or one behaviorally equivalent shared implementation.
- [ ] Replace `_R2NegativeControl` with the real current-v2 policy adapter. Train/evaluate full legal-candidate choice, not only `option_type`.
- [ ] Add top-1, top-3, rare-action recall, action-type NLL, p50/p95 latency, CPU preprocessing, and CUDA VRAM metrics; support three deterministic training seeds and validation early stopping.
- [ ] Run a bounded real-record benchmark with independent episode/component partitions. Mark Gate 1 passed only if relation tests pass and the selected R3 has no material supervised regression versus real R2 under the plan threshold.

### Task 3: Real teacher validation, weighted recurrent BC, calibrated critic, and sealed theta0

**Files:**
- Modify: `src/mage_ptcg/meta_specialist/teacher_revalidation_v3.py`
- Modify: `src/mage_ptcg/meta_specialist/bc_trainer_v3.py`
- Modify: `src/mage_ptcg/meta_specialist/critic_conditioning_v3.py`
- Modify: `src/mage_ptcg/meta_specialist/critic_warmup_v3.py`
- Modify: `src/mage_ptcg/meta_specialist/critic_v3.py`
- Modify: `src/mage_ptcg/meta_specialist/neural_model_v3.py`
- Modify: `scripts/run_meta_specialist_v3_teacher_manifest.py`
- Modify: `scripts/run_meta_specialist_v3_bc.py`
- Modify: `scripts/run_meta_specialist_v3_critic.py`
- Test: `tests/meta_specialist/test_teacher_revalidation_v3.py`
- Test: `tests/meta_specialist/test_bc_trainer_v3.py`
- Test: `tests/meta_specialist/test_critic_conditioning_v3.py`
- Test: `tests/meta_specialist/test_critic_warmup_v3.py`
- Test: `tests/meta_specialist/test_critic_v3.py`

**Interfaces:**
- Teacher records contain policy implementation/source/version, usage boundary, deck fingerprint, current-pool results, fault rate, confidence inputs, and a generated quality weight from the plan's 1.0/0.7/0.4/0.2/0.0 policy.
- Recurrent BC consumes ordered episode sequences with explicit episode starts, padding masks, and optional burn-in; GRU state persists within each episode and resets only at boundaries.
- Critic warm-up consumes real eventual outcomes and episode-balanced weights. Calibration reports overall, seat, opponent-family, and trajectory-position strata against a uniform baseline.
- Stable conditioning maps categories below 64 examples to unknown, permits dedicated category embeddings at 128 or more examples, and excludes game-seed identity.
- Sealed theta0 is a real checkpoint file plus canonical source/dataset/split/teacher/model/critic manifests and hashes.

- [ ] Add failing tests that reject teacher manifests missing real current-pool evidence, policy/deck provenance, usage boundary, or fault accounting.
- [ ] Add failing tests deriving every quality-weight tier from independent confidence/agreement/search/strength inputs; prove stored default `1.0` cannot bypass derivation.
- [ ] Add failing recurrent BC tests proving state persists across ordered decisions, resets between episodes, ignores padding, and validation has no connected-component overlap.
- [ ] Add failing critic tests for real final-outcome labeling, episode-balanced loss, calibration strata, category-count thresholds, and total absence of game-seed conditioning in the v3 production forward path.
- [ ] Implement strict teacher revalidation against the current validation pool, including the Archaludon lane; weak teachers are down-weighted or excluded by measured evidence.
- [ ] Implement quality-weight generation and sequence batching. Save best-by-independent-validation checkpoint rather than hashing an in-memory state only.
- [ ] Wire OutcomeCriticV3 to SpecialistModelV3 and actual training batches; train on real eventual outcomes and record calibration strata.
- [ ] Implement canonical theta0 manifest hashing only selected source/config/data files, excluding `__pycache__`, `.pyc`, transient runs, and unrelated repository files; include untracked source content in the manifest.
- [ ] Run bounded four-lane, three-seed BC/critic validation if compute permits; otherwise run a small real-data integration proof and record the exact remaining compute-only Gate work without claiming Gate 2/3.

### Task 4: End-to-end trajectory schema and actual recurrent PPO, V-trace, and AWR/CRR learners

**Files:**
- Modify: `src/mage_ptcg/meta_specialist/trajectory_schema_v3.py`
- Modify: `src/mage_ptcg/meta_specialist/trajectory_targets_v3.py`
- Modify: `src/mage_ptcg/meta_specialist/learner_common_v1.py`
- Modify: `src/mage_ptcg/meta_specialist/learner_ppo_recurrent_v1.py`
- Modify: `src/mage_ptcg/meta_specialist/learner_vtrace_online_v1.py`
- Modify: `src/mage_ptcg/meta_specialist/learner_awr_crr_v1.py`
- Modify: `src/mage_ptcg/meta_specialist/collect_trajectories_v1.py`
- Modify: `src/mage_ptcg/meta_specialist/train_from_trajectories_v1.py`
- Modify: `src/mage_ptcg/meta_specialist/cli.py`
- Modify: `scripts/run_meta_specialist_v3_rl.py`
- Test: `tests/meta_specialist/test_trajectory_schema_v3.py`
- Test: `tests/meta_specialist/test_trajectory_targets_v3.py`
- Test: `tests/meta_specialist/test_learners_v1.py`
- Add: `tests/meta_specialist/test_v3_collection_training_e2e.py`

**Interfaces:**
- Collector writes actual `TrajectoryEpisodeV3` files containing ordered decisions, legal-action IDs/masks, base behavior logits and log-probabilities, chosen action, rewards/outcome, episode boundaries, recurrent provenance, actor version, opponent/deck/seat/seed/fault identity.
- Learner batch separates chosen log-probabilities `[B,T]` from full legal distributions `[B,T,A]` and masks `[B,T,A]`.
- PPO implements recurrent burn-in, masked sequence loss, GAE, clipped policy/value objectives, entropy, adaptive exact legal-action KL, gradient clipping, and early stop.
- V-trace rejects future actor versions, consumes each trajectory once, performs actor+critic optimizer steps, records discard/reuse/lag, and computes effective-horizon diagnostics across start positions including d40 and terminal-to-opening influence.
- AWR/CRR is a real replay optimizer over the shared trajectory/model/critic schema, not only a weight function.

- [ ] Add failing schema and end-to-end tests where the actual collector writes a v3 episode and each learner consumes it without synthetic feature substitution.
- [ ] Add failing tensor-shape tests that distinguish batch/time/action axes, variable legal-action counts, masks, padding, hidden state, and burn-in.
- [ ] Add failing numerical PPO tests for chosen ratio, exact forward/reverse KL on masked full distributions, GAE/value loss, adaptive KL stop, and recurrent sequence gradients.
- [ ] Add failing V-trace tests for future-version rejection, exactly-once consumption after exceptions, optimizer parameter change, d40, mass-based 90% horizon, per-start medians, and position diagnostics.
- [ ] Add failing AWR/CRR tests proving a real optimizer update uses replay advantages and legal-candidate log-probabilities.
- [ ] Implement collector serialization and CLI/config routing for v3 while leaving v1 compatibility behavior unchanged.
- [ ] Implement the three actual learner loops over SpecialistModelV3/OutcomeCriticV3 with sequence batching and checkpoint save/resume.
- [ ] Replace synthetic learner-health generation with metrics emitted from real optimizer steps: action-type KL, policy drift, entropy, gradient norms, advantage episode variance/autocorrelation/position bins, throughput, and trajectory age/reuse.
- [ ] Run focused suites and a bounded collect→train→checkpoint→reload→evaluate integration for PPO, V-trace, and AWR/CRR from the same saved theta0.

### Task 5: Real opponent schedules, search-guided DAgger, evaluation tiers, and truthful artifacts

**Files:**
- Modify: `src/mage_ptcg/meta_specialist/opponent_schedule_v2.py`
- Modify: `src/mage_ptcg/meta_specialist/search_teacher_v1.py`
- Modify: `src/mage_ptcg/meta_specialist/dagger_dataset_v1.py`
- Modify: `src/mage_ptcg/meta_specialist/evaluation_protocol_v2.py`
- Modify: `src/mage_ptcg/meta_specialist/experiment_manifest_v1.py`
- Modify: `src/mage_ptcg/meta_specialist/promotion_gate_v1.py`
- Modify: `scripts/run_meta_specialist_v3_phase7_9_smoke.py`
- Modify: `scripts/run_meta_specialist_v3_eval.py`
- Modify: `scripts/build_meta_specialist_v3_report.py`
- Test: `tests/meta_specialist/test_opponent_schedule_v2.py`
- Test: `tests/meta_specialist/test_search_teacher_v1.py`
- Test: `tests/meta_specialist/test_dagger_dataset_v1.py`
- Test: `tests/meta_specialist/test_evaluation_protocol_v2.py`
- Test: `tests/meta_specialist/test_experiment_manifest_v1.py`
- Test: `tests/meta_specialist/test_promotion_gate_v1.py`

**Interfaces:**
- O0/O1/O2 are distinct, auditable distributions with disjoint train/validation/promotion pools; O2 is actually mirror-heavy and all probabilities/floors are persisted.
- Search teacher records public-belief/PIMC determinizations, query reason, search budget, candidate Q values/visit counts, temperature, soft target, and state provenance without hidden leakage.
- DAgger deduplicates connected near-duplicate states in addition to exact state+policy identity and preserves uncertainty/disagreement priority.
- Evaluation tiers enforce Smoke 8–32, Screening 128–256, Confirmation 512–1024, and Promotion 4096 games as configured by the source plan; only sealed promotion manifests can set a promotion gate.

- [ ] Add failing tests proving O0/O1/O2 differ, pools are disjoint, mirror mass in O2 exceeds the configured baseline, and schedules obey probability floors.
- [ ] Add failing PIMC/search contract tests for public-belief-only determinizations, reproducible budget accounting, Q/visit soft targets, and hidden-information rejection.
- [ ] Add failing DAgger tests where exact hashes differ but near-duplicate connectivity requires a single component representative.
- [ ] Add failing tier/manifest tests rejecting wrong game counts, unsealed pools, source manifests containing bytecode/transient files, unpaired identities, and any synthetic `promotion_gate=true`.
- [ ] Implement real schedule selection in the collector and training manifests, real query-state/search output plumbing, and connected near-duplicate DAgger storage.
- [ ] Replace the Phase 7–9 script's identical L1/L2/L3 calls with actual configured learner entry points and distinct O0/O1/O2 runs; remove hard-coded synthetic outcome arithmetic.
- [ ] Make report wording derive from integration/evidence status. Use `promotion_not_sealed_not_run` until a real pool is sealed and the required evaluation is executed.
- [ ] Run bounded real-data Phase 7–9 smoke only after Tasks 1–4 pass. The smoke validates wiring and artifact truthfulness, not performance or promotion.

### Task 6: Whole-pipeline verification and long-training readiness decision

**Files:**
- Modify: `docs/evidence/meta-specialist-v3-final-report.md`
- Create: `docs/evidence/meta-specialist-v3-remediation-verification-20260808.md`
- Modify generated manifests under: `runs/meta-specialist-v3/`

**Interfaces:**
- Produce one evidence report mapping every original Task/Gate and every audit finding to code, test, bounded run, status, and remaining compute-only work.
- Produce a readiness decision with explicit technical, data, optimization, and evaluation uncertainties; do not infer expected performance from unit tests.

- [ ] Run the full isolated-namespace meta-specialist suite and record exact pass/skip/warning counts and command.
- [ ] Run focused end-to-end collection/training/evaluation tests with real CABT records for all three learners from the same theta0.
- [ ] Re-run deterministic fresh-process paired games and report exact replay, fault, draw, seat, and opponent-family results.
- [ ] Audit call sites to prove v3 model, critic, trajectory schema, learners, fault retry, schedule, and DAgger are reachable from actual CLI production paths rather than only tests/scripts.
- [ ] Re-run bounded representation, critic, BC, learner, schedule, and DAgger experiments and clearly separate valid real evidence from synthetic unit fixtures.
- [ ] Correct final report labels, manifest status, source hashes, split claims, and all previously overstated completion language.
- [ ] State whether the pipeline is ready for long training. A positive readiness decision requires deterministic evaluation or a formally justified replacement, zero split leakage, saved theta0, real optimizer updates for all learners, provenance-complete artifacts, and no load-bearing test/review findings.

