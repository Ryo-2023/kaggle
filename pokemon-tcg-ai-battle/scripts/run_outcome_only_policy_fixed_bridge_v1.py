#!/usr/bin/env python3
"""Run one already-sealed policy-fixed bridge evaluation.

This is a research-only entrypoint.  It accepts a bridge manifest and its
game sidecar, re-verifies the manifest and pairing before starting the
existing evaluator, and never starts training, promotion, longrun, or
submission paths.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Sequence

_ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(_ROOT), str(_ROOT / "src")]

from mage_ptcg.meta_specialist.outcome_only_policy_fixed_bridge_v1 import (  # noqa: E402
    OutcomeOnlyPolicyFixedBridgeError,
    verify_policy_fixed_short_bridge_v1,
)
from scripts.parallel_cabt_evaluator_v1 import (  # noqa: E402
    _game_from_payload,
    run_parallel_cabt_evaluation,
)


def _load_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OutcomeOnlyPolicyFixedBridgeError(f"invalid JSON artifact: {path}") from exc


def _load_games(manifest: dict[str, object], sidecar_path: Path):
    sidecar = _load_json(sidecar_path)
    if type(sidecar) is not dict or set(sidecar) != {
        "schema_version", "bridge_sha256", "execution_allowed", "control_games", "candidate_games"
    }:
        raise OutcomeOnlyPolicyFixedBridgeError("bridge game sidecar schema is not closed")
    if sidecar["schema_version"] != manifest["schema_version"]:
        raise OutcomeOnlyPolicyFixedBridgeError("bridge sidecar schema mismatch")
    if sidecar["bridge_sha256"] != manifest["bridge_sha256"]:
        raise OutcomeOnlyPolicyFixedBridgeError("bridge sidecar SHA mismatch")
    if sidecar["execution_allowed"] is not False:
        raise OutcomeOnlyPolicyFixedBridgeError("bridge sidecar enables execution")
    control_payloads = sidecar["control_games"]
    candidate_payloads = sidecar["candidate_games"]
    if not isinstance(control_payloads, list) or not isinstance(candidate_payloads, list):
        raise OutcomeOnlyPolicyFixedBridgeError("bridge game sidecar arms are malformed")
    control = tuple(_game_from_payload(payload) for payload in control_payloads)
    candidate = tuple(_game_from_payload(payload) for payload in candidate_payloads)
    if len(control) != 96 or len(candidate) != 96:
        raise OutcomeOnlyPolicyFixedBridgeError("bridge requires exactly 96 games per arm")
    control_keys = tuple(
        (game.opponent_id, game.seat, game.metadata.get("repetition"), game.seed)
        for game in control
    )
    candidate_keys = tuple(
        (game.opponent_id, game.seat, game.metadata.get("repetition"), game.seed)
        for game in candidate
    )
    if control_keys != candidate_keys:
        raise OutcomeOnlyPolicyFixedBridgeError("bridge candidate/control strata mismatch")
    if len({game.game_id for game in control + candidate}) != 192:
        raise OutcomeOnlyPolicyFixedBridgeError("bridge game IDs are not unique")
    if any(game.metadata.get("heldout_exposure") != 0 for game in control + candidate):
        raise OutcomeOnlyPolicyFixedBridgeError("bridge sidecar contains heldout exposure")
    if any(game.metadata.get("bridge_sha256") != manifest["bridge_sha256"] for game in control + candidate):
        raise OutcomeOnlyPolicyFixedBridgeError("bridge game metadata SHA mismatch")
    return control, candidate


def run_bridge(
    *,
    bridge_path: Path,
    games_path: Path,
    output_dir: Path,
    max_workers: int,
    worker_recycle_games: int,
) -> dict[str, object]:
    manifest = _load_json(bridge_path)
    if type(manifest) is not dict:
        raise OutcomeOnlyPolicyFixedBridgeError("bridge manifest must be a JSON object")
    verify_policy_fixed_short_bridge_v1(manifest, repo_root=_ROOT)
    control, candidate = _load_games(manifest, games_path)
    result = run_parallel_cabt_evaluation(
        control + candidate,
        output_dir=output_dir,
        max_workers=max_workers,
        worker_recycle_games=worker_recycle_games,
        overwrite=False,
    )
    result_path = output_dir.parent / "run-result.json"
    result_path.write_text(
        json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bridge", type=Path, required=True)
    parser.add_argument("--games", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--worker-recycle-games", type=int, default=16)
    args = parser.parse_args(argv)
    result = run_bridge(
        bridge_path=args.bridge.resolve(),
        games_path=args.games.resolve(),
        output_dir=args.output.resolve(),
        max_workers=args.workers,
        worker_recycle_games=args.worker_recycle_games,
    )
    print(json.dumps(result.get("summary", result), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["main", "run_bridge"]
