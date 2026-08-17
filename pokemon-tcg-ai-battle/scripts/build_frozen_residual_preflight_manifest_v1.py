#!/usr/bin/env python3
"""Build a hash-bound, diagnostic-only Wave6 residual known-domain manifest.

The builder reads sealed actor-visible transition JSONL and extracts only
public serial-free residual context/action hashes.  It never loads a model,
creates an optimizer, starts CABT, or calls a trainer.  The output is suitable
for the dry-run tiny-overfit descriptor, not a training permit.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Mapping

from mage_ptcg.meta_specialist.dagger_v4 import parse_transition_payload_v4
from mage_ptcg.meta_specialist.frozen_residual_v1 import STOP_ACTION_KEY_V1, build_residual_context_v1
from mage_ptcg.meta_specialist.frozen_residual_preflight_v1 import (
    FrozenResidualPreflightError,
    Wave6ProvenanceV1,
    build_frozen_residual_preflight_manifest_v1,
    build_seed_known_manifest_v1,
)


TRANSITION_SCHEMA_V1 = "meta-specialist-v4-dagger-transition-v1"
_TRANSITION_KEYS = {
    "component_id", "env_seed", "episode_group", "game_id", "opponent_id", "partition",
    "schema", "seat", "transition", "transition_index",
}


def sha256_file(path: Path) -> str:
    if not path.is_file() or path.is_symlink():
        raise FrozenResidualPreflightError(f"source must be a regular non-symlink file: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_run_manifest(path: Path, seed: int) -> Mapping[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FrozenResidualPreflightError("Wave6 run manifest is not readable JSON") from exc
    if type(value) is not dict or type(value.get("checkpoints")) is not dict:
        raise FrozenResidualPreflightError("Wave6 run manifest has no closed checkpoints mapping")
    checkpoint = value["checkpoints"].get(str(seed))
    if type(checkpoint) is not dict or set(checkpoint) != {"file_sha256", "path", "tensor_state_sha256"}:
        raise FrozenResidualPreflightError(f"Wave6 checkpoint provenance for seed{seed} is not closed")
    return checkpoint


def build_seed_domain(
    *,
    seed: int,
    screen_path: Path,
    transitions_path: Path,
    run_manifest_path: Path,
    subject_deck_sha256: str,
) -> object:
    checkpoint = _read_run_manifest(run_manifest_path, seed)
    checkpoint_path = Path(str(checkpoint["path"]))
    checkpoint_file_sha = str(checkpoint["file_sha256"])
    checkpoint_tensor_sha = str(checkpoint["tensor_state_sha256"])
    actual_checkpoint_sha = sha256_file(checkpoint_path)
    if actual_checkpoint_sha != checkpoint_file_sha:
        raise FrozenResidualPreflightError(f"seed{seed} checkpoint file SHA differs from run manifest")

    transition_sha = sha256_file(transitions_path)
    screen_sha = sha256_file(screen_path)
    provenance = Wave6ProvenanceV1(
        seed=seed,
        checkpoint_path=str(checkpoint_path),
        checkpoint_file_sha256=checkpoint_file_sha,
        checkpoint_tensor_state_sha256=checkpoint_tensor_sha,
        screen_path=str(screen_path),
        screen_file_sha256=screen_sha,
        transitions_path=str(transitions_path),
        transitions_file_sha256=transition_sha,
        subject_deck_sha256=subject_deck_sha256,
        partition="train",
    )
    contexts: set[str] = set()
    actions: set[str] = set()
    transition_count = 0
    prefix_count = 0
    previous_game: str | None = None
    previous_index = -1
    with transitions_path.open("rb") as handle:
        for line_no, raw in enumerate(handle, start=1):
            try:
                row = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise FrozenResidualPreflightError(f"transition line {line_no} is invalid JSON") from exc
            if type(row) is not dict or set(row) != _TRANSITION_KEYS or row.get("schema") != TRANSITION_SCHEMA_V1:
                raise FrozenResidualPreflightError(f"transition line {line_no} has an open schema")
            if row.get("partition") != "train":
                continue
            game_id = row.get("game_id")
            transition_index = row.get("transition_index")
            if type(game_id) is not str or type(transition_index) is not int or transition_index < 0:
                raise FrozenResidualPreflightError(f"transition line {line_no} identity is invalid")
            if game_id != previous_game:
                previous_game = game_id
                previous_index = -1
            if transition_index != previous_index + 1:
                raise FrozenResidualPreflightError(f"train transition order is not contiguous at line {line_no}")
            previous_index = transition_index
            try:
                transition = parse_transition_payload_v4(row["transition"])
            except (AttributeError, KeyError, TypeError, ValueError) as exc:
                raise FrozenResidualPreflightError(f"transition line {line_no} failed canonical parse") from exc
            transition_count += 1
            for prefix in transition.prefix_steps:
                context = build_residual_context_v1(transition.model_input, prefix.step_input)
                contexts.add(context.context_id)
                actions.update(context.action_keys)
                if context.stop_available:
                    actions.add(STOP_ACTION_KEY_V1)
                prefix_count += 1
    if transition_count < 1 or prefix_count < transition_count or not contexts or not actions:
        raise FrozenResidualPreflightError(f"seed{seed} train source has no usable residual domain")
    return build_seed_known_manifest_v1(
        provenance,
        context_ids=contexts,
        action_keys=actions,
        transition_count=transition_count,
        prefix_count=prefix_count,
    )


def build_manifest(
    *,
    run_manifest_path: Path,
    seed0_screen: Path,
    seed0_transitions: Path,
    seed1_screen: Path,
    seed1_transitions: Path,
    subject_deck_sha256: str,
) -> object:
    seeds = (
        build_seed_domain(
            seed=0, screen_path=seed0_screen, transitions_path=seed0_transitions,
            run_manifest_path=run_manifest_path, subject_deck_sha256=subject_deck_sha256,
        ),
        build_seed_domain(
            seed=1, screen_path=seed1_screen, transitions_path=seed1_transitions,
            run_manifest_path=run_manifest_path, subject_deck_sha256=subject_deck_sha256,
        ),
    )
    return build_frozen_residual_preflight_manifest_v1(
        seeds, subject_deck_sha256=subject_deck_sha256,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-manifest", type=Path, required=True)
    parser.add_argument("--seed0-screen", type=Path, required=True)
    parser.add_argument("--seed0-transitions", type=Path, required=True)
    parser.add_argument("--seed1-screen", type=Path, required=True)
    parser.add_argument("--seed1-transitions", type=Path, required=True)
    parser.add_argument("--subject-deck-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        manifest = build_manifest(
            run_manifest_path=args.run_manifest,
            seed0_screen=args.seed0_screen,
            seed0_transitions=args.seed0_transitions,
            seed1_screen=args.seed1_screen,
            seed1_transitions=args.seed1_transitions,
            subject_deck_sha256=args.subject_deck_sha256,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(manifest.to_dict(), ensure_ascii=True, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
    except (FrozenResidualPreflightError, OSError, TypeError, ValueError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
