"""Tests for the O6-AUD-002 public-only privacy gate."""
from __future__ import annotations

import pytest

from mage_ptcg.opponents.privacy_gate import PrivacyViolation, assert_public_only, scan_public_only


def _step(your_index=0, opp_hand=None, action=None, extra_current=None):
    current = {"yourIndex": your_index, "players": [
        {"hand": [] if your_index == 0 else opp_hand, "deckCount": 60},
        {"hand": opp_hand if your_index == 0 else [], "deckCount": 60},
    ]}
    if extra_current:
        current.update(extra_current)
    return {"observation": {"current": current, "select": {}}, "action": action, "status": "ACTIVE"}


def test_public_only_fixture_accepted():
    assert scan_public_only(_step())["status"] == "PASS"


def test_hidden_opponent_hand_rejected():
    result = scan_public_only(_step(opp_hand=[{"id": 1, "name": "Pikachu"}]))
    assert result["status"] == "REJECTED"
    assert "hand" in result["violation"]["path"]
    with pytest.raises(PrivacyViolation):
        assert_public_only(_step(opp_hand=[{"id": 1}]))


def test_deck_order_key_name_rejected():
    result = scan_public_only({"hidden_deck_order": [1, 2, 3]})
    assert result["status"] == "REJECTED"


def test_engine_internal_state_key_rejected():
    result = scan_public_only({"engine_internal_state": {"rng_state": [1, 2]}})
    assert result["status"] == "REJECTED"


def test_python_repr_value_rejected():
    result = scan_public_only({"note": "<CabtEngine object at 0x7f1234abcd00>"})
    assert result["status"] == "REJECTED"


def test_absolute_path_value_rejected():
    result = scan_public_only({"note": "/home/bfe-lab-ono/kaggle/secret.json"})
    assert result["status"] == "REJECTED"


def test_unknown_sensitive_field_fails_closed():
    result = scan_public_only({"credential_bundle": "whatever"})
    assert result["status"] == "REJECTED"


def test_nested_list_scanned():
    result = scan_public_only({"actions": [{"note": "ok"}, {"note": "object at 0x1234"}]})
    assert result["status"] == "REJECTED"


def test_renamed_nested_unknown_sensitive_field_still_caught_by_pattern():
    # A key that is semantically "hidden state" but under a name the denylist regex matches
    # even after renaming (credential_bundle -> auth_credential_bundle): still rejected.
    result = scan_public_only({"observation": {"nested": {"auth_credential_bundle": "value"}}})
    assert result["status"] == "REJECTED"


def test_genuinely_novel_unrecognized_key_name_is_not_caught_by_denylist_alone():
    # Documents the known limitation this defense-in-depth layer has: a key name with no
    # denylist-matching substring and a value with no denylist-matching pattern passes here.
    # This is why PRIVACY-002's actual fix is the allow-list projection builder
    # (public_trajectory_projection.py), not a stronger denylist -- this test pins the
    # *documented* boundary of this layer.
    result = scan_public_only({"totally_novel_unrelated_key_name": "ordinary looking value"})
    assert result["status"] == "PASS"
