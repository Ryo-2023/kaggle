from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "curate_team_knowledge", ROOT / "scripts" / "curate_team_knowledge.py"
)
assert SPEC is not None and SPEC.loader is not None
CURATOR = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = CURATOR
SPEC.loader.exec_module(CURATOR)

SOURCE = ROOT / "artifacts" / "team-knowledge-mining"
OUTPUT = ROOT / "artifacts" / "team-knowledge-curated"


def rows(filename: str) -> list[dict]:
    return CURATOR.read_jsonl(OUTPUT / filename)


def test_curated_outputs_pass_all_integrity_checks() -> None:
    validation = CURATOR.validate_outputs(OUTPUT)
    assert all(validation.values()), validation


def test_stage1_inputs_match_recorded_hashes() -> None:
    summary = json.loads((OUTPUT / "summary.json").read_text(encoding="utf-8"))
    actual = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(SOURCE.iterdir())
        if path.is_file()
    }
    assert summary["source_file_sha256"] == actual


def test_all_49_placeholder_sources_are_processed_without_placeholder_text() -> None:
    policies = rows("../team-knowledge-mining/policy_rules.jsonl")
    placeholder_ids = {
        row["rule_id"]
        for row in policies
        if row["preferred_action"] == CURATOR.PLACEHOLDER
    }
    canonical = rows("canonical_rules.jsonl")
    hard = rows("hard_constraints.jsonl")
    rejected = rows("rejected_or_quarantined.jsonl")
    processed = {
        source_id
        for collection in (canonical, hard, rejected)
        for row in collection
        for source_id in row.get("source_policy_rule_ids", [])
    }
    assert len(placeholder_ids) == 49
    assert placeholder_ids <= processed
    for filename in CURATOR.OUTPUT_FILES:
        assert CURATOR.PLACEHOLDER not in (OUTPUT / filename).read_text(encoding="utf-8")


def test_deck_variants_merge_aliases_and_exclude_invalid_fixture() -> None:
    decks = rows("deck_variants.jsonl")
    assert len(decks) == len({row["content_sha256"] for row in decks})
    assert all(row["card_count"] == 60 for row in decks if row["training_eligible"])

    jumbo = next(row for row in decks if row["version_name"] == "alakazam_control_jumbo_v2_20260712")
    policies = {row["policy"] for row in jumbo["branch_commit_policy_versions"]}
    assert {"ruruko_v0_snapshot", "experiment_a_same_deck"} <= policies

    baseline = next(row for row in decks if row["version_name"] == "rule_agent_v0_official_sample_shared_baseline")
    source_ids = {alias["source_deck_id"] for alias in baseline["aliases"] if "source_deck_id" in alias}
    assert {"DECK-000006", "DECK-000019"} <= source_ids

    rejected = rows("rejected_or_quarantined.jsonl")
    invalid_sources = {
        source_id
        for row in rejected
        if row["decision"] == "REJECT_INVALID_FIXTURE"
        for source_id in row["source_item_ids"]
    }
    assert "DECK-000009" in invalid_sources


def test_teacher_registry_has_executable_scope_condition_and_evidence() -> None:
    teachers = rows("executable_teacher_registry.jsonl")
    canonical_ids = {row["rule_id"] for row in rows("canonical_rules.jsonl")}
    macro_ids = {row["macro_id"] for row in rows("macros.jsonl")}
    assert teachers
    for teacher in teachers:
        assert teacher["canonical_rule_id"] in canonical_ids | macro_ids
        assert teacher["deck_scope"]
        assert teacher["observable_condition"]
        assert teacher["source_evidence_ids"]
        assert teacher["score_formula"] or teacher["priority"]
        assert teacher["decision"] == "ACCEPT_TEACHER_RULE"


def test_every_teacher_decision_is_registered() -> None:
    teachers = rows("executable_teacher_registry.jsonl")
    accepted = {
        row[id_key]
        for filename, id_key in (
            ("canonical_rules.jsonl", "rule_id"),
            ("combos.jsonl", "combo_id"),
            ("macros.jsonl", "macro_id"),
            ("matchup_rules.jsonl", "matchup_rule_id"),
        )
        for row in rows(filename)
        if row["decision"] == "ACCEPT_TEACHER_RULE"
    }
    registered = {row["canonical_rule_id"] for row in teachers}
    assert registered == accepted
    assert len(teachers) == len(accepted)


def test_public_opponent_decks_are_merged_as_aliases() -> None:
    decks = rows("deck_variants.jsonl")
    homes = {
        alias["source_evidence_id"]: row["deck_variant_id"]
        for row in decks
        for alias in row["aliases"]
        if alias.get("alias_type") == "public_opponent_deck"
    }
    assert homes == {
        "EV-001240": "DV-000009",
        "EV-001249": "DV-000007",
        "EV-001279": "DV-000006",
        "EV-001285": "DV-000009",
        "EV-001297": "DV-000003",
        "EV-001334": "DV-000003",
    }


def test_evaluations_are_measured_and_missing_fields_are_explained() -> None:
    evidence = {row["evidence_id"]: row for row in rows("../team-knowledge-mining/evidence.jsonl")}
    null_fields = (
        "subject_policy", "subject_deck", "baseline", "seed", "wins", "losses",
        "draws", "win_rate", "confidence_interval", "kaggle_score",
    )
    forbidden = {"EV-000073", *{f"EV-{number:06d}" for number in range(1845, 1854)}}
    for evaluation in rows("evaluation_registry.jsonl"):
        missing = evaluation["unavailable_reason"]
        for field in null_fields:
            if evaluation[field] is None:
                assert field in missing, (evaluation["evaluation_id"], field)
        if evaluation["games"] == 0:
            assert "games" in missing
        if not evaluation["opponents"]:
            assert "opponents" in missing
        if not evaluation["failure_counts"]:
            assert "failure_counts" in missing
        assert not (set(evaluation["source_evidence_ids"]) & forbidden)
        assert all(evidence[eid]["evidence_type"] not in {"code", "commit"} for eid in evaluation["source_evidence_ids"])


def test_three_contradictions_are_held_and_not_accepted() -> None:
    contradictions = {row["contradiction_id"] for row in rows("../team-knowledge-mining/contradictions.jsonl")}
    rejected = rows("rejected_or_quarantined.jsonl")
    held = {
        source_id
        for row in rejected
        if row["item_type"] == "contradiction" and row["decision"] == "HOLD_FOR_EXPERIMENT"
        for source_id in row["source_item_ids"]
    }
    assert len(contradictions) == 3
    assert held == contradictions
    for rule in rows("canonical_rules.jsonl"):
        if set(rule["conflicts_with"]) & contradictions:
            assert rule["decision"] == "HOLD_FOR_EXPERIMENT"


def test_generation_is_byte_deterministic(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    CURATOR.generate(first)
    CURATOR.generate(second)
    assert CURATOR.output_hashes(first) == CURATOR.output_hashes(second) == CURATOR.output_hashes(OUTPUT)
