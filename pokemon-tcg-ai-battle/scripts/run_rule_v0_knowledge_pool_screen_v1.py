"""Research-only Rule v0 KnowledgePack tie-break screen against native pool.

This module deliberately keeps the production Rule v0 sources untouched.  A
candidate is only a hash-bound ``KnowledgePack`` passed through the existing
``main.make_rule_agent`` optional argument; the adapter can reorder equal Rule
score ties, but cannot add an action or change the legal action set.  The
native opponent pool is used for local evaluation only and is never copied to
the submission package.
"""

from __future__ import annotations

import argparse
from dataclasses import replace
import hashlib
import json
import math
from pathlib import Path
import sys
from typing import Callable, Mapping, Sequence

_ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(_ROOT), str(_ROOT / "src")]

from main import make_rule_agent, read_deck_csv  # noqa: E402
from mage_ptcg.knowledge import (  # noqa: E402
    KnowledgePack,
    build_team_deck_pack,
    content_hash,
    load_pack,
    serialize_pack,
    write_pack,
)
from mage_ptcg.meta_specialist.opponent_pool_v1 import (  # noqa: E402
    build_opponent_agent_factory_v1,
    default_pool_root_v1,
    load_opponent_pool_v1,
    resolve_opponent_v1,
)
from scripts.parallel_cabt_evaluator_v1 import (  # noqa: E402
    EvaluationGameV1,
    _game_from_payload,
    evaluator_implementation_sha256_v1,
    run_parallel_cabt_evaluation,
)
from scripts.run_performance_first_arena_v1 import (  # noqa: E402
    ROOT_DECK,
    root_policy_sha256,
)
from scripts.test_sim import run_match  # noqa: E402


SCREEN_SCHEMA_V1 = "rule-v0-knowledge-pool-screen-v1"
RUNNER_REF_V1 = "scripts.run_rule_v0_knowledge_pool_screen_v1:run_rule_v0_knowledge_pool_game_v1"
DEFAULT_BROAD_CONFIG = _ROOT / "configs/meta_specialist/performance_first_broad_pool_v1.json"
DEFAULT_POOL_MANIFEST = _ROOT / "opponents/pool_manifest.json"
_ACTION_TYPE_NAMES = {7: "PLAY", 8: "ATTACH", 9: "EVOLVE", 10: "ABILITY", 13: "ATTACK", 14: "END"}
_MAX_ACTION_DELTA = 200.0


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _sha256(path: Path | str) -> str:
    return _sha256_bytes(Path(path).read_bytes())


def _require_sha(value: object, name: str) -> str:
    if type(value) is not str or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise ValueError(f"{name} must be a lowercase SHA-256")
    return value


def _require_candidate_id(value: object) -> str:
    if type(value) is not str or not value or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for c in value):
        raise ValueError("candidate_id must be a lowercase identifier")
    return value


def _validated_action_deltas(value: Mapping[str, object] | None) -> dict[str, float]:
    """Validate the bounded, public action-type delta surface."""
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError("action_deltas must be a mapping")
    allowed = set(_ACTION_TYPE_NAMES.values())
    output: dict[str, float] = {}
    for raw_name, raw_delta in value.items():
        if type(raw_name) is not str or raw_name not in allowed:
            raise ValueError("action_deltas contains an unknown action type")
        if isinstance(raw_delta, bool) or not isinstance(raw_delta, (int, float)):
            raise ValueError("action delta must be numeric")
        delta = float(raw_delta)
        if not math.isfinite(delta) or abs(delta) > _MAX_ACTION_DELTA:
            raise ValueError("action delta must be finite and bounded")
        if delta:
            output[raw_name] = delta
    return dict(sorted(output.items()))


def pack_bytes_sha256(pack: KnowledgePack) -> str:
    """Hash the exact canonical bytes that are passed to the runtime loader."""
    if not isinstance(pack, KnowledgePack):
        raise TypeError("pack must be a KnowledgePack")
    return _sha256_bytes(serialize_pack(pack))


def build_candidate_pack(
    deck_path: Path | str,
    *,
    candidate_id: str,
    score: float,
) -> KnowledgePack:
    """Build one immutable tie-break pack with a deterministic candidate id."""
    candidate_id = _require_candidate_id(candidate_id)
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        raise ValueError("score must be numeric")
    base = build_team_deck_pack(
        deck_path,
        source=f"research-only:rule-v0-knowledge-pool-screen:{candidate_id}",
    )
    prior = replace(
        base.action_priors[0],
        rule_id=f"rule-v0-knowledge-tie-{candidate_id}",
        score=float(score),
        source_ref="agents/rule_agent.py:_MAIN_ACTION_SCORES (tie-break only)",
    )
    content = dict(base.content_payload())
    content["action_priors"] = [prior.to_payload()]
    digest = content_hash(content)
    manifest = replace(
        base.manifest,
        pack_id=f"knowledge-pack-v0-{digest[:20]}",
        content_hash=digest,
    )
    return KnowledgePack(manifest=manifest, team_deck=base.team_deck, action_priors=(prior,))


def _candidate_policy_sha(
    root_sha: str,
    pack: KnowledgePack | None,
    action_deltas: Mapping[str, object] | None = None,
) -> str:
    root_sha = _require_sha(root_sha, "root_policy_sha256")
    deltas = _validated_action_deltas(action_deltas)
    if pack is None and not deltas:
        return root_sha
    descriptor = {
        "schema": SCREEN_SCHEMA_V1,
        "root_policy_sha256": root_sha,
        "pack_sha256": pack_bytes_sha256(pack) if pack is not None else None,
        "action_deltas": deltas,
    }
    raw = json.dumps(descriptor, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return _sha256_bytes(b"rule-v0-knowledge-candidate\0" + raw)


def build_candidate_manifest(
    *,
    candidate_id: str,
    pack: KnowledgePack | None,
    action_deltas: Mapping[str, object] | None = None,
    root_policy_sha256: str,
    deck_sha256: str,
    pool_manifest_sha256: str,
    broad_config_sha256: str,
    evaluator_sha256: str,
    common24_ids: Sequence[str],
) -> dict[str, object]:
    """Return a fail-closed provenance record for one screen arm."""
    candidate_id = _require_candidate_id(candidate_id)
    for value, name in (
        (root_policy_sha256, "root_policy_sha256"),
        (deck_sha256, "deck_sha256"),
        (pool_manifest_sha256, "pool_manifest_sha256"),
        (broad_config_sha256, "broad_config_sha256"),
        (evaluator_sha256, "evaluator_sha256"),
    ):
        _require_sha(value, name)
    ids = tuple(str(item) for item in common24_ids)
    if not ids:
        raise ValueError("common24_ids must be non-empty")
    deltas = _validated_action_deltas(action_deltas)
    if pack is not None and deltas:
        raise ValueError("one candidate cannot combine KnowledgePack and action deltas")
    pack_sha = pack_bytes_sha256(pack) if pack is not None else None
    return {
        "schema_version": SCREEN_SCHEMA_V1,
        "candidate_id": candidate_id,
        "candidate_policy_sha256": _candidate_policy_sha(root_policy_sha256, pack, deltas),
        "root_policy_sha256": root_policy_sha256,
        "deck_sha256": deck_sha256,
        "pack_sha256": pack_sha,
        "action_deltas": deltas,
        "pool_manifest_sha256": pool_manifest_sha256,
        "broad_config_sha256": broad_config_sha256,
        "evaluator_sha256": evaluator_sha256,
        "common24_ids": list(ids),
        "usage_boundary": "local_eval_only",
        "source": "repository-root-rule-v0 + immutable KnowledgePack; native pool local evaluation",
        "research_only": True,
        "promotion_authority": False,
        "training_authority": False,
        "submission_authority": False,
    }


def build_action_delta_agent(
    *,
    candidate_id: str,
    deltas: Mapping[str, object],
    deck: Sequence[int] | None = None,
    seed: int | None = None,
) -> Callable[[dict], list[int]]:
    """Build a research-only Rule v0 wrapper using public action-type deltas.

    The wrapper first constructs the unchanged Rule v0 agent.  Only mandatory
    MAIN selections are re-ranked, using the option ``type`` field and the
    existing public ``rank_rule_indices`` score.  Any malformed or unsupported
    observation returns the exact baseline answer.
    """
    _require_candidate_id(candidate_id)
    normalized = _validated_action_deltas(deltas)
    from agents.rule_agent import rank_rule_indices

    baseline = make_rule_agent(deck=deck, seed=seed)

    def choose(observation: dict) -> list[int]:
        fallback = baseline(observation)
        try:
            select = observation.get("select")
            if not isinstance(select, Mapping):
                return fallback
            selection_type = select.get("type")
            if not (selection_type == 0 or str(selection_type).rsplit(".", 1)[-1].upper() == "MAIN"):
                return fallback
            options = select.get("option")
            if not isinstance(options, list) or not options:
                return fallback
            ranked = rank_rule_indices(observation)
            if not ranked:
                return fallback
            raw_min = select.get("minCount", 0)
            raw_max = select.get("maxCount", 0)
            if type(raw_min) is not int or type(raw_max) is not int or raw_min < 0 or raw_max < raw_min:
                return fallback
            count = raw_min if raw_min else 1
            if count > raw_max or count > len(options):
                return fallback
            adjusted: list[tuple[int, float, int]] = []
            for index, base_score in ranked:
                if type(index) is not int or index < 0 or index >= len(options):
                    return fallback
                option = options[index]
                if not isinstance(option, Mapping) or type(option.get("type")) is not int:
                    return fallback
                name = _ACTION_TYPE_NAMES.get(option["type"])
                adjusted.append((index, float(base_score) + normalized.get(name or "", 0.0), index))
            adjusted.sort(key=lambda item: (-item[1], item[2]))
            selected = [item[0] for item in adjusted[:count]]
            if len(selected) != count or len(set(selected)) != count:
                return fallback
            return selected
        except (AttributeError, KeyError, TypeError, ValueError):
            return fallback

    choose.__name__ = f"rule_v0_{candidate_id}_research_only"
    return choose


def _load_common24(config_path: Path) -> tuple[str, ...]:
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid broad config: {config_path}") from exc
    ids = payload.get("opponent_ids") if isinstance(payload, Mapping) else None
    if not isinstance(ids, list) or len(ids) != 24 or any(type(item) is not str or not item for item in ids):
        raise ValueError("broad config must contain exactly 24 opponent_ids")
    if len(set(ids)) != 24:
        raise ValueError("broad config opponent_ids must be unique")
    return tuple(ids)


def _verify_pack_file(pack: KnowledgePack | None, pack_path: str | None) -> KnowledgePack | None:
    if pack is None:
        if pack_path is not None:
            raise ValueError("baseline cannot carry pack_path")
        return None
    if not pack_path:
        raise ValueError("candidate pack requires pack_path")
    loaded = load_pack(pack_path)
    if serialize_pack(loaded) != serialize_pack(pack):
        raise ValueError("pack file bytes do not match candidate pack")
    return loaded


def build_screen_games(
    *,
    candidate_id: str,
    pack_path: Path | str | None,
    pack: KnowledgePack | None,
    action_deltas: Mapping[str, object] | None = None,
    opponent_ids: Sequence[str],
    games_per_seat: int,
    base_seed: int,
    subject_deck: Path | str,
    pool_manifest_sha256: str,
    broad_config_sha256: str,
    evaluator_sha256: str,
    root_policy_sha256: str,
    block_id: str | None = None,
    max_steps: int = 2000,
) -> tuple[EvaluationGameV1, ...]:
    """Create balanced native-pool game cells with candidate provenance."""
    candidate_id = _require_candidate_id(candidate_id)
    if type(games_per_seat) is not int or games_per_seat <= 0:
        raise ValueError("games_per_seat must be positive")
    if type(base_seed) is not int or base_seed < 0:
        raise ValueError("base_seed must be nonnegative")
    if type(max_steps) is not int or max_steps <= 0:
        raise ValueError("max_steps must be positive")
    subject_deck = Path(subject_deck).resolve()
    if not subject_deck.is_file():
        raise ValueError(f"subject deck is missing: {subject_deck}")
    pack = _verify_pack_file(pack, str(pack_path) if pack_path is not None else None)
    action_deltas = _validated_action_deltas(action_deltas)
    if pack is not None and action_deltas:
        raise ValueError("one screen arm cannot combine KnowledgePack and action deltas")
    pool = load_opponent_pool_v1(default_pool_root_v1(_ROOT))
    root_deck_sha = _sha256(subject_deck)
    if root_deck_sha != _sha256(ROOT_DECK) and subject_deck == ROOT_DECK.resolve():
        raise ValueError("root deck SHA changed while using repository root")
    manifest = build_candidate_manifest(
        candidate_id=candidate_id,
        pack=pack,
        action_deltas=action_deltas,
        root_policy_sha256=root_policy_sha256,
        deck_sha256=root_deck_sha,
        pool_manifest_sha256=pool_manifest_sha256,
        broad_config_sha256=broad_config_sha256,
        evaluator_sha256=evaluator_sha256,
        common24_ids=opponent_ids,
    )
    policy_sha = str(manifest["candidate_policy_sha256"])
    games: list[EvaluationGameV1] = []
    block = block_id or f"{SCREEN_SCHEMA_V1}-{candidate_id}-96"
    ordinal = 0
    for opponent_id in opponent_ids:
        opponent = resolve_opponent_v1(pool, str(opponent_id), subject_deck_csv_path=str(subject_deck))
        opponent_policy_sha = _sha256(opponent.policy_path) if opponent.policy_path else "0" * 64
        opponent_deck_sha = _sha256(opponent.deck_csv_path)
        for seat in (0, 1):
            for repetition in range(games_per_seat):
                game_id = f"{block}-{opponent_id}-seat{seat}-g{repetition:04d}"
                games.append(
                    EvaluationGameV1(
                        game_id=game_id,
                        block_id=block,
                        policy_id=f"rule-v0-{candidate_id}",
                        policy_sha256=policy_sha,
                        deck_id="root-deck-current-worktree",
                        deck_sha256=root_deck_sha,
                        opponent_id=str(opponent_id),
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
                        policy_agent_name=f"rule-v0-{candidate_id}",
                        opponent_agent_name=str(opponent_id),
                        runner_ref=RUNNER_REF_V1,
                        metadata={
                            **manifest,
                            "pack_path": str(Path(pack_path).resolve()) if pack_path else None,
                            "action_deltas": action_deltas,
                            "opponent_id": str(opponent_id),
                            "opponent_usage_boundary": opponent.usage_boundary,
                            "opponent_source": opponent.source,
                            "repetition": repetition,
                            "seat": seat,
                            "base_seed": base_seed,
                        },
                    )
                )
                ordinal += 1
    return tuple(games)


def run_rule_v0_knowledge_pool_game_v1(payload: Mapping[str, object]) -> Mapping[str, object]:
    """Worker entrypoint for one baseline or KnowledgePack candidate game."""
    game = _game_from_payload(payload)
    metadata = game.metadata
    candidate_id = _require_candidate_id(metadata.get("candidate_id"))
    root_sha = _require_sha(metadata.get("root_policy_sha256"), "root_policy_sha256")
    expected_policy_sha = _require_sha(metadata.get("candidate_policy_sha256"), "candidate_policy_sha256")
    pack_path = metadata.get("pack_path")
    pack = load_pack(str(pack_path)) if pack_path else None
    raw_deltas = metadata.get("action_deltas")
    action_deltas = _validated_action_deltas(raw_deltas if isinstance(raw_deltas, Mapping) else None)
    if pack is not None and action_deltas:
        raise ValueError("candidate cannot combine KnowledgePack and action deltas")
    actual_policy_sha = _candidate_policy_sha(root_sha, pack, action_deltas)
    if actual_policy_sha != expected_policy_sha or game.policy_sha256 != expected_policy_sha:
        raise ValueError("candidate policy identity mismatch")
    if pack is not None:
        expected_pack_sha = _require_sha(metadata.get("pack_sha256"), "pack_sha256")
        if pack_bytes_sha256(pack) != expected_pack_sha:
            raise ValueError("KnowledgePack SHA mismatch")
    pool = load_opponent_pool_v1(default_pool_root_v1(_ROOT))
    opponent = resolve_opponent_v1(pool, game.opponent_id, subject_deck_csv_path=game.subject_deck_path)
    opponent_factory = build_opponent_agent_factory_v1(opponent)

    def subject_factory(deck: object, seed: int):
        if action_deltas:
            return build_action_delta_agent(
                candidate_id=candidate_id,
                deltas=action_deltas,
                deck=deck,
                seed=seed,
            )
        return make_rule_agent(deck=deck, seed=seed, knowledge_pack=pack)

    subject_first = game.seat == 0
    return run_match(
        deck_a_path=game.subject_deck_path if subject_first else opponent.deck_csv_path,
        deck_b_path=opponent.deck_csv_path if subject_first else game.subject_deck_path,
        agent_a_name=f"rule-v0-{candidate_id}" if subject_first else game.opponent_id,
        agent_b_name=game.opponent_id if subject_first else f"rule-v0-{candidate_id}",
        seed=game.seed,
        max_steps=game.max_steps,
        output_dir=str(_ROOT / "runs" / "rule-v0-knowledge-pool-worker" / candidate_id / game.game_id),
        save_html=False,
        save_result=False,
        agent_a_factory=subject_factory if subject_first else opponent_factory,
        agent_b_factory=opponent_factory if subject_first else subject_factory,
    )


def _candidate_specs() -> dict[str, tuple[float | None, dict[str, float]]]:
    return {
        "baseline-no-pack": (None, {}),
        "play-minus": (-2.0, {}),
        "play-plus": (2.0, {}),
        "attack-plus-200": (None, {"ATTACK": 200.0}),
        "play-minus-200": (None, {"PLAY": -200.0}),
    }


def run_screen(
    *,
    output_dir: Path,
    opponent_ids: Sequence[str],
    games_per_seat: int = 2,
    base_seed: int = 14900000,
    subject_deck: Path | str = ROOT_DECK,
    workers: int = 12,
    worker_recycle_games: int = 16,
    overwrite: bool = False,
) -> dict[str, object]:
    """Run baseline plus two tie-break candidates into a fresh root."""
    output_dir = Path(output_dir)
    subject_deck = Path(subject_deck).resolve()
    if not subject_deck.is_file():
        raise FileNotFoundError(f"subject deck is missing: {subject_deck}")
    if output_dir.exists() and not overwrite and any(output_dir.iterdir()):
        raise FileExistsError(f"output is not empty: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    broad_sha = _sha256(DEFAULT_BROAD_CONFIG)
    pool_sha = _sha256(DEFAULT_POOL_MANIFEST)
    evaluator_sha = evaluator_implementation_sha256_v1()
    root_sha = root_policy_sha256()
    deck_sha = _sha256(subject_deck)
    candidate_results: dict[str, object] = {}
    for candidate_id, (score, action_deltas) in _candidate_specs().items():
        arm_root = output_dir / candidate_id
        arm_root.mkdir(parents=True, exist_ok=True)
        pack: KnowledgePack | None = None
        pack_path: Path | None = None
        if score is not None:
            pack = build_candidate_pack(subject_deck, candidate_id=candidate_id, score=score)
            pack_path = arm_root / "knowledge-pack.json"
            pack_path.parent.mkdir(parents=True, exist_ok=True)
            write_pack(pack, pack_path)
        games = build_screen_games(
            candidate_id=candidate_id,
            pack_path=pack_path,
            pack=pack,
            action_deltas=action_deltas,
            opponent_ids=opponent_ids,
            games_per_seat=games_per_seat,
            base_seed=base_seed,
            subject_deck=subject_deck,
            pool_manifest_sha256=pool_sha,
            broad_config_sha256=broad_sha,
            evaluator_sha256=evaluator_sha,
            root_policy_sha256=root_sha,
        )
        manifest = build_candidate_manifest(
            candidate_id=candidate_id,
            pack=pack,
            action_deltas=action_deltas,
            root_policy_sha256=root_sha,
            deck_sha256=deck_sha,
            pool_manifest_sha256=pool_sha,
            broad_config_sha256=broad_sha,
            evaluator_sha256=evaluator_sha,
            common24_ids=opponent_ids,
        )
        (arm_root / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        result = run_parallel_cabt_evaluation(
            games,
            output_dir=arm_root / "evaluation",
            max_workers=workers,
            worker_recycle_games=worker_recycle_games,
            overwrite=overwrite,
        )
        candidate_results[candidate_id] = result["summary"]
    summary = {"schema_version": SCREEN_SCHEMA_V1, "arms": candidate_results, "requested_games_per_arm": len(opponent_ids) * 2 * games_per_seat}
    (output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return summary


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--broad-config", type=Path, default=DEFAULT_BROAD_CONFIG)
    parser.add_argument("--opponent-ids", default="")
    parser.add_argument("--games-per-seat", type=int, default=2)
    parser.add_argument("--base-seed", type=int, default=14900000)
    parser.add_argument("--subject-deck", type=Path, default=ROOT_DECK)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--worker-recycle-games", type=int, default=16)
    parser.add_argument("--overwrite", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    config = args.broad_config.resolve()
    ids = tuple(x.strip() for x in args.opponent_ids.split(",") if x.strip()) if args.opponent_ids else _load_common24(config)
    if len(ids) != 24:
        raise SystemExit("exactly 24 opponent ids are required for the common24 screen")
    result = run_screen(
        output_dir=args.output,
        opponent_ids=ids,
        games_per_seat=args.games_per_seat,
        base_seed=args.base_seed,
        subject_deck=args.subject_deck,
        workers=args.workers,
        worker_recycle_games=args.worker_recycle_games,
        overwrite=args.overwrite,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "SCREEN_SCHEMA_V1",
    "build_candidate_manifest",
    "build_candidate_pack",
    "build_screen_games",
    "main",
    "pack_bytes_sha256",
    "run_rule_v0_knowledge_pool_game_v1",
    "run_screen",
]
