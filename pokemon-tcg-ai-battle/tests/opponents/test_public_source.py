"""Tests for the hardened metadata-only Public Opponent Source intake (O6 Phase B)."""
from __future__ import annotations

import inspect
import io
import json
import tarfile
from pathlib import Path

import pytest

from mage_ptcg.competition_intelligence.canonical import sha256_hex
from mage_ptcg.opponents.core import OpponentError
from mage_ptcg.opponents import public_source as ps


def _source_files(*, source_id, code_availability="EXACT", deck_fidelity="EXACT", explicit_license="UNKNOWN",
                   policies=None, usability="REVIEW_REQUIRED", card_ids=None):
    card_ids = card_ids if card_ids is not None else list(range(1, 61))
    policies = policies or dict(ps.UNKNOWN_LICENSE_SCOPE_DECISIONS)
    # translate O6 scope names back to the corpus's own policy key vocabulary
    reverse = {v: k for k, v in ps._POLICY_TO_SCOPE.items()}
    corpus_policies = {reverse[scope]: value for scope, value in policies.items()}
    manifest = {
        "author": "UNKNOWN", "behavior_manifest_ref": "behavior.json", "code_manifest_ref": "code.json",
        "collector_name": "public_opponent_collector", "collector_version": "1.0.0", "deck_manifest_ref": "deck.json",
        "immutable_source_version": "1.0.0", "permissions_manifest_ref": "permissions.json",
        "provenance_manifest_ref": "provenance.json", "published_at": "2026-07-21T00:00:00Z",
        "retrieved_at": "2026-07-21T18:00:00+09:00", "schema_version": ps.CORPUS_SCHEMA_VERSION,
        "source_id": source_id, "source_ref": f"https://example.invalid/{source_id}", "source_type": "Kaggle Public Notebook",
        "source_url": f"https://example.invalid/{source_id}", "technical_validation_manifest_ref": "technical_validation.json",
        "visibility": "PUBLIC",
    }
    return {
        "source_manifest.json": manifest,
        "code.json": {"code_availability": code_availability, "entrypoint_candidate": "main.py" if code_availability == "EXACT" else None},
        "deck.json": {"card_hash": sha256_hex(json.dumps(card_ids).encode()), "card_ids": card_ids, "deck_fidelity": deck_fidelity},
        "behavior.json": {"behavior_fidelity": "EXACT_CODE", "usability_classification": usability, "usability_reason": "test fixture"},
        "provenance.json": {"explicit_license": explicit_license, "original_url": manifest["source_url"], "policies": corpus_policies, "source_id": source_id},
        "permissions.json": {"policies": corpus_policies},
        "technical_validation.json": {"cabt_smoke": "NOT_RUN", "isolated_import": "NOT_RUN", "legal_action": "NOT_RUN", "runtime_compatibility": "NOT_RUN", "state_leakage": "NOT_RUN"},
        "deck_validation.json": {"is_legal": True, "issues": [], "mismatches": [], "total_count": len(card_ids)},
    }


def _write_corpus(root: Path, *, source_ids, blocked=None, overrides=None, schema_version=None, per_source_overrides=None):
    root.mkdir(parents=True, exist_ok=True)
    (root / "sources").mkdir(exist_ok=True)
    per_source_overrides = per_source_overrides or {}
    source_package_hashes = {}
    for source_id in source_ids:
        kwargs = per_source_overrides.get(source_id, {})
        files = _source_files(source_id=source_id, **kwargs)
        source_dir = root / "sources" / source_id
        source_dir.mkdir(parents=True, exist_ok=True)
        for name, content in files.items():
            (source_dir / name).write_text(json.dumps(content, sort_keys=True), encoding="utf-8")
        source_package_hashes[source_id] = sha256_hex((source_dir / "source_manifest.json").read_bytes())

    manifest = {
        "collector_version": "1.0.0", "generated_at": "2026-07-21T09:00:00+00:00",
        "included_source_ids": list(source_ids), "permission_status": "REVIEW_REQUIRED",
        "schema_version": schema_version or ps.CORPUS_SCHEMA_VERSION,
        "source_package_hashes": source_package_hashes,
        "deck_fidelity_counts": {"exact": len(source_ids), "partial": 0, "reconstructed": 0},
        "usability_counts": {"BLOCKED": 0, "DECK_STANDARD_PILOT_CANDIDATE": 0, "NATIVE_OPPONENT_CANDIDATE": 0, "REVIEW_REQUIRED": len(source_ids), "SURROGATE_CANDIDATE": 0},
        "known_limitations": ["Offline mode fallback used; Live Kaggle sync functions not completed"],
        "corpus_semantic_hash": "test-semantic-hash",
    }
    (root / "corpus_manifest.json").write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
    (root / "source_index.json").write_text("{}", encoding="utf-8")
    (root / "deck_registry.json").write_text("{}", encoding="utf-8")
    (root / "classification_registry.json").write_text("{}", encoding="utf-8")
    (root / "provenance_summary.json").write_text("{}", encoding="utf-8")
    (root / "permission_summary.json").write_text("{}", encoding="utf-8")
    (root / "validation_summary.json").write_text("{}", encoding="utf-8")
    (root / "technical_validation_summary.json").write_text("{}", encoding="utf-8")
    (root / "blocked_sources.json").write_text(json.dumps(sorted(blocked or [])), encoding="utf-8")
    (root / "collector_run_manifest.json").write_text("{}", encoding="utf-8")
    (root / "README.md").write_text("# fixture corpus\n", encoding="utf-8")
    (root / "review_override.json").write_text(json.dumps(overrides or {}, sort_keys=True), encoding="utf-8")

    checksum_targets = ["corpus_manifest.json", "source_index.json", "deck_registry.json", "classification_registry.json",
                         "provenance_summary.json", "permission_summary.json", "validation_summary.json",
                         "technical_validation_summary.json", "blocked_sources.json", "collector_run_manifest.json",
                         "README.md", "review_override.json"]
    lines = [f"{name}:{sha256_hex((root / name).read_bytes())}" for name in checksum_targets]
    (root / "checksums.sha256").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return root


# ---------------------------------------------------------------------------
# Schema / Import
# ---------------------------------------------------------------------------

def test_valid_corpus_imports_cleanly(tmp_path):
    corpus = _write_corpus(tmp_path / "corpus", source_ids=["alpha_source", "beta_source"])
    report = ps.import_public_source_corpus(corpus_root=corpus, output_dir=tmp_path / "out")
    assert report["imported"] == ["alpha_source", "beta_source"]
    assert report["unchanged"] == [] and report["rejected"] == []
    records = ps.list_public_sources(output_dir=tmp_path / "out")
    assert {r["source_id"] for r in records} == {"alpha_source", "beta_source"}


def test_unknown_schema_rejected(tmp_path):
    corpus = _write_corpus(tmp_path / "corpus", source_ids=["alpha_source"], schema_version="not-a-real-schema")
    with pytest.raises(OpponentError, match="unsupported public source corpus schema_version"):
        ps.import_public_source_corpus(corpus_root=corpus, output_dir=tmp_path / "out")


def test_future_schema_rejected(tmp_path):
    corpus = _write_corpus(tmp_path / "corpus", source_ids=["alpha_source"], schema_version="o6-public-source-corpus-v2")
    with pytest.raises(OpponentError, match="unsupported public source corpus schema_version"):
        ps.import_public_source_corpus(corpus_root=corpus, output_dir=tmp_path / "out")


def test_missing_split_json_rejected(tmp_path):
    corpus = _write_corpus(tmp_path / "corpus", source_ids=["alpha_source"])
    (corpus / "sources" / "alpha_source" / "behavior.json").unlink()
    with pytest.raises(OpponentError, match="missing"):
        ps.import_public_source_corpus(corpus_root=corpus, output_dir=tmp_path / "out")


def test_hash_mismatch_rejected(tmp_path):
    corpus = _write_corpus(tmp_path / "corpus", source_ids=["alpha_source"])
    manifest_path = corpus / "corpus_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["source_package_hashes"]["alpha_source"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest, sort_keys=True))
    # corpus-level checksums no longer match the mutated manifest either, but we
    # want to isolate the *source*-hash-mismatch path, so recompute those first.
    checksum_targets = ["corpus_manifest.json", "source_index.json", "deck_registry.json", "classification_registry.json",
                         "provenance_summary.json", "permission_summary.json", "validation_summary.json",
                         "technical_validation_summary.json", "blocked_sources.json", "collector_run_manifest.json",
                         "README.md", "review_override.json"]
    lines = [f"{name}:{sha256_hex((corpus / name).read_bytes())}" for name in checksum_targets]
    (corpus / "checksums.sha256").write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(OpponentError, match="hash mismatch"):
        ps.import_public_source_corpus(corpus_root=corpus, output_dir=tmp_path / "out")


def test_top_level_checksum_mismatch_rejected(tmp_path):
    corpus = _write_corpus(tmp_path / "corpus", source_ids=["alpha_source"])
    (corpus / "README.md").write_text("# tampered\n", encoding="utf-8")
    with pytest.raises(OpponentError, match="checksum mismatch"):
        ps.import_public_source_corpus(corpus_root=corpus, output_dir=tmp_path / "out")


def test_semantic_hash_mismatch_detected_by_verify_metadata(tmp_path):
    corpus = _write_corpus(tmp_path / "corpus", source_ids=["alpha_source"])
    out = tmp_path / "out"
    ps.import_public_source_corpus(corpus_root=corpus, output_dir=out)
    record_path = out / "public_sources" / "alpha_source.json"
    record = json.loads(record_path.read_text())
    record["candidate_state"] = "NATIVE_OPPONENT_CANDIDATE"  # tamper the record at rest, hash untouched
    record_path.write_text(json.dumps(record))
    with pytest.raises(OpponentError, match="hash mismatch"):
        ps.verify_public_source_metadata(output_dir=out)


def test_source_order_independence(tmp_path):
    corpus_a = _write_corpus(tmp_path / "corpus_a", source_ids=["alpha_source", "beta_source"])
    corpus_b = _write_corpus(tmp_path / "corpus_b", source_ids=["beta_source", "alpha_source"])
    ps.import_public_source_corpus(corpus_root=corpus_a, output_dir=tmp_path / "out_a")
    ps.import_public_source_corpus(corpus_root=corpus_b, output_dir=tmp_path / "out_b")
    hashes_a = {r["source_id"]: r["source_metadata_hash"] for r in ps.list_public_sources(output_dir=tmp_path / "out_a")}
    hashes_b = {r["source_id"]: r["source_metadata_hash"] for r in ps.list_public_sources(output_dir=tmp_path / "out_b")}
    assert hashes_a == hashes_b


def test_idempotent_reimport(tmp_path):
    corpus = _write_corpus(tmp_path / "corpus", source_ids=["alpha_source"])
    out = tmp_path / "out"
    first = ps.import_public_source_corpus(corpus_root=corpus, output_dir=out)
    assert first["imported"] == ["alpha_source"]
    second = ps.import_public_source_corpus(corpus_root=corpus, output_dir=out)
    assert second["imported"] == [] and second["unchanged"] == ["alpha_source"]


def test_same_source_id_different_content_rejected(tmp_path):
    corpus = _write_corpus(tmp_path / "corpus", source_ids=["alpha_source"])
    out = tmp_path / "out"
    ps.import_public_source_corpus(corpus_root=corpus, output_dir=out)
    # Re-derive a corpus with the same source_id but a materially different behavior.json.
    mutated = _write_corpus(tmp_path / "corpus_mutated", source_ids=["alpha_source"],
                             per_source_overrides={"alpha_source": {"usability": "BLOCKED"}})
    with pytest.raises(OpponentError, match="already imported with different content"):
        ps.import_public_source_corpus(corpus_root=mutated, output_dir=out)


def test_generated_at_timestamp_does_not_affect_reimport(tmp_path):
    corpus = _write_corpus(tmp_path / "corpus", source_ids=["alpha_source"])
    out = tmp_path / "out"
    ps.import_public_source_corpus(corpus_root=corpus, output_dir=out)
    manifest_path = corpus / "corpus_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["generated_at"] = "2099-01-01T00:00:00+00:00"
    manifest_path.write_text(json.dumps(manifest, sort_keys=True))
    checksum_targets = ["corpus_manifest.json", "source_index.json", "deck_registry.json", "classification_registry.json",
                         "provenance_summary.json", "permission_summary.json", "validation_summary.json",
                         "technical_validation_summary.json", "blocked_sources.json", "collector_run_manifest.json",
                         "README.md", "review_override.json"]
    lines = [f"{name}:{sha256_hex((corpus / name).read_bytes())}" for name in checksum_targets]
    (corpus / "checksums.sha256").write_text("\n".join(lines) + "\n", encoding="utf-8")
    second = ps.import_public_source_corpus(corpus_root=corpus, output_dir=out)
    assert second["unchanged"] == ["alpha_source"]


def test_unsafe_source_id_rejected(tmp_path):
    corpus = _write_corpus(tmp_path / "corpus", source_ids=["alpha_source"])
    manifest_path = corpus / "corpus_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["included_source_ids"] = ["../../etc/passwd"]
    manifest_path.write_text(json.dumps(manifest, sort_keys=True))
    checksum_targets = ["corpus_manifest.json", "source_index.json", "deck_registry.json", "classification_registry.json",
                         "provenance_summary.json", "permission_summary.json", "validation_summary.json",
                         "technical_validation_summary.json", "blocked_sources.json", "collector_run_manifest.json",
                         "README.md", "review_override.json"]
    lines = [f"{name}:{sha256_hex((corpus / name).read_bytes())}" for name in checksum_targets]
    (corpus / "checksums.sha256").write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(OpponentError, match="unsafe source_id"):
        ps.import_public_source_corpus(corpus_root=corpus, output_dir=tmp_path / "out")


# ---------------------------------------------------------------------------
# Permission
# ---------------------------------------------------------------------------

def test_unknown_license_maps_to_mandated_scope_table(tmp_path):
    corpus = _write_corpus(tmp_path / "corpus", source_ids=["alpha_source"])
    ps.import_public_source_corpus(corpus_root=corpus, output_dir=tmp_path / "out")
    record = ps.inspect_public_source(output_dir=tmp_path / "out", source_id="alpha_source")
    assert record["permission_scopes"] == ps.UNKNOWN_LICENSE_SCOPE_DECISIONS
    assert record["permission_scopes"]["public_redistribution"] == "DENIED"
    assert record["permission_scopes"]["submission_bundle"] == "DENIED"
    assert record["permission_scopes"]["strategy_analysis"] == "ALLOWED_METADATA_ONLY"


def test_unknown_license_deviating_scopes_rejected(tmp_path):
    bad_policies = dict(ps.UNKNOWN_LICENSE_SCOPE_DECISIONS)
    bad_policies["public_redistribution"] = "ALLOWED"  # a corpus claiming UNKNOWN license but ALLOWED public redistribution is inconsistent
    corpus = _write_corpus(tmp_path / "corpus", source_ids=["alpha_source"], per_source_overrides={"alpha_source": {"policies": bad_policies}})
    with pytest.raises(OpponentError, match="deviate from the mandated table"):
        ps.import_public_source_corpus(corpus_root=corpus, output_dir=tmp_path / "out")


def test_check_permissions_exits_6_when_review_required(tmp_path):
    corpus = _write_corpus(tmp_path / "corpus", source_ids=["alpha_source"])
    ps.import_public_source_corpus(corpus_root=corpus, output_dir=tmp_path / "out")
    with pytest.raises(ps.PermissionReviewRequiredError) as excinfo:
        ps.check_public_source_permissions(output_dir=tmp_path / "out")
    assert excinfo.value.exit_code == 6


def test_check_permissions_passes_when_fully_allowed(tmp_path):
    allowed_policies = {scope: "ALLOWED" for scope in ("evaluation", "training_data_generation", "strategy_analysis", "team_redistribution")}
    allowed_policies["public_redistribution"] = "DENIED"
    allowed_policies["submission_bundle"] = "DENIED"
    corpus = _write_corpus(tmp_path / "corpus", source_ids=["alpha_source"],
                            per_source_overrides={"alpha_source": {"explicit_license": "MIT", "policies": allowed_policies}})
    ps.import_public_source_corpus(corpus_root=corpus, output_dir=tmp_path / "out")
    result = ps.check_public_source_permissions(output_dir=tmp_path / "out")
    assert result["review_required"] == []


def test_activation_fail_closed_no_execute_function_exists():
    """There must be no code path in this module that can run a Public Agent."""
    names = [name for name, _ in inspect.getmembers(ps, inspect.isfunction)]
    forbidden = {"execute", "activate", "build", "run", "invoke", "smoke"}
    assert forbidden.isdisjoint(set(names))


# ---------------------------------------------------------------------------
# Candidate
# ---------------------------------------------------------------------------

def test_exact_code_and_deck_never_active_without_technical_validation(tmp_path):
    allowed_policies = {scope: "ALLOWED" for scope in ("evaluation", "training_data_generation", "strategy_analysis", "team_redistribution")}
    allowed_policies["public_redistribution"] = "DENIED"; allowed_policies["submission_bundle"] = "DENIED"
    corpus = _write_corpus(tmp_path / "corpus", source_ids=["alpha_source"],
                            per_source_overrides={"alpha_source": {"explicit_license": "MIT", "policies": allowed_policies}})
    ps.import_public_source_corpus(corpus_root=corpus, output_dir=tmp_path / "out")
    record = ps.inspect_public_source(output_dir=tmp_path / "out", source_id="alpha_source")
    assert record["candidate_state"] in ps.CANDIDATE_STATES
    assert record["candidate_state"] == "NATIVE_OPPONENT_CANDIDATE"  # candidate ceiling only, never "ACTIVE"/"VALIDATED"
    assert all(v == "NOT_RUN" for v in record["technical_validation"].values())


def test_reconstructed_deck_cannot_be_native(tmp_path):
    allowed_policies = {scope: "ALLOWED" for scope in ("evaluation", "training_data_generation", "strategy_analysis", "team_redistribution")}
    allowed_policies["public_redistribution"] = "DENIED"; allowed_policies["submission_bundle"] = "DENIED"
    corpus = _write_corpus(tmp_path / "corpus", source_ids=["zzz_totally_unrelated_name"],
                            per_source_overrides={"zzz_totally_unrelated_name": {"explicit_license": "MIT", "policies": allowed_policies, "deck_fidelity": "RECONSTRUCTED"}})
    ps.import_public_source_corpus(corpus_root=corpus, output_dir=tmp_path / "out")
    record = ps.inspect_public_source(output_dir=tmp_path / "out", source_id="zzz_totally_unrelated_name")
    assert record["candidate_state"] == "DECK_STANDARD_PILOT_CANDIDATE"


def test_candidate_derivation_is_source_id_agnostic():
    """derive_candidate_state must not accept or branch on source_id at all."""
    params = set(inspect.signature(ps.derive_candidate_state).parameters)
    assert "source_id" not in params


def test_review_override_is_applied_and_audited(tmp_path):
    override = {"alpha_source": {"usability_classification": "DECK_STANDARD_PILOT_CANDIDATE", "usability_reason": "manual pilot-only override"}}
    corpus = _write_corpus(tmp_path / "corpus", source_ids=["alpha_source"], overrides=override)
    ps.import_public_source_corpus(corpus_root=corpus, output_dir=tmp_path / "out")
    record = ps.inspect_public_source(output_dir=tmp_path / "out", source_id="alpha_source")
    assert record["review_override_applied"] is True
    assert record["candidate_state"] == "DECK_STANDARD_PILOT_CANDIDATE"
    assert record["rule_derived_candidate_state"] == "REVIEW_REQUIRED"  # the un-overridden, rule-only classification is preserved for audit


def test_blocked_source_forced_blocked_even_with_override(tmp_path):
    override = {"alpha_source": {"usability_classification": "NATIVE_OPPONENT_CANDIDATE", "usability_reason": "attempted override"}}
    corpus = _write_corpus(tmp_path / "corpus", source_ids=["alpha_source"], blocked=["alpha_source"], overrides=override)
    ps.import_public_source_corpus(corpus_root=corpus, output_dir=tmp_path / "out")
    record = ps.inspect_public_source(output_dir=tmp_path / "out", source_id="alpha_source")
    assert record["candidate_state"] == "BLOCKED"
    assert record["review_override_applied"] is False  # BLOCKED is never overridable away


def test_override_requesting_forbidden_non_candidate_state_rejected(tmp_path):
    override = {"alpha_source": {"usability_classification": "VALIDATED", "usability_reason": "invalid escalation attempt"}}
    corpus = _write_corpus(tmp_path / "corpus", source_ids=["alpha_source"], overrides=override)
    with pytest.raises(OpponentError, match="forbidden non-candidate state"):
        ps.import_public_source_corpus(corpus_root=corpus, output_dir=tmp_path / "out")


def test_candidate_states_never_include_forbidden_values():
    assert set(ps.CANDIDATE_STATES).isdisjoint(ps._FORBIDDEN_CANDIDATE_STATES)


# ---------------------------------------------------------------------------
# Security (archive ingestion; the real corpus is a plain directory, but this
# module also supports an archived .tar.gz corpus package for a future
# ingestion path -- exercised here with crafted malicious archives).
# ---------------------------------------------------------------------------

def _make_tar(tmp_path: Path, members: list[tarfile.TarInfo], contents: dict[str, bytes] | None = None) -> Path:
    contents = contents or {}
    archive = tmp_path / "corpus.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        for member in members:
            data = contents.get(member.name, b"x" * member.size if member.size else b"")
            if member.isfile():
                member.size = len(data)
                tar.addfile(member, io.BytesIO(data))
            else:
                tar.addfile(member)
    return archive


def test_extract_rejects_path_traversal(tmp_path):
    info = tarfile.TarInfo(name="../escape.txt"); info.size = 1
    archive = _make_tar(tmp_path, [info])
    with pytest.raises(OpponentError):
        ps.extract_corpus_archive(archive, tmp_path / "out")


def test_extract_rejects_symlink(tmp_path):
    info = tarfile.TarInfo(name="link"); info.type = tarfile.SYMTYPE; info.linkname = "/etc/passwd"
    archive = _make_tar(tmp_path, [info])
    with pytest.raises(OpponentError):
        ps.extract_corpus_archive(archive, tmp_path / "out")


def test_extract_rejects_hardlink(tmp_path):
    info = tarfile.TarInfo(name="hardlink"); info.type = tarfile.LNKTYPE; info.linkname = "somefile"
    archive = _make_tar(tmp_path, [info])
    with pytest.raises(OpponentError):
        ps.extract_corpus_archive(archive, tmp_path / "out")


def test_extract_rejects_device_and_fifo(tmp_path):
    for member_type in (tarfile.CHRTYPE, tarfile.BLKTYPE, tarfile.FIFOTYPE):
        info = tarfile.TarInfo(name="dev0"); info.type = member_type
        archive = _make_tar(tmp_path, [info])
        with pytest.raises(OpponentError):
            ps.extract_corpus_archive(archive, tmp_path / "out")


def test_extract_rejects_windows_drive_path(tmp_path):
    info = tarfile.TarInfo(name="C:/Windows/evil.txt"); info.size = 1
    archive = _make_tar(tmp_path, [info])
    with pytest.raises(OpponentError):
        ps.extract_corpus_archive(archive, tmp_path / "out")


def test_extract_rejects_backslash_traversal(tmp_path):
    info = tarfile.TarInfo(name="..\\..\\evil.txt"); info.size = 1
    archive = _make_tar(tmp_path, [info])
    with pytest.raises(OpponentError):
        ps.extract_corpus_archive(archive, tmp_path / "out")


def test_extract_rejects_nested_archive_member(tmp_path):
    info = tarfile.TarInfo(name="nested/inner.tar.gz"); info.size = 4
    archive = _make_tar(tmp_path, [info], contents={"nested/inner.tar.gz": b"fake"})
    with pytest.raises(OpponentError, match="nested archive"):
        ps.extract_corpus_archive(archive, tmp_path / "out")


def test_extract_rejects_oversized_single_file(tmp_path):
    info = tarfile.TarInfo(name="big.bin"); info.size = ps.MAX_ARCHIVE_FILE_BYTES + 1
    archive = _make_tar(tmp_path, [info])
    with pytest.raises(OpponentError, match="max_file_bytes"):
        ps.extract_corpus_archive(archive, tmp_path / "out")


def test_extract_rejects_oversized_total_size(tmp_path):
    chunk = ps.MAX_ARCHIVE_FILE_BYTES
    count = (ps.MAX_ARCHIVE_TOTAL_BYTES // chunk) + 2
    members = []
    for i in range(count):
        info = tarfile.TarInfo(name=f"file_{i}.bin"); info.size = chunk
        members.append(info)
    archive = _make_tar(tmp_path, members)
    with pytest.raises(OpponentError, match="max_total_bytes|max_files"):
        ps.extract_corpus_archive(archive, tmp_path / "out")


def test_extract_rejects_too_many_files(tmp_path):
    members = []
    for i in range(ps.MAX_ARCHIVE_FILE_COUNT + 5):
        info = tarfile.TarInfo(name=f"f{i}.txt"); info.size = 1
        members.append(info)
    archive = _make_tar(tmp_path, members)
    with pytest.raises(OpponentError, match="max_files"):
        ps.extract_corpus_archive(archive, tmp_path / "out")


def test_extract_rejects_compression_bomb_ratio(tmp_path):
    # A single highly-compressible large file: real gzip compresses
    # `b"\x00" * N` far below the payload size, giving a huge ratio. Exercised
    # directly against safe_extract_tar_gz with a tight ratio so the test does
    # not depend on this host's exact gzip ratio for a 2MB all-zero payload
    # clearing the production MAX_ARCHIVE_COMPRESSION_RATIO (100x) threshold.
    from mage_ptcg.opponents.core import safe_extract_tar_gz
    info = tarfile.TarInfo(name="bomb.bin")
    payload = b"\x00" * (2 * 1024 * 1024)
    info.size = len(payload)
    archive = _make_tar(tmp_path, [info], contents={"bomb.bin": payload})
    with pytest.raises(OpponentError, match="max_compression_ratio"):
        safe_extract_tar_gz(archive, tmp_path / "out", max_compression_ratio=1.0)


def test_unknown_code_never_imported_or_executed():
    """The module must never read/import/exec raw or extracted agent code."""
    assert "raw" in ps._FORBIDDEN_IMPORT_DIRS and "extracted" in ps._FORBIDDEN_IMPORT_DIRS
    assert set(ps._FORBIDDEN_IMPORT_DIRS).isdisjoint(ps._MANDATORY_SOURCE_FILES)
    assert set(ps._FORBIDDEN_IMPORT_DIRS).isdisjoint(ps._OPTIONAL_SOURCE_FILES)
    source = inspect.getsource(ps)
    assert "exec(" not in source and "eval(" not in source and "importlib.import_module" not in source


# ---------------------------------------------------------------------------
# Regression: real corpus + Team Population non-regression
# ---------------------------------------------------------------------------

def test_real_corpus_imports_all_seven_sources_with_documented_classification(tmp_path):
    corpus_root = Path("/home/bfe-lab-ono/kaggle/handoff-artifacts/o6-public-source-corpus-v1")
    if not corpus_root.exists():
        pytest.skip("real Public Source Corpus handoff artifact not available in this environment")
    report = ps.import_public_source_corpus(corpus_root=corpus_root, output_dir=tmp_path / "out")
    assert report["total_sources"] == 7
    assert len(report["imported"]) == 7
    records = {r["source_id"]: r for r in ps.list_public_sources(output_dir=tmp_path / "out")}
    assert records["itsuki9180_lucario_jp"]["candidate_state"] == "DECK_STANDARD_PILOT_CANDIDATE"
    assert records["itsuki9180_lucario_jp"]["review_override_applied"] is True
    assert records["tomatomato_archaludon"]["candidate_state"] == "DECK_STANDARD_PILOT_CANDIDATE"
    review_required = [sid for sid, r in records.items() if r["candidate_state"] == "REVIEW_REQUIRED"]
    assert len(review_required) == 5
    with pytest.raises(ps.PermissionReviewRequiredError):
        ps.check_public_source_permissions(output_dir=tmp_path / "out")
