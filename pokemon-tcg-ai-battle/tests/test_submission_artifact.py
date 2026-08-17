"""Regression tests for the standalone Rule Agent v0 submission package."""

from __future__ import annotations

import hashlib
import io
from pathlib import Path
import tarfile

import pytest

from scripts.build_submission import (
    ARCHIVE_NAME,
    ArtifactValidationError,
    _safe_relative_path,
    build_submission,
    scan_artifact_secrets,
    validate_artifact,
    validate_submission_archive,
    verify_submission_artifact,
    verify_submission_clean_room,
)


def _write_test_archive(path: Path, members: list[tuple[tarfile.TarInfo, bytes]]) -> None:
    with tarfile.open(path, mode="w:gz") as archive:
        for member, data in members:
            archive.addfile(member, io.BytesIO(data))


def test_repeated_builds_have_identical_content_hash_and_valid_manifest(tmp_path: Path) -> None:
    first_path = tmp_path / "first"
    second_path = tmp_path / "second"
    first = build_submission(first_path)
    second = build_submission(second_path)

    assert first["content_hash"] == second["content_hash"]
    assert first["archive"]["sha256"] == second["archive"]["sha256"]
    assert (first_path / ARCHIVE_NAME).read_bytes() == (second_path / ARCHIVE_NAME).read_bytes()
    assert first["agent_identity"] == "rule-agent-v0"
    assert first["source_revision"]["commit"]
    assert [record["path"] for record in first["files"]] == [
        "main.py",
        "deck.csv",
        "agents/__init__.py",
        "agents/rule_agent.py",
    ]
    assert validate_submission_archive(first_path / ARCHIVE_NAME) == first["files"]
    assert validate_artifact(first_path)["content_hash"] == first["content_hash"]


def test_manifest_validation_detects_runtime_tampering(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact"
    build_submission(artifact)
    (artifact / "deck.csv").write_text("1\n" * 60, encoding="utf-8")

    with pytest.raises(ArtifactValidationError, match="hashes or sizes"):
        validate_artifact(artifact)


def test_manifest_validation_detects_tarball_tampering(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact"
    build_submission(artifact)
    archive = artifact / ARCHIVE_NAME
    archive.write_bytes(archive.read_bytes() + b"tamper")

    with pytest.raises(ArtifactValidationError, match="tar.gz hash"):
        validate_artifact(artifact)


def test_path_traversal_and_symlink_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(ArtifactValidationError, match="unsafe artifact path"):
        _safe_relative_path("../deck.csv")

    artifact = tmp_path / "artifact"
    build_submission(artifact)
    (artifact / "escape.py").symlink_to(artifact / "main.py")

    with pytest.raises(ArtifactValidationError, match="unlisted, missing, or symlinked"):
        validate_artifact(artifact)


@pytest.mark.parametrize(
    ("member", "expected"),
    [
        (tarfile.TarInfo("../deck.csv"), "unsafe artifact path"),
        (tarfile.TarInfo("main.py"), "duplicate member"),
        (tarfile.TarInfo("link"), "regular file, not a link or directory"),
    ],
)
def test_archive_validator_rejects_unsafe_members(
    tmp_path: Path, member: tarfile.TarInfo, expected: str
) -> None:
    archive = tmp_path / "unsafe.tar.gz"
    member.size = 1
    if member.name == "link":
        member.type = tarfile.SYMTYPE
        member.linkname = "main.py"
    members = [(member, b"x")]
    if member.name == "main.py":
        duplicate = tarfile.TarInfo("main.py")
        duplicate.size = 1
        members.append((duplicate, b"y"))
    _write_test_archive(archive, members)

    with pytest.raises(ArtifactValidationError, match=expected):
        validate_submission_archive(archive)


def test_secret_scan_is_clean_for_artifact_and_flags_obvious_marker(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact"
    build_submission(artifact)
    assert scan_artifact_secrets(artifact) == []

    (artifact / "secret.txt").write_text("KAGGLE_KEY=not-a-real-token\n", encoding="utf-8")
    assert scan_artifact_secrets(artifact) == ["secret.txt"]


def test_clean_room_import_uses_only_extracted_tarball_and_keeps_rule_v0_contract(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact"
    build_submission(artifact)

    verification = verify_submission_artifact(artifact)
    assert verify_submission_clean_room(artifact) == {"deck_size": 60, "mandatory": [0, 1]}
    assert verification["tar_gz_sha256"] == hashlib.sha256(
        (artifact / ARCHIVE_NAME).read_bytes()
    ).hexdigest()
