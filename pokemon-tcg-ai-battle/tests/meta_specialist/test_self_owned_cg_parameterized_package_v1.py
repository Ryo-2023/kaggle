"""Tests for parameterized policy packages bound to self-owned decks."""

from __future__ import annotations

from pathlib import Path

import pytest

from mage_ptcg.meta_specialist.cg_p1_parameterization_v1 import P1ParameterConfig
from mage_ptcg.meta_specialist.self_owned_cg_package_v1 import verify_self_owned_cg_package_v1
from mage_ptcg.meta_specialist.self_owned_cg_parameterized_package_v1 import (
    materialize_self_owned_cg_parameterized_package_v1,
)


ROOT = Path(__file__).resolve().parents[2]
P1_PACKAGE = ROOT / "runs/final-sprint-autonomous/cg-policy-screen-v1-retry-safe4-20260814/candidates/cg-lethal-target-v1/package"
DECK_PACKAGE = ROOT / "runs/cg-self-owned-deck-generation-v2-20260816-00/package"


def test_materializer_rebinds_policy_and_self_owned_manifest(tmp_path: Path) -> None:
    output = tmp_path / "candidate"
    config = P1ParameterConfig.default()
    config = P1ParameterConfig.from_mapping({"lethal_bonus": 13000})

    manifest = materialize_self_owned_cg_parameterized_package_v1(
        source_package=P1_PACKAGE,
        self_owned_deck_package=DECK_PACKAGE,
        output_package=output,
        config=config,
        candidate_id="self-owned-parameterized-test",
    )

    verified = verify_self_owned_cg_package_v1(output)
    assert verified["candidate_id"] == "self-owned-parameterized-test"
    assert verified["parent_policy_sha256"] == "1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9"
    assert verified["policy_sha256"] == manifest["policy_sha256"]
    assert verified["canonical_deck_sha256"] == "210155470edbe072f5c4237d84f799afeec69ac1819e715ce4dfff6ec1901963"
    assert '"lethal_bonus":13000' in (output / "main.py").read_text(encoding="utf-8")
    assert "self-owned-cg" == verified["archetype_id"]


def test_materializer_is_no_clobber(tmp_path: Path) -> None:
    output = tmp_path / "candidate"
    kwargs = {
        "source_package": P1_PACKAGE,
        "self_owned_deck_package": DECK_PACKAGE,
        "output_package": output,
        "config": P1ParameterConfig.default(),
        "candidate_id": "self-owned-parameterized-test",
    }
    materialize_self_owned_cg_parameterized_package_v1(**kwargs)
    with pytest.raises(FileExistsError):
        materialize_self_owned_cg_parameterized_package_v1(**kwargs)


def test_materializer_rejects_non_self_owned_deck_package(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="self-owned"):
        materialize_self_owned_cg_parameterized_package_v1(
            source_package=P1_PACKAGE,
            self_owned_deck_package=P1_PACKAGE,
            output_package=tmp_path / "candidate",
            config=P1ParameterConfig.default(),
            candidate_id="invalid",
        )
