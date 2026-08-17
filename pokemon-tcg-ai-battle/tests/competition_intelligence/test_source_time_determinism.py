"""Tests for independent-audit finding #4: ingestion-time non-determinism.

``SourceEnvelope.acquired_at`` is part of the envelope's content-derived
identity (``content_hash()``). Silently substituting the ingestion tool's
current wall-clock time whenever a caller omitted it made re-ingesting
byte-identical content produce a different ``SourceEnvelope`` identity
depending on *when* ingestion happened to run. This is now fail-closed:
callers must supply the source's own declared time explicitly.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import pytest
import yaml

from mage_ptcg.competition_intelligence.contracts import AllowedUse, SourceKind
from mage_ptcg.competition_intelligence.local_ingest import IngestError, ingest_local_file
from mage_ptcg.competition_intelligence.provenance import SourceTimeError, require_declared_time
from mage_ptcg.competition_intelligence.team_bundle import TeamBundleError, import_team_bundle


def _fixture(tmp_path: Path, name: str = "fixture.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps({"turn": 1, "phase": "OPENING"}), encoding="utf-8")
    return path


def _team_bundle(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    note_bytes = b"a team note"
    (root / "note.txt").write_bytes(note_bytes)
    manifest = {
        "bundle_version": "team-bundle-v1",
        "source_kind": "TEAM_SHARED",
        "owner": "teammate-1",
        "permission_statement": "shared for internal analysis",
        "allowed_uses": ["ARCHIVE", "ANALYSIS"],
        "files": [{"path": "note.txt", "sha256": hashlib.sha256(note_bytes).hexdigest()}],
    }
    (root / "manifest.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    return root


class TestRequireDeclaredTimeHelper:
    def test_missing_value_raises(self) -> None:
        with pytest.raises(SourceTimeError):
            require_declared_time(None, field_name="acquired_at", context="test")

    def test_empty_string_raises(self) -> None:
        with pytest.raises(SourceTimeError):
            require_declared_time("", field_name="acquired_at", context="test")

    def test_provided_value_is_returned_unchanged(self) -> None:
        assert require_declared_time("2026-07-18T00:00:00Z", field_name="acquired_at", context="test") == "2026-07-18T00:00:00Z"


class TestLocalIngestFailClosed:
    def test_missing_acquired_at_is_fail_closed_not_silently_defaulted(self, tmp_path: Path) -> None:
        fixture = _fixture(tmp_path)
        with pytest.raises(IngestError):
            ingest_local_file(tmp_path / "run", fixture, source_id="fixture-1")

    def test_explicit_acquired_at_produces_identical_identity_regardless_of_real_time(self, tmp_path: Path) -> None:
        fixture = _fixture(tmp_path)
        first = ingest_local_file(
            tmp_path / "run-a", fixture, source_id="fixture-1", acquired_at="2026-07-18T00:00:00Z",
        )
        time.sleep(1.1)  # real wall-clock time genuinely advances between the two calls
        second = ingest_local_file(
            tmp_path / "run-b", fixture, source_id="fixture-1", acquired_at="2026-07-18T00:00:00Z",
        )
        # Same declared acquired_at + same content -> identical content_hash,
        # independent of when the ingestion tool actually ran.
        assert first["content_hash"] == second["content_hash"]

    def test_different_declared_acquired_at_changes_identity(self, tmp_path: Path) -> None:
        fixture = _fixture(tmp_path)
        first = ingest_local_file(
            tmp_path / "run-a", fixture, source_id="fixture-1", acquired_at="2026-07-18T00:00:00Z",
        )
        second = ingest_local_file(
            tmp_path / "run-b", fixture, source_id="fixture-1", acquired_at="2026-08-01T00:00:00Z",
        )
        assert first["content_hash"] != second["content_hash"]

    def test_ingested_at_is_present_as_operational_metadata_only(self, tmp_path: Path) -> None:
        fixture = _fixture(tmp_path)
        result = ingest_local_file(
            tmp_path / "run", fixture, source_id="fixture-1", acquired_at="2026-07-18T00:00:00Z",
        )
        assert "ingested_at" in result
        # ingested_at is real-clock-derived and must be a valid ISO-8601 stamp,
        # but is never part of content_hash (see the determinism test above).
        assert result["ingested_at"].endswith("Z")


class TestTeamBundleFailClosed:
    def test_missing_created_at_is_fail_closed_not_silently_defaulted(self, tmp_path: Path) -> None:
        bundle = _team_bundle(tmp_path / "bundle")
        with pytest.raises(TeamBundleError):
            import_team_bundle(bundle, tmp_path / "run")

    def test_explicit_created_at_produces_identical_identity_regardless_of_real_time(self, tmp_path: Path) -> None:
        bundle = _team_bundle(tmp_path / "bundle")
        first = import_team_bundle(bundle, tmp_path / "run-a", created_at="2026-07-18T00:00:00Z")
        time.sleep(1.1)
        second_bundle = _team_bundle(tmp_path / "bundle-copy")
        second = import_team_bundle(second_bundle, tmp_path / "run-b", created_at="2026-07-18T00:00:00Z")
        # Both bundles have identical manifest+file content -> identical
        # source_id (content-derived) regardless of when each ran.
        assert first.source_id == second.source_id


class TestObservedAtStillAffectsIdentityWhenDeclared:
    def test_changing_observed_at_changes_content_hash(self) -> None:
        from mage_ptcg.competition_intelligence.provenance import build_source_envelope

        base_kwargs = dict(
            source_id="src-1", source_kind=SourceKind.LOCAL_SELFPLAY, acquisition_mode="LOCAL_ONLY",
            acquired_at="2026-07-18T00:00:00Z", origin_reference="x", owner_scope="self", visibility="private",
            allowed_uses=[AllowedUse.ARCHIVE.value], raw_sha256="a" * 64, parser_version="v1", redaction_version="v1",
        )
        without_observed = build_source_envelope(**base_kwargs)
        with_observed = build_source_envelope(observed_at="2026-07-19T00:00:00Z", **base_kwargs)
        assert without_observed.content_hash() != with_observed.content_hash()
