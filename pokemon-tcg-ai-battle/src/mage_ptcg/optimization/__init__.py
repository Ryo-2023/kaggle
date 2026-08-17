"""Fail-closed, Rule-relative optimization primitives.

Optimization Core v1 deliberately treats every non-Rule policy as a proposal
generator.  It does not alter the submission agent or the Rule v0 code path.
"""

from .core import (
    ActionKeyVNext,
    AdvantageRecord,
    DisagreementRootBuffer,
    OpponentPublicPosterior,
    ResidualRanker,
    RuleOverlay,
    StateIdentityVNext,
    build_advantage_records,
    robust_rank,
)

__all__ = [
    "ActionKeyVNext", "AdvantageRecord", "DisagreementRootBuffer",
    "OpponentPublicPosterior", "ResidualRanker", "RuleOverlay",
    "StateIdentityVNext", "build_advantage_records", "robust_rank",
]
