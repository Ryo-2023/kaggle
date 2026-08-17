"""Exact-deck policy binding and independent deck-specialist confirmation."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import argparse
import json
from pathlib import Path
import statistics
from typing import Mapping, Sequence

from main import make_rule_agent, read_deck_csv, validate_deck

from .core import canonical, digest
from .outcome import deck_digest, mutate_deck
from .sparse import (PRE_REGISTERED, SparsePolicyParameters, SparseProposalController,
                     ablation_population, evaluate_pair)

SCHEMA = "deck-specialized-policy-confirmation-v1"
CONFIRMATION_GATE = {"blocks": 8, "games_per_block": 32, "min_divergence": .01, "max_divergence": .20, "max_worst_block_regression": .25, "min_noninferior_blocks": 5, "safety_faults": 0}


class DeckSpecialistError(ValueError): pass


@dataclass(frozen=True)
class DeckCompatibility:
    level: str
    exact_deck_hash: str
    family: str
    variant: str
    required_cards: tuple[int, ...]
    required_package: tuple[int, ...]
    runtime_schema: str = "robust-sparse-policy-v2"

    def validate(self, deck: Sequence[int], *, runtime_schema: str) -> tuple[bool, str]:
        if self.level != "EXACT_DECK_ONLY": raise DeckSpecialistError("only exact-deck binding is allowed at this stage")
        if runtime_schema != self.runtime_schema: return False, "RUNTIME_SCHEMA_MISMATCH"
        if deck_digest(deck) != self.exact_deck_hash: return False, "DECK_HASH_MISMATCH"
        if not set(self.required_cards).issubset(deck) or not set(self.required_package).issubset(deck): return False, "REQUIRED_PACKAGE_MISSING"
        return True, "COMPATIBLE"


@dataclass(frozen=True)
class JointCandidate:
    joint_candidate_id: str
    deck_id: str
    deck_hash: str
    deck_family: str
    deck_variant: str
    required_card_ids: tuple[int, ...]
    required_package_ids: tuple[int, ...]
    policy_id: str
    policy_hash: str
    rule_overlay_hash: str | None
    opponent_posterior_config_hash: str
    runtime_config_hash: str
    proposal_source_versions: Mapping[str, str]
    compatibility: DeckCompatibility
    source_iteration: str
    parent_candidate: str | None
    provenance: Mapping[str, object] = field(default_factory=dict)

    @property
    def joint_hash(self) -> str: return digest(asdict(self), SCHEMA)


def current_policy(deck: list[int]) -> SparsePolicyParameters:
    source = Path("/home/bfe-lab-ono/kaggle/handoff-artifacts/robust-sparse-policy-optimization-v2-20260725_204500/evaluation_blocks/checkpoint.json")
    state = json.loads(source.read_text(encoding="utf-8")); return SparsePolicyParameters.from_payload(state["configs"]["sparse-cem-b-00"])


def joint_candidate(deck: list[int], *, deck_id: str, policy: SparsePolicyParameters, overlay_hash: str | None = None) -> JointCandidate:
    compat = DeckCompatibility("EXACT_DECK_ONLY", deck_digest(deck), "MEGA_ABOMASNOW_EX", "CURRENT" if deck_id == "current" else "MUTATION_3_TO_721", (722, 723), (3,))
    runtime = digest({"schema": "robust-sparse-policy-v2", "cooldown": policy.override_cooldown_decisions, "budget": policy.maximum_overrides_per_game}, "runtime")
    return JointCandidate(f"{deck_id}--{policy.candidate_id}", deck_id, deck_digest(deck), "MEGA_ABOMASNOW_EX", compat.variant, (722, 723), (3,), policy.candidate_id, policy.config_hash, overlay_hash, digest({"min_posterior": policy.minimum_posterior_confidence}, "posterior"), runtime, {"rule": "rule-v0", "family": "config-driven-family-v1"}, compat, "deck-specialist-v1", policy.parent_id, {"policy_source": "robust-sparse-v2"})


class DeckBoundPolicy:
    """Compatibility guard: mismatch is planned Rule delegation, never error."""
    def __init__(self, policy: SparsePolicyParameters, deck: list[int], compatibility: DeckCompatibility) -> None:
        self.policy = policy; self.deck = list(validate_deck(deck)); self.compatibility = compatibility; self.compatible, self.reason = compatibility.validate(self.deck, runtime_schema="robust-sparse-policy-v2")
        self.rule = make_rule_agent(deck=self.deck, seed=53); self.controller = SparseProposalController(policy, self.deck) if self.compatible else None
        self.compatibility_rejections = 0

    def choose(self, observation: object, configuration: object = None) -> list[int]:
        if self.compatible: return self.controller.choose(observation, configuration)  # type: ignore[union-attr]
        if isinstance(observation, Mapping) and observation.get("select") is not None: self.compatibility_rejections += 1
        return self.rule(observation, configuration)


@dataclass(frozen=True)
class DeckSpecificOverlay:
    overlay_id: str
    exact_deck_hash: str
    source_policy_id: str
    source_policy_hash: str
    phase: tuple[str, ...]
    action_types: tuple[str, ...]
    support: int
    runtime_guard: str = "EXACT_DECK_ONLY"

    @property
    def overlay_hash(self) -> str: return digest(asdict(self), "deck-specific-overlay-v1")

    def bind(self, deck: list[int], policy: SparsePolicyParameters) -> DeckBoundPolicy:
        compat = DeckCompatibility("EXACT_DECK_ONLY", self.exact_deck_hash, "MEGA_ABOMASNOW_EX", "CURRENT", (722, 723), (3,))
        return DeckBoundPolicy(policy, deck, compat)


def compile_overlay(deck: list[int], policy: SparsePolicyParameters, *, support: int) -> DeckSpecificOverlay:
    return DeckSpecificOverlay("overlay-current-opening-family-v1", deck_digest(deck), policy.candidate_id, policy.config_hash, policy.allowed_phase_buckets, policy.allowed_action_types, support)


def _checkpoint(path: Path) -> dict[str, object]: return json.loads(path.read_text()) if path.exists() else {"schema": SCHEMA, "gate": CONFIRMATION_GATE, "evaluations": {}, "completed": []}
def _save(path: Path, value: Mapping[str, object]) -> None: path.write_text(canonical(value) + "\n")


def run_stage(output: Path, *, stage: str, index: int | None = None) -> dict[str, object]:
    output.mkdir(parents=True, exist_ok=True); path = output / "checkpoint.json"; state = _checkpoint(path); deck = list(read_deck_csv(Path("deck.csv"))); policy = current_policy(deck)
    if stage not in {"confirmation", "mutation-search"}: raise DeckSpecialistError("unknown stage")
    ev = state["evaluations"]; assert isinstance(ev, dict)
    def record(key: str, params: SparsePolicyParameters, cards: list[int], deck_id: str) -> None:
        if key not in ev: ev[key] = evaluate_pair(params, cards, split=stage, block_id=key); _save(path, state)
    if stage == "confirmation":
        selected = range(CONFIRMATION_GATE["blocks"]) if index is None else (index,)
        for block in selected: record(f"confirm-{block}", policy, deck, "current")
        rows = [ev[f"confirm-{i}"] for i in range(CONFIRMATION_GATE["blocks"]) if f"confirm-{i}" in ev]
        if len(rows) == CONFIRMATION_GATE["blocks"]:
            state["confirmation_gate"] = all(row["safety_pass"] and row["effective_policy_pass"] and float(row["delta"]) >= -CONFIRMATION_GATE["max_worst_block_regression"] for row in rows) and sum(float(row["delta"]) >= 0 for row in rows) >= CONFIRMATION_GATE["min_noninferior_blocks"]
            if "confirmation" not in state["completed"]: state["completed"].append("confirmation")
    else:
        if not state.get("confirmation_gate"): state["mutation_status"] = "DEFERRED_CURRENT_NOT_CONFIRMED"; _save(path, state); return state
        cards = mutate_deck(deck); base = ablation_population(cards)
        # Mutation-specific sparse candidates are deck-bound; no current-deck policy is forced here.
        candidates = [SparsePolicyParameters.from_payload(row.payload() | {"candidate_id": f"mutation-{row.candidate_id}", "deck_id": "mutation-3-to-721", "deck_hash": deck_digest(cards), "parent_id": None}) for row in base[:4]]
        selected = range(4) if index is None else (index,)
        for item in selected: record(f"mutation-{item}", candidates[item], cards, "mutation-3-to-721")
        if all(f"mutation-{i}" in ev for i in range(4)):
            state["mutation_status"] = "DECK_SPECIALIST_CANDIDATE"; state["mutation_best"] = max((ev[f"mutation-{i}"] for i in range(4)), key=lambda row: float(row["delta"]))["candidate_id"]
            if "mutation-search" not in state["completed"]: state["completed"].append("mutation-search")
    _save(path, state); return state


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--output", type=Path, required=True); parser.add_argument("--stage", choices=("confirmation", "mutation-search"), required=True); parser.add_argument("--index", type=int)
    args = parser.parse_args(argv); state = run_stage(args.output, stage=args.stage, index=args.index); print(canonical({"stage": args.stage, "completed": state["completed"], "evaluations": len(state["evaluations"]), "confirmation_gate": state.get("confirmation_gate"), "mutation_status": state.get("mutation_status")})); return 0
