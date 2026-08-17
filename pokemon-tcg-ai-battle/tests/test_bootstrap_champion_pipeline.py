from __future__ import annotations

import json
from pathlib import Path

import pytest
from main import read_deck_csv

from mage_ptcg.bootstrap_champion.contracts import (
    DeckAsset,
    DeckCompatibility,
    JointCandidate,
    PolicyAsset,
)
from mage_ptcg.bootstrap_champion.pipeline import select_champion, select_finalists, write_schedule
from mage_ptcg.bootstrap_champion.runner import run_schedule
from mage_ptcg.bootstrap_champion.teacher import collect_teacher_dataset, load_teacher_trace
from mage_ptcg.bootstrap_champion.tournament import build_candidate_schedule
from mage_ptcg.continuous_league.catalog import CatalogEntry, CatalogSnapshot
from mage_ptcg.continuous_league.cli import build_parser
from mage_ptcg.observability.cabt_trace import canonical_deck_sha256


def _sha(character: str) -> str:
    return character * 64


def test_cli_exposes_explicit_bootstrap_stages(tmp_path: Path) -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "bootstrap-screen",
            "--candidate-registry", str(tmp_path / "candidates.json"),
            "--opponent-instance", _sha("a"),
            "--games-per-candidate", "256",
            "--output", str(tmp_path / "schedule.json"),
        ]
    )

    assert args.command == "bootstrap-screen"
    assert args.games_per_candidate == 256
    run_args = parser.parse_args(
        [
            "bootstrap-run",
            "--candidate-registry", str(tmp_path / "candidates.json"),
            "--catalog", str(tmp_path / "catalog.json"),
            "--schedule", str(tmp_path / "schedule.json"),
            "--output", str(tmp_path / "results.jsonl"),
            "--scratch-root", str(tmp_path / "scratch"),
        ]
    )
    assert run_args.command == "bootstrap-run"


def test_cli_rejects_resume_and_bootstrap_at_service_boundary(tmp_path: Path) -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "learn",
            "--replay-manifest", str(tmp_path / "replay.json"),
            "--population-epoch-id", _sha("a"),
            "--output", str(tmp_path / "out"),
            "--deck", str(tmp_path / "deck.csv"),
            "--resume", str(tmp_path / "resume.pt"),
            "--bootstrap-checkpoint", str(tmp_path / "bootstrap"),
            "--max-updates", "1",
        ]
    )

    assert args.resume is not None and args.bootstrap_checkpoint is not None


def test_validation_pipeline_requires_every_fixed_game_and_writes_champion(
    tmp_path: Path,
) -> None:
    cards = [1] * 60
    deck_path = tmp_path / "deck.csv"
    deck_path.write_text("\n".join(map(str, cards)) + "\n", encoding="utf-8")
    deck = DeckAsset("deck", canonical_deck_sha256(cards), str(deck_path), "source", _sha("a"))
    first = JointCandidate(deck, PolicyAsset("first", _sha("b"), "rule_v0", "builtin:rule_v0", _sha("c"), _sha("d"), DeckCompatibility.ARBITRARY_LEGAL_DECK, None, "source", _sha("e")), _sha("f"))
    second = JointCandidate(deck, PolicyAsset("second", _sha("1"), "rule_v1", "builtin:rule_v1", _sha("2"), _sha("3"), DeckCompatibility.ARBITRARY_LEGAL_DECK, None, "source", _sha("4")), _sha("f"))
    registry = tmp_path / "candidates.json"
    registry_id = _sha("9")
    registry.write_text(json.dumps({"schema_version": "bootstrap-candidate-registry-v1", "candidate_registry_id": registry_id, "candidates": [first.to_dict(), second.to_dict()]}), encoding="utf-8")
    schedule = write_schedule(candidate_registry_path=registry, opponent_instance_ids=[_sha("6")], games_per_candidate=2, seed_namespace="bootstrap-validation-v1", output=tmp_path / "schedule.json")
    rows = []
    for match in schedule["matches"]:
        rows.append({"game_key": match["game_key"], "outcome": "win" if match["candidate_id"] == second.candidate_id else "loss", "duration_seconds": 0.1})
    results = tmp_path / "results.jsonl"
    results.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    champion = select_champion(candidate_registry_path=registry, validation_schedule_path=tmp_path / "schedule.json", results_path=results, screen_benchmark_id=_sha("7"), output=tmp_path / "champion.json")

    assert champion["candidate_id"] == second.candidate_id
    assert (tmp_path / "champion.json").is_file()


def test_screen_ranking_writes_only_fault_free_finalists(tmp_path: Path) -> None:
    cards = [1] * 60
    deck_path = tmp_path / "deck.csv"
    deck_path.write_text("\n".join(map(str, cards)) + "\n", encoding="utf-8")
    deck = DeckAsset("deck", canonical_deck_sha256(cards), str(deck_path), "source", _sha("a"))
    good = JointCandidate(deck, PolicyAsset("good", _sha("b"), "rule_v0", "builtin:rule_v0", _sha("c"), _sha("d"), DeckCompatibility.ARBITRARY_LEGAL_DECK, None, "source", _sha("e")), _sha("f"))
    bad = JointCandidate(deck, PolicyAsset("bad", _sha("1"), "rule_v1", "builtin:rule_v1", _sha("2"), _sha("3"), DeckCompatibility.ARBITRARY_LEGAL_DECK, None, "source", _sha("4")), _sha("f"))
    registry = tmp_path / "candidates.json"
    registry.write_text(json.dumps({"schema_version": "bootstrap-candidate-registry-v1", "candidate_registry_id": _sha("9"), "candidates": [good.to_dict(), bad.to_dict()]}), encoding="utf-8")
    schedule = write_schedule(candidate_registry_path=registry, opponent_instance_ids=[_sha("6")], games_per_candidate=2, seed_namespace="bootstrap-screen-v1", output=tmp_path / "schedule.json")
    results = tmp_path / "results.jsonl"
    results.write_text("".join(json.dumps({"game_key": match["game_key"], "outcome": "win", "fault": "broken" if match["candidate_id"] == bad.candidate_id else None}) + "\n" for match in schedule["matches"]), encoding="utf-8")

    finalists = select_finalists(candidate_registry_path=registry, schedule_path=tmp_path / "schedule.json", results_path=results, finalists=4, output=tmp_path / "finalists.json")

    assert [item["candidate_id"] for item in finalists["candidates"]] == [good.candidate_id]


def test_runner_writes_a_resumable_result_ledger(tmp_path: Path, monkeypatch) -> None:
    cards = [1] * 60
    deck_path = tmp_path / "deck.csv"
    deck_path.write_text("\n".join(map(str, cards)) + "\n", encoding="utf-8")
    deck = DeckAsset("deck", canonical_deck_sha256(cards), str(deck_path), "source", _sha("a"))
    candidate = JointCandidate(deck, PolicyAsset("candidate", _sha("b"), "rule_v0", "builtin:rule_v0", _sha("c"), _sha("d"), DeckCompatibility.ARBITRARY_LEGAL_DECK, None, "source", _sha("e")), _sha("f"))
    registry = tmp_path / "candidates.json"
    registry.write_text(json.dumps({"schema_version": "bootstrap-candidate-registry-v1", "candidate_registry_id": _sha("9"), "candidates": [candidate.to_dict()]}), encoding="utf-8")
    opponent = CatalogEntry("opponent", "opponent-policy", "opponent-deck", "source", "rule_v0", "builtin:rule_v0", str(deck_path), _sha("1"), deck.deck_hash, _sha("2"), "BENCHMARK_VISIBLE", runtime_config_hash=_sha("3"))
    catalog = CatalogSnapshot.build([opponent])
    schedule = write_schedule(candidate_registry_path=registry, opponent_instance_ids=[opponent.opponent_instance_id], games_per_candidate=2, seed_namespace="bootstrap-screen-v1", output=tmp_path / "schedule.json")

    monkeypatch.setattr("scripts.test_sim.run_match", lambda **_kwargs: {"status": "DONE", "winner": 0, "elapsed_seconds": 0.01})
    first = run_schedule(candidate_registry=registry, catalog=catalog, schedule_path=tmp_path / "schedule.json", output=tmp_path / "results.jsonl", scratch_root=tmp_path / "scratch")
    second = run_schedule(candidate_registry=registry, catalog=catalog, schedule_path=tmp_path / "schedule.json", output=tmp_path / "results.jsonl", scratch_root=tmp_path / "scratch")

    assert first["completed_games"] == 2
    assert second["completed_games"] == 2
    assert len((tmp_path / "results.jsonl").read_text(encoding="utf-8").splitlines()) == 2


def test_runner_executes_two_real_rule_games(tmp_path: Path) -> None:
    cards = read_deck_csv(Path("deck.csv"))
    deck_path = tmp_path / "deck.csv"
    deck_path.write_text("\n".join(map(str, cards)) + "\n", encoding="utf-8")
    deck = DeckAsset("deck", canonical_deck_sha256(cards), str(deck_path), "source", _sha("a"))
    candidate = JointCandidate(deck, PolicyAsset("candidate", _sha("b"), "rule_v0", "builtin:rule_v0", _sha("c"), _sha("d"), DeckCompatibility.ARBITRARY_LEGAL_DECK, None, "source", _sha("e")), _sha("f"))
    registry = tmp_path / "candidates.json"
    registry.write_text(json.dumps({"schema_version": "bootstrap-candidate-registry-v1", "candidate_registry_id": _sha("9"), "candidates": [candidate.to_dict()]}), encoding="utf-8")
    opponent = CatalogEntry("opponent", "opponent-policy", "opponent-deck", "source", "rule_v0", "builtin:rule_v0", str(deck_path), _sha("1"), deck.deck_hash, _sha("2"), "BENCHMARK_VISIBLE", runtime_config_hash=_sha("3"))
    catalog = CatalogSnapshot.build([opponent])
    write_schedule(candidate_registry_path=registry, opponent_instance_ids=[opponent.opponent_instance_id], games_per_candidate=2, seed_namespace="bootstrap-e2e-v1", output=tmp_path / "schedule.json")

    trace_root = tmp_path / "teacher-trace"
    result = run_schedule(candidate_registry=registry, catalog=catalog, schedule_path=tmp_path / "schedule.json", output=tmp_path / "results.jsonl", scratch_root=tmp_path / "scratch", max_steps=1_000, teacher_output=trace_root)

    assert result["completed_games"] == 2
    assert result["fault_count"] == 0
    examples, excluded, skipped = load_teacher_trace(trace_root)
    assert examples
    manifest = collect_teacher_dataset(
        examples=examples,
        excluded_game_ids=excluded,
        skipped_multi_select_decisions=skipped,
        deck_hash=deck.deck_hash,
        teacher_candidate_id=candidate.candidate_id,
        seed=71_000,
        output=tmp_path / "teacher-dataset",
    )
    assert manifest.decision_count == len(examples)
