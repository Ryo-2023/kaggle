from __future__ import annotations

import pytest

from mage_ptcg.meta_specialist.cg_p1_parameterization_v1 import P1ParameterConfig
from mage_ptcg.meta_specialist.deck_bound_source_cem_v1 import (
    DeckBoundSourceCemError,
    candidate_id_for_deck_config_v1,
    load_deck_bound_source_cem_plan_v1,
    select_diverse_source_elites_v1,
    source_rankable_v1,
    source_side_gate_v1,
)


def _aggregate(*, seat_gap: float, faults: int = 0, objective: float = 0.5) -> dict[str, object]:
    return {
        "valid": faults == 0 and seat_gap <= 0.05,
        "faults": faults,
        "max_seat_gap": seat_gap,
        "robust_objective": objective,
        "reference_count": 3,
    }


def test_candidate_identity_is_bound_to_deck_recipe_and_config() -> None:
    config = P1ParameterConfig.default()
    first = candidate_id_for_deck_config_v1(
        deck_recipe_id="balanced", generation=0, index=0, config=config
    )
    second = candidate_id_for_deck_config_v1(
        deck_recipe_id="search", generation=0, index=0, config=config
    )

    assert first != second
    assert first.startswith("deck-bound-source-g00-balanced-c00-")
    assert config.config_sha256()[:12] in first


def test_candidate_identity_rejects_invalid_recipe_id() -> None:
    with pytest.raises(DeckBoundSourceCemError, match="deck_recipe_id"):
        candidate_id_for_deck_config_v1(
            deck_recipe_id="../escape",
            generation=0,
            index=0,
            config=P1ParameterConfig.default(),
        )


def test_source_side_gate_requires_fault_free_small_seat_gap() -> None:
    assert source_side_gate_v1(_aggregate(seat_gap=0.0)) is True
    assert source_side_gate_v1(_aggregate(seat_gap=0.0625)) is False
    assert source_side_gate_v1(_aggregate(seat_gap=0.0, faults=1)) is False


def test_source_rankability_allows_screen_seat_noise_but_not_missing_or_faulted_refs() -> None:
    aggregate = {
        "faults": 0,
        "reference_count": 2,
        "reference_results": {
            "a": {"requested_games": 8, "faults": 0, "seat_collapse": False},
            "b": {"requested_games": 8, "faults": 0, "seat_collapse": False},
        },
    }
    assert source_rankable_v1(aggregate, expected_reference_count=2) is True
    aggregate["reference_results"]["b"]["seat_collapse"] = True
    assert source_rankable_v1(aggregate, expected_reference_count=2) is True
    aggregate["reference_results"]["b"]["faults"] = 1
    assert source_rankable_v1(aggregate, expected_reference_count=2) is False


def test_diverse_elites_take_one_candidate_per_deck_before_filling() -> None:
    results = [
        {"candidate_id": "a-high", "deck_recipe_id": "deck-a", "objective": 0.90, "valid": True, "faults": 0},
        {"candidate_id": "a-second", "deck_recipe_id": "deck-a", "objective": 0.89, "valid": True, "faults": 0},
        {"candidate_id": "b-high", "deck_recipe_id": "deck-b", "objective": 0.70, "valid": True, "faults": 0},
        {"candidate_id": "c-high", "deck_recipe_id": "deck-c", "objective": 0.60, "valid": True, "faults": 0},
    ]

    selected = select_diverse_source_elites_v1(results, elite_count=3)

    assert [item["candidate_id"] for item in selected] == ["a-high", "b-high", "c-high"]


def test_diverse_elites_reject_insufficient_valid_candidates() -> None:
    with pytest.raises(DeckBoundSourceCemError, match="enough eligible"):
        select_diverse_source_elites_v1(
            [{"candidate_id": "only", "deck_recipe_id": "deck-a", "objective": 0.1, "valid": False, "faults": 0}],
            elite_count=1,
        )


def test_plan_loader_requires_distinct_reference_and_deck_lineages(tmp_path) -> None:
    card_db = tmp_path / "cards.csv"
    card_db.write_text("cards\n", encoding="utf-8")
    p1 = tmp_path / "p1"
    p1.mkdir()
    refs = []
    for name in ("ref-a", "ref-b"):
        root = tmp_path / name
        root.mkdir()
        refs.append({"id": name, "package": str(root)})
    spec = tmp_path / "deck.json"
    spec.write_text("{}\n", encoding="utf-8")
    payload = {
        "schema_version": "self-owned-cg-deck-bound-source-cem-plan-v1",
        "source_epoch": "epoch-test",
        "seed_namespace": "seed-test",
        "card_database": str(card_db),
        "p1_source_package": str(p1),
        "public_scan_roots": [str(tmp_path)],
        "reference_specs": refs,
        "deck_recipes": [
            {"id": "deck-a", "spec": str(spec), "seed": 1, "ordinal": 0},
            {"id": "deck-b", "spec": str(spec), "seed": 2, "ordinal": 1},
        ],
    }
    path = tmp_path / "plan.json"
    path.write_text(__import__("json").dumps(payload) + "\n", encoding="utf-8")

    loaded = load_deck_bound_source_cem_plan_v1(path)

    assert loaded["source_epoch"] == "epoch-test"
    assert len(loaded["reference_specs"]) == 2
    assert len(loaded["deck_recipes"]) == 2


def test_plan_loader_rejects_duplicate_reference_ids(tmp_path) -> None:
    card_db = tmp_path / "cards.csv"
    card_db.write_text("cards\n", encoding="utf-8")
    p1 = tmp_path / "p1"
    p1.mkdir()
    spec = tmp_path / "deck.json"
    spec.write_text("{}\n", encoding="utf-8")
    payload = {
        "schema_version": "self-owned-cg-deck-bound-source-cem-plan-v1",
        "source_epoch": "epoch-test",
        "seed_namespace": "seed-test",
        "card_database": str(card_db),
        "p1_source_package": str(p1),
        "public_scan_roots": [str(tmp_path)],
        "reference_specs": [
            {"id": "same", "package": str(tmp_path)},
            {"id": "same", "package": str(tmp_path)},
        ],
        "deck_recipes": [
            {"id": "deck-a", "spec": str(spec), "seed": 1, "ordinal": 0},
            {"id": "deck-b", "spec": str(spec), "seed": 2, "ordinal": 1},
        ],
    }
    path = tmp_path / "plan.json"
    path.write_text(__import__("json").dumps(payload) + "\n", encoding="utf-8")

    with pytest.raises(DeckBoundSourceCemError, match="reference"):
        load_deck_bound_source_cem_plan_v1(path)
