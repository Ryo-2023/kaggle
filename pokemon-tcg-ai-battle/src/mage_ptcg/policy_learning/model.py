"""Recurrent, variable-legal-action actor-critic model."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from mage_ptcg.student.features import ACTION_FEATURE_DIM, STATE_FEATURE_DIM


HISTORY_FEATURE_DIM = 32


@dataclass(frozen=True, slots=True)
class ActorCriticConfig:
    hidden_size: int = 128
    recurrent_size: int = 128
    blocks: int = 2
    dropout: float = 0.05
    family_classes: int = 1
    use_recurrence: bool = True
    use_rule_proposal: bool = False
    architecture_version: str = "recurrent-legal-action-actor-critic-v1"

    def validate(self) -> None:
        if self.hidden_size < 8 or (self.use_recurrence and self.recurrent_size < 8) or self.blocks < 1:
            raise ValueError("actor-critic dimensions are too small")
        if not 0 <= self.dropout < 1 or self.family_classes < 1:
            raise ValueError("actor-critic configuration is invalid")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)


def _torch() -> tuple[Any, Any]:
    try:
        import torch
        import torch.nn as nn
    except ImportError as exc:  # pragma: no cover - environment guard
        raise RuntimeError("PyTorch is required for policy learning") from exc
    return torch, nn


def build_actor_critic(config: ActorCriticConfig):
    """Build a policy/value/family model with mask-safe legal-action scores."""
    config.validate()
    torch, nn = _torch()

    class Residual(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.layers = nn.Sequential(
                nn.LayerNorm(config.hidden_size), nn.Linear(config.hidden_size, config.hidden_size * 2),
                nn.GELU(), nn.Dropout(config.dropout), nn.Linear(config.hidden_size * 2, config.hidden_size),
            )

        def forward(self, value):
            return value + self.layers(value)

    class RecurrentLegalActorCritic(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.state_encoder = nn.Sequential(nn.Linear(STATE_FEATURE_DIM, config.hidden_size), nn.LayerNorm(config.hidden_size), nn.GELU())
            self.history_encoder = nn.GRU(HISTORY_FEATURE_DIM, config.recurrent_size, batch_first=True) if config.use_recurrence else None
            self.history_projection = (nn.Sequential(nn.Linear(config.recurrent_size, config.hidden_size), nn.LayerNorm(config.hidden_size), nn.GELU())
                                       if config.use_recurrence else None)
            self.action_encoder = nn.Sequential(nn.Linear(ACTION_FEATURE_DIM, config.hidden_size), nn.LayerNorm(config.hidden_size), nn.GELU())
            self.trunk = nn.Sequential(*[Residual() for _ in range(config.blocks)])
            self.policy_head = nn.Sequential(nn.LayerNorm(config.hidden_size), nn.Linear(config.hidden_size, config.hidden_size // 2), nn.GELU(), nn.Linear(config.hidden_size // 2, 1))
            self.rule_proposal_bias = nn.Parameter(torch.zeros(())) if config.use_rule_proposal else None
            self.value_head = nn.Sequential(nn.LayerNorm(config.hidden_size), nn.Linear(config.hidden_size, config.hidden_size // 2), nn.GELU(), nn.Linear(config.hidden_size // 2, 1), nn.Tanh())
            self.family_head = nn.Linear(config.hidden_size, config.family_classes)

        def forward(self, state, history, history_lengths, actions, action_mask, rule_proposal_mask=None):
            if state.ndim != 2 or history.ndim != 3 or actions.ndim != 3 or action_mask.ndim != 2:
                raise ValueError("actor-critic inputs have invalid rank")
            if state.shape[0] != history.shape[0] or state.shape[0] != actions.shape[0] or action_mask.shape != actions.shape[:2]:
                raise ValueError("actor-critic batch dimensions differ")
            if not bool(action_mask.any(dim=1).all()):
                raise ValueError("every actor-critic row needs a legal action")
            context = self.state_encoder(state)
            if self.history_encoder is not None and self.history_projection is not None:
                lengths = history_lengths.to(dtype=torch.long).clamp(min=1, max=history.shape[1]).cpu()
                packed = nn.utils.rnn.pack_padded_sequence(history, lengths, batch_first=True, enforce_sorted=False)
                _output, hidden = self.history_encoder(packed)
                context = context + self.history_projection(hidden[-1])
            state_repr = self.trunk(context)
            action_repr = self.action_encoder(actions)
            fused = self.trunk(state_repr.unsqueeze(1) + action_repr + state_repr.unsqueeze(1) * action_repr)
            logits = self.policy_head(fused).squeeze(-1).masked_fill(~action_mask, float("-inf"))
            if config.use_rule_proposal:
                if rule_proposal_mask is None or rule_proposal_mask.shape != action_mask.shape:
                    raise ValueError("rule proposal mask is required and must align with legal actions")
                # A learned scalar is deliberately the only extra signal: the
                # proposal is an actor-visible Rule-v0 action, never a hidden
                # score or a private-state feature.
                logits = logits + rule_proposal_mask.to(dtype=logits.dtype) * self.rule_proposal_bias
            return {"policy_logits": logits, "value": self.value_head(state_repr).squeeze(-1), "family_logits": self.family_head(state_repr)}

    return RecurrentLegalActorCritic()
