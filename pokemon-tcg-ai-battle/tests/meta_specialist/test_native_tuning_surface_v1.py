from pathlib import Path

import pytest

from mage_ptcg.meta_specialist.native_tuning_surface_v1 import (
    NativeTuningSurfaceError,
    audit_native_pair_v1,
    surface_to_dict_v1,
)


ROOT = Path(__file__).resolve().parents[2]


def test_tomato_surface_is_direct_rule_tunable_without_search():
    surface = audit_native_pair_v1(
        "tomatomato_archaludon",
        ROOT / "opponents/tomatomato_archaludon/main.py",
        ROOT / "opponents/tomatomato_archaludon/deck.csv",
    )
    assert surface.asset_id == "tomatomato_archaludon"
    assert surface.has_agent_entrypoint is True
    assert surface.has_native_fallback is True
    assert surface.has_search is False
    assert "DIRECT_PARAMETER_TUNABLE" in surface.classifications
    assert "RULE_EDIT_TUNABLE" in surface.classifications
    assert any(spec.name == "_ICE_CREAM_HP_THRESHOLD" for spec in surface.parameters)
    assert surface.policy_sha256 == surface_to_dict_v1(surface)["policy_sha256"]


def test_plamen_surface_exposes_search_budget_and_native_fallback():
    surface = audit_native_pair_v1(
        "plamen06_steel",
        ROOT / "opponents/plamen06_steel/main.py",
        ROOT / "opponents/plamen06_steel/deck.csv",
    )
    names = {spec.name for spec in surface.parameters}
    assert surface.has_search is True
    assert {"BUDGET", "CAND", "MAXD", "MARGIN"}.issubset(names)
    assert "SEARCH_ROLLOUT_READY" in surface.classifications
    assert surface.native_fallback_reason


def test_surface_audit_fails_closed_for_missing_or_malformed_source(tmp_path: Path):
    with pytest.raises(NativeTuningSurfaceError, match="main.py"):
        audit_native_pair_v1("missing", tmp_path / "main.py", tmp_path / "deck.csv")
    main = tmp_path / "main.py"
    deck = tmp_path / "deck.csv"
    main.write_text("def agent(:\n", encoding="utf-8")
    deck.write_text("1\n" * 60, encoding="utf-8")
    with pytest.raises(NativeTuningSurfaceError, match="syntax"):
        audit_native_pair_v1("broken", main, deck)


def test_surface_rejects_illegal_deck_card_count(tmp_path: Path):
    main = tmp_path / "main.py"
    deck = tmp_path / "deck.csv"
    main.write_text("def agent(obs):\n    return [0]\n", encoding="utf-8")
    deck.write_text("1\n" * 59, encoding="utf-8")
    with pytest.raises(NativeTuningSurfaceError, match="60"):
        audit_native_pair_v1("short", main, deck)
