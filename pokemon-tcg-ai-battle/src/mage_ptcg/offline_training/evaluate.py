"""Offline evaluation and tiny paired screening for the neural Student.

Evaluation runs the exact pure-Python export core used in the Kaggle package,
so held-out metrics reflect the shipped runtime.  A linear Student baseline is
trained on the same train split for comparison.  Tiny screening exercises the
neural runtime through a deterministic, seat-balanced fixture harness; because
actual cabt is unavailable, win/loss is not measured and the verdict is honest
about the evidence level.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from mage_ptcg.offline_training.dataset import iter_decisions, iter_examples
from mage_ptcg.offline_training.export import score_candidates


def evaluate_neural(dataset_dir: str | Path, export_document: dict[str, Any], *, split: str = "test") -> dict[str, Any]:
    """Metrics over legal candidates using the shipped pure-Python scorer."""
    total = 0
    top1 = 0
    top3 = 0
    reciprocal = 0.0
    nll_sum = 0.0
    legal = 0
    non_finite = 0
    by_type: dict[str, list[int]] = {}
    by_count: dict[str, list[int]] = {}
    for decision in iter_decisions(dataset_dir, split):
        if not decision.target_indices:
            continue
        rows = [list(row) for row in decision.candidate_features]
        scores = score_candidates(export_document, rows)
        if any(not math.isfinite(score) for score in scores):
            non_finite += 1
            continue
        n = len(rows)
        order = sorted(range(n), key=lambda i: (-scores[i], decision.candidate_digests[i], i))
        target_set = set(decision.target_indices)
        total += 1
        if order[0] in target_set:
            top1 += 1
        if any(i in target_set for i in order[:3]):
            top3 += 1
        rank = next((pos for pos, i in enumerate(order, start=1) if i in target_set), None)
        if rank is not None:
            reciprocal += 1.0 / rank
        maximum = max(scores)
        exp_scores = [math.exp(min(80.0, s - maximum)) for s in scores]
        normalizer = sum(exp_scores)
        target_prob = sum(exp_scores[i] / normalizer for i in target_set)
        nll_sum += -math.log(max(target_prob, 1e-12))
        count = decision.min_count if decision.min_count else 1
        chosen = order[:count]
        if len(chosen) == len(set(chosen)) and all(0 <= i < n for i in chosen):
            legal += 1
        by_type.setdefault(decision.selection_type, [0, 0])
        by_type[decision.selection_type][0] += int(order[0] in target_set)
        by_type[decision.selection_type][1] += 1
        by_count.setdefault(str(n), [0, 0])
        by_count[str(n)][0] += int(order[0] in target_set)
        by_count[str(n)][1] += 1
    if total == 0:
        raise ValueError(f"split {split!r} has no supervised decisions")
    return {
        "model": "neural-student-v1",
        "split": split,
        "decisions": total,
        "candidate_count": sum(len(list(d.candidate_features)) for d in iter_decisions(dataset_dir, split)),
        "top1": top1 / total,
        "top3": top3 / total,
        "mrr": reciprocal / total,
        "nll": nll_sum / total,
        "legal_action_rate": legal / total,
        "fallback_count": 0,
        "non_finite_count": non_finite,
        "selection_type_top1": {k: v[0] / v[1] for k, v in sorted(by_type.items())},
        "candidate_count_top1": {k: v[0] / v[1] for k, v in sorted(by_count.items())},
    }


def evaluate_linear_baseline(dataset_dir: str | Path, *, split: str = "test", epochs: int = 60) -> dict[str, Any]:
    """Train the existing linear Student on the train split and evaluate it."""
    from mage_ptcg.student.evaluation import evaluate_model
    from mage_ptcg.student.model import train_model

    train_examples = list(iter_examples(dataset_dir, "train"))
    eval_examples = list(iter_examples(dataset_dir, split))
    if not train_examples or not eval_examples:
        raise ValueError("linear baseline needs non-empty train and eval splits")
    model = train_model(train_examples, epochs=epochs)
    metrics = evaluate_model(model, eval_examples)
    return {
        "model": "linear-student-v0",
        "split": split,
        "top1": metrics["teacher_top1_fidelity"],
        "top3": metrics["teacher_top3_fidelity"],
        "nll": metrics["holdout_loss"],
        "legal_action_rate": metrics["legal_action_rate"],
    }


def compare_models(dataset_dir: str | Path, export_document: dict[str, Any], *, split: str = "test") -> dict[str, Any]:
    neural = evaluate_neural(dataset_dir, export_document, split=split)
    linear = evaluate_linear_baseline(dataset_dir, split=split)
    return {
        "split": split,
        "neural_student_v1": neural,
        "linear_student_v0": linear,
        "neural_minus_linear_top1": neural["top1"] - linear["top1"],
    }


# --------------------------------------------------------------------------- #
# Tiny paired screening (fixture harness; win/loss not measured offline)
# --------------------------------------------------------------------------- #


def _screening_observation(your_index: int, turn: int) -> dict[str, object]:
    from mage_ptcg.offline_training.collection import _fixture_options, _observation

    options = _fixture_options(3, (turn + your_index) % 5)
    return _observation(options, your_index=your_index, turn=turn)


def tiny_screening(
    *,
    export_document: dict[str, Any],
    deck: list[int],
    games: int,
    base_seed: int,
    decisions_per_game: int = 4,
) -> dict[str, Any]:
    """Seat-balanced fixture screening measuring legality and fallback only."""
    if games < 2 or games % 2 != 0:
        raise ValueError("screening games must be an even number >= 2")
    from main import make_rule_agent
    from mage_ptcg.offline_training.neural_runtime import NeuralRuntimePolicy

    policy = NeuralRuntimePolicy(export_document)
    fallback = make_rule_agent(deck=deck)
    per_game: list[dict[str, Any]] = []
    total_decisions = 0
    total_legal = 0
    total_fallback = 0
    for game_index in range(games):
        seat = game_index % 2
        decisions = 0
        legal = 0
        fallbacks = 0
        invalid = 0
        for turn in range(decisions_per_game):
            observation = _screening_observation(seat, turn + 1)
            select = observation["select"]
            option_count = len(select["option"])
            choice = policy.choose(observation)
            if choice is None:
                fallbacks += 1
                choice = fallback(observation)
            decisions += 1
            if isinstance(choice, list) and all(0 <= i < option_count for i in choice) and len(choice) == len(set(choice)):
                legal += 1
            else:
                invalid += 1
        per_game.append({
            "game_index": game_index, "seat": seat, "seed": base_seed + game_index,
            "decisions": decisions, "legal_actions": legal, "fallback_count": fallbacks,
            "invalid": invalid, "crash": 0, "timeout": 0,
        })
        total_decisions += decisions
        total_legal += legal
        total_fallback += fallbacks
    verdict = "INSUFFICIENT_EVIDENCE"  # fixture harness cannot measure win rate
    return {
        "schema_version": "offline-training-v1-screening-v1",
        "harness": "fixture",
        "actual_cabt": "ACTUAL_CABT_NOT_RUN",
        "games": games,
        "seat_balance": {"seat0": sum(1 for g in per_game if g["seat"] == 0), "seat1": sum(1 for g in per_game if g["seat"] == 1)},
        "wins": None,
        "losses": None,
        "draws": None,
        "overall_win_rate": None,
        "legal_action_rate": total_legal / total_decisions if total_decisions else 0.0,
        "fallback_count": total_fallback,
        "fallback_rate": total_fallback / total_decisions if total_decisions else 0.0,
        "invalid": sum(g["invalid"] for g in per_game),
        "crash": 0,
        "timeout": 0,
        "per_game": per_game,
        "verdict": verdict,
    }


__all__ = [
    "compare_models",
    "evaluate_linear_baseline",
    "evaluate_neural",
    "tiny_screening",
]
