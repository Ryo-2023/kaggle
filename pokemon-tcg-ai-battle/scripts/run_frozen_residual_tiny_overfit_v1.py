#!/usr/bin/env python3
"""Run a bounded research-only frozen residual tiny overfit.

Execution is disabled unless ``--execute`` is explicitly supplied.  Even with
that flag this runner only reads a small number of sealed train transitions,
uses the Rule teacher to create fixed targets, updates the residual sidecar
for a fixed number of sequence updates, and writes a non-promotable sidecar
artifact.  It never invokes CABT, the production actor pool, or the V4 BC
trainer.
"""

from __future__ import annotations

import argparse
from dataclasses import replace
import hashlib
import json
from pathlib import Path
from typing import Mapping

import torch

from mage_ptcg.meta_specialist.actor_pool_v1 import build_rule_agent_policy_factory_v1
from mage_ptcg.meta_specialist.dagger_v4 import (
    merge_dagger_episode_sequences_v4,
    parse_transition_payload_v4,
    relabel_transition_v4,
)
from mage_ptcg.meta_specialist.frozen_residual_preflight_v1 import (
    FrozenResidualPreflightManifestV1,
    FrozenResidualPreflightError,
    SeedKnownDomainManifestV1,
    load_frozen_residual_preflight_manifest_v1,
)
from mage_ptcg.meta_specialist.frozen_residual_trainer_v1 import (
    FrozenResidualTrainerError,
    TARGET_KIND_SELF_IMITATION_V1,
    build_residual_checkpoint_descriptor_v1,
    load_wave6_base_from_provenance_v1,
    residual_sidecar_tensor_state_sha256_v1,
    train_residual_sequences_v1,
)
from mage_ptcg.meta_specialist.frozen_residual_v1 import FrozenResidualSidecarV1
from mage_ptcg.meta_specialist.neural_model_v4 import SpecialistModelV4
from mage_ptcg.meta_specialist.recurrent_dataset_v4 import RecurrentBCSequenceV4


RUNNER_SCHEMA_V1 = "specialist-frozen-wave6-residual-tiny-run-v1"
TRANSITION_SCHEMA_V1 = "meta-specialist-v4-dagger-transition-v1"


def _sha(value: object, *, field: str) -> str:
    if type(value) is not str or len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise FrozenResidualTrainerError(f"{field} must be a lowercase SHA-256")
    return value


def _file_sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_train_rows(path: Path, *, expected_sha256: str) -> tuple[dict[str, object], ...]:
    if not path.is_file() or path.is_symlink():
        raise FrozenResidualTrainerError("sealed transition source is not a regular file")
    expected = _sha(expected_sha256, field="transitions_file_sha256")
    if _file_sha(path) != expected:
        raise FrozenResidualTrainerError("sealed transition source SHA differs from preflight provenance")
    rows: list[dict[str, object]] = []
    with path.open("rb") as handle:
        for line_no, raw in enumerate(handle, start=1):
            try:
                value = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise FrozenResidualTrainerError(f"transition line {line_no} is invalid JSON") from exc
            if type(value) is not dict or set(value) != {
                "component_id", "env_seed", "episode_group", "game_id", "opponent_id", "partition",
                "schema", "seat", "transition", "transition_index",
            } or value.get("schema") != TRANSITION_SCHEMA_V1:
                raise FrozenResidualTrainerError(f"transition line {line_no} has an open schema")
            if value.get("partition") == "train":
                rows.append(value)
    if not rows:
        raise FrozenResidualTrainerError("sealed transition source has no train rows")
    return tuple(rows)


def _bounded_teacher_sequences(
    rows: tuple[dict[str, object], ...],
    *,
    seed: int,
    max_prefixes: int,
) -> tuple[RecurrentBCSequenceV4, ...]:
    if type(max_prefixes) is not int or max_prefixes < 2:
        raise FrozenResidualTrainerError("max_prefixes must be at least 2")
    first_game = str(rows[0]["game_id"])
    game_rows = [row for row in rows if row["game_id"] == first_game]
    game_rows.sort(key=lambda row: int(row["transition_index"]))
    selected: list[dict[str, object]] = []
    prefix_count = 0
    for row in game_rows:
        transition = parse_transition_payload_v4(row["transition"])
        if selected and prefix_count + len(transition.prefix_steps) > max_prefixes:
            break
        selected.append(row)
        prefix_count += len(transition.prefix_steps)
        if prefix_count >= max_prefixes:
            break
    if not selected or prefix_count < 2:
        raise FrozenResidualTrainerError("bounded game contains too few prefix rows")
    teacher_factory, teacher_identity = build_rule_agent_policy_factory_v1()
    relabelled: list[RecurrentBCSequenceV4] = []
    for row in selected:
        transition = parse_transition_payload_v4(row["transition"])
        sequence = relabel_transition_v4(
            transition,
            teacher_factory=teacher_factory,
            policy_version=teacher_identity,
            lane="archaludon",
            episode_group=str(row["episode_group"]),
            component_id=str(row["component_id"]),
            partition="train",
        )
        # Singleton/forced domains are valid recurrent context but carry no
        # policy information.  Keep them in the forward sequence and remove
        # them from the residual denominator explicitly.
        relabelled.append(RecurrentBCSequenceV4(
            lane=sequence.lane,
            episode_group=sequence.episode_group,
            component_id=sequence.component_id,
            partition=sequence.partition,
            steps=tuple(
                replace(step, supervision_weight=0.0)
                if len(step.target_masses) <= 1 else step
                for step in sequence.steps
            ),
            burn_in=sequence.burn_in,
            research_only=True,
        ))
    # One selected game has one component/episode identity in the sealed
    # source; merge preserves complete-action recurrent order.
    return (merge_dagger_episode_sequences_v4(tuple(relabelled)),)


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
    manifest: object,
    *,
    manifest_sha256: str,
    seed: int,
    max_prefixes: int,
    max_updates: int,
    learning_rate: float,
    device: str,
    output_dir: Path,
) -> dict[str, object]:
    if type(manifest) is not FrozenResidualPreflightManifestV1:
        raise FrozenResidualTrainerError("runner requires typed preflight manifest")
    domain = next((item for item in manifest.seeds if item.provenance.seed == seed), None)
    if domain is None:
        raise FrozenResidualTrainerError("requested seed is absent from preflight manifest")
    rows = _read_train_rows(Path(domain.provenance.transitions_path), expected_sha256=domain.provenance.transitions_file_sha256)
    sequences = _bounded_teacher_sequences(rows, seed=seed, max_prefixes=max_prefixes)
    base_file_sha_before = _file_sha(Path(domain.provenance.checkpoint_path))
    if base_file_sha_before != domain.provenance.checkpoint_file_sha256:
        raise FrozenResidualTrainerError("base checkpoint SHA changed before residual run")
    base = load_wave6_base_from_provenance_v1(domain.provenance, device=device)
    sidecar = _sidecar_for_domain(domain)
    initial_sidecar_tensor_sha = residual_sidecar_tensor_state_sha256_v1(sidecar)
    result = train_residual_sequences_v1(
        base, sidecar, sequences, known_domain=domain,
        max_updates=max_updates, learning_rate=learning_rate,
    )
    # The base artifact is immutable: re-hash the exact file after training and
    # fail closed if any concurrent process changed it.  The sidecar is the only
    # intentionally mutable state and is written to this fresh research output.
    if _file_sha(Path(domain.provenance.checkpoint_path)) != base_file_sha_before:
        raise FrozenResidualTrainerError("base checkpoint SHA changed during residual run")
    if (
        sidecar.base_checkpoint_file_sha256 != domain.provenance.checkpoint_file_sha256
        or sidecar.base_checkpoint_tensor_sha256 != domain.provenance.checkpoint_tensor_state_sha256
    ):
        raise FrozenResidualTrainerError("sidecar base checkpoint binding changed during residual run")
    output_dir.mkdir(parents=True, exist_ok=True)
    sidecar_path = output_dir / f"seed-{seed}-residual-sidecar.pt"
    torch.save(
        {"schema_version": "specialist-frozen-wave6-residual-sidecar-checkpoint-v1", "state_dict": sidecar.state_dict()},
        sidecar_path,
    )
    sidecar_file_sha = _file_sha(sidecar_path)
    descriptor = build_residual_checkpoint_descriptor_v1(
        domain.provenance,
        sidecar,
        seed=seed,
        preflight_manifest_sha256=manifest_sha256,
        target_kind=TARGET_KIND_SELF_IMITATION_V1,
        target_manifest_sha256=domain.provenance.transitions_file_sha256,
        optimizer_updates=result.optimizer_updates,
        effective_loss_mass=result.effective_loss_mass,
    )
    payload = {
        "schema_version": RUNNER_SCHEMA_V1,
        "execution": "EXECUTED_BOUNDED_RESEARCH_TINY",
        "evidence_class": "SELF_IMITATION_INTEGRATION_ONLY",
        "performance_evidence": False,
        "teacher_target_source": "rule_teacher_relabel_research_only",
        "target_kind": TARGET_KIND_SELF_IMITATION_V1,
        "target_manifest_sha256": domain.provenance.transitions_file_sha256,
        "seed": seed,
        "max_prefixes": max_prefixes,
        "max_updates": max_updates,
        "device": device,
        "base_checkpoint_file_sha256": domain.provenance.checkpoint_file_sha256,
        "base_checkpoint_tensor_state_sha256": domain.provenance.checkpoint_tensor_state_sha256,
        "base_checkpoint_sha256_unchanged": True,
        "sidecar_base_checkpoint_binding_verified": True,
        "sidecar_path": str(sidecar_path),
        "sidecar_file_sha256": sidecar_file_sha,
        "sidecar_tensor_state_sha256": residual_sidecar_tensor_state_sha256_v1(sidecar),
        "training_result": {
            "optimizer_updates": result.optimizer_updates,
            "total_rows": result.total_rows,
            "context_only_rows": result.context_only_rows,
            "loss_bearing_rows": result.loss_bearing_rows,
            "denominator_rows": result.denominator_rows,
            "effective_loss_mass": result.effective_loss_mass,
            "imitation_loss": result.imitation_loss,
            "anchor_kl": result.anchor_kl,
            "residual_l2": result.residual_l2,
            "sidecar_parameter_count": result.sidecar_parameter_count,
            "initial_sidecar_tensor_state_sha256": initial_sidecar_tensor_sha,
            "final_sidecar_tensor_state_sha256": result.sidecar_tensor_state_sha256,
        },
        "checkpoint_descriptor": descriptor.to_dict(),
        "training_permitted": False,
        "cabt_permitted": False,
        "promotion_authority": False,
        "longrun_allowed": False,
    }
    (output_dir / f"seed-{seed}-tiny-report.json").write_text(
        json.dumps(payload, ensure_ascii=True, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--manifest-sha256")
    parser.add_argument("--seed", type=int, choices=(0, 1))
    parser.add_argument("--max-prefixes", type=int, default=64)
    parser.add_argument("--max-updates", type=int, default=1)
    parser.add_argument("--learning-rate", type=float, default=0.01)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    if not args.execute and not args.dry_run:
        parser.error("residual tiny execution is disabled; pass --execute explicitly (or --dry-run)")
    try:
        if args.dry_run:
            manifest = load_frozen_residual_preflight_manifest_v1(args.manifest)
            payload = {
                "schema_version": "specialist-frozen-wave6-residual-tiny-run-v1",
                "execution": "DRY_RUN_NOT_STARTED",
                "evidence_class": "SELF_IMITATION_INTEGRATION_ONLY",
                "performance_evidence": False,
                "teacher_target_source": "rule_teacher_relabel_research_only",
                "target_kind": TARGET_KIND_SELF_IMITATION_V1,
                "training_permitted": False,
                "cabt_permitted": False,
                "promotion_authority": False,
                "longrun_allowed": False,
                "optimizer_updates": 0,
                "epochs": 0,
                "seeds": [
                    {
                        "seed": item.provenance.seed,
                        "transition_count": item.transition_count,
                        "prefix_count": item.prefix_count,
                        "target_manifest_sha256": item.provenance.transitions_file_sha256,
                    }
                    for item in manifest.seeds
                ],
            }
            serialized = json.dumps(payload, ensure_ascii=True, sort_keys=True, indent=2) + "\n"
            if args.output is not None:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(serialized, encoding="utf-8")
            print(serialized, end="")
            return 0
        if args.manifest_sha256 is None or args.seed is None or args.output_dir is None:
            parser.error("--execute requires --manifest-sha256, --seed, and --output-dir")
        manifest = load_frozen_residual_preflight_manifest_v1(
            args.manifest, expected_sha256=args.manifest_sha256, verify_files=True,
        )
        payload = run_seed(
            manifest,
            manifest_sha256=args.manifest_sha256,
            seed=args.seed,
            max_prefixes=args.max_prefixes,
            max_updates=args.max_updates,
            learning_rate=args.learning_rate,
            device=args.device,
            output_dir=args.output_dir,
        )
        print(json.dumps(payload, ensure_ascii=True, sort_keys=True, indent=2))
    except (FrozenResidualPreflightError, FrozenResidualTrainerError, OSError, RuntimeError, TypeError, ValueError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
