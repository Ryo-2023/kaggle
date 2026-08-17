"""Iterative distillation and DAgger orchestration module.

Manages DAgger iteration state machine round manifests, dataset mixing plans, and stopping rule checks.
"""

from __future__ import annotations

import time
import json
import random
from pathlib import Path
from typing import Any, Iterable

from mage_ptcg.offline_training_v1_support.contracts import (
    FileLock,
    SupportContractError,
    atomic_write_json,
    atomic_write_records,
    load_records,
)

ROUND_MANIFEST_SCHEMA_VERSION = "support-round-manifest-v1"
VALID_PHASES = {"COLLECTING", "LABELING", "MIXING", "TRAINING_REQUESTED", "EVALUATING"}


class DistillationOrchestrator:
    """Orchestrates sequence round manifests, validation phases, and DAgger state progressions."""

    def __init__(self, run_dir: Path):
        self.run_dir = Path(run_dir)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.lock_path = self.run_dir / "iteration.lock"

    def _load_manifest(self, round_index: int) -> dict[str, Any]:
        path = self.run_dir / f"round_{round_index:03d}_manifest.json"
        if not path.exists():
            raise SupportContractError(f"Round {round_index} manifest not found.")
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)

    def _write_manifest(self, round_index: int, manifest: dict[str, Any]) -> None:
        path = self.run_dir / f"round_{round_index:03d}_manifest.json"
        manifest["updated_at"] = time.time()
        atomic_write_json(path, manifest)

    def create_round(self, iteration_id: str, round_index: int, config: dict[str, Any]) -> dict[str, Any]:
        """Initialize a new distillation round manifest."""
        with FileLock(self.lock_path):
            path = self.run_dir / f"round_{round_index:03d}_manifest.json"
            if path.exists():
                raise SupportContractError(f"Round {round_index} already exists.")

            manifest = {
                "schema_version": ROUND_MANIFEST_SCHEMA_VERSION,
                "iteration_id": iteration_id,
                "round_index": round_index,
                "parent_round_id": config.get("parent_round_id"),
                "base_dataset_id": config.get("base_dataset_id"),
                "new_collection_id": None,
                "hard_state_source_id": None,
                "teacher_snapshot": config.get("teacher_snapshot"),
                "student_snapshot": None,
                "query_budget": config.get("query_budget", 10000),
                "mixing_policy": config.get("mixing_policy", {}),
                "seed": config.get("seed", 42),
                "status": "PENDING",
                "phase_statuses": {p: "PENDING" for p in VALID_PHASES},
                "input_hashes": {},
                "output_hashes": {},
                "metrics": {},
                "stop_reason": None,
                "created_at": time.time(),
                "updated_at": time.time(),
            }
            atomic_write_json(path, manifest)
            return manifest

    def advance_phase(self, round_index: int, phase: str, status: str, outputs: dict[str, str] = None) -> dict[str, Any]:
        """Atomically transition DAgger phases with lock protection."""
        if phase not in VALID_PHASES:
            raise SupportContractError(f"Invalid distillation phase: {phase}")

        valid_statuses = {"PENDING", "RUNNING", "COMPLETE", "FAILED"}
        if status not in valid_statuses:
            raise SupportContractError(f"Invalid phase status: {status}")

        with FileLock(self.lock_path):
            manifest = self._load_manifest(round_index)

            # State machine rule: prevent double run
            if manifest["status"] in ("COMPLETE", "STOPPED") and status != "PENDING":
                raise SupportContractError(f"Round {round_index} is already completed or stopped.")

            manifest["phase_statuses"][phase] = status

            if status == "RUNNING":
                manifest["status"] = phase
            elif status == "FAILED":
                manifest["status"] = "FAILED_RETRYABLE"
            elif status == "COMPLETE":
                if outputs:
                    manifest["output_hashes"].update(outputs)
                # Auto advance round overall state
                if all(manifest["phase_statuses"][p] == "COMPLETE" for p in VALID_PHASES):
                    manifest["status"] = "COMPLETE"

            self._write_manifest(round_index, manifest)
            return manifest

    def check_stopping_rules(self, round_index: int, history_metrics: list[dict[str, Any]]) -> tuple[bool, str]:
        """Check for stopping conditions using machine-readable metrics (plateau, queries)."""
        manifest = self._load_manifest(round_index)

        # 1. Query budget limit
        query_count = manifest["metrics"].get("teacher_queries", 0)
        budget = manifest.get("query_budget", 10000)
        if query_count >= budget:
            return True, "teacher_query_budget_exhausted"

        # 2. Maximum rounds check
        max_rounds = manifest["mixing_policy"].get("max_rounds", 10)
        if round_index >= max_rounds:
            return True, "maximum_rounds_exceeded"

        # 3. Disagreement or fallback plateau (no progress over last 3 rounds)
        if len(history_metrics) >= 3:
            recent_fallbacks = [h.get("fallback_rate", 0.0) for h in history_metrics[-3:]]
            if len(set(recent_fallbacks)) == 1:
                return True, "fallback_plateau"

        return False, ""

    def generate_mixing_plan(self, config: dict[str, Any]) -> dict[str, Any]:
        """Generate a deterministic plan configuration to mix datasets."""
        required = {"base_fraction", "new_fraction", "hard_state_fraction"}
        missing = required - set(config)
        if missing:
            raise SupportContractError(f"Missing mixing policy fractions: {missing}")

        for k in required:
            val = config[k]
            if val < 0.0 or val > 1.0:
                raise SupportContractError(f"Mixing fraction {k} must be in [0.0, 1.0]")

        return {
            "schema_version": "support-dataset-mixing-plan-v1",
            "fractions": {
                "base": config["base_fraction"],
                "new": config["new_fraction"],
                "hard_state": config["hard_state_fraction"],
            },
            "constraints": {
                "selection_balance": config.get("selection_balance", False),
                "episode_level_grouping": config.get("episode_level_grouping", True),
            },
            "seed": config.get("seed", 42),
        }

    def mix_dataset_records(
        self,
        plan: dict[str, Any],
        base_records: list[dict[str, Any]],
        new_records: list[dict[str, Any]],
        hard_records: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Combine lists of records based on the plan instructions deterministically."""
        rng = random.Random(plan.get("seed", 42))

        base_frac = plan["fractions"]["base"]
        new_frac = plan["fractions"]["new"]
        hard_frac = plan["fractions"]["hard_state"]

        # Calculate sample targets
        total_target = len(base_records) + len(new_records)
        base_target = int(total_target * base_frac)
        new_target = int(total_target * new_frac)
        hard_target = int(total_target * hard_frac)

        # Draw deterministic samples
        # Use sorted keys for stable shuffled output
        base_sorted = sorted(base_records, key=lambda x: x.get("decision_id", ""))
        new_sorted = sorted(new_records, key=lambda x: x.get("decision_id", ""))
        hard_sorted = sorted(hard_records, key=lambda x: x.get("decision_id", ""))

        sampled_base = rng.sample(base_sorted, min(len(base_sorted), base_target)) if base_sorted else []
        sampled_new = rng.sample(new_sorted, min(len(new_sorted), new_target)) if new_sorted else []
        sampled_hard = rng.sample(hard_sorted, min(len(hard_sorted), hard_target)) if hard_sorted else []

        mixed = sampled_base + sampled_new + sampled_hard
        # Shuffle result deterministically
        rng.shuffle(mixed)
        return mixed
