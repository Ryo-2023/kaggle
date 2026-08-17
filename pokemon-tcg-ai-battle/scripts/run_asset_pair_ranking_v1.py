"""Native deck+policy asset ranking for the performance-first arena.

This is a research-only adapter.  It ranks the *original* external pair in the
opponent pool; it does not distil a policy, alter ``main.py``, or imply that a
``local_eval_only`` asset is eligible for training or submission.  Every game
is routed through the bounded spawn evaluator so faults remain in the requested
denominator and the pair identity is retained in the ledger.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import sys
from typing import Mapping, Sequence

_ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(_ROOT), str(_ROOT / "src")]

from mage_ptcg.meta_specialist.opponent_pool_v1 import (  # noqa: E402
    OpponentInstanceV1,
    build_opponent_agent_factory_v1,
    default_pool_root_v1,
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


ASSET_PAIR_RANKING_SCHEMA_V1 = "meta-specialist-asset-pair-ranking-v1"
ASSET_PAIR_RANKING_RUNNER_REF_V1 = (
    "scripts.run_asset_pair_ranking_v1:run_native_asset_pair_game_v1"
)
DEFAULT_REFERENCE_CONFIG_V1 = (
    _ROOT / "configs/meta_specialist/performance_first_broad_pool_v1.json"
)
_SHA256_HEX = frozenset("0123456789abcdef")
_POOL_CACHE_V1: dict[str, Mapping[str, OpponentInstanceV1]] = {}


class AssetPairRankingError(ValueError):
    """Raised when an asset inventory or ranking spec is not closed."""


def _require_text(value: object, name: str) -> str:
    if type(value) is not str or not value.strip():
        raise AssetPairRankingError(f"{name} must be a non-empty string")
    return value


def _require_sha(value: object, name: str) -> str:
    if (
        type(value) is not str
        or len(value) != 64
        or any(char not in _SHA256_HEX for char in value)
    ):
        raise AssetPairRankingError(f"{name} must be a lowercase SHA-256 hex string")
    return value


def _sha256(path: Path | str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@dataclass(frozen=True, slots=True)
class AssetPairV1:
    """One deck/policy pair that can be ranked as a native external agent."""

    asset_id: str
    deck_csv_path: Path
    policy_path: Path
    deck_sha256: str
    policy_sha256: str
    canonical_deck_hash: str
    smoke_ok: bool
    usage_boundary: str
    source: str

    def __post_init__(self) -> None:
        _require_text(self.asset_id, "asset_id")
        if not isinstance(self.deck_csv_path, Path) or not isinstance(self.policy_path, Path):
            raise AssetPairRankingError("asset paths must be pathlib.Path values")
        _require_sha(self.deck_sha256, "deck_sha256")
        _require_sha(self.policy_sha256, "policy_sha256")
        _require_sha(self.canonical_deck_hash, "canonical_deck_hash")
        if type(self.smoke_ok) is not bool:
            raise AssetPairRankingError("smoke_ok must be bool")
        _require_text(self.usage_boundary, "usage_boundary")
        _require_text(self.source, "source")


def _load_manifest_rows_v1(pool_root: Path) -> Mapping[str, Mapping[str, object]]:
    manifest_path = pool_root / "pool_manifest.json"
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AssetPairRankingError(f"cannot read pool manifest: {manifest_path}") from exc
    rows = raw if isinstance(raw, list) else raw.get("opponents", raw)
    if isinstance(rows, dict):
        rows = list(rows.values())
    if not isinstance(rows, list) or not rows:
        raise AssetPairRankingError("pool manifest must contain a non-empty list")
    output: dict[str, Mapping[str, object]] = {}
    for row in rows:
        if not isinstance(row, Mapping) or type(row.get("id")) is not str:
            raise AssetPairRankingError("pool manifest rows require string id")
        asset_id = str(row["id"])
        if asset_id in output:
            raise AssetPairRankingError(f"duplicate asset id: {asset_id}")
        output[asset_id] = dict(row)
    return output


def load_asset_inventory_v1(pool_root: Path | str = _ROOT / "opponents") -> tuple[AssetPairV1, ...]:
    """Load and byte-bind every external pool asset.

    ``load_opponent_pool_v1`` remains the authority for policy/deck existence,
    policy SHA and structural deck validation.  The ranking inventory adds the
    raw deck SHA and the manifest's smoke/usage metadata.
    """
    root = Path(pool_root).resolve()
    rows = _load_manifest_rows_v1(root)
    pool = load_opponent_pool_v1(root)
    if set(rows) != set(pool):
        raise AssetPairRankingError("manifest and verified pool ids differ")
    assets: list[AssetPairV1] = []
    for asset_id in sorted(pool):
        instance = pool[asset_id]
        row = rows[asset_id]
        deck_path = Path(instance.deck_csv_path).resolve()
        policy_path = Path(instance.policy_path).resolve()
        assets.append(
            AssetPairV1(
                asset_id=asset_id,
                deck_csv_path=deck_path,
                policy_path=policy_path,
                deck_sha256=_sha256(deck_path),
                policy_sha256=_sha256(policy_path),
                canonical_deck_hash=str(instance.canonical_deck_hash),
                smoke_ok=bool(row.get("smoke_ok", False)),
                usage_boundary=str(instance.usage_boundary),
                source=str(instance.source),
            )
        )
    return tuple(assets)


def filter_asset_inventory_v1(
    assets: Sequence[AssetPairV1], *, include_smoke_false: bool = False
) -> tuple[AssetPairV1, ...]:
    """Return sorted assets, optionally retaining smoke-failed diagnostics."""
    selected = [asset for asset in assets if include_smoke_false or asset.smoke_ok]
    return tuple(sorted(selected, key=lambda asset: asset.asset_id))


def select_reference_opponents_v1(
    subject_id: str,
    reference_ids: Sequence[str],
    *,
    available_ids: Sequence[str],
    fallback_ids: Sequence[str] = (),
) -> tuple[str, ...]:
    """Select references while removing only the candidate's own self-play.

    If the requested reference list contains the subject, the first available
    fallback fills that slot.  Missing/duplicate IDs are ignored rather than
    silently converted to a self mirror.
    """
    _require_text(subject_id, "subject_id")
    available = set(str(item) for item in available_ids)
    selected: list[str] = []
    subject_was_requested = False
    for opponent_id in reference_ids:
        if type(opponent_id) is not str or not opponent_id:
            raise AssetPairRankingError("reference_ids must contain non-empty strings")
        if opponent_id == subject_id:
            subject_was_requested = True
            continue
        if opponent_id not in available or opponent_id in selected:
            continue
        selected.append(opponent_id)
    if subject_was_requested:
        for opponent_id in fallback_ids:
            if (
                type(opponent_id) is str
                and opponent_id in available
                and opponent_id != subject_id
                and opponent_id not in selected
            ):
                selected.append(opponent_id)
                break
    if not selected:
        raise AssetPairRankingError(f"no reference opponent remains for {subject_id}")
    return tuple(selected)


def _placeholder_sha_v1(seed: str) -> str:
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def build_asset_ranking_games_v1(
    assets: Sequence[AssetPairV1],
    *,
    reference_ids: Sequence[str],
    available_ids: Sequence[str],
    fallback_ids: Sequence[str] = (),
    games_per_opponent_seat: int = 2,
    base_seed: int = 9_000_000,
    block_id: str = "asset-ranking-primary",
    max_steps: int = 2_000,
    timeout_seconds: float = 1_200.0,
    opponent_identities: Mapping[str, Mapping[str, object]] | None = None,
) -> tuple[EvaluationGameV1, ...]:
    """Create native-pair games with balanced seats and deterministic seeds."""
    if type(games_per_opponent_seat) is not int or games_per_opponent_seat <= 0:
        raise AssetPairRankingError("games_per_opponent_seat must be positive")
    if type(base_seed) is not int or base_seed < 0:
        raise AssetPairRankingError("base_seed must be a nonnegative integer")
    if type(max_steps) is not int or max_steps <= 0:
        raise AssetPairRankingError("max_steps must be positive")
    if type(timeout_seconds) not in (int, float) or float(timeout_seconds) <= 0:
        raise AssetPairRankingError("timeout_seconds must be positive")
    identities = opponent_identities or {}
    available_set = set(available_ids)
    games: list[EvaluationGameV1] = []
    ordinal = 0
    for asset in sorted(assets, key=lambda item: item.asset_id):
        opponents = select_reference_opponents_v1(
            asset.asset_id,
            reference_ids,
            available_ids=available_set,
            fallback_ids=fallback_ids,
        )
        for opponent_id in opponents:
            identity = identities.get(opponent_id, {})
            opponent_deck_path = Path(str(identity.get("deck_csv_path", f"/tmp/{opponent_id}/deck.csv")))
            opponent_deck_sha = str(identity.get("deck_sha256", _placeholder_sha_v1(f"deck:{opponent_id}")))
            opponent_policy_sha = str(identity.get("policy_sha256", _placeholder_sha_v1(f"policy:{opponent_id}")))
            _require_sha(opponent_deck_sha, f"opponent[{opponent_id}].deck_sha256")
            _require_sha(opponent_policy_sha, f"opponent[{opponent_id}].policy_sha256")
            for seat in (0, 1):
                for repetition in range(games_per_opponent_seat):
                    game_id = (
                        f"{block_id}-{asset.asset_id}-{opponent_id}-"
                        f"seat{seat}-g{repetition:04d}"
                    )
                    games.append(
                        EvaluationGameV1(
                            game_id=game_id,
                            block_id=block_id,
                            policy_id=f"native-{asset.asset_id}",
                            policy_sha256=asset.policy_sha256,
                            deck_id=asset.asset_id,
                            deck_sha256=asset.deck_sha256,
                            opponent_id=opponent_id,
                            opponent_identity={
                                "policy_sha256": opponent_policy_sha,
                                "deck_sha256": opponent_deck_sha,
                                "usage_boundary": str(identity.get("usage_boundary", "unknown")),
                                "source": str(identity.get("source", "unknown")),
                            },
                            opponent_deck_sha256=opponent_deck_sha,
                            seat=seat,
                            seed=base_seed + ordinal,
                            max_steps=max_steps,
                            timeout_seconds=float(timeout_seconds),
                            subject_deck_path=str(asset.deck_csv_path),
                            opponent_deck_path=str(opponent_deck_path),
                            policy_agent_name=f"native:{asset.asset_id}",
                            opponent_agent_name=opponent_id,
                            runner_ref=ASSET_PAIR_RANKING_RUNNER_REF_V1,
                            metadata={
                                "arena_schema": ASSET_PAIR_RANKING_SCHEMA_V1,
                                "subject_id": asset.asset_id,
                                "subject_policy_sha256": asset.policy_sha256,
                                "subject_deck_sha256": asset.deck_sha256,
                                "subject_canonical_deck_hash": asset.canonical_deck_hash,
                                "subject_smoke_ok": asset.smoke_ok,
                                "subject_usage_boundary": asset.usage_boundary,
                                "subject_source": asset.source,
                                "opponent_usage_boundary": str(identity.get("usage_boundary", "unknown")),
                                "opponent_source": str(identity.get("source", "unknown")),
                                "repetition": repetition,
                            },
                        )
                    )
                    ordinal += 1
    return tuple(games)


def _pool_for_worker_v1(pool_root: Path) -> Mapping[str, OpponentInstanceV1]:
    key = str(pool_root.resolve())
    pool = _POOL_CACHE_V1.get(key)
    if pool is None:
        pool = load_opponent_pool_v1(pool_root)
        _POOL_CACHE_V1[key] = pool
    return pool


def run_native_asset_pair_game_v1(payload: Mapping[str, object]) -> Mapping[str, object]:
    """Run one native external-pair match in a spawn worker."""
    game = _game_from_payload(payload)
    pool_root = Path(str(game.metadata.get("pool_root", _ROOT / "opponents"))).resolve()
    pool = _pool_for_worker_v1(pool_root)
    subject_id = str(game.metadata.get("subject_id", game.deck_id))
    subject = resolve_opponent_v1(
        pool, subject_id, subject_deck_csv_path=game.subject_deck_path
    )
    opponent = resolve_opponent_v1(
        pool, game.opponent_id, subject_deck_csv_path=game.subject_deck_path
    )
    subject_factory = build_opponent_agent_factory_v1(subject)
    opponent_factory = build_opponent_agent_factory_v1(opponent)
    subject_first = game.seat == 0
    return run_match(
        deck_a_path=game.subject_deck_path if subject_first else opponent.deck_csv_path,
        deck_b_path=opponent.deck_csv_path if subject_first else game.subject_deck_path,
        agent_a_name=f"native:{subject_id}" if subject_first else game.opponent_id,
        agent_b_name=game.opponent_id if subject_first else f"native:{subject_id}",
        seed=game.seed,
        max_steps=game.max_steps,
        output_dir=str(_ROOT / "runs" / "asset-ranking-worker" / game.game_id),
        save_html=False,
        save_result=False,
        agent_a_factory=subject_factory if subject_first else opponent_factory,
        agent_b_factory=opponent_factory if subject_first else subject_factory,
    )


def _counter_to_sorted_dict(counter: Counter[str]) -> dict[str, int]:
    return {key: int(counter[key]) for key in sorted(counter)}


def summarize_asset_rows_v1(rows: Sequence[Mapping[str, object]]) -> tuple[dict[str, object], ...]:
    """Build per-asset ledgers; faults stay in ``requested_games``."""
    grouped: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    for row in rows:
        metadata = row.get("metadata", {})
        subject_id = metadata.get("subject_id") if isinstance(metadata, Mapping) else None
        if not isinstance(subject_id, str) or not subject_id:
            subject_id = str(row.get("deck_id", row.get("policy_id", "unknown")))
        grouped[subject_id].append(row)
    ranking: list[dict[str, object]] = []
    for asset_id, asset_rows in grouped.items():
        ledger = aggregate_ledger_v1(asset_rows)
        first = asset_rows[0]
        metadata = first.get("metadata", {})
        metadata = metadata if isinstance(metadata, Mapping) else {}
        seats: dict[str, dict[str, object]] = {}
        opponents: dict[str, dict[str, object]] = {}
        for label, key in (("seat", "seat"), ("opponent", "opponent_id")):
            bucket: dict[str, list[Mapping[str, object]]] = defaultdict(list)
            for row in asset_rows:
                bucket[str(row.get(key))].append(row)
            target = seats if label == "seat" else opponents
            for bucket_id, bucket_rows in sorted(bucket.items()):
                target[bucket_id] = aggregate_ledger_v1(bucket_rows)
        ranking.append(
            {
                "asset_id": asset_id,
                "policy_id": first.get("policy_id"),
                "policy_sha256": first.get("policy_sha256"),
                "deck_sha256": first.get("deck_sha256"),
                "canonical_deck_hash": metadata.get("subject_canonical_deck_hash"),
                "smoke_ok": metadata.get("subject_smoke_ok"),
                "usage_boundary": metadata.get("subject_usage_boundary"),
                "source": metadata.get("subject_source"),
                **ledger,
                "seats": seats,
                "opponents": opponents,
            }
        )
    ranking.sort(
        key=lambda item: (
            -(float(item["score_rate"]) if item.get("score_rate") is not None else -1.0),
            str(item["asset_id"]),
        )
    )
    return tuple(ranking)


def _load_reference_ids_v1(path: Path) -> tuple[str, ...]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping) or not isinstance(payload.get("opponent_ids"), list):
        raise AssetPairRankingError("reference config requires opponent_ids list")
    if payload.get("promotion_authority") is not False:
        raise AssetPairRankingError("reference config must be research-only")
    return tuple(str(item) for item in payload["opponent_ids"])


def _identity_map_v1(pool: Mapping[str, OpponentInstanceV1]) -> dict[str, dict[str, object]]:
    return {
        opponent_id: {
            "policy_sha256": _sha256(instance.policy_path),
            "deck_sha256": _sha256(instance.deck_csv_path),
            "deck_csv_path": instance.deck_csv_path,
            "usage_boundary": instance.usage_boundary,
            "source": instance.source,
        }
        for opponent_id, instance in pool.items()
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pool-root", type=Path, default=_ROOT / "opponents")
    parser.add_argument("--reference-config", type=Path, default=DEFAULT_REFERENCE_CONFIG_V1)
    parser.add_argument("--asset-ids", default="", help="comma-separated ids; default is all assets")
    parser.add_argument("--include-smoke-false", action="store_true")
    parser.add_argument("--games-per-opponent-seat", type=int, default=2)
    parser.add_argument("--base-seed", type=int, default=9_000_000)
    parser.add_argument("--block-id", default="asset-ranking-primary")
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--worker-recycle-games", type=int, default=32)
    parser.add_argument("--timeout-seconds", type=float, default=1_200.0)
    parser.add_argument("--max-steps", type=int, default=2_000)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.games_per_opponent_seat <= 0 or args.workers <= 0 or args.worker_recycle_games <= 0:
        raise SystemExit("games/workers/recycle values must be positive")
    if args.timeout_seconds <= 0 or args.max_steps <= 0:
        raise SystemExit("timeout/max-steps values must be positive")
    pool_root = args.pool_root.resolve()
    pool = load_opponent_pool_v1(pool_root)
    assets = filter_asset_inventory_v1(
        load_asset_inventory_v1(pool_root), include_smoke_false=args.include_smoke_false
    )
    if args.asset_ids.strip():
        requested = {item.strip() for item in args.asset_ids.split(",") if item.strip()}
        unknown = requested - {asset.asset_id for asset in assets}
        if unknown:
            raise SystemExit(f"unknown asset ids: {sorted(unknown)}")
        assets = tuple(asset for asset in assets if asset.asset_id in requested)
    if not assets:
        raise SystemExit("no assets selected")
    reference_ids = _load_reference_ids_v1(args.reference_config)
    available_ids = tuple(sorted(pool))
    fallback_ids = tuple(
        item for item in ("tomatomato_archaludon", "official_random", "lucifer19_battlecore")
        if item in pool
    )
    games = build_asset_ranking_games_v1(
        assets,
        reference_ids=reference_ids,
        available_ids=available_ids,
        fallback_ids=fallback_ids,
        games_per_opponent_seat=args.games_per_opponent_seat,
        base_seed=args.base_seed,
        block_id=args.block_id,
        max_steps=args.max_steps,
        timeout_seconds=args.timeout_seconds,
        opponent_identities=_identity_map_v1(pool),
    )
    games = tuple(
        type(game)(
            **{
                **game.to_payload(),
                "metadata": {**dict(game.metadata), "pool_root": str(pool_root)},
            }
        )
        for game in games
    )
    result = run_parallel_cabt_evaluation(
        games,
        output_dir=args.output,
        max_workers=args.workers,
        worker_recycle_games=args.worker_recycle_games,
        overwrite=args.overwrite,
    )
    ranking = summarize_asset_rows_v1(result["rows"])
    manifest_sha = _sha256(pool_root / "pool_manifest.json")
    summary = {
        "schema_version": ASSET_PAIR_RANKING_SCHEMA_V1,
        "research_only": True,
        "promotion_authority": False,
        "training_authority": False,
        "submission_authority": False,
        "asset_count": len(assets),
        "requested_games": len(games),
        "games_per_opponent_seat": args.games_per_opponent_seat,
        "reference_ids": list(reference_ids),
        "include_smoke_false": args.include_smoke_false,
        "pool_manifest_sha256": manifest_sha,
        "evaluator_implementation_sha256": evaluator_implementation_sha256_v1(),
        "ranking": list(ranking),
        "arena_summary": result["summary"],
    }
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "asset_ranking.json").write_text(
        json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"top": list(ranking[:10]), "arena_summary": result["summary"]}, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "ASSET_PAIR_RANKING_RUNNER_REF_V1",
    "ASSET_PAIR_RANKING_SCHEMA_V1",
    "AssetPairRankingError",
    "AssetPairV1",
    "build_asset_ranking_games_v1",
    "filter_asset_inventory_v1",
    "load_asset_inventory_v1",
    "run_native_asset_pair_game_v1",
    "select_reference_opponents_v1",
    "summarize_asset_rows_v1",
]
