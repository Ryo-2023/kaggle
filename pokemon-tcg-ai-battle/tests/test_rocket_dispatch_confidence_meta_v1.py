from __future__ import annotations

import json
from pathlib import Path

import pytest

from mage_ptcg.opponent_ingest.rocket_dispatch_confidence_meta_v1 import (
    ROCKET_DISPATCH_CONFIDENCE_VARIANTS_V1,
    RocketDispatchConfidenceMetaError,
    _dispatch_commit_allowed,
    _transform_dispatch_confidence,
    seal_rocket_dispatch_confidence_meta_v1,
)


SOURCE = b'''\
import os

_TIER_A_TO_GROUP = {
    675: "A01", 676: "A01", 677: "A01", 678: "A01",
    646: "A09", 647: "A09", 648: "A09",
    741: "A07", 742: "A07", 743: "A07",
    721: "A11", 722: "A11", 723: "A11",
}
_A01_GENERIC_EVIDENCE_IDS = {673, 674}
_DISPATCH_ENABLED = os.environ.get("ROCKET_DISPATCH_DISABLE") != "1"
_DISPATCH_ACTUAL_FAMILY = os.environ.get("ROCKET_DISPATCH_ACTUAL_FAMILY")
_dispatch_state_by_player: dict[int, dict] = {}

def _dispatch_update(obs_dict, obs):
    if obs.current is None:
        return
    player = obs.current.yourIndex
    turn = obs.current.turn
    state = _dispatch_state_by_player.get(player)
    if state is None:
        state = {
            "player": player, "last_turn": turn, "groups": set(), "family": None,
            "commit_turn": None, "conflict": False, "miscommit": False,
        }
        _dispatch_state_by_player[player] = state
    state["last_turn"] = turn
    if _DISPATCH_ENABLED and not state["conflict"]:
        opponent_card_ids = {675}
        groups = {_TIER_A_TO_GROUP[cid] for cid in opponent_card_ids if cid in _TIER_A_TO_GROUP}
        state["groups"].update(groups)
        if len(state["groups"]) > 1:
            state["conflict"] = True
            state["family"] = None
        elif state["family"] is None and len(state["groups"]) == 1:
            state["family"] = next(iter(state["groups"]))
            state["commit_turn"] = turn

    theta = {"x": 1}
    return theta
'''


def test_confidence_transform_injects_only_bounded_dispatch_gate() -> None:
    transformed, recipe = _transform_dispatch_confidence(SOURCE, "TWO_TURN_CONFIRM")

    assert recipe == "ROCKET_DISPATCH_CONFIDENCE_V1:TWO_TURN_CONFIRM"
    assert b"_ROCKET_DISPATCH_CONFIDENCE_MODE = \"TWO_TURN_CONFIRM\"" in transformed
    assert b'"group_turns": {}' in transformed
    assert b"_dispatch_commit_allowed(" in transformed
    assert b"_TIER_A_TO_GROUP" in transformed
    assert transformed != SOURCE


def test_all_confidence_variants_are_deterministic_and_distinct() -> None:
    outputs = []
    for variant in ROCKET_DISPATCH_CONFIDENCE_VARIANTS_V1:
        first = _transform_dispatch_confidence(SOURCE, variant)
        second = _transform_dispatch_confidence(SOURCE, variant)
        assert first == second
        outputs.append(first[0])
    assert len(ROCKET_DISPATCH_CONFIDENCE_VARIANTS_V1) == 12
    assert len(set(outputs)) == 12


def test_unknown_variant_and_drift_fail_closed() -> None:
    with pytest.raises(RocketDispatchConfidenceMetaError, match="unsupported"):
        _transform_dispatch_confidence(SOURCE, "UNKNOWN")
    with pytest.raises(RocketDispatchConfidenceMetaError, match="commit condition"):
        _transform_dispatch_confidence(SOURCE.replace(
            b'elif state["family"] is None and len(state["groups"]) == 1:',
            b'elif state["family"] is None:',
        ), "TURN1_DELAY")


@pytest.mark.parametrize(
    ("mode", "turn", "turns", "ids", "expected"),
    [
        ("GENERAL_ONLY", 5, {0, 1, 2, 3, 4, 5}, {675}, False),
        ("TURN1_DELAY", 0, {0}, {675}, False),
        ("TURN1_DELAY", 1, {0, 1}, {675}, True),
        ("TWO_TURN_CONFIRM", 1, {0, 1}, {675}, True),
        ("TWO_TURN_CONFIRM", 1, {1}, {675}, False),
        ("MULTI_CARD_CONFIRM", 0, {0}, {675, 676}, True),
        ("MULTI_CARD_CONFIRM", 0, {0}, {675}, False),
        ("TWO_TURN_AND_MULTI_CARD", 1, {0, 1}, {675, 676}, True),
    ],
)
def test_dispatch_commit_gate_is_deterministic(
    mode: str, turn: int, turns: set[int], ids: set[int], expected: bool
) -> None:
    state = {"groups": {"A01"}, "group_turns": {"A01": turns}}
    assert _dispatch_commit_allowed(mode, state, turn, ids) is expected


def test_seal_emits_fresh_8_2_2_pool(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    base_root = repo_root / "runs/cg-fresh-internal-meta-intake-20260815-f/internal_ozawa-rocket-rule_de797c3646e9"
    p1_package = repo_root / "runs/final-sprint-autonomous/cg-policy-screen-v1-retry-safe4-20260814/candidates/cg-lethal-target-v1/package"
    variants = list(ROCKET_DISPATCH_CONFIDENCE_VARIANTS_V1)
    split = {
        variant: ("META_TRAIN" if index < 8 else "META_DEV" if index < 10 else "META_FINAL")
        for index, variant in enumerate(variants)
    }

    report = seal_rocket_dispatch_confidence_meta_v1(
        base_root=base_root,
        output_root=tmp_path / "sealed",
        source_epoch="test-confidence-epoch",
        seed_namespace="test-confidence-seed",
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


def test_checked_in_config_has_explicit_split() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    config = json.loads(
        (repo_root / "configs/meta_specialist/cg_rocket_dispatch_confidence_v1.json").read_text()
    )
    assert config["variants"] == list(ROCKET_DISPATCH_CONFIDENCE_VARIANTS_V1)
    assert len(set(config["variants"])) == 12
    assert {
        name: list(config["split_by_variant"].values()).count(name)
        for name in ("META_TRAIN", "META_DEV", "META_FINAL")
    } == {"META_TRAIN": 8, "META_DEV": 2, "META_FINAL": 2}
