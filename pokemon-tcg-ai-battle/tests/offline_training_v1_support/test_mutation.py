"""Mutation-style test adequacy verification.

Verifies that mutations of critical code structures break expected security or correctness assertions.
"""

from __future__ import annotations
import pytest
from mage_ptcg.offline_training_v1_support.contracts import walk_safe, SupportContractError

def test_mutation_privacy_gate_bypass():
    # A mutant that bypasses PRIVATE_KEYS check
    def mutant_walk_safe(value: object, *, path: str = "$") -> None:
        # Mutated: skip PRIVATE_KEYS inspection entirely
        if isinstance(value, dict):
            for key, child in value.items():
                mutant_walk_safe(child, path=f"{path}.{key}")
        elif isinstance(value, (list, tuple)):
            for index, child in enumerate(value):
                mutant_walk_safe(child, path=f"{path}[{index}]")

    leaky_payload = {"token": "my-secret-token"}

    # Standard walk_safe MUST reject this
    with pytest.raises(SupportContractError):
        walk_safe(leaky_payload)

    # The mutated walk_safe will bypass and NOT raise
    try:
        mutant_walk_safe(leaky_payload)
    except SupportContractError:
        pytest.fail("Mutant raised contract error when it was expected to bypass the check!")

def test_mutation_checksum_omission():
    # Standard JSON loads vs safe loads
    import json
    dup_json = '{"key": 1, "key": 2}'

    # Standard json.loads (mutant behavior: duplicate key check omitted) does not raise
    data = json.loads(dup_json)
    assert data["key"] == 2

    # Safe JSON loads must catch this
    from mage_ptcg.offline_training_v1_support.contracts import safe_json_loads
    with pytest.raises(SupportContractError):
        safe_json_loads(dup_json)
