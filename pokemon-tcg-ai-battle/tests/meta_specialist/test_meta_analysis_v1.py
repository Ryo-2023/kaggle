"""正典 §2.4 の production メタレポートを、値で確かめる。

旧テストは `rank_distribution` が合計を返すことだけを見ていた。本テストは §2.4 が
求める粒度 (三段階集計、core/flex、bootstrap CI、順位感度、過去差分) が実際に
計算されるかを検査する。
"""

from __future__ import annotations

import pytest

from mage_ptcg.meta_specialist.meta_analysis_v1 import (
    CORE_ADOPTION_THRESHOLD_V1,
    RANK_STRATA_V1,
    DeckObservationV1,
    MetaAnalysisV1Error,
    build_band_report_v1,
    build_historical_diff_v1,
    build_meta_analysis_manifest_v1,
    matchup_matrix_v1,
    render_markdown_report_v1,
)


def _deck(
    submission_id: str,
    *,
    band: str = "Gold",
    rank: int = 1,
    archetype: str = "archaludon",
    package: str = "steel_core",
    exact: str = "hash_a",
    cards: dict[int, int] | None = None,
) -> DeckObservationV1:
    return DeckObservationV1(
        submission_id=submission_id, source_rank_band=band, rank_within_band=rank,
        archetype_id=archetype, support_package_id=package, exact_deck_hash=exact,
        card_counts=cards if cards is not None else {100: 4, 200: 2},
    )


def test_source_rank_band_is_the_kaggle_medal_not_a_strength() -> None:
    """正典 §13 は source_rank_band と local_strength_band の分離を求める。"""
    with pytest.raises(MetaAnalysisV1Error, match="not a measured strength"):
        _deck("a", band="high")


def test_three_level_aggregation_is_reported_separately() -> None:
    observations = [
        _deck("a", archetype="archaludon", package="steel_core", exact="x"),
        _deck("b", archetype="archaludon", package="steel_core", exact="y", rank=2),
        _deck("c", archetype="lucario", package="fight_core", exact="z", rank=3),
    ]

    report = build_band_report_v1("Gold", observations)

    assert {row.key: row.count for row in report.archetype_shares} == {
        "archaludon": 2, "lucario": 1
    }
    assert {row.key: row.count for row in report.support_package_shares} == {
        "steel_core": 2, "fight_core": 1
    }
    # exact 60-card hash は archetype より細かい: 3 件が別々に立つ。
    assert len(report.exact_deck_shares) == 3
    assert report.archetype_diversity == 2


def test_core_and_flex_split_uses_the_reported_threshold() -> None:
    # card 100 は全デッキ、card 300 は 1/4 だけ。
    observations = [
        _deck(f"s{index}", rank=index + 1, exact=f"h{index}",
              cards={100: 4, 300: 1} if index == 0 else {100: 4})
        for index in range(4)
    ]

    report = build_band_report_v1("Gold", observations)
    composition = report.compositions[0]

    assert 100 in composition.core_card_ids
    assert 300 in composition.flex_card_ids
    adoption = {item.card_id: item for item in composition.adoption}
    assert adoption[100].adoption_rate == 1.0 >= CORE_ADOPTION_THRESHOLD_V1
    assert adoption[300].adoption_rate == 0.25
    assert adoption[100].count_distribution == {"4": 4}


def test_observed_share_carries_a_bootstrap_interval_that_contains_it() -> None:
    observations = [
        _deck(f"s{index}", rank=index + 1, exact=f"h{index}",
              archetype="archaludon" if index < 6 else "lucario")
        for index in range(10)
    ]

    report = build_band_report_v1("Gold", observations)
    top = report.archetype_shares[0]

    assert top.key == "archaludon"
    assert top.share == pytest.approx(0.6)
    assert top.ci_low <= top.share <= top.ci_high
    assert top.ci_low < top.ci_high, "CI が幅を持っていない"


def test_bootstrap_interval_is_reproducible() -> None:
    observations = [
        _deck(f"s{index}", rank=index + 1, exact=f"h{index}",
              archetype="a" if index % 2 else "b")
        for index in range(12)
    ]

    first = build_band_report_v1("Gold", observations)
    second = build_band_report_v1("Gold", observations)

    assert [row.to_dict() for row in first.archetype_shares] == [
        row.to_dict() for row in second.archetype_shares
    ]


def test_rank_sensitivity_separates_the_top_of_a_band_from_its_tail() -> None:
    # 上位 3 件だけ archaludon、残り 6 件は lucario。
    observations = [
        DeckObservationV1(
            submission_id=f"s{index}", source_rank_band="Silver", rank_within_band=index + 1,
            archetype_id="archaludon" if index < 3 else "lucario",
            support_package_id="pkg", exact_deck_hash=f"h{index}", card_counts={100: 4},
        )
        for index in range(9)
    ]

    report = build_band_report_v1("Silver", observations)

    assert set(report.rank_sensitivity) == set(RANK_STRATA_V1)
    assert report.rank_sensitivity["upper"] == {"archaludon": 1.0}
    assert report.rank_sensitivity["lower"] == {"lucario": 1.0}
    # 集計だけ見ると archaludon は 1/3 だが、上位では 100% である。
    shares = {row.key: row.share for row in report.archetype_shares}
    assert shares["archaludon"] == pytest.approx(1 / 3)


def test_unclassified_and_multi_candidate_rates_are_reported_not_dropped() -> None:
    observations = [
        _deck("a"),
        DeckObservationV1(
            submission_id="b", source_rank_band="Gold", rank_within_band=2,
            archetype_id="", support_package_id="", exact_deck_hash="",
            classification="unclassified",
        ),
        DeckObservationV1(
            submission_id="c", source_rank_band="Gold", rank_within_band=3,
            archetype_id="", support_package_id="", exact_deck_hash="",
            classification="multi_candidate",
        ),
    ]

    report = build_band_report_v1("Gold", observations)

    assert report.teams == 3
    assert report.classified == 1
    assert report.coverage == pytest.approx(1 / 3)
    assert report.unclassified_rate == pytest.approx(1 / 3)
    assert report.multi_candidate_rate == pytest.approx(1 / 3)


def test_a_manifest_needs_the_census_hash_it_came_from() -> None:
    with pytest.raises(MetaAnalysisV1Error, match="census hash"):
        build_meta_analysis_manifest_v1(
            manifest_id="m1", census_id="c1", census_content_hash="",
            classifier_version="v1", observations=[_deck("a")],
        )


def test_a_manifest_covers_every_band_present_in_the_observations() -> None:
    manifest = build_meta_analysis_manifest_v1(
        manifest_id="m1", census_id="c1", census_content_hash="deadbeef",
        classifier_version="classifier-v1",
        observations=[_deck("a"), _deck("b", band="Bronze", exact="q")],
    )

    assert [report.source_rank_band for report in manifest.bands] == ["Gold", "Bronze"]
    assert manifest.band("Bronze").teams == 1
    assert manifest.content_hash() == manifest.content_hash()


def test_historical_diff_refuses_a_classifier_version_mismatch() -> None:
    previous = build_meta_analysis_manifest_v1(
        manifest_id="m0", census_id="c0", census_content_hash="aa",
        classifier_version="classifier-v1", observations=[_deck("a")],
    )

    with pytest.raises(MetaAnalysisV1Error, match="classifier version"):
        build_historical_diff_v1(
            previous=previous, current_bands=(), classifier_version="classifier-v2"
        )


def test_historical_diff_reports_inflow_outflow_and_deltas() -> None:
    previous = build_meta_analysis_manifest_v1(
        manifest_id="m0", census_id="c0", census_content_hash="aa",
        classifier_version="classifier-v1",
        observations=[_deck("a", archetype="lucario")],
    )
    current = build_meta_analysis_manifest_v1(
        manifest_id="m1", census_id="c1", census_content_hash="bb",
        classifier_version="classifier-v1",
        observations=[_deck("b", archetype="archaludon")],
        previous=previous,
    )

    diff = current.historical_diff
    assert diff is not None
    assert diff.inflow_archetypes == ("Gold/archaludon",)
    assert diff.outflow_archetypes == ("Gold/lucario",)
    assert diff.share_deltas["Gold/archaludon"] == pytest.approx(1.0)
    assert diff.share_deltas["Gold/lucario"] == pytest.approx(-1.0)


def test_a_matchup_matrix_is_refused_without_replay_outcomes_and_provenance() -> None:
    """正典 §2.4: deck share から相性因果を推測しない。"""
    with pytest.raises(MetaAnalysisV1Error, match="forbids inferring matchup causality"):
        matchup_matrix_v1(replay_outcomes=None, opponent_provenance=None)


def test_markdown_report_states_the_census_and_the_intervals() -> None:
    manifest = build_meta_analysis_manifest_v1(
        manifest_id="m1", census_id="census-2026-08-01", census_content_hash="c0ffee" * 10,
        classifier_version="classifier-v1",
        observations=[_deck("a"), _deck("b", rank=2, exact="y")],
    )

    text = render_markdown_report_v1(manifest)

    assert "census-2026-08-01" in text
    assert "classifier-v1" in text
    assert "bootstrap 95% CI" in text
    assert "## Gold" in text
