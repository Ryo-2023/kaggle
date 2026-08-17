"""Tests for deterministic, value-independent schema fingerprinting."""

from __future__ import annotations

from mage_ptcg.competition.fingerprint import schema_fingerprint


def test_same_schema_different_key_order_yields_same_fingerprint() -> None:
    a = {"alpha": 1, "beta": "x"}
    b = {"beta": "y", "alpha": 2}

    assert schema_fingerprint(a) == schema_fingerprint(b)


def test_same_structure_different_values_yields_same_fingerprint() -> None:
    a = {"score": 10, "name": "foo"}
    b = {"score": 99999, "name": "a completely different string"}

    assert schema_fingerprint(a) == schema_fingerprint(b)


def test_type_change_yields_different_fingerprint() -> None:
    a = {"value": 1}
    b = {"value": "1"}

    assert schema_fingerprint(a) != schema_fingerprint(b)


def test_key_addition_yields_different_fingerprint() -> None:
    a = {"alpha": 1}
    b = {"alpha": 1, "beta": 2}

    assert schema_fingerprint(a) != schema_fingerprint(b)


def test_key_removal_yields_different_fingerprint() -> None:
    a = {"alpha": 1, "beta": 2}
    b = {"alpha": 1}

    assert schema_fingerprint(a) != schema_fingerprint(b)


def test_nested_schema_change_yields_different_fingerprint() -> None:
    a = {"outer": {"inner": 1}}
    b = {"outer": {"inner": "1"}}

    assert schema_fingerprint(a) != schema_fingerprint(b)


def test_list_order_is_irrelevant() -> None:
    a = {"items": [{"id": 1, "name": "a"}, {"id": 2, "score": 3.5}]}
    b = {"items": [{"id": 9, "score": 1.5}, {"id": 8, "name": "z"}]}

    assert schema_fingerprint(a) == schema_fingerprint(b)


def test_list_length_is_irrelevant_when_shape_repeats() -> None:
    a = {"items": [{"id": 1}, {"id": 2}, {"id": 3}]}
    b = {"items": [{"id": 1}]}

    assert schema_fingerprint(a) == schema_fingerprint(b)


def test_heterogeneous_list_is_handled_deterministically() -> None:
    payload = {
        "items": [
            {"id": 1, "name": "a"},
            {"id": 2, "score": 3.5},
            {"id": 3, "name": "c", "score": 1.0},
        ]
    }

    first = schema_fingerprint(payload)
    second = schema_fingerprint(payload)

    assert first == second


def test_heterogeneous_list_shape_change_is_detected() -> None:
    a = {"items": [{"id": 1, "name": "a"}, {"id": 2, "score": 3.5}]}
    b = {"items": [{"id": 1, "name": "a"}, {"id": 2, "score": 3.5}, {"id": 3, "flag": True}]}

    assert schema_fingerprint(a) != schema_fingerprint(b)


def test_bool_and_int_are_distinguished() -> None:
    a = {"flag": True}
    b = {"flag": 1}

    assert schema_fingerprint(a) != schema_fingerprint(b)


def test_none_is_a_distinct_type() -> None:
    a = {"value": None}
    b = {"value": 0}

    assert schema_fingerprint(a) != schema_fingerprint(b)


def test_empty_list_has_stable_fingerprint() -> None:
    a = {"items": []}
    b = {"items": []}

    assert schema_fingerprint(a) == schema_fingerprint(b)


def test_top_level_scalar_is_supported() -> None:
    assert schema_fingerprint("hello") == schema_fingerprint("world")
    assert schema_fingerprint("hello") != schema_fingerprint(1)


def test_fingerprint_is_a_sha256_hex_digest() -> None:
    digest = schema_fingerprint({"a": 1})

    assert isinstance(digest, str)
    assert len(digest) == 64
    int(digest, 16)  # must be valid hex
