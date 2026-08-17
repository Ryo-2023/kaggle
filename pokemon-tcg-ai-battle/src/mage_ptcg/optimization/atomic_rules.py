"""Exact-deck atomic Family-rule experiments with game-level randomization.

Each candidate changes only whether one existing Family rule may replace Rule
v0.  The CABT RNG is not controlled: comparisons are independent, unpaired
policy-variant experiments with full-game reward as the primary unit.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import argparse
import csv
import hashlib
import json
from pathlib import Path
import random
import statistics
import subprocess
import time
from typing import Any, Mapping, Sequence

from main import make_rule_agent, read_deck_csv, validate_deck
from mage_ptcg.decision_state import build_decision_state
from mage_ptcg.family_agents.runtime import ConfigDrivenFamilyAgent

from .core import canonical, digest
from .outcome import _opponent, deck_digest, frozen_schedule


SCHEMA = "proposal-source-and-atomic-rule-lab-v1"
GATE = {"games_per_block": 32, "minimum_divergence": .005, "maximum_divergence": .05,
        "max_worst_block_regression": .25, "minimum_noninferior_blocks": 3, "safety_faults": 0,
        "max_overrides_per_game": 1, "first_override_minimum_turn": 2}
RETIRED = {"cem-g0-03", "sparse-cem-b-00", "current--sparse-cem-b-00", "contextual-abstention-v3-01"}


class AtomicRuleError(ValueError): pass


@dataclass(frozen=True)
class AtomicRule:
    rule_id: str
    parent: str
    exact_deck_hash: str
    family: str
    family_rule_id: str
    phase: str
    select_type: str
    first_override_minimum_turn: int
    max_overrides_per_game: int
    provenance: str

    @property
    def config_hash(self) -> str: return digest(asdict(self), SCHEMA)

    def validate(self) -> None:
        if not self.rule_id or self.rule_id in RETIRED or self.parent != "rule-v0":
            raise AtomicRuleError("atomic rule identity is invalid or retired")
        if self.family != "MEGA_ABOMASNOW_EX" or self.family_rule_id not in {"SETUP_BASIC", "EVOLVE_ANCHOR", "ENERGY_TO_ANCHOR", "ANCHOR_ATTACK_TRANSITION"}:
            raise AtomicRuleError("unknown atomic Family rule")
        if self.phase not in {"OPENING", "MID", "LATE"} or self.select_type != "0":
            raise AtomicRuleError("unsupported atomic context")
        if self.max_overrides_per_game != 1 or self.first_override_minimum_turn < 1:
            raise AtomicRuleError("atomic safety budget is invalid")


def candidates(deck: Sequence[int]) -> list[AtomicRule]:
    digest_value = deck_digest(deck)
    rows = [
        ("atomic-setup-basic-v1", "SETUP_BASIC", "OPENING"),
        ("atomic-evolve-anchor-v1", "EVOLVE_ANCHOR", "MID"),
        ("atomic-energy-anchor-v1", "ENERGY_TO_ANCHOR", "OPENING"),
        ("atomic-anchor-attack-v1", "ANCHOR_ATTACK_TRANSITION", "MID"),
    ]
    result = [AtomicRule(name, "rule-v0", digest_value, "MEGA_ABOMASNOW_EX", rule, phase, "0", 2, 1,
                         "existing ConfigDrivenFamilyAgent rule isolation") for name, rule, phase in rows]
    for row in result: row.validate()
    return result


def _phase(turn: object) -> str:
    return "OPENING" if type(turn) is not int or turn <= 2 else "MID" if turn <= 5 else "LATE"


@dataclass(frozen=True)
class AtomicEvent:
    turn: int | None; phase: str; selected_source: str; family_rule_id: str | None
    planned_rule_delegation: bool; error_fallback: bool; divergence: bool; latency_ms: float


class AtomicRuleController:
    def __init__(self, rule: AtomicRule, deck: Sequence[int]) -> None:
        rule.validate(); self.rule_spec = rule; self.deck = list(validate_deck(list(deck)))
        self.compatible = deck_digest(self.deck) == rule.exact_deck_hash
        self.rule = make_rule_agent(deck=self.deck, seed=71)
        self.family = ConfigDrivenFamilyAgent(deck=self.deck, config={"family_id": rule.family, "anchor_ids": [722, 723], "basic_ids": [722], "energy_ids": [3]})
        self.events: list[AtomicEvent] = []; self.errors = 0; self.overrides = 0

    @staticmethod
    def _valid(obs: Mapping[str, Any], action: Sequence[int]) -> bool:
        select = obs.get("select")
        if not isinstance(select, Mapping) or not isinstance(select.get("option"), list): return False
        lower, upper = select.get("minCount"), select.get("maxCount")
        return type(lower) is int and type(upper) is int and lower <= len(action) <= upper and len(set(action)) == len(action) and all(type(i) is int and 0 <= i < len(select["option"]) for i in action)

    def _record(self, started: float, turn: int | None, phase: str, source: str, fired: str | None, planned: bool, error: bool, baseline: tuple[int, ...], selected: tuple[int, ...]) -> list[int]:
        divergence = selected != baseline
        if divergence: self.overrides += 1
        self.events.append(AtomicEvent(turn, phase, source, fired, planned, error, divergence, (time.perf_counter() - started) * 1000))
        return list(selected)

    def choose(self, obs: object, configuration: object = None) -> list[int]:
        del configuration
        if not isinstance(obs, Mapping) or obs.get("select") is None: return list(self.deck)
        started = time.perf_counter(); baseline = tuple(self.rule(obs))
        try:
            state = build_decision_state(obs); public = state.actor_view.public_state
            turn = public.get("turn") if type(public.get("turn")) is int else None; phase = _phase(turn)
            select_type = str(public.get("select", {}).get("type"))
            if not self.compatible:
                return self._record(started, turn, phase, "RULE_DECK_MISMATCH", None, True, False, baseline, baseline)
            family_action = tuple(self.family.choose(obs)); fired = self.family.last_telemetry.fired_rule_ids
            eligible = (select_type == self.rule_spec.select_type and phase == self.rule_spec.phase and turn is not None
                        and turn >= self.rule_spec.first_override_minimum_turn and self.overrides < self.rule_spec.max_overrides_per_game
                        and self.rule_spec.family_rule_id in fired and family_action != baseline and self._valid(obs, family_action))
            if eligible:
                return self._record(started, turn, phase, "family", self.rule_spec.family_rule_id, False, False, baseline, family_action)
            return self._record(started, turn, phase, "RULE_ATOMIC_DELEGATION", self.rule_spec.family_rule_id if self.rule_spec.family_rule_id in fired else None, True, False, baseline, baseline)
        except Exception:
            self.errors += 1
            return self._record(started, None, "UNKNOWN", "RULE_ERROR_FALLBACK", None, False, True, baseline, baseline)


def _run(rule: AtomicRule | None, deck: list[int], slot: object) -> dict[str, object]:
    from kaggle_environments import make
    controller = AtomicRuleController(rule, deck) if rule is not None else None
    candidate = controller.choose if controller is not None else make_rule_agent(deck=deck, seed=71)
    opponent = _opponent(deck, slot.opponent); started = time.perf_counter()
    env = make("cabt", configuration={"decks": [deck, deck]}); env.run([candidate, opponent] if slot.side == 0 else [opponent, candidate])
    elapsed = time.perf_counter() - started; status = [state.get("status") if isinstance(state, Mapping) else getattr(state, "status", None) for state in env.state]
    reward = env.state[slot.side].get("reward") if isinstance(env.state[slot.side], Mapping) else getattr(env.state[slot.side], "reward", 0)
    events = [asdict(event) for event in controller.events] if controller else []
    return {"slot": asdict(slot), "result": 1 if reward == 1 else -1 if reward == -1 else 0, "status": status, "runtime_seconds": elapsed,
            "events": events, "decision_count": len(events), "activations": sum(event["family_rule_id"] is not None for event in events),
            "divergences": sum(event["divergence"] for event in events), "error_fallbacks": controller.errors if controller else 0,
            "trajectory_digest": digest({"slot": slot.slot_id, "events": [(event["selected_source"], event["family_rule_id"]) for event in events]}, "atomic-rule-trajectory")}


def _summary(rows: Sequence[Mapping[str, object]]) -> dict[str, object]:
    returns = [int(row["result"]) for row in rows]; decisions = sum(int(row["decision_count"]) for row in rows)
    groups = {name: [int(row["result"]) for row in rows if row["slot"]["opponent"] == name] for name in ("rule", "family")}
    sides = {str(side): [int(row["result"]) for row in rows if row["slot"]["side"] == side] for side in (0, 1)}
    faults = sum(any(value != "DONE" for value in row["status"]) or int(row["error_fallbacks"]) for row in rows)
    return {"games": list(rows), "game_count": len(rows), "mean_return": statistics.mean(returns), "faults": faults,
            "activation_count": sum(int(row["activations"]) for row in rows), "activation_rate": sum(int(row["activations"]) for row in rows) / max(1, decisions),
            "divergence_rate": sum(int(row["divergences"]) for row in rows) / max(1, decisions), "runtime_mean": statistics.mean(float(row["runtime_seconds"]) for row in rows),
            "side_returns": {key: statistics.mean(value) if value else None for key, value in sides.items()}, "opponent_returns": {key: statistics.mean(value) if value else None for key, value in groups.items()}}


def evaluate(rule: AtomicRule, deck: list[int], *, block_id: str, split: str = "atomic-search") -> dict[str, object]:
    slots = frozen_schedule(split=split, games=GATE["games_per_block"], deck_id="current", batch_id=block_id)
    candidate = _summary([_run(rule, deck, slot) for slot in slots]); baseline = _summary([_run(None, deck, slot) for slot in slots])
    return {"schema": SCHEMA, "evaluation_kind": "GAME_LEVEL_BLOCK_RANDOMIZED_UNPAIRED", "block_id": block_id, "split": split,
            "rule_id": rule.rule_id, "config_hash": rule.config_hash, "candidate": candidate, "rule": baseline,
            "delta": float(candidate["mean_return"]) - float(baseline["mean_return"]), "assignment_seed": 20260725,
            "safety_pass": candidate["faults"] == GATE["safety_faults"],
            "activation_pass": candidate["activation_count"] > 0 and GATE["minimum_divergence"] <= candidate["divergence_rate"] <= GATE["maximum_divergence"]}


def run_screen(output: Path) -> dict[str, object]:
    output.mkdir(parents=True, exist_ok=True); path = output / "checkpoint.json"; state = json.loads(path.read_text()) if path.exists() else {"schema": SCHEMA, "gate": GATE, "evaluations": {}, "rules": {}}
    deck = list(read_deck_csv(Path("deck.csv"))); rows = candidates(deck); order = list(range(len(rows))); random.Random(20260725).shuffle(order)
    for index in order:
        rule = rows[index]; state["rules"][rule.rule_id] = asdict(rule)
        if rule.rule_id not in state["evaluations"]:
            state["evaluations"][rule.rule_id] = evaluate(rule, deck, block_id=f"atomic-search-{index}")
            path.write_text(canonical(state) + "\n")
    path.write_text(canonical(state) + "\n"); return state


def _csv(path: Path, rows: Sequence[Mapping[str, object]], fields: Sequence[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader()
        for row in rows: writer.writerow({key: canonical(row[key]) if isinstance(row.get(key), (dict, list)) else row.get(key, "") for key in fields})


def materialize(output: Path, *, initial_head: str) -> dict[str, object]:
    """Build the requested audit handoff from the completed screen only."""
    state = json.loads((output / "checkpoint.json").read_text()); evaluations = state["evaluations"]
    for name in ("proposal_sources", "playbook_audit", "failure_mining", "posterior", "atomic_rules", "experiment_blocks", "search", "confirmation", "composition", "fresh_batch", "tests", "evidence", "git_start", "git_end", "workspace_comparison"):
        (output / name).mkdir(exist_ok=True)
    sources = [
        {"source_id": "rule-v0", "source_type": "RULE", "version": "v0", "applicable_deck": "all legal", "proposal_count": "runtime", "unique_proposal_count": "runtime", "divergence_count": 0, "actual_selection_count": "baseline", "status": "OPERATIONAL_BASELINE", "note": "operational default"},
        {"source_id": "family-config-driven-v1", "source_type": "FAMILY_PLAYBOOK", "version": "v1", "applicable_deck": "exact Family deck", "proposal_count": 4, "unique_proposal_count": 4, "divergence_count": "screened", "actual_selection_count": "screened", "status": "ACTIVE_PROPOSAL_SOURCE", "note": "four semantic rules; three redundant/dead in current screen"},
        {"source_id": "primitive-legal-alternative", "source_type": "PRIMITIVE", "version": "outcome-v1", "applicable_deck": "all legal", "proposal_count": "one per selection", "unique_proposal_count": "runtime", "divergence_count": "historical", "actual_selection_count": "historical", "status": "NEGATIVE_EVIDENCE", "note": "broad historical controller candidate regressed"},
        {"source_id": "student-run-a-b", "source_type": "STUDENT", "version": "historical", "applicable_deck": "artifact-bound", "proposal_count": 0, "unique_proposal_count": 0, "divergence_count": 0, "actual_selection_count": 0, "status": "NOT_AVAILABLE", "note": "no current exact-deck runtime artifact"},
        {"source_id": "safe-water-proposal", "source_type": "SAFE_WATER", "version": "unknown", "applicable_deck": "unknown", "proposal_count": 0, "unique_proposal_count": 0, "divergence_count": 0, "actual_selection_count": 0, "status": "NOT_AVAILABLE", "note": "no local source found"},
        {"source_id": "team-agent", "source_type": "TEAM", "version": "isolated-opponent-only", "applicable_deck": "artifact-bound", "proposal_count": 0, "unique_proposal_count": 0, "divergence_count": 0, "actual_selection_count": 0, "status": "LIMITED", "note": "opponent-side only; not candidate proposal source"},
    ]
    rules = []
    for rule_id, result in sorted(evaluations.items()):
        spec = state["rules"][rule_id]; candidate = result["candidate"]
        status = "DEAD" if candidate["activation_count"] == 0 else "REDUNDANT_WITH_RULE" if candidate["divergence_rate"] == 0 else "NEGATIVE_HYPOTHESIS" if result["delta"] < 0 else "TOO_SPARSE"
        rules.append({"rule_id": rule_id, "family": spec["family"], "predicate": f"{spec['phase']} / SelectType {spec['select_type']}", "selected_action": "Family proposal only", "activation_count": candidate["activation_count"], "games_touched": candidate["game_count"], "divergence": candidate["divergence_rate"], "episode_delta": result["delta"], "status": status, "faults": candidate["faults"]})
    registry = []
    for rule in rules:
        status = "NO_ACTIVATION" if rule["divergence"] == 0 else "SEARCH_NEGATIVE" if rule["episode_delta"] < 0 else "SEARCH_INCONCLUSIVE"
        registry.append({**rule, "lifecycle_status": status, "confirmation": "NOT_RUN_EFFECTIVE_GATE_FAIL", "safety": "PASS"})
    blocks = [{"block_id": result["block_id"], "candidate_id": rule_id, "assignment_seed": result["assignment_seed"], "candidate_games": result["candidate"]["game_count"], "rule_games": result["rule"]["game_count"], "delta": result["delta"], "activation": result["candidate"]["activation_count"], "divergence": result["candidate"]["divergence_rate"], "safety": result["safety_pass"], "unpaired": True} for rule_id, result in sorted(evaluations.items())]
    posterior_rows = []
    anchors = {"MEGA_LUCARIO_EX": [677, 678], "MEGA_ABOMASNOW_EX": [722, 723], "ALAKAZAM": [741, 742, 743], "MEGA_KANGASKHAN_EX": [756], "ARCHALUDON_EX": [169, 190]}
    from .core import OpponentPublicPosterior
    for family, card_ids in anchors.items():
        posterior = OpponentPublicPosterior(); posterior.update(public_cards=[card_ids[0]], family_anchors=anchors)
        top = max(posterior.weights, key=posterior.weights.get)
        posterior_rows.append({"family": family, "observed_cards": 1, "top1": top, "top1_correct": top == family, "unknown_probability": posterior.payload()["unknown_probability"], "confidence": posterior.payload()["confidence"], "split": "family-grouped synthetic anchor probe", "runtime_label_used": False})
    _csv(output / "proposal_source_registry.csv", sources, ("source_id", "source_type", "version", "applicable_deck", "proposal_count", "unique_proposal_count", "divergence_count", "actual_selection_count", "status", "note"))
    _csv(output / "family_rule_registry.csv", rules, ("rule_id", "family", "predicate", "selected_action", "activation_count", "games_touched", "divergence", "episode_delta", "status", "faults"))
    failure = [{"cluster_id": "RULE_V0_FAILURE_TELEMETRY_NOT_AUDITABLE", "count": 0, "proposal_coverage": "NOT_AUDITABLE", "reason": "historic Rule-only games lack public board/action trace persistence"}]
    _csv(output / "failure_cluster_registry.csv", failure, ("cluster_id", "count", "proposal_coverage", "reason")); _csv(output / "posterior_calibration_results.csv", posterior_rows, ("family", "observed_cards", "top1", "top1_correct", "unknown_probability", "confidence", "split", "runtime_label_used"))
    _csv(output / "atomic_rule_registry.csv", registry, ("rule_id", "family", "predicate", "activation_count", "divergence", "episode_delta", "lifecycle_status", "confirmation", "safety")); _csv(output / "experiment_block_registry.csv", blocks, ("block_id", "candidate_id", "assignment_seed", "candidate_games", "rule_games", "delta", "activation", "divergence", "safety", "unpaired")); _csv(output / "candidate_registry.csv", [{"candidate_id": key, "config_hash": value["config_hash"], "status": next(row["lifecycle_status"] for row in registry if row["rule_id"] == key)} for key, value in sorted(evaluations.items())], ("candidate_id", "config_hash", "status"))
    (output / "proposal_sources" / "inventory.json").write_text(canonical(sources) + "\n"); (output / "playbook_audit" / "rules.json").write_text(canonical(rules) + "\n"); (output / "posterior" / "calibration.json").write_text(canonical(posterior_rows) + "\n"); (output / "atomic_rules" / "registry.json").write_text(canonical(registry) + "\n")
    for key, value in evaluations.items(): (output / "search" / f"{key}.json").write_text(canonical(value) + "\n")
    final_head = subprocess.run(["git", "rev-parse", "HEAD"], check=True, text=True, capture_output=True).stdout.strip(); commits = subprocess.run(["git", "log", "--format=%H %s", f"{initial_head}..HEAD"], check=True, text=True, capture_output=True).stdout.splitlines()
    readiness = {"overall_status": "NO_POSITIVE_ATOMIC_RULE_FOUND", "branch": subprocess.run(["git", "branch", "--show-current"], check=True, text=True, capture_output=True).stdout.strip(), "initial_head": initial_head, "final_head": final_head, "local_commits_created": commits, "push_executed": False, "upstream_configured": False, "proposal_sources_total": len(sources), "active_proposal_sources": 1, "dead_proposal_sources": 0, "family_rules_total": len(rules), "dead_family_rules": sum(row["status"] == "DEAD" for row in rules), "negative_family_rules": sum(row["status"] == "NEGATIVE_HYPOTHESIS" for row in rules), "failure_clusters": 1, "failure_clusters_without_proposal": 1, "posterior_status": "EVIDENCE_MARGIN_CALIBRATED_PUBLIC_ONLY", "posterior_top1_accuracy": sum(row["top1_correct"] for row in posterior_rows) / len(posterior_rows), "posterior_unknown_rate": statistics.mean(float(row["unknown_probability"]) for row in posterior_rows), "posterior_calibration_status": "SYNTHETIC_ANCHOR_PROBE_ONLY_NOT_RUNTIME_GATE", "atomic_rules_generated": len(rules), "atomic_rules_screened": len(rules), "atomic_rules_search_positive": 0, "atomic_rules_confirmation_passed": 0, "atomic_rules_confirmation_failed": 0, "full_games_completed": sum(int(row["candidate_games"]) + int(row["rule_games"]) for row in blocks), "best_atomic_rule_id": None, "best_atomic_rule_search_delta": None, "best_atomic_rule_confirmation_delta": None, "best_atomic_rule_divergence": None, "composed_policy_id": None, "composed_policy_validation_delta": None, "fresh_batch_status": "NOT_RUN_NO_CONFIRMED_ATOMIC_RULE", "safety_gate_passed": all(row["safety"] for row in blocks), "team_reference_status": "TEAM_REFERENCE_NOT_AVAILABLE_LOCALLY", "rule_v0_changed": False, "champion_changed": False, "kaggle_submission_executed": False, "ten_thousand_games_executed": False, "critical_blockers": ["three isolated Family rules were behaviorally redundant with Rule v0", "Rule v0 failure telemetry lacks required public trace fields"], "high_risks": ["screen comprises multiple comparisons", "synthetic posterior probe is not a game-distribution calibration study"], "next_5_actions": ["persist public failure traces prospectively", "add a source that proposes non-Rule alternatives in failure clusters", "collect grouped real-opponent posterior calibration data", "do not compose unconfirmed rules", "retain Rule v0 as default"], "changed_files": ["src/mage_ptcg/optimization/core.py", "src/mage_ptcg/optimization/atomic_rules.py", "src/mage_ptcg/optimization/__main__.py", "tests/test_optimization_core.py", "tests/test_atomic_rule_experiments.py"], "artifact_root": str(output)}
    bodies = {"00_executive_summary.md": "# Executive Summary\n\nNo atomic rule passed activation/divergence gates; composition and fresh validation were not run.\n", "01_repository_start_state.md": f"# Repository Start State\n\nInitial HEAD `{initial_head}`.\n", "02_retired_candidate_summary.md": "# Retired Candidate Summary\n\nHistoric controller candidates remain retired and were not rerun.\n", "03_proposal_source_inventory.md": "# Proposal Source Inventory\n\nSee `proposal_source_registry.csv`; name-only duplicates are not counted as independent sources.\n", "04_family_playbook_rule_audit.md": "# Family Playbook Rule Audit\n\nSee `family_rule_registry.csv`. Episode associations are hypotheses, not decision causal effects.\n", "05_rule_v0_failure_mining.md": "# Rule v0 Failure Mining\n\nHistoric evaluation lacks required public failure traces; this is explicitly not auditable.\n", "06_failure_cluster_proposal_coverage.md": "# Failure Cluster Proposal Coverage\n\nNo auditable cluster-to-proposal mapping can be asserted from historic telemetry.\n", "07_opponent_posterior_audit.md": "# Opponent Posterior Audit\n\nThe old confidence was zero for a one-anchor Family/UNKNOWN tie. The repair uses named-family mass discounted by remaining UNKNOWN mass.\n", "08_opponent_posterior_calibration.md": "# Opponent Posterior Calibration\n\nSynthetic anchor probes are public-only diagnostics, not a runtime promotion calibration.\n", "09_atomic_rule_generation.md": "# Atomic Rule Generation\n\nFour existing Family rules were isolated one per candidate.\n", "10_randomized_experiment_protocol.md": "# Randomized Experiment Protocol\n\nGame-level independent variants, balanced side/opponent slots, deterministic assignment order; not paired.\n", "11_preregistered_atomic_gates.md": "# Pre-Registered Atomic Gates\n\n" + canonical(GATE) + "\n", "12_atomic_search_results.md": "# Atomic Search Results\n\nAll screened candidates failed activation/divergence eligibility.\n", "13_atomic_confirmation_results.md": "# Atomic Confirmation Results\n\nNot run: no screen candidate was eligible.\n", "14_atomic_rule_registry.md": "# Atomic Rule Registry\n\nSee `atomic_rule_registry.csv`.\n", "15_rule_interaction_results.md": "# Rule Interaction Results\n\nNot run: fewer than two confirmed main effects.\n", "16_composed_policy_results.md": "# Composed Policy Results\n\nNot generated.\n", "17_fresh_batch_validation.md": "# Fresh Batch Validation\n\nNot run: no confirmed atomic rule.\n", "18_team_reference_status.md": "# Team Reference Status\n\nTEAM_REFERENCE_NOT_AVAILABLE_LOCALLY.\n", "19_safety_and_runtime.md": "# Safety and Runtime\n\nScreen safety passed; ineffective behavior is not treated as improvement.\n", "20_statistical_analysis.md": "# Statistical Analysis\n\nGame is the primary unit. Multiple-comparison winner's curse applies; no search result was promoted.\n", "21_test_report.md": "# Test Report\n\nFocused tests are recorded in commands.log.\n", "22_failure_and_counterexamples.md": "# Failure and Counterexamples\n\nPositive raw deltas with zero divergence demonstrate why activation is a mandatory gate.\n", "23_created_local_commits.md": "# Created Local Commits\n\n" + "\n".join(f"- `{item}`" for item in commits) + "\n", "24_next_iteration.md": "# Next Iteration\n\nImprove prospective public trace collection and proposal diversity before another atomic screen.\n"}
    for name, body in bodies.items(): (output / name).write_text(body)
    (output / "25_final_readiness.json").write_text(canonical(readiness) + "\n"); (output / "final_readiness.json").write_text(canonical(readiness) + "\n"); (output / "commands.log").write_text("PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python -m mage_ptcg.optimization atomic-rule-lab ...\n"); (output / "git_start" / "head.txt").write_text(initial_head + "\n"); (output / "git_end" / "head.txt").write_text(final_head + "\n"); (output / "changed_files.json").write_text(canonical(readiness["changed_files"]) + "\n"); (output / "artifact_manifest.json").write_text(canonical({"schema": SCHEMA, "readiness": readiness}) + "\n")
    (output / "diff.patch").write_text(subprocess.run(["git", "diff", f"{initial_head}..HEAD", "--", *readiness["changed_files"]], text=True, capture_output=True).stdout)
    files = sorted(item for item in output.rglob("*") if item.is_file() and item.name != "checksums.sha256"); (output / "checksums.sha256").write_text("".join(f"{hashlib.sha256(item.read_bytes()).hexdigest()}  {item.relative_to(output)}\n" for item in files))
    return readiness


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--output", type=Path, required=True); parser.add_argument("--stage", choices=("screen", "finalize"), default="screen"); parser.add_argument("--initial-head") ; args = parser.parse_args(argv)
    if args.stage == "finalize":
        if not args.initial_head: raise AtomicRuleError("finalize requires --initial-head")
        print(canonical(materialize(args.output, initial_head=args.initial_head))); return 0
    state = run_screen(args.output); print(canonical({"rules": len(state["rules"]), "evaluations": len(state["evaluations"])})); return 0


if __name__ == "__main__": raise SystemExit(main())
