"""Candidate-only CABT runtime for a trained recurrent distributional Q model."""
from __future__ import annotations

import hashlib
import time
from typing import Any, Mapping

from mage_ptcg.decision_state import build_decision_state
from .actor import select_legal_action
from .semantic_action import encode_legal_action
from .semantic_state import encode_public_state


def deck_hash(deck: list[int]) -> str:
    return hashlib.sha256(("\n".join(map(str, deck)) + "\n").encode()).hexdigest()


class R2D3CandidatePolicy:
    def __init__(self, model: Any, *, deck: list[int], device: Any, policy_version: str,
                 action_mode: str = "greedy", epsilon: float = 0.0, temperature: float = 1.0,
                 inference_server: Any | None = None, game_id: str = "game", seat: int = 0,
                 callback_timeout_seconds: float = 10.0) -> None:
        if len(deck) != 60: raise ValueError("R2D3 runtime requires an exact 60-card Deck")
        self.model, self.deck, self.device, self.policy_version = model, list(deck), device, policy_version
        self.action_mode, self.epsilon, self.temperature = action_mode, epsilon, temperature
        self.inference_server, self.game_id, self.seat = inference_server, game_id, seat
        self.callback_timeout_seconds = callback_timeout_seconds
        self.hidden = None; self.deck_delivered = False; self.last_trace: dict[str, Any] | None = None
        self.traces: list[dict[str, Any]] = []
    def reset(self) -> None:
        self.hidden = None; self.deck_delivered = False; self.last_trace = None; self.traces.clear()
        if self.inference_server is not None: self.inference_server.reset_game(self.game_id)
    def __call__(self, observation: object, configuration: object = None) -> list[int]:
        del configuration
        if not isinstance(observation, Mapping): raise ValueError("R2D3 observation must be an object")
        if observation.get("select") is None:
            if not self.deck_delivered:
                self.deck_delivered = True; self.hidden = None; return list(self.deck)
            return []
        select = observation["select"]; minimum, maximum = int(select["minCount"]), int(select["maxCount"])
        if maximum == 0 or minimum == 0: return []
        state = build_decision_state(dict(observation))
        semantic_actions = []
        for action in state.legal_actions:
            key = action.action_key
            semantic_actions.append(encode_legal_action({"digest": key.digest, "action_type": key.selection_type, "card_id": key.card_id,
                "source_zone": key.source_entity_key, "target_zone": key.target_entity_key, "target_card": key.target_entity_key,
                "amount": None, "selection_order": action.option_index, "phase": key.context, "optional": minimum == 0,
                "semantic_role": key.semantic_operation}))
        import torch
        state_t = torch.tensor([encode_public_state(state.actor_view.public_state)], dtype=torch.float32, device=self.device)
        actions_t = torch.tensor([semantic_actions], dtype=torch.float32, device=self.device)
        legal_t = torch.ones((1, len(semantic_actions)), dtype=torch.bool, device=self.device)
        inference_started = time.perf_counter()
        with torch.no_grad():
            if self.inference_server is None:
                output = self.model(state_t, actions_t, legal_t, self.hidden); self.hidden = output["hidden"]
            else:
                from .inference_server import InferenceRequest
                request = InferenceRequest(self.game_id, self.seat, "main", self.policy_version, state_t, actions_t, legal_t)
                started = time.perf_counter()
                output = self.inference_server.infer(request, expected_policy_version=self.policy_version)
                if time.perf_counter() - started > self.callback_timeout_seconds:
                    raise TimeoutError("central inference callback exceeded its deadline")
        callback_latency_ms = (time.perf_counter() - inference_started) * 1000
        q = output["q"][0].float().cpu().tolist()
        index = select_legal_action(q, mode=self.action_mode, epsilon=self.epsilon, temperature=self.temperature,
                                    seed=int(hashlib.sha256(f"{self.policy_version}:{len(q)}".encode()).hexdigest()[:16], 16))
        chosen = state.legal_actions[index]; count = 1 if maximum == 1 else maximum
        ranked = sorted(range(len(q)), key=lambda offset: (-q[offset], state.legal_actions[offset].option_index))[:count]
        from .sequence import public_prize_potential
        self.last_trace = {"policy_version": self.policy_version, "state": state_t[0].cpu().tolist(),
                           "potential": public_prize_potential(state.actor_view.public_state),
                           "actions": semantic_actions, "selected_action": index, "option_index": chosen.option_index,
                           "selected_actions": ranked, "selection_count": count,
                           "trainable_single_action": count == 1,
                           "q": q, "legal_action_digests": [item.action_key.digest for item in state.legal_actions],
                           "callback_latency_ms": callback_latency_ms}
        self.traces.append(self.last_trace)
        return [state.legal_actions[offset].option_index for offset in ranked]
