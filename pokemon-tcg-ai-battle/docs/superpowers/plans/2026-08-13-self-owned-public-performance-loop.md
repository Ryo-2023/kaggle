# Self-Owned Public Performance Loop Implementation Plan

> **For agentic workers:** 実装は研究専用の新規ファイルへ限定し、既存 production `main.py`、`agents/rule_agent.py`、既存 evaluator、既存性能 artifact は変更しない。

**Goal:** self-owned Rule v0 を固定し、公開される合法 action と結果だけを使う bounded candidate を同一 common24 protocol で実測し、96→384 の機械的 gate を通す。

**Architecture:** 既存 native opponent pool と evaluator を local evaluation の control として再利用し、新規 bridge が candidate factory と provenance を束ねる。候補は Rule v0 を最初に呼び、MAIN の option type bias だけを有限範囲で適用し、未知・不正・非対応 selection は exact baseline に戻す。META_DEV/META_FINAL、private state、native behavior label は入力にしない。

**Tech Stack:** Python 3、既存 `scripts.test_sim.run_match`、`scripts.parallel_cabt_evaluator_v1`、`opponent_pool_v1`、pytest、canonical JSON/SHA-256。

## Global Constraints

- `NATIVE_BEHAVIOR_PERMISSION_BLOCKED` を維持し、local-eval-only native asset を teacher/behavior source として使わない。
- candidate、manifest、ledger の authority flags はすべて `false`、`research_only=true` とする。
- common24、両seat、同一 seed schedule、同一 evaluator、同一 max_steps を baseline と candidate で共有する。
- 96局は screen、明確な paired positive・fault0・seat非崩壊の場合だけ384へ進む。
- 384で native/parent 比おおむね+3pt未満、または fault/seat collapse があれば 768/1536/longrun を起動しない。
- deck race は別 run root に置き、policy candidate と混ぜず、既存 artifact を上書きしない。

---

### Task 1: Research-only candidate factory and closed manifest

**Files:**
- Create: `src/mage_ptcg/meta_specialist/self_owned_public_candidate_v1.py`
- Create: `scripts/run_self_owned_public_candidate_screen_v1.py`
- Test: `tests/meta_specialist/test_self_owned_public_candidate_v1.py`

**Interfaces:**
- Consumes: root Rule v0 policy/deck SHA, `performance_first_broad_pool_v1.json`, `opponents/pool_manifest.json`, evaluator SHA.
- Produces: `build_action_overlay_agent_v1(deck, seed, config) -> callable`, `build_candidate_manifest_v1(...) -> dict`, `build_common24_games_v1(...) -> tuple[EvaluationGameV1, ...]`.

- [ ] **Step 1: Write failing tests** for bounded action-type config, exact baseline fallback on malformed/unknown/multi-select input, legal index/count validation, manifest authority false, and candidate identity hash.
- [ ] **Step 2: Run the focused tests** and confirm the missing module/API failure.
- [ ] **Step 3: Implement the minimum adapter** around `make_rule_agent`; only public option `type` and the existing Rule v0 score order are read. Candidate deltas must be finite and bounded; no target/private fields are consumed.
- [ ] **Step 4: Implement common24 game construction** using `EvaluationGameV1`, `resolve_opponent_v1`, and `build_opponent_agent_factory_v1`; bind root policy/deck/pool/config/evaluator SHA, candidate config SHA, seed, seat, and block identity.
- [ ] **Step 5: Run focused tests, `py_compile`, and `git diff --check`**; do not start CABT until all pass.

### Task 2: Real 96-game screen

**Files:**
- Create: `runs/final-sprint-autonomous/self-owned-public-candidate-screen-v1/` (runtime artifacts only)
- Create: `docs/evidence/autonomous-self-owned-public-candidate-screen-v1-20260813.md`
- Modify: `docs/status/current_status.md` (append-only result)
- Modify: `docs/status/handoff.md` (append-only result)

**Interfaces:**
- Consumes: Task 1 bridge, fixed common24 IDs, base seed, candidate configs.
- Produces: baseline and candidate manifests, evaluator summaries, paired outcome matrix, override/support/fallback coverage, evidence SHA.

- [ ] **Step 1: Run baseline and at most three bounded candidates** with `workers=1` first to avoid the previously observed transient worker/import race.
- [ ] **Step 2: Verify every arm** has requested game count, unique game IDs, DONE status, fault0, draw/fault denominator consistency, both seats, and identical `(opponent, seat, repetition, seed)` strata.
- [ ] **Step 3: Recompute paired loss→win and win→loss counts** from raw ledgers; do not use only aggregate summary fields.
- [ ] **Step 4: Classify** as `SCREEN_ONLY`, `SCREEN_INVALID`, `POSITIVE_CONTINUE_384`, or `NOT_PROMOTABLE`; preserve all authority false.
- [ ] **Step 5: Persist evidence and append current status/handoff** with exact paths and SHA values.

### Task 3: Conditional 384 confirmation

**Files:**
- Create: `runs/final-sprint-autonomous/self-owned-public-candidate-384-v1/`
- Create: `docs/evidence/autonomous-self-owned-public-candidate-384-v1-20260813.md`

**Interfaces:**
- Consumes: only a Task 2 candidate classified `POSITIVE_CONTINUE_384` with its exact config and seed lineage.
- Produces: seed-disjoint 384-arm summaries, seat/opponent support, regression count, `LONGRUN_READY_CANDIDATE` or `NOT_PROMOTABLE`.

- [ ] **Step 1: Refuse to run** unless the selected 96 artifact is complete, fault0, and paired-positive under the documented gate.
- [ ] **Step 2: Run baseline/control and candidate on a fresh seed-disjoint block** with identical protocol.
- [ ] **Step 3: Recompute aggregate and per-seat deltas** and require both seat evidence and non-concentrated opponent support.
- [ ] **Step 4: Stop at 384** when the +3pt/seat/fault gate is not met; do not auto-start longrun.
- [ ] **Step 5: Update evidence and handoff** without changing champion or submission package.

### Task 4: Parallel deck race

**Files:**
- Create: `runs/final-sprint-autonomous/deck-race-next-v1/`
- Create: `docs/evidence/autonomous-deck-race-next-v1-20260813.md`

**Interfaces:**
- Consumes: existing legal deck mutation generator, fixed Tomato/Plamen policy identities, common24 protocol.
- Produces: parent/candidate/native-control arm summaries and candidate-only classification.

- [ ] **Step 1: Select only new, statically legal role-based one-card swaps** and bind deck multiset/core/known-card SHA.
- [ ] **Step 2: Run parent, candidate, and Tomato native control on the same 96 strata** in a new root.
- [ ] **Step 3: Advance only candidates with fault0 and a clear parent-relative positive signal to384**; otherwise retain as candidate-only.
- [ ] **Step 4: Record policy×deck interaction separately**; never treat native local-eval assets as submission-compatible behavior sources.

## Verification gate

Before claiming progress, run the closest focused tests, `python scripts/docs/validate_docs.py`, `python -m py_compile` for new Python files, and `git diff --check`. A performance result is valid only when raw ledger, summary, manifest, seed/seat strata, and evidence hashes agree.
