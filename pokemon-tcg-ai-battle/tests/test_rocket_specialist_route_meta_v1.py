from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from mage_ptcg.opponent_ingest.rocket_specialist_route_meta_v1 import (
    ROCKET_SPECIALIST_ROUTE_VARIANTS_V1,
    RocketSpecialistRouteMetaError,
    _transform_specialist_route,
    seal_rocket_specialist_route_meta_v1,
)


SOURCE = b'''\
import os

_THETA_GENERAL = {"x": 1}
_THETA_LUCMIX = {"x": 2}
_THETA_A09_MERGED = {"x": 3}
_THETA_A07_MERGED = {"x": 4}
_THETA_ABOMASNOW_R2 = {"x": 5}

_SPECIALIST_THETA = {
    "A01": _THETA_LUCMIX,
    "A09": _THETA_A09_MERGED,
    "A07": _THETA_A07_MERGED,
    "A11": _THETA_ABOMASNOW_R2,
}

_TIER_A_TO_GROUP = {675: "A01", 646: "A09", 741: "A07", 721: "A11"}

def _apply_theta(theta):
    return theta
'''


def _route_map(source: bytes) -> dict[str, str]:
    tree = ast.parse(source.decode("utf-8"))
    assignment = next(
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == "_SPECIALIST_THETA"
    )
    assert isinstance(assignment.value, ast.Dict)
    return {
        ast.literal_eval(key): value.id
        for key, value in zip(assignment.value.keys, assignment.value.values)
    }


def test_route_transform_changes_only_specialist_value_tokens() -> None:
    transformed, recipe = _transform_specialist_route(SOURCE, "A01_GENERAL")

    assert recipe == "ROCKET_SPECIALIST_ROUTE_V1:A01_GENERAL"
    assert _route_map(transformed) == {
        "A01": "_THETA_GENERAL",
        "A09": "_THETA_A09_MERGED",
        "A07": "_THETA_A07_MERGED",
        "A11": "_THETA_ABOMASNOW_R2",
    }
    assert b'_TIER_A_TO_GROUP = {675: "A01", 646: "A09", 741: "A07", 721: "A11"}' in transformed
    assert b"def _apply_theta(theta):" in transformed
    assert b"import os" in transformed
    assert transformed != SOURCE


def test_all_declared_variants_are_deterministic_and_non_noop() -> None:
    outputs = []
    for variant in ROCKET_SPECIALIST_ROUTE_VARIANTS_V1:
        first = _transform_specialist_route(SOURCE, variant)
        second = _transform_specialist_route(SOURCE, variant)
        assert first == second
        assert first[0] != SOURCE
        outputs.append(first[0])
    assert len(set(outputs)) == len(outputs)


def test_unknown_variant_fails_closed() -> None:
    with pytest.raises(RocketSpecialistRouteMetaError, match="unsupported"):
        _transform_specialist_route(SOURCE, "UNKNOWN")


def test_route_structure_fail_closed_on_missing_key() -> None:
    broken = SOURCE.replace(b'    "A11": _THETA_ABOMASNOW_R2,\n', b'')
    with pytest.raises(RocketSpecialistRouteMetaError, match="keys"):
        _transform_specialist_route(broken, "A01_GENERAL")


def test_route_structure_fail_closed_on_non_name_value() -> None:
    broken = SOURCE.replace(b'    "A11": _THETA_ABOMASNOW_R2,', b'    "A11": make_theta(),')
    with pytest.raises(RocketSpecialistRouteMetaError, match="Name"):
        _transform_specialist_route(broken, "A01_GENERAL")


def test_seal_emits_hash_bound_split_reserved_pool(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    base_root = repo_root / "runs/cg-fresh-internal-meta-intake-20260815-f/internal_ozawa-rocket-rule_de797c3646e9"
    p1_package = repo_root / "runs/final-sprint-autonomous/cg-policy-screen-v1-retry-safe4-20260814/candidates/cg-lethal-target-v1/package"
    variants = list(ROCKET_SPECIALIST_ROUTE_VARIANTS_V1)
    split = {
        variant: ("META_TRAIN" if index < 8 else "META_DEV" if index < 10 else "META_FINAL")
        for index, variant in enumerate(variants)
    }

    report = seal_rocket_specialist_route_meta_v1(
        base_root=base_root,
        output_root=tmp_path / "sealed",
        source_epoch="test-route-epoch",
        seed_namespace="test-route-seed",
        p1_package=p1_package,
        variants=variants,
        split_by_variant=split,
        current_pool_manifest=repo_root / "opponents/pool_manifest.json",
        scan_roots=(tmp_path,),
    )

    assert report["status"] == "SEALED"
    assert report["accepted_count"] == 12
    assert report["split_counts"] == {"META_TRAIN": 8, "META_DEV": 2, "META_FINAL": 2}
    pool = json.loads((tmp_path / "sealed/pool_manifest.json").read_text())
    assert len(pool) == 12
    assert len({row["policy_hash"] for row in pool}) == 12
    fresh = json.loads((tmp_path / "sealed/fresh_meta.json").read_text())
    assert all(item["usage_boundary"] == "local_eval_only" for item in fresh["references"])
    assert all(value is False for value in fresh["authority"].values())


def test_checked_in_config_has_explicit_8_2_2_split() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    config = json.loads(
        (repo_root / "configs/meta_specialist/cg_rocket_specialist_route_v1.json").read_text()
    )
    variants = config["variants"]
    assert variants == list(ROCKET_SPECIALIST_ROUTE_VARIANTS_V1)
    assert len(set(variants)) == 12
    assert {name: list(config["split_by_variant"].values()).count(name) for name in ("META_TRAIN", "META_DEV", "META_FINAL")} == {
        "META_TRAIN": 8,
        "META_DEV": 2,
        "META_FINAL": 2,
    }
