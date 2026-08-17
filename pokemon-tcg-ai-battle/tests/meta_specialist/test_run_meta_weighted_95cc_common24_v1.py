from __future__ import annotations


def test_95cc_common24_binds_the_neighborhood_schema_and_parallel_default() -> None:
    from scripts import run_meta_weighted_95cc_common24_v1 as runner

    assert runner.SCHEMA == "meta-specialist-meta-weighted-95cc-neighborhood-common24-v1"
    assert runner.common.SCHEMA == runner.SCHEMA


def test_95cc_common24_rejects_non_default_workers() -> None:
    from scripts import run_meta_weighted_95cc_common24_v1 as runner

    try:
        runner.execute(source_root=runner.parent_lane.ROOT, output=runner.parent_lane.ROOT, workers=1)
    except ValueError as exc:
        assert "workers=12" in str(exc)
    else:
        raise AssertionError("workers override was not rejected")
