"""Run paired evaluation statistics and the conservative promotion gate."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from mage_ptcg.meta_specialist.evaluation_protocol_v2 import (  # noqa: E402
    IndependentEvaluationRecordV2,
    IndependentEvaluationRecordV3,
    PairedEvaluationRecordV2,
    evaluation_inference_allowed_v2,
    independent_stratified_summary_v2,
    independent_readiness_summary_v3,
    paired_summary_from_records_v2,
    validate_evidence_attestation_v2,
)
from mage_ptcg.meta_specialist.experiment_manifest_v1 import promotion_gate_v1  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--bootstrap-seed", type=int, default=20260809)
    parser.add_argument("--bootstrap-replicates", type=int, default=20000)
    parser.add_argument("--records", type=Path, required=True,
                        help="JSON list of provenance-bearing candidate/baseline paired records")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    raw_records = json.loads(args.records.read_text(encoding="utf-8"))
    if not isinstance(raw_records, list):
        raise ValueError("--records must contain a JSON list")
    if raw_records and all("policy_role" in row for row in raw_records):
        for row in raw_records:
            validate_evidence_attestation_v2(row)
        records = [
            IndependentEvaluationRecordV3(
                lane_id=row["lane_id"], training_seed=row["training_seed"],
                policy_role=row["policy_role"], policy_artifact_sha256=row["policy_artifact_sha256"],
                theta0_sha256=row["theta0_sha256"], repetition=row["repetition"],
                outcome=row["outcome"], seat=row["seat"], opponent_family=row["opponent_family"],
                canonical_game_identity=row["canonical_game_identity"], record_hash=row["record_hash"],
                engine_seed_supported=row["engine_seed_supported"], replay_verified=row["replay_verified"],
                run_attestation=row["run_attestation"], seed_attestation=row["seed_attestation"],
                evidence_kind=row["evidence_kind"],
                fault_provenance=row.get("fault_provenance"),
            ) for row in raw_records
        ]
        summary = independent_readiness_summary_v3(
            records, bootstrap_seed=args.bootstrap_seed, bootstrap_replicates=args.bootstrap_replicates,
        )
        report = {
            "schema": "meta-specialist-evaluation-v3-independent-readiness",
            "records_path": str(args.records),
            "promotion_gate": "NOT_APPLICABLE_INDEPENDENT_ARMS",
            **summary,
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
        return 0
    if raw_records and all("arm" in row for row in raw_records):
        for row in raw_records:
            validate_evidence_attestation_v2(row)
        records = [
            IndependentEvaluationRecordV2(
                arm=row["arm"], outcome=row["outcome"], seat=row["seat"],
                opponent_family=row["opponent_family"],
                canonical_game_identity=row["canonical_game_identity"], record_hash=row["record_hash"],
                engine_seed_supported=row["engine_seed_supported"],
                replay_verified=row["replay_verified"], run_attestation=row["run_attestation"],
                seed_attestation=row["seed_attestation"], fault_provenance=row.get("fault_provenance"),
            ) for row in raw_records
        ]
        summary = independent_stratified_summary_v2(records, seed=args.seed)
        report = {
            "schema": "meta-specialist-evaluation-v2", "seed": args.seed,
            "records_path": str(args.records), "promotion_gate": "NOT_APPLICABLE_INDEPENDENT_ARMS",
            **summary,
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
        return 0
    for row in raw_records:
        validate_evidence_attestation_v2(row)
    records = [
        PairedEvaluationRecordV2(
            candidate_outcome=row["candidate_outcome"], baseline_outcome=row["baseline_outcome"],
            candidate_ledger_identity=row["candidate_ledger_identity"],
            baseline_ledger_identity=row["baseline_ledger_identity"],
            candidate_record_hash=row["candidate_record_hash"], baseline_record_hash=row["baseline_record_hash"],
            candidate_state_hash_sequence=tuple(row["candidate_state_hash_sequence"]),
            baseline_state_hash_sequence=tuple(row["baseline_state_hash_sequence"]),
            candidate_action_sequence=tuple(tuple(action) for action in row["candidate_action_sequence"]),
            baseline_action_sequence=tuple(tuple(action) for action in row["baseline_action_sequence"]),
            seat=row["seat"], opponent_family=row["opponent_family"],
        )
        for row in raw_records
    ]
    # Counterfactual paired evidence cannot be interpreted when the engine did
    # not attest deterministic seeding plus exact replay verification.
    engine_seed_supported = all(row["engine_seed_supported"] for row in raw_records)
    replay_verified = all(row["replay_verified"] for row in raw_records)
    evaluation_inference_allowed_v2(
        engine_seed_supported=engine_seed_supported, replay_verified=replay_verified,
    )
    if not all(row.get("run_attestation") and row.get("seed_attestation") for row in raw_records):
        raise ValueError("paired performance inference requires run and seed attestations")
    summary = paired_summary_from_records_v2(records, seed=args.seed)
    candidate_by_seat = {
        seat: [record.candidate_outcome == "win" for record in records if record.seat == seat]
        for seat in (0, 1)
    }
    seat_rates = [sum(values) / len(values) for values in candidate_by_seat.values() if values]
    measured_seat_delta = max(seat_rates) - min(seat_rates) if len(seat_rates) == 2 else 1.0
    summary["promotion_gate"] = promotion_gate_v1(
        paired_delta=float(summary["paired_delta"]), ci_lower=float(summary["bootstrap_ci_low"]),
        fault_rate=float(summary["candidate"]["faults"]) / len(records), seat_delta=measured_seat_delta,
        training_seed_consistency=all(row["run_attestation"] == row["seed_attestation"] for row in raw_records),
    )
    report = {
        "schema": "meta-specialist-evaluation-v2", "seed": args.seed,
        "records_path": str(args.records), **summary,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
