from __future__ import annotations

import pytest


def test_known_variants_render_from_hash_bound_p1_source() -> None:
    from mage_ptcg.meta_specialist.cg_p1_policy_candidate_v2 import (
        BASE_SOURCE_SHA256,
        VARIANT_IDS,
        render_p1_variant_source_v2,
    )

    assert len(BASE_SOURCE_SHA256) == 64
    assert VARIANT_IDS == (
        "cg-p1-search-priority-v3",
        "cg-p1-gust-ko-v3",
        "cg-p1-carmine-tempo-v1",
        "cg-p1-carmine-tempo-v2",
    )
    for candidate_id in VARIANT_IDS:
        source = render_p1_variant_source_v2(candidate_id)
        compile(source, f"<{candidate_id}>", "exec")
        assert "RESEARCH_VARIANT" in source


def test_unknown_variant_fails_closed() -> None:
    from mage_ptcg.meta_specialist.cg_p1_policy_candidate_v2 import render_p1_variant_source_v2

    with pytest.raises(ValueError):
        render_p1_variant_source_v2("unknown")
