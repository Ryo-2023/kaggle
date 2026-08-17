from __future__ import annotations

import inspect
from pathlib import Path

from mage_ptcg.meta_specialist.cg_p1_parameterization_v1 import P1ParameterConfig
from mage_ptcg.meta_specialist.cg_weekend_split_v1 import load_weekend_split
from scripts.run_cg_p1_p2_validation_v1 import build_validation_games


ROOT = Path(__file__).resolve().parents[2]
P1_PACKAGE = (
    ROOT
    / "runs/final-sprint-autonomous/cg-policy-screen-v1-retry-safe4-20260814"
    / "candidates/cg-lethal-target-v1/package"
)
SPLIT = load_weekend_split(ROOT / "configs/meta_specialist/cg_weekend_splits_v1.json")


def test_validation_plan_has_fault_inclusive_train384_and_dev96_strata() -> None:
    config = P1ParameterConfig.default()
    train = build_validation_games(
        candidate_package=P1_PACKAGE,
        candidate_id="p2-test",
        config_sha256=config.config_sha256(),
        split=SPLIT,
        stage="META_TRAIN_384",
        base_seed=123456,
    )
    dev = build_validation_games(
        candidate_package=P1_PACKAGE,
        candidate_id="p2-test",
        config_sha256=config.config_sha256(),
        split=SPLIT,
        stage="META_DEV_96",
        base_seed=123456,
    )
    assert len(train) == 768  # candidate + immutable P1 control
    assert len(dev) == 192
    assert {game.metadata["split"] for game in train} == {"META_TRAIN"}
    assert {game.metadata["split"] for game in dev} == {"META_DEV"}
    assert all(game.metadata["config_sha256"] == config.config_sha256() for game in train + dev)


def test_validation_plan_accepts_a_staged_pool_root_override() -> None:
    config = P1ParameterConfig.default()
    assert "pool_root" in inspect.signature(build_validation_games).parameters
    games = build_validation_games(
        candidate_package=P1_PACKAGE,
        candidate_id="p2-staged-pool-test",
        config_sha256=config.config_sha256(),
        split=SPLIT,
        stage="META_DEV_96",
        base_seed=123456,
        pool_root=ROOT / "opponents",
    )
    assert all(game.metadata["pool_root"].endswith("/opponents") for game in games)
