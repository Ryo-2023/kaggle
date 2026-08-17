from __future__ import annotations

import json
from pathlib import Path

import pytest

from mage_ptcg.meta_specialist.cg_p1_public_hypothesis_v1 import (
    PUBLIC_HYPOTHESIS_SCHEMA,
    analyze_public_hypotheses_v1,
    bucket_public_state_v1,
    load_public_decisions_v1,
)


def _row(*, game_id: str, operation: str, outcome: str, turn: int = 4) -> dict[str, object]:
    return {
        "schema_version": "cg-public-telemetry-v1",
        "record_type": "decision",
        "game_id": game_id,
        "candidate_id": "cg-lethal-target-v1",
        "seat": 0,
        "turn": turn,
        "turn_action_count": 1,
        "step": turn + 1,
        "options": [{"option_index": 0, "type": 7, "type_name": operation, "fields": {}}],
        "action": [0],
        "select": {"type": 0, "type_name": "MAIN", "context": 0, "min_count": 1, "max_count": 1},
        "board": {"energy_attached": True, "retreated": False, "stadium_played": False, "supporter_played": False},
        "self": {"active": [{"fields": {"id": 678, "hp": 220, "maxHp": 220, "energies_count": 2, "tools_count": 0}}], "bench": [], "bench_max": 5, "status": {}},
        "opponent": {"active": [{"fields": {"id": 721, "hp": 250, "maxHp": 300, "energies_count": 1, "tools_count": 0}}], "bench": [], "bench_max": 5, "status": {}},
        "outcome": outcome,
    }


def test_bucket_uses_only_allowlisted_public_projection() -> None:
    row = _row(game_id="g0", operation="ATTACK", outcome="win")
    bucket = bucket_public_state_v1(row)
    assert bucket["operation"] == "ATTACK"
    assert bucket["self_active_id"] == 678
    assert bucket["opponent_active_id"] == 721
    assert "hand" not in repr(bucket)
    assert "prize" not in repr(bucket)
    assert "deck" not in repr(bucket)


def test_load_public_decisions_binds_terminal_wdl_and_rejects_private_keys(tmp_path: Path) -> None:
    telemetry = tmp_path / "telemetry"
    telemetry.mkdir()
    (telemetry / "g0.jsonl").write_text(json.dumps(_row(game_id="g0", operation="ATTACK", outcome="win")) + "\n", encoding="utf-8")
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text(json.dumps({"game_id": "g0", "outcome": "win", "status": "DONE", "fault_kind": None}) + "\n", encoding="utf-8")
    rows = load_public_decisions_v1(telemetry_root=telemetry, ledger_path=ledger)
    assert len(rows) == 1
    assert rows[0].outcome == "win"
    assert rows[0].schema_version == PUBLIC_HYPOTHESIS_SCHEMA

    bad = _row(game_id="g1", operation="PLAY", outcome="loss")
    bad["opponent"]["hand"] = []  # type: ignore[index]
    (telemetry / "bad.jsonl").write_text(json.dumps(bad) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="forbidden|private"):
        load_public_decisions_v1(telemetry_root=telemetry, ledger_path=ledger)


def test_analyzer_caps_candidates_and_fails_closed_without_competing_signal() -> None:
    rows = []
    for i in range(12):
        rows.append(_row(game_id=f"g{i}", operation="ATTACK", outcome="win" if i < 8 else "loss"))
    result = analyze_public_hypotheses_v1(rows, min_support=8, max_candidates=3)
    assert result["schema_version"] == PUBLIC_HYPOTHESIS_SCHEMA
    assert result["ready_for_candidate_screen"] is False
    assert result["candidates"] == []
    assert "competing" in " ".join(result["reasons"])
