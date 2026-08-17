"""Research-only Tomato setup-active priority weighted48 screen.

The native Tomato policy and deck remain read-only.  Only the sealed
``_SETUP_ACTIVE_PRIORITY`` mapping is changed in two copied policy files.
This lane is bounded to weighted48 and never auto-starts common24, 384,
longrun, training, promotion, or submission.
"""

from __future__ import annotations

from collections import defaultdict
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import time
from typing import Mapping, Sequence

from mage_ptcg.meta_specialist.opponent_pool_v1 import load_opponent_pool_v1
from mage_ptcg.meta_specialist.resource_governor_v1 import ResourceBudget, ResourceGovernor, ResourceSnapshot
from scripts import run_resource_aware_tomato_surface_weighted_v1 as surface
from scripts.parallel_cabt_evaluator_v1 import aggregate_ledger_v1, evaluator_implementation_sha256_v1, run_parallel_cabt_evaluation
from scripts.run_native_policy_candidate_pilot_v1 import _config_sha, build_native_candidate_games_v1


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "meta-specialist-resource-aware-tomato-policy-setup-priority-weighted-v1"
TOMATO_PARENT_POLICY = surface.TOMATO_PARENT_POLICY
TOMATO_PARENT_DECK = surface.TOMATO_PARENT_DECK
TOMATO_PARENT_POLICY_SHA256 = surface.TOMATO_PARENT_POLICY_SHA256
TOMATO_PARENT_DECK_SHA256 = surface.TOMATO_PARENT_DECK_SHA256
META_MANIFEST = surface.META_MANIFEST
POOL_ROOT = surface.POOL_ROOT
RESOURCE_CONFIG = surface.RESOURCE_CONFIG
OUTPUT_DEFAULT = ROOT / "runs/final-sprint-autonomous/resource-aware-tomato-policy-setup-priority-weighted-v1-20260814"
COMMON24_OUTPUT_DEFAULT = ROOT / "runs/final-sprint-autonomous/resource-aware-tomato-policy-setup-priority-common24-v1-20260814"
COMMON24_SOURCE_ROOT = ROOT / "runs/final-sprint-autonomous/resource-aware-tomato-policy-setup-priority-weighted-v1-retry-20260814"
WEIGHTED_BASE_SEED = 22910000
WARMUP_BASE_SEED = 22900000
COMMON24_BASE_SEED = 22920000
WEIGHTED_GAMES_PER_OPPONENT_SEAT = 2
RAMP_WORKERS = (1, 2, 4, 8, 12)
AUTHORITY_FALSE = {
    "research_only": True,
    "execution_authority": False,
    "training_authority": False,
    "promotion_authority": False,
    "submission_authority": False,
    "longrun_authority": False,
}
_PRIORITY_KEYS = ("CINDERACE", "DURALUDON", "RELICANTH")
_PRIORITY_BLOCK = re.compile(
    r"_SETUP_ACTIVE_PRIORITY\s*=\s*\{\n"
    r"\s*CINDERACE:\s*\(100000,\s*\"Active: Cinderace Explosiveness\"\),\n"
    r"\s*DURALUDON:\s*\(20000,\s*\"Active fallback: Duraludon\"\),\n"
    r"\s*RELICANTH:\s*\(5000,\s*\"Active fallback: Relicanth\"\),\n"
    r"\}",
)
_BROAD_CONFIG = ROOT / "configs/meta_specialist/performance_first_broad_pool_v1.json"
_COMMON24_HELDOUT = frozenset({
    "aristophanivan_multiply",
    "dashimaki360_crustlecounter",
    "lucifer19_battlecore",
    "plamen06_steel",
})


class SetupPriorityError(ValueError):
    """Raised when a copied setup-priority policy is not sealed/bounded."""


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _write_bytes_exclusive(path: Path, raw: bytes) -> str:
    if path.exists():
        raise FileExistsError(f"refusing to overwrite: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.tmp-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path, follow_symlinks=False)
        os.unlink(temporary)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return _sha(path)


def _write_json(path: Path, payload: object) -> str:
    return _write_bytes_exclusive(path, (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode())


def materialize_setup_priority_policy_copy(*, source: Path, destination: Path, priorities: Mapping[str, int]) -> str:
    """Replace exactly the native setup-active priority mapping in a copy."""

    if _sha(source) != TOMATO_PARENT_POLICY_SHA256:
        raise SetupPriorityError("Tomato source policy bytes changed")
    if set(priorities) != set(_PRIORITY_KEYS):
        raise SetupPriorityError("priority keys must exactly match the native mapping")
    checked: dict[str, int] = {}
    for key in _PRIORITY_KEYS:
        value = priorities[key]
        if type(value) is not int or not 0 <= value <= 100000:
            raise SetupPriorityError(f"priority {key} must be an integer in [0,100000]")
        checked[key] = value
    source_text = source.read_text(encoding="utf-8")
    replacement = (
        "_SETUP_ACTIVE_PRIORITY = {\n"
        f"    CINDERACE: ({checked['CINDERACE']}, \"Active: Cinderace Explosiveness\"),\n"
        f"    DURALUDON: ({checked['DURALUDON']}, \"Active fallback: Duraludon\"),\n"
        f"    RELICANTH: ({checked['RELICANTH']}, \"Active fallback: Relicanth\"),\n"
        "}"
    )
    updated, count = _PRIORITY_BLOCK.subn(replacement, source_text, count=1)
    if count != 1 or updated == source_text:
        raise SetupPriorityError("sealed setup priority mapping was not replaced exactly once")
    return _write_bytes_exclusive(destination, updated.encode("utf-8"))


def build_setup_priority_variants() -> tuple[dict[str, object], ...]:
    variants = (
        (
            "setup-duraludon-first-v1",
            {"CINDERACE": 20000, "DURALUDON": 100000, "RELICANTH": 5000},
        ),
        (
            "setup-relicanth-first-v1",
            {"CINDERACE": 20000, "DURALUDON": 5000, "RELICANTH": 100000},
        ),
    )
    return tuple(
        {
            "candidate_id": candidate_id,
            "parameter_name": "_SETUP_ACTIVE_PRIORITY",
            "priorities": priorities,
            "priority_config_sha256": hashlib.sha256(_canonical(priorities)).hexdigest(),
            "policy_sha256": hashlib.sha256(_canonical({"source": TOMATO_PARENT_POLICY_SHA256, "priorities": priorities})).hexdigest(),
            **AUTHORITY_FALSE,
        }
        for candidate_id, priorities in variants
    )


def _fresh_root(output: Path) -> Path:
    resolved = output.resolve()
    allowed = (ROOT / "runs/final-sprint-autonomous").resolve()
    if allowed not in resolved.parents or resolved == allowed:
        raise SetupPriorityError("output must be below final-sprint-autonomous")
    if resolved.exists() and any(resolved.iterdir()):
        raise SetupPriorityError("output root must be fresh and empty")
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def _references(subset: Mapping[str, object]) -> tuple[str, ...]:
    refs = tuple(str(item) for item in subset["selected_ids"])
    if len(refs) != 12 or len(set(refs)) != 12:
        raise SetupPriorityError("META_TRAIN subset must contain 12 unique IDs")
    return refs


def build_common24_reference_ids() -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return the broad 24 evaluation IDs and the four evaluation-only heldout IDs."""

    payload = json.loads(_BROAD_CONFIG.read_text(encoding="utf-8"))
    refs = tuple(str(item) for item in payload.get("opponent_ids", ()))
    if len(refs) != 24 or len(set(refs)) != 24:
        raise SetupPriorityError("broad common24 config must contain 24 unique IDs")
    heldout = tuple(item for item in refs if item in _COMMON24_HELDOUT)
    if set(heldout) != set(_COMMON24_HELDOUT) or len(heldout) != 4:
        raise SetupPriorityError("common24 heldout set is not closed")
    return refs, heldout


def _policy_spec(*, policy_path: Path, policy_sha: str, deck_path: Path, deck_sha: str, arm: str) -> dict[str, object]:
    env: dict[str, str] = {}
    biases: dict[str, float] = {}
    return {
        "main_path": str(policy_path),
        "deck_path": str(deck_path),
        "policy_sha256": policy_sha,
        "deck_sha256": deck_sha,
        "env": env,
        "biases": biases,
        "config_sha256": _config_sha(env, biases),
        "pool_root": str(POOL_ROOT.resolve()),
        "policy_setup_priority_arm": arm,
    }


def _write_deck_copy(source: Path, destination: Path) -> str:
    return _write_bytes_exclusive(destination, source.read_bytes())


def materialize_manifest(*, output: Path) -> dict[str, object]:
    output = _fresh_root(output)
    if _sha(TOMATO_PARENT_POLICY) != TOMATO_PARENT_POLICY_SHA256 or _sha(TOMATO_PARENT_DECK) != TOMATO_PARENT_DECK_SHA256:
        raise SetupPriorityError("Tomato parent identity changed")
    subset = surface.load_meta_train_subset(META_MANIFEST)
    variants = build_setup_priority_variants()
    materialized: list[dict[str, object]] = []
    for variant in variants:
        candidate_id = str(variant["candidate_id"])
        policy_path = output / "policies" / candidate_id / "main.py"
        deck_sidecar = policy_path.parent / "deck.csv"
        policy_sha = materialize_setup_priority_policy_copy(
            source=TOMATO_PARENT_POLICY,
            destination=policy_path,
            priorities=variant["priorities"],
        )
        _write_deck_copy(TOMATO_PARENT_DECK, deck_sidecar)
        materialized.append({
            **variant,
            "policy_path": str(policy_path.resolve()),
            "policy_sha256": policy_sha,
            "deck_sidecar_path": str(deck_sidecar.resolve()),
        })
    manifest = {
        "schema_version": SCHEMA,
        "purpose": "TOMATO_NATIVE_PARENT_SETUP_ACTIVE_PRIORITY_WEIGHTED48",
        "parent": {
            "candidate_id": "tomatomato_archaludon-native",
            "policy_path": str(TOMATO_PARENT_POLICY.resolve()),
            "policy_sha256": TOMATO_PARENT_POLICY_SHA256,
            "deck_path": str(TOMATO_PARENT_DECK.resolve()),
            "deck_sha256": TOMATO_PARENT_DECK_SHA256,
            **AUTHORITY_FALSE,
        },
        "variants": materialized,
        "meta_train_subset": subset,
        "protocol": {
            "weighted_games_per_arm": len(subset["selected_ids"]) * 2 * WEIGHTED_GAMES_PER_OPPONENT_SEAT,
            "same_seed_schedule_across_arms": True,
            "warmup_ramp_workers": list(RAMP_WORKERS),
            "weighted_base_seed": WEIGHTED_BASE_SEED,
            "common24_auto_start": False,
            "confirmation_auto_start": False,
        },
        "evaluator_implementation_sha256": evaluator_implementation_sha256_v1(),
        "pool_manifest_sha256": _sha(POOL_ROOT / "pool_manifest.json"),
        "resource_budget_sha256": _sha(RESOURCE_CONFIG),
        **AUTHORITY_FALSE,
        "candidate_status": "candidate_only",
        "performance_run_started": False,
    }
    return {**manifest, "manifest_sha256": _write_json(output / "candidate_manifest.json", manifest), "output_root": str(output)}


def _build_games(*, arm: str, policy_path: Path, policy_sha: str, deck_path: Path, deck_sha: str, refs: Sequence[str], base_seed: int = WEIGHTED_BASE_SEED, games_per_opponent_seat: int = WEIGHTED_GAMES_PER_OPPONENT_SEAT, block_id: str | None = None) -> tuple[object, ...]:
    pool = load_opponent_pool_v1(POOL_ROOT)
    spec = _policy_spec(policy_path=policy_path, policy_sha=policy_sha, deck_path=deck_path, deck_sha=deck_sha, arm=arm)
    built = build_native_candidate_games_v1(
        candidate_id=f"tomato-setup-priority-{arm}",
        candidate=spec,
        pool=pool,
        reference_ids=refs,
        games_per_opponent_seat=games_per_opponent_seat,
        base_seed=base_seed,
        block_id=block_id or f"{SCHEMA}-weighted48",
    )
    return tuple(replace_game(game, arm=arm) for game in built)


def replace_game(game: object, *, arm: str) -> object:
    """Bind comparison metadata locally; do not depend on another lane's private helper."""

    from dataclasses import replace

    return replace(game, metadata={**dict(game.metadata), "comparison_arm": arm, "weighted_meta_train": True, **AUTHORITY_FALSE})


def _weighted(rows: Sequence[Mapping[str, object]], weights: Mapping[str, float]) -> dict[str, object]:
    numerator = denominator = 0.0
    per_opponent: dict[str, object] = {}
    for opponent, weight in weights.items():
        values = [row for row in rows if str(row.get("opponent_id")) == opponent]
        score = sum(1.0 if row.get("outcome") == "win" else 0.5 if row.get("outcome") == "draw" else 0.0 for row in values)
        rate = score / len(values) if values else None
        per_opponent[opponent] = {"weight": weight, "games": len(values), "rate": rate}
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
        "seat_counts": {str(seat): len([row for row in rows if int(row.get("seat", -1)) == seat]) for seat in (0, 1)},
    }


def _warmup(*, output: Path, budget: ResourceBudget) -> dict[str, object]:
    snapshot = ResourceSnapshot.collect()
    plan = surface.build_warmup_plan(budget=budget, task_cap=budget.max_workers, snapshot=snapshot, ramp_workers=RAMP_WORKERS)
    if plan.get("warmup_status") != "ready":
        raise SetupPriorityError("ResourceGovernor blocked warmup")
    pool = load_opponent_pool_v1(POOL_ROOT)
    subset = surface.load_meta_train_subset(META_MANIFEST)
    refs = _references(subset)[-2:]
    records: list[dict[str, object]] = []
    for workers in RAMP_WORKERS:
        if workers > int(plan["safe_workers"]):
            records.append({"workers": workers, "status": "not_admitted"})
            continue
        before = ResourceSnapshot.collect()
        spec = _policy_spec(policy_path=TOMATO_PARENT_POLICY, policy_sha=TOMATO_PARENT_POLICY_SHA256, deck_path=TOMATO_PARENT_DECK, deck_sha=TOMATO_PARENT_DECK_SHA256, arm=f"warmup-{workers}")
        games = build_native_candidate_games_v1(candidate_id=f"tomato-setup-priority-warmup-{workers}", candidate=spec, pool=pool, reference_ids=refs, games_per_opponent_seat=1, base_seed=WARMUP_BASE_SEED + workers * 100, block_id=f"{SCHEMA}-warmup-{workers}")
        destination = output / "warmup" / f"workers-{workers}" / "evaluation"
        started = time.monotonic()
        result = run_parallel_cabt_evaluation(games, output_dir=destination, max_workers=workers, worker_recycle_games=budget.recycle_games, overwrite=False)
        elapsed = max(time.monotonic() - started, 1e-9)
        after = ResourceSnapshot.collect()
        summary = result["summary"]
        records.append({"workers": workers, "status": "DONE", "requested_games": summary["requested_games"], "completed_games": summary["completed_games"], "faults": summary["faults"], "fault_gate": int(summary["faults"]) == 0, "throughput_games_per_second": summary["completed_games"] / elapsed, "memory_available_before_bytes": before.memory_available_bytes, "memory_available_after_bytes": after.memory_available_bytes, "rss_before_bytes": before.process_rss_bytes, "rss_after_bytes": after.process_rss_bytes, "worker_restarts_observed": 0})
    telemetry = {"schema_version": f"{SCHEMA}-warmup", "budget": budget.to_dict(), "governor_plan": plan, "governor_decision": ResourceGovernor(budget).decide(task_cap=budget.max_workers, snapshot=snapshot).to_dict(), "ramp": records, "authority": dict(AUTHORITY_FALSE), "no_process_kill": True}
    telemetry["telemetry_sha256"] = _write_json(output / "warmup_telemetry.json", telemetry)
    return telemetry


def execute(*, output: Path = OUTPUT_DEFAULT) -> dict[str, object]:
    manifest = materialize_manifest(output=output)
    output_path = Path(str(manifest["output_root"]))
    budget = ResourceBudget.from_json(RESOURCE_CONFIG)
    warmup = _warmup(output=output_path, budget=budget)
    refs = _references(manifest["meta_train_subset"])
    parent = manifest["parent"]
    specs: list[tuple[str, Path, str, Path, str]] = [("parent", Path(str(parent["policy_path"])), str(parent["policy_sha256"]), Path(str(parent["deck_path"])), str(parent["deck_sha256"]))]
    for row in manifest["variants"]:
        specs.append((str(row["candidate_id"]), Path(str(row["policy_path"])), str(row["policy_sha256"]), Path(str(row["deck_sidecar_path"])), TOMATO_PARENT_DECK_SHA256))
    games: list[object] = []
    for arm, policy_path, policy_sha, deck_path, deck_sha in specs:
        games.extend(_build_games(arm=arm, policy_path=policy_path, policy_sha=policy_sha, deck_path=deck_path, deck_sha=deck_sha, refs=refs))
    expected = len(specs) * len(refs) * 2 * WEIGHTED_GAMES_PER_OPPONENT_SEAT
    if len(games) != expected or len({game.game_id for game in games}) != expected:
        raise SetupPriorityError("weighted game count/GID gate failed")
    grouped: dict[str, list[object]] = defaultdict(list)
    for game in games:
        grouped[str(game.metadata["comparison_arm"])].append(game)
    parent_keys = {(g.opponent_id, g.seat, int(g.metadata["repetition"])): g for g in grouped["parent"]}
    for arm in sorted(set(grouped) - {"parent"}):
        keys = {(g.opponent_id, g.seat, int(g.metadata["repetition"])): g for g in grouped[arm]}
        if keys.keys() != parent_keys.keys() or any(keys[key].seed != parent_keys[key].seed for key in parent_keys):
            raise SetupPriorityError(f"paired schedule mismatch: {arm}")
    before = ResourceSnapshot.collect()
    decision = ResourceGovernor(budget).decide(task_cap=budget.max_workers, snapshot=before)
    workers = int(decision.recommended_workers)
    if workers <= 0:
        raise SetupPriorityError("ResourceGovernor blocked weighted48")
    destination = output_path / "weighted48" / "evaluation"
    started = time.monotonic()
    result = run_parallel_cabt_evaluation(tuple(games), output_dir=destination, max_workers=workers, worker_recycle_games=budget.recycle_games, overwrite=False)
    elapsed = max(time.monotonic() - started, 1e-9)
    after = ResourceSnapshot.collect()
    final_grouped: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    for row in result["rows"]:
        final_grouped[str(row["metadata"].get("comparison_arm", "unknown"))].append(row)
    weights = {str(key): float(value) for key, value in manifest["meta_train_subset"]["selected_weights"].items()}
    arms = {arm: _weighted(rows, weights) for arm, rows in sorted(final_grouped.items())}
    parent_score = float(arms["parent"]["weighted_meta_score"])
    candidates = []
    for arm in sorted(set(arms) - {"parent"}):
        row = next(item for item in manifest["variants"] if item["candidate_id"] == arm)
        delta = float(arms[arm]["weighted_meta_score"]) - parent_score
        candidates.append({"candidate_id": arm, "policy_sha256": row["policy_sha256"], "priority_config_sha256": row["priority_config_sha256"], "weighted_delta": delta, "weighted_delta_points": delta * 100.0, "fault_gate": int(arms[arm]["faults"]) == 0, "identity_gate": bool(arms[arm]["unique_game_ids"] and arms[arm]["unique_seeds"]), "paired_strata_gate": True, "status": "weighted_positive_candidate_only" if int(arms[arm]["faults"]) == 0 and delta > 0 else "candidate_only"})
    summary = {"schema_version": f"{SCHEMA}-weighted48", "manifest_sha256": _sha(output_path / "candidate_manifest.json"), "warmup_telemetry_sha256": warmup["telemetry_sha256"], "weighted_subset_sha256": manifest["meta_train_subset"]["subset_sha256"], "arms": arms, "parent_weighted_meta_score": parent_score, "candidates": candidates, "all_faults_zero": int(result["summary"]["faults"]) == 0, "telemetry": {"workers": workers, "governor_decision": decision.to_dict(), "requested_games": expected, "completed_games": result["summary"]["completed_games"], "faults": result["summary"]["faults"], "elapsed_seconds_wall": elapsed, "throughput_games_per_second": result["summary"]["completed_games"] / elapsed, "memory_available_before_bytes": before.memory_available_bytes, "memory_available_after_bytes": after.memory_available_bytes, "rss_before_bytes": before.process_rss_bytes, "rss_after_bytes": after.process_rss_bytes, "worker_recycle_games": budget.recycle_games}, **AUTHORITY_FALSE, "candidate_status": "candidate_only", "next_gate": "positive weighted candidates only; no automatic common24/384/longrun"}
    summary["summary_sha256"] = _write_json(output_path / "weighted48_summary.json", summary)
    summary["summary_md_sha256"] = surface._write_text_no_clobber(output_path / "weighted48_summary.md", "# Tomato setup-active priority weighted48\n\n" + "\n".join(f"- {row['candidate_id']}: {row['weighted_delta_points']:+.3f}pt; faults={row['fault_gate']}; status={row['status']}" for row in candidates) + "\n")
    summary["final_summary_sha256"] = _write_json(output_path / "final_summary.json", {"schema_version": SCHEMA, "output_root": str(output_path), "weighted_summary_sha256": summary["summary_sha256"], "weighted_summary_md_sha256": summary["summary_md_sha256"], "warmup_telemetry_sha256": warmup["telemetry_sha256"], **AUTHORITY_FALSE, "performance_run_started": True})
    return summary


def materialize_common24_manifest(*, output: Path = COMMON24_OUTPUT_DEFAULT, source_root: Path = COMMON24_SOURCE_ROOT) -> dict[str, object]:
    """Materialize a full common24 guardrail from the sealed weighted candidates."""

    output = _fresh_root(output)
    source_manifest_path = source_root / "candidate_manifest.json"
    if not source_manifest_path.is_file():
        raise SetupPriorityError("sealed weighted source manifest is missing")
    source_manifest_sha = _sha(source_manifest_path)
    source_manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))
    if source_manifest.get("schema_version") != SCHEMA:
        raise SetupPriorityError("common24 source manifest schema mismatch")
    refs, heldout = build_common24_reference_ids()
    if _sha(TOMATO_PARENT_POLICY) != TOMATO_PARENT_POLICY_SHA256 or _sha(TOMATO_PARENT_DECK) != TOMATO_PARENT_DECK_SHA256:
        raise SetupPriorityError("Tomato parent identity changed")
    materialized: list[dict[str, object]] = []
    for row in source_manifest.get("variants", ()):
        candidate_id = str(row["candidate_id"])
        source_policy = Path(str(row["policy_path"]))
        source_deck = Path(str(row["deck_sidecar_path"]))
        policy_path = output / "policies" / candidate_id / "main.py"
        deck_path = policy_path.parent / "deck.csv"
        policy_sha = _write_bytes_exclusive(policy_path, source_policy.read_bytes())
        deck_sha = _write_deck_copy(source_deck, deck_path)
        if policy_sha != str(row["policy_sha256"]):
            raise SetupPriorityError(f"candidate policy copy SHA mismatch: {candidate_id}")
        materialized.append({**row, "policy_path": str(policy_path.resolve()), "policy_sha256": policy_sha, "deck_sidecar_path": str(deck_path.resolve()), "deck_sidecar_sha256": deck_sha})
    if len(materialized) != 2:
        raise SetupPriorityError("common24 requires exactly two sealed setup-priority candidates")
    manifest = {
        "schema_version": f"{SCHEMA}-common24",
        "purpose": "TOMATO_NATIVE_PARENT_SETUP_ACTIVE_PRIORITY_COMMON24_GUARDRAIL",
        "source_weighted_manifest_path": str(source_manifest_path.resolve()),
        "source_weighted_manifest_sha256": source_manifest_sha,
        "parent": source_manifest["parent"],
        "variants": materialized,
        "reference_ids": list(refs),
        "heldout_evaluation_only_ids": list(heldout),
        "heldout_training_exposure": 0,
        "protocol": {
            "common24_games_per_arm": len(refs) * 2 * WEIGHTED_GAMES_PER_OPPONENT_SEAT,
            "same_seed_schedule_across_arms": True,
            "common24_base_seed": COMMON24_BASE_SEED,
            "meta_train_weighting_used": False,
            "heldout_training_exposure": 0,
            "weighted_source_subset_sha256": source_manifest.get("meta_train_subset", {}).get("subset_sha256"),
            "confirmation_auto_start": False,
        },
        "broad_config_sha256": _sha(_BROAD_CONFIG),
        "evaluator_implementation_sha256": evaluator_implementation_sha256_v1(),
        "pool_manifest_sha256": _sha(POOL_ROOT / "pool_manifest.json"),
        "resource_budget_sha256": _sha(RESOURCE_CONFIG),
        **AUTHORITY_FALSE,
        "candidate_status": "candidate_only",
        "performance_run_started": False,
    }
    return {**manifest, "manifest_sha256": _write_json(output / "common24_manifest.json", manifest), "output_root": str(output)}


def execute_common24(*, output: Path = COMMON24_OUTPUT_DEFAULT, source_root: Path = COMMON24_SOURCE_ROOT) -> dict[str, object]:
    manifest = materialize_common24_manifest(output=output, source_root=source_root)
    output_path = Path(str(manifest["output_root"]))
    budget = ResourceBudget.from_json(RESOURCE_CONFIG)
    warmup = _warmup(output=output_path, budget=budget)
    refs = tuple(str(item) for item in manifest["reference_ids"])
    parent = manifest["parent"]
    specs: list[tuple[str, Path, str, Path, str]] = [("parent", Path(str(parent["policy_path"])), str(parent["policy_sha256"]), Path(str(parent["deck_path"])), str(parent["deck_sha256"]))]
    for row in manifest["variants"]:
        specs.append((str(row["candidate_id"]), Path(str(row["policy_path"])), str(row["policy_sha256"]), Path(str(row["deck_sidecar_path"])), TOMATO_PARENT_DECK_SHA256))
    games: list[object] = []
    for arm, policy_path, policy_sha, deck_path, deck_sha in specs:
        games.extend(_build_games(arm=arm, policy_path=policy_path, policy_sha=policy_sha, deck_path=deck_path, deck_sha=deck_sha, refs=refs, base_seed=COMMON24_BASE_SEED, games_per_opponent_seat=WEIGHTED_GAMES_PER_OPPONENT_SEAT, block_id=f"{SCHEMA}-common24"))
    expected = len(specs) * len(refs) * 2 * WEIGHTED_GAMES_PER_OPPONENT_SEAT
    if len(games) != expected or len({game.game_id for game in games}) != expected:
        raise SetupPriorityError("common24 game count/GID gate failed")
    grouped: dict[str, list[object]] = defaultdict(list)
    for game in games:
        grouped[str(game.metadata["comparison_arm"])].append(game)
    parent_keys = {(g.opponent_id, g.seat, int(g.metadata["repetition"])): g for g in grouped["parent"]}
    for arm in sorted(set(grouped) - {"parent"}):
        keys = {(g.opponent_id, g.seat, int(g.metadata["repetition"])): g for g in grouped[arm]}
        if keys.keys() != parent_keys.keys() or any(keys[key].seed != parent_keys[key].seed for key in parent_keys):
            raise SetupPriorityError(f"common24 paired schedule mismatch: {arm}")
    before = ResourceSnapshot.collect()
    decision = ResourceGovernor(budget).decide(task_cap=budget.max_workers, snapshot=before)
    workers = int(decision.recommended_workers)
    if workers <= 0:
        raise SetupPriorityError("ResourceGovernor blocked common24")
    destination = output_path / "common24" / "evaluation"
    started = time.monotonic()
    result = run_parallel_cabt_evaluation(tuple(games), output_dir=destination, max_workers=workers, worker_recycle_games=budget.recycle_games, overwrite=False)
    elapsed = max(time.monotonic() - started, 1e-9)
    after = ResourceSnapshot.collect()
    final_grouped: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    for row in result["rows"]:
        final_grouped[str(row["metadata"].get("comparison_arm", "unknown"))].append(row)
    equal_weights = {opponent: 1.0 for opponent in refs}
    heldout = set(manifest["heldout_evaluation_only_ids"])
    arms = {}
    for arm, rows in sorted(final_grouped.items()):
        entry = _weighted(rows, equal_weights)
        heldout_rows = [row for row in rows if str(row.get("opponent_id")) in heldout]
        entry["heldout_evaluation_only"] = _weighted(heldout_rows, {opponent: 1.0 for opponent in heldout})
        entry["heldout_training_exposure"] = 0
        arms[arm] = entry
    parent_score = float(arms["parent"]["weighted_meta_score"])
    candidates = []
    for arm in sorted(set(arms) - {"parent"}):
        row = next(item for item in manifest["variants"] if item["candidate_id"] == arm)
        delta = float(arms[arm]["weighted_meta_score"]) - parent_score
        candidates.append({"candidate_id": arm, "policy_sha256": row["policy_sha256"], "priority_config_sha256": row["priority_config_sha256"], "common24_delta": delta, "common24_delta_points": delta * 100.0, "fault_gate": int(arms[arm]["faults"]) == 0, "identity_gate": bool(arms[arm]["unique_game_ids"] and arms[arm]["unique_seeds"]), "paired_strata_gate": True, "heldout_training_exposure": 0, "status": "common24_positive_candidate_only" if int(arms[arm]["faults"]) == 0 and delta > 0 else "candidate_only"})
    summary = {"schema_version": f"{SCHEMA}-common24", "manifest_sha256": _sha(output_path / "common24_manifest.json"), "warmup_telemetry_sha256": warmup["telemetry_sha256"], "arms": arms, "parent_common24_score": parent_score, "candidates": candidates, "all_faults_zero": int(result["summary"]["faults"]) == 0, "heldout_training_exposure": 0, "telemetry": {"workers": workers, "governor_decision": decision.to_dict(), "requested_games": expected, "completed_games": result["summary"]["completed_games"], "faults": result["summary"]["faults"], "elapsed_seconds_wall": elapsed, "throughput_games_per_second": result["summary"]["completed_games"] / elapsed, "memory_available_before_bytes": before.memory_available_bytes, "memory_available_after_bytes": after.memory_available_bytes, "rss_before_bytes": before.process_rss_bytes, "rss_after_bytes": after.process_rss_bytes, "worker_recycle_games": budget.recycle_games}, **AUTHORITY_FALSE, "candidate_status": "candidate_only", "next_gate": "positive common24 candidates only; no automatic 384/longrun"}
    summary["summary_sha256"] = _write_json(output_path / "common24_summary.json", summary)
    summary["summary_md_sha256"] = surface._write_text_no_clobber(output_path / "common24_summary.md", "# Tomato setup-active priority common24\n\n" + "\n".join(f"- {row['candidate_id']}: {row['common24_delta_points']:+.3f}pt; faults={row['fault_gate']}; status={row['status']}" for row in candidates) + "\n")
    summary["final_summary_sha256"] = _write_json(output_path / "final_summary.json", {"schema_version": f"{SCHEMA}-common24", "output_root": str(output_path), "common24_summary_sha256": summary["summary_sha256"], "common24_summary_md_sha256": summary["summary_md_sha256"], "warmup_telemetry_sha256": warmup["telemetry_sha256"], **AUTHORITY_FALSE, "performance_run_started": True})
    return summary


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT_DEFAULT)
    parser.add_argument("--common24", action="store_true", help="run the full common24 evaluation-only guardrail")
    parser.add_argument("--source-root", type=Path, default=COMMON24_SOURCE_ROOT, help="sealed weighted root for --common24")
    args = parser.parse_args()
    result = execute_common24(output=args.output, source_root=args.source_root) if args.common24 else execute(output=args.output)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "OUTPUT_DEFAULT",
    "COMMON24_OUTPUT_DEFAULT",
    "COMMON24_SOURCE_ROOT",
    "SetupPriorityError",
    "TOMATO_PARENT_POLICY",
    "TOMATO_PARENT_POLICY_SHA256",
    "build_setup_priority_variants",
    "build_common24_reference_ids",
    "execute",
    "execute_common24",
    "materialize_common24_manifest",
    "materialize_manifest",
    "materialize_setup_priority_policy_copy",
]
