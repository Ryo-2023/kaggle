from __future__ import annotations

import pytest

from scripts.run_cg_p1_observed_failure_confirmation_v1 import (
    confirmation_contract_v1,
    validate_confirmation_contract_v1,
)


def test_confirmation_contract_is_sealed_to_384_and_workers12() -> None:
    assert confirmation_contract_v1() == {
        "games_per_opponent_seat": 8,
        "requested_games_per_arm": 384,
        "workers": 12,
        "worker_recycle_games": 64,
        "authority": {
            "training_allowed": False,
            "promotion_allowed": False,
            "submission_allowed": False,
            "longrun_allowed": False,
        },
    }


def test_confirmation_contract_rejects_other_stage() -> None:
    with pytest.raises(ValueError):
        validate_confirmation_contract_v1(workers=1, worker_recycle_games=16)
