"""Published bidirectional harmonic-probability association wrapper.

The implementation mirrors Yusuke Togashi's public Biohub v18 notebook.  It
keeps the upstream model and inference loop intact while evaluating the same
candidate pair in reverse and combining the two calibrated probability
distributions before the unchanged downstream softmax/ILP path.
"""

from __future__ import annotations

import math
from collections.abc import Callable

import torch

DEFAULT_REVERSE_WEIGHT = 0.20
MAX_PUBLISHED_REVERSE_WEIGHT = 0.35


def _validate_reverse_weight(reverse_weight: float) -> float:
    weight = float(reverse_weight)
    if not math.isfinite(weight) or not 0.0 < weight <= MAX_PUBLISHED_REVERSE_WEIGHT:
        raise ValueError(
            "reverse_weight must be finite and in (0, 0.35] to match the published method"
        )
    return weight


def fuse_harmonic_logits(
    forward_logits: torch.Tensor,
    reverse_native_logits: torch.Tensor,
    *,
    reverse_weight: float = DEFAULT_REVERSE_WEIGHT,
) -> torch.Tensor:
    """Fuse forward and native reverse logits using the published formula.

    ``forward_logits`` has shape ``(B, N_source, N_target)``.  The reverse
    model call naturally returns ``(B, N_target, N_source)``; its node axes are
    transposed before calibration and probability-space fusion.  Softmax is
    intentionally over ``dim=1`` because that is the unchanged upstream
    source-axis convention after batch removal.
    """

    weight = _validate_reverse_weight(reverse_weight)
    if forward_logits.ndim != 3 or reverse_native_logits.ndim != 3:
        raise ValueError("forward and reverse logits must both have shape (B, N, M)")
    if (
        forward_logits.shape[0] != reverse_native_logits.shape[0]
        or forward_logits.shape[1] != reverse_native_logits.shape[2]
        or forward_logits.shape[2] != reverse_native_logits.shape[1]
    ):
        raise ValueError(
            "reverse logits must have native shape (B, N_target, N_source) matching forward logits"
        )
    if not torch.isfinite(forward_logits).all() or not torch.isfinite(reverse_native_logits).all():
        raise ValueError("forward and reverse logits must be finite")

    reverse_logits = reverse_native_logits.transpose(1, 2)

    # Published per-target logit alignment: match reverse mean/std to forward.
    forward_center = forward_logits.mean(dim=1, keepdim=True)
    forward_scale = forward_logits.float().std(
        dim=1, keepdim=True, unbiased=False
    ).clamp_min(1e-4)
    reverse_center = reverse_logits.mean(dim=1, keepdim=True)
    reverse_scale = reverse_logits.float().std(
        dim=1, keepdim=True, unbiased=False
    ).clamp_min(1e-4)
    reverse_scale_ratio = (forward_scale / reverse_scale).clamp(0.5, 2.0)
    reverse_scale_ratio = reverse_scale_ratio.to(reverse_logits.dtype)
    reverse_aligned = (
        (reverse_logits - reverse_center) * reverse_scale_ratio + forward_center
    )

    # Published weighted harmonic probability fusion.
    forward_prob = torch.softmax(forward_logits.float(), dim=1).clamp_min(1e-8)
    reverse_prob = torch.softmax(reverse_aligned.float(), dim=1).clamp_min(1e-8)
    harmonic_prob = 1.0 / (
        (1.0 - weight) / forward_prob + weight / reverse_prob
    )
    harmonic_prob = harmonic_prob / harmonic_prob.sum(dim=1, keepdim=True).clamp_min(1e-8)
    harmonic_logits = torch.log(harmonic_prob.clamp_min(1e-8))

    # Re-align fused logits to the forward scale expected by upstream.
    harmonic_center = harmonic_logits.mean(dim=1, keepdim=True)
    harmonic_scale = harmonic_logits.std(
        dim=1, keepdim=True, unbiased=False
    ).clamp_min(1e-4)
    harmonic_scale_ratio = (forward_scale / harmonic_scale).clamp(0.5, 2.0)
    return (
        (harmonic_logits - harmonic_center) * harmonic_scale_ratio + forward_center
    ).to(reverse_aligned.dtype)


def harmonic_predict_edges(
    original_predict_edges: Callable[..., torch.Tensor],
    *,
    reverse_weight: float = DEFAULT_REVERSE_WEIGHT,
) -> Callable[..., torch.Tensor]:
    """Wrap an upstream edge predictor with a reverse harmonic pass.

    The returned callable has the same eight positional arguments as the
    upstream ``UNetNodeTransformer.predict_edges`` method.  The second model
    call receives every feature, coordinate, position, and mask argument in
    source/target-reversed order.
    """

    _validate_reverse_weight(reverse_weight)

    def predict_edges(
        feat_source: torch.Tensor,
        feat_target: torch.Tensor,
        coords_source: torch.Tensor,
        coords_target: torch.Tensor,
        pos_source: torch.Tensor,
        pos_target: torch.Tensor,
        mask_source: torch.Tensor,
        mask_target: torch.Tensor,
    ) -> torch.Tensor:
        forward_logits = original_predict_edges(
            feat_source,
            feat_target,
            coords_source,
            coords_target,
            pos_source,
            pos_target,
            mask_source,
            mask_target,
        )
        reverse_native_logits = original_predict_edges(
            feat_target,
            feat_source,
            coords_target,
            coords_source,
            pos_target,
            pos_source,
            mask_target,
            mask_source,
        )
        return fuse_harmonic_logits(
            forward_logits,
            reverse_native_logits,
            reverse_weight=reverse_weight,
        )

    return predict_edges


__all__ = [
    "DEFAULT_REVERSE_WEIGHT",
    "MAX_PUBLISHED_REVERSE_WEIGHT",
    "fuse_harmonic_logits",
    "harmonic_predict_edges",
]
