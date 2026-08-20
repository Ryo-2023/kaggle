from __future__ import annotations

import pytest
import torch

from biohub.device import resolve_torch_device


def test_auto_prefers_nvidia_cuda(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    assert resolve_torch_device("auto") == torch.device("cuda")


def test_auto_uses_mps_when_cuda_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    mps = getattr(torch.backends, "mps", None)
    if mps is None:
        pytest.skip("this torch build has no MPS backend object")
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    monkeypatch.setattr(mps, "is_available", lambda: True)
    assert resolve_torch_device("auto") == torch.device("mps")


def test_auto_falls_back_to_cpu(monkeypatch: pytest.MonkeyPatch) -> None:
    mps = getattr(torch.backends, "mps", None)
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    if mps is not None:
        monkeypatch.setattr(mps, "is_available", lambda: False)
    assert resolve_torch_device("auto") == torch.device("cpu")


def test_unavailable_explicit_accelerator_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    with pytest.raises(RuntimeError, match="CUDA"):
        resolve_torch_device("cuda")
