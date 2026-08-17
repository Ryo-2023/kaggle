"""Research-only native pilot for materialized deck mutation candidates.

The subject policy is always the byte-identified ``plamen06_steel`` native
policy.  Only the candidate deck path changes.  This module owns no training,
promotion, champion, or submission authority; it only creates game cells and
delegates execution to the existing bounded parallel evaluator.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Mapping, Sequence

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

from mage_ptcg.deck_io import read_deck_csv  # noqa: E402
from mage_ptcg.meta_specialist.joint_optimization_v1 import (  # noqa: E402
    deck_multiset_identity_v1,
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


DECK_MUTATION_NATIVE_PILOT_SCHEMA_V1 = "meta-specialist-deck-mutation-native-pilot-v1"
RUNNER_REF_V1 = (
    "scripts.run_deck_mutation_native_pilot_v1:run_deck_mutation_native_pilot_game_v1"
)
DEFAULT_MANIFEST_V1 = _ROOT / "runs/final-sprint-autonomous/deck-mutation-plamen-v1/candidates.json"
DEFAULT_REFERENCE_CONFIG_V1 = _ROOT / "configs/meta_specialist/performance_first_broad_pool_v1.json"
DEFAULT_BASELINE_ARTIFACT_V1 = _ROOT / "docs/evidence/strong-asset-top3-pooled1536-20260812.md"
_SHA_HEX = frozenset("0123456789abcdef")
_POOL_CACHE_V1: dict[str, Mapping[str, OpponentInstanceV1]] = {}


class DeckMutationNativePilotError(ValueError):
    """Raised when the candidate/native pilot contract is not closed."""


def _sha256(path: Path | str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _require_sha(value: object, name: str) -> str:
    if type(value) is not str or len(value) != 64 or any(char not in _SHA_HEX for char in value):
        raise DeckMutationNativePilotError(f"{name} must be a lowercase SHA-256")
    return value


def _atomic_write_json(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.tmp-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(dict(payload), handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


@dataclass(frozen=True, slots=True)
class CandidateDeckV1:
    candidate_id: str
    deck_csv_path: Path
    deck_csv_sha256: str
    deck_multiset_sha256: str
    card_ids: tuple[int, ...]
    authority: Mapping[str, bool]


@dataclass(frozen=True, slots=True)
class CandidateManifestV1:
    manifest_path: Path
    manifest_sha256: str
    subject_id: str
    parent_policy_path: Path
    parent_policy_sha256: str
    parent_deck_path: Path
    parent_deck_file_sha256: str
    parent_deck_multiset_sha256: str
    candidates: tuple[CandidateDeckV1, ...]

    @property
    def pool_policy_sha256(self) -> str:
        """The currently materialized native pool policy identity."""
        return self.parent_policy_sha256


def _resolve_path(value: object, *, base: Path) -> Path:
    if type(value) is not str or not value.strip():
        raise DeckMutationNativePilotError("path fields must be non-empty strings")
    path = Path(value)
    return path if path.is_absolute() else (base / path).resolve()


def _false_authority(value: object, *, name: str) -> dict[str, bool]:
    if not isinstance(value, Mapping):
        raise DeckMutationNativePilotError(f"{name} authority must be an object")
    keys = ("promotion_allowed", "training_allowed", "submission_allowed")
    result = {key: value.get(key) for key in keys}
    if any(type(result[key]) is not bool for key in keys) or any(result[key] for key in keys):
        raise DeckMutationNativePilotError(f"{name} authority must be all false")
    return {key: False for key in keys}


def _false_manifest_authority(value: object) -> None:
    """Validate the manifest's additional execute flag without granting it."""
    if not isinstance(value, Mapping) or type(value.get("execute_allowed")) is not bool or value.get("execute_allowed"):
        raise DeckMutationNativePilotError("manifest execute authority must be false")
    _false_authority(value, name="manifest")


def load_candidate_manifest_v1(path: Path | str = DEFAULT_MANIFEST_V1) -> CandidateManifestV1:
    """Load and byte-bind a candidate-only manifest; reject authority drift."""
    manifest_path = Path(path).resolve()
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DeckMutationNativePilotError(f"cannot read candidate manifest: {manifest_path}") from exc
    if not isinstance(payload, Mapping):
        raise DeckMutationNativePilotError("candidate manifest must be an object")
    if payload.get("candidate_status") != "candidate_only" or payload.get("research_only") is not True:
        raise DeckMutationNativePilotError("candidate manifest must be research-only candidate_only")
    _false_manifest_authority(payload.get("authority"))
    parent = payload.get("parent")
    if not isinstance(parent, Mapping):
        raise DeckMutationNativePilotError("candidate manifest parent is required")
    subject_id = parent.get("asset_id")
    if type(subject_id) is not str or subject_id != "plamen06_steel":
        raise DeckMutationNativePilotError("native pilot subject must be plamen06_steel")
    parent_policy_path = _resolve_path(parent.get("policy_path"), base=_ROOT)
    parent_deck_path = _resolve_path(parent.get("deck_path"), base=_ROOT)
    if not parent_policy_path.is_file() or not parent_deck_path.is_file():
        raise DeckMutationNativePilotError("parent native policy/deck is missing")
    parent_policy_sha = _require_sha(parent.get("policy_sha256"), "parent.policy_sha256")
    parent_deck_file_sha = _require_sha(parent.get("deck_file_sha256"), "parent.deck_file_sha256")
    parent_multiset_sha = _require_sha(parent.get("deck_multiset_sha256"), "parent.deck_multiset_sha256")
    if _sha256(parent_policy_path) != parent_policy_sha:
        raise DeckMutationNativePilotError("parent policy SHA does not match on-disk native policy")
    if _sha256(parent_deck_path) != parent_deck_file_sha:
        raise DeckMutationNativePilotError("parent deck SHA does not match on-disk native deck")
    if deck_multiset_identity_v1(read_deck_csv(parent_deck_path)) != parent_multiset_sha:
        raise DeckMutationNativePilotError("parent deck multiset SHA does not match on-disk deck")
    raw_candidates = payload.get("candidates")
    if not isinstance(raw_candidates, list) or not raw_candidates:
        raise DeckMutationNativePilotError("candidate manifest requires non-empty candidates")
    candidates: list[CandidateDeckV1] = []
    seen_ids: set[str] = set()
    seen_decks: set[str] = {parent_multiset_sha}
    for index, row in enumerate(raw_candidates):
        if not isinstance(row, Mapping):
            raise DeckMutationNativePilotError(f"candidate[{index}] must be an object")
        candidate_id = row.get("candidate_id")
        if type(candidate_id) is not str or not candidate_id or candidate_id in seen_ids:
            raise DeckMutationNativePilotError("candidate IDs must be non-empty and unique")
        seen_ids.add(candidate_id)
        if row.get("candidate_status") != "candidate_only" or row.get("research_only") is not True:
            raise DeckMutationNativePilotError(f"candidate[{candidate_id}] is not candidate-only")
        authority = _false_authority(row.get("authority"), name=f"candidate[{candidate_id}]")
        deck_path = _resolve_path(row.get("deck_csv_path"), base=manifest_path.parent)
        if not deck_path.is_file():
            raise DeckMutationNativePilotError(f"candidate deck is missing: {deck_path}")
        deck_sha = _require_sha(row.get("deck_csv_sha256"), f"candidate[{candidate_id}].deck_csv_sha256")
        multiset_sha = _require_sha(row.get("deck_multiset_sha256"), f"candidate[{candidate_id}].deck_multiset_sha256")
        if _sha256(deck_path) != deck_sha:
            raise DeckMutationNativePilotError(f"candidate[{candidate_id}] deck SHA mismatch")
        cards = tuple(read_deck_csv(deck_path))
        if deck_multiset_identity_v1(cards) != multiset_sha:
            raise DeckMutationNativePilotError(f"candidate[{candidate_id}] multiset SHA mismatch")
        if multiset_sha in seen_decks:
            raise DeckMutationNativePilotError(f"candidate[{candidate_id}] duplicates parent/another candidate deck")
        seen_decks.add(multiset_sha)
        candidates.append(CandidateDeckV1(candidate_id, deck_path, deck_sha, multiset_sha, cards, authority))
    return CandidateManifestV1(
        manifest_path=manifest_path,
        manifest_sha256=_sha256(manifest_path),
        subject_id=subject_id,
        parent_policy_path=parent_policy_path,
        parent_policy_sha256=parent_policy_sha,
        parent_deck_path=parent_deck_path,
        parent_deck_file_sha256=parent_deck_file_sha,
        parent_deck_multiset_sha256=parent_multiset_sha,
        candidates=tuple(candidates),
    )


def _reference_ids_v1(path: Path, *, subject_id: str, pool: Mapping[str, OpponentInstanceV1]) -> tuple[str, ...]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DeckMutationNativePilotError(f"cannot read reference config: {path}") from exc
    if not isinstance(payload, Mapping) or payload.get("promotion_authority") is not False:
        raise DeckMutationNativePilotError("reference config must be research-only")
    raw = payload.get("opponent_ids")
    if not isinstance(raw, list):
        raise DeckMutationNativePilotError("reference config requires opponent_ids")
    selected = tuple(item for item in raw if type(item) is str and item != subject_id and item in pool)
    if len(raw) != 24 or len(selected) != 23 or len(set(selected)) != len(selected):
        raise DeckMutationNativePilotError(
            f"expected 24 reference IDs and 23 non-self opponents, got {len(raw)}/{len(selected)}"
        )
    return selected


def _identity_v1(instance: OpponentInstanceV1) -> dict[str, object]:
    return {
        "policy_sha256": _sha256(instance.policy_path),
        "deck_sha256": _sha256(instance.deck_csv_path),
        "deck_csv_path": instance.deck_csv_path,
        "usage_boundary": instance.usage_boundary,
        "source": instance.source,
    }


def build_native_candidate_games_v1(
    manifest: CandidateManifestV1,
    *,
    reference_config_path: Path | str = DEFAULT_REFERENCE_CONFIG_V1,
    pool_root: Path | str = _ROOT / "opponents",
    games_per_opponent_seat: int = 2,
    base_seed: int = 9_500_000,
    block_id: str = "deck-mutation-plamen-v1-native-pilot",
    max_steps: int = 2_000,
    timeout_seconds: float = 600.0,
) -> tuple[EvaluationGameV1, ...]:
    if not isinstance(manifest, CandidateManifestV1):
        raise DeckMutationNativePilotError("manifest must be CandidateManifestV1")
    if type(games_per_opponent_seat) is not int or games_per_opponent_seat <= 0:
        raise DeckMutationNativePilotError("games_per_opponent_seat must be positive")
    pool_root = Path(pool_root).resolve()
    pool = load_opponent_pool_v1(pool_root)
    subject = resolve_opponent_v1(pool, manifest.subject_id, subject_deck_csv_path=str(manifest.parent_deck_path))
    if _sha256(subject.policy_path) != manifest.parent_policy_sha256:
        raise DeckMutationNativePilotError("native subject policy changed after manifest validation")
    references = _reference_ids_v1(Path(reference_config_path).resolve(), subject_id=manifest.subject_id, pool=pool)
    games: list[EvaluationGameV1] = []
    ordinal = 0
    for candidate in manifest.candidates:
        for opponent_id in references:
            identity = _identity_v1(pool[opponent_id])
            for seat in (0, 1):
                for repetition in range(games_per_opponent_seat):
                    games.append(
                        EvaluationGameV1(
                            game_id=f"{block_id}-{candidate.candidate_id[:12]}-{opponent_id}-seat{seat}-g{repetition:04d}",
                            block_id=block_id,
                            policy_id=f"native-plamen06_steel:{candidate.candidate_id}",
                            policy_sha256=manifest.parent_policy_sha256,
                            deck_id=candidate.candidate_id,
                            deck_sha256=candidate.deck_csv_sha256,
                            opponent_id=opponent_id,
                            opponent_identity={
                                "policy_sha256": identity["policy_sha256"],
                                "deck_sha256": identity["deck_sha256"],
                                "usage_boundary": identity["usage_boundary"],
                                "source": identity["source"],
                            },
                            opponent_deck_sha256=str(identity["deck_sha256"]),
                            seat=seat,
                            seed=base_seed + ordinal,
                            max_steps=max_steps,
                            timeout_seconds=float(timeout_seconds),
                            subject_deck_path=str(candidate.deck_csv_path),
                            opponent_deck_path=str(identity["deck_csv_path"]),
                            policy_agent_name="native:plamen06_steel",
                            opponent_agent_name=opponent_id,
                            runner_ref=RUNNER_REF_V1,
                            metadata={
                                "schema_version": DECK_MUTATION_NATIVE_PILOT_SCHEMA_V1,
                                "candidate_id": candidate.candidate_id,
                                "candidate_deck_csv_sha256": candidate.deck_csv_sha256,
                                "candidate_deck_multiset_sha256": candidate.deck_multiset_sha256,
                                "native_policy_sha256": manifest.parent_policy_sha256,
                                "native_policy_path": str(manifest.parent_policy_path),
                                "manifest_path": str(manifest.manifest_path),
                                "manifest_sha256": manifest.manifest_sha256,
                                "pool_root": str(pool_root),
                                "repetition": repetition,
                                "promotion_authority": False,
                                "training_authority": False,
                                "submission_authority": False,
                                "research_only": True,
                            },
                        )
                    )
                    ordinal += 1
    return tuple(games)


def _pool_for_worker_v1(pool_root: Path) -> Mapping[str, OpponentInstanceV1]:
    key = str(pool_root.resolve())
    if key not in _POOL_CACHE_V1:
        _POOL_CACHE_V1[key] = load_opponent_pool_v1(pool_root)
    return _POOL_CACHE_V1[key]


def run_deck_mutation_native_pilot_game_v1(payload: Mapping[str, object]) -> Mapping[str, object]:
    game = _game_from_payload(payload)
    pool = _pool_for_worker_v1(Path(str(game.metadata["pool_root"])))
    subject = resolve_opponent_v1(pool, "plamen06_steel", subject_deck_csv_path=game.subject_deck_path)
    if _sha256(subject.policy_path) != str(game.metadata["native_policy_sha256"]):
        raise DeckMutationNativePilotError("native policy bytes changed in worker")
    opponent = resolve_opponent_v1(pool, game.opponent_id, subject_deck_csv_path=game.subject_deck_path)
    subject_factory = build_opponent_agent_factory_v1(subject)
    opponent_factory = build_opponent_agent_factory_v1(opponent)
    subject_first = game.seat == 0
    return run_match(
        deck_a_path=game.subject_deck_path if subject_first else opponent.deck_csv_path,
        deck_b_path=opponent.deck_csv_path if subject_first else game.subject_deck_path,
        agent_a_name="native:plamen06_steel" if subject_first else game.opponent_id,
        agent_b_name=game.opponent_id if subject_first else "native:plamen06_steel",
        seed=game.seed,
        max_steps=game.max_steps,
        output_dir=str(_ROOT / "runs" / "deck-mutation-native-pilot-worker" / game.game_id),
        save_html=False,
        save_result=False,
        agent_a_factory=subject_factory if subject_first else opponent_factory,
        agent_b_factory=opponent_factory if subject_first else subject_factory,
    )


def summarize_native_candidate_rows_v1(rows: Sequence[Mapping[str, object]]) -> dict[str, dict[str, object]]:
    grouped: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    for row in rows:
        metadata = row.get("metadata", {})
        candidate_id = metadata.get("candidate_id") if isinstance(metadata, Mapping) else None
        if not isinstance(candidate_id, str) or not candidate_id:
            candidate_id = str(row.get("deck_id", "unknown"))
        grouped[candidate_id].append(row)
    summary: dict[str, dict[str, object]] = {}
    for candidate_id, candidate_rows in grouped.items():
        first_metadata = candidate_rows[0].get("metadata", {})
        metadata = first_metadata if isinstance(first_metadata, Mapping) else {}
        summary[candidate_id] = {
            "candidate_id": candidate_id,
            "candidate_deck_csv_sha256": metadata.get("candidate_deck_csv_sha256", metadata.get("candidate_deck_sha256")),
            "candidate_deck_multiset_sha256": metadata.get("candidate_deck_multiset_sha256"),
            "native_policy_sha256": metadata.get("native_policy_sha256"),
            "research_only": True,
            "promotion_authority": False,
            "training_authority": False,
            "submission_authority": False,
            **aggregate_ledger_v1(candidate_rows),
        }
    return dict(sorted(summary.items()))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST_V1)
    parser.add_argument("--reference-config", type=Path, default=DEFAULT_REFERENCE_CONFIG_V1)
    parser.add_argument("--pool-root", type=Path, default=_ROOT / "opponents")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--worker-recycle-games", type=int, default=16)
    parser.add_argument("--games-per-opponent-seat", type=int, default=2)
    parser.add_argument("--base-seed", type=int, default=9_500_000)
    parser.add_argument("--timeout-seconds", type=float, default=600.0)
    parser.add_argument("--max-steps", type=int, default=2_000)
    parser.add_argument("--overwrite", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    manifest = load_candidate_manifest_v1(args.manifest)
    games = build_native_candidate_games_v1(
        manifest,
        reference_config_path=args.reference_config,
        pool_root=args.pool_root,
        games_per_opponent_seat=args.games_per_opponent_seat,
        base_seed=args.base_seed,
        max_steps=args.max_steps,
        timeout_seconds=args.timeout_seconds,
    )
    result = run_parallel_cabt_evaluation(
        games,
        output_dir=args.output,
        max_workers=args.workers,
        worker_recycle_games=args.worker_recycle_games,
        overwrite=args.overwrite,
    )
    baseline = DEFAULT_BASELINE_ARTIFACT_V1
    summary = {
        "schema_version": DECK_MUTATION_NATIVE_PILOT_SCHEMA_V1,
        "research_only": True,
        "promotion_authority": False,
        "training_authority": False,
        "submission_authority": False,
        "manifest_path": str(manifest.manifest_path),
        "manifest_sha256": manifest.manifest_sha256,
        "native_policy_sha256": manifest.parent_policy_sha256,
        "requested_games": len(games),
        "opponents_per_candidate": 23,
        "games_per_candidate": 23 * 2 * args.games_per_opponent_seat,
        "evaluator_implementation_sha256": evaluator_implementation_sha256_v1(),
        "native_baseline_artifact": {
            "path": str(baseline),
            "sha256": _sha256(baseline) if baseline.is_file() else None,
            "score_reference": "1102/1536 = 0.7174479167 (pooled plamen native reference)",
        },
        "arena_summary": result["summary"],
        "candidate_summary": summarize_native_candidate_rows_v1(result["rows"]),
    }
    _atomic_write_json(args.output / "candidate_summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "CandidateDeckV1",
    "CandidateManifestV1",
    "DECK_MUTATION_NATIVE_PILOT_SCHEMA_V1",
    "DeckMutationNativePilotError",
    "RUNNER_REF_V1",
    "build_native_candidate_games_v1",
    "load_candidate_manifest_v1",
    "run_deck_mutation_native_pilot_game_v1",
    "summarize_native_candidate_rows_v1",
]
