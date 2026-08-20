"""Portable PyTorch device selection with accelerator-first fallback."""

from __future__ import annotations

import torch

DEVICE_SELECTION_ORDER = ("cuda", "mps", "cpu")


def _mps_is_available() -> bool:
    backends = getattr(torch, "backends", None)
    mps = getattr(backends, "mps", None)
    checker = getattr(mps, "is_available", None)
    return bool(checker()) if callable(checker) else False


def resolve_torch_device(requested: str | torch.device = "auto") -> torch.device:
    """Resolve ``auto`` as CUDA, then Apple MPS, then CPU.

    Explicit accelerator requests remain strict so a typo or an unavailable
    runtime cannot silently change a reproducibility-sensitive run.  ``auto``
    is the portable default for moving the same command between CPU-only
    Docker, Apple Silicon, and NVIDIA hosts.
    """

    if isinstance(requested, torch.device):
        device = requested
        requested_name = device.type
    elif isinstance(requested, str) and requested.strip():
        requested_name = requested.strip().lower()
        if requested_name == "auto":
            device = torch.device("cpu")
        else:
            device = torch.device(requested_name)
    else:
        raise ValueError("requested device must be a non-empty string or torch.device")

    if requested_name == "auto":
        if torch.cuda.is_available():
            return torch.device("cuda")
        if _mps_is_available():
            return torch.device("mps")
        return torch.device("cpu")

    if device.type == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA was requested but torch.cuda.is_available() is false")
    if device.type == "mps" and not _mps_is_available():
        raise RuntimeError("MPS was requested but torch.backends.mps.is_available() is false")
    if device.type not in {"cpu", "cuda", "mps"}:
        raise ValueError(f"unsupported torch device type: {device.type}")
    return device


__all__ = ["DEVICE_SELECTION_ORDER", "resolve_torch_device"]
