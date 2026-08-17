"""Tests for canonical belief serialization."""

import hashlib
import json

import pytest

from mage_ptcg.belief import (
    HASH_PREFIX,
    BeliefValidationError,
    CardCounts,
    HiddenZoneKnowledge,
    KnownCardPosition,
    canonical_digest,
    from_canonical_bytes,
    to_canonical_bytes,
)
from mage_ptcg.contracts import CardId


def test_card_counts_round_trip_and_order_independence() -> None:
    left = CardCounts({CardId(2): 1, CardId(1): 2})
    right = CardCounts({CardId(1): 2, CardId(2): 1})

    assert to_canonical_bytes(left) == to_canonical_bytes(right)
    assert canonical_digest(left) == canonical_digest(right)
    assert from_canonical_bytes(
        to_canonical_bytes(left),
        expected_type=CardCounts,
    ) == left


def test_hidden_zone_round_trip() -> None:
    value = HiddenZoneKnowledge(
        total_count=4,
        positioned_known=(KnownCardPosition(3, CardId(2)),),
        unpositioned_known=CardCounts({CardId(1): 2}),
    )
    assert from_canonical_bytes(
        to_canonical_bytes(value),
        expected_type=HiddenZoneKnowledge,
    ) == value


def test_digest_matches_independent_sha256_calculation() -> None:
    value = CardCounts({CardId(1): 2})
    expected = hashlib.sha256(HASH_PREFIX + to_canonical_bytes(value)).hexdigest()
    assert canonical_digest(value) == expected


@pytest.mark.parametrize(
    "payload",
    [
        b'{"schema_version":',
        b'{"schema_version":1,"schema_version":1,"type":"x","data":[]}',
        b'{"schema_version":NaN,"type":"x","data":[]}',
        b"\xff",
    ],
)
def test_malformed_json_is_rejected(payload: bytes) -> None:
    with pytest.raises(BeliefValidationError):
        from_canonical_bytes(payload)


def test_unknown_bool_and_wrong_schema_versions_are_rejected() -> None:
    for version, code in [
        (True, "invalid_schema_version"),
        (2, "unknown_schema_version"),
    ]:
        payload = json.dumps(
            {
                "schema_version": version,
                "type": "mage_ptcg.belief.CardCounts",
                "data": [],
            },
            separators=(",", ":"),
        )
        with pytest.raises(BeliefValidationError) as caught:
            from_canonical_bytes(payload)
        assert caught.value.code == code


def test_unknown_type_and_extra_fields_are_rejected() -> None:
    unknown_type = b'{"data":[],"schema_version":1,"type":"unknown"}'
    extra_field = (
        b'{"data":[],"extra":1,"schema_version":1,'
        b'"type":"mage_ptcg.belief.CardCounts"}'
    )
    with pytest.raises(BeliefValidationError) as unknown:
        from_canonical_bytes(unknown_type)
    assert unknown.value.code == "unknown_type"

    with pytest.raises(BeliefValidationError) as extra:
        from_canonical_bytes(extra_field)
    assert extra.value.code == "invalid_document_fields"


def test_expected_type_mismatch_is_rejected() -> None:
    payload = to_canonical_bytes(CardCounts())
    with pytest.raises(BeliefValidationError) as caught:
        from_canonical_bytes(payload, expected_type=HiddenZoneKnowledge)
    assert caught.value.code == "unexpected_type"


@pytest.mark.parametrize(
    ("data", "code"),
    [
        ([[2, 1], [1, 1]], "noncanonical_card_order"),
        ([[1, 0]], "noncanonical_zero_count"),
        ([[1, 1], [1, 1]], "noncanonical_card_order"),
    ],
)
def test_noncanonical_card_counts_payload_is_rejected(
    data: object,
    code: str,
) -> None:
    payload = json.dumps(
        {
            "schema_version": 1,
            "type": "mage_ptcg.belief.CardCounts",
            "data": data,
        },
        separators=(",", ":"),
    )
    with pytest.raises(BeliefValidationError) as caught:
        from_canonical_bytes(payload)
    assert caught.value.code == code


def test_noncanonical_position_order_is_rejected() -> None:
    payload = json.dumps(
        {
            "schema_version": 1,
            "type": "mage_ptcg.belief.HiddenZoneKnowledge",
            "data": {
                "total_count": 3,
                "positioned_known": [
                    {"card_id": 2, "position": 2},
                    {"card_id": 1, "position": 0},
                ],
                "unpositioned_known": [],
            },
        },
        separators=(",", ":"),
    )
    with pytest.raises(BeliefValidationError) as caught:
        from_canonical_bytes(payload)
    assert caught.value.code == "noncanonical_position_order"
