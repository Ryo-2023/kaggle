from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import torch

from biohub.strong_baseline.harmonic import (
    fuse_harmonic_logits,
    harmonic_predict_edges,
)
from biohub.strong_baseline.runner import InferenceRequest, build_harmonic_command


def _published_fusion_reference(
    forward: torch.Tensor,
    reverse_native: torch.Tensor,
    reverse_weight: float,
) -> torch.Tensor:
    reverse = reverse_native.transpose(1, 2)
    forward_center = forward.mean(dim=1, keepdim=True)
    forward_scale = forward.float().std(dim=1, keepdim=True, unbiased=False).clamp_min(1e-4)
    reverse_center = reverse.mean(dim=1, keepdim=True)
    reverse_scale = reverse.float().std(dim=1, keepdim=True, unbiased=False).clamp_min(1e-4)
    reverse_scale_ratio = (forward_scale / reverse_scale).clamp(0.5, 2.0).to(reverse.dtype)
    reverse_aligned = (reverse - reverse_center) * reverse_scale_ratio + forward_center
    forward_prob = torch.softmax(forward.float(), dim=1).clamp_min(1e-8)
    reverse_prob = torch.softmax(reverse_aligned.float(), dim=1).clamp_min(1e-8)
    harmonic_prob = 1.0 / (
        (1.0 - reverse_weight) / forward_prob + reverse_weight / reverse_prob
    )
    harmonic_prob = harmonic_prob / harmonic_prob.sum(dim=1, keepdim=True).clamp_min(1e-8)
    harmonic_logits = torch.log(harmonic_prob.clamp_min(1e-8))
    harmonic_center = harmonic_logits.mean(dim=1, keepdim=True)
    harmonic_scale = harmonic_logits.std(dim=1, keepdim=True, unbiased=False).clamp_min(1e-4)
    harmonic_scale_ratio = (forward_scale / harmonic_scale).clamp(0.5, 2.0)
    return ((harmonic_logits - harmonic_center) * harmonic_scale_ratio + forward_center).to(reverse.dtype)


def test_fuse_harmonic_logits_matches_published_square_fixture() -> None:
    forward = torch.tensor([[[2.0, 0.0], [0.0, 2.0]]])
    reverse_native = torch.tensor([[[2.0, 0.0], [0.0, 2.0]]])

    fused = fuse_harmonic_logits(forward, reverse_native, reverse_weight=0.20)

    expected = _published_fusion_reference(forward, reverse_native, reverse_weight=0.20)
    assert fused.shape == (1, 2, 2)
    assert torch.isfinite(fused).all()
    torch.testing.assert_close(fused, expected)


def test_fuse_harmonic_logits_transposes_non_square_reverse_axes() -> None:
    forward = torch.tensor([[[2.0, 0.0, -1.0], [0.0, 2.0, 1.0]]])
    reverse_native = torch.tensor([[[2.0, 0.0], [0.0, 2.0], [1.0, -1.0]]])

    fused = fuse_harmonic_logits(forward, reverse_native, reverse_weight=0.20)

    expected = _published_fusion_reference(forward, reverse_native, reverse_weight=0.20)
    assert fused.shape == (1, 2, 3)
    assert torch.isfinite(fused).all()
    torch.testing.assert_close(fused, expected)


def test_harmonic_predict_edges_reverses_every_model_argument() -> None:
    calls: list[tuple[torch.Tensor, ...]] = []

    def original_predict_edges(*args: torch.Tensor) -> torch.Tensor:
        calls.append(args)
        if len(calls) == 1:
            return torch.tensor([[[2.0, 0.0], [0.0, 2.0]]])
        return torch.tensor([[[1.0, 3.0], [4.0, 2.0]]])

    wrapper = harmonic_predict_edges(original_predict_edges, reverse_weight=0.20)
    source = tuple(torch.tensor([[[float(i)]]]) for i in range(8))
    wrapper(*source)

    assert len(calls) == 2
    assert calls[0] == source
    assert calls[1] == (source[1], source[0], source[3], source[2], source[5], source[4], source[7], source[6])


def test_build_harmonic_command_is_image_only_and_pins_weight(tmp_path: Path) -> None:
    request = InferenceRequest(
        upstream_root=tmp_path / "upstream",
        image_stem=tmp_path / "44b6_0113de3b",
        checkpoint=tmp_path / "model.pth",
        output_dir=tmp_path / "harmonic_ilp",
        expected_device="cpu",
    )

    command = build_harmonic_command(request)

    assert "--ground-truth" not in command
    assert not any(str(part).endswith(".geff") for part in command)
    assert "--reverse-weight" not in command


@pytest.mark.parametrize("command", ["infer-harmonic", "smoke-harmonic"])
def test_harmonic_cli_is_inference_only(command: str) -> None:
    script = Path(__file__).parents[1] / "scripts" / "run_strong_baseline_v1.py"
    result = subprocess.run(
        [sys.executable, str(script), command, "--help"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "--ground-truth" not in result.stdout
    assert "--reverse-weight" not in result.stdout


def test_harmonic_source_receipt_does_not_claim_unretained_source_text() -> None:
    fixture = Path(__file__).parent / "fixtures" / "strong_baseline_v1" / "harmonic" / "source_receipt.json"
    receipt = json.loads(fixture.read_text())

    assert receipt["source"]["version_number"] == 18
    assert receipt["source"]["script_version_id"] == 338569479
    assert receipt["acquisition"]["source_sha256"] == (
        "dd3819cff82851b491d9cbeb6f5f0fc36e8da3c5e9ca90a8b0d5284785a250d"
    )
    assert receipt["formula_fixture_status"].startswith("BLOCKED:")
