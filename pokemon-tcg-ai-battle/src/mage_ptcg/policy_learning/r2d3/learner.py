"""Distributional Double-Q learner with true recurrent sequence updates."""
from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

from .distributional_q import project_categorical


@dataclass(frozen=True, slots=True)
class LearnerConfig:
    n_step: int = 5
    target_update_interval: int = 250
    gradient_clip: float = 40.0
    demonstration_margin: float = 0.8
    bc_weight: float = 0.0
    auxiliary_weight: float = 0.01
    conservative_weight: float = 0.05
    priority_eta: float = 0.9
    priority_epsilon: float = 1e-6
    demonstration_priority_bonus: float = 1.0

    def validate(self) -> None:
        if self.n_step < 1 or self.target_update_interval < 1 or self.gradient_clip <= 0:
            raise ValueError("invalid learner schedule")
        if self.demonstration_margin < 0 or self.bc_weight < 0 or self.auxiliary_weight < 0 or self.conservative_weight < 0:
            raise ValueError("learner loss weights must be non-negative")
        if not 0 <= self.priority_eta <= 1 or self.priority_epsilon <= 0 or self.demonstration_priority_bonus < 0:
            raise ValueError("invalid priority configuration")


class R2D3Learner:
    def __init__(self, model: Any, optimizer: Any, *, config: LearnerConfig = LearnerConfig()) -> None:
        config.validate()
        self.model, self.target, self.optimizer, self.config, self.steps = model, copy.deepcopy(model), optimizer, config, 0
        for parameter in self.target.parameters():
            parameter.requires_grad_(False)

    @staticmethod
    def _selected(values: Any, indices: Any) -> Any:
        return values.gather(-2, indices.unsqueeze(-1).unsqueeze(-1).expand(*indices.shape, 1, values.shape[-1])).squeeze(-2)

    @staticmethod
    def _selected_q(values: Any, indices: Any) -> Any:
        return values.gather(-1, indices.unsqueeze(-1)).squeeze(-1)

    def _finish(self, loss: Any) -> tuple[Any, bool]:
        import torch
        loss_finite = torch.isfinite(loss)
        self.optimizer.zero_grad(set_to_none=True)
        loss.backward()
        norm = torch.nn.utils.clip_grad_norm_(self.model.parameters(), self.config.gradient_clip)
        # One fail-closed synchronization covers both loss and gradient.  A
        # non-finite backward is safe here because optimizer.step() is still
        # gated on this result.
        if not bool(loss_finite & torch.isfinite(norm)):
            self.optimizer.zero_grad(set_to_none=True)
            raise FloatingPointError("R2D3 loss or gradient is non-finite")
        self.optimizer.step()
        self.steps += 1
        updated = self.steps % self.config.target_update_interval == 0
        if updated:
            self.target.load_state_dict(self.model.state_dict())
        return norm.detach(), updated

    @staticmethod
    def _metric_payload(
        names: tuple[str, ...],
        values: tuple[Any, ...],
        priorities: Any,
    ) -> dict[str, Any]:
        """Transfer all scalar metrics and priorities with one device sync."""

        import torch

        scalars = torch.stack(
            tuple(value.detach().float().reshape(()) for value in values)
        )
        priority_values = priorities.detach().float().reshape(-1)
        host = torch.cat((scalars, priority_values)).cpu().tolist()
        return {
            **dict(zip(names, host[: len(names)], strict=True)),
            "sequence_priorities": host[len(names) :],
        }

    def update(
        self,
        states: Any,
        actions: Any,
        legal_mask: Any,
        selected: Any,
        rewards: Any,
        discounts: Any,
        next_states: Any | None = None,
        next_actions: Any | None = None,
        next_mask: Any | None = None,
        importance: Any | None = None,
        demonstration: Any | None = None,
        opponent_embedding_target: Any | None = None,
        deck_family_target: Any | None = None,
        next_action_type_target: Any | None = None,
        *,
        sequence_mask: Any | None = None,
        bootstrap_indices: Any | None = None,
        burn_in_states: Any | None = None,
        burn_in_mask: Any | None = None,
        opponent_class_target: Any | None = None,
    ) -> dict[str, Any]:
        """Update either the preserved flat baseline or a recurrent unroll.

        Rank-two states are the frozen ``OFFLINE_WINDOWED_C51_BASELINE`` path.
        Rank-three states require masks, burn-in, and bootstrap indices and
        compute a loss at every valid unroll position.
        """
        if states.ndim == 2:
            if any(value is None for value in (next_states, next_actions, next_mask, importance)):
                raise ValueError("flat baseline update lacks next-state tensors")
            return self._update_flat(states, actions, legal_mask, selected, rewards, discounts, next_states, next_actions,
                                     next_mask, importance, demonstration, opponent_embedding_target,
                                     deck_family_target, next_action_type_target)
        if states.ndim != 3:
            raise ValueError("states must be rank two or three")
        if any(value is None for value in (importance, sequence_mask, bootstrap_indices, burn_in_states, burn_in_mask)):
            raise ValueError("sequence update lacks recurrent tensors")
        return self._update_sequence(
            states, actions, legal_mask, selected, rewards, discounts, importance, demonstration,
            sequence_mask, bootstrap_indices, burn_in_states, burn_in_mask,
            opponent_class_target, deck_family_target, next_action_type_target,
        )

    def _update_flat(
        self, states: Any, actions: Any, legal_mask: Any, selected: Any, rewards: Any, discounts: Any,
        next_states: Any, next_actions: Any, next_mask: Any, importance: Any, demonstration: Any | None,
        opponent_embedding_target: Any | None, deck_family_target: Any | None, next_action_type_target: Any | None,
    ) -> dict[str, Any]:
        import torch
        import torch.nn.functional as functional
        output = self.model(states, actions, legal_mask)
        selected_logits = self._selected(output["logits"], selected)
        with torch.no_grad():
            online_next = self.model(next_states, next_actions, next_mask)
            next_choice = online_next["q"].masked_fill(~next_mask, float("-inf")).argmax(dim=1)
            target_next = self._selected(self.target(next_states, next_actions, next_mask)["logits"], next_choice)
            projected = project_categorical(rewards, discounts, torch.softmax(target_next, dim=-1), self.model.support)
        per_item = -(projected * torch.log_softmax(selected_logits, dim=-1)).sum(dim=-1)
        selected_q = self._selected_q(output["q"], selected)
        td = (selected_q - (projected * self.model.support).sum(dim=-1)).abs()
        distributional = (per_item * importance).mean()
        conservative = (torch.logsumexp(output["q"].masked_fill(~legal_mask, float("-inf")), dim=-1) - selected_q).mean()
        loss = distributional + self.config.conservative_weight * conservative
        margin = torch.zeros((), device=loss.device)
        if demonstration is not None:
            competing = output["q"].masked_fill(~legal_mask, float("-inf")).clone()
            competing.scatter_(1, selected.view(-1, 1), float("-inf"))
            raw_margin = functional.relu(
                self.config.demonstration_margin - selected_q + competing.max(dim=1).values
            )
            margin = (raw_margin * demonstration).sum() / demonstration.sum().clamp_min(1)
            loss = loss + margin
        bc = torch.zeros((), device=loss.device)
        if demonstration is not None and self.config.bc_weight:
            selected_log_probability = torch.log_softmax(output["q"].masked_fill(~legal_mask, float("-inf")), dim=-1).gather(1, selected.view(-1, 1)).squeeze(1)
            bc = -(selected_log_probability * demonstration).sum() / demonstration.sum().clamp_min(1)
            loss = loss + self.config.bc_weight * bc
        auxiliary = torch.zeros((), device=loss.device)
        if opponent_embedding_target is not None:
            auxiliary = auxiliary + functional.mse_loss(output["opponent_embedding"], opponent_embedding_target)
        if deck_family_target is not None:
            auxiliary = auxiliary + functional.mse_loss(output["deck_family_logits"].mean(dim=-1), deck_family_target.float())
        if next_action_type_target is not None:
            auxiliary = auxiliary + functional.mse_loss(output["next_action_type_logits"].mean(dim=-1), next_action_type_target.float())
        loss = loss + self.config.auxiliary_weight * auxiliary
        norm, updated = self._finish(loss)
        priorities = (td + self.config.priority_epsilon + (demonstration.float() if demonstration is not None else 0.0)
                      * self.config.demonstration_priority_bonus)
        return self._metric_payload(
            (
                "loss",
                "distributional_loss",
                "conservative_loss",
                "td_error_mean",
                "td_error_max",
                "margin",
                "bc_loss",
                "auxiliary_loss",
                "q_mean",
                "gradient_norm",
                "target_updated",
                "sequence_length",
            ),
            (
                loss,
                distributional,
                conservative,
                td.mean(),
                td.max(),
                margin,
                bc,
                auxiliary,
                output["q"][legal_mask].mean(),
                norm,
                loss.new_tensor(float(updated)),
                loss.new_tensor(1.0),
            ),
            priorities,
        )

    def _update_sequence(
        self, states: Any, actions: Any, legal_mask: Any, selected: Any, rewards: Any, discounts: Any,
        importance: Any, demonstration: Any | None, sequence_mask: Any, bootstrap_indices: Any,
        burn_in_states: Any, burn_in_mask: Any, opponent_class_target: Any | None,
        deck_family_target: Any | None, next_action_type_target: Any | None,
    ) -> dict[str, Any]:
        import torch
        import torch.nn.functional as functional
        if not bool(sequence_mask.any(dim=1).all()):
            raise ValueError("every recurrent sample needs a learner step")
        with torch.no_grad():
            hidden = self.model.burn_in(burn_in_states, burn_in_mask).detach()
            target_hidden = self.target.burn_in(burn_in_states, burn_in_mask).detach()
        output = self.model(states, actions, legal_mask, hidden)
        selected_logits = self._selected(output["logits"], selected)
        selected_q = self._selected_q(output["q"], selected)
        with torch.no_grad():
            target_output = self.target(states, actions, legal_mask, target_hidden)
            batch = torch.arange(states.shape[0], device=states.device).unsqueeze(1).expand_as(bootstrap_indices)
            safe_bootstrap = bootstrap_indices.clamp_min(0)
            online_next_q = output["q"].detach()[batch, safe_bootstrap]
            online_next_mask = legal_mask[batch, safe_bootstrap]
            next_choice = online_next_q.masked_fill(~online_next_mask, float("-inf")).argmax(dim=-1)
            target_next_logits = target_output["logits"][batch, safe_bootstrap]
            target_next = self._selected(target_next_logits, next_choice)
            effective_discount = torch.where(bootstrap_indices >= 0, discounts, torch.zeros_like(discounts))
            projected = project_categorical(
                rewards.reshape(-1), effective_discount.reshape(-1),
                torch.softmax(target_next.reshape(-1, target_next.shape[-1]), dim=-1), self.model.support,
            ).reshape(*rewards.shape, -1)
        per_step = -(projected * torch.log_softmax(selected_logits, dim=-1)).sum(dim=-1)
        target_q = (projected * self.model.support).sum(dim=-1)
        td = (selected_q - target_q).abs() * sequence_mask
        valid_count = sequence_mask.sum(dim=1).clamp_min(1)
        per_sequence = (per_step * sequence_mask).sum(dim=1) / valid_count
        distributional = (per_sequence * importance).mean()
        conservative_step = torch.logsumexp(output["q"].masked_fill(~legal_mask, float("-inf")), dim=-1) - selected_q
        conservative = (conservative_step * sequence_mask).sum() / sequence_mask.sum()
        loss = distributional + self.config.conservative_weight * conservative
        margin = torch.zeros((), device=loss.device)
        if demonstration is not None:
            competing = output["q"].masked_fill(~legal_mask, float("-inf")).clone()
            competing.scatter_(-1, selected.unsqueeze(-1), float("-inf"))
            raw_margin = functional.relu(self.config.demonstration_margin - selected_q + competing.max(dim=-1).values)
            demo_mask = demonstration & sequence_mask
            margin = (raw_margin * demo_mask).sum() / demo_mask.sum().clamp_min(1)
            loss = loss + margin
        bc = torch.zeros((), device=loss.device)
        if demonstration is not None and self.config.bc_weight:
            log_probability = torch.log_softmax(output["q"].masked_fill(~legal_mask, float("-inf")), dim=-1)
            selected_log_probability = log_probability.gather(-1, selected.unsqueeze(-1)).squeeze(-1)
            bc = -(selected_log_probability * demo_mask).sum() / demo_mask.sum().clamp_min(1)
            loss = loss + self.config.bc_weight * bc
        auxiliary = torch.zeros((), device=loss.device)
        flat_mask = sequence_mask.reshape(-1)
        if opponent_class_target is not None:
            auxiliary = auxiliary + functional.cross_entropy(
                output["opponent_logits"].reshape(-1, output["opponent_logits"].shape[-1])[flat_mask],
                opponent_class_target.reshape(-1)[flat_mask],
            )
        if deck_family_target is not None:
            auxiliary = auxiliary + functional.cross_entropy(
                output["deck_family_logits"].reshape(-1, output["deck_family_logits"].shape[-1])[flat_mask],
                deck_family_target.reshape(-1)[flat_mask],
            )
        if next_action_type_target is not None:
            auxiliary = auxiliary + functional.cross_entropy(
                output["next_action_type_logits"].reshape(-1, output["next_action_type_logits"].shape[-1])[flat_mask],
                next_action_type_target.reshape(-1)[flat_mask],
            )
        loss = loss + self.config.auxiliary_weight * auxiliary
        norm, updated = self._finish(loss)
        maximum = td.masked_fill(~sequence_mask, float("-inf")).max(dim=1).values
        mean = td.sum(dim=1) / valid_count
        priorities = self.config.priority_eta * maximum + (1.0 - self.config.priority_eta) * mean + self.config.priority_epsilon
        if demonstration is not None:
            priorities = priorities + (demonstration & sequence_mask).any(dim=1).float() * self.config.demonstration_priority_bonus
        return self._metric_payload(
            (
                "loss",
                "distributional_loss",
                "conservative_loss",
                "td_error_mean",
                "td_error_max",
                "priority_mean",
                "priority_max",
                "margin",
                "bc_loss",
                "auxiliary_loss",
                "q_mean",
                "gradient_norm",
                "target_updated",
                "sequence_length",
            ),
            (
                loss,
                distributional,
                conservative,
                td[sequence_mask].mean(),
                td[sequence_mask].max(),
                priorities.mean(),
                priorities.max(),
                margin,
                bc,
                auxiliary,
                output["q"][legal_mask].mean(),
                norm,
                loss.new_tensor(float(updated)),
                sequence_mask.sum(dim=1).float().mean(),
            ),
            priorities,
        )
