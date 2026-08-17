from __future__ import annotations

import hashlib
import json
from pathlib import Path

from mage_ptcg.meta_specialist.research_logit_ensemble_v1 import (
    ResearchLogitEnsemblePolicyFactoryV1,
)
from scripts.measure_v4_research_ensemble_strength import _sha256_json


class _Policy:
    def __init__(self) -> None:
        self.reset_count = 0

    def reset(self) -> None:
        self.reset_count += 1

    def begin_decision(self):  # pragma: no cover - factory contract only
        raise AssertionError

    def policy_telemetry(self):  # pragma: no cover - factory contract only
        raise AssertionError


class _Factory:
    def __init__(self) -> None:
        self.calls = 0

    def new_policy(self):
        self.calls += 1
        return _Policy()


def test_research_factory_returns_fresh_policy_and_hash_bound_telemetry() -> None:
    identity = "1" * 64
    lineage = "2" * 64
    factory = ResearchLogitEnsemblePolicyFactoryV1(
        [_Factory(), _Factory()],
        reset_mode="normal",
        policy_identity=identity,
        checkpoint_lineage_id=lineage,
    )
    first = factory.new_policy()
    second = factory.new_policy()
    assert first is not second
    assert first.member_count == second.member_count == 2
    telemetry = first.policy_telemetry()
    assert telemetry.policy_identity == identity
    assert telemetry.checkpoint_lineage_id == lineage
    assert telemetry.candidate_class == "checkpointed_specialist"


def test_ensemble_identity_is_ordered_and_hashable() -> None:
    members = [{"file_sha256": "a" * 64}, {"file_sha256": "b" * 64}]
    first = _sha256_json({"schema": "x", "members": members, "reset_mode": "normal"})
    second = _sha256_json({"schema": "x", "members": list(reversed(members)), "reset_mode": "normal"})
    assert first != second
    assert len(first) == len(second) == 64
    assert all(char in "0123456789abcdef" for char in first + second)


def test_duplicate_members_are_explicitly_a_recurrence_ablation_input() -> None:
    members = [{"file_sha256": "a" * 64}, {"file_sha256": "a" * 64}]
    identity = _sha256_json({"schema": "x", "members": members, "reset_mode": "action"})
    assert identity != _sha256_json({"schema": "x", "members": members, "reset_mode": "normal"})
