#!/usr/bin/env python3
"""Run the strictly verified ATTACH+120 common24 evaluation guardrail."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Mapping, Sequence

_ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(_ROOT), str(_ROOT / "src")]

from mage_ptcg.meta_specialist.outcome_only_common24_guardrail_v1 import (  # noqa: E402
    OutcomeOnlyCommon24GuardrailError,
    verify_outcome_only_common24_guardrail_v1,
)
from scripts.parallel_cabt_evaluator_v1 import _game_from_payload, run_parallel_cabt_evaluation  # noqa: E402


def _load(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise OutcomeOnlyCommon24GuardrailError(f"invalid JSON artifact: {path}") from exc


def _games(manifest: Mapping[str, object], path: Path):
    sidecar = _load(path)
    if type(sidecar) is not dict or set(sidecar) != {"schema_version", "screen_sha256", "execution_allowed", "control_games", "candidate_games"}:
        raise OutcomeOnlyCommon24GuardrailError("common24 sidecar schema is not closed")
    if sidecar["schema_version"] != manifest["schema_version"] or sidecar["screen_sha256"] != manifest["screen_sha256"] or sidecar["execution_allowed"] is not False:
        raise OutcomeOnlyCommon24GuardrailError("common24 sidecar identity/authority mismatch")
    control = tuple(_game_from_payload(item) for item in sidecar["control_games"])
    candidate = tuple(_game_from_payload(item) for item in sidecar["candidate_games"])
    if len(control) != 96 or len(candidate) != 96:
        raise OutcomeOnlyCommon24GuardrailError("common24 guardrail requires exactly 96 games per arm")
    def key(game: object) -> tuple[object, ...]:
        return (game.opponent_id, game.seat, game.seed, game.metadata.get("repetition"), game.metadata.get("stratum_key"))
    if tuple(key(game) for game in control) != tuple(key(game) for game in candidate):
        raise OutcomeOnlyCommon24GuardrailError("common24 candidate/control strata mismatch")
    if len({game.game_id for game in control + candidate}) != 192:
        raise OutcomeOnlyCommon24GuardrailError("common24 game IDs are not unique")
    for game in control + candidate:
        metadata = game.metadata
        if metadata.get("screen_sha256") != manifest["screen_sha256"] or metadata.get("bridge_sha256") != manifest["bridge_sha256"] or metadata.get("heldout_training_exposure") != 0 or metadata.get("evaluation_only") is not True:
            raise OutcomeOnlyCommon24GuardrailError("common24 game metadata identity/exposure mismatch")
        if metadata.get("opponent_usage_boundary") != "local_eval_only" or metadata.get("synthetic_opponent") is not False:
            raise OutcomeOnlyCommon24GuardrailError("common24 game permission boundary mismatch")
        if game.policy_sha256 not in {manifest["candidate_policy_sha256"], manifest["control_policy_sha256"]}:
            raise OutcomeOnlyCommon24GuardrailError("common24 game policy identity mismatch")
    return control, candidate


def run_common24_guardrail(*, screen: Path, games: Path, output: Path, workers: int = 12, recycle: int = 16):
    manifest = _load(screen)
    if type(manifest) is not dict:
        raise OutcomeOnlyCommon24GuardrailError("common24 manifest must be an object")
    verify_outcome_only_common24_guardrail_v1(manifest, repo_root=_ROOT)
    control, candidate = _games(manifest, games)
    result = run_parallel_cabt_evaluation(control + candidate, output_dir=output, max_workers=workers, worker_recycle_games=recycle, overwrite=False)
    (output.parent / "run-result.json").write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2, default=str) + "\n", encoding="utf-8")
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--screen", type=Path, required=True)
    parser.add_argument("--games", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--worker-recycle-games", type=int, default=16)
    args = parser.parse_args(argv)
    result = run_common24_guardrail(screen=args.screen.resolve(), games=args.games.resolve(), output=args.output.resolve(), workers=args.workers, recycle=args.worker_recycle_games)
    print(json.dumps(result.get("summary", result), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["main", "run_common24_guardrail"]
