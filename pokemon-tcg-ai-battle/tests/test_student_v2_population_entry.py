from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from mage_ptcg.offline_scaleup.candidate_runtime import STUDENT_V2_LOADER, _deck_fingerprint
from mage_ptcg.offline_scaleup.pipeline import ContractError, add_student_v2_entry, validate_population


DECK = [1] * 60


def _base_population(tmp_path: Path) -> Path:
    rule_entry = {
        "opponent_id": "rule-v0-current-deck", "opponent_type": "RULE_V0_DECK", "source_path": "agents/rule_agent.py",
        "deck_id": "current-deck", "deck_fingerprint": _deck_fingerprint(DECK), "runtime_id": "rule-agent-v0",
        "runtime_fingerprint": "a" * 64, "agent_digest": "a" * 64, "validation_status": "VALIDATED",
        "availability_status": "AVAILABLE", "evaluation_eligibility": "ALLOWED",
        "training_eligibility": "ALLOWED_FOR_VALID_FAULT_FREE_GAMES", "teacher_trust": "TRUSTED",
        "quarantine_reason": None, "family_id": None, "strategy_tags": ["rule"], "variant_tags": [],
        "evidence_paths": [], "loader": "rule_v0", "deck_cards": DECK,
    }
    population = {"schema_version": "offline-scaleup-population-v2", "entries": [rule_entry],
                  "semantic_population_digest": "old-digest", "alias_count": 0, "population_id": "population-old"}
    path = tmp_path / "population.json"
    path.write_text(json.dumps(population), encoding="utf-8")
    return path


def _checkpoint(tmp_path: Path) -> Path:
    import torch
    from mage_ptcg.offline_scaleup.gpu_student_v2 import _model

    torch.manual_seed(0)
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    checkpoint = {"schema_version": "offline-scaleup-student-v2", "epoch": 0, "model": _model(hidden=8, blocks=1, dropout=0.0).state_dict(),
                  "optimizer": {}, "best_score": 0.5, "epochs_without_improvement": 0, "config": {"hidden": 8, "blocks": 1, "dropout": 0.0}}
    torch.save(checkpoint, model_dir / "best.pt")
    (model_dir / "training_summary.json").write_text(json.dumps({"hidden": 8, "blocks": 1, "dropout": 0.0}), encoding="utf-8")
    return model_dir


def test_add_student_v2_entry_preserves_old_snapshot_and_appends_one_entry(tmp_path: Path) -> None:
    old_path = _base_population(tmp_path)
    old_bytes = old_path.read_bytes()
    model_dir = _checkpoint(tmp_path)
    output = tmp_path / "population_with_student_v2.json"
    result = add_student_v2_entry(old_population_path=old_path, output=output, model_dir=model_dir, device="cpu")
    assert old_path.read_bytes() == old_bytes
    assert len(result["entries"]) == 2
    new_entry = next(entry for entry in result["entries"] if entry["opponent_type"] == "STUDENT_AGENT")
    assert new_entry["loader"] == STUDENT_V2_LOADER
    assert new_entry["deck_fingerprint"] == _deck_fingerprint(DECK)
    assert new_entry["provenance"]["model_dir"] == str(model_dir)
    assert new_entry["runtime_fingerprint"] == hashlib.sha256((model_dir / "best.pt").read_bytes()).hexdigest()
    validate_population(result)


def test_add_student_v2_entry_refuses_to_overwrite_existing_output(tmp_path: Path) -> None:
    old_path = _base_population(tmp_path)
    model_dir = _checkpoint(tmp_path)
    output = tmp_path / "population_with_student_v2.json"
    add_student_v2_entry(old_population_path=old_path, output=output, model_dir=model_dir, device="cpu")
    with pytest.raises(ContractError, match="already exists"):
        add_student_v2_entry(old_population_path=old_path, output=output, model_dir=model_dir, device="cpu")


def test_add_student_v2_entry_requires_rule_v0_current_deck_anchor(tmp_path: Path) -> None:
    path = tmp_path / "population.json"
    path.write_text(json.dumps({"schema_version": "offline-scaleup-population-v2", "entries": [],
                                 "semantic_population_digest": "d", "alias_count": 0, "population_id": "p"}), encoding="utf-8")
    model_dir = _checkpoint(tmp_path)
    with pytest.raises(ContractError, match="rule-v0-current-deck"):
        add_student_v2_entry(old_population_path=path, output=tmp_path / "out.json", model_dir=model_dir, device="cpu")
