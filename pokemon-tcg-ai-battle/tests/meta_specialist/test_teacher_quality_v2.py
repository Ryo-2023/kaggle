from __future__ import annotations

import hashlib
import inspect
import json
import os
from contextlib import contextmanager
from dataclasses import FrozenInstanceError
from pathlib import Path
import tempfile
import tracemalloc

import pytest

from mage_ptcg.meta_specialist.teacher_quality_v2 import (
    TeacherQualityOverlayRowV2,
    _open_anchored,
    read_teacher_quality_manifest_v2,
    seal_teacher_quality_v2,
    stream_ready_teacher_quality_overlay_v2,
)


def _raw(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _write(path: Path, value: object) -> str:
    raw = _raw(value)
    path.write_bytes(raw)
    return _sha(raw)


def _rule() -> dict[str, object]:
    return {
        "schema": "meta-specialist-teacher-quality-rule-v2",
        "approval_status": "APPROVED",
        "rule_id": "fixture-only-quality-rule",
        "rule_version": "v2",
        "allowed_weights": [0.0, 0.7],
        "min_logical_games": 12,
        "max_fault_rate": 0.1,
        "unavailable_strength_policy": "ALLOW",
        "assignments": [{"teacher_id": "teacher-a", "teacher_revision": "r1", "quality_weight": 0.7}],
    }


def _logical_game_id(opponent_id: str, seat: int, repetition: int) -> str:
    return _sha(_raw({
        "lane": "alakazam", "teacher_id": "teacher-a", "teacher_revision": "r1",
        "opponent_id": opponent_id, "seat": seat, "repetition": repetition,
    }))


def _result_rows() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for opponent_number in range(6):
        opponent_id = f"opponent-{opponent_number}"
        for seat in (0, 1):
            rows.append({
                "schema": "meta-specialist-teacher-quality-attempt-v2",
                "lane": "alakazam", "teacher_id": "teacher-a", "teacher_revision": "r1",
                "logical_game_id": _logical_game_id(opponent_id, seat, 0),
                "opponent_id": opponent_id, "seat": seat, "repetition": 0, "attempt_index": 0,
                "fault": None, "outcome": "win" if seat == 0 else "loss",
            })
    return rows


def _result_raw(rows: list[dict[str, object]] | None = None, *, crlf: bool = False) -> bytes:
    separator = b"\r\n" if crlf else b"\n"
    return separator.join(_raw(row) for row in (rows or _result_rows())) + separator


def _bundle(result_file_sha256: str = "9" * 64) -> dict[str, object]:
    result_files = [{"basename": "results.jsonl", "file_sha256": result_file_sha256}]
    return {
        "schema": "meta-specialist-teacher-quality-primary-bundle-v2",
        "lane": "alakazam",
        "teacher_id": "teacher-a",
        "teacher_revision": "r1",
        "policy": {"implementation_sha256": "1" * 64, "version": "v1", "usage_boundary": "local_eval_only"},
        "deck_bytes_sha256": "2" * 64,
        "source_permission_sha256": "3" * 64,
        "current_pool": {"schedule_sha256": "4" * 64, "pool_sha256": "5" * 64, "engine_sha256": "6" * 64, "source_commit_sha256": "7" * 64},
        "logical_game_matrix": {"logical_games": 12, "teachers": 1, "opponents": 6, "seats": 2, "repetitions": 1},
        "per_attempt_fault_provenance": {"attempts": 12, "faults": 0, "result_sha256": _sha(_raw(result_files))},
        "result_aggregate": {"games": 12, "wins": 6, "draws": 0, "losses": 6},
        "result_files": result_files,
        "strength": {"status": "unavailable"},
        "source_artifact_sha256": "a" * 64,
    }


def _overlay(*, bundle: dict[str, object] | None = None, record_id: str = "0" * 64, content_hash: str = "b" * 64, weight: float = 0.7, reason: str | None = None) -> bytes:
    primary = bundle or _bundle()
    row = {
        "record_id": record_id,
        "content_hash": content_hash,
        "teacher_id": "teacher-a",
        "source_artifact_sha256": "a" * 64,
        "evidence_class_sha256": _sha(_raw(primary)),
        "quality_weight": weight,
        "exclusion_reason": reason,
    }
    return _raw(row) + b"\n"


def _inputs(tmp_path: Path) -> tuple[Path, str, Path, str, Path, str]:
    rule_path = tmp_path / "teacher-quality-rule-v2.json"
    bundle_path = tmp_path / "teacher-quality-primary-bundle-v2.json"
    overlay_path = tmp_path / "teacher-quality-overlay-v2.jsonl"
    result_path = tmp_path / "results.jsonl"
    result_raw = _result_raw()
    result_path.write_bytes(result_raw)
    bundle_value = _bundle(_sha(result_raw))
    rule_sha = _write(rule_path, _rule())
    bundle_sha = _write(bundle_path, bundle_value)
    overlay_raw = _overlay(bundle=bundle_value)
    overlay_path.write_bytes(overlay_raw)
    return rule_path, rule_sha, bundle_path, bundle_sha, overlay_path, _sha(overlay_raw)


def test_untrusted_self_declared_rule_can_never_make_actual_authority_ready(tmp_path: Path) -> None:
    rule, rule_sha, bundle, bundle_sha, overlay, overlay_sha = _inputs(tmp_path)
    output = tmp_path / "teacher-quality-manifest-v2.json"

    manifest = seal_teacher_quality_v2(
        rule_path=rule, expected_rule_file_sha256=rule_sha,
        primary_bundle_path=bundle, expected_primary_bundle_file_sha256=bundle_sha,
        overlay_path=overlay, expected_overlay_file_sha256=overlay_sha,
        output_path=output,
    )

    assert manifest["status"] == "AUTHORITY_GAP"
    assert manifest["theta0_allowed"] is False
    assert manifest["authority_gap"]["code"] == "trusted_rule_digest_missing"
    assert manifest["row_count"] == 1
    assert manifest["eligible_record_count"] == 1
    assert manifest["weight_histogram"] == {"0.7": 1}
    raw = output.read_bytes()
    reloaded = read_teacher_quality_manifest_v2(
        output, expected_manifest_file_sha256=_sha(raw), expected_manifest_sha256=manifest["manifest_sha256"],
    )
    assert reloaded == manifest


def test_overlay_weight_is_rederived_not_trusted_from_row(tmp_path: Path) -> None:
    rule, rule_sha, bundle, bundle_sha, overlay, _ = _inputs(tmp_path)
    bundle_value = json.loads(bundle.read_text())
    bad_raw = _overlay(bundle=bundle_value, weight=1.0)
    overlay.write_bytes(bad_raw)

    with pytest.raises(ValueError, match="quality_weight does not match"):
        seal_teacher_quality_v2(
            rule_path=rule, expected_rule_file_sha256=rule_sha,
            primary_bundle_path=bundle, expected_primary_bundle_file_sha256=bundle_sha,
            overlay_path=overlay, expected_overlay_file_sha256=_sha(bad_raw), output_path=tmp_path / "teacher-quality-manifest-v2.json",
        )


def test_overlay_requires_canonical_unique_sorted_complete_rows(tmp_path: Path) -> None:
    rule, rule_sha, bundle, bundle_sha, overlay, _ = _inputs(tmp_path)
    bundle_value = json.loads(bundle.read_text())
    first = json.loads(_overlay(bundle=bundle_value).decode())
    second = dict(first)
    second["record_id"] = "f" * 64
    raw = _raw(second) + b"\n" + _raw(first) + b"\n"
    overlay.write_bytes(raw)
    with pytest.raises(ValueError, match="sorted"):
        seal_teacher_quality_v2(
            rule_path=rule, expected_rule_file_sha256=rule_sha,
            primary_bundle_path=bundle, expected_primary_bundle_file_sha256=bundle_sha,
            overlay_path=overlay, expected_overlay_file_sha256=_sha(raw), output_path=tmp_path / "teacher-quality-manifest-v2.json",
        )

    duplicate = _overlay(bundle=bundle_value) + _overlay(bundle=bundle_value)
    overlay.write_bytes(duplicate)
    with pytest.raises(ValueError, match="duplicate record_id"):
        seal_teacher_quality_v2(
            rule_path=rule, expected_rule_file_sha256=rule_sha,
            primary_bundle_path=bundle, expected_primary_bundle_file_sha256=bundle_sha,
            overlay_path=overlay, expected_overlay_file_sha256=_sha(duplicate), output_path=tmp_path / "teacher-quality-manifest-v2.json",
        )


def test_reader_rejects_sidecar_replacement_and_path_escape(tmp_path: Path) -> None:
    rule, rule_sha, bundle, bundle_sha, overlay, overlay_sha = _inputs(tmp_path)
    output = tmp_path / "teacher-quality-manifest-v2.json"
    manifest = seal_teacher_quality_v2(
        rule_path=rule, expected_rule_file_sha256=rule_sha,
        primary_bundle_path=bundle, expected_primary_bundle_file_sha256=bundle_sha,
        overlay_path=overlay, expected_overlay_file_sha256=overlay_sha, output_path=output,
    )
    original = output.read_bytes()
    bundle_value = json.loads(bundle.read_text())
    overlay.write_bytes(_overlay(bundle=bundle_value, content_hash="c" * 64))
    with pytest.raises(ValueError, match="overlay file SHA-256"):
        read_teacher_quality_manifest_v2(output, expected_manifest_file_sha256=_sha(original), expected_manifest_sha256=manifest["manifest_sha256"])

    body = json.loads(original)
    body["overlay"]["basename"] = "../escape.jsonl"
    body.pop("manifest_sha256")
    body["manifest_sha256"] = _sha(_raw(body))
    escaped = _raw(body)
    output.write_bytes(escaped)
    with pytest.raises(ValueError, match="escapes"):
        read_teacher_quality_manifest_v2(output, expected_manifest_file_sha256=_sha(escaped), expected_manifest_sha256=body["manifest_sha256"])


def test_authority_gap_preserves_rows_but_never_permits_theta0(tmp_path: Path) -> None:
    rule, rule_sha, bundle, bundle_sha, overlay, _ = _inputs(tmp_path)
    rule_body = _rule()
    rule_body["unavailable_strength_policy"] = "EXCLUDE"
    rule_sha = _write(rule, rule_body)
    bundle_value = json.loads(bundle.read_text())
    raw = _overlay(bundle=bundle_value, weight=0.0, reason="strength_unavailable")
    overlay.write_bytes(raw)
    manifest = seal_teacher_quality_v2(
        rule_path=rule, expected_rule_file_sha256=rule_sha,
        primary_bundle_path=bundle, expected_primary_bundle_file_sha256=bundle_sha,
        overlay_path=overlay, expected_overlay_file_sha256=_sha(raw), output_path=tmp_path / "teacher-quality-manifest-v2.json",
    )
    assert manifest["status"] == "AUTHORITY_GAP"
    assert manifest["theta0_allowed"] is False
    assert manifest["authority_gap"]["code"] == "trusted_rule_digest_missing"


def test_external_sha_is_checked_before_parsing(tmp_path: Path) -> None:
    rule, rule_sha, bundle, bundle_sha, overlay, overlay_sha = _inputs(tmp_path)
    rule.write_text("{invalid", encoding="utf-8")
    with pytest.raises(ValueError, match="external rule file SHA-256"):
        seal_teacher_quality_v2(
            rule_path=rule, expected_rule_file_sha256=rule_sha,
            primary_bundle_path=bundle, expected_primary_bundle_file_sha256=bundle_sha,
            overlay_path=overlay, expected_overlay_file_sha256=overlay_sha, output_path=tmp_path / "teacher-quality-manifest-v2.json",
        )


def test_missing_result_file_and_self_reported_aggregate_are_rejected(tmp_path: Path) -> None:
    rule, rule_sha, bundle, _, overlay, _ = _inputs(tmp_path)
    (tmp_path / "results.jsonl").unlink()
    bundle_value = json.loads(bundle.read_text())
    bundle_sha = _sha(bundle.read_bytes())
    overlay_raw = _overlay(bundle=bundle_value)
    overlay.write_bytes(overlay_raw)
    with pytest.raises(ValueError, match="result file"):
        seal_teacher_quality_v2(
            rule_path=rule, expected_rule_file_sha256=rule_sha,
            primary_bundle_path=bundle, expected_primary_bundle_file_sha256=bundle_sha,
            overlay_path=overlay, expected_overlay_file_sha256=_sha(overlay_raw), output_path=tmp_path / "teacher-quality-manifest-v2.json",
        )


def test_result_ledger_rederives_matrix_attempt_fault_and_outcome_counts(tmp_path: Path) -> None:
    rule, rule_sha, bundle, _, overlay, _ = _inputs(tmp_path)
    bundle_value = json.loads(bundle.read_text())
    bundle_value["result_aggregate"]["wins"] = 7
    bundle_value["result_aggregate"]["losses"] = 5
    bundle_sha = _write(bundle, bundle_value)
    overlay_raw = _overlay(bundle=bundle_value)
    overlay.write_bytes(overlay_raw)
    with pytest.raises(ValueError, match="result aggregate"):
        seal_teacher_quality_v2(
            rule_path=rule, expected_rule_file_sha256=rule_sha,
            primary_bundle_path=bundle, expected_primary_bundle_file_sha256=bundle_sha,
            overlay_path=overlay, expected_overlay_file_sha256=_sha(overlay_raw), output_path=tmp_path / "teacher-quality-manifest-v2.json",
        )


def test_result_ledger_rejects_duplicate_attempt_and_missing_fault_provenance(tmp_path: Path) -> None:
    rule, rule_sha, bundle, _, overlay, _ = _inputs(tmp_path)
    rows = _result_rows()
    rows.insert(1, dict(rows[0]))
    result_raw = _result_raw(rows)
    (tmp_path / "results.jsonl").write_bytes(result_raw)
    bundle_value = _bundle(_sha(result_raw))
    bundle_value["per_attempt_fault_provenance"]["attempts"] = 13
    bundle_value["per_attempt_fault_provenance"]["result_sha256"] = _sha(_raw(bundle_value["result_files"]))
    bundle_sha = _write(bundle, bundle_value)
    overlay_raw = _overlay(bundle=bundle_value)
    overlay.write_bytes(overlay_raw)
    with pytest.raises(ValueError, match="duplicate attempt|attempt order"):
        seal_teacher_quality_v2(
            rule_path=rule, expected_rule_file_sha256=rule_sha,
            primary_bundle_path=bundle, expected_primary_bundle_file_sha256=bundle_sha,
            overlay_path=overlay, expected_overlay_file_sha256=_sha(overlay_raw), output_path=tmp_path / "teacher-quality-manifest-v2.json",
        )

    rows = _result_rows()
    rows[0]["fault"] = {}
    rows[0]["outcome"] = None
    malformed = _result_raw(rows)
    (tmp_path / "results.jsonl").write_bytes(malformed)
    bundle_value = _bundle(_sha(malformed))
    bundle_sha = _write(bundle, bundle_value)
    overlay_raw = _overlay(bundle=bundle_value)
    overlay.write_bytes(overlay_raw)
    with pytest.raises(ValueError, match="fault provenance"):
        seal_teacher_quality_v2(
            rule_path=rule, expected_rule_file_sha256=rule_sha,
            primary_bundle_path=bundle, expected_primary_bundle_file_sha256=bundle_sha,
            overlay_path=overlay, expected_overlay_file_sha256=_sha(overlay_raw), output_path=tmp_path / "teacher-quality-manifest-v2.json",
        )


def test_canonical_jsonl_rejects_crlf_for_overlay_and_result_ledger(tmp_path: Path) -> None:
    rule, rule_sha, bundle, bundle_sha, overlay, _ = _inputs(tmp_path)
    overlay_raw = _overlay(bundle=json.loads(bundle.read_text())).replace(b"\n", b"\r\n")
    overlay.write_bytes(overlay_raw)
    with pytest.raises(ValueError, match="CRLF"):
        seal_teacher_quality_v2(
            rule_path=rule, expected_rule_file_sha256=rule_sha,
            primary_bundle_path=bundle, expected_primary_bundle_file_sha256=bundle_sha,
            overlay_path=overlay, expected_overlay_file_sha256=_sha(overlay_raw), output_path=tmp_path / "teacher-quality-manifest-v2.json",
        )

    result_raw = _result_raw(crlf=True)
    (tmp_path / "results.jsonl").write_bytes(result_raw)
    bundle_value = _bundle(_sha(result_raw))
    bundle_sha = _write(bundle, bundle_value)
    overlay_raw = _overlay(bundle=bundle_value)
    overlay.write_bytes(overlay_raw)
    with pytest.raises(ValueError, match="CRLF"):
        seal_teacher_quality_v2(
            rule_path=rule, expected_rule_file_sha256=rule_sha,
            primary_bundle_path=bundle, expected_primary_bundle_file_sha256=bundle_sha,
            overlay_path=overlay, expected_overlay_file_sha256=_sha(overlay_raw), output_path=tmp_path / "teacher-quality-manifest-v2.json",
        )


def test_large_overlay_scan_has_bounded_python_memory(tmp_path: Path) -> None:
    rule, rule_sha, bundle, bundle_sha, overlay, _ = _inputs(tmp_path)
    bundle_value = json.loads(bundle.read_text())
    with overlay.open("wb") as handle:
        for index in range(20_000):
            handle.write(_overlay(bundle=bundle_value, record_id=f"{index:064x}"))
    overlay_sha = _sha(overlay.read_bytes())

    tracemalloc.start()
    manifest = seal_teacher_quality_v2(
        rule_path=rule, expected_rule_file_sha256=rule_sha,
        primary_bundle_path=bundle, expected_primary_bundle_file_sha256=bundle_sha,
        overlay_path=overlay, expected_overlay_file_sha256=overlay_sha, output_path=tmp_path / "teacher-quality-manifest-v2.json",
    )
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    assert manifest["row_count"] == 20_000
    assert peak < 8 * 1024 * 1024


def test_anchored_parse_rejects_same_inode_same_size_rewrite_with_restored_mtime(tmp_path: Path) -> None:
    path = tmp_path / "authority.bin"
    original = b"A" * 64
    changed = b"B" * 64
    path.write_bytes(original)
    before = path.stat()

    with pytest.raises(ValueError, match="changed during parse|parse SHA-256"):
        with _open_anchored(path, _sha(original), "authority") as handle:
            with path.open("r+b") as writer:
                writer.write(changed)
                writer.flush()
                os.fsync(writer.fileno())
            os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
            assert handle.read() == changed


def test_anchored_parse_digest_rejects_rewrite_even_if_descriptor_identity_is_masked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "authority.bin"
    original = b"A" * 64
    changed = b"B" * 64
    path.write_bytes(original)
    before = path.stat()

    with pytest.raises(ValueError, match="parse SHA-256"):
        with _open_anchored(path, _sha(original), "authority") as handle:
            path.write_bytes(changed)
            os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
            monkeypatch.setattr(os, "fstat", lambda _: before)
            assert handle.read() == changed


def test_anchored_parse_digest_rejects_intermediate_rewrite_restored_before_close(tmp_path: Path) -> None:
    path = tmp_path / "authority.bin"
    original = b"A" * 64
    changed = b"B" * 64
    path.write_bytes(original)
    before = path.stat()

    with pytest.raises(ValueError, match="changed during parse|parse SHA-256"):
        with _open_anchored(path, _sha(original), "authority") as handle:
            path.write_bytes(changed)
            assert handle.read() == changed
            path.write_bytes(original)
            os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))


@pytest.mark.parametrize("mutation", ["truncate", "append"])
def test_anchored_parse_rejects_truncate_or_append(tmp_path: Path, mutation: str) -> None:
    path = tmp_path / "authority.bin"
    original = b"A" * 64
    path.write_bytes(original)

    with pytest.raises(ValueError, match="changed during parse|parse SHA-256"):
        with _open_anchored(path, _sha(original), "authority") as handle:
            if mutation == "truncate":
                path.write_bytes(original[:-1])
            else:
                with path.open("ab") as writer:
                    writer.write(b"B")
            handle.read()


def test_ready_overlay_stream_is_manifest_bound_sorted_and_read_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import mage_ptcg.meta_specialist.teacher_quality_v2 as quality_module

    rule, rule_sha, bundle, bundle_sha, overlay, _ = _inputs(tmp_path)
    bundle_value = json.loads(bundle.read_text())
    overlay_raw = (
        _overlay(bundle=bundle_value, record_id="0" * 64, content_hash="a" * 64)
        + _overlay(bundle=bundle_value, record_id="1" * 64, content_hash="b" * 64)
    )
    overlay.write_bytes(overlay_raw)
    monkeypatch.setattr(quality_module, "_TRUSTED_RULE_FILE_SHA256_V2", frozenset({rule_sha}))
    output = tmp_path / "teacher-quality-manifest-v2.json"
    manifest = seal_teacher_quality_v2(
        rule_path=rule, expected_rule_file_sha256=rule_sha,
        primary_bundle_path=bundle, expected_primary_bundle_file_sha256=bundle_sha,
        overlay_path=overlay, expected_overlay_file_sha256=_sha(overlay_raw), output_path=output,
    )
    manifest_raw = output.read_bytes()

    rows = list(stream_ready_teacher_quality_overlay_v2(
        output, expected_manifest_file_sha256=_sha(manifest_raw),
        expected_manifest_sha256=manifest["manifest_sha256"],
    ))

    assert rows == [
        TeacherQualityOverlayRowV2("0" * 64, "a" * 64, "teacher-a", "a" * 64, _sha(_raw(bundle_value)), 0.7, None),
        TeacherQualityOverlayRowV2("1" * 64, "b" * 64, "teacher-a", "a" * 64, _sha(_raw(bundle_value)), 0.7, None),
    ]
    with pytest.raises(FrozenInstanceError):
        rows[0].quality_weight = 1.0  # type: ignore[misc]


def test_ready_overlay_stream_rejects_authority_gap_before_first_row(tmp_path: Path) -> None:
    rule, rule_sha, bundle, bundle_sha, overlay, overlay_sha = _inputs(tmp_path)
    output = tmp_path / "teacher-quality-manifest-v2.json"
    manifest = seal_teacher_quality_v2(
        rule_path=rule, expected_rule_file_sha256=rule_sha,
        primary_bundle_path=bundle, expected_primary_bundle_file_sha256=bundle_sha,
        overlay_path=overlay, expected_overlay_file_sha256=overlay_sha, output_path=output,
    )
    manifest_raw = output.read_bytes()

    stream = stream_ready_teacher_quality_overlay_v2(
        output, expected_manifest_file_sha256=_sha(manifest_raw),
        expected_manifest_sha256=manifest["manifest_sha256"],
    )
    with pytest.raises(ValueError, match="not READY"):
        next(stream)


def test_ready_overlay_stream_uses_sealed_sidecar_not_a_caller_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import mage_ptcg.meta_specialist.teacher_quality_v2 as quality_module

    rule, rule_sha, bundle, bundle_sha, overlay, overlay_sha = _inputs(tmp_path)
    monkeypatch.setattr(quality_module, "_TRUSTED_RULE_FILE_SHA256_V2", frozenset({rule_sha}))
    output = tmp_path / "teacher-quality-manifest-v2.json"
    manifest = seal_teacher_quality_v2(
        rule_path=rule, expected_rule_file_sha256=rule_sha,
        primary_bundle_path=bundle, expected_primary_bundle_file_sha256=bundle_sha,
        overlay_path=overlay, expected_overlay_file_sha256=overlay_sha, output_path=output,
    )
    manifest_raw = output.read_bytes()
    overlay.write_bytes(_overlay(bundle=json.loads(bundle.read_text()), content_hash="c" * 64))

    stream = stream_ready_teacher_quality_overlay_v2(
        output, expected_manifest_file_sha256=_sha(manifest_raw),
        expected_manifest_sha256=manifest["manifest_sha256"],
    )
    with pytest.raises(ValueError, match="overlay file SHA-256"):
        next(stream)


def _ready_two_row_stream_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, dict[str, object], bytes, Path, bytes, bytes, dict[str, object]]:
    import mage_ptcg.meta_specialist.teacher_quality_v2 as quality_module

    rule, rule_sha, bundle, bundle_sha, overlay, _ = _inputs(tmp_path)
    bundle_value = json.loads(bundle.read_text())
    first = _overlay(bundle=bundle_value, record_id="0" * 64, content_hash="a" * 64)
    second = _overlay(bundle=bundle_value, record_id="1" * 64, content_hash="b" * 64)
    overlay_raw = first + second
    overlay.write_bytes(overlay_raw)
    monkeypatch.setattr(quality_module, "_TRUSTED_RULE_FILE_SHA256_V2", frozenset({rule_sha}))
    output = tmp_path / "teacher-quality-manifest-v2.json"
    manifest = seal_teacher_quality_v2(
        rule_path=rule, expected_rule_file_sha256=rule_sha,
        primary_bundle_path=bundle, expected_primary_bundle_file_sha256=bundle_sha,
        overlay_path=overlay, expected_overlay_file_sha256=_sha(overlay_raw), output_path=output,
    )
    return output, manifest, output.read_bytes(), overlay, first, second, bundle_value


@pytest.mark.parametrize("mutation", ["same-size", "truncate", "append", "bad-final-row"])
def test_ready_overlay_stream_rejects_post_hash_source_mutation_before_first_yield(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str,
) -> None:
    """Fails if source validation can be deferred until after a consumer-visible row."""
    import mage_ptcg.meta_specialist.teacher_quality_v2 as quality_module

    output, manifest, manifest_raw, overlay, first, second, bundle_value = _ready_two_row_stream_fixture(
        tmp_path, monkeypatch,
    )
    original_open = quality_module._open_anchored
    real_temporary_file = tempfile.TemporaryFile
    spool_fds: list[int] = []

    def tracked_temporary_file(*args: object, **kwargs: object):
        handle = real_temporary_file(*args, **kwargs)
        spool_fds.append(handle.fileno())
        return handle

    @contextmanager
    def attacked_open(path: str | Path, expected_sha256: str, name: str):
        with original_open(path, expected_sha256, name) as handle:
            functions = {frame.function for frame in inspect.stack()}
            if (
                name == "overlay file"
                and "stream_ready_teacher_quality_overlay_v2" in functions
                and "read_teacher_quality_manifest_v2" not in functions
            ):
                before = Path(path).stat()
                if mutation == "same-size":
                    changed = _overlay(bundle=bundle_value, record_id="0" * 64, content_hash="c" * 64) + second
                    assert len(changed) == len(first + second)
                    Path(path).write_bytes(changed)
                elif mutation == "truncate":
                    Path(path).write_bytes(first)
                elif mutation == "append":
                    with Path(path).open("ab") as writer:
                        writer.write(_overlay(bundle=bundle_value, record_id="2" * 64, content_hash="c" * 64))
                else:
                    Path(path).write_bytes(first + b"{bad-json}\n")
                os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
            yield handle

    monkeypatch.setattr(quality_module, "_open_anchored", attacked_open)
    monkeypatch.setattr(quality_module.tempfile, "TemporaryFile", tracked_temporary_file)
    stream = stream_ready_teacher_quality_overlay_v2(
        output, expected_manifest_file_sha256=_sha(manifest_raw),
        expected_manifest_sha256=manifest["manifest_sha256"],
    )

    with pytest.raises(ValueError):
        next(stream)
    assert len(spool_fds) == 1
    with pytest.raises(OSError):
        os.fstat(spool_fds[0])


def test_ready_overlay_stream_opens_source_overlay_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Fails if public iteration validates one source read and yields from a second source read."""
    import mage_ptcg.meta_specialist.teacher_quality_v2 as quality_module

    output, manifest, manifest_raw, _, _, _, _ = _ready_two_row_stream_fixture(tmp_path, monkeypatch)
    original_open = quality_module._open_anchored
    overlay_opens = 0

    @contextmanager
    def counting_open(path: str | Path, expected_sha256: str, name: str):
        nonlocal overlay_opens
        if name == "overlay file":
            overlay_opens += 1
        with original_open(path, expected_sha256, name) as handle:
            yield handle

    monkeypatch.setattr(quality_module, "_open_anchored", counting_open)
    stream = stream_ready_teacher_quality_overlay_v2(
        output, expected_manifest_file_sha256=_sha(manifest_raw),
        expected_manifest_sha256=manifest["manifest_sha256"],
    )

    assert next(stream).record_id == "0" * 64
    assert overlay_opens == 1
    stream.close()


def test_ready_overlay_stream_closes_private_spool_on_iterator_close(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fails if a partially consumed iterator leaves its disk-backed private spool open."""
    import mage_ptcg.meta_specialist.teacher_quality_v2 as quality_module

    output, manifest, manifest_raw, _, _, _, _ = _ready_two_row_stream_fixture(tmp_path, monkeypatch)
    real_temporary_file = tempfile.TemporaryFile
    spool_fds: list[int] = []

    def tracked_temporary_file(*args: object, **kwargs: object):
        handle = real_temporary_file(*args, **kwargs)
        spool_fds.append(handle.fileno())
        return handle

    monkeypatch.setattr(quality_module.tempfile, "TemporaryFile", tracked_temporary_file)
    stream = stream_ready_teacher_quality_overlay_v2(
        output, expected_manifest_file_sha256=_sha(manifest_raw),
        expected_manifest_sha256=manifest["manifest_sha256"],
    )

    assert next(stream).record_id == "0" * 64
    assert len(spool_fds) == 1
    os.fstat(spool_fds[0])
    stream.close()
    with pytest.raises(OSError):
        os.fstat(spool_fds[0])
