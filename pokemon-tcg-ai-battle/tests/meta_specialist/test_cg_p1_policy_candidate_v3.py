from __future__ import annotations

import pytest


def test_supporter_variants_render_from_hash_bound_p1_source() -> None:
    from mage_ptcg.meta_specialist.cg_p1_policy_candidate_v3 import (
        BASE_SOURCE_SHA256,
        VARIANT_IDS,
        render_p1_variant_source_v3,
    )

    assert len(BASE_SOURCE_SHA256) == 64
    assert VARIANT_IDS == (
        "cg-p1-lillie-early-v1",
        "cg-p1-boss-ko-v1",
        "cg-p1-carmine-lowhand-v1",
    )
    markers = {
        "cg-p1-lillie-early-v1": ("LILLIE", "turn <= 2"),
        "cg-p1-boss-ko-v1": ("BOSS", "hp <= 150"),
        "cg-p1-carmine-lowhand-v1": ("CARMINE", "hand_count <= 4"),
    }
    for candidate_id in VARIANT_IDS:
        source = render_p1_variant_source_v3(candidate_id)
        compile(source, f"<{candidate_id}>", "exec")
        assert "RESEARCH_VARIANT" in source
        for marker in markers[candidate_id]:
            assert marker in source


def test_unknown_variant_fails_closed() -> None:
    from mage_ptcg.meta_specialist.cg_p1_policy_candidate_v3 import render_p1_variant_source_v3

    with pytest.raises(ValueError):
        render_p1_variant_source_v3("unknown")
