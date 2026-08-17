# CG deck-adaptive renderer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 公式カードCSVから生成したdeckごとに、P1固定規則と独立した公開状態policyを生成し、fault-free source poolをP1 CEMへ渡せる状態にする。

**Architecture:** レンダラーはdeck IDとbounded configからself-contained `main.py`を作り、既存の公式`cg/` runtimeだけをコピーする。generatorは既存self-owned deck generatorとbatch stagingを再利用し、candidate package rootとopponent promoted rootを分離する。重いCABTはmain coordinatorだけが起動し、BestKnown/Champion/production/submissionは触らない。

**Tech Stack:** Python 3、`cg.api`、公式カードCSV、既存`self_owned_cg_package_v1`／`self_owned_cg_meta_source_v1`、pytest、既存CABT evaluator。

## Global Constraints

- hidden opponent zones、native action labels、teacher labels、search APIは使用しない。
- 生成物はresearch-only、authorityは`training_allowed=false`、`promotion_allowed=false`、`submission_allowed=false`。
- 同じファイルの同時編集、commit、push、Champion変更、Kaggle提出は禁止。
- candidate席は`packages/`、opponent席はpromoted source rootとし、`cg/`欠落はfail-closedする。
- smokeは4 games/seat、独立評価はraw same-deck controlとpairedにする。

---

### Task 1: Renderer contract and tests

**Files:**
- Create: `src/mage_ptcg/meta_specialist/cg_deck_adaptive_renderer_v1.py`
- Test: `tests/meta_specialist/test_cg_deck_adaptive_renderer_v1.py`

**Interfaces:**
- `DeckAdaptiveConfig.from_mapping(values) -> DeckAdaptiveConfig`
- `render_deck_adaptive_source(config, deck_ids, candidate_id) -> str`
- `materialize_deck_adaptive_package(source_runtime_package, deck_package, output_package, config, candidate_id) -> dict[str, object]`

- [ ] Write tests for config bounds/canonical SHA, no P1 policy marker, 60-card `ROOT_DECK`, public-only helper source, and package manifest verification.
- [ ] Run `pytest -q tests/meta_specialist/test_cg_deck_adaptive_renderer_v1.py` and observe the missing-module failure.
- [ ] Implement the bounded config, self-contained renderer, package copier, canonical manifest, and deterministic fallback.
- [ ] Re-run the focused tests and `python -m py_compile` on the renderer.

### Task 2: Generator and fresh source plan

**Files:**
- Create: `scripts/generate_self_owned_cg_deck_adaptive_meta_v1.py`
- Create: `configs/meta_specialist/self_owned_cg_deck_adaptive_family_v1.json`
- Test: `tests/meta_specialist/test_generate_self_owned_cg_deck_adaptive_meta_v1.py`

**Interfaces:**
- `load_deck_adaptive_plan_v1(path) -> Mapping[str, object]`
- `run_generation_v1(plan, output, runtime_package) -> dict[str, object]`

- [ ] Write tests for duplicate recipe/config rejection, distinct policy/deck identities, and explicit `--execute` gating.
- [ ] Run the focused generator tests to capture the failing contract.
- [ ] Implement generation by calling `generate_self_owned_deck_v1`, rendering each policy variant, and staging with `materialize_self_owned_cg_meta_batch_v1` using a deck-adaptive source kind.
- [ ] Add six fresh recipes/variants with a new seed namespace and public canonical collision roots.
- [ ] Re-run tests and generate a dry-run plan without touching existing pools.

### Task 3: Runtime smoke and promotion gate

**Files:**
- Modify: `scripts/run_self_owned_cg_independent_source_smoke_v1.py` only if an explicit candidate-root option is missing
- Test: `tests/test_run_self_owned_cg_deck_adaptive_source_smoke_v1.py`
- Create: `runs/cg-self-owned-deck-adaptive-v1-20260816/` (generated artifact)

- [ ] Generate the new staged batch with a fresh seed namespace.
- [ ] Run package static legality/manifest checks before CABT.
- [ ] Run 4 games per seat for every candidate package against the existing local source pool; require all rows `DONE` and fault0.
- [ ] Promote only the fault-free batch into a new no-clobber root and verify `fresh_meta.json` with `build_fresh_meta_batch_v1`.
- [ ] If any runtime fault occurs, keep the batch staged and record the exact fault; do not retry the same seed.

### Task 4: Independent paired screen and CEM handoff

**Files:**
- Create: `docs/evidence/cg-self-owned-deck-adaptive-v1-20260816.md`
- Modify: `docs/status/current_status.md`, `docs/status/handoff.md`, `docs/status/chatgpt_context_pack_cg_bestknown_2026-08-15.md`

- [ ] Connect promoted source opponent root to the existing raw-control screen/CEM runner while passing candidate package root separately.
- [ ] Run bounded META_TRAIN screen with unused seed and verify candidate/control pair strata, runtime status, and fault0.
- [ ] Re-evaluate every screen elite on independent seed; compute mean, worst, seat gap, opponent×seat-safe, and retain incumbent on any failed gate.
- [ ] Read DEV/FINAL only after candidate selection is frozen; never use them for CEM updates.
- [ ] Record all artifact paths/SHA and explicit `BESTKNOWN_UNCHANGED` or promotion result in evidence/status/ChatGPT pack.
- [ ] Run focused tests, docs validator, `git diff --check`, and leave the worktree uncommitted.

