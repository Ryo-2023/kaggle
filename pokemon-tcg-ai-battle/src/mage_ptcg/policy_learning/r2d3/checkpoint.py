"""Atomic R2D3 checkpoint metadata binding replay and population identities."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import os
import random
from typing import Any


def save_checkpoint(path: str | Path, *, model: Any, optimizer: Any, population_hash: str, replay_manifest_hash: str, step: int,
                    target: Any | None = None, replay: Any | None = None,
                    training_identity_hash: str | None = None,
                    scheduler: Any | None = None, strict_state: bool = False) -> dict[str, Any]:
    import torch
    schema = (
        "r2d3-checkpoint-v3"
        if strict_state or scheduler is not None
        else "r2d3-checkpoint-v2"
        if replay is not None or training_identity_hash is not None
        else "r2d3-checkpoint-v1"
    )
    payload = {"schema": schema, "population_hash": population_hash, "replay_manifest_hash": replay_manifest_hash, "step": step, "model": model.state_dict(), "target": (target or model).state_dict(), "optimizer": optimizer.state_dict(),
               "rng": {"cpu": torch.get_rng_state(), "cuda": torch.cuda.get_rng_state_all() if torch.cuda.is_available() else []}}
    if replay is not None:
        payload["replay_priority_state"] = replay.priority_state()
    if training_identity_hash is not None:
        payload["training_identity_hash"] = str(training_identity_hash)
    if schema == "r2d3-checkpoint-v3":
        payload["scheduler"] = scheduler.state_dict() if scheduler is not None else None
        payload["rng"]["python"] = random.getstate()
        try:
            import numpy
            payload["rng"]["numpy"] = numpy.random.get_state()
        except ImportError:
            payload["rng"]["numpy"] = None
    destination = Path(path); destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp"); torch.save(payload, temporary)
    descriptor = os.open(temporary, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    temporary.replace(destination)
    directory = os.open(destination.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    return {**{key: payload[key] for key in ("schema", "population_hash", "replay_manifest_hash", "step")}, "sha256": checkpoint_hash(destination)}


def load_checkpoint(path: str | Path, *, model: Any, target: Any, optimizer: Any, expected_population_hash: str,
                    expected_replay_manifest_hash: str, map_location: Any, replay: Any | None = None,
                    expected_training_identity_hash: str | None = None,
                    scheduler: Any | None = None, strict_state: bool = False) -> int:
    import torch
    payload = torch.load(Path(path), map_location=map_location, weights_only=False)
    if payload.get("schema") not in {"r2d3-checkpoint-v1", "r2d3-checkpoint-v2", "r2d3-checkpoint-v3"} or payload.get("population_hash") != expected_population_hash or payload.get("replay_manifest_hash") != expected_replay_manifest_hash:
        raise ValueError("R2D3 checkpoint identity mismatch")
    if strict_state and payload.get("schema") != "r2d3-checkpoint-v3":
        raise ValueError("R2D3 checkpoint lacks strict resume state")
    if expected_training_identity_hash is not None and (
        payload.get("schema") not in {"r2d3-checkpoint-v2", "r2d3-checkpoint-v3"}
        or payload.get("training_identity_hash") != expected_training_identity_hash
    ):
        raise ValueError("R2D3 checkpoint training identity mismatch")
    if replay is not None:
        if payload.get("schema") not in {"r2d3-checkpoint-v2", "r2d3-checkpoint-v3"} or "replay_priority_state" not in payload:
            raise ValueError("R2D3 checkpoint lacks replay priority state")
        replay.load_priority_state(payload["replay_priority_state"])
    model.load_state_dict(payload["model"]); target.load_state_dict(payload["target"]); optimizer.load_state_dict(payload["optimizer"])
    if scheduler is not None:
        if payload.get("schema") != "r2d3-checkpoint-v3" or payload.get("scheduler") is None:
            raise ValueError("R2D3 checkpoint lacks scheduler state")
        scheduler.load_state_dict(payload["scheduler"])
    torch.set_rng_state(payload["rng"]["cpu"].cpu())
    if torch.cuda.is_available() and payload["rng"].get("cuda"): torch.cuda.set_rng_state_all([value.cpu() for value in payload["rng"]["cuda"]])
    if payload.get("schema") == "r2d3-checkpoint-v3":
        random.setstate(payload["rng"]["python"])
        numpy_state = payload["rng"].get("numpy")
        if numpy_state is not None:
            try:
                import numpy
            except ImportError as exc:
                raise ValueError("checkpoint needs NumPy RNG restoration") from exc
            numpy.random.set_state(numpy_state)
    return int(payload["step"])


def checkpoint_hash(path: str | Path) -> str: return hashlib.sha256(Path(path).read_bytes()).hexdigest()
