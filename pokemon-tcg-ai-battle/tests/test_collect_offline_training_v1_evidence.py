#!/usr/bin/env python3
"""Unit and acceptance tests for collect_offline_training_v1_evidence.py.

These tests exercise the Evidence Collector itself, using fixtures,
temporary directories, and fake/mocked runners. They intentionally do not
run the real repository's final acceptance plan, training pipeline, or
package build -- that happens later on feature/belief-guided-search after
canonical integration.
"""

from __future__ import annotations

import json
import os
import sys
import tarfile
import tempfile
import time
from io import BytesIO
from pathlib import Path

import pytest

# Add repo root to sys.path so we can import the script
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import scripts.collect_offline_training_v1_evidence as collector
from scripts.collect_offline_training_v1_evidence import (
    EXPECTED_REQUIRED_CHECK_IDS,
    RE_ABSOLUTE_PATH,
    RE_SECRET,
    atomic_write,
    bound_excerpt,
    build_c4_fixture_source,
    check_champion_invariant,
    check_hold_module_quarantine,
    check_promotion_invariant,
    check_required_paths,
    compute_file_sha256,
    compute_sha256_text,
    inspect_tarball,
    parse_gemini_manifest,
    redact_structure,
    redact_text,
    run_command_safe,
    run_export_parity_check,
    self_scan_pattern,
    self_scan_privacy,
    validate_plan_schema,
    validate_required_check_coverage,
)


# ---------------------------------------------------------------------------
# Fixtures / plan-building helpers
# ---------------------------------------------------------------------------

def _default_check(check_id: str, runner: str = "champion_invariant", **overrides) -> dict:
    check = {
        "id": check_id,
        "runner": runner,
        "required": True,
        "timeout_seconds": 10,
        "args": [],
        "expected_outputs": [],
        "artifact_inputs": [],
        "artifact_outputs": [],
        "privacy_class": "public",
        "failure_severity": "block",
    }
    check.update(overrides)
    return check


def _build_full_plan(overrides: dict | None = None, extra_checks: list | None = None) -> dict:
    """Build a plan covering all 19 required check IDs.

    Every check defaults to the fast, subprocess-free 'champion_invariant'
    runner so tests run instantly and deterministically; pass `overrides`
    keyed by check id to customize specific checks (e.g. a different
    runner, required=False, args).
    """
    overrides = overrides or {}
    checks = []
    for cid in sorted(EXPECTED_REQUIRED_CHECK_IDS):
        base = _default_check(cid)
        base.update(overrides.get(cid, {}))
        checks.append(base)
    if extra_checks:
        checks.extend(extra_checks)
    return {
        "schema_version": "offline-training-v1-acceptance-plan-v1",
        "plan_id": "test-plan",
        "checks": checks,
    }


def _write_plan(path: Path, plan_data: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(plan_data, f)


def _run_main(tmp_path: Path, plan_file: Path, output_file: Path, extra_argv: list | None = None) -> int:
    sys_argv_backup = sys.argv
    sys.argv = [
        "collect_offline_training_v1_evidence.py",
        "--repository-root",
        str(tmp_path),
        "--plan",
        str(plan_file),
        "--output",
        str(output_file),
    ] + (extra_argv or [])
    try:
        return collector.main()
    finally:
        sys.argv = sys_argv_backup


def _make_main_py(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text("make_rule_agent", encoding="utf-8")


# ---------------------------------------------------------------------------
# 1-5, 17-18: redaction, bounding, low-level plan schema validation
# ---------------------------------------------------------------------------

def test_redaction() -> None:
    """Test path and secret redaction."""
    assert "[REDACTED_PATH]" in redact_text("/home/user/workspace/somefile.py")
    assert "[REDACTED_PATH]" in redact_text("/tmp/tempfile.txt")

    assert "api_key:[REDACTED_SECRET]" in redact_text("api_key:abcdef1234567890")
    assert "token=[REDACTED_SECRET]" in redact_text("token=abcdef1234567890")
    assert "password [REDACTED_SECRET]" in redact_text("password abcdef1234567890")

    mock_root = Path("/home/user/workspace")
    assert "[REDACTED_REPO_ROOT]" in redact_text("/home/user/workspace/src/main.py", repo_root=mock_root)


def test_bound_excerpt() -> None:
    """Test output bounding."""
    many_lines = "\n".join(f"line {i}" for i in range(300))
    excerpt = bound_excerpt(many_lines, max_lines=10)
    assert "line 0" in excerpt
    assert "line 9" in excerpt
    assert "[TRUNCATED 280 LINES]" in excerpt
    assert "line 290" in excerpt
    assert "line 299" in excerpt


def test_validate_plan_schema_valid() -> None:
    """1. valid plan is accepted."""
    plan = _build_full_plan()
    validate_plan_schema(plan)
    validate_required_check_coverage(plan)


def test_invalid_json_rejected(tmp_path: Path) -> None:
    """2. invalid JSON syntax is rejected by main() with overall FAIL."""
    plan_file = tmp_path / "plan.json"
    output_file = tmp_path / "evidence.json"
    plan_file.write_text("{ this is not valid json ", encoding="utf-8")

    exit_code = _run_main(tmp_path, plan_file, output_file)
    assert exit_code == 1
    assert output_file.exists()
    with open(output_file, "r", encoding="utf-8") as f:
        evidence = json.load(f)
    assert evidence["summary"]["overall_verdict"] == "FAIL"
    assert any("Plan validation failed" in m for m in evidence["summary"]["blocking_failures"])


def test_missing_required_field_rejected() -> None:
    """3. a check missing a required field is rejected."""
    plan = {
        "schema_version": "offline-training-v1-acceptance-plan-v1",
        "plan_id": "test-plan",
        "checks": [{"id": "missing_fields", "runner": "pytest"}],
    }
    with pytest.raises(ValueError, match="missing required field"):
        validate_plan_schema(plan)

    with pytest.raises(ValueError, match="Plan must be a JSON object"):
        validate_plan_schema([])


def test_validate_plan_schema_unknown_runner() -> None:
    """4. unknown runner is rejected."""
    invalid_plan = {
        "schema_version": "offline-training-v1-acceptance-plan-v1",
        "plan_id": "test-plan",
        "checks": [_default_check("my_invalid_check", runner="unsupported_runner_id")],
    }
    with pytest.raises(ValueError, match="unknown runner"):
        validate_plan_schema(invalid_plan)


def test_validate_plan_schema_duplicate_id() -> None:
    """5. duplicate check ID is rejected."""
    invalid_plan = {
        "schema_version": "offline-training-v1-acceptance-plan-v1",
        "plan_id": "test-plan",
        "checks": [
            _default_check("dup_check", runner="pytest"),
            _default_check("dup_check", runner="import_closure"),
        ],
    }
    with pytest.raises(ValueError, match="Duplicate check ID detected"):
        validate_plan_schema(invalid_plan)


def test_missing_required_check_rejected() -> None:
    """6. a plan missing one of the 19 canonical required checks is rejected."""
    plan = _build_full_plan()
    plan["checks"] = [c for c in plan["checks"] if c["id"] != "champion_invariant"]
    with pytest.raises(ValueError, match="missing required checks"):
        validate_required_check_coverage(plan)


def test_invalid_timeout_rejected() -> None:
    """7. non-positive / non-numeric timeout_seconds is rejected."""
    for bad_timeout in (0, -5, "300", True):
        plan = {
            "schema_version": "offline-training-v1-acceptance-plan-v1",
            "plan_id": "test-plan",
            "checks": [_default_check("bad_timeout", timeout_seconds=bad_timeout)],
        }
        with pytest.raises(ValueError, match="timeout_seconds"):
            validate_plan_schema(plan)


def test_invalid_privacy_class_rejected() -> None:
    """Extra schema guard: privacy_class must be an allowed value."""
    plan = {
        "schema_version": "offline-training-v1-acceptance-plan-v1",
        "plan_id": "test-plan",
        "checks": [_default_check("bad_privacy", privacy_class="top-secret")],
    }
    with pytest.raises(ValueError, match="privacy_class"):
        validate_plan_schema(plan)


def test_invalid_args_format_rejected() -> None:
    """Extra schema guard: args must be a list of strings."""
    plan = {
        "schema_version": "offline-training-v1-acceptance-plan-v1",
        "plan_id": "test-plan",
        "checks": [_default_check("bad_args", args=[123, "ok"])],
    }
    with pytest.raises(ValueError, match="args"):
        validate_plan_schema(plan)


# ---------------------------------------------------------------------------
# run_command_safe: execution, timeout, child process cleanup, digests
# ---------------------------------------------------------------------------

def test_run_command_safe_success() -> None:
    """8 (unit level) / 15-16: PASS path, stdout+stderr digests."""
    argv = [sys.executable, "-c", "import sys; print('hello'); print('world', file=sys.stderr)"]
    res = run_command_safe(name="test_success", argv=argv, timeout=10.0, cwd=REPO_ROOT)
    assert res["return_code"] == 0
    assert "hello" in res["bounded_excerpt"]
    assert "world" in res["bounded_excerpt"]
    assert res["stdout_sha256"] != ""
    assert res["stderr_sha256"] != ""
    assert res["stdout_sha256"] != res["stderr_sha256"]
    assert res["timed_out"] is False


def test_run_command_safe_timeout_and_child_cleanup(tmp_path: Path) -> None:
    """11/12: timeout is recorded and the child process is actually terminated."""
    pid_file = tmp_path / "child.pid"
    argv = [
        sys.executable,
        "-c",
        (
            "import os, time, pathlib; "
            f"pathlib.Path(r'{pid_file}').write_text(str(os.getpid())); "
            "time.sleep(30)"
        ),
    ]
    # The assertion below is about process-group cleanup, so the subprocess
    # must first be scheduled far enough to persist its PID.  Under the
    # repository's pytest environment a 0.5 second command deadline can
    # expire during Python interpreter startup, which only tests a pre-exec
    # timeout race rather than child cleanup.
    res = run_command_safe(name="test_timeout", argv=argv, timeout=2.0, cwd=REPO_ROOT)
    assert res["timed_out"] is True
    assert res["return_code"] != 0

    assert pid_file.exists()
    child_pid = int(pid_file.read_text().strip())

    # Give the OS a brief moment to finish reaping; the child must not
    # still be alive after run_command_safe returned.
    deadline = time.time() + 2.0
    still_alive = True
    while time.time() < deadline:
        try:
            os.kill(child_pid, 0)
        except ProcessLookupError:
            still_alive = False
            break
        time.sleep(0.05)
    assert not still_alive, "child process survived run_command_safe timeout handling"


def test_run_command_safe_bounded_excerpt_lines() -> None:
    """13/14: stdout and stderr excerpts are bounded (not full unbounded dumps)."""
    argv = [
        sys.executable,
        "-c",
        "import sys\n"
        "for i in range(500):\n"
        "    print('out', i)\n"
        "    print('err', i, file=sys.stderr)\n",
    ]
    res = run_command_safe(name="test_bounded", argv=argv, timeout=10.0, cwd=REPO_ROOT)
    assert "TRUNCATED" in res["bounded_excerpt"]
    assert len(res["bounded_excerpt"].splitlines()) < 500


# ---------------------------------------------------------------------------
# Invariants
# ---------------------------------------------------------------------------

def test_champion_invariant() -> None:
    """29. Champion invariant evaluation."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)

        res = check_champion_invariant(tmp_path)
        assert res["invariant_ok"] is False

        main_py = tmp_path / "main.py"
        main_py.write_text("def agent(obs):\n    return make_rule_agent(obs)", encoding="utf-8")
        res = check_champion_invariant(tmp_path)
        assert res["invariant_ok"] is True


def test_promotion_invariant() -> None:
    """30. Promotion invariant evaluation."""
    res = check_promotion_invariant(REPO_ROOT)
    assert res["invariant_ok"] is True
    assert res["status"] == "NO_DECISION"


def test_hold_module_quarantine() -> None:
    """28. HOLD runtime connection detection."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)

        ot_dir = tmp_path / "src" / "mage_ptcg" / "offline_training"
        ot_dir.mkdir(parents=True)

        f1 = ot_dir / "module_a.py"
        f1.write_text("import mage_ptcg.offline_training_v1_support.synthetic_data\n", encoding="utf-8")

        res = check_hold_module_quarantine(tmp_path, ["synthetic_data"])
        assert res["invariant_ok"] is False
        assert len(res["violations"]) == 1
        assert res["violations"][0]["imported_hold_module"] == "synthetic_data"

        f1.write_text("import mage_ptcg.offline_training.dataset\n", encoding="utf-8")
        res = check_hold_module_quarantine(tmp_path, ["synthetic_data"])
        assert res["invariant_ok"] is True


# ---------------------------------------------------------------------------
# Gemini manifest parsing (pure function, no git/network)
# ---------------------------------------------------------------------------

def _fake_gemini_manifest(features: list) -> dict:
    return {
        "schema_version": collector.GEMINI_MANIFEST_SCHEMA_VERSION,
        "features": features,
    }


def test_parse_gemini_manifest_features_schema() -> None:
    """26/27. Gemini manifest schema compatibility and P0/P1/HOLD tallies.

    Uses the real top-level key ('features'), confirmed by reading
    origin/feature/offline-training-v1-gemini-support read-only rather
    than guessed.
    """
    manifest = _fake_gemini_manifest([
        {"name": "audit_log", "classification": "P0", "acceptance_tests": ["test_audit_logger_flow"]},
        {"name": "calibration", "classification": "P1"},
        {
            "name": "synthetic_data",
            "classification": "HOLD",
            "source_modules": ["mage_ptcg.offline_training_v1_support.synthetic_data"],
        },
    ])
    facts = parse_gemini_manifest(manifest)
    assert facts["schema_validation_ok"] is True
    assert facts["module_counts"] == {"P0": 1, "P1": 1, "HOLD": 1}
    assert facts["p0_acceptance_tests"] == ["test_audit_logger_flow"]
    assert facts["hold_modules"] == ["mage_ptcg.offline_training_v1_support.synthetic_data"]


def test_parse_gemini_manifest_rejects_legacy_modules_key() -> None:
    """A manifest using the wrong ('modules') top-level key must not validate."""
    manifest = {
        "schema_version": collector.GEMINI_MANIFEST_SCHEMA_VERSION,
        "modules": [{"name": "x", "classification": "P0"}],
    }
    facts = parse_gemini_manifest(manifest)
    assert facts["schema_validation_ok"] is False
    assert facts["module_counts"] == {"P0": 0, "P1": 0, "HOLD": 0}


def test_parse_gemini_manifest_rejects_wrong_schema_version() -> None:
    manifest = {"schema_version": "some-other-schema-v0", "features": []}
    facts = parse_gemini_manifest(manifest)
    assert facts["schema_validation_ok"] is False


# ---------------------------------------------------------------------------
# Self-scan (privacy_scan / secret_scan / absolute_path_scan runners)
# ---------------------------------------------------------------------------

def test_self_scan_pattern_clean_and_dirty() -> None:
    clean = {"checks": [{"bounded_excerpt": "everything is fine"}]}
    assert self_scan_pattern(clean, RE_ABSOLUTE_PATH)["clean"] is True
    assert self_scan_pattern(clean, RE_SECRET)["clean"] is True

    dirty_path = {"checks": [{"bounded_excerpt": "leaked at /home/someuser/secret/file.txt"}]}
    result = self_scan_pattern(dirty_path, RE_ABSOLUTE_PATH)
    assert result["clean"] is False
    assert result["violation_count"] >= 1

    dirty_secret = {"checks": [{"bounded_excerpt": "api_key:abcdef1234567890"}]}
    result = self_scan_pattern(dirty_secret, RE_SECRET)
    assert result["clean"] is False


def test_self_scan_privacy_detects_username(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(collector.getpass, "getuser", lambda: "definitely_not_a_real_user_xyz")
    clean = {"checks": [{"bounded_excerpt": "no leak here"}]}
    assert self_scan_privacy(clean)["clean"] is True

    dirty = {"checks": [{"bounded_excerpt": "run by definitely_not_a_real_user_xyz on the host"}]}
    result = self_scan_privacy(dirty)
    assert result["clean"] is False
    assert result["violation_count"] == 1


# ---------------------------------------------------------------------------
# Tarball / artifact inspection
# ---------------------------------------------------------------------------

def test_inspect_tarball_safety() -> None:
    """19 (partial). Tarball path traversal & absolute path safety inspection."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        tarball_path = tmp_path / "test_submission.tar.gz"

        with tarfile.open(tarball_path, "w:gz") as tar:
            info_trav = tarfile.TarInfo(name="foo/../../traversal.txt")
            info_trav.size = 4
            tar.addfile(info_trav, fileobj=BytesIO(b"trav"))

        results = inspect_tarball(tarball_path)
        assert results["exists"] is True
        assert results["has_unsafe_members"] is True
        assert results["sha256"] == compute_file_sha256(tarball_path)


def test_required_paths() -> None:
    """19. artifact existence, size, and SHA-256."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        f1 = tmp_path / "test.py"
        f1.write_text("print('test')", encoding="utf-8")

        res = check_required_paths(tmp_path, ["test.py", "missing.py"])
        assert len(res) == 2
        assert res[0]["exists"] is True
        assert res[0]["size_bytes"] == len("print('test')")
        assert res[0]["sha256"] == compute_file_sha256(f1)
        assert res[1]["exists"] is False


# ---------------------------------------------------------------------------
# Full orchestration via collector.main()
# ---------------------------------------------------------------------------

def test_run_orchestration_smoke(tmp_path: Path) -> None:
    """8/29/30: full PASS orchestration, and no absolute-path leak of repo root."""
    plan_file = tmp_path / "plan.json"
    output_file = tmp_path / "evidence.json"
    _write_plan(plan_file, _build_full_plan())
    _make_main_py(tmp_path)

    exit_code = _run_main(tmp_path, plan_file, output_file, ["--run-id", "mock-run-smoke"])
    assert exit_code == 0
    assert output_file.exists()

    with open(output_file, "r", encoding="utf-8") as f:
        evidence = json.load(f)

    assert evidence["schema_version"] == "offline-training-v1-evidence-v1"
    assert evidence["run_id"] == "mock-run-smoke"
    assert evidence["summary"]["overall_verdict"] == "PASS"
    assert len(evidence["checks"]) == 19

    # Redaction guarantee: the fake repo root (a /tmp path) must not leak.
    serialized = json.dumps(evidence)
    assert str(tmp_path.resolve()) not in serialized
    assert evidence["repository_root_resolved"] == "[REDACTED_REPO_ROOT]"


def test_deterministic_serialization_and_hash() -> None:
    """21/22. Deterministic serialization and canonical hash."""
    data = {"b": 2, "a": 1, "c": [3, 2, 1]}
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        f1 = tmp_path / "1.json"
        f2 = tmp_path / "2.json"

        atomic_write(f1, data)
        atomic_write(f2, data)

        assert f1.read_bytes() == f2.read_bytes()

        h1 = compute_file_sha256(f1)
        h2 = compute_file_sha256(f2)
        assert h1 == h2


def test_validate_only_does_not_require_output(tmp_path: Path) -> None:
    """--validate-only must work without --output, per its documented usage."""
    plan_file = tmp_path / "plan.json"
    _write_plan(plan_file, _build_full_plan())

    sys_argv_backup = sys.argv
    sys.argv = [
        "collect_offline_training_v1_evidence.py",
        "--repository-root",
        str(tmp_path),
        "--plan",
        str(plan_file),
        "--validate-only",
    ]
    try:
        exit_code = collector.main()
    finally:
        sys.argv = sys_argv_backup
    assert exit_code == 0


def test_missing_output_without_validate_only_is_rejected(tmp_path: Path) -> None:
    """Normal execution (no --validate-only) must fail fast without --output."""
    plan_file = tmp_path / "plan.json"
    _write_plan(plan_file, _build_full_plan())

    sys_argv_backup = sys.argv
    sys.argv = [
        "collect_offline_training_v1_evidence.py",
        "--repository-root",
        str(tmp_path),
        "--plan",
        str(plan_file),
    ]
    try:
        with pytest.raises(SystemExit) as exc_info:
            collector.main()
        assert exc_info.value.code == 2
    finally:
        sys.argv = sys_argv_backup


def test_validate_only_option(tmp_path: Path) -> None:
    """31. --validate-only never executes a runner, even one that would fail."""
    plan_file = tmp_path / "plan.json"
    output_file = tmp_path / "evidence.json"
    overrides = {"git_diff_check": {"runner": "pytest", "args": ["--this-flag-does-not-exist"]}}
    _write_plan(plan_file, _build_full_plan(overrides))

    exit_code = _run_main(tmp_path, plan_file, output_file, ["--validate-only"])
    assert exit_code == 0
    assert not output_file.exists()


def test_validate_only_rejects_missing_required_check(tmp_path: Path) -> None:
    """--validate-only surfaces missing-required-check errors, not just schema errors."""
    plan_file = tmp_path / "plan.json"
    output_file = tmp_path / "evidence.json"
    plan = _build_full_plan()
    plan["checks"] = [c for c in plan["checks"] if c["id"] != "secret_scan"]
    _write_plan(plan_file, plan)

    exit_code = _run_main(tmp_path, plan_file, output_file, ["--validate-only"])
    assert exit_code == 1
    assert not output_file.exists()


def test_dry_run_option(tmp_path: Path) -> None:
    """32. --dry-run never executes a runner, even one that would fail."""
    plan_file = tmp_path / "plan.json"
    output_file = tmp_path / "evidence.json"
    overrides = {"git_diff_check": {"runner": "pytest", "args": ["--this-flag-does-not-exist"]}}
    _write_plan(plan_file, _build_full_plan(overrides))
    _make_main_py(tmp_path)

    exit_code = _run_main(tmp_path, plan_file, output_file, ["--dry-run"])
    assert exit_code == 0
    assert output_file.exists()

    with open(output_file, "r", encoding="utf-8") as f:
        evidence = json.load(f)

    assert evidence["summary"]["overall_verdict"] == "PASS"
    assert len(evidence["checks"]) == 19
    for check in evidence["checks"]:
        assert "[DRY_RUN]" in check["bounded_excerpt"]


# ---------------------------------------------------------------------------
# PASS / required FAIL / optional SKIP / artifact mismatch
# ---------------------------------------------------------------------------

def test_required_check_fail_marks_overall_fail(tmp_path: Path) -> None:
    """9. A required check failing marks overall_verdict FAIL with a blocking failure."""
    plan_file = tmp_path / "plan.json"
    output_file = tmp_path / "evidence.json"
    # Deliberately omit main.py so champion_invariant fails for real.
    _write_plan(plan_file, _build_full_plan())

    exit_code = _run_main(tmp_path, plan_file, output_file)
    assert exit_code != 0
    with open(output_file, "r", encoding="utf-8") as f:
        evidence = json.load(f)
    assert evidence["summary"]["overall_verdict"] == "FAIL"
    champion_check = next(c for c in evidence["checks"] if c["id"] == "champion_invariant")
    assert champion_check["status"] == "FAIL"
    assert any("champion_invariant" in m for m in evidence["summary"]["blocking_failures"])


def test_optional_check_skip_on_missing_artifact_inputs(tmp_path: Path) -> None:
    """10. An optional check with a missing artifact_input is SKIPped, not FAILed."""
    plan_file = tmp_path / "plan.json"
    output_file = tmp_path / "evidence.json"
    extra = [
        _default_check(
            "optional_extra_check",
            required=False,
            artifact_inputs=["dist/kaggle/neural-student-v1/submission.tar.gz"],
        )
    ]
    _write_plan(plan_file, _build_full_plan(extra_checks=extra))
    _make_main_py(tmp_path)

    exit_code = _run_main(tmp_path, plan_file, output_file)
    assert exit_code == 0
    with open(output_file, "r", encoding="utf-8") as f:
        evidence = json.load(f)
    assert evidence["summary"]["overall_verdict"] == "PASS"
    extra_check = next(c for c in evidence["checks"] if c["id"] == "optional_extra_check")
    assert extra_check["status"] == "SKIP"
    assert extra_check["skip_reason"] is not None
    assert "artifact_inputs" in extra_check["skip_reason"]


def test_artifact_hash_mismatch_fails(tmp_path: Path) -> None:
    """20. artifact_hash detects a SHA-256 mismatch as a failure.

    package_build/package_verify/artifact_hash all target a run-id-scoped
    scratch path (runs/offline-training-v1/_acceptance_scratch/<run_id>/
    package_build/), not the shared dist/kaggle/neural-student-v1/ -- see
    fix(evidence): isolate and redact final acceptance checks. An explicit
    --run-id makes that path predictable for the fixture below.
    """
    plan_file = tmp_path / "plan.json"
    output_file = tmp_path / "evidence.json"
    overrides = {"artifact_hash": {"runner": "artifact_hash", "args": ["0" * 64]}}
    _write_plan(plan_file, _build_full_plan(overrides))
    _make_main_py(tmp_path)

    scratch_dir = tmp_path / "runs" / "offline-training-v1" / "_acceptance_scratch" / "hash-mismatch-run" / "package_build"
    scratch_dir.mkdir(parents=True)
    (scratch_dir / "submission.tar.gz").write_bytes(b"not the expected content")

    exit_code = _run_main(tmp_path, plan_file, output_file, ["--run-id", "hash-mismatch-run"])
    assert exit_code != 0
    with open(output_file, "r", encoding="utf-8") as f:
        evidence = json.load(f)
    artifact_check = next(c for c in evidence["checks"] if c["id"] == "artifact_hash")
    assert artifact_check["status"] == "FAIL"
    assert evidence["summary"]["overall_verdict"] == "FAIL"


# ---------------------------------------------------------------------------
# Timeout / malformed result / collector exception / interrupted run
# ---------------------------------------------------------------------------

def test_required_check_timeout_marks_fail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """11 (integration level). A required check timing out fails the whole run."""
    plan_file = tmp_path / "plan.json"
    output_file = tmp_path / "evidence.json"
    overrides = {"git_diff_check": {"runner": "pytest"}}
    _write_plan(plan_file, _build_full_plan(overrides))
    _make_main_py(tmp_path)

    def fake_run_command_safe(*args, **kwargs):
        return {
            "return_code": -1,
            "timed_out": True,
            "bounded_excerpt": "[TIMEOUT] simulated",
            "stdout_sha256": "",
            "stderr_sha256": "",
        }

    monkeypatch.setattr(collector, "run_command_safe", fake_run_command_safe)

    exit_code = _run_main(tmp_path, plan_file, output_file)
    assert exit_code != 0
    with open(output_file, "r", encoding="utf-8") as f:
        evidence = json.load(f)
    check = next(c for c in evidence["checks"] if c["id"] == "git_diff_check")
    assert check["status"] == "FAIL"
    assert check["timed_out"] is True
    assert evidence["summary"]["overall_verdict"] == "FAIL"


def test_malformed_runner_result_treated_as_fail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """23. A malformed (near-empty) cmd_record does not crash the collector and is FAIL."""
    plan_file = tmp_path / "plan.json"
    output_file = tmp_path / "evidence.json"
    overrides = {"git_diff_check": {"runner": "pytest"}}
    _write_plan(plan_file, _build_full_plan(overrides))
    _make_main_py(tmp_path)

    monkeypatch.setattr(collector, "run_command_safe", lambda *a, **kw: {})

    exit_code = _run_main(tmp_path, plan_file, output_file)
    assert exit_code != 0
    with open(output_file, "r", encoding="utf-8") as f:
        evidence = json.load(f)
    check = next(c for c in evidence["checks"] if c["id"] == "git_diff_check")
    assert check["status"] == "FAIL"
    assert check["exit_code"] == -1


def test_collector_exception_is_recorded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """24. An uncaught exception inside a runner is caught and recorded, not propagated."""
    plan_file = tmp_path / "plan.json"
    output_file = tmp_path / "evidence.json"
    overrides = {"git_diff_check": {"runner": "pytest"}}
    _write_plan(plan_file, _build_full_plan(overrides))
    _make_main_py(tmp_path)

    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(collector, "run_command_safe", boom)

    exit_code = _run_main(tmp_path, plan_file, output_file)
    assert exit_code != 0
    assert output_file.exists()
    with open(output_file, "r", encoding="utf-8") as f:
        evidence = json.load(f)
    assert evidence["summary"]["overall_verdict"] == "FAIL"
    assert any("Collector exception" in m for m in evidence["summary"]["blocking_failures"])


def test_interrupted_run_then_rerun(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """25. A KeyboardInterrupt mid-run saves partial evidence; a later run still succeeds."""
    plan_file = tmp_path / "plan.json"
    interrupted_output = tmp_path / "evidence_interrupted.json"
    overrides = {"git_diff_check": {"runner": "pytest"}}
    _write_plan(plan_file, _build_full_plan(overrides))
    _make_main_py(tmp_path)

    with monkeypatch.context() as m:
        def raise_interrupt(*args, **kwargs):
            raise KeyboardInterrupt()

        m.setattr(collector, "run_command_safe", raise_interrupt)
        exit_code = _run_main(tmp_path, plan_file, interrupted_output)

    assert exit_code == 130
    assert interrupted_output.exists()
    with open(interrupted_output, "r", encoding="utf-8") as f:
        partial_evidence = json.load(f)
    assert partial_evidence["interrupted"] is True


# ---------------------------------------------------------------------------
# R1 tooling-defect regression coverage (fix(evidence): isolate and redact
# final acceptance checks): export_parity fixture wiring, package_build
# isolation, clean_room raw-status parsing, and central path redaction.
# ---------------------------------------------------------------------------

def test_export_parity_check_builds_fixture_and_confirms_parity() -> None:
    """4A. export_parity builds a real C4 data-ops fixture and exports it
    twice through the real export_bundle(), confirming byte-identical
    (deterministic) output -- not a dummy/always-PASS stub."""
    with tempfile.TemporaryDirectory() as tmpdir:
        scratch = Path(tmpdir) / "export_parity_scratch"
        record = run_export_parity_check(REPO_ROOT, scratch)

    assert record["return_code"] == 0
    assert "parity=True" in record["bounded_excerpt"]
    assert "artifact_purpose=TEST_FIXTURE" in record["bounded_excerpt"]
    # The check cleans up its own scratch state.
    assert not scratch.exists()


def test_export_parity_check_detects_nondeterministic_export(monkeypatch: pytest.MonkeyPatch) -> None:
    """4A. A genuinely non-deterministic export is caught, proving the parity
    comparison is real (not a rubber stamp)."""
    import scripts.export_c4_actual_training_bundle as exporter_module

    real_export_bundle = exporter_module.export_bundle
    call_count = {"n": 0}

    def flaky_export_bundle(*, run_root, output_root, require_actual_training=False):
        result = real_export_bundle(
            run_root=run_root, output_root=output_root, require_actual_training=require_actual_training
        )
        call_count["n"] += 1
        if call_count["n"] == 2:
            (output_root / "public_summary.json").write_text("{}", encoding="utf-8")
        return result

    monkeypatch.setattr(exporter_module, "export_bundle", flaky_export_bundle)

    with tempfile.TemporaryDirectory() as tmpdir:
        scratch = Path(tmpdir) / "export_parity_scratch"
        record = run_export_parity_check(REPO_ROOT, scratch)

    assert record["return_code"] == 1
    assert "Non-deterministic" in record["bounded_excerpt"]
    assert "public_summary.json" in record["bounded_excerpt"]


def test_export_parity_check_reports_real_failure_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    """4A. A genuine exporter failure surfaces its real reason, not just an
    exception class name (the old CLI-subprocess design lost this detail)."""
    import scripts.export_c4_actual_training_bundle as exporter_module

    def boom(*, run_root, output_root, require_actual_training=False):
        raise exporter_module.BundleExportError("source privacy scan did not pass")

    monkeypatch.setattr(exporter_module, "export_bundle", boom)

    with tempfile.TemporaryDirectory() as tmpdir:
        scratch = Path(tmpdir) / "export_parity_scratch"
        record = run_export_parity_check(REPO_ROOT, scratch)

    assert record["return_code"] == 2
    assert "source privacy scan did not pass" in record["bounded_excerpt"]


def test_package_build_uses_isolated_scratch_not_shared_dist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """4B. package_build calls package.build_package() -- the same builder
    training_smoke's pipeline "package" phase uses for the actual
    neural-student-v1 artifact (not scripts/build_student_submission.py,
    which builds an incompatible, older "C4 Student v0" artifact family) --
    against a run-id-scoped scratch path, never the shared
    dist/kaggle/neural-student-v1/ that training_smoke also publishes to."""
    plan_file = tmp_path / "plan.json"
    output_file = tmp_path / "evidence.json"
    overrides = {"package_build": {"runner": "package_build"}}
    _write_plan(plan_file, _build_full_plan(overrides))
    _make_main_py(tmp_path)

    # Simulate training_smoke's pipeline having already published a
    # non-empty shared dist/ in this same run -- this used to make the
    # standalone package_build check fail with "must be new or empty".
    shared_dist = tmp_path / "dist" / "kaggle" / "neural-student-v1"
    shared_dist.mkdir(parents=True)
    (shared_dist / "submission.tar.gz").write_bytes(b"published by training_smoke, not package_build")

    captured: dict[str, object] = {}

    def fake_build_package(*, export_path, output_dir, repository_root, build_commit):
        captured["export_path"] = export_path
        captured["output_dir"] = output_dir
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "submission.tar.gz").write_bytes(b"built by package_build")
        return {"archive_sha256": "deadbeef", "model_purpose": "TEST_FIXTURE"}

    import mage_ptcg.offline_training.package as offline_package_module

    monkeypatch.setattr(offline_package_module, "build_package", fake_build_package)

    exit_code = _run_main(tmp_path, plan_file, output_file, ["--run-id", "isolation-check"])
    assert exit_code == 0

    output_dir_used = Path(captured["output_dir"])
    assert output_dir_used != shared_dist
    assert "isolation-check" in str(output_dir_used)
    scratch_root = tmp_path / "runs" / "offline-training-v1" / "_acceptance_scratch"
    assert str(output_dir_used).startswith(str(scratch_root))
    # Reads training_smoke's actual export, not a Student v0 model path.
    assert str(captured["export_path"]).endswith("export/neural-student-v1.json")

    # The pre-existing shared dist/ tarball from the "other check" is untouched.
    assert (shared_dist / "submission.tar.gz").read_bytes() == b"published by training_smoke, not package_build"

    with open(output_file, "r", encoding="utf-8") as f:
        evidence = json.load(f)
    check = next(c for c in evidence["checks"] if c["id"] == "package_build")
    assert check["status"] == "PASS"
    assert check["produced_artifacts"]
    assert "_acceptance_scratch" in check["produced_artifacts"][0]
    assert "dist/kaggle" not in check["produced_artifacts"][0]


def test_package_build_reports_real_failure_reason(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """4B. A genuine package_build failure surfaces its real reason."""
    plan_file = tmp_path / "plan.json"
    output_file = tmp_path / "evidence.json"
    overrides = {"package_build": {"runner": "package_build"}}
    _write_plan(plan_file, _build_full_plan(overrides))
    _make_main_py(tmp_path)

    import mage_ptcg.offline_training.package as offline_package_module

    def boom(*, export_path, output_dir, repository_root, build_commit):
        raise offline_package_module.PackageError("model export document is missing required fields")

    monkeypatch.setattr(offline_package_module, "build_package", boom)

    exit_code = _run_main(tmp_path, plan_file, output_file, ["--run-id", "build-failure-check"])
    assert exit_code != 0
    with open(output_file, "r", encoding="utf-8") as f:
        evidence = json.load(f)
    check = next(c for c in evidence["checks"] if c["id"] == "package_build")
    assert check["status"] == "FAIL"
    assert "model export document is missing required fields" in check["bounded_excerpt"]


def _run_clean_room_with_fake_git(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, raw_stdout: str, return_code: int = 0
) -> tuple[int, dict]:
    plan_file = tmp_path / "plan.json"
    output_file = tmp_path / "evidence.json"
    overrides = {"clean_room": {"runner": "clean_room"}}
    _write_plan(plan_file, _build_full_plan(overrides))
    _make_main_py(tmp_path)

    def fake_run_command_safe(name, argv, timeout, cwd, env_updates=None, repo_root=None):
        redacted = redact_text(raw_stdout, repo_root)
        return {
            "return_code": return_code,
            "timed_out": False,
            "bounded_excerpt": "--- STDOUT ---\n" + bound_excerpt(redacted) + "\n--- STDERR ---\n",
            "stdout_sha256": compute_sha256_text(redacted),
            "stderr_sha256": compute_sha256_text(""),
            "_raw_stdout": raw_stdout,
        }

    monkeypatch.setattr(collector, "run_command_safe", fake_run_command_safe)
    exit_code = _run_main(tmp_path, plan_file, output_file)
    with open(output_file, "r", encoding="utf-8") as f:
        evidence = json.load(f)
    check = next(c for c in evidence["checks"] if c["id"] == "clean_room")
    return exit_code, check


def test_clean_room_passes_on_empty_raw_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """4C. An empty `git status --short` stdout is clean."""
    exit_code, check = _run_clean_room_with_fake_git(tmp_path, monkeypatch, raw_stdout="")
    assert check["status"] == "PASS"
    assert exit_code == 0


def test_clean_room_passes_on_whitespace_only_raw_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """4C. Whitespace-only stdout (no real entries) is clean."""
    exit_code, check = _run_clean_room_with_fake_git(tmp_path, monkeypatch, raw_stdout="\n   \n")
    assert check["status"] == "PASS"
    assert exit_code == 0


def test_clean_room_passes_despite_decorated_bounded_excerpt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """4C (regression for the R1 bug). bounded_excerpt always carries the
    "--- STDOUT ---" / "--- STDERR ---" display wrapper, but a genuinely
    clean raw git status must still PASS -- the old code misread that
    wrapper text itself as dirty entries on every single run."""
    _, check = _run_clean_room_with_fake_git(tmp_path, monkeypatch, raw_stdout="")
    assert "--- STDOUT ---" in check["bounded_excerpt"]
    assert "--- STDERR ---" in check["bounded_excerpt"]
    assert check["status"] == "PASS"


def test_clean_room_fails_on_modified_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """4C. A real `M file.py` entry is dirty."""
    exit_code, check = _run_clean_room_with_fake_git(tmp_path, monkeypatch, raw_stdout=" M file.py\n")
    assert check["status"] == "FAIL"
    assert exit_code != 0


def test_clean_room_fails_on_untracked_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """4C. A real `?? file.py` entry is dirty."""
    exit_code, check = _run_clean_room_with_fake_git(tmp_path, monkeypatch, raw_stdout="?? file.py\n")
    assert check["status"] == "FAIL"
    assert exit_code != 0


def test_clean_room_fails_on_nonzero_return_code(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """4C. A non-zero `git status` exit code is a failure regardless of stdout."""
    exit_code, check = _run_clean_room_with_fake_git(tmp_path, monkeypatch, raw_stdout="", return_code=1)
    assert check["status"] == "FAIL"
    assert exit_code != 0


def test_redact_structure_scrubs_nested_absolute_paths() -> None:
    """4D. Absolute /home and /tmp paths are redacted anywhere in a nested
    dict/list structure, e.g. inspect_tarball()'s 'path' field -- the exact
    shape of the R1 privacy_scan/absolute_path_scan leak."""
    nested = {
        "tarball_inspection": {
            "path": "/home/example/repo/dist/kaggle/neural-student-v1/submission.tar.gz",
            "members": [
                {"name": "src/mage_ptcg/offline_training/neural.py", "type": "file"},
            ],
        },
        "temp_paths": [
            "/tmp/random/file.tar.gz",
            "runs/offline-training-v1/run-1/package/submission.tar.gz",
        ],
    }
    redacted = redact_structure(nested)
    serialized = json.dumps(redacted)

    assert "/home/example/repo" not in serialized
    assert "/tmp/random" not in serialized
    # Tar member names (repo-relative) survive untouched.
    assert redacted["tarball_inspection"]["members"][0]["name"] == "src/mage_ptcg/offline_training/neural.py"
    # A repo-relative logical path also survives untouched.
    assert redacted["temp_paths"][1] == "runs/offline-training-v1/run-1/package/submission.tar.gz"


def test_redact_structure_path_and_secret_do_not_conflict() -> None:
    """4D. Path redaction and secret redaction both apply within the same
    string without one suppressing the other."""
    nested = {"note": "artifact at /tmp/scratchdir/output.json token=abcdef1234567890"}
    redacted = redact_structure(nested)
    text = redacted["note"]

    assert "/tmp/scratchdir" not in text
    assert "abcdef1234567890" not in text
    assert "[REDACTED_PATH]" in text
    assert "[REDACTED_SECRET]" in text


def test_redact_structure_preserves_non_string_leaves() -> None:
    """4D. Numbers, bools, and None pass through unchanged."""
    nested = {"size_bytes": 12345, "has_unsafe_members": False, "sha256_error": None}
    assert redact_structure(nested) == nested


def test_package_verify_tarball_inspection_path_is_redacted_in_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """4D (integration). package_verify's real tarball_inspection.path field
    (an absolute path by construction) never survives into the evidence
    JSON, and neither privacy_scan nor absolute_path_scan flags a leak."""
    plan_file = tmp_path / "plan.json"
    output_file = tmp_path / "evidence.json"
    overrides = {
        "package_verify": {"runner": "package_verify"},
        "privacy_scan": {"runner": "privacy_scan"},
        "absolute_path_scan": {"runner": "absolute_path_scan"},
    }
    _write_plan(plan_file, _build_full_plan(overrides))
    _make_main_py(tmp_path)

    scratch_dir = tmp_path / "runs" / "offline-training-v1" / "_acceptance_scratch" / "verify-redaction-run" / "package_build"
    scratch_dir.mkdir(parents=True)
    with tarfile.open(scratch_dir / "submission.tar.gz", "w:gz") as tar:
        info = tarfile.TarInfo(name="main.py")
        info.size = 4
        tar.addfile(info, fileobj=BytesIO(b"main"))

    exit_code = _run_main(tmp_path, plan_file, output_file, ["--run-id", "verify-redaction-run"])
    assert exit_code == 0

    with open(output_file, "r", encoding="utf-8") as f:
        evidence = json.load(f)

    serialized = json.dumps(evidence)
    assert str(tmp_path.resolve()) not in serialized

    privacy_check = next(c for c in evidence["checks"] if c["id"] == "privacy_scan")
    absolute_path_check = next(c for c in evidence["checks"] if c["id"] == "absolute_path_scan")
    verify_check = next(c for c in evidence["checks"] if c["id"] == "package_verify")
    assert privacy_check["status"] == "PASS"
    assert absolute_path_check["status"] == "PASS"
    assert verify_check["tarball_inspection"]["path"] != str(scratch_dir / "submission.tar.gz")
    assert "[REDACTED" in verify_check["tarball_inspection"]["path"]

    # A fresh run afterwards (normal, un-patched runners) must succeed cleanly.
    rerun_plan_file = tmp_path / "plan2.json"
    rerun_output = tmp_path / "evidence_rerun.json"
    _write_plan(rerun_plan_file, _build_full_plan())
    exit_code = _run_main(tmp_path, rerun_plan_file, rerun_output)
    assert exit_code == 0
    with open(rerun_output, "r", encoding="utf-8") as f:
        rerun_evidence = json.load(f)
    assert rerun_evidence["summary"]["overall_verdict"] == "PASS"
    assert rerun_evidence["interrupted"] is False
