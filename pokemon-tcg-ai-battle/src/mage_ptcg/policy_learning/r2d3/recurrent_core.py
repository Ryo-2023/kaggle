"""Persistent GRU and lightweight gated linear recurrent (LRU) cores."""
from __future__ import annotations

from typing import Any


def build_recurrent_core(kind: str, input_size: int, hidden_size: int) -> Any:
    import torch
    if kind == "gru": return torch.nn.GRU(input_size, hidden_size, batch_first=True)
    if kind != "lru": raise ValueError("recurrent core must be gru or lru")
    return LRU(input_size, hidden_size)


class LRUCell:  # constructed lazily only with torch present
    def __init__(self, input_size: int, hidden_size: int) -> None:
        import torch
        self._module = torch.nn.Module(); self._module.input = torch.nn.Linear(input_size, hidden_size); self._module.gate = torch.nn.Linear(input_size + hidden_size, hidden_size)
        self.hidden_size = hidden_size
    def __call__(self, value: Any, hidden: Any) -> Any:
        import torch
        gate = torch.sigmoid(self._module.gate(torch.cat((value, hidden), dim=-1)))
        return gate * hidden + (1.0 - gate) * torch.tanh(self._module.input(value))


def _lru_init(self: Any, input_size: int, hidden_size: int) -> None:
    import torch
    torch.nn.Module.__init__(self); self.input = torch.nn.Linear(input_size, hidden_size); self.gate = torch.nn.Linear(input_size + hidden_size, hidden_size); self.hidden_size = hidden_size


def _lru_forward(self: Any, values: Any, hidden: Any | None = None) -> tuple[Any, Any]:
    import torch
    batch, length, _ = values.shape
    state = torch.zeros((batch, self.hidden_size), dtype=values.dtype, device=values.device) if hidden is None else hidden.squeeze(0)
    output = []
    for offset in range(length):
        value = values[:, offset]; gate = torch.sigmoid(self.gate(torch.cat((value, state), dim=-1)))
        state = gate * state + (1.0 - gate) * torch.tanh(self.input(value)); output.append(state)
    return torch.stack(output, dim=1), state.unsqueeze(0)


def _lru_reset(self: Any) -> None: return None


def _make_lru() -> Any:
    import torch
    return type("LRU", (torch.nn.Module,), {"__init__": _lru_init, "forward": _lru_forward, "reset_parameters": _lru_reset})


LRU = _make_lru()
