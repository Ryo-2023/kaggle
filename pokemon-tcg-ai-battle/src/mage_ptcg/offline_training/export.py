"""Pure-Python export of the neural Student and a torch-free inference core.

The export artifact is plain JSON: architecture, activation, feature schema,
train-only normalization, and per-layer weights/biases.  The inference core
below reproduces the PyTorch forward pass using only the standard library, so
the Kaggle package never imports torch or numpy.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Sequence


EXPORT_SCHEMA_VERSION = "offline-training-v1-neural-export-v1"


class ExportError(ValueError):
    """Raised when an export artifact is malformed or incompatible."""


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _extract_layers(module) -> list[dict[str, Any]]:
    import torch.nn as nn

    layers: list[dict[str, Any]] = []
    for child in module:
        if isinstance(child, nn.Linear):
            weight = child.weight.detach().cpu().double().tolist()
            bias = child.bias.detach().cpu().double().tolist()
            layers.append({"weight": weight, "bias": bias})
    if not layers:
        raise ExportError("module has no linear layers to export")
    return layers


def build_export(
    *,
    module,
    model_spec_dict: dict[str, Any],
    normalization: dict[str, Any],
    feature_schema: dict[str, Any],
    dataset_hash: str,
    config_hash: str,
    teacher_id: str,
    model_purpose: str,
) -> dict[str, Any]:
    """Build a JSON-safe export document (without writing it) from a torch module."""
    layers = _extract_layers(module)
    document: dict[str, Any] = {
        "schema_version": EXPORT_SCHEMA_VERSION,
        "architecture": {
            "input_dim": int(model_spec_dict["input_dim"]),
            "hidden_dims": list(model_spec_dict["hidden_dims"]),
            "activation": model_spec_dict["activation"],
        },
        "feature_schema_version": feature_schema["feature_schema_version"],
        "feature_schema_hash": feature_schema["feature_schema_hash"],
        "feature_dimension": feature_schema["feature_dimension"],
        "normalization": {"mean": list(normalization["mean"]), "std": list(normalization["std"])},
        "layers": layers,
        "dataset_hash": dataset_hash,
        "config_hash": config_hash,
        "teacher_id": teacher_id,
        "model_purpose": model_purpose,
        "fallback_policy": "rule-agent-v0",
    }
    document["model_hash"] = _digest({k: v for k, v in document.items() if k != "model_hash"})
    return document


def write_export(document: dict[str, Any], path: str | Path) -> str:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("x", encoding="utf-8") as handle:
        handle.write(_canonical_json(document) + "\n")
    return document["model_hash"]


def load_export(path: str | Path) -> dict[str, Any]:
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExportError(f"export artifact is unreadable: {exc}") from exc
    validate_export(document)
    return document


def validate_export(document: object) -> None:
    if not isinstance(document, dict):
        raise ExportError("export artifact must be a JSON object")
    if document.get("schema_version") != EXPORT_SCHEMA_VERSION:
        raise ExportError("unsupported export schema version")
    for name in ("architecture", "normalization", "layers", "feature_schema_hash", "feature_dimension", "model_hash"):
        if name not in document:
            raise ExportError(f"export artifact is missing {name}")
    recomputed = _digest({k: v for k, v in document.items() if k != "model_hash"})
    if recomputed != document["model_hash"]:
        raise ExportError("export model hash mismatch")
    layers = document["layers"]
    if not isinstance(layers, list) or not layers:
        raise ExportError("export layers are invalid")
    for layer in layers:
        weight = layer.get("weight")
        bias = layer.get("bias")
        if not isinstance(weight, list) or not isinstance(bias, list) or not weight:
            raise ExportError("export layer shape is invalid")
        cols = len(weight[0])
        for row in weight:
            if len(row) != cols or any(not math.isfinite(v) for v in row):
                raise ExportError("export layer weights are ragged or non-finite")
        if len(bias) != len(weight) or any(not math.isfinite(v) for v in bias):
            raise ExportError("export layer bias is invalid")


def _relu(values: list[float]) -> list[float]:
    return [value if value > 0.0 else 0.0 for value in values]


def _linear(weight: list[list[float]], bias: list[float], inputs: list[float]) -> list[float]:
    outputs: list[float] = []
    for row, offset in zip(weight, bias):
        total = offset
        for w, x in zip(row, inputs):
            total += w * x
        outputs.append(total)
    return outputs


def score_candidate(document: dict[str, Any], feature_vector: Sequence[float]) -> float:
    """Reproduce the torch forward pass for one candidate using pure Python."""
    mean = document["normalization"]["mean"]
    std = document["normalization"]["std"]
    if len(feature_vector) != len(mean):
        raise ExportError("feature dimension mismatch")
    activation = [(value - m) / s for value, m, s in zip(feature_vector, mean, std)]
    layers = document["layers"]
    for index, layer in enumerate(layers):
        activation = _linear(layer["weight"], layer["bias"], activation)
        if index < len(layers) - 1:
            activation = _relu(activation)
    if len(activation) != 1:
        raise ExportError("export head must produce a single score")
    score = activation[0]
    if not math.isfinite(score):
        raise ExportError("export produced a non-finite score")
    return score


def score_candidates(document: dict[str, Any], feature_rows: Sequence[Sequence[float]]) -> list[float]:
    return [score_candidate(document, row) for row in feature_rows]


__all__ = [
    "EXPORT_SCHEMA_VERSION",
    "ExportError",
    "build_export",
    "load_export",
    "score_candidate",
    "score_candidates",
    "validate_export",
    "write_export",
]
