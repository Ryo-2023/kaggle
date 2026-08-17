from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.run_meta_specialist_v4_dagger_screen import (
    build_dagger_jobs_v4,
    collect_dagger_screen_v4,
)


def test_build_jobs_uses_fixed_order_both_seats_and_seed_progression() -> None:
    jobs = build_dagger_jobs_v4(
        checkpoint=Path("/tmp/checkpoint.pt"),
        checkpoint_file_sha256="a" * 64,
        checkpoint_tensor_state_sha256="b" * 64,
        subject_deck_csv=Path("/tmp/deck.csv"),
        subject_archetype_id="archaludon",
        source_commit="c" * 40,
        opponent_count=2,
        games_per_seat=2,
        base_seed=900,
    )
    assert len(jobs) == 8
    assert [job.opponent_kind for job in jobs[:4]] == [
        "kiyotah_lucario", "kiyotah_lucario", "kiyotah_lucario", "kiyotah_lucario",
    ]
    assert [job.seat for job in jobs[:4]] == [0, 0, 1, 1]
    assert [job.env_seed for job in jobs] == list(range(900, 908))
    assert all(job.behavior_kind == "neural_specialist_v4" for job in jobs)
    # The V4 DAgger screen is an on-policy greedy screen.  Sampling is a
    # separate recipe in ActorPoolV1 (seeded Gumbel wrapper) and must never be
    # silently introduced by a future change to the job builder.
    assert all(job.decoding_mode == "greedy" for job in jobs)
    assert all(job.sampling_seed == 0 for job in jobs)


def test_collect_rejects_existing_output(tmp_path: Path) -> None:
    output = tmp_path / "screen.json"
    output.write_text("already exists\n", encoding="utf-8")
    with pytest.raises(ValueError, match="already exists"):
        collect_dagger_screen_v4(
            jobs=(), checkpoint=Path("/tmp/checkpoint.pt"), output=output,
            transitions_output=tmp_path / "screen.transitions.jsonl",
        )


def test_collect_writes_completed_transition_payloads_atomically(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class _Result:
        status = "completed"
        outcome = "win"
        winner = 0
        steps = 3
        transitions = ()
        fault = None

    monkeypatch.setattr("scripts.run_meta_specialist_v4_dagger_screen.run_one_actor_game_v1", lambda **_: _Result())
    deck = tmp_path / "deck.csv"
    deck.write_text("card,count\nexample,60\n", encoding="utf-8")
    job = build_dagger_jobs_v4(
        checkpoint=Path("/tmp/checkpoint.pt"),
        checkpoint_file_sha256="a" * 64,
        checkpoint_tensor_state_sha256="b" * 64,
        subject_deck_csv=deck,
        subject_archetype_id="archaludon",
        source_commit="c" * 40,
        opponent_count=1,
        games_per_seat=1,
        base_seed=900,
    )[0]
    payload = collect_dagger_screen_v4(
        jobs=(job,), checkpoint=Path("/tmp/checkpoint.pt"), output=tmp_path / "screen.json",
        transitions_output=tmp_path / "screen.transitions.jsonl",
    )
    assert payload["games_completed"] == 1
    assert payload["faults"] == 0
    saved = json.loads((tmp_path / "screen.json").read_text(encoding="utf-8"))
    assert saved["promotion_authority"] is False
    assert saved["status"] == "VALID"
    assert saved["subject_archetype_id"] == "archaludon"
    assert saved["subject_deck_file_sha256"] == hashlib.sha256(deck.read_bytes()).hexdigest()


def test_collect_marks_setup_failure_in_progress_artifact(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "scripts.run_meta_specialist_v4_dagger_screen.run_one_actor_game_v1",
        lambda **_: (_ for _ in ()).throw(RuntimeError("deck binding failed")),
    )
    deck = tmp_path / "deck.csv"
    deck.write_text("card,count\nexample,60\n", encoding="utf-8")
    job = build_dagger_jobs_v4(
        checkpoint=Path("/tmp/checkpoint.pt"),
        checkpoint_file_sha256="a" * 64,
        checkpoint_tensor_state_sha256="b" * 64,
        subject_deck_csv=deck,
        subject_archetype_id="archaludon",
        source_commit="c" * 40,
        opponent_count=1,
        games_per_seat=1,
        base_seed=900,
    )[0]
    progress = tmp_path / "screen.progress.json"
    with pytest.raises(RuntimeError, match="deck binding failed"):
        collect_dagger_screen_v4(
            jobs=(job,), checkpoint=Path("/tmp/checkpoint.pt"), output=tmp_path / "screen.json",
            transitions_output=tmp_path / "screen.transitions.jsonl", progress_path=progress,
        )
    saved = json.loads(progress.read_text(encoding="utf-8"))
    assert saved["status"] == "failed"
    assert saved["failed_job_index"] == 1
    assert saved["error_type"] == "RuntimeError"


@pytest.mark.parametrize("kind", ("missing", "directory", "symlink"))
def test_collect_rejects_non_regular_subject_deck(tmp_path: Path, kind: str) -> None:
    deck = tmp_path / "deck.csv"
    if kind == "directory":
        deck.mkdir()
    elif kind == "symlink":
        target = tmp_path / "real.csv"
        target.write_text("card,count\nexample,60\n", encoding="utf-8")
        deck.symlink_to(target)
    job = build_dagger_jobs_v4(
        checkpoint=Path("/tmp/checkpoint.pt"),
        checkpoint_file_sha256="a" * 64,
        checkpoint_tensor_state_sha256="b" * 64,
        subject_deck_csv=deck,
        subject_archetype_id="archaludon",
        source_commit="c" * 40,
        opponent_count=1,
        games_per_seat=1,
        base_seed=900,
    )[0]
    with pytest.raises(ValueError, match="subject deck"):
        collect_dagger_screen_v4(
            jobs=(job,), checkpoint=Path("/tmp/checkpoint.pt"),
            output=tmp_path / "screen.json",
            transitions_output=tmp_path / "screen.transitions.jsonl",
        )


def test_collect_rejects_mixed_subject_deck_or_archetype(tmp_path: Path) -> None:
    deck_a = tmp_path / "a.csv"
    deck_b = tmp_path / "b.csv"
    deck_a.write_text("card,count\nexample,60\n", encoding="utf-8")
    deck_b.write_text("card,count\nother,60\n", encoding="utf-8")
    common = {
        "checkpoint": Path("/tmp/checkpoint.pt"),
        "checkpoint_file_sha256": "a" * 64,
        "checkpoint_tensor_state_sha256": "b" * 64,
        "source_commit": "c" * 40,
        "opponent_count": 1,
        "games_per_seat": 1,
        "base_seed": 900,
    }
    first = build_dagger_jobs_v4(subject_deck_csv=deck_a, subject_archetype_id="archaludon", **common)[0]
    second = build_dagger_jobs_v4(subject_deck_csv=deck_b, subject_archetype_id="alakazam", **common)[0]
    with pytest.raises(ValueError, match="same subject"):
        collect_dagger_screen_v4(
            jobs=(first, second), checkpoint=Path("/tmp/checkpoint.pt"),
            output=tmp_path / "screen.json",
            transitions_output=tmp_path / "screen.transitions.jsonl",
        )
