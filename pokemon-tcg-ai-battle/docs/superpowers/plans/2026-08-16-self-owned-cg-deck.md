# Self-Owned CG Deck Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task with verification checkpoints.

**Goal:** 公式カードデータから公開deckを親にせず再生成できる合法deckを作り、P1 policyの隔離CABT screenへ接続する。

**Architecture:** `self_owned_cg_deck_v1.py` は公式CSVとversioned role specificationから候補を生成し、deck bytes／canonical multiset／provenanceを封印する。`self_owned_cg_package_v1.py` はP1の policy/runtime code だけを明示的にコピーし、P1内の公開 `ROOT_DECK` 定数を候補deckへ置換して candidate package を作る。既存の `run_root_cg_candidate_arena_v1.py` が candidate package のdeckを読み、candidate/controlを同一 opponent・seat・seedで比較する。

**Tech Stack:** Python 3.12、標準 `csv`／`json`／`hashlib`／`random`、既存 `mage_ptcg.deck_io`、official `scripts.test_sim.run_match`、既存 parallel CABT evaluator。

## Global Constraints

- `data/raw/EN_Card_Data.csv` と role spec 以外のdeck bytesをgeneratorの入力にしない。
- `opponents/**/deck.csv` は生成後のcollision auditにだけ使い、parent deckにはしない。
- 60枚、公式ID、同名通常カード4枚以下、ACE SPEC exactly one、Basic Pokémon、Basic Energyをfail-closedで検査する。
- 既存root `deck.csv`、production `main.py`、Champion、submission archive、`opponents/`、commit、push、Kaggle送信は変更しない。
- 候補は `research_only=true`、training/promotion/submission authorityは全てfalse、出力rootはno-clobberとする。
- heavy CABTは static/package gate と bounded smoke がPASSした候補だけに実行し、TTY progressはrunnerに所有させる。

---

### Task 1: Official-card scratch generator

**Files:**
- Create: `configs/meta_specialist/self_owned_cg_deck_spec_v1.json`
- Create: `src/mage_ptcg/meta_specialist/self_owned_cg_deck_v1.py`
- Test: `tests/meta_specialist/test_self_owned_cg_deck_v1.py`

**Interfaces:**
- `load_card_catalog_v1(card_database_path: Path) -> CardCatalogV1`
- `generate_self_owned_deck_v1(*, catalog: CardCatalogV1, spec: SelfOwnedDeckSpecV1, seed: int, ordinal: int, forbidden_canonical_hashes: Collection[str] = ()) -> SelfOwnedDeckCandidateV1`
- `validate_self_owned_deck_v1(card_ids: Sequence[int], catalog: CardCatalogV1, spec: SelfOwnedDeckSpecV1) -> None`
- `canonical_deck_sha256_v1(card_ids: Sequence[int]) -> str`

- [ ] **Step 1: Write failing tests for catalog parsing and deterministic generation**

  Assert that a catalog reads 1,267 official IDs, a fixed seed/ordinal returns the same ordered cards and canonical hash, and a second seed can produce a different candidate without reading `deck.csv`.

- [ ] **Step 2: Run the focused test and confirm the expected missing-symbol failure**

  Run: `pytest tests/meta_specialist/test_self_owned_cg_deck_v1.py -q`

  Expected: FAIL because the new module and spec are not present.

- [ ] **Step 3: Implement catalog/spec dataclasses and deterministic role-slot generation**

  Parse `Card ID`, `Card Name`, `Stage (Pokémon)/Type (Energy and Trainer)`, `Rule`, and `Previous stage`. Build slots from the spec’s fixed counts and candidate pools using a local `random.Random(seed)`, retrying boundedly when same-name or ACE limits would be exceeded. Do not import or read any repository deck path.

- [ ] **Step 4: Implement fail-closed legality and identity checks**

  Count by card name (except Basic Energy), enforce 60 cards, official IDs, Basic Pokémon, Basic Energy, ACE exactly one, evolution-parent presence for every selected Stage 1/Stage 2 card, and reject forbidden canonical hashes. Canonical identity must hash sorted integer IDs using stable JSON bytes.

- [ ] **Step 5: Run focused tests and verify GREEN**

  Run: `pytest tests/meta_specialist/test_self_owned_cg_deck_v1.py -q`

  Expected: all generator, cap, ACE, unknown-ID, evolution, reproducibility, and collision tests PASS.

### Task 2: Identity seal and candidate package materialization

**Files:**
- Create: `src/mage_ptcg/meta_specialist/self_owned_cg_package_v1.py`
- Create: `scripts/generate_self_owned_cg_deck_v1.py`
- Test: `tests/meta_specialist/test_self_owned_cg_package_v1.py`

**Interfaces:**
- `write_self_owned_deck_artifact_v1(candidate: SelfOwnedDeckCandidateV1, output_root: Path, card_database_sha256: str, role_spec_sha256: str, seed: int, candidate_ordinal: int) -> Mapping[str, object]`
- `materialize_self_owned_cg_package_v1(*, source_package: Path, candidate_deck: Path, output_package: Path, candidate_id: str) -> Mapping[str, object]`
- `verify_self_owned_cg_package_v1(package_root: Path) -> Mapping[str, object]`

- [ ] **Step 1: Write failing tests for no-parent manifest and package shape**

  Assert that the artifact records `parent_deck is None`, official card DB SHA, role-spec SHA, seed, raw/canonical deck hashes, all authority flags false, and that the materialized package contains only generated `deck.csv`, policy `main.py`, and `cg/` runtime files. Assert the public P1 deck bytes are not present in candidate `deck.csv` or the `ROOT_DECK` constant.

- [ ] **Step 2: Run the focused package test and confirm RED**

  Run: `pytest tests/meta_specialist/test_self_owned_cg_package_v1.py -q`

  Expected: FAIL because package functions are not present.

- [ ] **Step 3: Implement no-clobber artifact writer and package copier**

  Copy only `main.py` and `cg/` from the P1 source package. Replace the exact P1 `ROOT_DECK` assignment with the generated tuple, write the generated deck, and add a canonical manifest binding policy/deck/runtime hashes. Do not copy source `deck.csv`, `submission.tar.gz`, or unrelated files.

- [ ] **Step 4: Implement package verifier and fallback binding**

  Verify `main.py`, `deck.csv`, `cg/api.py`, candidate manifest, and the policy/deck hashes. Ensure P1’s fallback uses the candidate tuple after the replacement; malformed selection must return legal indices or the candidate deck, never the old public tuple.

- [ ] **Step 5: Run focused tests and static checks**

  Run: `pytest tests/meta_specialist/test_self_owned_cg_package_v1.py -q` and `git diff --check`.

  Expected: PASS; no public root deck bytes in candidate package; no symlink or path escape.

### Task 3: Generator CLI and static/runtime qualification bridge

**Files:**
- Modify: `scripts/generate_self_owned_cg_deck_v1.py`
- Create: `scripts/run_self_owned_cg_deck_screen_v1.py`
- Test: `tests/meta_specialist/test_self_owned_cg_deck_cli_v1.py`

**Interfaces:**
- CLI `python scripts/generate_self_owned_cg_deck_v1.py --output runs/cg-self-owned-deck-baseline-20260816 --seed 20260816 --ordinal 0 --execute`
- `run_screen(*, candidate_package: Path, output_root: Path, pool_root: Path, refs: Sequence[str], base_seed: int, execute: bool) -> Mapping[str, object]`

- [ ] **Step 1: Write failing CLI/bridge tests**

  Test dry-run refusal without `--execute`, no-clobber output, package verifier integration, and matched candidate/control game payloads with identical pair keys and seeds.

- [ ] **Step 2: Run tests and confirm RED**

  Run: `pytest tests/meta_specialist/test_self_owned_cg_deck_cli_v1.py -q`

  Expected: FAIL on missing CLI/bridge behavior.

- [ ] **Step 3: Implement CLI and static gates**

  Load the official CSV/spec, build a forbidden hash set from existing audit artifacts, generate one candidate, write the artifact, materialize the package, and refuse to overwrite a nonempty output root. Static checks must finish before any CABT invocation.

- [ ] **Step 4: Implement matched screen bridge using existing runner**

  Reuse `run_root_cg_candidate_arena_v1._build_games` and `run_parallel_cabt_evaluation`; add candidate/control metadata for deck identity, policy identity, generator manifest SHA, `fresh_meta` binding, and research-only authority. Do not alter `run_cg_p1_p2_validation_v1.py`’s immutable-root-deck rule.

- [ ] **Step 5: Run focused tests and static checks**

  Run: `pytest tests/meta_specialist/test_self_owned_cg_deck_cli_v1.py -q` and `python -m compileall src/mage_ptcg/meta_specialist/self_owned_cg_deck_v1.py src/mage_ptcg/meta_specialist/self_owned_cg_package_v1.py scripts/generate_self_owned_cg_deck_v1.py scripts/run_self_owned_cg_deck_screen_v1.py`.

### Task 4: Candidate qualification and CABT evidence

**Files:**
- Create: `tests/meta_specialist/test_self_owned_cg_deck_runtime_v1.py`
- Create: `docs/evidence/cg-self-owned-deck-baseline-20260816.md`
- Generate only: `runs/cg-self-owned-deck-baseline-20260816/`

- [ ] **Step 1: Run static/package verification**

  Run the CLI in dry-run/static mode and verify official IDs, copy cap, ACE exactly one, policy import, policy/deck hashes, and clean-room package shape. Record all digests in the run manifest.

- [ ] **Step 2: Run bounded CABT legality probes**

  Execute two seats with independent seeds through `scripts.test_sim.run_match`; accept only `DONE` with agent statuses in `{DONE, ACTIVE, INACTIVE}` and no illegal action/fault. Persist canonical evidence without HTML/log floods.

- [ ] **Step 3: Run matched 96-game screen**

  Compare candidate and P1 control on the same opponent, seat, repetition, and seed strata. If any fault occurs, stop the candidate and record `runtime_fail` without starting 384 games.

- [ ] **Step 4: Run independent unused-meta confirmation only if screen passes**

  Bind a fresh, unconsumed pool/split manifest and run 384 games. Require positive delta, fault0, seat-safe, and opponent×seat-safe before considering the deck a new research parent. This step does not change Champion or submit.

- [ ] **Step 5: Write evidence and update status/handoff**

  Record generator/spec/card DB/policy/deck/evaluator/split/pool hashes, requested/completed/faulted games, WDL by seat, and the gate decision in `docs/evidence/`, then append a concise current-state/handoff entry. Do not claim performance if only smoke passed.

### Task 5: Verification and handoff

**Files:**
- Modify: `docs/status/current_status.md`
- Modify: `docs/status/handoff.md`
- Modify: `docs/status/chatgpt_context_pack_cg_bestknown_2026-08-15.md`

- [ ] **Step 1: Run targeted tests**

  Run: `pytest tests/meta_specialist/test_self_owned_cg_deck_v1.py tests/meta_specialist/test_self_owned_cg_package_v1.py tests/meta_specialist/test_self_owned_cg_deck_cli_v1.py -q`.

- [ ] **Step 2: Run repository document validation**

  Run: `python scripts/docs/validate_docs.py` and `git diff --check`.

- [ ] **Step 3: Audit Git state**

  Run: `git status --short`; preserve all pre-existing dirty files and report that no commit/push/Champion/submission was performed.

- [ ] **Step 4: Handoff next loop**

  If the candidate passes all gates, use its sealed package as the deck-phase incumbent for `cg_bestknown_loop_v1.py`; otherwise keep P1/root deck as BestKnown and record the exact failing gate and next design change.
