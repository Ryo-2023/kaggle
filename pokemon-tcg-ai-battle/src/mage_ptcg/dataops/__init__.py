"""Privacy-safe C4 actual-cabt training-data collection (data-ops only).

This package is intentionally not imported by ``main.py`` or any Student
runtime module.  It reuses the existing actual-cabt environment, the Stable
``ActionKey`` adapter, the actor-visible ``DecisionState`` projection, and Rule
Agent v0 to turn actual decisions into the existing ``rule-bc-v1`` dataset plus
a Git-ignored private candidate binding.
"""

from mage_ptcg.dataops.collector import (
    BINDING_SCHEMA_VERSION,
    COLLECTOR_SCHEMA_VERSION,
    ActualEpisodeLineageInput,
    DataOpsError,
    LineageValidationError,
    build_decision_artifacts,
    collect_actual_dataset,
    compute_manifest,
    scan_public_artifact,
    split_by_episode_group,
    validate_run,
)

__all__ = [
    "BINDING_SCHEMA_VERSION",
    "COLLECTOR_SCHEMA_VERSION",
    "ActualEpisodeLineageInput",
    "DataOpsError",
    "LineageValidationError",
    "build_decision_artifacts",
    "collect_actual_dataset",
    "compute_manifest",
    "scan_public_artifact",
    "split_by_episode_group",
    "validate_run",
]
