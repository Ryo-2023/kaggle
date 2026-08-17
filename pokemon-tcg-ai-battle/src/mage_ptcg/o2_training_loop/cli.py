"""O2 entrypoint for planning and fixture-labelled orchestration.

``pipeline`` delegates training to the established Offline Training v1 CLI;
it never installs a new trainer or changes the submission default.  Fixture
execution is intentionally explicit and yields an insufficient-evidence
report, not a promotion claim.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from mage_ptcg.competition_intelligence.atomic_io import atomic_write_json
from mage_ptcg.offline_training.cli import main as offline_training_main

from .core import build_match_matrix, execute_match_plan, load_deck_pool, load_opponent_pool, paired_evaluation, promotion_report


def _load(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema_version") != "o2-training-loop-v1":
        raise ValueError("unsupported O2 training loop config")
    return value


def _plan(config: dict[str, Any]):
    decks = load_deck_pool(config["deck_pool"])
    opponents = load_opponent_pool(config["opponent_pool"], deck_ids=decks)
    challenger = str(config["challenger"])
    return build_match_matrix(decks=decks, opponents=opponents, challenger_id=challenger, opponent_ids=[item for item in opponents if item != challenger and opponents[item].enabled], seeds=config["seeds"], engine_version=str(config["engine_version"]), created_from_manifest=str(config["schema_version"]))


def _fixture(spec):
    # The fixture has no cabt semantics and cannot be consumed as a replay.
    return {"status": "DONE", "winner": 2, "elapsed_seconds": 0.0, "public_trace": {"fixture": True}}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="o2-training-loop")
    parser.add_argument("--config", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--run-id", default="o2")
    parser.add_argument("--run-offline-training", action="store_true")
    args = parser.parse_args(argv)
    try:
        config, specs, output = _load(args.config), _plan(_load(args.config)), Path(args.output_dir)
        atomic_write_json(output / "match_plan.json", {"schema_version": "o2-match-plan-manifest-v1", "matches": [item.to_dict() for item in specs]})
        # A config has to opt in to fixture mode.  Actual cabt invocation stays
        # with scripts.test_sim/dataops and must be wired with real factories.
        if config.get("fixture_only") is not True:
            raise ValueError("actual cabt backend must be supplied by the evaluation harness; refusing fixture substitution")
        batch = execute_match_plan(specs, output_dir=output, backend=_fixture, backend_kind="fixture_backend")
        records = [json.loads(path.read_text(encoding="utf-8")) for path in (output / "matches").glob("*/normalized.json")]
        evaluation = paired_evaluation(specs, records)
        report = promotion_report(evaluation)
        report["fixture_only"] = True
        report["student_evaluation"] = "NOT_RUN_FIXTURE_BACKEND"
        atomic_write_json(output / "promotion_report.json", report)
        offline_status = "NOT_RUN"
        if args.run_offline_training:
            offline_status = "RUNNING"
            result = offline_training_main(["pipeline", "--config", config["offline_training_config"], "--run-id", args.run_id, "--run-dir", str(output / "offline-training")])
            offline_status = "COMPLETE" if result == 0 else f"FAILED:{result}"
        print(json.dumps({"batch": batch, "evaluation": evaluation, "promotion": report["decision"], "offline_training": offline_status}, sort_keys=True))
        return 0 if offline_status in {"NOT_RUN", "COMPLETE"} else 2
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
