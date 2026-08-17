from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from mage_ptcg.meta_specialist.cg_alternating_runtime_v1 import CgPackageSpecV1, CgAlternatingRuntimeError
from mage_ptcg.meta_specialist.cg_policy_holdout_v1 import validate_policy_holdout_pair_v1


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _package(tmp_path: Path, name: str, *, policy: str, card: int) -> CgPackageSpecV1:
    root = tmp_path / name / "package"
    root.mkdir(parents=True)
    (root / "main.py").write_text(policy, encoding="utf-8")
    (root / "deck.csv").write_text((f"{card}\n" * 60), encoding="utf-8")
    archive = root.parent / "submission.tar.gz"
    archive.write_bytes(f"archive-{name}".encode())
    (root.parent / "candidate_manifest.json").write_text(
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


def test_policy_holdout_requires_same_deck_and_different_policy(tmp_path: Path) -> None:
    candidate = _package(tmp_path, "candidate", policy="def agent(obs): return [1]\n", card=1)
    control = _package(tmp_path, "control", policy="def agent(obs): return [0]\n", card=1)
    pair = validate_policy_holdout_pair_v1(candidate=candidate, control=control, stage_games=96)
    assert pair.candidate.candidate_id == "candidate"
    assert pair.control.candidate_id == "control"


def test_policy_holdout_rejects_deck_change(tmp_path: Path) -> None:
    candidate = _package(tmp_path, "candidate", policy="candidate\n", card=2)
    control = _package(tmp_path, "control", policy="control\n", card=1)
    with pytest.raises(CgAlternatingRuntimeError, match="same deck"):
        validate_policy_holdout_pair_v1(candidate=candidate, control=control, stage_games=96)
