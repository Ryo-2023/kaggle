#!/usr/bin/env python3
"""Run a bounded synthetic GPU overfit/parity probe for Student v3 set."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from mage_ptcg.offline_scaleup.gpu_student_v3_set import (
    PURPOSE,
    _atomic_json,
    decode_set_predictions,
    make_set_cardinality_model,
    set_cardinality_loss,
)


def run_probe(*, output: Path, device_name: str, steps: int, seed: int) -> dict[str, object]:
    import torch

    if output.exists():
        raise FileExistsError(f"refusing to overwrite probe artifact: {output}")
    if not device_name.startswith("cuda") or not torch.cuda.is_available():
        raise RuntimeError("synthetic V3 set probe requires CUDA")
    if type(steps) is not int or steps < 1:
        raise ValueError("steps must be positive")
    device = torch.device(device_name)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    state = torch.randn(6, 32, device=device)
    actions = torch.randn(6, 4, 64, device=device)
    legal = torch.ones(6, 4, dtype=torch.bool, device=device)
    target_set = torch.tensor(
        [
            [False, False, False, False],
            [True, False, False, False],
            [False, True, True, False],
            [False, False, False, True],
            [True, False, True, False],
            [False, True, False, False],
        ],
        device=device,
    )
    batch = {
        "legal_mask": legal,
        "target_set": target_set,
        "target_count": target_set.sum(dim=1).long(),
        "min_count": torch.tensor([0, 0, 1, 1, 2, 0], device=device),
        "max_count": torch.tensor([2, 2, 2, 1, 2, 2], device=device),
    }
    config = {"hidden": 32, "blocks": 1, "dropout": 0.0, "max_count": 2}
    model = make_set_cardinality_model(**config).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=0.02)

    def metrics() -> tuple[dict[str, float], list[list[int]]]:
        model.eval()
        with torch.no_grad(), torch.autocast(device_type="cuda", dtype=torch.bfloat16):
            action_logits, count_logits = model(state, actions, legal)
            losses = set_cardinality_loss(action_logits, count_logits, batch)
        decoded = decode_set_predictions(
            action_logits,
            count_logits,
            legal,
            batch["min_count"],
            batch["max_count"],
        )
        return (
            {key: float(value.item()) for key, value in losses.items()},
            decoded,
        )

    initial, _initial_decode = metrics()
    model.train()
    for _step in range(steps):
        optimizer.zero_grad(set_to_none=True)
        with torch.autocast(device_type="cuda", dtype=torch.bfloat16):
            action_logits, count_logits = model(state, actions, legal)
            loss = set_cardinality_loss(action_logits, count_logits, batch)["total"]
        if not bool(torch.isfinite(loss).item()):
            raise RuntimeError("synthetic probe loss became non-finite")
        loss.backward()
        optimizer.step()
    final, gpu_decode = metrics()
    if not final["total"] < initial["total"]:
        raise RuntimeError("synthetic probe did not reduce total loss")

    cpu_model = make_set_cardinality_model(**config)
    cpu_model.load_state_dict(model.state_dict())
    cpu_model.eval()
    with torch.no_grad():
        cpu_action, cpu_count = cpu_model(state.cpu(), actions.cpu(), legal.cpu())
    cpu_decode = decode_set_predictions(
        cpu_action,
        cpu_count,
        legal.cpu(),
        batch["min_count"].cpu(),
        batch["max_count"].cpu(),
    )
    agreement = sum(
        gpu == cpu for gpu, cpu in zip(gpu_decode, cpu_decode, strict=True)
    ) / len(cpu_decode)
    if agreement != 1.0:
        raise RuntimeError("synthetic GPU/CPU decode parity failed")
    target_decode = [
        torch.nonzero(row, as_tuple=False).flatten().tolist() for row in target_set.cpu()
    ]
    exact = sum(
        set(prediction) == set(target)
        for prediction, target in zip(gpu_decode, target_decode, strict=True)
    ) / len(target_decode)
    payload: dict[str, object] = {
        "schema_version": "offline-scaleup-student-v3-set-synthetic-probe-v1",
        "purpose": PURPOSE,
        "synthetic_only": True,
        "performance_evidence": False,
        "seed": seed,
        "steps": steps,
        "examples": 6,
        "coverage": [
            "optional_decline_k0",
            "optional_accept_k1",
            "variable_multi_k2",
            "fixed_single_k1",
            "fixed_multi_k2",
        ],
        "model_config": config,
        "optimizer": {"kind": "AdamW", "learning_rate": 0.02},
        "device": str(device),
        "device_name": torch.cuda.get_device_name(device),
        "torch_version": torch.__version__,
        "cuda_version": torch.version.cuda,
        "bf16_supported": bool(torch.cuda.is_bf16_supported()),
        "initial_loss": initial,
        "final_loss": final,
        "total_loss_ratio": final["total"] / initial["total"],
        "exact_set_fidelity": exact,
        "gpu_cpu_decode_agreement": agreement,
        "all_metrics_finite": all(
            math.isfinite(value) for values in (initial, final) for value in values.values()
        ),
        "authority": {
            "training_authority": False,
            "promotion_authority": False,
            "submission_authority": False,
        },
    }
    _atomic_json(output, payload)
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="run-student-v3-set-synthetic-probe")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--steps", type=int, default=80)
    parser.add_argument("--seed", type=int, default=91)
    args = parser.parse_args(argv)
    try:
        result = run_probe(
            output=args.output,
            device_name=args.device,
            steps=args.steps,
            seed=args.seed,
        )
    except (OSError, ValueError, RuntimeError) as exc:
        print(json.dumps({"error": type(exc).__name__, "message": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(result, ensure_ascii=False, allow_nan=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
