"""Tests for json_schema.py and schema_registry.py."""

from __future__ import annotations
import pytest
from mage_ptcg.offline_training_v1_support.json_schema import get_schema, validate_dict_by_schema
from mage_ptcg.offline_training_v1_support.schema_registry import SchemaRegistry
from mage_ptcg.offline_training_v1_support.contracts import canonical_json

def test_deterministic_schema_output():
    schema1 = get_schema("game_result")
    schema2 = get_schema("game_result")

    # Assert exact structural equality and serialization determinism
    assert schema1 == schema2
    assert canonical_json(schema1) == canonical_json(schema2)

def test_validate_dict_by_schema():
    schema = get_schema("game_result")

    valid_data = {
        "game_id": "game-123",
        "winner_seat": 0,
        "turns": 15,
        "player_0_deck": "Grass",
        "player_1_deck": "Fire"
    }

    is_valid, err = validate_dict_by_schema(valid_data, schema)
    assert is_valid is True, err

    invalid_data = {
        "game_id": "game-123",
        # winner_seat is missing
        "turns": 15
    }
    is_valid, err = validate_dict_by_schema(invalid_data, schema)
    assert is_valid is False
    assert "winner_seat" in err

def test_schema_registry_inspect_and_validate():
    reg = SchemaRegistry()

    # game_result is registered on initialization
    info = reg.inspect("game_result", "v1")
    assert info["schema_id"] == "game_result"
    assert info["version"] == "v1"

    data = {
        "game_id": "game-777",
        "winner_seat": 1,
        "turns": 8
    }
    is_valid, err = reg.validate("game_result", "v1", data)
    assert is_valid is True

def test_schema_registry_compatibility():
    reg = SchemaRegistry()

    schema_a = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "age": {"type": "integer"}
        },
        "required": ["name"]
    }

    # Compatible change: adding an optional field or keeping types
    schema_b_compatible = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "age": {"type": "integer"},
            "email": {"type": "string"}
        },
        "required": ["name"]
    }

    res1 = reg.compare_schemas(schema_a, schema_b_compatible)
    assert res1["compatible"] is True

    # Breaking change: adding a required field
    schema_c_breaking = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "age": {"type": "integer"},
            "email": {"type": "string"}
        },
        "required": ["name", "email"]
    }

    res2 = reg.compare_schemas(schema_a, schema_c_breaking)
    assert res2["compatible"] is False
    assert any("required field" in x for x in res2["issues"])
