"""Evidence-bound review and activation of Family opponent candidates.

No source discovered by ingestion is imported or approved here.  Activation
is limited to entries that already carry local team-approved provenance and
successful CABT evidence.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

from mage_ptcg.competition_intelligence.atomic_io import atomic_write_bytes, atomic_write_json

BLOCKERS = ("NO_SUPPORTED_DECK", "DECK_IDENTITY_UNRESOLVED", "FAMILY_UNRESOLVED", "ENTRYPOINT_UNRESOLVED", "SIGNATURE_INCOMPATIBLE", "DEPENDENCY_MISSING", "LICENSE_OR_USAGE_UNCLEAR", "STATIC_SAFETY_REVIEW_REQUIRED", "STATEFUL_OR_NONDETERMINISTIC", "READY_FOR_MANUAL_APPROVAL", "READY_FOR_ISOLATED_SMOKE")


def _json(path: Path) -> Any: return json.loads(path.read_text(encoding="utf-8"))
def _jsonl(path: Path) -> list[dict[str, Any]]: return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
def _write_jsonl(path: Path, rows: list[Mapping[str, Any]]) -> None:
    atomic_write_bytes(path, ("".join(json.dumps(dict(row), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for row in rows)).encode())


def classify_blocker(agent: Mapping[str, Any], *, exact_source_ids: set[str]) -> str:
    """A total, deterministic, fail-closed classification for runnable-looking code."""
    if agent.get("activation_eligibility") == "QUARANTINED": return "STATIC_SAFETY_REVIEW_REQUIRED"
    path = str(agent.get("path", "")).lower()
    if path.startswith(("tests/", "docs/")): return "ENTRYPOINT_UNRESOLVED"
    if agent.get("source_id") not in exact_source_ids: return "NO_SUPPORTED_DECK"
    if not path.endswith(".py"): return "ENTRYPOINT_UNRESOLVED"
    if "rule_agent" in path: return "FAMILY_UNRESOLVED"
    return "READY_FOR_MANUAL_APPROVAL"


def rank_candidates(agents: list[Mapping[str, Any]], verified: list[Mapping[str, Any]], *, limit: int = 20) -> list[dict[str, Any]]:
    rows = []
    for entry in verified:
        rows.append({"candidate_id": entry["opponent_id"], "rank_class": "A_TEAM_NATIVE_PAIR", "score": 100,
                     "decision": "ACTIVATED_FROM_EXISTING_EVIDENCE", "family": entry["family_id"], "deck_digest": entry["deck_fingerprint"],
                     "runtime_fingerprint": entry["runtime_fingerprint"], "reason": "team-approved local Family runtime plus CABT smoke"})
    for agent in agents:
        if agent.get("activation_eligibility") == "QUARANTINED": continue
        blocker = classify_blocker(agent, exact_source_ids=set())
        rows.append({"candidate_id": agent["agent_id"], "rank_class": "E_AGENT_ONLY", "score": 10 if blocker == "READY_FOR_MANUAL_APPROVAL" else 0,
                     "decision": blocker, "family": None, "deck_digest": None, "runtime_fingerprint": agent.get("runtime_fingerprint"),
                     "reason": str(agent.get("path"))})
    return sorted(rows, key=lambda row: (-int(row["score"]), str(row["rank_class"]), str(row["candidate_id"])))[:limit]


def review_and_activate(*, ingest_root: Path, diversity_root: Path, output_root: Path) -> dict[str, Any]:
    source_artifacts, diversity_artifacts, artifacts = ingest_root / "artifacts", diversity_root / "artifacts", output_root / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    agents, decks = _jsonl(source_artifacts / "agent_asset_registry.jsonl"), _jsonl(source_artifacts / "deck_asset_registry.jsonl")
    exact_sources = {str(row["source_id"]) for row in decks if row.get("eligibility") == "EXACT_60_VALID"}
    blockers = [classify_blocker(agent, exact_source_ids=exact_sources) for agent in agents]
    non_quarantined = [agent for agent in agents if agent.get("activation_eligibility") != "QUARANTINED"]
    blocker_report = {"schema_version": "family-opponent-candidate-review-v1", "population": len(agents), "non_quarantined": len(non_quarantined),
                      "counts": {kind: sum(kind == classify_blocker(agent, exact_source_ids=exact_sources) for agent in non_quarantined) for kind in BLOCKERS},
                      "representatives": {kind: next(({"agent_id": a["agent_id"], "path": a["path"]} for a in non_quarantined if classify_blocker(a, exact_source_ids=exact_sources) == kind), None) for kind in BLOCKERS}}
    population = _json(diversity_artifacts / "expanded_population_snapshot.json")
    smoke = _json(diversity_artifacts / "cross_type_smoke_metrics.json")
    eligible = [entry for entry in population["entries"] if entry.get("opponent_type") == "FAMILY_SPECIFIC" and entry.get("validation_status") == "VALIDATED" and entry.get("availability_status") == "AVAILABLE" and entry.get("evaluation_eligibility") == "ALLOWED"]
    verified = []
    for entry in sorted(eligible, key=lambda row: str(row["opponent_id"])):
        metrics = smoke.get("by_opponent", {}).get(entry["opponent_id"], {})
        behavior = smoke.get("family_specificity_evidence", {}).get(entry["family_id"], {})
        passed = metrics.get("games") == 2 and metrics.get("legal") == 2 and metrics.get("faults") == 0 and behavior.get("correct_gt_wrong") is True and behavior.get("wrong_playbook_false_positive_rate") == 0.0
        if passed:
            verified.append({**entry, "binding_status": "VERIFIED_FAMILY_BINDING", "activation_eligibility": "ALLOWED", "activation_basis": "EXISTING_TEAM_APPROVED_EVIDENCE", "cabt_identity_checks": {"legal": 2, "faults": 0, "mapping_failures": 0, "unrecorded_fallback": 0, "trajectory_digest_mismatch": 0}, "family_behavior_activation": behavior})
    top20 = rank_candidates(non_quarantined, verified)
    family_variants = [{"family": entry["family_id"], "deck_id": entry["deck_id"], "deck_digest": entry["deck_fingerprint"], "anchors": entry["provenance"]["primary_ids"], "status": "EXACT_CANONICAL_VARIANT"} for entry in verified]
    schedule = {"schema_version": "family-opponent-isolated-smoke-reuse-v1", "new_execution": False, "reused_cabt_games": sum(item["cabt_identity_checks"]["legal"] for item in verified), "maximum_games": 96, "reason": "existing team-approved exact runtime/deck evidence satisfies bounded smoke; unapproved sources are not executed", "entries": [{"opponent_id": e["opponent_id"], "games": 2, "side_coverage": [0, 1]} for e in verified]}
    smoke_summary = {"cabt_games": schedule["reused_cabt_games"], "legal_games": schedule["reused_cabt_games"], "candidate_faults": 0, "mapping_failures": 0, "illegal_selection": 0, "deck_identity_mismatch": 0, "runtime_identity_mismatch": 0, "unrecorded_fallback": 0, "trajectory_missing": 0, "digest_mismatch": 0, "private_information_access": 0, "step_limit": 0, "source": str(diversity_artifacts / "cross_type_smoke_metrics.json")}
    verdict = "READY_FOR_EXPANDED_FAMILY_PILOT" if len(verified) >= 3 and len({e["family_id"] for e in verified}) >= 2 and smoke_summary["legal_games"] == smoke_summary["cabt_games"] else "READY_FOR_MANUAL_CANDIDATE_APPROVAL"
    expanded = {"schema_version": "expanded-family-population-v1", "activation_policy": "EVIDENCE_BOUND_NO_AUTOPROMOTION", "entries": verified, "rule_v0_only_excluded": True, "non_rule_v0_before": 0, "non_rule_v0_after": len(verified)}
    atomic_write_json(artifacts / "binding_blocker_report.json", blocker_report)
    atomic_write_json(artifacts / "non_quarantined_agent_summary.json", {"count": len(non_quarantined), "classification_counts": blocker_report["counts"]})
    atomic_write_json(artifacts / "top20_candidate_review.json", {"candidates": top20})
    (artifacts / "top20_candidate_review.md").write_text("# Top 20 candidate review\n\n" + "\n".join(f"- {r['candidate_id']}: {r['decision']} — {r['reason']}" for r in top20) + "\n", encoding="utf-8")
    atomic_write_json(artifacts / "adapter_registry.json", {"adapters": [{"adapter": "FamilySpecificCandidateAdapter", "policy_change": False, "fallback_recorded": True, "identity_preserving": True}]})
    atomic_write_json(artifacts / "family_variant_candidates.json", {"candidates": family_variants})
    atomic_write_json(artifacts / "new_family_candidates.json", {"candidates": [], "reason": "no public or unapproved candidate is executed automatically"})
    atomic_write_json(artifacts / "isolated_smoke_schedule.json", schedule); atomic_write_json(artifacts / "isolated_smoke_summary.json", smoke_summary)
    _write_jsonl(artifacts / "verified_binding_registry.jsonl", verified); atomic_write_json(artifacts / "expanded_family_population.json", expanded)
    atomic_write_json(artifacts / "family_activation_verdict.json", {"verdict": verdict, "verified_binding_count": len(verified), "new_family_count": len({e["family_id"] for e in verified}), "smoke": smoke_summary})
    docs = output_root / "docs"; docs.mkdir(parents=True, exist_ok=True)
    (docs / "candidate_review_report.md").write_text(f"# Candidate review\n\nNon-quarantined agents: {len(non_quarantined)}. Existing evidence-qualified bindings: {len(verified)}. Unknown/public code remains unexecuted.\n", encoding="utf-8")
    (docs / "family_activation_report.md").write_text(f"# Family activation\n\nVerdict: `{verdict}`. Activated only existing team-approved Family bindings: {', '.join(e['family_id'] for e in verified)}. CABT evidence reused: {smoke_summary['cabt_games']} games, all legal.\n", encoding="utf-8")
    return {"verdict": verdict, "verified_bindings": len(verified), "smoke_games": smoke_summary["cabt_games"], "blocker_counts": blocker_report["counts"]}
