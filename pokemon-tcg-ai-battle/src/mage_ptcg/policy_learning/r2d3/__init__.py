"""Feature-flagged recurrent off-policy learning components.

The package is intentionally separate from the PPO/V-trace implementation:
existing checkpoints and the Rule-v0 fallback remain operational.
"""
from .replay import PrioritizedSequenceReplay, ReplaySample
from .sequence import R2D3Transition, SequenceBatch, n_step_returns, split_episode

__all__ = ["PrioritizedSequenceReplay", "R2D3Transition", "ReplaySample", "SequenceBatch", "n_step_returns", "split_episode"]
