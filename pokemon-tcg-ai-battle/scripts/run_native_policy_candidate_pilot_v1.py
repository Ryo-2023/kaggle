"""Research-only native-policy candidate evaluator.

This runner keeps an upstream native ``deck + agent`` pair intact and applies
only a hash-bound environment configuration or a bounded native score bias.
The subject always calls the native agent first; malformed, unsupported, or
non-main overrides fall back to the exact native action.  It is deliberately a
new evaluator: the production entrypoint, opponent registry, and submission
bundle are not modified.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

from mage_ptcg.meta_specialist.meta_distribution_v1 import (  # noqa: E402
    load_meta_distribution_manifest_v1,
)
from mage_ptcg.meta_specialist.native_preserving_adapter_v1 import (  # noqa: E402
    NativePreservingAdapterError,
    NativeScoreConfigV1,
    build_native_guarded_score_policy_v1,
    build_native_score_policy_v1,
)
from mage_ptcg.meta_specialist.opponent_pool_v1 import (  # noqa: E402
    OpponentInstanceV1,
    build_opponent_agent_factory_v1,
    load_opponent_pool_v1,
    resolve_opponent_v1,
)
from scripts.parallel_cabt_evaluator_v1 import (  # noqa: E402
    EvaluationGameV1,
    _game_from_payload,
    aggregate_ledger_v1,
    evaluator_implementation_sha256_v1,
    run_parallel_cabt_evaluation,
)
from scripts.test_sim import run_match  # noqa: E402


NATIVE_CANDIDATE_SCHEMA_V1 = "meta-specialist-native-policy-candidate-pilot-v1"
RUNNER_REF_V1 = "scripts.run_native_policy_candidate_pilot_v1:run_native_candidate_game_v1"
_SHA_HEX = frozenset("0123456789abcdef")
_ALLOWED_ENV_KEYS = frozenset(
    {"USE_SEARCH", "SP_BUDGET", "BEAM_CAND", "BEAM_MAXD", "BEAM_MARGIN", "POKE_SEARCH_BUDGET"}
)
_PRESERVED_PREFIXES = ("main", "__main__", "agents")
_MODULE_CACHE: dict[str, tuple[object, Any]] = {}


class NativeCandidatePilotError(ValueError):
    """Raised when a candidate is not closed or reproducible."""


def _sha256(path: Path | str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _sha(value: object, name: str) -> str:
    if type(value) is not str or len(value) != 64 or any(c not in _SHA_HEX for c in value):
        raise NativeCandidatePilotError(f"{name} must be a lowercase SHA-256")
    return value


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _config_sha(
    env: Mapping[str, str],
    biases: Mapping[str, float],
    min_score_gain: float | None = None,
) -> str:
    payload: dict[str, object] = {
        "env": dict(sorted(env.items())),
        "biases": dict(sorted(biases.items())),
    }
    # ``None`` retains the v1 identity for historical candidates.  New
    # guarded candidates bind the threshold into their immutable config.
    if min_score_gain is not None:
        payload["min_score_gain"] = float(min_score_gain)
    return hashlib.sha256(_canonical(payload)).hexdigest()


def _validate_env(env: object) -> dict[str, str]:
    if not isinstance(env, Mapping):
        raise NativeCandidatePilotError("env must be an object")
    result: dict[str, str] = {}
    for key, value in env.items():
        if type(key) is not str or key not in _ALLOWED_ENV_KEYS:
            raise NativeCandidatePilotError(f"unsupported native env key: {key!r}")
        if type(value) not in (str, int, float) or isinstance(value, bool):
            raise NativeCandidatePilotError(f"env[{key}] must be scalar")
        text = str(value)
        if not text or len(text) > 32:
            raise NativeCandidatePilotError(f"env[{key}] is malformed")
        result[key] = text
    return dict(sorted(result.items()))


def _validate_biases(biases: object) -> dict[str, float]:
    if not isinstance(biases, Mapping):
        raise NativeCandidatePilotError("biases must be an object")
    try:
        config = NativeScoreConfigV1.from_mapping(dict(biases))
    except NativePreservingAdapterError as exc:
        raise NativeCandidatePilotError(str(exc)) from exc
    return {key: value for key, value in config.biases}


def _validate_min_score_gain(value: object) -> float:
    if type(value) not in (int, float) or isinstance(value, bool):
        raise NativeCandidatePilotError("min_score_gain must be a finite number")
    numeric = float(value)
    if not math.isfinite(numeric) or numeric < 0.0 or numeric > 100_000.0:
        raise NativeCandidatePilotError("min_score_gain is outside bounded range")
    return numeric


def _candidate_module_v1(path: Path, env: Mapping[str, str], config_sha: str) -> tuple[object, Any]:
    """Load a native policy under a unique config-bound module name.

    The import roots mirror the verified opponent loader, while preserving the
    repository's ``main``/``agents`` modules after import.  The cache is
    worker-local and keyed by source/config bytes.
    """

    source_sha = _sha256(path)
    cache_key = f"{source_sha}:{config_sha}"
    cached = _MODULE_CACHE.get(cache_key)
    if cached is not None:
        return cached
    module_name = f"_native_candidate_{source_sha[:16]}_{config_sha[:16]}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise NativeCandidatePilotError(f"cannot load candidate module: {path}")
    module = importlib.util.module_from_spec(spec)
    preserved = {
        name: value
        for name, value in list(sys.modules.items())
        if name in _PRESERVED_PREFIXES or name.startswith(tuple(f"{p}." for p in _PRESERVED_PREFIXES))
    }
    old_path = list(sys.path)
    old_env = {key: os.environ.get(key) for key in env}
    repo_root = path.resolve().parents[2]
    roots = [repo_root / "vendor_opponent_pilots", repo_root]
    sys.path[:0] = [str(root) for root in roots if root.is_dir() and str(root) not in sys.path]
    for name in preserved:
        sys.modules.pop(name, None)
    for key, value in env.items():
        os.environ[key] = value
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        sys.modules.pop(module_name, None)
        raise NativeCandidatePilotError(f"candidate import failed: {type(exc).__name__}: {exc}") from exc
    finally:
        sys.path[:] = old_path
        for key, value in old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        for name in list(sys.modules):
            if name in _PRESERVED_PREFIXES or name.startswith(tuple(f"{p}." for p in _PRESERVED_PREFIXES)):
                sys.modules.pop(name, None)
        sys.modules.update(preserved)
    agent = getattr(module, "agent", None)
    if not callable(agent):
        raise NativeCandidatePilotError("candidate module must expose callable agent")
    _MODULE_CACHE[cache_key] = (module, agent)
    return module, agent


def _candidate_action_factory_v1(
    *, path: Path, env: Mapping[str, str], biases: Mapping[str, float], baseline_sha: str, config_sha: str,
    min_score_gain: float = 0.0,
):
    module, agent = _candidate_module_v1(path, env, config_sha)
    if biases or min_score_gain > 0.0:
        config = NativeScoreConfigV1.from_mapping(dict(biases))
        policy_builder = (
            build_native_guarded_score_policy_v1
            if min_score_gain > 0.0
            else build_native_score_policy_v1
        )
        policy = policy_builder(
            native_agent=agent,
            native_module=module,
            config=config,
            baseline_policy_sha256=baseline_sha,
            **({"min_score_gain": min_score_gain} if min_score_gain > 0.0 else {}),
        )
        # CABT introspects ``__code__`` to decide the callable arity.  Return a
        # plain function instead of the policy object so the research wrapper
        # follows the same runtime boundary as the verified opponent loader.
        return lambda _deck, _seed: (lambda observation: policy(observation))

    def factory(_deck: object, _seed: int):
        return lambda observation: agent(observation)

    return factory


def _identity(pool: Mapping[str, OpponentInstanceV1], opponent_id: str) -> dict[str, object]:
    instance = pool[opponent_id]
    return {
        "policy_sha256": _sha256(instance.policy_path),
        "deck_sha256": _sha256(instance.deck_csv_path),
        "deck_csv_path": instance.deck_csv_path,
        "usage_boundary": instance.usage_boundary,
        "source": instance.source,
    }


def build_native_candidate_games_v1(
    *, candidate_id: str, candidate: Mapping[str, object], pool: Mapping[str, OpponentInstanceV1],
    reference_ids: Sequence[str], games_per_opponent_seat: int = 2, base_seed: int = 9_400_000,
    block_id: str = "native-candidate-pilot", max_steps: int = 2_000, timeout_seconds: float = 600.0,
) -> tuple[EvaluationGameV1, ...]:
    if not reference_ids or games_per_opponent_seat <= 0:
        raise NativeCandidatePilotError("reference_ids and games_per_opponent_seat must be positive")
    main_path = Path(str(candidate["main_path"])).resolve()
    deck_path = Path(str(candidate["deck_path"])).resolve()
    policy_sha = _sha(candidate["policy_sha256"], "policy_sha256")
    deck_sha = _sha(candidate["deck_sha256"], "deck_sha256")
    env = _validate_env(candidate.get("env", {}))
    biases = _validate_biases(candidate.get("biases", {}))
    raw_gain = candidate.get("min_score_gain")
    min_score_gain = _validate_min_score_gain(raw_gain) if raw_gain is not None else 0.0
    default_config_sha = _config_sha(env, biases, min_score_gain if raw_gain is not None else None)
    config_sha = _sha(candidate.get("config_sha256", default_config_sha), "config_sha256")
    games: list[EvaluationGameV1] = []
    ordinal = 0
    available = set(pool)
    for opponent_id in reference_ids:
        if opponent_id not in available or opponent_id == candidate_id:
            continue
        identity = _identity(pool, opponent_id)
        for seat in (0, 1):
            for repetition in range(games_per_opponent_seat):
                games.append(
                    EvaluationGameV1(
                        game_id=f"{block_id}-{candidate_id}-{config_sha[:12]}-{opponent_id}-seat{seat}-g{repetition:04d}",
                        block_id=block_id,
                        policy_id=f"native-candidate:{candidate_id}:{config_sha[:12]}",
                        policy_sha256=policy_sha,
                        deck_id=candidate_id,
                        deck_sha256=deck_sha,
                        opponent_id=opponent_id,
                        opponent_identity={"policy_sha256": identity["policy_sha256"], "deck_sha256": identity["deck_sha256"], "usage_boundary": identity["usage_boundary"], "source": identity["source"]},
                        opponent_deck_sha256=str(identity["deck_sha256"]),
                        seat=seat,
                        seed=base_seed + ordinal,
                        max_steps=max_steps,
                        timeout_seconds=timeout_seconds,
                        subject_deck_path=str(deck_path),
                        opponent_deck_path=str(identity["deck_csv_path"]),
                        policy_agent_name=f"native-candidate:{candidate_id}",
                        opponent_agent_name=opponent_id,
                        runner_ref=RUNNER_REF_V1,
                        metadata={
                            "schema_version": NATIVE_CANDIDATE_SCHEMA_V1,
                            "candidate_id": candidate_id,
                            "candidate_main_path": str(main_path),
                            "candidate_deck_path": str(deck_path),
                            "candidate_policy_sha256": policy_sha,
                            "candidate_deck_sha256": deck_sha,
                            "candidate_config_sha256": config_sha,
                            "candidate_env": dict(env),
                            "candidate_biases": dict(biases),
                            "candidate_min_score_gain": min_score_gain,
                            "pool_root": str(Path(str(candidate.get("pool_root", _ROOT / "opponents"))).resolve()),
                            "repetition": repetition,
                            "promotion_authority": False,
                            "training_authority": False,
                            "submission_authority": False,
                        },
                    )
                )
                ordinal += 1
    if not games:
        raise NativeCandidatePilotError("no valid candidate games")
    return tuple(games)


def run_native_candidate_game_v1(payload: Mapping[str, object]) -> Mapping[str, object]:
    game = _game_from_payload(payload)
    metadata = game.metadata
    pool_root = Path(str(metadata.get("pool_root", _ROOT / "opponents"))).resolve()
    pool = load_opponent_pool_v1(pool_root)
    candidate_id = str(metadata.get("candidate_id", game.deck_id))
    candidate_path = Path(str(metadata["candidate_main_path"])).resolve()
    env = _validate_env(metadata.get("candidate_env", {}))
    biases = _validate_biases(metadata.get("candidate_biases", {}))
    min_score_gain = _validate_min_score_gain(metadata.get("candidate_min_score_gain", 0.0))
    config_sha = _sha(metadata["candidate_config_sha256"], "candidate_config_sha256")
    baseline_sha = _sha(metadata["candidate_policy_sha256"], "candidate_policy_sha256")
    subject_factory = _candidate_action_factory_v1(
        path=candidate_path,
        env=env,
        biases=biases,
        baseline_sha=baseline_sha,
        config_sha=config_sha,
        min_score_gain=min_score_gain,
    )
    opponent = resolve_opponent_v1(pool, game.opponent_id, subject_deck_csv_path=game.subject_deck_path)
    opponent_factory = build_opponent_agent_factory_v1(opponent)
    subject_first = game.seat == 0
    return run_match(
        deck_a_path=game.subject_deck_path if subject_first else opponent.deck_csv_path,
        deck_b_path=opponent.deck_csv_path if subject_first else game.subject_deck_path,
        agent_a_name=f"native-candidate:{candidate_id}" if subject_first else game.opponent_id,
        agent_b_name=game.opponent_id if subject_first else f"native-candidate:{candidate_id}",
        seed=game.seed,
        max_steps=game.max_steps,
        output_dir=str(_ROOT / "runs" / "native-candidate-worker" / game.game_id),
        save_html=False,
        save_result=False,
        agent_a_factory=subject_factory if subject_first else opponent_factory,
        agent_b_factory=opponent_factory if subject_first else subject_factory,
    )


def summarize_native_candidate_rows_v1(rows: Sequence[Mapping[str, object]]) -> dict[str, object]:
    ledger = aggregate_ledger_v1(rows)
    first = rows[0] if rows else {}
    metadata = first.get("metadata", {}) if isinstance(first, Mapping) else {}
    return {
        "schema_version": NATIVE_CANDIDATE_SCHEMA_V1,
        "candidate_id": metadata.get("candidate_id"),
        "candidate_policy_sha256": metadata.get("candidate_policy_sha256"),
        "candidate_deck_sha256": metadata.get("candidate_deck_sha256"),
        "candidate_config_sha256": metadata.get("candidate_config_sha256"),
        "candidate_env": metadata.get("candidate_env", {}),
        "candidate_biases": metadata.get("candidate_biases", {}),
        "candidate_min_score_gain": metadata.get("candidate_min_score_gain", 0.0),
        "requested_games": len(rows),
        **ledger,
        "promotion_authority": False,
        "training_authority": False,
        "submission_authority": False,
        "research_only": True,
    }


def _load_reference_ids(path: Path) -> tuple[str, ...]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping) or not isinstance(payload.get("opponent_ids"), list):
        raise NativeCandidatePilotError("reference config requires opponent_ids")
    return tuple(str(item) for item in payload["opponent_ids"])


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pool-root", type=Path, default=_ROOT / "opponents")
    parser.add_argument("--asset-id", required=True)
    parser.add_argument("--reference-config", type=Path, default=_ROOT / "configs/meta_specialist/performance_first_broad_pool_v1.json")
    parser.add_argument("--reference-ids", default="")
    parser.add_argument("--env-json", default="{}")
    parser.add_argument("--biases-json", default="{}")
    parser.add_argument(
        "--min-score-gain", type=float, default=0.0,
        help="guarded policy only: minimum score gain over native action (0 keeps legacy score adapter)",
    )
    parser.add_argument("--games-per-opponent-seat", type=int, default=2)
    parser.add_argument("--base-seed", type=int, default=9_400_000)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--worker-recycle-games", type=int, default=16)
    parser.add_argument("--timeout-seconds", type=float, default=600.0)
    parser.add_argument("--max-steps", type=int, default=2_000)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    pool_root = args.pool_root.resolve()
    pool = load_opponent_pool_v1(pool_root)
    if args.asset_id not in pool:
        raise SystemExit(f"unknown asset id: {args.asset_id}")
    instance = pool[args.asset_id]
    env = _validate_env(json.loads(args.env_json))
    biases = _validate_biases(json.loads(args.biases_json))
    min_score_gain = _validate_min_score_gain(args.min_score_gain)
    candidate = {
        "main_path": instance.policy_path,
        "deck_path": instance.deck_csv_path,
        "policy_sha256": _sha256(instance.policy_path),
        "deck_sha256": _sha256(instance.deck_csv_path),
        "env": env,
        "biases": biases,
        "min_score_gain": min_score_gain,
        "config_sha256": _config_sha(env, biases, min_score_gain),
        "pool_root": str(pool_root),
    }
    refs = tuple(item.strip() for item in args.reference_ids.split(",") if item.strip()) if args.reference_ids.strip() else _load_reference_ids(args.reference_config)
    games = build_native_candidate_games_v1(
        candidate_id=args.asset_id,
        candidate=candidate,
        pool=pool,
        reference_ids=refs,
        games_per_opponent_seat=args.games_per_opponent_seat,
        base_seed=args.base_seed,
        max_steps=args.max_steps,
        timeout_seconds=args.timeout_seconds,
    )
    result = run_parallel_cabt_evaluation(
        games, output_dir=args.output, max_workers=args.workers,
        worker_recycle_games=args.worker_recycle_games, overwrite=args.overwrite,
    )
    summary = summarize_native_candidate_rows_v1(result["rows"])
    summary.update({
        "evaluator_implementation_sha256": evaluator_implementation_sha256_v1(),
        "reference_ids": list(refs),
        "pool_manifest_sha256": _sha256(pool_root / "pool_manifest.json"),
        "arena_summary": result["summary"],
        "candidate_source_sha256": _sha256(instance.policy_path),
    })
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "candidate_summary.json").write_text(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    candidate_manifest = {
        "schema_version": "meta-specialist-native-policy-candidate-manifest-v1",
        "candidate_id": args.asset_id,
        "candidate_family": "guarded-native-score-v1" if min_score_gain > 0.0 else "native-score-v1",
        "pair_identity": {
            "asset_id": args.asset_id,
            "policy_sha256": summary["candidate_policy_sha256"],
            "deck_sha256": summary["candidate_deck_sha256"],
            "source_sha256": summary["candidate_source_sha256"],
            "config_sha256": summary["candidate_config_sha256"],
        },
        "config": {
            "env": env,
            "biases": biases,
            "min_score_gain": min_score_gain,
        },
        "reference_ids": list(refs),
        "reference_config_sha256": _sha256(args.reference_config.resolve()) if args.reference_config.is_file() else None,
        "pool_manifest_sha256": summary["pool_manifest_sha256"],
        "evaluator_implementation_sha256": summary["evaluator_implementation_sha256"],
        "runner_ref": RUNNER_REF_V1,
        "research_only": True,
        "promotion_authority": False,
        "training_authority": False,
        "submission_authority": False,
        "native_first": True,
        "fail_closed": True,
        "notes": [
            "Candidate keeps the upstream native deck and policy bytes intact.",
            "Guarded mode changes only single-choice MAIN selections whose bounded native score gain exceeds the configured threshold.",
            "Candidate selection is not permission to train, promote, or submit the upstream asset.",
        ],
    }
    manifest_text = json.dumps(candidate_manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    (args.output / "candidate_manifest.json").write_text(manifest_text, encoding="utf-8")
    summary["candidate_manifest_sha256"] = hashlib.sha256(manifest_text.encode("utf-8")).hexdigest()
    (args.output / "candidate_summary.json").write_text(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "NATIVE_CANDIDATE_SCHEMA_V1",
    "NativeCandidatePilotError",
    "RUNNER_REF_V1",
    "build_native_candidate_games_v1",
    "run_native_candidate_game_v1",
    "summarize_native_candidate_rows_v1",
]
