"""Tests for the O6-AUD-002 independent trajectory digest verifier.

The verifier module must not import the runtime projection/writer/digest
code or the League runner (see
test_verifier_source_does_not_import_runtime_modules) -- that is the whole
point of it being an *independent* recomputation rather than the same
function called twice.
"""
from __future__ import annotations

import ast
import gzip
import subprocess
import sys
from pathlib import Path

from mage_ptcg.opponents import independent_trajectory_verifier as verifier
from mage_ptcg.opponents.public_trajectory_evidence import persist_game_evidence
from mage_ptcg.opponents.public_trajectory_projection import build_public_trajectory_events
from mage_ptcg.opponents.trajectory import compute_trajectory_digests

REPO_ROOT = Path(__file__).resolve().parents[2]


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
    return {"current": current, "logs": [], "search_begin_input": "tok", "select": _select(), "step": 1}


def _canonical_steps():
    return [
        [{"observation": _obs(0), "action": None, "status": "ACTIVE"}, {"observation": _obs(1), "action": None, "status": "ACTIVE"}],
        [{"observation": _obs(0), "action": [0], "status": "ACTIVE"}, {"observation": _obs(1), "action": None, "status": "ACTIVE"}],
        [{"observation": _obs(0, result=0), "action": None, "status": "DONE"}, {"observation": _obs(1, result=0), "action": None, "status": "DONE"}],
    ]


def _events():
    return build_public_trajectory_events(_canonical_steps())


FORBIDDEN_IMPORT_MODULES = {
    "mage_ptcg.opponents.trajectory", "mage_ptcg.opponents.league_runtime",
    "mage_ptcg.opponents.public_trajectory_projection", "mage_ptcg.opponents.public_trajectory_evidence",
    "mage_ptcg.league.actual_runner", "mage_ptcg.competition_intelligence.canonical",
    "scripts.run_o6_team_league",
}


def test_verifier_source_does_not_import_runtime_modules():
    source = Path(verifier.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert imported.isdisjoint(FORBIDDEN_IMPORT_MODULES), imported & FORBIDDEN_IMPORT_MODULES


def test_independent_digest_matches_runtime_digest_for_identical_content():
    events = _events()
    runtime = compute_trajectory_digests(events)
    independent = verifier.recompute_digests(events)
    assert independent["complete_trajectory_digest"] == runtime["complete_trajectory_digest"]
    assert independent["initial_observation_digest"] == runtime["initial_observation_digest"]
    assert independent["action_trace_digest"] == runtime["action_trace_digest"]
    assert independent["terminal_observation_digest"] == runtime["terminal_observation_digest"]


def test_own_privacy_scan_rejects_opponent_hand_leak():
    event = {"public_payload": {"players": [{"hand": [{"id": 1}]}, {}], "board": {}, "result": None, "action": None}}
    result = verifier.independent_privacy_scan(event)
    assert result["status"] == "REJECTED"


def test_own_privacy_scan_passes_clean_projection():
    event = {"schema_version": "o6-public-trajectory-v1", "event_type": "INITIAL_PUBLIC_STATE", "step_index": 0,
              "seat_direction": None, "public_payload": {"players": [{"hand_count": 1}, {"hand_count": 1}], "board": {}, "result": None, "action": None}}
    assert verifier.independent_privacy_scan(event)["status"] == "PASS"


def test_schema_conformance_rejects_additional_property():
    bad = {"schema_version": "o6-public-trajectory-v1", "event_type": "INITIAL_PUBLIC_STATE", "step_index": 0,
           "seat_direction": None, "public_payload": {"players": [{}, {}], "board": {}, "result": None, "action": None},
           "unexpected_extra_key": 1}
    errors = verifier.validate_event_schema(bad)
    assert errors


def test_schema_conformance_rejects_bool_int_confusion():
    bad = {"schema_version": "o6-public-trajectory-v1", "event_type": "INITIAL_PUBLIC_STATE", "step_index": True,
           "seat_direction": None, "public_payload": {"players": [{}, {}], "board": {}, "result": None, "action": None}}
    errors = verifier.validate_event_schema(bad)
    assert errors


def test_schema_conformance_accepts_real_event():
    events = _events()
    for event in events:
        assert verifier.validate_event_schema(event) == []


def test_end_to_end_persist_then_independently_verify_matches(tmp_path):
    steps = _canonical_steps()
    events = build_public_trajectory_events(steps)
    runtime_digests = compute_trajectory_digests(events)
    persist_game_evidence(tmp_path, "g0", canonical_steps=steps, runtime_digests=runtime_digests, metadata={"game_id": "g0"})
    result = verifier.verify_game(tmp_path / "games" / "g0")
    assert result["match"] is True
    assert result["malformed"] is False
    assert result["privacy_valid"] is True
    assert result["schema_valid"] is True


def test_tampered_runtime_digest_produces_mismatch_not_silent_pass(tmp_path):
    steps = _canonical_steps()
    persist_game_evidence(
        tmp_path, "g0", canonical_steps=steps,
        runtime_digests={"complete_trajectory_digest": "not-the-real-digest", "initial_observation_digest": "x",
                          "action_trace_digest": "y", "terminal_observation_digest": "z"},
        metadata={},
    )
    result = verifier.verify_game(tmp_path / "games" / "g0")
    assert result["match"] is False


def test_missing_terminal_event_rejected(tmp_path):
    steps = _canonical_steps()
    events = build_public_trajectory_events(steps)
    runtime_digests = compute_trajectory_digests(events)
    persist_game_evidence(tmp_path, "g0", canonical_steps=steps, runtime_digests=runtime_digests, metadata={})
    path = tmp_path / "games" / "g0" / "public_projection_trajectory.jsonl.gz"
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        lines = [line for line in handle if line.strip()][:-1]
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    result = verifier.verify_game(tmp_path / "games" / "g0")
    # hashes.json now points at the original (pre-truncation) hash, so this
    # is caught as a hash mismatch (tamper) before ever reaching the
    # step-sequence check -- either way it must not silently pass.
    assert result["malformed"] is True or result["match"] is False


def test_malformed_jsonl_rejected(tmp_path):
    steps = _canonical_steps()
    events = build_public_trajectory_events(steps)
    runtime_digests = compute_trajectory_digests(events)
    persist_game_evidence(tmp_path, "g0", canonical_steps=steps, runtime_digests=runtime_digests, metadata={})
    path = tmp_path / "games" / "g0" / "public_projection_trajectory.jsonl.gz"
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        handle.write("{not valid json\n")
    result = verifier.verify_game(tmp_path / "games" / "g0")
    assert result["malformed"] is True


def test_verify_league_evidence_aggregates_across_games(tmp_path):
    for index in range(3):
        steps = _canonical_steps()
        # vary the action content per game so complete digests differ. The seat's select at raw
        # step 0 is answered by its action at raw step 1 (real engine pairing: response lands
        # one raw index after the prompt), so the varying option list belongs on step 0.
        steps[0][0]["observation"]["select"] = {"type": 0, "option": [{"type": 14, "index": 0}, {"type": 13, "attackId": index, "count": index}]}
        steps[1][0]["action"] = [1]
        events = build_public_trajectory_events(steps)
        runtime_digests = compute_trajectory_digests(events)
        persist_game_evidence(tmp_path, f"g{index}", canonical_steps=steps, runtime_digests=runtime_digests, metadata={"game_id": f"g{index}"})
    summary = verifier.verify_league_evidence(tmp_path)
    assert summary["game_count"] == 3
    assert summary["independently_verified_count"] == 3
    assert summary["digest_mismatches"] == 0
    assert summary["malformed_trajectories"] == 0
    assert summary["privacy_violations"] == 0
    assert summary["schema_violations"] == 0
    assert summary["unique_complete_trajectories"] == 3


def test_full_mode_without_anchor_returns_unanchored_evidence(tmp_path):
    (tmp_path / "games").mkdir()
    result = verifier.verify_run_chain(tmp_path)
    assert result["status"] == "UNANCHORED_EVIDENCE"


def test_full_mode_wrong_expected_root_fails(tmp_path):
    (tmp_path / "games").mkdir()
    (tmp_path / "run_manifest.json").write_text("{}", encoding="utf-8")
    (tmp_path / "run_summary.json").write_text("{}", encoding="utf-8")
    result = verifier.verify_run_chain(tmp_path, expected_root_sha256="0" * 64)
    assert result["status"] != "PASS"
    assert result["root_hash_match"] is False


def test_verifier_source_still_does_not_import_integrity_chain_module():
    source = Path(verifier.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert not any(name.endswith("league_integrity_chain") for name in imported)


def test_cli_runs_as_separate_subprocess(tmp_path):
    steps = _canonical_steps()
    events = build_public_trajectory_events(steps)
    runtime_digests = compute_trajectory_digests(events)
    persist_game_evidence(tmp_path, "g0", canonical_steps=steps, runtime_digests=runtime_digests, metadata={})
    completed = subprocess.run(
        [sys.executable, "-m", "mage_ptcg.opponents.independent_trajectory_verifier", "--evidence", str(tmp_path), "--json"],
        cwd=REPO_ROOT, capture_output=True, text=True,
        env={**__import__("os").environ, "PYTHONPATH": str(REPO_ROOT / "src"), "PYTHONDONTWRITEBYTECODE": "1"},
    )
    assert completed.returncode == 0, completed.stderr
    assert '"independently_verified_count":1' in completed.stdout.replace(" ", "")
