from __future__ import annotations

import pytest


def test_active_threat_attach_variant_is_hash_bound_and_compilable() -> None:
    from mage_ptcg.meta_specialist.cg_p1_active_threat_attach_v1 import (
        BASE_SOURCE_SHA256,
        VARIANT_IDS,
        render_p1_variant_source_v1,
    )

    assert len(BASE_SOURCE_SHA256) == 64
    assert VARIANT_IDS == ("cg-p1-active-threat-attach-v1",)
    source = render_p1_variant_source_v1(VARIANT_IDS[0])
    compile(source, "<cg-p1-active-threat-attach-v1>", "exec")
    assert "RESEARCH_VARIANT: cg-p1-active-threat-attach-v1" in source
    assert "_CG_P1_ACTIVE_THREAT_BASE_MAIN_SCORE" in source
    assert "ATTACH" in source


def test_unknown_active_threat_attach_variant_fails_closed() -> None:
    from mage_ptcg.meta_specialist.cg_p1_active_threat_attach_v1 import render_p1_variant_source_v1

    with pytest.raises(ValueError):
        render_p1_variant_source_v1("unknown")


def test_active_threat_attach_screen_wires_the_variant_runner() -> None:
    from scripts.run_cg_p1_active_threat_attach_screen_v1 import VARIANT_IDS as SCREEN_VARIANT_IDS

    assert SCREEN_VARIANT_IDS == ("cg-p1-active-threat-attach-v1",)
