"""Focused contract tests for the O2 minimum viable training-loop sidecar."""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from mage_ptcg.o2_training_loop import (
    O2ContractError, build_match_matrix, deck_content_hash, execute_match_plan,
    load_deck_pool, load_opponent_pool, paired_evaluation, promotion_report,
    resolve_real_agent, resolve_real_deck,
)


ROOT = Path(__file__).resolve().parents[1]


def _pools():
    decks = load_deck_pool(ROOT / "configs/competition/deck_pool_o2_v1.yaml")
    opponents = load_opponent_pool(ROOT / "configs/competition/opponent_pool_o2_v1.yaml", deck_ids=decks)
    return decks, opponents


def _specs():
    decks, opponents = _pools()
    return build_match_matrix(decks=decks, opponents=opponents, challenger_id="rule-agent-v0", opponent_ids=["random-legal-v0"], seeds=[7], engine_version="cabt-test", created_from_manifest="manifest-v1")


def test_deck_hash_is_order_invariant_and_pool_is_valid():
    decks, _ = _pools()
    cards = list(next(iter(decks.values())).cards)
    assert deck_content_hash(cards) == deck_content_hash(list(reversed(cards)))


def test_duplicate_deck_content_and_unknown_agent_fail_closed(tmp_path: Path):
    deck = json.loads((ROOT / "configs/competition/deck_pool_o2_v1.yaml").read_text())
    duplicate = dict(deck["decks"][0]); duplicate["deck_id"] = "duplicate"
    deck["decks"].append(duplicate)
    path = tmp_path / "deck.yaml"; path.write_text(json.dumps(deck))
    with pytest.raises(O2ContractError, match="duplicate deck content"):
        load_deck_pool(path)
    opponents = json.loads((ROOT / "configs/competition/opponent_pool_o2_v1.yaml").read_text())
    opponents["opponents"][0]["agent_kind"] = "unknown"
    path = tmp_path / "opponents.yaml"; path.write_text(json.dumps(opponents))
    with pytest.raises(O2ContractError, match="unknown agent"):
        load_opponent_pool(path, deck_ids={"repository-default-v1"})


def test_plan_is_deterministic_and_excludes_run_metadata():
    first, second = _specs(), _specs()
    assert [item.to_dict() for item in first] == [item.to_dict() for item in second]
    assert len(first) == 2
    assert {item.first_player for item in first} == {0, 1}


def test_execution_is_atomic_resume_and_fixture_marked(tmp_path: Path):
    calls = 0
    def fixture(spec):
        nonlocal calls
        calls += 1
        return {"status": "DONE", "winner": spec.first_player, "elapsed_seconds": .01}
    summary = execute_match_plan(_specs(), output_dir=tmp_path, backend=fixture, backend_kind="fixture_backend")
    assert summary["completed"] == 2 and calls == 2
    assert execute_match_plan(_specs(), output_dir=tmp_path, backend=fixture, backend_kind="fixture_backend")["completed"] == 2
    assert calls == 2
    record = json.loads(next((tmp_path / "matches").glob("*/normalized.json")).read_text())
    assert record["backend_kind"] == "fixture_backend"


def test_paired_evaluation_and_incomplete_pair_reporting(tmp_path: Path):
    specs = _specs()
    execute_match_plan(specs, output_dir=tmp_path, backend=lambda spec: {"status":"DONE", "winner":spec.first_player, "elapsed_seconds":.1}, backend_kind="fixture_backend")
    records = [json.loads(path.read_text()) for path in (tmp_path / "matches").glob("*/normalized.json")]
    report = paired_evaluation(specs, records)
    assert report["paired_games"] == 1 and report["challenger_win_rate"] == 1.0
    assert paired_evaluation(specs, records[:1])["incomplete_pairs"] == 1


def test_promotion_report_never_changes_rule_v0_champion():
    report = promotion_report({"paired_games": 1, "legality_failures": 0, "timeouts": 0, "fallbacks": 0, "failed_matches": 0, "confidence_interval_95": [1.0, 1.0]})
    assert report["decision"] == "INSUFFICIENT_EVIDENCE"
    assert report["champion_before"] == report["champion_after"] == "Rule Agent v0"
    assert report["automatic_champion_change"] is False


def test_real_pool_adapters_validate_allowlist_artifact_and_deck_identity(tmp_path: Path):
    decks, opponents = _pools()
    deck = decks["repository-default-v1"]
    path, fingerprint = resolve_real_deck(deck, repository_root=ROOT)
    assert path == ROOT / "deck.csv" and len(fingerprint) == 64
    assert callable(resolve_real_agent(opponents["rule-agent-v0"], repository_root=ROOT))
    with pytest.raises(O2ContractError, match="artifact_reference"):
        resolve_real_agent(opponents["student-v0"], repository_root=ROOT)
    bad = replace(opponents["rule-agent-v0"], agent_factory="builtins.eval")
    with pytest.raises(O2ContractError, match="allowlisted"):
        resolve_real_agent(bad, repository_root=ROOT)
