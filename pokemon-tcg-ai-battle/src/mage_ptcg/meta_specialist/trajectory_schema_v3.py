"""Replayable v3 trajectory schema with complete legal-action distributions."""

from __future__ import annotations

from dataclasses import dataclass
import math

from mage_ptcg.meta_specialist.representation_v3 import RelationalStateV3


@dataclass(frozen=True, slots=True)
class TrajectoryDecisionV3:
    state: RelationalStateV3
    base_logits: tuple[float, ...]
    base_log_probs: tuple[float, ...]
    chosen_index: int
    behavior_log_prob: float
    sampling_mode: str
    legal_action_count: int
    normalized_entropy: float
    policy_version: str
    hidden_state_hash: str
    model_latency_ms: float
    environment_latency_ms: float

    def __post_init__(self) -> None:
        if type(self.state) is not RelationalStateV3 or len(self.base_logits) != len(self.state.candidates) or len(self.base_log_probs) != len(self.base_logits):
            raise ValueError("trajectory state and complete logits are misaligned")
        if not self.base_logits or type(self.chosen_index) is not int or not 0 <= self.chosen_index < len(self.base_logits):
            raise ValueError("trajectory chosen action is invalid")
        if any(not math.isfinite(value) for value in (*self.base_logits, *self.base_log_probs, self.behavior_log_prob)):
            raise ValueError("trajectory logits/log-probabilities must be finite")
        if not math.isclose(sum(math.exp(value) for value in self.base_log_probs), 1.0, abs_tol=1e-4):
            raise ValueError("base_log_probs must define a normalized distribution")
        if not math.isclose(self.behavior_log_prob, self.base_log_probs[self.chosen_index], abs_tol=1e-4):
            raise ValueError("behavior_log_prob must come from base policy probability")
        if self.sampling_mode not in {"greedy", "categorical", "gumbel-max"} or self.legal_action_count != len(self.base_logits):
            raise ValueError("trajectory sampling mode/legal count is invalid")
        if not 0 <= self.normalized_entropy <= 1 or self.model_latency_ms < 0 or self.environment_latency_ms < 0:
            raise ValueError("trajectory entropy/latency is invalid")
        if not self.policy_version or len(self.hidden_state_hash) != 64:
            raise ValueError("trajectory policy/hidden hash is invalid")


@dataclass(frozen=True, slots=True)
class TrajectoryEpisodeV3:
    episode_id: str
    decisions: tuple[TrajectoryDecisionV3, ...]
    outcome: str
    opponent_provenance: str

    def __post_init__(self) -> None:
        if len(self.episode_id) != 64 or not self.decisions or any(type(item) is not TrajectoryDecisionV3 for item in self.decisions):
            raise ValueError("trajectory episode identity/decisions are invalid")
        if self.outcome not in {"win", "draw", "loss"} or not self.opponent_provenance:
            raise ValueError("trajectory episode outcome/provenance is invalid")


__all__ = ["TrajectoryDecisionV3", "TrajectoryEpisodeV3"]
