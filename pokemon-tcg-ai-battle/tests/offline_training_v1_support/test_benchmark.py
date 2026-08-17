"""Tests for dataset format and compression level benchmarks."""

from __future__ import annotations
from pathlib import Path
import pytest
from mage_ptcg.offline_training_v1_support.format_benchmark import run_format_benchmark, run_compression_benchmark

def test_format_and_compression_benchmarks(tmp_path: Path):
    records = [
        {"id": i, "name": f"name_{i}", "score": i * 1.5, "flag": i % 2 == 0}
        for i in range(100)
    ]

    # 1. Format benchmark
    formats = run_format_benchmark(records, tmp_path)
    assert "JSONL" in formats
    assert "Gzipped_JSONL" in formats
    assert "Canonical_JSON_Array" in formats

    assert formats["JSONL"]["write_time_sec"] >= 0.0
    assert formats["JSONL"]["file_size_bytes"] > 0

    # 2. Compression benchmark
    compressions = run_compression_benchmark(records, tmp_path)
    assert "Level_1" in compressions
    assert "Level_9" in compressions
    assert compressions["Level_1"]["compression_time_sec"] >= 0.0
    assert compressions["Level_9"]["file_size_bytes"] <= compressions["Level_1"]["file_size_bytes"]
