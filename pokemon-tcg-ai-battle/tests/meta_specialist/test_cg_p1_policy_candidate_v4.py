from __future__ import annotations

import pytest


def test_item_tempo_variants_render_from_hash_bound_p1_source() -> None:
    from mage_ptcg.meta_specialist.cg_p1_policy_candidate_v4 import (
        BASE_SOURCE_SHA256,
        VARIANT_IDS,
        render_p1_variant_source_v4,
    )

    assert len(BASE_SOURCE_SHA256) == 64
    assert VARIANT_IDS == (
        "cg-p1-gravity-stage2-lethal-v1",
        "cg-p1-premium-power-lethal-v1",
        "cg-p1-switch-powered-bench-v1",
    )
    markers = {
        "cg-p1-gravity-stage2-lethal-v1": ("GRAVITY", "preEvolution"),
        "cg-p1-premium-power-lethal-v1": ("PREMIUM_POWER", "damage + bonus"),
        "cg-p1-switch-powered-bench-v1": ("SWITCH", "powered bench"),
    }
    for candidate_id in VARIANT_IDS:
        source = render_p1_variant_source_v4(candidate_id)
        compile(source, f"<{candidate_id}>", "exec")
        assert "RESEARCH_VARIANT" in source
        for marker in markers[candidate_id]:
            assert marker in source


def test_unknown_variant_fails_closed() -> None:
    from mage_ptcg.meta_specialist.cg_p1_policy_candidate_v4 import render_p1_variant_source_v4

    with pytest.raises(ValueError):
        render_p1_variant_source_v4("unknown")
