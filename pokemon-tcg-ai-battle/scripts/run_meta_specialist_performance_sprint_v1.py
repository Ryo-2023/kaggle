"""Create or execute a bounded current-R2 research sprint.

The script has no hidden collector seed-report default and no synthetic fallback.
``--execute`` requires an explicit driver factory which binds the committed
collector, V-trace trainer, and CABT quick-screen for the local environment.
Without it this writes an auditable command manifest only.
"""

from __future__ import annotations

import argparse
import importlib
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from mage_ptcg.meta_specialist.performance_sprint_v1 import (  # noqa: E402
    PERFORMANCE_SPRINT_SCHEMA_V1,
    PerformanceSprintConfigV1,
    PerformanceSprintHooksV1,
    run_performance_sprint_v1,
)


def _parse_driver_v1(spec: str, config: PerformanceSprintConfigV1) -> PerformanceSprintHooksV1:
    module_name, separator, attribute = spec.partition(":")
    if not separator or not module_name or not attribute:
        raise ValueError("--driver must be MODULE:FACTORY")
    factory = getattr(importlib.import_module(module_name), attribute)
    hooks = factory(config)
    if not isinstance(hooks, PerformanceSprintHooksV1):
        raise TypeError("driver factory must return PerformanceSprintHooksV1")
    return hooks


def _manifest_v1(config: PerformanceSprintConfigV1, driver: str | None) -> dict[str, object]:
    return {
        "schema_version": PERFORMANCE_SPRINT_SCHEMA_V1,
        "research_only": True,
        "status": "COMMAND_MANIFEST_ONLY",
        "baseline_checkpoint": str(config.baseline_checkpoint.resolve()),
        "baseline_strength_status": config.baseline_strength_status,
        "seed_qualification_report": str(config.seed_qualification_report.resolve()),
        "training_opponent_instance_ids": list(config.training_opponent_instance_ids),
        "evaluation_opponent_instance_ids": list(config.evaluation_opponent_instance_ids),
        "evaluation_seats": [0, 1],
        "value_warmup_updates": config.value_warmup_updates,
        "actor_updates": 1,
        "minimum_score_delta": config.minimum_score_delta,
        "driver": driver,
        "required_driver_contract": [
            "collect_fresh must use an explicit qualified seed report and return a new rollout_id per optimizer update",
            "warmup_value_head must update only value_head; actor_state_sha256 proves all other parameters unchanged",
            "actor_update must perform exactly one V-trace actor update",
            "runtime_policy_preflight must call the actual inference policy loader, not payload-only checkpoint loading",
            "evaluate must play candidate and baseline independently against held-out instances in seats 0 and 1",
            "evaluate must use actor_pool neural factory/runtime make_agent injection; scripts/run_evaluation_suite.py policy.build_agent() is not a valid current-R2 route",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--baseline-checkpoint", type=Path, required=True)
    parser.add_argument("--seed-qualification-report", type=Path, required=True)
    parser.add_argument("--training-opponent-instance-id", action="append", required=True)
    parser.add_argument("--evaluation-opponent-instance-id", action="append", required=True)
    parser.add_argument("--value-warmup-updates", type=int, default=1)
    parser.add_argument("--minimum-score-delta", type=float, default=0.0)
    parser.add_argument("--driver", help="MODULE:FACTORY returning PerformanceSprintHooksV1")
    parser.add_argument("--execute", action="store_true", help="run the explicitly supplied real driver")
    args = parser.parse_args(argv)
    config = PerformanceSprintConfigV1(
        run_dir=args.run_dir,
        baseline_checkpoint=args.baseline_checkpoint,
        training_opponent_instance_ids=tuple(args.training_opponent_instance_id),
        evaluation_opponent_instance_ids=tuple(args.evaluation_opponent_instance_id),
        seed_qualification_report=args.seed_qualification_report,
        value_warmup_updates=args.value_warmup_updates,
        minimum_score_delta=args.minimum_score_delta,
    )
    manifest = _manifest_v1(config, args.driver)
    config.run_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = config.run_dir / "command_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    if not args.execute:
        print(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2))
        return 0
    if not args.driver:
        parser.error("--execute requires --driver; no synthetic or implicit runtime adapter exists")
    result = run_performance_sprint_v1(config, _parse_driver_v1(args.driver, config))
    report = {**manifest, "status": "EXECUTED_RESEARCH_SPRINT", "result": {
        "challenger_checkpoint": str(result.challenger_checkpoint),
        "selected_checkpoint": str(result.selected_checkpoint),
        "challenger_sha256": result.challenger_sha256,
        "reloaded_sha256": result.reloaded_sha256,
        "consumed_rollout_ids": list(result.consumed_rollout_ids),
        "candidate_score": result.candidate_score,
        "baseline_score": result.baseline_score,
        "promoted": result.promoted,
        "rollback_applied": result.rollback_applied,
    }}
    (config.run_dir / "run_report.json").write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
