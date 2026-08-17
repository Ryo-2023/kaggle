from __future__ import annotations


def test_package_common24_binds_the_package_schema() -> None:
    from scripts import run_rule_v0_root_deck_package_common24_v1 as runner

    assert runner.common.WEIGHTED_SCHEMA == runner.package.SCHEMA
    assert runner.common.COMMON24_SCHEMA == runner.SCHEMA


def test_package_common24_rejects_non_default_workers() -> None:
    from scripts import run_rule_v0_root_deck_package_common24_v1 as runner

    try:
        runner.execute_common24(source_root=runner.package.ROOT, output=runner.package.ROOT, workers=1)
    except ValueError as exc:
        assert "workers=12" in str(exc)
    else:
        raise AssertionError("workers override was not rejected")
