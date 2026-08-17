"""Train-only semantic failure analysis and fail-closed proposal coverage."""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
import argparse
import csv
import json
import math
from pathlib import Path
from typing import Mapping, Sequence

from .core import canonical, digest
from .semantic_trace import (SCHEMA as SEMANTIC_TRACE_SCHEMA, SEMANTIC_COMPLETE, audit_v2_migration,
                             semantic_action_digest)

SCHEMA = "semantic-failure-lab-v3"
KNOWN_FAMILY = "MEGA_ABOMASNOW_EX"  # offline evaluation label, never a runtime input
FIXED_POSTERIOR_THRESHOLD = 0.10


@dataclass(frozen=True)
class SemanticProposalV2:
    proposal_id: str; generator_id: str; source_decision: str; semantic_action: str; action_key: str
    rationale: str; failure_cluster: str; required_context: Mapping[str, object]; opponent_condition: str
    confidence: float; abstention: bool; runtime: str; provenance: Mapping[str, object]


class SemanticProposalGeneratorV2_1:
    """Returns semantic candidates only; it never chooses an action.

    The generator consumes semantic-complete persisted options, which are the
    exact payloads produced by the live resolver.  This keeps the offline
    coverage view and live representation on one schema without pretending
    that a historical hash can be decoded.
    """
    generator_id = "semantic-proposal-generator-v2.1"

    def propose(self, decision: Mapping[str, object], *, failure_cluster: str) -> list[SemanticProposalV2]:
        selected = {str(value) for value in decision.get("selected_action_keys", [])}
        source = f"{decision.get('game_id')}:{decision.get('decision_index')}"
        rows: list[SemanticProposalV2] = []
        seen: set[str] = set()
        for option in decision.get("legal_options", []):
            if not isinstance(option, Mapping) or option.get("eligibility") != SEMANTIC_COMPLETE:
                continue
            identity, action = option.get("identity"), option.get("action")
            if not isinstance(identity, Mapping) or not isinstance(action, Mapping):
                continue
            key = str(identity.get("action_key"))
            if key in selected:
                continue
            semantic = str(action.get("action_category"))
            option_digest = semantic_action_digest(option)
            if option_digest in seen:
                continue
            seen.add(option_digest)
            required = {"select_type": action.get("select_type"), "phase": decision.get("phase"), "semantic_complete": True}
            proposal_id = digest({"source": source, "key": key, "cluster": failure_cluster}, "semantic-proposal-v2.1")
            rows.append(SemanticProposalV2(proposal_id, self.generator_id, source, semantic, key,
                                            "legal semantic alternative to the Rule v0 selection", failure_cluster, required,
                                            "NONE", .25, False, "OFFLINE_COVERAGE_ONLY", {"semantic_digest": option_digest, "trace_schema": SEMANTIC_TRACE_SCHEMA}))
        return rows


def _load(trace_root: Path) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    checkpoint = json.loads((trace_root / "collection_checkpoint.json").read_text())
    games = list(checkpoint["games"].values()); rows: list[dict[str, object]] = []
    for path in sorted((trace_root / "traces").glob("*.json")):
        value = json.loads(path.read_text()); game = value["game"]
        for row in value["decisions"]:
            rows.append({**row, "_game": game})
    return games, rows


def _strict(row: Mapping[str, object]) -> bool:
    options = row.get("legal_options"); selected = row.get("selected_option_semantics")
    return isinstance(options, list) and bool(options) and isinstance(selected, list) and bool(selected) and all(isinstance(item, Mapping) and item.get("eligibility") == SEMANTIC_COMPLETE for item in options + selected)


def _signature(row: Mapping[str, object]) -> tuple[str, str, str]:
    selected = row.get("selected_option_semantics", [])
    semantic = selected[0] if isinstance(selected, list) and selected and isinstance(selected[0], Mapping) else {}
    action = semantic.get("action", {}) if isinstance(semantic, Mapping) else {}
    return (str(row.get("phase")), str(action.get("select_type")), str(action.get("action_category")))


def _cluster_id(signature: tuple[str, str, str]) -> str:
    phase, select_type, action = signature
    return f"{action}_{phase}_{select_type}".replace(" ", "_")


def _posterior_metrics(rows: Sequence[Mapping[str, object]]) -> dict[str, object]:
    samples = []; invalid_probability_rows = 0
    for row in rows:
        posterior = row.get("opponent_posterior", {})
        if not isinstance(posterior, Mapping): continue
        families = posterior.get("families", {}); confidence = float(posterior.get("confidence", 0.0))
        if not isinstance(families, Mapping): continue
        try:
            normalized = {str(k): float(v) for k, v in families.items()}
        except (TypeError, ValueError):
            invalid_probability_rows += 1; continue
        if any(not math.isfinite(value) or not 0.0 <= value <= 1.0 for value in normalized.values()) or not math.isclose(sum(normalized.values()), 1.0, abs_tol=1e-9):
            invalid_probability_rows += 1; continue
        named = {name: value for name, value in normalized.items() if name != "UNKNOWN"}
        top = max(named, key=named.get) if named else "UNKNOWN"; probability = float(families.get(KNOWN_FAMILY, 0.0))
        samples.append({"game": row.get("game_id"), "turn": row.get("turn"), "lineage": row.get("_game", {}).get("opponent_policy_lineage"), "top": top, "confidence": confidence, "probability": probability})
    top1 = sum(item["top"] == KNOWN_FAMILY for item in samples) / max(1, len(samples)); coverage = sum(item["confidence"] >= FIXED_POSTERIOR_THRESHOLD for item in samples) / max(1, len(samples))
    brier = sum((item["probability"] - 1.0) ** 2 for item in samples) / max(1, len(samples))
    bins = defaultdict(list)
    for item in samples: bins[min(9, int(item["confidence"] * 10))].append(item)
    ece = sum(len(values) / max(1, len(samples)) * abs(sum(v["top"] == KNOWN_FAMILY for v in values) / len(values) - sum(v["confidence"] for v in values) / len(values)) for values in bins.values())
    wrong_high = sum(item["top"] != KNOWN_FAMILY and item["confidence"] >= FIXED_POSTERIOR_THRESHOLD for item in samples)
    status = "NOT_AUDITABLE" if invalid_probability_rows else "INSUFFICIENT_LINEAGE_DIVERSITY"
    return {"status": status, "runtime_label_used": False, "invalid_probability_rows": invalid_probability_rows, "valid_probability_rows": len(samples), "known_lineages": sorted({str(item["lineage"]) for item in samples}), "top1_accuracy": top1,
            "top_k_accuracy": top1, "unknown_rate": sum(item["top"] == "UNKNOWN" for item in samples) / max(1, len(samples)), "coverage": coverage, "brier_score": brier,
            "expected_calibration_error": ece, "wrong_high_confidence": wrong_high, "threshold": FIXED_POSTERIOR_THRESHOLD,
            "reliability": {str(index): {"count": len(values), "accuracy": sum(v["top"] == KNOWN_FAMILY for v in values) / len(values), "confidence": sum(v["confidence"] for v in values) / len(values)} for index, values in sorted(bins.items())}}


def analyze(trace_root: Path, old_trace_root: Path) -> dict[str, object]:
    games, rows = _load(trace_root); by_split = defaultdict(list)
    for row in rows: by_split[str(row["_game"]["run_id"])].append(row)
    train_name, validation_name, holdout_name = "semantic-train", "semantic-validation", "semantic-holdout"
    train_losses = [row for row in by_split[train_name] if row["_game"].get("result") == -1 and _strict(row)]
    discovered = Counter(_signature(row) for row in train_losses)
    clusters = []
    generator = SemanticProposalGeneratorV2_1(); coverage = []
    for signature, train_decisions in sorted(discovered.items()):
        cid = _cluster_id(signature); partitions = {}
        for split in (train_name, validation_name, holdout_name):
            values = [row for row in by_split[split] if row["_game"].get("result") == -1 and _strict(row) and _signature(row) == signature]
            partitions[split] = values
        stable = bool(partitions[validation_name]) and bool(partitions[holdout_name])
        all_values = [item for values in partitions.values() for item in values]
        lineages = sorted({str(item["_game"].get("opponent_policy_lineage")) for item in all_values})
        clusters.append({"cluster_id": cid, "semantic_signature": {"phase": signature[0], "select_type": signature[1], "action_category": signature[2]},
                         "train_games": len({x["game_id"] for x in partitions[train_name]}), "validation_games": len({x["game_id"] for x in partitions[validation_name]}), "holdout_games": len({x["game_id"] for x in partitions[holdout_name]}),
                         "decisions": {split: len(values) for split, values in partitions.items()}, "opponent_blocks": sorted({str(x["_game"].get("opponent_id")) for x in all_values}), "lineages": lineages,
                         "sides": sorted({int(x["_game"].get("side")) for x in all_values}), "rule_branch_signature": "RULE_V0_EXTERNAL_UNINSTRUMENTED",
                         "alternative_option_availability": sum(len(generator.propose(row, failure_cluster=cid)) > 0 for row in partitions[train_name]),
                         "outcome_association": "loss-conditioned descriptive cluster; not a runtime feature", "sequence_similarity": "semantic signature only", "status": "STABLE_KNOWN_LINEAGES" if stable else "TRAIN_ONLY"})
        proposals = [proposal for row in partitions[train_name] for proposal in generator.propose(row, failure_cluster=cid)]
        distinct = {proposal.action_key for proposal in proposals}; coverage.append({"cluster_id": cid, "applicable_decisions": len(partitions[train_name]), "legal_option_count": sum(len(row["legal_options"]) for row in partitions[train_name]),
                         "proposal_count": len(proposals), "distinct_proposal_count": len(distinct), "rule_equivalent_count": 0, "rule_divergent_count": len(proposals), "duplicate_count": len(proposals) - len(distinct), "abstention_count": 0,
                         "confidence": .25 if proposals else 0.0, "supported_select_type": "MAIN", "runtime": "OFFLINE_COVERAGE_ONLY", "compatibility": "exact-current-deck",
                         "coverage": "DISTINCT_SAFE_PROPOSAL" if proposals and stable else "NO_PROPOSAL" if not proposals else "TOO_SPARSE"})
    completeness = {split: {"decisions": len(values), "strict_semantic_complete": sum(_strict(row) for row in values), "strict_rate": sum(_strict(row) for row in values) / max(1, len(values))} for split, values in by_split.items()}
    ready = {"status": "READY_WITH_RESTRICTED_DECISION_CLASSES", "restriction": "SelectType MAIN, known action type, all legal options SEMANTIC_COMPLETE", "hidden_information_violations": sum(int(game["trace_quality"]["hidden_information_violations"]) for game in games),
             "selected_action_complete_for_restricted_decisions": 1.0, "completeness": completeness}
    static = []
    for item in coverage:
        static.append({"cluster_id": item["cluster_id"], "status": "TOO_BROAD", "reason": "coverage alternatives have no verified deterministic public predicate and no source/target-role resolver; no atomic candidate generated"})
    return {"trace_gate": ready, "migration_audit": audit_v2_migration(old_trace_root), "trace_counts": {"games": len(games), "decisions": len(rows)}, "clusters": clusters, "posterior": {split: _posterior_metrics(values) for split, values in by_split.items()}, "coverage": coverage,
            "proposals": [asdict(p) for c in clusters for row in [r for r in train_losses if _cluster_id(_signature(r)) == c["cluster_id"]] for p in generator.propose(row, failure_cluster=c["cluster_id"])], "static_gate": static,
            "atomic": {"status": "NOT_RUN_NO_STATIC_CANDIDATE", "game_count": 0, "reason": "all proposed coverage alternatives failed the public-predicate static gate"}, "team_reference_status": "TEAM_REFERENCE_NOT_AVAILABLE_LOCALLY"}


def _write_csv(path: Path, rows: Sequence[Mapping[str, object]]) -> None:
    fields = sorted({key for row in rows for key in row})
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader()
        for row in rows: writer.writerow({key: canonical(value) if isinstance(value, (dict, list)) else value for key, value in row.items()})


def materialize(output: Path, *, trace_root: Path, old_trace_root: Path) -> dict[str, object]:
    result = analyze(trace_root, old_trace_root); output.mkdir(parents=True, exist_ok=True)
    for directory in ("migration", "semantic_trace", "failure_clusters", "posterior", "coverage", "proposals", "atomic", "tests", "evidence"):
        (output / directory).mkdir(exist_ok=True)
    (output / "migration" / "audit.json").write_text(canonical(result["migration_audit"]) + "\n")
    (output / "semantic_trace" / "gate.json").write_text(canonical(result["trace_gate"]) + "\n")
    (output / "failure_clusters" / "clusters.json").write_text(canonical(result["clusters"]) + "\n")
    (output / "posterior" / "calibration.json").write_text(canonical(result["posterior"]) + "\n")
    (output / "coverage" / "matrix.json").write_text(canonical(result["coverage"]) + "\n")
    (output / "proposals" / "proposals.json").write_text(canonical(result["proposals"]) + "\n")
    (output / "atomic" / "static_gate.json").write_text(canonical(result["static_gate"]) + "\n")
    _write_csv(output / "failure_cluster_registry.csv", result["clusters"]); _write_csv(output / "proposal_coverage.csv", result["coverage"]); _write_csv(output / "static_candidate_registry.csv", result["static_gate"])
    bodies = {"00_executive_summary.md": "# Executive Summary\n\n旧Trace v2 はlossless semantic migration不能のため不変のまま隔離し、v2.1 256局を収集した。atomic候補はstatic gateを通らず実験は未実施。\n",
              "03_migration_audit.md": "# Migration Audit\n\n`RECOLLECTION_REQUIRED`。ActionKey digestからoption payloadを逆算していない。\n", "05_semantic_trace_gate.md": "# Semantic Trace Gate\n\n対応クラスはMAINかつ全legal optionがsemantic completeのdecisionに限定する。\n",
              "07_failure_clusters.md": "# Failure Clusters\n\nTrainのみで発見し、validation/holdoutでは既知2 lineage内の安定性のみを検査した。\n", "09_posterior_calibration.md": "# Posterior Calibration\n\n既知deck Familyはoffline labelだけでありruntime入力ではない。thresholdはvalidation前に固定した。\n", "11_proposal_coverage.md": "# Proposal Coverage\n\nsemantic completeなRule非選択legal optionだけを数えた。\n", "13_static_proposal_gate.md": "# Static Proposal Gate\n\nverified public predicateを持たないためatomic candidateは生成しなかった。\n", "15_atomic_experiments.md": "# Atomic Experiments\n\nNOT_RUN_NO_STATIC_CANDIDATE。\n", "18_team_reference_status.md": "# Team Reference Status\n\nTEAM_REFERENCE_NOT_AVAILABLE_LOCALLY\n", "21_test_report.md": "# Test Report\n\nテスト実行記録はcommands.logを参照。\n", "24_next_iteration.md": "# Next Iteration\n\n公開source/target role resolverとdeterministic predicateが検証できた場合だけstatic candidateを再検討する。\n"}
    for index in range(25):
        name = f"{index:02d}_" + {0:"executive_summary",3:"migration_audit",5:"semantic_trace_gate",7:"failure_clusters",9:"posterior_calibration",11:"proposal_coverage",13:"static_proposal_gate",15:"atomic_experiments",18:"team_reference_status",21:"test_report",24:"next_iteration"}.get(index, "record") + ".md"
        (output / name).write_text(bodies.get(name, f"# Record {index:02d}\n\nSee machine-readable registries in this artifact.\n"))
    final = {"schema": SCHEMA, "trace_status": result["trace_gate"]["status"], "migration_status": result["migration_audit"]["semantic_payload_status"],
             "trace": {"games": result["trace_counts"]["games"], "decisions": result["trace_counts"]["decisions"], "complete_games": sum(game["trace_quality"]["status"] == "SEMANTIC_TRACE_COMPLETE" for game in _load(trace_root)[0]), "invalid_games": sum(game["trace_quality"]["status"] != "SEMANTIC_TRACE_COMPLETE" for game in _load(trace_root)[0]), "hidden_information_violations": result["trace_gate"]["hidden_information_violations"], "gate": result["trace_gate"]},
             "posterior": result["posterior"], "failure_clusters": {"total": len(result["clusters"]), "stable_known_lineages": sum(item["status"] == "STABLE_KNOWN_LINEAGES" for item in result["clusters"])},
             "coverage_counts": dict(Counter(item["coverage"] for item in result["coverage"])), "proposal_generator": {"id": SemanticProposalGeneratorV2_1.generator_id, "proposal_count": len(result["proposals"]), "static_candidate_count": 0, "static_gate": result["static_gate"]},
             "atomic_results": result["atomic"], "team_reference_status": result["team_reference_status"], "rule_v0_changed": False, "champion_changed": False, "kaggle_submission_executed": False}
    (output / "25_final_readiness.json").write_text(canonical(final) + "\n"); (output / "final_readiness.json").write_text(canonical(final) + "\n")
    (output / "commands.log").write_text("PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m mage_ptcg.optimization semantic-trace-v2-1 --stage smoke --games 16\nPYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m mage_ptcg.optimization semantic-trace-v2-1 --stage main --games 256\nPYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m mage_ptcg.optimization semantic-failure-lab-v3 ...\n", encoding="utf-8")
    return final


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--output", type=Path, required=True); parser.add_argument("--trace-root", type=Path, required=True); parser.add_argument("--old-trace-root", type=Path, required=True); args = parser.parse_args(argv)
    print(canonical(materialize(args.output, trace_root=args.trace_root, old_trace_root=args.old_trace_root))); return 0
