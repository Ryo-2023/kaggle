"""Compatibility module for the plan's standalone promotion-gate name.

The implementation lives with the sealed experiment manifest so the gate and
its lineage contract cannot drift apart.  This module keeps the planned import
surface explicit for downstream runners.
"""

from mage_ptcg.meta_specialist.experiment_manifest_v1 import promotion_gate_v1

__all__ = ["promotion_gate_v1"]
