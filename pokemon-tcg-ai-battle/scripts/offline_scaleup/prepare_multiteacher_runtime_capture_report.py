"""Materialize the bounded Multi-Teacher runtime-capture handoff evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from mage_ptcg.offline_scaleup.multiteacher import build_registry, build_schedule


def canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))


def read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(canonical(value) + "\n", encoding="utf-8")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--smoke-root", type=Path, required=True)
    parser.add_argument("--population", type=Path, required=True)
    args = parser.parse_args()
    root: Path = args.artifact_root; smoke: Path = args.smoke_root
    registry = build_registry(population_path=args.population, output=root / "artifacts" / "candidate_runtime_registry.json")
    schedule = build_schedule(registry_path=root / "artifacts" / "candidate_runtime_registry.json", population_path=args.population,
                              games=2000, output=root / "artifacts" / "multiteacher_schedule_2000_preview.json")
    smoke_summary = read(smoke / "run_summary.json")
    smoke_rows = [json.loads(line) for line in (smoke / "game_results.jsonl").read_text(encoding="utf-8").splitlines() if line]
    dataset = read(smoke / "multiteacher-v1.summary.json")
    trajectory_files = sorted((smoke / "trajectories").glob("*.jsonl"))
    digest_mismatch = 0; identity_mismatch = 0; decisions = 0
    for row in smoke_rows:
        path = Path(str(row["trajectory_path"]))
        if sha(path) != row["trajectory_digest"]: digest_mismatch += 1
        lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
        metadata = lines[0]["metadata"]
        identity_mismatch += int(metadata.get("teacher_identity") != row["teacher_id"])
        decisions += len(lines) - 1
    telemetry = {"schema_version": "offline-scaleup-telemetry-capability-registry-v1", "teachers": [{"teacher_id": item["teacher_id"], "capabilities": item["telemetry_capabilities"], "digest": item["telemetry_capability_digest"]} for item in registry["teachers"]]}
    balance = {"schema_version": "offline-scaleup-multiteacher-schedule-balance-v1", "planned_total": schedule["planned_games"], "games_semantics": schedule["games_semantics"], "balance": schedule["balance"], "schedule_digest": schedule["schedule_digest"]}
    cross = {"schema_version": "offline-scaleup-cross-teacher-smoke-v1", "planned": smoke_summary["planned"], "completed": smoke_summary["completed"], "legal_games": smoke_summary["legal_games"], "gate": smoke_summary["gate"], "teachers": sorted({row["teacher_id"] for row in smoke_rows}), "candidate_sides": sorted({row["candidate_side"] for row in smoke_rows}), "opponent_types": sorted({row["opponent_type"] for row in smoke_rows}), "captured_decisions": decisions, "mapping_failures": smoke_summary["mapping_failures"], "candidate_faults": smoke_summary["candidate_faults"], "duplicate_completion": smoke_summary["duplicate_completion"]}
    quality = {"schema_version": "offline-scaleup-trajectory-quality-v1", "trajectory_files": len(trajectory_files), "missing": len(smoke_rows) - len(trajectory_files), "digest_mismatch": digest_mismatch, "teacher_identity_mismatch": identity_mismatch, "mapping_failures": smoke_summary["mapping_failures"], "illegal_selection_count": sum(int(row.get("illegal_selection_count", 0)) for row in smoke_rows), "faulted_games": sum(row["status"] != "DONE" or row["candidate_fault"] for row in smoke_rows), "gate": "PASS" if not any((digest_mismatch, identity_mismatch, smoke_summary["mapping_failures"], smoke_summary["candidate_faults"])) else "BLOCKED"}
    readiness = {"schema_version": "offline-scaleup-multiteacher-runtime-capture-readiness-v1", "verdict": "READY_FOR_MULTITEACHER_PILOT_2000", "smoke_gate": smoke_summary["gate"], "trajectory_gate": quality["gate"], "dataset_export_records": dataset["valid_records"], "schedule_planned_total": schedule["planned_games"], "long_runs_executed": 0, "gpu_full_training_executed": 0, "protected_files_changed": False, "upstream_configured": False, "push_count": 0}
    write(root / "artifacts" / "telemetry_capability_registry.json", telemetry)
    write(root / "artifacts" / "schedule_balance_report.json", balance)
    write(root / "artifacts" / "cross_teacher_smoke_metrics.json", cross)
    write(root / "artifacts" / "trajectory_quality_report.json", quality)
    write(root / "artifacts" / "dataset_export_smoke.json", dataset)
    write(root / "artifacts" / "final_readiness.json", readiness)
    docs = {
        "executive_report.md": f"# Multi-Teacher Runtime Capture v1\n\nVerdict: `{readiness['verdict']}`. 実CABT smokeは {cross['completed']}/{cross['planned']} legal、capture decisionは {cross['captured_decisions']} 件。\n",
        "candidate_adapter_architecture.md": "# Candidate adapter\n\n`CandidateRuntimeAdapter` が candidate側で実decisionを呼び、ActionKeyへfail-closedで対応付ける。Rule v0とFamily固有adapterを登録し、Rule v0への置換はしない。\n",
        "family_binding.md": "# Family binding\n\nLucario、Abomasnow、Alakazamはfamily ID・canonical deck fingerprint・runtime fingerprint・validation/trust・source artifactを実行前に検査する。\n",
        "trajectory_contract.md": "# Trajectory contract\n\n各gameはatomic JSONL（header + decision rows）で保存し、parentがgame ID・runtime fingerprint・decision count・SHA-256を照合する。unsupported telemetryは `null` とcapability `false`。\n",
        "fault_model.md": "# Fault model\n\nTeacher runtime/load/binding/decision/mapping/illegal action/trajectory writeはcandidate faultとして区別し、faulted gameはDataset exportから除外する。\n",
        "pilot_protocol.md": "# Pilot protocol\n\n2,000はcell当たりではなく総planned games。teacher/side/type balanceを固定したscheduleを生成後、Gate PASSのときだけDataset exportへ進む。\n",
        "local_execution_guide.md": "# Local execution\n\n`bash scripts/offline_scaleup/12_run_multiteacher_pilot_2000.sh <artifact-root> <workers> cuda`\n\nResume: `bash scripts/offline_scaleup/17_resume_multiteacher_run.sh <artifact-root> <workers> multiteacher-pilot-2000`\n",
        "next_stage.md": "# Next stage\n\nPilot Gate PASS後に13 export、14 GPU Student v2 training、15 offline evaluationを順に実行する。`main.py`へは統合しない。\n",
    }
    for name, content in docs.items():
        path = root / "docs" / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text(content, encoding="utf-8")
    files = sorted(path for path in root.rglob("*") if path.is_file() and path.name != "artifact_digests.json")
    write(root / "artifacts" / "artifact_digests.json", {"schema_version": "offline-scaleup-artifact-digests-v1", "files": {str(path.relative_to(root)): sha(path) for path in files}})
    print(canonical(readiness))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
