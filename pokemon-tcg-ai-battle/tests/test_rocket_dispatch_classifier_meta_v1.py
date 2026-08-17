from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from mage_ptcg.opponent_ingest.rocket_dispatch_classifier_meta_v1 import (
    ROCKET_DISPATCH_CLASSIFIER_VARIANTS_V1,
    RocketDispatchClassifierMetaError,
    _transform_dispatch_classifier,
    seal_rocket_dispatch_classifier_meta_v1,
)


SOURCE = b'''\
import os

_TIER_A_TO_GROUP = {
    675: "A01", 676: "A01", 677: "A01", 678: "A01",
    646: "A09", 647: "A09", 648: "A09",
    741: "A07", 742: "A07", 743: "A07",
    721: "A11", 722: "A11", 723: "A11",
}

def choose(obs):
    return obs
'''

EXPECTED_KEYS = {675, 676, 677, 678, 646, 647, 648, 741, 742, 743, 721, 722, 723}


def _classifier_map(source: bytes) -> dict[int, str]:
    tree = ast.parse(source.decode("utf-8"))
    assignment = next(
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == "_TIER_A_TO_GROUP"
    )
    assert isinstance(assignment.value, ast.Dict)
    return {
        ast.literal_eval(key): ast.literal_eval(value)
        for key, value in zip(assignment.value.keys, assignment.value.values)
    }


def test_classifier_transform_changes_only_declared_map_values() -> None:
    transformed, recipe = _transform_dispatch_classifier(SOURCE, "A01_ENGINE_TO_A09")

    assert recipe == "ROCKET_DISPATCH_CLASSIFIER_V1:A01_ENGINE_TO_A09"
    mapped = _classifier_map(transformed)
    assert set(mapped) == EXPECTED_KEYS
    assert mapped[675] == mapped[676] == "A09"
    assert mapped[677] == mapped[678] == "A01"
    assert mapped[646] == "A09"
    assert b"import os" in transformed
    assert b"def choose(obs):" in transformed
    assert transformed != SOURCE


def test_all_declared_variants_are_deterministic_and_distinct() -> None:
    outputs = []
    for variant in ROCKET_DISPATCH_CLASSIFIER_VARIANTS_V1:
        first = _transform_dispatch_classifier(SOURCE, variant)
        second = _transform_dispatch_classifier(SOURCE, variant)
        assert first == second
        assert first[0] != SOURCE
        outputs.append(first[0])
    assert len(ROCKET_DISPATCH_CLASSIFIER_VARIANTS_V1) == 12
    assert len(set(outputs)) == 12


def test_unknown_variant_fails_closed() -> None:
    with pytest.raises(RocketDispatchClassifierMetaError, match="unsupported"):
        _transform_dispatch_classifier(SOURCE, "UNKNOWN")


def test_classifier_structure_fails_closed_on_missing_or_wrong_key() -> None:
    missing = SOURCE.replace(b"723: \"A11\",", b"")
    with pytest.raises(RocketDispatchClassifierMetaError, match="keys"):
        _transform_dispatch_classifier(missing, "A01_ENGINE_TO_A09")

    wrong = SOURCE.replace(b"    675: \"A01\",", b"    675: \"A99\",")
    with pytest.raises(RocketDispatchClassifierMetaError, match="family"):
        _transform_dispatch_classifier(wrong, "A01_ENGINE_TO_A09")


def test_classifier_rejects_mismatched_and_malformed_dictionary() -> None:
    mismatched = SOURCE.replace(b'    675: "A01",', b'    675: "A09",')
    with pytest.raises(RocketDispatchClassifierMetaError, match="old family"):
        _transform_dispatch_classifier(mismatched, "A01_ENGINE_TO_A09")

    malformed = SOURCE.replace(
        b"    675: \"A01\", 676: \"A01\", 677: \"A01\", 678: \"A01\",",
        b"    675: make_group(), 676: \"A01\", 677: \"A01\", 678: \"A01\",",
    )
    with pytest.raises(RocketDispatchClassifierMetaError, match="literal"):
        _transform_dispatch_classifier(malformed, "A01_ENGINE_TO_A09")


def test_seal_emits_hash_bound_split_reserved_pool(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    base_root = repo_root / "runs/cg-fresh-internal-meta-intake-20260815-f/internal_ozawa-rocket-rule_de797c3646e9"
    p1_package = repo_root / "runs/final-sprint-autonomous/cg-policy-screen-v1-retry-safe4-20260814/candidates/cg-lethal-target-v1/package"
    variants = list(ROCKET_DISPATCH_CLASSIFIER_VARIANTS_V1)
    split = {
        variant: ("META_TRAIN" if index < 8 else "META_DEV" if index < 10 else "META_FINAL")
        for index, variant in enumerate(variants)
    }

    report = seal_rocket_dispatch_classifier_meta_v1(
        base_root=base_root,
        output_root=tmp_path / "sealed",
        source_epoch="test-dispatch-epoch",
        seed_namespace="test-dispatch-seed",
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
        (repo_root / "configs/meta_specialist/cg_rocket_dispatch_classifier_v1.json").read_text()
    )
    variants = config["variants"]
    assert variants == list(ROCKET_DISPATCH_CLASSIFIER_VARIANTS_V1)
    assert len(set(variants)) == 12
    assert {
        name: list(config["split_by_variant"].values()).count(name)
        for name in ("META_TRAIN", "META_DEV", "META_FINAL")
    } == {"META_TRAIN": 8, "META_DEV": 2, "META_FINAL": 2}
