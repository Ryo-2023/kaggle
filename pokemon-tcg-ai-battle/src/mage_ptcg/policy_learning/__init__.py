"""Population-trained legal-action policy learning.

All runtime decisions score only CABT-provided legal actions.  The package is
research/candidate-only: importing it never changes Rule Agent v0 or the
submission entry point.
"""

from .algorithms import awr_weights, ppo_clipped_loss, vtrace_targets
from .data import PolicyLearningExample, load_examples
from .league import PSROState, solve_meta_strategy
from .model import ActorCriticConfig, build_actor_critic
from .online import OnlineStep, ppo_update, vtrace_update

__all__ = [
    "ActorCriticConfig",
    "PSROState",
    "OnlineStep",
    "PolicyLearningExample",
    "awr_weights",
    "build_actor_critic",
    "load_examples",
    "ppo_clipped_loss",
    "ppo_update",
    "solve_meta_strategy",
    "vtrace_targets",
    "vtrace_update",
]
