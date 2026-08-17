#!/usr/bin/env python3
"""Run one real outcome-only alternating stage for a Tomato-policy deck candidate.

This is a research-only bridge for validating the alternating runtime against a
real native policy/deck pair.  It never grants promotion, training, submission,
or long-run authority.  The default worker budget follows the repository
parallel-evaluation default (12 workers, 16 games per worker recycle).
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from mage_ptcg.meta_specialist.outcome_only_alternating_runtime_v1 import (
    OutcomeOnlyCandidateSpecV1,
    _config_sha,
    run_alternating_stage_v1,
)


ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "opponents/tomatomato_archaludon/main.py"
CONTROL_DECK_PATH = ROOT / "opponents/tomatomato_archaludon/deck.csv"
CANDIDATE_DECK_PATH = (
    ROOT
    / "runs/final-sprint-autonomous/deck-mutation-weighted-halving-v1-20260813"
    / "candidates/a73fbc771ec8d414ff7e6355419c3bf153c49309e511601327fe9f7db1506299/deck.csv"
)
POOL_ROOT = ROOT / "opponents"
REFERENCE_CONFIG = ROOT / "configs/meta_specialist/performance_first_broad_pool_v1.json"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_specs(
    *,
    candidate_id: str = "alternating-tomato-a73",
    candidate_deck_path: Path = CANDIDATE_DECK_PATH,
    candidate_policy_path: Path = POLICY_PATH,
    control_deck_path: Path = CONTROL_DECK_PATH,
    control_policy_path: Path = POLICY_PATH,
) -> tuple[OutcomeOnlyCandidateSpecV1, OutcomeOnlyCandidateSpecV1, list[str]]:
    candidate_policy_sha = _sha(candidate_policy_path)
    control_policy_sha = _sha(control_policy_path)
    config_sha = _config_sha({}, {}, 0.0)
    reference_ids = json.loads(REFERENCE_CONFIG.read_text(encoding="utf-8"))["opponent_ids"]
    common = {
        "env": {},
        "biases": {},
        "config_sha256": config_sha,
        "min_score_gain": 0.0,
    }
    candidate = OutcomeOnlyCandidateSpecV1(
        candidate_id=candidate_id,
        main_path=candidate_policy_path,
        policy_sha256=candidate_policy_sha,
        deck_path=candidate_deck_path,
        deck_sha256=_sha(candidate_deck_path),
        **common,
    )
    native_control = OutcomeOnlyCandidateSpecV1(
        candidate_id="alternating-tomato-parent",
        main_path=control_policy_path,
        policy_sha256=control_policy_sha,
        deck_path=control_deck_path,
        deck_sha256=_sha(control_deck_path),
        **common,
    )
    return candidate, native_control, list(reference_ids)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-root",
        type=Path,
        default=ROOT / "runs/final-sprint-autonomous/alternating-tomato-a73-96-20260814-v2",
    )
    parser.add_argument("--candidate-id", default="alternating-tomato-a73")
    parser.add_argument("--candidate-deck", type=Path, default=CANDIDATE_DECK_PATH)
    parser.add_argument("--candidate-policy", type=Path, default=POLICY_PATH)
    parser.add_argument("--control-deck", type=Path, default=CONTROL_DECK_PATH)
    parser.add_argument("--control-policy", type=Path, default=POLICY_PATH)
    parser.add_argument("--stage-games", type=int, choices=(96, 384, 768, 1536), default=96)
    parser.add_argument("--base-seed", type=int, default=25000000)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--worker-recycle-games", type=int, default=16)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()

    candidate, native_control, reference_ids = build_specs(
        candidate_id=args.candidate_id,
        candidate_deck_path=args.candidate_deck.resolve(),
        candidate_policy_path=args.candidate_policy.resolve(),
        control_deck_path=args.control_deck.resolve(),
        control_policy_path=args.control_policy.resolve(),
    )
    result = run_alternating_stage_v1(
        candidate=candidate,
        native_control=native_control,
        pool_root=POOL_ROOT,
        reference_ids=reference_ids,
        stage_games=args.stage_games,
        base_seed=args.base_seed,
        block_id=f"alternating-tomato-a73-{args.stage_games}-20260814-v2",
        output_root=args.output_root,
        execute=bool(args.execute),
        workers=args.workers,
        worker_recycle_games=args.worker_recycle_games,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
