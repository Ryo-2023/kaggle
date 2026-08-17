#!/usr/bin/env python3
"""Run a bounded full-screen signed residual training arm.

This is research-only: the Wave6 base remains frozen and only the residual
sidecar is updated.  It uses the sealed cross-fitted target and writes a new
artifact; it never changes a production checkpoint or invokes CABT.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import torch

from mage_ptcg.meta_specialist.cross_fitted_outcome_materializer_v1 import materialize_signed_outcome_targets_v1
from mage_ptcg.meta_specialist.frozen_residual_preflight_v1 import load_frozen_residual_preflight_manifest_v1
from mage_ptcg.meta_specialist.frozen_residual_trainer_v1 import (
    load_wave6_base_from_provenance_v1,
    residual_sidecar_tensor_state_sha256_v1,
)
from mage_ptcg.meta_specialist.frozen_residual_v1 import FrozenResidualSidecarV1
from mage_ptcg.meta_specialist.signed_residual_trainer_v1 import train_signed_outcome_materialization_v1


SCHEMA = "specialist-signed-outcome-residual-full-run-v1"


def _sha(path: Path) -> str:
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"not a regular file: {path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _domain(preflight, seed: int):
    rows = [item for item in preflight.seeds if item.provenance.seed == seed]
    if len(rows) != 1:
        raise ValueError("preflight seed is not unique")
    return rows[0]


def run(args: argparse.Namespace) -> dict[str, object]:
    if not args.execute:
        raise ValueError("full signed residual run requires --execute")
    if args.max_episodes <= 2:
        raise ValueError("use the tiny runner for max_episodes <= 2")
    if args.max_episodes > 100 or args.max_updates > 100:
        raise ValueError("full research run is bounded at 100 episodes/updates")
    preflight_sha = _sha(args.preflight)
    if preflight_sha != args.preflight_sha256:
        raise ValueError("preflight SHA mismatch")
    preflight = load_frozen_residual_preflight_manifest_v1(args.preflight, expected_sha256=preflight_sha, verify_files=True)
    domain = _domain(preflight, args.seed)
    target_sha = _sha(args.target_manifest)
    if target_sha != args.target_manifest_sha256:
        raise ValueError("target manifest SHA mismatch")
    checkpoint = Path(domain.provenance.checkpoint_path)
    if _sha(checkpoint) != domain.provenance.checkpoint_file_sha256:
        raise ValueError("base checkpoint SHA mismatch")
    materialization = materialize_signed_outcome_targets_v1(
        args.target_manifest, expected_manifest_sha256=target_sha,
        known_domain=domain, max_episodes=args.max_episodes,
    )
    base = load_wave6_base_from_provenance_v1(domain.provenance, device=args.device)
    sidecar = FrozenResidualSidecarV1(
        known_context_ids=domain.context_ids, known_action_keys=domain.action_keys,
        base_checkpoint_file_sha256=domain.provenance.checkpoint_file_sha256,
        base_checkpoint_tensor_sha256=domain.provenance.checkpoint_tensor_state_sha256,
    ).to(args.device)
    base_before = domain.provenance.checkpoint_tensor_state_sha256
    result = train_signed_outcome_materialization_v1(
        base, domain.provenance, sidecar, materialization, known_domain=domain,
        max_updates=args.max_updates, learning_rate=args.learning_rate,
    )
    if _sha(checkpoint) != domain.provenance.checkpoint_file_sha256:
        raise ValueError("base checkpoint changed")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    sidecar_path = args.output_dir / f"seed-{args.seed}-signed-residual-full-sidecar.pt"
    torch.save({
        "schema_version": "specialist-signed-outcome-residual-sidecar-v1",
        "base_checkpoint_file_sha256": domain.provenance.checkpoint_file_sha256,
        "base_checkpoint_tensor_state_sha256": domain.provenance.checkpoint_tensor_state_sha256,
        "target_kind": result.target_kind,
        "target_manifest_file_sha256": result.target_manifest_file_sha256,
        "source_episode_sha256": result.source_episode_sha256,
        "state_dict": sidecar.state_dict(),
        "training_permitted": False, "promotion_authority": False, "longrun_allowed": False,
    }, sidecar_path)
    report = {
        "schema_version": SCHEMA, "execution": "EXECUTED_BOUNDED_FULL_SCREEN",
        "evidence_class": "SIGNED_OUTCOME_RESIDUAL_RESEARCH_ONLY",
        "performance_evidence": False, "training_permitted": False,
        "promotion_authority": False, "longrun_allowed": False, "cabt_permitted": False,
        "seed": args.seed, "max_episodes": args.max_episodes, "max_updates": args.max_updates,
        "sequence_count": len(materialization.sequences), "prefix_rows": len(materialization.prefix_targets),
        "signed_loss_rows": result.signed_loss_rows,
        "positive_effective_mass": result.positive_effective_mass,
        "negative_effective_mass": result.negative_effective_mass,
        "zero_weight_rows": result.zero_weight_rows,
        "loss_normalizer": result.loss_normalizer,
        "signed_behavior_loss": result.signed_behavior_loss,
        "optimizer_updates": result.optimizer_updates,
        "preflight_sha256": preflight_sha, "target_manifest_sha256": target_sha,
        "base_checkpoint_file_sha256": domain.provenance.checkpoint_file_sha256,
        "base_checkpoint_tensor_state_sha256_before": base_before,
        "base_checkpoint_tensor_state_sha256_after": result.base_tensor_state_sha256_after,
        "base_checkpoint_sha256_unchanged": result.base_tensor_state_sha256_after == base_before,
        "sidecar_path": str(sidecar_path), "sidecar_file_sha256": _sha(sidecar_path),
        "sidecar_tensor_state_sha256": residual_sidecar_tensor_state_sha256_v1(sidecar),
    }
    report_path = args.output_dir / f"seed-{args.seed}-signed-residual-full-report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preflight", type=Path, required=True)
    parser.add_argument("--preflight-sha256", required=True)
    parser.add_argument("--seed", type=int, choices=(0, 1), required=True)
    parser.add_argument("--target-manifest", type=Path, required=True)
    parser.add_argument("--target-manifest-sha256", required=True)
    parser.add_argument("--max-episodes", type=int, required=True)
    parser.add_argument("--max-updates", type=int, required=True)
    parser.add_argument("--learning-rate", type=float, default=1.0e-3)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    report = run(args)
    print(json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
