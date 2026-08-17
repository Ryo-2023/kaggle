from __future__ import annotations

import json
from pathlib import Path

import pytest

from mage_ptcg.meta_specialist.outcome_only_iteration1_schedule_v1 import (
    OutcomeOnlyIteration1ScheduleError,
    build_outcome_only_iteration1_schedule_v1,
    verify_outcome_only_iteration1_schedule_v1,
)
from scripts.build_outcome_only_iteration1_schedule_v1 import main as build_schedule_cli


REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIRMATION = (
    REPO_ROOT
    / "runs/final-sprint-autonomous/v4-seed1-policy-fixed-short-attack-plus-120-confirmation-384-v1-20260813/confirmation.json"
)
LEDGER = (
    REPO_ROOT
    / "runs/final-sprint-autonomous/v4-seed1-policy-fixed-short-attack-plus-120-confirmation-384-20260813/evaluation/ledger.jsonl"
)


def test_iteration1_schedule_contract_is_available() -> None:
    assert CONFIRMATION.is_file()
    assert LEDGER.is_file()


def test_iteration1_materializes_candidate_wdl_only_schedule() -> None:
    artifact = build_outcome_only_iteration1_schedule_v1(
        repo_root=REPO_ROOT,
        candidate_ledger_path=LEDGER,
        confirmation_path=CONFIRMATION,
        quota=96,
    )
    manifest = artifact["manifest"]
    assert manifest["schema_version"] == "meta-specialist-outcome-only-hard-negative-iteration-v1"
    assert manifest["iteration"] == 1
    assert manifest["ready_for_evaluation"] is True
    assert manifest["authority"] == {
        "training_authority": False,
        "promotion_authority": False,
        "submission_authority": False,
        "external_execution_authority": False,
        "longrun_authority": False,
    }
    assert manifest["summary"]["source_games"] == 384
    assert manifest["summary"]["candidate_rows"] == 384
    assert manifest["summary"]["heldout_exposure"] == 0
    assert manifest["summary"]["action_trace_used"] is False
    assert manifest["summary"]["private_fields_used"] is False
    assert manifest["summary"]["teacher_labels_used"] is False
    assert manifest["summary"]["training_data"] is False
    assert manifest["summary"]["quota_sum"] == 96
    assert len(manifest["entries"]) == 20
    assert set(manifest["heldout_ids"]) == {
        "aristophanivan_multiply",
        "dashimaki360_crustlecounter",
        "lucifer19_battlecore",
        "plamen06_steel",
    }
    assert manifest["source_projection_fields"] == [
        "game_id", "opponent_id", "opponent_identity", "outcome", "seat", "seed",
    ]
    assert manifest["source_projection_forbidden_fields"] == []


def test_iteration1_verifier_rejects_semantic_and_heldout_mutation() -> None:
    artifact = build_outcome_only_iteration1_schedule_v1(
        repo_root=REPO_ROOT,
        candidate_ledger_path=LEDGER,
        confirmation_path=CONFIRMATION,
        quota=96,
    )
    manifest = json.loads(json.dumps(artifact["manifest"]))
    manifest["heldout_ids"] = []
    with pytest.raises(OutcomeOnlyIteration1ScheduleError, match="semantic SHA"):
        verify_outcome_only_iteration1_schedule_v1(manifest, repo_root=REPO_ROOT)


def test_iteration1_verifier_rejects_control_only_or_fault_rows(tmp_path: Path) -> None:
    rows = [json.loads(line) for line in LEDGER.read_text(encoding="utf-8").splitlines()]
    rows[0]["metadata"]["arm"] = "control"
    bad_ledger = tmp_path / "ledger.jsonl"
    bad_ledger.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")
    with pytest.raises(OutcomeOnlyIteration1ScheduleError, match="candidate"):
        build_outcome_only_iteration1_schedule_v1(
            repo_root=REPO_ROOT,
            candidate_ledger_path=bad_ledger,
            confirmation_path=CONFIRMATION,
            quota=96,
        )


def test_iteration1_cli_writes_strict_schedule(tmp_path: Path) -> None:
    output = tmp_path / "schedule.json"
    assert build_schedule_cli(
        [
            "--repo-root", str(REPO_ROOT),
            "--candidate-ledger", str(LEDGER),
            "--confirmation", str(CONFIRMATION),
            "--output", str(output),
        ]
    ) == 0
    manifest = json.loads(output.read_text(encoding="utf-8"))
    assert verify_outcome_only_iteration1_schedule_v1(manifest, repo_root=REPO_ROOT)["schedule_sha256"] == manifest["schedule_sha256"]
