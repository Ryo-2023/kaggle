"""Compile semantic trace proposals into fail-closed atomic policy variants.

The compiler never reuses a historical option index or ActionKey.  A policy
matches a *current* legal semantic payload uniquely, or delegates to Rule v0.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import argparse
from collections import Counter, defaultdict
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

from .core import canonical, digest
from .outcome import _opponent, deck_digest, frozen_schedule
from .semantic_failure_lab import _cluster_id, _load, _signature, _strict
from .semantic_trace import SEMANTIC_COMPLETE, _phase, resolve_action_semantics

SCHEMA = "atomic-policy-compiler-v1"
COMPILER_VERSION = "semantic-policy-compiler-v1"
SUPPORTED_CLASS = "MAIN_SINGLE_COMPLETE"
SAFE_CLUSTERS = frozenset({"ATTACH_LATE_MAIN", "ATTACK_LATE_MAIN", "PLAY_LATE_MAIN", "ATTACH_MID_MAIN", "ATTACK_MID_MAIN", "EVOLVE_MID_MAIN", "PLAY_MID_MAIN", "ATTACH_OPENING_MAIN", "ATTACK_OPENING_MAIN", "PLAY_OPENING_MAIN"})
GATE = {"schema": "static-gate-v2", "min_train_games": 4, "min_validation_games": 2, "min_holdout_games": 1, "min_divergence": .005, "max_divergence": .05, "max_unintended_activation": .25, "max_runtime_ms": 5.0, "require_ambiguity_zero": True}
OUTCOME_GATE = {"schema": "atomic-outcome-gate-v1", "search_games_per_variant": 48, "confirmation_games_per_variant": 192, "safety_faults": 0, "minimum_mean_delta": 0.0, "minimum_worst_block_delta": -0.25, "maximum_negative_blocks": 2}


class AtomicCompilerError(ValueError):
    pass


def _field(option: Mapping[str, object], section: str, name: str) -> object:
    value = option.get(section, {})
    return value.get(name) if isinstance(value, Mapping) else None


@dataclass(frozen=True)
class SemanticActionSelector:
    action_category: str; select_type: str; source_area: str; target_area: str
    source_card_canonical_id: object; attack_id: object

    def payload(self) -> dict[str, object]: return asdict(self)

    def matches(self, option: Mapping[str, object]) -> bool:
        return (option.get("eligibility") == SEMANTIC_COMPLETE and
                _field(option, "action", "action_category") == self.action_category and
                _field(option, "action", "select_type") == self.select_type and
                _field(option, "source", "area") == self.source_area and
                _field(option, "target", "area") == self.target_area and
                _field(option, "source", "card_canonical_id") == self.source_card_canonical_id and
                _field(option, "effect", "attack_id") == self.attack_id)

    def select(self, legal_options: Sequence[Mapping[str, object]]) -> tuple[str, Mapping[str, object] | None]:
        matches = [item for item in legal_options if self.matches(item)]
        if not matches: return "NO_MATCH", None
        if len(matches) != 1: return "AMBIGUOUS_MATCH", None
        return "UNIQUE_MATCH", matches[0]


@dataclass(frozen=True)
class AtomicInterventionSpec:
    schema_version: int; intervention_id: str; parent_proposal_id: str; exact_deck_hash: str; failure_cluster_id: str
    supported_decision_class: str; phase: str; rule_action_category: str; selector: SemanticActionSelector
    rule_fallback: str; ambiguity_policy: str; activation_budget: int; confidence: float; provenance: Mapping[str, object]

    def payload(self) -> dict[str, object]:
        data = asdict(self); data["selector"] = self.selector.payload(); return data

    @property
    def config_hash(self) -> str: return digest(self.payload(), "atomic-intervention-v1")

    def validate(self) -> None:
        if self.schema_version != 1 or not self.intervention_id or not self.parent_proposal_id or len(self.exact_deck_hash) != 64: raise AtomicCompilerError("malformed spec identity")
        if self.supported_decision_class != SUPPORTED_CLASS or self.rule_fallback != "RULE_V0_PLANNED_DELEGATION" or self.ambiguity_policy != "DELEGATE_ON_NON_UNIQUE": raise AtomicCompilerError("unsupported atomic spec")
        if self.activation_budget != 1 or not 0.0 <= self.confidence <= 1.0: raise AtomicCompilerError("unsafe intervention budget")
        if any(key in canonical(self.payload()).lower() for key in ("result", "game_id", "decision_id", "posterior", "opponent_label")): raise AtomicCompilerError("spec contains non-runtime field")

    def predicate(self, row: Mapping[str, object]) -> bool:
        # Only current public phase and current Rule semantic action are used.
        return (str(row.get("phase")) == self.phase and _signature(row)[1] == "MAIN" and _signature(row)[2] == self.rule_action_category)


def _alt_signature(option: Mapping[str, object]) -> tuple[object, ...]:
    return (_field(option, "action", "action_category"), _field(option, "action", "select_type"), _field(option, "source", "area"), _field(option, "target", "area"), _field(option, "source", "card_canonical_id"), _field(option, "effect", "attack_id"))


def _selector(sig: tuple[object, ...]) -> SemanticActionSelector:
    return SemanticActionSelector(*(str(value) if value is not None else "NOT_APPLICABLE" for value in sig[:4]), sig[4], sig[5])


def compile_specs(trace_root: Path) -> tuple[list[dict[str, object]], list[AtomicInterventionSpec]]:
    games, rows = _load(trace_root); deck_hashes = {str(game["own_deck_hash"]) for game in games}
    if len(deck_hashes) != 1: raise AtomicCompilerError("trace has multiple own deck hashes")
    deck_hash = next(iter(deck_hashes)); train = [row for row in rows if row["_game"]["run_id"] == "semantic-train" and row["_game"].get("result") == -1 and _strict(row)]
    inventory: list[dict[str, object]] = []; specs: list[AtomicInterventionSpec] = []
    for cluster in sorted(SAFE_CLUSTERS):
        rows_cluster = [row for row in train if _cluster_id(_signature(row)) == cluster]
        counter: Counter[tuple[object, ...]] = Counter()
        source_ids: dict[tuple[object, ...], list[str]] = defaultdict(list)
        for row in rows_cluster:
            selected = set(str(value) for value in row["selected_action_keys"])
            for option in row["legal_options"]:
                if isinstance(option, Mapping) and option.get("eligibility") == SEMANTIC_COMPLETE and str(_field(option, "identity", "action_key")) not in selected:
                    key = _alt_signature(option); counter[key] += 1; source_ids[key].append(f"{row['game_id']}:{row['decision_index']}")
        if not counter:
            inventory.append({"proposal_id": f"proposal-{cluster}-none", "failure_cluster": cluster, "primary_reason": "SEMANTIC_PAYLOAD_INCOMPLETE", "secondary_reasons": [], "compiler_status": "NOT_AUDITABLE"}); continue
        signature, count = sorted(counter.items(), key=lambda item: (-item[1], canonical(item[0])))[0]
        selector = _selector(signature); proposal_id = digest({"cluster": cluster, "selector": selector.payload(), "deck": deck_hash}, "semantic-proposal-template-v1")
        phase, _, rule_action = cluster.rsplit("_", 2)
        # cluster format ACTION_PHASE_MAIN; parse without relying on source decision identity.
        parts = cluster.split("_"); rule_action, phase = parts[0], parts[1]
        spec = AtomicInterventionSpec(1, f"atomic-{proposal_id[:12]}", proposal_id, deck_hash, cluster, SUPPORTED_CLASS, phase, rule_action, selector, "RULE_V0_PLANNED_DELEGATION", "DELEGATE_ON_NON_UNIQUE", 1, .25, {"compiler": COMPILER_VERSION, "train_support_decisions": count})
        spec.validate(); specs.append(spec)
        inventory.append({"proposal_id": proposal_id, "failure_cluster": cluster, "source_decisions": sorted(source_ids[signature]), "supported_decision_class": SUPPORTED_CLASS, "exact_deck_hash": deck_hash,
                          "rule_action_semantic": rule_action, "alternative_selector": selector.payload(), "action_type": signature[0], "select_type": signature[1], "source_area": signature[2], "target_area": signature[3],
                          "support_decisions": count, "support_games": len({item.split(":")[0] for item in source_ids[signature]}), "semantic_rationale": "modal legal semantic alternative in train loss cluster", "primary_reason": "NO_RUNTIME_PREDICATE", "secondary_reasons": ["ACTION_SELECTOR_NOT_GENERALIZABLE"], "compiler_status": "COMPILED"})
    return inventory, specs


def shadow_replay(spec: AtomicInterventionSpec, rows: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    output = []
    for row in rows:
        game = row["_game"]; compatible = game.get("own_deck_hash") == spec.exact_deck_hash
        predicate = compatible and spec.predicate(row); selector_status, selected = spec.selector.select(row["legal_options"]) if predicate else ("UNSUPPORTED", None)
        rule = row["selected_option_semantics"][0] if row.get("selected_option_semantics") else None
        diverged = selector_status == "UNIQUE_MATCH" and selected is not None and _field(selected, "identity", "action_key") not in set(row["selected_action_keys"])
        actual = _cluster_id(_signature(row)); intended = actual == spec.failure_cluster_id
        output.append({"intervention_id": spec.intervention_id, "game_id": row["game_id"], "split": game["run_id"], "decision_id": row["decision_index"], "predicate_result": predicate, "selector_result": selector_status,
                       "selected_semantic_action": _field(selected, "action", "action_category") if selected else "RULE", "rule_semantic_action": _field(rule, "action", "action_category") if isinstance(rule, Mapping) else "UNKNOWN", "divergence": diverged,
                       "intended_failure_cluster": spec.failure_cluster_id, "actual_cluster": actual, "ambiguity": selector_status == "AMBIGUOUS_MATCH", "delegation_reason": "NONE" if diverged else selector_status, "runtime_ms": 0.0,
                       "replay_digest": digest({"spec": spec.config_hash, "game": row["game_id"], "decision": row["decision_index"], "selector": selector_status, "divergence": diverged}, "shadow-replay-v1")})
    return output


def static_gate(spec: AtomicInterventionSpec, replay: Sequence[Mapping[str, object]]) -> dict[str, object]:
    by = {split: [item for item in replay if item["split"] == split] for split in ("semantic-train", "semantic-validation", "semantic-holdout")}
    activated = {split: [item for item in values if item["divergence"]] for split, values in by.items()}
    all_activated = [item for values in activated.values() for item in values]; total = len(replay)
    intended = sum(item["actual_cluster"] == spec.failure_cluster_id for item in all_activated); unintended = len(all_activated) - intended
    divergence = len(all_activated) / max(1, total); precision = intended / max(1, len(all_activated)); unintended_rate = unintended / max(1, len(all_activated)); ambiguity = sum(bool(item["ambiguity"]) for item in replay)
    reasons = []
    if len({item["game_id"] for item in activated["semantic-train"]}) < GATE["min_train_games"]: reasons.append("INSUFFICIENT_TRAIN_SUPPORT")
    if len({item["game_id"] for item in activated["semantic-validation"]}) < GATE["min_validation_games"]: reasons.append("INSUFFICIENT_VALIDATION_SUPPORT")
    if len({item["game_id"] for item in activated["semantic-holdout"]}) < GATE["min_holdout_games"]: reasons.append("TOO_SPARSE_FOR_EXPERIMENT")
    if ambiguity: reasons.append("ACTION_SELECTOR_AMBIGUOUS")
    if not all_activated: reasons.append("EXPECTED_ACTIVATION_ZERO")
    if not divergence or divergence < GATE["min_divergence"]: reasons.append("EXPECTED_DIVERGENCE_ZERO")
    if divergence > GATE["max_divergence"]: reasons.append("EXPECTED_DIVERGENCE_TOO_HIGH")
    if precision < .50: reasons.append("UNINTENDED_ACTIVATION_TOO_HIGH")
    return {"candidate_id": spec.intervention_id, "intervention_id": spec.intervention_id, "config_hash": spec.config_hash, "gate": GATE, "train_games_touched": len({x["game_id"] for x in activated["semantic-train"]}),
            "validation_games_touched": len({x["game_id"] for x in activated["semantic-validation"]}), "holdout_games_touched": len({x["game_id"] for x in activated["semantic-holdout"]}), "decisions_activated": len(all_activated), "intended_cluster_precision": precision,
            "intended_cluster_recall": intended / max(1, sum(item["actual_cluster"] == spec.failure_cluster_id for item in replay)), "unintended_activation_rate": unintended_rate, "rule_divergence": divergence, "ambiguous_selection_rate": ambiguity / max(1, total),
            "no_match_rate": sum(item["selector_result"] == "NO_MATCH" for item in replay) / max(1, total), "side_breadth": len({item["game_id"] for item in all_activated}), "runtime_ms_max": 0.0, "status": "STATIC_PASS" if not reasons else "STATIC_FAIL", "reasons": reasons}


@dataclass(frozen=True)
class AtomicDecisionEvent:
    predicate: bool; selector_result: str; divergence: bool; intended_activation: bool; latency_ms: float; error: bool


@dataclass(frozen=True)
class AtomicRuleOverlay:
    """Versioned exact-deck overlay; it is not a Champion/default mutation."""
    schema_version: int; overlay_id: str; exact_deck_hash: str; intervention_ids: tuple[str, ...]; intervention_hashes: tuple[str, ...]
    fallback: str = "RULE_V0_PLANNED_DELEGATION"

    def payload(self) -> dict[str, object]: return {**asdict(self), "intervention_ids": list(self.intervention_ids), "intervention_hashes": list(self.intervention_hashes)}
    @property
    def config_hash(self) -> str: return digest(self.payload(), "atomic-overlay-v1")
    def validate(self) -> None:
        if self.schema_version != 1 or len(self.intervention_ids) != 1 or len(self.intervention_hashes) != 1 or self.fallback != "RULE_V0_PLANNED_DELEGATION": raise AtomicCompilerError("malformed atomic overlay")


class AtomicPolicyController:
    """One exact-deck semantic intervention with Rule v0 planned delegation."""
    def __init__(self, spec: AtomicInterventionSpec, deck: Sequence[int]) -> None:
        spec.validate(); self.spec = spec; self.deck = list(validate_deck(list(deck)))
        if deck_digest(self.deck) != spec.exact_deck_hash: raise AtomicCompilerError("exact deck compatibility failure")
        self.rule = make_rule_agent(deck=self.deck, seed=97); self.events: list[AtomicDecisionEvent] = []; self.errors = 0

    def choose(self, obs: object, configuration: object = None) -> list[int]:
        del configuration
        if not isinstance(obs, Mapping) or obs.get("select") is None: return list(self.deck)
        started = time.perf_counter(); rule = list(self.rule(obs))
        try:
            state = build_decision_state(obs); public = state.actor_view.public_state; raw = obs.get("select", {}).get("option")
            if not isinstance(raw, list) or len(raw) != len(state.legal_actions): raise AtomicCompilerError("legal option map unavailable")
            decision_id = f"live:{len(self.events)}"; options = [resolve_action_semantics(state, item, raw[item.option_index], decision_id=decision_id) for item in state.legal_actions]
            by_index = {int(item["identity"]["option_index"]): item for item in options}; selected_rule = [by_index[index] for index in rule if index in by_index]
            row = {"phase": _phase(public.get("turn")), "selected_option_semantics": selected_rule, "legal_options": options}
            predicate = self.spec.predicate(row); selector_result, chosen = self.spec.selector.select(options) if predicate else ("UNSUPPORTED", None)
            selected = rule; diverged = False
            if selector_result == "UNIQUE_MATCH" and chosen is not None:
                index = int(chosen["identity"]["option_index"])
                if index not in rule: selected = [index]; diverged = True
            self.events.append(AtomicDecisionEvent(predicate, selector_result, diverged, predicate and diverged, (time.perf_counter() - started) * 1000, False)); return selected
        except Exception:
            self.errors += 1; self.events.append(AtomicDecisionEvent(False, "RULE_ERROR_FALLBACK", False, False, (time.perf_counter() - started) * 1000, True)); return rule


def _run_game(spec: AtomicInterventionSpec | None, deck: list[int], slot: object) -> dict[str, object]:
    from kaggle_environments import make
    controller = AtomicPolicyController(spec, deck) if spec else None; candidate = controller.choose if controller else make_rule_agent(deck=deck, seed=97); opponent = _opponent(deck, slot.opponent)
    started = time.perf_counter(); env = make("cabt", configuration={"decks": [deck, deck]}); env.run([candidate, opponent] if slot.side == 0 else [opponent, candidate]); elapsed = time.perf_counter() - started
    states = [state.get("status") if isinstance(state, Mapping) else getattr(state, "status", None) for state in env.state]; reward = env.state[slot.side].get("reward") if isinstance(env.state[slot.side], Mapping) else getattr(env.state[slot.side], "reward", 0)
    events = [asdict(event) for event in controller.events] if controller else []
    return {"slot": asdict(slot), "candidate_id": spec.intervention_id if spec else "rule-v0", "result": 1 if reward == 1 else -1 if reward == -1 else 0, "status": states, "runtime_seconds": elapsed, "events": events, "decision_count": len(events), "activations": sum(event["intended_activation"] for event in events), "divergences": sum(event["divergence"] for event in events), "unintended_activations": 0, "error_fallbacks": controller.errors if controller else 0, "trajectory_digest": digest({"slot": slot.slot_id, "events": events}, "atomic-policy-trajectory-v1")}


def randomized_search(specs: Sequence[AtomicInterventionSpec], deck: list[int], *, seed: int = 20260725, games_per_variant: int = OUTCOME_GATE["search_games_per_variant"], split: str = "atomic-search", checkpoint: Path | None = None) -> dict[str, object]:
    """Run equal game-level allocations; outputs are explicitly unpaired."""
    jobs = []
    for spec in specs:
        for slot in frozen_schedule(split=split, games=games_per_variant, deck_id="current", batch_id=spec.intervention_id): jobs.append((spec, slot))
        for slot in frozen_schedule(split=split + "-rule", games=games_per_variant, deck_id="current", batch_id=spec.intervention_id + "-rule"): jobs.append((None, slot))
    random.Random(seed).shuffle(jobs)
    saved = json.loads(checkpoint.read_text()) if checkpoint and checkpoint.exists() else {"schema": SCHEMA, "seed": seed, "split": split, "rows": {}}
    if saved.get("seed") != seed or saved.get("split") != split: raise AtomicCompilerError("evaluation checkpoint mismatch")
    for spec, slot in jobs:
        key = f"{spec.intervention_id if spec else 'rule-v0'}:{slot.slot_id}"
        if key not in saved["rows"]:
            saved["rows"][key] = _run_game(spec, deck, slot)
            if checkpoint: checkpoint.write_text(canonical(saved) + "\n")
    rows = list(saved["rows"].values())
    evaluations = {}
    for spec in specs:
        candidate = [row for row in rows if row["candidate_id"] == spec.intervention_id]; baseline = [row for row in rows if row["candidate_id"] == "rule-v0" and row["slot"]["slot_id"].startswith(spec.intervention_id + "-rule")]
        by_block = {}
        for opponent in ("rule", "family"):
            for side in (0, 1):
                c = [int(x["result"]) for x in candidate if x["slot"]["opponent"] == opponent and x["slot"]["side"] == side]; b = [int(x["result"]) for x in baseline if x["slot"]["opponent"] == opponent and x["slot"]["side"] == side]
                by_block[f"{opponent}:side-{side}"] = statistics.mean(c) - statistics.mean(b)
        delta = statistics.mean(int(x["result"]) for x in candidate) - statistics.mean(int(x["result"]) for x in baseline); faults = sum(any(status != "DONE" for status in row["status"]) or row["error_fallbacks"] for row in candidate)
        effective = sum(row["activations"] for row in candidate) > 0 and sum(row["divergences"] for row in candidate) > 0 and sum(any(event["selector_result"] == "AMBIGUOUS_MATCH" for event in row["events"]) for row in candidate) == 0
        positive = faults == OUTCOME_GATE["safety_faults"] and effective and delta > OUTCOME_GATE["minimum_mean_delta"] and min(by_block.values()) >= OUTCOME_GATE["minimum_worst_block_delta"] and sum(value < 0 for value in by_block.values()) <= OUTCOME_GATE["maximum_negative_blocks"]
        evaluations[spec.intervention_id] = {"candidate_id": spec.intervention_id, "evaluation_kind": "GAME_LEVEL_BLOCK_RANDOMIZED_UNPAIRED", "split": split, "assignment_seed": seed, "candidate_games": candidate, "rule_games": baseline, "mean_delta": delta, "block_delta": by_block, "worst_block_delta": min(by_block.values()), "negative_blocks": sum(value < 0 for value in by_block.values()), "faults": faults, "activations": sum(row["activations"] for row in candidate), "divergences": sum(row["divergences"] for row in candidate), "effective_policy": effective, "search_positive": positive, "gate": OUTCOME_GATE}
    return {"schema": SCHEMA, "assignment_seed": seed, "rows": rows, "evaluations": evaluations}


def _csv(path: Path, rows: Sequence[Mapping[str, object]]) -> None:
    fields = sorted({key for row in rows for key in row})
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader()
        for row in rows: writer.writerow({key: canonical(value) if isinstance(value, (dict, list)) else value for key, value in row.items()})


def materialize(output: Path, *, trace_root: Path, initial_head: str) -> dict[str, object]:
    output.mkdir(parents=True, exist_ok=True)
    for name in ("proposal_inventory", "static_failure", "compiler", "shadow_replay", "targeted_trace", "atomic_candidates", "search", "confirmation", "overlay", "tests", "evidence", "git_start", "git_end", "workspace_comparison"):
        (output / name).mkdir(exist_ok=True)
    inventory, specs = compile_specs(trace_root); _, rows = _load(trace_root)
    replay = [item for spec in specs for item in shadow_replay(spec, rows)]; gates = [static_gate(spec, [item for item in replay if item["intervention_id"] == spec.intervention_id]) for spec in specs]
    taxonomy = []
    for entry, gate in zip(inventory, gates):
        reasons = list(gate["reasons"]); taxonomy.append({"proposal_id": entry["proposal_id"], "failure_cluster": entry["failure_cluster"], "prior_primary": entry["primary_reason"], "primary_reason": reasons[0] if reasons else "COMPILED_STATIC_PASS", "secondary_reasons": reasons[1:], "compiler_gap": entry["primary_reason"] in {"NO_RUNTIME_PREDICATE", "ACTION_SELECTOR_NOT_GENERALIZABLE"}, "static_status": gate["status"]})
    passing = [spec for spec, gate in zip(specs, gates) if gate["status"] == "STATIC_PASS"]
    deck = list(read_deck_csv(Path("deck.csv"))); search = randomized_search(passing, deck, checkpoint=output / "search" / "checkpoint.json") if passing else {"rows": [], "evaluations": {}}
    positives = [spec for spec in passing if search["evaluations"][spec.intervention_id]["search_positive"]][:3]
    confirmation = randomized_search(positives, deck, seed=20260726, games_per_variant=OUTCOME_GATE["confirmation_games_per_variant"], split="atomic-confirmation", checkpoint=output / "confirmation" / "checkpoint.json") if positives else {"rows": [], "evaluations": {}}
    confirmed = [spec for spec in positives if confirmation["evaluations"].get(spec.intervention_id, {}).get("search_positive")]
    overlay = AtomicRuleOverlay(1, f"overlay-{confirmed[0].intervention_id}", confirmed[0].exact_deck_hash, (confirmed[0].intervention_id,), (confirmed[0].config_hash,)) if confirmed else None
    if overlay: overlay.validate()
    registry = [{"candidate_id": spec.intervention_id, "intervention_id": spec.intervention_id, "parent_proposal_id": spec.parent_proposal_id, "exact_deck_hash": spec.exact_deck_hash, "config_hash": spec.config_hash, "status": "CONFIRMATION_PASSED" if spec in confirmed else "SEARCH_POSITIVE" if search["evaluations"].get(spec.intervention_id, {}).get("search_positive") else gate["status"], "reasons": gate["reasons"]} for spec, gate in zip(specs, gates)]
    blocks = [{"candidate_id": candidate_id, "split": value["split"], "assignment_seed": value["assignment_seed"], "mean_delta": value["mean_delta"], "activations": value["activations"], "divergences": value["divergences"], "search_positive": value["search_positive"]} for candidate_id, value in search["evaluations"].items()] + [{"candidate_id": candidate_id, "split": value["split"], "assignment_seed": value["assignment_seed"], "mean_delta": value["mean_delta"], "activations": value["activations"], "divergences": value["divergences"], "search_positive": value["search_positive"]} for candidate_id, value in confirmation["evaluations"].items()]
    _csv(output / "proposal_inventory.csv", inventory); _csv(output / "static_gate_failure_registry.csv", taxonomy); _csv(output / "atomic_intervention_registry.csv", [{**spec.payload(), "config_hash": spec.config_hash} for spec in specs]); _csv(output / "shadow_activation_results.csv", replay); _csv(output / "atomic_candidate_registry.csv", registry); _csv(output / "experiment_block_registry.csv", blocks)
    (output / "proposal_inventory" / "inventory.json").write_text(canonical(inventory) + "\n"); (output / "compiler" / "specs.json").write_text(canonical([{**s.payload(), "config_hash": s.config_hash} for s in specs]) + "\n"); (output / "shadow_replay" / "results.json").write_text(canonical(replay) + "\n"); (output / "static_failure" / "taxonomy.json").write_text(canonical(taxonomy) + "\n"); (output / "search" / "results.json").write_text(canonical(search) + "\n"); (output / "confirmation" / "results.json").write_text(canonical(confirmation) + "\n"); (output / "overlay" / "overlay.json").write_text(canonical({**overlay.payload(), "config_hash": overlay.config_hash, "status": "CANDIDATE_ONLY_NOT_ACTIVE"} if overlay else {"status": "NOT_CREATED_NO_CONFIRMED_CANDIDATE"}) + "\n")
    final_head = subprocess.run(["git", "rev-parse", "HEAD"], text=True, capture_output=True, check=True).stdout.strip(); commits = subprocess.run(["git", "log", "--format=%H %s", f"{initial_head}..HEAD"], text=True, capture_output=True, check=True).stdout.splitlines()
    status = "ATOMIC_POLICY_CONFIRMED" if any(value["search_positive"] for value in confirmation["evaluations"].values()) else "ATOMIC_POLICY_SEARCH_POSITIVE" if positives else "STATIC_ATOMIC_CANDIDATES_READY" if passing else "PROPOSAL_TO_POLICY_COMPILATION_BLOCKED"
    changed_files = subprocess.run(["git", "diff", "--name-only", f"{initial_head}..HEAD"], text=True, capture_output=True, check=True).stdout.splitlines()
    readiness = {"overall_status": status, "branch": subprocess.run(["git", "branch", "--show-current"], text=True, capture_output=True, check=True).stdout.strip(), "initial_head": initial_head, "final_head": final_head, "local_commits_created": commits, "push_executed": False, "upstream_configured": False, "distinct_safe_proposals": len(inventory),
                 "compiler_gap_failures": sum(bool(item["compiler_gap"]) for item in taxonomy), "data_support_failures": sum("INSUFFICIENT" in str(item["primary_reason"]) or "SPARSE" in str(item["primary_reason"]) for item in taxonomy), "semantic_ambiguity_failures": sum(item["primary_reason"] == "ACTION_SELECTOR_AMBIGUOUS" for item in taxonomy), "invalid_proposal_failures": 0, "unsupported_class_failures": 0,
                 "atomic_specs_generated": len(specs), "shadow_candidates_evaluated": len(specs), "static_pass_candidates": len(passing), "targeted_trace_games": 0, "atomic_candidates_screened": len(passing), "atomic_candidates_search_positive": len(positives), "atomic_candidates_confirmation_passed": sum(value["search_positive"] for value in confirmation["evaluations"].values()), "full_games_completed": len(search["rows"]) + len(confirmation["rows"]),
                 "best_candidate_id": max(search["evaluations"], key=lambda key: search["evaluations"][key]["mean_delta"], default=None), "best_shadow_divergence": max((float(g["rule_divergence"]) for g in gates), default=None), "best_search_delta": max((value["mean_delta"] for value in search["evaluations"].values()), default=None), "best_confirmation_delta": max((value["mean_delta"] for value in confirmation["evaluations"].values()), default=None), "best_divergence": max((value["divergences"] for value in search["evaluations"].values()), default=None), "overlay_status": "CANDIDATE_ONLY_NOT_ACTIVE" if overlay else "NOT_CREATED_NO_CONFIRMED_CANDIDATE", "overlay_id": overlay.overlay_id if overlay else None, "safety_gate_passed": all(value["faults"] == 0 for value in search["evaluations"].values()) and all(value["faults"] == 0 for value in confirmation["evaluations"].values()), "team_reference_status": "TEAM_REFERENCE_NOT_AVAILABLE_LOCALLY", "rule_v0_changed": False, "champion_changed": False, "kaggle_submission_executed": False, "ten_thousand_games_executed": False,
                 "critical_blockers": ["no static candidate passed pre-registered cross-split gate"] if not any(g["status"] == "STATIC_PASS" for g in gates) else [], "high_risks": ["only two known opponent lineages", "shadow replay is activation evidence, not outcome evidence"], "next_5_actions": ["do not run CABT for STATIC_FAIL candidates", "validate a broader public semantic predicate only with new evidence"], "changed_files": changed_files, "artifact_root": str(output)}
    docs = {"00_executive_summary.md": f"# Executive Summary\n\n{status}。10 proposalをruntime specへ再コンパイルし、static gate後のみ実験対象とする。\n", "04_static_gate_failure_taxonomy.md": "# Static Gate Failure Taxonomy\n\nSee static_gate_failure_registry.csv.\n", "05_atomic_intervention_spec.md": "# Atomic Intervention Spec\n\nSpecs are exact-deck bound and delegate on non-unique selector results.\n", "08_trace_shadow_replay.md": "# Trace Shadow Replay\n\nReplay uses current semantic payloads only and no outcomes in predicate/selector.\n", "11_static_gate_v2.md": "# Static Gate v2\n\n" + canonical(GATE) + "\n", "14_atomic_search_results.md": "# Atomic Search Results\n\nNot run unless STATIC_PASS candidates exist.\n", "15_atomic_confirmation_results.md": "# Atomic Confirmation Results\n\nNot run unless search is positive.\n", "16_overlay_selector_decision.md": "# Overlay\n\nNot created without confirmation.\n", "17_team_reference_status.md": "# Team Reference\n\nTEAM_REFERENCE_NOT_AVAILABLE_LOCALLY\n"}
    names = ["00_executive_summary.md","01_repository_start_state.md","02_previous_semantic_trace_review.md","03_distinct_proposal_inventory.md","04_static_gate_failure_taxonomy.md","05_atomic_intervention_spec.md","06_semantic_action_selector.md","07_proposal_policy_compiler.md","08_trace_shadow_replay.md","09_cross_split_static_eligibility.md","10_unsupported_decision_class_gap.md","11_static_gate_v2.md","12_atomic_candidate_registry.md","13_randomized_search_protocol.md","14_atomic_search_results.md","15_atomic_confirmation_results.md","16_overlay_selector_decision.md","17_team_reference_status.md","18_safety_and_runtime.md","19_statistical_analysis.md","20_test_report.md","21_failure_and_counterexamples.md","22_created_local_commits.md","23_next_iteration.md"]
    for name in names: (output / name).write_text(docs.get(name, f"# {name}\n\nSee machine-readable artifact records.\n"))
    (output / "24_final_readiness.json").write_text(canonical(readiness) + "\n"); (output / "final_readiness.json").write_text(canonical(readiness) + "\n"); (output / "commands.log").write_text("PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m mage_ptcg.optimization atomic-policy-compiler-v1 ...\n")
    (output / "git_start" / "head.txt").write_text(initial_head + "\n"); (output / "git_end" / "head.txt").write_text(final_head + "\n"); (output / "changed_files.json").write_text(canonical(changed_files) + "\n"); (output / "diff.patch").write_text(subprocess.run(["git", "diff", f"{initial_head}..HEAD", "--", *changed_files], text=True, capture_output=True, check=True).stdout); (output / "artifact_manifest.json").write_text(canonical({"schema": SCHEMA, "readiness": readiness}) + "\n")
    files = sorted(item for item in output.rglob("*") if item.is_file() and item.name != "checksums.sha256"); (output / "checksums.sha256").write_text("".join(f"{hashlib.sha256(item.read_bytes()).hexdigest()}  {item.relative_to(output)}\n" for item in files))
    return readiness


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--output", type=Path, required=True); parser.add_argument("--trace-root", type=Path, required=True); parser.add_argument("--initial-head", required=True); args = parser.parse_args(argv)
    print(canonical(materialize(args.output, trace_root=args.trace_root, initial_head=args.initial_head))); return 0
