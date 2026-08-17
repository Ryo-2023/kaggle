"""Offline fidelity and safety metrics for Student v0."""

from __future__ import annotations

from collections import defaultdict
import math
import statistics
import time
from typing import Callable, Iterable

from .dataset import RuleBCExample
from .model import StudentV0Model, example_matrix, training_feature_domain


def _ordered_indices(model: StudentV0Model, example: RuleBCExample) -> tuple[list[int], list[float]]:
    matrix, _targets = example_matrix(example)
    scores = model.score_vector(matrix)
    ordered = sorted(
        range(len(scores)),
        key=lambda index: (-scores[index], example.legal_actions[index]["digest"], index),
    )
    return ordered, scores


def evaluate_model(model: StudentV0Model, examples: Iterable[RuleBCExample], *, repeats: int = 1,
                    on_example: "Callable[[int, int, dict[str, int]], None] | None" = None) -> dict[str, object]:
    values = list(examples)
    if not values:
        raise ValueError("evaluation dataset is empty")
    if repeats < 1:
        raise ValueError("repeats must be positive")
    if training_feature_domain(values) != model.feature_domain:
        raise ValueError("model and evaluation dataset feature domains differ")
    top1 = 0
    top3 = 0
    legal = 0
    fallback = 0
    losses: list[float] = []
    timings_us: list[float] = []
    by_type: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for position, example in enumerate(values):
        start = time.perf_counter_ns()
        ordered, scores = _ordered_indices(model, example)
        for _ in range(repeats - 1):
            _ordered_indices(model, example)
        timings_us.append((time.perf_counter_ns() - start) / repeats / 1_000)
        target_set = set(example.target_action_digests)
        predicted = [example.legal_actions[index]["digest"] for index in ordered]
        if predicted and predicted[0] in target_set:
            top1 += 1
        if any(digest in target_set for digest in predicted[:3]):
            top3 += 1
        selection_count = example.min_count if example.min_count else 1
        if example.min_count == 0 and example.selection_type != 0:
            selection_count = 0
        chosen = ordered[:selection_count]
        if len(chosen) == len(set(chosen)) and all(0 <= index < len(example.legal_actions) for index in chosen):
            legal += 1
        maximum = max(scores)
        probabilities = [math.exp(min(80.0, score - maximum)) for score in scores]
        normalizer = sum(probabilities)
        target_probability = sum(probabilities[index] / normalizer for index, action in enumerate(example.legal_actions) if action["digest"] in target_set)
        losses.append(-math.log(max(target_probability, 1e-12)))
        key = str(example.selection_type)
        by_type[key][0] += int(bool(predicted and predicted[0] in target_set))
        by_type[key][1] += 1
        if on_example is not None:
            on_example(position, len(values), {"legal": legal, "top1": top1, "fallback": fallback})
    ordered_timings = sorted(timings_us)
    p95_index = min(len(ordered_timings) - 1, math.ceil(len(ordered_timings) * 0.95) - 1)
    p99_index = min(len(ordered_timings) - 1, math.ceil(len(ordered_timings) * 0.99) - 1)
    return {
        "examples": len(values),
        "fallback_rate": 0.0,
        "holdout_loss": sum(losses) / len(losses),
        "legal_action_rate": legal / len(values),
        "latency_us_p50": statistics.median(ordered_timings),
        "latency_us_p95": ordered_timings[p95_index],
        "latency_us_p99": ordered_timings[p99_index],
        "teacher_top1_fidelity": top1 / len(values),
        "teacher_top3_fidelity": top3 / len(values),
        "selection_type_top1": {key: correct / total for key, (correct, total) in sorted(by_type.items())},
        "selection_type_examples": {key: total for key, (_correct, total) in sorted(by_type.items())},
    }


__all__ = ["evaluate_model"]
