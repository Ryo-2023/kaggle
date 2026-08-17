"""Regression tests for the Kaggle submission Verifier's readiness computation.

``compute_readiness`` and ``local_import_closure`` are pure functions over
already-collected facts (see scripts/verify_kaggle_submission.py), so every
blocker condition can be exercised with hand-crafted data below without
re-running kaggle_environments games or the 100-game stress preflight.
"""
from __future__ import annotations

from pathlib import Path
import hashlib
import json
import os
import shutil
import subprocess
import sys

import pytest

import scripts.verify_kaggle_submission as verifier
import scripts.verify_kaggle_submission_candidate as candidate_verifier
from scripts.verify_kaggle_submission import compute_readiness, local_import_closure

_EXPECTED_HASH = "a" * 64


def _gate_python311() -> Path:
    configured = os.environ.get("KAGGLE_GATE_PYTHON")
    candidates = [
        Path(configured) if configured else None,
        Path("/tmp/kaggle-validation-v2-py311/bin/python"),
        Path(sys.executable) if sys.version_info[:2] == (3, 11) else None,
    ]
    for candidate in candidates:
        if candidate is not None and candidate.is_file():
            return candidate
    pytest.skip("Python 3.11 + kaggle-environments 1.32.0 gate runtime is unavailable")


@pytest.fixture
def short_real_gate(monkeypatch):
    """Keep unmocked regression fixtures short; the formal gate still requires 20."""
    monkeypatch.setattr(candidate_verifier, "MINIMUM_SMOKE_GAMES", 2)


def _passing_gate(**overrides: object) -> dict:
    base = {
        "status": "PASS",
        "model_loaded": True,
        "model_hash": _EXPECTED_HASH,
        "fallback_count": 0,
        "invalid_count": 0,
        "crash_count": 0,
        "timeout_count": 0,
        "statuses": ["DONE", "DONE"],
    }
    base.update(overrides)
    return base


def _passing_stress(stress_games: int = 100, **overrides: object) -> dict:
    base = {
        "status": "PASS",
        "total_games": stress_games,
        "terminal_errors": 0,
        "all_statuses_done": True,
        "fresh_model_hashes": [_EXPECTED_HASH],
        "aggregated_telemetry": {
            "inference_requested": 400,
            "inference_completed": 400,
            "student_selection_count": 200,
            "fallback_count": 0,
            "invalid_count": 0,
            "crash_count": 0,
            "timeout_count": 0,
            "legal_decision_count": 400,
            "legal_action_count": 400,
        },
    }
    base.update(overrides)
    return base


def _passing_check(stress_games: int = 100) -> dict:
    return {
        "archive_only_actual_cabt": {
            "gate_a": {"status": "PASS"},
            "gate_b": _passing_gate(),
            "gate_c0": _passing_gate(),
            "gate_c1": _passing_gate(),
            "stress": _passing_stress(stress_games),
            "local_import_closure": {"status": "PASS", "missing_required": [], "allowed_optional": []},
        },
        "privacy_violations": 0,
    }


def _passing_manifest() -> dict:
    return {"agent_kind": "student", "model_hash": _EXPECTED_HASH}


def test_all_conditions_pass_yields_ready_with_empty_blockers() -> None:
    """11: 全条件PASSの場合だけblockersが空。"""
    readiness, blockers = compute_readiness(_passing_check(), _passing_manifest(), 100)
    assert readiness == "READY_TO_SUBMIT"
    assert blockers == []


def test_model_hash_none_blocks_readiness() -> None:
    """11: model hashがNoneならREADYにならない。"""
    check = _passing_check()
    check["archive_only_actual_cabt"]["gate_c0"]["model_hash"] = None
    readiness, blockers = compute_readiness(check, _passing_manifest(), 100)
    assert readiness == "PREFLIGHT_ONLY"
    assert "gate_c0_model_hash_missing" in blockers


def test_model_hash_mismatch_blocks_readiness() -> None:
    """11: model hash不一致ならREADYにならない。"""
    check = _passing_check()
    check["archive_only_actual_cabt"]["gate_c1"]["model_hash"] = "b" * 64
    readiness, blockers = compute_readiness(check, _passing_manifest(), 100)
    assert readiness == "PREFLIGHT_ONLY"
    assert "gate_c1_model_hash_mismatch" in blockers


def test_expected_model_hash_unavailable_blocks_readiness() -> None:
    manifest = _passing_manifest()
    manifest["model_hash"] = None
    readiness, blockers = compute_readiness(_passing_check(), manifest, 100)
    assert readiness == "PREFLIGHT_ONLY"
    assert "expected_model_hash_unavailable" in blockers


def test_legal_action_rate_below_one_blocks_readiness() -> None:
    """11: legal action rateが1未満ならREADYにならない。"""
    check = _passing_check()
    check["archive_only_actual_cabt"]["stress"]["aggregated_telemetry"]["legal_decision_count"] = 399
    readiness, blockers = compute_readiness(check, _passing_manifest(), 100)
    assert readiness == "PREFLIGHT_ONLY"
    assert "legal_decision_count_mismatch" in blockers
    assert "legal_action_rate_not_1" in blockers


def test_inference_requested_zero_blocks_readiness() -> None:
    """11: inference_requestedが0ならREADYにならない。"""
    check = _passing_check()
    agg = check["archive_only_actual_cabt"]["stress"]["aggregated_telemetry"]
    agg["inference_requested"] = 0
    agg["legal_decision_count"] = 0
    readiness, blockers = compute_readiness(check, _passing_manifest(), 100)
    assert readiness == "PREFLIGHT_ONLY"
    assert "inference_requested_not_positive" in blockers


def test_missing_required_local_import_blocks_readiness() -> None:
    """11: required import欠損があればREADYにならない。"""
    check = _passing_check()
    check["archive_only_actual_cabt"]["local_import_closure"] = {
        "status": "FAIL",
        "missing_required": [{"source": "runtime_main.py", "module": "mage_ptcg.knowledge"}],
        "allowed_optional": [],
    }
    readiness, blockers = compute_readiness(check, _passing_manifest(), 100)
    assert readiness == "PREFLIGHT_ONLY"
    assert "local_import_closure_not_PASS" in blockers


def test_stress_total_games_99_blocks_readiness() -> None:
    """11: stress total gamesが99ならREADYにならない。"""
    check = _passing_check()
    check["archive_only_actual_cabt"]["stress"]["total_games"] = 99
    readiness, blockers = compute_readiness(check, _passing_manifest(), 100)
    assert readiness == "PREFLIGHT_ONLY"
    assert "stress_total_games_mismatch" in blockers


def test_stress_terminal_error_blocks_readiness() -> None:
    """11: terminal errorが1ならREADYにならない。"""
    check = _passing_check()
    check["archive_only_actual_cabt"]["stress"]["terminal_errors"] = 1
    readiness, blockers = compute_readiness(check, _passing_manifest(), 100)
    assert readiness == "PREFLIGHT_ONLY"
    assert "stress_terminal_errors_nonzero" in blockers


def test_stress_timeout_blocks_readiness() -> None:
    """11: timeoutが1ならREADYにならない。"""
    check = _passing_check()
    check["archive_only_actual_cabt"]["stress"]["aggregated_telemetry"]["timeout_count"] = 1
    readiness, blockers = compute_readiness(check, _passing_manifest(), 100)
    assert readiness == "PREFLIGHT_ONLY"
    assert "stress_timeout_count_nonzero" in blockers


def test_bad_game_status_blocks_readiness() -> None:
    """11: statusにERROR/INVALID/TIMEOUTがあればREADYにならない。"""
    check = _passing_check()
    check["archive_only_actual_cabt"]["gate_b"]["statuses"] = ["DONE", "ERROR"]
    check["archive_only_actual_cabt"]["stress"]["all_statuses_done"] = False
    readiness, blockers = compute_readiness(check, _passing_manifest(), 100)
    assert readiness == "PREFLIGHT_ONLY"
    assert "gate_b_statuses_not_DONE" in blockers
    assert "stress_statuses_not_all_DONE" in blockers


def test_stress_games_below_100_is_preflight_only() -> None:
    """11: stress-gamesが100以外ならPREFLIGHT_ONLY(Gate結果自体は独立に判定できる)。"""
    readiness, blockers = compute_readiness(_passing_check(stress_games=0), _passing_manifest(), 0)
    assert readiness == "PREFLIGHT_ONLY"
    assert "stress_games_below_100" in blockers
    assert "gate_a_status_not_PASS" not in blockers
    assert "gate_c0_model_hash_missing" not in blockers


def test_non_student_agent_kind_is_ready_without_cabt_checks() -> None:
    readiness, blockers = compute_readiness({}, {"agent_kind": "rule"}, 0)
    assert readiness == "READY_TO_SUBMIT"
    assert blockers == []


def test_cg_agent_kind_requires_remote_contract_confirmation() -> None:
    readiness, blockers = compute_readiness(
        {"status": "PASS"},
        {"agent_kind": "cg", "contract": {"submission_method": "UNKNOWN"}},
        0,
    )
    assert readiness == "PREFLIGHT_ONLY"
    assert blockers == ["remote_contract_confirmation_required"]


def test_missing_cabt_report_blocks_readiness() -> None:
    readiness, blockers = compute_readiness({}, _passing_manifest(), 100)
    assert readiness == "PREFLIGHT_ONLY"
    assert blockers == ["archive_only_actual_cabt_missing"]


def _write_kaggle_wrapper_fixture(root: Path) -> dict[str, object]:
    root.mkdir()
    deck = b"1\n" * 60
    (root / "deck.csv").write_bytes(deck)
    inner_manifest: dict[str, object] = {}
    (root / "manifest.json").write_text(
        json.dumps(inner_manifest, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    manifest: dict[str, object] = {
        "schema_version": "kaggle-agent-package-v1",
        "agent_kind": "student",
        "competition_slug": "pokemon-tcg-ai-battle",
        "entrypoint": "main.py",
        "deck_hash": hashlib.sha256(deck).hexdigest(),
        "source_head": "0" * 40,
        "private_artifacts_included": False,
        "contract": {
            "submission_method": "UNKNOWN",
            "archive_type": "UNKNOWN",
            "entrypoint": "main.py",
        },
        "model_hash": _EXPECTED_HASH,
        "artifact_purpose": "ACTUAL_TRAINED",
        "performance_eligible": True,
        "fallback_policy": "Rule Agent v0",
        "builder_result": inner_manifest,
    }
    (root / "kaggle-package-manifest.json").write_text(
        json.dumps(manifest, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def _write_cg_wrapper_fixture(root: Path) -> dict[str, object]:
    root.mkdir()
    deck = b"1\n" * 60
    (root / "deck.csv").write_bytes(deck)
    runtime_members = (
        "cg/__init__.py",
        "cg/api.py",
        "cg/libcg.so",
        "cg/sim.py",
        "cg/utils.py",
        "deck.csv",
        "main.py",
    )
    files: dict[str, dict[str, object]] = {}
    for name in runtime_members:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if name == "deck.csv":
            data = deck
        else:
            data = b"x"
        path.write_bytes(data)
        files[name] = {
            "sha256": hashlib.sha256(data).hexdigest(),
            "size": len(data),
        }
    inner_manifest: dict[str, object] = {
        "schema_version": "meta-specialist-root-cg-policy-screen-v1-test",
        "archive": {"path": "submission.tar.gz", "sha256": "a" * 64},
        "files": files,
        "deck_sha256": hashlib.sha256(deck).hexdigest(),
        "source_deck_sha256": hashlib.sha256(deck).hexdigest(),
        "policy_source_sha256": hashlib.sha256(b"x").hexdigest(),
    }
    (root / "manifest.json").write_text(
        json.dumps(inner_manifest, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (root / "submission.tar.gz").write_bytes(b"archive")
    manifest: dict[str, object] = {
        "schema_version": "kaggle-agent-package-v1",
        "agent_kind": "cg",
        "competition_slug": "pokemon-tcg-ai-battle",
        "entrypoint": "main.py",
        "deck_hash": hashlib.sha256(deck).hexdigest(),
        "source_head": "0" * 40,
        "private_artifacts_included": False,
        "contract": {
            "submission_method": "UNKNOWN",
            "archive_type": "UNKNOWN",
            "entrypoint": "main.py",
        },
        "builder_result": inner_manifest,
    }
    (root / "kaggle-package-manifest.json").write_text(
        json.dumps(manifest, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def test_cg_wrapper_accepts_current_candidate_manifest_shape(tmp_path: Path) -> None:
    root = tmp_path / "cg-package"
    _write_cg_wrapper_fixture(root)

    manifest, snapshot = verifier._load_kaggle_package_manifest_snapshot(root)

    assert manifest["agent_kind"] == "cg"
    assert snapshot.member_bytes("cg/api.py") == b"x"
    assert snapshot.member_bytes("submission.tar.gz") == b"archive"


def test_cg_wrapper_main_dispatches_to_cg_runtime_adapter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "cg-package"
    _write_cg_wrapper_fixture(root)
    monkeypatch.setattr(
        verifier,
        "_verify_cg_runtime_artifact",
        lambda _root, _manifest: {"status": "PASS", "runtime": "cg"},
    )

    assert verifier.main(["--artifact", str(root)]) == 0

    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "PASS"
    assert result["check"]["status"] == "PASS"
    assert result["readiness"] == "PREFLIGHT_ONLY"
    assert result["readiness_blockers"] == ["remote_contract_confirmation_required"]


def test_kaggle_wrapper_manifest_requires_its_exact_top_level_schema(
    tmp_path: Path,
) -> None:
    root = tmp_path / "package"
    manifest = _write_kaggle_wrapper_fixture(root)
    manifest["unexpected"] = "not-allowlisted"
    (root / "kaggle-package-manifest.json").write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="top-level fields"):
        verifier._load_kaggle_package_manifest(root)


@pytest.mark.parametrize(
    "suffix",
    (
        ',"agent_kind":"student"}',
        ',"diagnostic":NaN}',
    ),
)
def test_kaggle_wrapper_manifest_requires_strict_json(
    tmp_path: Path,
    suffix: str,
) -> None:
    root = tmp_path / "package"
    _write_kaggle_wrapper_fixture(root)
    path = root / "kaggle-package-manifest.json"
    raw = path.read_text(encoding="utf-8").strip()
    path.write_text(raw[:-1] + suffix, encoding="utf-8")

    with pytest.raises(ValueError, match="strict JSON"):
        verifier._load_kaggle_package_manifest(root)


def test_kaggle_wrapper_manifest_rejects_private_locator_fields(
    tmp_path: Path,
) -> None:
    root = tmp_path / "package"
    manifest = _write_kaggle_wrapper_fixture(root)
    manifest["public_audit"] = {"optionIndex": 7}
    (root / "kaggle-package-manifest.json").write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="local-only field"):
        verifier._load_kaggle_package_manifest(root)


@pytest.mark.parametrize("field", ("deck_hash", "model_hash"))
def test_kaggle_wrapper_manifest_requires_lowercase_sha256_fields(
    tmp_path: Path,
    field: str,
) -> None:
    root = tmp_path / "package"
    manifest = _write_kaggle_wrapper_fixture(root)
    manifest[field] = str(manifest[field]).upper()
    (root / "kaggle-package-manifest.json").write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=field):
        verifier._load_kaggle_package_manifest(root)


def test_kaggle_wrapper_manifest_binds_deck_and_inner_manifest(
    tmp_path: Path,
) -> None:
    root = tmp_path / "package"
    manifest = _write_kaggle_wrapper_fixture(root)
    manifest["deck_hash"] = "b" * 64
    manifest["builder_result"] = {"tampered": True}
    (root / "kaggle-package-manifest.json").write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="deck_hash does not match"):
        verifier._load_kaggle_package_manifest(root)

    manifest["deck_hash"] = hashlib.sha256((root / "deck.csv").read_bytes()).hexdigest()
    (root / "kaggle-package-manifest.json").write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="builder_result does not match"):
        verifier._load_kaggle_package_manifest(root)


def _build_real_student_wrapper_package(tmp_path: Path) -> Path:
    from mage_ptcg.student.artifact import (
        ARTIFACT_SCHEMA_VERSION,
        MODEL_FORMAT,
        feature_schema,
        sha256_file,
    )
    from mage_ptcg.student.model import (
        MODEL_FEATURE_DIM,
        MODEL_SCHEMA_VERSION,
        StudentV0Model,
    )
    from scripts.build_student_submission import (
        KAGGLE_STUDENT_RUNTIME_PATHS,
        build_student_submission,
    )
    from scripts.kaggle_student_entrypoint import (
        render_student_cabt_trace,
        render_student_entrypoint,
        render_student_package_init,
        render_student_runtime_model,
    )

    artifact = tmp_path / "model-artifact"
    artifact.mkdir()
    model_path = artifact / "student-v0.json"
    model = StudentV0Model((0.0,) * MODEL_FEATURE_DIM)
    model.export(model_path)
    model_manifest = {
        "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
        "artifact_type": "C4_STUDENT_MODEL",
        "artifact_purpose": "ACTUAL_TRAINED",
        "performance_eligible": True,
        "model_format": MODEL_FORMAT,
        "model_version": MODEL_SCHEMA_VERSION,
        "feature_domain": model.feature_domain,
        "model_hash": sha256_file(model_path),
        "model_size_bytes": model_path.stat().st_size,
        **feature_schema(model.feature_domain),
        "privacy_scan_executed": True,
        "privacy_violations": 0,
    }
    artifact_manifest_bytes = (
        json.dumps(model_manifest, sort_keys=True) + "\n"
    ).encode("utf-8")
    embedded_manifest = {
        "schema_version": "kaggle-student-package-model-v1",
        "agent_kind": "student",
        "entrypoint": "main.py",
        "model_hash": model_manifest["model_hash"],
        "artifact_purpose": "ACTUAL_TRAINED",
        "performance_eligible": True,
        "fallback_policy": "Rule Agent v0",
    }
    source_root = Path(__file__).resolve().parents[1] / "src" / "mage_ptcg"
    package = tmp_path / "package"
    builder_result = build_student_submission(
        model_path,
        package,
        runtime_paths=KAGGLE_STUDENT_RUNTIME_PATHS,
        generated_main=render_student_entrypoint().encode("utf-8"),
        generated_files={
            "src/mage_ptcg/student/__init__.py": render_student_package_init().encode(
                "utf-8"
            ),
            "src/mage_ptcg/student/model.py": render_student_runtime_model(
                (source_root / "student" / "model.py").read_text(encoding="utf-8")
            ).encode("utf-8"),
            "src/mage_ptcg/observability/cabt_trace.py": render_student_cabt_trace(
                (source_root / "observability" / "cabt_trace.py").read_text(
                    encoding="utf-8"
                )
            ).encode("utf-8"),
        },
        extra_files={
            "student-model-manifest.json": artifact_manifest_bytes,
            "student-package-manifest.json": (
                json.dumps(embedded_manifest, sort_keys=True) + "\n"
            ).encode("utf-8"),
        },
    )
    wrapper_manifest = {
        "schema_version": "kaggle-agent-package-v1",
        "agent_kind": "student",
        "competition_slug": "pokemon-tcg-ai-battle",
        "entrypoint": "main.py",
        "deck_hash": hashlib.sha256((package / "deck.csv").read_bytes()).hexdigest(),
        "source_head": "0" * 40,
        "private_artifacts_included": False,
        "contract": {
            "submission_method": "UNKNOWN",
            "archive_type": "UNKNOWN",
            "entrypoint": "main.py",
        },
        "model_hash": model_manifest["model_hash"],
        "artifact_purpose": "ACTUAL_TRAINED",
        "performance_eligible": True,
        "fallback_policy": "Rule Agent v0",
        "builder_result": builder_result,
    }
    (package / "kaggle-package-manifest.json").write_text(
        json.dumps(wrapper_manifest, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return package


def test_student_wrapper_validates_then_parks_and_restores_its_sidecar(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    package = _build_real_student_wrapper_package(tmp_path)
    sidecar = package / "kaggle-package-manifest.json"
    expected_hash = json.loads(sidecar.read_text(encoding="utf-8"))["model_hash"]
    smoke = _passing_check()["archive_only_actual_cabt"]
    smoke["gate_c0"]["model_hash"] = expected_hash
    smoke["gate_c1"]["model_hash"] = expected_hash
    smoke["stress"]["fresh_model_hashes"] = [expected_hash]
    monkeypatch.setattr(
        verifier,
        "_archive_only_student_smoke",
        lambda *_args, **_kwargs: smoke,
    )
    monkeypatch.setattr(verifier, "_privacy_scan", lambda _root: 0)

    assert verifier.main(
        ["--artifact", str(package), "--stress-games", "100"]
    ) == 0

    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "PASS"
    assert result["readiness"] == "READY_TO_SUBMIT"
    assert sidecar.is_file() and not sidecar.is_symlink()


def test_wrapper_sidecar_snapshot_rejects_replacement_before_parking(
    tmp_path: Path,
) -> None:
    package = _build_real_student_wrapper_package(tmp_path)
    _manifest, snapshot = verifier._load_kaggle_package_manifest_snapshot(package)
    sidecar = package / "kaggle-package-manifest.json"
    sidecar.write_bytes(b'{"own_private_state":[101],"serial":7}')

    with pytest.raises(ValueError, match="changed before parking"):
        verifier._verify_without_package_sidecar(
            package,
            lambda _root: {},
            snapshot=snapshot,
        )


def test_wrapper_parking_never_overwrites_a_racing_parked_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package = _build_real_student_wrapper_package(tmp_path)
    _manifest, snapshot = verifier._load_kaggle_package_manifest_snapshot(package)
    sidecar = package / "kaggle-package-manifest.json"
    parked = package.parent / f".{package.name}.kaggle-package-manifest.json.check"
    racing_bytes = b"unrelated concurrent parking target\n"
    original_link = verifier.os.link
    raced = {"injected": False}

    def race_before_exclusive_park(
        source: os.PathLike[str] | str,
        destination: os.PathLike[str] | str,
        *,
        follow_symlinks: bool = True,
    ) -> None:
        if Path(destination) == parked and not raced["injected"]:
            raced["injected"] = True
            parked.write_bytes(racing_bytes)
        original_link(source, destination, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(verifier.os, "link", race_before_exclusive_park)

    with pytest.raises(ValueError, match="parking path is occupied"):
        verifier._verify_without_package_sidecar(
            package,
            lambda _root: pytest.fail("verification must not run after parking race"),
            snapshot=snapshot,
        )

    assert raced["injected"] is True
    assert sidecar.read_bytes() == snapshot.sidecar_bytes
    assert parked.read_bytes() == racing_bytes


def test_wrapper_restores_snapshot_after_parked_sidecar_is_replaced(
    tmp_path: Path,
) -> None:
    package = _build_real_student_wrapper_package(tmp_path)
    _manifest, snapshot = verifier._load_kaggle_package_manifest_snapshot(package)
    sidecar = package / "kaggle-package-manifest.json"
    parked = package.parent / f".{package.name}.kaggle-package-manifest.json.check"

    def replace_parked_sidecar(_root: Path) -> dict[str, object]:
        parked.write_bytes(b'{"own_private_state":[101],"serial":7}')
        return {}

    with pytest.raises(ValueError, match="changed while parked"):
        verifier._verify_without_package_sidecar(
            package,
            replace_parked_sidecar,
            snapshot=snapshot,
        )

    assert sidecar.read_bytes() == snapshot.sidecar_bytes
    assert not parked.exists()


def test_wrapper_restore_keeps_parked_original_when_destination_is_obstructed(
    tmp_path: Path,
) -> None:
    package = _build_real_student_wrapper_package(tmp_path)
    _manifest, snapshot = verifier._load_kaggle_package_manifest_snapshot(package)
    sidecar = package / "kaggle-package-manifest.json"
    parked = package.parent / f".{package.name}.kaggle-package-manifest.json.check"
    sidecar.replace(parked)
    sidecar.mkdir()

    with pytest.raises(ValueError, match="destination is occupied"):
        verifier._restore_verified_sidecar(package, sidecar, parked, snapshot)

    assert sidecar.is_dir()
    assert parked.is_file()
    assert parked.read_bytes() == snapshot.sidecar_bytes


@pytest.mark.parametrize(
    "changed_member",
    (
        "kaggle-package-manifest.json",
        "submission.tar.gz",
        "main.py",
    ),
)
def test_wrapper_rejects_any_snapshot_member_mutated_by_smoke_before_ready(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    changed_member: str,
) -> None:
    package = _build_real_student_wrapper_package(tmp_path)
    sidecar = package / "kaggle-package-manifest.json"
    expected_hash = json.loads(sidecar.read_text(encoding="utf-8"))["model_hash"]
    smoke = _passing_check()["archive_only_actual_cabt"]
    smoke["gate_c0"]["model_hash"] = expected_hash
    smoke["gate_c1"]["model_hash"] = expected_hash
    smoke["stress"]["fresh_model_hashes"] = [expected_hash]
    original_archive = (package / "submission.tar.gz").read_bytes()

    smoke_inputs: list[object] = []

    def mutate_after_snapshot(archive_bytes: object, *_args: object, **_kwargs: object) -> dict[str, object]:
        smoke_inputs.append(archive_bytes)
        (package / changed_member).write_bytes(b"mutated after verified snapshot\n")
        return smoke

    monkeypatch.setattr(verifier, "_archive_only_student_smoke", mutate_after_snapshot)
    monkeypatch.setattr(verifier, "_privacy_scan", lambda _snapshot: 0)

    assert verifier.main(["--artifact", str(package), "--stress-games", "100"]) == 4
    assert smoke_inputs == [original_archive]
    assert json.loads(capsys.readouterr().out) == {"status": "BLOCKED", "reason": "ValueError"}


def test_local_import_closure_flags_missing_required_module(tmp_path: Path) -> None:
    (tmp_path / "runtime_main.py").write_text(
        "from mage_ptcg.knowledge import KnowledgePack\n", encoding="utf-8"
    )
    report = local_import_closure(tmp_path)
    assert report["status"] == "FAIL"
    assert {"source": "runtime_main.py", "module": "mage_ptcg.knowledge"} in report["missing_required"]


def test_local_import_closure_passes_when_module_present(tmp_path: Path) -> None:
    agents_dir = tmp_path / "mage_submission_agents"
    agents_dir.mkdir()
    (agents_dir / "__init__.py").write_text(
        "from .rule_agent import choose_rule_indices\n", encoding="utf-8"
    )
    (agents_dir / "rule_agent.py").write_text(
        "def choose_rule_indices(obs):\n    return None\n", encoding="utf-8"
    )
    (tmp_path / "main.py").write_text("import mage_submission_agents\n", encoding="utf-8")
    report = local_import_closure(tmp_path)
    assert report["status"] == "PASS"
    assert report["missing_required"] == []


def test_local_import_closure_reports_allowed_optional(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setitem(
        verifier.OPTIONAL_LOCAL_IMPORTS,
        ("runtime_main.py", "mage_ptcg.telemetry_extra"),
        "verified unreachable in Student inference",
    )
    (tmp_path / "runtime_main.py").write_text(
        "from mage_ptcg.telemetry_extra import unused\n", encoding="utf-8"
    )
    report = local_import_closure(tmp_path)
    assert report["status"] == "PASS"
    assert report["missing_required"] == []
    assert report["allowed_optional"] == [
        {
            "source": "runtime_main.py",
            "module": "mage_ptcg.telemetry_extra",
            "reason": "verified unreachable in Student inference",
        }
    ]


def test_optional_local_imports_rejects_forbidden_markers(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setitem(
        verifier.OPTIONAL_LOCAL_IMPORTS,
        ("runtime_main.py", "mage_ptcg.knowledge"),
        "should never be allowed",
    )
    (tmp_path / "runtime_main.py").write_text(
        "from mage_ptcg.knowledge import KnowledgePack\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="forbidden module"):
        local_import_closure(tmp_path)


def test_optional_local_imports_allowlist_is_empty_by_default() -> None:
    """Task 3: attestation依存はcabt_traceコピーから除去済みのため、allowlistは空。"""
    assert verifier.OPTIONAL_LOCAL_IMPORTS == {}


# =========================================================================== #
# Safety Gate and Submission Wrapper Tests (G1-G8)
# =========================================================================== #

from unittest.mock import MagicMock
from scripts.verify_kaggle_submission_candidate import verify_archive

def _create_dummy_tarball(tmp_path: Path, main_content: str, model_hash: str = "94564328a10f1e914beb63073235722694093e281905b5cbd546b2a35742dea4", required_files_override: list[str] | None = None) -> Path:
    pkg_dir = tmp_path / "pkg"
    pkg_dir.mkdir(exist_ok=True)
    
    files = {
        "main.py": main_content,
        "runtime_main.py": "def make_rule_agent(deck=None, deck_path=None): return lambda obs: [0]*60\ndef _deck_supplier(deck, deck_path): return lambda: [0]*60\ndef _selection_contract(obs): return None",
        "deck.csv": "card1,card2",
        "models/neural-student-v1.json": json.dumps({"model_hash": model_hash}),
    }
    
    if required_files_override is not None:
        files = {k: v for k, v in files.items() if k in required_files_override}

    for name, content in files.items():
        p = pkg_dir / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        
    archive_path = tmp_path / "submission.tar.gz"
    import gzip, tarfile, io
    with archive_path.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz:
            with tarfile.open(fileobj=gz, mode="w", format=tarfile.USTAR_FORMAT) as archive:
                for name in files:
                    p = pkg_dir / name
                    data = p.read_bytes()
                    info = tarfile.TarInfo(name)
                    info.size = len(data)
                    archive.addfile(info, io.BytesIO(data))
    return archive_path


def _mock_subprocess_runner(monkeypatch):
    def mock_run(args, **kwargs):
        if isinstance(args, list) and "RUNTIME_PROBE" in str(args):
            class ProbeCompletedProcess:
                returncode = 0
                stdout = json.dumps({
                    "marker": "RUNTIME_PROBE",
                    "python_version": "3.11.11",
                    "kaggle_environments_version": "1.32.0",
                })
                stderr = ""
            return ProbeCompletedProcess()
        cwd = kwargs.get("cwd")
        if cwd:
            cwd_path = Path(cwd)
            # Simulate G5 output
            if "g5_output.json" in str(args) or (isinstance(args, list) and "-c" in args and "g5_output.json" in args[2]):
                (cwd_path / "g5_output.json").write_text(json.dumps({
                    "status": "SUCCESS", "steps": 215, "agent_statuses": ["DONE", "DONE"]
                }))
        
        # Simulate G6 output
        if cwd and ("smoke_run_result.json" in str(args) or (isinstance(args, list) and "-c" in args and "smoke_run_result.json" in args[2])):
            (cwd_path / "smoke_run_result.json").write_text(json.dumps({
                "gate_status": "CLEAN_PASS",
                "crashes": 0, "invalid_actions": 0, "timeouts": 0, "wins": 10, "losses": 10,
                "external_files_read": []
            }))

        class DummyCompletedProcess:
            returncode = 0
            stdout = "MOCK SUCCESS"
            stderr = ""
        return DummyCompletedProcess()
    
    monkeypatch.setattr(subprocess, "run", mock_run)


def test_verify_candidate_passes_entryfix_archive(tmp_path: Path, monkeypatch) -> None:
    _mock_subprocess_runner(monkeypatch)
    main_content = "def agent(obs): return [0]*60"
    archive = _create_dummy_tarball(tmp_path, main_content)
    output_json = tmp_path / "verification.json"
    
    code = verify_archive(archive, "pokemon-tcg-ai-battle", 20, 33000, output_json)
    assert code == 0
    assert output_json.is_file()
    
    data = json.loads(output_json.read_text("utf-8"))
    assert data["local_submission_ready"] is True
    assert data["gates"]["kaggle_raw_exec"]["status"] == "PASS"
    assert data["cwd_decoupled_verification"] is True
    assert data["code_root"] == "extracted_archive"
    assert data["working_directory"] == "separate_empty_directory"
    assert data["python_version"] == "3.11.11"
    assert data["kaggle_environments_version"] == "1.32.0"


def test_verify_candidate_reads_entrypoint_from_archive_not_workspace(tmp_path: Path) -> None:
    # Do not mock subprocess run. This allows the broken main.py in the archive
    # to fail compilation/exec inside G3, raising an exception.
    main_content = "raise RuntimeError('archive execution check')"
    archive = _create_dummy_tarball(tmp_path, main_content)
    output_json = tmp_path / "verification.json"
    
    with pytest.raises(Exception):
        verify_archive(archive, "pokemon-tcg-ai-battle", 20, 33000, output_json)


def test_verify_candidate_rejects_missing_main(tmp_path: Path, monkeypatch) -> None:
    _mock_subprocess_runner(monkeypatch)
    archive = _create_dummy_tarball(tmp_path, "def agent(obs): pass", required_files_override=["runtime_main.py", "deck.csv", "models/neural-student-v1.json"])
    output_json = tmp_path / "verification.json"
    
    with pytest.raises(Exception):
        verify_archive(archive, "pokemon-tcg-ai-battle", 20, 33000, output_json)


def test_verify_candidate_rejects_missing_model(tmp_path: Path, monkeypatch) -> None:
    _mock_subprocess_runner(monkeypatch)
    archive = _create_dummy_tarball(tmp_path, "def agent(obs): pass", required_files_override=["main.py", "runtime_main.py", "deck.csv"])
    output_json = tmp_path / "verification.json"
    
    with pytest.raises(Exception):
        verify_archive(archive, "pokemon-tcg-ai-battle", 20, 33000, output_json)


def test_verify_candidate_rejects_path_traversal(tmp_path: Path, monkeypatch) -> None:
    _mock_subprocess_runner(monkeypatch)
    # Create archive with path traversal in name
    archive_path = tmp_path / "submission.tar.gz"
    import tarfile
    with tarfile.open(archive_path, "w:gz") as tar:
        info = tarfile.TarInfo(name="../traversal.py")
        info.size = 0
        tar.addfile(info)
    
    output_json = tmp_path / "verification.json"
    with pytest.raises(Exception):
        verify_archive(archive_path, "pokemon-tcg-ai-battle", 20, 33000, output_json)


def test_verify_candidate_rejects_non_regular_member(tmp_path: Path, monkeypatch) -> None:
    _mock_subprocess_runner(monkeypatch)
    archive_path = tmp_path / "submission.tar.gz"
    import tarfile
    with tarfile.open(archive_path, "w:gz") as archive:
        member = tarfile.TarInfo("device")
        member.type = tarfile.CHRTYPE
        archive.addfile(member)

    with pytest.raises(ValueError, match="regular file"):
        verify_archive(
            archive_path, "pokemon-tcg-ai-battle", 20, 33000,
            tmp_path / "verification.json",
        )


def test_gate_rejects_python_312_runtime(monkeypatch, tmp_path: Path) -> None:
    def fake_probe(args, **kwargs):
        class Completed:
            returncode = 0
            stdout = json.dumps({
                "marker": "RUNTIME_PROBE",
                "python_version": "3.12.3",
                "kaggle_environments_version": "1.32.0",
            })
            stderr = ""
        return Completed()

    monkeypatch.setattr(subprocess, "run", fake_probe)
    with pytest.raises(RuntimeError, match="requires Python 3.11"):
        candidate_verifier._probe_runtime(Path(sys.executable))


def test_verify_candidate_rejects_raw_exec_error(tmp_path: Path, monkeypatch) -> None:
    # Fail compilation/exec inside raw exec check
    def mock_run_fail_g3(args, **kwargs):
        class DummyCompletedProcess:
            returncode = 1
            stdout = ""
            stderr = "NameError: name '__file__' is not defined"
        return DummyCompletedProcess()
    
    monkeypatch.setattr(subprocess, "run", mock_run_fail_g3)
    archive = _create_dummy_tarball(tmp_path, "def agent(obs): pass")
    output_json = tmp_path / "verification.json"
    with pytest.raises(Exception):
        verify_archive(archive, "pokemon-tcg-ai-battle", 20, 33000, output_json)


def test_verify_candidate_rejects_initial_observation_error(tmp_path: Path, monkeypatch) -> None:
    # Fail Step 0 check by returning exit code 4
    def mock_run_fail_g4(args, **kwargs):
        # We need G3 to pass but G4 to fail
        if "obs = {" in str(args) or (isinstance(args, list) and "-c" in args and "obs = {" in args[2]):
            class DummyCompletedProcess:
                returncode = 4
                stdout = ""
                stderr = "ValueError: Step 0 action must be a list of 60 cards"
            return DummyCompletedProcess()
        
        class DummyCompletedProcess:
            returncode = 0
            stdout = ""
            stderr = ""
        return DummyCompletedProcess()
        
    monkeypatch.setattr(subprocess, "run", mock_run_fail_g4)
    archive = _create_dummy_tarball(tmp_path, "def agent(obs): pass")
    output_json = tmp_path / "verification.json"
    with pytest.raises(Exception):
        verify_archive(archive, "pokemon-tcg-ai-battle", 20, 33000, output_json)


def test_verify_candidate_rejects_validation_episode_error(tmp_path: Path, monkeypatch) -> None:
    # Fail G5 episode check
    def mock_run_fail_g5(args, **kwargs):
        if "make(" in str(args) or (isinstance(args, list) and "-c" in args and "make(" in args[2]):
            class DummyCompletedProcess:
                returncode = 5
                stdout = ""
                stderr = "Agent validation error: statuses: ERROR, DONE"
            return DummyCompletedProcess()
        
        class DummyCompletedProcess:
            returncode = 0
            stdout = ""
            stderr = ""
        return DummyCompletedProcess()

    monkeypatch.setattr(subprocess, "run", mock_run_fail_g5)
    archive = _create_dummy_tarball(tmp_path, "def agent(obs): pass")
    output_json = tmp_path / "verification.json"
    with pytest.raises(Exception):
        verify_archive(archive, "pokemon-tcg-ai-battle", 20, 33000, output_json)


def test_verify_candidate_rejects_archive_mutated_after_verification(tmp_path: Path, monkeypatch) -> None:
    # We mutate archive_path inside subproc run
    def mock_run_mutate(args, **kwargs):
        cwd = kwargs.get("cwd")
        if cwd:
            # Overwrite original archive with different content
            nonlocal archive
            archive.write_bytes(b"corrupted bytes")
            cwd_path = Path(cwd)
            if "g5_output.json" in str(args) or (isinstance(args, list) and "-c" in args and "g5_output.json" in args[2]):
                (cwd_path / "g5_output.json").write_text(json.dumps({
                    "status": "SUCCESS", "steps": 215, "agent_statuses": ["DONE", "DONE"]
                }))
        
        if isinstance(args, list) and "run_actual_agent_viability.py" in args[1]:
            out_idx = args.index("--output")
            out_path = Path(args[out_idx + 1])
            out_path.write_text(json.dumps({
                "gate_status": "CLEAN_PASS",
                "crashes": 0, "invalid_actions": 0, "timeouts": 0, "wins": 10, "losses": 10
            }))

        class DummyCompletedProcess:
            returncode = 0
            stdout = ""
            stderr = ""
        return DummyCompletedProcess()

    monkeypatch.setattr(subprocess, "run", mock_run_mutate)
    archive = _create_dummy_tarball(tmp_path, "def agent(obs): pass")
    output_json = tmp_path / "verification.json"
    with pytest.raises(Exception):
        verify_archive(archive, "pokemon-tcg-ai-battle", 20, 33000, output_json)


def test_verify_candidate_rejects_model_sha_mismatch(tmp_path: Path, monkeypatch) -> None:
    _mock_subprocess_runner(monkeypatch)
    archive = _create_dummy_tarball(tmp_path, "def agent(obs): pass", model_hash="")
    output_json = tmp_path / "verification.json"
    with pytest.raises(Exception):
        verify_archive(archive, "pokemon-tcg-ai-battle", 20, 33000, output_json)


REPO_ROOT = Path(__file__).resolve().parents[1]

# A real, __file__-safe Kaggle entrypoint that delegates every decision to the
# actual Rule Agent v0 policy (``runtime_main.make_rule_agent``). Unlike a
# trivial ``def agent(obs): return [0]*60`` stub, this plays fully legal cabt
# games end to end, so it survives a real, unmocked G1-G6 run. It never
# imports ``mage_ptcg`` (no ``knowledge_pack`` is supplied), which is exactly
# what lets ``test_g6_reports_fallback_telemetry_unavailable_honestly`` prove
# the gate reports unmeasured telemetry honestly instead of fabricating zero.
_RULE_ONLY_ENTRYPOINT = '''"""Test-only Kaggle entrypoint: Rule Agent v0 only, __file__-safe."""

from __future__ import annotations

import sys
from pathlib import Path

_REQUIRED = ("main.py", "runtime_main.py", "deck.csv", "models/neural-student-v1.json")


def _is_package_root(candidate):
    return candidate.is_dir() and all((candidate / item).is_file() for item in _REQUIRED)


def _root_candidates():
    if "__file__" in globals():
        yield Path(__file__).resolve().parent
    source_name = getattr(sys._getframe().f_code, "co_filename", "")
    if source_name and not source_name.startswith("<"):
        yield Path(source_name).resolve().parent
    yield Path("/kaggle_simulations/agent")


_ROOT = next((candidate for candidate in _root_candidates() if _is_package_root(candidate)), None)
if _ROOT is None:
    raise RuntimeError("submission package root could not be resolved")
for _entry in (str(_ROOT), str(_ROOT / "src")):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import runtime_main

_DECK_PATH = _ROOT / "deck.csv"
_AGENT = runtime_main.make_rule_agent(deck_path=_DECK_PATH)


def agent(obs_dict):
    return _AGENT(obs_dict)
'''


def _build_rule_only_tarball(
    tmp_path: Path,
    *,
    name: str = "submission.tar.gz",
    main_override: str | None = None,
    omit: tuple[str, ...] = (),
) -> Path:
    """Build a real, playable, archive-only Rule Agent v0 submission tarball.

    Every member is read from the actual repository sources (never a
    hand-rolled stub), so the resulting archive plays legal cabt games and
    exercises the real G1-G6 pipeline without mocking ``subprocess.run``.
    """
    files: dict[str, bytes] = {
        "main.py": (main_override if main_override is not None else _RULE_ONLY_ENTRYPOINT).encode("utf-8"),
        "runtime_main.py": (REPO_ROOT / "main.py").read_bytes(),
        "mage_submission_agents/__init__.py": (REPO_ROOT / "agents" / "__init__.py").read_bytes(),
        "mage_submission_agents/rule_agent.py": (REPO_ROOT / "agents" / "rule_agent.py").read_bytes(),
        "deck.csv": (REPO_ROOT / "deck.csv").read_bytes(),
        "models/neural-student-v1.json": json.dumps(
            {"model_hash": "94564328a10f1e914beb63073235722694093e281905b5cbd546b2a35742dea4"}
        ).encode("utf-8"),
    }
    files = {
        name: data.replace(b"from agents", b"from mage_submission_agents")
        for name, data in files.items()
    }
    for path in omit:
        files.pop(path, None)

    archive_path = tmp_path / name
    import gzip
    import io
    import tarfile as _tarfile

    with archive_path.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz:
            with _tarfile.open(fileobj=gz, mode="w", format=_tarfile.USTAR_FORMAT) as archive:
                for member_name, data in files.items():
                    info = _tarfile.TarInfo(member_name)
                    info.size = len(data)
                    archive.addfile(info, io.BytesIO(data))
    return archive_path


def test_g6_succeeds_with_tarball_only(tmp_path: Path, short_real_gate) -> None:
    archive = _build_rule_only_tarball(tmp_path)
    output_json = tmp_path / "verification.json"

    code = verify_archive(
        archive, "pokemon-tcg-ai-battle", 2, 34321, output_json,
        python_executable=_gate_python311(),
    )
    assert code == 0
    assert output_json.is_file()

    data = json.loads(output_json.read_text("utf-8"))
    assert data["local_submission_ready"] is True
    assert data["archive_only_verification"] is True
    assert data["external_files_read"] == []
    assert data["g6_runtime_source"] == "extracted_archive/main.py"
    assert data["gates"]["artifact_runtime_smoke"]["crashes"] == 0
    assert data["gates"]["artifact_runtime_smoke"]["invalid_actions"] == 0
    assert data["gates"]["artifact_runtime_smoke"]["timeouts"] == 0


def test_gate_passes_after_source_candidate_directory_deleted(tmp_path: Path, short_real_gate) -> None:
    candidate_dir = tmp_path / "candidate"
    candidate_dir.mkdir()
    archive = _build_rule_only_tarball(candidate_dir)
    (candidate_dir / "manifest.json").write_text("poison sidecar", encoding="utf-8")
    (candidate_dir / "main.py").write_text("raise RuntimeError('candidate main used')", encoding="utf-8")
    artifact_dir = tmp_path / "artifact"
    artifact_dir.mkdir()
    frozen_archive = artifact_dir / archive.name
    shutil.copy2(archive, frozen_archive)
    shutil.rmtree(candidate_dir)

    output_json = artifact_dir / "verification.json"
    assert verify_archive(
        frozen_archive, "pokemon-tcg-ai-battle", 2, 34320, output_json,
        python_executable=_gate_python311(),
    ) == 0
    assert json.loads(output_json.read_text("utf-8"))["external_files_read"] == []


def test_gate_ignores_fake_sidecar_manifest(tmp_path: Path, short_real_gate) -> None:
    archive = _build_rule_only_tarball(tmp_path)

    # Poison a sidecar manifest.json next to the archive with data that would
    # change the reported result if it were ever read.
    fake_manifest = archive.parent / "manifest.json"
    fake_manifest.write_text(json.dumps({"model_hash": "0" * 64}), encoding="utf-8")

    output_json = tmp_path / "verification.json"
    code = verify_archive(
        archive, "pokemon-tcg-ai-battle", 2, 34322, output_json,
        python_executable=_gate_python311(),
    )
    assert code == 0

    data = json.loads(output_json.read_text("utf-8"))
    assert data["local_submission_ready"] is True
    assert data["archive_only_verification"] is True
    assert data["external_files_read"] == []
    # The archive's own model hash must win; the sidecar's poison must not leak in.
    assert data["semantic_model_hash"] == "94564328a10f1e914beb63073235722694093e281905b5cbd546b2a35742dea4"


def test_g6_rejects_external_runtime_read(tmp_path: Path, short_real_gate) -> None:
    poison = tmp_path / "candidate-sidecar.txt"
    poison.write_text("must not be read", encoding="utf-8")
    main = _RULE_ONLY_ENTRYPOINT.replace(
        "def agent(obs_dict):\n    return _AGENT(obs_dict)",
        f"def agent(obs_dict):\n    Path({str(poison)!r}).read_text(encoding='utf-8')\n    return _AGENT(obs_dict)",
    )
    archive = _build_rule_only_tarball(tmp_path, main_override=main)

    with pytest.raises(ValueError, match="read external files"):
        verify_archive(
            archive, "pokemon-tcg-ai-battle", 2, 343221,
            tmp_path / "verification.json", python_executable=_gate_python311(),
        )


def test_gate_ignores_fake_workspace_main(tmp_path: Path, monkeypatch, short_real_gate) -> None:
    archive = _build_rule_only_tarball(tmp_path)

    fake_workspace = tmp_path / "fake_workspace"
    fake_workspace.mkdir()
    (fake_workspace / "main.py").write_text("raise RuntimeError('poisoned workspace main.py was read')", encoding="utf-8")
    monkeypatch.chdir(fake_workspace)

    output_json = fake_workspace / "verification.json"
    code = verify_archive(
        archive, "pokemon-tcg-ai-battle", 2, 34323, output_json,
        python_executable=_gate_python311(),
    )
    assert code == 0

    data = json.loads(output_json.read_text("utf-8"))
    assert data["local_submission_ready"] is True
    assert data["external_files_read"] == []


def test_gate_ignores_fake_workspace_model(tmp_path: Path, monkeypatch, short_real_gate) -> None:
    archive = _build_rule_only_tarball(tmp_path)

    fake_workspace = tmp_path / "fake_workspace"
    (fake_workspace / "models").mkdir(parents=True)
    (fake_workspace / "models" / "neural-student-v1.json").write_text(
        json.dumps({"model_hash": "f" * 64}), encoding="utf-8"
    )
    monkeypatch.chdir(fake_workspace)

    output_json = fake_workspace / "verification.json"
    code = verify_archive(
        archive, "pokemon-tcg-ai-battle", 2, 34324, output_json,
        python_executable=_gate_python311(),
    )
    assert code == 0

    data = json.loads(output_json.read_text("utf-8"))
    assert data["local_submission_ready"] is True
    assert data["external_files_read"] == []
    assert data["semantic_model_hash"] == "94564328a10f1e914beb63073235722694093e281905b5cbd546b2a35742dea4"


def test_g6_fails_when_main_missing(tmp_path: Path, short_real_gate) -> None:
    archive = _build_rule_only_tarball(tmp_path, omit=("main.py",))
    output_json = tmp_path / "verification.json"
    with pytest.raises(FileNotFoundError):
        verify_archive(
            archive, "pokemon-tcg-ai-battle", 2, 34325, output_json,
            python_executable=_gate_python311(),
        )


def test_gate_fails_if_archive_model_is_missing(tmp_path: Path, short_real_gate) -> None:
    archive = _build_rule_only_tarball(tmp_path, omit=("models/neural-student-v1.json",))
    output_json = tmp_path / "verification.json"
    with pytest.raises(FileNotFoundError):
        verify_archive(
            archive, "pokemon-tcg-ai-battle", 2, 34326, output_json,
            python_executable=_gate_python311(),
        )


def test_gate_fails_if_archive_main_is_broken(tmp_path: Path, short_real_gate) -> None:
    archive = _build_rule_only_tarball(
        tmp_path, main_override="raise RuntimeError('archive execution check')"
    )
    output_json = tmp_path / "verification.json"
    with pytest.raises(RuntimeError):
        verify_archive(
            archive, "pokemon-tcg-ai-battle", 2, 34327, output_json,
            python_executable=_gate_python311(),
        )


def test_g6_reports_fallback_telemetry_unavailable_honestly(tmp_path: Path, short_real_gate) -> None:
    # The rule-only fixture never imports mage_ptcg.offline_training.neural_runtime,
    # so the archive genuinely cannot report fallback telemetry. The gate must
    # say so explicitly rather than fabricating a fallback count of zero.
    archive = _build_rule_only_tarball(tmp_path)
    output_json = tmp_path / "verification.json"

    code = verify_archive(
        archive, "pokemon-tcg-ai-battle", 2, 34328, output_json,
        python_executable=_gate_python311(),
    )
    assert code == 0

    data = json.loads(output_json.read_text("utf-8"))
    smoke = data["gates"]["artifact_runtime_smoke"]
    assert smoke["fallback_telemetry_status"] == "UNAVAILABLE_FROM_ARCHIVE_RUNTIME"
    assert smoke["fallback_telemetry_detail"]
    # Absence of telemetry must never be reported as a measured zero.
    assert smoke["fallback_reasons"] == []
    assert smoke["selected_count"] == 0
    # The gate itself is still safety-decided by crash/invalid/timeout counts only.
    assert data["local_submission_ready"] is True
