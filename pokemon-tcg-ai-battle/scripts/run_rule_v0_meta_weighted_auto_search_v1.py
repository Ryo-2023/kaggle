#!/usr/bin/env python3
"""Automatic META_TRAIN-weighted search for the submission-compatible Rule v0 deck.

This is a research-only bridge.  It derives novel one-card candidates from the
sealed META_TRAIN frequency distribution, evaluates the root Rule v0 policy on
the same 12-opponent strata for every arm, and writes a sealed weighted48
summary.  It does not modify ``main.py``, ``agents/``, ``deck.csv``, pool
manifests, or any promotion/submission authority.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
from typing import Mapping, Sequence

from mage_ptcg.deck_io import parse_deck_csv_bytes, validate_deck
from mage_ptcg.meta_specialist.card_vocabulary_registry_v1 import (
    load_production_card_vocabulary_v1,
)
from mage_ptcg.meta_specialist.deck_mutation_v1 import DeckMutationCandidateV1
from mage_ptcg.meta_specialist.joint_optimization_v1 import (
    CoreSignatureV1,
    deck_multiset_identity_v1,
)
from mage_ptcg.meta_specialist.meta_weighted_deck_search_v1 import (
    build_replacement_pool_v1,
    build_weighted_card_frequency_v1,
    generate_meta_weighted_candidates_v1,
)
from mage_ptcg.meta_specialist.opponent_pool_v1 import (
    default_pool_root_v1,
    load_opponent_pool_v1,
)
from mage_ptcg.meta_specialist.resource_governor_v1 import (
    ResourceBudget,
    ResourceGovernor,
    ResourceSnapshot,
)
from scripts.parallel_cabt_evaluator_v1 import (
    aggregate_ledger_v1,
    evaluator_implementation_sha256_v1,
    run_parallel_cabt_evaluation,
)
from scripts.run_performance_first_arena_v1 import (
    ROOT_DECK,
    build_root_arena_games,
    root_policy_sha256,
)
from scripts.run_resource_aware_weighted_deck_halving_v1 import load_meta_train_subset


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "meta-specialist-rule-v0-root-deck-meta-weighted-auto-v1"
META_MANIFEST = ROOT / "runs/final-sprint-autonomous/meta-distribution-v1/manifest.json"
POOL_ROOT = ROOT / "opponents"
POOL_MANIFEST = POOL_ROOT / "pool_manifest.json"
RESOURCE_CONFIG = ROOT / "configs/meta_specialist/resource_budget_v1.json"
OUTPUT_DEFAULT = ROOT / "runs/final-sprint-autonomous/rule-v0-meta-weighted-auto-search-v1-20260814"
DEFAULT_WORKERS = 12
DEFAULT_WORKER_RECYCLE_GAMES = 16
DEFAULT_CANDIDATE_COUNT = 4
DEFAULT_GENERATOR_SEED = 20260814
DEFAULT_BASE_SEED = 23650000
GAMES_PER_OPPONENT_SEAT = 2
REFERENCE_COUNT = 12
AUTHORITY_FALSE = {
    "research_only": True,
    "execution_authority": False,
    "training_authority": False,
    "promotion_authority": False,
    "submission_authority": False,
    "longrun_authority": False,
}
ROOT_CORE_COUNTS = {
    673: 2,
    674: 2,
    675: 2,
    676: 3,
    677: 4,
    678: 4,
    6: 14,
}


class RuleV0MetaWeightedAutoError(ValueError):
    """Raised when the root auto-search contract cannot be closed."""


def _file_sha(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise RuleV0MetaWeightedAutoError(f"cannot read artifact: {path}") from exc


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _write_bytes_no_clobber(path: Path, raw: bytes) -> str:
    if path.exists():
        raise FileExistsError(f"refusing to overwrite artifact: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.tmp-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path, follow_symlinks=False)
        os.unlink(temporary)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return _file_sha(path)


def _write_json_no_clobber(path: Path, payload: object) -> str:
    raw = (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
    return _write_bytes_no_clobber(path, raw)


def _fresh_root(path: Path) -> Path:
    resolved = path.resolve()
    allowed = (ROOT / "runs/final-sprint-autonomous").resolve()
    if allowed not in resolved.parents or resolved == allowed:
        raise RuleV0MetaWeightedAutoError("output must be below runs/final-sprint-autonomous")
    if resolved.exists() and any(resolved.iterdir()):
        raise RuleV0MetaWeightedAutoError("output root must be fresh and empty")
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def _existing_multisets() -> set[str]:
    vocabulary = load_production_card_vocabulary_v1()
    paths = list((ROOT / "opponents").glob("**/deck.csv"))
    paths.extend((ROOT / "runs/final-sprint-autonomous").glob("**/deck.csv"))
    identities: set[str] = set()
    for path in sorted(set(paths)):
        try:
            cards = tuple(parse_deck_csv_bytes(path.read_bytes()))
            validate_deck(cards, known_card_ids=vocabulary.recognized_card_ids)
        except Exception as exc:
            raise RuleV0MetaWeightedAutoError(f"malformed existing deck during novelty scan: {path}") from exc
        identities.add(deck_multiset_identity_v1(cards))
    return identities


def generate_root_meta_candidates(
    *,
    parent_cards: Sequence[int],
    frequency_rows: Sequence[tuple[int, float, float]],
    prior_multisets: set[str],
    known_card_ids: Sequence[int],
    candidate_count: int,
    seed: int,
) -> tuple[DeckMutationCandidateV1, ...]:
    """Generate legal, novel root-deck candidates from weighted frequencies."""
    if type(candidate_count) is not int or candidate_count < 1:
        raise RuleV0MetaWeightedAutoError("candidate_count must be a positive int")
    if len(tuple(parent_cards)) != 60:
        raise RuleV0MetaWeightedAutoError("root parent must contain 60 cards")
    parent = tuple(int(card) for card in parent_cards)
    violation = CoreSignatureV1(
        archetype_id="rule-v0-root-deck",
        required_counts=ROOT_CORE_COUNTS,
    ).violation(parent)
    if violation is not None:
        raise RuleV0MetaWeightedAutoError(f"root parent core is invalid: {violation}")
    replacement_pool = build_replacement_pool_v1(
        frequency_rows=frequency_rows,
        parent_cards=parent,
        known_card_ids=known_card_ids,
        limit=64,
    )
    frequency = {int(card): float(weight) for card, weight, _support in frequency_rows}
    try:
        candidates = generate_meta_weighted_candidates_v1(
            parent_cards=parent,
            replacement_pool=replacement_pool,
            card_frequency=frequency,
            prior_multisets=set(prior_multisets),
            known_card_ids=known_card_ids,
            core_signature=CoreSignatureV1(
                archetype_id="rule-v0-root-deck",
                required_counts=ROOT_CORE_COUNTS,
            ),
            candidate_count=candidate_count,
            seed=seed,
            candidates_per_swap=max(512, candidate_count * 128),
        )
    except (ValueError, TypeError) as exc:
        raise RuleV0MetaWeightedAutoError(str(exc)) from exc
    parent_identity = deck_multiset_identity_v1(parent)
    for candidate in candidates:
        if candidate.deck_multiset_sha256 in prior_multisets or candidate.deck_multiset_sha256 == parent_identity:
            raise RuleV0MetaWeightedAutoError("candidate novelty gate failed")
        if any(candidate.card_ids.count(card) < minimum for card, minimum in ROOT_CORE_COUNTS.items()):
            raise RuleV0MetaWeightedAutoError("candidate root core gate failed")
    return tuple(candidates)


def _selected_deck_paths(subset: Mapping[str, object]) -> dict[str, Path]:
    pool = load_opponent_pool_v1(POOL_ROOT)
    selected = tuple(str(item) for item in subset["selected_ids"])
    result: dict[str, Path] = {}
    for opponent_id in selected:
        instance = pool.get(opponent_id)
        if instance is None:
            raise RuleV0MetaWeightedAutoError(f"META_TRAIN opponent is absent from pool: {opponent_id}")
        path = Path(str(instance.deck_csv_path)).resolve()
        if not path.is_file():
            raise RuleV0MetaWeightedAutoError(f"selected opponent deck is missing: {path}")
        result[opponent_id] = path
    return result


def materialize_manifest(
    *,
    output: Path = OUTPUT_DEFAULT,
    candidate_count: int = DEFAULT_CANDIDATE_COUNT,
    generator_seed: int = DEFAULT_GENERATOR_SEED,
    workers: int = DEFAULT_WORKERS,
    worker_recycle_games: int = DEFAULT_WORKER_RECYCLE_GAMES,
) -> dict[str, object]:
    output = _fresh_root(output)
    subset = load_meta_train_subset(META_MANIFEST)
    if len(subset["selected_ids"]) != REFERENCE_COUNT:
        raise RuleV0MetaWeightedAutoError("META_TRAIN subset must contain exactly 12 opponents")
    parent_cards = tuple(parse_deck_csv_bytes(ROOT_DECK.read_bytes()))
    vocabulary = load_production_card_vocabulary_v1()
    validate_deck(parent_cards, known_card_ids=vocabulary.recognized_card_ids)
    selected_paths = _selected_deck_paths(subset)
    frequency_rows = build_weighted_card_frequency_v1(
        deck_paths=selected_paths,
        selected_ids=tuple(str(item) for item in subset["selected_ids"]),
        selected_weights={str(key): float(value) for key, value in subset["selected_weights"].items()},
    )
    candidates = generate_root_meta_candidates(
        parent_cards=parent_cards,
        frequency_rows=frequency_rows,
        prior_multisets=_existing_multisets(),
        known_card_ids=vocabulary.recognized_card_ids,
        candidate_count=candidate_count,
        seed=generator_seed,
    )
    rows: list[dict[str, object]] = []
    for candidate in candidates:
        deck_path = output / "candidates" / candidate.candidate_id / "deck.csv"
        deck_sha = _write_bytes_no_clobber(deck_path, ("\n".join(str(card) for card in candidate.card_ids) + "\n").encode("utf-8"))
        rows.append({
            **candidate.to_dict(),
            "deck_path": str(deck_path.resolve()),
            "deck_file_sha256": deck_sha,
            "weighted_frequency_added_card": next(
                (float(freq) for card, freq, _support in frequency_rows if int(card) == int(candidate.added_cards[0])),
                0.0,
            ),
            **AUTHORITY_FALSE,
        })
    policy_members = {
        "main.py": _file_sha(ROOT / "main.py"),
        "agents/__init__.py": _file_sha(ROOT / "agents/__init__.py"),
        "agents/rule_agent.py": _file_sha(ROOT / "agents/rule_agent.py"),
        "package_policy_sha256": root_policy_sha256(),
    }
    manifest = {
        "schema_version": SCHEMA,
        "purpose": "SUBMISSION_COMPATIBLE_RULE_V0_ROOT_DECK_META_TRAIN_WEIGHTED_AUTO_SEARCH",
        "parent": {
            "candidate_id": "rule-v0-root-deck",
            "deck_path": str(ROOT_DECK.resolve()),
            "deck_file_sha256": _file_sha(ROOT_DECK),
            "deck_multiset_sha256": deck_multiset_identity_v1(parent_cards),
            "policy_path": str((ROOT / "main.py").resolve()),
            **policy_members,
            "usage_boundary": "submission_compatible_local_eval",
        },
        "meta_train_subset": {
            **dict(subset),
            "selected_deck_paths": {key: str(path) for key, path in sorted(selected_paths.items())},
            "frequency_rows": [
                {"card_id": int(card), "weighted_frequency": float(freq), "weighted_deck_support": float(support)}
                for card, freq, support in frequency_rows
            ],
        },
        "candidate_generation": {
            "module": "src/mage_ptcg/meta_specialist/meta_weighted_deck_search_v1.py",
            "module_sha256": _file_sha(ROOT / "src/mage_ptcg/meta_specialist/meta_weighted_deck_search_v1.py"),
            "generator_seed": generator_seed,
            "candidate_count": candidate_count,
            "root_core_counts": {str(card): count for card, count in sorted(ROOT_CORE_COUNTS.items())},
            "novelty_scan": "opponents/**/deck.csv + runs/final-sprint-autonomous/**/deck.csv",
        },
        "candidates": rows,
        "protocol": {
            "weighted_games_per_arm": REFERENCE_COUNT * 2 * GAMES_PER_OPPONENT_SEAT,
            "same_seed_schedule_across_arms": True,
            "workers_requested": workers,
            "worker_recycle_games": worker_recycle_games,
            "stages": [48, 96, 384, 768, 1536],
            "common24_auto_start": False,
            "confirmation_auto_start": False,
        },
        "pool_manifest_path": str(POOL_MANIFEST.resolve()),
        "pool_manifest_sha256": _file_sha(POOL_MANIFEST),
        "resource_config_path": str(RESOURCE_CONFIG.resolve()),
        "resource_config_sha256": _file_sha(RESOURCE_CONFIG),
        "evaluator_implementation_sha256": evaluator_implementation_sha256_v1(),
        "authority": dict(AUTHORITY_FALSE),
        "candidate_status": "candidate_only",
        "performance_run_started": False,
    }
    manifest_sha = _write_json_no_clobber(output / "candidate_manifest.json", manifest)
    return {**manifest, "output_root": str(output), "manifest_sha256": manifest_sha}


def _build_arm_games(
    *,
    arm_id: str,
    deck_path: Path,
    deck_sha: str,
    references: Sequence[str],
    base_seed: int,
    games_per_seat: int = GAMES_PER_OPPONENT_SEAT,
    block_id_prefix: str | None = None,
) -> tuple[object, ...]:
    prefix = block_id_prefix or f"{SCHEMA}-weighted48"
    games = build_root_arena_games(
        opponent_ids=references,
        games_per_seat=games_per_seat,
        base_seed=base_seed,
        subject_deck=deck_path,
        block_id=f"{prefix}-{arm_id}",
    )
    return tuple(
        replace(
            game,
            deck_id=arm_id,
            deck_sha256=deck_sha,
            opponent_deck_path=str(Path(game.opponent_deck_path).resolve()),
            metadata={
                **dict(game.metadata),
                "schema_version": SCHEMA,
                "comparison_arm": arm_id,
                "weighted_meta_train": True,
                **AUTHORITY_FALSE,
            },
        )
        for game in games
    )


def _weighted(rows: Sequence[Mapping[str, object]], weights: Mapping[str, float]) -> dict[str, object]:
    by_opponent: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    for row in rows:
        by_opponent[str(row.get("opponent_id"))].append(row)
    numerator = denominator = 0.0
    per_opponent: dict[str, object] = {}
    for opponent_id, weight in weights.items():
        values = by_opponent.get(opponent_id, [])
        score = sum(
            1.0 if row.get("outcome") == "win" else 0.5 if row.get("outcome") == "draw" else 0.0
            for row in values
        )
        rate = score / len(values) if values else None
        per_opponent[opponent_id] = {"weight": float(weight), "games": len(values), "rate": rate}
        if rate is not None:
            numerator += float(weight) * rate
            denominator += float(weight)
    aggregate = aggregate_ledger_v1(rows)
    return {
        **aggregate,
        "weighted_meta_score": numerator / denominator if denominator else None,
        "per_opponent": per_opponent,
        "unique_game_ids": len({str(row.get("game_id")) for row in rows}) == len(rows),
        "unique_seeds": len({int(row.get("seed")) for row in rows}) == len(rows),
        "seat_counts": {str(seat): sum(int(row.get("seat", -1)) == seat for row in rows) for seat in (0, 1)},
    }


def _execute_manifest(
    *,
    manifest: Mapping[str, object],
    base_seed: int = DEFAULT_BASE_SEED,
    workers: int = DEFAULT_WORKERS,
    worker_recycle_games: int = DEFAULT_WORKER_RECYCLE_GAMES,
) -> dict[str, object]:
    if type(workers) is not int or workers < 1 or type(worker_recycle_games) is not int or worker_recycle_games < 1:
        raise RuleV0MetaWeightedAutoError("workers and worker_recycle_games must be positive ints")
    output_path = Path(str(manifest["output_root"]))
    subset = manifest["meta_train_subset"]
    references = tuple(str(item) for item in subset["selected_ids"])
    weights = {str(key): float(value) for key, value in subset["selected_weights"].items()}
    parent = manifest["parent"]
    specs: list[tuple[str, Path, str]] = [("parent", Path(str(parent["deck_path"])), str(parent["deck_file_sha256"]))]
    for row in manifest["candidates"]:
        specs.append((str(row["candidate_id"]), Path(str(row["deck_path"])), str(row["deck_file_sha256"])))
    all_games: list[object] = []
    by_arm: dict[str, list[object]] = defaultdict(list)
    for arm_id, deck_path, deck_sha in specs:
        games = _build_arm_games(arm_id=arm_id, deck_path=deck_path, deck_sha=deck_sha, references=references, base_seed=base_seed)
        if len(games) != REFERENCE_COUNT * 2 * GAMES_PER_OPPONENT_SEAT:
            raise RuleV0MetaWeightedAutoError(f"weighted48 count gate failed for {arm_id}")
        by_arm[arm_id].extend(games)
        all_games.extend(games)
    expected_per_arm = REFERENCE_COUNT * 2 * GAMES_PER_OPPONENT_SEAT
    if len(all_games) != expected_per_arm * len(specs):
        raise RuleV0MetaWeightedAutoError("global game count gate failed")
    if len({game.game_id for game in all_games}) != len(all_games):
        raise RuleV0MetaWeightedAutoError("global game ID gate failed")
    parent_keys = {(game.opponent_id, game.seat, int(game.metadata["repetition"])): game for game in by_arm["parent"]}
    for arm_id, games in by_arm.items():
        if arm_id == "parent":
            continue
        keys = {(game.opponent_id, game.seat, int(game.metadata["repetition"])): game for game in games}
        if keys.keys() != parent_keys.keys() or any(keys[key].seed != parent_keys[key].seed for key in parent_keys):
            raise RuleV0MetaWeightedAutoError(f"paired schedule mismatch: {arm_id}")
    budget = ResourceBudget.from_json(RESOURCE_CONFIG)
    before = ResourceSnapshot.collect()
    decision = ResourceGovernor(budget).decide(task_cap=min(workers, budget.max_workers), snapshot=before)
    admitted_workers = min(workers, budget.max_workers, int(decision.recommended_workers))
    if admitted_workers < 1:
        raise RuleV0MetaWeightedAutoError("resource governor admitted no workers")
    started = time.monotonic()
    result = run_parallel_cabt_evaluation(
        tuple(all_games),
        output_dir=output_path / "weighted48" / "evaluation",
        max_workers=admitted_workers,
        worker_recycle_games=worker_recycle_games,
        overwrite=False,
    )
    elapsed = max(time.monotonic() - started, 1e-9)
    grouped: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    for row in result["rows"]:
        grouped[str(row.get("metadata", {}).get("comparison_arm", "unknown"))].append(row)
    summaries = {arm_id: _weighted(rows, weights) for arm_id, rows in sorted(grouped.items())}
    if set(summaries) != {arm_id for arm_id, _path, _sha in specs}:
        raise RuleV0MetaWeightedAutoError("evaluator arm metadata was not preserved")
    parent_score = float(summaries["parent"]["weighted_meta_score"])
    candidates: list[dict[str, object]] = []
    row_by_id = {str(row["candidate_id"]): row for row in manifest["candidates"]}
    for arm_id in sorted(set(summaries) - {"parent"}):
        row = row_by_id[arm_id]
        delta = float(summaries[arm_id]["weighted_meta_score"]) - parent_score
        candidates.append({
            "candidate_id": arm_id,
            "deck_file_sha256": row["deck_file_sha256"],
            "deck_multiset_sha256": row["deck_multiset_sha256"],
            "weighted_delta": delta,
            "weighted_delta_points": delta * 100.0,
            "fault_gate": int(summaries[arm_id]["faults"]) == 0,
            "identity_gate": bool(summaries[arm_id]["unique_game_ids"] and summaries[arm_id]["unique_seeds"]),
            "status": "weighted_positive_candidate_only" if int(summaries[arm_id]["faults"]) == 0 and delta > 0.0 else "candidate_only",
        })
    after = ResourceSnapshot.collect()
    telemetry = {
        "workers_requested": workers,
        "workers_admitted": admitted_workers,
        "worker_recycle_games": worker_recycle_games,
        "governor_decision": decision.to_dict(),
        "requested_games": len(all_games),
        "completed_games": result["summary"]["completed_games"],
        "faults": result["summary"]["faults"],
        "elapsed_seconds_wall": elapsed,
        "throughput_games_per_second": len(all_games) / elapsed,
        "memory_available_before_bytes": before.memory_available_bytes,
        "memory_available_after_bytes": after.memory_available_bytes,
        "rss_before_bytes": before.process_rss_bytes,
        "rss_after_bytes": after.process_rss_bytes,
    }
    payload = {
        "schema_version": f"{SCHEMA}-weighted48-summary",
        "manifest_path": str((output_path / "candidate_manifest.json").resolve()),
        "manifest_file_sha256": _file_sha(output_path / "candidate_manifest.json"),
        "weighted_subset_sha256": subset["subset_sha256"],
        "arms": summaries,
        "parent_weighted_meta_score": parent_score,
        "candidates": candidates,
        "telemetry": telemetry,
        "all_faults_zero": int(result["summary"]["faults"]) == 0,
        "authority": dict(AUTHORITY_FALSE),
        "next_gate": "common24 only for positive candidates; no automatic 384/longrun",
    }
    summary_sha = _write_json_no_clobber(output_path / "weighted48_summary.json", payload)
    lines = ["# Rule v0/root deck automatic META_TRAIN weighted48", "", f"- parent: {summaries['parent']['wins']}-{summaries['parent']['draws']}-{summaries['parent']['losses']}-{summaries['parent']['faults']} weighted={parent_score:.9f}"]
    lines.extend(
        f"- {row['candidate_id']}: {row['weighted_delta_points']:+.3f}pt; faults={row['fault_gate']}; status={row['status']}"
        for row in candidates
    )
    md_sha = _write_bytes_no_clobber(output_path / "weighted48_summary.md", ("\n".join(lines) + "\n").encode("utf-8"))
    final = {
        "schema_version": f"{SCHEMA}-final",
        "output_root": str(output_path),
        "manifest_sha256": _file_sha(output_path / "candidate_manifest.json"),
        "summary_sha256": summary_sha,
        "summary_md_sha256": md_sha,
        "weighted_subset_sha256": subset["subset_sha256"],
        "candidates": candidates,
        "all_faults_zero": payload["all_faults_zero"],
        "authority": dict(AUTHORITY_FALSE),
        "candidate_status": "candidate_only",
        "performance_run_started": True,
    }
    _write_json_no_clobber(output_path / "final_summary.json", final)
    return final


def execute(
    *,
    output: Path = OUTPUT_DEFAULT,
    candidate_count: int = DEFAULT_CANDIDATE_COUNT,
    generator_seed: int = DEFAULT_GENERATOR_SEED,
    base_seed: int = DEFAULT_BASE_SEED,
    workers: int = DEFAULT_WORKERS,
    worker_recycle_games: int = DEFAULT_WORKER_RECYCLE_GAMES,
) -> dict[str, object]:
    manifest = materialize_manifest(
        output=output,
        candidate_count=candidate_count,
        generator_seed=generator_seed,
        workers=workers,
        worker_recycle_games=worker_recycle_games,
    )
    return _execute_manifest(
        manifest=manifest,
        base_seed=base_seed,
        workers=workers,
        worker_recycle_games=worker_recycle_games,
    )


def execute_existing(
    *,
    output: Path,
    base_seed: int = DEFAULT_BASE_SEED,
    workers: int = DEFAULT_WORKERS,
    worker_recycle_games: int = DEFAULT_WORKER_RECYCLE_GAMES,
) -> dict[str, object]:
    """Run a previously sealed manifest without regenerating its candidates."""
    output = output.resolve()
    manifest_path = output / "candidate_manifest.json"
    if not manifest_path.is_file():
        raise RuleV0MetaWeightedAutoError(f"sealed manifest is missing: {manifest_path}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuleV0MetaWeightedAutoError("sealed manifest is not valid JSON") from exc
    if not isinstance(manifest, Mapping) or manifest.get("schema_version") != SCHEMA:
        raise RuleV0MetaWeightedAutoError("sealed manifest schema mismatch")
    if manifest.get("authority") != AUTHORITY_FALSE:
        raise RuleV0MetaWeightedAutoError("sealed manifest authority mismatch")
    if str(manifest.get("output_root", output)) != str(output):
        # Older prepare-only output omitted this field; path identity is still
        # checked through each deck path below.
        if any(not str(row.get("deck_path", "")).startswith(str(output)) for row in manifest.get("candidates", ())):
            raise RuleV0MetaWeightedAutoError("sealed manifest output root mismatch")
    parent = manifest.get("parent")
    if not isinstance(parent, Mapping) or _file_sha(Path(str(parent["deck_path"]))) != parent.get("deck_file_sha256"):
        raise RuleV0MetaWeightedAutoError("sealed root parent changed")
    if str(parent.get("package_policy_sha256")) != root_policy_sha256():
        raise RuleV0MetaWeightedAutoError("sealed root policy changed")
    candidates = manifest.get("candidates")
    if not isinstance(candidates, Sequence) or not candidates:
        raise RuleV0MetaWeightedAutoError("sealed candidate list is empty")
    for row in candidates:
        if not isinstance(row, Mapping):
            raise RuleV0MetaWeightedAutoError("sealed candidate row is malformed")
        deck_path = Path(str(row["deck_path"]))
        if _file_sha(deck_path) != row.get("deck_file_sha256"):
            raise RuleV0MetaWeightedAutoError(f"sealed candidate deck changed: {deck_path}")
    # The manifest is loaded from a fresh root and all evaluation artifacts are
    # written with exclusive links; a rerun therefore fails closed rather than
    # clobbering an earlier ledger.
    if (output / "weighted48" / "evaluation").exists():
        raise RuleV0MetaWeightedAutoError("weighted48 evaluation already exists; use a fresh root")
    manifest = {**dict(manifest), "output_root": str(output)}
    return _execute_manifest(
        manifest=manifest,
        base_seed=base_seed,
        workers=workers,
        worker_recycle_games=worker_recycle_games,
    )


def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT_DEFAULT)
    parser.add_argument("--candidate-count", type=int, default=DEFAULT_CANDIDATE_COUNT)
    parser.add_argument("--generator-seed", type=int, default=DEFAULT_GENERATOR_SEED)
    parser.add_argument("--base-seed", type=int, default=DEFAULT_BASE_SEED)
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--worker-recycle-games", type=int, default=DEFAULT_WORKER_RECYCLE_GAMES)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--run-existing", action="store_true")
    args = parser.parse_args(argv)
    if args.prepare_only and args.run_existing:
        raise SystemExit("choose at most one of --prepare-only and --run-existing")
    if args.run_existing:
        result = execute_existing(
            output=args.output,
            base_seed=args.base_seed,
            workers=args.workers,
            worker_recycle_games=args.worker_recycle_games,
        )
    elif args.prepare_only:
        result = materialize_manifest(
            output=args.output,
            candidate_count=args.candidate_count,
            generator_seed=args.generator_seed,
            workers=args.workers,
            worker_recycle_games=args.worker_recycle_games,
        )
    else:
        result = execute(
            output=args.output,
            candidate_count=args.candidate_count,
            generator_seed=args.generator_seed,
            base_seed=args.base_seed,
            workers=args.workers,
            worker_recycle_games=args.worker_recycle_games,
        )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
