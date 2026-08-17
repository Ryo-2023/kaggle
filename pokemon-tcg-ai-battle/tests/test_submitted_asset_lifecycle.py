from scripts import run_submitted_asset_lifecycle as lifecycle


def test_score_only_records_are_not_runnable_assets() -> None:
    rows = lifecycle._static_records()
    assert {row["asset_id"] for row in rows} == {"rule-v0-score-only", "neural-student-score-only"}
    assert all(row["exactness"] == "SCORE_ONLY_IDENTITY_INCOMPLETE" for row in rows)


def test_dev_representatives_are_bounded_and_unique() -> None:
    assert len(lifecycle.DEV_REPRESENTATIVES) == 8
    assert len(set(lifecycle.DEV_REPRESENTATIVES)) == 8


def test_known_native_runtime_issue_is_not_called_an_agent_failure() -> None:
    assert "agents/water-box-search" in lifecycle.KNOWN_LOCAL_NATIVE_UNSUPPORTED


def test_score_provenance_keeps_exactness_separate_from_the_branch_tip() -> None:
    water = lifecycle.OFFICIAL_SCORE_RECORDS["agents/water-box-search"]
    assert water["exactness"] == "EXACT_COMMIT_ARCHIVE_MISSING"
    assert water["source_commit"] != "3759a983164732b7babdcca5c9620da83ebc8acf"
