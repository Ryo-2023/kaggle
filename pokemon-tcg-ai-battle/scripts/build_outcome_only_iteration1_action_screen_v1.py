#!/usr/bin/env python3
"""Materialize one bounded non-ATTACK iteration-1 action screen."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

_ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(_ROOT), str(_ROOT / "src")]

from mage_ptcg.meta_specialist.outcome_only_iteration1_action_screen_v1 import (  # noqa: E402
    build_outcome_only_iteration1_action_screen_v1,
)


def _write_new(path: Path, value: object) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError as exc:
            raise FileExistsError(f"refusing to replace existing action screen: {path}") from exc
    finally:
        temporary.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=_ROOT)
    parser.add_argument("--schedule", type=Path, required=True)
    parser.add_argument("--candidate-id", required=True)
    parser.add_argument("--action-deltas-json", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    deltas = json.loads(args.action_deltas_json)
    if type(deltas) is not dict:
        raise ValueError("--action-deltas-json must be an object")
    artifact = build_outcome_only_iteration1_action_screen_v1(
        repo_root=args.repo_root,
        schedule_path=args.schedule,
        candidate_id=args.candidate_id,
        action_deltas=deltas,
    )
    _write_new(args.output, artifact["manifest"])
    games_path = args.output.with_name(f"{args.output.stem}.games.json")
    _write_new(
        games_path,
        {
            "schema_version": artifact["manifest"]["schema_version"],
            "screen_sha256": artifact["manifest"]["screen_sha256"],
            "execution_allowed": False,
            "control_games": [game.to_payload() for game in artifact["control_games"]],
            "candidate_games": [game.to_payload() for game in artifact["candidate_games"]],
        },
    )
    print(json.dumps({"manifest": str(args.output.resolve()), "games": str(games_path.resolve()), "screen_sha256": artifact["manifest"]["screen_sha256"], "slot_count": 96, "execution_allowed": False}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
