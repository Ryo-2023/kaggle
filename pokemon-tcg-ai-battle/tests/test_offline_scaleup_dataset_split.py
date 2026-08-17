"""Contracts for the deterministic 5-cohort dataset split remediation."""
from __future__ import annotations

import json
from pathlib import Path

from mage_ptcg.offline_scaleup.pipeline import (
    DATASET_SCHEMA,
    RESULT_SCHEMA,
    ContractError,
    MIN_SPLIT_EPISODES,
    _write_jsonl_once,
    build_schedule,
    build_split_manifest,
    default_worker_count,
    export_dataset_v2,
    select_deck_holdout,
    select_opponent_holdout,
    validate_split_gate,
)
from mage_ptcg.student.dataset import build_rule_bc_example


def _population(entries: list[dict[str, object]]) -> dict[str, object]:
    return {"schema_version": "offline-scaleup-population-v2", "entries": entries,
            "semantic_population_digest": "d" * 64}


def _entry(opponent_id: str, opponent_type: str, deck_fingerprint: str) -> dict[str, object]:
    return {"opponent_id": opponent_id, "opponent_type": opponent_type, "deck_fingerprint": deck_fingerprint}


_POPULATION = _population([
    _entry("rule-v0-current-deck", "RULE_V0_DECK", "fp-current"),
    _entry("rule-v0-deck-a", "RULE_V0_DECK", "fp-a"),
    _entry("rule-v0-deck-b", "RULE_V0_DECK", "fp-b"),
    _entry("family-x", "FAMILY_SPECIFIC", "fp-family-x"),
    _entry("family-y", "FAMILY_SPECIFIC", "fp-family-y"),
    _entry("team-native-p", "TEAM_NATIVE", "fp-team-p"),
    _entry("team-native-q", "TEAM_NATIVE", "fp-team-q"),
])
_PRESENT = {"rule-v0-current-deck", "rule-v0-deck-a", "rule-v0-deck-b", "family-x", "family-y", "team-native-p", "team-native-q"}


def test_opponent_holdout_is_deterministic_and_from_team_native_or_family() -> None:
    first = select_opponent_holdout(_POPULATION, _PRESENT)
    second = select_opponent_holdout(_POPULATION, _PRESENT)
    assert first == second
    entries = {e["opponent_id"]: e for e in _POPULATION["entries"]}
    assert entries[first]["opponent_type"] in {"TEAM_NATIVE", "FAMILY_SPECIFIC"}


def test_opponent_holdout_rejects_when_no_team_native_or_family_present() -> None:
    rule_only = {"rule-v0-current-deck", "rule-v0-deck-a", "rule-v0-deck-b"}
    try:
        select_opponent_holdout(_POPULATION, rule_only)
    except ContractError:
        return
    raise AssertionError("expected ContractError")


def test_deck_holdout_is_deterministic_and_from_rule_v0_and_excludes_opponent_holdout_deck() -> None:
    opponent_holdout = select_opponent_holdout(_POPULATION, _PRESENT)
    first = select_deck_holdout(_POPULATION, _PRESENT, opponent_holdout)
    second = select_deck_holdout(_POPULATION, _PRESENT, opponent_holdout)
    assert first == second
    entries_by_fp = {e["deck_fingerprint"]: e for e in _POPULATION["entries"]}
    assert entries_by_fp[first]["opponent_type"] == "RULE_V0_DECK"
    holdout_fp = next(e["deck_fingerprint"] for e in _POPULATION["entries"] if e["opponent_id"] == opponent_holdout)
    assert first != holdout_fp


def test_deck_holdout_rejects_when_no_rule_v0_deck_present() -> None:
    non_rule = {"family-x", "family-y", "team-native-p", "team-native-q"}
    try:
        select_deck_holdout(_POPULATION, non_rule, "family-x")
    except ContractError:
        return
    raise AssertionError("expected ContractError")


def _card(card_id: int) -> dict[str, object]:
    return {"id": card_id, "serial": 0, "playerIndex": 0, "hp": 100, "maxHp": 100, "appearThisTurn": False, "energies": [], "energyCards": [], "tools": [], "preEvolution": []}


def _observation() -> dict[str, object]:
    player = lambda card: {"active": [], "asleep": False, "bench": [], "benchMax": 5, "burned": False, "confused": False, "deckCount": 53, "discard": [], "hand": [_card(card)], "handCount": 1, "paralyzed": False, "poisoned": False, "prize": [object() for _ in range(6)]}
    return {"current": {"energyAttached": False, "firstPlayer": 0, "players": [player(100), player(700)], "result": -1, "retreated": False, "stadium": [], "stadiumPlayed": False, "supporterPlayed": False, "turn": 2, "turnActionCount": 3, "yourIndex": 0}, "select": {"context": 0, "maxCount": 1, "minCount": 1, "option": [{"type": 14}, {"type": 13, "attackId": 1}], "type": 0}, "step": 7}


def _build_run(tmp_path: Path, *, games_per_opponent: int) -> tuple[Path, Path]:
    entries = [
        {"opponent_id": "rule-v0-current-deck", "opponent_type": "RULE_V0_DECK", "source_path": "x", "deck_id": "current-deck", "deck_fingerprint": "fp-current", "runtime_id": "r", "runtime_fingerprint": "a" * 64, "agent_digest": "a" * 64, "validation_status": "VALIDATED", "availability_status": "AVAILABLE", "evaluation_eligibility": "ALLOWED", "training_eligibility": "ALLOWED_FOR_VALID_FAULT_FREE_GAMES", "teacher_trust": "TRUSTED", "quarantine_reason": None, "family_id": None, "strategy_tags": [], "variant_tags": [], "evidence_paths": []},
        {"opponent_id": "rule-v0-deck-a", "opponent_type": "RULE_V0_DECK", "source_path": "x", "deck_id": "deck-a", "deck_fingerprint": "fp-a", "runtime_id": "r", "runtime_fingerprint": "a" * 64, "agent_digest": "a" * 64, "validation_status": "VALIDATED", "availability_status": "AVAILABLE", "evaluation_eligibility": "ALLOWED", "training_eligibility": "ALLOWED_FOR_VALID_FAULT_FREE_GAMES", "teacher_trust": "TRUSTED", "quarantine_reason": None, "family_id": None, "strategy_tags": [], "variant_tags": [], "evidence_paths": []},
        {"opponent_id": "family-x", "opponent_type": "FAMILY_SPECIFIC", "source_path": "x", "deck_id": "deck-x", "deck_fingerprint": "fp-family-x", "runtime_id": "r", "runtime_fingerprint": "b" * 64, "agent_digest": "b" * 64, "validation_status": "VALIDATED", "availability_status": "AVAILABLE", "evaluation_eligibility": "ALLOWED", "training_eligibility": "ALLOWED_FOR_VALID_FAULT_FREE_GAMES", "teacher_trust": "LIMITED", "quarantine_reason": None, "family_id": "X", "strategy_tags": [], "variant_tags": [], "evidence_paths": []},
        {"opponent_id": "team-native-p", "opponent_type": "TEAM_NATIVE", "source_path": "x", "deck_id": "deck-p", "deck_fingerprint": "fp-team-p", "runtime_id": "r", "runtime_fingerprint": "c" * 64, "agent_digest": "c" * 64, "validation_status": "VALIDATED", "availability_status": "AVAILABLE", "evaluation_eligibility": "ALLOWED", "training_eligibility": "ALLOWED_FOR_VALID_FAULT_FREE_GAMES", "teacher_trust": "LIMITED", "quarantine_reason": None, "family_id": None, "strategy_tags": [], "variant_tags": [], "evidence_paths": []},
    ]
    population = {"schema_version": "offline-scaleup-population-v2", "entries": entries, "semantic_population_digest": "d" * 64, "alias_count": 0, "created_by": "test", "population_id": "population-test"}
    population_path = tmp_path / "population.json"
    population_path.write_text(json.dumps(population), encoding="utf-8")
    schedule = build_schedule(population, candidate="rule-v0-current-deck",
                               opponents=["rule-v0-current-deck", "rule-v0-deck-a", "family-x", "team-native-p"],
                               games=games_per_opponent, base_seed=41)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "schedule.json").write_text(json.dumps(schedule), encoding="utf-8")
    example = build_rule_bc_example(_observation(), deck=[1] * 60, source_id="fixture", source_revision="test")
    for game in schedule["games"]:
        _write_jsonl_once(run_dir / "game_results.jsonl", {"schema_version": RESULT_SCHEMA, **game, "status": "DONE", "legal": True,
                           "candidate_fault": False, "mapping_valid": True, "score_identity_valid": True,
                           "teacher_samples": [example.to_dict()], "fault": {"kind": "COMPLETED"}})
    from mage_ptcg.offline_scaleup.pipeline import summarize_run
    summarize_run(run_dir)
    return run_dir, population_path


def test_split_manifest_is_deterministic_covers_every_episode_and_isolates_holdouts(tmp_path: Path) -> None:
    run_dir, population_path = _build_run(tmp_path, games_per_opponent=20)
    manifest = build_split_manifest(run_dir=run_dir, population_path=population_path)
    repeat = build_split_manifest(run_dir=run_dir, population_path=population_path)
    assert manifest["episode_assignment"] == repeat["episode_assignment"]
    assert manifest["opponent_holdout_id"] == repeat["opponent_holdout_id"]
    assert manifest["deck_holdout_fingerprint"] == repeat["deck_holdout_fingerprint"]
    assert set(manifest["episode_assignment"]) == set(manifest["episode_opponent"])
    assert manifest["opponent_holdout_id"] in {"family-x", "team-native-p"}
    holdout_episodes = [ep for ep, split in manifest["episode_assignment"].items() if split == "opponent_holdout"]
    assert all(manifest["episode_opponent"][ep] == manifest["opponent_holdout_id"] for ep in holdout_episodes)
    assert {manifest["episode_side"][ep] for ep in holdout_episodes} == {0, 1}
    deck_episodes = [ep for ep, split in manifest["episode_assignment"].items() if split == "deck_holdout"]
    assert deck_episodes, "deck holdout must not be empty"
    assert not (set(holdout_episodes) & set(deck_episodes))


def test_split_gate_reports_failures_below_minimum(tmp_path: Path) -> None:
    run_dir, population_path = _build_run(tmp_path, games_per_opponent=4)
    manifest = build_split_manifest(run_dir=run_dir, population_path=population_path)
    gate = validate_split_gate(manifest)
    assert gate["gate"] == "BLOCKED"
    assert any(name.startswith("train") for name in gate["failures"])


def test_split_gate_passes_when_minimums_met(tmp_path: Path) -> None:
    # Only 2 of the 4 fixture opponents remain after both holdouts are reserved,
    # so each must contribute enough games for the 500-episode train minimum.
    run_dir, population_path = _build_run(tmp_path, games_per_opponent=400)
    manifest = build_split_manifest(run_dir=run_dir, population_path=population_path)
    gate = validate_split_gate(manifest)
    assert gate["gate"] == "PASS", gate["failures"]
    for name, minimum in MIN_SPLIT_EPISODES.items():
        assert manifest["split_counts"].get(name, 0) >= minimum


def test_default_worker_count_is_positive_and_bounded_by_cpu() -> None:
    import os
    workers = default_worker_count()
    assert 1 <= workers <= (os.cpu_count() or 1)


def test_export_dataset_v2_writes_five_cohorts_and_all_artifacts(tmp_path: Path) -> None:
    run_dir, population_path = _build_run(tmp_path, games_per_opponent=400)
    artifact_root = tmp_path / "artifacts_root"
    result = export_dataset_v2(run_dir=run_dir, population_path=population_path, artifact_root=artifact_root, workers=2, show_progress=False)
    assert result["gate"] == "PASS"
    dataset_path = artifact_root / "datasets" / "stability-900-split-v2.jsonl"
    assert dataset_path.exists()
    rows = [json.loads(line) for line in dataset_path.read_text(encoding="utf-8").splitlines()]
    assert rows and all(row["schema_version"] == DATASET_SCHEMA for row in rows)
    splits_seen = {row["split"] for row in rows}
    assert splits_seen == {"train", "validation", "test", "opponent_holdout", "deck_holdout"}
    for name in ("dataset_split_manifest_v2.json", "dataset_composition_v2.json", "dataset_teacher_distribution_v2.json",
                 "dataset_leakage_check_v2.json", "dataset_quality_report_v2.json", "dataset_split_remediation_verdict.json"):
        assert (artifact_root / "artifacts" / name).exists(), name
    teacher = json.loads((artifact_root / "artifacts" / "dataset_teacher_distribution_v2.json").read_text(encoding="utf-8"))
    assert teacher["single_teacher_finding"]["all_teachers_rule_v0"] is True
    leakage = json.loads((artifact_root / "artifacts" / "dataset_leakage_check_v2.json").read_text(encoding="utf-8"))
    assert leakage["episode_leakage"] == 0 and leakage["opponent_holdout_leakage"] == 0 and leakage["deck_holdout_leakage"] == 0
    verdict = json.loads((artifact_root / "artifacts" / "dataset_split_remediation_verdict.json").read_text(encoding="utf-8"))
    assert verdict["verdict"] == "READY_FOR_STUDENT_V1_TRAINING"


def test_export_dataset_v2_rejects_second_write_to_same_output(tmp_path: Path) -> None:
    run_dir, population_path = _build_run(tmp_path, games_per_opponent=400)
    artifact_root = tmp_path / "artifacts_root_dup"
    export_dataset_v2(run_dir=run_dir, population_path=population_path, artifact_root=artifact_root, workers=1, show_progress=False)
    try:
        export_dataset_v2(run_dir=run_dir, population_path=population_path, artifact_root=artifact_root, workers=1, show_progress=False)
    except ContractError:
        return
    raise AssertionError("expected ContractError on duplicate dataset output")


def test_episode_atomicity_every_episode_records_share_one_split(tmp_path: Path) -> None:
    run_dir, population_path = _build_run(tmp_path, games_per_opponent=400)
    artifact_root = tmp_path / "atomicity"
    export_dataset_v2(run_dir=run_dir, population_path=population_path, artifact_root=artifact_root, workers=2, show_progress=False)
    rows = [json.loads(line) for line in (artifact_root / "datasets" / "stability-900-split-v2.jsonl").read_text(encoding="utf-8").splitlines()]
    by_episode: dict[str, set[str]] = {}
    for row in rows:
        by_episode.setdefault(row["episode_id"], set()).add(row["split"])
    assert all(len(splits) == 1 for splits in by_episode.values())


def test_side_preservation_holdouts_contain_both_candidate_sides(tmp_path: Path) -> None:
    run_dir, population_path = _build_run(tmp_path, games_per_opponent=400)
    artifact_root = tmp_path / "sides"
    export_dataset_v2(run_dir=run_dir, population_path=population_path, artifact_root=artifact_root, workers=2, show_progress=False)
    rows = [json.loads(line) for line in (artifact_root / "datasets" / "stability-900-split-v2.jsonl").read_text(encoding="utf-8").splitlines()]
    for split_name in ("opponent_holdout", "deck_holdout"):
        sides = {row["candidate_side"] for row in rows if row["split"] == split_name}
        assert sides == {0, 1}, f"{split_name} missing a side: {sides}"


def test_empty_holdout_pool_is_rejected(tmp_path: Path) -> None:
    entries = [
        {"opponent_id": "rule-v0-current-deck", "opponent_type": "RULE_V0_DECK", "source_path": "x", "deck_id": "current-deck", "deck_fingerprint": "fp-current", "runtime_id": "r", "runtime_fingerprint": "a" * 64, "agent_digest": "a" * 64, "validation_status": "VALIDATED", "availability_status": "AVAILABLE", "evaluation_eligibility": "ALLOWED", "training_eligibility": "ALLOWED_FOR_VALID_FAULT_FREE_GAMES", "teacher_trust": "TRUSTED", "quarantine_reason": None, "family_id": None, "strategy_tags": [], "variant_tags": [], "evidence_paths": []},
    ]
    population = {"schema_version": "offline-scaleup-population-v2", "entries": entries, "semantic_population_digest": "e" * 64, "alias_count": 0, "created_by": "test", "population_id": "population-test"}
    population_path = tmp_path / "population.json"
    population_path.write_text(json.dumps(population), encoding="utf-8")
    schedule = build_schedule(population, candidate="rule-v0-current-deck", opponents=["rule-v0-current-deck"], games=6, base_seed=7)
    run_dir = tmp_path / "run"; run_dir.mkdir()
    (run_dir / "schedule.json").write_text(json.dumps(schedule), encoding="utf-8")
    example = build_rule_bc_example(_observation(), deck=[1] * 60, source_id="fixture", source_revision="test")
    for game in schedule["games"]:
        _write_jsonl_once(run_dir / "game_results.jsonl", {"schema_version": RESULT_SCHEMA, **game, "status": "DONE", "legal": True,
                           "candidate_fault": False, "mapping_valid": True, "score_identity_valid": True,
                           "teacher_samples": [example.to_dict()], "fault": {"kind": "COMPLETED"}})
    from mage_ptcg.offline_scaleup.pipeline import summarize_run
    summarize_run(run_dir)
    try:
        build_split_manifest(run_dir=run_dir, population_path=population_path)
    except ContractError as exc:
        assert "opponent-holdout" in str(exc)
        return
    raise AssertionError("expected ContractError for empty opponent-holdout pool")


def test_insufficient_split_is_rejected_by_gate_not_by_silent_pass(tmp_path: Path) -> None:
    run_dir, population_path = _build_run(tmp_path, games_per_opponent=4)
    manifest = build_split_manifest(run_dir=run_dir, population_path=population_path)
    gate = validate_split_gate(manifest)
    assert gate["gate"] == "BLOCKED"


def test_split_manifest_round_trip_through_disk(tmp_path: Path) -> None:
    run_dir, population_path = _build_run(tmp_path, games_per_opponent=400)
    artifact_root = tmp_path / "roundtrip"
    export_dataset_v2(run_dir=run_dir, population_path=population_path, artifact_root=artifact_root, workers=1, show_progress=False)
    manifest_path = artifact_root / "artifacts" / "dataset_split_manifest_v2.json"
    reloaded = json.loads(manifest_path.read_text(encoding="utf-8"))
    dataset_rows = [json.loads(line) for line in (artifact_root / "datasets" / "stability-900-split-v2.jsonl").read_text(encoding="utf-8").splitlines()]
    for row in dataset_rows:
        assert reloaded["episode_assignment"][row["episode_id"]] == row["split"]


def test_old_dataset_file_is_never_touched_by_v2_export(tmp_path: Path) -> None:
    run_dir, population_path = _build_run(tmp_path, games_per_opponent=400)
    artifact_root = tmp_path / "old-dataset-guard"
    old_dataset = artifact_root / "datasets" / "stability-1000.jsonl"
    old_dataset.parent.mkdir(parents=True)
    old_dataset.write_text('{"marker": "do-not-touch"}\n', encoding="utf-8")
    before = old_dataset.read_bytes()
    export_dataset_v2(run_dir=run_dir, population_path=population_path, artifact_root=artifact_root, workers=1, show_progress=False)
    assert old_dataset.read_bytes() == before


def test_export_dataset_v2_periodic_progress_has_no_ansi_and_reports_split(tmp_path: Path, capsys) -> None:
    run_dir, population_path = _build_run(tmp_path, games_per_opponent=400)
    artifact_root = tmp_path / "periodic-progress"
    export_dataset_v2(run_dir=run_dir, population_path=population_path, artifact_root=artifact_root, workers=2,
                       progress=None, progress_interval_seconds=0)
    captured = capsys.readouterr()
    assert "\x1b" not in captured.err
    assert "PROGRESS phase=dataset-build" in captured.err or "PROGRESS phase=dataset-write" in captured.err
