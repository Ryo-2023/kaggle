from __future__ import annotations

from scripts.watch_v4_dagger_progress import _counts, _line


def test_screen_progress_is_one_game_counter_with_aggregate_fields() -> None:
    payload = {
        "stage": "games", "status": "running", "games_finished": 3,
        "games_requested": 12, "faults": 1, "transition_records": 40,
    }
    assert _counts(payload) == (3, 12)
    text = _line(payload)
    assert "3/12" in text and "faults=1" in text and "transition_records=40" in text


def test_training_progress_uses_one_seed_counter_and_nll() -> None:
    payload = {
        "stage": "training", "status": "running", "seed_index": 1,
        "seeds_total": 2, "seed": 0,
        "history_row": {"validation_complete_action_nll": 0.51},
    }
    assert _counts(payload) == (1, 2)
    assert "1/2" in _line(payload)
    assert "val_nll=0.51" in _line(payload)


def test_line_includes_training_heartbeat_and_elapsed_fields() -> None:
    payload = {
        "stage": "training", "status": "running", "seed_index": 1,
        "seeds_total": 2, "seed": 0, "epoch": 2,
        "epochs_completed": 1, "epochs_requested": 3,
        "optimizer_updates_completed": 512,
        "elapsed_seconds": 123.4, "heartbeat_age_seconds": 7.2,
    }
    text = _line(payload)
    assert "epoch=2" in text
    assert "updates=512" in text
    assert "elapsed=123s" in text
    assert "heartbeat_age=7s" in text


def test_line_includes_in_epoch_sequence_progress() -> None:
    payload = {
        "stage": "training", "status": "running", "seed_index": 1,
        "seeds_total": 2, "seed": 0, "epoch": 0,
        "sequences_completed": 18, "sequences_total": 64,
        "partial_train_complete_action_nll": 0.91,
    }
    text = _line(payload)
    assert "sequences=18/64" in text
    assert "partial_train_nll=0.91" in text
