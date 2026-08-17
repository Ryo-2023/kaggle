"""Scale check performance and memory verification module.

Simulates 10,000+ record datasets using memory-efficient generators
and monitors wall-clock elapsed time.
"""

from __future__ import annotations

import time
import tempfile
import json
import gzip
from pathlib import Path
from typing import Any

from mage_ptcg.offline_training_v1_support.contracts import (
    digest,
    atomic_write_records,
)
from mage_ptcg.offline_training_v1_support.dataset_ops import (
    DatasetLifecycleManager,
    validate_shard_stream,
)

def run_scale_check(record_count: int = 10000) -> dict[str, Any]:
    """Verify performance characteristics under specified data volume."""
    start_time = time.time()

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        shard_path = tmp_path / "scale_shard.jsonl.gz"

        # Generate large gzip dataset streaming-wise
        # (avoid storing all records in memory list simultaneously)
        import random
        rng = random.Random(42)

        # Write streaming-wise
        with gzip.open(shard_path, "wt", encoding="utf-8") as f:
            for i in range(record_count):
                rec = {
                    "episode_id": f"ep_{i // 10}",
                    "decision_id": f"dec_{i}",
                    "state_digest": digest(f"state_{i}", domain="scale-state"),
                    "teacher_action_key": f"act_{rng.randint(0, 3)}",
                    "student_action_key": f"act_{rng.randint(0, 3)}",
                    "legal_actions": ["act_0", "act_1", "act_2", "act_3"],
                    "candidate_legal_rate": 1.0,
                    "candidate_fallback_count": 0,
                    "student_confidence": 0.95,
                    "selection_type": "normal",
                    "context_type": "normal",
                    "priority_score": 0.5,
                }
                f.write(json.dumps(rec, sort_keys=True, ensure_ascii=False) + "\n")

        write_elapsed = time.time() - start_time

        # Run stream validation
        val_start = time.time()
        stats = validate_shard_stream(shard_path, compression="gzip")
        val_elapsed = time.time() - val_start

        # Calculate metrics
        total_time = time.time() - start_time
        input_bytes = shard_path.stat().st_size
        records_per_second = record_count / val_elapsed if val_elapsed > 0.0 else 0.0

        # Memory RSS peak indicator (mock / standard fallback if resource module not imported)
        rss_bytes = 0
        try:
            import resource
            rss_bytes = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024 # KB to Bytes
        except ImportError:
            pass

        return {
            "status": "SUCCESS",
            "record_count": record_count,
            "write_time_sec": write_elapsed,
            "validation_time_sec": val_elapsed,
            "total_time_sec": total_time,
            "input_bytes": input_bytes,
            "records_per_second": records_per_second,
            "peak_rss_bytes": rss_bytes,
        }
