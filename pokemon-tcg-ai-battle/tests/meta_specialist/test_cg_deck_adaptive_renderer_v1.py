from __future__ import annotations

from pathlib import Path

import pytest

from mage_ptcg.meta_specialist.cg_deck_adaptive_renderer_v1 import (
    DeckAdaptiveConfig,
    render_deck_adaptive_source,
    materialize_deck_adaptive_package,
)
from mage_ptcg.meta_specialist.self_owned_cg_package_v1 import verify_self_owned_cg_package_v1


ROOT = Path(__file__).resolve().parents[2]
RUNTIME_PACKAGE = (
    ROOT
    / "runs/final-sprint-autonomous/cg-policy-screen-v1-retry-safe4-20260814"
    / "candidates/cg-lethal-target-v1/package"
)
DECK_PACKAGE = ROOT / "runs/cg-self-owned-action-conditioned-v2-lucario-20260816/deck-generation/lucario-v5-ac-00/package"


def _deck_ids() -> tuple[int, ...]:
    return tuple(int(token) for token in (DECK_PACKAGE / "deck.csv").read_text(encoding="utf-8").split())


def test_deck_adaptive_config_is_bounded_and_hashable() -> None:
    config = DeckAdaptiveConfig.from_mapping({"attack_damage_weight_milli": 1400})
    assert config.as_dict()["attack_damage_weight_milli"] == 1400
    assert len(config.config_sha256()) == 64
    with pytest.raises(ValueError):
        DeckAdaptiveConfig.from_mapping({"attack_damage_weight_milli": 9001})


def test_rendered_source_is_independent_public_state_policy() -> None:
    source = render_deck_adaptive_source(
        DeckAdaptiveConfig.default(), deck_ids=_deck_ids(), candidate_id="test-adaptive"
    )
    compile(source, "<deck-adaptive>", "exec")
    assert "ROOT_DECK = (" in source
    assert "from cg.api import" in source
    assert "all_card_data" in source
    assert "cg-p1-action-conditioned" not in source
    assert "_opponent(obs).hand" not in source
    assert "_opponent(obs).deck" not in source


def test_materialized_package_is_verified_and_deck_bound(tmp_path: Path) -> None:
    package = tmp_path / "package"
    manifest = materialize_deck_adaptive_package(
        source_runtime_package=RUNTIME_PACKAGE,
        deck_package=DECK_PACKAGE,
        output_package=package,
        config=DeckAdaptiveConfig.default(),
        candidate_id="test-adaptive-package",
    )
    verified = verify_self_owned_cg_package_v1(package)
    assert verified == manifest
    assert (package / "cg").is_dir()
    assert verified["policy_sha256"] != verified["parent_policy_sha256"]
    assert verified["canonical_deck_sha256"]
