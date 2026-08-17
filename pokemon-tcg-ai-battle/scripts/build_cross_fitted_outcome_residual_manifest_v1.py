#!/usr/bin/env python3
"""Build sealed cross-fitted MC residual targets from one screen JSONL.

The input screen contains opponent/seat provenance because it was captured at
the actor boundary.  This builder may use those fields only to prove one
contiguous game topology.  They are never copied into the outcome manifest.
No model, trainer, actor pool, or CABT evaluator is imported.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Mapping

from mage_ptcg.meta_specialist.cross_fitted_outcome_residual_v1 import (
    CrossFittedOutcomeResidualError,
    OutcomeEpisodeV1,
    build_cross_fitted_outcome_manifest_v1,
)
from mage_ptcg.meta_specialist.dagger_v4 import parse_transition_payload_v4


SCREEN_SCHEMA_V1 = "meta-specialist-v4-dagger-transition-v1"
_SCREEN_KEYS = {
    "component_id", "env_seed", "episode_group", "game_id", "opponent_id", "partition",
    "schema", "seat", "transition", "transition_index",
}
_HEX64 = frozenset("0123456789abcdef")


def _sha(value: object, *, field: str) -> str:
    if type(value) is not str or len(value) != 64 or any(char not in _HEX64 for char in value):
        raise ValueError(f"{field} must be a lowercase SHA-256")
    return value


def _sha_file(path: Path) -> str:
    if not path.is_file() or path.is_symlink():
        raise ValueError("screen source must be a regular non-symlink file")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _atomic_json(path: Path, payload: Mapping[str, object]) -> str:
    raw = (json.dumps(payload, ensure_ascii=True, sort_keys=True, indent=2) + "\n").encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.tmp.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return digest


def _read_train_episodes(path: Path) -> tuple[OutcomeEpisodeV1, ...]:
    active_key: tuple[str, str, int] | None = None
    active_rows = []
    seen_keys: set[tuple[str, str, int]] = set()
    seen_game_ids: set[str] = set()
    episodes: list[OutcomeEpisodeV1] = []

    def close_active() -> None:
        nonlocal active_key, active_rows
        if active_key is None:
            return
        game_id, _opponent_id, _seat = active_key
        episodes.append(OutcomeEpisodeV1(episode_id=game_id, transitions=tuple(active_rows)))
        seen_keys.add(active_key)
        seen_game_ids.add(game_id)
        active_key, active_rows = None, []

    with path.open("rb") as handle:
        for line_number, raw in enumerate(handle, start=1):
            try:
                row = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError(f"screen line {line_number} is invalid JSON") from exc
            if type(row) is not dict or set(row) != _SCREEN_KEYS or row.get("schema") != SCREEN_SCHEMA_V1:
                raise ValueError(f"screen line {line_number} has an open schema")
            if row["partition"] != "train":
                continue
            game_id = _sha(row["game_id"], field="screen game_id")
            component_id = _sha(row["component_id"], field="screen component_id")
            episode_group = _sha(row["episode_group"], field="screen episode_group")
            if game_id != component_id or game_id != episode_group:
                raise ValueError(f"screen line {line_number} game/component topology differs")
            opponent_id = row["opponent_id"]
            seat = row["seat"]
            transition_index = row["transition_index"]
            if type(opponent_id) is not str or not opponent_id or type(seat) is not int or seat not in {0, 1}:
                raise ValueError(f"screen line {line_number} grouping provenance is invalid")
            if type(transition_index) is not int or transition_index < 0:
                raise ValueError(f"screen line {line_number} transition index is invalid")
            key = (game_id, opponent_id, seat)
            if active_key != key:
                close_active()
                if key in seen_keys or game_id in seen_game_ids:
                    raise ValueError(f"screen line {line_number} has game reentry")
                active_key = key
            if transition_index != len(active_rows):
                raise ValueError(f"screen line {line_number} transition order is not contiguous")
            try:
                transition = parse_transition_payload_v4(row["transition"])
            except (AttributeError, KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"screen line {line_number} has an invalid sealed transition") from exc
            active_rows.append(transition)
    close_active()
    if not episodes:
        raise ValueError("screen has no train episodes")
    return tuple(episodes)


def build_manifest_from_screen_jsonl_v1(
    source: Path,
    *,
    output: Path,
    fold_count: int = 2,
    advantage_clip: float = 1.0,
) -> dict[str, object]:
    """Publish an anonymous research target manifest and return a public summary."""
    source_sha = _sha_file(source)
    episodes = _read_train_episodes(source)
    manifest = build_cross_fitted_outcome_manifest_v1(
        episodes, fold_count=fold_count, advantage_clip=advantage_clip,
    )
    payload = manifest.to_dict()
    manifest_sha = _atomic_json(output, payload)
    returns = [item.return_value for item in manifest.episodes]
    return {
        "schema_version": "meta-specialist-cross-fitted-outcome-builder-v1",
        "source_file_sha256": source_sha,
        "output_file_sha256": manifest_sha,
        "episodes": len(manifest.episodes),
        "transitions": sum(len(item.targets) for item in manifest.episodes),
        "return_distribution": {
            "negative": sum(value < 0.0 for value in returns),
            "zero": sum(value == 0.0 for value in returns),
            "positive": sum(value > 0.0 for value in returns),
            "minimum": min(returns),
            "maximum": max(returns),
        },
        "target_kind": "signed_behavior_log_probability",
        "research_only": True,
        "promotion_authority": False,
        "longrun_allowed": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--screen-transitions", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--fold-count", type=int, default=2)
    parser.add_argument("--advantage-clip", type=float, default=1.0)
    args = parser.parse_args(argv)
    try:
        summary = build_manifest_from_screen_jsonl_v1(
            args.screen_transitions, output=args.output,
            fold_count=args.fold_count, advantage_clip=args.advantage_clip,
        )
    except (CrossFittedOutcomeResidualError, OSError, TypeError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(summary, ensure_ascii=True, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
