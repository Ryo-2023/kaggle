#!/usr/bin/env python3
"""Run a strictly verified four-block policy-fixed confirmation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Sequence

_ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(_ROOT), str(_ROOT / "src")]

from mage_ptcg.meta_specialist.outcome_only_policy_fixed_confirmation_v1 import (  # noqa: E402
    OutcomeOnlyPolicyFixedConfirmationError,
    verify_policy_fixed_confirmation_v1,
)
from scripts.parallel_cabt_evaluator_v1 import _game_from_payload, run_parallel_cabt_evaluation  # noqa: E402


def _json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OutcomeOnlyPolicyFixedConfirmationError(f"invalid JSON: {path}") from exc


def run_confirmation(*, confirmation: Path, games: Path, output: Path, workers: int = 12, recycle: int = 16):
    manifest = _json(confirmation)
    if type(manifest) is not dict:
        raise OutcomeOnlyPolicyFixedConfirmationError("confirmation manifest must be an object")
    verify_policy_fixed_confirmation_v1(manifest, repo_root=_ROOT)
    sidecar = _json(games)
    if type(sidecar) is not dict or sidecar.get("schema_version") != manifest["schema_version"]:
        raise OutcomeOnlyPolicyFixedConfirmationError("confirmation sidecar schema mismatch")
    if sidecar.get("confirmation_sha256") != manifest["confirmation_sha256"] or sidecar.get("execution_allowed") is not False:
        raise OutcomeOnlyPolicyFixedConfirmationError("confirmation sidecar identity/authority mismatch")
    control_payloads = sidecar.get("control_games")
    candidate_payloads = sidecar.get("candidate_games")
    if not isinstance(control_payloads, list) or not isinstance(candidate_payloads, list):
        raise OutcomeOnlyPolicyFixedConfirmationError("confirmation sidecar arms malformed")
    control = tuple(_game_from_payload(item) for item in control_payloads)
    candidate = tuple(_game_from_payload(item) for item in candidate_payloads)
    if len(control) != 384 or len(candidate) != 384:
        raise OutcomeOnlyPolicyFixedConfirmationError("confirmation requires 384 games per arm")
    control_keys = tuple((g.opponent_id, g.seat, g.seed, g.metadata.get("repetition")) for g in control)
    candidate_keys = tuple((g.opponent_id, g.seat, g.seed, g.metadata.get("repetition")) for g in candidate)
    if control_keys != candidate_keys:
        raise OutcomeOnlyPolicyFixedConfirmationError("confirmation candidate/control strata mismatch")
    all_games = control + candidate
    if len({g.game_id for g in all_games}) != 768:
        raise OutcomeOnlyPolicyFixedConfirmationError("confirmation game IDs are not unique")
    if any(g.metadata.get("heldout_exposure") != 0 for g in all_games):
        raise OutcomeOnlyPolicyFixedConfirmationError("confirmation heldout exposure")
    result = run_parallel_cabt_evaluation(
        all_games,
        output_dir=output,
        max_workers=workers,
        worker_recycle_games=recycle,
        overwrite=False,
    )
    (output.parent / "run-result.json").write_text(
        json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirmation", type=Path, required=True)
    parser.add_argument("--games", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--worker-recycle-games", type=int, default=16)
    args = parser.parse_args(argv)
    result = run_confirmation(
        confirmation=args.confirmation.resolve(),
        games=args.games.resolve(),
        output=args.output.resolve(),
        workers=args.workers,
        recycle=args.worker_recycle_games,
    )
    print(json.dumps(result.get("summary", result), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["main", "run_confirmation"]
