"""Run a matched research-only CABT screen for a self-owned deck package.

The candidate and control are both packaged ``root_cg`` arms.  Games use the
same opponent, seat, repetition, and seed strata; the package deck/policy
identities remain explicit in every row.  This bridge never changes the
repository BestKnown/Champion and never submits to Kaggle.
"""

from __future__ import annotations

from dataclasses import replace
import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Callable, Iterable, Mapping, Sequence

_ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(_ROOT), str(_ROOT / "src")]

from mage_ptcg.meta_specialist.self_owned_cg_deck_v1 import (  # noqa: E402
    canonical_deck_sha256_v1,
)
from mage_ptcg.meta_specialist.self_owned_cg_package_v1 import (  # noqa: E402
    SelfOwnedCgPackageV1Error,
    verify_self_owned_cg_package_v1,
)
from scripts.parallel_cabt_evaluator_v1 import (  # noqa: E402
    DEFAULT_MAX_WORKERS_V1,
    DEFAULT_WORKER_RECYCLE_GAMES_V1,
    EvaluationGameV1,
    evaluation_implementation_sha256_v1,
    run_parallel_cabt_evaluation,
)
from scripts.run_root_cg_candidate_arena_v1 import (  # noqa: E402
    ArenaArm,
    RootCgArenaError,
    _aggregate,
    _build_games,
    _sha256,
)


SCHEMA = "self-owned-cg-deck-screen-v1"
RUNNER_REF = "scripts.run_root_cg_candidate_arena_v1:run_root_cg_game_v1"
AUTHORITY_FALSE = {
    "training_allowed": False,
    "promotion_allowed": False,
    "submission_allowed": False,
    "longrun_allowed": False,
}
DEFAULT_CONFIG = _ROOT / "configs/meta_specialist/performance_first_broad_pool_v1.json"
DEFAULT_POOL = _ROOT / "opponents"


class SelfOwnedCgDeckScreenV1Error(ValueError):
    """Raised when a matched deck screen cannot be bound safely."""


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _semantic_sha(payload: Mapping[str, object]) -> str:
    return _sha256_bytes(_canonical_json(payload))


def _write_json(path: Path, payload: Mapping[str, object]) -> None:
    try:
        with path.open("xb") as handle:
            handle.write(_canonical_json(payload) + b"\n")
            handle.flush()
    except FileExistsError:
        raise
    except OSError as exc:
        raise SelfOwnedCgDeckScreenV1Error(f"cannot write screen artifact: {path}") from exc


def _read_refs(config_path: Path) -> tuple[str, ...]:
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SelfOwnedCgDeckScreenV1Error(f"cannot read opponent config: {config_path}") from exc
    refs = payload.get("opponent_ids") if isinstance(payload, Mapping) else None
    if not isinstance(refs, list) or not refs or len(refs) != len(set(refs)):
        raise SelfOwnedCgDeckScreenV1Error("opponent config must contain unique opponent_ids")
    return tuple(str(ref) for ref in refs)


def _deck_identity(
    package_root: Path,
    *,
    require_self_owned_manifest: bool = False,
) -> tuple[str, str, str, str | None]:
    main = package_root / "main.py"
    deck = package_root / "deck.csv"
    policy_sha = _sha256(main)
    deck_sha = _sha256(deck)
    try:
        values = tuple(int(token) for token in deck.read_bytes().decode("utf-8").split())
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise SelfOwnedCgDeckScreenV1Error(f"package deck is not integer UTF-8: {deck}") from exc
    if len(values) != 60 or any(value <= 0 for value in values):
        raise SelfOwnedCgDeckScreenV1Error(f"package deck must contain 60 positive IDs: {deck}")
    canonical = canonical_deck_sha256_v1(values)
    package_manifest_path = package_root / "self_owned_cg_package_manifest.json"
    manifest_sha: str | None = None
    if require_self_owned_manifest and not package_manifest_path.is_file():
        raise SelfOwnedCgDeckScreenV1Error(
            f"candidate package has no self-owned identity manifest: {package_root}"
        )
    if package_manifest_path.is_file():
        manifest = verify_self_owned_cg_package_v1(package_root)
        manifest_sha = str(manifest["manifest_sha256"])
        if manifest.get("policy_sha256") != policy_sha or manifest.get("deck_file_sha256") != deck_sha:
            raise SelfOwnedCgDeckScreenV1Error("self-owned package manifest identity mismatch")
    return policy_sha, deck_sha, canonical, manifest_sha


def _decorate_games(
    games: Sequence[EvaluationGameV1],
    *,
    screen_arm: str,
    package_root: Path,
    policy_sha: str,
    deck_sha: str,
    canonical_deck_sha: str,
    package_manifest_sha: str | None,
) -> tuple[EvaluationGameV1, ...]:
    decorated: list[EvaluationGameV1] = []
    for game in games:
        metadata = dict(game.metadata)
        metadata.update(
            {
                "screen_schema_version": SCHEMA,
                "screen_arm": screen_arm,
                "package_root": str(package_root),
                "package_policy_sha256": policy_sha,
                "package_deck_sha256": deck_sha,
                "deck_sha256": deck_sha,
                "canonical_deck_sha256": canonical_deck_sha,
                "package_manifest_sha256": package_manifest_sha,
                "authority": dict(AUTHORITY_FALSE),
            }
        )
        decorated.append(replace(game, metadata=metadata))
    return tuple(decorated)


def _screen_aggregate(rows: Sequence[Mapping[str, object]], arm_id: str) -> dict[str, object]:
    scoped = [row for row in rows if row.get("metadata", {}).get("arm_id") == arm_id]
    result = _aggregate(scoped)
    result["arm_id"] = arm_id
    return result


def run_screen_v1(
    *,
    candidate_package: str | Path,
    control_package: str | Path,
    output: str | Path,
    pool_root: str | Path = DEFAULT_POOL,
    refs: Iterable[str] = (),
    base_seed: int = 2026081601,
    games_per_opponent_seat: int = 1,
    workers: int = DEFAULT_MAX_WORKERS_V1,
    worker_recycle_games: int = DEFAULT_WORKER_RECYCLE_GAMES_V1,
    execute: bool = False,
    evaluator: Callable[..., Mapping[str, object]] | None = None,
) -> dict[str, object]:
    """Build and optionally execute a matched candidate/control deck screen."""
    if not execute:
        return {"status": "BLOCKED_EXECUTE_REQUIRED", "research_only": True}
    candidate_root = Path(candidate_package).resolve()
    control_root = Path(control_package).resolve()
    output_root = Path(output).resolve()
    if output_root.exists():
        raise FileExistsError(f"output root already exists: {output_root}")
    ref_tuple = tuple(str(ref) for ref in refs)
    if not ref_tuple or len(set(ref_tuple)) != len(ref_tuple):
        raise SelfOwnedCgDeckScreenV1Error("refs must be a non-empty unique sequence")
    if type(base_seed) is not int or base_seed < 0:
        raise SelfOwnedCgDeckScreenV1Error("base_seed must be a non-negative integer")
    if type(games_per_opponent_seat) is not int or games_per_opponent_seat <= 0:
        raise SelfOwnedCgDeckScreenV1Error("games_per_opponent_seat must be positive")
    candidate_policy_sha, candidate_deck_sha, candidate_canonical, candidate_manifest_sha = _deck_identity(
        candidate_root,
        require_self_owned_manifest=True,
    )
    control_policy_sha, control_deck_sha, control_canonical, control_manifest_sha = _deck_identity(control_root)
    pool_path = Path(pool_root).resolve()
    if not pool_path.is_dir():
        raise SelfOwnedCgDeckScreenV1Error(f"pool root is not a directory: {pool_path}")
    candidate_arm = ArenaArm(
        arm_id="self_owned_candidate_v1",
        policy_id="self-owned-cg-candidate-v1",
        policy_sha256=candidate_policy_sha,
        arm_kind="root_cg",
        candidate_package_root=candidate_root,
    )
    control_arm = ArenaArm(
        arm_id="self_owned_control_v1",
        policy_id="self-owned-cg-control-v1",
        policy_sha256=control_policy_sha,
        arm_kind="root_cg",
        candidate_package_root=control_root,
    )
    candidate_games = _decorate_games(
        _build_games(
            arm=candidate_arm,
            refs=ref_tuple,
            pool_root=pool_path,
            base_seed=base_seed,
            games_per_opponent_seat=games_per_opponent_seat,
            block_id=f"{SCHEMA}-candidate-{base_seed}",
        ),
        screen_arm="candidate",
        package_root=candidate_root,
        policy_sha=candidate_policy_sha,
        deck_sha=candidate_deck_sha,
        canonical_deck_sha=candidate_canonical,
        package_manifest_sha=candidate_manifest_sha,
    )
    control_games = _decorate_games(
        _build_games(
            arm=control_arm,
            refs=ref_tuple,
            pool_root=pool_path,
            base_seed=base_seed,
            games_per_opponent_seat=games_per_opponent_seat,
            block_id=f"{SCHEMA}-control-{base_seed}",
        ),
        screen_arm="control",
        package_root=control_root,
        policy_sha=control_policy_sha,
        deck_sha=control_deck_sha,
        canonical_deck_sha=control_canonical,
        package_manifest_sha=control_manifest_sha,
    )
    games = candidate_games + control_games
    output_root.mkdir(parents=True, exist_ok=False)
    manifest: dict[str, object] = {
        "schema_version": SCHEMA,
        "status": "EXECUTING",
        "research_only": True,
        "authority": dict(AUTHORITY_FALSE),
        "candidate_package": str(candidate_root),
        "control_package": str(control_root),
        "candidate_policy_sha256": candidate_policy_sha,
        "control_policy_sha256": control_policy_sha,
        "candidate_deck_sha256": candidate_deck_sha,
        "control_deck_sha256": control_deck_sha,
        "candidate_canonical_deck_sha256": candidate_canonical,
        "control_canonical_deck_sha256": control_canonical,
        "reference_ids": list(ref_tuple),
        "pool_root": str(pool_path),
        "base_seed": base_seed,
        "games_per_opponent_seat": games_per_opponent_seat,
        "requested_games": len(games),
        "workers": workers,
        "worker_recycle_games": worker_recycle_games,
        "evaluator_implementation_sha256": evaluation_implementation_sha256_v1(),
    }
    _write_json(output_root / "manifest.json", manifest)
    evaluate = evaluator or run_parallel_cabt_evaluation
    evaluation = evaluate(
        games,
        output_dir=output_root / "evaluation",
        max_workers=workers,
        worker_recycle_games=worker_recycle_games,
        overwrite=False,
    )
    rows = evaluation.get("rows")
    if not isinstance(rows, Sequence):
        raise SelfOwnedCgDeckScreenV1Error("evaluator returned no rows")
    by_arm = {
        candidate_arm.arm_id: _screen_aggregate(rows, candidate_arm.arm_id),
        control_arm.arm_id: _screen_aggregate(rows, control_arm.arm_id),
    }
    candidate_score = float(by_arm[candidate_arm.arm_id].get("score_rate") or 0.0)
    control_score = float(by_arm[control_arm.arm_id].get("score_rate") or 0.0)
    summary: dict[str, object] = {
        "schema_version": SCHEMA,
        "status": "COMPLETE",
        "research_only": True,
        "authority": dict(AUTHORITY_FALSE),
        "arms": by_arm,
        "candidate_delta_points": (candidate_score - control_score) * 100.0,
        "requested_games": len(games),
        "evaluator_summary": evaluation.get("summary", {}),
        "deck_difference": candidate_canonical != control_canonical,
    }
    _write_json(output_root / "summary.json", summary)
    manifest.update(
        {
            "status": "COMPLETE",
            "summary_sha256": _sha256_bytes((output_root / "summary.json").read_bytes()),
        }
    )
    _write_json(output_root / "manifest-complete.json", manifest)
    return {"status": "COMPLETE", "summary": summary, "output_root": str(output_root)}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="enable CABT execution")
    parser.add_argument("--candidate-package", type=Path, required=True)
    parser.add_argument("--control-package", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--pool-root", type=Path, default=DEFAULT_POOL)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--opponent-id", action="append", default=[])
    parser.add_argument("--base-seed", type=int, default=2026081601)
    parser.add_argument("--games-per-opponent-seat", type=int, default=1)
    parser.add_argument("--workers", type=int, default=DEFAULT_MAX_WORKERS_V1)
    parser.add_argument("--worker-recycle-games", type=int, default=DEFAULT_WORKER_RECYCLE_GAMES_V1)
    args = parser.parse_args(argv)
    if not args.execute:
        print(json.dumps({"status": "BLOCKED_EXECUTE_REQUIRED", "research_only": True}, ensure_ascii=False))
        return 2
    try:
        refs = tuple(args.opponent_id) if args.opponent_id else _read_refs(args.config)
        result = run_screen_v1(
            candidate_package=args.candidate_package,
            control_package=args.control_package,
            output=args.output,
            pool_root=args.pool_root,
            refs=refs,
            base_seed=args.base_seed,
            games_per_opponent_seat=args.games_per_opponent_seat,
            workers=args.workers,
            worker_recycle_games=args.worker_recycle_games,
            execute=True,
        )
    except (SelfOwnedCgDeckScreenV1Error, SelfOwnedCgPackageV1Error, RootCgArenaError, OSError, ValueError) as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc), "research_only": True}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
