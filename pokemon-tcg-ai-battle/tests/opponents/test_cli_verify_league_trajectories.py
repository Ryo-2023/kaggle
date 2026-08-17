"""Tests for the `python -m mage_ptcg.opponents verify-league-trajectories` subcommand."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from mage_ptcg.opponents.cli import main as cli_main
from mage_ptcg.opponents.public_trajectory_evidence import persist_game_evidence
from mage_ptcg.opponents.public_trajectory_projection import build_public_trajectory_events
from mage_ptcg.opponents.trajectory import compute_trajectory_digests


def _player():
    return {"active": [None], "asleep": False, "bench": [None] * 5, "benchMax": 5, "burned": False,
            "confused": False, "deckCount": 52, "discard": [], "hand": [], "handCount": 0,
            "paralyzed": False, "poisoned": False, "prize": [{"id": 9}] * 6}


def _obs(your_index, *, result=None):
    current = {"yourIndex": your_index, "players": [_player(), _player()], "energyAttached": False,
               "retreated": False, "stadium": None, "stadiumPlayed": False, "supporterPlayed": False}
    if result is not None:
        current["result"] = result
    select = {"type": 0, "option": [{"type": 14, "index": 0}]}
    return {"current": current, "logs": [], "search_begin_input": "tok", "select": select, "step": 1}


def _canonical_steps():
    return [
        [{"observation": _obs(0), "action": None, "status": "ACTIVE"}, {"observation": _obs(1), "action": None, "status": "ACTIVE"}],
        [{"observation": _obs(0), "action": [0], "status": "ACTIVE"}, {"observation": _obs(1), "action": None, "status": "ACTIVE"}],
        [{"observation": _obs(0, result=0), "action": None, "status": "DONE"}, {"observation": _obs(1, result=0), "action": None, "status": "DONE"}],
    ]


def test_verify_league_trajectories_subcommand_dispatches_to_independent_verifier(tmp_path, capsys):
    steps = _canonical_steps()
    events = build_public_trajectory_events(steps)
    runtime_digests = compute_trajectory_digests(events)
    persist_game_evidence(tmp_path, "g0", canonical_steps=steps, runtime_digests=runtime_digests, metadata={})

    exit_code = cli_main(["verify-league-trajectories", "--evidence", str(tmp_path), "--json"])
    assert exit_code == 0
    captured = capsys.readouterr()
    assert '"independently_verified_count":1' in captured.out.replace(" ", "")


def test_verify_league_trajectories_reports_mismatch_without_crashing(tmp_path, capsys):
    steps = _canonical_steps()
    persist_game_evidence(
        tmp_path, "g0", canonical_steps=steps,
        runtime_digests={"complete_trajectory_digest": "wrong", "initial_observation_digest": "wrong",
                          "action_trace_digest": "wrong", "terminal_observation_digest": "wrong"},
        metadata={},
    )
    exit_code = cli_main(["verify-league-trajectories", "--evidence", str(tmp_path), "--json"])
    assert exit_code == 0  # the CLI wrapper itself succeeds; mismatches are reported in the payload, not a crash
    captured = capsys.readouterr()
    assert '"digest_mismatches":1' in captured.out.replace(" ", "")


def test_full_mode_without_anchor_flags_exits_nonzero_and_reports_unanchored(tmp_path):
    (tmp_path / "games").mkdir()
    repo_root = Path(__file__).resolve().parents[2]
    completed = subprocess.run(
        [sys.executable, "-m", "mage_ptcg.opponents.independent_trajectory_verifier", "--evidence", str(tmp_path), "--json", "--mode", "full"],
        cwd=repo_root, capture_output=True, text=True,
        env={**os.environ, "PYTHONPATH": str(repo_root / "src"), "PYTHONDONTWRITEBYTECODE": "1"},
    )
    assert completed.returncode != 0
    assert json.loads(completed.stdout)["status"] == "UNANCHORED_EVIDENCE"
