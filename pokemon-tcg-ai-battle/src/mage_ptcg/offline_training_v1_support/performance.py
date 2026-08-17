"""Performance and latency measurement analysis module.

Aggregates execution duration percentiles, throughput, and memory consumption.
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, Iterable, Sequence

from mage_ptcg.offline_training_v1_support.contracts import SupportContractError


def calculate_percentile(sorted_data: list[float], percentile: float) -> float:
    """Calculate deterministic percentile using linear interpolation."""
    if not sorted_data:
        return 0.0
    idx = (len(sorted_data) - 1) * percentile
    f = math.floor(idx)
    c = math.ceil(idx)
    if f == c:
        return sorted_data[int(idx)]
    return sorted_data[int(f)] * (c - idx) + sorted_data[int(c)] * (idx - f)


def analyze_performance_measurements(
    measurements: Iterable[dict[str, Any]],
    min_count: int = 5,
) -> dict[str, Any]:
    """Aggregate performance traces to compile execution benchmarking reports."""
    records = list(measurements)
    if len(records) < min_count:
        return {"status": "INSUFFICIENT_EVIDENCE", "metrics": {}}

    durations = []
    memories = []

    # Categorizations
    cold_durations = []
    warm_durations = []
    by_candidate = defaultdict(list)
    by_batch = defaultdict(list)
    by_device = defaultdict(list)

    for r in records:
        dur = r.get("duration_ns")
        # Validation: Reject non-finite durations
        if dur is None or not isinstance(dur, (int, float)) or not math.isfinite(dur) or dur < 0:
            continue

        durations.append(dur)

        mem = r.get("memory_bytes")
        if mem is not None and isinstance(mem, (int, float)) and math.isfinite(mem):
            memories.append(mem)

        is_warmup = bool(r.get("warmup", False))
        if is_warmup:
            cold_durations.append(dur)  # Warmup phase can represent cold start
        else:
            warm_durations.append(dur)

        cand_cnt = r.get("candidate_count")
        if cand_cnt is not None:
            # Bucket candidates (e.g. 0-5, 6-15, 16+)
            c_val = int(cand_cnt)
            if c_val <= 5:
                bucket = "0_5"
            elif c_val <= 15:
                bucket = "6_15"
            else:
                bucket = "16_plus"
            by_candidate[bucket].append(dur)

        batch_sz = r.get("batch_size")
        if batch_sz is not None:
            by_batch[str(batch_sz)].append(dur)

        device = r.get("device", "unknown")
        by_device[str(device)].append(dur)

    if not durations:
        return {"status": "INSUFFICIENT_EVIDENCE", "metrics": {}}

    durations.sort()
    n = len(durations)

    # IQR for Outliers
    p25 = calculate_percentile(durations, 0.25)
    p75 = calculate_percentile(durations, 0.75)
    iqr = p75 - p25
    outlier_threshold = p75 + 1.5 * iqr
    outliers = sum(1 for d in durations if d > outlier_threshold)

    # Throughput: Operations per second (1 / mean_duration_sec)
    mean_dur_ns = sum(durations) / n
    throughput = (1e9 / mean_dur_ns) if mean_dur_ns > 0 else 0.0

    # Bucketed aggregates helper
    def get_bucket_mean(mapping: dict[str, list[float]]) -> dict[str, float]:
        res = {}
        for k, vals in mapping.items():
            if vals:
                res[k] = sum(vals) / len(vals)
        return res

    metrics = {
        "count": n,
        "min_ns": durations[0],
        "max_ns": durations[-1],
        "mean_ns": mean_dur_ns,
        "median_ns": calculate_percentile(durations, 0.50),
        "p50_ns": calculate_percentile(durations, 0.50),
        "p90_ns": calculate_percentile(durations, 0.90),
        "p95_ns": calculate_percentile(durations, 0.95),
        "p99_ns": calculate_percentile(durations, 0.99),
        "throughput_ops_sec": throughput,
        "outlier_count": outliers,
        "cold_start_mean_ns": sum(cold_durations) / len(cold_durations) if cold_durations else None,
        "warm_start_mean_ns": sum(warm_durations) / len(warm_durations) if warm_durations else None,
        "memory_mean_bytes": sum(memories) / len(memories) if memories else None,
        "candidate_count_buckets_mean_ns": get_bucket_mean(by_candidate),
        "batch_size_buckets_mean_ns": get_bucket_mean(by_batch),
        "device_buckets_mean_ns": get_bucket_mean(by_device),
    }

    return {
        "status": "PASS",
        "metrics": metrics,
    }
