#!/usr/bin/env python3
"""Run one bounded, research-only signed-outcome residual update.

The command is deliberately unavailable without ``--execute``.  It reads one
hash-bound preflight and one sealed cross-fitted target manifest, materializes
at most two complete episodes, and trains only a fresh residual sidecar.  It
does not invoke production policy code, CABT, an actor pool, or evaluation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import torch

from mage_ptcg.meta_specialist.cross_fitted_outcome_materializer_v1 import (
    CrossFittedOutcomeMaterializerError,
    materialize_signed_outcome_targets_v1,
)
from mage_ptcg.meta_specialist.frozen_residual_preflight_v1 import (
    FrozenResidualPreflightError,
    SeedKnownDomainManifestV1,
    load_frozen_residual_preflight_manifest_v1,
)
from mage_ptcg.meta_specialist.frozen_residual_trainer_v1 import (
    FrozenResidualTrainerError,
    load_wave6_base_from_provenance_v1,
    residual_sidecar_tensor_state_sha256_v1,
)
from mage_ptcg.meta_specialist.frozen_residual_v1 import FrozenResidualSidecarV1
from mage_ptcg.meta_specialist.signed_residual_trainer_v1 import (
    SignedResidualTrainerError,
    train_signed_outcome_materialization_v1,
)


RUNNER_SCHEMA_V1 = "specialist-signed-outcome-residual-tiny-run-v1"
_EVIDENCE_CLASS = "SELF_SIGNED_OUTCOME_INTEGRATION_ONLY"


def _file_sha(path: Path) -> str:
    if not path.is_file() or path.is_symlink():
        raise SignedResidualTrainerError("artifact must be a regular non-symlink file")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _domain_for_seed(manifest: object, seed: int) -> SeedKnownDomainManifestV1:
    seeds = getattr(manifest, "seeds", None)
    if type(seed) is not int or seed not in {0, 1} or type(seeds) is not tuple:
        raise SignedResidualTrainerError("requested preflight seed is invalid")
    domain = next((item for item in seeds if item.provenance.seed == seed), None)
    if type(domain) is not SeedKnownDomainManifestV1:
        raise SignedResidualTrainerError("requested seed is absent from the preflight")
    return domain


def _sidecar_for_domain(domain: SeedKnownDomainManifestV1) -> FrozenResidualSidecarV1:
    return FrozenResidualSidecarV1(
        state_feature_dim=16,
        action_feature_dim=8,
        hidden_dim=32,
        max_abs_residual=0.25,
        known_context_ids=domain.context_ids,
        known_action_keys=domain.action_keys,
        base_checkpoint_file_sha256=domain.provenance.checkpoint_file_sha256,
        base_checkpoint_tensor_sha256=domain.provenance.checkpoint_tensor_state_sha256,
    )


def run_seed(
    *,
    preflight_path: Path,
    preflight_sha256: str,
    seed: int,
    outcome_target_manifest_path: Path,
    outcome_target_manifest_sha256: str,
    max_episodes: int,
    max_updates: int,
    output_dir: Path,
    device: str = "cpu",
) -> dict[str, object]:
    """Execute exactly one closed bounded sidecar update and write artifacts."""
    if type(max_episodes) is not int or not 1 <= max_episodes <= 2:
        raise SignedResidualTrainerError("max_episodes must be explicitly bounded in [1, 2]")
    if type(max_updates) is not int or max_updates != 1:
        raise SignedResidualTrainerError("max_updates must be exactly 1 for the tiny signed runner")
    preflight = load_frozen_residual_preflight_manifest_v1(
        preflight_path, expected_sha256=preflight_sha256, verify_files=True,
    )
    domain = _domain_for_seed(preflight, seed)
    materialization = materialize_signed_outcome_targets_v1(
        outcome_target_manifest_path,
        expected_manifest_sha256=outcome_target_manifest_sha256,
        known_domain=domain,
        max_episodes=max_episodes,
    )
    base_file_before = _file_sha(Path(domain.provenance.checkpoint_path))
    if base_file_before != domain.provenance.checkpoint_file_sha256:
        raise SignedResidualTrainerError("base checkpoint file SHA differs from preflight provenance")
    base = load_wave6_base_from_provenance_v1(domain.provenance, device=device)
    sidecar = _sidecar_for_domain(domain).to(device)
    initial_sidecar_tensor_sha = residual_sidecar_tensor_state_sha256_v1(sidecar)
    result = train_signed_outcome_materialization_v1(
        base,
        domain.provenance,
        sidecar,
        materialization,
        known_domain=domain,
        max_updates=max_updates,
    )
    base_file_after = _file_sha(Path(domain.provenance.checkpoint_path))
    if base_file_after != base_file_before:
        raise SignedResidualTrainerError("base checkpoint file changed during signed sidecar update")
    output_dir.mkdir(parents=True, exist_ok=True)
    sidecar_path = output_dir / f"seed-{seed}-signed-residual-sidecar.pt"
    torch.save({
        "schema_version": "specialist-signed-outcome-residual-sidecar-v1",
        "base_checkpoint_file_sha256": domain.provenance.checkpoint_file_sha256,
        "base_checkpoint_tensor_state_sha256": domain.provenance.checkpoint_tensor_state_sha256,
        "target_kind": "signed_behavior_log_probability",
        "target_manifest_file_sha256": materialization.target_manifest_file_sha256,
        "source_episode_sha256": materialization.source_episode_sha256,
        "state_dict": sidecar.state_dict(),
        "training_permitted": False,
        "promotion_authority": False,
        "longrun_allowed": False,
    }, sidecar_path)
    sidecar_file_sha = _file_sha(sidecar_path)
    payload = {
        "schema_version": RUNNER_SCHEMA_V1,
        "execution": "EXECUTED_BOUNDED_RESEARCH_TINY",
        "evidence_class": _EVIDENCE_CLASS,
        "performance_evidence": False,
        "seed": seed,
        "max_episodes": max_episodes,
        "max_updates": max_updates,
        "target_kind": result.target_kind,
        "preflight_manifest_file_sha256": preflight_sha256,
        "target_manifest_file_sha256": result.target_manifest_file_sha256,
        "source_transitions_file_sha256": result.source_transitions_file_sha256,
        "source_episode_sha256": result.source_episode_sha256,
        "base_checkpoint_file_sha256_before": base_file_before,
        "base_checkpoint_file_sha256_after": base_file_after,
        "base_checkpoint_tensor_state_sha256_before": result.base_tensor_state_sha256_before,
        "base_checkpoint_tensor_state_sha256_after": result.base_tensor_state_sha256_after,
        "sidecar_path": str(sidecar_path),
        "sidecar_file_sha256": sidecar_file_sha,
        "sidecar_initial_tensor_state_sha256": initial_sidecar_tensor_sha,
        "sidecar_tensor_state_sha256": residual_sidecar_tensor_state_sha256_v1(sidecar),
        "optimizer_updates": result.optimizer_updates,
        "context_only_rows": result.context_only_rows,
        "signed_loss_rows": result.signed_loss_rows,
        "zero_weight_rows": result.zero_weight_rows,
        "positive_effective_mass": result.positive_effective_mass,
        "negative_effective_mass": result.negative_effective_mass,
        "loss_normalizer": result.loss_normalizer,
        "signed_behavior_loss": result.signed_behavior_loss,
        "anchor_kl": result.anchor_kl,
        "residual_l2": result.residual_l2,
        "training_permitted": False,
        "promotion_authority": False,
        "longrun_allowed": False,
        "cabt_permitted": False,
    }
    report_path = output_dir / f"seed-{seed}-signed-tiny-report.json"
    report_path.write_text(json.dumps(payload, ensure_ascii=True, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preflight", type=Path, required=True)
    parser.add_argument("--preflight-sha256", required=True)
    parser.add_argument("--seed", type=int, choices=(0, 1), required=True)
    parser.add_argument("--outcome-target-manifest", type=Path, required=True)
    parser.add_argument("--outcome-target-manifest-sha256", required=True)
    parser.add_argument("--max-episodes", type=int)
    parser.add_argument("--max-updates", type=int, default=1)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    if not args.execute:
        parser.error("signed residual tiny execution is disabled; pass --execute explicitly")
    if args.max_episodes is None:
        parser.error("--max-episodes is required explicitly; default selection is forbidden")
    try:
        payload = run_seed(
            preflight_path=args.preflight,
            preflight_sha256=args.preflight_sha256,
            seed=args.seed,
            outcome_target_manifest_path=args.outcome_target_manifest,
            outcome_target_manifest_sha256=args.outcome_target_manifest_sha256,
            max_episodes=args.max_episodes,
            max_updates=args.max_updates,
            output_dir=args.output_dir,
            device=args.device,
        )
    except (
        CrossFittedOutcomeMaterializerError,
        FrozenResidualPreflightError,
        FrozenResidualTrainerError,
        SignedResidualTrainerError,
        OSError,
        RuntimeError,
        TypeError,
        ValueError,
    ) as exc:
        parser.error(str(exc))
    print(json.dumps(payload, ensure_ascii=True, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
