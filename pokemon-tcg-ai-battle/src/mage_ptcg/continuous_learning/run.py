"""Scheduler-ready O3 control plane; phase gates prevent fixture-to-training flow."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

from mage_ptcg.competition_intelligence.atomic_io import atomic_write_json
from mage_ptcg.competition_intelligence.canonical import digest
from mage_ptcg.competition_intelligence.run_live_acquisition import LiveAcquisitionConfig, main as live_main

from .evaluation import build_o3_promotion_report


SCHEMA_VERSION = "o3-continuous-learning-v1"
PHASES = ("acquisition", "snapshot", "pools", "dataset", "training", "evaluation", "promotion")


def load_config(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, Mapping) or value.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported O3 continuous learning config")
    if value.get("champion") != "rule-agent-v0" or value.get("auto_promote") is not False or value.get("auto_submit") is not False:
        raise ValueError("O3 requires Rule Agent v0 Champion and disabled auto-promotion/submission")
    return dict(value)


def _load_state(path: Path, config_hash: str) -> dict[str, Any]:
    if not path.exists():
        return {"schema_version": SCHEMA_VERSION, "config_hash": config_hash, "phases": {phase: "PENDING" for phase in PHASES}}
    state = json.loads(path.read_text(encoding="utf-8"))
    if state.get("config_hash") != config_hash:
        raise ValueError("resume rejected: config hash differs")
    return state


def run(*, config_path: str | Path, run_root: str | Path, fixture: str | None = None) -> dict[str, Any]:
    """Run/record phases. Training is intentionally blocked without an actual Snapshot input.

    The existing O2/C4 trainers remain the only training implementation; this
    control plane refuses to synthesize an alternate dataset when no actual
    Snapshot has been materialized.
    """
    config = load_config(config_path)
    root = Path(run_root)
    root.mkdir(parents=True, exist_ok=True)
    state_path = root / "o3_phase_manifest.json"
    state = _load_state(state_path, digest(config, domain="o3-continuous-config"))
    acquisition_config = Path(config_path).parent.parent.parent / config["external_acquisition_config"]
    arguments = ["--config", str(acquisition_config), "--run-root", str(root / "intelligence")]
    rules = Path(config_path).parent.parent.parent / config["rules_attestation_config"]
    arguments.extend(("--rules-attestation", str(rules)))
    if fixture:
        arguments.extend(("--fixture", fixture))
    if state["phases"]["acquisition"] == "PENDING":
        exit_code = live_main(arguments)
        state["phases"]["acquisition"] = "COMPLETE" if exit_code == 0 else "FAILED"
        atomic_write_json(state_path, state)
    # No raw replay/archive is allowed past this point.  A separately built
    # IntelligenceSnapshot is the mandatory boundary into O2/C4.
    manifest = root / "intelligence" / "reports" / "live_acquisition_manifest.json"
    fixture_contamination = bool(fixture)
    for phase in ("snapshot", "pools", "dataset", "training", "evaluation"):
        if state["phases"][phase] == "PENDING":
            state["phases"][phase] = "BLOCKED_FIXTURE_CONTAMINATION" if fixture_contamination else "BLOCKED_MISSING_ACTUAL_SNAPSHOT"
    if state["phases"]["promotion"] == "PENDING":
        state["promotion_report"] = build_o3_promotion_report({"seat_matched_logical_pairs": 0})
        state["phases"]["promotion"] = "COMPLETE_INSUFFICIENT_EVIDENCE"
    state["source_manifest_present"] = manifest.exists()
    state["champion"] = "Rule Agent v0"
    state["automatic_champion_change"] = False
    state["kaggle_submission_performed"] = False
    atomic_write_json(state_path, state)
    return state


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mage-ptcg-continuous-learning")
    parser.add_argument("--config", required=True)
    parser.add_argument("--run-root", required=True)
    parser.add_argument("--fixture")
    args = parser.parse_args(argv)
    print(json.dumps(run(config_path=args.config, run_root=args.run_root, fixture=args.fixture), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
