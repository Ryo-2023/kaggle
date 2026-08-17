#!/usr/bin/env python3
"""Run a bounded production-shape learner soak on a verified frozen replay."""
from __future__ import annotations

import argparse
from dataclasses import asdict, replace
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from scripts.policy_learning.run_r2d3_multiseed_psro_performance import (  # noqa: E402
    Controller,
    PROFILES,
    TerminalProgress,
    atomic_json,
    digest,
    sha,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--replay-artifact", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--updates", type=int, default=200)
    parser.add_argument("--batch-size", type=int, default=2048)
    parser.add_argument("--hidden-size", type=int, default=256)
    parser.add_argument("--core", choices=("gru", "lru"), default="gru")
    parser.add_argument("--demo-ratio", type=float, default=1 / 32)
    parser.add_argument("--seed", type=int, default=890000)
    parser.add_argument("--gpu-id", default="0")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument(
        "--deck-pool",
        type=Path,
        default=ROOT / "data/opponent_deck_pool_20260730/opponent_deck_pool.json",
    )
    parser.add_argument(
        "--progress-mode", choices=("auto", "bar", "summary", "quiet"), default="auto"
    )
    args = parser.parse_args(argv)
    if args.updates < 2 or args.batch_size < 1 or args.hidden_size < 8:
        raise ValueError("invalid learner soak configuration")
    os.environ["CUDA_VISIBLE_DEVICES"] = str(args.gpu_id)

    replay_artifact = args.replay_artifact.resolve()
    artifact = args.artifact_root.resolve()
    required = (
        replay_artifact / "replay.json",
        replay_artifact / "replay_manifest.json",
        replay_artifact / "source_identity.json",
    )
    if any(not path.is_file() for path in required):
        raise RuntimeError("replay artifact is incomplete")
    if artifact.exists() and not args.resume:
        raise RuntimeError("soak artifact exists; use --resume")
    artifact.mkdir(parents=True, exist_ok=True)

    controller_args = argparse.Namespace(
        profile="production",
        artifact_root=artifact,
        run_root=artifact / "controller",
        replay_input_artifact=None,
        deck_pool=args.deck_pool.resolve(),
        progress_mode=args.progress_mode,
        rebaseline_source_identity=False,
        resume=args.resume,
    )
    controller = Controller(controller_args)
    source = controller.identity()
    replay_source = json.loads((replay_artifact / "source_identity.json").read_text())
    replay_manifest = json.loads((replay_artifact / "replay_manifest.json").read_text())
    if replay_source.get("semantic_feature_version") != source["semantic_feature_version"]:
        raise RuntimeError("replay semantic feature version differs")
    if replay_manifest.get("replay_sha256") != sha(replay_artifact / "replay.json"):
        raise RuntimeError("replay artifact checksum differs")

    controller.profile = replace(
        PROFILES["production"],
        model_hidden_size=args.hidden_size,
        training_log_interval=max(1, min(10, args.updates // 20)),
    )
    controller.artifact = artifact
    controller.context = {
        "profile": asdict(controller.profile),
        "profile_hash": digest(asdict(controller.profile)),
        "source": source,
        "replay_identity": replay_manifest["replay_sha256"],
    }
    from mage_ptcg.policy_learning.r2d3.replay import PrioritizedSequenceReplay

    controller.context["replay"] = PrioritizedSequenceReplay.load(replay_artifact / "replay.json")
    controller.context["replay_manifest"] = replay_manifest
    controller.monitor = TerminalProgress(args.progress_mode)
    controller.started = time.monotonic()
    controller._active_stage = "learner_soak"
    selected = {
        "status": "PASS",
        "batch_size": min(args.batch_size, len(controller.context["replay"])),
        "measurement": "explicit_soak_batch",
    }
    stage = controller.stage_dir("learner_scale_benchmark")
    atomic_json(stage / "output_manifest.json", {"status": "PASS", "selected": selected})

    identity = {
        "schema": "r2d3-learner-soak-v1",
        "replay_artifact": str(replay_artifact),
        "replay_hash": replay_manifest["replay_sha256"],
        "semantic_feature_version": source["semantic_feature_version"],
        "source_patch_hash": source["source_patch_hash"],
        "population_hash": source["population_hash"],
        "updates": args.updates,
        "batch_size": selected["batch_size"],
        "hidden_size": args.hidden_size,
        "core": args.core,
        "demo_ratio": args.demo_ratio,
        "seed": args.seed,
    }
    identity_path = artifact / "soak_identity.json"
    if identity_path.exists() and json.loads(identity_path.read_text()) != identity:
        raise RuntimeError("soak resume identity differs")
    atomic_json(identity_path, identity, durable=True)
    try:
        result = controller.train(
            name="learner-soak",
            core=args.core,
            demo_ratio=args.demo_ratio,
            updates=args.updates,
            seed=args.seed,
            resume_proof=True,
        )
        curve = result["curve"]
        atomic_json(
            artifact / "soak_result.json",
            {
                "status": "PASS",
                **identity,
                "checkpoint": result["checkpoint"],
                "checkpoint_hash": result["checkpoint_hash"],
                "resumed": result["resumed"],
                "metrics_samples": len(curve),
                "last_metrics": curve[-1] if curve else None,
            },
            durable=True,
        )
        return 0
    finally:
        controller.monitor.close()


if __name__ == "__main__":
    raise SystemExit(main())
