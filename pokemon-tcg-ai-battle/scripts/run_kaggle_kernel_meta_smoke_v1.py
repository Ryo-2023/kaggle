#!/usr/bin/env python3
"""Run a bounded CABT smoke for one sealed public-kernel meta source.

The intake package has already passed static safety and exact-deck gates.  This
runner is the next, evaluation-only boundary: it executes the generated
wrapper against explicitly supplied existing pool rows on both seats, records
the immutable identities, and never mutates ``opponents/`` or grants training,
promotion, or submission authority.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sys
from typing import Mapping, Sequence

_ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(_ROOT), str(_ROOT / "src")]

from scripts.parallel_cabt_evaluator_v1 import (  # noqa: E402
    DEFAULT_MAX_WORKERS_V1,
    DEFAULT_WORKER_RECYCLE_GAMES_V1,
    _game_from_payload,
    evaluation_implementation_sha256_v1,
    run_parallel_cabt_evaluation,
)
from scripts.run_root_cg_candidate_arena_v1 import (  # noqa: E402
    AUTHORITY_FALSE,
    ArenaArm,
    _aggregate,
    _build_games,
    _load_candidate,
    _sha256,
)
from mage_ptcg.meta_specialist.opponent_pool_v1 import (  # noqa: E402
    build_opponent_agent_factory_v1,
    load_opponent_pool_v1,
    resolve_opponent_v1,
)
from scripts.test_sim import run_match  # noqa: E402


SCHEMA = "kaggle-kernel-meta-smoke-v1"
DEFAULT_POOL = _ROOT / "opponents"


class KaggleKernelMetaSmokeError(ValueError):
    """Raised when the bounded source smoke cannot be bound safely."""


def run_kaggle_kernel_meta_game_v1(payload: Mapping[str, object]) -> Mapping[str, object]:
    """Run one source game with the pool opponent imported before the source.

    A sealed public source may import a private ``cg`` module at module load
    time.  Loading that source first and then asking the opponent loader to
    evict/reload ``cg`` can abort the native extension.  This runner binds the
    opponent factory first and defers source import until ``run_match`` asks
    for the subject agent, preserving the loader's isolation boundary.
    """

    game = _game_from_payload(payload)
    if game.metadata.get("research_only") is not True or game.metadata.get("authority") != AUTHORITY_FALSE:
        raise KaggleKernelMetaSmokeError("game is not bound to the research-only smoke schema")
    subject_deck = Path(game.subject_deck_path).resolve()
    opponent_deck = Path(game.opponent_deck_path).resolve()
    if _sha256(subject_deck) != game.deck_sha256 or _sha256(opponent_deck) != game.opponent_deck_sha256:
        raise KaggleKernelMetaSmokeError("deck identity changed during source smoke")
    pool_root = Path(str(game.metadata.get("pool_root", DEFAULT_POOL))).resolve()
    expected_pool_sha = game.metadata.get("pool_manifest_sha256")
    if not isinstance(expected_pool_sha, str) or _sha256(pool_root / "pool_manifest.json") != expected_pool_sha:
        raise KaggleKernelMetaSmokeError("opponent pool manifest identity changed")
    pool = load_opponent_pool_v1(pool_root)
    opponent = resolve_opponent_v1(pool, game.opponent_id, subject_deck_csv_path=str(subject_deck))
    opponent_factory = build_opponent_agent_factory_v1(opponent)
    package_root = Path(str(game.metadata.get("candidate_package_root"))).resolve()
    if _sha256(package_root / "main.py") != game.policy_sha256:
        raise KaggleKernelMetaSmokeError("candidate policy identity changed")

    def subject_factory(_deck: object, _seed: int):
        # The opponent loader has completed before this deferred import.
        return _load_candidate(package_root).agent

    subject_first = game.seat == 0
    return run_match(
        deck_a_path=subject_deck if subject_first else opponent.deck_csv_path,
        deck_b_path=opponent.deck_csv_path if subject_first else subject_deck,
        agent_a_name=game.policy_id if subject_first else game.opponent_id,
        agent_b_name=game.opponent_id if subject_first else game.policy_id,
        seed=game.seed,
        max_steps=game.max_steps,
        output_dir=f"/tmp/kaggle-kernel-meta-smoke-worker/{game.game_id}",
        save_html=False,
        save_result=False,
        agent_a_factory=subject_factory if subject_first else opponent_factory,
        agent_b_factory=opponent_factory if subject_first else subject_factory,
    )


def _read_candidate(candidate_root: Path) -> tuple[Path, str]:
    candidate_root = candidate_root.resolve()
    main_path = candidate_root / "main.py"
    deck_path = candidate_root / "deck.csv"
    if not main_path.is_file() or not deck_path.is_file():
        raise KaggleKernelMetaSmokeError(f"candidate package is incomplete: {candidate_root}")
    try:
        cards = [int(token) for token in deck_path.read_text(encoding="utf-8").split()]
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise KaggleKernelMetaSmokeError(f"candidate deck is unreadable: {deck_path}") from exc
    if len(cards) != 60:
        raise KaggleKernelMetaSmokeError(f"candidate deck must contain exactly 60 cards: {deck_path}")
    return candidate_root, _sha256(main_path)


def _aggregate_smoke(rows: Sequence[Mapping[str, object]]) -> dict[str, object]:
    outcomes = Counter(str(row.get("outcome", "fault")) for row in rows)
    result = _aggregate(rows)
    result["fault_free"] = outcomes.get("fault", 0) == 0
    return result


def run_smoke(
    *,
    candidate_root: Path | str,
    output_root: Path | str,
    opponent_ids: Sequence[str],
    pool_root: Path | str = DEFAULT_POOL,
    base_seed: int = 2026089601,
    games_per_opponent_seat: int = 2,
    workers: int = DEFAULT_MAX_WORKERS_V1,
    worker_recycle_games: int = DEFAULT_WORKER_RECYCLE_GAMES_V1,
) -> dict[str, object]:
    if not opponent_ids or len(opponent_ids) != len(set(opponent_ids)):
        raise KaggleKernelMetaSmokeError("opponent_ids must be non-empty and unique")
    if type(games_per_opponent_seat) is not int or games_per_opponent_seat <= 0:
        raise KaggleKernelMetaSmokeError("games_per_opponent_seat must be positive")
    candidate_root, policy_sha = _read_candidate(Path(candidate_root))
    pool_root = Path(pool_root).resolve()
    pool_manifest = pool_root / "pool_manifest.json"
    pool_sha = _sha256(pool_manifest)
    output = Path(output_root).resolve()
    if output.exists():
        raise FileExistsError(f"smoke output already exists: {output}")

    arm = ArenaArm(
        arm_id="kaggle_kernel_source",
        policy_id=candidate_root.name,
        policy_sha256=policy_sha,
        arm_kind="root_cg",
        candidate_package_root=candidate_root,
    )
    games = _build_games(
        arm=arm,
        refs=tuple(opponent_ids),
        pool_root=pool_root,
        base_seed=base_seed,
        games_per_opponent_seat=games_per_opponent_seat,
        block_id=f"{SCHEMA}-{base_seed}",
    )
    games = tuple(
        replace(game, runner_ref="scripts.run_kaggle_kernel_meta_smoke_v1:run_kaggle_kernel_meta_game_v1")
        for game in games
    )
    output.mkdir(parents=True, exist_ok=False)
    manifest = {
        "schema_version": SCHEMA,
        "status": "EXECUTING",
        "research_only": True,
        "authority": dict(AUTHORITY_FALSE),
        "candidate_root": str(candidate_root),
        "candidate_policy_sha256": policy_sha,
        "pool_root": str(pool_root),
        "pool_manifest_sha256": pool_sha,
        "opponent_ids": list(opponent_ids),
        "base_seed": base_seed,
        "games_per_opponent_seat": games_per_opponent_seat,
        "requested_games": len(games),
        "workers": workers,
        "worker_recycle_games": worker_recycle_games,
        "evaluator_implementation_sha256": evaluation_implementation_sha256_v1(),
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    evaluation = run_parallel_cabt_evaluation(
        games,
        output_dir=output / "evaluation",
        max_workers=workers,
        worker_recycle_games=worker_recycle_games,
        overwrite=False,
    )
    rows = evaluation.get("rows")
    if not isinstance(rows, Sequence):
        raise KaggleKernelMetaSmokeError("evaluator returned no rows")
    aggregate = _aggregate_smoke(rows)
    summary = {
        "schema_version": SCHEMA,
        "status": "COMPLETE",
        "research_only": True,
        "authority": dict(AUTHORITY_FALSE),
        "candidate_policy_sha256": policy_sha,
        "pool_manifest_sha256": pool_sha,
        "opponent_ids": list(opponent_ids),
        "requested_games": len(games),
        "evaluator_summary": evaluation.get("summary", {}),
        "aggregate": aggregate,
        "decision": "SMOKE_PASS" if aggregate["fault_free"] else "SMOKE_FAIL",
    }
    summary_path = output / "smoke_summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    manifest.update({
        "status": "COMPLETE",
        "summary_sha256": _sha256(summary_path),
    })
    (output / "manifest-complete.json").write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return {"status": "COMPLETE", "output_root": str(output), "summary": summary}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--opponent-id", action="append", required=True)
    parser.add_argument("--pool-root", type=Path, default=DEFAULT_POOL)
    parser.add_argument("--base-seed", type=int, default=2026089601)
    parser.add_argument("--games-per-opponent-seat", type=int, default=2)
    parser.add_argument("--workers", type=int, default=DEFAULT_MAX_WORKERS_V1)
    parser.add_argument("--worker-recycle-games", type=int, default=DEFAULT_WORKER_RECYCLE_GAMES_V1)
    args = parser.parse_args(argv)
    try:
        result = run_smoke(
            candidate_root=args.candidate_root,
            output_root=args.output,
            opponent_ids=tuple(args.opponent_id),
            pool_root=args.pool_root,
            base_seed=args.base_seed,
            games_per_opponent_seat=args.games_per_opponent_seat,
            workers=args.workers,
            worker_recycle_games=args.worker_recycle_games,
        )
    except (KaggleKernelMetaSmokeError, OSError, ValueError) as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc), "research_only": True}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
