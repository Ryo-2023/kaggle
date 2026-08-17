from __future__ import annotations

from pathlib import Path

import pytest


def _sha(char: str) -> str:
    return char * 64


def _asset(asset_id: str, *, smoke_ok: bool = True):
    from scripts.run_asset_pair_ranking_v1 import AssetPairV1

    return AssetPairV1(
        asset_id=asset_id,
        deck_csv_path=Path(f"/tmp/{asset_id}/deck.csv"),
        policy_path=Path(f"/tmp/{asset_id}/main.py"),
        deck_sha256=_sha("a"),
        policy_sha256=_sha("b"),
        canonical_deck_hash=_sha("c"),
        smoke_ok=smoke_ok,
        usage_boundary="local_eval_only",
        source="public",
    )


def test_reference_selection_replaces_subject_without_duplicate() -> None:
    from scripts.run_asset_pair_ranking_v1 import select_reference_opponents_v1

    selected = select_reference_opponents_v1(
        "subject",
        ("subject", "reference-a", "reference-b"),
        available_ids=("subject", "reference-a", "reference-b", "fallback"),
        fallback_ids=("fallback",),
    )
    assert selected == ("reference-a", "reference-b", "fallback")
    assert "subject" not in selected
    assert len(selected) == len(set(selected))


def test_build_asset_games_preserves_pair_identity_and_balances_seats() -> None:
    from scripts.run_asset_pair_ranking_v1 import build_asset_ranking_games_v1

    assets = (_asset("subject"), _asset("other"))
    games = build_asset_ranking_games_v1(
        assets,
        reference_ids=("subject", "reference-a"),
        available_ids=("subject", "other", "reference-a", "fallback"),
        fallback_ids=("fallback",),
        games_per_opponent_seat=1,
        base_seed=7000000,
    )
    assert len(games) == 8
    assert {game.seat for game in games} == {0, 1}
    assert all(game.runner_ref.endswith(":run_native_asset_pair_game_v1") for game in games)
    assert all(game.policy_id.startswith("native-") for game in games)
    assert all(game.opponent_id != game.metadata["subject_id"] for game in games)
    assert {game.metadata["subject_id"] for game in games} == {"subject", "other"}


def test_asset_inventory_can_include_smoke_false_as_diagnostic() -> None:
    from scripts.run_asset_pair_ranking_v1 import filter_asset_inventory_v1

    assets = (_asset("ok"), _asset("diagnostic", smoke_ok=False))
    assert [a.asset_id for a in filter_asset_inventory_v1(assets, include_smoke_false=False)] == ["ok"]
    assert [a.asset_id for a in filter_asset_inventory_v1(assets, include_smoke_false=True)] == ["diagnostic", "ok"]
