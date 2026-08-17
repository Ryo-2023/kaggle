"""Tests for scripts/check_o1_protected_files.py: baseline/verify/tamper detection.

Runs against an isolated temporary git repository (not the real project repo)
so tamper-detection tests can freely modify files without touching any real
protected file.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from scripts.check_o1_protected_files import (
    BASELINE_SCHEMA_VERSION,
    ProtectedFilesError,
    cmd_baseline,
    cmd_verify,
    collect,
    repo_root,
)


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
    return repo


class _Args:
    def __init__(self, **kwargs: object) -> None:
        self.__dict__.update(kwargs)


class TestCollectAgainstRealRepo:
    def test_repo_root_resolves(self) -> None:
        root = repo_root()
        assert (root / "main.py").exists()

    def test_collect_reports_all_configured_paths_as_present_in_real_repo(self) -> None:
        root = repo_root()
        entries = collect(root)
        assert entries["main.py"]["status"] == "present"
        assert entries["deck.csv"]["status"] == "present"
        assert entries["main.py"]["git_blob_sha"] is not None

    def test_runs_data_root_reflects_actual_worktree_state(self) -> None:
        # runs/ is git-ignored data; its presence varies by worktree and by
        # whether an actual-cabt collection has been run in it (e.g. the O2
        # actual-lineage collection under runs/o2-real-cabt/).  This asserts
        # only that collect() reports the correct status for whichever state
        # this worktree is actually in, never a hardcoded snapshot.
        root = repo_root()
        entries = collect(root)
        key = "runs/ (git-ignored data root, presence-only)"
        expected = "present_not_hashed" if (root / "runs").exists() else "absent_in_this_worktree"
        assert entries[key]["status"] == expected


class TestBaselineAndVerifyRoundTrip:
    def test_baseline_then_verify_with_no_changes_reports_unchanged(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        repo = _init_repo(tmp_path)
        (repo / "main.py").write_text("print('v1')\n", encoding="utf-8")
        (repo / "deck.csv").write_text("1,2,3\n", encoding="utf-8")
        monkeypatch.chdir(repo)

        baseline_path = tmp_path / "baseline.json"
        assert cmd_baseline(_Args(output=str(baseline_path))) == 0
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        assert baseline["schema_version"] == BASELINE_SCHEMA_VERSION
        assert baseline["entries"]["main.py"]["status"] == "present"

        assert cmd_verify(_Args(baseline=str(baseline_path))) == 0

    def test_verify_detects_a_tampered_protected_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        repo = _init_repo(tmp_path)
        (repo / "main.py").write_text("print('original')\n", encoding="utf-8")
        monkeypatch.chdir(repo)

        baseline_path = tmp_path / "baseline.json"
        cmd_baseline(_Args(output=str(baseline_path)))
        capsys.readouterr()

        (repo / "main.py").write_text("print('TAMPERED')\n", encoding="utf-8")

        exit_code = cmd_verify(_Args(baseline=str(baseline_path)))
        captured = capsys.readouterr()
        result = json.loads(captured.out)
        assert exit_code == 1
        assert result["protected_files_unchanged"] is False
        assert any(diff["path"] == "main.py" for diff in result["diffs"])

    def test_verify_detects_a_deleted_protected_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        repo = _init_repo(tmp_path)
        (repo / "deck.csv").write_text("1,2,3\n", encoding="utf-8")
        monkeypatch.chdir(repo)

        baseline_path = tmp_path / "baseline.json"
        cmd_baseline(_Args(output=str(baseline_path)))
        capsys.readouterr()

        (repo / "deck.csv").unlink()

        exit_code = cmd_verify(_Args(baseline=str(baseline_path)))
        captured = capsys.readouterr()
        result = json.loads(captured.out)
        assert exit_code == 1
        assert any(diff["path"] == "deck.csv" for diff in result["diffs"])

    def test_verify_rejects_wrong_schema_version_baseline(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        repo = _init_repo(tmp_path)
        monkeypatch.chdir(repo)
        baseline_path = tmp_path / "baseline.json"
        baseline_path.write_text(json.dumps({"schema_version": "wrong-v0", "entries": {}}), encoding="utf-8")
        with pytest.raises(ProtectedFilesError):
            cmd_verify(_Args(baseline=str(baseline_path)))

    def test_unmodified_file_produces_no_diff_but_untouched_new_file_does_not_appear(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        repo = _init_repo(tmp_path)
        (repo / "main.py").write_text("print('v1')\n", encoding="utf-8")
        monkeypatch.chdir(repo)
        baseline_path = tmp_path / "baseline.json"
        cmd_baseline(_Args(output=str(baseline_path)))
        capsys.readouterr()
        # Adding a brand-new, non-protected file must not trigger a false positive.
        (repo / "unrelated.txt").write_text("new file", encoding="utf-8")
        exit_code = cmd_verify(_Args(baseline=str(baseline_path)))
        assert exit_code == 0
