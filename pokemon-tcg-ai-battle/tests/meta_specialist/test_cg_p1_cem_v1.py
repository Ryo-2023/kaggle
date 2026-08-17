from __future__ import annotations

import json
from pathlib import Path

import pytest

from mage_ptcg.meta_specialist.cg_p1_cem_v1 import (
    CemCampaignConfig,
    CemState,
    aggregate_candidate_rows,
    load_latest_checkpoint,
    rank_valid_results,
    sample_population,
    save_checkpoint,
    update_distribution,
)
from mage_ptcg.meta_specialist.cg_p1_parameterization_v1 import P1ParameterConfig


def test_campaign_defaults_match_weekend_contract() -> None:
    config = CemCampaignConfig()
    config.validate()
    assert config.population_size == 24
    assert config.elite_count == 6
    assert config.generations == 6
    assert config.games_per_candidate == 48
    assert config.workers == 12
    assert config.worker_recycle_games == 16


def test_sampling_and_update_are_deterministic_and_fault_closed() -> None:
    center = P1ParameterConfig.default()
    first = sample_population(center, generation=0, population_size=8, seed=17)
    second = sample_population(center, generation=0, population_size=8, seed=17)
    assert first == second
    assert first[0] == center
    results = [
        {"config": item, "objective": float(1.0 - index / 10), "valid": True, "faults": 0}
        for index, item in enumerate(first)
    ]
    results.append({"config": P1ParameterConfig.default(), "objective": 99.0, "valid": False, "faults": 1})
    ranked = rank_valid_results(results, elite_count=3)
    assert len(ranked) == 3
    new_center, scales = update_distribution(center, ranked)
    new_center.validate()
    assert set(scales) == set(center.as_dict())
    assert new_center != center or any(value != 0 for value in scales.values())


def test_objective_keeps_faults_in_denominator_and_weights_opponents() -> None:
    aggregate = aggregate_candidate_rows(
        [
            {"opponent_id": "a", "outcome": "win", "seat": 0},
            {"opponent_id": "a", "outcome": "loss", "seat": 1},
            {"opponent_id": "b", "outcome": "fault", "seat": 0},
            {"opponent_id": "b", "outcome": "win", "seat": 1},
        ],
        weights={"a": 0.8, "b": 0.2},
    )
    assert aggregate["requested_games"] == 4
    assert aggregate["faults"] == 1
    assert aggregate["valid"] is False
    assert aggregate["objective"] < 0.5


def test_one_win_in_small_pilot_seat_is_not_catastrophic() -> None:
    rows = [
        *({"opponent_id": "a", "outcome": "win", "seat": 0} for _ in range(1)),
        *({"opponent_id": "a", "outcome": "loss", "seat": 0} for _ in range(23)),
        *({"opponent_id": "a", "outcome": "win", "seat": 1} for _ in range(1)),
        *({"opponent_id": "a", "outcome": "loss", "seat": 1} for _ in range(23)),
    ]
    aggregate = aggregate_candidate_rows(rows, weights={"a": 1.0})
    assert aggregate["seat_collapse"] is False


def test_objective_exposes_opponent_by_seat_rates() -> None:
    rows = [
        {"opponent_id": "a", "outcome": "win", "seat": 0},
        {"opponent_id": "a", "outcome": "loss", "seat": 1},
        {"opponent_id": "b", "outcome": "draw", "seat": 0},
    ]

    aggregate = aggregate_candidate_rows(rows, weights={"a": 1.0, "b": 1.0})

    assert aggregate["opponent_seat_rates"] == {
        "a": {"0": 1.0, "1": 0.0},
        "b": {"0": 0.5},
    }


def test_checkpoint_is_no_clobber_and_resumable(tmp_path: Path) -> None:
    state = CemState(
        generation=0,
        center=P1ParameterConfig.default(),
        scales={name: 1.0 for name in P1ParameterConfig.default().as_dict()},
        next_candidate_index=3,
        evaluated=[{"candidate_id": "c0", "valid": True}],
        campaign_identity={"split_sha256": "a" * 64},
    )
    path = save_checkpoint(tmp_path, state)
    assert path.is_file()
    assert load_latest_checkpoint(tmp_path).next_candidate_index == 3
    with pytest.raises(FileExistsError):
        save_checkpoint(tmp_path, state)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "cg-p1-cem-state-v1"
