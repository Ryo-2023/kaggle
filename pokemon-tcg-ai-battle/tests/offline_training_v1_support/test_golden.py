"""Tests using golden fixtures and programmatically generated edge cases."""

from __future__ import annotations
import math
from pathlib import Path
import pytest
from mage_ptcg.offline_training_v1_support.contracts import (
    walk_safe,
    safe_json_loads,
    SupportContractError
)
from mage_ptcg.offline_training_v1_support.json_schema import get_schema, validate_dict_by_schema

GOLDEN_DIR = Path(__file__).parent / "golden"

def test_minimal_valid_fixture():
    path = GOLDEN_DIR / "minimal_valid.json"
    content = path.read_text(encoding="utf-8")
    data = safe_json_loads(content)

    # Verify contract walk passes
    walk_safe(data)

    # Verify schema passes
    schema = get_schema("game_result")
    is_valid, err = validate_dict_by_schema(data, schema)
    assert is_valid is True, err

def test_missing_required_fixture():
    path = GOLDEN_DIR / "missing_required.json"
    content = path.read_text(encoding="utf-8")
    data = safe_json_loads(content)

    schema = get_schema("game_result")
    is_valid, err = validate_dict_by_schema(data, schema)
    assert is_valid is False
    assert "turns" in err

def test_duplicate_key_fixture():
    path = GOLDEN_DIR / "duplicate_key.json"
    content = path.read_text(encoding="utf-8")
    with pytest.raises(SupportContractError) as exc_info:
        safe_json_loads(content)
    assert "Duplicate JSON key" in str(exc_info.value)

def test_privacy_violation_fixture():
    path = GOLDEN_DIR / "privacy_violation.json"
    content = path.read_text(encoding="utf-8")
    data = safe_json_loads(content)

    # walk_safe must catch the api_key leak
    with pytest.raises(SupportContractError) as exc_info:
        walk_safe(data)
    assert "Forbidden key" in str(exc_info.value) or "api_key" in str(exc_info.value)

def test_corrupt_json_fixture():
    path = GOLDEN_DIR / "corrupt.json"
    content = path.read_text(encoding="utf-8")
    with pytest.raises(SupportContractError) as exc_info:
        safe_json_loads(content)
    assert "Invalid or duplicate key" in str(exc_info.value)

def test_programmatic_nan_infinity_rejection():
    # NaN check
    nan_data = {"game_id": "game-009", "winner_seat": 0, "turns": 10, "score": float("nan")}
    with pytest.raises(SupportContractError, match="Non-finite value"):
        walk_safe(nan_data)

    # Infinity check
    inf_data = {"game_id": "game-009", "winner_seat": 0, "turns": 10, "score": float("inf")}
    with pytest.raises(SupportContractError, match="Non-finite value"):
        walk_safe(inf_data)

def test_programmatic_negative_zero_rejection():
    neg_zero_data = {"game_id": "game-010", "winner_seat": 0, "turns": 10, "score": -0.0}
    with pytest.raises(SupportContractError, match="Negative zero not allowed"):
        walk_safe(neg_zero_data)

def test_oversized_record_rejection():
    # Large 64-bit integer limit overflow
    neg_overflow = -9223372036854775809
    overflow_data = {"game_id": "game-011", "winner_seat": 0, "turns": neg_overflow}
    with pytest.raises(SupportContractError, match="Integer out of 64-bit signed range"):
        walk_safe(overflow_data)
