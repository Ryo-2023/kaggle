"""Run exactly one real CABT actor-pool game with a closed V4 checkpoint.

This is deliberately a smoke runner, not a Kaggle submission path.  It
requires both checkpoint bindings on the command line so a local file's
current contents cannot silently replace the intended V4 artifact.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(_ROOT), str(_ROOT / "src")]

from mage_ptcg.meta_specialist.actor_pool_v1 import (
    ActorJobConfigV1,
    current_repo_commit_v1,
    derive_actor_job_id_v1,
    run_one_actor_game_v1,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", required=True, help="Closed SpecialistModelV4 checkpoint path.")
    parser.add_argument("--checkpoint-file-sha256", required=True)
    parser.add_argument("--checkpoint-tensor-state-sha256", required=True)
    parser.add_argument("--deck-csv", required=True)
    parser.add_argument("--archetype-id", required=True)
    parser.add_argument("--opponent-kind", default="cabt_rule_agent_v0")
    parser.add_argument("--env-seed", type=int, default=20260810)
    parser.add_argument("--seat", type=int, choices=(0, 1), default=0)
    parser.add_argument("--decoding-mode", choices=("greedy", "sample"), default="greedy")
    parser.add_argument("--sampling-seed", type=int, default=0)
    parser.add_argument("--max-steps", type=int, default=4000)
    parser.add_argument("--timeout-seconds", type=float, default=600.0)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    file_sha = args.checkpoint_file_sha256
    source_commit = current_repo_commit_v1()
    job = ActorJobConfigV1(
        job_id=derive_actor_job_id_v1(
            archetype_id=args.archetype_id, deck_csv_path=args.deck_csv, source_commit=source_commit,
            env_seed=args.env_seed, seat=args.seat, behavior_kind="neural_specialist_v4",
            behavior_identity=file_sha, opponent_kind=args.opponent_kind,
            decoding_mode=args.decoding_mode, sampling_seed=args.sampling_seed,
        ),
        archetype_id=args.archetype_id, deck_csv_path=args.deck_csv, source_commit=source_commit,
        env_seed=args.env_seed, seat=args.seat, behavior_kind="neural_specialist_v4",
        behavior_identity=file_sha, opponent_kind=args.opponent_kind,
        neural_checkpoint_path=args.checkpoint,
        neural_checkpoint_file_sha256=file_sha,
        neural_checkpoint_tensor_state_sha256=args.checkpoint_tensor_state_sha256,
        decoding_mode=args.decoding_mode, sampling_seed=args.sampling_seed,
        max_steps=args.max_steps, timeout_seconds=args.timeout_seconds,
    )
    result = run_one_actor_game_v1(job=job, output_dir=args.output_dir)
    print(json.dumps({
        "status": result.status,
        "job_id": result.job_id,
        "outcome": result.outcome,
        "winner": result.winner,
        "steps": result.steps,
        "transitions": len(result.transitions),
        "fault": None if result.fault is None else {"kind": result.fault.kind, "detail": result.fault.detail},
        "engine_output_dir": str(Path(args.output_dir)),
    }, ensure_ascii=False, sort_keys=True))
    return 0 if result.status == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
