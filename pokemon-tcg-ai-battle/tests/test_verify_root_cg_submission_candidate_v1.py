from __future__ import annotations

import io
import tarfile
from pathlib import Path

import pytest

from scripts.verify_root_cg_submission_candidate_v1 import (
    EXPECTED_CORE_MEMBERS,
    SAMPLE_CG_ROOT,
    _isolated_python,
    inspect_cg_archive,
)


ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / "runs/final-sprint-autonomous/root-cg-submission-candidate-v1-20260814/submission.tar.gz"


def test_sample_runtime_snapshot_is_complete() -> None:
    assert SAMPLE_CG_ROOT.is_dir()
    assert tuple(sorted(EXPECTED_CORE_MEMBERS)) == (
        "cg/__init__.py",
        "cg/api.py",
        "cg/libcg.so",
        "cg/sim.py",
        "cg/utils.py",
        "deck.csv",
        "main.py",
    )


def test_candidate_archive_matches_sample_cg_runtime() -> None:
    result = inspect_cg_archive(ARCHIVE)
    assert result["archive_shape"] == "PASS"
    assert result["cg_runtime_parity"] == "PASS"
    assert result["deck_card_count"] == 60


def test_isolated_python_can_import_host_cabt() -> None:
    python = _isolated_python()
    result = __import__("subprocess").run(
        [str(python), "-I", "-c", "import kaggle_environments"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_archive_rejects_traversal_member(tmp_path: Path) -> None:
    archive = tmp_path / "unsafe.tar.gz"
    info = tarfile.TarInfo("../escape.py")
    data = b"x"
    info.size = len(data)
    with tarfile.open(archive, "w:gz") as handle:
        handle.addfile(info, io.BytesIO(data))
    with pytest.raises(ValueError, match="unsafe archive member"):
        inspect_cg_archive(archive)
