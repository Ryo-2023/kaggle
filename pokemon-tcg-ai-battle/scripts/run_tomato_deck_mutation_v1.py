"""Research-only deck mutation screen for the native tomato pair.

This wrapper is deliberately narrow: it materializes eight hash-bound 1/2-swap
deck candidates around ``tomatomato_archaludon`` and evaluates them with the
unaltered native policy.  It never edits a production deck or agent and it
never grants training, promotion, package, or submission authority.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Mapping, Sequence

_ROOT = Path(__file__).resolve().parents[1]

import sys

if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

from mage_ptcg.deck_io import read_deck_csv, validate_deck  # noqa: E402
from mage_ptcg.meta_specialist.deck_mutation_v1 import (  # noqa: E402
    generate_deck_mutation_candidates_v1,
)
from mage_ptcg.meta_specialist.joint_optimization_v1 import (  # noqa: E402
    CoreSignatureV1,
    deck_multiset_identity_v1,
)
from mage_ptcg.meta_specialist.opponent_pool_v1 import load_opponent_pool_v1  # noqa: E402
from scripts.parallel_cabt_evaluator_v1 import (  # noqa: E402
    EvaluationGameV1,
    aggregate_ledger_v1,
    evaluator_implementation_sha256_v1,
    run_parallel_cabt_evaluation,
)
from scripts.run_native_policy_candidate_pilot_v1 import (  # noqa: E402
    _config_sha,
    _sha256,
    build_native_candidate_games_v1,
)
from scripts.test_sim import load_known_card_ids  # noqa: E402


SCHEMA_V1 = "meta-specialist-tomato-deck-mutation-v1"
SUBJECT_ID_V1 = "tomatomato_archaludon"
ARCHETYPE_ID_V1 = "archaludon-cinderace"
CORE_SIGNATURE_V1 = CoreSignatureV1(
    archetype_id=ARCHETYPE_ID_V1,
    required_counts={57: 1, 169: 4, 190: 4, 666: 4},
)
# The native policy contains explicit scoring for this card set.  Restricting
# mutations to it avoids silently adding cards the parent policy cannot rank.
REPLACEMENT_POOL_V1 = (8, 1097, 1121, 1122, 1147, 1152, 1159, 1182, 1185, 1213, 1227, 1244)
COMMON_PROTOCOL_V1 = _ROOT / "configs/meta_specialist/performance_first_broad_pool_v1.json"
_FALSE_AUTHORITY_V1 = {
    "execute_allowed": False,
    "promotion_allowed": False,
    "submission_allowed": False,
    "training_allowed": False,
}


class TomatoDeckMutationError(ValueError):
    """Raised when a tomato mutation artifact is not closed and reproducible."""


def _atomic_json(path: Path, value: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.tmp-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(dict(value), handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _write_deck(path: Path, cards: Sequence[int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".deck.tmp-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write("\n".join(str(card) for card in cards) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _canonical_sha(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _reference_ids(path: Path, pool: Mapping[str, object]) -> tuple[str, ...]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping) or payload.get("promotion_authority") is not False:
        raise TomatoDeckMutationError("common reference config must be research-only")
    raw = payload.get("opponent_ids")
    if not isinstance(raw, list) or len(raw) != 24 or len(set(raw)) != 24:
        raise TomatoDeckMutationError("common protocol requires exactly 24 unique opponents")
    if any(type(item) is not str or item not in pool for item in raw):
        raise TomatoDeckMutationError("common protocol contains an unknown opponent")
    return tuple(raw)


def _source_paths(source_root: Path) -> tuple[Path, Path, Path]:
    parent = source_root / "opponents" / SUBJECT_ID_V1
    policy = parent / "main.py"
    deck = parent / "deck.csv"
    card_data = source_root / "data" / "raw" / "EN_Card_Data.csv"
    if not policy.is_file() or not deck.is_file() or not card_data.is_file():
        raise TomatoDeckMutationError("tomato policy/deck/card data is missing")
    return policy.resolve(), deck.resolve(), card_data.resolve()


def prepare_tomato_mutation_manifest_v1(
    *, output_root: Path | str, source_root: Path | str = _ROOT, seed: int = 20260813,
) -> Path:
    """Materialize eight candidate-only 1/2-swap decks and their exact manifest."""
    source_root = Path(source_root).resolve()
    output_root = Path(output_root).resolve()
    policy_path, deck_path, card_data = _source_paths(source_root)
    known_cards = load_known_card_ids(card_data)
    parent_cards = tuple(read_deck_csv(deck_path, known_card_ids=known_cards))
    validate_deck(parent_cards, known_card_ids=known_cards)
    parent_multiset = deck_multiset_identity_v1(parent_cards)
    if CORE_SIGNATURE_V1.violation(parent_cards) is not None:
        raise TomatoDeckMutationError(CORE_SIGNATURE_V1.violation(parent_cards) or "core violation")
    candidates = generate_deck_mutation_candidates_v1(
        base_cards=parent_cards,
        signature=CORE_SIGNATURE_V1,
        replacement_pool=REPLACEMENT_POOL_V1,
        swap_counts=(1, 2),
        candidates_per_swap=4,
        seed=seed,
        known_card_ids=known_cards,
        legality_checker=lambda cards: (True, "structural-only; real CABT probe required before screen"),
    )
    if len(candidates) != 8:
        raise TomatoDeckMutationError(f"expected eight deterministic candidates, got {len(candidates)}")
    rows: list[dict[str, object]] = []
    for candidate in candidates:
        deck_out = output_root / candidate.candidate_id / "deck.csv"
        _write_deck(deck_out, candidate.card_ids)
        if tuple(read_deck_csv(deck_out, known_card_ids=known_cards)) != candidate.card_ids:
            raise TomatoDeckMutationError("candidate deck bytes do not round-trip")
        row = candidate.to_dict()
        row.update({
            "deck_csv_path": str(deck_out),
            "deck_csv_sha256": _sha256(deck_out),
            "deck_multiset_sha256": candidate.deck_multiset_sha256,
            "research_only": True,
        })
        rows.append(row)
    manifest = {
        "schema_version": SCHEMA_V1,
        "subject_id": SUBJECT_ID_V1,
        "archetype_id": ARCHETYPE_ID_V1,
        "candidate_status": "candidate_only",
        "research_only": True,
        "authority": dict(_FALSE_AUTHORITY_V1),
        "source_seed": seed,
        "core_signature": {"archetype_id": ARCHETYPE_ID_V1, "required_counts": dict(CORE_SIGNATURE_V1.required_counts)},
        "replacement_pool": list(REPLACEMENT_POOL_V1),
        "known_card_ids_path": str(card_data),
        "known_card_ids_sha256": _sha256(card_data),
        "generator_source_sha256": _sha256(source_root / "src/mage_ptcg/meta_specialist/deck_mutation_v1.py"),
        "parent": {
            "asset_id": SUBJECT_ID_V1,
            "policy_path": str(policy_path),
            "policy_sha256": _sha256(policy_path),
            "deck_path": str(deck_path),
            "deck_file_sha256": _sha256(deck_path),
            "deck_multiset_sha256": parent_multiset,
            "usage_boundary": "local_eval_only",
            "training_usable": "yes_bounded_local_teacher_collection",
            "behavior_allowed": False,
            "submission_allowed": False,
        },
        "candidates": rows,
    }
    manifest_path = output_root / "candidates.json"
    _atomic_json(manifest_path, manifest)
    _load_tomato_manifest_v1(manifest_path, source_root=source_root)
    return manifest_path


def _load_tomato_manifest_v1(path: Path | str, *, source_root: Path | str = _ROOT) -> dict[str, object]:
    path = Path(path).resolve()
    source_root = Path(source_root).resolve()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TomatoDeckMutationError(f"cannot read tomato candidate manifest: {path}") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_V1:
        raise TomatoDeckMutationError("unsupported tomato candidate manifest")
    if payload.get("subject_id") != SUBJECT_ID_V1 or payload.get("candidate_status") != "candidate_only":
        raise TomatoDeckMutationError("manifest subject/status is invalid")
    if payload.get("research_only") is not True or payload.get("authority") != _FALSE_AUTHORITY_V1:
        raise TomatoDeckMutationError("manifest authority drifted")
    policy_path, parent_deck_path, card_data = _source_paths(source_root)
    parent = payload.get("parent")
    if not isinstance(parent, Mapping):
        raise TomatoDeckMutationError("manifest parent is missing")
    for key, resolved in (("policy_path", policy_path), ("deck_path", parent_deck_path)):
        if parent.get(key) != str(resolved):
            raise TomatoDeckMutationError(f"manifest parent {key} drifted")
    if parent.get("policy_sha256") != _sha256(policy_path) or parent.get("deck_file_sha256") != _sha256(parent_deck_path):
        raise TomatoDeckMutationError("parent bytes changed after manifest creation")
    known_cards = load_known_card_ids(card_data)
    parent_cards = tuple(read_deck_csv(parent_deck_path, known_card_ids=known_cards))
    if parent.get("deck_multiset_sha256") != deck_multiset_identity_v1(parent_cards):
        raise TomatoDeckMutationError("parent deck multiset drifted")
    if payload.get("known_card_ids_sha256") != _sha256(card_data):
        raise TomatoDeckMutationError("known card vocabulary bytes changed")
    rows = payload.get("candidates")
    if not isinstance(rows, list) or len(rows) != 8:
        raise TomatoDeckMutationError("manifest must contain exactly eight candidates")
    seen_ids: set[str] = set()
    seen_decks = {deck_multiset_identity_v1(parent_cards)}
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping) or row.get("candidate_status") != "candidate_only" or row.get("research_only") is not True:
            raise TomatoDeckMutationError(f"candidate {index} is not candidate-only")
        if row.get("authority") != {"promotion_allowed": False, "training_allowed": False, "submission_allowed": False}:
            raise TomatoDeckMutationError(f"candidate {index} authority drifted")
        candidate_id = row.get("candidate_id")
        if type(candidate_id) is not str or not candidate_id or candidate_id in seen_ids:
            raise TomatoDeckMutationError("candidate IDs must be unique")
        seen_ids.add(candidate_id)
        deck_path = Path(str(row.get("deck_csv_path", ""))).resolve()
        if not deck_path.is_file() or row.get("deck_csv_sha256") != _sha256(deck_path):
            raise TomatoDeckMutationError(f"candidate {candidate_id} deck bytes drifted")
        cards = tuple(read_deck_csv(deck_path, known_card_ids=known_cards))
        identity = deck_multiset_identity_v1(cards)
        if row.get("deck_multiset_sha256") != identity or identity in seen_decks:
            raise TomatoDeckMutationError(f"candidate {candidate_id} deck identity is invalid/duplicated")
        seen_decks.add(identity)
        if CORE_SIGNATURE_V1.violation(cards) is not None:
            raise TomatoDeckMutationError(f"candidate {candidate_id} broke tomato core")
    return payload


def build_tomato_mutation_screen_games_v1(
    *, manifest_path: Path | str,
    source_root: Path | str = _ROOT,
    reference_config: Path | str = COMMON_PROTOCOL_V1,
    games_per_opponent_seat: int = 2,
    block_id: str = "tomato-deck-mutation-screen-v1",
) -> tuple[EvaluationGameV1, ...]:
    """Build 96 games/arm over all 24 references, both seats, plus parent."""
    if type(games_per_opponent_seat) is not int or games_per_opponent_seat <= 0:
        raise TomatoDeckMutationError("games_per_opponent_seat must be positive")
    source_root = Path(source_root).resolve()
    payload = _load_tomato_manifest_v1(manifest_path, source_root=source_root)
    pool_root = source_root / "opponents"
    pool = load_opponent_pool_v1(pool_root)
    reference_ids = _reference_ids(Path(reference_config).resolve(), pool)
    parent = payload["parent"]
    assert isinstance(parent, Mapping)
    arms: list[tuple[str, dict[str, object], int]] = [
        ("parent_native", {
            "main_path": parent["policy_path"], "deck_path": parent["deck_path"],
            "policy_sha256": parent["policy_sha256"], "deck_sha256": parent["deck_file_sha256"],
            "env": {}, "biases": {}, "config_sha256": _config_sha({}, {}), "pool_root": str(pool_root),
        }, 13_000_000),
    ]
    candidates = payload["candidates"]
    assert isinstance(candidates, list)
    for index, row in enumerate(candidates):
        assert isinstance(row, Mapping)
        arms.append((f"candidate:{index}", {
            "main_path": parent["policy_path"], "deck_path": row["deck_csv_path"],
            "policy_sha256": parent["policy_sha256"], "deck_sha256": row["deck_csv_sha256"],
            "env": {}, "biases": {}, "config_sha256": _config_sha({}, {}), "pool_root": str(pool_root),
        }, 14_000_000 + index * 100_000))
    games: list[EvaluationGameV1] = []
    manifest_sha = _sha256(manifest_path)
    for arm, spec, seed in arms:
        built = build_native_candidate_games_v1(
            candidate_id=f"tomato-mutation-{arm}", candidate=spec, pool=pool,
            reference_ids=reference_ids, games_per_opponent_seat=games_per_opponent_seat,
            base_seed=seed, block_id=f"{block_id}-{arm}",
        )
        games.extend(replace(game, metadata={
            **dict(game.metadata), "schema_version": SCHEMA_V1, "comparison_arm": arm,
            "manifest_path": str(Path(manifest_path).resolve()), "manifest_sha256": manifest_sha,
            "common_protocol": True, "common_reference_count": 24,
            "research_only": True, "promotion_authority": False,
            "training_authority": False, "submission_authority": False,
        }) for game in built)
    expected = len(arms) * 24 * 2 * games_per_opponent_seat
    if len(games) != expected:
        raise TomatoDeckMutationError(f"expected {expected} screen games, got {len(games)}")
    return tuple(games)


def build_tomato_mutation_legality_games_v1(
    *, manifest_path: Path | str, source_root: Path | str = _ROOT,
) -> tuple[EvaluationGameV1, ...]:
    """Build one native-policy CABT cell per seat and mutation arm.

    The generic ``probe_deck_legality_v1`` uses the repository rule agent.  It
    cannot establish whether a deck is operational under the fixed native
    tomato policy, so this gate deliberately uses the exact same runner and
    policy/deck import boundary as the subsequent broad screen.
    """
    games = build_tomato_mutation_screen_games_v1(
        manifest_path=manifest_path,
        source_root=source_root,
        games_per_opponent_seat=1,
        block_id="tomato-deck-mutation-native-legality-v1",
    )
    selected = tuple(game for game in games if game.opponent_id == "official_random")
    if len(selected) != 9 * 2:
        raise TomatoDeckMutationError("native legality gate did not retain one cell per arm and seat")
    return selected


def build_tomato_mutation_confirmation_games_v1(
    *, manifest_path: Path | str, source_root: Path | str = _ROOT,
    candidate_index: int, block_id: str = "tomato-deck-mutation-confirmation-v1",
) -> tuple[EvaluationGameV1, ...]:
    """Build 384 games each for one screen-positive candidate and its parent."""
    if type(candidate_index) is not int or not 0 <= candidate_index < 8:
        raise TomatoDeckMutationError("candidate_index must be an int in [0, 7]")
    games = build_tomato_mutation_screen_games_v1(
        manifest_path=manifest_path,
        source_root=source_root,
        games_per_opponent_seat=8,
        block_id=block_id,
    )
    keep = {"parent_native", f"candidate:{candidate_index}"}
    selected = tuple(game for game in games if game.metadata["comparison_arm"] in keep)
    if len(selected) != 2 * 24 * 2 * 8:
        raise TomatoDeckMutationError("confirmation did not contain 384 games per arm")
    return selected


def verify_tomato_mutation_legality_v1(
    *, manifest_path: Path | str, source_root: Path | str = _ROOT,
    output_path: Path | str, seed: int = 15_000_000, max_steps: int = 2_000,
) -> dict[str, object]:
    """Execute native-policy CABT legality cells before the broad screen."""
    del seed, max_steps  # The cells pin their own deterministic manifest seeds.
    games = build_tomato_mutation_legality_games_v1(
        manifest_path=manifest_path, source_root=source_root,
    )
    destination = Path(output_path).resolve().parent / "cabt-legality-games"
    result = run_parallel_cabt_evaluation(
        games, output_dir=destination, max_workers=6, worker_recycle_games=8,
    )
    by_arm: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    for row in result["rows"]:
        metadata = row.get("metadata")
        arm = metadata.get("comparison_arm") if isinstance(metadata, Mapping) else None
        by_arm[str(arm or "unknown")].append(row)
    results = {arm: aggregate_ledger_v1(rows) for arm, rows in sorted(by_arm.items())}
    report = {
        "schema_version": SCHEMA_V1,
        "manifest_sha256": _sha256(manifest_path),
        "research_only": True,
        "authority": dict(_FALSE_AUTHORITY_V1),
        "probe_protocol": "native_tomato_policy_vs_official_random_both_seats",
        "game_ledger_path": str(destination / "ledger.jsonl"),
        "all_legal": all(
            value["requested_games"] == 2
            and value["completed_games"] == 2
            and value["faults"] == 0
            for value in results.values()
        ),
        "arms": results,
    }
    _atomic_json(Path(output_path), report)
    if not report["all_legal"]:
        raise TomatoDeckMutationError("at least one CABT legality probe failed; screen is refused")
    return report


def summarize_tomato_mutation_screen_v1(rows: Sequence[Mapping[str, object]]) -> dict[str, object]:
    grouped: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    for row in rows:
        metadata = row.get("metadata")
        arm = metadata.get("comparison_arm") if isinstance(metadata, Mapping) else None
        grouped[str(arm or "unknown")].append(row)
    arms = {arm: aggregate_ledger_v1(values) for arm, values in sorted(grouped.items())}
    parent = arms.get("parent_native")
    if parent is None:
        raise TomatoDeckMutationError("screen rows have no parent_native arm")
    parent_score = float(parent["score_rate"])
    candidates: dict[str, object] = {}
    # A 96-game result is only a filter.  Six additional wins is predeclared as
    # the minimum signal to spend a 384-game confirmation block; it is not a
    # promotion condition and cannot establish BestKnown by itself.
    for arm, summary in arms.items():
        if not arm.startswith("candidate:"):
            continue
        delta = float(summary["score_rate"]) - parent_score
        candidates[arm] = {
            **summary,
            "delta_vs_parent": delta,
            "confirmation_eligible": bool(summary["faults"] == 0 and delta >= 6.0 / 96.0),
        }
    return {
        "schema_version": SCHEMA_V1,
        "research_only": True,
        "authority": dict(_FALSE_AUTHORITY_V1),
        "arms": arms,
        "candidate_decisions": candidates,
        "confirmation_rule": "faults == 0 and score_rate(candidate) - score_rate(parent) >= 6/96",
    }


def _run_screen(args: argparse.Namespace) -> int:
    manifest = prepare_tomato_mutation_manifest_v1(
        output_root=args.output, source_root=args.source_root, seed=args.seed,
    )
    legality = verify_tomato_mutation_legality_v1(
        manifest_path=manifest, source_root=args.source_root,
        output_path=args.output / "cabt_legality_probes.json", max_steps=args.max_steps,
    )
    games = build_tomato_mutation_screen_games_v1(
        manifest_path=manifest, source_root=args.source_root,
        games_per_opponent_seat=args.games_per_opponent_seat,
    )
    result = run_parallel_cabt_evaluation(
        games, output_dir=args.output / "screen", max_workers=args.workers,
        worker_recycle_games=32, overwrite=args.overwrite,
    )
    summary = summarize_tomato_mutation_screen_v1(result["rows"])
    summary.update({
        "manifest_path": str(manifest), "manifest_sha256": _sha256(manifest),
        "reference_config_sha256": _sha256(Path(args.source_root) / "configs/meta_specialist/performance_first_broad_pool_v1.json"),
        "evaluator_implementation_sha256": evaluator_implementation_sha256_v1(),
        "cabt_legality_report": str(args.output / "cabt_legality_probes.json"),
        "cabt_legality_all_legal": legality["all_legal"], "arena_summary": result["summary"],
    })
    summary_path = args.output / "screen_summary.json"
    _atomic_json(summary_path, summary)
    print(json.dumps({**summary, "summary_sha256": _sha256(summary_path)}, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


def _run_confirmation(args: argparse.Namespace) -> int:
    if args.confirm_candidate_index is None:
        raise TomatoDeckMutationError("confirmation requires a candidate index")
    output = args.output.resolve()
    manifest = output / "candidates.json"
    payload = _load_tomato_manifest_v1(manifest, source_root=args.source_root)
    screen_path = output / "screen_summary.json"
    try:
        screen = json.loads(screen_path.read_text(encoding="utf-8"))
        decision = screen["candidate_decisions"][f"candidate:{args.confirm_candidate_index}"]
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise TomatoDeckMutationError("confirmation requires a completed tomato screen summary") from exc
    if float(decision.get("delta_vs_parent", 0.0)) <= 0.0 or int(decision.get("faults", 1)) != 0:
        raise TomatoDeckMutationError("only a positive, fault-free 96-game candidate may enter confirmation")
    games = build_tomato_mutation_confirmation_games_v1(
        manifest_path=manifest, source_root=args.source_root,
        candidate_index=args.confirm_candidate_index,
    )
    destination = output / f"confirmation-candidate-{args.confirm_candidate_index}"
    result = run_parallel_cabt_evaluation(
        games, output_dir=destination, max_workers=args.workers,
        worker_recycle_games=32, overwrite=args.overwrite,
    )
    summary = summarize_tomato_mutation_screen_v1(result["rows"])
    candidates = payload["candidates"]
    assert isinstance(candidates, list)
    selected = candidates[args.confirm_candidate_index]
    assert isinstance(selected, Mapping)
    summary.update({
        "manifest_path": str(manifest), "manifest_sha256": _sha256(manifest),
        "screen_summary_sha256": _sha256(screen_path),
        "screen_delta_vs_parent": decision["delta_vs_parent"],
        "selected_candidate": dict(selected),
        "reference_config_sha256": _sha256(Path(args.source_root) / "configs/meta_specialist/performance_first_broad_pool_v1.json"),
        "evaluator_implementation_sha256": evaluator_implementation_sha256_v1(),
        "arena_summary": result["summary"],
    })
    summary_path = destination / "confirmation_summary.json"
    _atomic_json(summary_path, summary)
    print(json.dumps({**summary, "summary_sha256": _sha256(summary_path)}, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, default=_ROOT)
    parser.add_argument("--seed", type=int, default=20260813)
    parser.add_argument("--games-per-opponent-seat", type=int, default=2)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--max-steps", type=int, default=2_000)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--confirm-candidate-index", type=int)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    return _run_confirmation(args) if args.confirm_candidate_index is not None else _run_screen(args)


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "ARCHETYPE_ID_V1", "CORE_SIGNATURE_V1", "REPLACEMENT_POOL_V1", "SCHEMA_V1",
    "SUBJECT_ID_V1", "TomatoDeckMutationError", "build_tomato_mutation_screen_games_v1",
    "build_tomato_mutation_confirmation_games_v1", "build_tomato_mutation_legality_games_v1",
    "prepare_tomato_mutation_manifest_v1", "summarize_tomato_mutation_screen_v1",
    "verify_tomato_mutation_legality_v1",
]
