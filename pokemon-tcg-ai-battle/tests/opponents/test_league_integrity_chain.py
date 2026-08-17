from __future__ import annotations

from pathlib import Path

from mage_ptcg.opponents.league_integrity_chain import (
    build_run_manifest,
    compute_run_root_sha256,
    load_trusted_root_entry,
    write_trusted_root_entry,
)


def test_compute_run_root_sha256_changes_on_any_file_change(tmp_path: Path):
    (tmp_path / "a.json").write_text('{"x": 1}', encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.json").write_text('{"y": 2}', encoding="utf-8")
    before = compute_run_root_sha256(tmp_path, exclude={"run_root.sha256"})
    (tmp_path / "sub" / "b.json").write_text('{"y": 3}', encoding="utf-8")
    after = compute_run_root_sha256(tmp_path, exclude={"run_root.sha256"})
    assert before != after


def test_compute_run_root_sha256_changes_on_file_insertion_or_deletion(tmp_path: Path):
    (tmp_path / "a.json").write_text('{"x": 1}', encoding="utf-8")
    before = compute_run_root_sha256(tmp_path, exclude={"run_root.sha256"})
    (tmp_path / "b.json").write_text('{"z": 1}', encoding="utf-8")
    after = compute_run_root_sha256(tmp_path, exclude={"run_root.sha256"})
    assert before != after


def test_run_manifest_contains_required_fields():
    manifest = build_run_manifest(
        run_id="run-1", sorted_game_ids=["g1", "g2"], game_manifest_hashes={"g1": "h1", "g2": "h2"},
        summary_hash="sh", participant_ids=["a", "b"], population_id="pop-1", team_bundle_hashes={"a": "bh"},
        ruleset_version="rv1", cabt_version="cv1", evidence_format_version="ev1",
    )
    for key in ("run_id", "schema_version", "canonicalization_version", "sorted_game_ids", "game_manifest_hashes",
                "summary_hash", "participant_ids", "population_id", "team_bundle_hashes", "ruleset_version",
                "cabt_version", "evidence_format_version"):
        assert key in manifest


def test_trusted_root_round_trip(tmp_path: Path):
    registry = tmp_path / "roots.json"
    write_trusted_root_entry(registry, run_id="run-1", run_root_sha256="abc", source_commit="deadbeef",
                              population_id="pop-1", evidence_schema="o6-public-trajectory-v1")
    entry = load_trusted_root_entry(registry, "run-1")
    assert entry is not None
    assert entry["run_root_sha256"] == "abc"
    assert entry["status"] == "TRUSTED"


def test_trusted_root_missing_run_id_returns_none(tmp_path: Path):
    registry = tmp_path / "roots.json"
    write_trusted_root_entry(registry, run_id="run-1", run_root_sha256="abc", source_commit="deadbeef",
                              population_id="pop-1", evidence_schema="o6-public-trajectory-v1")
    assert load_trusted_root_entry(registry, "run-does-not-exist") is None


def test_write_trusted_root_entry_replaces_same_run_id(tmp_path: Path):
    registry = tmp_path / "roots.json"
    write_trusted_root_entry(registry, run_id="run-1", run_root_sha256="abc", source_commit="deadbeef",
                              population_id="pop-1", evidence_schema="o6-public-trajectory-v1")
    write_trusted_root_entry(registry, run_id="run-1", run_root_sha256="def", source_commit="cafebabe",
                              population_id="pop-1", evidence_schema="o6-public-trajectory-v1")
    entry = load_trusted_root_entry(registry, "run-1")
    assert entry["run_root_sha256"] == "def"
