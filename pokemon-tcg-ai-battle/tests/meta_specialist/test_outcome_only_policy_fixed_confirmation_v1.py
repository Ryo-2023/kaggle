from __future__ import annotations

import json
from pathlib import Path

import pytest

from mage_ptcg.meta_specialist.outcome_only_policy_fixed_confirmation_v1 import (
    OutcomeOnlyPolicyFixedConfirmationError,
    build_policy_fixed_confirmation_v1,
    verify_policy_fixed_confirmation_v1,
)
from scripts.build_outcome_only_policy_fixed_confirmation_v1 import main as build_confirmation_cli


REPO_ROOT = Path(__file__).resolve().parents[2]
PARENT_BRIDGE = (
    REPO_ROOT
    / "runs/final-sprint-autonomous/v4-seed1-policy-fixed-short-attack-plus-120-96-retry-v1/bridge.json"
)


def test_confirmation_module_contract_is_available() -> None:
    assert PARENT_BRIDGE.is_file()


def test_confirmation_materializes_four_disjoint_blocks() -> None:
    artifact = build_policy_fixed_confirmation_v1(
        repo_root=REPO_ROOT,
        parent_bridge_path=PARENT_BRIDGE,
        block_count=4,
    )
    manifest = artifact["manifest"]
    assert manifest["schema_version"] == "meta-specialist-outcome-only-policy-fixed-confirmation-v1"
    assert manifest["phase"] == "POLICY_FIXED_CONFIRMATION"
    assert manifest["ready_for_evaluation"] is True
    assert manifest["execution_allowed"] is False
    assert manifest["heldout_exposure"] == 0
    assert manifest["block_count"] == 4
    assert manifest["block_quota"] == 96
    assert len(manifest["slots"]) == 384
    assert len(artifact["control_games"]) == len(artifact["candidate_games"]) == 384
    assert len({slot["seed"] for slot in manifest["slots"]}) == 384
    assert set(manifest["heldout_ids"]) == {
        "aristophanivan_multiply",
        "dashimaki360_crustlecounter",
        "lucifer19_battlecore",
        "plamen06_steel",
    }
    assert all(slot["split"] == "META_TRAIN" for slot in manifest["slots"])
    assert all(game.metadata["heldout_exposure"] == 0 for game in artifact["candidate_games"])
    assert [
        (game.opponent_id, game.seat, game.seed, game.metadata["repetition"])
        for game in artifact["control_games"]
    ] == [
        (game.opponent_id, game.seat, game.seed, game.metadata["repetition"])
        for game in artifact["candidate_games"]
    ]
    assert {block["seat_counts"]["0"] for block in manifest["blocks"]} == {48}
    assert {block["seat_counts"]["1"] for block in manifest["blocks"]} == {48}


def test_confirmation_verifier_rejects_parent_or_seed_mutation() -> None:
    artifact = build_policy_fixed_confirmation_v1(
        repo_root=REPO_ROOT,
        parent_bridge_path=PARENT_BRIDGE,
        block_count=4,
    )
    manifest = json.loads(json.dumps(artifact["manifest"]))
    manifest["slots"][0]["seed"] += 1
    with pytest.raises(OutcomeOnlyPolicyFixedConfirmationError, match="confirmation semantic SHA"):
        verify_policy_fixed_confirmation_v1(manifest, repo_root=REPO_ROOT)


def test_confirmation_verifier_roundtrip(tmp_path: Path) -> None:
    artifact = build_policy_fixed_confirmation_v1(
        repo_root=REPO_ROOT,
        parent_bridge_path=PARENT_BRIDGE,
        block_count=4,
    )
    path = tmp_path / "confirmation.json"
    path.write_text(json.dumps(artifact["manifest"], sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert verify_policy_fixed_confirmation_v1(loaded, repo_root=REPO_ROOT)["confirmation_sha256"] == artifact["manifest"]["confirmation_sha256"]


def test_confirmation_cli_writes_manifest_and_games(tmp_path: Path) -> None:
    output = tmp_path / "confirmation.json"
    assert build_confirmation_cli(
        [
            "--repo-root", str(REPO_ROOT),
            "--parent-bridge", str(PARENT_BRIDGE),
            "--output", str(output),
        ]
    ) == 0
    manifest = json.loads(output.read_text(encoding="utf-8"))
    verify_policy_fixed_confirmation_v1(manifest, repo_root=REPO_ROOT)
    games = json.loads(output.with_name("confirmation.games.json").read_text(encoding="utf-8"))
    assert games["confirmation_sha256"] == manifest["confirmation_sha256"]
    assert len(games["candidate_games"]) == len(games["control_games"]) == 384
