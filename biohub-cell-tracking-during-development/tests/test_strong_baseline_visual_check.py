from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from biohub.strong_baseline.visual_check import (
    EXPECTED_VISUAL_SUMMARY,
    validate_visual_summary,
)


def test_expected_visual_summary_fixture_is_self_consistent() -> None:
    validate_visual_summary(deepcopy(EXPECTED_VISUAL_SUMMARY))


def test_visual_summary_validation_rejects_overlay_mismatch() -> None:
    summary = deepcopy(EXPECTED_VISUAL_SUMMARY)
    summary["overlay_totals"]["fp"] += 1

    with pytest.raises(ValueError, match=r"overlay_totals\.fp"):
        validate_visual_summary(summary)


def test_visual_summary_validation_rejects_window_mismatch() -> None:
    summary = deepcopy(EXPECTED_VISUAL_SUMMARY)
    summary["windows"]["matched"]["source"]["node_id"] += 1

    with pytest.raises(ValueError, match=r"windows\.matched\.source\.node_id"):
        validate_visual_summary(summary)


def test_persisted_visual_receipt_matches_checker_contract() -> None:
    root = Path(__file__).parents[1]
    summary_path = root / "tests/fixtures/strong_baseline_v1/visual_sanity.json"
    text_path = root / "tests/fixtures/strong_baseline_v1/visual_sanity.txt"

    assert summary_path.is_file()
    assert text_path.is_file()
    summary = json.loads(summary_path.read_text())
    validate_visual_summary(summary)
    text = text_path.read_text()
    assert "raw_slice_endpoint=/api/frame" in text
    assert "window.matched.category=tp" in text
    assert "window.error.category=fp" in text
    assert "window.sparse_unmatched.category=prediction" in text
