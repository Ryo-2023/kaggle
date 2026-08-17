from __future__ import annotations

import inspect

from scripts.run_non_main_target_overlay_screen_v1 import run_non_main_target_overlay_screen
from scripts.run_outcome_only_weighted_action_screen_v1 import run_weighted_action_screen
from scripts.run_self_owned_public_outcome_rollout_v1 import _parser
from scripts.run_self_owned_rule_v0_public_outcome_screen_v1 import run_common24_screen_v1


def _default_workers(function: object) -> int:
    parameter = inspect.signature(function).parameters["workers"]
    return int(parameter.default)


def test_screen_and_rollout_function_defaults_use_parallel_worker_budget() -> None:
    assert _default_workers(run_weighted_action_screen) == 12
    assert _default_workers(run_non_main_target_overlay_screen) == 12
    assert _default_workers(run_common24_screen_v1) == 12


def test_public_rollout_cli_defaults_use_parallel_worker_budget() -> None:
    args = _parser().parse_args(["--output", "/tmp/parallel-default-test"])
    assert args.workers == 12
    assert args.worker_recycle_games == 16
