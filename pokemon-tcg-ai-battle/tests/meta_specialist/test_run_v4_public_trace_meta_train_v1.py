"""Contracts for the research-only V4 public trace META_TRAIN runner."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mage_ptcg.meta_specialist.opponent_pool_v1 import OpponentInstanceV1


def _load_module():
    import importlib

    return importlib.import_module("scripts.run_v4_public_trace_meta_train_v1")


def test_meta_train_config_requires_exact_permission_and_pool_binding() -> None:
    module = _load_module()
    config = json.loads(
        Path("configs/meta_specialist/performance_first_broad_pool_v1.json").read_text(
            encoding="utf-8"
        )
    )
    verified = module.validate_meta_train_config_v1(
        config,
        config_sha256="832273ff656280d2556c9df09a9a3db9f2564a181be78a3e658509d3b396209b",
        pool_manifest_sha256="e0013cf31b3e6e24db54591faeef6f092b9ebf85247bd0e57598eb8d447f20ca",
    )
    assert len(verified) == 24
    assert verified[0] == "aman_crustleaware_fighting"

    forged = dict(config)
    forged["local_eval_only"] = False
    with pytest.raises(ValueError, match="local_eval_only"):
        module.validate_meta_train_config_v1(
            forged,
            config_sha256="832273ff656280d2556c9df09a9a3db9f2564a181be78a3e658509d3b396209b",
            pool_manifest_sha256="e0013cf31b3e6e24db54591faeef6f092b9ebf85247bd0e57598eb8d447f20ca",
        )


def test_pool_entries_reject_non_local_eval_only_and_duplicate_identity() -> None:
    module = _load_module()
    entries = {
        "a": OpponentInstanceV1(
            opponent_id="a", deck_csv_path="/a/deck.csv", policy_path="/a/main.py",
            canonical_deck_hash="a" * 64, policy_hash="b" * 64,
            usage_boundary="local_eval_only", source="public", mean_decision_ms=1.0,
        ),
        "b": OpponentInstanceV1(
            opponent_id="b", deck_csv_path="/b/deck.csv", policy_path="/b/main.py",
            canonical_deck_hash="d" * 64, policy_hash="b" * 64,
            usage_boundary="local_eval_only", source="public", mean_decision_ms=1.0,
        ),
    }
    with pytest.raises(ValueError, match="duplicate policy"):
        module.verify_meta_train_pool_entries_v1(("a", "b"), entries)

    entries["b"] = OpponentInstanceV1(
        opponent_id="b", deck_csv_path="/b/deck.csv", policy_path="/b/main.py",
        canonical_deck_hash="d" * 64, policy_hash="c" * 64,
        usage_boundary="submission", source="public", mean_decision_ms=1.0,
    )
    with pytest.raises(ValueError, match="local_eval_only"):
        module.verify_meta_train_pool_entries_v1(("a", "b"), entries)


def test_trace_rows_are_public_and_outcome_bound() -> None:
    module = _load_module()
    row = module.bind_public_trace_outcome_v1(
        {
            "opponent_id": "a",
            "seat": 1,
            "seed": 42,
            "decision_index": 0,
            "action_types": ["ATTACK"],
            "trace_variant": "public-v1-redacted",
        },
        game_id="g0",
        outcome="win",
        winner=1,
    )
    assert row["game_id"] == "g0"
    assert row["outcome"] == "win"
    assert row["winner"] == 1
    assert "private_state" not in json.dumps(row)
    assert "raw_observation" not in json.dumps(row)


def test_trace_summary_counts_faults_in_requested_denominator() -> None:
    module = _load_module()
    summary = module.aggregate_meta_train_rows_v1(
        [
            {"outcome": "win", "seat": 0, "opponent_id": "a"},
            {"outcome": "loss", "seat": 1, "opponent_id": "a"},
            {"outcome": "fault", "seat": 0, "opponent_id": "b"},
        ]
    )
    assert summary["requested_games"] == 3
    assert summary["completed_games"] == 2
    assert summary["faults"] == 1
    assert summary["score_rate"] == pytest.approx(1 / 3)
