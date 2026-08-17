from __future__ import annotations

import json
from pathlib import Path

import pytest

from mage_ptcg.meta_specialist.cg_public_telemetry_v1 import (
    build_public_telemetry_record_v1,
    materialize_telemetry_package_v1,
)


def _card(card_id: int, *, player_index: int = 0, hp: int = 100) -> dict[str, object]:
    return {
        "id": card_id,
        "serial": card_id + 1000,
        "playerIndex": player_index,
        "hp": hp,
        "maxHp": 120,
        "appearThisTurn": False,
        "energies": [],
        "energyCards": [],
        "tools": [],
        "preEvolution": [],
    }


def _observation(*, select: dict[str, object] | None = None) -> dict[str, object]:
    player = {
        "active": [_card(678)],
        "bench": [],
        "benchMax": 5,
        "deckCount": 52,
        "discard": [],
        "hand": [_card(675)],
        "handCount": 1,
        "prize": [_card(900 + i) for i in range(6)],
        "poisoned": False,
        "burned": False,
        "asleep": False,
        "paralyzed": False,
        "confused": False,
    }
    opponent = {**player, "hand": None, "handCount": 4}
    return {
        "current": {
            "turn": 3,
            "turnActionCount": 2,
            "yourIndex": 0,
            "firstPlayer": 0,
            "supporterPlayed": False,
            "stadiumPlayed": False,
            "energyAttached": False,
            "retreated": False,
            "result": -1,
            "stadium": [],
            "players": [player, opponent],
        },
        "select": select
        if select is not None
        else {
            "type": 0,
            "context": 0,
            "minCount": 1,
            "maxCount": 1,
            "option": [
                {"type": 13, "attackId": 983},
                {"type": 14},
            ],
        },
        "step": 7,
        "logs": [{"type": 4, "cardId": 675}],
        "search_begin_input": "opaque-engine-token",
    }


def test_public_record_never_persists_private_zones_or_opaque_fields() -> None:
    observation = _observation()
    record = build_public_telemetry_record_v1(
        observation,
        [0],
        seat=0,
        game_id="game-1",
        candidate_id="cg-lethal-target-v1",
    )
    encoded = json.dumps(record, ensure_ascii=False, sort_keys=True)
    assert record["schema_version"] == "cg-public-telemetry-v1"
    assert record["record_type"] == "decision"
    assert "hand" not in record["self"]
    assert "deck" not in record["self"]
    assert "prize" not in record["self"]
    assert "logs" not in encoded
    assert "search_begin_input" not in encoded
    assert "raw_observation" not in encoded
    assert '"id": 675' not in encoded
    assert record["action"] == [0]


def test_deck_registration_is_redacted() -> None:
    observation = _observation(select=None)
    observation["select"] = None
    record = build_public_telemetry_record_v1(
        observation,
        [673] * 60,
        seat=1,
        game_id="game-2",
        candidate_id="cg-lethal-target-v1",
    )
    assert record["record_type"] == "deck_registration_redacted"
    assert record["deck_size"] == 60
    assert "deck_card_ids" not in record


def test_materialize_telemetry_package_refuses_clobber_and_preserves_source(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "main.py").write_text("def agent(obs):\n    return []\n", encoding="utf-8")
    (source / "deck.csv").write_text("673\n" * 60, encoding="utf-8")
    target = tmp_path / "target"
    result = materialize_telemetry_package_v1(
        source_package=source,
        output_package=target,
        candidate_id="cg-lethal-target-v1",
    )
    assert result["candidate_id"] == "cg-lethal-target-v1"
    assert (target / "cg_base.py").read_text(encoding="utf-8") == (source / "main.py").read_text(encoding="utf-8")
    assert "cg_base" in (target / "main.py").read_text(encoding="utf-8")
    with pytest.raises(FileExistsError):
        materialize_telemetry_package_v1(
            source_package=source,
            output_package=target,
            candidate_id="cg-lethal-target-v1",
        )
