from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from mage_ptcg.meta_specialist.cg_bestknown_loop_v1 import build_fresh_meta_batch_v1
from mage_ptcg.meta_specialist.cg_weekend_split_v1 import load_weekend_split
from mage_ptcg.meta_specialist.opponent_pool_v1 import load_opponent_pool_v1
from mage_ptcg.observability.cabt_trace import canonical_deck_sha256
from mage_ptcg.opponent_ingest.behavior_family_meta_v1 import (
    COMFEY_BEHAVIOR_VARIANTS_V1,
    DerivedInternalMetaError,
    FESTIVAL_BEHAVIOR_VARIANTS_V1,
    ALAKAZAM_BEHAVIOR_VARIANTS_V1,
    PSYCHIC_BEHAVIOR_VARIANTS_V1,
    METAL_BEHAVIOR_VARIANTS_V1,
    _replace_comfey_behavior,
    _replace_festival_behavior,
    _replace_alakazam_behavior,
    _replace_psychic_behavior,
    _replace_metal_behavior,
    _replace_metal_runtime_safe_behavior,
    _replace_starmie_behavior,
    seal_comfey_behavior_family_v1,
    seal_festival_behavior_family_v1,
    seal_metal_behavior_family_v1,
    seal_starmie_behavior_family_v1,
)


def _base(tmp_path: Path) -> Path:
    root = tmp_path / "base"
    root.mkdir()
    main = """BOSSES_ORDERS = 1
CRISPIN = 2
HILDA = 3
JUDGE = 4
LILLIE_DECISION = 5
BUDEW = 6
STARYU = 7
SNORUNT = 8
MUNKIDORI = 9
MEOWTH_EX = 10
SUPPORTER_PRIORITY_ORDER = [BOSSES_ORDERS, CRISPIN, HILDA, JUDGE, LILLIE_DECISION]
BASIC_PLAY_PRIORITY = {
    BUDEW: 500,
    STARYU: 400,
    SNORUNT: 300,
    MUNKIDORI: 200,
    MEOWTH_EX: 100,
}
POFFIN_IDEAL_COUNT = {STARYU: 2, SNORUNT: 1, BUDEW: 1}
COMFEY_LO_SELF_DECK_RESERVE = 4
COMFEY_LO_COMFEY = 164
COMFEY_LO_MAWILE = 987
COMFEY_LO_MIMIKYU = 767
COMFEY_LO_LITWICK = 97
COMFEY_LO_DUNSPARCE = 65

def _comfey_lo_setup_priority(card_id, *, active):
    if active:
        return {
            COMFEY_LO_COMFEY: 1000,
            COMFEY_LO_MAWILE: 900,
            COMFEY_LO_MIMIKYU: 800,
        }.get(card_id, 0)
    return {
        COMFEY_LO_LITWICK: 1000,
        COMFEY_LO_COMFEY: 950,
        COMFEY_LO_DUNSPARCE: 900,
    }.get(card_id, 0)

def agent(obs):
    return []
"""
    deck = "\n".join(str(index) for index in range(1, 61)) + "\n"
    (root / "main.py").write_text(main, encoding="utf-8")
    (root / "deck.csv").write_text(deck, encoding="utf-8")
    source_sha = hashlib.sha256(main.encode()).hexdigest()
    deck_sha = hashlib.sha256(deck.encode()).hexdigest()
    canonical = canonical_deck_sha256(list(range(1, 61)))
    (root / "SOURCE.md").write_text(
        "\n".join(
            [
                "# Internal source snapshot",
                "",
                "- branch: `agents/test-starmie`",
                "- commit: `0123456789abcdef0123456789abcdef01234567`",
                f"- source policy SHA-256: `{source_sha}`",
                f"- staged policy SHA-256: `{source_sha}`",
                f"- deck bytes SHA-256: `{deck_sha}`",
                f"- canonical deck SHA-256: `{canonical}`",
                "- localization patch: `NONE` (0 replacement(s))",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return root


def _p1(tmp_path: Path) -> Path:
    root = tmp_path / "p1"
    root.mkdir()
    (root / "main.py").write_text("def agent(obs):\n    return []\n", encoding="utf-8")
    (root / "deck.csv").write_text("\n".join(str(index) for index in range(1, 61)) + "\n", encoding="utf-8")
    return root


def _festival_base(tmp_path: Path) -> Path:
    parent = tmp_path / "festival-parent"
    parent.mkdir()
    root = _base(parent)
    main_path = root / "main.py"
    main = main_path.read_text(encoding="utf-8") + """

POKEMON_PRIORITY = {
    ALAKAZAM: 600,
    KADABRA: 500,
    ABRA: 400,
    DUNSPARCE: 300,
    LEGACY_DUNSPARCE: 300,
    SHAYMIN: 250,
    DUDUNSPARCE: 200,
    FEZANDIPITI_EX: 100,
}
SETUP_ACTIVE_PRIORITY = {
    DUNSPARCE: 300,
    LEGACY_DUNSPARCE: 300,
    ABRA: 200,
    SHAYMIN: 150,
    FEZANDIPITI_EX: 100,
}

def _festival_select(source, has_abra, has_dunsparce):
    if source == BUDDY_BUDDY_POFFIN:
        search_priority = {
            ABRA: 1000 if not has_abra else 800,
            DUNSPARCE: 950 if not has_dunsparce else 750,
            LEGACY_DUNSPARCE: 950 if not has_dunsparce else 750,
            SHAYMIN: 300,
        }
        return search_priority
    return SETUP_ACTIVE_PRIORITY
"""
    main_path.write_text(main, encoding="utf-8")
    main_sha = hashlib.sha256(main.encode()).hexdigest()
    note_path = root / "SOURCE.md"
    note = note_path.read_text(encoding="utf-8")
    source_old = note.split("- source policy SHA-256: `", 1)[1].split("`", 1)[0]
    staged_old = note.split("- staged policy SHA-256: `", 1)[1].split("`", 1)[0]
    note = note.replace(source_old, main_sha, 1).replace(staged_old, main_sha, 1)
    note_path.write_text(note, encoding="utf-8")
    return root


def test_starmie_transform_is_exact_and_fail_closed() -> None:
    source = b"SUPPORTER_PRIORITY_ORDER = [BOSSES_ORDERS, CRISPIN, HILDA, JUDGE, LILLIE_DECISION]\n"
    transformed, recipe = _replace_starmie_behavior(source, "SUPPORTER_DRAW_FIRST")
    assert recipe.endswith(":SUPPORTER_DRAW_FIRST")
    assert b"[LILLIE_DECISION, JUDGE, CRISPIN, HILDA, BOSSES_ORDERS]" in transformed
    with pytest.raises(DerivedInternalMetaError):
        _replace_starmie_behavior(source, "UNKNOWN")
    with pytest.raises(DerivedInternalMetaError):
        _replace_starmie_behavior(source + source, "SUPPORTER_DRAW_FIRST")


def test_comfey_transform_is_exact() -> None:
    source = b"COMFEY_LO_SELF_DECK_RESERVE = 4\n"
    transformed, recipe = _replace_comfey_behavior(source, "DECKOUT_AGGRESSIVE")
    assert recipe.endswith(":DECKOUT_AGGRESSIVE")
    assert b"COMFEY_LO_SELF_DECK_RESERVE = 2" in transformed
    with pytest.raises(DerivedInternalMetaError):
        _replace_comfey_behavior(source, "UNKNOWN")


def test_festival_transform_is_exact() -> None:
    source = b"""POKEMON_PRIORITY = {
    ALAKAZAM: 600,
    KADABRA: 500,
    ABRA: 400,
    DUNSPARCE: 300,
    LEGACY_DUNSPARCE: 300,
    SHAYMIN: 250,
    DUDUNSPARCE: 200,
    FEZANDIPITI_EX: 100,
}
"""
    transformed, recipe = _replace_festival_behavior(source, "ALAKAZAM_FIRST")
    assert recipe.endswith(":ALAKAZAM_FIRST")
    assert b"ALAKAZAM: 300" in transformed
    assert b"DUNSPARCE: 600" in transformed
    with pytest.raises(DerivedInternalMetaError):
        _replace_festival_behavior(source, "UNKNOWN")
    with pytest.raises(DerivedInternalMetaError):
        _replace_festival_behavior(source + source, "ALAKAZAM_FIRST")


def test_alakazam_transform_is_exact() -> None:
    source = b"""POKEMON_PRIORITY = {
    ALAKAZAM: 600,
    KADABRA: 500,
    ABRA: 400,
    DUNSPARCE: 300,
    DUDUNSPARCE: 200,
    FEZANDIPITI_EX: 100,
}
"""
    transformed, recipe = _replace_alakazam_behavior(source, "ABRA_FIRST")
    assert recipe.endswith(":ABRA_FIRST")
    assert b"ALAKAZAM: 400" in transformed
    assert b"ABRA: 700" in transformed
    with pytest.raises(DerivedInternalMetaError):
        _replace_alakazam_behavior(source, "UNKNOWN")
    with pytest.raises(DerivedInternalMetaError):
        _replace_alakazam_behavior(source + source, "ABRA_FIRST")


def test_psychic_transform_is_exact() -> None:
    source = b"""POKEMON_PRIORITY = {
    MELOETTA_EX: 400,
    ZACIAN: 300,
    XERNEAS_EX: 200,
    ENAMORUS: 100,
}
SUPPORTER_PRIORITY = {
    LILLIES_DETERMINATION: 300,
    ZEYU: 200,
    CHEREN: 100,
}
"""
    transformed, recipe = _replace_psychic_behavior(source, "ZACIAN_FIRST")
    assert recipe.endswith(":ZACIAN_FIRST")
    assert b"MELOETTA_EX: 250" in transformed
    assert b"ZACIAN: 500" in transformed
    with pytest.raises(DerivedInternalMetaError):
        _replace_psychic_behavior(source, "UNKNOWN")
    with pytest.raises(DerivedInternalMetaError):
        _replace_psychic_behavior(source + source, "ZACIAN_FIRST")


def test_metal_transform_is_exact() -> None:
    source = b"""POKEMON_PRIORITY = {
    PIPLUP: 800,
    BELDUM: 700,
    BUDEW: 600,
    GENESECT_EX: 500,
    DIALGA: 400,
    LATIAS_EX: 300,
    CLEFAIRY_EX: 200,
    MEGA_MAWILE_EX: 100,
    METANG: 650,
    PRINPLUP: 630,
    METAGROSS_EX: 350,
    EMPOLEON_EX: 380,
}
"""
    transformed, recipe = _replace_metal_behavior(source, "METAGROSS_FIRST")
    assert recipe.endswith(":METAGROSS_FIRST")
    assert b"METAGROSS_EX: 1000" in transformed
    assert b"EMPOLEON_EX: 900" in transformed
    with pytest.raises(DerivedInternalMetaError):
        _replace_metal_behavior(source, "UNKNOWN")
    with pytest.raises(DerivedInternalMetaError):
        _replace_metal_behavior(source + source, "METAGROSS_FIRST")


def test_metal_transform_accepts_current_snapshot_without_prinplup() -> None:
    source = b"""POKEMON_PRIORITY = {
    PIPLUP: 800,
    BELDUM: 700,
    BUDEW: 600,
    GENESECT_EX: 500,
    DIALGA: 400,
    LATIAS_EX: 300,
    CLEFAIRY_EX: 200,
    MEGA_MAWILE_EX: 100,
    METANG: 650,
    METAGROSS_EX: 350,
    EMPOLEON_EX: 380,
}
"""
    transformed, recipe = _replace_metal_behavior(source, "PIPLUP_FIRST")
    assert recipe.endswith(":PIPLUP_FIRST")
    assert b"PIPLUP: 1000" in transformed
    assert b"BELDUM: 550" in transformed

    transformed, recipe = _replace_metal_behavior(source, "METAGROSS_FIRST")
    assert recipe.endswith(":METAGROSS_FIRST")
    assert b"METANG: 850" in transformed
    assert b"METAGROSS_EX: 1000" in transformed
    assert b"EMPOLEON_EX: 900" in transformed


def test_metal_runtime_safe_transform_disables_search_exactly() -> None:
    source = b"""POKEMON_PRIORITY = {
    PIPLUP: 800,
    BELDUM: 700,
    BUDEW: 600,
    GENESECT_EX: 500,
    DIALGA: 400,
    LATIAS_EX: 300,
    CLEFAIRY_EX: 200,
    MEGA_MAWILE_EX: 100,
    METANG: 650,
    PRINPLUP: 630,
    METAGROSS_EX: 350,
    EMPOLEON_EX: 380,
}
SEARCH_NUM_WORLDS = 3
SEARCH_LOCAL_FIXED_BUDGET = float(os.environ.get("SEARCH_LOCAL_FIXED_BUDGET", "1.0"))
"""
    transformed, recipe = _replace_metal_runtime_safe_behavior(source, "RULE_ONLY_METAGROSS_FIRST")
    assert recipe.endswith(":RULE_ONLY_METAGROSS_FIRST")
    assert b"METAGROSS_EX: 1000" in transformed
    assert b"SEARCH_NUM_WORLDS = 0" in transformed
    assert b'os.environ.get("SEARCH_LOCAL_FIXED_BUDGET", "0.0")' in transformed
    with pytest.raises(DerivedInternalMetaError):
        _replace_metal_runtime_safe_behavior(source, "METAGROSS_FIRST")


def test_seal_builds_fresh_behavior_pool_and_split(tmp_path: Path) -> None:
    base = _base(tmp_path)
    current_pool = tmp_path / "current" / "pool_manifest.json"
    current_pool.parent.mkdir()
    current_pool.write_text("[]\n", encoding="utf-8")
    output = tmp_path / "behavior"
    report = seal_starmie_behavior_family_v1(
        base_root=base,
        output_root=output,
        source_epoch="behavior-epoch",
        seed_namespace="behavior-seed",
        p1_package=_p1(tmp_path),
        current_pool_manifest=current_pool,
    )
    assert report["status"] == "SEALED"
    assert report["accepted_count"] == 4
    assert len(load_opponent_pool_v1(output)) == 4
    batch = build_fresh_meta_batch_v1(
        manifest_path=output / "fresh_meta.json",
        pool_manifest_path=output / "pool_manifest.json",
    )
    assert len(batch.reference_ids) == 4
    split = load_weekend_split(output / "cg_historical_split.json")
    assert len(split.ids("META_TRAIN")) == 2
    assert len(split.ids("META_DEV")) == 1
    assert len(split.ids("META_FINAL")) == 1


def test_seal_comfey_family_uses_same_fresh_contract(tmp_path: Path) -> None:
    base = _base(tmp_path)
    current_pool = tmp_path / "current" / "pool_manifest.json"
    current_pool.parent.mkdir()
    current_pool.write_text("[]\n", encoding="utf-8")
    output = tmp_path / "comfey"
    report = seal_comfey_behavior_family_v1(
        base_root=base,
        output_root=output,
        source_epoch="comfey-epoch",
        seed_namespace="comfey-seed",
        p1_package=_p1(tmp_path),
        variants=COMFEY_BEHAVIOR_VARIANTS_V1,
        current_pool_manifest=current_pool,
    )
    assert report["status"] == "SEALED"
    assert report["accepted_count"] == 4
    assert len(load_opponent_pool_v1(output)) == 4


def test_seal_festival_family_uses_same_fresh_contract(tmp_path: Path) -> None:
    base = _festival_base(tmp_path)
    current_pool = tmp_path / "current" / "pool_manifest.json"
    current_pool.parent.mkdir()
    current_pool.write_text("[]\n", encoding="utf-8")
    output = tmp_path / "festival"
    report = seal_festival_behavior_family_v1(
        base_root=base,
        output_root=output,
        source_epoch="festival-epoch",
        seed_namespace="festival-seed",
        p1_package=_p1(tmp_path),
        variants=FESTIVAL_BEHAVIOR_VARIANTS_V1,
        current_pool_manifest=current_pool,
    )
    assert report["status"] == "SEALED"
    assert report["accepted_count"] == 4
    batch = build_fresh_meta_batch_v1(
        manifest_path=output / "fresh_meta.json",
        pool_manifest_path=output / "pool_manifest.json",
    )
    assert len(batch.reference_ids) == 4
    split = load_weekend_split(output / "cg_historical_split.json")
    assert len(split.ids("META_TRAIN")) == 2
    assert len(split.ids("META_DEV")) == 1
    assert len(split.ids("META_FINAL")) == 1
