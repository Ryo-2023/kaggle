# TRAIN-only難度校正付き heterogeneous meta pool 実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 未消費かつfault-freeな複数source familyのcandidateを、P1のTRAIN-only baselineから難度・seat support・family diversityで選別し、`cg_bestknown_loop_v1`へ渡せるhash-boundなresearch-only meta batchを生成する。

**Architecture:** 既存candidateのdeck＋policyを一つのwrapperへ混ぜず、候補ディレクトリをsource rootから安全に再配置するpool-level builderを追加する。builderはTRAIN-only ledgerを集計し、candidateごとのscore・両seat support・fault-free条件を検証してから、family floor/capとtarget difficultyに基づく決定的選択を行う。選択結果は新しいpool/fresh/meta/split/evidenceへ固定し、既存のCEM・BestKnown runnerを変更せず接続する。

**Tech Stack:** Python 3、標準ライブラリ（`json`、`hashlib`、`shutil`、`dataclasses`）、既存の`canonical_deck_sha256`、`load_opponent_pool_v1`、`evaluation_implementation_sha256_v1`、pytest。

## Global Constraints

- 既存のChampion、P1 package、`opponents/`、提出物、git commit、push、Kaggle submissionは変更しない。
- `training_allowed`、`promotion_allowed`、`submission_allowed`、`longrun_allowed`は全artifactで`false`、`research_only`は`true`に固定する。
- TRAIN以外のledger行（`META_DEV`、`META_FINAL`、split不明のholdout行）は校正選択へ利用しない。
- fault行、invalid行、片seatしか存在しないcandidateは選択しない。faultを勝率へ変換しない。
- source candidateのpolicy bytes、deck bytes、`policy_hash`、canonical deck hashは再書込みせず、出力で再検証する。
- `unused_before_run`は候補IDとpolicy SHAの消費台帳との重複がない場合だけ`true`にする。
- source root間でIDまたは同一policy+deck identityが重複する場合はfail-closedする。
- 既存のユーザー差分は上書きせず、同一ファイルを別作業と同時編集しない。

---

### Task 1: 校正選択とfreshness契約をテストで固定する

**Files:**
- Create: `tests/test_calibrated_meta_pool_v1.py`
- Create: `src/mage_ptcg/opponent_ingest/calibrated_meta_pool_v1.py`

**Interfaces:**
- `build_calibrated_meta_pool_v1(*, source_roots: Sequence[Path | str], calibration_ledger_paths: Sequence[Path | str], output_root: Path | str, p1_package: Path | str, source_epoch: str, seed_namespace: str, target_score: float = 0.15, score_floor: float = 0.02, score_ceiling: float = 0.35, requested_count: int = 12, min_families: int = 3, family_cap: int = 4, min_games_per_candidate: int = 2, consumed_ids: Sequence[str] = (), consumed_policy_sha256: Sequence[str] = ()) -> dict[str, object]`
- The function returns a report containing output paths, selected IDs, rejected IDs, calibration summaries, SHA-256 bindings, and all authority flags.
- The builder emits `pool_manifest.json`, `fresh_meta.json`, `meta_manifest.json`, `cg_historical_split.json`, `evidence/<id>.json`, and `intake_report.json` under `output_root`.

- [ ] **Step 1: Write the failing tests**

```python
def test_selects_train_only_moderate_candidates_with_family_floor_and_seat_support(tmp_path):
    roots = _write_source_roots(tmp_path, families=("A", "B", "C"))
    ledger = _write_ledger(tmp_path, rows={
        "a1": ((0, "win"), (1, "loss")),
        "a2": ((0, "loss"), (1, "loss")),
        "b1": ((0, "win"), (1, "win")),
        "b2": ((0, "loss"),),
        "c1": ((0, "loss"), (1, "loss")),
    })
    report = build_calibrated_meta_pool_v1(
        source_roots=roots,
        calibration_ledger_paths=(ledger,),
        output_root=tmp_path / "out",
        p1_package=_write_p1(tmp_path),
        source_epoch="test-epoch",
        seed_namespace="test-seed",
        requested_count=3,
        min_families=3,
    )
    assert report["selected_count"] == 3
    assert set(report["selected_families"]) == {"A", "B", "C"}
    assert "a2" not in report["selected_ids"]
    assert "b2" not in report["selected_ids"]
    assert json.loads((tmp_path / "out" / "fresh_meta.json").read_text())["authority"]["promotion_allowed"] is False
```

```python
def test_rejects_holdout_rows_faults_and_consumed_policy_identity(tmp_path):
    roots = _write_source_roots(tmp_path, families=("A",))
    ledger = _write_ledger_with_dev_final_fault(tmp_path)
    with pytest.raises(CalibratedMetaPoolError, match="TRAIN-only"):
        build_calibrated_meta_pool_v1(
            source_roots=roots,
            calibration_ledger_paths=(ledger,),
            output_root=tmp_path / "out",
            p1_package=_write_p1(tmp_path),
            source_epoch="test-epoch",
            seed_namespace="test-seed",
            consumed_policy_sha256=("a" * 64,),
        )
```

- [ ] **Step 2: Run the focused tests and verify the expected missing-symbol failure**

Run: `TMPDIR=/tmp PYTHONPATH=.:src pytest -q tests/test_calibrated_meta_pool_v1.py`

Expected: FAIL because `calibrated_meta_pool_v1` and its contract are not implemented.

- [ ] **Step 3: Implement the minimal pure selection and ledger parser**

Implement strict JSONL parsing with these rules:

1. Accept only rows with `status == "DONE"`, `raw_status == "DONE"`, `outcome in {"win", "loss", "draw"}`, and `fault_detail is None`.
2. Require a TRAIN marker: `metadata["calibration_scope"] == "META_TRAIN"` or top-level `split == "META_TRAIN"`; reject a ledger file containing a row explicitly marked `META_DEV` or `META_FINAL`.
3. Aggregate wins, losses, draws, faults, games, and seat counts by `opponent_id`; require `games >= min_games_per_candidate`, both seats, and zero faults.
4. Compute `score = wins / games`, reject scores outside `[score_floor, score_ceiling]`, then select by family floor followed by deterministic `abs(score-target_score), -games, family, id` order with `family_cap`.
5. Derive family from `source_family` when present, otherwise `derivation_recipe` prefix, otherwise `source + canonical_deck_hash`.
6. Refuse duplicate candidate IDs, duplicate `(policy_hash, canonical_deck_hash)`, missing rows, malformed SHA values, and any overlap with `consumed_ids` or `consumed_policy_sha256`.

- [ ] **Step 4: Run the focused tests and verify they pass**

Run: `TMPDIR=/tmp PYTHONPATH=.:src pytest -q tests/test_calibrated_meta_pool_v1.py`

Expected: PASS.

---

### Task 2: Hash-bound artifact materialization and command-line entrypoint

**Files:**
- Modify: `src/mage_ptcg/opponent_ingest/calibrated_meta_pool_v1.py`
- Create: `scripts/build_calibrated_meta_pool_v1.py`
- Modify: `tests/test_calibrated_meta_pool_v1.py`

**Interfaces:**
- The builder copies only selected candidate directories and rejects symlinks, missing `main.py`/`deck.csv`, policy hash mismatch, non-60-card decks, non-`local_eval_only` rows, and unsafe output collisions.
- CLI arguments mirror the function and support repeatable `--source-root`, `--ledger`, `--consumed-id`, and `--consumed-policy-sha256`.

- [ ] **Step 1: Add a failing artifact integrity test**

```python
def test_materializes_pool_fresh_meta_split_and_rebinds_hashes(tmp_path):
    root = _write_source_roots(tmp_path, families=("A", "B", "C"))
    ledger = _write_balanced_ledger(tmp_path)
    report = build_calibrated_meta_pool_v1(
        source_roots=root,
        calibration_ledger_paths=(ledger,),
        output_root=tmp_path / "out",
        p1_package=_write_p1(tmp_path),
        source_epoch="test-epoch",
        seed_namespace="test-seed",
        requested_count=3,
        min_families=3,
    )
    pool = json.loads((tmp_path / "out" / "pool_manifest.json").read_text())
    fresh = json.loads((tmp_path / "out" / "fresh_meta.json").read_text())
    split = json.loads((tmp_path / "out" / "cg_historical_split.json").read_text())
    assert {row["id"] for row in pool} == set(fresh["reference_ids"])
    assert split["evaluation_contract"]["final_results_read_during_search"] is False
    for row in pool:
        assert hashlib.sha256((tmp_path / "out" / row["id"] / "main.py").read_bytes()).hexdigest() == row["policy_hash"]
```

- [ ] **Step 2: Run the test and confirm the materialization assertion fails**

Run: `TMPDIR=/tmp PYTHONPATH=.:src pytest -q tests/test_calibrated_meta_pool_v1.py::test_materializes_pool_fresh_meta_split_and_rebinds_hashes`

Expected: FAIL until artifact writing is implemented.

- [ ] **Step 3: Implement no-clobber artifact writing**

Write canonical JSON with a trailing newline, copy selected source trees without following symlinks, compute pool/fresh/meta/split/evidence hashes, and bind `p1_policy_sha256`, `p1_deck_sha256`, and evaluator SHA in the split. Use deterministic sorted IDs; put the final two selected IDs in `META_DEV` and `META_FINAL`, and all earlier IDs in `META_TRAIN`.

- [ ] **Step 4: Add CLI and run all focused tests**

Run: `TMPDIR=/tmp PYTHONPATH=.:src pytest -q tests/test_calibrated_meta_pool_v1.py`

Expected: PASS, including CLI parser coverage and `build_fresh_meta_batch_v1` acceptance of the generated fresh manifest after smoke promotion fixtures set `smoke_ok=true`.

---

### Task 3: Real unused-source calibration batch and downstream gate

**Files:**
- Create: `docs/evidence/cg-calibrated-heterogeneous-meta-pool-20260815.md`
- Modify: `docs/status/current_status.md`
- Modify: `docs/status/handoff.md`
- Modify: `docs/status/chatgpt_context_pack_cg_bestknown_2026-08-15.md`

**Interfaces:**
- Use only source roots whose candidate IDs and policy SHAs are absent from the consumed CEM audit.
- Run P1 calibration smoke over the broad source panel with one game per seat, then materialize the calibrated pool using only those TRAIN rows.
- Promote smoke only after all selected candidates are complete and fault-free; do not read DEV/FINAL during selection.
- Run a bounded P1 CEM screen only if the calibrated batch passes `build_fresh_meta_batch_v1`; preserve the P1 center when the candidate fails independent seat-safe gates.

- [ ] **Step 1: Build an immutable consumed-ID/policy audit artifact**

Record the source roots, CEM manifests scanned, consumed IDs/policy SHAs, and exclusion reason. Do not treat a prior smoke-only use as CEM consumption, but do treat any CEM screen, independent re-evaluation, or candidate manifest reference as consumed.

- [ ] **Step 2: Run broad TRAIN-only calibration smoke**

Use `scripts/run_historical_meta_smoke_v1.py` with P1, fixed base seed, both seats, and a bounded one-game-per-seat budget. Store the ledger and summary under a new `runs/` root; no DEV/FINAL split is read.

- [ ] **Step 3: Materialize, statically validate, and promote the calibrated pool**

Run `scripts/build_calibrated_meta_pool_v1.py`, then the existing static/legality checks and `scripts/promote_historical_meta_smoke_v1.py` or an equivalent fail-closed promotion wrapper. Verify `build_fresh_meta_batch_v1` with the consumed audit.

- [ ] **Step 4: Run bounded CEM and fresh validation only after the freshness gate**

Run `scripts/run_cg_p1_cem_v1.py` against the calibrated pool with P1 immutable. Use screen → independent re-evaluation → unused DEV/FINAL in that order. Never promote or alter Champion from a screen-only result.

- [ ] **Step 5: Update evidence and status**

Record exact roots, SHA-256 values, seed namespaces, game counts, per-family selection, seat support, fault counts, candidate score bands, CEM outcome, remaining risk, and the next decision. Update the ChatGPT context pack with the new canonical source recipe and preserve the prior action-level failure as historical evidence.

---

## Verification checklist

- [ ] `TMPDIR=/tmp PYTHONPATH=.:src pytest -q tests/test_calibrated_meta_pool_v1.py tests/test_routed_ensemble_meta_v1.py tests/test_promote_historical_meta_smoke_v1.py tests/meta_specialist/test_cg_bestknown_loop_v1.py`
- [ ] `python -m py_compile src/mage_ptcg/opponent_ingest/calibrated_meta_pool_v1.py scripts/build_calibrated_meta_pool_v1.py`
- [ ] `python scripts/docs/validate_docs.py`
- [ ] `git diff --check`
- [ ] `git status --short` confirms no commit, push, Champion change, or submission was performed.
