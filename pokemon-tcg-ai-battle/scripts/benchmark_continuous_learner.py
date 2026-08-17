#!/usr/bin/env python3
"""実 replay に対する continuous learner の短時間 stage benchmark。"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for module_root in (ROOT, ROOT / "src"):
    if str(module_root) not in sys.path:
        sys.path.insert(0, str(module_root))

from mage_ptcg.continuous_league.batching import (
    PackedReplayBatcher,
    learner_batch,
)
from mage_ptcg.continuous_league.replay_sealer import load_sealed_replay
from mage_ptcg.policy_learning.r2d3.learner import LearnerConfig, R2D3Learner
from mage_ptcg.policy_learning.r2d3.model import (
    R2D3ModelConfig,
    RecurrentDistributionalQ,
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--replay-manifest", type=Path, required=True)
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--batch-sizes", default="32,128,512")
    parser.add_argument("--updates", type=int, default=3)
    parser.add_argument("--warmup-updates", type=int, default=1)
    parser.add_argument("--hidden-size", type=int, default=256)
    parser.add_argument("--seed", type=int, default=910_000)
    parser.add_argument("--bf16", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument(
        "--prepack", action=argparse.BooleanOptionalAction, default=False
    )
    parser.add_argument(
        "--resident-replay",
        action=argparse.BooleanOptionalAction,
        default=False,
    )
    parser.add_argument(
        "--pin-memory", action=argparse.BooleanOptionalAction, default=False
    )
    parser.add_argument(
        "--compare-pin-memory",
        action=argparse.BooleanOptionalAction,
        default=False,
    )
    parser.add_argument(
        "--fused-optimizer",
        action=argparse.BooleanOptionalAction,
        default=False,
    )
    parser.add_argument(
        "--matmul-precision",
        choices=("highest", "high", "medium"),
        default="highest",
    )
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def _synchronize(torch: Any, device: Any) -> None:
    if device.type == "cuda":
        torch.cuda.synchronize(device)


def _percentiles(values: list[float]) -> dict[str, float]:
    ordered = sorted(values)
    return {
        "mean_ms": 1_000.0 * statistics.fmean(ordered),
        "p50_ms": 1_000.0 * statistics.median(ordered),
        "max_ms": 1_000.0 * ordered[-1],
    }


def _measure_candidate(
    *,
    source_replay: Any,
    batch_size: int,
    updates: int,
    warmup_updates: int,
    hidden_size: int,
    seed: int,
    device: Any,
    use_bf16: bool,
    batcher: PackedReplayBatcher | None,
    pin_memory: bool,
    fused_optimizer: bool,
    resident_replay: bool,
) -> dict[str, Any]:
    import torch

    replay = source_replay.fork()
    torch.manual_seed(seed + batch_size)
    if device.type == "cuda":
        torch.cuda.manual_seed_all(seed + batch_size)
    model_config = R2D3ModelConfig(hidden_size=hidden_size)
    learner_config = LearnerConfig()
    model = RecurrentDistributionalQ(model_config).to(device)
    optimizer = torch.optim.AdamW(
        model.parameters(), lr=1e-4, fused=fused_optimizer
    )
    learner = R2D3Learner(model, optimizer, config=learner_config)
    autocast_enabled = (
        device.type == "cuda" and use_bf16 and torch.cuda.is_bf16_supported()
    )
    if device.type == "cuda":
        torch.cuda.empty_cache()
        torch.cuda.reset_peak_memory_stats(device)

    sample_times: list[float] = []
    cpu_batch_times: list[float] = []
    transfer_times: list[float] = []
    batch_times: list[float] = []
    update_times: list[float] = []
    total_times: list[float] = []
    total_iterations = warmup_updates + updates
    last_metrics: dict[str, Any] | None = None
    for iteration in range(total_iterations):
        measured = iteration >= warmup_updates
        started = time.perf_counter()
        sample = replay.sample(
            min(batch_size, len(replay)),
            beta=0.4,
            demonstration_ratio=1.0 / 32.0,
            seed=seed + iteration,
            episode_first=True,
        )
        sampled = time.perf_counter()
        cpu_batched = sampled
        if batcher is not None and resident_replay:
            batch = batcher.resident_batch(sample, device)
        elif batcher is None:
            batch = learner_batch(
                sample,
                device,
                n_step=learner_config.n_step,
                opponent_classes=model_config.opponent_classes,
                deck_family_classes=model_config.deck_family_classes,
                action_type_classes=model_config.action_type_classes,
            )
        else:
            cpu_batch = batcher.cpu_batch(
                sample, pin_memory=pin_memory
            )
            cpu_batched = time.perf_counter()
            batch = batcher.upload(cpu_batch, device)
        _synchronize(torch, device)
        batched = time.perf_counter()
        with torch.autocast(
            device_type=device.type,
            dtype=torch.bfloat16,
            enabled=autocast_enabled,
        ):
            last_metrics = learner.update(**batch)
        _synchronize(torch, device)
        updated = time.perf_counter()
        replay.update_priorities(
            sample.indices,
            last_metrics["sequence_priorities"],
            importance=sample.weights,
        )
        finished = time.perf_counter()
        if measured:
            sample_times.append(sampled - started)
            cpu_batch_times.append(cpu_batched - sampled)
            transfer_times.append(batched - cpu_batched)
            batch_times.append(batched - sampled)
            update_times.append(updated - batched)
            total_times.append(finished - started)

    assert last_metrics is not None
    elapsed = sum(total_times)
    row = {
        "batch_size": batch_size,
        "updates": updates,
        "device": str(device),
        "bf16": autocast_enabled,
        "prepacked": batcher is not None,
        "resident_replay": resident_replay,
        "pin_memory": pin_memory,
        "fused_optimizer": fused_optimizer,
        "sample": _percentiles(sample_times),
        "batch_cpu": _percentiles(cpu_batch_times),
        "transfer": _percentiles(transfer_times),
        "batch_and_transfer": _percentiles(batch_times),
        "learner_update": _percentiles(update_times),
        "end_to_end": _percentiles(total_times),
        "updates_per_second": updates / elapsed,
        "sequences_per_second": updates * batch_size / elapsed,
        "loss": float(last_metrics["loss"]),
        "gradient_norm": float(last_metrics["gradient_norm"]),
        "status": "PASS",
    }
    if device.type == "cuda":
        row["peak_allocated_mb"] = torch.cuda.max_memory_allocated(device) / 2**20
        row["peak_reserved_mb"] = torch.cuda.max_memory_reserved(device) / 2**20
    if not all(
        math.isfinite(float(row[key])) for key in ("loss", "gradient_norm")
    ):
        raise FloatingPointError("benchmark learner produced non-finite metrics")
    return row


def main() -> None:
    args = _arguments()
    if args.updates < 1 or args.warmup_updates < 0:
        raise ValueError("updates must be positive and warmup must be non-negative")
    batch_sizes = tuple(int(value) for value in args.batch_sizes.split(","))
    if any(value < 1 for value in batch_sizes):
        raise ValueError("batch sizes must be positive")

    import torch

    device = torch.device(args.device)
    if device.type == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA is not available to this Python process")
    if args.pin_memory and device.type != "cuda":
        raise ValueError("pin-memory requires CUDA")
    if args.fused_optimizer and device.type != "cuda":
        raise ValueError("fused optimizer requires CUDA")
    if args.resident_replay and (device.type != "cuda" or not args.prepack):
        raise ValueError("resident-replay requires prepack on CUDA")
    torch.set_float32_matmul_precision(args.matmul_precision)
    load_started = time.perf_counter()
    replay = load_sealed_replay(args.replay_manifest)
    load_seconds = time.perf_counter() - load_started
    prepack_started = time.perf_counter()
    batcher = (
        PackedReplayBatcher(replay, eager=True)
        if args.prepack
        else None
    )
    if batcher is not None and (
        args.pin_memory or args.compare_pin_memory or args.resident_replay
    ):
        batcher.reserve_pinned(max(batch_sizes))
    prepack_seconds = time.perf_counter() - prepack_started
    resident_started = time.perf_counter()
    if batcher is not None and args.resident_replay:
        batcher.materialize_resident(
            device, chunk_size=max(batch_sizes)
        )
    resident_seconds = time.perf_counter() - resident_started
    pin_memory_modes = (
        (False, True) if args.compare_pin_memory else (args.pin_memory,)
    )
    rows: list[dict[str, Any]] = []
    for pin_memory in pin_memory_modes:
        for candidate in batch_sizes:
            try:
                rows.append(
                    _measure_candidate(
                        source_replay=replay,
                        batch_size=min(candidate, len(replay)),
                        updates=args.updates,
                        warmup_updates=args.warmup_updates,
                        hidden_size=args.hidden_size,
                        seed=args.seed + candidate * 100,
                        device=device,
                        use_bf16=args.bf16,
                        batcher=batcher,
                        pin_memory=pin_memory,
                        fused_optimizer=args.fused_optimizer,
                        resident_replay=args.resident_replay,
                    )
                )
            except torch.OutOfMemoryError as exc:
                rows.append(
                    {
                        "batch_size": candidate,
                        "device": str(device),
                        "prepacked": batcher is not None,
                        "resident_replay": args.resident_replay,
                        "pin_memory": pin_memory,
                        "status": "REJECTED_OOM",
                        "error": str(exc).splitlines()[0],
                    }
                )
                if device.type == "cuda":
                    torch.cuda.empty_cache()
    payload = {
        "schema": "continuous-learner-benchmark-v1",
        "replay_manifest": str(args.replay_manifest),
        "replay_sequences": len(replay),
        "load_seconds": load_seconds,
        "prepack_seconds": prepack_seconds,
        "resident_seconds": resident_seconds,
        "hidden_size": args.hidden_size,
        "matmul_precision": args.matmul_precision,
        "rows": rows,
    }
    text = json.dumps(payload, indent=2, sort_keys=True)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
