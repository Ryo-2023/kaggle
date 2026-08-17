"""Sealed, teacher-forced offline diagnostics for recurrent V4 checkpoints.

The metrics in this module are diagnostic only.  They evaluate the exact
projected V4 teacher rows selected by the bounded materializer and deliberately
do not execute CABT games or claim promotion authority.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
import math

import torch
from torch.nn import functional as F

from mage_ptcg.meta_specialist.neural_model_v4 import SpecialistModelV4
from mage_ptcg.meta_specialist.recurrent_bc_v4 import (
    RESEARCH_ONLY_UNIFORM_WEIGHT,
    _record_groups,
    _validate_sequences,
)
from mage_ptcg.meta_specialist.recurrent_dataset_v4 import RecurrentBCSequenceV4, RecurrentBCStepV4


V4_IMITATION_METRICS_SCHEMA_V1 = "meta-specialist-v4-imitation-metrics-v1"
_RECURRENCES = frozenset(("carry", "reset"))


class V4ImitationMetricsError(ValueError):
    """Raised when an offline imitation pass would not be comparable."""


class _Summary:
    __slots__ = ("count", "exact", "top3", "nll_numerator", "nll_weight", "teacher_mass_at_top1")

    def __init__(self) -> None:
        self.count = 0
        self.exact = 0
        self.top3 = 0
        self.nll_numerator = 0.0
        self.nll_weight = 0.0
        self.teacher_mass_at_top1 = 0.0

    def add_logits(self, *, logits: torch.Tensor, nll: float, weight: float, target: int, target_masses: tuple[float, ...]) -> tuple[int, bool]:
        if logits.ndim != 1 or logits.numel() != len(target_masses) or logits.numel() < 2:
            raise V4ImitationMetricsError("complete-action logits are invalid")
        if torch.isnan(logits).any() or not torch.isfinite(logits).any():
            raise V4ImitationMetricsError("complete-action logits have no finite choice")
        prediction = int(torch.argmax(logits).item())
        top_indices = tuple(int(index) for index in logits.topk(min(3, logits.numel())).indices.tolist())
        if not math.isfinite(nll) or not math.isfinite(weight) or weight <= 0.0:
            raise V4ImitationMetricsError("complete-action metric is non-finite")
        self.count += 1
        exact = prediction == target
        self.exact += int(exact)
        self.top3 += int(target in top_indices)
        self.nll_numerator += weight * nll
        self.nll_weight += weight
        self.teacher_mass_at_top1 += float(target_masses[prediction])
        return prediction, exact

    def report(self) -> dict[str, float | int | None]:
        return {
            "eligible_rows": self.count,
            "exact_top1_count": self.exact,
            "top1": self.exact / self.count if self.count else None,
            "top3": self.top3 / self.count if self.count else None,
            "complete_action_nll": self.nll_numerator / self.nll_weight if self.nll_weight else None,
            "nll_weight": self.nll_weight,
            "mean_teacher_mass_at_model_top1": self.teacher_mass_at_top1 / self.count if self.count else None,
        }


def _complete_logits_v4(model: SpecialistModelV4, step: RecurrentBCStepV4, output: object) -> torch.Tensor:
    logits = getattr(output, "logits", None)
    global_token = getattr(output, "global_token", None)
    if not isinstance(logits, torch.Tensor) or not isinstance(global_token, torch.Tensor):
        raise V4ImitationMetricsError("V4 model output is invalid")
    if bool(getattr(step.step_input, "stop_available", False)):
        stop = model.stop_vector @ global_token + model.stop_bias
        logits = torch.cat((logits, stop.reshape(1)))
    if logits.numel() != len(step.target_masses):
        raise V4ImitationMetricsError("complete-action logits do not match the sealed target domain")
    return logits


def _action_type(step: RecurrentBCStepV4) -> str:
    if step.target_index < len(step.state.candidates):
        return str(step.state.candidates[step.target_index].action_type)
    if bool(getattr(step.step_input, "stop_available", False)) and step.target_index == len(step.state.candidates):
        return "STOP"
    raise V4ImitationMetricsError("target index is outside its sealed complete-action domain")


def _prefix_depth(step: RecurrentBCStepV4) -> int:
    prefix = getattr(step.state, "semantic_prefix", None)
    if type(prefix) is not tuple:
        raise V4ImitationMetricsError("V4 state semantic prefix is invalid")
    return len(prefix)


def evaluate_recurrent_imitation_v4(
    model: SpecialistModelV4, sequences: Sequence[RecurrentBCSequenceV4], *,
    partition: str, recurrence: str,
) -> dict[str, object]:
    """Evaluate one exact V4 partition with either carried or reset GRU state.

    A forced complete-action domain of size one contributes only to the
    ``forced_domain_size1_rows`` audit counter.  It is excluded from NLL and
    ranking metrics because it carries no policy-selection information.
    """
    if type(model) is not SpecialistModelV4:
        raise V4ImitationMetricsError("V4 imitation metrics require SpecialistModelV4")
    if partition not in {"train", "validation"} or recurrence not in _RECURRENCES:
        raise V4ImitationMetricsError("partition or recurrence mode is invalid")
    _validate_sequences(sequences, partition=partition, mode=RESEARCH_ONLY_UNIFORM_WEIGHT)
    previous_training = model.training
    model.eval()
    all_rows = _Summary()
    root = _Summary()
    later = _Summary()
    action_type: dict[str, _Summary] = defaultdict(_Summary)
    survival: dict[int, list[int]] = defaultdict(lambda: [0, 0])
    forced = 0
    post_burn_records = 0
    non_reset_hidden_records = 0
    try:
        with torch.no_grad():
            for sequence in sequences:
                hidden: torch.Tensor | None = None
                for record_index, group in enumerate(_record_groups(sequence)):
                    carry = recurrence == "carry"
                    if carry and hidden is not None:
                        non_reset_hidden_records += 1
                    outputs = model.forward_record_group_v4(
                        tuple(step.state for step in group),
                        hidden_state=hidden if carry else None,
                        episode_start=group[0].episode_start if carry else True,
                    )
                    if record_index >= sequence.burn_in:
                        post_burn_records += 1
                        survived_so_far = True
                        for step, output in zip(group, outputs, strict=True):
                            logits = _complete_logits_v4(model, step, output)
                            if logits.numel() == 1:
                                forced += 1
                                continue
                            target = step.target_index
                            target_masses = step.target_masses
                            log_probs = F.log_softmax(logits, dim=0)
                            nll = float((-
                                torch.tensor(target_masses, dtype=logits.dtype, device=logits.device) * log_probs
                            ).sum().item())
                            weight = float(step.reach_mass)
                            prediction, exact = all_rows.add_logits(
                                logits=logits, nll=nll, weight=weight, target=target, target_masses=target_masses,
                            )
                            target_summary = root if _prefix_depth(step) == 0 else later
                            target_summary.add_logits(
                                logits=logits, nll=nll, weight=weight, target=target, target_masses=target_masses,
                            )
                            action_type[_action_type(step)].add_logits(
                                logits=logits, nll=nll, weight=weight, target=target, target_masses=target_masses,
                            )
                            depth = _prefix_depth(step)
                            survival[depth][0] += 1
                            survived_so_far = survived_so_far and exact
                            survival[depth][1] += int(survived_so_far)
                    hidden = outputs[0].hidden_state if carry else None
                    if hidden is not None:
                        hidden = hidden.detach()
    finally:
        model.train(previous_training)
    if all_rows.count == 0:
        raise V4ImitationMetricsError("partition contains no non-forced post-burn-in complete actions")
    return {
        "schema": V4_IMITATION_METRICS_SCHEMA_V1,
        "partition": partition,
        "recurrence": recurrence,
        "post_burn_records": post_burn_records,
        "non_reset_hidden_records": non_reset_hidden_records,
        "complete_action": {**all_rows.report(), "forced_domain_size1_rows": forced},
        "root": root.report(),
        "later": later.report(),
        "action_type": {key: value.report() for key, value in sorted(action_type.items())},
        "teacher_prefix_survival": [
            {
                "prefix_depth": depth,
                "eligible_rows": values[0],
                "survived_rows": values[1],
                "survival_rate": values[1] / values[0] if values[0] else None,
            }
            for depth, values in sorted(survival.items())
        ],
    }


__all__ = [
    "V4_IMITATION_METRICS_SCHEMA_V1", "V4ImitationMetricsError", "evaluate_recurrent_imitation_v4",
]
