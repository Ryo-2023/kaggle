from __future__ import annotations

import json
from pathlib import Path

from scripts.orchestration.snapshot import WorkspaceSnapshot, path_manifest, workspace_state


def test_snapshot_reproduces_tracked_and_untracked_content(repository: Path) -> None:
    (repository / "fixture.py").write_text("VALUE = 7\n", encoding="utf-8")
    (repository / "notes.txt").write_text("untracked\n", encoding="utf-8")
    (repository / ".env").write_text("TOKEN=secret\n", encoding="utf-8")
    (repository / "innocent.txt").write_text("KAGGLE_KEY=secret\n", encoding="utf-8")
    manager = WorkspaceSnapshot(repository, repository / ".orchestrator" / "snapshots")

    snapshot_id, manifest = manager.create(["fixture.py"])
    destination = repository / ".orchestrator" / "materialized"
    manager.materialize(snapshot_id, destination)

    assert (destination / "fixture.py").read_text(encoding="utf-8") == "VALUE = 7\n"
    assert (destination / "notes.txt").read_text(encoding="utf-8") == "untracked\n"
    assert not (destination / ".env").exists()
    assert not (destination / "innocent.txt").exists()
    assert any(item["path"] == ".env" for item in manifest["excluded_files"])
    assert any(
        item["path"] == "innocent.txt" and item["reason"] == "secret_scan"
        for item in manifest["excluded_files"]
    )


def test_snapshot_manifest_contains_required_artifacts(repository: Path) -> None:
    manager = WorkspaceSnapshot(repository, repository / ".orchestrator" / "snapshots")
    snapshot_id, _ = manager.create(["fixture.py"])
    directory = repository / ".orchestrator" / "snapshots" / snapshot_id

    assert (directory / "manifest.json").is_file()
    assert (directory / "tracked.patch").is_file()
    assert (directory / "untracked_manifest.json").is_file()
    assert (directory / "untracked").is_dir()
    assert manager.load_manifest(snapshot_id)["secret_scan"]["limitations"]


def test_path_records_use_only_git_significant_mode_information(repository: Path) -> None:
    plain = repository / "README.md"
    plain.chmod(0o664)
    executable = repository / "tool.sh"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)
    link = repository / "readme-link"
    link.symlink_to("README.md")
    unsupported = repository / "directory"
    unsupported.mkdir()

    records = {
        item["path"]: item
        for item in path_manifest(
            repository,
            ["README.md", "tool.sh", "readme-link", "missing", "directory"],
        )
    }

    assert records["README.md"]["executable"] is False
    assert records["tool.sh"]["executable"] is True
    assert records["readme-link"]["link_target"] == "README.md"
    assert records["missing"]["kind"] == "missing"
    assert records["directory"]["kind"] == "unsupported"
    assert all("mode" not in record for record in records.values())
    untracked = {
        item["path"]: item
        for item in workspace_state(repository, ["fixture.py"])["untracked_manifest"]
    }
    assert untracked["tool.sh"]["executable"] is True
    assert untracked["readme-link"]["kind"] == "symlink"
