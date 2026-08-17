"""Outcome-weighted behavior cloning used only before RL step zero."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from mage_ptcg.continuous_league.contracts import atomic_write_json

from .contracts import BootstrapContractError


def behavior_cloning_loss(q_values: Any, legal_mask: Any, selected: Any, behavior_weight: Any) -> Any:
    """Weighted selected-action cross entropy over legal actions only."""

    import torch
    import torch.nn.functional as functional

    if q_values.ndim != 2 or legal_mask.shape != q_values.shape:
        raise BootstrapContractError("behavior cloning q_values and legal_mask shapes differ")
    if selected.shape != (q_values.shape[0],) or behavior_weight.shape != (q_values.shape[0],):
        raise BootstrapContractError("behavior cloning batch shape is invalid")
    if not bool(legal_mask.any(dim=1).all()):
        raise BootstrapContractError("every behavior cloning sample needs a legal action")
    if not bool(((selected >= 0) & (selected < q_values.shape[1])).all()):
        raise BootstrapContractError("behavior cloning selected action is out of range")
    chosen_legal = legal_mask.gather(1, selected.view(-1, 1)).squeeze(1)
    if not bool(chosen_legal.all()):
        raise BootstrapContractError("behavior cloning selected action is illegal")
    if not bool(torch.isfinite(behavior_weight).all()) or not bool((behavior_weight > 0).all()):
        raise BootstrapContractError("behavior cloning weights must be finite and positive")
    logits = q_values.masked_fill(~legal_mask, float("-inf"))
    per_item = functional.cross_entropy(logits, selected, reduction="none")
    return (per_item * behavior_weight).sum() / behavior_weight.sum()


@dataclass(frozen=True, slots=True)
class DistillationConfig:
    learning_rate: float = 1e-4
    batch_size: int = 32
    max_epochs: int = 20
    patience_epochs: int = 4
    gradient_clip: float = 40.0
    seed: int = 71_000

    def validate(self) -> None:
        if self.learning_rate <= 0 or self.batch_size < 1 or self.max_epochs < 1 or self.patience_epochs < 0 or self.gradient_clip <= 0:
            raise BootstrapContractError("invalid distillation configuration")


@dataclass(frozen=True, slots=True)
class DistillationResult:
    weights_path: str
    best_epoch: int
    train_loss: float
    validation_loss: float | None
    validation_top1: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "weights_path": self.weights_path,
            "best_epoch": self.best_epoch,
            "train_loss": self.train_loss,
            "validation_loss": self.validation_loss,
            "validation_top1": self.validation_top1,
        }


def _batch(values: Sequence[Mapping[str, Any]], *, indices: Sequence[int], device: Any) -> tuple[Any, Any, Any, Any, Any]:
    import torch

    states = torch.tensor([values[index]["state"] for index in indices], dtype=torch.float32, device=device)
    selected_examples = [values[index] for index in indices]
    action_count = max(len(example["actions"]) for example in selected_examples)
    action_width = len(selected_examples[0]["actions"][0])
    actions = torch.zeros((len(indices), action_count, action_width), dtype=torch.float32, device=device)
    legal = torch.zeros((len(indices), action_count), dtype=torch.bool, device=device)
    for offset, example in enumerate(selected_examples):
        encoded_actions = torch.tensor(example["actions"], dtype=torch.float32, device=device)
        if encoded_actions.ndim != 2 or encoded_actions.shape[1] != action_width:
            raise BootstrapContractError("distillation action encoding widths differ")
        count = encoded_actions.shape[0]
        actions[offset, :count] = encoded_actions
        legal[offset, :count] = torch.tensor(example["legal_mask"], dtype=torch.bool, device=device)
    selected = torch.tensor([values[index]["selected_action"] for index in indices], dtype=torch.long, device=device)
    weights = torch.tensor([values[index]["behavior_weight"] for index in indices], dtype=torch.float32, device=device)
    return states, actions, legal, selected, weights


def _validate_encoded_example(example: Mapping[str, Any]) -> None:
    required = {"state", "actions", "legal_mask", "selected_action", "behavior_weight"}
    missing = required - set(example)
    if missing:
        raise BootstrapContractError(f"distillation example misses {sorted(missing)}")
    actions = example["actions"]
    legal = example["legal_mask"]
    if not isinstance(actions, list) or not isinstance(legal, list) or len(actions) != len(legal) or not actions:
        raise BootstrapContractError("distillation action encoding is invalid")
    selected = example["selected_action"]
    if type(selected) is not int or not 0 <= selected < len(legal) or not legal[selected]:
        raise BootstrapContractError("distillation selected action is invalid")


def distill_bootstrap_policy(
    *,
    model: Any,
    train_examples: Sequence[Mapping[str, Any]],
    validation_examples: Sequence[Mapping[str, Any]],
    config: DistillationConfig,
    output: Path,
    device: Any | None = None,
) -> DistillationResult:
    """Train the existing R2D3 Q output as a masked behavior policy.

    The caller supplies already actor-visible semantic encodings.  This keeps
    raw CABT observations out of the learner and makes the privacy boundary
    auditable at the dataset seal stage.
    """

    import torch

    config.validate()
    if not train_examples:
        raise BootstrapContractError("distillation needs at least one training example")
    for example in (*train_examples, *validation_examples):
        _validate_encoded_example(example)
    target_device = device or next(model.parameters()).device
    random.seed(config.seed)
    torch.manual_seed(config.seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(config.seed)
    optimizer = torch.optim.AdamW(model.parameters(), lr=config.learning_rate)
    best_state: dict[str, Any] | None = None
    best_loss = math.inf
    best_epoch = -1
    stale = 0
    last_train = math.inf
    for epoch in range(config.max_epochs):
        model.train()
        order = list(range(len(train_examples)))
        random.Random(config.seed + epoch).shuffle(order)
        losses: list[float] = []
        for start in range(0, len(order), config.batch_size):
            states, actions, legal, selected, weights = _batch(train_examples, indices=order[start:start + config.batch_size], device=target_device)
            output_values = model(states, actions, legal)["q"]
            loss = behavior_cloning_loss(output_values, legal, selected, weights)
            if not bool(torch.isfinite(loss)):
                raise FloatingPointError("distillation loss is non-finite")
            optimizer.zero_grad(set_to_none=True)
            loss.backward()
            gradient_norm = torch.nn.utils.clip_grad_norm_(model.parameters(), config.gradient_clip)
            if not bool(torch.isfinite(gradient_norm)):
                raise FloatingPointError("distillation gradient is non-finite")
            optimizer.step()
            losses.append(float(loss.detach().cpu()))
        last_train = sum(losses) / len(losses)
        model.eval()
        if validation_examples:
            with torch.no_grad():
                values = _batch(validation_examples, indices=list(range(len(validation_examples))), device=target_device)
                q = model(values[0], values[1], values[2])["q"]
                validation_loss = float(behavior_cloning_loss(q, values[2], values[3], values[4]).cpu())
                top1 = q.masked_fill(~values[2], float("-inf")).argmax(dim=1)
                validation_top1 = float((top1 == values[3]).float().mean().cpu())
        else:
            validation_loss = last_train
            validation_top1 = None
        if validation_loss < best_loss:
            best_loss = validation_loss
            best_epoch = epoch
            best_state = {key: value.detach().cpu().clone() for key, value in model.state_dict().items()}
            stale = 0
        else:
            stale += 1
            if stale > config.patience_epochs:
                break
    if best_state is None:  # defensive; a finite training epoch always sets it
        raise BootstrapContractError("distillation produced no checkpoint")
    model.load_state_dict(best_state)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    weights_path = output / "distilled_weights.pt"
    temporary = weights_path.with_suffix(".pt.tmp")
    torch.save(best_state, temporary)
    temporary.replace(weights_path)
    result = DistillationResult(str(weights_path), best_epoch, last_train, best_loss if validation_examples else None, validation_top1)
    atomic_write_json(output / "distillation_summary.json", result.to_dict())
    return result
