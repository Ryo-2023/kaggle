#!/usr/bin/env python3
"""Write a small, machine-readable health record for local ingestion jobs."""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--status", choices=("RUNNING", "SUCCESS", "FAILED"), required=True)
    parser.add_argument("--detail", required=True)
    args = parser.parse_args()
    path = args.artifact_root / "state" / "scheduler_health.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "opponent-ingest-scheduler-health-v1",
        "run_id": args.run_id,
        "status": args.status,
        "detail": args.detail,
        "updated_at_unix": time.time(),
        "pid": os.getpid(),
        "schedule": "systemd user timer: daily at 00:00 and 12:00 local time",
        "next_run_source": "systemctl --user list-timers pokemon-opponent-ingest.timer",
    }
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    temporary.replace(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
