# Teacher-quality v3 Fresh-worker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** external teacherとRule v0のprimary performance evidenceを、source-snapshotとattempt単位fresh workerで正直に収集可能にする。

**Architecture:** 親collectorはsource snapshotとsealed requestを作り、external policyをimportしない。spawn workerがsnapshotだけを検証・importし、import前にprocess global RNGを初期化して1 gameを実行する。親はcanonical worker responseを再検証して既存のfault/retry ledgerへ追加する。

**Tech Stack:** Python 3、`multiprocessing` spawn、subprocess、`os.open(O_NOFOLLOW)`、SHA-256、canonical JSON、pytest、CABT shipped runner。

## Global Constraints

- old `namespace_only` seed、Git HEAD-only source attestation、original worktree fallbackをproduction evidenceへ使わない。
- source snapshotはsingle-FD read、pre/post dev/ino/mode/size/mtime/ctime、EOF SHA-256、relative contained pathsを必須にする。
- workerはimport前にPython/NumPy/Torch RNGを `agent_sampling_seed` で初期化し、engine randomnessの未attestationを偽らない。
- attemptごとにfresh spawned workerを終了する。worker stdoutはcanonical JSON response一行だけとする。
- calibrationは`strata_complete=false`、`smoke_ok=false` subjectはproduction campaignをfail-closedにする。
- source snapshot、worker response、campaign/ledgerはmissing、symlink、TOCTOU、unexpected field、identity driftをfail-closedにする。
- shared dirty worktreeではcommit/pushを行わない。

---

### Task 1: Sealed source snapshot

**Files:**
- Create: `src/mage_ptcg/meta_specialist/teacher_quality_worker_v3.py`
- Modify: `src/mage_ptcg/meta_specialist/teacher_quality_evidence_v3.py`
- Test: `tests/meta_specialist/test_teacher_quality_evidence_v3.py`

**Interfaces:**
- Produces `SourceSnapshotV3(root: Path, manifest_path: Path, file_sha256: str, tree_sha256: str)`.
- Produces `seal_teacher_quality_source_snapshot_v3(*, plan: CampaignPlanV3, staging_root: Path) -> SourceSnapshotV3`.
- Consumes only `CampaignPlanV3` paths and frozen panel/teacher identity.

- [ ] **Step 1: Write failing snapshot integrity tests**

```python
def test_snapshot_rejects_same_inode_post_hash_rewrite(tmp_path):
    plan = _plan(tmp_path)
    with pytest.raises(ValueError, match="changed while being snapshotted"):
        seal_teacher_quality_source_snapshot_v3(plan=plan, staging_root=tmp_path / "stage")

def test_snapshot_manifest_rejects_symlink_and_escape(tmp_path):
    snapshot = _sealed_snapshot(tmp_path)
    (snapshot.root / "src" / "outside.py").symlink_to("/etc/passwd")
    with pytest.raises(ValueError, match="snapshot entry"):
        verify_source_snapshot_v3(snapshot)
```

- [ ] **Step 2: Run the focused test and confirm RED**

Run: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python -m pytest -q tests/meta_specialist/test_teacher_quality_evidence_v3.py -k snapshot`

Expected: FAIL because snapshot APIs do not exist.

- [ ] **Step 3: Implement single-FD snapshot sealing**

```python
def seal_teacher_quality_source_snapshot_v3(*, plan, staging_root):
    entries = _resolve_required_snapshot_entries(plan)
    with _private_staging_directory(staging_root) as staging:
        manifest_rows = [_copy_verified_regular_file(entry, staging) for entry in entries]
        manifest = _canonical_manifest(manifest_rows)
        _atomic_write(staging / "source-manifest.json", manifest)
        return _publish_snapshot(staging, _sha(manifest))
```

`_copy_verified_regular_file` must use the same descriptor bytes for digest and copy; it must reject symlink/path escape and verify fstat identity before/after EOF.

- [ ] **Step 4: Run Task 1 tests and full collector tests**

Run: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python -m pytest -q tests/meta_specialist/test_teacher_quality_evidence_v3.py`

Expected: PASS, including post-hash rewrite/symlink cases.

- [ ] **Step 5: Record completion without committing shared worktree**

Append RED/GREEN commands, snapshot tree digest semantics, and no-commit reason to `.superpowers/sdd/2026-08-09-r3-recurrent-gate/teacher-quality-v3-fresh-worker-report.md`.

### Task 2: Fresh worker request/response and RNG provenance

**Files:**
- Modify: `src/mage_ptcg/meta_specialist/teacher_quality_worker_v3.py`
- Modify: `src/mage_ptcg/meta_specialist/teacher_quality_evidence_v3.py`
- Test: `tests/meta_specialist/test_teacher_quality_evidence_v3.py`

**Interfaces:**
- Produces `run_teacher_quality_attempt_worker_v3(request_path: Path) -> WorkerResponseV3`.
- Produces `WorkerResponseV3` with closed fields `schema`, `request_sha256`, `snapshot_sha256`, `rng`, `engine_randomness`, `outcome|fault`, `elapsed_seconds`.
- Consumes a canonical request containing logical-game, retry, snapshot and subject-seat identity.

- [ ] **Step 1: Write failing worker-isolation/provenance tests**

```python
def test_worker_seeds_before_external_policy_import(tmp_path):
    response = _run_fixture_worker(tmp_path, agent_sampling_seed=17)
    assert response["rng"] == {"python": 17, "numpy": 17, "torch": 17}

def test_second_worker_does_not_observe_first_policy_global(tmp_path):
    assert _run_fixture_worker(tmp_path, agent_sampling_seed=1)["outcome"] == "win"
    assert _run_fixture_worker(tmp_path, agent_sampling_seed=1)["outcome"] == "win"

def test_worker_never_claims_unattested_engine_seed(tmp_path):
    assert _run_fixture_worker(tmp_path)["engine_randomness"] == "unattested"
```

- [ ] **Step 2: Run the focused tests and confirm RED**

Run: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python -m pytest -q tests/meta_specialist/test_teacher_quality_evidence_v3.py -k 'worker or seed'`

Expected: FAIL because worker protocol does not exist.

- [ ] **Step 3: Implement worker protocol**

```python
def worker_main(request_path: str) -> int:
    request = _read_closed_request(Path(request_path))
    snapshot = _verify_snapshot(request["snapshot"])
    _seed_process_rngs(request["agent_sampling_seed"])
    result = _run_one_snapshot_game(request, snapshot)
    sys.stdout.buffer.write(_canonical(_closed_response(request, result)) + b"\n")
    return 0
```

Use `multiprocessing.get_context("spawn")` or a `sys.executable -I` subprocess. Parent accepts exactly one canonical response line, validates request/snapshot digest/RNG map, and turns malformed output/nonzero exit/timeout into a fault observation.

- [ ] **Step 4: Run Task 2 tests and regression tests**

Run: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python -m pytest -q tests/meta_specialist/test_teacher_quality_evidence_v3.py tests/meta_specialist/test_opponent_schedule_v2.py`

Expected: PASS; a policy module global from a prior request cannot affect the next request.

- [ ] **Step 5: Record completion without committing shared worktree**

Append exact worker schema, timeout/nonzero behavior, and test output to the fresh-worker report.

### Task 3: Collector integration and production preflight

**Files:**
- Modify: `src/mage_ptcg/meta_specialist/teacher_quality_evidence_v3.py`
- Modify: `scripts/run_meta_specialist_teacher_quality_evidence_v3.py`
- Modify: `tests/meta_specialist/test_teacher_quality_evidence_v3.py`
- Test: `tests/meta_specialist/test_teacher_quality_v2.py`

**Interfaces:**
- `build_live_attempt_runner_v3(..., source_snapshot: SourceSnapshotV3, worker_timeout_seconds: int)` returns a fresh-worker runner.
- campaign payload and manifest include `source_snapshot_file_sha256`, `source_snapshot_tree_sha256`, and engine seed capability.
- full evidence is accepted only with profile `full`, full matrix and worker provenance; calibration remains non-authoritative.

- [ ] **Step 1: Write failing integration tests**

```python
def test_live_collector_rejects_smoke_failed_subject_before_worker(tmp_path):
    with pytest.raises(ValueError, match="smoke_ok"):
        build_live_attempt_runner_v3(plan=_smoke_failed_plan(tmp_path))

def test_calibration_manifest_cannot_be_read_as_ready_quality(tmp_path):
    manifest = _run_calibration_fixture(tmp_path)
    assert manifest["strata_complete"] is False
    with pytest.raises(ValueError, match="full performance evidence"):
        read_ready_teacher_quality_manifest_v3(...)
```

- [ ] **Step 2: Run focused tests and confirm RED**

Run: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python -m pytest -q tests/meta_specialist/test_teacher_quality_evidence_v3.py tests/meta_specialist/test_teacher_quality_v2.py -k 'smoke or calibration or provenance'`

Expected: FAIL before integration.

- [ ] **Step 3: Integrate preflight and sealed provenance**

```python
def build_live_attempt_runner_v3(*, plan, transient_root=None, max_steps=10_000):
    _require_production_subject_smoke(plan)
    snapshot = seal_teacher_quality_source_snapshot_v3(plan=plan, staging_root=...)
    return _fresh_worker_runner(plan=plan, snapshot=snapshot, timeout_seconds=...)
```

CLI must expose source snapshot root and timeout, emit plan-only without worker launch, and refuse a live campaign if snapshot/subject smoke/engine capability preflight fails.

- [ ] **Step 4: Run affected full tests and static checks**

Run: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python -m pytest -q tests/meta_specialist/test_teacher_quality_evidence_v3.py tests/meta_specialist/test_teacher_quality_v2.py tests/meta_specialist/test_theta0_manifest_v3.py && .venv/bin/python -m py_compile scripts/run_meta_specialist_teacher_quality_evidence_v3.py src/mage_ptcg/meta_specialist/teacher_quality_evidence_v3.py src/mage_ptcg/meta_specialist/teacher_quality_worker_v3.py && git diff --check`

Expected: PASS; no calibration result can unlock READY/theta0.

- [ ] **Step 5: Independent review and host command preparation**

Review fixture and default worker path for original-worktree fallback, source/snapshot TOCTOU, seed provenance overclaim, stdout injection, retry identity, and `smoke_ok=false`. Only after PASS, write one calibration command; do not execute it or full campaign in this task.

### Task 4: Dataset stream-integrity regression confirmation

**Files:**
- Modify: `src/mage_ptcg/meta_specialist/recurrent_dataset_v3.py`
- Modify: `src/mage_ptcg/meta_specialist/recurrent_dataset_v4.py`
- Modify: `tests/meta_specialist/test_recurrent_dataset_v3.py`
- Modify: `tests/meta_specialist/test_recurrent_dataset_v4.py`

**Interfaces:**
- `_frozen_index_entries_v3` and `_quality_rows_v4` consume only a fully validated private spool.
- `_PhysicalEpisodeTrackerV3` rejects an A→B→A episode sequence without corpus-size Python set growth.

- [ ] **Step 1: Re-run the newly added P1/P2 RED-to-GREEN tests**

Run: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python -m pytest -q tests/meta_specialist/test_recurrent_dataset_v3.py tests/meta_specialist/test_recurrent_dataset_v4.py`

Expected: PASS; same-inode post-hash rewrite, sidecar/index parse drift, and A→B→A are rejected.

- [ ] **Step 2: Run v4 integration regression**

Run: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python -m pytest -q tests/meta_specialist/test_recurrent_dataset_v3.py tests/meta_specialist/test_recurrent_dataset_v4.py tests/meta_specialist/test_teacher_quality_v2.py tests/meta_specialist/test_representation_v4.py`

Expected: PASS; no default quality weight and no legacy receipt acceptance.

- [ ] **Step 3: Record completion without committing shared worktree**

Append P1/P2 reproduction and final command output to `recurrent-dataset-v4-report.md`.

## Plan self-review

- Spec coverage: Task 1 covers source snapshot identity and TOCTOU; Task 2 covers fresh isolation, real RNG initialization and response validation; Task 3 covers live integration, smoke/calibration authority boundaries and host-command preconditions; Task 4 confirms the independently found v3/v4 stream-integrity regressions.
- Placeholder scan: 未完記号や未指定の検証手順は残さず、production rejection条件と具体的なコマンドを上記へ記載した。
- Type consistency: `SourceSnapshotV3` is produced by Task 1 and consumed by Task 2/3; `WorkerResponseV3` is produced only by Task 2 and validated by Task 3; dataset interfaces remain independent from collector interfaces.
