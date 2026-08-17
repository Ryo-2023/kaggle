from __future__ import annotations

from scripts.analyze_public_replay_visualizations import _exact_decks_from_visualize, deck_hash


def test_exact_visualize_reader_requires_two_literal_60_card_arrays() -> None:
    payload = {"steps": [[{"visualize": [{"action": [[1] * 60, [2] * 60]}]}]]}
    decks, frames = _exact_decks_from_visualize(payload)
    assert decks == [[1] * 60, [2] * 60]
    assert frames == 1
    assert deck_hash(decks[0]) != deck_hash(decks[1])


def test_exact_visualize_reader_rejects_non_public_or_incomplete_arrays() -> None:
    payload = {"steps": [[{"observation": {"current": {"players": [{"deck": [1] * 60}]}}, "visualize": [{"action": [[1] * 59, [2] * 60]}]}]]}
    decks, frames = _exact_decks_from_visualize(payload)
    assert decks is None
    assert frames == 1
