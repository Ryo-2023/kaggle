"""Legal semantic-action recurrent distributional Q network."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .distributional_q import DistributionalQHead, expected_q
from .recurrent_core import build_recurrent_core


@dataclass(frozen=True, slots=True)
class R2D3ModelConfig:
    state_size: int = 128
    action_size: int = 64
    hidden_size: int = 128
    recurrent_core: str = "gru"
    atoms: int = 51
    # Potential shaping has magnitude at most 0.1 in addition to the terminal
    # outcome.  Keep C51's support wide enough that valid shaped returns are
    # represented rather than silently clipped at the former [-1, 1] bounds.
    v_min: float = -1.1
    v_max: float = 1.1
    opponent_classes: int = 64
    deck_family_classes: int = 32
    action_type_classes: int = 32


class RecurrentDistributionalQ:  # lazily owns torch implementation
    def __new__(cls, config: R2D3ModelConfig) -> Any:
        import torch
        class _Model(torch.nn.Module):
            def __init__(self) -> None:
                super().__init__()
                self.config = config
                self.state = torch.nn.Sequential(
                    torch.nn.Linear(config.state_size, config.hidden_size),
                    torch.nn.LayerNorm(config.hidden_size),
                    torch.nn.SiLU(),
                    torch.nn.Linear(config.hidden_size, config.hidden_size),
                    torch.nn.SiLU(),
                )
                self.core = build_recurrent_core(config.recurrent_core, config.hidden_size, config.hidden_size)
                self.action = torch.nn.Sequential(
                    torch.nn.Linear(config.action_size, config.hidden_size),
                    torch.nn.LayerNorm(config.hidden_size),
                    torch.nn.SiLU(),
                    torch.nn.Linear(config.hidden_size, config.hidden_size),
                    torch.nn.SiLU(),
                )
                self.q = DistributionalQHead(config.hidden_size, config.hidden_size, config.atoms)
                self.opponent_embedding = torch.nn.Linear(config.hidden_size, config.hidden_size)
                self.opponent_classifier = torch.nn.Linear(config.hidden_size, config.opponent_classes)
                self.deck_family = torch.nn.Linear(config.hidden_size, config.deck_family_classes)
                self.next_action_type = torch.nn.Linear(config.hidden_size, config.action_type_classes)
                self.register_buffer("support", torch.linspace(config.v_min, config.v_max, config.atoms))
            def recurrent(self, states: Any, hidden: Any | None = None) -> tuple[Any, Any]:
                if states.ndim != 3:
                    raise ValueError("recurrent states must have shape [batch, time, state]")
                return self.core(self.state(states), hidden)
            def burn_in(self, states: Any, mask: Any, hidden: Any | None = None) -> Any:
                """Advance hidden state through a right-padded prefix.

                Masked padding is not allowed to alter hidden state.  The
                learner calls this under ``no_grad`` and detaches the result
                before the trainable unroll.
                """
                if states.ndim != 3 or mask.ndim != 2 or states.shape[:2] != mask.shape:
                    raise ValueError("burn-in tensors have inconsistent shapes")
                batch = states.shape[0]
                if hidden is None:
                    hidden = states.new_zeros((1, batch, config.hidden_size))
                encoded = self.state(states)
                output, _final = self.core(encoded, hidden)
                lengths = mask.sum(dim=1)
                last = output[
                    torch.arange(batch, device=states.device),
                    (lengths - 1).clamp_min(0),
                ].unsqueeze(0)
                return torch.where(lengths.view(1, batch, 1) > 0, last, hidden)
            def forward(self, states: Any, actions: Any, legal_mask: Any, hidden: Any | None = None) -> dict[str, Any]:
                single = states.ndim == 2
                if single:
                    states = states.unsqueeze(1); actions = actions.unsqueeze(1); legal_mask = legal_mask.unsqueeze(1)
                if states.ndim != 3 or actions.ndim != 4 or legal_mask.ndim != 3:
                    raise ValueError("R2D3 inputs must be [B,T,S], [B,T,A,D], [B,T,A]")
                if states.shape[:2] != actions.shape[:2] or states.shape[:2] != legal_mask.shape[:2] or actions.shape[:3] != legal_mask.shape:
                    raise ValueError("R2D3 input shapes differ")
                sequence, hidden = self.recurrent(states, hidden)
                batch, length, action_count, action_width = actions.shape
                flat_latent = sequence.reshape(batch * length, -1)
                flat_actions = self.action(actions.reshape(batch * length, action_count, action_width))
                flat_mask = legal_mask.reshape(batch * length, action_count)
                flat_logits = self.q(flat_latent, flat_actions, flat_mask)
                logits = flat_logits.reshape(batch, length, action_count, config.atoms)
                result = {"logits": logits, "q": expected_q(logits, self.support), "hidden": hidden,
                          "opponent_embedding": self.opponent_embedding(sequence),
                          "opponent_logits": self.opponent_classifier(sequence),
                          "deck_family_logits": self.deck_family(sequence),
                          "next_action_type_logits": self.next_action_type(sequence),
                          "latent": sequence}
                if single:
                    for name in ("logits", "q", "opponent_embedding", "opponent_logits", "deck_family_logits", "next_action_type_logits", "latent"):
                        result[name] = result[name][:, 0]
                    # Compatibility aliases for older diagnostics.
                    result["deck_family"] = result["deck_family_logits"]
                    result["next_action_type"] = result["next_action_type_logits"]
                return result
        return _Model()
