from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.build_outcome_only_hard_negative_schedule_v1 import main as build_cli
from mage_ptcg.meta_specialist.outcome_only_hard_negative_v1 import (
    OutcomeOnlyHardNegativeError,
    build_outcome_only_hard_negative_schedule_v1,
    verify_outcome_only_hard_negative_schedule_v1,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
RUN_ROOT = (
    REPO_ROOT
    / "runs/final-sprint-autonomous/v4-seed1-public-trace-meta-train-common24-96-serial-20260813-v1"
)
META_MANIFEST = REPO_ROOT / "runs/final-sprint-autonomous/meta-distribution-v1/manifest.json"
POOL_MANIFEST = REPO_ROOT / "opponents/pool_manifest.json"
SUMMARY = RUN_ROOT / "summary.json"
LEDGER = RUN_ROOT / "ledger.jsonl"


def _build(output: Path) -> dict[str, object]:
    return build_outcome_only_hard_negative_schedule_v1(
        repo_root=REPO_ROOT,
        ledger_path=LEDGER,
        summary_path=SUMMARY,
        meta_manifest_path=META_MANIFEST,
        pool_manifest_path=POOL_MANIFEST,
        output_manifest_path=output,
        quota=96,
        seed="v4-seed1-common24-96",
    )


def test_real_v4_ledger_excludes_meta_final_and_closes_schedule(tmp_path: Path) -> None:
    payload = _build(tmp_path / "schedule.json")

    assert payload["schema_version"] == "meta-specialist-outcome-only-hard-negative-v1"
    assert payload["research_only"] is True
    assert payload["authority"] == {
        "training_authority": False,
        "promotion_authority": False,
        "submission_authority": False,
        "external_execution_authority": False,
        "longrun_authority": False,
    }
    assert payload["summary"]["source_games"] == 96
    assert payload["summary"]["included_games"] == 80
    assert payload["summary"]["excluded_games"] == 16
    assert payload["summary"]["faults"] == 0
    assert payload["summary"]["action_trace_used"] is False
    assert payload["summary"]["teacher_labels_used"] is False
    assert payload["summary"]["private_fields_used"] is False
    assert len(payload["entries"]) == 20
    assert sum(int(row["quota"]) for row in payload["entries"]) == 96
    assert abs(sum(float(row["weight"]) for row in payload["entries"]) - 1.0) < 1e-9
    excluded = {row["opponent_id"] for row in payload["excluded_heldout"]}
    assert excluded == {
        "aristophanivan_multiply",
        "dashimaki360_crustlecounter",
        "lucifer19_battlecore",
        "plamen06_steel",
    }
    assert all(row["split"] == "META_TRAIN" for row in payload["entries"])
    assert all(row["teacher_behavior_allowed"] is False for row in payload["entries"])
    assert all(row["training_exposure_allowed"] is False for row in payload["entries"])


def test_schedule_is_deterministic_and_strictly_reloadable(tmp_path: Path) -> None:
    first = _build(tmp_path / "one.json")
    second = _build(tmp_path / "two.json")

    assert first == second
    assert verify_outcome_only_hard_negative_schedule_v1(
        tmp_path / "one.json", REPO_ROOT
    ) == first


def test_schedule_rejects_mutated_semantic_payload(tmp_path: Path) -> None:
    output = tmp_path / "schedule.json"
    _build(output)
    payload = json.loads(output.read_text(encoding="utf-8"))
    payload["entries"][0]["weight"] = 0.0
    output.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")

    with pytest.raises(OutcomeOnlyHardNegativeError, match="semantic SHA"):
        verify_outcome_only_hard_negative_schedule_v1(output, REPO_ROOT)


def test_schedule_binds_current_evaluator_implementation() -> None:
    from scripts.parallel_cabt_evaluator_v1 import evaluator_implementation_sha256_v1

    payload = verify_outcome_only_hard_negative_schedule_v1(
        REPO_ROOT
        / "runs/final-sprint-autonomous/v4-seed1-outcome-only-hard-negative-schedule-v1-20260813/schedule.json",
        REPO_ROOT,
    )
    assert payload["subject_identity"]["evaluator_sha256"] == evaluator_implementation_sha256_v1()


def test_source_action_or_private_row_is_rejected(tmp_path: Path) -> None:
    source_ledger = tmp_path / "ledger.jsonl"
    lines = LEDGER.read_text(encoding="utf-8").splitlines()
    row = json.loads(lines[0])
    row["selected_action"] = [0]
    lines[0] = json.dumps(row, sort_keys=True, separators=(",", ":"))
    source_ledger.write_text("\n".join(lines) + "\n", encoding="utf-8")

    source_summary = tmp_path / "summary.json"
    summary = json.loads(SUMMARY.read_text(encoding="utf-8"))
    summary["ledger_sha256"] = hashlib.sha256(source_ledger.read_bytes()).hexdigest()
    source_summary.write_text(json.dumps(summary, sort_keys=True) + "\n", encoding="utf-8")

    with pytest.raises(OutcomeOnlyHardNegativeError, match="forbidden"):
        build_outcome_only_hard_negative_schedule_v1(
            repo_root=REPO_ROOT,
            ledger_path=source_ledger,
            summary_path=source_summary,
            meta_manifest_path=META_MANIFEST,
            pool_manifest_path=POOL_MANIFEST,
            output_manifest_path=tmp_path / "rejected.json",
            quota=96,
            seed="v4-seed1-common24-96",
        )


def test_source_unknown_row_field_is_rejected(tmp_path: Path) -> None:
    source_ledger = tmp_path / "ledger.jsonl"
    lines = LEDGER.read_text(encoding="utf-8").splitlines()
    row = json.loads(lines[0])
    row["unbound_extra"] = "must-not-be-consumed"
    lines[0] = json.dumps(row, sort_keys=True, separators=(",", ":"))
    source_ledger.write_text("\n".join(lines) + "\n", encoding="utf-8")

    source_summary = tmp_path / "summary.json"
    summary = json.loads(SUMMARY.read_text(encoding="utf-8"))
    summary["ledger_sha256"] = hashlib.sha256(source_ledger.read_bytes()).hexdigest()
    source_summary.write_text(json.dumps(summary, sort_keys=True) + "\n", encoding="utf-8")

    with pytest.raises(OutcomeOnlyHardNegativeError, match="closed schema"):
        build_outcome_only_hard_negative_schedule_v1(
            repo_root=REPO_ROOT,
            ledger_path=source_ledger,
            summary_path=source_summary,
            meta_manifest_path=META_MANIFEST,
            pool_manifest_path=POOL_MANIFEST,
            output_manifest_path=tmp_path / "rejected-unknown.json",
            quota=96,
            seed="v4-seed1-common24-96",
        )


def test_cli_builds_a_new_sidecar_root(tmp_path: Path) -> None:
    output = tmp_path / "sidecar" / "schedule.json"
    assert build_cli(
        [
            "--repo-root",
            str(REPO_ROOT),
            "--ledger",
            str(LEDGER),
            "--summary",
            str(SUMMARY),
            "--meta-manifest",
            str(META_MANIFEST),
            "--pool-manifest",
            str(POOL_MANIFEST),
            "--output",
            str(output),
            "--seed",
            "v4-seed1-common24-96",
        ]
    ) == 0
    assert output.is_file()
    assert verify_outcome_only_hard_negative_schedule_v1(output, REPO_ROOT)["summary"]["quota_sum"] == 96
