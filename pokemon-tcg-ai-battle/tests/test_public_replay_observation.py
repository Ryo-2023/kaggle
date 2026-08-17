import json
from pathlib import Path

from mage_ptcg.competition_intelligence.public_replay_observation import (
    classify_alakazam,
    extract_public_card_observations,
    partial_reconstruction,
)


def _replay() -> dict[str, object]:
    return {
        "steps": [
            [{"observation": {"current": {"players": [
                {"active": [{"id": 741}], "bench": [{"id": 741}], "discard": [{"id": 999}], "hand": [{"id": 500}], "deck": [500]},
                {"active": [{"id": 743}], "bench": [{"id": 742}], "discard": [], "hand": [{"id": 501}], "deck": [501]},
            ]}, "logs": "private", "search_begin_input": {"private": True}, "looking": [123]}}],
        ]
    }


def test_extracts_only_public_board_lower_bounds() -> None:
    rows = extract_public_card_observations(_replay(), episode_id="e", submission_id="s")
    assert {(row.seat, row.card_id, row.minimum_count) for row in rows} == {(0, 741, 2), (0, 999, 1), (1, 742, 1), (1, 743, 1)}
    assert all(500 not in {row.card_id for row in rows} and 501 not in {row.card_id for row in rows} for _ in [0])


def test_classification_and_partial_reconstruction_do_not_fill_unknown_slots() -> None:
    rows = extract_public_card_observations(_replay(), episode_id="e", submission_id="s")
    alakazam_rows = [row for row in rows if row.seat == 1]
    assert classify_alakazam(alakazam_rows) == "PROBABLE_ALAKAZAM"
    reconstruction = partial_reconstruction(alakazam_rows)
    assert reconstruction["status"] == "PARTIAL_OBSERVED_DECK"
    assert reconstruction["confirmed_slots"] == 2
    assert reconstruction["unknown_slots"] == 58


def test_alakazam_baseline_is_a_complete_non_default_deck() -> None:
    payload = json.loads((Path(__file__).parents[1] / "configs" / "alakazam" / "baseline_v1.json").read_text())
    assert len(payload["deck"]) == 60
    assert payload["provenance"].startswith("TEAM_SHARED")
    assert "default" in payload["known_limitations"][0].lower()
    classification = payload["slot_classification"]
    assert classification["core"] == [741, 742, 743]
    assert set(classification["flex"]) <= set(payload["slot_groups"]["tech"])


def test_flex_candidate_changes_only_a_configured_adjustable_slot() -> None:
    root = Path(__file__).parents[1]
    baseline = json.loads((root / "configs" / "alakazam" / "baseline_v1.json").read_text())
    candidate = json.loads((root / "configs" / "alakazam" / "flex_candidates_v1.json").read_text())["candidates"][0]
    assert candidate["status"] == "SCREENING_INCONCLUSIVE"
    assert set(candidate["remove"]) <= set(baseline["slot_classification"]["count_adjustable"])
    assert not set(candidate["preserves"]).intersection(candidate["remove"])
