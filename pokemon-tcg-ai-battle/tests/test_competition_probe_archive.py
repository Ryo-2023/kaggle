"""Archive safety and atomic publication tests for C2b."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mage_ptcg.competition.archive import ArchiveSafetyError, DuplicateProbeError, archive_probe


def archive(tmp_path: Path, *, probe_id: str = "probe", response: bytes | None = b'{"ok":true}', response_json: object | None = {"ok": True}) -> Path:
    return archive_probe(
        output_dir=tmp_path,
        probe_id=probe_id,
        manifest={"kind": "test"},
        summary={"response_content_type": "application/json"},
        response=response,
        response_json=response_json,
        error=None,
    )


def test_archive_has_expected_safe_tree(tmp_path: Path) -> None:
    result = archive(tmp_path)
    assert (result / "manifest.json").is_file()
    assert (result / "summary.json").is_file()
    assert (result / "schema-fingerprint.json").is_file()
    assert (result / "raw" / "response.bin").read_bytes() == b'{"ok":true}'
    assert json.loads((result / "redacted" / "response.json").read_text()) == {"ok": True}


def test_sensitive_raw_is_quarantined_but_redacted_copy_is_safe(tmp_path: Path) -> None:
    raw = b'{"token":"super-secret-value","email":"user@example.com","url":"https://x/?X-Amz-Signature=abc"}'
    parsed = json.loads(raw)
    result = archive(tmp_path, response=raw, response_json=parsed)
    assert not (result / "raw" / "response.bin").exists()
    assert (result / "quarantine" / "response.bin").read_bytes() == raw
    redacted = (result / "redacted" / "response.json").read_text()
    assert "super-secret-value" not in redacted
    assert "user@example.com" not in redacted
    assert "X-Amz-Signature=abc" not in redacted


def test_duplicate_and_path_traversal_are_rejected(tmp_path: Path) -> None:
    archive(tmp_path)
    with pytest.raises(DuplicateProbeError):
        archive(tmp_path)
    with pytest.raises(ArchiveSafetyError):
        archive(tmp_path, probe_id="../escape")


def test_archive_failure_does_not_publish_partial_final_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from mage_ptcg.competition import archive as archive_module

    original = archive_module._write_file

    def fail_on_summary(root: Path, relative: str, content: bytes) -> None:
        if relative == "summary.json":
            raise ArchiveSafetyError("injected write failure")
        original(root, relative, content)

    monkeypatch.setattr(archive_module, "_write_file", fail_on_summary)
    with pytest.raises(ArchiveSafetyError, match="injected"):
        archive_probe(
            output_dir=tmp_path,
            probe_id="bad",
            manifest={},
            summary={"response_content_type": "application/json"},
            response=b'{"ok": true}',
            response_json={"ok": True},
            error=None,
        )
    assert not (tmp_path / "bad").exists()
    assert not list(tmp_path.glob(".bad.tmp-*"))
