"""Archive tests: content-addressed dedup, atomic write, quarantine, corruption.

Also covers SourceEnvelope provenance round-tripping through the manifest
JSON file, since it depends directly on the archive/atomic-write layer.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mage_ptcg.competition_intelligence import archive
from mage_ptcg.competition_intelligence.canonical import sha256_hex
from mage_ptcg.competition_intelligence.contracts import ContractError
from mage_ptcg.competition_intelligence.provenance import (
    build_source_envelope,
    envelope_from_manifest_payload,
    read_source_manifest,
    source_manifest_path,
    write_source_manifest,
)


class TestContentAddressedStore:
    def test_store_and_read_round_trip(self, tmp_path: Path) -> None:
        digest = archive.store_raw(tmp_path, b"hello world")
        assert digest == sha256_hex(b"hello world")
        assert archive.read_raw(tmp_path, digest) == b"hello world"

    def test_storing_identical_bytes_twice_is_idempotent(self, tmp_path: Path) -> None:
        first = archive.store_raw(tmp_path, b"same content")
        second = archive.store_raw(tmp_path, b"same content")
        assert first == second
        # exactly one blob on disk for this hash
        assert archive.raw_path(tmp_path, first).exists()

    def test_read_missing_blob_raises(self, tmp_path: Path) -> None:
        with pytest.raises(archive.ArchiveError):
            archive.read_raw(tmp_path, "a" * 64)

    def test_read_detects_on_disk_corruption(self, tmp_path: Path) -> None:
        digest = archive.store_raw(tmp_path, b"original content")
        path = archive.raw_path(tmp_path, digest)
        path.write_bytes(b"corrupted!!")
        with pytest.raises(archive.ArchiveError):
            archive.read_raw(tmp_path, digest)

    def test_rejects_non_hex_digest_path(self, tmp_path: Path) -> None:
        with pytest.raises(archive.ArchiveError):
            archive.read_raw(tmp_path, "not-a-valid-hash")

    def test_atomic_write_leaves_no_temp_file_behind(self, tmp_path: Path) -> None:
        archive.store_raw(tmp_path, b"payload")
        leftovers = list((tmp_path / "raw").rglob(".*.tmp"))
        assert leftovers == []


class TestQuarantine:
    def test_quarantine_writes_content_and_reason(self, tmp_path: Path) -> None:
        digest = archive.quarantine_bytes(tmp_path, b'{"token": "abcdefgh12345678"}', reason="secret_scan_hit")
        content_path = archive.quarantine_root(tmp_path) / digest[:2] / digest / "content.bin"
        reason_path = archive.quarantine_root(tmp_path) / digest[:2] / digest / "reason.json"
        assert content_path.exists()
        reason = json.loads(reason_path.read_text(encoding="utf-8"))
        assert reason["reason"] == "secret_scan_hit"

    def test_quarantined_content_not_in_raw_archive(self, tmp_path: Path) -> None:
        data = b'{"password": "hunter2hunter2"}'
        digest = archive.quarantine_bytes(tmp_path, data, reason="secret_scan_hit")
        assert not archive.raw_path(tmp_path, digest).exists()


class TestScanBeforeArchive:
    def test_clean_json_is_safe(self) -> None:
        is_safe, labels = archive.scan_before_archive(json.dumps({"turn": 1, "phase": "OPENING"}).encode("utf-8"))
        assert is_safe
        assert labels == ()

    def test_json_with_secret_key_is_unsafe(self) -> None:
        is_safe, labels = archive.scan_before_archive(json.dumps({"api_key": "sk-abcdefghijklmnop"}).encode("utf-8"))
        assert not is_safe
        assert any("sensitive_key" in label for label in labels)

    def test_non_json_bytes_are_treated_as_opaque_and_safe(self) -> None:
        is_safe, labels = archive.scan_before_archive(b"\xff\xfe\x00\x01binary-not-json")
        assert is_safe
        assert labels == ()


class TestSourceEnvelopeProvenance:
    def _build(self, **overrides: object):
        fields = dict(
            source_id="local:fixture-1",
            source_kind="LOCAL_SELFPLAY",
            acquisition_mode="LOCAL_ONLY",
            acquired_at="2026-07-18T00:00:00Z",
            origin_reference="fixture.json",
            owner_scope="self",
            visibility="private",
            allowed_uses=["ARCHIVE", "ANALYSIS"],
            raw_sha256="b" * 64,
            parser_version="v1",
            redaction_version="v1",
        )
        fields.update(overrides)
        return build_source_envelope(**fields)

    def test_build_rejects_single_string_for_allowed_uses(self) -> None:
        with pytest.raises(ContractError):
            self._build(allowed_uses="ARCHIVE")  # a string is iterable char-by-char; must be rejected

    def test_write_and_read_round_trip(self, tmp_path: Path) -> None:
        envelope = self._build()
        path = write_source_manifest(tmp_path, envelope)
        assert path == source_manifest_path(tmp_path, envelope.source_id)
        reloaded = read_source_manifest(tmp_path, envelope.source_id)
        assert reloaded.content_hash() == envelope.content_hash()

    def test_manifest_filename_is_hash_derived_not_raw_source_id(self, tmp_path: Path) -> None:
        malicious_id = "../../etc/passwd"
        envelope = self._build(source_id=malicious_id)
        path = write_source_manifest(tmp_path, envelope)
        # must stay inside source_manifests/, never escape via the raw id
        assert path.parent == tmp_path / "source_manifests"
        assert path.resolve().is_relative_to((tmp_path / "source_manifests").resolve())

    def test_tampered_content_hash_is_rejected_on_load(self, tmp_path: Path) -> None:
        envelope = self._build()
        path = write_source_manifest(tmp_path, envelope)
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["content_hash"] = "0" * 64
        path.write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(ContractError):
            read_source_manifest(tmp_path, envelope.source_id)

    def test_envelope_from_manifest_payload_rejects_missing_field(self) -> None:
        envelope = self._build()
        payload = envelope.content_payload()
        del payload["origin_reference"]
        with pytest.raises(ContractError):
            envelope_from_manifest_payload(payload)
