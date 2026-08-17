from __future__ import annotations

import json
from pathlib import Path

import pytest

from mage_ptcg.meta_specialist.outcome_only_policy_fixed_bridge_v1 import (
    OutcomeOnlyPolicyFixedBridgeError,
    _bridge_sha,
    build_policy_fixed_short_bridge_v1,
    verify_policy_fixed_short_bridge_v1,
)
from scripts.build_outcome_only_policy_fixed_bridge_v1 import main as build_bridge_cli


REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEDULE = (
    REPO_ROOT
    / "runs/final-sprint-autonomous/v4-seed1-outcome-only-hard-negative-schedule-v1-20260813/schedule.json"
)
ROOT_DECK = REPO_ROOT / "deck.csv"


def test_bridge_module_import_is_missing_until_implementation() -> None:
    # This assertion intentionally exercises the public contract once the
    # module is present; the first RED run fails at collection with ImportError.
    assert SCHEDULE.is_file()


def test_policy_fixed_bridge_reloads_schedule_and_excludes_heldout() -> None:
    artifact = build_policy_fixed_short_bridge_v1(
        repo_root=REPO_ROOT,
        schedule_path=SCHEDULE,
        subject_deck_path=ROOT_DECK,
        candidate_id="play-minus",
        action_deltas={"PLAY": -2.0},
    )
    manifest = artifact["manifest"]
    assert manifest["schema_version"] == "meta-specialist-outcome-only-policy-fixed-short-bridge-v1"
    assert manifest["phase"] == "POLICY_FIXED_SHORT"
    assert manifest["ready_for_evaluation"] is True
    assert manifest["execution_allowed"] is False
    assert manifest["research_only"] is True
    assert manifest["authority"] == {
        "training_authority": False,
        "promotion_authority": False,
        "submission_authority": False,
        "external_execution_authority": False,
        "longrun_authority": False,
    }
    assert manifest["schedule_summary"]["heldout_exposure"] == 0
    assert manifest["schedule_summary"]["slot_count"] == 96
    heldout = set(manifest["heldout_ids"])
    assert heldout == {
        "aristophanivan_multiply",
        "dashimaki360_crustlecounter",
        "lucifer19_battlecore",
        "plamen06_steel",
    }
    assert len(artifact["control_games"]) == len(artifact["candidate_games"]) == 96
    control_keys = [game.metadata["stratum_key"] for game in artifact["control_games"]]
    candidate_keys = [game.metadata["stratum_key"] for game in artifact["candidate_games"]]
    assert control_keys == candidate_keys
    assert {game.opponent_id for game in artifact["candidate_games"]} == (
        set(manifest["train_ids"]) - set(manifest["zero_quota_ids"])
    )
    assert {game.seat for game in artifact["candidate_games"]} == {0, 1}
    assert all(game.metadata["heldout_exposure"] == 0 for game in artifact["candidate_games"])
    assert all(game.metadata["schedule_sha256"] == manifest["schedule_sha256"] for game in artifact["candidate_games"])
    assert all(game.metadata["synthetic_opponent"] is False for game in artifact["candidate_games"])
    assert artifact["control_games"][0].policy_sha256 != artifact["candidate_games"][0].policy_sha256


def test_bridge_verifier_rejects_manifest_mutation() -> None:
    artifact = build_policy_fixed_short_bridge_v1(
        repo_root=REPO_ROOT,
        schedule_path=SCHEDULE,
        subject_deck_path=ROOT_DECK,
        candidate_id="play-minus",
        action_deltas={"PLAY": -2.0},
    )
    manifest = dict(artifact["manifest"])
    manifest["heldout_ids"] = []
    with pytest.raises(OutcomeOnlyPolicyFixedBridgeError, match="bridge semantic SHA"):
        verify_policy_fixed_short_bridge_v1(manifest, repo_root=REPO_ROOT)


def test_bridge_rejects_unbounded_or_unknown_delta() -> None:
    with pytest.raises(OutcomeOnlyPolicyFixedBridgeError, match="action"):
        build_policy_fixed_short_bridge_v1(
            repo_root=REPO_ROOT,
            schedule_path=SCHEDULE,
            subject_deck_path=ROOT_DECK,
            candidate_id="bad",
            action_deltas={"UNKNOWN": 1.0},
        )
    with pytest.raises(OutcomeOnlyPolicyFixedBridgeError, match="bounded"):
        build_policy_fixed_short_bridge_v1(
            repo_root=REPO_ROOT,
            schedule_path=SCHEDULE,
            subject_deck_path=ROOT_DECK,
            candidate_id="bad",
            action_deltas={"PLAY": 201.0},
        )


def test_bridge_manifest_roundtrip_json(tmp_path: Path) -> None:
    artifact = build_policy_fixed_short_bridge_v1(
        repo_root=REPO_ROOT,
        schedule_path=SCHEDULE,
        subject_deck_path=ROOT_DECK,
        candidate_id="play-minus",
        action_deltas={"PLAY": -2.0},
    )
    path = tmp_path / "bridge.json"
    path.write_text(json.dumps(artifact["manifest"], sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert verify_policy_fixed_short_bridge_v1(loaded, repo_root=REPO_ROOT)["bridge_sha256"] == artifact["manifest"]["bridge_sha256"]


@pytest.mark.parametrize(
    ("field", "mutate"),
    [
        ("deck_sha256", lambda value: "0" * 64),
        ("pool_manifest_sha256", lambda value: "1" * 64),
        ("candidate_policy_sha256", lambda value: "2" * 64),
        ("train_ids", lambda value: list(value[:-1]) + ["forged-opponent"]),
    ],
)
def test_bridge_verifier_rederives_bound_identities(field: str, mutate) -> None:
    artifact = build_policy_fixed_short_bridge_v1(
        repo_root=REPO_ROOT,
        schedule_path=SCHEDULE,
        subject_deck_path=ROOT_DECK,
        candidate_id="play-minus",
        action_deltas={"PLAY": -2.0},
    )
    manifest = json.loads(json.dumps(artifact["manifest"]))
    manifest[field] = mutate(manifest[field])
    manifest["bridge_sha256"] = _bridge_sha(manifest)
    with pytest.raises(OutcomeOnlyPolicyFixedBridgeError):
        verify_policy_fixed_short_bridge_v1(manifest, repo_root=REPO_ROOT)


def test_bridge_verifier_rederives_control_identity() -> None:
    artifact = build_policy_fixed_short_bridge_v1(
        repo_root=REPO_ROOT,
        schedule_path=SCHEDULE,
        subject_deck_path=ROOT_DECK,
        candidate_id="play-minus",
        action_deltas={"PLAY": -2.0},
    )
    manifest = json.loads(json.dumps(artifact["manifest"]))
    manifest["control_config"]["identity"]["candidate_id"] = "forged-control"
    manifest["bridge_sha256"] = _bridge_sha(manifest)
    with pytest.raises(OutcomeOnlyPolicyFixedBridgeError):
        verify_policy_fixed_short_bridge_v1(manifest, repo_root=REPO_ROOT)


def test_bridge_cli_writes_strict_manifest_and_game_sidecar(tmp_path: Path) -> None:
    output = tmp_path / "bridge.json"
    assert build_bridge_cli(
        [
            "--repo-root",
            str(REPO_ROOT),
            "--schedule",
            str(SCHEDULE),
            "--subject-deck",
            str(ROOT_DECK),
            "--candidate-id",
            "play-minus",
            "--action-deltas-json",
            '{"PLAY":-2}',
            "--output",
            str(output),
        ]
    ) == 0
    manifest = json.loads(output.read_text(encoding="utf-8"))
    checked = verify_policy_fixed_short_bridge_v1(manifest, repo_root=REPO_ROOT)
    assert checked["execution_allowed"] is False
    games = json.loads(output.with_name("bridge.games.json").read_text(encoding="utf-8"))
    assert games["bridge_sha256"] == manifest["bridge_sha256"]
    assert len(games["candidate_games"]) == len(games["control_games"]) == 96
