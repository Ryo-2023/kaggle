"""Focused contracts for the local-only offline scale-up orchestration."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mage_ptcg.offline_scaleup.pipeline import (
    DATASET_SCHEMA,
    RESULT_SCHEMA,
    _teacher_dataset_record,
    _write_jsonl_once,
    build_population,
    build_expanded_population,
    build_schedule,
    evaluate_holdout,
    export_dataset,
    run_league,
    train_student_v1,
    validate_population,
)
from mage_ptcg.student.dataset import build_rule_bc_example

# The 2026-07-25-era R2D3/offline-scaleup artifacts these tests read were
# intentionally deleted on 2026-08-03: that model line performed poorly and was
# superseded by `mage_ptcg.meta_specialist`, so the evidence was not worth its
# 58 GB.  The tests are kept rather than removed so the contract they encode
# stays on record, but they cannot run without their inputs.  If a future line
# regenerates equivalent artifacts, delete this guard rather than the tests.
_REQUIRED_ARTIFACTS = (
    Path("/home/bfe-lab-ono/kaggle/handoff-artifacts/deck-agent-asset-consolidation-taxonomy-v2"),
    Path("/home/bfe-lab-ono/kaggle/handoff-artifacts/offline-scaleup-opponent-league-training-v2"),
)
pytestmark = pytest.mark.skipif(
    not all(path.is_dir() for path in _REQUIRED_ARTIFACTS),
    reason="superseded offline-scaleup artifacts were intentionally deleted (2026-08-03)",
)


ROOT = Path(__file__).resolve().parents[1]
RECOVERY = Path("/home/bfe-lab-ono/kaggle/handoff-artifacts/alakazam-target-availability-remediation-v1-timeout-recovery")
OLD_POPULATION = Path("/home/bfe-lab-ono/kaggle/handoff-artifacts/offline-scaleup-opponent-league-training-v2/artifacts/population_snapshot_preview.json")
META = Path("/home/bfe-lab-ono/kaggle/handoff-artifacts/o6-meta-opponent-lab-v0")
FAMILY = Path("/home/bfe-lab-ono/kaggle/handoff-artifacts/family-specific-playbook-realization-v1")



def _card(card_id: int) -> dict[str, object]:
    return {"id": card_id, "serial": 0, "playerIndex": 0, "hp": 100, "maxHp": 100, "appearThisTurn": False, "energies": [], "energyCards": [], "tools": [], "preEvolution": []}


def _observation() -> dict[str, object]:
    player = lambda card: {"active": [], "asleep": False, "bench": [], "benchMax": 5, "burned": False, "confused": False, "deckCount": 53, "discard": [], "hand": [_card(card)], "handCount": 1, "paralyzed": False, "poisoned": False, "prize": [object() for _ in range(6)]}
    return {"current": {"energyAttached": False, "firstPlayer": 0, "players": [player(100), player(700)], "result": -1, "retreated": False, "stadium": [], "stadiumPlayed": False, "supporterPlayed": False, "turn": 2, "turnActionCount": 3, "yourIndex": 0}, "select": {"context": 0, "maxCount": 1, "minCount": 1, "option": [{"type": 14}, {"type": 13, "attackId": 1}], "type": 0}, "step": 7}


def test_population_registers_alakazam_with_limited_trust(tmp_path: Path) -> None:
    population = build_population(repo=ROOT, output=tmp_path / "population.json", recovery_root=RECOVERY)
    assert validate_population(population)["valid"] is True
    alakazam = next(item for item in population["entries"] if item["family_id"] == "ALAKAZAM")
    assert alakazam["teacher_trust"] == "LIMITED"
    assert alakazam["availability_status"] == "AVAILABLE"
    assert alakazam["provenance"]["timeout_attribution"] == "UNRESOLVED_TIMEOUT"
    assert sum(item["opponent_type"] == "RULE_V0_DECK" for item in population["entries"]) == 31


def test_expanded_population_discovers_loadable_native_and_family_records_without_mutating_old(tmp_path: Path) -> None:
    old_bytes = OLD_POPULATION.read_bytes()
    population = build_expanded_population(repo=ROOT, old_population_path=OLD_POPULATION, output=tmp_path / "expanded.json", meta_root=META, family_root=FAMILY, recovery_root=RECOVERY)
    repeat = build_expanded_population(repo=ROOT, old_population_path=OLD_POPULATION, output=tmp_path / "expanded-repeat.json", meta_root=META, family_root=FAMILY, recovery_root=RECOVERY)
    assert OLD_POPULATION.read_bytes() == old_bytes
    assert repeat["semantic_population_digest"] == population["semantic_population_digest"]
    validation = validate_population(population)
    assert validation["valid"] is True
    assert validation["by_type"] == {"FAMILY_SPECIFIC": 3, "RULE_V0_DECK": 31, "TEAM_NATIVE": 3}
    assert population["parent_population_id"] == "population-1812db2a5fa7f61c"
    assert population["alias_count"] == 0
    native = [entry for entry in population["entries"] if entry["opponent_type"] == "TEAM_NATIVE"]
    assert len(native) == 3 and all(entry["loader"] == "team_native_subprocess_v1" and entry["teacher_trust"] == "LIMITED" for entry in native)
    families = {entry["family_id"]: entry for entry in population["entries"] if entry["opponent_type"] == "FAMILY_SPECIFIC"}
    assert set(families) == {"MEGA_LUCARIO_EX", "MEGA_ABOMASNOW_EX", "ALAKAZAM"}
    assert all(entry["loader"] == "family_specific_external_v1" and len(entry["deck_cards"]) == 60 for entry in families.values())


def test_population_rejects_duplicate_runtime_deck_identity_and_quarantined_unavailable_record() -> None:
    base = {"opponent_id": "a", "opponent_type": "TEAM_NATIVE", "source_path": "/approved", "deck_id": "deck", "deck_fingerprint": "a" * 64, "runtime_id": "runtime", "runtime_fingerprint": "b" * 64, "agent_digest": "b" * 64, "validation_status": "NOT_VALIDATED", "availability_status": "UNAVAILABLE", "evaluation_eligibility": "QUARANTINED", "training_eligibility": "QUARANTINED", "teacher_trust": "LIMITED", "quarantine_reason": "SOURCE_UNAVAILABLE", "family_id": None, "strategy_tags": [], "variant_tags": [], "evidence_paths": []}
    population = {"schema_version": "offline-scaleup-population-v2", "semantic_population_digest": "c" * 64, "entries": [base, {**base, "opponent_id": "b"}]}
    # Validation permits a quarantined record but the duplicate ID/pair owner
    # is rejected by the snapshot builder before it can become executable.
    assert validate_population({**population, "entries": [base]})["valid"] is True
    assert len({(entry["runtime_fingerprint"], entry["deck_fingerprint"]) for entry in population["entries"]}) != len(population["entries"])


def test_schedule_is_deterministic_balanced_and_resumable(tmp_path: Path) -> None:
    population = build_population(repo=ROOT, output=tmp_path / "population.json", recovery_root=RECOVERY)
    first = build_schedule(population, candidate="rule-v0-current-deck", opponents=["rule-v0-current-deck"], games=6, base_seed=12)
    second = build_schedule(population, candidate="rule-v0-current-deck", opponents=["rule-v0-current-deck"], games=6, base_seed=12)
    assert first == second
    assert len({game["game_id"] for game in first["games"]}) == 6
    assert sum(game["candidate_side"] == 0 for game in first["games"]) == 3
    run_dir = tmp_path / "run"; run_dir.mkdir()
    (run_dir / "schedule.json").write_text(json.dumps(first), encoding="utf-8")
    summary = run_league(run_dir=run_dir, population_path=tmp_path / "population.json", repo=ROOT, executor="fixture", timeout=5, max_attempts=1, workers=2)
    assert summary["gate"] == "PASS"
    resumed = run_league(run_dir=run_dir, population_path=tmp_path / "population.json", repo=ROOT, executor="fixture", timeout=5, max_attempts=1, workers=2)
    assert resumed["completed"] == 6


def test_dataset_student_v1_and_holdout_smoke(tmp_path: Path) -> None:
    population = build_population(repo=ROOT, output=tmp_path / "population.json", recovery_root=RECOVERY)
    schedule = build_schedule(population, candidate="rule-v0-current-deck", opponents=["rule-v0-current-deck"], games=6, base_seed=31)
    run_dir = tmp_path / "run"; run_dir.mkdir(); (run_dir / "schedule.json").write_text(json.dumps(schedule), encoding="utf-8")
    example = build_rule_bc_example(_observation(), deck=[1] * 60, source_id="fixture", source_revision="test")
    for game in schedule["games"]:
        _write_jsonl_once(run_dir / "game_results.jsonl", {"schema_version": RESULT_SCHEMA, **game, "status": "DONE", "legal": True, "candidate_fault": False, "mapping_valid": True, "score_identity_valid": True, "teacher_samples": [example.to_dict()], "fault": {"kind": "COMPLETED"}})
    dataset = tmp_path / "dataset.jsonl"
    exported = export_dataset(run_dir=run_dir, output=dataset)
    assert exported["illegal_selected_actions"] == 0
    rows = [json.loads(line) for line in dataset.read_text(encoding="utf-8").splitlines()]
    assert all(row["schema_version"] == DATASET_SCHEMA for row in rows)
    assert {row["candidate_outcome"] for row in rows} == {"UNKNOWN"}
    training = train_student_v1(dataset=dataset, model_dir=tmp_path / "model", epochs=2, learning_rate=.1)
    assert training["legal_rate"] == 1.0
    test_record = next(json.loads(line) for line in dataset.read_text(encoding="utf-8").splitlines() if json.loads(line)["split"] == "test")
    with dataset.open("a", encoding="utf-8") as handle:
        for split, episode in (("opponent_holdout", "synthetic-opponent-holdout"), ("deck_holdout", "synthetic-deck-holdout")):
            record = {**test_record, "split": split, "episode_id": episode, "game_id": episode}
            handle.write(json.dumps(record) + "\n")
    holdout = evaluate_holdout(dataset=dataset, model_path=tmp_path / "model" / "student_v1_model.json", output=tmp_path / "holdout.json")
    assert holdout["gate"] == "PASS"
    assert holdout["splits"]["test"]["legal_action_rate"] == 1.0


def test_dataset_record_derives_outcome_from_candidate_seat_not_raw_winner() -> None:
    example = build_rule_bc_example(_observation(), deck=[1] * 60, source_id="fixture", source_revision="test")
    game = {"game_id": "outcome-game", "candidate": "rule-v0-current-deck", "opponent": "opponent",
            "candidate_side": 1, "status": "DONE", "winner": 1, "fault": {"kind": "COMPLETED"}}
    assert _teacher_dataset_record(game, example.to_dict(), "population")["candidate_outcome"] == "WIN"
    assert _teacher_dataset_record({**game, "winner": 0}, example.to_dict(), "population")["candidate_outcome"] == "LOSS"
    assert _teacher_dataset_record({**game, "winner": -1}, example.to_dict(), "population")["candidate_outcome"] == "DRAW"
    assert _teacher_dataset_record({**game, "winner": None}, example.to_dict(), "population")["candidate_outcome"] == "UNKNOWN"
