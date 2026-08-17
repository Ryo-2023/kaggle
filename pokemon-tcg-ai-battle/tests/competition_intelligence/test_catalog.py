"""Catalog tests: rebuildability from canonical artifacts, corruption tolerance."""

from __future__ import annotations

from pathlib import Path

import pytest

from mage_ptcg.competition_intelligence import catalog
from mage_ptcg.competition_intelligence.provenance import build_source_envelope, write_source_manifest
from mage_ptcg.competition_intelligence.runstate import RunPaths


def _write_source(run_root: Path, source_id: str) -> None:
    envelope = build_source_envelope(
        source_id=source_id,
        source_kind="LOCAL_SELFPLAY",
        acquisition_mode="LOCAL_ONLY",
        acquired_at="2026-07-18T00:00:00Z",
        origin_reference="fixture.json",
        owner_scope="self",
        visibility="private",
        allowed_uses=["ARCHIVE", "ANALYSIS"],
        raw_sha256="c" * 64,
        parser_version="v1",
        redaction_version="v1",
    )
    write_source_manifest(run_root, envelope)


class TestRebuildCatalog:
    def test_rebuild_from_scratch_indexes_all_sources(self, tmp_path: Path) -> None:
        _write_source(tmp_path, "src-a")
        _write_source(tmp_path, "src-b")
        result = catalog.rebuild_catalog(tmp_path)
        assert result["source_count"] == 2
        summary = catalog.catalog_summary(tmp_path)
        assert summary["source_count"] == "2"

    def test_deleting_catalog_and_rebuilding_reproduces_same_counts(self, tmp_path: Path) -> None:
        _write_source(tmp_path, "src-a")
        first = catalog.rebuild_catalog(tmp_path)
        paths = RunPaths(tmp_path)
        paths.catalog_db.unlink()
        second = catalog.rebuild_catalog(tmp_path)
        assert first == second

    def test_rebuild_with_no_sources_yet_is_not_an_error(self, tmp_path: Path) -> None:
        result = catalog.rebuild_catalog(tmp_path)
        assert result["source_count"] == 0

    def test_corrupt_manifest_is_quarantined_from_catalog_not_fatal(self, tmp_path: Path) -> None:
        _write_source(tmp_path, "src-a")
        paths = RunPaths(tmp_path)
        (paths.source_manifests / "corrupt.json").write_text("{not valid json", encoding="utf-8")
        result = catalog.rebuild_catalog(tmp_path)
        assert result["source_count"] == 1
        assert result["quarantined_manifest_count"] == 1

    def test_summary_without_prior_rebuild_raises(self, tmp_path: Path) -> None:
        with pytest.raises(catalog.CatalogError):
            catalog.catalog_summary(tmp_path)

    def test_no_leftover_temp_sqlite_file(self, tmp_path: Path) -> None:
        _write_source(tmp_path, "src-a")
        catalog.rebuild_catalog(tmp_path)
        paths = RunPaths(tmp_path)
        leftovers = list(paths.state.glob(".catalog.*.tmp.sqlite3"))
        assert leftovers == []
