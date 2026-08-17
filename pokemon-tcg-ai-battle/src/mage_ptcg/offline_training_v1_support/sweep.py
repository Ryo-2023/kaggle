"""Experiment sweep orchestration module.

Implements grid search, deterministic random search, and successive halving planning.
"""

from __future__ import annotations

import math
import random
import time
from typing import Any

from mage_ptcg.offline_training_v1_support.contracts import (
    SupportContractError,
    digest,
)

SWEEP_SCHEMA_VERSION = "support-sweep-manifest-v1"


def get_canonical_config_hash(config: dict[str, Any]) -> str:
    """Generate a unique canonical hash for a trial config configuration."""
    return digest(config, domain="trial-config")


class SweepOrchestrator:
    """Orchestrates configuration spaces, trial progress, and successive halving prune logic."""

    def __init__(self, sweep_id: str):
        self.sweep_id = sweep_id

    def generate_initial_trials(
        self,
        base_config: dict[str, Any],
        parameter_space: dict[str, list[Any]],
        search_method: str,
        maximum_trials: int = 10,
        seed: int = 42,
    ) -> list[dict[str, Any]]:
        """Generate first stage trial config combinations based on sweep strategy."""
        trials = []

        if search_method == "grid":
            # Grid combinations (Cartesian product)
            keys = sorted(parameter_space.keys())
            import itertools
            combinations = list(itertools.product(*(parameter_space[k] for k in keys)))

            # Limit if exceeded
            if len(combinations) > maximum_trials:
                combinations = combinations[:maximum_trials]

            for idx, combo in enumerate(combinations):
                cfg = base_config.copy()
                for k, val in zip(keys, combo):
                    cfg[k] = val
                trials.append(cfg)

        elif search_method in ("random", "deterministic-random"):
            rng = random.Random(seed)
            keys = sorted(parameter_space.keys())

            seen_hashes = set()
            attempts = 0
            while len(trials) < maximum_trials and attempts < maximum_trials * 10:
                cfg = base_config.copy()
                for k in keys:
                    cfg[k] = rng.choice(parameter_space[k])

                cfg_hash = get_canonical_config_hash(cfg)
                if cfg_hash not in seen_hashes:
                    seen_hashes.add(cfg_hash)
                    trials.append(cfg)
                attempts += 1

        else:
            raise SupportContractError(f"Unsupported search method: {search_method}")

        # Create structured planned trials
        trial_records = []
        for idx, cfg in enumerate(trials):
            cfg_hash = get_canonical_config_hash(cfg)
            trial_records.append({
                "trial_id": f"trial_{self.sweep_id[:8]}_{idx:04d}",
                "config": cfg,
                "config_hash": cfg_hash,
                "budget": 1.0,  # Base budget stage
                "stage": 0,
                "status": "PLANNED",
                "attempts": 0,
                "result": None,
                "failure_reason": None,
                "parent_trial_id": None,
            })

        return trial_records

    def advance_successive_halving(
        self,
        trials: list[dict[str, Any]],
        reduction_factor: int = 3,
        min_survivors: int = 1,
        objective: str = "val_loss",
        direction: str = "minimize",
    ) -> list[dict[str, Any]]:
        """Perform Successive Halving prune steps and return top-performing advanced trials."""
        # Find maximum current stage of complete trials
        completed_by_stage: dict[int, list[dict[str, Any]]] = {}
        for t in trials:
            if t["status"] == "COMPLETE":
                stage = t["stage"]
                completed_by_stage.setdefault(stage, []).append(t)

        if not completed_by_stage:
            # Nothing completed to advance
            return []

        curr_stage = max(completed_by_stage.keys())
        stage_trials = completed_by_stage[curr_stage]

        # Verify objective exists and has finite values
        valid_trials = []
        for t in stage_trials:
            res = t.get("result", {})
            if not res or objective not in res:
                # Missing metrics -> skip
                continue

            val = res[objective]
            if not isinstance(val, (int, float)) or not math.isfinite(val):
                # Reject NaN/Inf
                t["status"] = "FAILED_FINAL"
                t["failure_reason"] = f"NaN/Inf metric rejected for objective '{objective}'"
                continue

            valid_trials.append(t)

        if not valid_trials:
            return []

        # Sort trials based on objective performance
        # Handle ties deterministically using config_hash sorting
        reverse_sort = (direction == "maximize")
        sorted_trials = sorted(
            valid_trials,
            key=lambda x: (x["result"][objective], x["config_hash"]),
            reverse=reverse_sort,
        )

        # Calculate survivors count
        survivors_count = max(min_survivors, len(sorted_trials) // reduction_factor)
        survivors = sorted_trials[:survivors_count]

        # Generate new trials for the next stage
        next_stage = curr_stage + 1
        new_trials = []
        for idx, survivor in enumerate(survivors):
            # Scale budget by reduction factor
            new_budget = survivor["budget"] * reduction_factor
            new_trials.append({
                "trial_id": f"{survivor['trial_id']}_s{next_stage}",
                "config": survivor["config"],
                "config_hash": survivor["config_hash"],
                "budget": new_budget,
                "stage": next_stage,
                "status": "READY",
                "attempts": 0,
                "result": None,
                "failure_reason": None,
                "parent_trial_id": survivor["trial_id"],
            })

        return new_trials
