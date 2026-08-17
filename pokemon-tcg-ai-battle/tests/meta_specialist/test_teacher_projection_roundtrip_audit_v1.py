"""Contract tests for the qualified-teacher projection round-trip audit."""

from __future__ import annotations

from scripts.audit_teacher_projection_roundtrip_v1 import _fixture_smoke


def test_projection_roundtrip_fixture_smoke_covers_required_edge_categories() -> None:
    report = _fixture_smoke()

    assert report["status"] == "PASS"
    assert set(report["cases"]) == {
        "empty_selection", "duplicate_semantic_alias", "ordered_prefix",
        "end_option", "retreat",
    }
    assert all(case["status"] == "PASS" and case["rows"] > 0 for case in report["cases"].values())
    assert report["cases"]["ordered_prefix"]["order_semantics"] == "ordered_sequence"
    assert report["cases"]["duplicate_semantic_alias"]["order_semantics"] == "unordered_set"

