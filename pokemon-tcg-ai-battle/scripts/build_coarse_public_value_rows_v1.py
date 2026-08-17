#!/usr/bin/env python3
"""Materialize sealed V4 complete-action rows for a coarse residual pilot.

The script replays one seed's actor-visible train screen with the matching
Wave6 checkpoint.  It emits detached base semantic/STOP logits and a signed
public-state-value target for each physical record prefix.  It is research
only: no optimizer, actor pool, CABT, or runtime policy is imported.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from mage_ptcg.meta_specialist.coarse_public_residual_gate_v1 import semantic_action_key_v1  # noqa: E402
from mage_ptcg.meta_specialist.coarse_record_residual_trainer_v1 import CoarsePrefixLogitRowV1  # noqa: E402
from mage_ptcg.meta_specialist.cross_fitted_public_state_value_v1 import (  # noqa: E402
    PublicStateValueError,
    load_cross_fitted_public_state_value_manifest_v1,
)
from mage_ptcg.meta_specialist.dagger_v4 import parse_transition_payload_v4  # noqa: E402
from mage_ptcg.meta_specialist.frozen_residual_v1 import STOP_ACTION_KEY_V1  # noqa: E402
from mage_ptcg.meta_specialist.neural_model_v4 import (  # noqa: E402
    SpecialistModelV4,
    load_specialist_checkpoint_v4,
)
from mage_ptcg.meta_specialist.public_confidence_ood_v1 import _bucket_id  # noqa: E402
from mage_ptcg.meta_specialist.representation_v4 import representation_v4_from_step_input_v1  # noqa: E402
from scripts.build_cross_fitted_outcome_residual_manifest_v1 import _read_train_episodes, _sha_file  # noqa: E402


SCHEMA = "specialist-coarse-public-value-logit-rows-v1"
SCREEN_SCHEMA = "meta-specialist-v4-dagger-transition-v1"
_HEX64 = frozenset("0123456789abcdef")


def _sha(value: object, field: str) -> str:
    if type(value) is not str or len(value) != 64 or any(char not in _HEX64 for char in value):
        raise ValueError(f"{field} must be lowercase SHA-256")
    return value


def _target_index(prefix: object) -> int:
    if prefix.chosen_is_stop:
        if not prefix.step_input.stop_available:
            raise ValueError("chosen STOP is outside the legal domain")
        return len(prefix.step_input.allowed_semantic_classes)
    chosen = prefix.chosen_semantic_action
    if chosen is None:
        raise ValueError("chosen semantic target is missing")
    matches = [
        index for index, candidate in enumerate(prefix.step_input.allowed_semantic_classes)
        if candidate.semantic_row == chosen
    ]
    if len(matches) != 1:
        raise ValueError("chosen semantic target is not uniquely aligned")
    return matches[0]


def _action_keys(prefix: object) -> tuple[str, ...]:
    keys = tuple(semantic_action_key_v1(candidate.semantic_row) for candidate in prefix.step_input.allowed_semantic_classes)
    if prefix.step_input.stop_available:
        keys += (STOP_ACTION_KEY_V1,)
    if len(set(keys)) != len(keys):
        raise ValueError("semantic action keys contain duplicates")
    return keys


def _logits(model: SpecialistModelV4, output: object, prefix: object) -> tuple[float, ...]:
    values = [float(value) for value in output.logits.detach().cpu().tolist()]
    if prefix.step_input.stop_available:
        stop = (model.stop_vector @ output.global_token + model.stop_bias).detach().cpu().item()
        values.append(float(stop))
    if not values or any(not torch.isfinite(torch.tensor(value)) for value in values):
        raise ValueError("replayed base logits are nonfinite or empty")
    return tuple(values)


def _load_value_manifest(path: Path, expected_sha256: str):
    actual = _sha_file(path)
    if actual != _sha(expected_sha256, "value manifest SHA"):
        raise ValueError("value manifest SHA mismatch")
    try:
        return load_cross_fitted_public_state_value_manifest_v1(
            json.loads(path.read_text(encoding="utf-8")),
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, PublicStateValueError, TypeError, ValueError) as exc:
        raise ValueError("value manifest is not a closed public-state-value manifest") from exc


def build_rows_v1(
    *,
    screen_path: Path,
    value_manifest_path: Path,
    value_manifest_sha256: str,
    checkpoint_path: Path,
    checkpoint_file_sha256: str,
    checkpoint_tensor_state_sha256: str,
    seed: int,
    card_vocabulary_size: int = 1267,
    hidden_dim: int = 128,
    embedding_dim: int = 64,
    torch_threads: int = 2,
) -> dict[str, Any]:
    if seed not in {0, 1}:
        raise ValueError("seed must be 0 or 1")
    source_sha = _sha_file(screen_path)
    value_manifest = _load_value_manifest(value_manifest_path, value_manifest_sha256)
    checkpoint_file_sha256 = _sha(checkpoint_file_sha256, "checkpoint file SHA")
    checkpoint_tensor_state_sha256 = _sha(checkpoint_tensor_state_sha256, "checkpoint tensor SHA")
    if _sha_file(checkpoint_path) != checkpoint_file_sha256:
        raise ValueError("checkpoint file SHA mismatch")
    episodes = _read_train_episodes(screen_path)
    if value_manifest.source_episode_sha256 != _episode_source_sha(episodes):
        raise ValueError("value manifest source episode SHA differs from screen")
    by_episode = {episode.episode_id: episode for episode in value_manifest.episodes}
    if set(by_episode) != {episode.episode_id for episode in episodes}:
        raise ValueError("value manifest episode set differs from screen")
    torch.set_num_threads(torch_threads)
    model = SpecialistModelV4(
        card_vocabulary_size=card_vocabulary_size,
        hidden_dim=hidden_dim,
        embedding_dim=embedding_dim,
    )
    load_specialist_checkpoint_v4(
        checkpoint_path,
        model,
        expected_file_sha256=checkpoint_file_sha256,
        expected_tensor_state_sha256=checkpoint_tensor_state_sha256,
    )
    model.eval()
    rows: list[CoarsePrefixLogitRowV1] = []
    episode_count = transition_count = 0
    public_bucket_counts: dict[str, int] = {}
    target_source_counts: dict[str, int] = {}
    with torch.inference_mode():
        for episode in sorted(episodes, key=lambda item: item.episode_id):
            manifest_episode = by_episode[episode.episode_id]
            if len(manifest_episode.targets) != len(episode.transitions):
                raise ValueError("value manifest transition count differs from screen")
            hidden = None
            for transition_index, transition in enumerate(episode.transitions):
                transition_sha = hashlib.sha256(
                    _canonical_transition_bytes(transition),
                ).hexdigest()
                target = manifest_episode.targets[transition_index]
                if target.transition_index != transition_index or target.transition_sha256 != transition_sha:
                    raise ValueError("value target transition SHA/index differs from screen")
                states = tuple(
                    representation_v4_from_step_input_v1(
                        transition.model_input, prefix.step_input, allow_unbound_selected=True,
                    )
                    for prefix in transition.prefix_steps
                )
                outputs = model.forward_record_group_v4(
                    states,
                    hidden_state=hidden,
                    episode_start=transition_index == 0,
                )
                hidden = outputs[-1].hidden_state.detach() if outputs[-1].hidden_state is not None else None
                for prefix_index, (prefix, output) in enumerate(zip(transition.prefix_steps, outputs, strict=True)):
                    effective_domain = len(prefix.step_input.allowed_semantic_classes) + int(prefix.step_input.stop_available)
                    bucket_id = _bucket_id(transition.model_input, prefix.step_input, effective_domain)
                    original_action_keys = _action_keys(prefix)
                    original_base_logits = _logits(model, output, prefix)
                    original_target_index = _target_index(prefix)
                    if original_target_index >= len(original_base_logits):
                        raise ValueError("chosen target is outside replayed logits")
                    target_key = original_action_keys[original_target_index]
                    sorted_pairs = tuple(sorted(zip(original_action_keys, original_base_logits, strict=True), key=lambda pair: pair[0]))
                    action_keys = tuple(pair[0] for pair in sorted_pairs)
                    base_logits = tuple(pair[1] for pair in sorted_pairs)
                    target_index = action_keys.index(target_key)
                    row = CoarsePrefixLogitRowV1(
                        episode_id=episode.episode_id,
                        record_id=transition_sha,
                        prefix_index=prefix_index,
                        bucket_id=bucket_id,
                        action_keys=action_keys,
                        base_logits=base_logits,
                        target_index=target_index,
                        signed_weight=float(target.signed_weight),
                    )
                    rows.append(row)
                    public_bucket_counts[bucket_id] = public_bucket_counts.get(bucket_id, 0) + 1
                    target_source_counts[target.baseline_source] = target_source_counts.get(target.baseline_source, 0) + 1
                transition_count += 1
            episode_count += 1
    payload_rows = []
    for row in rows:
        payload_rows.append({
            "episode_id": row.episode_id,
            "record_id": row.record_id,
            "prefix_index": row.prefix_index,
            "bucket_id": row.bucket_id,
            "action_keys": list(row.action_keys),
            "base_logits": list(row.base_logits),
            "target_index": row.target_index,
            "signed_weight": row.signed_weight,
        })
    return {
        "schema_version": SCHEMA,
        "seed": seed,
        "screen_file_sha256": source_sha,
        "value_manifest_file_sha256": _sha(value_manifest_sha256, "value manifest SHA"),
        "value_objective_kind": value_manifest.objective_kind,
        "value_feature_schema": value_manifest.value_feature_schema,
        "value_model_sha256": value_manifest.value_model_sha256,
        "value_ridge_lambda": value_manifest.ridge_lambda,
        "source_episode_sha256": value_manifest.source_episode_sha256,
        "checkpoint_file_sha256": checkpoint_file_sha256,
        "checkpoint_tensor_state_sha256": checkpoint_tensor_state_sha256,
        "episode_count": episode_count,
        "transition_count": transition_count,
        "prefix_row_count": len(rows),
        "public_bucket_count": len(public_bucket_counts),
        "public_bucket_counts": dict(sorted(public_bucket_counts.items())),
        "target_baseline_source_counts": dict(sorted(target_source_counts.items())),
        "target_kind": "signed_public_state_value_residual",
        "rows": payload_rows,
        "research_only": True,
        "training_permitted": False,
        "promotion_authority": False,
        "longrun_allowed": False,
        "performance_evidence": False,
    }


def _canonical_transition_bytes(transition: object) -> bytes:
    # The canonical helper is imported lazily to keep the script's public
    # imports small and to use the same digest contract as target builders.
    from mage_ptcg.meta_specialist.trajectory_v1 import canonical_actor_trajectory_transition_bytes_v1
    return canonical_actor_trajectory_transition_bytes_v1(transition)


def _episode_source_sha(episodes: object) -> str:
    from mage_ptcg.meta_specialist.cross_fitted_public_state_value_v1 import _episode_source_sha
    return _episode_source_sha(episodes)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--screen", type=Path, required=True)
    parser.add_argument("--value-manifest", type=Path, required=True)
    parser.add_argument("--value-manifest-sha256", required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--checkpoint-file-sha256", required=True)
    parser.add_argument("--checkpoint-tensor-state-sha256", required=True)
    parser.add_argument("--seed", type=int, choices=(0, 1), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--torch-threads", type=int, default=2)
    args = parser.parse_args(argv)
    payload = build_rows_v1(
        screen_path=args.screen,
        value_manifest_path=args.value_manifest,
        value_manifest_sha256=args.value_manifest_sha256,
        checkpoint_path=args.checkpoint,
        checkpoint_file_sha256=args.checkpoint_file_sha256,
        checkpoint_tensor_state_sha256=args.checkpoint_tensor_state_sha256,
        seed=args.seed,
        torch_threads=args.torch_threads,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=True, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: payload[key] for key in ("seed", "episode_count", "transition_count", "prefix_row_count", "public_bucket_count", "target_baseline_source_counts")}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
