# Rocket Theta Behavior Meta v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 受理済みRocket sourceから5つのtheta tableを対象にbounded behavior variantsを生成し、未使用TRAIN/DEV/FINAL splitとP1 CEMへhash-boundで接続する。

**Architecture:** `rocket_theta_behavior_meta_v2.py` はsource materializationとfreshness gateだけを担当し、既存のopponent pool loader・historical split builder・CEM runnerを再利用する。transformerはtheta dictionaryの値tokenだけを厳密に置換し、deck、dispatch、観測境界、runtimeを保持する。CLI/configで12 variantとsplitを宣言し、TRAIN-only smokeを明示IDで実行する。

**Tech Stack:** Python 3.12、既存 `mage_ptcg.opponent_ingest`、JSON manifest、`pytest`、`py_compile`、既存CABT runner。

## Global Constraints

- 新規候補は `local_eval_only`、`training_exposure=0`、全authority falseとする。
- 現行 `opponents/`、BestKnown、Champion、production、submission packageは変更しない。
- source commit、source/staged policy SHA、policy SHA、deck SHA、recipe、splitを全artifactへ記録する。
- DEV/FINALをTRAIN smokeまたはCEMへ暗黙に混入させない。
- 同一ファイルの同時編集を行わず、既存のdirty差分を上書きしない。
- `git commit`、`git push`、Kaggle提出はユーザーの明示許可なしに実行しない。

---

### Task 1: 失敗するtransformer契約テストを追加

**Files:**
- Create: `tests/test_rocket_theta_behavior_meta_v2.py`
- Reference: `src/mage_ptcg/opponent_ingest/derived_internal_meta_v1.py`
- Reference: `runs/cg-fresh-internal-meta-intake-20260815-f/internal_ozawa-rocket-rule_de797c3646e9/main.py`

**Interfaces:**
- Planned import: `from mage_ptcg.opponent_ingest.rocket_theta_behavior_meta_v2 import _transform_rocket_theta, RocketThetaBehaviorMetaError`
- Produces failing tests for deterministic transform, table/key validation, boolean preservation, unchanged dispatch, unknown recipe rejection, and composed recipe bounds.

- [ ] **Step 1: Write the failing test**

  Add an in-memory fixture containing the five 27-key theta tables and the `_SPECIALIST_THETA`/`_apply_theta` markers. Assert that `SETUP_SHRINK` changes all five tables, preserves every boolean, leaves the dispatch and `_apply_theta` markers unchanged, and returns a recipe string containing the exact variant. Assert that unknown variants and missing tables raise `RocketThetaBehaviorMetaError`.

- [ ] **Step 2: Run test to verify it fails**

  Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONPATH=.:src pytest -q tests/test_rocket_theta_behavior_meta_v2.py`
  Expected: FAIL because the module and transformer do not exist.

### Task 2: Implement strict theta transformer

**Files:**
- Create: `src/mage_ptcg/opponent_ingest/rocket_theta_behavior_meta_v2.py`
- Test: `tests/test_rocket_theta_behavior_meta_v2.py`

**Interfaces:**
- `ROCKET_THETA_BEHAVIOR_META_SCHEMA_V2: str`
- `ROCKET_THETA_VARIANTS_V2: tuple[str, ...]`
- `RocketThetaBehaviorMetaError(ValueError)`
- `_transform_rocket_theta(source: bytes, variant: str) -> tuple[bytes, str]`
- `_validate_theta_tables(source_text: str) -> tuple[str, ...]`

- [ ] **Step 1: Implement exact table discovery**

  Parse the source with `ast`, require exactly `_THETA_GENERAL`, `_THETA_LUCMIX`, `_THETA_A09_MERGED`, `_THETA_A07_MERGED`, `_THETA_ABOMASNOW_R2`, require dictionary literals with identical 27-key sets, and reject non-literal or duplicate assignments.

- [ ] **Step 2: Implement bounded numeric token replacement**

  Use AST source offsets only to identify value spans; replace numeric literals in-place without reserializing unrelated source. Preserve `True`/`False`, whitespace, comments, strings, imports, environment keys, dispatch code, and deck sidecar behavior. Implement the ten named recipes in the design spec, with fixed decimal rounding and explicit clip bounds.

- [ ] **Step 3: Implement deterministic failure behavior**

  Reject unknown recipe, missing key, unsupported value type, no-op output, and a transformed source whose five tables no longer parse or whose policy SHA equals the input SHA. Return `ROCKET_THETA_BEHAVIOR_V2:<variant>` as recipe metadata.

- [ ] **Step 4: Run focused tests**

  Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONPATH=.:src pytest -q tests/test_rocket_theta_behavior_meta_v2.py`
  Expected: PASS.

### Task 3: Implement sealing, lineage, and CLI contract

**Files:**
- Modify: `src/mage_ptcg/opponent_ingest/rocket_theta_behavior_meta_v2.py`
- Create: `scripts/generate_rocket_theta_behavior_meta_v2.py`
- Modify: `tests/test_rocket_theta_behavior_meta_v2.py`

**Interfaces:**
- `seal_rocket_theta_behavior_meta_v2(*, base_root: Path|str, output_root: Path|str, source_epoch: str, seed_namespace: str, p1_package: Path|str, variants: Sequence[str], split_by_variant: Mapping[str,str], current_pool_manifest: Path|str|None, scan_roots: Sequence[Path|str]) -> dict[str, object]`
- CLI flags: `--base-root`, `--output`, `--source-epoch`, `--seed-namespace`, repeated `--variant`, repeated `--scan-root`, `--current-pool-manifest`, `--p1-package`, and required JSON `--split-config`.

- [ ] **Step 1: Write failing sealing tests**

  Build temporary sealed base/source notes and assert that sealing emits 12 candidates, pool/fresh/split/intake manifests, exact deck copies, local-only authority, unique policy hashes, and explicit 8/2/2 split. Assert that a scan-root policy hit, existing output, duplicate label, or DEV/FINAL omission fails closed.

- [ ] **Step 2: Implement base/source verification**

  Reuse `_read_base_source`, `_static_findings`, `_artifact_hits`, `_sha256_file`, and `load_opponent_pool_v1` patterns. Verify source note hashes, exact 60-card deck, static findings, and current pool identities before writing anything.

- [ ] **Step 3: Implement no-clobber artifact sealing**

  Write each candidate with exclusive creation, write `SOURCE.md` and per-candidate evidence, then write pool/fresh/meta/split/intake manifests. A single failure raises before accepting the pool; no dummy rows or authority escalation are allowed.

- [ ] **Step 4: Add CLI and run focused tests**

  Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONPATH=.:src pytest -q tests/test_rocket_theta_behavior_meta_v2.py`
  Expected: PASS.

### Task 4: Add explicit variant/split configuration

**Files:**
- Create: `configs/meta_specialist/cg_rocket_theta_behavior_v2.json`
- Modify: `tests/test_rocket_theta_behavior_meta_v2.py`

**Interfaces:**
- Config schema declares `base_root`, `output_root`, `source_epoch`, `seed_namespace`, `variants`, and `split_by_variant`.
- Split values are exactly `META_TRAIN` (8), `META_DEV` (2), `META_FINAL` (2).

- [ ] **Step 1: Add config validation test**

  Assert that the config lists 12 unique variants, all ten base recipes are represented, and no DEV/FINAL ID appears in the TRAIN ID list.

- [ ] **Step 2: Add the config**

  Use source epoch `20260815-rocket-theta-v2`, seed namespace `cg-rocket-theta-v2-a`, output root `runs/cg-rocket-theta-behavior-meta-20260815-a`, and the 8/2/2 split from the design.

- [ ] **Step 3: Run config/sealing tests**

  Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONPATH=.:src pytest -q tests/test_rocket_theta_behavior_meta_v2.py`
  Expected: PASS.

### Task 5: Seal and verify the TRAIN-reserved pool

**Files:**
- Create at runtime only: `runs/cg-rocket-theta-behavior-meta-20260815-a/`
- Create: `docs/evidence/cg-rocket-theta-behavior-meta-v2-20260815.md`

**Interfaces:**
- Consumes the config and accepted source root.
- Produces pool, fresh meta, split, intake report, per-candidate evidence, and a short evidence record with SHA-256 values.

- [ ] **Step 1: Run generator**

  Run: `TMPDIR=/tmp PYTHONPATH=.:src python scripts/generate_rocket_theta_behavior_meta_v2.py --config configs/meta_specialist/cg_rocket_theta_behavior_v2.json --p1-package runs/final-sprint-autonomous/cg-policy-screen-v1-retry-safe4-20260814/candidates/cg-lethal-target-v1/package --current-pool-manifest opponents/pool_manifest.json --scan-root runs --scan-root docs/evidence`
  Expected: `SEALED` report, 12 candidates, no current-pool/artifact identity hits.

- [ ] **Step 2: Run deterministic preflight**

  Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONPATH=.:src pytest -q tests/test_rocket_theta_behavior_meta_v2.py && python -m py_compile src/mage_ptcg/opponent_ingest/rocket_theta_behavior_meta_v2.py scripts/generate_rocket_theta_behavior_meta_v2.py && python scripts/docs/validate_docs.py`
  Expected: PASS and no doc validation errors.

- [ ] **Step 3: Record evidence**

  Record source lineage, pool/fresh/split/meta SHAs, all candidate policy SHAs, exact split membership, and the fact that no DEV/FINAL was executed.

### Task 6: TRAIN-only smoke, P1 CEM, and gated fresh validation

**Files:**
- Create at runtime only: `runs/cg-rocket-theta-behavior-smoke-20260815-a/`, `runs/cg-rocket-theta-behavior-cem-20260815-a/`, `runs/cg-rocket-theta-behavior-dev-20260815-a/`, and `runs/cg-rocket-theta-behavior-final-20260815-a/`.
- Modify: `docs/evidence/cg-rocket-theta-behavior-meta-v2-20260815.md`
- Modify: `docs/status/current_status.md`, `docs/status/handoff.md`, `docs/status/chatgpt_context_pack_cg_bestknown_2026-08-15.md`

**Interfaces:**
- Smoke and CEM consume only the eight `META_TRAIN` IDs from the sealed split.
- CEM control is the immutable P1 package with explicit `--control-package` path; no `same` sentinel.

- [ ] **Step 1: Run TRAIN-only fault smoke**

  Invoke `scripts/run_historical_meta_smoke_v1.py` once per explicit TRAIN ID (or its supported repeated-reference form), with no DEV/FINAL IDs. Require `DONE`, fault 0, illegal 0, and artifact metadata proving TRAIN-only exposure.

- [ ] **Step 2: Run P1 CEM**

  Invoke `scripts/run_cg_p1_cem_v1.py` with the sealed split, explicit P1 source/control package, `--all-train-refs`, bounded population 16, at least two generations, independent reevaluation, positive-delta gate, risk-aware update, and no final refs. Preserve progress summaries and avoid `tee`.

- [ ] **Step 3: Gate candidate and run DEV**

  If and only if all independent TRAIN blocks are positive with fault0 and seat gap≤5%, run DEV once with fresh seeds and an independent repeat. Otherwise record `PERFORMANCE_PROMOTION_FAIL` and stop this source epoch without touching FINAL.

- [ ] **Step 4: Run FINAL only after DEV transfer**

  If DEV passes, run FINAL exactly once and record whether it was used for selection. A positive FINAL with fault0 and seat gap≤5% is evidence for the next policy/deck loop, not automatic Champion replacement.

- [ ] **Step 5: Update evidence and status docs**

  Append results, exact commands, split exposure, SHA-256, interpretation, and next action to the evidence file and the three status/context documents. Keep BestKnown unchanged unless the explicit promotion gate is met and separately authorized.

## Verification Checklist

- [ ] Focused transformer and sealing tests pass.
- [ ] Generated policies compile and retain exact deck/dispatch/observation boundary.
- [ ] Pool/fresh/split/meta hashes are recorded and load successfully.
- [ ] TRAIN-only smoke has fault0 and no DEV/FINAL identity hits.
- [ ] CEM has explicit P1 control, independent reevaluation, and risk-aware gate.
- [ ] DEV/FINAL are run only after their predecessor gate and never used as hidden training data.
- [ ] Status/evidence documents distinguish source-generation success from performance promotion.
- [ ] No commit, push, submission, Champion mutation, or current pool mutation occurred.
