"""Tests for O6-AUD-002's public-projection evidence + integrity-chain finalize step in run_o6_team_league.py.

Population loading and the real cabt engine are heavy dependencies this
script normally uses; these tests monkeypatch `_load_participants` (so no
real Population/artifact-store bundle is needed) and `play_game` (so no
real cabt game is played), while leaving the independent verifier itself as
a real subprocess invocation -- the abort-on-mismatch test relies on the
real verifier subprocess actually detecting a genuine digest mismatch, not
on mocking its result. TRUSTED_ROOT_REGISTRY_PATH is monkeypatched to a
tmp_path file so tests never write to the real, git-tracked trusted root
registry.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import scripts.run_o6_team_league as run_o6_team_league  # noqa: E402
from mage_ptcg.opponents.public_trajectory_projection import build_public_trajectory_events  # noqa: E402


def _fake_manifest():
    return {"cabt_version": "test", "population_identity_hash": "test-hash", "ruleset_version": "test-ruleset"}


def _fake_participants():
    return {
        "agent-a": {"kind": "rule", "deck": [1] * 60, "label": "agent-a"},
        "agent-b": {"kind": "rule", "deck": [1] * 60, "label": "agent-b"},
    }, _fake_manifest()


def _player():
    return {"active": [None], "asleep": False, "bench": [None] * 5, "benchMax": 5, "burned": False,
            "confused": False, "deckCount": 52, "discard": [], "hand": [], "handCount": 0,
            "paralyzed": False, "poisoned": False, "prize": [{"id": 9}] * 6}


def _obs(your_index, *, attack_id=None, result=None):
    current = {"yourIndex": your_index, "players": [_player(), _player()], "energyAttached": False,
               "retreated": False, "stadium": None, "stadiumPlayed": False, "supporterPlayed": False}
    if result is not None:
        current["result"] = result
    select = {"type": 0, "option": [{"type": 13, "attackId": attack_id, "count": attack_id}]}
    return {"current": current, "logs": [], "search_begin_input": "tok", "select": select, "step": 1}


def _canonical_step(action, *, attack_id=None, status="ACTIVE", result=None):
    return {"observation": _obs(0, attack_id=attack_id, result=result), "action": action, "status": status}


def _canonical_steps(variant: int):
    # Real engine pairing: a seat's select at raw step i is answered by that seat's action at
    # raw step i + 1, so the varying select (attack_id) belongs on step 0 to affect step 1's
    # projected action.
    return [
        [_canonical_step(None, attack_id=variant), {"observation": _obs(1), "action": None, "status": "ACTIVE"}],
        [_canonical_step([0]), {"observation": _obs(1), "action": None, "status": "ACTIVE"}],
        [{**_canonical_step(None, status="DONE", result=0)}, {"observation": _obs(1, result=0), "action": None, "status": "DONE"}],
    ]


def _valid_trajectory(canonical_steps):
    from mage_ptcg.opponents.trajectory import compute_trajectory_digests
    return compute_trajectory_digests(build_public_trajectory_events(canonical_steps))


def _install_fakes(monkeypatch, *, play_game_impl):
    monkeypatch.setattr(run_o6_team_league, "_load_participants", lambda *, population_dir, deck_path: _fake_participants())

    class _FakeStore:
        def __init__(self, artifact_store):
            pass

        def fetch_to_cache(self, population, cache_dir, verify_hashes=True):
            cache_dir = Path(cache_dir)
            cache_dir.mkdir(parents=True, exist_ok=True)
            return cache_dir

    monkeypatch.setattr(run_o6_team_league, "LocalArtifactStore", _FakeStore)
    monkeypatch.setattr(run_o6_team_league, "play_game", play_game_impl)
    monkeypatch.setattr(run_o6_team_league, "_make_callable", lambda participant, **kwargs: (lambda *a, **k: [], None))


def _base_args(tmp_path):
    return [
        "--artifact-store", str(tmp_path / "store"),
        "--population", "unused",
        "--cache-dir", str(tmp_path / "cache"),
        "--output-dir", str(tmp_path / "evidence" / "league"),
        "--evidence-root", str(tmp_path / "evidence"),
        "--games-per-pair", "2",
        "--base-seed", "1000",
    ]


def test_valid_run_produces_v4_summary_and_evidence_root_artifacts(tmp_path, monkeypatch):
    trusted_root_registry = tmp_path / "trusted_roots.json"
    monkeypatch.setattr(run_o6_team_league, "TRUSTED_ROOT_REGISTRY_PATH", trusted_root_registry)

    def fake_play_game(*, deck_a, deck_b, call_a, call_b, max_steps):
        fake_play_game.calls += 1
        steps = _canonical_steps(fake_play_game.calls)
        return {"status": "DONE", "winner": 0, "elapsed_seconds": 0.01, "agent_status": ["DONE", "DONE"], "steps": len(steps),
                "trajectory": _valid_trajectory(steps), "engine_seed_support": "ENGINE_SEED_UNSUPPORTED", "canonical_steps": steps}
    fake_play_game.calls = 0

    _install_fakes(monkeypatch, play_game_impl=fake_play_game)
    exit_code = run_o6_team_league.main(_base_args(tmp_path))
    assert exit_code == 0

    evidence_root = tmp_path / "evidence"
    assert (evidence_root / "checksums.sha256").exists()
    assert (evidence_root / "run_summary.json").exists()
    assert (evidence_root / "run_manifest.json").exists()
    assert (evidence_root / "run_root.sha256").exists()
    games_dir = evidence_root / "games"
    game_dirs = sorted(p for p in games_dir.iterdir() if p.is_dir())
    assert len(game_dirs) == 2  # games-per-pair=2, single pair (agent-a vs agent-b)
    for game_dir in game_dirs:
        for filename in ("public_projection_trajectory.jsonl.gz", "trajectory_manifest.json", "runtime_digest.txt",
                          "independent_digest.txt", "hashes.json", "game_metadata.json"):
            assert (game_dir / filename).exists(), f"missing {filename} in {game_dir}"

    summary = json.loads((evidence_root / "league" / "league_summary.json").read_text(encoding="utf-8"))
    assert summary["schema_version"] == "o6-team-league-summary-v3"
    assert summary["digest_basis"] == "independently_verified"
    assert summary["independent_verification"]["digest_mismatches"] == 0
    assert summary["independent_verification"]["independently_verified_count"] == 2
    assert summary["league_run_id"] == "o6-team-league-test-hash-public-v2"

    registry = json.loads(trusted_root_registry.read_text(encoding="utf-8"))
    entry = next(e for e in registry["trusted_roots"] if e["run_id"] == summary["league_run_id"])
    assert entry["status"] == "TRUSTED"
    assert entry["run_root_sha256"] == (evidence_root / "run_root.sha256").read_text(encoding="utf-8").strip()


def test_digest_mismatch_aborts_without_writing_final_summary(tmp_path, monkeypatch):
    trusted_root_registry = tmp_path / "trusted_roots.json"
    monkeypatch.setattr(run_o6_team_league, "TRUSTED_ROOT_REGISTRY_PATH", trusted_root_registry)

    def fake_play_game(*, deck_a, deck_b, call_a, call_b, max_steps):
        fake_play_game.calls += 1
        steps = _canonical_steps(fake_play_game.calls)
        trajectory = _valid_trajectory(steps)
        if fake_play_game.calls == 2:
            trajectory = {**trajectory, "complete_trajectory_digest": "deliberately-wrong-digest"}
        return {"status": "DONE", "winner": 0, "elapsed_seconds": 0.01, "agent_status": ["DONE", "DONE"], "steps": len(steps),
                "trajectory": trajectory, "engine_seed_support": "ENGINE_SEED_UNSUPPORTED", "canonical_steps": steps}
    fake_play_game.calls = 0

    _install_fakes(monkeypatch, play_game_impl=fake_play_game)
    with pytest.raises(SystemExit):
        run_o6_team_league.main(_base_args(tmp_path))

    league_summary_path = tmp_path / "evidence" / "league" / "league_summary.json"
    assert not league_summary_path.exists()
    assert not trusted_root_registry.exists()
