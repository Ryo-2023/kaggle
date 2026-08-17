# Kaggle公開kernel meta intake v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 公開Kaggle kernelの安全な提出コードを隔離したfresh `local_eval_only` opponent poolへ変換し、P1固定CEMと独立validationへ再現可能に接続する。

**Architecture:** 標準ライブラリだけのtar intake層が、path/symlink/容量を検証し、保持sourceをAST監査する。候補ごとのwrapperは取得元policyを固有module namespaceでロードし、shared repo `cg`だけをengineとして使う。sealed poolとfresh manifestは既存loader・historical split・CEM runnerのhash契約に合わせ、既存poolとChampionは変更しない。

**Tech Stack:** Python 3.12、`tarfile`、`ast`、`hashlib`、`json`、既存`normalize_deck_text`/`canonical_deck_sha256`、pytest、既存`load_opponent_pool_v1`、`build_historical_meta_split_v1.py`、`run_cg_p1_cem_v1.py`。

## Global Constraints

- 既存`opponents/pool_manifest.json`、Champion、P1 package、submission bundleを変更しない。
- 公開kernelは`local_eval_only`であり、Kaggle提出、再配布、teacher label利用を禁止する。
- 同一ファイルの同時編集を行わず、重いCABT実行はmain coordinatorだけが起動する。
- unsafe static finding、tar path violation、loader faultはfail-closedで記録し、candidateを静かに除外しない。
- final splitはCEM探索へ渡さず、独立validationのみに使う。
- TDDで各production codeの前に失敗テストを実行し、完了主張は実行ログで検証する。

## File Map

- Create: `src/mage_ptcg/opponent_ingest/kaggle_kernel_meta_v1.py` — source spec、safe extraction、AST scan、wrapper、fresh pool sealing。
- Create: `scripts/generate_kaggle_kernel_meta_v1.py` — JSON configを読み、ローカルtar群をsealするCLI。
- Create: `tests/test_kaggle_kernel_meta_v1.py` — tar safety、AST、wrapper隔離、fresh manifestのfocused tests。
- Create: `configs/meta_specialist/cg_kaggle_kernel_meta_v1.json` — 2026-08-15取得batchのsource URL/tar SHA/splitを固定する設定。
- Create: `docs/evidence/cg-kaggle-kernel-meta-intake-v1-20260815.md` — source provenance、smoke/CEM結果を要約する。
- Modify: `docs/status/current_status.md` — actual outputと未昇格理由を追記する。
- Modify: `docs/status/handoff.md` — 次のCEM/validationコマンドとfresh境界を追記する。
- Modify: `docs/status/chatgpt_context_pack_cg_bestknown_2026-08-15.md` — ChatGPTへ渡す最新状態を追記する。

### Task 1: Define the safe intake API and test tar boundaries

**Files:**
- Create: `tests/test_kaggle_kernel_meta_v1.py`
- Create: `src/mage_ptcg/opponent_ingest/kaggle_kernel_meta_v1.py`

**Interfaces:**
- Consumes: a `KernelSourceSpec`, an existing pool manifest path, and an output root.
- Produces: `seal_kaggle_kernel_meta_v1(specs, pool_manifest_path, output_root, source_epoch, seed_namespace, scan_roots=()) -> dict[str, object]`.
- Produces: `scan_source_text(text: str) -> tuple[list[str], tuple[str, ...]]` and `safe_extract_kernel_tar(spec, payload_root) -> dict[str, object]` for focused tests.

- [ ] **Step 1: Write the failing tests**

```python
def test_rejects_tar_path_traversal_and_symlink(tmp_path):
    spec = make_tar_spec(tmp_path, {"../escape.py": b"x"}, symlink="link.py")
    with pytest.raises(KaggleKernelMetaError, match="unsafe tar member"):
        safe_extract_kernel_tar(spec, tmp_path / "out")

def test_rejects_static_network_write_and_dynamic_import_but_allows_list_remove():
    assert "network_import" in scan_source_text("import requests\ndef agent(obs): return []")[0]
    assert "filesystem_write" in scan_source_text("from pathlib import Path\nPath('x').write_text('x')")[0]
    assert "dynamic_import" in scan_source_text("import importlib\nimportlib.import_module('x')")[0]
    assert scan_source_text("def agent(obs):\n  xs=[1]\n  xs.remove(1)\n  return []")[0] == []

def test_wrapper_loads_two_candidates_without_payload_module_collision(tmp_path):
    first = seal_fixture_candidate(tmp_path, "one", "from helper import value\ndef agent(obs): return []", "1\n" * 60)
    second = seal_fixture_candidate(tmp_path, "two", "from helper import value\ndef agent(obs): return []", "2\n" * 60)
    assert load_agent(first)({}) == []
    assert load_agent(second)({}) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest -q tests/test_kaggle_kernel_meta_v1.py`

Expected: FAIL with import errors for `mage_ptcg.opponent_ingest.kaggle_kernel_meta_v1`, proving the tests exercise the new API.

- [ ] **Step 3: Implement the minimal safe primitives**

Implement `KernelSourceSpec`, `KaggleKernelMetaError`, `_sha256_bytes`, `_canonical_json`, `scan_source_text`, and `safe_extract_kernel_tar`. Require root `main.py`/`deck.csv`, reject path traversal, links, non-regular members, and payload limits; skip only `cg/`, caches, pyc, submission archive, and notebook outputs. Copy root policy as `payload/original_main.py`, retain non-engine helper files below `payload/`, and return member/hash/exclusion evidence without network access.

- [ ] **Step 4: Run tests to verify the primitives pass**

Run: `pytest -q tests/test_kaggle_kernel_meta_v1.py -k 'tar or static'`

Expected: PASS for path/symlink/capacity rejection and AST findings.

### Task 2: Implement isolated wrapper and sealed pool manifest

**Files:**
- Modify: `src/mage_ptcg/opponent_ingest/kaggle_kernel_meta_v1.py`
- Test: `tests/test_kaggle_kernel_meta_v1.py`

**Interfaces:**
- Consumes: Task 1 extracted payload and existing pool manifest.
- Produces: candidate root with wrapper `main.py`, sidecar `deck.csv`, `SOURCE.md`, evidence JSON, `pool_manifest.json`, `fresh_meta.json`, `intake_report.json`.
- Produces: `write_candidate_wrapper(candidate_id, payload_root, destination) -> str` and `seal_kaggle_kernel_meta_v1(...) -> dict[str, object]`.

- [ ] **Step 1: Write the failing tests**

```python
def test_seal_writes_hash_bound_local_eval_pool_and_fresh_meta(tmp_path):
    spec = make_safe_kernel_spec(tmp_path, "candidate-a")
    pool = write_pool_manifest(tmp_path / "current", [])
    report = seal_kaggle_kernel_meta_v1(
        specs=[spec], pool_manifest_path=pool, output_root=tmp_path / "sealed",
        source_epoch="kaggle-public-20260815-a", seed_namespace="kernel-seed-a",
    )
    assert report["status"] == "SEALED"
    row = json.loads((tmp_path / "sealed/pool_manifest.json").read_text())[0]
    assert row["usage_boundary"] == "local_eval_only"
    assert row["policy_hash"] == sha256(tmp_path / "sealed/candidate-a/main.py")
    fresh = json.loads((tmp_path / "sealed/fresh_meta.json").read_text())
    assert fresh["references"][0]["fresh"] is True
    assert fresh["authority"]["submission_allowed"] is False

def test_seal_rejects_duplicate_policy_or_deck_identity(tmp_path):
    first = make_safe_kernel_spec(tmp_path, "candidate-a")
    second = make_safe_kernel_spec(tmp_path, "candidate-b", same_assets_as=first)
    report = seal_kaggle_kernel_meta_v1(
        specs=[first, second], pool_manifest_path=write_pool_manifest(tmp_path / "current", []),
        output_root=tmp_path / "sealed", source_epoch="epoch", seed_namespace="seed",
    )
    assert report["accepted_ids"] == ["candidate-a"]
    assert report["rejections"]["candidate-b"] == ["batch_identity_reused"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest -q tests/test_kaggle_kernel_meta_v1.py -k 'seal'`

Expected: FAIL because sealing and wrapper functions are not implemented.

- [ ] **Step 3: Implement minimal sealing**

Validate URL/ref/id/hash fields, extract each tar into a temporary candidate staging directory, run all retained `.py` scans, normalize and hash the deck, write a candidate-specific wrapper that evicts only payload-root modules before import, and write no-clobber JSON/evidence files. The staged wrapper must append its payload path after the repo path so shared `cg` resolves to the repository engine. Freshness scans check current pool plus configured text artifact roots for candidate id, wrapper policy hash, and canonical deck hash. A duplicate within the same batch is rejected with `batch_identity_reused`.

- [ ] **Step 4: Run tests and loader checks**

Run: `pytest -q tests/test_kaggle_kernel_meta_v1.py -k 'seal or wrapper'` and `python -m py_compile src/mage_ptcg/opponent_ingest/kaggle_kernel_meta_v1.py`.

Expected: PASS; output pool is readable by `load_opponent_pool_v1` and no output path is overwritten.

### Task 3: Add the reproducible CLI/config and source batch

**Files:**
- Create: `scripts/generate_kaggle_kernel_meta_v1.py`
- Create: `configs/meta_specialist/cg_kaggle_kernel_meta_v1.json`
- Modify: `tests/test_kaggle_kernel_meta_v1.py`

**Interfaces:**
- Consumes: JSON with `source_epoch`, `seed_namespace`, `output_root`, `pool_manifest`, `p1_package`, `sources`, `split`.
- Produces: a sealed pool and split-ready manifest under `runs/cg-kaggle-kernel-meta-intake-v1-20260815/`.

- [ ] **Step 1: Write the failing CLI test**

```python
def test_cli_config_requires_existing_tar_and_does_not_call_network(tmp_path):
    config = make_config_with_safe_kernel(tmp_path)
    result = subprocess.run([sys.executable, "scripts/generate_kaggle_kernel_meta_v1.py", "--config", str(config)], text=True)
    assert result.returncode == 0
    assert json.loads(result.stdout)["network_access"] is False
```

- [ ] **Step 2: Run it and verify the missing CLI failure**

Run: `pytest -q tests/test_kaggle_kernel_meta_v1.py -k 'cli'`

Expected: FAIL because the CLI file does not yet exist.

- [ ] **Step 3: Implement CLI/config parsing**

Require every `tar_path` and exact `tar_sha256` to match before invoking the pure sealing function. Resolve relative paths against the repo root, preserve source URL and fetched timestamp, and print only the canonical report JSON. The checked-in config references five already-downloaded public kernels (tetsutani Grimmsnarl, jazivxt Alakazam/Crustle/Garchomp, prvsiyan Grimmsnarl v21), assigns 3/1/1 train/dev/final IDs, and records that no source is used for submission.

- [ ] **Step 4: Run focused CLI/docs checks**

Run: `python scripts/generate_kaggle_kernel_meta_v1.py --config configs/meta_specialist/cg_kaggle_kernel_meta_v1.json --dry-run`, then `python scripts/docs/validate_docs.py` and `git diff --check`.

Expected: dry-run validates tar hashes without writing; docs validator and diff check pass.

### Task 4: Seal the real source pool and run research-only smoke/CEM

**Files:**
- Create: `runs/cg-kaggle-kernel-meta-intake-v1-20260815/` (ignored runtime artifact)
- Create: `docs/evidence/cg-kaggle-kernel-meta-intake-v1-20260815.md`
- Modify: `docs/status/current_status.md`, `docs/status/handoff.md`, `docs/status/chatgpt_context_pack_cg_bestknown_2026-08-15.md`

**Interfaces:**
- Consumes: Task 3 config, P1 package, existing engine, historical split builder, and CEM runner.
- Produces: static-sealed pool, train-only fault-inclusive smoke ledger, CEM candidate report, and independent dev/final validation report if gates are met.

- [ ] **Step 1: Seal without changing repository pool**

Run: `python scripts/generate_kaggle_kernel_meta_v1.py --config configs/meta_specialist/cg_kaggle_kernel_meta_v1.json --output runs/cg-kaggle-kernel-meta-intake-v1-20260815`.

Expected: report says `SEALED`, all accepted rows are `local_eval_only`, and `git diff -- opponents/pool_manifest.json` is empty.

- [ ] **Step 2: Build split and verify loader/hash contracts**

Run the existing `build_historical_meta_split_v1.py` with 3 `--train-id`, 1 `--dev-id`, and 1 `--final-id`, then load the pool and split with `load_opponent_pool_v1` and `load_weekend_split(..., verify_sources=True)`. Do not pass `META_DEV` or `META_FINAL` into candidate selection.

- [ ] **Step 3: Run a bounded train-only both-seat smoke**

Run the root candidate arena/evaluator with P1 as subject, only `META_TRAIN` source IDs, fixed fresh smoke seed namespace, 1–2 games per opponent seat, and `workers=1`. Record fault count and opponent import status; stop and record `BLOCKED` if any candidate faults or module collision appears.

- [ ] **Step 4: Connect to P1-fixed CEM only after smoke is clean**

Run `scripts/run_cg_p1_cem_v1.py --source-package <P1 package> --control-package <P1 package> --pool-root <sealed root> --split <sealed split> --all-train-refs --population-size 4 --elite-count 1 --generations 1 --positive-delta-gate --execute`. Use independent `--campaign-seed`; never include final refs. Preserve P1 if the positive gate is not met.

- [ ] **Step 5: Update evidence and handoff**

Record exact commands, hashes, seed IDs, games, faults, CEM delta, and authority boundaries in the evidence file. Update status/handoff/ChatGPT pack only with observed outcomes; mark public source as local-eval-only and do not claim native/public leaderboard performance.

### Task 5: Verify and hand off

**Files:**
- Modify: the files listed above only if verification reveals a concrete defect.

- [ ] **Step 1: Run focused and regression tests**

Run: `pytest -q tests/test_kaggle_kernel_meta_v1.py tests/test_fresh_internal_meta_v1.py tests/test_historical_meta_split_v1.py`.

- [ ] **Step 2: Run source/static checks**

Run: `python -m compileall -q src/mage_ptcg/opponent_ingest scripts/generate_kaggle_kernel_meta_v1.py`, `python scripts/docs/validate_docs.py`, and `git diff --check`.

- [ ] **Step 3: Check Git and process state**

Run: `git status --short`, `git diff -- opponents/pool_manifest.json`, and `pgrep -af 'run_cg_p1_cem|cabt|parallel_cabt' || true`.

Expected: no repository pool/Champion mutation, no unreported heavy process, and all claims in the final handoff match artifacts.

