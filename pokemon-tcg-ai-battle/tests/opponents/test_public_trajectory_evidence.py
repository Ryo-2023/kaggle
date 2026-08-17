from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from mage_ptcg.opponents.public_trajectory_evidence import (
    ImmutableEvidenceConflict,
    compute_checksums_file,
    persist_game_evidence,
)
from mage_ptcg.opponents.public_trajectory_projection import PublicSchemaUnknownFieldError


def _canonical_steps():
    def player():
        return {"active": [None] * 1, "asleep": False, "bench": [None] * 5, "benchMax": 5, "burned": False,
                "confused": False, "deckCount": 52, "discard": [], "hand": [{"id": 1}], "handCount": 1,
                "paralyzed": False, "poisoned": False, "prize": [{"id": 9}] * 6}

    def obs(your_index, select=None, result=None):
        current = {"yourIndex": your_index, "players": [player(), player()], "energyAttached": False,
                   "retreated": False, "stadium": None, "stadiumPlayed": False, "supporterPlayed": False}
        if result is not None:
            current["result"] = result
        return {"current": current, "logs": [], "search_begin_input": "tok", "select": select, "step": 1}

    select = {"type": 0, "option": [{"type": 14, "index": 0}]}
    return [
        [{"observation": obs(0, select=select), "action": None, "status": "ACTIVE"},
         {"observation": obs(1, select=select), "action": None, "status": "ACTIVE"}],
        [{"observation": obs(0, select=select), "action": [0], "status": "ACTIVE"},
         {"observation": obs(1, select=select), "action": None, "status": "ACTIVE"}],
        [{"observation": obs(0, select=select, result=0), "action": None, "status": "DONE"},
         {"observation": obs(1, select=select, result=0), "action": None, "status": "DONE"}],
    ]


def _digests():
    return {"initial_observation_digest": "a", "action_trace_digest": "b", "terminal_observation_digest": "c", "complete_trajectory_digest": "d"}


def test_persist_writes_expected_files(tmp_path: Path):
    persist_game_evidence(tmp_path, "pair__match0", canonical_steps=_canonical_steps(), runtime_digests=_digests(), metadata={"game_id": "pair#0"})
    game_dir = tmp_path / "games" / "pair__match0"
    assert (game_dir / "public_projection_trajectory.jsonl.gz").exists()
    assert (game_dir / "trajectory_manifest.json").exists()
    assert (game_dir / "runtime_digest.txt").exists()
    assert (game_dir / "game_metadata.json").exists()
    with gzip.open(game_dir / "public_projection_trajectory.jsonl.gz", "rt") as handle:
        lines = [json.loads(line) for line in handle if line.strip()]
    assert len(lines) == 3
    assert lines[0]["schema_version"] == "o6-public-trajectory-v1"
    blob = json.dumps(lines)
    assert '"id": 1' not in blob and "tok" not in blob and '"logs"' not in blob


def test_privacy_violation_still_blocks_persist_as_defense_in_depth(tmp_path: Path, monkeypatch):
    from mage_ptcg.opponents import privacy_gate
    monkeypatch.setattr(privacy_gate, "scan_public_only", lambda value: {"schema_version": "x", "status": "REJECTED", "violation": {"path": "$", "reason": "forced"}})
    with pytest.raises(privacy_gate.PrivacyViolation):
        persist_game_evidence(tmp_path, "pair__match0", canonical_steps=_canonical_steps(), runtime_digests=_digests(), metadata={"game_id": "pair#0"})
    assert not (tmp_path / "games" / "pair__match0" / "public_projection_trajectory.jsonl.gz").exists()


def test_unknown_field_in_canonical_steps_blocks_persist(tmp_path: Path):
    steps = _canonical_steps()
    steps[0][0]["observation"]["current"]["players"][0]["a_brand_new_key"] = 1
    with pytest.raises(PublicSchemaUnknownFieldError):
        persist_game_evidence(tmp_path, "pair__match0", canonical_steps=steps, runtime_digests=_digests(), metadata={"game_id": "pair#0"})
    assert not (tmp_path / "games" / "pair__match0").exists() or not list((tmp_path / "games" / "pair__match0").iterdir())


def test_immutable_write_idempotent_then_rejects_tamper(tmp_path: Path):
    persist_game_evidence(tmp_path, "pair__match0", canonical_steps=_canonical_steps(), runtime_digests=_digests(), metadata={"game_id": "pair#0"})
    persist_game_evidence(tmp_path, "pair__match0", canonical_steps=_canonical_steps(), runtime_digests=_digests(), metadata={"game_id": "pair#0"})
    with pytest.raises(ImmutableEvidenceConflict):
        persist_game_evidence(tmp_path, "pair__match0", canonical_steps=_canonical_steps(), runtime_digests=_digests(), metadata={"game_id": "pair#0-different"})


def test_checksums_file_covers_all_files_and_is_sha256sum_verifiable(tmp_path: Path):
    persist_game_evidence(tmp_path, "pair__match0", canonical_steps=_canonical_steps(), runtime_digests=_digests(), metadata={"game_id": "pair#0"})
    compute_checksums_file(tmp_path, tmp_path / "checksums.sha256")
    import subprocess
    result = subprocess.run(["sha256sum", "-c", "checksums.sha256"], cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
