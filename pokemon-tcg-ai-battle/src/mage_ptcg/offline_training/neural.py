"""Candidate-wise neural Student: PyTorch training with checkpoint/resume.

The model scores each legal candidate independently from its shared
96-dimensional Stable-ActionKey feature vector, then a masked softmax over the
legal set is supervised by the teacher-selected candidate.  Illegal candidates
never enter the loss, normalization, metrics, or the argmax.

PyTorch is imported lazily so this module is importable without it; the train
and evaluate entry points raise a clear error if torch is missing.  Checkpoints
persist model/optimizer/epoch/step/best/early-stop/config/hashes/RNG state and a
content checksum, and reload through torch's weights-only path.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import os
import random
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from mage_ptcg.offline_training.dataset import Decision, iter_decisions, load_manifest


MODEL_PURPOSE_ACTUAL = "NEURAL_ACTUAL_TRAINED"
MODEL_PURPOSE_SMOKE = "NEURAL_FIXTURE_SMOKE"
NEURAL_ARCH_VERSION = "offline-training-v1-neural-mlp-v1"
CHECKPOINT_SCHEMA = "offline-training-v1-checkpoint-v1"


class NeuralError(RuntimeError):
    """Raised when neural training cannot proceed safely."""


class CheckpointValidationError(NeuralError):
    """A checkpoint is missing, unreadable, or incompatible.

    ``reason`` is a stable machine-readable code:
    ``metadata_missing`` / ``metadata_deserialization_failed`` /
    ``checksum_mismatch`` / ``dataset_hash_mismatch`` /
    ``feature_hash_mismatch`` / ``schema_mismatch`` /
    ``config_mismatch`` / ``tensor_file_missing`` /
    ``tensor_deserialization_failed`` / ``tensor_payload_invalid`` /
    ``state_shape_mismatch``.  Messages never contain paths or payloads.
    """

    def __init__(self, message: str, *, reason: str):
        super().__init__(message)
        self.reason = reason


class _InjectedOOM(RuntimeError):
    """Test hook: a simulated out-of-memory event to exercise batch reduction."""


def _require_torch():
    try:
        import torch  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        raise NeuralError("PyTorch is required for neural training but is unavailable") from exc
    import torch

    return torch


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ModelSpec:
    input_dim: int
    hidden_dims: tuple[int, ...]
    activation: str = "relu"

    def to_dict(self) -> dict[str, Any]:
        return {"input_dim": self.input_dim, "hidden_dims": list(self.hidden_dims), "activation": self.activation}


def build_module(spec: ModelSpec):
    torch = _require_torch()
    import torch.nn as nn

    if spec.activation != "relu":
        raise NeuralError("only relu activation is supported")
    layers: list[nn.Module] = []
    previous = spec.input_dim
    for width in spec.hidden_dims:
        layers.append(nn.Linear(previous, width))
        layers.append(nn.ReLU())
        previous = width
    layers.append(nn.Linear(previous, 1))
    return nn.Sequential(*layers)


def _pad_batch(decisions: Sequence[Decision], mean: list[float], std: list[float], torch, device):
    """Return normalized padded features, legal mask, and target distribution."""
    batch = len(decisions)
    max_candidates = max(len(d.candidate_features) for d in decisions)
    dim = len(mean)
    features = torch.zeros((batch, max_candidates, dim), dtype=torch.float32)
    mask = torch.zeros((batch, max_candidates), dtype=torch.bool)
    target = torch.zeros((batch, max_candidates), dtype=torch.float32)
    mean_t = torch.tensor(mean, dtype=torch.float32)
    std_t = torch.tensor(std, dtype=torch.float32)
    for row, decision in enumerate(decisions):
        candidates = decision.candidate_features
        for col, vector in enumerate(candidates):
            features[row, col] = (torch.tensor(vector, dtype=torch.float32) - mean_t) / std_t
            mask[row, col] = True
        targets = decision.target_indices
        if targets:
            share = 1.0 / len(targets)
            for index in targets:
                target[row, index] = share
    return features.to(device), mask.to(device), target.to(device)


def _masked_log_softmax(scores, mask, torch):
    # Accumulate in fp32 even when the forward pass ran under bf16 autocast, and
    # fill *after* the fp32 cast so the sentinel is always representable.
    scores_fp32 = scores.float()
    neg_inf = torch.finfo(torch.float32).min
    # Safe fallback if a row has all False masks: temporarily make the first candidate True
    # to avoid NaN generation in log_softmax. The row will be excluded from loss anyway.
    any_legal = mask.sum(dim=1, keepdim=True) > 0
    safe_mask = torch.where(any_legal, mask, torch.cat([torch.ones_like(mask[:, :1]), torch.zeros_like(mask[:, 1:])], dim=1))
    masked = scores_fp32.masked_fill(~safe_mask, neg_inf)
    return torch.log_softmax(masked, dim=1)


def _forward_loss(module, decisions, mean, std, torch, device, use_bf16: bool):
    features, mask, target = _pad_batch(decisions, mean, std, torch, device)
    autocast = torch.autocast(device_type="cuda", dtype=torch.bfloat16) if use_bf16 else _nullcontext()
    with autocast:
        scores = module(features).squeeze(-1)
    log_probs = _masked_log_softmax(scores, mask, torch)  # fp32 accumulation
    has_target = (target.sum(dim=1) > 0) & (mask.sum(dim=1) > 0)
    if not bool(has_target.any()):
        return None
    per_decision = -(target * log_probs).sum(dim=1)
    loss = per_decision[has_target].mean()
    return loss


class _nullcontext:
    def __enter__(self):
        return None

    def __exit__(self, *exc):
        return False


def _supervised_count(decisions: Sequence[Decision]) -> int:
    """Decisions that contribute to the loss (mirrors _forward_loss's has_target)."""
    return sum(1 for d in decisions if d.target_indices and d.candidate_features)


def _train_batch_once(module, optimizer, batch, *, microbatch, mean, std, torch, device, use_bf16, grad_clip, oom_hook=None, step=0):
    """One optimizer step over ``batch`` split into ``microbatch``-sized micros.

    Each micro's mean loss is weighted by its supervised-decision count so the
    accumulated gradient equals the full-batch mean regardless of the split
    (unequal tail micros included).  Raises on OOM; the caller owns retries.
    """
    optimizer.zero_grad(set_to_none=True)
    micros = [batch[i : i + microbatch] for i in range(0, len(batch), microbatch)]
    total_supervised = _supervised_count(batch)
    accumulated = 0.0
    counted = 0
    for micro in micros:
        if oom_hook is not None and oom_hook(step):
            raise _InjectedOOM("injected oom")
        loss = _forward_loss(module, micro, mean, std, torch, device, use_bf16)
        if loss is None:
            continue
        micro_supervised = _supervised_count(micro)
        scaled = loss * (micro_supervised / total_supervised)
        scaled.backward()
        accumulated += float(loss.detach()) * micro_supervised
        counted += micro_supervised
    torch.nn.utils.clip_grad_norm_(module.parameters(), grad_clip)
    optimizer.step()
    return accumulated / counted if counted else 0.0


def _load_decisions(dataset_dir: str | Path, split: str) -> list[Decision]:
    from mage_ptcg.offline_training.dataset import load_feature_cache, save_feature_cache
    decisions = load_feature_cache(dataset_dir, split)
    if decisions is not None:
        return decisions
    decisions = list(iter_decisions(dataset_dir, split))
    save_feature_cache(dataset_dir, split, decisions)
    return decisions


def evaluate_module(module, decisions: Sequence[Decision], mean, std, *, torch=None, device=None, use_bf16: bool = False) -> dict[str, Any]:
    """Compute top-1/top-3/MRR/NLL and breakdowns over legal candidates only."""
    torch = torch or _require_torch()
    device = device if device is not None else torch.device("cpu")
    module.eval()
    total = 0
    top1 = 0
    top3 = 0
    reciprocal = 0.0
    nll_sum = 0.0
    non_finite = 0
    by_type: dict[str, list[int]] = {}
    by_count: dict[str, list[int]] = {}
    with torch.no_grad():
        for decision in decisions:
            if not decision.target_indices or not decision.candidate_features:
                continue
            features, mask, target = _pad_batch([decision], mean, std, torch, device)
            autocast = torch.autocast(device_type="cuda", dtype=torch.bfloat16) if use_bf16 else _nullcontext()
            with autocast:
                scores = module(features).squeeze(-1)
            log_probs = _masked_log_softmax(scores, mask, torch)[0]
            values = log_probs.tolist()
            n = len(decision.candidate_features)
            legal = list(range(n))
            if any(not math.isfinite(values[i]) for i in legal):
                non_finite += 1
                continue
            order = sorted(legal, key=lambda i: (-values[i], decision.candidate_digests[i], i))
            target_set = set(decision.target_indices)
            total += 1
            if order[0] in target_set:
                top1 += 1
            if any(i in target_set for i in order[:3]):
                top3 += 1
            rank = next((pos for pos, i in enumerate(order, start=1) if i in target_set), None)
            if rank is not None:
                reciprocal += 1.0 / rank
            target_prob = sum(math.exp(values[i]) for i in target_set)
            nll_sum += -math.log(max(target_prob, 1e-12))
            tkey = decision.selection_type
            by_type.setdefault(tkey, [0, 0])
            by_type[tkey][0] += int(order[0] in target_set)
            by_type[tkey][1] += 1
            ckey = str(n)
            by_count.setdefault(ckey, [0, 0])
            by_count[ckey][0] += int(order[0] in target_set)
            by_count[ckey][1] += 1
    if total == 0:
        raise NeuralError("evaluation set has no supervised decisions")
    return {
        "decisions": total,
        "candidate_count": sum(len(d.candidate_features) for d in decisions),
        "top1": top1 / total,
        "top3": top3 / total,
        "mrr": reciprocal / total,
        "nll": nll_sum / total,
        "non_finite_count": non_finite,
        "fallback_count": 0,
        "selection_type_top1": {k: v[0] / v[1] for k, v in sorted(by_type.items())},
        "candidate_count_top1": {k: v[0] / v[1] for k, v in sorted(by_count.items())},
    }


def _rng_state(torch) -> dict[str, Any]:
    import numpy as np

    np_state = np.random.get_state()
    return {
        "python": list(random.getstate()[1]),
        "python_pos": random.getstate()[2],
        "numpy_keys": np_state[1].tolist(),
        "numpy_pos": int(np_state[2]),
        "numpy_has_gauss": int(np_state[3]),
        "numpy_cached_gauss": float(np_state[4]),
        "torch_cpu": torch.get_rng_state().tolist(),
        "torch_cuda": torch.cuda.get_rng_state_all() and [state.tolist() for state in torch.cuda.get_rng_state_all()] if torch.cuda.is_available() else [],
    }


def _save_checkpoint(
    path: Path,
    *,
    module,
    optimizer,
    epoch: int,
    global_step: int,
    best_metric: float,
    patience_left: int,
    spec: ModelSpec,
    normalization: dict[str, Any],
    dataset_hash: str,
    feature_schema_hash: str,
    train_split_hash: str,
    model_purpose: str,
    effective_batch: int,
    microbatch: int,
    accumulation: int,
    torch,
) -> str:
    tensors = {
        "model_state": module.state_dict(),
        "optimizer_state": optimizer.state_dict(),
    }
    tmp_pt = path.with_suffix(".pt.tmp")
    torch.save(tensors, tmp_pt)
    pt_bytes = tmp_pt.read_bytes()
    metadata = {
        "schema": CHECKPOINT_SCHEMA,
        "epoch": epoch,
        "global_step": global_step,
        "best_metric": best_metric,
        "patience_left": patience_left,
        "model_spec": spec.to_dict(),
        "normalization": normalization,
        "dataset_hash": dataset_hash,
        "feature_schema_hash": feature_schema_hash,
        "train_split_hash": train_split_hash,
        "model_purpose": model_purpose,
        "effective_batch": effective_batch,
        "microbatch": microbatch,
        "accumulation": accumulation,
        "rng_state": _rng_state(torch),
        "state_sha256": hashlib.sha256(pt_bytes).hexdigest(),
    }
    metadata["checksum"] = _digest({k: v for k, v in metadata.items() if k != "checksum"})
    os.replace(tmp_pt, path.with_suffix(".pt"))
    path.with_suffix(".json").write_text(_canonical_json(metadata) + "\n", encoding="utf-8")
    return metadata["checksum"]


def load_checkpoint_metadata(path: Path) -> dict[str, Any]:
    meta_path = path.with_suffix(".json")
    if not meta_path.is_file():
        raise CheckpointValidationError("checkpoint metadata file missing", reason="metadata_missing")
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise CheckpointValidationError(
            "checkpoint metadata deserialization failed", reason="metadata_deserialization_failed"
        ) from exc
    if not isinstance(meta, dict):
        raise CheckpointValidationError(
            "checkpoint metadata is not an object", reason="metadata_deserialization_failed"
        )
    recomputed = _digest({k: v for k, v in meta.items() if k != "checksum"})
    if recomputed != meta.get("checksum"):
        raise CheckpointValidationError("checkpoint metadata checksum mismatch", reason="checksum_mismatch")
    return meta


def assert_checkpoint_compatible(meta: dict[str, Any], *, dataset_hash: str, feature_schema_hash: str, spec: ModelSpec, model_purpose: str) -> None:
    if meta.get("dataset_hash") != dataset_hash:
        raise CheckpointValidationError("checkpoint dataset hash is incompatible", reason="dataset_hash_mismatch")
    if meta.get("feature_schema_hash") != feature_schema_hash:
        raise CheckpointValidationError("checkpoint feature schema is incompatible", reason="feature_hash_mismatch")
    if meta.get("model_spec") != spec.to_dict():
        raise CheckpointValidationError("checkpoint architecture is incompatible", reason="schema_mismatch")
    if meta.get("model_purpose") != model_purpose:
        raise CheckpointValidationError("checkpoint model purpose is incompatible", reason="config_mismatch")


def _load_checkpoint_tensors(path: Path, torch, device) -> dict[str, Any]:
    """Load the tensor payload of a checkpoint, fail-closed with typed errors."""
    pt_path = path.with_suffix(".pt")
    if not pt_path.is_file():
        raise CheckpointValidationError("checkpoint tensor file missing", reason="tensor_file_missing")
    try:
        tensors = torch.load(pt_path, map_location=device, weights_only=True)
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:  # torch raises library-specific untyped errors here
        raise CheckpointValidationError(
            "checkpoint tensor deserialization failed", reason="tensor_deserialization_failed"
        ) from exc
    if not isinstance(tensors, dict) or "model_state" not in tensors or "optimizer_state" not in tensors:
        raise CheckpointValidationError(
            "checkpoint tensor payload is missing model or optimizer state", reason="tensor_payload_invalid"
        )
    return tensors


def _restore_state(module, optimizer, path: Path, torch, device) -> None:
    tensors = _load_checkpoint_tensors(path, torch, device)
    try:
        module.load_state_dict(tensors["model_state"])
        optimizer.load_state_dict(tensors["optimizer_state"])
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:  # shape/schema mismatches surface as untyped errors
        raise CheckpointValidationError(
            "checkpoint state does not match the module or optimizer shape", reason="state_shape_mismatch"
        ) from exc


def _split_hash(decisions: Sequence[Decision]) -> str:
    return _digest(sorted(d.example_id for d in decisions))


def train(
    *,
    dataset_dir: str | Path,
    checkpoint_dir: str | Path,
    hidden_dims: Sequence[int],
    epochs: int,
    learning_rate: float,
    weight_decay: float,
    grad_clip: float,
    patience: int,
    seed: int,
    max_batch_decisions: int,
    model_purpose: str,
    device: str = "auto",
    metrics_path: str | Path | None = None,
    resume: bool = False,
    oom_hook: Callable[[int], bool] | None = None,
) -> dict[str, Any]:
    """Train the candidate-wise Student with checkpoint/resume and OOM recovery."""
    torch = _require_torch()
    manifest = load_manifest(dataset_dir)
    normalization = manifest["normalization"]
    mean = list(normalization["mean"])
    std = list(normalization["std"])
    input_dim = int(manifest["feature_dimension"])
    dataset_hash = manifest["dataset_hash"]
    feature_schema_hash = manifest["feature_schema_hash"]
    spec = ModelSpec(input_dim=input_dim, hidden_dims=tuple(int(v) for v in hidden_dims))

    if device == "auto":
        device = "cuda" if torch.cuda.is_available() else "cpu"
    if device == "cuda" and not torch.cuda.is_available():
        raise NeuralError("cuda device requested but not available")
    torch_device = torch.device(device)
    use_bf16 = device == "cuda" and torch.cuda.is_bf16_supported()

    random.seed(seed)
    torch.manual_seed(seed)
    try:
        import numpy as np

        np.random.seed(seed & 0xFFFFFFFF)
    except Exception:  # noqa: BLE001
        pass

    train_decisions = _load_decisions(dataset_dir, "train")
    val_decisions = _load_decisions(dataset_dir, "validation")
    if not train_decisions or not val_decisions:
        raise NeuralError("train and validation splits must be non-empty")
    train_split_hash = _split_hash(train_decisions)

    module = build_module(spec).to(torch_device)
    optimizer = torch.optim.AdamW(module.parameters(), lr=learning_rate, weight_decay=weight_decay)

    checkpoint_dir = Path(checkpoint_dir)
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    best_path = checkpoint_dir / "best"
    last_path = checkpoint_dir / "last"
    metrics_path = Path(metrics_path) if metrics_path else checkpoint_dir / "metrics.jsonl"

    start_epoch = 0
    best_metric = math.inf
    patience_left = patience
    if resume and last_path.with_suffix(".json").exists():
        meta = load_checkpoint_metadata(last_path)
        assert_checkpoint_compatible(meta, dataset_hash=dataset_hash, feature_schema_hash=feature_schema_hash, spec=spec, model_purpose=model_purpose)
        _restore_state(module, optimizer, last_path, torch, torch_device)
        start_epoch = int(meta["epoch"]) + 1
        best_metric = float(meta["best_metric"])
        patience_left = int(meta["patience_left"])

    # Batch calibration: start from the requested cap and reduce on OOM.
    effective_batch = min(max_batch_decisions, len(train_decisions))
    microbatch = effective_batch

    def train_one_batch(batch: list[Decision], step: int) -> float:
        nonlocal microbatch
        while True:
            try:
                return _train_batch_once(
                    module, optimizer, batch, microbatch=microbatch, mean=mean, std=std,
                    torch=torch, device=torch_device, use_bf16=use_bf16, grad_clip=grad_clip,
                    oom_hook=oom_hook, step=step,
                )
            except (_InjectedOOM, RuntimeError) as exc:
                if isinstance(exc, RuntimeError) and "out of memory" not in str(exc).lower() and not isinstance(exc, _InjectedOOM):
                    raise
                if device == "cuda":
                    torch.cuda.empty_cache()
                if microbatch <= 1:
                    raise NeuralError("out-of-memory persisted at microbatch=1") from exc
                microbatch = max(1, microbatch // 2)

    metrics_records: list[dict[str, Any]] = []
    resolved = {
        "device": device, "use_bf16": use_bf16, "effective_batch": effective_batch,
        "seed": seed, "epochs": epochs, "learning_rate": learning_rate,
    }
    global_step = start_epoch * max(1, math.ceil(len(train_decisions) / max(1, effective_batch)))
    stopped_early = False
    last_completed_epoch = start_epoch - 1
    for epoch in range(start_epoch, epochs):
        if patience_left <= 0:
            # A resumed checkpoint already reached its early-stop decision; a
            # continuous run would not have trained past this point either.
            stopped_early = True
            break
        module.train()
        indices = list(range(len(train_decisions)))
        rng = random.Random(seed + epoch)
        rng.shuffle(indices)
        shuffled = [train_decisions[i] for i in indices]
        epoch_loss = 0.0
        batches = 0
        for start in range(0, len(shuffled), effective_batch):
            batch = shuffled[start : start + effective_batch]
            epoch_loss += train_one_batch(batch, global_step)
            batches += 1
            global_step += 1
        val_metrics = evaluate_module(module, val_decisions, mean, std, torch=torch, device=torch_device, use_bf16=use_bf16)
        val_nll = float(val_metrics["nll"])
        record = {"epoch": epoch, "train_loss": epoch_loss / max(1, batches), "val_nll": val_nll, "val_top1": val_metrics["top1"], "microbatch": microbatch}
        metrics_records.append(record)
        accumulation = max(1, math.ceil(effective_batch / max(1, microbatch)))
        # Order matters for resume parity: apply this epoch's validation verdict
        # to best/patience FIRST, then checkpoint the post-decision state, so a
        # resumed run continues with exactly the state a continuous run had.
        improved = val_nll < best_metric - 1e-9
        if improved:
            best_metric = val_nll
            patience_left = patience
        else:
            patience_left -= 1
        _save_checkpoint(
            last_path, module=module, optimizer=optimizer, epoch=epoch, global_step=global_step,
            best_metric=best_metric, patience_left=patience_left, spec=spec,
            normalization=normalization, dataset_hash=dataset_hash, feature_schema_hash=feature_schema_hash,
            train_split_hash=train_split_hash, model_purpose=model_purpose, effective_batch=effective_batch,
            microbatch=microbatch, accumulation=accumulation, torch=torch,
        )
        if improved:
            _save_checkpoint(
                best_path, module=module, optimizer=optimizer, epoch=epoch, global_step=global_step,
                best_metric=best_metric, patience_left=patience_left, spec=spec,
                normalization=normalization, dataset_hash=dataset_hash, feature_schema_hash=feature_schema_hash,
                train_split_hash=train_split_hash, model_purpose=model_purpose, effective_batch=effective_batch,
                microbatch=microbatch, accumulation=accumulation, torch=torch,
            )
        last_completed_epoch = epoch
        if patience_left <= 0:
            stopped_early = True
            break

    if metrics_path:
        with Path(metrics_path).open("w", encoding="utf-8") as handle:
            for record in metrics_records:
                handle.write(_canonical_json(record) + "\n")

    if not best_path.with_suffix(".json").exists():
        # No validation improvement was ever recorded; adopt the last state.
        import shutil

        shutil.copyfile(last_path.with_suffix(".pt"), best_path.with_suffix(".pt"))
        shutil.copyfile(last_path.with_suffix(".json"), best_path.with_suffix(".json"))

    best_meta = load_checkpoint_metadata(best_path)
    return {
        "best_checkpoint": str(best_path),
        "last_checkpoint": str(last_path),
        "best_metric": best_meta["best_metric"],
        "epochs_run": last_completed_epoch + 1,
        "stopped_early": stopped_early,
        "resolved": resolved,
        "final_microbatch": microbatch,
        "dataset_hash": dataset_hash,
        "feature_schema_hash": feature_schema_hash,
        "train_split_hash": train_split_hash,
        "model_purpose": model_purpose,
        "metrics_path": str(metrics_path),
    }


def load_module_from_checkpoint(checkpoint_path: str | Path, *, device: str = "cpu"):
    """Load a module and its metadata from a checkpoint for export/evaluation."""
    torch = _require_torch()
    path = Path(checkpoint_path)
    meta = load_checkpoint_metadata(path)
    spec = ModelSpec(
        input_dim=int(meta["model_spec"]["input_dim"]),
        hidden_dims=tuple(int(v) for v in meta["model_spec"]["hidden_dims"]),
        activation=meta["model_spec"]["activation"],
    )
    module = build_module(spec).to(torch.device(device))
    tensors = _load_checkpoint_tensors(path, torch, torch.device(device))
    try:
        module.load_state_dict(tensors["model_state"])
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        raise CheckpointValidationError(
            "checkpoint state does not match the module shape", reason="state_shape_mismatch"
        ) from exc
    module.eval()
    return module, meta, spec


__all__ = [
    "CHECKPOINT_SCHEMA",
    "CheckpointValidationError",
    "MODEL_PURPOSE_ACTUAL",
    "MODEL_PURPOSE_SMOKE",
    "NEURAL_ARCH_VERSION",
    "ModelSpec",
    "NeuralError",
    "assert_checkpoint_compatible",
    "build_module",
    "evaluate_module",
    "load_checkpoint_metadata",
    "load_module_from_checkpoint",
    "train",
]
