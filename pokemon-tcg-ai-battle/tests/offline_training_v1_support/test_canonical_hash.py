"""Tests for canonical serialization and hashing improvements."""

from __future__ import annotations
import pytest
from mage_ptcg.offline_training_v1_support.contracts import (
    canonical_json,
    digest,
    walk_safe,
    safe_json_loads,
    SupportContractError
)

def test_domain_separation():
    payload = {"dataset_id": "PTCG-2026", "count": 100}

    hash_ds1 = digest(payload, domain="dataset:v1")
    hash_ds2 = digest(payload, domain="model:v1")
    hash_default = digest(payload)

    # Hashes must be unique depending on domain separating salt
    assert hash_ds1 != hash_ds2
    assert hash_ds1 != hash_default
    assert len(hash_ds1) == 64

def test_duplicate_keys_rejection():
    # standard json loads accepts duplicate keys (usually overwrites)
    # safe_json_loads must reject it
    dup_json = '{"a": 1, "b": 2, "a": 3}'
    with pytest.raises(SupportContractError) as exc_info:
        safe_json_loads(dup_json)
    assert "Duplicate JSON key" in str(exc_info.value)

def test_surrogate_rejection():
    # \ud800 is a lone surrogate character
    surrogate_str = "invalid_\ud800_char"
    with pytest.raises(SupportContractError) as exc_info:
        walk_safe(surrogate_str)
    assert "Surrogate character detected" in str(exc_info.value)

def test_normal_json_loads():
    normal_json = '{"a": 1, "b": 2}'
    res = safe_json_loads(normal_json)
    assert res == {"a": 1, "b": 2}
