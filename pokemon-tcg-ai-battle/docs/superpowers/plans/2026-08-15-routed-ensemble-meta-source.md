# Actor-visible Routed Ensemble Meta Source Implementation Plan

> **For agentic workers:** This plan is executed inline in the current task. No commit, push, Champion mutation, or Kaggle submission is authorized.

**Goal:** Generate fresh, research-only opponent meta sources by deterministically routing between two smoke-qualified parent policies using actor-visible public state, then bind them to a hash-verified CEM-ready split.

**Architecture:** A sealed candidate contains two copied parent payloads and a repository-owned wrapper. The wrapper reads only turn, visible active/bench card IDs, stadium, and selection context; a fixed routing recipe chooses exactly one parent agent per observation. The generator emits `smoke_ok=false` rows, freshness evidence, parent/deck identities, and a split; a separate bounded smoke promotion and split rebind are required before CEM.

**Tech Stack:** Python 3, JSON/SHA-256 manifests, existing `kaggle_kernel_meta_v1` static scan/wrapper conventions, `cg_weekend_split_v1`, `run_historical_meta_smoke_v1.py`, and `run_cg_p1_cem_v1.py`.

## Global Constraints

- Use only `local_eval_only` parent sources; do not copy private information, expert/action labels, future RNG, or network behavior.
- Validate exact 60 cards, official card IDs, ACE SPEC exactly one, regular files, static payload safety, and bounded runtime before CEM.
- The generated pool starts with `smoke_ok=false`; promotion is a separate no-clobber artifact.
- Keep P1 policy SHA `1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9` and root deck SHA `2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19` unchanged.
- Do not modify `opponents/`, Champion, production, submission artifacts, or Git history.

### Task 1: Define failing tests for routed candidate sealing

**Files:**
- Create: `tests/test_routed_ensemble_meta_v1.py`
- Test fixtures: temporary parent roots with payload, deck, `SOURCE.md`, and one-row pool manifest.

**Interfaces:**
- Test `seal_routed_ensemble_meta_v1(...)` returns a sealed report and emits unique candidate policy/deck pairs.
- Test `build_routed_ensemble_split_v1(...)` refuses an unpromoted pool and binds a smoke-promoted pool.
- Test generated wrapper exposes `agent` and does not read opponent hand/prize/deck fields while routing.

- [x] **Step 1: Write tests for `smoke_ok=false`, static identity, routing metadata, and split rebind.**
- [x] **Step 2: Run `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONPATH=.:src pytest -q tests/test_routed_ensemble_meta_v1.py` and confirm import/implementation failures.**

### Task 2: Implement the routed ensemble generator

**Files:**
- Create: `src/mage_ptcg/opponent_ingest/routed_ensemble_meta_v1.py`
- Create: `scripts/generate_routed_ensemble_meta_v1.py`
- Create: `scripts/rebind_routed_ensemble_split_v1.py`

**Interfaces:**
- `seal_routed_ensemble_meta_v1(parent_roots, specifications, output_root, source_epoch, seed_namespace, p1_package, current_pool_manifest=None, scan_roots=()) -> dict[str, object]`.
- `build_routed_ensemble_split_v1(output_root, p1_package) -> dict[str, object]`.
- A specification contains `policy_a`, `policy_b`, `deck_parent`, and one of the fixed routing recipes `PUBLIC_HASH_V1`, `TURN_PARITY_V1`, `OPPONENT_BOARD_HASH_V1`, `CONTEXT_TURN_HASH_V1`, `OPPONENT_DAMAGE_SWITCH_V1`, `OPPONENT_BOARD_SIZE_SWITCH_V1`, or `CONTEXT_THREAT_SWITCH_V1`.

- [x] **Step 1: Add strict parent loader and static/legality checks.**
- [x] **Step 2: Add isolated dual-payload wrapper and public-state-only route functions.**
- [x] **Step 3: Emit no-clobber candidate directories, evidence, pool/fresh/meta/split manifests, and authority-false metadata.**
- [x] **Step 4: Run the focused tests and `py_compile`.**

### Task 3: Seal a real v4/v7/v9 source epoch and smoke it

**Files:**
- Generated only: `runs/cg-routed-ensemble-meta-20260815-a/`, promotion root, smoke root.
- Existing helper: `scripts/promote_historical_meta_smoke_v1.py`.

- [x] **Step 1: Promote v4 Koushikrudra from its completed two-game smoke ledger; keep v7 Raunak and v9 Prvsiyan as separate parent roots.**
- [x] **Step 2: Generate at least four routed candidates with distinct pair/route identities and verify the pool starts `smoke_ok=false`.**
- [x] **Step 3: Run bounded two-seat smoke for all generated candidates as runtime-only exposure, record that no win/loss is used for selection, and promote only fault-free rows.**
- [x] **Step 4: Rebind the split and verify `build_fresh_meta_batch_v1` accepts only the promoted, hash-bound pool.**

### Task 4: Run bounded P1-fixed CEM and fresh validation

**Files:**
- Generated only: `runs/cg-routed-ensemble-cem-20260815-a/` and validation artifacts.
- Evidence: `docs/evidence/cg-routed-ensemble-meta-cem-20260815.md`.

- [x] **Step 1: Run population 8, elite 2, one or two generations, independent re-evaluation at least two blocks, positive/risk-aware gates, and P1 control.**
- [x] **Step 2: Keep DEV/FINAL out of selection; read fresh DEV only after candidate selection and FINAL only for confirmation.**
- [x] **Step 3: No candidate met fault 0 plus independent positive lower-tail, seat gap ≤5%, and opponent×seat-safe; retain P1 and do not spend FINAL performance exposure.**
- [x] **Step 4: Update current status, handoff, and ChatGPT context pack with exact artifact paths and hashes.**

### Task 5: Repair and re-run semantic routing smoke

- [x] **Step 1: Reproduce the `list + tuple` failure on empty public benches and trace it to wrapper normalization.**
- [x] **Step 2: Normalize active/bench containers to tuples and add an empty-bench regression test.**
- [x] **Step 3: Seal a corrected `c-fix` epoch, run 8-game two-seat smoke, promote fault-free rows, and rebind the split.**
- [x] **Step 4: Run one P1-fixed CEM generation with independent re-evaluation enabled; retain P1 when no valid screen candidate exists.**
- [x] **Step 5: Acquire or generate a genuinely new, lower-correlation parent source before the next heavy campaign.** Same-canonical-deck Prvsiyan Alakazam v10／control v11 routed source was generated and independently confirmed; see `docs/evidence/cg-adversarial-route-meta-source-20260815.md`. The source is runtime-safe and a strong P1 challenge, but its first policy CEM produced no valid seat-safe candidate, so the BestKnown loop remains gated.

## Verification commands

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONPATH=.:src pytest -q tests/test_routed_ensemble_meta_v1.py
PYTHONPATH=.:src .venv/bin/python -m py_compile src/mage_ptcg/opponent_ingest/routed_ensemble_meta_v1.py scripts/generate_routed_ensemble_meta_v1.py scripts/rebind_routed_ensemble_split_v1.py
PYTHONPATH=.:src .venv/bin/python scripts/docs/validate_docs.py
git diff --check
```
