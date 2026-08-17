#!/usr/bin/env python3
"""Execute one verified 48-game non-MAIN target screen."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

_ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(_ROOT), str(_ROOT / "src")]

from mage_ptcg.meta_specialist.non_main_target_overlay_v1 import (  # noqa: E402
    NonMainTargetOverlayError,
    verify_non_main_target_screen_v1,
)
from mage_ptcg.meta_specialist.resource_governor_v1 import (  # noqa: E402
    ResourceBudget,
    ResourceGovernor,
)
from scripts.parallel_cabt_evaluator_v1 import (  # noqa: E402
    _game_from_payload,
    run_parallel_cabt_evaluation,
)


def _load(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise NonMainTargetOverlayError(f"invalid JSON artifact: {path}") from exc


def _games(manifest: dict[str, object], path: Path):
    sidecar = _load(path)
    if type(sidecar) is not dict or set(sidecar) != {
        "schema_version", "screen_sha256", "execution_allowed", "control_games", "candidate_games",
    }:
        raise NonMainTargetOverlayError("target sidecar schema is not closed")
    if (
        sidecar["schema_version"] != manifest["schema_version"]
        or sidecar["screen_sha256"] != manifest["screen_sha256"]
        or sidecar["execution_allowed"] is not False
    ):
        raise NonMainTargetOverlayError("target sidecar identity/authority mismatch")
    control = tuple(_game_from_payload(item) for item in sidecar["control_games"])
    candidate = tuple(_game_from_payload(item) for item in sidecar["candidate_games"])
    if len(control) != 48 or len(candidate) != 48:
        raise NonMainTargetOverlayError("target screen requires exactly 48 games per arm")
    control_keys = tuple(
        (game.opponent_id, game.seat, game.seed, game.metadata.get("repetition"), game.metadata.get("stratum_key"))
        for game in control
    )
    candidate_keys = tuple(
        (game.opponent_id, game.seat, game.seed, game.metadata.get("repetition"), game.metadata.get("stratum_key"))
        for game in candidate
    )
    if control_keys != candidate_keys:
        raise NonMainTargetOverlayError("target candidate/control strata mismatch")
    if len({game.game_id for game in control + candidate}) != 96:
        raise NonMainTargetOverlayError("target game IDs are not unique")
    for game in control + candidate:
        if (
            game.metadata.get("schema_version") != manifest["schema_version"]
            or game.metadata.get("screen_sha256") != manifest["screen_sha256"]
            or game.metadata.get("bridge_sha256") != manifest["bridge_sha256"]
            or game.metadata.get("heldout_exposure") != 0
            or game.metadata.get("opponent_usage_boundary") != "local_eval_only"
            or game.metadata.get("synthetic_opponent") is not False
        ):
            raise NonMainTargetOverlayError("target game metadata/permission mismatch")
        if game.runner_ref != manifest["runner_ref"]:
            raise NonMainTargetOverlayError("target runner identity mismatch")
        if game.policy_sha256 not in {manifest["candidate_policy_sha256"], manifest["control_policy_sha256"]}:
            raise NonMainTargetOverlayError("target game policy identity mismatch")
    return control, candidate


def run_non_main_target_overlay_screen(
    *, screen: Path, games: Path, output: Path, workers: int = 12, recycle: int = 16,
) -> dict[str, object]:
    manifest = _load(screen)
    if type(manifest) is not dict:
        raise NonMainTargetOverlayError("target manifest must be an object")
    verify_non_main_target_screen_v1(manifest, repo_root=_ROOT)
    control, candidate = _games(manifest, games)
    budget_path = _ROOT / "configs/meta_specialist/resource_budget_v1.json"
    governor = ResourceGovernor(ResourceBudget.from_json(budget_path))
    decision = governor.decide(task_cap=workers, gpu_required=False)
    if decision.recommended_workers < 1:
        raise NonMainTargetOverlayError(
            f"ResourceGovernor denied evaluation: state={decision.state} reasons={decision.reasons}"
        )
    safe_workers = min(workers, decision.recommended_workers)
    telemetry_path = output.parent / "resource-governor.json"
    governor.write_telemetry(telemetry_path, task_cap=workers, gpu_required=False)
    result = run_parallel_cabt_evaluation(
        control + candidate,
        output_dir=output,
        max_workers=safe_workers,
        worker_recycle_games=recycle,
        overwrite=False,
    )
    result_path = output.parent / "run-result.json"
    result_path.write_text(
        json.dumps(
            {
                "schema_version": manifest["schema_version"],
                "screen_sha256": manifest["screen_sha256"],
                "candidate_policy_sha256": manifest["candidate_policy_sha256"],
                "control_policy_sha256": manifest["control_policy_sha256"],
                "resource_governor_safe_workers": safe_workers,
                "resource_governor_state": decision.state,
                "summary": result["summary"],
            },
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--screen", type=Path, required=True)
    parser.add_argument("--games", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--worker-recycle-games", type=int, default=16)
    args = parser.parse_args(argv)
    result = run_non_main_target_overlay_screen(
        screen=args.screen.resolve(),
        games=args.games.resolve(),
        output=args.output.resolve(),
        workers=args.workers,
        recycle=args.worker_recycle_games,
    )
    print(json.dumps(result.get("summary", result), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
