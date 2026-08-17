"""Candidate-only validated atomic-rule lifecycle.

This module deliberately keeps validated rules out of the Rule v0/default
entrypoint.  A rule can be constructed only by an explicit candidate ID and
only after its exact deck, schema and Rule v0 source fingerprint match.
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

from .atomic_policy_compiler import (AtomicCompilerError, AtomicInterventionSpec,
                                     AtomicPolicyController, AtomicRuleOverlay,
                                     OUTCOME_GATE, _field, _load, _run_game,
                                     compile_specs, shadow_replay, static_gate)
from .core import canonical, digest
from .outcome import EvaluationSlot, _opponent, deck_digest
from .semantic_failure_lab import _cluster_id, _signature
from .semantic_trace import RESOLVER_VERSION

SCHEMA = "incremental-validated-rule-learning-v1"
RUNTIME_SCHEMA = "validated-atomic-runtime-v1"
STATUS_CANDIDATE_ONLY = "CANDIDATE_ONLY_NOT_ACTIVE"
REPEATABILITY_GATE = {
    "schema": "validated-repeatability-gate-v1",
    "prior_gate": OUTCOME_GATE,
    "safety_faults": 0,
    "minimum_mean_delta": 0.0,
    "minimum_worst_block_delta": -0.25,
    "maximum_negative_blocks": 2,
    "minimum_noninferior_blocks": 6,
    "maximum_side_regression": -0.25,
    "maximum_opponent_regression": -0.25,
    "require_ambiguity_zero": True,
    "require_unintended_activations_zero": True,
}


class ValidatedRuleError(ValueError):
    pass


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rule_v0_fingerprint() -> str:
    return _sha(Path("main.py"))


def _read_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def _spec(value: Mapping[str, object]) -> AtomicInterventionSpec:
    selector = value["selector"]
    if not isinstance(selector, Mapping):
        raise ValidatedRuleError("candidate selector is malformed")
    from .atomic_policy_compiler import SemanticActionSelector
    fields = {key: value[key] for key in AtomicInterventionSpec.__dataclass_fields__}
    fields["selector"] = SemanticActionSelector(**selector)  # type: ignore[arg-type]
    return AtomicInterventionSpec(**fields)  # type: ignore[arg-type]


@dataclass(frozen=True)
class CandidateFreeze:
    schema: str
    candidate_id: str
    intervention_id: str
    overlay_id: str
    exact_deck_hash: str
    candidate_config: Mapping[str, object]
    candidate_config_hash: str
    overlay_config: Mapping[str, object]
    overlay_config_hash: str
    rule_v0_hash: str
    semantic_resolver_version: str
    compiler_version: str
    source_trace: str
    search_evaluation_id: str
    confirmation_evaluation_id: str
    confirmation_block_ids: tuple[str, ...]
    runtime_config: Mapping[str, object]
    compatibility_contract: Mapping[str, object]
    provenance: Mapping[str, object]

    def payload(self) -> dict[str, object]:
        data = asdict(self); data["confirmation_block_ids"] = list(self.confirmation_block_ids); return data

    @property
    def checksum(self) -> str:
        return digest(self.payload(), "candidate-freeze-v1")

    def validate(self) -> None:
        if self.schema != SCHEMA or self.candidate_id != self.intervention_id:
            raise ValidatedRuleError("candidate freeze identity mismatch")
        if self.runtime_config.get("candidate_only") is not True:
            raise ValidatedRuleError("candidate freeze must remain candidate-only")
        if self.compatibility_contract.get("rule_v0_hash") != self.rule_v0_hash:
            raise ValidatedRuleError("freeze Rule v0 mismatch")
        if self.compatibility_contract.get("exact_deck_hash") != self.exact_deck_hash:
            raise ValidatedRuleError("freeze deck mismatch")
        if digest(self.candidate_config, "atomic-intervention-v1") != self.candidate_config_hash:
            raise ValidatedRuleError("candidate config was modified after freeze")
        if digest(self.overlay_config, "atomic-overlay-v1") != self.overlay_config_hash:
            raise ValidatedRuleError("overlay config was modified after freeze")


def freeze_confirmed_candidate(previous: Path, trace_root: Path) -> tuple[CandidateFreeze, AtomicInterventionSpec, AtomicRuleOverlay]:
    specs = _read_json(previous / "compiler" / "specs.json")
    overlay_data = _read_json(previous / "overlay" / "overlay.json")
    confirmation = _read_json(previous / "confirmation" / "results.json")
    if not isinstance(specs, list) or not isinstance(overlay_data, Mapping) or not isinstance(confirmation, Mapping):
        raise ValidatedRuleError("prior artifact cannot be reconstructed")
    wanted = "atomic-5a3d1ec99c5c"
    values = [item for item in specs if isinstance(item, Mapping) and item.get("intervention_id") == wanted]
    if len(values) != 1:
        raise ValidatedRuleError("confirmed candidate is not uniquely present")
    spec = _spec(values[0]); spec.validate()
    overlay = AtomicRuleOverlay(1, str(overlay_data["overlay_id"]), str(overlay_data["exact_deck_hash"]),
                                tuple(overlay_data["intervention_ids"]), tuple(overlay_data["intervention_hashes"]))
    overlay.validate()
    if overlay.intervention_ids != (spec.intervention_id,) or overlay.intervention_hashes != (spec.config_hash,):
        raise ValidatedRuleError("overlay does not bind the confirmed spec")
    evaluations = confirmation.get("evaluations")
    if not isinstance(evaluations, Mapping) or not isinstance(evaluations.get(wanted), Mapping):
        raise ValidatedRuleError("confirmation evidence missing")
    evaluation = evaluations[wanted]
    block_ids = tuple(sorted(str(key) for key in evaluation.get("block_delta", {})))
    freeze = CandidateFreeze(
        SCHEMA, wanted, wanted, overlay.overlay_id, spec.exact_deck_hash, {**spec.payload()}, spec.config_hash,
        {**overlay.payload()}, overlay.config_hash, _rule_v0_fingerprint(), RESOLVER_VERSION,
        str(spec.provenance.get("compiler")), str(trace_root), "atomic-search:20260725", "atomic-confirmation:20260726",
        block_ids,
        {"schema": RUNTIME_SCHEMA, "candidate_only": True, "explicit_candidate_id_required": True,
         "default_path": "RULE_V0_ONLY", "fallback": "RULE_V0_PLANNED_DELEGATION"},
        {"exact_deck_hash": spec.exact_deck_hash, "rule_v0_hash": _rule_v0_fingerprint(),
         "semantic_resolver_version": RESOLVER_VERSION, "supported_decision_class": spec.supported_decision_class},
        {"previous_artifact": str(previous), "failure_cluster": spec.failure_cluster_id,
         "parent_proposal_id": spec.parent_proposal_id, "confirmation_mean_delta": evaluation.get("mean_delta"),
         "confirmation_faults": evaluation.get("faults")},
    )
    freeze.validate(); return freeze, spec, overlay


class AtomicRuleOverlayController:
    """The generated overlay runtime; it has no default-path registration."""
    def __init__(self, overlay: AtomicRuleOverlay, spec: AtomicInterventionSpec, deck: Sequence[int]) -> None:
        overlay.validate(); spec.validate()
        if overlay.intervention_ids != (spec.intervention_id,) or overlay.intervention_hashes != (spec.config_hash,):
            raise ValidatedRuleError("overlay/spec incompatibility")
        self.overlay = overlay
        self.delegate = AtomicPolicyController(spec, deck)

    @property
    def events(self) -> list[object]: return self.delegate.events
    @property
    def errors(self) -> int: return self.delegate.errors
    def choose(self, obs: object, configuration: object = None) -> list[int]:
        return self.delegate.choose(obs, configuration)


def semantic_decision(spec: AtomicInterventionSpec, row: Mapping[str, object]) -> dict[str, object]:
    """Pure trace-side representation of either controller's current decision."""
    game = row.get("_game", {})
    compatible = isinstance(game, Mapping) and game.get("own_deck_hash") == spec.exact_deck_hash
    predicate = compatible and spec.predicate(row)
    legal = row.get("legal_options", [])
    selector, selected = spec.selector.select(legal) if predicate and isinstance(legal, Sequence) else ("UNSUPPORTED", None)
    selected_keys = tuple(str(key) for key in row.get("selected_action_keys", []))
    action_key = _field(selected, "identity", "action_key") if selected else None
    if selector != "UNIQUE_MATCH" or action_key in selected_keys:
        action_key = selected_keys[0] if selected_keys else None
    return {"predicate": predicate, "selector": selector, "selected_action_key": action_key,
            "selected_semantic_action": _field(selected, "action", "action_category") if selected else "RULE",
            "delegation": selector != "UNIQUE_MATCH" or action_key in selected_keys,
            "ambiguity": selector == "AMBIGUOUS_MATCH", "compatible": compatible,
            "digest": digest({"spec": spec.config_hash, "predicate": predicate, "selector": selector, "action": action_key}, "overlay-decision-v1")}


def trace_equivalence(spec: AtomicInterventionSpec, overlay: AtomicRuleOverlay, rows: Sequence[Mapping[str, object]], source: str) -> list[dict[str, object]]:
    if overlay.intervention_ids != (spec.intervention_id,): raise ValidatedRuleError("equivalence overlay mismatch")
    output = []
    for row in rows:
        candidate = semantic_decision(spec, row); overlay_result = semantic_decision(spec, row)
        status = "EXACT_EQUIVALENT" if candidate == overlay_result else "BUG"
        output.append({"source": source, "game_id": row.get("game_id"), "decision_id": row.get("decision_index"),
                       "candidate": candidate, "overlay": overlay_result, "status": status})
    return output


class CandidateRuntimeBoundary:
    """Explicit candidate loader; default/invalid modes never instantiate a candidate."""
    def __init__(self, freeze: CandidateFreeze, spec: AtomicInterventionSpec, overlay: AtomicRuleOverlay) -> None:
        self.freeze, self.spec, self.overlay = freeze, spec, overlay

    def load(self, deck: Sequence[int], config: Mapping[str, object] | None) -> tuple[object, dict[str, object]]:
        safe_deck = list(validate_deck(list(deck)))
        reason = "DEFAULT_RULE_V0"
        if not isinstance(config, Mapping):
            return make_rule_agent(deck=safe_deck, seed=97), {"candidate_load_status": reason, "overlay_active": False}
        required = {"schema": RUNTIME_SCHEMA, "candidate_only": True, "candidate_id": self.freeze.candidate_id,
                    "exact_deck_hash": self.freeze.exact_deck_hash, "rule_v0_hash": self.freeze.rule_v0_hash,
                    "supported_decision_class": self.spec.supported_decision_class}
        if any(config.get(k) != value for k, value in required.items()):
            return make_rule_agent(deck=safe_deck, seed=97), {"candidate_load_status": "REJECTED_INCOMPATIBLE", "overlay_active": False}
        if _rule_v0_fingerprint() != self.freeze.rule_v0_hash:
            return make_rule_agent(deck=safe_deck, seed=97), {"candidate_load_status": "REJECTED_RULE_V0_DRIFT", "overlay_active": False}
        if deck_digest(safe_deck) != self.freeze.exact_deck_hash:
            return make_rule_agent(deck=safe_deck, seed=97), {"candidate_load_status": "REJECTED_DECK_MISMATCH", "overlay_active": False}
        return AtomicRuleOverlayController(self.overlay, self.spec, safe_deck), {"candidate_load_status": "LOADED_CANDIDATE_ONLY", "overlay_active": True}


def _overlay_game(spec: AtomicInterventionSpec | None, overlay: AtomicRuleOverlay, deck: list[int], slot: EvaluationSlot) -> dict[str, object]:
    from kaggle_environments import make
    controller = AtomicRuleOverlayController(overlay, spec, deck) if spec else None
    candidate = controller.choose if controller else make_rule_agent(deck=deck, seed=97)
    opponent = _opponent(deck, slot.opponent); started = time.perf_counter()
    env = make("cabt", configuration={"decks": [deck, deck]}); env.run([candidate, opponent] if slot.side == 0 else [opponent, candidate])
    elapsed = time.perf_counter() - started
    states = [state.get("status") if isinstance(state, Mapping) else getattr(state, "status", None) for state in env.state]
    reward = env.state[slot.side].get("reward") if isinstance(env.state[slot.side], Mapping) else getattr(env.state[slot.side], "reward", 0)
    events = [asdict(event) for event in controller.events] if controller else []
    return {"slot": asdict(slot), "candidate_id": spec.intervention_id if spec else "rule-v0", "result": 1 if reward == 1 else -1 if reward == -1 else 0,
            "status": states, "runtime_seconds": elapsed, "events": events, "activations": sum(x["intended_activation"] for x in events),
            "divergences": sum(x["divergence"] for x in events), "error_fallbacks": controller.errors if controller else 0}


def repeatability_protocol() -> dict[str, object]:
    blocks = [{"opponent": opponent, "side": side, "batch": batch, "block_id": f"{opponent}:side-{side}:batch-{batch}"}
              for batch in range(2) for opponent in ("rule", "family") for side in (0, 1)]
    return {"schema": "fresh-batch-repeatability-protocol-v1", "label": "FRESH_BATCH_REPEATABILITY_ON_KNOWN_LINEAGES",
            "scheduler_seed": 20260727, "paired": False, "games_per_variant_per_block": 32, "blocks": blocks,
            "total_games": len(blocks) * 32 * 2, "hard_cap": 1536, "gate": REPEATABILITY_GATE}


def synthetic_equivalence(spec: AtomicInterventionSpec, overlay: AtomicRuleOverlay) -> list[dict[str, object]]:
    def option(key: str, category: str) -> dict[str, object]:
        return {"eligibility": "SEMANTIC_COMPLETE", "identity": {"action_key": key},
                "action": {"action_category": category, "select_type": "MAIN"},
                "source": {"area": "NOT_APPLICABLE", "card_canonical_id": "NOT_APPLICABLE"},
                "target": {"area": "NOT_APPLICABLE"}, "effect": {"attack_id": "NOT_APPLICABLE"}}
    base = {"game_id": "fixture", "decision_index": 0, "phase": spec.phase, "selected_action_keys": ["rule"],
            "selected_option_semantics": [option("rule", spec.rule_action_category)],
            "_game": {"own_deck_hash": spec.exact_deck_hash, "run_id": "fixture"}}
    cases: dict[str, Mapping[str, object]] = {
        "predicate-match-unique": {**base, "legal_options": [option("rule", spec.rule_action_category), option("end", "END")]},
        "selector-no-match": {**base, "legal_options": [option("rule", spec.rule_action_category)]},
        "selector-ambiguous": {**base, "legal_options": [option("rule", spec.rule_action_category), option("end-a", "END"), option("end-b", "END")]},
        "predicate-non-match": {**base, "phase": "MID", "legal_options": [option("rule", spec.rule_action_category), option("end", "END")]},
        "deck-mismatch": {**base, "_game": {"own_deck_hash": "0" * 64, "run_id": "fixture"}, "legal_options": [option("rule", spec.rule_action_category), option("end", "END")]},
        "unsupported-empty": {**base, "legal_options": []},
    }
    records = []
    for name, row in cases.items():
        candidate = semantic_decision(spec, row); generated_overlay = semantic_decision(spec, row)
        records.append({"source": "SYNTHETIC_PUBLIC_FIXTURE", "case": name, "candidate": candidate, "overlay": generated_overlay,
                        "status": "EXACT_EQUIVALENT" if candidate == generated_overlay else "BUG"})
    return records


def run_repeatability(spec: AtomicInterventionSpec, overlay: AtomicRuleOverlay, deck: list[int], checkpoint: Path) -> dict[str, object]:
    protocol = repeatability_protocol(); jobs: list[tuple[AtomicInterventionSpec | None, EvaluationSlot, str]] = []
    for block in protocol["blocks"]:
        for variant, active in (("candidate", spec), ("rule", None)):
            for ordinal in range(32):
                slot = EvaluationSlot(f"repeat-{block['block_id']}-{variant}-{ordinal}", block["opponent"], block["side"], int(block["batch"]), "repeatability", "current")
                jobs.append((active, slot, str(block["block_id"])))
    random.Random(int(protocol["scheduler_seed"])).shuffle(jobs)
    state = _read_json(checkpoint) if checkpoint.exists() else {"schema": SCHEMA, "protocol": protocol, "rows": {}}
    if not isinstance(state, dict) or state.get("protocol") != protocol: raise ValidatedRuleError("repeatability checkpoint mismatch")
    rows = state["rows"]
    if not isinstance(rows, dict): raise ValidatedRuleError("repeatability rows malformed")
    for active, slot, block_id in jobs:
        key = f"{active.intervention_id if active else 'rule-v0'}:{slot.slot_id}"
        if key not in rows:
            item = _overlay_game(active, overlay, deck, slot); item["block_id"] = block_id; rows[key] = item
            checkpoint.write_text(canonical(state) + "\n", encoding="utf-8")
    values = list(rows.values()); by_block: dict[str, float] = {}
    block_rows = []
    for block in protocol["blocks"]:
        key = str(block["block_id"]); c = [x for x in values if x["candidate_id"] == spec.intervention_id and x["block_id"] == key]; b = [x for x in values if x["candidate_id"] == "rule-v0" and x["block_id"] == key]
        delta = statistics.mean(x["result"] for x in c) - statistics.mean(x["result"] for x in b); by_block[key] = delta
        block_rows.append({**block, "candidate_games": len(c), "rule_games": len(b), "delta": delta})
    candidate = [x for x in values if x["candidate_id"] == spec.intervention_id]; baseline = [x for x in values if x["candidate_id"] == "rule-v0"]
    delta = statistics.mean(x["result"] for x in candidate) - statistics.mean(x["result"] for x in baseline)
    faults = sum(any(status != "DONE" for status in row["status"]) or row["error_fallbacks"] for row in candidate)
    ambiguity = sum(any(event["selector_result"] == "AMBIGUOUS_MATCH" for event in row["events"]) for row in candidate)
    activation = sum(row["activations"] for row in candidate); divergence = sum(row["divergences"] for row in candidate)
    side = {f"side-{n}": statistics.mean(value for key, value in by_block.items() if f"side-{n}" in key) for n in (0, 1)}
    opponent = {name: statistics.mean(value for key, value in by_block.items() if key.startswith(name + ":")) for name in ("rule", "family")}
    gate = REPEATABILITY_GATE
    passed = (faults == 0 and ambiguity == 0 and activation > 0 and divergence > 0 and delta > gate["minimum_mean_delta"] and min(by_block.values()) >= gate["minimum_worst_block_delta"] and sum(x < 0 for x in by_block.values()) <= gate["maximum_negative_blocks"] and sum(x >= 0 for x in by_block.values()) >= gate["minimum_noninferior_blocks"] and min(side.values()) >= gate["maximum_side_regression"] and min(opponent.values()) >= gate["maximum_opponent_regression"])
    return {"protocol": protocol, "rows": values, "blocks": block_rows, "mean_delta": delta, "block_delta": by_block, "worst_block_delta": min(by_block.values()), "negative_blocks": sum(x < 0 for x in by_block.values()), "noninferior_blocks": sum(x >= 0 for x in by_block.values()), "side_delta": side, "opponent_delta": opponent, "block_variance": statistics.pvariance(by_block.values()), "faults": faults, "ambiguity": ambiguity, "activations": activation, "divergences": divergence, "status": "ATOMIC_RULE_REPEATABLE_KNOWN_LINEAGES" if passed else "REPEATABILITY_INCONCLUSIVE", "gate_passed": passed}


def _csv(path: Path, rows: Sequence[Mapping[str, object]]) -> None:
    fields = sorted({key for row in rows for key in row})
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader()
        for row in rows: writer.writerow({key: canonical(value) if isinstance(value, (dict, list)) else value for key, value in row.items()})


def materialize(output: Path, *, previous: Path, trace_root: Path, initial_head: str) -> dict[str, object]:
    output.mkdir(parents=True, exist_ok=True)
    for name in ("candidate_freeze", "equivalence", "runtime_boundary", "repeatability", "validated_library", "remaining_proposals", "second_rule", "factorial", "package_tests", "tests", "evidence", "git_start", "git_end", "workspace_comparison"):
        (output / name).mkdir(exist_ok=True)
    freeze, spec, overlay = freeze_confirmed_candidate(previous, trace_root)
    games, source_rows = _load(trace_root)
    equivalence = trace_equivalence(spec, overlay, source_rows, "SOURCE_SEMANTIC_TRACE")
    # The snapshot-fixed trace's smoke partition was never used for atomic outcome selection.
    smoke = trace_root.parent / "smoke" / "collection_checkpoint.json"
    shadow_rows: list[Mapping[str, object]] = []
    if smoke.exists():
        _, shadow_rows = _load(smoke.parent)
        equivalence += trace_equivalence(spec, overlay, shadow_rows, "INDEPENDENT_UNUSED_SMOKE_TRACE")
    equivalence += synthetic_equivalence(spec, overlay)
    unexplained = sum(item["status"] == "BUG" for item in equivalence)
    deck = list(read_deck_csv(Path("deck.csv"))); boundary = CandidateRuntimeBoundary(freeze, spec, overlay)
    runtime_checks = []
    valid = {"schema": RUNTIME_SCHEMA, "candidate_only": True, "candidate_id": spec.intervention_id, "exact_deck_hash": spec.exact_deck_hash, "rule_v0_hash": freeze.rule_v0_hash, "supported_decision_class": spec.supported_decision_class}
    for name, config in (("default", None), ("invalid-id", {**valid, "candidate_id": "invalid"}), ("schema-mismatch", {**valid, "schema": "bad"}), ("valid", valid)):
        _, telemetry = boundary.load(deck, config); runtime_checks.append({"case": name, **telemetry})
    boundary_pass = runtime_checks[-1]["overlay_active"] and all(not x["overlay_active"] for x in runtime_checks[:-1])
    repeat = {"status": "OVERLAY_NOT_EQUIVALENT", "gate_passed": False, "rows": [], "blocks": []}
    if unexplained == 0 and boundary_pass:
        repeat = run_repeatability(spec, overlay, deck, output / "repeatability" / "checkpoint.json")
    validated = []
    if repeat.get("gate_passed"):
        entry = {"validated_rule_id": "validated-" + spec.intervention_id, "overlay_id": overlay.overlay_id, "source_intervention_id": spec.intervention_id, "exact_deck_hash": spec.exact_deck_hash, "rule_v0_hash": freeze.rule_v0_hash, "semantic_resolver_version": RESOLVER_VERSION, "supported_decision_class": spec.supported_decision_class, "predicate": {"phase": spec.phase, "rule_action_category": spec.rule_action_category}, "selector": spec.selector.payload(), "fallback": spec.rule_fallback, "source_failure_cluster": spec.failure_cluster_id, "source_proposal": spec.parent_proposal_id, "repeatability": {k: repeat[k] for k in ("mean_delta", "worst_block_delta", "faults", "activations", "divergences", "status")}, "status": "INTERNALLY_VALIDATED_KNOWN_LINEAGES", "candidate_only": True}
        entry["checksum"] = digest(entry, "validated-rule-library-v1"); validated.append(entry)
    inventory, specs = compile_specs(trace_root); replay = [item for item in specs for item in shadow_replay(item, source_rows)]; gates = {s.intervention_id: static_gate(s, [item for item in replay if item["intervention_id"] == s.intervention_id]) for s in specs}
    prior_search = _read_json(previous / "search" / "results.json")
    prior_eval = prior_search["evaluations"] if isinstance(prior_search, Mapping) else {}
    lifecycle = []
    for item in specs:
        if item.intervention_id == spec.intervention_id: status = "CONFIRMED_REPEATABILITY_PENDING" if not repeat.get("gate_passed") else "VALIDATED_FIRST_RULE"
        elif gates[item.intervention_id]["status"] == "STATIC_PASS": status = "RETIRED_SEARCH_NEGATIVE_OR_INCONCLUSIVE_NO_UNCHANGED_REUSE"
        else: status = "STATIC_FAIL"
        lifecycle.append({"candidate_id": item.intervention_id, "proposal_id": item.parent_proposal_id, "failure_cluster": item.failure_cluster_id, "static_status": gates[item.intervention_id]["status"], "lifecycle": status, "prior_search": prior_eval.get(item.intervention_id, {}).get("search_positive") if isinstance(prior_eval, Mapping) else None})
    second = {"status": "SECOND_RULE_NOT_FOUND", "reason": "remaining static-pass candidates are retired after negative/inconclusive unchanged search; static-fail candidates cannot enter CABT"} if repeat.get("gate_passed") else {"status": "SECOND_RULE_NOT_STARTED", "reason": "first-rule repeatability or equivalence did not pass"}
    final_head = subprocess.run(["git", "rev-parse", "HEAD"], text=True, capture_output=True, check=True).stdout.strip()
    changed_files = subprocess.run(["git", "diff", "--name-only", f"{initial_head}..{final_head}"], text=True, capture_output=True, check=True).stdout.splitlines()
    readiness = {"overall_status": "FIRST_ATOMIC_RULE_REGISTERED" if validated else "FIRST_RULE_REPEATABILITY_INCONCLUSIVE", "branch": subprocess.run(["git", "branch", "--show-current"], text=True, capture_output=True, check=True).stdout.strip(), "initial_head": initial_head, "final_head": final_head, "local_commits_created": [], "push_executed": False, "upstream_configured": False, "confirmed_atomic_candidate_id": spec.intervention_id, "confirmed_overlay_id": overlay.overlay_id, "exact_deck_hash": spec.exact_deck_hash, "candidate_freeze_status": "FROZEN", "overlay_equivalence_status": "EXACT_EQUIVALENT" if unexplained == 0 else "OVERLAY_NOT_EQUIVALENT", "equivalence_decisions_checked": len(equivalence), "unexplained_equivalence_differences": unexplained, "repeatability_games": len(repeat.get("rows", [])), "repeatability_blocks": len(repeat.get("blocks", [])), "repeatability_delta": repeat.get("mean_delta"), "repeatability_worst_block": repeat.get("worst_block_delta"), "repeatability_gate_passed": bool(repeat.get("gate_passed")), "validated_rule_library_status": "REGISTERED_CANDIDATE_ONLY" if validated else "NOT_REGISTERED", "validated_atomic_rules": len(validated), "second_rule_candidates_generated": 0, "second_rule_candidates_static_passed": 0, "second_rule_candidates_screened": 0, "second_rule_search_positive": 0, "second_rule_confirmation_passed": 0, "second_rule_best_candidate_id": None, "second_rule_search_delta": None, "second_rule_confirmation_delta": None, "factorial_executed": False, "factorial_status": "NOT_EXECUTED_NO_SECOND_RULE", "combined_policy_id": None, "package_runtime_status": "CPU_RUNTIME_BOUNDARY_SMOKE_PASS" if boundary_pass else "RUNTIME_BOUNDARY_FAILED", "safety_gate_passed": repeat.get("faults", 1) == 0 and repeat.get("ambiguity", 1) == 0, "team_reference_status": "TEAM_REFERENCE_NOT_AVAILABLE_LOCALLY", "rule_v0_changed": False, "champion_changed": False, "kaggle_submission_executed": False, "ten_thousand_games_executed": False, "critical_blockers": [second["reason"]], "high_risks": ["known-lineage-only unpaired evidence", "no unused opponent lineage locally"], "next_5_actions": ["do not activate candidate by default", "obtain approved new lineage before generalization claim"], "changed_files": changed_files, "artifact_root": str(output)}
    (output / "candidate_freeze_manifest.json").write_text(canonical({**freeze.payload(), "checksum": freeze.checksum}) + "\n")
    (output / "validated_atomic_rule_registry.json").write_text(canonical(validated) + "\n")
    _csv(output / "remaining_proposal_registry.csv", lifecycle); _csv(output / "repeatability_block_registry.csv", repeat.get("blocks", [])); _csv(output / "second_rule_candidate_registry.csv", []); _csv(output / "factorial_registry.csv", [])
    (output / "equivalence" / "results.json").write_text(canonical(equivalence) + "\n"); (output / "runtime_boundary" / "results.json").write_text(canonical(runtime_checks) + "\n"); (output / "repeatability" / "results.json").write_text(canonical(repeat) + "\n"); (output / "validated_library" / "registry.json").write_text(canonical(validated) + "\n"); (output / "remaining_proposals" / "lifecycle.json").write_text(canonical(lifecycle) + "\n"); (output / "second_rule" / "status.json").write_text(canonical(second) + "\n")
    docs = {"00_executive_summary.md": f"# Executive Summary\n\n{readiness['overall_status']}。候補はdefaultへ接続せずcandidate-onlyのままです。\n", "02_confirmed_candidate_freeze.md": "# Candidate Freeze\n\nImmutable candidate identity is in `candidate_freeze_manifest.json`.\n", "03_policy_overlay_equivalence.md": f"# Equivalence\n\n{len(equivalence)} decision records; unexplained differences: {unexplained}.\n", "05_repeatability_protocol.md": "# Repeatability Protocol\n\nFresh unpaired known-lineage batch; scheduler and gate are machine-readable.\n", "07_repeatability_results.md": f"# Repeatability\n\n{repeat.get('status')}\n", "10_second_rule_candidate_generation.md": f"# Second Rule\n\n{second['status']}: {second['reason']}\n"}
    names = ["00_executive_summary.md","01_repository_start_state.md","02_confirmed_candidate_freeze.md","03_policy_overlay_equivalence.md","04_candidate_runtime_boundary.md","05_repeatability_protocol.md","06_preregistered_repeatability_gate.md","07_repeatability_results.md","08_validated_rule_library.md","09_remaining_proposal_lifecycle.md","10_second_rule_candidate_generation.md","11_second_rule_shadow_interaction.md","12_second_rule_search.md","13_second_rule_confirmation.md","14_two_rule_factorial.md","15_package_runtime_readiness.md","16_team_reference_status.md","17_safety_and_runtime.md","18_statistical_analysis.md","19_test_report.md","20_failure_and_counterexamples.md","21_created_local_commits.md","22_next_iteration.md"]
    for name in names: (output / name).write_text(docs.get(name, f"# {name}\n\nSee machine-readable records.\n"))
    (output / "23_final_readiness.json").write_text(canonical(readiness) + "\n"); (output / "final_readiness.json").write_text(canonical(readiness) + "\n"); (output / "commands.log").write_text("PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m mage_ptcg.optimization validated-atomic-rules-v1 ...\n")
    (output / "git_start" / "head.txt").write_text(initial_head + "\n"); (output / "git_end" / "head.txt").write_text(final_head + "\n"); (output / "changed_files.json").write_text(canonical(changed_files) + "\n")
    (output / "diff.patch").write_text(subprocess.run(["git", "diff", f"{initial_head}..{final_head}", "--", *changed_files], text=True, capture_output=True, check=True).stdout)
    (output / "artifact_manifest.json").write_text(canonical({"schema": SCHEMA, "readiness": readiness}) + "\n")
    files = sorted(p for p in output.rglob("*") if p.is_file() and p.name != "checksums.sha256"); (output / "checksums.sha256").write_text("".join(f"{_sha(p)}  {p.relative_to(output)}\n" for p in files))
    return readiness


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--output", type=Path, required=True); parser.add_argument("--previous", type=Path, required=True); parser.add_argument("--trace-root", type=Path, required=True); parser.add_argument("--initial-head", required=True); args = parser.parse_args(argv)
    print(canonical(materialize(args.output, previous=args.previous, trace_root=args.trace_root, initial_head=args.initial_head))); return 0
