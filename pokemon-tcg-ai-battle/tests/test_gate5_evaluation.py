from __future__ import annotations

import json
from pathlib import Path

from mage_ptcg.policy_learning.gate5_evaluation import (
    select_holdout_opponents,
    summarize_holdout,
    summarize_primary,
)


def _write_run(root: Path, label: str, *, opponents: list[str], wins: set[int]) -> None:
    run = root / "evaluations" / label
    run.mkdir(parents=True)
    games = []
    index = 0
    for opponent in opponents:
        for repetition in range(2):
            for side in (0, 1):
                games.append(
                    {
                        "game_id": f"{label}-{index}",
                        "candidate": label,
                        "opponent": opponent,
                        "candidate_side": side,
                        "repetition": repetition,
                        "seed": 1000 + index,
                        "winner": side if index in wins else 1 - side,
                        "status": "DONE",
                        "legal": True,
                        "candidate_fault": False,
                    }
                )
                index += 1
    (run / "game_results.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in games),
        encoding="utf-8",
    )
    (run / "run_summary.json").write_text(
        json.dumps(
            {
                "gate": "PASS",
                "completed": len(games),
                "legal_games": len(games),
                "candidate_faults": 0,
                "mapping_failures": 0,
                "score_identity_failures": 0,
                "fault_counts": {"COMPLETED": len(games)},
                "wall_clock_games_per_second": 2.5,
            }
        ),
        encoding="utf-8",
    )


def test_gate5_final_evaluation_selects_checkpoint_and_checks_holdouts(tmp_path: Path) -> None:
    manifest = {
        "candidates": [
            {"label": "primary-bc", "role": "bc", "model_dir": "bc", "decisions": 0},
            {"label": "primary-round3", "role": "frozen-round-3", "model_dir": "round3", "decisions": 20},
            {"label": "primary-final", "role": "final", "model_dir": "final", "decisions": 100},
        ]
    }
    manifest_path = tmp_path / "candidates.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    _write_run(tmp_path, "primary-bc", opponents=["rule-v0-current-deck", "rule-v0-current-deck"], wins={0, 5})
    _write_run(tmp_path, "primary-round3", opponents=["rule-v0-current-deck", "rule-v0-current-deck"], wins={0, 1, 4, 5})
    _write_run(tmp_path, "primary-final", opponents=["rule-v0-current-deck", "rule-v0-current-deck"], wins=set(range(8)))
    primary_path = tmp_path / "primary.json"
    primary = summarize_primary(
        output_root=tmp_path,
        candidate_manifest=manifest_path,
        expected_games=8,
        base_seed=1000,
        workers=2,
        output=primary_path,
    )
    assert primary["selected"] == "primary-final"
    assert primary["bc_improvement"] == "CONFIRMED"
    assert primary["rule_v0_point_target"] == "MET"
    assert primary["primary_side_both_improved"] is True

    holdout_manifest = {
        "opponents": [
            {"opponent_id": "unknown-a", "opponent_policy_hash": "hash-a", "unknown_policy_hash": True},
            {"opponent_id": "unknown-b", "opponent_policy_hash": "hash-b", "unknown_policy_hash": True},
        ]
    }
    holdout_path = tmp_path / "holdout.json"
    holdout_path.write_text(json.dumps(holdout_manifest), encoding="utf-8")
    _write_run(tmp_path, "holdout-bc", opponents=["unknown-a", "unknown-b"], wins={4, 5})
    _write_run(tmp_path, "holdout-selected", opponents=["unknown-a", "unknown-b"], wins=set(range(8)))
    final = summarize_holdout(
        output_root=tmp_path,
        primary_summary_path=primary_path,
        holdout_manifest_path=holdout_path,
        expected_games=8,
        output=tmp_path / "final.json",
    )
    assert final["learning_curve_verdict"] == "ROUND15_ABOVE_FROZEN_ROUND3"
    # A perfect 8/8 synthetic fixture puts the Wilson lower bound above 50%;
    # the production runner still enforces at least 1,024 games.
    assert final["ppo_continuation_gate"] == "GATE5A_PPO_IMPROVEMENT_CONFIRMED"
    assert final["gate"] == "GATE5A_FINAL_CANDIDATE_VALIDATED"
    assert all(final["conditions"].values())


def test_gate5_holdout_selection_uses_unseen_policy_hashes_and_one_deck(tmp_path: Path) -> None:
    entries = []
    known_ids = []
    for index in range(4):
        opponent = f"known-{index}"
        known_ids.append(opponent)
        entries.append(
            {
                "opponent_id": opponent,
                "opponent_type": "RULE_V0_DECK",
                "runtime_fingerprint": "known-policy",
                "deck_fingerprint": f"known-deck-{index}",
                "availability_status": "AVAILABLE",
                "validation_status": "VALIDATED",
            }
        )
    for index in range(7):
        if index < 3:
            opponent_type, loader = "FAMILY_SPECIFIC", "family_specific_external_v1"
        elif index < 6:
            opponent_type, loader = "TEAM_NATIVE", "team_native_subprocess_v1"
        else:
            opponent_type, loader = "STUDENT_AGENT", "policy_learning_actor_critic_v1"
        entries.append(
            {
                "opponent_id": f"unknown-{index}",
                "opponent_type": opponent_type,
                "loader": loader,
                "runtime_fingerprint": f"unknown-policy-{index}",
                "deck_fingerprint": f"policy-holdout-deck-{index}",
                "availability_status": "AVAILABLE",
                "validation_status": "VALIDATED",
            }
        )
    for suffix, deck_fingerprint in (
        ("seen", "known-deck-0"),
        ("a", "unseen-deck-a"),
        ("b", "unseen-deck-b"),
    ):
        entries.append(
            {
                "opponent_id": f"rule-v0-deck-holdout-{suffix}",
                "opponent_type": "RULE_V0_DECK",
                "runtime_fingerprint": "known-policy",
                "deck_fingerprint": deck_fingerprint,
                "availability_status": "AVAILABLE",
                "validation_status": "VALIDATED",
            }
        )
    population = tmp_path / "population.json"
    population.write_text(json.dumps({"semantic_population_digest": "digest", "entries": entries}), encoding="utf-8")
    source = tmp_path / "source"
    source.mkdir()
    (source / "schedule.json").write_text(json.dumps({"opponents": known_ids}), encoding="utf-8")
    result = select_holdout_opponents(
        population_path=population,
        source_run=source,
        output=tmp_path / "selected.json",
    )
    assert len(result["opponents"]) == 8
    assert sum(row["unknown_policy_hash"] for row in result["opponents"]) == 6
    assert [row["opponent_id"] for row in result["excluded_opponents"]] == [
        "unknown-6",
        "rule-v0-deck-holdout-seen",
    ]
    assert result["training_deck_hashes"] == [
        "known-deck-0", "known-deck-1", "known-deck-2", "known-deck-3",
    ]
    selected_decks = [
        row for row in result["opponents"]
        if row["opponent_type"] == "RULE_V0_DECK"
    ]
    assert all(row["unknown_deck_hash"] for row in selected_decks)
    assert result["opponents"][-1]["opponent_id"] == "rule-v0-deck-holdout-b"
