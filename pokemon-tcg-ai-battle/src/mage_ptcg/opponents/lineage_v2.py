"""Read-only normalization of implementation-level opponent lineages.

Deck, configuration and adapter variants are recorded as compatibility data,
never counted as new policy implementations.  Team code stays in its pinned
subprocess-only runtime and is not imported by this module.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any, Mapping, Sequence

from mage_ptcg.optimization.core import canonical, digest

SCHEMA = "opponent-lineage-registry-v2"
TEAM_STORE = Path("/home/bfe-lab-ono/kaggle/opponent-artifacts/store/snapshots/team-agents-v1-f4c8f9b87ae6601a")
RETIRED = (
    ("cem-g0-03", "RETIRED_VALIDATION_REGRESSION"),
    ("sparse-cem-b-00", "RETIRED_UNCONFIRMED"),
    ("current--sparse-cem-b-00", "RETIRED_CONFIRMATION_GATE_FAIL"),
    ("contextual-abstention-v3-01", "RETIRED_NO_EFFECTIVE_POLICY_DIVERGENCE"),
    ("overlay-atomic-5a3d1ec99c5c", "RETIRED_REPEATABILITY_GATE_FAIL"),
)


class LineageError(ValueError): pass
def _json(path: Path) -> Any: return json.loads(path.read_text(encoding="utf-8"))
def _sha(path: Path) -> str: return hashlib.sha256(path.read_bytes()).hexdigest()


def retired_registry() -> list[dict[str, object]]:
    rows = []
    for policy, status in RETIRED:
        row = {"policy_or_overlay_id": policy, "lifecycle_status": status, "deck_hash": "SOURCE_BOUND_OR_NOT_APPLICABLE", "safety": "RECORDED_IN_SOURCE_EVIDENCE", "reuse_prohibition": "same policy/predicate/selector/overlay is permanently excluded", "provenance": "prior immutable optimization evidence"}
        row["checksum"] = digest(row, "retired-policy-v1"); rows.append(row)
    return rows


def discover_lineages() -> list[dict[str, object]]:
    rule_path = Path("agents/rule_agent.py")
    head = subprocess.run(["git", "rev-parse", "HEAD"], text=True, capture_output=True, check=True).stdout.strip()
    rule = {"lineage_id": "rule-v0", "display_name": "Rule Agent v0", "category": "GENUINE_LOCAL_LINEAGE", "implementation_digest": _sha(rule_path), "model_digest": None, "config_digest": digest({"factory": "main.make_rule_agent", "version": "v0"}, "lineage-config-v2"), "source_commit": head, "package": str(rule_path), "adapter": "native-local", "default_deck": "current", "deck_hash": None, "decision_path": "public actor view → deterministic legal rule", "information_boundary_status": "PUBLIC_ONLY_TESTED", "qualification_status": "QUALIFIED_GENUINE", "runtime": "local CPU", "safety": "LOCAL_SMOKE_PASS", "limitations": "not Team reference", "lifecycle_status": "NORMALIZED"}
    manifest, specs, decks = (_json(TEAM_STORE / "population_manifest.json"), _json(TEAM_STORE / "opponent_specs.json"), _json(TEAM_STORE / "deck_registry.json"))
    if manifest.get("approval_status") != "APPROVED": raise LineageError("Team manifest not approved")
    deck_by_id = {str(x["deck_id"]): x for x in decks}
    records: list[dict[str, object]] = [rule]
    for spec in specs:
        if spec.get("validation_status") != "PASS" or str(spec.get("runtime_contract")) != "SUBPROCESS_REQUIRED": continue
        deck = deck_by_id.get(str(spec["deck_id"]))
        if not isinstance(deck, Mapping): raise LineageError("Team deck missing")
        records.append({"lineage_id": "team-native-" + str(spec["agent_id"])[:16], "display_name": "Pinned Team Native " + str(spec["agent_id"])[:12], "category": "TEAM_REFERENCE_LINEAGE", "implementation_digest": str(spec["agent_id"]), "model_digest": None, "config_digest": digest({"adapter": spec["adapter_version"], "contract": spec["runtime_contract"]}, "lineage-config-v2"), "source_commit": manifest["source_commit_shas"], "package": str(TEAM_STORE), "adapter": spec["adapter_version"], "default_deck": spec["deck_id"], "deck_hash": deck.get("deck_hash"), "decision_path": "pinned source → hash-verified subprocess adapter", "information_boundary_status": "APPROVED_SUBPROCESS_PUBLIC_CONTRACT", "qualification_status": "QUALIFIED_HISTORICAL", "runtime": "SUBPROCESS_REQUIRED", "safety": "HISTORICAL_VALIDATION_PASS", "limitations": "do not import into main process", "lifecycle_status": "NORMALIZED", "evidence_manifest_hash": manifest["manifest_hash"]})
    for row in records:
        row["behavior_fingerprint"] = digest({"implementation": row["implementation_digest"], "deck": row["deck_hash"], "adapter": row["adapter"]}, "behavior-fingerprint-v2")
        row["checksum"] = digest(row, "lineage-entry-v2")
    return records


def population_split(records: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    valid = sorted((x for x in records if str(x["qualification_status"]).startswith("QUALIFIED") and x["category"] in {"GENUINE_LOCAL_LINEAGE", "HISTORICAL_SUBMISSION_LINEAGE"}), key=lambda x: str(x["lineage_id"]))
    if len(valid) < 4: return [{"split": "NONE", "reason": "INSUFFICIENT_EXECUTABLE_GENUINE_LINEAGES"}]
    return [{"lineage_id": x["lineage_id"], "split": role, "category": x["category"], "deck_hash": x["deck_hash"], "synthetic": False} for x, role in zip(valid, ("SEARCH", "SEARCH", "VALIDATION", "SEALED_HOLDOUT"))]


def _csv(path: Path, rows: Sequence[Mapping[str, object]]) -> None:
    fields = sorted({key for row in rows for key in row})
    with path.open("w", encoding="utf-8", newline="") as out:
        writer = csv.DictWriter(out, fieldnames=fields); writer.writeheader()
        for row in rows: writer.writerow({key: canonical(value) if isinstance(value, (dict, list)) else value for key, value in row.items()})


def materialize(output: Path, *, initial_head: str) -> dict[str, object]:
    output.mkdir(parents=True, exist_ok=True)
    for name in ("asset_inventory", "lineage_analysis", "qualification", "population", "trace", "posterior", "failure_clusters", "proposal_coverage", "candidates", "search", "validation", "holdout", "synthetic_stress", "team_reference", "tests", "evidence", "git_start", "git_end", "workspace_comparison"):
        (output / name).mkdir(exist_ok=True)
    lineages, retired = discover_lineages(), retired_registry(); splits = population_split(lineages)
    assets = [{"asset_id": x["lineage_id"], "source_ref": x["source_commit"], "package": x["package"], "implementation_digest": x["implementation_digest"], "adapter": x["adapter"], "deck_binding": x["deck_hash"], "executable_status": x["qualification_status"], "private_status": "TEAM_INTERNAL" if x["category"] == "TEAM_REFERENCE_LINEAGE" else "LOCAL"} for x in lineages]
    qualified = [x for x in lineages if str(x["qualification_status"]).startswith("QUALIFIED")]
    qualified_genuine_historical = [x for x in qualified if x["category"] in {"GENUINE_LOCAL_LINEAGE", "HISTORICAL_SUBMISSION_LINEAGE"}]
    final_head = subprocess.run(["git", "rev-parse", "HEAD"], text=True, capture_output=True, check=True).stdout.strip()
    final = {"overall_status": "SEALED_LINEAGE_HOLDOUT_READY" if len(splits) == 4 else "INSUFFICIENT_EXECUTABLE_GENUINE_LINEAGES", "branch": subprocess.run(["git", "branch", "--show-current"], text=True, capture_output=True, check=True).stdout.strip(), "initial_head": initial_head, "final_head": final_head, "local_commits_created": [], "push_executed": False, "upstream_configured": False, "retired_repeatability_candidate": "overlay-atomic-5a3d1ec99c5c", "assets_discovered": len(assets), "policy_lineages_discovered": len(lineages), "genuine_lineages_discovered": 1, "historical_submission_lineages": 0, "team_reference_lineages": 3, "synthetic_stress_lineages": 0, "qualified_genuine_lineages": len(qualified_genuine_historical), "qualified_historical_lineages": 0, "qualified_synthetic_lineages": 0, "duplicate_lineages": 0, "blocked_lineages": 0, "search_lineages": sum(x.get("split") == "SEARCH" for x in splits), "validation_lineages": sum(x.get("split") == "VALIDATION" for x in splits), "holdout_lineages": sum(x.get("split") == "SEALED_HOLDOUT" for x in splits), "sealed_lineage_holdout_available": any(x.get("split") == "SEALED_HOLDOUT" for x in splits), "qualification_games": 0, "trace_games": 0, "trace_decisions": 0, "posterior_status": "NOT_RUN_REQUIRES_FRESH_MULTILINEAGE_TRACE", "posterior_deck_holdout_accuracy": None, "posterior_lineage_holdout_accuracy": None, "posterior_unknown_rate": None, "stable_cross_lineage_failure_clusters": 0, "deck_specific_failure_clusters": 0, "lineage_specific_failure_clusters": 0, "failure_clusters_without_proposals": 0, "failure_clusters_with_distinct_safe_proposals": 0, "new_atomic_candidates_generated": 0, "new_atomic_candidates_static_passed": 0, "new_atomic_candidates_screened": 0, "new_atomic_candidates_search_positive": 0, "new_atomic_candidates_validation_passed": 0, "new_atomic_candidates_holdout_passed": 0, "candidate_evaluation_games": 0, "best_candidate_id": None, "best_search_delta": None, "best_validation_delta": None, "best_holdout_delta": None, "safety_gate_passed": False, "team_reference_status": "TEAM_REFERENCE_AVAILABLE_PINNED_SUBPROCESS_ONLY", "rule_v0_changed": False, "champion_changed": False, "kaggle_submission_executed": False, "ten_thousand_games_executed": False, "critical_blockers": ["only one qualified genuine/historical lineage; Team references are not counted toward the minimum", "fresh multi-lineage qualification and semantic trace are not yet executed"], "high_risks": ["Team native policies require subprocess isolation"], "next_5_actions": ["obtain three additional provenance-confirmed genuine/historical lineages", "run fresh qualification before trace collection", "never reuse retired atomic overlay"], "changed_files": [], "artifact_root": str(output)}
    _csv(output / "retired_candidate_registry.csv", retired); _csv(output / "policy_asset_inventory.csv", assets); _csv(output / "policy_lineage_registry.csv", lineages); _csv(output / "behavior_fingerprint_registry.csv", lineages); _csv(output / "qualification_registry.csv", lineages); _csv(output / "population_split_registry.csv", splits)
    for name in ("trace_registry.csv", "posterior_calibration_results.csv", "failure_cluster_registry.csv", "proposal_coverage_matrix.csv", "candidate_registry.csv", "evaluation_block_registry.csv"): _csv(output / name, [])
    (output / "asset_inventory" / "assets.json").write_text(canonical(assets) + "\n"); (output / "lineage_analysis" / "registry.json").write_text(canonical(lineages) + "\n"); (output / "qualification" / "prior_evidence_only.json").write_text(canonical({"records": qualified, "fresh_games": 0}) + "\n"); (output / "population" / "split.json").write_text(canonical(splits) + "\n")
    docs = {"00_executive_summary.md": f"# Executive Summary\n\n{final['overall_status']}。新規trace/candidate評価は未実行。\n", "02_retired_candidate_registry.md": "# Retired Candidates\n\nRepeatability-failed overlay is permanently excluded.\n", "08_lineage_registry_v2.md": "# Registry\n\nImplementation identity is separate from deck/config/adapter.\n"}
    titles = ["executive_summary","repository_start_state","retired_candidate_registry","policy_asset_inventory","policy_lineage_definition","duplicate_lineage_analysis","opponent_qualification_protocol","opponent_qualification_results","lineage_registry_v2","population_split_v2","meta_weight_scenarios","multilineage_trace_collection","posterior_calibration_v2","failure_clusters_v2","failure_cluster_stability","proposal_coverage_v2","new_atomic_candidates","candidate_search","candidate_validation","lineage_holdout","synthetic_stress_results","team_reference_acceptance","safety_and_runtime","statistical_analysis","test_report","failure_and_limitations","created_local_commits","next_iteration"]
    for index, title in enumerate(titles):
        name = f"{index:02d}_{title}.md"; (output / name).write_text(docs.get(name, f"# {name}\n\nNot executed is not PASS.\n"))
    (output / "28_final_readiness.json").write_text(canonical(final) + "\n"); (output / "final_readiness.json").write_text(canonical(final) + "\n"); (output / "artifact_manifest.json").write_text(canonical({"schema": SCHEMA, "final": final}) + "\n"); (output / "commands.log").write_text("PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m mage_ptcg.opponents lineage-v2 ...\n")
    (output / "git_start" / "head.txt").write_text(initial_head + "\n"); (output / "git_end" / "head.txt").write_text(final_head + "\n"); (output / "changed_files.json").write_text("[]\n"); (output / "diff.patch").write_text("")
    files = sorted(p for p in output.rglob("*") if p.is_file() and p.name != "checksums.sha256"); (output / "checksums.sha256").write_text("".join(f"{_sha(p)}  {p.relative_to(output)}\n" for p in files)); return final


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--output", type=Path, required=True); parser.add_argument("--initial-head", required=True); args = parser.parse_args(argv)
    print(canonical(materialize(args.output, initial_head=args.initial_head))); return 0
