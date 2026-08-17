#!/usr/bin/env python3
"""Materialize a research-only policy-fixed bridge manifest.

This CLI only verifies and serializes a paired evaluation plan.  It never
starts the evaluator, training, promotion, submission, or long-run paths.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Mapping

_ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(_ROOT), str(_ROOT / "src")]

from mage_ptcg.meta_specialist.outcome_only_policy_fixed_bridge_v1 import (  # noqa: E402
    build_policy_fixed_short_bridge_v1,
)


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _write_new(path: Path, value: object) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = _canonical(value) + b"\n"
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError as exc:
            raise FileExistsError(f"refusing to replace existing bridge artifact: {path}") from exc
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _parse_deltas(raw: str) -> Mapping[str, object]:
    value = json.loads(raw)
    if type(value) is not dict:
        raise ValueError("--action-deltas-json must contain an object")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=_ROOT)
    parser.add_argument("--schedule", type=Path, required=True)
    parser.add_argument("--subject-deck", type=Path, required=True)
    parser.add_argument("--candidate-id", required=True)
    parser.add_argument("--action-deltas-json", default="{}")
    parser.add_argument("--pack", type=Path, default=None)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)

    artifact = build_policy_fixed_short_bridge_v1(
        repo_root=args.repo_root,
        schedule_path=args.schedule,
        subject_deck_path=args.subject_deck,
        candidate_id=args.candidate_id,
        action_deltas=_parse_deltas(args.action_deltas_json),
        pack_path=args.pack,
    )
    manifest_path = args.output
    _write_new(manifest_path, artifact["manifest"])
    game_path = manifest_path.with_name(f"{manifest_path.stem}.games.json")
    _write_new(
        game_path,
        {
            "schema_version": artifact["manifest"]["schema_version"],
            "bridge_sha256": artifact["manifest"]["bridge_sha256"],
            "execution_allowed": False,
            "control_games": [game.to_payload() for game in artifact["control_games"]],
            "candidate_games": [game.to_payload() for game in artifact["candidate_games"]],
        },
    )
    print(
        json.dumps(
            {
                "manifest": str(manifest_path.resolve()),
                "games": str(game_path.resolve()),
                "bridge_sha256": artifact["manifest"]["bridge_sha256"],
                "slot_count": artifact["manifest"]["schedule_summary"]["slot_count"],
                "ready_for_evaluation": artifact["manifest"]["ready_for_evaluation"],
                "execution_allowed": artifact["manifest"]["execution_allowed"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
