"""TDD contracts for the research-only V4 policy-drift audit."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "audit_v4_policy_drift_v1.py"


def _load_runner():
    spec = importlib.util.spec_from_file_location("audit_v4_policy_drift_v1", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_symmetric_js_is_finite_for_masked_domains() -> None:
    runner = _load_runner()

    metrics = runner.compare_logit_rows_v1([
        {
            "baseline_logits": (2.0, 0.0, float("-inf")),
            "candidate_logits": (0.0, 2.0, float("-inf")),
            "baseline_action_types": ("ATTACK", "END", "RETREAT"),
            "candidate_action_types": ("ATTACK", "END", "RETREAT"),
            "root": True,
            "sequence_index": 0,
            "group_index": 0,
        },
    ])

    assert metrics["rows"] == 1
    assert metrics["top1_action_change_count"] == 1
    assert 0.0 < metrics["mean_js"] <= 1.0
    assert metrics["by_domain_bucket"]["3"]["rows"] == 1


def test_first_divergence_and_action_type_buckets_are_reported() -> None:
    runner = _load_runner()
    rows = [
        {
            "baseline_logits": (3.0, 0.0),
            "candidate_logits": (3.0, 0.0),
            "baseline_action_types": ("ATTACK", "END"),
            "candidate_action_types": ("ATTACK", "END"),
            "root": True,
            "sequence_index": 4,
            "group_index": 0,
        },
        {
            "baseline_logits": (3.0, 0.0),
            "candidate_logits": (0.0, 3.0),
            "baseline_action_types": ("ATTACK", "END"),
            "candidate_action_types": ("ATTACK", "END"),
            "root": False,
            "sequence_index": 4,
            "group_index": 1,
        },
        {
            "baseline_logits": (0.0, 3.0),
            "candidate_logits": (3.0, 0.0),
            "baseline_action_types": ("END", "ATTACK"),
            "candidate_action_types": ("END", "ATTACK"),
            "root": True,
            "sequence_index": 9,
            "group_index": 0,
        },
    ]

    metrics = runner.compare_logit_rows_v1(rows)

    assert metrics["top1_action_change_count"] == 2
    assert metrics["root_action_change_count"] == 1
    assert metrics["first_divergence_positions"] == {"1": 1, "0": 1}
    assert metrics["by_baseline_top1_action_type"]["ATTACK"]["changed"] == 2
    assert metrics["by_baseline_top1_action_type"]["ATTACK"]["rows"] == 3


def test_hidden_and_parameter_deltas_use_only_numeric_arrays() -> None:
    runner = _load_runner()
    hidden = runner.summarize_hidden_deltas_v1([
        {"baseline_hidden": (1.0, 0.0), "candidate_hidden": (0.0, 1.0)},
    ])

    assert hidden["rows"] == 1
    assert hidden["mean_l2"] == pytest.approx(2 ** 0.5)
    assert hidden["mean_cosine"] == pytest.approx(0.0)

    deltas = runner.parameter_delta_v1(
        {"memory.weight": (1.0, 2.0), "head.bias": (0.0,)},
        {"memory.weight": (2.0, 2.0), "head.bias": (1.0,)},
    )
    assert set(deltas) == {"memory", "head"}
    assert deltas["memory"]["tensor_count"] == 1
    assert deltas["head"]["mean_abs"] == pytest.approx(1.0)


def test_runtime_features_reject_opponent_and_seat_fields() -> None:
    runner = _load_runner()
    with pytest.raises(runner.PolicyDriftAuditError, match="opponent|seat"):
        runner.compare_logit_rows_v1([
            {
                "baseline_logits": (1.0, 0.0),
                "candidate_logits": (1.0, 0.0),
                "baseline_action_types": ("ATTACK", "END"),
                "candidate_action_types": ("ATTACK", "END"),
                "root": True,
                "sequence_index": 0,
                "group_index": 0,
                "opponent_id": "must-not-be-a-feature",
            },
        ])


def test_bounded_row_cap_is_deterministic() -> None:
    runner = _load_runner()
    rows = {key: {"sequence_index": key[0]} for key in ((0, 0, 0), (0, 1, 0), (1, 0, 0))}
    capped = runner.truncate_replay_rows_v1(rows, max_rows=2)
    assert tuple(capped) == ((0, 0, 0), (0, 1, 0))
