from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from mage_ptcg.meta_specialist.cg_alternating_runtime_v1 import (
    CG_DECK_FIXED_LONG_V1,
    CG_POLICY_FIXED_SHORT_V1,
    CG_STAGE_GAMES_V1,
    CgAlternatingRuntimeError,
    CgPackageSpecV1,
    DEFAULT_WORKER_RECYCLE_GAMES_V1,
    DEFAULT_WORKERS_V1,
    next_cg_stage_games_v1,
    load_cg_alternating_stage_v1,
    validate_cg_pair_v1,
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _package(tmp_path: Path, name: str, *, policy: str, card: int) -> CgPackageSpecV1:
    root = tmp_path / name / "package"
    root.mkdir(parents=True)
    (root / "main.py").write_text(policy, encoding="utf-8")
    (root / "deck.csv").write_text((f"{card}\n" * 60), encoding="utf-8")
    archive = root.parent / "submission.tar.gz"
    archive.write_bytes(f"archive-{name}".encode())
    manifest = root.parent / "candidate_manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "candidate_id": name,
                "deck_sha256": _sha(root / "deck.csv"),
                "archive": {"path": "submission.tar.gz", "sha256": _sha(archive)},
                "policy_source_sha256": _sha(root / "main.py"),
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return CgPackageSpecV1.from_package(root)


def test_cg_package_spec_rebinds_manifest_and_runtime_hashes(tmp_path: Path) -> None:
    spec = _package(tmp_path, "parent", policy="def agent(obs): return []\n", card=1)
    assert spec.candidate_id == "parent"
    assert spec.policy_sha256 == _sha(spec.package_root / "main.py")
    assert spec.deck_sha256 == _sha(spec.package_root / "deck.csv")
    assert spec.archive_sha256 == _sha(spec.package_root.parent / "submission.tar.gz")
    assert len(spec.manifest_sha256) == 64
    spec.verify_sources()


def test_cg_fixed_phases_allow_only_one_identity_dimension(tmp_path: Path) -> None:
    parent = _package(tmp_path, "parent", policy="a\n", card=1)
    deck_child = _package(tmp_path, "deck-child", policy="a\n", card=2)
    policy_child = _package(tmp_path, "policy-child", policy="b\n", card=2)
    policy_control = _package(tmp_path, "policy-control", policy="a\n", card=2)
    deck_pair = validate_cg_pair_v1(
        phase=CG_POLICY_FIXED_SHORT_V1,
        candidate=deck_child,
        control=parent,
        stage_games=96,
    )
    assert deck_pair.phase == CG_POLICY_FIXED_SHORT_V1
    policy_pair = validate_cg_pair_v1(
        phase=CG_DECK_FIXED_LONG_V1,
        candidate=policy_child,
        control=policy_control,
        stage_games=96,
    )
    assert policy_pair.phase == CG_DECK_FIXED_LONG_V1
    with pytest.raises(CgAlternatingRuntimeError, match="policy-fixed"):
        validate_cg_pair_v1(
            phase=CG_POLICY_FIXED_SHORT_V1,
            candidate=policy_child,
            control=policy_control,
            stage_games=96,
        )
    with pytest.raises(CgAlternatingRuntimeError, match="deck-fixed"):
        validate_cg_pair_v1(
            phase=CG_DECK_FIXED_LONG_V1,
            candidate=deck_child,
            control=parent,
            stage_games=96,
        )


def test_cg_stage_sequence_and_parallel_defaults_are_sealed() -> None:
    assert CG_STAGE_GAMES_V1 == (96, 384, 768, 1536)
    assert DEFAULT_WORKERS_V1 == 12
    assert DEFAULT_WORKER_RECYCLE_GAMES_V1 == 16
    assert next_cg_stage_games_v1(96, positive=True) == 384
    assert next_cg_stage_games_v1(768, positive=True) == 1536
    assert next_cg_stage_games_v1(1536, positive=True) is None
    assert next_cg_stage_games_v1(96, positive=False) is None


def test_load_cg_stage_rejects_changed_stage_spec(tmp_path: Path) -> None:
    root = tmp_path / "stage"
    root.mkdir()
    spec = {
        "schema_version": "meta-specialist-cg-alternating-runtime-v1",
        "research_only": True,
        "authority": {
            "training_allowed": False,
            "promotion_allowed": False,
            "submission_allowed": False,
            "longrun_allowed": False,
        },
    }
    spec_path = root / "stage-spec.json"
    raw = (json.dumps(spec, sort_keys=True, separators=(",", ":")) + "\n").encode()
    spec_path.write_bytes(raw)
    manifest = {
        "schema_version": "meta-specialist-cg-alternating-runtime-v1",
        "status": "DRY_RUN",
        "research_only": True,
        "authority": {
            "training_allowed": False,
            "promotion_allowed": False,
            "submission_allowed": False,
            "longrun_allowed": False,
        },
        "stage_spec_sha256": hashlib.sha256(raw).hexdigest(),
    }
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    assert load_cg_alternating_stage_v1(root)["status"] == "DRY_RUN"
    spec_path.write_text("{}\n", encoding="utf-8")
    with pytest.raises(CgAlternatingRuntimeError, match="stage spec SHA|schema mismatch"):
        load_cg_alternating_stage_v1(root)
