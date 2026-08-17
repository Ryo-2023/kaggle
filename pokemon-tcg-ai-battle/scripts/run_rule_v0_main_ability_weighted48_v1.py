"""Run a previously materialized ABILITY+120 weighted48 bridge.

The materializer is intentionally separate from execution.  This runner only
accepts a strict, fresh manifest and its paired game sidecar; it does not
train, promote, submit, or alter production code.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from collections import Counter

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from mage_ptcg.meta_specialist.rule_v0_main_ability_weighted48_v1 import (  # noqa: E402
    SCHEMA_V1,
    run_rule_v0_main_ability_weighted48_game_v1 as _run_rule_v0_main_ability_weighted48_game_v1,
    verify_rule_v0_main_ability_weighted48_v1,
)
from scripts.parallel_cabt_evaluator_v1 import (  # noqa: E402
    EvaluationGameV1,
    run_parallel_cabt_evaluation,
)


def run_rule_v0_main_ability_weighted48_game_v1(payload: dict[str, object]) -> dict[str, object]:
    """Stable spawn-import wrapper for the research game runner."""
    return dict(_run_rule_v0_main_ability_weighted48_game_v1(payload))


def _summary(rows: list[dict[str, object]], arm: str) -> dict[str, object]:
    selected = [row for row in rows if row.get("metadata", {}).get("arm") == arm]
    outcomes = Counter(str(row.get("outcome", "fault")) for row in selected)
    seats = Counter(str(row.get("seat")) for row in selected)
    return {
        "requested_games": len(selected),
        "wins": outcomes.get("win", 0),
        "draws": outcomes.get("draw", 0),
        "losses": outcomes.get("loss", 0),
        "faults": outcomes.get("fault", 0),
        "score_rate": (outcomes.get("win", 0) + 0.5 * outcomes.get("draw", 0)) / len(selected) if selected else None,
        "seat_counts": dict(sorted(seats.items())),
        "coverage_gate": "telemetry_not_available_in_existing_runner",
        "fallback_gate": "fail_closed_unknown",
    }


def run_screen(*, screen: Path, output: Path, workers: int = 12, worker_recycle_games: int = 16) -> dict[str, object]:
    manifest = json.loads((screen / "manifest.json").read_text(encoding="utf-8"))
    verify_rule_v0_main_ability_weighted48_v1(manifest, repo_root=ROOT)
    if manifest.get("execution_allowed") is not False:
        raise ValueError("execution authority must remain false in materialized bridge")
    sidecar = json.loads((screen / "games.json").read_text(encoding="utf-8"))
    if sidecar.get("schema_version") != SCHEMA_V1 or sidecar.get("screen_sha256") != manifest.get("screen_sha256") or sidecar.get("execution_allowed") is not False:
        raise ValueError("game sidecar identity/authority mismatch")
    candidate = tuple(EvaluationGameV1(**item) for item in sidecar.get("candidate_games", []))
    control = tuple(EvaluationGameV1(**item) for item in sidecar.get("control_games", []))
    if len(candidate) != 48 or len(control) != 48:
        raise ValueError("weighted48 sidecar must contain 48 candidate and 48 control games")
    if [(g.opponent_id, g.seat, g.seed) for g in candidate] != [(g.opponent_id, g.seat, g.seed) for g in control]:
        raise ValueError("candidate/control strata are not paired")
    result = run_parallel_cabt_evaluation(candidate + control, output_dir=output, max_workers=workers, worker_recycle_games=worker_recycle_games)
    return {
        "schema_version": SCHEMA_V1,
        "screen_sha256": manifest["screen_sha256"],
        "summary": {"candidate": _summary(result["rows"], "candidate"), "control": _summary(result["rows"], "control")},
        "evaluator": result["summary"],
        "authority": manifest["authority"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--screen", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--worker-recycle-games", type=int, default=16)
    args = parser.parse_args(argv)
    result = run_screen(screen=args.screen, output=args.output, workers=args.workers, worker_recycle_games=args.worker_recycle_games)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
