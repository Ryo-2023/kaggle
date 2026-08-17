from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

import scripts.run_batch_eval as batch


def _write_deck(path: Path) -> Path:
    path.write_text("\n".join(["1"] * 60) + "\n", encoding="utf-8")
    return path


def _raw_result(*, status: str = "DONE", winner: int | None = 0, steps: int | None = 10) -> dict[str, Any]:
    return {
        "status": status,
        "winner": winner,
        "rewards": [1, -1] if status == "DONE" else None,
        "steps": steps,
        "cabt_turn": 5 if steps is not None else None,
        "terminal_reason": "cabt_result=0; reason=3" if winner is not None else "missing winner",
        "elapsed_seconds": 0.25 if steps is not None else None,
        "max_steps": 100,
        "engine_seed_supported": False,
    }


def _stub_preflight(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(batch, "_preflight", lambda **_kwargs: object())


def _run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_run_match,
    **kwargs: Any,
) -> dict[str, Any]:
    _stub_preflight(monkeypatch)
    monkeypatch.setattr(batch, "run_match", fake_run_match)
    deck = _write_deck(tmp_path / "deck.csv")
    return batch.run_batch_evaluation(
        deck_a_path=deck,
        deck_b_path=deck,
        agent_a_name="random",
        agent_b_name="deterministic",
        num_matches=kwargs.pop("num_matches", 2),
        base_seed=kwargs.pop("base_seed", 1000),
        max_steps=kwargs.pop("max_steps", 100),
        output_dir=kwargs.pop("output_dir", tmp_path / "evaluation"),
        **kwargs,
    )


@pytest.mark.parametrize("field", ["num_matches", "max_steps"])
def test_non_positive_batch_limits_are_rejected(tmp_path: Path, field: str) -> None:
    deck = _write_deck(tmp_path / "deck.csv")
    kwargs = {"num_matches": 1, "max_steps": 100}
    kwargs[field] = 0

    with pytest.raises(ValueError, match="positive integer"):
        batch.run_batch_evaluation(
            deck_a_path=deck,
            deck_b_path=deck,
            agent_a_name="random",
            agent_b_name="deterministic",
            base_seed=1,
            output_dir=tmp_path / "evaluation",
            **kwargs,
        )


def test_seats_alternate_and_winners_map_to_agent_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, Any]] = []

    def fake_run_match(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return _raw_result(winner=0)

    summary = _run(tmp_path, monkeypatch, fake_run_match, num_matches=4, alternate_seats=True)
    records = [json.loads(line) for line in (tmp_path / "evaluation" / "matches.jsonl").read_text().splitlines()]

    assert [record["agent_a_player_index"] for record in records] == [0, 1, 0, 1]
    assert [record["winner_agent"] for record in records] == ["agent_a", "agent_b", "agent_a", "agent_b"]
    assert [record["agent_a_seed"] for record in records] == [1000, 1002, 1002, 1004]
    assert [call["seed"] for call in calls] == [1000, 1001, 1002, 1003]
    assert summary["agent_a_wins"] == 2
    assert summary["agent_b_wins"] == 2


def test_draws_and_non_done_matches_are_not_wins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    results = iter(
        [
            _raw_result(winner=2),
            _raw_result(status="AGENT_INVALID", winner=None),
        ]
    )

    summary = _run(tmp_path, monkeypatch, lambda **_kwargs: next(results))

    assert summary["completed_matches"] == 1
    assert summary["draws"] == 1
    assert summary["agent_a_wins"] == 0
    assert summary["agent_b_wins"] == 0
    assert summary["completion_rate"] == 0.5


def test_summary_handles_zero_decisive_matches_and_numeric_statistics() -> None:
    records = [
        {
            "status": "DONE",
            "winner_agent": "draw",
            "winner_player_index": 2,
            "agent_a_player_index": 0,
            "agent_b_player_index": 1,
            "player_0_agent": "random",
            "player_1_agent": "deterministic",
            "steps": 4,
            "cabt_turn": 2,
            "elapsed_seconds": 0.2,
            "terminal_reason": "draw",
        },
        {
            "status": "DONE",
            "winner_agent": "draw",
            "winner_player_index": 2,
            "agent_a_player_index": 1,
            "agent_b_player_index": 0,
            "player_0_agent": "deterministic",
            "player_1_agent": "random",
            "steps": 8,
            "cabt_turn": 6,
            "elapsed_seconds": 0.6,
            "terminal_reason": "draw",
        },
        {
            "status": "STEP_LIMIT",
            "winner_agent": None,
            "winner_player_index": None,
            "agent_a_player_index": 0,
            "agent_b_player_index": 1,
            "player_0_agent": "random",
            "player_1_agent": "deterministic",
            "steps": 100,
            "cabt_turn": None,
            "elapsed_seconds": 1.0,
            "terminal_reason": "STEP_LIMIT",
        },
    ]

    summary = batch.build_summary(records, requested_matches=3)

    assert summary["agent_a_decisive_win_rate"] is None
    assert summary["agent_b_decisive_win_rate"] is None
    assert summary["match_length"]["steps"] == {
        "count": 2,
        "mean": 6.0,
        "median": 6.0,
        "minimum": 4.0,
        "maximum": 8.0,
    }
    assert summary["status_distribution"]["DONE"] == 2
    assert summary["status_distribution"]["STEP_LIMIT"] == 1
    assert summary["terminal_reason_distribution"] == {"STEP_LIMIT": 1, "draw": 2}


def test_jsonl_and_summary_files_are_written(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    summary = _run(tmp_path, monkeypatch, lambda **_kwargs: _raw_result(), num_matches=3)
    output_dir = tmp_path / "evaluation"
    lines = (output_dir / "matches.jsonl").read_text(encoding="utf-8").splitlines()

    assert len(lines) == 3
    assert [json.loads(line)["match_index"] for line in lines] == [0, 1, 2]
    assert json.loads((output_dir / "summary.json").read_text(encoding="utf-8")) == summary
    assert "completion_rate" in (output_dir / "summary.csv").read_text(encoding="utf-8")


def test_existing_output_is_rejected_unless_overwrite_is_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output_dir = tmp_path / "evaluation"
    output_dir.mkdir()
    (output_dir / "matches.jsonl").write_text("old\n", encoding="utf-8")
    _stub_preflight(monkeypatch)

    with pytest.raises(FileExistsError, match="--overwrite"):
        batch.run_batch_evaluation(
            deck_a_path=_write_deck(tmp_path / "deck.csv"),
            deck_b_path=tmp_path / "deck.csv",
            agent_a_name="random",
            agent_b_name="deterministic",
            num_matches=1,
            base_seed=1,
            max_steps=100,
            output_dir=output_dir,
        )

    monkeypatch.setattr(batch, "run_match", lambda **_kwargs: _raw_result())
    summary = batch.run_batch_evaluation(
        deck_a_path=tmp_path / "deck.csv",
        deck_b_path=tmp_path / "deck.csv",
        agent_a_name="random",
        agent_b_name="deterministic",
        num_matches=1,
        base_seed=1,
        max_steps=100,
        output_dir=output_dir,
        overwrite=True,
    )
    assert summary["attempted_matches"] == 1
    assert len((output_dir / "matches.jsonl").read_text(encoding="utf-8").splitlines()) == 1


def test_rule_agent_name_uses_the_existing_batch_registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_preflight(monkeypatch)
    monkeypatch.setattr(batch, "run_match", lambda **_kwargs: _raw_result())
    deck = _write_deck(tmp_path / "deck.csv")
    summary = batch.run_batch_evaluation(
        deck_a_path=deck,
        deck_b_path=deck,
        agent_a_name="rule",
        agent_b_name="rule",
        num_matches=1,
        base_seed=1,
        max_steps=100,
        output_dir=tmp_path / "evaluation",
        save_html="none",
    )

    assert summary["attempted_matches"] == 1
    assert "rule" in batch.AGENT_NAMES


def test_one_match_exception_is_recorded_and_later_matches_continue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0

    def fake_run_match(**_kwargs: Any) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("synthetic failure")
        return _raw_result()

    summary = _run(tmp_path, monkeypatch, fake_run_match)
    lines = [json.loads(line) for line in (tmp_path / "evaluation" / "matches.jsonl").read_text().splitlines()]

    assert [line["status"] for line in lines] == ["ERROR", "DONE"]
    assert summary["attempted_matches"] == 2
    assert summary["completed_matches"] == 1


def test_keyboard_interrupt_flushes_completed_jsonl_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0

    def fake_run_match(**_kwargs: Any) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise KeyboardInterrupt
        return _raw_result()

    with pytest.raises(KeyboardInterrupt):
        _run(tmp_path, monkeypatch, fake_run_match)

    lines = (tmp_path / "evaluation" / "matches.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["status"] == "DONE"


@pytest.mark.parametrize(
    ("policy", "expected"),
    [("none", False), ("failures", "failures"), ("all", True)],
)
def test_save_html_policy_is_forwarded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, policy: str, expected: bool | str
) -> None:
    observed: list[bool | str] = []

    def fake_run_match(**kwargs: Any) -> dict[str, Any]:
        observed.append(kwargs["save_html"])
        return _raw_result()

    _run(tmp_path, monkeypatch, fake_run_match, save_html=policy)

    assert observed == [expected, expected]


@pytest.mark.skipif(
    importlib.util.find_spec("kaggle_environments") is None,
    reason="kaggle-environments with cabt is not installed",
)
def test_real_cabt_batch_smoke(tmp_path: Path) -> None:
    output_dir = tmp_path / "real-cabt"
    summary = batch.run_batch_evaluation(
        deck_a_path=Path("deck.csv"),
        deck_b_path=Path("deck.csv"),
        agent_a_name="random",
        agent_b_name="deterministic",
        num_matches=2,
        base_seed=1000,
        max_steps=10000,
        output_dir=output_dir,
        save_html="none",
    )

    assert summary["attempted_matches"] == 2
    assert len((output_dir / "matches.jsonl").read_text(encoding="utf-8").splitlines()) == 2
