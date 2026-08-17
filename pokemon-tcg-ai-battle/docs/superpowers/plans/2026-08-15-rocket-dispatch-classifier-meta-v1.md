# Rocket Dispatch Classifier Meta v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 受理済みRocket policyの公開観測classifierだけをboundedに変種生成し、P1 CEMへfresh TRAIN metaとして安全に接続する。

**Architecture:** `rocket_dispatch_classifier_meta_v1.py` がsealed sourceの `_TIER_A_TO_GROUP` AST辞書を検証し、指定キーのfamily valueだけを置換して12 policyを生成する。CLIは既存のfresh meta／historical split契約を使ってpool、fresh manifest、split、intake reportをno-clobber sealし、CABT runnerはTRAIN referencesのみを実行する。

**Tech Stack:** Python 3、AST、既存 `cg_bestknown_loop_v1` fresh-meta contract、`build_historical_meta_split_v1`、pytest、既存 CABT smoke／CEM runner。

## Global Constraints

- 生成物は `local_eval_only`、`authority=false`、`research_only=true` とし、提出・Champion変更・commit・pushを行わない。
- deck、観測境界、theta値、import、environment、fallback、dispatcher state logicは変更しない。
- source commit `de797c3646e935157618be3edea17615430ccfec` と current poolのpolicy identityをhash検証する。
- smoke／CEMはTRAIN 8件だけを使い、DEV／FINALを未使用holdoutとして保持する。
- independent lower-tail positive、seat gap ≤5%、opponent×seat gap ≤5%を満たさないcandidateは昇格しない。

---

### Task 1: Transform contract tests

**Files:**
- Create: `tests/test_rocket_dispatch_classifier_meta_v1.py`

**Interfaces:**
- Consumes: sealed Rocket `main.py` bytes and recipe names.
- Produces: tests for `_transform_dispatch_classifier`, recipe registry, and sealing contract.

- [ ] **Step 1: Write failing tests**

  Test the exact 13-key map, each recipe's changed keys, deterministic output, AST-only difference, unknown/no-op rejection, malformed dictionary rejection, and all generated references being `local_eval_only` with TRAIN 8／DEV 2／FINAL 2.

- [ ] **Step 2: Run the focused test and verify failure**

  Run `TMPDIR=/tmp PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONPATH=.:src pytest -q tests/test_rocket_dispatch_classifier_meta_v1.py`.
  Expected: import failure because the generator module does not exist yet.

### Task 2: Bounded classifier transformer and sealing API

**Files:**
- Create: `src/mage_ptcg/opponent_ingest/rocket_dispatch_classifier_meta_v1.py`

**Interfaces:**
- Consumes: `base_source_root: Path`, `variant: str`, current pool manifest, output root, split config.
- Produces: `_transform_dispatch_classifier(source: bytes, variant: str) -> tuple[bytes, str]` and `seal_rocket_dispatch_classifier_meta_v1(...) -> Mapping[str, object]`.

- [ ] **Step 1: Parse and validate the exact AST dictionary**

  Locate exactly one assignment to `_TIER_A_TO_GROUP`, require all keys to be integer constants matching the allowed key set, require string values in `A01/A09/A07/A11`, and reject duplicate or missing keys.

- [ ] **Step 2: Apply only recipe value replacements**

  Replace each selected `key: "old_group"` token inside the AST source span exactly once. Reject unknown recipe, no-op, old-value mismatch, replacement count mismatch, or any source change outside that dictionary span.

- [ ] **Step 3: Seal hash-bound artifacts**

  Reuse existing base-source, static scan, no-clobber, current-pool identity, `build_historical_meta_split_v1`, and `build_fresh_meta_batch_v1` conventions. Write 12 derived policy directories, pool manifest, `fresh_meta.json`, `cg_historical_split.json`, and `intake_report.json` with source／recipe／policy／deck identities and false authority.

- [ ] **Step 4: Run the focused tests**

  Run `TMPDIR=/tmp PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONPATH=.:src pytest -q tests/test_rocket_dispatch_classifier_meta_v1.py`.
  Expected: PASS.

### Task 3: CLI, config, and preflight

**Files:**
- Create: `scripts/generate_rocket_dispatch_classifier_meta_v1.py`
- Create: `configs/meta_specialist/cg_rocket_dispatch_classifier_v1.json`

**Interfaces:**
- Consumes: config, P1 source package, source root, current pool manifest, scan root.
- Produces: reproducible seal command and 12-reference split (8 TRAIN, 2 DEV, 2 FINAL).

- [ ] **Step 1: Add the config with explicit recipes and split IDs**

  Use source epoch `20260815-rocket-dispatch-classifier-v1`, seed namespace `cg-rocket-dispatch-classifier-v1`, and output root `runs/cg-rocket-dispatch-classifier-meta-20260815-c`.

- [ ] **Step 2: Implement CLI argument validation**

  Require regular source/package/pool paths, reject output reuse, and print only a compact report with artifact hashes.

- [ ] **Step 3: Seal and preflight**

  Run the CLI with `TMPDIR=/tmp PYTHONPATH=.:src`, compile all policies, load the pool, verify split identity, run the focused Rocket／derived／stratified suites, `python scripts/docs/validate_docs.py`, and `git diff --check`.

### Task 4: TRAIN smoke and P1 CEM

**Files:**
- Create: `runs/cg-rocket-dispatch-classifier-smoke-20260815-c/`
- Create: `runs/cg-rocket-dispatch-classifier-cem-20260815-c/`

**Interfaces:**
- Consumes: sealed pool/split and P1 package.
- Produces: smoke summary, CEM results, campaign manifest, and promotion decision.

- [ ] **Step 1: Run TRAIN-only smoke**

  Use explicit TRAIN IDs, both seats, fixed base seed `20260885`, one game per opponent/seat, and require DONE/fault0/illegal0/draw0.

- [ ] **Step 2: Run generation-0 CEM**

  Use P1 as control and source package, population16, elite2, two independent re-evaluations, campaign seed `20260886`, positive-delta and risk-aware gates, and `--all-train-refs` only.

- [ ] **Step 3: Apply the gate**

  Preserve P1 center unless a candidate has positive independent lower-tail, seat-safe, and opponent×seat-safe results. Do not start DEV／FINAL or deck phase for a failed gate.

### Task 5: Evidence and handoff update

**Files:**
- Create: `docs/evidence/cg-rocket-dispatch-classifier-meta-v1-20260815.md`
- Modify: `docs/status/current_status.md`
- Modify: `docs/status/handoff.md`
- Modify: `docs/status/chatgpt_context_pack_cg_bestknown_2026-08-15.md`

**Interfaces:**
- Consumes: seal hashes, smoke/CEM summaries, test and docs validation output.
- Produces: evidence-backed `SOURCE_GENERATION_PASS / PERFORMANCE_PROMOTION_*` verdict and next action.

- [ ] **Step 1: Record source-generation and performance verdict**

  Include source commit, recipe list, artifact hashes, exact commands, faults, scores, lower-tail, safety gates, and explicit statement that P1/BestKnown/Champion/submission are unchanged.

- [ ] **Step 2: Update status and ChatGPT context**

  Append one concise Japanese section to each canonical handoff/status document and link the evidence path.

- [ ] **Step 3: Run final verification**

  Run the focused suite, `python scripts/docs/validate_docs.py`, `git diff --check`, and active-process check. Leave the worktree uncommitted and report any unrelated pre-existing dirty files without touching them.
