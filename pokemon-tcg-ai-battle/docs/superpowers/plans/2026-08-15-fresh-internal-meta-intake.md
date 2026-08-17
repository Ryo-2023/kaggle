# Fresh Internal Meta Intake Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 許可済みの `origin/agents/*` 内部 branch snapshot から、既存 pool と重複しない policy＋60 枚 deck を read-only に発見し、静的安全検査・identity・freshness 証拠を固定した research-only staged pool として `cg_bestknown_loop_v1.py` に接続できる状態を作る。

**Architecture:** branch の commit tree から root `main.py` と `deck.csv` を同一 commit で取得し、self-owned P1、既存 pool identity、既存消費 ledger を除外する。候補の bytes は checkout/import せず content-addressed staging directory へ保存し、`pool_manifest.json` と freshness evidence を同時に生成する。生成物は現行 `opponents/` を変更せず、`build_fresh_meta_batch_v1` が受け取れる形式だけを検証する。

**Tech Stack:** Python 3、標準ライブラリ `subprocess` / `hashlib` / `json` / `ast` / `pathlib`、既存 `mage_ptcg.opponent_ingest.pipeline` の deck normalization・static audit、既存 `mage_ptcg.meta_specialist.cg_bestknown_loop_v1` の fresh-meta contract、pytest。

## Global Constraints

- 対象は `configs/opponents/permissions/pokemon_team_agents_internal_v1.yaml` が許可する `origin/agents/*` のみ。公開 source の取得・解禁・分析は行わない。
- discovery/seal は Git の checkout、import、ネットワーク、依存インストール、現行 pool の上書きを行わない。
- `usage_boundary` は常に `local_eval_only`、authority は training/promotion/submission/longrun すべて false、research_only は true とする。
- self-owned `origin/agents/ono-cg-lethal-v1`、既存 `opponents/pool_manifest.json` の source/policy/deck identity、明示的な consumed ledger の ID は fresh 候補から除外する。
- suspicious static finding、構文エラー、root asset 欠落、60 枚不正、同一 ref 内の identity 不一致は fail-closed にする。
- heavy CABT、Champion 変更、commit、push、Kaggle submit はこの計画の成果物作成後にも自動実行しない。
- 既存の大量未コミット差分を上書き・整形・削除しない。同一ファイルの既存差分と重なる場合は停止して報告する。

---

## Task 1: Define the staged-source contract and red tests

**Files:**
- Create: `src/mage_ptcg/opponent_ingest/fresh_internal_meta_v1.py`
- Create: `tests/test_fresh_internal_meta_v1.py`

- [x] Define immutable candidate/manifest dataclasses or typed mappings with `ref`, `commit`, raw policy/deck SHA, canonical deck SHA, source, usage boundary, and exclusion reason.
- [x] Add tests using a temporary Git repository with remote-like `refs/remotes/origin/agents/*` refs that prove root `main.py` and `deck.csv` are paired from the same commit.
- [x] Add failing tests for reused source commit, reused policy/deck identity, excluded self-owned ref, missing asset, invalid 60-card deck, suspicious network/subprocess code, and deterministic ordering.
- [x] Add a no-clobber test for the staged output path and a test that discovery leaves `HEAD`, refs, and the worktree unchanged.
- [x] Run `PYTHONPATH=.:src pytest -q tests/test_fresh_internal_meta_v1.py` and confirm the new tests fail before implementation.

## Task 2: Implement read-only branch snapshot discovery and sealing

**Files:**
- Modify: `src/mage_ptcg/opponent_ingest/fresh_internal_meta_v1.py`

- [x] Restrict refs to configured `refs/remotes/origin/agents/*` (with an explicit optional ref glob for controlled tests) and resolve each ref through `git rev-parse`/`git show`/`git ls-tree` without checkout.
- [x] Read only root `main.py` and `deck.csv`, reuse the existing official-card/deck normalization, apply a deterministic local deck sidecar patch when needed, and reject unsafe or unresolved candidates.
- [x] Compute content-addressed candidate IDs from branch/ref identity plus policy/deck hashes; keep deck-duplicate policies as distinct candidates but reject exact identity reuse.
- [x] Materialize a staged `<output>/<candidate_id>/{main.py,deck.csv,SOURCE.md}` snapshot with atomic/no-clobber writes, then emit `<output>/pool_manifest.json` and per-candidate freshness evidence.
- [x] Emit a research-only fresh-meta manifest compatible with `build_fresh_meta_batch_v1`, including pool hash, seed namespace, source epoch, sorted references, and evidence hashes.
- [x] Run focused tests until green, then add explicit failure reasons to the report rather than silently dropping candidates.

## Task 3: Add a bounded CLI and integration contract

**Files:**
- Create: `scripts/discover_fresh_internal_meta_v1.py`
- Modify: `tests/test_fresh_internal_meta_v1.py`

- [x] Expose `--repo`, `--pool-manifest`, `--output`, `--source-epoch`, `--seed-namespace`, `--ref-glob`, repeated `--exclude-ref`, and optional `--consumed-ledger` arguments.
- [x] Make dry-run/discovery the default; do not provide an implicit apply-to-current-pool mode.
- [x] Print a compact JSON summary with candidate count, rejection counts, staged pool path, fresh-meta path, and hashes; write full evidence to files.
- [x] Verify the generated staged pool with `load_opponent_pool_v1` and the fresh-meta file with `build_fresh_meta_batch_v1` in a test fixture.
- [x] Run `python -m py_compile`, focused pytest, and `git diff --check`.

## Task 4: Record the actual branch intake and update handoff materials

**Files:**
- Create: `docs/evidence/cg-fresh-internal-source-intake-20260815.md`
- Modify: `docs/status/current_status.md`
- Modify: `docs/status/handoff.md`
- Modify: `docs/status/chatgpt_context_pack_cg_bestknown_2026-08-15.md`

- [x] Run the CLI against the current repository with the two new remote heads (`ozawa-grimmsnarl-rule+RL`, `ozawa-rocket-rule`) and exclude self-owned P1.
- [x] Record commit, policy SHA, raw/canonical deck SHA, static findings, local-only permission, and whether each candidate is staged; do not claim CABT performance before a smoke run.
- [x] Record the existing public-source block (`UNVERIFIED_RULES_CONSTRAINT`) and the fact that current smoke-ready public fresh-unused count is zero.
- [x] Document the exact handoff command to validate the staged pool and call `cg_bestknown_loop_v1.py`; state that CABT launch remains a separate coordinator action.
- [x] Run `python scripts/docs/validate_docs.py` and re-check the final dirty-worktree scope.

## Task 5: Research gate before any CABT

- [x] Require staged pool loader pass, policy smoke pass, fault-free short CABT smoke, seat symmetry check, and fresh-meta verification before using the candidates in policy CEM. The one accepted candidate passed these preflight checks.
- [x] If any gate fails, keep the candidates quarantined and update the restart condition; do not mutate the current BestKnown or submit. The Grimmsnarl snapshot remains quarantined for filesystem write.
- [ ] Only after a real fresh batch is sealed, connect the batch to `cg_bestknown_loop_v1.py` for `P1 → policy CEM → fresh validation → deck → policy`; the CEM execution itself remains pending because source diversity/custom split still needs to be fixed.

## Verification commands

```bash
PYTHONPATH=src pytest -q tests/test_fresh_internal_meta_v1.py
PYTHONPATH=src python scripts/discover_fresh_internal_meta_v1.py --help
python -m py_compile src/mage_ptcg/opponent_ingest/fresh_internal_meta_v1.py scripts/discover_fresh_internal_meta_v1.py
python scripts/docs/validate_docs.py
git diff --check
```
