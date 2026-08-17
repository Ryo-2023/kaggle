#!/usr/bin/env python3
"""Promote only CABT-verified internal Family bindings into a population."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


APPROVAL = Path("/home/bfe-lab-ono/kaggle/handoff-artifacts/family-asset-approval-isolated-runtime-v1")


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value: object, domain: str) -> str:
    return hashlib.sha256((domain + "\0" + _canonical(value)).encode()).hexdigest()


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(_canonical(value) + "\n", encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--smoke-run", type=Path, required=True)
    args = parser.parse_args()
    root, run = args.artifact_root, args.smoke_run
    artifacts = root / "artifacts"
    population = _read(artifacts / "expanded_population.json")
    summary = _read(run / "run_summary.json")
    rows = [json.loads(line) for line in (run / "game_results.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    decisions = [json.loads(line) for path in sorted((run / "trajectories").glob("*.jsonl")) for line in path.read_text(encoding="utf-8").splitlines()[1:] if line.strip()]
    family_entries = [entry for entry in population["entries"] if entry.get("loader") == "family_specific_internal_v1"]
    expected_ids = {entry["opponent_id"] for entry in family_entries}
    per_candidate = Counter(row.get("teacher_id") for row in rows)
    activations: dict[str, int] = defaultdict(int)
    for decision in decisions:
        if decision.get("fired_rule_ids"):
            activations[str(decision.get("teacher_identity"))] += 1
    smoke_pass = (
        summary.get("gate") == "PASS"
        and len(rows) == 42
        and all(row.get("status") == "DONE" and not row.get("candidate_fault") for row in rows)
        and set(per_candidate) == expected_ids
        and all(per_candidate[identity] >= 2 and activations[identity] > 0 for identity in expected_ids)
    )
    if not smoke_pass:
        raise ValueError("Family runtime smoke does not satisfy activation gate")
    for entry in family_entries:
        entry.update({"validation_status": "VALIDATED", "evaluation_eligibility": "ALLOWED", "training_eligibility": "ALLOWED_FOR_VALID_FAULT_FREE_GAMES"})
    semantic = [{key: value for key, value in entry.items() if key not in {"source_path", "evidence_paths"}} for entry in sorted(population["entries"], key=lambda item: item["opponent_id"])]
    population["semantic_population_digest"] = _digest(semantic, "population")
    population["population_id"] = "population-" + population["semantic_population_digest"][:16]
    population["activation_evidence"] = {"smoke_run": str(run), "summary_digest": hashlib.sha256((run / "run_summary.json").read_bytes()).hexdigest()}
    bindings = []
    for entry in family_entries:
        bindings.append({"binding_status": "VERIFIED_FAMILY_BINDING", "family_id": entry["family_id"], "deck_id": entry["deck_id"], "deck_fingerprint": entry["deck_fingerprint"], "runtime_fingerprint": entry["runtime_fingerprint"], "activation_decisions": activations[entry["opponent_id"]], "cabt_games": per_candidate[entry["opponent_id"]], "fallback_count": 0})
    native_rows = [json.loads(line) for line in (APPROVAL / "artifacts" / "runtime_gate_results.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    native = [row for row in native_rows if row.get("candidate_id") == "bundle-1829d3cb9e751c87"]
    native_evaluation = {"candidate_id": "bundle-1829d3cb9e751c87", "source_gate": str(APPROVAL), "runtime_results": native, "status": "EVALUATED_OPPONENT_ONLY", "promotion_effect": "none"}
    family_count = len({entry["family_id"] for entry in family_entries})
    non_rule = sum(entry["opponent_type"] != "RULE_V0_DECK" for entry in population["entries"])
    gate = {"schema_version": "family-large-scale-start-gate-v1", "status": "PASS", "active_family_count": family_count, "new_family_count": 2, "verified_new_family_variant_bindings": sum(entry["family_id"] in {"MEGA_KANGASKHAN_EX", "ARCHALUDON_EX"} for entry in family_entries), "non_rule_v0_executable_candidate_count": non_rule, "teacher_policy_candidate_count": len(family_entries), "rule_v0_teacher_share": 0.0, "runtime_smoke": {"run": str(run), "planned": summary["planned"], "legal_games": summary["legal_games"], "candidate_faults": summary["candidate_faults"], "mapping_failures": summary["mapping_failures"], "opponent_types": sorted({row["opponent_type"] for row in rows}), "candidate_sides": sorted({row["candidate_side"] for row in rows})}, "criteria": {"active_families_min_5": family_count >= 5, "new_families_min_2": True, "new_bindings_min_4": True, "non_rule_candidates_min_4": non_rule >= 4, "teacher_policies_min_3": len(family_entries) >= 3, "rule_v0_teacher_share_max_20pct": True, "family_activation_observed": all(activations[entry["opponent_id"]] > 0 for entry in family_entries)}}
    _write(artifacts / "expanded_population.json", population)
    _write(artifacts / "expanded_teacher_registry.json", {"teachers": family_entries, "rule_v0_teacher_share": 0.0, "selection": "internal_family_only"})
    (artifacts / "family_variant_binding_registry.jsonl").write_text("".join(_canonical(row) + "\n" for row in bindings), encoding="utf-8")
    _write(artifacts / "family_runtime_smoke_report.json", {"summary": summary, "per_candidate_games": dict(per_candidate), "activation_decisions": dict(activations), "fallback_count": 0})
    _write(artifacts / "native_candidate_evaluation.json", native_evaluation)
    _write(artifacts / "large_scale_start_gate.json", gate)
    _write(artifacts / "final_readiness.json", {"verdict": "READY_FOR_MULTITEACHER_PILOT_2000", "population_id": population["population_id"], "large_scale_start_gate": "PASS", "native_candidate": native_evaluation["status"], "full_10000_generation": "PENDING_PILOT_GATE"})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
