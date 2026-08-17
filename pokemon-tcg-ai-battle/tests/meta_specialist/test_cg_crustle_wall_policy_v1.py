from __future__ import annotations

import hashlib

import pytest

from mage_ptcg.meta_specialist.cg_crustle_wall_policy_v1 import (
    BASE_SOURCE_SHA256,
    CANDIDATE_ID,
    render_variant_source,
    variant_source_sha256,
)


def test_crustle_wall_variant_is_bound_to_immutable_cg_source() -> None:
    source = render_variant_source(CANDIDATE_ID)
    assert CANDIDATE_ID == "cg-crustle-wall-v1"
    assert "RESEARCH_VARIANT: cg-crustle-wall-v1" in source
    assert "_CG_POLICY_BASE_MAIN_SCORE" in source
    assert "agent(obs_dict: dict)" in source
    assert BASE_SOURCE_SHA256 == "617a23c060084c8b2601800b4f729238563925165f3520628d938eab065aebef"


def test_crustle_wall_variant_uses_public_crustle_and_non_ex_attack_ids() -> None:
    source = render_variant_source(CANDIDATE_ID)
    assert "CRUSTLE = 345" in source
    assert "{976, 977, 978, 979, 980, 981}" in source
    assert "{982, 983}" in source
    assert "OptionType.ATTACK" in source


def test_crustle_wall_variant_hash_is_deterministic() -> None:
    source = render_variant_source(CANDIDATE_ID).encode("utf-8")
    assert variant_source_sha256(CANDIDATE_ID) == hashlib.sha256(source).hexdigest()


def test_unknown_crustle_wall_variant_fails_closed() -> None:
    with pytest.raises(ValueError, match="unknown"):
        render_variant_source("cg-crustle-wall-unknown")
