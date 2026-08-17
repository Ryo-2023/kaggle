from __future__ import annotations

import pytest


def test_attack_cooldown_variant_renders_from_hash_bound_p1_source() -> None:
    from mage_ptcg.meta_specialist.cg_p1_policy_candidate_v6 import (
        BASE_SOURCE_SHA256,
        VARIANT_IDS,
        render_p1_variant_source_v6,
    )

    assert len(BASE_SOURCE_SHA256) == 64
    assert VARIANT_IDS == ("cg-p1-aura-jab-cooldown-safe-v1",)
    source = render_p1_variant_source_v6(VARIANT_IDS[0])
    compile(source, "<cg-p1-aura-jab-cooldown-safe-v1>", "exec")
    assert "RESEARCH_VARIANT" in source
    assert "AURA_JAB" in source
    assert "MEGA_BRAVE" in source
    assert "discard" in source


def test_unknown_variant_fails_closed() -> None:
    from mage_ptcg.meta_specialist.cg_p1_policy_candidate_v6 import render_p1_variant_source_v6

    with pytest.raises(ValueError):
        render_p1_variant_source_v6("unknown")
