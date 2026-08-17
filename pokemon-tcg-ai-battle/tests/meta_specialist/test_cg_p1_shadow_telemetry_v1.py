from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from mage_ptcg.meta_specialist.cg_p1_shadow_telemetry_v1 import (
    build_shadow_record_v1,
    materialize_shadow_package,
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


def _observation() -> dict[str, object]:
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
        "select": {
            "type": 0,
            "context": 0,
            "minCount": 1,
            "maxCount": 1,
            "option": [{"type": 13, "attackId": 983}, {"type": 14}],
        },
        "step": 7,
    }


def test_shadow_record_is_same_observation_and_public_only() -> None:
    record = build_shadow_record_v1(
        _observation(),
        behavior_action=[0],
        shadow_action=[1],
        seat=0,
        game_id="game-1",
        behavior_policy_id="behavior",
        shadow_policy_id="shadow",
        decision_index=4,
        first_divergence_index=4,
        behavior_scores={"option_0": 50000},
        shadow_scores={"option_0": 42000},
    )
    encoded = json.dumps(record, ensure_ascii=False, sort_keys=True)
    assert record["schema_version"] == "cg-p1-shadow-telemetry-v1"
    assert record["actions_differ"] is True
    assert record["same_observation"] is True
    assert record["first_divergence_index"] == 4
    assert "\"hand\"" not in encoded
    assert "\"deck\"" not in encoded
    assert "\"prize\"" not in encoded
    assert "raw_observation" not in encoded
    assert len(record["public_state_digest"]) == 64
    assert len(record["legal_action_digest"]) == 64


def test_shadow_package_wrapper_executes_only_behavior_action(tmp_path: Path, monkeypatch) -> None:
    behavior = tmp_path / "behavior"
    candidate = tmp_path / "candidate"
    behavior.mkdir()
    candidate.mkdir()
    (behavior / "main.py").write_text("def agent(obs):\n    return [0]\n", encoding="utf-8")
    (candidate / "main.py").write_text("def agent(obs):\n    return [1]\n", encoding="utf-8")
    (behavior / "deck.csv").write_text("673\n" * 60, encoding="utf-8")
    (candidate / "deck.csv").write_text("673\n" * 60, encoding="utf-8")
    target = tmp_path / "shadow"
    materialize_shadow_package(
        behavior_package=behavior,
        shadow_package=candidate,
        output_package=target,
        behavior_policy_id="behavior",
        shadow_policy_id="shadow",
    )
    telemetry = tmp_path / "telemetry.jsonl"
    monkeypatch.setenv("CG_P1_SHADOW_TELEMETRY_PATH", str(telemetry))
    monkeypatch.setenv("CG_P1_SHADOW_TELEMETRY_GAME_ID", "game-1")
    monkeypatch.setenv("CG_P1_SHADOW_TELEMETRY_SEAT", "0")
    spec = importlib.util.spec_from_file_location("cg_shadow_wrapper_test", target / "main.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.agent({"select": None}) == [0]
    rows = [json.loads(line) for line in telemetry.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert rows[0]["behavior_action"] == [0]
    assert rows[0]["shadow_action"] == [1]
