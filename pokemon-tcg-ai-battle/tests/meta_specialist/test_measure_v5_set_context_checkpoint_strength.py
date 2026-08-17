"""研究専用V5 SetContext checkpoint evaluatorの契約テスト。"""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

torch = pytest.importorskip("torch")

from mage_ptcg.meta_specialist.neural_model_v4 import SpecialistModelV4, save_specialist_checkpoint_v4
from mage_ptcg.meta_specialist.neural_model_v5 import transfer_specialist_checkpoint_v4_to_v5


def _load_runner():
    script = Path(__file__).resolve().parents[2] / "scripts" / "measure_v5_set_context_checkpoint_strength.py"
    spec = importlib.util.spec_from_file_location("measure_v5_set_context_checkpoint_strength", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Reporter:
    def note(self, _message: str) -> None:
        pass

    def update(self, _count: int, **_kwargs: object) -> None:
        pass

    def close(self) -> None:
        pass


class _Opponent:
    def __init__(self, opponent_id: str, deck_csv_path: Path) -> None:
        self.opponent_id = opponent_id
        self.deck_csv_path = str(deck_csv_path)
        self.canonical_deck_hash = hashlib.sha256(f"deck:{opponent_id}".encode()).hexdigest()
        self.policy_hash = hashlib.sha256(f"policy:{opponent_id}".encode()).hexdigest()


def _v5_fixture(tmp_path: Path) -> tuple[Path, dict[str, object]]:
    base_path = tmp_path / "base-v4.pt"
    save_specialist_checkpoint_v4(
        base_path,
        SpecialistModelV4(card_vocabulary_size=64, hidden_dim=16, embedding_dim=12, seed=11).eval(),
    )
    payload = torch.load(base_path, map_location="cpu", weights_only=True)
    v5_path = tmp_path / "subject-v5.pt"
    descriptor = transfer_specialist_checkpoint_v4_to_v5(
        base_path,
        v5_path,
        expected_base_file_sha256=hashlib.sha256(base_path.read_bytes()).hexdigest(),
        expected_base_tensor_state_sha256=payload["descriptor"]["tensor_state_sha256"],
        head_seed=13,
    )
    return v5_path, descriptor


def test_runner_uses_fixed_six_both_seats_and_preserves_v5_provenance(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Breaks if V5 reports use the V4 schema or hide base provenance."""
    runner = _load_runner()
    checkpoint, descriptor = _v5_fixture(tmp_path)
    subject_deck = tmp_path / "subject.csv"
    subject_deck.write_text("1\n", encoding="utf-8")
    output = tmp_path / "report.json"
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(runner, "load_opponent_pool_v1", lambda _root: object())
    monkeypatch.setattr(runner, "resolve_opponent_v1", lambda _pool, opponent_id, **_kwargs: _Opponent(opponent_id, subject_deck))
    monkeypatch.setattr(runner, "build_opponent_agent_factory_v1", lambda opponent: opponent.opponent_id)
    monkeypatch.setattr(runner, "_v5_subject_factory", lambda **_kwargs: "subject-factory")
    monkeypatch.setattr(runner, "ProgressReporterV1", lambda **_kwargs: _Reporter())
    monkeypatch.setattr(runner, "seed_agent_randomness_v1", lambda _seed: None)

    def fake_match(**kwargs: object) -> dict[str, object]:
        calls.append(kwargs)
        if len(calls) == 1:
            return {"status": "DONE", "winner": 0}
        if len(calls) == 2:
            return {"status": "FAULT", "winner": None}
        return {"status": "DONE", "winner": 2}

    monkeypatch.setattr(runner, "run_match", fake_match)
    assert runner.main([
        "--checkpoint", str(checkpoint),
        "--subject-deck-csv", str(subject_deck),
        "--subject-archetype-id", "alakazam",
        "--games-per-seat", "1",
        "--base-seed", "37",
        "--max-steps", "123",
        "--output", str(output),
    ]) == 0

    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["schema_version"] == runner.V5_HELDOUT_CHECKPOINT_STRENGTH_SCHEMA_V1
    assert report["opponent_ids"] == list(runner.EVAL_HELD_OUT_V1)
    assert report["requested_games"] == 12
    assert report["games_played"] == 11
    assert report["faults"] == 1
    assert report["comparison_status"] == "invalid_faults"
    assert report["checkpoint"]["tensor_state_sha256"] == descriptor["tensor_state_sha256"]
    assert report["checkpoint"]["base_provenance"] == descriptor["base_provenance"]
    assert report["v5_artifact"]["checkpoint_schema"] == runner.CHECKPOINT_SCHEMA_V5
    assert len(report["v5_artifact"]["descriptor_sha256"]) == 64
    assert report["evaluation_protocol_sha256"] == runner.heldout_protocol_sha256_v1()
    assert {call["max_steps"] for call in calls} == {123}
    assert {call["seed"] for call in calls} == {37}
    assert {call["agent_a_factory"] for call in calls} == {"subject-factory", *runner.EVAL_HELD_OUT_V1}
    assert {call["agent_b_factory"] for call in calls} == {"subject-factory", *runner.EVAL_HELD_OUT_V1}


def test_checkpoint_provenance_rejects_v4_artifact(tmp_path: Path) -> None:
    runner = _load_runner()
    checkpoint = tmp_path / "v4.pt"
    save_specialist_checkpoint_v4(
        checkpoint,
        SpecialistModelV4(card_vocabulary_size=64, hidden_dim=16, embedding_dim=12, seed=17).eval(),
    )
    with pytest.raises(ValueError, match="V5|v5|schema"):
        runner._checkpoint_provenance_v5(checkpoint)


def test_runner_requires_explicit_json_output(tmp_path: Path) -> None:
    runner = _load_runner()
    with pytest.raises(SystemExit):
        runner.main([
            "--checkpoint", str(tmp_path / "missing.pt"),
            "--subject-deck-csv", "/subject.csv",
            "--subject-archetype-id", "alakazam",
        ])
