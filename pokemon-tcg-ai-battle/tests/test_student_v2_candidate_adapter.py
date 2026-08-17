from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from mage_ptcg.offline_scaleup.candidate_runtime import (
    STUDENT_V2_LOADER,
    CandidateRuntimeError,
    StudentV2CandidateAdapter,
    _deck_fingerprint,
    adapter_for,
)


DECK = [1] * 60


def _observation() -> dict[str, object]:
    card = {"id": 1, "serial": 0, "playerIndex": 0, "hp": 100, "maxHp": 100, "appearThisTurn": False,
            "energies": [], "energyCards": [], "tools": [], "preEvolution": []}
    player = {"active": [], "asleep": False, "bench": [], "benchMax": 5, "burned": False, "confused": False,
              "deckCount": 53, "discard": [], "hand": [card], "handCount": 1, "paralyzed": False, "poisoned": False,
              "prize": [object() for _ in range(6)]}
    return {"current": {"energyAttached": False, "firstPlayer": 0, "players": [player, player], "result": -1,
                         "retreated": False, "stadium": [], "stadiumPlayed": False, "supporterPlayed": False,
                         "turn": 2, "turnActionCount": 3, "yourIndex": 0},
            "select": {"context": 0, "maxCount": 1, "minCount": 1, "option": [{"type": 14}, {"type": 13, "attackId": 1}], "type": 0},
            "step": 7}


def _write_checkpoint(model_dir: Path) -> str:
    import torch
    from mage_ptcg.offline_scaleup.gpu_student_v2 import _model

    torch.manual_seed(0)
    model = _model(hidden=8, blocks=1, dropout=0.0)
    model_dir.mkdir(parents=True, exist_ok=True)
    checkpoint = {"schema_version": "offline-scaleup-student-v2", "epoch": 0, "model": model.state_dict(),
                  "optimizer": {}, "best_score": 0.5, "epochs_without_improvement": 0,
                  "config": {"hidden": 8, "blocks": 1, "dropout": 0.0}}
    torch.save(checkpoint, model_dir / "best.pt")
    (model_dir / "training_summary.json").write_text(
        json.dumps({"hidden": 8, "blocks": 1, "dropout": 0.0}), encoding="utf-8"
    )
    return hashlib.sha256((model_dir / "best.pt").read_bytes()).hexdigest()


def _entry(*, model_dir: Path, model_sha256: str, deck_fingerprint: str, device: str = "cpu") -> dict[str, object]:
    return {
        "opponent_id": "student-v2-run-a", "opponent_type": "STUDENT_AGENT", "loader": STUDENT_V2_LOADER,
        "runtime_fingerprint": model_sha256, "deck_fingerprint": deck_fingerprint, "family_id": None,
        "teacher_trust": "LIMITED", "validation_status": "VALIDATED", "evaluation_eligibility": "ALLOWED",
        "training_eligibility": "ALLOWED_FOR_VALID_FAULT_FREE_GAMES", "evidence_paths": [],
        "provenance": {"model_dir": str(model_dir), "model_sha256": model_sha256, "device": device},
    }


def test_adapter_for_returns_student_v2_adapter(tmp_path: Path) -> None:
    digest = _write_checkpoint(tmp_path / "model")
    entry = _entry(model_dir=tmp_path / "model", model_sha256=digest, deck_fingerprint=_deck_fingerprint(DECK))
    assert isinstance(adapter_for(entry), StudentV2CandidateAdapter)


def test_prepare_rejects_deck_fingerprint_mismatch(tmp_path: Path) -> None:
    digest = _write_checkpoint(tmp_path / "model")
    entry = _entry(model_dir=tmp_path / "model", model_sha256=digest, deck_fingerprint="wrong-fingerprint")
    with pytest.raises(CandidateRuntimeError, match="deck fingerprint"):
        adapter_for(entry).prepare(DECK)


def test_prepare_rejects_missing_model_directory(tmp_path: Path) -> None:
    entry = _entry(model_dir=tmp_path / "does-not-exist", model_sha256="0" * 64, deck_fingerprint=_deck_fingerprint(DECK))
    with pytest.raises(CandidateRuntimeError, match="model directory"):
        adapter_for(entry).prepare(DECK)


def test_prepare_rejects_checkpoint_digest_mismatch(tmp_path: Path) -> None:
    _write_checkpoint(tmp_path / "model")
    entry = _entry(model_dir=tmp_path / "model", model_sha256="0" * 64, deck_fingerprint=_deck_fingerprint(DECK))
    with pytest.raises(CandidateRuntimeError, match="digest mismatch"):
        adapter_for(entry).prepare(DECK)


def test_prepared_adapter_decides_and_captures_a_legal_action(tmp_path: Path) -> None:
    digest = _write_checkpoint(tmp_path / "model")
    entry = _entry(model_dir=tmp_path / "model", model_sha256=digest, deck_fingerprint=_deck_fingerprint(DECK))
    adapter = adapter_for(entry).prepare(DECK)
    assert adapter.decide({"select": None}) == DECK
    choice = adapter.decide(_observation())
    assert choice and set(choice).issubset({0, 1})
    captured = adapter.capture(_observation(), choice, game_id="game-1", candidate_side=0, deck=DECK)
    assert captured is not None
    _example, telemetry = captured
    assert telemetry["fallback_used"] is False
    assert telemetry["provenance"]["adapter_type"] == "student_v2_candidate_v1"


def test_telemetry_capabilities_declare_fallback_support(tmp_path: Path) -> None:
    digest = _write_checkpoint(tmp_path / "model")
    entry = _entry(model_dir=tmp_path / "model", model_sha256=digest, deck_fingerprint=_deck_fingerprint(DECK))
    adapter = adapter_for(entry)
    assert adapter.telemetry_capabilities["fallback"] is True
