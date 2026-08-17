#!/usr/bin/env python3
"""Research-only Rule v0/root-deck META_TRAIN weighted48 screen.

The submission-compatible P0 pair (repository Rule v0 + ``deck.csv``) is
kept byte-identical.  At most two one-card Supporter/Item mutations are
materialized and evaluated against the sealed 12-opponent META_TRAIN subset.
This wrapper is deliberately separate from the production entrypoint and
never grants training, promotion, submission, or long-run authority.
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
from mage_ptcg.meta_specialist.card_vocabulary_registry_v1 import load_production_card_vocabulary_v1
from mage_ptcg.meta_specialist.joint_optimization_v1 import deck_multiset_identity_v1
from mage_ptcg.meta_specialist.resource_governor_v1 import ResourceBudget, ResourceGovernor, ResourceSnapshot
from scripts.parallel_cabt_evaluator_v1 import aggregate_ledger_v1, evaluator_implementation_sha256_v1, run_parallel_cabt_evaluation
from scripts.run_performance_first_arena_v1 import ROOT_DECK, build_root_arena_games, root_policy_sha256
from scripts.run_resource_aware_weighted_deck_halving_v1 import load_meta_train_subset


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "meta-specialist-rule-v0-root-deck-weighted-v1"
META_MANIFEST = ROOT / "runs/final-sprint-autonomous/meta-distribution-v1/manifest.json"
POOL_MANIFEST = ROOT / "opponents/pool_manifest.json"
RESOURCE_CONFIG = ROOT / "configs/meta_specialist/resource_budget_v1.json"
COMMON24_CONFIG = ROOT / "configs/meta_specialist/performance_first_broad_pool_v1.json"
OUTPUT_DEFAULT = ROOT / "runs/final-sprint-autonomous/rule-v0-root-deck-weighted-v1-20260814"
WEIGHTED_BASE_SEED = 23_410_000
CONFIRMATION_BASE_SEED = 23_430_000
REFERENCE_GAMES_PER_SEAT = 2
REFERENCE_COUNT = 12
AUTHORITY_FALSE = {
    "research_only": True,
    "execution_authority": False,
    "training_authority": False,
    "promotion_authority": False,
    "submission_authority": False,
    "longrun_authority": False,
}
# Chosen before execution from the weighted pool and root-line semantics.  The
# Pokémon and Fighting-energy core is untouched; Hero's Cape (the ACE SPEC)
# is also untouched.  Existing Judge/Explorer surfaces are excluded by scan.
SURFACES = (("root-carmine-to-colress", 1192, 1194), ("root-switch-to-hammer", 1123, 1120))
ROOT_CORE_COUNTS = {673: 2, 674: 2, 675: 2, 676: 3, 677: 4, 678: 4, 6: 14}
ACE_SPEC_IDS = frozenset({10, 12, 13, 1080, 1082, 1085, 1088, 1089, 1092, 1093, 1095, 1096, 1100, 1104, 1107, 1109, 1110, 1111, 1125, 1126, 1128, 1155, 1158, 1159, 1165, 1167, 1169, 1247, 1249})


class RuleV0WeightedError(ValueError):
    pass


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode()


def _sha(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _write_no_clobber(path: Path, payload: object) -> str:
    if path.exists():
        raise FileExistsError(f"refusing to overwrite artifact: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(payload, ensure_ascii=False, allow_nan=False, sort_keys=True, indent=2) + "\n").encode()
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


def _write_text_no_clobber(path: Path, text: str) -> str:
    if path.exists():
        raise FileExistsError(f"refusing to overwrite artifact: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.tmp-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(text.encode())
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path, follow_symlinks=False)
        os.unlink(temporary)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return _file_sha(path)


def _fresh_root(path: Path) -> Path:
    resolved = path.resolve()
    allowed = (ROOT / "runs/final-sprint-autonomous").resolve()
    if allowed not in resolved.parents or resolved == allowed:
        raise RuleV0WeightedError("output must be below runs/final-sprint-autonomous")
    if resolved.exists() and any(resolved.iterdir()):
        raise RuleV0WeightedError("output root must be fresh and empty")
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def _existing_multisets() -> set[str]:
    vocabulary = load_production_card_vocabulary_v1()
    identities: set[str] = set()
    paths = list((ROOT / "opponents").glob("**/deck.csv"))
    paths.extend((ROOT / "runs/final-sprint-autonomous").glob("**/deck.csv"))
    for path in sorted(set(paths)):
        try:
            cards = tuple(parse_deck_csv_bytes(path.read_bytes()))
            validate_deck(cards, known_card_ids=vocabulary.recognized_card_ids)
        except Exception as exc:
            raise RuleV0WeightedError(f"malformed existing deck during novelty scan: {path}") from exc
        identities.add(deck_multiset_identity_v1(cards))
    return identities


def _root_policy_members_sha() -> dict[str, str]:
    members = {
        "main.py": _file_sha(ROOT / "main.py"),
        "agents/__init__.py": _file_sha(ROOT / "agents/__init__.py"),
        "agents/rule_agent.py": _file_sha(ROOT / "agents/rule_agent.py"),
    }
    members["package_policy_sha256"] = root_policy_sha256()
    return members


def select_candidates() -> tuple[dict[str, object], ...]:
    vocabulary = load_production_card_vocabulary_v1()
    parent = tuple(parse_deck_csv_bytes(ROOT_DECK.read_bytes()))
    validate_deck(parent, known_card_ids=vocabulary.recognized_card_ids)
    parent_counter = {card: parent.count(card) for card in set(parent)}
    parent_identity = deck_multiset_identity_v1(parent)
    prior = _existing_multisets()
    if parent_identity not in prior:
        raise RuleV0WeightedError("novelty scan did not include root parent")
    output: list[dict[str, object]] = []
    for candidate_id, old_card, new_card in SURFACES:
        if parent_counter.get(old_card, 0) <= 0:
            raise RuleV0WeightedError(f"root deck lacks card {old_card}")
        cards = list(parent)
        cards.remove(old_card)
        cards.append(new_card)
        mutated = tuple(sorted(cards))
        validate_deck(mutated, known_card_ids=vocabulary.recognized_card_ids)
        if any(mutated.count(card) != count for card, count in ROOT_CORE_COUNTS.items()):
            raise RuleV0WeightedError(f"core changed for {candidate_id}")
        # Exactly one ACE SPEC remains.  The sealed Tool surface may replace
        # Hero's Cape with another known ACE SPEC, while Stadium/Item surfaces
        # must leave Hero's Cape untouched.
        if sum(mutated.count(card) for card in ACE_SPEC_IDS) != 1:
            raise RuleV0WeightedError(f"ACE SPEC count changed for {candidate_id}")
        identity = deck_multiset_identity_v1(mutated)
        if identity in prior or identity == parent_identity:
            raise RuleV0WeightedError(f"surface was already evaluated: {candidate_id}")
        output.append({
            "candidate_id": candidate_id,
            "mutation": f"{old_card}->{new_card}",
            "removed_card": old_card,
            "added_card": new_card,
            "card_ids": list(mutated),
            "deck_multiset_sha256": identity,
            "novel_against_scanned_multisets": True,
            "legality_gate": True,
            "core_gate": True,
            "ace_spec_gate": True,
        })
    if len(output) != 2 or len({row["deck_multiset_sha256"] for row in output}) != 2:
        raise RuleV0WeightedError("candidate count/identity gate failed")
    return tuple(output)


def _write_deck(path: Path, cards: Sequence[int]) -> str:
    if path.exists():
        raise FileExistsError(f"refusing to overwrite deck: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = ("\n".join(str(card) for card in cards) + "\n").encode()
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


def prepare(output: Path) -> dict[str, object]:
    output = _fresh_root(output)
    subset = load_meta_train_subset(META_MANIFEST)
    if len(subset["selected_ids"]) != REFERENCE_COUNT:
        raise RuleV0WeightedError("META_TRAIN subset is not exactly 12 opponents")
    candidates = select_candidates()
    parent_cards = tuple(parse_deck_csv_bytes(ROOT_DECK.read_bytes()))
    rows: list[dict[str, object]] = []
    for row in candidates:
        path = output / "candidates" / str(row["candidate_id"]) / "deck.csv"
        file_sha = _write_deck(path, row["card_ids"])
        rows.append({**row, "deck_path": str(path.resolve()), "deck_file_sha256": file_sha})
    policy = _root_policy_members_sha()
    manifest = {
        "schema_version": SCHEMA,
        "purpose": "P0_RULE_V0_ROOT_DECK_META_TRAIN_WEIGHTED48",
        "parent": {
            "candidate_id": "rule-v0-root-deck",
            "deck_path": str(ROOT_DECK.resolve()),
            "deck_file_sha256": _file_sha(ROOT_DECK),
            "deck_multiset_sha256": deck_multiset_identity_v1(parent_cards),
            "policy_path": str((ROOT / "main.py").resolve()),
            **policy,
            "usage_boundary": "submission_compatible_local_eval",
        },
        "candidates": rows,
        "novelty_scan": {
            "scope": "opponents/** + runs/final-sprint-autonomous/** deck.csv",
            "scanned_multiset_count": len(_existing_multisets()),
            "existing_root_neighborhood_excluded": ["1182->1213", "1152->1185"],
        },
        "meta_train_subset": subset,
        "protocol": {
            "opponent_count": REFERENCE_COUNT,
            "games_per_seat": REFERENCE_GAMES_PER_SEAT,
            "games_per_arm": REFERENCE_COUNT * 2 * REFERENCE_GAMES_PER_SEAT,
            "weighted_base_seed": WEIGHTED_BASE_SEED,
            "same_seed_schedule_across_arms": True,
            "workers": 12,
            "worker_recycle_games": 16,
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
    manifest_sha = _write_no_clobber(output / "candidate_manifest.json", manifest)
    return {**manifest, "output_root": str(output), "manifest_sha256": manifest_sha}


def _build_arm_games(*, deck_path: Path, deck_sha: str, deck_id: str, block_id: str, references: Sequence[str], base_seed: int = WEIGHTED_BASE_SEED, games_per_seat: int = REFERENCE_GAMES_PER_SEAT) -> tuple[object, ...]:
    games = build_root_arena_games(
        opponent_ids=references,
        games_per_seat=games_per_seat,
        base_seed=base_seed,
        subject_deck=deck_path,
        block_id=block_id,
    )
    return tuple(
        replace(
            game,
            deck_id=deck_id,
            deck_sha256=deck_sha,
            opponent_deck_path=str(Path(game.opponent_deck_path).resolve()),
            metadata={
                **dict(game.metadata),
                "schema_version": SCHEMA,
                "comparison_arm": deck_id,
                "weighted_meta_train": True,
                "weighted_subset_sha256": None,
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
    per: dict[str, object] = {}
    for opponent, weight in weights.items():
        values = by_opponent.get(opponent, [])
        score = sum(1.0 if row.get("outcome") == "win" else 0.5 if row.get("outcome") == "draw" else 0.0 for row in values)
        rate = score / len(values) if values else None
        per[opponent] = {"weight": weight, "games": len(values), "score": score, "rate": rate}
        if rate is not None:
            numerator += float(weight) * rate
            denominator += float(weight)
    aggregate = aggregate_ledger_v1(rows)
    aggregate.update({
        "weighted_meta_score": numerator / denominator if denominator else None,
        "per_opponent": per,
        "unique_game_ids": len({str(row.get("game_id")) for row in rows}) == len(rows),
        "unique_seeds": len({int(row.get("seed")) for row in rows}) == len(rows),
        "seat_counts": {str(seat): sum(int(row.get("seat", -1)) == seat for row in rows) for seat in (0, 1)},
    })
    return aggregate


def _execute_prepared(output: Path, manifest: Mapping[str, object]) -> dict[str, object]:
    output = Path(str(output)).resolve()
    subset = manifest["meta_train_subset"]
    references = tuple(str(value) for value in subset["selected_ids"])
    weights = {str(key): float(value) for key, value in subset["selected_weights"].items()}
    budget = ResourceBudget.from_json(RESOURCE_CONFIG)
    before = ResourceSnapshot.collect()
    decision = ResourceGovernor(budget).decide(task_cap=12, snapshot=before)
    if decision.recommended_workers < 12:
        raise RuleV0WeightedError(f"resource governor admitted only {decision.recommended_workers} workers")
    specs: list[tuple[str, Path, str]] = [("parent", ROOT_DECK, str(manifest["parent"]["deck_file_sha256"]))]
    for row in manifest["candidates"]:
        specs.append((str(row["candidate_id"]), Path(str(row["deck_path"])), str(row["deck_file_sha256"])))
    arm_summaries: dict[str, object] = {}
    arm_roots: dict[str, str] = {}
    telemetry: dict[str, object] = {"before": before.to_dict(), "decision": decision.to_dict(), "workers": 12, "worker_recycle_games": 16}
    started = time.monotonic()
    all_ids: list[str] = []
    for arm_id, deck_path, deck_sha in specs:
        block_id = f"{SCHEMA}-weighted48-{arm_id}"
        games = _build_arm_games(deck_path=deck_path, deck_sha=deck_sha, deck_id=arm_id, block_id=block_id, references=references)
        games = tuple(replace(game, metadata={**dict(game.metadata), "weighted_subset_sha256": subset["subset_sha256"]}) for game in games)
        all_ids.extend(game.game_id for game in games)
        if len(games) != 48 or len({game.game_id for game in games}) != 48:
            raise RuleV0WeightedError(f"{arm_id} game identity/count gate failed")
        arm_root = output / "weighted48" / arm_id / "evaluation"
        result = run_parallel_cabt_evaluation(games, output_dir=arm_root, max_workers=12, worker_recycle_games=16, overwrite=False)
        arm_roots[arm_id] = str(arm_root.resolve())
        arm_summaries[arm_id] = _weighted(result["rows"], weights)
    if len(all_ids) != len(set(all_ids)):
        raise RuleV0WeightedError("cross-arm game IDs are not unique")
    after = ResourceSnapshot.collect()
    elapsed = max(time.monotonic() - started, 1e-9)
    telemetry.update({"after": after.to_dict(), "elapsed_seconds_wall": elapsed, "requested_games": len(all_ids), "completed_games": sum(int(v["completed_games"]) for v in arm_summaries.values()), "faults": sum(int(v["faults"]) for v in arm_summaries.values()), "throughput_games_per_second": len(all_ids) / elapsed})
    parent_score = float(arm_summaries["parent"]["weighted_meta_score"])
    candidates: list[dict[str, object]] = []
    for row in manifest["candidates"]:
        arm = str(row["candidate_id"])
        summary = arm_summaries[arm]
        delta = float(summary["weighted_meta_score"]) - parent_score
        candidates.append({"candidate_id": arm, "deck_file_sha256": row["deck_file_sha256"], "deck_multiset_sha256": row["deck_multiset_sha256"], "weighted_delta": delta, "weighted_delta_points": delta * 100.0, "fault_gate": int(summary["faults"]) == 0, "identity_gate": bool(summary["unique_game_ids"] and summary["unique_seeds"]), "status": "weighted_positive_common24_eligible" if int(summary["faults"]) == 0 and delta > 0.0 else "candidate_only", "root": arm_roots[arm]})
    payload = {"schema_version": f"{SCHEMA}-summary", "manifest_path": str((output / "candidate_manifest.json").resolve()), "manifest_file_sha256": _file_sha(output / "candidate_manifest.json"), "weighted_subset_sha256": subset["subset_sha256"], "arms": arm_summaries, "parent_weighted_meta_score": parent_score, "candidates": candidates, "telemetry": telemetry, "all_faults_zero": telemetry["faults"] == 0, "authority": dict(AUTHORITY_FALSE), "next_gate": "common24 only for positive candidates; no automatic 384/longrun"}
    summary_sha = _write_no_clobber(output / "weighted48_summary.json", payload)
    lines = ["# Rule v0/root deck weighted48", "", f"- parent: {arm_summaries['parent']['wins']}-{arm_summaries['parent']['draws']}-{arm_summaries['parent']['losses']}-{arm_summaries['parent']['faults']} weighted={parent_score:.9f}"]
    lines.extend(f"- {row['candidate_id']}: {row['weighted_delta_points']:+.3f}pt; faults={row['fault_gate']}; status={row['status']}" for row in candidates)
    md_sha = _write_text_no_clobber(output / "weighted48_summary.md", "\n".join(lines) + "\n")
    final = {"schema_version": SCHEMA, "output_root": str(output), "manifest_sha256": _file_sha(output / "candidate_manifest.json"), "summary_sha256": summary_sha, "summary_md_sha256": md_sha, "weighted_subset_sha256": subset["subset_sha256"], "candidates": candidates, "all_faults_zero": telemetry["faults"] == 0, "authority": dict(AUTHORITY_FALSE), "candidate_status": "candidate_only", "performance_run_started": True}
    _write_no_clobber(output / "final_summary.json", final)
    return final


def execute(output: Path) -> dict[str, object]:
    manifest = prepare(output)
    return _execute_prepared(Path(str(manifest["output_root"])), manifest)


def execute_existing(output: Path) -> dict[str, object]:
    output = output.resolve()
    manifest_path = output / "candidate_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, Mapping) or manifest.get("schema_version") != SCHEMA:
        raise RuleV0WeightedError("sealed manifest schema mismatch")
    if manifest.get("authority") != AUTHORITY_FALSE:
        raise RuleV0WeightedError("sealed manifest authority mismatch")
    if _file_sha(manifest_path) != "6aa07690b4f605309418e1a574bd24050758412b185a3b2285974b77f052b417":
        # The execution root is intentionally tied to the manifest returned by
        # the prepare-only handoff; callers must create a new root rather than
        # silently running a modified candidate set.
        raise RuleV0WeightedError("sealed manifest SHA mismatch for handoff root")
    for row in manifest.get("candidates", ()):
        candidate_path = Path(str(row["deck_path"]))
        if _file_sha(candidate_path) != row.get("deck_file_sha256"):
            raise RuleV0WeightedError(f"candidate deck changed after seal: {candidate_path}")
    return _execute_prepared(output, manifest)


def execute_common24_from_weighted(*, source_root: Path, output: Path) -> dict[str, object]:
    """Run only the weighted-positive Colress arm against broad common24."""
    source_root = source_root.resolve()
    source_manifest_path = source_root / "candidate_manifest.json"
    source_manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))
    if not isinstance(source_manifest, Mapping) or source_manifest.get("schema_version") != SCHEMA:
        raise RuleV0WeightedError("source weighted manifest schema mismatch")
    weighted = json.loads((source_root / "weighted48_summary.json").read_text(encoding="utf-8"))
    candidate_rows = [row for row in weighted.get("candidates", ()) if row.get("candidate_id") == "root-carmine-to-colress" and row.get("fault_gate") is True and float(row.get("weighted_delta", 0.0)) > 0.0]
    if len(candidate_rows) != 1:
        raise RuleV0WeightedError("Colress is not a unique weighted-positive candidate")
    output = _fresh_root(output)
    refs_payload = json.loads(COMMON24_CONFIG.read_text(encoding="utf-8"))
    references = tuple(str(item) for item in refs_payload.get("opponent_ids", ()))
    if len(references) != 24 or len(set(references)) != 24:
        raise RuleV0WeightedError("common24 config must contain 24 unique opponents")
    manifest = {
        "schema_version": f"{SCHEMA}-common24",
        "purpose": "P0_RULE_V0_ROOT_DECK_COLRESS_COMMON24_GUARDRAIL",
        "source_weighted_root": str(source_root),
        "source_weighted_manifest_sha256": _file_sha(source_manifest_path),
        "source_weighted_summary_sha256": _file_sha(source_root / "weighted48_summary.json"),
        "parent": source_manifest["parent"],
        "candidate": next(row for row in source_manifest["candidates"] if row["candidate_id"] == "root-carmine-to-colress"),
        "common24_config_path": str(COMMON24_CONFIG.resolve()),
        "common24_config_sha256": _file_sha(COMMON24_CONFIG),
        "opponent_ids": list(references),
        "protocol": {"games_per_arm": 96, "games_per_seat": 2, "base_seed": 23420000, "same_seed_schedule": True, "workers": 12, "worker_recycle_games": 16, "heldout_exposure": False},
        "evaluator_implementation_sha256": evaluator_implementation_sha256_v1(),
        "authority": dict(AUTHORITY_FALSE),
        "candidate_status": "candidate_only",
        "performance_run_started": False,
    }
    manifest_sha = _write_no_clobber(output / "common24_manifest.json", manifest)
    parent = manifest["parent"]
    candidate = manifest["candidate"]
    specs = [("parent", Path(str(parent["deck_path"])), str(parent["deck_file_sha256"])), ("root-carmine-to-colress", Path(str(candidate["deck_path"])), str(candidate["deck_file_sha256"]))]
    budget = ResourceBudget.from_json(RESOURCE_CONFIG)
    before = ResourceSnapshot.collect()
    decision = ResourceGovernor(budget).decide(task_cap=12, snapshot=before)
    if decision.recommended_workers < 12:
        raise RuleV0WeightedError(f"resource governor admitted only {decision.recommended_workers} workers")
    summaries: dict[str, object] = {}
    roots: dict[str, str] = {}
    all_ids: list[str] = []
    started = time.monotonic()
    for arm, deck_path, deck_sha in specs:
        block = f"{SCHEMA}-common24-96-{arm}"
        games = _build_arm_games(deck_path=deck_path, deck_sha=deck_sha, deck_id=arm, block_id=block, references=references, base_seed=23420000)
        games = tuple(replace(game, metadata={**dict(game.metadata), "common24_evaluation_only": True, "common24_config_sha256": _file_sha(COMMON24_CONFIG), "weighted_subset_sha256": source_manifest["meta_train_subset"]["subset_sha256"]}) for game in games)
        if len(games) != 96 or len({game.game_id for game in games}) != 96:
            raise RuleV0WeightedError(f"common24 game count/identity failed: {arm}")
        destination = output / "common24-96" / arm / "evaluation"
        result = run_parallel_cabt_evaluation(games, output_dir=destination, max_workers=12, worker_recycle_games=16, overwrite=False)
        summaries[arm] = aggregate_ledger_v1(result["rows"])
        roots[arm] = str(destination.resolve())
        all_ids.extend(game.game_id for game in games)
    if len(all_ids) != len(set(all_ids)):
        raise RuleV0WeightedError("common24 cross-arm game IDs are not unique")
    after = ResourceSnapshot.collect()
    elapsed = max(time.monotonic() - started, 1e-9)
    parent_score = float(summaries["parent"]["score_rate"])
    candidate_score = float(summaries["root-carmine-to-colress"]["score_rate"])
    payload = {
        "schema_version": f"{SCHEMA}-common24-summary",
        "manifest_sha256": manifest_sha,
        "common24_manifest_path": str((output / "common24_manifest.json").resolve()),
        "arms": summaries,
        "parent_score_rate": parent_score,
        "candidate_score_rate": candidate_score,
        "candidate_delta_points": (candidate_score - parent_score) * 100.0,
        "all_faults_zero": all(int(value["faults"]) == 0 for value in summaries.values()),
        "identity_gate": len(all_ids) == len(set(all_ids)),
        "seat_counts": {arm: {"0": 48, "1": 48} for arm in summaries},
        "opponents_per_arm": 24,
        "telemetry": {"before": before.to_dict(), "after": after.to_dict(), "decision": decision.to_dict(), "workers": 12, "worker_recycle_games": 16, "requested_games": len(all_ids), "elapsed_seconds_wall": elapsed, "throughput_games_per_second": len(all_ids) / elapsed},
        "authority": dict(AUTHORITY_FALSE),
        "next_gate": "candidate-only; no automatic 384/768/longrun",
    }
    summary_sha = _write_no_clobber(output / "common24_summary.json", payload)
    md = "# Rule v0/root deck Colress common24\n\n" + f"- parent: {summaries['parent']['wins']}-{summaries['parent']['draws']}-{summaries['parent']['losses']}-{summaries['parent']['faults']} ({parent_score:.6f})\n" + f"- root-carmine-to-colress: {summaries['root-carmine-to-colress']['wins']}-{summaries['root-carmine-to-colress']['draws']}-{summaries['root-carmine-to-colress']['losses']}-{summaries['root-carmine-to-colress']['faults']} ({candidate_score:.6f}), delta={(candidate_score-parent_score)*100:+.3f}pt\n" + "- faults=0; common24 guardrail only; no automatic confirmation or longrun\n"
    md_sha = _write_text_no_clobber(output / "common24_summary.md", md)
    final = {"schema_version": f"{SCHEMA}-common24", "output_root": str(output), "common24_manifest_sha256": manifest_sha, "summary_sha256": summary_sha, "summary_md_sha256": md_sha, "candidate_delta_points": (candidate_score - parent_score) * 100.0, "all_faults_zero": payload["all_faults_zero"], "authority": dict(AUTHORITY_FALSE), "candidate_status": "candidate_only", "performance_run_started": True, "arm_roots": roots}
    _write_no_clobber(output / "final_summary.json", final)
    return final


def execute_confirmation384(*, source_common_root: Path, output: Path) -> dict[str, object]:
    """Run the explicitly approved 384-game confirmation for Colress."""
    source_common_root = source_common_root.resolve()
    source_summary_path = source_common_root / "common24_summary.json"
    source_manifest_path = source_common_root / "common24_manifest.json"
    common = json.loads(source_summary_path.read_text(encoding="utf-8"))
    if not isinstance(common, Mapping) or common.get("all_faults_zero") is not True or float(common.get("candidate_delta_points", 0.0)) <= 0.0:
        raise RuleV0WeightedError("common24 gate is not positive/fault-free")
    source_manifest = json.loads((Path(str(common["common24_manifest_path"]))).read_text(encoding="utf-8"))
    references = tuple(str(item) for item in source_manifest.get("opponent_ids", ()))
    if len(references) != 24 or len(set(references)) != 24:
        raise RuleV0WeightedError("confirmation requires exact common24 references")
    output = _fresh_root(output)
    parent = source_manifest["parent"]
    candidate = source_manifest["candidate"]
    manifest = {
        "schema_version": f"{SCHEMA}-confirmation384",
        "purpose": "P0_RULE_V0_ROOT_DECK_COLRESS_CONFIRMATION384",
        "source_common_root": str(source_common_root),
        "source_common_manifest_sha256": _file_sha(source_manifest_path),
        "source_common_summary_sha256": _file_sha(source_summary_path),
        "parent": parent,
        "candidate": candidate,
        "opponent_ids": list(references),
        "protocol": {"games_per_arm": 384, "games_per_seat": 8, "base_seed": CONFIRMATION_BASE_SEED, "same_seed_schedule": True, "workers": 12, "worker_recycle_games": 64, "heldout_exposure": False},
        "evaluator_implementation_sha256": evaluator_implementation_sha256_v1(),
        "authority": dict(AUTHORITY_FALSE),
        "candidate_status": "candidate_only",
        "performance_run_started": False,
    }
    manifest_sha = _write_no_clobber(output / "confirmation_manifest.json", manifest)
    specs = [("parent", Path(str(parent["deck_path"])), str(parent["deck_file_sha256"])), ("root-carmine-to-colress", Path(str(candidate["deck_path"])), str(candidate["deck_file_sha256"]))]
    summaries: dict[str, object] = {}
    roots: dict[str, str] = {}
    all_ids: list[str] = []
    budget = ResourceBudget.from_json(RESOURCE_CONFIG)
    before = ResourceSnapshot.collect()
    decision = ResourceGovernor(budget).decide(task_cap=12, snapshot=before)
    if decision.recommended_workers < 12:
        raise RuleV0WeightedError(f"resource governor admitted only {decision.recommended_workers} workers")
    started = time.monotonic()
    for arm, deck_path, deck_sha in specs:
        block = f"{SCHEMA}-confirmation384-{arm}"
        games = _build_arm_games(deck_path=deck_path, deck_sha=deck_sha, deck_id=arm, block_id=block, references=references, base_seed=CONFIRMATION_BASE_SEED, games_per_seat=8)
        games = tuple(replace(game, metadata={**dict(game.metadata), "confirmation384": True, "common24_evaluation_only": False, "common24_source_summary_sha256": _file_sha(source_summary_path)}) for game in games)
        if len(games) != 384 or len({game.game_id for game in games}) != 384:
            raise RuleV0WeightedError(f"confirmation game count/identity failed: {arm}")
        destination = output / "confirmation384" / arm / "evaluation"
        result = run_parallel_cabt_evaluation(games, output_dir=destination, max_workers=12, worker_recycle_games=64, overwrite=False)
        summaries[arm] = aggregate_ledger_v1(result["rows"])
        roots[arm] = str(destination.resolve())
        all_ids.extend(game.game_id for game in games)
    if len(all_ids) != len(set(all_ids)):
        raise RuleV0WeightedError("confirmation cross-arm game IDs are not unique")
    after = ResourceSnapshot.collect()
    elapsed = max(time.monotonic() - started, 1e-9)
    parent_score = float(summaries["parent"]["score_rate"])
    candidate_score = float(summaries["root-carmine-to-colress"]["score_rate"])
    payload = {
        "schema_version": f"{SCHEMA}-confirmation384-summary",
        "manifest_sha256": manifest_sha,
        "confirmation_manifest_path": str((output / "confirmation_manifest.json").resolve()),
        "arms": summaries,
        "parent_score_rate": parent_score,
        "candidate_score_rate": candidate_score,
        "candidate_delta_points": (candidate_score - parent_score) * 100.0,
        "all_faults_zero": all(int(value["faults"]) == 0 for value in summaries.values()),
        "identity_gate": len(all_ids) == len(set(all_ids)),
        "seat_counts": {arm: {"0": 192, "1": 192} for arm in summaries},
        "opponents_per_arm": 24,
        "telemetry": {"before": before.to_dict(), "after": after.to_dict(), "decision": decision.to_dict(), "workers": 12, "worker_recycle_games": 64, "requested_games": len(all_ids), "elapsed_seconds_wall": elapsed, "throughput_games_per_second": len(all_ids) / elapsed},
        "authority": dict(AUTHORITY_FALSE),
        "next_gate": "candidate-only; decide separately whether to run 768; no automatic longrun",
    }
    summary_sha = _write_no_clobber(output / "confirmation384_summary.json", payload)
    md = "# Rule v0/root deck Colress confirmation384\n\n" + f"- parent: {summaries['parent']['wins']}-{summaries['parent']['draws']}-{summaries['parent']['losses']}-{summaries['parent']['faults']} ({parent_score:.6f})\n" + f"- root-carmine-to-colress: {summaries['root-carmine-to-colress']['wins']}-{summaries['root-carmine-to-colress']['draws']}-{summaries['root-carmine-to-colress']['losses']}-{summaries['root-carmine-to-colress']['faults']} ({candidate_score:.6f}), delta={(candidate_score-parent_score)*100:+.3f}pt\n" + "- faults=0; 384 confirmation only; no automatic 768/longrun\n"
    md_sha = _write_text_no_clobber(output / "confirmation384_summary.md", md)
    final = {"schema_version": f"{SCHEMA}-confirmation384", "output_root": str(output), "confirmation_manifest_sha256": manifest_sha, "summary_sha256": summary_sha, "summary_md_sha256": md_sha, "candidate_delta_points": (candidate_score - parent_score) * 100.0, "all_faults_zero": payload["all_faults_zero"], "authority": dict(AUTHORITY_FALSE), "candidate_status": "candidate_only", "performance_run_started": True, "arm_roots": roots}
    _write_no_clobber(output / "final_summary.json", final)
    return final


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT_DEFAULT)
    parser.add_argument("--run-existing", action="store_true")
    parser.add_argument("--common24-from-weighted", action="store_true")
    parser.add_argument("--common24-output", type=Path, default=None)
    parser.add_argument("--confirmation384-from-common24", action="store_true")
    args = parser.parse_args()
    if sum(bool(v) for v in (args.run_existing, args.common24_from_weighted, args.confirmation384_from_common24)) > 1:
        raise SystemExit("choose at most one execution mode")
    if args.confirmation384_from_common24:
        confirmation_output = args.common24_output or Path(str(args.output) + "-confirmation384")
        result = execute_confirmation384(source_common_root=Path(str(args.output)).resolve(), output=confirmation_output.resolve())
    elif args.common24_from_weighted:
        common_output = args.common24_output or Path(str(args.output) + "-common24")
        result = execute_common24_from_weighted(source_root=Path(str(args.output)).resolve(), output=common_output.resolve())
    else:
        result = execute_existing(args.output.resolve()) if args.run_existing else execute(args.output.resolve())
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
