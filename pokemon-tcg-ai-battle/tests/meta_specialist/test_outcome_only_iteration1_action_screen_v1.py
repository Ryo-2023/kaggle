from __future__ import annotations

import json
from pathlib import Path

import pytest

from mage_ptcg.meta_specialist.outcome_only_iteration1_action_screen_v1 import (
    OutcomeOnlyIteration1ActionScreenError,
    build_outcome_only_iteration1_action_screen_v1,
    verify_outcome_only_iteration1_action_screen_v1,
)
from scripts.build_outcome_only_iteration1_action_screen_v1 import main as build_action_screen_cli
from scripts.run_outcome_only_iteration1_action_screen_v1 import _games as load_action_screen_games


REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEDULE = REPO_ROOT / "runs/final-sprint-autonomous/v4-seed1-outcome-only-hard-negative-iteration1-20260813/schedule.json"


def test_action_screen_module_contract_is_available() -> None:
    assert SCHEDULE.is_file()


def test_action_screen_materializes_bounded_non_attack_candidate() -> None:
    artifact = build_outcome_only_iteration1_action_screen_v1(
        repo_root=REPO_ROOT,
        schedule_path=SCHEDULE,
        candidate_id="play-minus-120",
        action_deltas={"PLAY": -120.0},
    )
    manifest = artifact["manifest"]
    assert manifest["schema_version"] == "meta-specialist-outcome-only-iteration1-action-screen-v1"
    assert manifest["phase"] == "ACTION_SCREEN_96"
    assert manifest["candidate_id"] == "play-minus-120"
    assert manifest["action_deltas"] == {"PLAY": -120.0}
    assert manifest["ready_for_evaluation"] is True
    assert manifest["execution_allowed"] is False
    assert manifest["summary"]["slot_count"] == 96
    assert manifest["summary"]["heldout_exposure"] == 0
    assert len(artifact["control_games"]) == len(artifact["candidate_games"]) == 96
    assert [
        (game.opponent_id, game.seat, game.seed, game.metadata["repetition"])
        for game in artifact["control_games"]
    ] == [
        (game.opponent_id, game.seat, game.seed, game.metadata["repetition"])
        for game in artifact["candidate_games"]
    ]
    assert all(game.metadata["synthetic_opponent"] is False for game in artifact["candidate_games"])
    assert all(game.metadata["heldout_exposure"] == 0 for game in artifact["candidate_games"])


@pytest.mark.parametrize(
    "deltas",
    [{"ATTACK": 120.0}, {"PLAY": 121.0}, {"UNKNOWN": 1.0}, {"PLAY": -120.0, "EVOLVE": 120.0, "ATTACH": 1.0}],
)
def test_action_screen_rejects_invalid_candidate_surface(deltas: dict[str, float]) -> None:
    with pytest.raises(OutcomeOnlyIteration1ActionScreenError):
        build_outcome_only_iteration1_action_screen_v1(
            repo_root=REPO_ROOT,
            schedule_path=SCHEDULE,
            candidate_id="bad",
            action_deltas=deltas,
        )


def test_action_screen_verifier_rejects_semantic_mutation() -> None:
    artifact = build_outcome_only_iteration1_action_screen_v1(
        repo_root=REPO_ROOT,
        schedule_path=SCHEDULE,
        candidate_id="play-minus-120",
        action_deltas={"PLAY": -120.0},
    )
    manifest = json.loads(json.dumps(artifact["manifest"]))
    manifest["train_ids"] = []
    with pytest.raises(OutcomeOnlyIteration1ActionScreenError, match="semantic SHA"):
        verify_outcome_only_iteration1_action_screen_v1(manifest, repo_root=REPO_ROOT)


def test_action_screen_verifier_roundtrip() -> None:
    artifact = build_outcome_only_iteration1_action_screen_v1(
        repo_root=REPO_ROOT,
        schedule_path=SCHEDULE,
        candidate_id="evolve-plus-120",
        action_deltas={"EVOLVE": 120.0},
    )
    loaded = json.loads(json.dumps(artifact["manifest"]))
    assert verify_outcome_only_iteration1_action_screen_v1(loaded, repo_root=REPO_ROOT)["screen_sha256"] == artifact["manifest"]["screen_sha256"]


def test_action_screen_cli_writes_strict_sidecar(tmp_path: Path) -> None:
    output = tmp_path / "screen.json"
    assert build_action_screen_cli(
        [
            "--repo-root", str(REPO_ROOT),
            "--schedule", str(SCHEDULE),
            "--candidate-id", "play-minus-120",
            "--action-deltas-json", '{"PLAY":-120}',
            "--output", str(output),
        ]
    ) == 0
    manifest = json.loads(output.read_text(encoding="utf-8"))
    verify_outcome_only_iteration1_action_screen_v1(manifest, repo_root=REPO_ROOT)
    sidecar = json.loads(output.with_name("screen.games.json").read_text(encoding="utf-8"))
    assert sidecar["screen_sha256"] == manifest["screen_sha256"]
    assert sidecar["execution_allowed"] is False
    assert len(sidecar["candidate_games"]) == len(sidecar["control_games"]) == 96
    control, candidate = load_action_screen_games(manifest, output.with_name("screen.games.json"))
    assert len(control) == len(candidate) == 96
