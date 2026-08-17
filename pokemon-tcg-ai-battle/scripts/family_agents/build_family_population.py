#!/usr/bin/env python3
"""Build an immutable, trusted internal-Family candidate population.

Only audited exact decks and repository-native policy code participate.  This
script never imports recovered/source-branch agents and never mutates Git.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[2]
TAXONOMY = Path("/home/bfe-lab-ono/kaggle/handoff-artifacts/deck-agent-asset-consolidation-taxonomy-v2")
DIVERSITY = Path("/home/bfe-lab-ono/kaggle/handoff-artifacts/offline-scaleup-population-diversity-expansion-v1")
APPROVAL = Path("/home/bfe-lab-ono/kaggle/handoff-artifacts/family-asset-approval-isolated-runtime-v1")


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value: object, domain: str) -> str:
    return hashlib.sha256((domain + "\0" + _canonical(value)).encode()).hexdigest()


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(_canonical(value) + "\n", encoding="utf-8")
    temp.replace(path)


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text("".join(_canonical(row) + "\n" for row in rows), encoding="utf-8")
    temp.replace(path)


def _decks() -> dict[str, list[int]]:
    registry = json.loads((TAXONOMY / "artifacts" / "deck_instance_registry.json").read_text(encoding="utf-8"))
    validity = {row["deck_id"]: row for row in json.loads((TAXONOMY / "artifacts" / "deck_validity_registry.json").read_text(encoding="utf-8"))}
    result: dict[str, list[int]] = {}
    for row in registry:
        deck_id, cards = row.get("deck_id"), row.get("cards")
        if not isinstance(deck_id, str) or validity.get(deck_id, {}).get("legality_status") != "PRIOR_CABT_VALID_EVIDENCE_MATCHED" or not isinstance(cards, list):
            continue
        expanded = [item["card_id"] for item in cards for _ in range(item["count"])]
        if len(expanded) == 60 and all(type(card) is int for card in expanded):
            result[deck_id] = expanded
    return result


def _deck_fp(cards: list[int]) -> str:
    return _digest(sorted(Counter(cards).items()), "deck-multiset")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    output = args.output_root
    artifacts, docs = output / "artifacts", output / "docs"
    config = yaml.safe_load((ROOT / "configs" / "family_population.yaml").read_text(encoding="utf-8"))
    if not isinstance(config, dict) or config.get("schema_version") != "family-population-v1" or not isinstance(config.get("families"), dict):
        raise ValueError("family population config is malformed")
    cards_by_deck = _decks()
    card_ids = {int(line.split(",", 1)[0]) for line in (ROOT / "data/raw/EN_Card_Data.csv").read_text(encoding="utf-8").splitlines()[1:] if line.split(",", 1)[0].isdigit()}
    runtime_bytes = (ROOT / "src/mage_ptcg/family_agents/runtime.py").read_bytes()
    all_families: list[dict[str, Any]] = []
    for family_id, raw in sorted(config["families"].items()):
        if not isinstance(raw, dict):
            raise ValueError(f"family config is malformed: {family_id}")
        for variant in raw.get("variants", []):
            if isinstance(variant, str):
                deck_id, energy_ids = variant, list(raw["energy_ids"])
            elif isinstance(variant, dict) and isinstance(variant.get("deck_id"), str) and isinstance(variant.get("energy_ids", raw.get("energy_ids")), list):
                deck_id, energy_ids = variant["deck_id"], list(variant.get("energy_ids", raw.get("energy_ids", [])))
            else:
                raise ValueError(f"variant config is malformed: {family_id}")
            deck = cards_by_deck.get(deck_id)
            if deck is None or set(deck) - card_ids:
                raise ValueError(f"unresolved or non-exact deck: {deck_id}")
            family_config = {"family_id": family_id, "anchor_ids": list(raw["anchor_ids"]), "basic_ids": list(raw["basic_ids"]), "energy_ids": energy_ids, "variant_id": deck_id, "variant_bonus": 0.0}
            if not set(family_config["anchor_ids"]) <= set(deck) or not set(family_config["energy_ids"]) & set(deck):
                raise ValueError(f"anchor/energy package mismatch: {family_id}/{deck_id}")
            runtime = hashlib.sha256(runtime_bytes + _canonical(family_config).encode()).hexdigest()
            all_families.append({
                "opponent_id": f"family-{family_id.lower()}-{deck_id}", "opponent_type": "FAMILY_SPECIFIC", "source_path": str(ROOT / "src/mage_ptcg/family_agents"),
                "deck_id": deck_id, "deck_fingerprint": _deck_fp(deck), "runtime_id": "family-specific-internal-v1", "runtime_fingerprint": runtime, "agent_digest": hashlib.sha256(runtime_bytes).hexdigest(),
                "validation_status": "CANDIDATE", "availability_status": "AVAILABLE", "evaluation_eligibility": "PENDING_RUNTIME_GATE", "training_eligibility": "PENDING_RUNTIME_GATE", "teacher_trust": "LIMITED", "quarantine_reason": None,
                "family_id": family_id, "strategy_tags": ["family-specific", "internal", "config-driven"], "variant_tags": [deck_id], "evidence_paths": [str(ROOT / "configs/family_population.yaml"), str(TAXONOMY / "artifacts/deck_instance_registry.json")],
                "loader": "family_specific_internal_v1", "deck_cards": deck, "provenance": {"family_config": family_config, "anchor_evidence": {"anchor_ids": family_config["anchor_ids"], "energy_ids": family_config["energy_ids"]}, "taxonomy_root": str(TAXONOMY)},
            })
    required = {"MEGA_LUCARIO_EX", "MEGA_ABOMASNOW_EX", "ALAKAZAM", "MEGA_KANGASKHAN_EX", "ARCHALUDON_EX"}
    if {entry["family_id"] for entry in all_families} != required:
        raise ValueError("configured Family set is incomplete")
    old = json.loads((DIVERSITY / "artifacts/expanded_population_snapshot.json").read_text(encoding="utf-8"))["entries"]
    rules = [entry for entry in old if entry.get("opponent_type") == "RULE_V0_DECK"][:2]
    teams = [entry for entry in old if entry.get("opponent_type") == "TEAM_NATIVE"]
    if len(rules) != 2 or len(teams) != 3:
        raise ValueError("expected validated rule/team population entries are unavailable")
    entries = [*rules, *teams, *all_families]
    semantic = [{key: value for key, value in entry.items() if key not in {"source_path", "evidence_paths"}} for entry in sorted(entries, key=lambda item: item["opponent_id"])]
    population = {"schema_version": "offline-scaleup-population-v2", "entries": sorted(entries, key=lambda item: item["opponent_id"]), "semantic_population_digest": _digest(semantic, "population"), "created_by": "family-population-autonomous-expansion-v1", "parent_population": "population-c2cb029f9ebeedbc"}
    population["population_id"] = "population-" + population["semantic_population_digest"][:16]
    ranking = [
        {"rank": 1, "family_id": "MEGA_KANGASKHAN_EX", "anchor_card_id": 756, "anchor_name": "Mega Kangaskhan ex", "exact_variant_count": 2, "selection": "IMPLEMENTED"},
        {"rank": 2, "family_id": "ARCHALUDON_EX", "anchor_card_id": 190, "anchor_name": "Archaludon ex", "exact_variant_count": 2, "selection": "IMPLEMENTED"},
        {"rank": 3, "family_id": "MEGA_FROSLASS_EX", "anchor_card_id": 861, "anchor_name": "Mega Froslass ex", "exact_variant_count": 1, "selection": "DEFERRED_SINGLE_VARIANT"},
        {"rank": 4, "family_id": "DWEBBLE", "anchor_card_id": 344, "anchor_name": "Dwebble", "exact_variant_count": 1, "selection": "DEFERRED_SINGLE_VARIANT"},
        {"rank": 5, "family_id": "BLOODMOON_URSALUNA", "anchor_card_id": 135, "anchor_name": "Bloodmoon Ursaluna", "exact_variant_count": 1, "selection": "DEFERRED_SINGLE_VARIANT"},
    ]
    blocked = [json.loads(line) for line in (APPROVAL / "artifacts/runtime_gate_results.jsonl").read_text(encoding="utf-8").splitlines() if line and json.loads(line).get("fault") == "DEPENDENCY_MISSING"]
    _write(artifacts / "family_candidate_ranking.json", {"candidates": ranking})
    (artifacts / "family_candidate_ranking.md").write_text("# Family候補順位\n\n1. `MEGA_KANGASKHAN_EX`: Mega Kangaskhan ex（756）、exact variant 2件。\n2. `ARCHALUDON_EX`: Archaludon ex（190）、exact variant 2件。\n\n両候補は監査済みtaxonomyの解決済みCard ID anchorとexact-60 deckだけを使う。\n", encoding="utf-8")
    _write(artifacts / "family_selection_evidence.json", {"taxonomy_root": str(TAXONOMY), "card_identity_source": str(ROOT / "data/raw/EN_Card_Data.csv"), "selected": ranking[:2]})
    _write(artifacts / "new_family_registry.json", {"families": [entry for entry in all_families if entry["family_id"] in {"MEGA_KANGASKHAN_EX", "ARCHALUDON_EX"}]})
    _write(artifacts / "family_playbook_registry.json", {"runtime": "mage_ptcg.family_agents.ConfigDrivenFamilyAgent", "families": [{"family_id": key, "config": value} for key, value in sorted(config["families"].items())]})
    _write_jsonl(artifacts / "family_variant_binding_registry.jsonl", [{"binding_status": "CANDIDATE_FAMILY_BINDING", "family_id": entry["family_id"], "deck_id": entry["deck_id"], "deck_fingerprint": entry["deck_fingerprint"], "runtime_fingerprint": entry["runtime_fingerprint"], "anchors": entry["provenance"]["anchor_evidence"]} for entry in all_families])
    _write(artifacts / "frozen_dependency_candidates.json", {"status": "DEPENDENCY_MISSING_PINNED", "candidates": blocked})
    _write(artifacts / "expanded_population.json", population)
    _write(artifacts / "expanded_teacher_registry.json", {"teachers": [entry for entry in all_families], "rule_v0_teacher_share": 0.0})
    _write(artifacts / "large_scale_start_gate.json", {"family_count": len({entry["family_id"] for entry in all_families}), "non_rule_v0_executable_candidate_count": len(teams) + len(all_families), "teacher_policy_candidate_count": len(all_families), "status": "PENDING_RUNTIME_SMOKE"})
    _write(artifacts / "final_readiness.json", {"verdict": "PENDING_INTERNAL_FAMILY_RUNTIME_GATE", "population_id": population["population_id"]})
    docs.mkdir(parents=True, exist_ok=True)
    (docs / "executive_report.md").write_text("# Family Population拡張\n\n新規Family 2件とvariant 4件は、CABT runtime gateを通過するまで候補専用である。\n", encoding="utf-8")
    (docs / "family_expansion_report.md").write_text("# Family根拠\n\n採用した新規FamilyはMega Kangaskhan exとArchaludon exである。anchorとexact deck variantは`artifacts/family_selection_evidence.json`に記録する。\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
