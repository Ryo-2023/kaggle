#!/usr/bin/env python3
"""Run the fault-free smoke gate for an independent self-owned source batch."""

from __future__ import annotations

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
from scripts.parallel_cabt_evaluator_v1 import (  # noqa: E402
    DEFAULT_MAX_WORKERS_V1,
    DEFAULT_WORKER_RECYCLE_GAMES_V1,
    EvaluationGameV1,
    evaluation_implementation_sha256_v1,
    run_parallel_cabt_evaluation,
)
from scripts.run_root_cg_candidate_arena_v1 import (  # noqa: E402
    AUTHORITY_FALSE,
    ArenaArm,
    _aggregate,
    _build_games,
    _sha256,
)


SCHEMA = "self-owned-cg-independent-source-smoke-v1"
DEFAULT_POOL = _ROOT / "opponents"
DEFAULT_CONFIG = _ROOT / "configs/meta_specialist/performance_first_broad_pool_v1.json"
PACKAGE_AUTHORITY_FALSE = {
    "training_allowed": False,
    "promotion_allowed": False,
    "submission_allowed": False,
}


class IndependentSourceSmokeError(ValueError):
    """Raised when a source batch cannot be evaluated fail-closed."""


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _read_refs(config_path: Path) -> tuple[str, ...]:
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise IndependentSourceSmokeError(f"cannot read opponent config: {config_path}") from exc
    refs = payload.get("opponent_ids") if isinstance(payload, Mapping) else None
    if not isinstance(refs, list) or not refs or len(refs) != len(set(refs)):
        raise IndependentSourceSmokeError("opponent config must contain unique opponent_ids")
    return tuple(str(ref) for ref in refs)


def _source_packages(staged_root: Path, candidate_root: Path | None = None) -> tuple[Path, ...]:
    root = Path(staged_root).resolve()
    if not root.is_dir():
        raise IndependentSourceSmokeError(f"staged root is not a directory: {root}")
    packages = tuple(
        sorted(
            path
            for path in root.iterdir()
            if path.is_dir()
            and (path / "main.py").is_file()
            and (path / "deck.csv").is_file()
            and ((path / "source_manifest.json").is_file()
                 or (path / "self_owned_cg_package_manifest.json").is_file())
        )
    )
    if not packages:
        raise IndependentSourceSmokeError("staged root has no complete source packages")
    for package in packages:
        # A staged meta source intentionally contains only main.py/deck.csv
        # and its provenance; the shared cg runtime is supplied by the local
        # evaluator.  A pre-staged package may retain cg/ and can use the
        # standard verifier.  In both cases recheck the copied identities.
        if (package / "self_owned_cg_package_manifest.json").is_file() and (package / "cg").is_dir():
            from mage_ptcg.meta_specialist.self_owned_cg_package_v1 import verify_self_owned_cg_package_v1

            verify_self_owned_cg_package_v1(package)
            continue
        try:
            manifest = json.loads((package / "self_owned_cg_package_manifest.json").read_text(encoding="utf-8"))
            main_bytes = (package / "main.py").read_bytes()
            deck_bytes = (package / "deck.csv").read_bytes()
            card_ids = tuple(int(token) for token in deck_bytes.decode("utf-8").split())
        except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
            raise IndependentSourceSmokeError(f"staged source identity is unreadable: {package}") from exc
        if not isinstance(manifest, Mapping):
            raise IndependentSourceSmokeError(f"staged source manifest is not an object: {package}")
        if (
            manifest.get("parent_deck") is not None
            or manifest.get("public_parent_read") is not False
            or manifest.get("research_only") is not True
            or manifest.get("authority") != PACKAGE_AUTHORITY_FALSE
            or len(card_ids) != 60
        ):
            raise IndependentSourceSmokeError(f"staged source provenance is invalid: {package}")
        if hashlib.sha256(main_bytes).hexdigest() != manifest.get("policy_sha256"):
            raise IndependentSourceSmokeError(f"staged source policy identity changed: {package}")
        if hashlib.sha256(deck_bytes).hexdigest() != manifest.get("deck_file_sha256"):
            raise IndependentSourceSmokeError(f"staged source deck identity changed: {package}")
        if canonical_deck_sha256_v1(card_ids) != manifest.get("canonical_deck_sha256"):
            raise IndependentSourceSmokeError(f"staged source canonical deck changed: {package}")
    if candidate_root is None:
        return packages

    # A promoted/staged opponent root intentionally omits ``cg/`` because the
    # opponent loader supplies the shared runtime.  Candidate seats must use
    # the original generator packages instead.  Rebind by immutable policy
    # SHA, never by directory order or candidate name.
    candidate_base = Path(candidate_root).resolve()
    if not candidate_base.is_dir():
        raise IndependentSourceSmokeError(f"candidate root is not a directory: {candidate_base}")
    from mage_ptcg.meta_specialist.self_owned_cg_package_v1 import verify_self_owned_cg_package_v1

    candidates_by_policy: dict[str, Path] = {}
    for candidate in sorted(candidate_base.iterdir()):
        if not candidate.is_dir() or not (candidate / "cg").is_dir():
            continue
        manifest = verify_self_owned_cg_package_v1(candidate)
        policy_sha = str(manifest["policy_sha256"])
        if policy_sha in candidates_by_policy:
            raise IndependentSourceSmokeError(f"duplicate candidate policy SHA: {policy_sha}")
        candidates_by_policy[policy_sha] = candidate

    rebound: list[Path] = []
    for staged in packages:
        try:
            source_manifest = json.loads((staged / "source_manifest.json").read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise IndependentSourceSmokeError(f"cannot read staged source manifest: {staged}") from exc
        policy_sha = source_manifest.get("policy_sha256")
        candidate = candidates_by_policy.get(str(policy_sha))
        if candidate is None:
            raise IndependentSourceSmokeError(f"candidate package is missing staged policy: {policy_sha}")
        rebound.append(candidate)
    return tuple(rebound)


def build_smoke_games(
    *,
    staged_root: str | Path,
    candidate_root: str | Path | None = None,
    refs: Sequence[str],
    pool_root: str | Path = DEFAULT_POOL,
    base_seed: int = 2026081901,
    games_per_opponent_seat: int = 1,
) -> tuple[EvaluationGameV1, ...]:
    """Build same-opponent/seat/seed smoke games for every staged source."""

    if not refs or len(refs) != len(set(refs)):
        raise IndependentSourceSmokeError("refs must be non-empty and unique")
    if type(games_per_opponent_seat) is not int or games_per_opponent_seat <= 0:
        raise IndependentSourceSmokeError("games_per_opponent_seat must be positive")
    packages = _source_packages(
        Path(staged_root),
        Path(candidate_root) if candidate_root is not None else None,
    )
    pool = Path(pool_root).resolve()
    games: list[EvaluationGameV1] = []
    for index, package in enumerate(packages):
        arm = ArenaArm(
            arm_id=f"independent_source_{index:02d}",
            policy_id=package.name,
            policy_sha256=_sha256(package / "main.py"),
            arm_kind="root_cg",
            candidate_package_root=package,
        )
        games.extend(
            _build_games(
                arm=arm,
                refs=refs,
                pool_root=pool,
                base_seed=base_seed + index * 1000,
                games_per_opponent_seat=games_per_opponent_seat,
                block_id=f"{SCHEMA}-{index:02d}",
            )
        )
    return tuple(games)


def run_smoke_v1(
    *,
    staged_root: str | Path,
    candidate_root: str | Path | None = None,
    output: str | Path,
    refs: Iterable[str],
    pool_root: str | Path = DEFAULT_POOL,
    base_seed: int = 2026081901,
    games_per_opponent_seat: int = 1,
    workers: int = DEFAULT_MAX_WORKERS_V1,
    worker_recycle_games: int = DEFAULT_WORKER_RECYCLE_GAMES_V1,
    execute: bool = False,
    evaluator: Callable[..., Mapping[str, object]] | None = None,
) -> dict[str, object]:
    if not execute:
        return {"status": "BLOCKED_EXECUTE_REQUIRED", "research_only": True}
    ref_tuple = tuple(str(ref) for ref in refs)
    games = build_smoke_games(
        staged_root=staged_root,
        candidate_root=candidate_root,
        refs=ref_tuple,
        pool_root=pool_root,
        base_seed=base_seed,
        games_per_opponent_seat=games_per_opponent_seat,
    )
    output_root = Path(output).resolve()
    if output_root.exists():
        raise FileExistsError(f"smoke output already exists: {output_root}")
    output_root.mkdir(parents=True, exist_ok=False)
    manifest: dict[str, object] = {
        "schema_version": SCHEMA,
        "status": "EXECUTING",
        "research_only": True,
        "authority": dict(AUTHORITY_FALSE),
        "staged_root": str(Path(staged_root).resolve()),
        "candidate_root": str(Path(candidate_root).resolve()) if candidate_root is not None else None,
        "pool_root": str(Path(pool_root).resolve()),
        "reference_ids": list(ref_tuple),
        "base_seed": base_seed,
        "games_per_opponent_seat": games_per_opponent_seat,
        "requested_games": len(games),
        "workers": workers,
        "worker_recycle_games": worker_recycle_games,
        "evaluator_implementation_sha256": evaluation_implementation_sha256_v1(),
    }
    (output_root / "manifest.json").write_text(_canonical_json(manifest) + "\n", encoding="utf-8")
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
        raise IndependentSourceSmokeError("evaluator returned no rows")
    by_source: dict[str, list[Mapping[str, object]]] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            raise IndependentSourceSmokeError("evaluator row is not an object")
        arm_id = str(row.get("metadata", {}).get("arm_id"))
        by_source.setdefault(arm_id, []).append(row)
    summary: dict[str, object] = {
        "schema_version": SCHEMA,
        "status": "COMPLETE",
        "research_only": True,
        "authority": dict(AUTHORITY_FALSE),
        "source_count": len(by_source),
        "requested_games": len(games),
        "evaluator_summary": evaluation.get("summary", {}),
        "sources": {
            arm_id: _aggregate(rows_for_arm)
            for arm_id, rows_for_arm in sorted(by_source.items())
        },
    }
    summary_path = output_root / "smoke_summary.json"
    summary_path.write_text(_canonical_json(summary) + "\n", encoding="utf-8")
    manifest.update({
        "status": "COMPLETE",
        "summary_sha256": _sha256(summary_path),
    })
    (output_root / "manifest-complete.json").write_text(
        _canonical_json(manifest) + "\n", encoding="utf-8"
    )
    return {"status": "COMPLETE", "output_root": str(output_root), "summary": summary}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--staged-root", type=Path, required=True)
    parser.add_argument("--candidate-root", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--pool-root", type=Path, default=DEFAULT_POOL)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--opponent-id", action="append", default=[])
    parser.add_argument("--base-seed", type=int, default=2026081901)
    parser.add_argument("--games-per-opponent-seat", type=int, default=1)
    parser.add_argument("--workers", type=int, default=DEFAULT_MAX_WORKERS_V1)
    parser.add_argument("--worker-recycle-games", type=int, default=DEFAULT_WORKER_RECYCLE_GAMES_V1)
    args = parser.parse_args(argv)
    if not args.execute:
        print(json.dumps({"status": "BLOCKED_EXECUTE_REQUIRED", "research_only": True}, ensure_ascii=False))
        return 2
    try:
        refs = tuple(args.opponent_id) if args.opponent_id else _read_refs(args.config)
        result = run_smoke_v1(
            staged_root=args.staged_root,
            candidate_root=args.candidate_root,
            output=args.output,
            refs=refs,
            pool_root=args.pool_root,
            base_seed=args.base_seed,
            games_per_opponent_seat=args.games_per_opponent_seat,
            workers=args.workers,
            worker_recycle_games=args.worker_recycle_games,
            execute=True,
        )
    except (IndependentSourceSmokeError, OSError, ValueError) as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc), "research_only": True}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
