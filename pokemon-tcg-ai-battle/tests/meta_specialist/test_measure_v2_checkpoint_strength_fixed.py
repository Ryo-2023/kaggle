"""Contracts for the fixed-pool V2 checkpoint strength runner."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


def _load_runner():
    script = Path(__file__).resolve().parents[2] / "scripts" / "measure_v2_checkpoint_strength_fixed.py"
    spec = importlib.util.spec_from_file_location("measure_v2_checkpoint_strength_fixed", script)
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


def test_runner_uses_fixed_six_opponents_and_invalidates_faulted_comparison(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A V2 comparison must retain faulted requested games and fixed ordering."""
    runner = _load_runner()
    checkpoint = tmp_path / "subject.pt"
    checkpoint.write_bytes(b"closed-v2-checkpoint")
    subject_deck = tmp_path / "subject.csv"
    subject_deck.write_text("1\n", encoding="utf-8")
    output = tmp_path / "report.json"
    calls: list[dict[str, object]] = []

    monkeypatch.setattr(runner, "load_opponent_pool_v1", lambda _root: object())
    monkeypatch.setattr(runner, "resolve_opponent_v1", lambda _pool, opponent_id, **_kwargs: _Opponent(opponent_id, subject_deck))
    monkeypatch.setattr(runner, "build_opponent_agent_factory_v1", lambda opponent: opponent.opponent_id)
    monkeypatch.setattr(runner, "_v2_subject_factory", lambda **_kwargs: "subject-factory")
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
    assert report["schema_version"] == runner.V2_FIXED_HELDOUT_CHECKPOINT_STRENGTH_SCHEMA_V1
    assert report["fixed_held_out_opponent_ids"] == list(runner.EVAL_HELD_OUT_V1)
    assert report["opponent_ids"] == list(runner.EVAL_HELD_OUT_V1)
    assert report["requested_games"] == 12
    assert report["games_played"] == 11
    assert report["faults"] == 1
    assert report["comparison_status"] == "invalid_faults"
    assert report["checkpoint"]["file_sha256"] == hashlib.sha256(checkpoint.read_bytes()).hexdigest()
    assert {call["max_steps"] for call in calls} == {123}
    assert {call["seed"] for call in calls} == {37}
    assert {call["agent_a_factory"] for call in calls} == {"subject-factory", *runner.EVAL_HELD_OUT_V1}
    assert {call["agent_b_factory"] for call in calls} == {"subject-factory", *runner.EVAL_HELD_OUT_V1}


def test_runner_allows_only_prefix_of_fixed_pool_for_small_screen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A short V2 screen cannot replace the canonical held-out IDs."""
    runner = _load_runner()
    checkpoint = tmp_path / "subject.pt"
    checkpoint.write_bytes(b"closed-v2-checkpoint")
    subject_deck = tmp_path / "subject.csv"
    subject_deck.write_text("1\n", encoding="utf-8")
    output = tmp_path / "screen.json"
    monkeypatch.setattr(runner, "load_opponent_pool_v1", lambda _root: object())
    monkeypatch.setattr(runner, "resolve_opponent_v1", lambda _pool, opponent_id, **_kwargs: _Opponent(opponent_id, subject_deck))
    monkeypatch.setattr(runner, "build_opponent_agent_factory_v1", lambda opponent: opponent.opponent_id)
    monkeypatch.setattr(runner, "_v2_subject_factory", lambda **_kwargs: "subject-factory")
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


@pytest.mark.parametrize(
    ("checkpoint", "deck_csv", "archetype_id"),
    [
        (
            "runs/from-worktree/meta-specialist-canonical/meta-specialist-bc-distill/"
            "v2smoke-alakazam/checkpoints/"
            "checkpoint-7849171dc6e70336a0222e991831a7b1df978ba9ebd87324a7dd61e172d05e79.pt",
            "opponents/nihei_alakazam/deck.csv",
            "alakazam",
        ),
        (
            "runs/from-worktree/meta-specialist-canonical/meta-specialist-bc-distill/"
            "v2smoke-archaludon/checkpoints/"
            "checkpoint-6518c148e3ac5849e0ded4cd6d45a11cc5314a716e97fe000f2853799fdcd45e.pt",
            "opponents/public_archaludon_cinderace_r7/deck.csv",
            "archaludon",
        ),
    ],
)
def test_runtime_compatible_v2smoke_checkpoints_bind_through_strict_actor_loader(
    checkpoint: str, deck_csv: str, archetype_id: str,
) -> None:
    """Both provisional V2 baselines must pass the production actor loader."""
    runner = _load_runner()
    checkpoint_path = Path(checkpoint)
    if not checkpoint_path.is_file():
        pytest.skip("local runtime-compatible V2 smoke artifact is unavailable")
    factory = runner._v2_subject_factory(
        checkpoint_path=checkpoint_path,
        file_sha256=hashlib.sha256(checkpoint_path.read_bytes()).hexdigest(),
        subject_deck_csv=Path(deck_csv),
        subject_archetype_id=archetype_id,
    )
    assert callable(factory)
