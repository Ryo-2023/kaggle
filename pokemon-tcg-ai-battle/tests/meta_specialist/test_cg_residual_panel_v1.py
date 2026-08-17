from __future__ import annotations

from pathlib import Path

from mage_ptcg.meta_specialist.cg_alternating_runtime_v1 import CgPackageSpecV1
from scripts.run_cg_residual_panel_v1 import (
    _build_residual_pair_games,
    load_residual_refs,
    summarize_residual_rows,
)


ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "configs/meta_specialist/cg_unused_meta_residual_v1.json"
P1_PACKAGE = ROOT / "runs/final-sprint-autonomous/cg-p1-robust-g01-submission-package-v1/package"
CONTROL_PACKAGE = ROOT / "runs/final-sprint-autonomous/cg-policy-screen-v1-retry-safe4-20260814/candidates/cg-lethal-target-v1/package"


def test_residual_config_is_three_public_refs_with_sixteen_repetitions() -> None:
    refs, repetitions = load_residual_refs(CONFIG)

    assert refs == (
        "rauffauzanrambe_advanced",
        "tomatomato_archaludon",
        "yaminh_agent",
    )
    assert repetitions == 16


def test_residual_pair_games_preserve_strata_and_arm_identity() -> None:
    spec = CgPackageSpecV1.from_package(P1_PACKAGE)
    control_spec = CgPackageSpecV1.from_package(CONTROL_PACKAGE)
    games = _build_residual_pair_games(
        candidate=spec,
        control=control_spec,
        reference_ids=("rauffauzanrambe_advanced", "tomatomato_archaludon", "yaminh_agent"),
        pool_root=ROOT / "opponents",
        base_seed=180260815,
        repetitions=16,
    )

    assert len(games) == 192
    candidate = [game for game in games if game.metadata["residual_arm"] == "candidate"]
    control = [game for game in games if game.metadata["residual_arm"] == "control"]
    assert len(candidate) == len(control) == 96
    assert {(g.metadata["pair_key"], g.seed) for g in candidate} == {
        (g.metadata["pair_key"], g.seed) for g in control
    }


def test_residual_summary_marks_fault_free_positive_signal() -> None:
    rows = []
    win_indices = {"candidate": {0, 1, 2, 8, 9, 10}, "control": {0, 2, 8, 10}}
    for arm in ("candidate", "control"):
        for index in range(16):
            rows.append(
                {
                    "outcome": "win" if index in win_indices[arm] else "loss",
                    "seat": 0 if index < 8 else 1,
                    "metadata": {
                        "residual_arm": arm,
                        "pair_key": f"opponent-{index % 3}|seat{0 if index < 8 else 1}|rep{index}",
                    },
                    "seed": index,
                }
            )

    summary = summarize_residual_rows(rows, stage_games_per_arm=16, protocol_sha256="a" * 64)

    assert summary["candidate_delta_points"] == 12.5
    assert summary["faults"] == 0
    assert summary["decision"] == "POSITIVE_SIGNAL"
