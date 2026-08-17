"""Tests for §5 Census seal and sensitivity analysis."""

import pytest
from mage_ptcg.meta_specialist.census_v1 import (
    CensusRecordV1,
    CensusV1Error,
    calculate_missing_sensitivity_v1,
    verify_census_seal_v1,
)


def test_verify_census_seal_pass():
    records = [
        CensusRecordV1("1", "Gold", True),
        CensusRecordV1("2", "Gold", True),
        CensusRecordV1("3", "Silver", True),
        CensusRecordV1("4", "Bronze", True),
    ]
    report = verify_census_seal_v1(records)
    assert report.is_sealed
    assert report.gold_coverage_rate == 1.0
    assert report.total_coverage_rate == 1.0


def test_verify_census_seal_gold_incomplete_fails():
    records = [
        CensusRecordV1("1", "Gold", True),
        CensusRecordV1("2", "Gold", False, ("deck_id",)),
    ]
    report = verify_census_seal_v1(records)
    assert not report.is_sealed
    assert report.gold_coverage_rate == 0.5


def test_verify_census_seal_empty_raises():
    with pytest.raises(CensusV1Error):
        verify_census_seal_v1([])


def test_calculate_missing_sensitivity():
    records = [
        CensusRecordV1("1", "Gold", True),
        CensusRecordV1("2", "Silver", False, ("action_key",)),
        CensusRecordV1("3", "Bronze", False, ("action_key", "policy_id")),
    ]
    sensitivity = calculate_missing_sensitivity_v1(records)
    assert sensitivity["action_key"] == pytest.approx(2 / 3)
    assert sensitivity["policy_id"] == pytest.approx(1 / 3)
