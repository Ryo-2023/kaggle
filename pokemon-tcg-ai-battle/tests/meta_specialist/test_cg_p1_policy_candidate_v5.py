from __future__ import annotations

import pytest


def test_lunar_cycle_variants_render_from_hash_bound_p1_source() -> None:
    from mage_ptcg.meta_specialist.cg_p1_policy_candidate_v5 import (
        BASE_SOURCE_SHA256,
        VARIANT_IDS,
        render_p1_variant_source_v5,
    )

    assert len(BASE_SOURCE_SHA256) == 64
    assert VARIANT_IDS == (
        "cg-p1-lunar-cycle-lowhand3-v1",
        "cg-p1-lunar-cycle-lowhand4-v1",
        "cg-p1-lunar-cycle-lowhand5-v1",
    )
    markers = {
        "cg-p1-lunar-cycle-lowhand3-v1": ("LUNATONE", "hand_count <= 3"),
        "cg-p1-lunar-cycle-lowhand4-v1": ("LUNATONE", "hand_count <= 4"),
        "cg-p1-lunar-cycle-lowhand5-v1": ("LUNATONE", "hand_count <= 5"),
    }
    for candidate_id in VARIANT_IDS:
        source = render_p1_variant_source_v5(candidate_id)
        compile(source, f"<{candidate_id}>", "exec")
        assert "RESEARCH_VARIANT" in source
        for marker in markers[candidate_id]:
            assert marker in source


def test_unknown_variant_fails_closed() -> None:
    from mage_ptcg.meta_specialist.cg_p1_policy_candidate_v5 import render_p1_variant_source_v5

    with pytest.raises(ValueError):
        render_p1_variant_source_v5("unknown")
