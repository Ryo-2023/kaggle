"""Tests for O6-AUD-002's addition of canonical_steps/public_trajectory_events to play_game's result.

play_game already computed trajectory digests from environment.steps; this
confirms the same run also exposes both canonical_steps (the exact input the
public trajectory evidence writer needs) and public_trajectory_events (the
allow-list-projected events actually hashed) so the League script can
persist evidence without recomputing environment access twice.
"""
from __future__ import annotations

import os
from pathlib import Path

import kaggle_environments

from mage_ptcg.opponents.league_runtime import NativeAgentWorker, play_game
from mage_ptcg.opponents.public_trajectory_projection import build_public_trajectory_events
from mage_ptcg.opponents.trajectory import canonical_step_seat, compute_trajectory_digests


class _FakeState:
    def __init__(self, status: str) -> None:
        self.status = status


class _FakeEnvironment:
    def __init__(self, steps, statuses, configuration_keys):
        self.steps = steps
        self.state = [_FakeState(status) for status in statuses]
        self.configuration = {key: None for key in configuration_keys}

    def run(self, agents) -> None:
        pass


def _player():
    return {"active": [None], "asleep": False, "bench": [None] * 5, "benchMax": 5, "burned": False,
            "confused": False, "deckCount": 52, "discard": [], "hand": [], "handCount": 0,
            "paralyzed": False, "poisoned": False, "prize": [{"id": 9}] * 6}


def _select():
    return {"type": 0, "option": [{"type": 14, "index": 0}]}


def _obs(your_index, *, result=None):
    current = {"yourIndex": your_index, "players": [_player(), _player()], "energyAttached": False,
               "retreated": False, "stadium": None, "stadiumPlayed": False, "supporterPlayed": False}
    if result is not None:
        current["result"] = result
    return {"current": current, "logs": [], "search_begin_input": "tok", "remainingOverageTime": 600, "select": _select(), "step": 1}


def _step(seat0_action, seat1_action, status0="ACTIVE", status1="ACTIVE", result=None):
    return [
        {"action": seat0_action, "observation": _obs(0, result=result), "status": status0},
        {"action": seat1_action, "observation": _obs(1, result=result), "status": status1},
    ]


def _fake_steps():
    return [
        _step(None, None),
        _step([0], None),
        _step(None, [0]),
        _step(None, None, status0="DONE", status1="DONE", result=0),
    ]


def test_play_game_returns_canonical_steps_matching_trajectory_digest(monkeypatch):
    steps = _fake_steps()

    def fake_make(env_name, configuration):
        return _FakeEnvironment(steps, ["DONE", "DONE"], ["decks", "episodeSteps", "actTimeout", "runTimeout"])

    monkeypatch.setattr(kaggle_environments, "make", fake_make)
    result = play_game(deck_a=[1] * 60, deck_b=[1] * 60, call_a=lambda *a, **k: [], call_b=lambda *a, **k: [])

    assert result["canonical_steps"] is not None
    expected = [[canonical_step_seat(seat) for seat in step] for step in steps]
    assert result["canonical_steps"] == expected

    assert result["public_trajectory_events"] is not None
    expected_events = build_public_trajectory_events(expected)
    assert result["public_trajectory_events"] == expected_events
    # public_trajectory_events must be exactly what compute_trajectory_digests hashed internally
    assert compute_trajectory_digests(expected_events)["complete_trajectory_digest"] == result["trajectory"]["complete_trajectory_digest"]


def test_play_game_fault_path_still_returns_canonical_steps_when_steps_exist(monkeypatch):
    steps = _fake_steps()

    def fake_make(env_name, configuration):
        env = _FakeEnvironment(steps, ["ACTIVE", "ACTIVE"], ["decks"])

        def raising_run(agents):
            raise TimeoutError("agent decision exceeded timeout")

        env.run = raising_run
        return env

    monkeypatch.setattr(kaggle_environments, "make", fake_make)
    result = play_game(deck_a=[1] * 60, deck_b=[1] * 60, call_a=lambda *a, **k: [], call_b=lambda *a, **k: [])

    assert result["status"] == "AGENT_TIMEOUT"
    assert result["canonical_steps"] is not None
    assert len(result["canonical_steps"]) == len(steps)
    assert result["public_trajectory_events"] is not None
    assert len(result["public_trajectory_events"]) == len(steps)


def test_native_agent_worker_accepts_relative_source_root(tmp_path):
    """Regression test for a latent bug found while running the real O6-AUD-002 League.

    subprocess.Popen(cwd=source_root) resolves a *relative* source_root
    against the parent's cwd (correct), but the harness script previously
    received that same relative string as an argv and re-joined it against
    the *child's own* (already-relocated) cwd via os.path.join(root, rel),
    doubling the path and raising FileNotFoundError before the agent could
    ever be imported -- invisible as long as every caller happened to pass
    an absolute source_root, which every prior League run did.
    """
    agent_dir = tmp_path / "fake-agent"
    agent_dir.mkdir()
    (agent_dir / "main.py").write_text("def agent(observation, configuration=None):\n    return []\n", encoding="utf-8")
    relative_source_root = os.path.relpath(agent_dir, Path.cwd())

    worker = NativeAgentWorker(relative_source_root, "main.py:agent", decision_timeout_seconds=10.0)
    try:
        result = worker({"dummy": True}, {})
        assert result == []
    finally:
        worker.close()
