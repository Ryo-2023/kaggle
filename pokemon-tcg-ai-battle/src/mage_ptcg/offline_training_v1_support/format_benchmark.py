"""Dataset format and compression level benchmarking utility.

Compares file sizes, write/read latencies, and features for various serialization formats
and compression levels using public-safe synthetic records.
"""

from __future__ import annotations
import time
import gzip
import json
from pathlib import Path
from typing import Any

def run_format_benchmark(records: list[dict[str, Any]], output_dir: Path) -> dict[str, Any]:
    """Benchmark different dataset serialization formats."""
    output_dir.mkdir(parents=True, exist_ok=True)

    results = {}

    # 1. JSONL (JSON Lines)
    jsonl_path = output_dir / "bench.jsonl"
    t0 = time.perf_counter()
    with open(jsonl_path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
    t_write_jsonl = time.perf_counter() - t0

    t0 = time.perf_counter()
    loaded_jsonl = []
    with open(jsonl_path, "r", encoding="utf-8") as f:
        for line in f:
            loaded_jsonl.append(json.loads(line))
    t_read_jsonl = time.perf_counter() - t0

    results["JSONL"] = {
        "write_time_sec": t_write_jsonl,
        "read_time_sec": t_read_jsonl,
        "file_size_bytes": jsonl_path.stat().st_size,
        "streaming_support": True,
        "random_access": False,
        "schema_flexibility": "High",
        "human_inspectability": "High",
    }

    # 2. Gzipped JSONL
    gzip_jsonl_path = output_dir / "bench.jsonl.gz"
    t0 = time.perf_counter()
    with gzip.open(gzip_jsonl_path, "wt", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
    t_write_gz = time.perf_counter() - t0

    t0 = time.perf_counter()
    loaded_gz = []
    with gzip.open(gzip_jsonl_path, "rt", encoding="utf-8") as f:
        for line in f:
            loaded_gz.append(json.loads(line))
    t_read_gz = time.perf_counter() - t0

    results["Gzipped_JSONL"] = {
        "write_time_sec": t_write_gz,
        "read_time_sec": t_read_gz,
        "file_size_bytes": gzip_jsonl_path.stat().st_size,
        "streaming_support": True,
        "random_access": False,
        "schema_flexibility": "High",
        "human_inspectability": "Low (Binary)",
    }

    # 3. Canonical JSON Array
    array_path = output_dir / "bench_array.json"
    t0 = time.perf_counter()
    with open(array_path, "w", encoding="utf-8") as f:
        f.write(json.dumps(records, sort_keys=True))
    t_write_array = time.perf_counter() - t0

    t0 = time.perf_counter()
    with open(array_path, "r", encoding="utf-8") as f:
        loaded_array = json.loads(f.read())
    t_read_array = time.perf_counter() - t0

    results["Canonical_JSON_Array"] = {
        "write_time_sec": t_write_array,
        "read_time_sec": t_read_array,
        "file_size_bytes": array_path.stat().st_size,
        "streaming_support": False,
        "random_access": False,
        "schema_flexibility": "High",
        "human_inspectability": "High",
    }

    # Clean up temp files
    for p in (jsonl_path, gzip_jsonl_path, array_path):
        if p.exists():
            p.unlink()

    return results


def run_compression_benchmark(records: list[dict[str, Any]], output_dir: Path) -> dict[str, Any]:
    """Benchmark gzip compression levels 1, 3, 6, 9."""
    output_dir.mkdir(parents=True, exist_ok=True)
    serialized_data = "\n".join(json.dumps(r) for r in records)

    results = {}
    for level in [1, 3, 6, 9]:
        gz_path = output_dir / f"bench_compress_l{level}.gz"

        # Write/Compress
        t0 = time.perf_counter()
        with gzip.open(gz_path, "wb", compresslevel=level) as f:
            f.write(serialized_data.encode("utf-8"))
        t_compress = time.perf_counter() - t0

        # Read/Decompress
        t0 = time.perf_counter()
        with gzip.open(gz_path, "rb") as f:
            decompressed = f.read().decode("utf-8")
        t_decompress = time.perf_counter() - t0

        file_size = gz_path.stat().st_size
        records_per_sec = len(records) / t_compress if t_compress > 0 else 0.0

        results[f"Level_{level}"] = {
            "compression_time_sec": t_compress,
            "decompression_time_sec": t_decompress,
            "file_size_bytes": file_size,
            "records_per_second": records_per_sec
        }

        if gz_path.exists():
            gz_path.unlink()

    return results
