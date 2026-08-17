# Factorial behavior-family v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. The repository forbids unrequested commits, so the commit steps below are replaced by verification and handoff.

**Goal:** 直交した可視行動軸を組み合わせた新しいlocal-eval-only meta sourceをsealし、P1 controlのCEMと独立fresh validationへ接続する。

**Architecture:** Factorial recipeは既存のexact replacement関数を2回だけ合成し、元のdeck・観測境界・静的安全性を保持する。専用moduleが既存behavior-family seal helperへtransformerを渡し、CLIがhash-boundなpool/fresh/split artifactを作る。実験runnerは既存CEMとfresh confirmation runnerを再利用し、promotion権限を持たない。

**Tech Stack:** Python 3.12、pytest、既存 `mage_ptcg` pool/split utilities、CABT evaluator、`apply_patch`。

## Global Constraints

- P1 `cg-lethal-target-v1`＋root deckをcontrolとし、BestKnown、Champion、production、submissionは変更しない。
- sourceは`local_eval_only`、`visible_state_only`、static findings 0、policy/deck identity hash-boundであること。
- 同一ファイルの同時編集は禁止し、既存dirty差分を上書きしない。
- smoke/CEM/FINALはfault0、seat gap≤5%、未使用seed/meta gateを満たさない限り昇格しない。
- `git commit`、`git push`、Kaggle提出は実行しない。

### Task 1: factorial composition contract

**Files:**
- Create: `tests/test_behavior_factorial_meta_v1.py`
- Reference: `src/mage_ptcg/opponent_ingest/behavior_family_meta_v1.py`

**Interfaces:**
- Consumes: Alakazam exact transforms `_replace_alakazam_behavior(source, variant)`。
- Produces: `_replace_alakazam_factorial_behavior(source: bytes, variant: str) -> tuple[bytes, str]` と `ALAKAZAM_FACTORIAL_VARIANTS_V1`。

- [x] **Step 1: Write the failing test**

```python
def test_factorial_transform_composes_two_disjoint_axes() -> None:
    source = _real_alakazam_source_bytes()
    transformed, recipe = _replace_alakazam_factorial_behavior(source, "ABRA_POFFIN")
    assert recipe.endswith(":ABRA_FIRST+POFFIN_FIRST")
    assert transformed != source
    assert b"ABRA: 700" in transformed
    assert b"BUDDY_BUDDY_POFFIN: 600" in transformed

def test_factorial_transform_rejects_unknown_or_duplicate_axis() -> None:
    with pytest.raises(DerivedInternalMetaError):
        _replace_alakazam_factorial_behavior(b"", "UNKNOWN")
```

- [x] **Step 2: Run test to verify it fails**

Run: `TMPDIR=/tmp PYTHONPATH=.:src pytest -q tests/test_behavior_factorial_meta_v1.py`

Expected: collection or import failure because the new module/function does not exist.

- [x] **Step 3: Define the minimal contract in the test helper**

Use a temporary source fixture containing the exact Alakazam Pokemon, setup, and item tables; do not import or execute a candidate policy in the unit test.

- [x] **Step 4: Keep the test RED until production API exists**

Run the same command and confirm the failure names the missing factorial API rather than a fixture typo.

### Task 2: factorial module and CLI

**Files:**
- Create: `src/mage_ptcg/opponent_ingest/behavior_factorial_meta_v1.py`
- Create: `scripts/generate_factorial_behavior_family_meta_v1.py`
- Modify: `tests/test_behavior_factorial_meta_v1.py`

**Interfaces:**
- Consumes: `_replace_alakazam_behavior`, `_seal_behavior_family_v1`, pool manifest, P1 package.
- Produces: `ALAKAZAM_FACTORIAL_VARIANTS_V1`, `_replace_alakazam_factorial_behavior`, `seal_alakazam_factorial_behavior_family_v1(...)`, and CLI `--base-root --output --source-epoch --seed-namespace --p1-package`.

- [x] **Step 1: Implement the exact composition**

Map four names to disjoint pairs:

```python
ALAKAZAM_FACTORIAL_VARIANTS_V1 = (
    "ABRA_POFFIN",
    "ABRA_FEZANDIPITI",
    "DUNSPARCE_POFFIN",
    "DUNSPARCE_FEZANDIPITI",
)
_FACTORIAL_STEPS = {
    "ABRA_POFFIN": ("ABRA_FIRST", "POFFIN_FIRST"),
    "ABRA_FEZANDIPITI": ("ABRA_FIRST", "FEZANDIPITI_DRAW_FIRST"),
    "DUNSPARCE_POFFIN": ("DUNSPARCE_FIRST", "POFFIN_FIRST"),
    "DUNSPARCE_FEZANDIPITI": ("DUNSPARCE_FIRST", "FEZANDIPITI_DRAW_FIRST"),
}
```

Apply the two transforms in order, reject unknown names, and return a recipe containing both axis names.

- [x] **Step 2: Implement the seal wrapper**

Pass the composition transformer to the existing no-clobber seal helper. Preserve `visible_state_only`, `local_eval_only`, static scan, current-pool identity scan, `META_TRAIN=2`, `META_DEV=1`, and remaining `META_FINAL` split.

- [x] **Step 3: Run the new unit test**

Run: `TMPDIR=/tmp PYTHONPATH=.:src pytest -q tests/test_behavior_factorial_meta_v1.py`

Expected: all factorial composition and fail-closed tests PASS.

- [x] **Step 4: Run the relevant regression tests**

Run: `TMPDIR=/tmp PYTHONPATH=.:src pytest -q tests/test_behavior_family_meta_v1.py tests/test_behavior_factorial_meta_v1.py`

Expected: existing behavior-family tests and factorial tests PASS.

### Task 3: seal and preflight the new source epochs

**Files:**
- Create: `runs/cg-alakazam-factorial-meta-20260815-t/` (research artifact only)
- Create: `runs/cg-comfey-factorial-meta-20260815-u/` (research artifact only)
- Use: `scripts/generate_factorial_behavior_family_meta_v1.py`
- Use: `runs/cg-source-audit-20260815-k4/internal_nihei-cynthias-garchomp_3818c21f59b6/`

**Interfaces:**
- Consumes: base snapshot, P1 package `runs/final-sprint-autonomous/kaggle-cg-wrapper-p1-20260815-v1/`, current pool manifest.
- Produces: four fresh factorial candidates, `pool_manifest.json`, `fresh_meta.json`, `cg_historical_split.json`, intake report, and hash-bound evidence.

- [x] **Step 1: Seal the source**

Run:

```bash
PYTHONPATH=src python scripts/generate_factorial_behavior_family_meta_v1.py \
  --base-root runs/cg-source-audit-20260815-k4/internal_nihei-cynthias-garchomp_3818c21f59b6 \
  --output runs/cg-alakazam-factorial-meta-20260815-t \
  --source-epoch internal-alakazam-factorial-20260815-t \
  --seed-namespace internal-alakazam-factorial-seed-20260815-t \
  --p1-package runs/final-sprint-autonomous/kaggle-cg-wrapper-p1-20260815-v1 \
  --current-pool-manifest opponents/pool_manifest.json \
  --scan-root configs --scan-root docs/evidence --scan-root docs/status \
  --scan-root runs/cg-source-audit-20260815-k4
```

Expected: `SEALED`, 4 accepted, no identity/security/deck findings.

- [x] **Step 2: Verify the split and pool**

Run `PYTHONPATH=src python -m pytest -q tests/test_historical_meta_split_v1.py tests/test_derived_internal_meta_v1.py` and load the generated pool with `load_opponent_pool_v1`.

Expected: split hashes match the intake report and every reference is fresh before evaluation.

- [x] **Step 3: Run policy/deck import smoke**

The same recipe was then applied to a separate Hydreigon/Comfey base with
`--family comfey`, producing four additional factorial variants. Both epochs
sealed with four accepted candidates, fresh pool/meta/split hashes, and no
static findings.

Run the existing candidate deck/read smoke against all four generated roots; any import, illegal action, timeout, or static finding stops the experiment before CABT.

### Task 4: CABT smoke, CEM, and fresh confirmation

**Files:**
- Create: `runs/cg-alakazam-factorial-smoke-20260815-t/`
- Create: `runs/cg-alakazam-factorial-cem-20260815-t/`
- Create: `runs/cg-alakazam-factorial-final-20260815-t/`
- Use: `scripts/run_historical_meta_smoke_v1.py`, `scripts/run_cg_p1_cem_v1.py`, `scripts/run_cg_historical_fresh_confirmation_v1.py`

**Interfaces:**
- Consumes: sealed factorial split/pool/fresh meta and P1 package.
- Produces: DONE/fault ledger, CEM generations, independent DEV/FINAL summaries, and a promotion decision.

- [x] **Step 1: Train smoke**

Run both META_TRAIN references, both seats, two games per seat with a new seed namespace. Expected: all requested games DONE, fault0, illegal0, timeout0.

- [x] **Step 2: CEM**

Run two generations with population8, elite2, `--reeval-for-update`, `--reeval-repeats 2`, `--positive-delta-gate`, `--risk-aware-update`, and independent seed `20260940`.

Expected: only a robust positive elite can update the center; otherwise P1 center is retained.

- [x] **Step 3: Fresh DEV and FINAL**

Use META_DEV and META_FINAL with separate base seeds `20260941` and `20260942`; do not feed FINAL results back into CEM or candidate selection.

- [x] **Step 4: Apply the promotion gate**

Promote only if candidate delta is positive, candidate seat gap≤5%, all games fault0, and the result is not explained by a single source-family block. Otherwise record `NOT_PROMOTABLE` and preserve P1.

### Task 5: evidence and handoff

**Files:**
- Create: `docs/evidence/cg-factorial-behavior-family-20260815.md`
- Modify: `docs/evidence/cg-current-state-report-20260815.md`
- Modify: `docs/status/current_status.md`
- Modify: `docs/status/handoff.md`
- Modify: `docs/status/chatgpt_context_pack_cg_bestknown_2026-08-15.md`

**Interfaces:**
- Consumes: source/CEM/FINAL manifests and hashes.
- Produces: a Japanese evidence record distinguishing observed results, correlation limits, and the next research gate.

- [x] **Step 1: Record the source lineage and hashes**

Include base commit/policy/deck, four recipes, pool/fresh/split hashes, seed plan, and authority flags.

- [x] **Step 2: Record outcome and decision**

Include smoke/CEM/DEV/FINAL counts, delta, seat gap, fault count, and whether P1 changed. Do not call a candidate BestKnown without the independent gate.

- [x] **Step 3: Verify documentation**

Run `python scripts/docs/validate_docs.py`, `git diff --check`, `python -m py_compile src/mage_ptcg/opponent_ingest/behavior_factorial_meta_v1.py scripts/generate_factorial_behavior_family_meta_v1.py`, and the focused pytest suite.

## Execution status (2026-08-15)

- Alakazam epoch `t`: smoke 8/8 DONE, fault0; CEM 272/272 DONE, fault0; both generations retained the P1 center; DEV center delta -3.125pt. No candidate was promoted.
- Comfey epoch `u`: smoke 8/8 DONE, fault0; CEM 272/272 DONE, fault0. Generation-1 candidate `cg-p1-cem-g01-c05-796b8f2986f4` had independent delta +25.00pt in both re-evaluation blocks, but opponent seat gaps 50.00% and 25.00% made `seat_safe=false`. Diagnostic fresh FINAL (64 games) was candidate 40.625% vs P1 53.125%, delta -12.50pt, fault0, `NOT_PROMOTABLE`.
- Remote source audits `r` (200 rejected) and `s` (48 rejected) both ended `BLOCKED_NO_SAFE_CANDIDATES`; no new safe external snapshot was available.
- P1, BestKnown, Champion, production, deck, commit, push, and submission remain unchanged. Documentation verification is complete: focused suite 60 passed, py_compile PASS, docs validator PASS, and `git diff --check` PASS.
