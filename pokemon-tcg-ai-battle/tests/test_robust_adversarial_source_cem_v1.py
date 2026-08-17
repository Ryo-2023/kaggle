from __future__ import annotations

import json

import pytest

from scripts.run_robust_adversarial_source_cem_v1 import (
    DEFAULT_REFERENCE_SPECS,
    _parse_reference,
    _load_initial_config,
    _passes_promotion_gate_v1,
)
from mage_ptcg.meta_specialist.cg_p1_parameterization_v1 import P1ParameterConfig
from mage_ptcg.opponent_ingest.robust_adversarial_source_cem_v1 import (
    RobustAdversarialSourceError,
    aggregate_portfolio_source_rows_v1,
)


def _rows(*, p1: tuple[str, ...], rule: tuple[str, ...], public: tuple[str, ...]):
    rows = []
    for opponent_id, outcomes in (
        ("p1", p1),
        ("rule", rule),
        ("public", public),
    ):
        for seat, outcome in enumerate(outcomes):
            rows.append(
                {
                    "policy_id": "candidate",
                    "opponent_id": opponent_id,
                    "seat": seat % 2,
                    "outcome": outcome,
                }
            )
    return rows


def test_portfolio_objective_uses_mean_and_worst_reference() -> None:
    rows = _rows(
        p1=("win", "win", "loss", "loss"),
        rule=("win", "loss", "loss", "win"),
        public=("win", "win", "win", "loss"),
    )

    result = aggregate_portfolio_source_rows_v1(
        rows,
        candidate_policy_id="candidate",
        reference_ids=("p1", "rule", "public"),
        seat_gap_limit=1.0,
    )

    assert result["faults"] == 0
    assert result["reference_count"] == 3
    assert result["mean_source_score"] == pytest.approx(7 / 12)
    assert result["min_reference_score"] == pytest.approx(0.5)
    assert result["robust_objective"] == pytest.approx(13 / 24)
    assert result["valid"] is True
    assert result["private_fields_used"] is False


def test_portfolio_rejects_fault_or_unbalanced_reference() -> None:
    rows = _rows(
        p1=("win", "win", "loss", "loss"),
        rule=("fault", "loss", "loss", "loss"),
        public=("win", "win", "loss", "loss"),
    )

    result = aggregate_portfolio_source_rows_v1(
        rows,
        candidate_policy_id="candidate",
        reference_ids=("p1", "rule", "public"),
    )

    assert result["faults"] == 1
    assert result["valid"] is False
    assert result["reference_results"]["rule"]["faults"] == 1


def test_portfolio_requires_exact_reference_set() -> None:
    rows = _rows(
        p1=("win", "loss"),
        rule=("win", "loss"),
        public=("win", "loss"),
    )

    with pytest.raises(RobustAdversarialSourceError, match="duplicate reference"):
        aggregate_portfolio_source_rows_v1(
            rows,
            candidate_policy_id="candidate",
            reference_ids=("p1", "p1"),
        )

    with pytest.raises(RobustAdversarialSourceError, match="no matching rows"):
        aggregate_portfolio_source_rows_v1(
            rows,
            candidate_policy_id="candidate",
            reference_ids=("p1", "missing"),
        )


def test_reference_parser_is_explicit_and_path_bound() -> None:
    spec = _parse_reference("fixed-id=./runs/reference")

    assert spec.reference_id == "fixed-id"
    assert spec.package_root.is_absolute()
    assert spec.source == "fixed_reference:fixed-id"

    with pytest.raises(ValueError, match="ID=PACKAGE_ROOT"):
        _parse_reference("missing-separator")


def test_promotion_gate_requires_fresh_seat_safe_validation() -> None:
    train = {"valid": True, "mean_source_score": 0.75}
    validation = {
        "valid": True,
        "mean_source_score": 0.6,
        "min_reference_score": 0.5,
        "seat_safe": True,
    }
    assert _passes_promotion_gate_v1(train, validation) is True

    validation["seat_safe"] = False
    assert _passes_promotion_gate_v1(train, validation) is False

    validation["seat_safe"] = True
    validation["min_reference_score"] = 0.125
    assert _passes_promotion_gate_v1(train, validation) is False


def test_default_reference_portfolio_is_self_owned() -> None:
    assert any(value.startswith("balanced-independent-v1=") for value in DEFAULT_REFERENCE_SPECS)
    assert not any(value.startswith("public-reference-v1=") for value in DEFAULT_REFERENCE_SPECS)


def test_load_initial_config_accepts_raw_or_wrapped_mapping(tmp_path) -> None:
    config = P1ParameterConfig.default().as_dict()
    raw = tmp_path / "raw.json"
    raw.write_text(json.dumps(config), encoding="utf-8")
    wrapped = tmp_path / "wrapped.json"
    wrapped.write_text(json.dumps({"config": config}), encoding="utf-8")

    assert _load_initial_config(raw).config_sha256() == P1ParameterConfig.default().config_sha256()
    assert _load_initial_config(wrapped).config_sha256() == P1ParameterConfig.default().config_sha256()
