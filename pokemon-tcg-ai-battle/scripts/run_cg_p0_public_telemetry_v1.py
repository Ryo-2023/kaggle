#!/usr/bin/env python3
"""Collect a P0 public telemetry block with the shared P1 collector contract."""

from __future__ import annotations

from pathlib import Path

from scripts.run_cg_public_telemetry_v1 import collect_p1_public_telemetry


ROOT = Path(__file__).resolve().parents[1]


if __name__ == "__main__":
    result = collect_p1_public_telemetry(
        source_package=ROOT / "runs/final-sprint-autonomous/root-cg-submission-candidate-v1-20260814/package",
        output_root=ROOT / "runs/final-sprint-autonomous/cg-p0-public-telemetry-96-20260814-v2",
        base_seed=40400000,
        workers=12,
        worker_recycle_games=16,
        games_per_opponent_seat=2,
        candidate_id="root-cg-self-owned-v1",
    )
    print(result["status"], result["output_root"])
