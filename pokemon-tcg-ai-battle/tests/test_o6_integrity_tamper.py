"""O6-AUD-002-INTEGRITY-001 closure: every layer of the evidence tree must be tamper-evident.

Builds one small synthetic run (1 game) with the real writer + integrity-chain
helpers, registers a trusted root, then mutates exactly one file/value per
test and asserts the independent verifier's full-chain mode fails. This is
the direct regression coverage for the re-audit's tamper_test_results.json
gap (manifest/hashes/summary tamper was previously invisible to the
verifier).
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


def _player():
    return {"active": [None], "asleep": False, "bench": [None] * 5, "benchMax": 5, "burned": False,
            "confused": False, "deckCount": 52, "discard": [], "hand": [{"id": 1}], "handCount": 1,
            "paralyzed": False, "poisoned": False, "prize": [{"id": 9}] * 6}


def _obs(your_index, *, result=None, attack_id=None):
    current = {"yourIndex": your_index, "players": [_player(), _player()], "energyAttached": False, "retreated": False,
               "stadium": None, "stadiumPlayed": False, "supporterPlayed": False}
    if result is not None:
        current["result"] = result
    if attack_id is not None:
        select = {"type": 0, "option": [{"type": 13, "attackId": attack_id, "count": attack_id}]}
    else:
        select = {"type": 0, "option": [{"type": 14, "index": 0}]}
    return {"current": current, "logs": [], "search_begin_input": "t", "select": select, "step": 1}


def _build_run(tmp_path: Path, *, variant: int = 0) -> tuple[Path, Path, str]:
    from mage_ptcg.competition_intelligence.canonical import sha256_hex
    from mage_ptcg.opponents.league_integrity_chain import build_run_manifest, compute_run_root_sha256, write_trusted_root_entry
    from mage_ptcg.opponents.public_trajectory_evidence import compute_checksums_file, persist_game_evidence, write_immutable_json
    from mage_ptcg.opponents.public_trajectory_projection import build_public_trajectory_events
    from mage_ptcg.opponents.trajectory import compute_trajectory_digests

    # Real engine pairing: a seat's select at raw step i is answered by that seat's action at
    # raw step i + 1, so the varying select (attack_id) belongs on step 0 to affect step 1's
    # projected action.
    canonical_steps = [
        [{"observation": _obs(0, attack_id=variant), "action": None, "status": "ACTIVE"}, {"observation": _obs(1), "action": None, "status": "ACTIVE"}],
        [{"observation": _obs(0), "action": [0], "status": "DONE"}, {"observation": _obs(1), "action": None, "status": "DONE"}],
        [{"observation": _obs(0, result=0), "action": None, "status": "DONE"}, {"observation": _obs(1, result=0), "action": None, "status": "DONE"}],
    ]
    evidence_root = tmp_path / "evidence"
    game_dir_id = "pairA__match0"
    runtime_digests = compute_trajectory_digests(build_public_trajectory_events(canonical_steps))
    persist_game_evidence(evidence_root, game_dir_id, canonical_steps=canonical_steps,
                           runtime_digests=runtime_digests, metadata={"game_id": "pairA#0"})
    game_dir = evidence_root / "games" / game_dir_id
    completed = subprocess.run(
        [sys.executable, "-m", "mage_ptcg.opponents.independent_trajectory_verifier", "--evidence", str(evidence_root), "--json", "--mode", "trajectory"],
        capture_output=True, text=True, cwd=REPO_ROOT, env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(SRC_ROOT), "PYTHONDONTWRITEBYTECODE": "1"},
    )
    trajectory_result = json.loads(completed.stdout)
    independent_digests = trajectory_result["per_game"][game_dir_id]["independent_digests"]
    (game_dir / "independent_digest.txt").write_text(json.dumps(independent_digests, sort_keys=True) + "\n", encoding="utf-8")
    hashes = json.loads((game_dir / "hashes.json").read_text(encoding="utf-8"))
    hashes["files"]["independent_digest.txt"] = sha256_hex((game_dir / "independent_digest.txt").read_bytes())
    (game_dir / "hashes.json").write_text(json.dumps(hashes, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")

    run_summary_bytes = json.dumps({"pairs": {}}, sort_keys=True).encode() + b"\n"
    (evidence_root / "run_summary.json").write_bytes(run_summary_bytes)
    run_manifest = build_run_manifest(
        run_id="tamper-test-run-v1", sorted_game_ids=[game_dir_id],
        game_manifest_hashes={game_dir_id: sha256_hex((game_dir / "trajectory_manifest.json").read_bytes())},
        summary_hash=sha256_hex(run_summary_bytes), participant_ids=["a", "b"], population_id="pop-1",
        team_bundle_hashes={}, ruleset_version="v1", cabt_version="v1", evidence_format_version="v1",
    )
    write_immutable_json(evidence_root / "run_manifest.json", run_manifest)
    compute_checksums_file(evidence_root, evidence_root / "checksums.sha256")
    root_hash = compute_run_root_sha256(evidence_root, exclude={"run_root.sha256"})
    (evidence_root / "run_root.sha256").write_text(root_hash + "\n", encoding="utf-8")

    registry = tmp_path / "trusted_roots.json"
    write_trusted_root_entry(registry, run_id="tamper-test-run-v1", run_root_sha256=root_hash, source_commit="deadbeef",
                              population_id="pop-1", evidence_schema="o6-public-trajectory-v1")
    return evidence_root, registry, game_dir_id


def _verify_full(evidence_root: Path, registry: Path) -> dict:
    completed = subprocess.run(
        [sys.executable, "-m", "mage_ptcg.opponents.independent_trajectory_verifier", "--evidence", str(evidence_root), "--json",
         "--mode", "full", "--trusted-root-registry", str(registry)],
        capture_output=True, text=True, cwd=REPO_ROOT, env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(SRC_ROOT), "PYTHONDONTWRITEBYTECODE": "1"},
    )
    return json.loads(completed.stdout)


def test_untampered_run_passes(tmp_path):
    evidence_root, registry, _ = _build_run(tmp_path)
    result = _verify_full(evidence_root, registry)
    assert result["status"] == "PASS", result


@pytest.mark.parametrize("mutate", [
    "trajectory_byte", "runtime_digest", "independent_digest", "hashes_json", "trajectory_manifest",
    "run_summary", "run_manifest", "run_root", "trusted_registry",
])
def test_single_layer_tamper_detected(tmp_path, mutate):
    evidence_root, registry, game_dir_id = _build_run(tmp_path)
    game_dir = evidence_root / "games" / game_dir_id
    if mutate == "trajectory_byte":
        path = game_dir / "public_projection_trajectory.jsonl.gz"
        data = bytearray(path.read_bytes()); data[-1] ^= 0xFF; path.write_bytes(bytes(data))
    elif mutate == "runtime_digest":
        (game_dir / "runtime_digest.txt").write_text('{"initial_observation_digest": "tampered"}\n', encoding="utf-8")
    elif mutate == "independent_digest":
        (game_dir / "independent_digest.txt").write_text('{"initial_observation_digest": "tampered"}\n', encoding="utf-8")
    elif mutate == "hashes_json":
        h = json.loads((game_dir / "hashes.json").read_text()); h["files"]["trajectory_manifest.json"] = "0" * 64
        (game_dir / "hashes.json").write_text(json.dumps(h), encoding="utf-8")
    elif mutate == "trajectory_manifest":
        m = json.loads((game_dir / "trajectory_manifest.json").read_text()); m["event_count"] = 999
        (game_dir / "trajectory_manifest.json").write_text(json.dumps(m), encoding="utf-8")
    elif mutate == "run_summary":
        (evidence_root / "run_summary.json").write_text('{"tampered": true}\n', encoding="utf-8")
    elif mutate == "run_manifest":
        rm = json.loads((evidence_root / "run_manifest.json").read_text()); rm["population_id"] = "tampered"
        (evidence_root / "run_manifest.json").write_text(json.dumps(rm), encoding="utf-8")
    elif mutate == "run_root":
        (evidence_root / "run_root.sha256").write_text("0" * 64 + "\n", encoding="utf-8")
    elif mutate == "trusted_registry":
        reg = json.loads(registry.read_text()); reg["trusted_roots"][0]["run_root_sha256"] = "0" * 64
        registry.write_text(json.dumps(reg), encoding="utf-8")

    result = _verify_full(evidence_root, registry)
    assert result["status"] != "PASS", f"tamper not detected for {mutate}: {result}"


def test_game_insertion_detected(tmp_path):
    evidence_root, registry, game_dir_id = _build_run(tmp_path)
    extra = evidence_root / "games" / "extra__match0"
    shutil.copytree(evidence_root / "games" / game_dir_id, extra)
    result = _verify_full(evidence_root, registry)
    assert result["status"] != "PASS"


def test_game_deletion_detected(tmp_path):
    evidence_root, registry, game_dir_id = _build_run(tmp_path)
    shutil.rmtree(evidence_root / "games" / game_dir_id)
    result = _verify_full(evidence_root, registry)
    assert result["status"] != "PASS"


def test_wrong_trusted_anchor_rejected(tmp_path):
    evidence_root, registry, _ = _build_run(tmp_path)
    reg = json.loads(registry.read_text())
    reg["trusted_roots"][0]["run_id"] = "different-run-id"
    registry.write_text(json.dumps(reg), encoding="utf-8")
    result = _verify_full(evidence_root, registry)
    assert result["status"] == "UNANCHORED_EVIDENCE"


def test_missing_trusted_anchor_rejected(tmp_path):
    evidence_root, registry, _ = _build_run(tmp_path)
    completed = subprocess.run(
        [sys.executable, "-m", "mage_ptcg.opponents.independent_trajectory_verifier", "--evidence", str(evidence_root), "--json", "--mode", "full"],
        capture_output=True, text=True, cwd=REPO_ROOT, env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(SRC_ROOT), "PYTHONDONTWRITEBYTECODE": "1"},
    )
    assert json.loads(completed.stdout)["status"] == "UNANCHORED_EVIDENCE"


def test_same_run_id_different_content_detected(tmp_path):
    """Two separately-built runs sharing a run_id but different game content: substituting
    the second run's games into the first run's directory must not pass as the first."""
    evidence_root_a, registry, _ = _build_run(tmp_path / "a", variant=0)
    evidence_root_b, _, _ = _build_run(tmp_path / "b", variant=1)
    shutil.rmtree(evidence_root_a / "games")
    shutil.copytree(evidence_root_b / "games", evidence_root_a / "games")
    result = _verify_full(evidence_root_a, registry)
    assert result["status"] != "PASS"
