"""Competition Intelligence sidecar (O1): provenance, permissions, immutable
snapshots, and analytics for external match/deck/knowledge data.

This package is intentionally **never imported by the submission runtime**
(``main.py`` and everything it reaches, directly or via a lazy/guarded
import). It is a sidecar that produces Immutable Intelligence Snapshots which
``offline_training`` may *optionally* read (a lazy, one-directional
dependency); nothing here feeds a running Champion/Challenger agent
directly, and nothing here is exempt from that boundary. See
``tests/test_competition_intelligence_runtime_isolation.py`` for the
enforcing test and
``docs/plan/implementation/04_kaggle_competition_intelligence_and_joint_optimization_implementation_plan.md``
for the full design.
"""

from __future__ import annotations
