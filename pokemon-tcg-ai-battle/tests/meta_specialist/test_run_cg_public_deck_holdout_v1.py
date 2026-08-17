from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from mage_ptcg.meta_specialist.opponent_pool_v1 import load_opponent_pool_v1
from scripts.run_cg_public_deck_holdout_v1 import (
    EXPECTED_FRESH_PUBLIC_DECK_IDS,
    HOLDOUT_ARM_IDS,
    PublicDeckHoldoutError,
    build_holdout_games,
    materialize_public_holdout_pool,
    select_public_holdout_entries,
    summarize_holdout_rows,
)


ROOT = Path(__file__).resolve().parents[2]
SOURCE_POOL = ROOT / "data/opponent_deck_pool_20260730/opponent_deck_pool.json"
CURRENT_POOL = ROOT / "opponents/pool_manifest.json"


def test_fresh_public_selection_is_exact_and_excludes_remote_aliases() -> None:
    payload = json.loads(SOURCE_POOL.read_text(encoding="utf-8"))
    current = json.loads(CURRENT_POOL.read_text(encoding="utf-8"))
    entries = select_public_holdout_entries(
        payload,
        current_deck_hashes={str(row["canonical_deck_hash"]) for row in current},
        seen_deck_hashes=set(),
    )

    assert tuple(entry.opponent_id for entry in entries) == EXPECTED_FRESH_PUBLIC_DECK_IDS
    assert all(entry.source_kind == "KAGGLE_PUBLIC_REPLAY" for entry in entries)
    assert all(set(entry.alias_source_kinds) == {"KAGGLE_PUBLIC_REPLAY"} for entry in entries)
    assert all(len(entry.deck_cards) == 60 for entry in entries)


def test_fresh_selection_rejects_prior_runtime_deck_csv_hash() -> None:
    payload = json.loads(SOURCE_POOL.read_text(encoding="utf-8"))
    row = next(row for row in payload["entries"] if row["opponent_id"] == "rule-v0-deck-ca42a47ab1c33580")
    runtime_hash = hashlib.sha256(("\n".join(str(card) for card in row["deck_cards"]) + "\n").encode()).hexdigest()

    with pytest.raises(PublicDeckHoldoutError, match="already used"):
        select_public_holdout_entries(
            payload,
            current_deck_hashes=set(),
            seen_deck_hashes={runtime_hash},
            expected_ids=(row["opponent_id"],),
        )


def test_materialized_pool_isolated_and_loadable(tmp_path: Path) -> None:
    payload = json.loads(SOURCE_POOL.read_text(encoding="utf-8"))
    entries = select_public_holdout_entries(payload, current_deck_hashes=set(), seen_deck_hashes=set())[:2]
    pool_root = materialize_public_holdout_pool(
        entries,
        output_root=tmp_path / "pool",
        pilot_main_source=ROOT / "opponents/medal_0004_01501d64/main.py",
    )

    assert pool_root != ROOT / "opponents"
    pool = load_opponent_pool_v1(pool_root)
    assert tuple(pool) == tuple(entry.opponent_id for entry in entries)
    assert (pool_root / "pool_manifest.json").is_file()
    assert not (ROOT / "opponents" / entries[0].opponent_id).exists()


def test_three_arm_games_share_public_deck_strata(tmp_path: Path) -> None:
    payload = json.loads(SOURCE_POOL.read_text(encoding="utf-8"))
    entries = select_public_holdout_entries(payload, current_deck_hashes=set(), seen_deck_hashes=set())[:2]
    pool_root = materialize_public_holdout_pool(
        entries,
        output_root=tmp_path / "pool",
        pilot_main_source=ROOT / "opponents/medal_0004_01501d64/main.py",
    )
    package = ROOT / "runs/final-sprint-autonomous/cg-p1-robust-g01-submission-package-v1/package"
    games = build_holdout_games(
        packages={arm_id: package for arm_id in HOLDOUT_ARM_IDS},
        reference_ids=tuple(entry.opponent_id for entry in entries),
        pool_root=pool_root,
        base_seeds=(180260815,),
        repetitions=1,
    )

    assert len(games) == 2 * 2 * len(HOLDOUT_ARM_IDS)
    by_arm = {
        arm_id: {(game.metadata["pair_key"], game.seed) for game in games if game.metadata["holdout_arm"] == arm_id}
        for arm_id in HOLDOUT_ARM_IDS
    }
    assert by_arm[HOLDOUT_ARM_IDS[0]] == by_arm[HOLDOUT_ARM_IDS[1]] == by_arm[HOLDOUT_ARM_IDS[2]]
    assert all(game.runner_ref.endswith(":run_public_deck_holdout_game_v1") for game in games)


def test_holdout_gate_requires_candidate_positive_on_each_seed() -> None:
    rows: list[dict[str, object]] = []
    for seed, candidate_wins in ((101, 3), (202, 0)):
        for arm_id, wins in (
            (HOLDOUT_ARM_IDS[0], candidate_wins),
            (HOLDOUT_ARM_IDS[1], 1),
            (HOLDOUT_ARM_IDS[2], 1),
        ):
            for index in range(4):
                rows.append(
                    {
                        "outcome": "win" if index < wins else "loss",
                        "seed": seed,
                        "metadata": {
                            "holdout_arm": arm_id,
                            "holdout_seed": seed,
                            "pair_key": f"deck-{index}|seat{index % 2}|rep{index}",
                        },
                    }
                )

    summary = summarize_holdout_rows(rows, games_per_arm=8)

    assert summary["decision"] == "NOT_PROMOTABLE"
    assert summary["promotion_authority"] is False
    assert summary["authority"]["promotion_allowed"] is False
