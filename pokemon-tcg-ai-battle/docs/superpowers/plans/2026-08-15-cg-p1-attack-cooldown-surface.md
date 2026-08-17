# cg P1 attack-cooldown surface 実装計画

> **For agentic workers:** この計画は現セッションでinline実行する。heavy CABTの起動権はmain coordinatorだけが持ち、commit/push/Champion変更/Kaggle提出は行わない。

**Goal:** P1 `cg-lethal-target-v1` の公開情報だけを使い、Mega Braveの次ターン使用禁止を避けられる局面でAura Jabを優先するhash-bound research-only候補をscreenし、独立seedで再現した場合だけ候補として記録する。

**Architecture:** 既存P1 `main.py` は変更せず、source SHAを検証して末尾overlayを追加するv6 moduleを作る。既存のpaired candidate runnerへ薄いadapterを接続し、candidate/controlの同一strata、fault、seat、manifest SHAを既存契約へ委譲する。fresh-unused public metaが0件であるため、正差はresearch signalに留め、昇格条件を緩めない。

**Tech Stack:** Python 3.11、pytest、既存 `cg.api` runtime、`scripts.run_cg_p1_variant_screen_v1`、既存CABT evaluator。

## Global Constraints

- P1 base policy SHAは `1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9` に固定する。
- overlayはactor-visible state（legal option、visible opponent HP、own discard/bench）だけを読む。
- `Aura Jab` (attack 982) と `Mega Brave` (attack 983) がともに合法で、983がvisible activeをKOできず、discardにFighting Energyがあり、benchに未充電のFighting系targetがある場合だけ982へ `+12000`する。
- malformed/unsupported state、983がKO可能な状態、982が無い状態はP1 exact scoreへfail-closedする。
- local poolのfresh・unused・smoke-ready public metaは0件で、実験結果をBestKnown/Championへ昇格しない。
- heavy CABTはscreenが正差かつseat-safeのときだけ独立384へ進め、全runでauthority falseを維持する。

---

### Task 1: v6 overlay契約の失敗テスト

**Files:**
- Create: `tests/meta_specialist/test_cg_p1_policy_candidate_v6.py`
- Create: `tests/meta_specialist/test_run_cg_p1_policy_candidate_v6_screen_v1.py`

**Interfaces:**
- Produces the expected `VARIANT_IDS`, render markers, source compilation, unknown-ID failure, and CLI adapter contract for later implementation.

- [ ] **Step 1: Write the failing tests**

  Add assertions for the single candidate ID `cg-p1-aura-jab-cooldown-safe-v1`, markers `AURA_JAB`/`MEGA_BRAVE`/`discard`, and the v6 adapter's `--help` output and two-games budget validation.

- [ ] **Step 2: Run tests to verify RED**

  Run `TMPDIR=/tmp PYTHONPATH=src pytest -q tests/meta_specialist/test_cg_p1_policy_candidate_v6.py tests/meta_specialist/test_run_cg_p1_policy_candidate_v6_screen_v1.py`.

  Expected: collection/import failure because the v6 module and adapter do not exist.

### Task 2: hash-bound v6 package materializer

**Files:**
- Create: `src/mage_ptcg/meta_specialist/cg_p1_policy_candidate_v6.py`
- Test: `tests/meta_specialist/test_cg_p1_policy_candidate_v6.py`

**Interfaces:**
- Produces `VARIANT_IDS`, `render_p1_variant_source_v6(candidate_id)`, and `materialize_p1_variant_package_v6(...)` matching v4/v5 materializer metadata.

- [ ] **Step 1: Implement the minimal overlay**

  Append a wrapper around `_main_score` that identifies attack 982/983, checks visible opponent HP, own discard Fighting count, and a bench target with 0 energy whose ID is one of `RIOLU`, `MEGA_LUCARIO`, `MAKUHITA`, `HARIYAMA`; add the bonus only when `983 damage < visible HP` and attack 982 is legal. Keep the base score for every other option.

- [ ] **Step 2: Run the module tests**

  Run the Task 1 pytest command. Expected: PASS.

- [ ] **Step 3: Compile source and materializer**

  Run `python -m py_compile src/mage_ptcg/meta_specialist/cg_p1_policy_candidate_v6.py`.

### Task 3: v6 runner adapter

**Files:**
- Create: `scripts/run_cg_p1_policy_candidate_v6_screen_v1.py`
- Test: `tests/meta_specialist/test_run_cg_p1_policy_candidate_v6_screen_v1.py`

**Interfaces:**
- Produces the same `main(argv)` and `run_p1_variant_screen` surface as v4/v5 while binding the v6 IDs and materializer to `run_cg_p1_variant_screen_v1`.

- [ ] **Step 1: Implement the thin adapter**

  Import v6 symbols, assign `_runner.VARIANT_IDS` and `_runner.materialize_p1_variant_package_v1`, and delegate `main`/`run_p1_variant_screen` without changing evaluator behavior.

- [ ] **Step 2: Run adapter tests**

  Run `TMPDIR=/tmp PYTHONPATH=src pytest -q tests/meta_specialist/test_run_cg_p1_policy_candidate_v6_screen_v1.py`.

  Expected: PASS.

### Task 4: paired screen and conditional independent confirmation

**Files:**
- Create: `docs/evidence/cg-p1-attack-cooldown-surface-20260815.md`
- Modify: `docs/status/current_status.md`
- Modify: `docs/status/handoff.md`

**Interfaces:**
- Consumes the v6 runner and the existing `performance_first_broad_pool_v1` configuration.
- Produces immutable screen/confirmation artifacts under `runs/final-sprint-autonomous/cg-p1-attack-cooldown-surface-screen-20260815/` and a decision that explicitly records `reused_meta_train`.

- [ ] **Step 1: Run the bounded screen**

  Run the v6 adapter with `--config configs/meta_specialist/performance_first_broad_pool_v1.json`, `--games-per-opponent-seat 2`, `--base-seed 49910000`, `--workers 12`, `--worker-recycle-games 16`, and the dated output root. Do not pipe output through `tee`.

- [ ] **Step 2: Apply the screen gate**

  Read the finalized summary and manifest. Continue only if candidate delta is positive, candidate seat gap is at most 5%, all rows are DONE/fault0, and candidate/control pairing is exact. Otherwise record STOP without a blind retry.

- [ ] **Step 3: Run one independent confirmation if eligible**

  Use the existing file-backed confirmation runner with base seed `49960000`, candidate/control each 384 games, and the same broad24 IDs. Label the provenance `reused_meta_train`; never call it fresh-unused.

- [ ] **Step 4: Record evidence and status**

  Append identity, exact counts, SHA values, gate result, and remaining freshness limitation to the evidence/status/handoff files. Keep P1/root deck/BestKnown/Champion/production unchanged.

### Task 5: deterministic verification

**Files:**
- No new source files.

- [ ] **Step 1: Run focused suite**

  Run all v2–v6 candidate/runner tests plus `tests/meta_specialist/test_cg_p1_cem_v1.py` and `tests/meta_specialist/test_run_cg_p1_cem_v1.py`.

- [ ] **Step 2: Run static/document checks**

  Run `python -m py_compile` for v6 source/adapter/tests, `python scripts/docs/validate_docs.py`, and `git diff --check`.

- [ ] **Step 3: Confirm process and authority state**

  Confirm no heavy CABT process remains, no package/Champion mutation occurred, and `git status --short` contains only intentional research/docs changes plus pre-existing user changes.
