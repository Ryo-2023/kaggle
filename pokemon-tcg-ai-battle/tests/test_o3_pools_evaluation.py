from __future__ import annotations

import json
from pathlib import Path

from mage_ptcg.competition_intelligence.contracts import AllowedUse, DECK_OBSERVATION_SCHEMA_VERSION, DeckObservation, SourceKind
from mage_ptcg.continuous_learning.evaluation import build_o3_promotion_report
from mage_ptcg.continuous_learning.pools import refresh_deck_pool, refresh_opponent_pool
from mage_ptcg.continuous_learning.run import run


ROOT = Path(__file__).resolve().parents[1]


def _observation() -> DeckObservation:
    deck = json.loads((ROOT / "configs/competition/deck_pool_o2_v1.yaml").read_text())["decks"][0]["cards"]
    counts = {card: deck.count(card) for card in set(deck)}
    return DeckObservation(DECK_OBSERVATION_SCHEMA_VERSION, "episode-1", 0, counts, "visualize_post_game", {}, {"UNKNOWN": 1.0}, {}, None, 1.0)


def test_pool_refresh_deduplicates_order_independent_exact_decks_and_excludes_public_other(tmp_path: Path) -> None:
    output = tmp_path / "decks.json"
    result = refresh_deck_pool(
        base_pool=ROOT / "configs/competition/deck_pool_o2_v1.yaml",
        observations=[(_observation(), SourceKind.OWN_KAGGLE, frozenset({AllowedUse.TRAINING})),
                      (_observation(), SourceKind.PUBLIC_OTHER, frozenset({AllowedUse.TRAINING}))],
        output_path=output, observed_at="2026-07-20T00:00:00Z",
    )
    assert result["admitted_decks"] == 0
    assert result["public_other_admitted"] is False


def test_opponent_refresh_requires_a_real_student_artifact(tmp_path: Path) -> None:
    output = tmp_path / "opponents.json"
    result = refresh_opponent_pool(base_pool=ROOT / "configs/competition/opponent_pool_o2_v1.yaml", output_path=output)
    assert result["student_enabled"] is False
    assert {item["agent_kind"] for item in json.loads(output.read_text())["opponents"] if item["enabled"]} >= {"rule_v0", "random_legal"}


def test_o3_evaluation_metadata_rejects_underpowered_unseeded_evidence() -> None:
    report = build_o3_promotion_report({"seat_matched_logical_pairs": 99})
    assert report["decision"] == "INSUFFICIENT_EVIDENCE"
    assert report["engine_seed_supported"] is False
    assert report["pairing_mode"] == "seat_matched_unseeded"
    assert report["exact_paired_inference"] is False
    assert report["champion_after"] == "Rule Agent v0"


def test_continuous_runner_never_uses_fixture_acquisition_for_training(tmp_path: Path) -> None:
    fixture = tmp_path / "fixture.json"
    fixture.write_text(json.dumps({
        "own_submission_listing": {"body": []}, "leaderboard": {"body": []}, "public_artifacts": {"body": []},
    }))
    state = run(config_path=ROOT / "configs/competition/continuous_learning_o3_v1.yaml", run_root=tmp_path / "run", fixture=str(fixture))
    assert state["phases"]["dataset"] == "BLOCKED_FIXTURE_CONTAMINATION"
    assert state["phases"]["training"] == "BLOCKED_FIXTURE_CONTAMINATION"
    assert state["champion"] == "Rule Agent v0"
    assert state["kaggle_submission_performed"] is False
