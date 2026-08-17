#!/usr/bin/env python3
"""Run one bounded outcome-only deck→policy alternating iteration.

This entrypoint is research-only.  It deliberately executes at most one
96/384/768/1536 stage per phase and never grants training, promotion,
submission, or long-run authority.  Evaluations default to workers=12 and
worker recycle=16.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Mapping

from mage_ptcg.meta_specialist.outcome_only_alternating_loop_v1 import (
    DEFAULT_WORKER_RECYCLE_GAMES_V1,
    DEFAULT_WORKERS_V1,
    OutcomeOnlyCandidateSpecV1,
    run_alternating_iteration_v1,
)
from mage_ptcg.meta_specialist.outcome_only_alternating_runtime_v1 import _config_sha


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POOL_ROOT = ROOT / "opponents"
DEFAULT_REFERENCE_CONFIG = ROOT / "configs/meta_specialist/performance_first_broad_pool_v1.json"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json_object(raw: str, name: str) -> dict[str, object]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{name} must be JSON object: {exc}") from exc
    if not isinstance(value, dict):
        raise SystemExit(f"{name} must be a JSON object")
    return value


def _spec(
    *,
    candidate_id: str,
    main_path: Path,
    deck_path: Path,
    env: Mapping[str, object],
    biases: Mapping[str, object],
    min_score_gain: float,
) -> OutcomeOnlyCandidateSpecV1:
    normalized_env = {str(key): str(value) for key, value in env.items()}
    normalized_biases = {str(key): float(value) for key, value in biases.items()}
    return OutcomeOnlyCandidateSpecV1(
        candidate_id=candidate_id,
        main_path=main_path.resolve(),
        deck_path=deck_path.resolve(),
        policy_sha256=_sha(main_path),
        deck_sha256=_sha(deck_path),
        config_sha256=_config_sha(normalized_env, normalized_biases, min_score_gain),
        env=normalized_env,
        biases=normalized_biases,
        min_score_gain=min_score_gain,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--pool-root", type=Path, default=DEFAULT_POOL_ROOT)
    parser.add_argument("--reference-config", type=Path, default=DEFAULT_REFERENCE_CONFIG)
    parser.add_argument("--deck-candidate-id", required=True)
    parser.add_argument("--deck-candidate-main", type=Path, required=True)
    parser.add_argument("--deck-candidate-deck", type=Path, required=True)
    parser.add_argument("--native-control-id", default="native-control")
    parser.add_argument("--native-control-main", type=Path, required=True)
    parser.add_argument("--native-control-deck", type=Path, required=True)
    parser.add_argument("--policy-candidate-id", required=True)
    parser.add_argument("--policy-candidate-main", type=Path, required=True)
    parser.add_argument("--policy-candidate-deck", type=Path, required=True)
    parser.add_argument("--policy-control-id", default="policy-control")
    parser.add_argument("--policy-control-main", type=Path, required=True)
    parser.add_argument("--policy-control-deck", type=Path, required=True)
    parser.add_argument("--policy-env-json", default="{}")
    parser.add_argument("--policy-biases-json", default="{}")
    parser.add_argument("--policy-min-score-gain", type=float, default=0.0)
    parser.add_argument("--stage-games", type=int, choices=(96, 384, 768, 1536), default=96)
    parser.add_argument("--base-seed", type=int, required=True)
    parser.add_argument("--block-id", default="outcome-only-alternating-loop-v1")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS_V1)
    parser.add_argument("--worker-recycle-games", type=int, default=DEFAULT_WORKER_RECYCLE_GAMES_V1)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)

    policy_env = _json_object(args.policy_env_json, "--policy-env-json")
    policy_biases = _json_object(args.policy_biases_json, "--policy-biases-json")
    empty_env: dict[str, object] = {}
    empty_biases: dict[str, object] = {}
    native_control = _spec(
        candidate_id=args.native_control_id,
        main_path=args.native_control_main,
        deck_path=args.native_control_deck,
        env=empty_env,
        biases=empty_biases,
        min_score_gain=0.0,
    )
    deck_candidate = _spec(
        candidate_id=args.deck_candidate_id,
        main_path=args.native_control_main,
        deck_path=args.deck_candidate_deck,
        env=empty_env,
        biases=empty_biases,
        min_score_gain=0.0,
    )
    policy_control = _spec(
        candidate_id=args.policy_control_id,
        main_path=args.policy_control_main,
        deck_path=args.policy_control_deck,
        env=empty_env,
        biases=empty_biases,
        min_score_gain=0.0,
    )
    policy_candidate = _spec(
        candidate_id=args.policy_candidate_id,
        main_path=args.policy_candidate_main,
        deck_path=args.policy_candidate_deck,
        env=policy_env,
        biases=policy_biases,
        min_score_gain=args.policy_min_score_gain,
    )
    try:
        config = json.loads(args.reference_config.read_text(encoding="utf-8"))
        reference_ids = tuple(str(item) for item in config["opponent_ids"])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise SystemExit(f"reference config must expose opponent_ids: {exc}") from exc
    result = run_alternating_iteration_v1(
        deck_candidate=deck_candidate,
        native_control=native_control,
        policy_candidate=policy_candidate,
        policy_control=policy_control,
        pool_root=args.pool_root.resolve(),
        reference_ids=reference_ids,
        output_root=args.output_root.resolve(),
        base_seed=args.base_seed,
        stage_games=args.stage_games,
        block_id=args.block_id,
        execute=bool(args.execute),
        workers=args.workers,
        worker_recycle_games=args.worker_recycle_games,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
