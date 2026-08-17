#!/usr/bin/env python3
"""Run one strictly verified 48-game weighted action screen."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Mapping, Sequence

_ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(_ROOT), str(_ROOT / "src")]

from mage_ptcg.meta_specialist.outcome_only_weighted_action_screen_v1 import (  # noqa: E402
    OutcomeOnlyWeightedActionScreenError,
    verify_outcome_only_weighted_action_screen_v1,
)
from scripts.parallel_cabt_evaluator_v1 import (  # noqa: E402
    _game_from_payload,
    run_parallel_cabt_evaluation,
)


def _load(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise OutcomeOnlyWeightedActionScreenError(f"invalid JSON artifact: {path}") from exc


def _games(manifest: Mapping[str, object], path: Path):
    sidecar = _load(path)
    if type(sidecar) is not dict or set(sidecar) != {
        "schema_version", "screen_sha256", "execution_allowed", "control_games", "candidate_games",
    }:
        raise OutcomeOnlyWeightedActionScreenError("weighted sidecar schema is not closed")
    if (
        sidecar["schema_version"] != manifest["schema_version"]
        or sidecar["screen_sha256"] != manifest["screen_sha256"]
        or sidecar["execution_allowed"] is not False
    ):
        raise OutcomeOnlyWeightedActionScreenError("weighted sidecar identity/authority mismatch")
    control = tuple(_game_from_payload(item) for item in sidecar["control_games"])
    candidate = tuple(_game_from_payload(item) for item in sidecar["candidate_games"])
    if len(control) != 48 or len(candidate) != 48:
        raise OutcomeOnlyWeightedActionScreenError("weighted screen requires exactly 48 games per arm")
    control_keys = tuple(
        (game.opponent_id, game.seat, game.seed, game.metadata.get("repetition"), game.metadata.get("stratum_key"))
        for game in control
    )
    candidate_keys = tuple(
        (game.opponent_id, game.seat, game.seed, game.metadata.get("repetition"), game.metadata.get("stratum_key"))
        for game in candidate
    )
    if control_keys != candidate_keys:
        raise OutcomeOnlyWeightedActionScreenError("weighted candidate/control strata mismatch")
    if len({game.game_id for game in control + candidate}) != 96:
        raise OutcomeOnlyWeightedActionScreenError("weighted game IDs are not unique")
    for game in control + candidate:
        if (
            game.metadata.get("screen_sha256") != manifest["screen_sha256"]
            or game.metadata.get("bridge_sha256") != manifest["bridge_sha256"]
            or game.metadata.get("heldout_exposure") != 0
            or game.metadata.get("weighted_slot_count") != 48
        ):
            raise OutcomeOnlyWeightedActionScreenError("weighted game metadata identity/heldout mismatch")
        if game.metadata.get("opponent_usage_boundary") != "local_eval_only" or game.metadata.get("synthetic_opponent") is not False:
            raise OutcomeOnlyWeightedActionScreenError("weighted game permission boundary mismatch")
        if game.policy_sha256 not in {manifest["candidate_policy_sha256"], manifest["control_policy_sha256"]}:
            raise OutcomeOnlyWeightedActionScreenError("weighted game policy identity mismatch")
    return control, candidate


def run_weighted_action_screen(*, screen: Path, games: Path, output: Path, workers: int = 12, recycle: int = 16):
    manifest = _load(screen)
    if type(manifest) is not dict:
        raise OutcomeOnlyWeightedActionScreenError("weighted manifest must be an object")
    verify_outcome_only_weighted_action_screen_v1(manifest, repo_root=_ROOT)
    control, candidate = _games(manifest, games)
    result = run_parallel_cabt_evaluation(
        control + candidate,
        output_dir=output,
        max_workers=workers,
        worker_recycle_games=recycle,
        overwrite=False,
    )
    result_path = output.parent / "run-result.json"
    result_path.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2, default=str) + "\n", encoding="utf-8")
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--screen", type=Path, required=True)
    parser.add_argument("--games", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--worker-recycle-games", type=int, default=16)
    args = parser.parse_args(argv)
    result = run_weighted_action_screen(
        screen=args.screen.resolve(), games=args.games.resolve(), output=args.output.resolve(),
        workers=args.workers, recycle=args.worker_recycle_games,
    )
    print(json.dumps(result.get("summary", result), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["main", "run_weighted_action_screen"]
