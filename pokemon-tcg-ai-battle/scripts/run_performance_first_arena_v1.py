"""Run the actual root Rule-v0 submission pair against pool opponents.

This is a research-only adapter around ``parallel_cabt_evaluator_v1``.  It
does not modify the opponent manifest, ``main.py``, or the production
evaluator.  The root deck and each opponent's local-eval-only policy/deck
bytes are bound into every game payload; the resulting ledger is independent
stratified evidence because CABT has no engine RNG setter.
"""

from __future__ import annotations

import argparse
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sys
from typing import Mapping, Sequence

_ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(_ROOT), str(_ROOT / "src")]

from main import make_rule_agent, read_deck_csv  # noqa: E402
from mage_ptcg.meta_specialist.opponent_pool_v1 import (  # noqa: E402
    build_opponent_agent_factory_v1,
    default_pool_root_v1,
    load_opponent_pool_v1,
    resolve_opponent_v1,
)
from scripts.parallel_cabt_evaluator_v1 import (  # noqa: E402
    EvaluationGameV1,
    _game_from_payload,
    run_parallel_cabt_evaluation,
)
from scripts.test_sim import run_match  # noqa: E402


ROOT_DECK = _ROOT / "deck.csv"
ROOT_POLICY_FILES = (
    _ROOT / "main.py",
    _ROOT / "agents/__init__.py",
    _ROOT / "agents/rule_agent.py",
)
ROOT_ARENA_SCHEMA_V1 = "meta-specialist-performance-first-root-arena-v1"


def _sha256(path: Path | str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def root_policy_sha256() -> str:
    # Match the package builder's policy identity exactly: the ordered raw
    # bytes of the three runtime source members, without a second namespace
    # prefix.  A ledger and an archive must be joinable by this SHA.
    return hashlib.sha256(b"".join(path.read_bytes() for path in ROOT_POLICY_FILES)).hexdigest()


def run_root_pool_game_v1(payload: Mapping[str, object]) -> Mapping[str, object]:
    """Run one root Rule-v0 game against a manifest-bound pool opponent."""
    game = _game_from_payload(payload)
    pool = load_opponent_pool_v1(default_pool_root_v1(_ROOT))
    opponent = resolve_opponent_v1(
        pool, game.opponent_id, subject_deck_csv_path=str(game.subject_deck_path)
    )
    subject_deck = read_deck_csv(game.subject_deck_path)
    opponent_factory = build_opponent_agent_factory_v1(opponent)

    def root_factory(deck: object, seed: int):
        return make_rule_agent(deck=deck, seed=seed)

    subject_first = game.seat == 0
    return run_match(
        deck_a_path=game.subject_deck_path if subject_first else opponent.deck_csv_path,
        deck_b_path=opponent.deck_csv_path if subject_first else game.subject_deck_path,
        agent_a_name="root-rule-v0" if subject_first else game.opponent_id,
        agent_b_name=game.opponent_id if subject_first else "root-rule-v0",
        seed=game.seed,
        max_steps=game.max_steps,
        output_dir=str(_ROOT / "runs" / "performance-first-root-worker" / game.game_id),
        save_html=False,
        save_result=False,
        agent_a_factory=root_factory if subject_first else opponent_factory,
        agent_b_factory=opponent_factory if subject_first else root_factory,
    )


def build_root_arena_games(
    *,
    opponent_ids: Sequence[str],
    games_per_seat: int,
    base_seed: int,
    subject_deck: Path = ROOT_DECK,
    block_id: str = "performance-first-root-stage0",
    max_steps: int = 2000,
) -> tuple[EvaluationGameV1, ...]:
    """Create balanced opponent×seat games for the current root pair."""
    if type(games_per_seat) is not int or not 1 <= games_per_seat <= 10000:
        raise ValueError("games_per_seat must be in [1, 10000]")
    if type(base_seed) is not int or base_seed < 0:
        raise ValueError("base_seed must be a nonnegative integer")
    pool = load_opponent_pool_v1(default_pool_root_v1(_ROOT))
    subject_deck = Path(subject_deck).resolve()
    if not subject_deck.is_file():
        raise FileNotFoundError(f"subject deck does not exist: {subject_deck}")
    root_deck_sha = _sha256(subject_deck)
    policy_sha = root_policy_sha256()
    games: list[EvaluationGameV1] = []
    ordinal = 0
    for opponent_id in opponent_ids:
        opponent = resolve_opponent_v1(
            pool, opponent_id, subject_deck_csv_path=str(subject_deck)
        )
        opponent_policy_sha = _sha256(opponent.policy_path)
        opponent_deck_sha = _sha256(opponent.deck_csv_path)
        for seat in (0, 1):
            for repetition in range(games_per_seat):
                game_id = f"{block_id}-{opponent_id}-seat{seat}-g{repetition:04d}"
                games.append(
                    EvaluationGameV1(
                        game_id=game_id,
                        block_id=block_id,
                        policy_id="rule-v0-root-deck",
                        policy_sha256=policy_sha,
                        deck_id="root-deck-current-worktree",
                        deck_sha256=root_deck_sha,
                        opponent_id=opponent_id,
                        opponent_identity={
                            "policy_sha256": opponent_policy_sha,
                            "deck_sha256": opponent_deck_sha,
                            "usage_boundary": opponent.usage_boundary,
                            "source": opponent.source,
                        },
                        opponent_deck_sha256=opponent_deck_sha,
                        seat=seat,
                        seed=base_seed + ordinal,
                        max_steps=max_steps,
                        subject_deck_path=str(subject_deck),
                        opponent_deck_path=str(opponent.deck_csv_path),
                        policy_agent_name="root-rule-v0",
                        opponent_agent_name=opponent_id,
                        runner_ref=(
                            "scripts.run_performance_first_arena_v1:"
                            "run_root_pool_game_v1"
                        ),
                        metadata={
                            "arena_schema": ROOT_ARENA_SCHEMA_V1,
                            "opponent_usage_boundary": opponent.usage_boundary,
                            "opponent_source": opponent.source,
                            "repetition": repetition,
                        },
                    )
                )
                ordinal += 1
    return tuple(games)


def _v4_provenance(checkpoint: Path) -> tuple[str, str]:
    raw = checkpoint.read_bytes()
    file_sha = hashlib.sha256(raw).hexdigest()
    try:
        import io
        import torch

        payload = torch.load(io.BytesIO(raw), map_location="cpu", weights_only=True)
        tensor_sha = str(payload["descriptor"]["tensor_state_sha256"])
    except (ImportError, KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
        raise ValueError(f"checkpoint has no closed V4 provenance: {checkpoint}") from exc
    if len(tensor_sha) != 64 or any(c not in "0123456789abcdef" for c in tensor_sha):
        raise ValueError("checkpoint tensor SHA is not lowercase SHA-256")
    return file_sha, tensor_sha


def run_wave6_pool_game_v1(payload: Mapping[str, object]) -> Mapping[str, object]:
    """Run one Wave6 V4 checkpoint game against the same pool as root arena."""
    game = _game_from_payload(payload)
    checkpoint = Path(str(game.metadata["checkpoint_path"]))
    subject_deck = Path(game.subject_deck_path)
    file_sha, tensor_sha = _v4_provenance(checkpoint)
    from mage_ptcg.meta_specialist.actor_pool_v1 import (
        ActorJobConfigV1,
        _build_actor_pool_deck_binding_v1,
        _build_neural_agent_policy_factory_v4,
    )
    from mage_ptcg.meta_specialist.runtime import RuntimeConstraintManifest, make_agent

    qualified, deck_lock, vocabulary = _build_actor_pool_deck_binding_v1(
        archetype_id="archaludon",
        deck_csv_path=subject_deck,
        source_commit="0" * 40,
    )
    job = ActorJobConfigV1(
        job_id=f"performance-first-wave6-{file_sha[:16]}",
        archetype_id="archaludon",
        deck_csv_path=str(subject_deck),
        source_commit="0" * 40,
        env_seed=0,
        seat=game.seat,
        behavior_kind="neural_specialist_v4",
        behavior_identity=file_sha,
        neural_checkpoint_path=str(checkpoint),
        neural_checkpoint_file_sha256=file_sha,
        neural_checkpoint_tensor_state_sha256=tensor_sha,
        opponent_kind=game.opponent_id,
    )
    policy_factory, identity = _build_neural_agent_policy_factory_v4(
        job, checkpoint_lineage_id=deck_lock.policy_lineage_id
    )
    if identity != file_sha:
        raise ValueError("Wave6 factory identity does not match checkpoint file SHA")
    constraints = RuntimeConstraintManifest.frozen_v1()

    def subject_factory(_deck: object, _seed: int):
        return make_agent(
            deck_asset=qualified,
            deck_lock=deck_lock,
            vocabulary=vocabulary,
            policy_factory=policy_factory,
            expected_policy_identity=file_sha,
            constraints=constraints,
        ).agent

    pool = load_opponent_pool_v1(default_pool_root_v1(_ROOT))
    opponent = resolve_opponent_v1(
        pool, game.opponent_id, subject_deck_csv_path=str(subject_deck)
    )
    opponent_factory = build_opponent_agent_factory_v1(opponent)
    subject_first = game.seat == 0
    return run_match(
        deck_a_path=game.subject_deck_path if subject_first else opponent.deck_csv_path,
        deck_b_path=opponent.deck_csv_path if subject_first else game.subject_deck_path,
        agent_a_name="wave6-v4" if subject_first else game.opponent_id,
        agent_b_name=game.opponent_id if subject_first else "wave6-v4",
        seed=game.seed,
        max_steps=game.max_steps,
        output_dir=str(_ROOT / "runs" / "performance-first-wave6-worker" / game.game_id),
        save_html=False,
        save_result=False,
        agent_a_factory=subject_factory if subject_first else opponent_factory,
        agent_b_factory=opponent_factory if subject_first else subject_factory,
    )


def build_wave6_arena_games(
    *,
    opponent_ids: Sequence[str],
    checkpoint: Path,
    subject_deck: Path,
    games_per_seat: int,
    base_seed: int,
    block_id: str = "performance-first-wave6-stage0",
    max_steps: int = 2000,
) -> tuple[EvaluationGameV1, ...]:
    """Create Wave6 games with the same pool/seat/ledger identity as root."""
    checkpoint = Path(checkpoint).resolve()
    subject_deck = Path(subject_deck).resolve()
    file_sha, tensor_sha = _v4_provenance(checkpoint)
    pool = load_opponent_pool_v1(default_pool_root_v1(_ROOT))
    subject_deck_sha = _sha256(subject_deck)
    games: list[EvaluationGameV1] = []
    ordinal = 0
    for opponent_id in opponent_ids:
        opponent = resolve_opponent_v1(
            pool, opponent_id, subject_deck_csv_path=str(subject_deck)
        )
        opponent_policy_sha = _sha256(opponent.policy_path)
        opponent_deck_sha = _sha256(opponent.deck_csv_path)
        for seat in (0, 1):
            for repetition in range(games_per_seat):
                games.append(
                    EvaluationGameV1(
                        game_id=f"{block_id}-{opponent_id}-seat{seat}-g{repetition:04d}",
                        block_id=block_id,
                        policy_id=f"wave6-v4-{file_sha[:16]}",
                        policy_sha256=file_sha,
                        deck_id="archaludon-r7-subject-deck",
                        deck_sha256=subject_deck_sha,
                        opponent_id=opponent_id,
                        opponent_identity={
                            "policy_sha256": opponent_policy_sha,
                            "deck_sha256": opponent_deck_sha,
                            "usage_boundary": opponent.usage_boundary,
                            "source": opponent.source,
                        },
                        opponent_deck_sha256=opponent_deck_sha,
                        seat=seat,
                        seed=base_seed + ordinal,
                        max_steps=max_steps,
                        subject_deck_path=str(subject_deck),
                        opponent_deck_path=str(opponent.deck_csv_path),
                        policy_agent_name="wave6-v4",
                        opponent_agent_name=opponent_id,
                        runner_ref=(
                            "scripts.run_performance_first_arena_v1:"
                            "run_wave6_pool_game_v1"
                        ),
                        metadata={
                            "arena_schema": ROOT_ARENA_SCHEMA_V1,
                            "checkpoint_path": str(checkpoint),
                            "checkpoint_tensor_sha256": tensor_sha,
                            "opponent_usage_boundary": opponent.usage_boundary,
                            "opponent_source": opponent.source,
                            "repetition": repetition,
                        },
                    )
                )
                ordinal += 1
    return tuple(games)


def run_root_arena(
    *,
    opponent_ids: Sequence[str],
    output_dir: Path,
    games_per_seat: int = 1,
    base_seed: int = 220000,
    subject_deck: Path = ROOT_DECK,
    workers: int = 12,
    worker_recycle_games: int = 16,
    overwrite: bool = False,
) -> dict[str, object]:
    games = build_root_arena_games(
        opponent_ids=opponent_ids,
        games_per_seat=games_per_seat,
        base_seed=base_seed,
        subject_deck=subject_deck,
    )
    result = run_parallel_cabt_evaluation(
        games,
        output_dir=output_dir,
        max_workers=workers,
        worker_recycle_games=worker_recycle_games,
        overwrite=overwrite,
    )
    result["arena_schema"] = ROOT_ARENA_SCHEMA_V1
    result["policy_sha256"] = root_policy_sha256()
    result["deck_sha256"] = _sha256(Path(subject_deck).resolve())
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--opponent-ids", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--games-per-seat", type=int, default=1)
    parser.add_argument("--base-seed", type=int, default=220000)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--worker-recycle-games", type=int, default=16)
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=600.0,
        help="per-game watchdog timeout; increase for queued broad-arena blocks",
    )
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument(
        "--wave6-checkpoint",
        type=Path,
        default=None,
        help="evaluate a Wave6 V4 checkpoint instead of the wired root Rule v0",
    )
    parser.add_argument(
        "--subject-deck",
        type=Path,
        default=ROOT_DECK,
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    ids = tuple(x.strip() for x in args.opponent_ids.split(",") if x.strip())
    if not ids:
        raise SystemExit("--opponent-ids must contain at least one id")
    if args.timeout_seconds <= 0:
        raise SystemExit("--timeout-seconds must be positive")
    if args.wave6_checkpoint is None:
        result = run_root_arena(
            opponent_ids=ids,
            output_dir=args.output,
            games_per_seat=args.games_per_seat,
            base_seed=args.base_seed,
            subject_deck=args.subject_deck,
            workers=args.workers,
            worker_recycle_games=args.worker_recycle_games,
            overwrite=args.overwrite,
        )
    else:
        games = build_wave6_arena_games(
            opponent_ids=ids,
            checkpoint=args.wave6_checkpoint,
            subject_deck=args.subject_deck,
            games_per_seat=args.games_per_seat,
            base_seed=args.base_seed,
        )
        games = tuple(replace(game, timeout_seconds=args.timeout_seconds) for game in games)
        result = run_parallel_cabt_evaluation(
            games,
            output_dir=args.output,
            max_workers=args.workers,
            worker_recycle_games=args.worker_recycle_games,
            overwrite=args.overwrite,
        )
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
