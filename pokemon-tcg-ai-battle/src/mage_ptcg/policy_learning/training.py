"""Offline AWR pretraining for the recurrent legal-action actor-critic."""
from __future__ import annotations

from collections import Counter
import hashlib
import json
import math
import os
from pathlib import Path
import random
from typing import Any, Iterable

from .algorithms import awr_weights
from .data import PolicyLearningExample, load_examples, vocabulary_hash
from .model import ActorCriticConfig, build_actor_critic
from mage_ptcg.offline_scaleup.progress import ProgressReporter


SCHEMA = "policy-learning-offline-awr-v2"


class TrainingError(ValueError):
    pass


def _torch() -> tuple[Any, Any, Any, Any]:
    try:
        import torch
        import torch.nn.functional as functional
        from torch.utils.data import DataLoader, Dataset
    except ImportError as exc:  # pragma: no cover - environment guard
        raise TrainingError("PyTorch is required for policy learning") from exc
    return torch, functional, DataLoader, Dataset


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(_canonical(value) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rng_state(torch: Any, generator: Any) -> dict[str, Any]:
    """Capture every RNG that affects the next local training update."""
    return {
        "python": random.getstate(),
        "torch_cpu": torch.get_rng_state(),
        "torch_cuda": torch.cuda.get_rng_state_all() if torch.cuda.is_available() else [],
        "loader_generator": generator.get_state(),
    }


def _restore_rng_state(torch: Any, generator: Any, state: object) -> None:
    if not isinstance(state, dict):
        raise TrainingError("checkpoint RNG state is missing")
    try:
        random.setstate(state["python"])
        torch.set_rng_state(state["torch_cpu"])
        if torch.cuda.is_available() and state["torch_cuda"]:
            torch.cuda.set_rng_state_all(state["torch_cuda"])
        generator.set_state(state["loader_generator"])
    except (KeyError, TypeError, ValueError, RuntimeError) as exc:
        raise TrainingError("checkpoint RNG state is invalid") from exc


def family_vocabulary(values: Iterable[PolicyLearningExample]) -> dict[str, int]:
    return {name: index for index, name in enumerate(sorted({item.family_target for item in values}))}


def collate(values: list[PolicyLearningExample], families: dict[str, int]) -> dict[str, Any]:
    """Pad a batch of decisions into model inputs.

    The padded blocks are filled in NumPy and wrapped once.  Building a fresh
    ``torch.tensor`` per row walked the nested tuples through the Python
    binding for every example, and this runs on the training and PPO critical
    paths; the resulting tensors are identical.
    """
    import numpy
    torch, _functional, _loader, _dataset = _torch()
    if not values:
        raise TrainingError("cannot collate an empty batch")
    actions_max = max(len(item.actions) for item in values)
    history_max = max(len(item.history) for item in values)
    action_dim = len(values[0].actions[0]); history_dim = len(values[0].history[0])
    history = numpy.zeros((len(values), history_max, history_dim), dtype=numpy.float32)
    history_lengths = numpy.zeros(len(values), dtype=numpy.int64)
    actions = numpy.zeros((len(values), actions_max, action_dim), dtype=numpy.float32)
    action_mask = numpy.zeros((len(values), actions_max), dtype=bool)
    rule_proposal_mask = numpy.zeros((len(values), actions_max), dtype=bool)
    for index, item in enumerate(values):
        history[index, :len(item.history)] = item.history
        history_lengths[index] = len(item.history)
        actions[index, :len(item.actions)] = item.actions
        action_mask[index, :len(item.actions)] = True
        if item.rule_proposal_index is not None:
            rule_proposal_mask[index, item.rule_proposal_index] = True
    return {
        "state": torch.from_numpy(numpy.asarray([item.state for item in values], dtype=numpy.float32)),
        "history": torch.from_numpy(history), "history_lengths": torch.from_numpy(history_lengths),
        "actions": torch.from_numpy(actions), "action_mask": torch.from_numpy(action_mask),
        "target": torch.from_numpy(numpy.asarray([item.target_index for item in values], dtype=numpy.int64)),
        "rule_proposal_mask": torch.from_numpy(rule_proposal_mask),
        "returns": torch.from_numpy(numpy.asarray([item.terminal_return for item in values], dtype=numpy.float32)),
        "family": torch.from_numpy(numpy.asarray([families[item.family_target] for item in values], dtype=numpy.int64)),
        "trust": [item.teacher_trust for item in values], "examples": values,
    }


def _device(requested: str) -> Any:
    torch, _functional, _loader, _dataset = _torch()
    if requested.startswith("cuda"):
        if not torch.cuda.is_available():
            raise TrainingError("CUDA requested but unavailable")
        return torch.device(requested)
    return torch.device("cpu")


def _loss(model: Any, batch: dict[str, Any], *, device: Any, objective: str, awr_beta: float, value_weight: float, family_weight: float, trust_weighting: bool) -> tuple[Any, dict[str, Any]]:
    torch, functional, _loader, _dataset = _torch()
    tensors = {key: value.to(device) for key, value in batch.items() if hasattr(value, "to")}
    output = model(tensors["state"], tensors["history"], tensors["history_lengths"], tensors["actions"], tensors["action_mask"], tensors["rule_proposal_mask"])
    per_policy = functional.cross_entropy(output["policy_logits"], tensors["target"], reduction="none")
    if objective == "awr":
        advantages = tensors["returns"] - output["value"].detach()
        weights = awr_weights(advantages, beta=awr_beta)
    elif objective == "bc":
        weights = torch.ones_like(per_policy)
    else:
        raise TrainingError("offline objective must be 'bc' or 'awr'")
    if trust_weighting:
        trusted = torch.tensor([1.0 if value == "TRUSTED" else 0.5 if value == "LIMITED" else 0.0 for value in batch["trust"]], device=device)
        if not bool((trusted.sum() > 0).item()):
            raise TrainingError("batch has no trusted training records")
        weights = weights * trusted
        weights = weights / weights.mean().clamp_min(torch.finfo(weights.dtype).eps)
    policy = (per_policy * weights).mean()
    value = functional.smooth_l1_loss(output["value"], tensors["returns"])
    family = functional.cross_entropy(output["family_logits"], tensors["family"])
    total = policy + value_weight * value + family_weight * family
    if not bool(torch.isfinite(total).item()):
        raise TrainingError("non-finite actor-critic loss")
    return total, {"total": float(total.detach()), "policy": float(policy.detach()), "value": float(value.detach()), "family": float(family.detach())}


def evaluate(model: Any, values: list[PolicyLearningExample], *, families: dict[str, int], device: Any, batch_size: int) -> dict[str, Any]:
    torch, functional, DataLoader, Dataset = _torch()
    class Values(Dataset):
        def __len__(self): return len(values)
        def __getitem__(self, index): return values[index]
    loader = DataLoader(Values(), batch_size=batch_size, shuffle=False, collate_fn=lambda rows: collate(rows, families))
    total = top1 = 0; policy_loss = value_loss = family_correct = brier_sum = 0.0
    non_forced_total = non_forced_correct = 0
    action_type_counts: Counter[str] = Counter()
    action_type_correct: Counter[str] = Counter()
    calibration: dict[str, list[float]] = {str(index): [0.0, 0.0, 0.0] for index in range(10)}
    model.eval()
    with torch.no_grad():
        for batch in loader:
            tensors = {key: value.to(device) for key, value in batch.items() if hasattr(value, "to")}
            output = model(tensors["state"], tensors["history"], tensors["history_lengths"], tensors["actions"], tensors["action_mask"], tensors["rule_proposal_mask"])
            policy_loss += float(functional.cross_entropy(output["policy_logits"], tensors["target"], reduction="sum"))
            value_loss += float(functional.smooth_l1_loss(output["value"], tensors["returns"], reduction="sum"))
            predicted = output["policy_logits"].argmax(dim=1)
            correct = predicted == tensors["target"]
            probabilities = torch.softmax(output["policy_logits"], dim=1)
            target_probability = probabilities.gather(1, tensors["target"].unsqueeze(1)).squeeze(1)
            brier_sum += float((probabilities.square().sum(dim=1) - 2.0 * target_probability + 1.0).sum())
            top1 += int(correct.sum())
            family_correct += int((output["family_logits"].argmax(dim=1) == tensors["family"]).sum())
            total += len(batch["examples"])
            for item, is_correct in zip(batch["examples"], correct.tolist()):
                if len(item.actions) > 1:
                    non_forced_total += 1
                    non_forced_correct += int(is_correct)
                    action_type_counts[item.action_type] += 1
                    action_type_correct[item.action_type] += int(is_correct)
            for confidence, is_correct in zip(probabilities.max(dim=1).values.tolist(), correct.tolist()):
                bucket = min(9, int(float(confidence) * 10))
                calibration[str(bucket)][0] += 1.0
                calibration[str(bucket)][1] += float(confidence)
                calibration[str(bucket)][2] += float(is_correct)
    if not total:
        raise TrainingError("evaluation has no examples")
    returns = [item.terminal_return for item in values]
    # Forced prompts have exactly one legal choice and would artificially
    # inflate imitation fidelity.  Retain them for safety accounting but
    # report the decision-quality metric separately.
    forced = total - non_forced_total
    type_metrics = {action_type: action_type_correct[action_type] / count for action_type, count in sorted(action_type_counts.items())}
    calibration_bins = {
        bucket: {"count": int(totals[0]), "mean_confidence": totals[1] / totals[0], "accuracy": totals[2] / totals[0]}
        for bucket, totals in calibration.items() if totals[0]
    }
    return {"examples": total, "teacher_top1_fidelity": top1 / total, "policy_nll": policy_loss / total,
            "value_huber": value_loss / total, "family_top1": family_correct / total,
            "outcome_distribution": dict(sorted(Counter(returns).items())), "legal_action_rate": 1.0,
            "forced_action_examples": forced, "non_forced_examples": non_forced_total,
            "forced_excluded_top1": non_forced_correct / non_forced_total if non_forced_total else None,
            "action_type_top1": type_metrics,
            "action_type_macro_top1": (sum(type_metrics.values()) / len(type_metrics)) if type_metrics else None,
            "rule_proposal_coverage": sum(item.rule_proposal_index is not None for item in values) / total,
            "policy_brier_score": brier_sum / total, "confidence_calibration": calibration_bins}


def _selection_metric(validation: dict[str, Any], *, objective: str, value_weight: float) -> float:
    """Score one epoch for best-checkpoint selection, per training objective.

    A policy-only run does not train the value head, so subtracting its Huber
    loss would let an untrained, essentially constant term decide which epoch
    is "best".  Only include the value term when the value head is actually
    being optimized.  Forced prompts have a single legal option and cannot
    discriminate between checkpoints, so prefer the forced-excluded top-1
    when it exists.
    """
    top1 = validation.get("forced_excluded_top1")
    if top1 is None:
        top1 = validation["teacher_top1_fidelity"]
    if value_weight <= 0:
        return float(top1)
    return float(top1) - float(validation["value_huber"])


def train_offline(
    *, dataset: Path, output_dir: Path, device_name: str = "cpu", epochs: int = 20, batch_size: int = 256,
    workers: int = 0, config: ActorCriticConfig = ActorCriticConfig(), learning_rate: float = 3e-4,
    objective: str = "awr", awr_beta: float = 1.0, value_weight: float = 0.5, family_weight: float = 0.1,
    trust_weighting: bool = True, seed: int = 71000, resume: bool = False, progress: bool | None = None,
    progress_interval_seconds: float | None = None, initialize_from: Path | None = None,
) -> dict[str, Any]:
    if (epochs < 1 or batch_size < 1 or workers < 0 or learning_rate <= 0 or awr_beta <= 0 or value_weight < 0 or family_weight < 0
            or objective not in {"bc", "awr"}):
        raise TrainingError("training hyperparameters are invalid")
    torch, _functional, DataLoader, Dataset = _torch(); device = _device(device_name); config.validate()
    random.seed(seed); torch.manual_seed(seed)
    if device.type == "cuda": torch.cuda.manual_seed_all(seed)
    dataset_digest = _sha256(dataset)
    vocabulary_digest = vocabulary_hash()
    train_values = load_examples(dataset, splits=("train",))
    validation_values = load_examples(dataset, splits=("validation",))
    if config.use_rule_proposal and any(value.rule_proposal_index is None for value in [*train_values, *validation_values]):
        raise TrainingError("rule proposal model requires a recorded legal Rule v0 proposal for every train/validation decision")
    families = family_vocabulary([*train_values, *validation_values])
    if len(families) != config.family_classes:
        config = ActorCriticConfig(hidden_size=config.hidden_size, recurrent_size=config.recurrent_size, blocks=config.blocks,
                                   dropout=config.dropout, family_classes=len(families), use_recurrence=config.use_recurrence,
                                   use_rule_proposal=config.use_rule_proposal, architecture_version=config.architecture_version)
    class Values(Dataset):
        def __len__(self): return len(train_values)
        def __getitem__(self, index): return train_values[index]
    generator = torch.Generator(); generator.manual_seed(seed)
    loader = DataLoader(Values(), batch_size=batch_size, shuffle=True, num_workers=workers, persistent_workers=workers > 0,
                        collate_fn=lambda rows: collate(rows, families), generator=generator)
    if resume and initialize_from is not None:
        raise TrainingError("resume and initialize_from are mutually exclusive")
    initialization: dict[str, Any] | None = None
    output_dir.mkdir(parents=True, exist_ok=True)
    model = build_actor_critic(config).to(device); optimizer = torch.optim.AdamW(model.parameters(), lr=learning_rate)
    scheduler = torch.optim.lr_scheduler.LambdaLR(optimizer, lr_lambda=lambda _epoch: 1.0)
    if initialize_from is not None:
        source_model, source_summary, source_families = load_model(initialize_from, device_name=device_name)
        if source_summary.get("config") != config.to_dict() or source_families != families:
            raise TrainingError("initialization model does not match the dataset/model contract")
        model.load_state_dict(source_model.state_dict())
        initialization = {"source_model_dir": str(initialize_from), "source_checkpoint_sha256": _sha256(initialize_from / "best.pt"),
                          "source_schema": source_summary.get("schema")}
    last, best = output_dir / "last.pt", output_dir / "best.pt"
    start_epoch = 0; best_metric = float("-inf"); resumed = False
    if resume and last.exists():
        checkpoint = torch.load(last, map_location=device, weights_only=False)
        if (checkpoint.get("schema") != SCHEMA or checkpoint.get("config") != config.to_dict()
                or checkpoint.get("families") != families or checkpoint.get("dataset_sha256") != dataset_digest
                or checkpoint.get("vocabulary_hash") != vocabulary_digest):
            raise TrainingError("checkpoint does not match the dataset/model contract")
        model.load_state_dict(checkpoint["model"]); optimizer.load_state_dict(checkpoint["optimizer"])
        try:
            scheduler.load_state_dict(checkpoint["scheduler"])
        except (KeyError, TypeError, ValueError) as exc:
            raise TrainingError("checkpoint scheduler state is missing or invalid") from exc
        _restore_rng_state(torch, generator, checkpoint.get("rng_state"))
        start_epoch = int(checkpoint["epoch"]) + 1; best_metric = float(checkpoint["best_metric"]); resumed = True
    metrics: list[dict[str, Any]] = []
    reporter = ProgressReporter(phase=f"train-{objective}", total=epochs, initial=start_epoch, run_id=output_dir.name,
                                unit="epoch", progress=progress, interval_seconds=progress_interval_seconds)
    try:
        for epoch in range(start_epoch, epochs):
            model.train(); losses = []
            for batch in loader:
                optimizer.zero_grad(set_to_none=True)
                loss, details = _loss(model, batch, device=device, objective=objective, awr_beta=awr_beta, value_weight=value_weight, family_weight=family_weight, trust_weighting=trust_weighting)
                loss.backward(); torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0); optimizer.step(); losses.append(details)
            validation = evaluate(model, validation_values, families=families, device=device, batch_size=batch_size)
            metric = _selection_metric(validation, objective=objective, value_weight=value_weight)
            scheduler.step()
            checkpoint = {"schema": SCHEMA, "epoch": epoch, "model": model.state_dict(), "optimizer": optimizer.state_dict(),
                          "scheduler": scheduler.state_dict(), "rng_state": _rng_state(torch, generator),
                          "dataset_sha256": dataset_digest, "vocabulary_hash": vocabulary_digest,
                          "best_metric": max(best_metric, metric), "config": config.to_dict(), "families": families}
            torch.save(checkpoint, last)
            if metric >= best_metric:
                best_metric = metric; torch.save(checkpoint, best)
            train_loss = {key: sum(item[key] for item in losses) / len(losses) for key in ("total", "policy", "value", "family")}
            metrics.append({"epoch": epoch, "train_loss": train_loss, "validation": validation})
            reporter.update(1, loss=round(train_loss["total"], 5), top1=round(validation["teacher_top1_fidelity"], 4))
    finally:
        reporter.close()
    summary = {"schema": SCHEMA, "dataset": str(dataset), "dataset_sha256": dataset_digest, "vocabulary_hash": vocabulary_digest, "config": config.to_dict(), "families": families,
               "device": str(device), "epochs_completed": len(metrics) + start_epoch, "resumed": resumed,
               "objective": objective, "awr": {"beta": awr_beta, "value_weight": value_weight, "family_weight": family_weight, "trust_weighting": trust_weighting},
               "selection_metric": "forced_excluded_top1" if value_weight <= 0 else "forced_excluded_top1_minus_value_huber",
               "best_checkpoint_sha256": _sha256(best), "best_metric": best_metric, "metrics": metrics,
               "initialization": initialization}
    _atomic_json(output_dir / "training_summary.json", summary)
    return summary


def load_model(model_dir: Path, *, device_name: str = "cpu") -> tuple[Any, dict[str, Any], dict[str, int]]:
    torch, _functional, _loader, _dataset = _torch(); device = _device(device_name)
    summary = json.loads((model_dir / "training_summary.json").read_text(encoding="utf-8"))
    if summary.get("schema") not in {"policy-learning-offline-awr-v1", SCHEMA, "policy-learning-ppo-pilot-v1"}:
        raise TrainingError("model artifact schema is unsupported")
    config = ActorCriticConfig(**summary["config"]); families = summary["families"]
    checkpoint = torch.load(model_dir / "best.pt", map_location=device, weights_only=False)
    expected_config = config.to_dict()
    if summary.get("schema") == "policy-learning-offline-awr-v1":
        expected_config.pop("use_recurrence", None); expected_config.pop("use_rule_proposal", None)
    if (checkpoint.get("config") != expected_config or checkpoint.get("families") != families
            or checkpoint.get("vocabulary_hash") != summary.get("vocabulary_hash")
            or checkpoint.get("dataset_sha256") != summary.get("dataset_sha256")):
        raise TrainingError("model artifact is inconsistent")
    model = build_actor_critic(config).to(device); model.load_state_dict(checkpoint["model"]); model.eval()
    return model, summary, families
