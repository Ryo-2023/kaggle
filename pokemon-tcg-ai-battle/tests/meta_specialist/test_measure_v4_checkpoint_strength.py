"""Contracts for the fixed-pool V4 checkpoint strength runner."""

from __future__ import annotations

import importlib.util
import hashlib
import json
from pathlib import Path

import pytest

torch = pytest.importorskip("torch")

from mage_ptcg.meta_specialist.neural_model_v4 import SpecialistModelV4, save_specialist_checkpoint_v4


def _load_runner():
    script = Path(__file__).resolve().parents[2] / "scripts" / "measure_v4_checkpoint_strength.py"
    spec = importlib.util.spec_from_file_location("measure_v4_checkpoint_strength", script)
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
    def __init__(self, opponent_id: str, deck_csv_path: Path | None = None) -> None:
        self.opponent_id = opponent_id
        self.deck_csv_path = str(deck_csv_path) if deck_csv_path is not None else f"/{opponent_id}.csv"
        self.canonical_deck_hash = hashlib.sha256(f"deck:{opponent_id}".encode()).hexdigest()
        self.policy_hash = hashlib.sha256(f"policy:{opponent_id}".encode()).hexdigest()


def test_runner_uses_fixed_six_opponents_and_marks_faults_comparison_invalid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Breaks if a V4 result can hide a fault or silently change its held-out pool."""
    runner = _load_runner()
    checkpoint = tmp_path / "subject.pt"
    subject_deck = tmp_path / "subject.csv"
    subject_deck.write_text("1\n", encoding="utf-8")
    descriptor = save_specialist_checkpoint_v4(
        checkpoint,
        SpecialistModelV4(card_vocabulary_size=64, hidden_dim=16, embedding_dim=12, seed=11).eval(),
    )
    output = tmp_path / "report.json"
    calls: list[dict[str, object]] = []

    monkeypatch.setattr(runner, "load_opponent_pool_v1", lambda _root: object())
    monkeypatch.setattr(runner, "resolve_opponent_v1", lambda _pool, opponent_id, **_kwargs: _Opponent(opponent_id, subject_deck))
    monkeypatch.setattr(runner, "build_opponent_agent_factory_v1", lambda opponent: opponent.opponent_id)
    monkeypatch.setattr(runner, "_v4_subject_factory", lambda **_kwargs: "subject-factory")
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
    assert report["opponent_ids"] == list(runner.EVAL_HELD_OUT_V1)
    assert report["requested_games"] == 12
    assert report["games_played"] == 11
    assert report["faults"] == 1
    assert report["comparison_status"] == "invalid_faults"
    assert report["checkpoint"]["file_sha256"] == hashlib.sha256(checkpoint.read_bytes()).hexdigest()
    assert report["checkpoint"]["tensor_state_sha256"] == descriptor["tensor_state_sha256"]
    assert {call["max_steps"] for call in calls} == {123}
    assert {call["seed"] for call in calls} == {37}
    assert {call["agent_a_factory"] for call in calls} == {"subject-factory", *runner.EVAL_HELD_OUT_V1}
    assert {call["agent_b_factory"] for call in calls} == {"subject-factory", *runner.EVAL_HELD_OUT_V1}


def test_runner_requires_an_explicit_json_output(tmp_path: Path) -> None:
    """Breaks if a measurement can run without preserving its provenance artifact."""
    runner = _load_runner()
    with pytest.raises(SystemExit):
        runner.main([
            "--checkpoint", str(tmp_path / "missing.pt"),
            "--subject-deck-csv", "/subject.csv",
            "--subject-archetype-id", "alakazam",
        ])


def test_runner_allows_only_a_prefix_of_the_fixed_pool_for_a_small_actual_screen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Breaks if a short screen can substitute arbitrary opponents for held-out IDs."""
    runner = _load_runner()
    checkpoint = tmp_path / "subject.pt"
    subject_deck = tmp_path / "subject.csv"
    subject_deck.write_text("1\n", encoding="utf-8")
    save_specialist_checkpoint_v4(
        checkpoint,
        SpecialistModelV4(card_vocabulary_size=64, hidden_dim=16, embedding_dim=12, seed=17).eval(),
    )
    output = tmp_path / "screen.json"
    monkeypatch.setattr(runner, "load_opponent_pool_v1", lambda _root: object())
    monkeypatch.setattr(runner, "resolve_opponent_v1", lambda _pool, opponent_id, **_kwargs: _Opponent(opponent_id, subject_deck))
    monkeypatch.setattr(runner, "build_opponent_agent_factory_v1", lambda opponent: opponent.opponent_id)
    monkeypatch.setattr(runner, "_v4_subject_factory", lambda **_kwargs: "subject-factory")
    monkeypatch.setattr(runner, "ProgressReporterV1", lambda **_kwargs: _Reporter())
    monkeypatch.setattr(runner, "seed_agent_randomness_v1", lambda _seed: None)
    monkeypatch.setattr(runner, "run_match", lambda **_kwargs: {"status": "DONE", "winner": 2})

    assert runner.main([
        "--checkpoint", str(checkpoint), "--subject-deck-csv", str(subject_deck),
        "--subject-archetype-id", "alakazam", "--games-per-seat", "1",
        "--opponent-count", "2", "--output", str(output),
    ]) == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["fixed_held_out_opponent_ids"] == list(runner.EVAL_HELD_OUT_V1)
    assert report["opponent_ids"] == list(runner.EVAL_HELD_OUT_V1[:2])
    assert report["requested_games"] == 4
