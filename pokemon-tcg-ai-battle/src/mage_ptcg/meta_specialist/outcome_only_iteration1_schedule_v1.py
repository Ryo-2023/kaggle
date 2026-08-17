"""Strict outcome-only iteration-1 schedule derived from a candidate ledger.

Only terminal WDL and public identity/stratum fields are projected from the
sealed candidate ledger.  The ledger's action configuration and all runtime
metadata remain outside the schedule.  The result is a research-only
META_TRAIN schedule and never a training dataset.
"""

from __future__ import annotations

from collections import defaultdict
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping

from mage_ptcg.meta_specialist.meta_distribution_v1 import load_meta_distribution_manifest_v1
from mage_ptcg.meta_specialist.outcome_only_hard_negative_v1 import (
    FORMULA_V1,
    _allocate_counts,
    _capped_distribution,
    verify_outcome_only_hard_negative_schedule_v1,
)
from mage_ptcg.meta_specialist.outcome_only_policy_fixed_confirmation_v1 import (
    OutcomeOnlyPolicyFixedConfirmationError,
    verify_policy_fixed_confirmation_v1,
)


SCHEMA_V1 = "meta-specialist-outcome-only-hard-negative-iteration-v1"
PURPOSE_V1 = "META_TRAIN_OUTCOME_ONLY_HARD_NEGATIVE_ITERATION_1_RESEARCH_ONLY"
AUTHORITY_FALSE_V1 = {
    "training_authority": False,
    "promotion_authority": False,
    "submission_authority": False,
    "external_execution_authority": False,
    "longrun_authority": False,
}
_WDL_SCORE = {"win": 1.0, "draw": 0.5, "loss": 0.0}
_SHA_HEX = frozenset("0123456789abcdef")
_MANIFEST_KEYS = frozenset(
    {
        "schema_version", "purpose", "iteration", "quota", "parameters", "sources",
        "confirmation_identity", "candidate_identity", "entries", "train_ids", "heldout_ids",
        "source_projection_fields", "source_projection_forbidden_fields", "summary", "authority",
        "research_only", "ready_for_evaluation", "schedule_sha256",
    }
)
_SOURCE_FIELDS = ["game_id", "opponent_id", "opponent_identity", "outcome", "seat", "seed"]


class OutcomeOnlyIteration1ScheduleError(ValueError):
    """Raised when an iteration-1 outcome schedule cannot be sealed."""


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise OutcomeOnlyIteration1ScheduleError(f"value is not canonical JSON: {exc}") from exc


def _sha_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sha(value: object, field: str) -> str:
    if type(value) is not str or len(value) != 64 or any(c not in _SHA_HEX for c in value):
        raise OutcomeOnlyIteration1ScheduleError(f"{field} must be a lowercase SHA-256")
    return value


def _inside(root: Path, value: Path | str, field: str) -> Path:
    candidate = Path(value)
    path = (root / candidate).resolve() if not candidate.is_absolute() else candidate.resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise OutcomeOnlyIteration1ScheduleError(f"{field} escapes repo_root") from exc
    if not path.is_file():
        raise OutcomeOnlyIteration1ScheduleError(f"{field} is not a file: {path}")
    return path


def _semantic_sha(manifest: Mapping[str, object]) -> str:
    body = {key: value for key, value in manifest.items() if key != "schedule_sha256"}
    return hashlib.sha256((SCHEMA_V1 + "\0").encode("ascii") + _canonical(body)).hexdigest()


def _strict_jsonl(path: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    try:
        with path.open("rb") as handle:
            for line_no, raw in enumerate(handle, 1):
                if not raw.endswith(b"\n") or raw == b"\n":
                    raise OutcomeOnlyIteration1ScheduleError(f"ledger framing invalid at line {line_no}")
                value = json.loads(raw[:-1].decode("utf-8"))
                if type(value) is not dict:
                    raise OutcomeOnlyIteration1ScheduleError("ledger row must be an object")
                rows.append(value)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise OutcomeOnlyIteration1ScheduleError(f"candidate ledger is invalid: {path}") from exc
    if not rows:
        raise OutcomeOnlyIteration1ScheduleError("candidate ledger is empty")
    return rows


def _load_confirmation(root: Path, confirmation_path: Path) -> dict[str, object]:
    try:
        manifest = json.loads(confirmation_path.read_text(encoding="utf-8"))
        if type(manifest) is not dict:
            raise ValueError("confirmation must be an object")
        verify_policy_fixed_confirmation_v1(manifest, repo_root=root)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError, OutcomeOnlyPolicyFixedConfirmationError) as exc:
        raise OutcomeOnlyIteration1ScheduleError(f"confirmation verification failed: {exc}") from exc
    return manifest


def _source_binding(root: Path, path: Path, role: str) -> dict[str, str]:
    return {"path": str(path.relative_to(root)), "sha256": _sha_file(path), "role": role}


def _candidate_rows(
    rows: list[dict[str, object]], *, confirmation: Mapping[str, object], ledger_sha: str
) -> tuple[list[dict[str, object]], dict[str, object]]:
    candidate_id = confirmation["candidate_id"]
    candidate_sha = confirmation["candidate_policy_sha256"]
    heldout = set(str(x) for x in confirmation["heldout_ids"])
    train = set(str(x) for x in confirmation["train_ids"])
    selected: list[dict[str, object]] = []
    seen: set[str] = set()
    for row in rows:
        required = {"game_id", "opponent_id", "opponent_identity", "outcome", "policy_sha256", "deck_sha256", "seat", "seed", "status", "fault_kind", "metadata"}
        if not required.issubset(row):
            raise OutcomeOnlyIteration1ScheduleError("candidate ledger row lacks sealed public fields")
        game_id = row["game_id"]
        if type(game_id) is not str or not game_id or game_id in seen:
            raise OutcomeOnlyIteration1ScheduleError("candidate ledger game_id is missing/duplicated")
        seen.add(game_id)
        metadata = row["metadata"]
        if type(metadata) is not dict or metadata.get("arm") != "candidate":
            continue
        if metadata.get("candidate_id") != candidate_id or metadata.get("candidate_policy_sha256") != candidate_sha:
            raise OutcomeOnlyIteration1ScheduleError("candidate ledger identity differs from confirmation")
        if metadata.get("split") != "META_TRAIN" or metadata.get("heldout_exposure") != 0 or metadata.get("synthetic_opponent") is not False:
            raise OutcomeOnlyIteration1ScheduleError("candidate ledger has heldout/synthetic exposure")
        if row["policy_sha256"] != candidate_sha or row["status"] != "DONE" or row["fault_kind"] is not None:
            raise OutcomeOnlyIteration1ScheduleError("candidate ledger has non-terminal or wrong-policy row")
        if row["outcome"] not in _WDL_SCORE or row["seat"] not in (0, 1) or type(row["seed"]) is not int:
            raise OutcomeOnlyIteration1ScheduleError("candidate ledger WDL stratum is malformed")
        opponent_id = row["opponent_id"]
        if type(opponent_id) is not str or opponent_id not in train or opponent_id in heldout:
            raise OutcomeOnlyIteration1ScheduleError("candidate ledger opponent is outside META_TRAIN")
        identity = row["opponent_identity"]
        if type(identity) is not dict or identity.get("usage_boundary") != "local_eval_only" or identity.get("meta_split") != "META_TRAIN":
            raise OutcomeOnlyIteration1ScheduleError("candidate ledger opponent permission identity is invalid")
        selected.append({
            "game_id": game_id,
            "opponent_id": opponent_id,
            "opponent_identity": {
                "policy_sha256": identity.get("policy_sha256"),
                "deck_sha256": identity.get("deck_sha256"),
                "source": identity.get("source"),
                "usage_boundary": identity.get("usage_boundary"),
                "meta_split": identity.get("meta_split"),
            },
            "outcome": row["outcome"],
            "seat": row["seat"],
            "seed": row["seed"],
        })
    if len(selected) != 384:
        raise OutcomeOnlyIteration1ScheduleError(f"candidate ledger must contain exactly 384 candidate rows, got {len(selected)}")
    return selected, {"candidate_id": candidate_id, "policy_sha256": candidate_sha, "source_ledger_sha256": ledger_sha}


def _build_manifest(*, root: Path, ledger_path: Path, confirmation_path: Path, quota: int) -> dict[str, object]:
    if type(quota) is not int or quota != 96:
        raise OutcomeOnlyIteration1ScheduleError("iteration-1 schedule quota must be 96")
    confirmation = _load_confirmation(root, confirmation_path)
    rows = _strict_jsonl(ledger_path)
    ledger_sha = _sha_file(ledger_path)
    selected, candidate_identity = _candidate_rows(rows, confirmation=confirmation, ledger_sha=ledger_sha)
    schedule_path = _inside(root, str(confirmation["schedule_path"]), "parent outcome schedule")
    try:
        source_schedule = verify_outcome_only_hard_negative_schedule_v1(schedule_path, root)
    except Exception as exc:
        raise OutcomeOnlyIteration1ScheduleError(f"parent outcome schedule verification failed: {exc}") from exc
    family_by_id = {str(item["opponent_id"]): str(item["family"]) for item in source_schedule["entries"]}
    quota_by_id = {str(item["opponent_id"]): int(item["quota"]) for item in source_schedule["entries"]}
    all_train_ids = set(quota_by_id)
    expected_ids = {key for key, value in quota_by_id.items() if value > 0}
    selected_ids = {str(row["opponent_id"]) for row in selected}
    if selected_ids != expected_ids:
        raise OutcomeOnlyIteration1ScheduleError("candidate ledger META_TRAIN IDs differ from parent schedule")
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in selected:
        grouped[str(row["opponent_id"])].append(row)
    for opponent_id in expected_ids:
        expected_count = quota_by_id[opponent_id] * 4
        if len(grouped[opponent_id]) != expected_count:
            raise OutcomeOnlyIteration1ScheduleError("candidate ledger support differs from confirmation quota")
    family_members: dict[str, list[str]] = defaultdict(list)
    stats: dict[str, dict[str, object]] = {}
    max_games = max(len(value) for value in grouped.values())
    for opponent_id in sorted(expected_ids):
        games = grouped[opponent_id]
        family = family_by_id[opponent_id]
        family_members[family].append(opponent_id)
        wins = sum(row["outcome"] == "win" for row in games)
        draws = sum(row["outcome"] == "draw" for row in games)
        losses = sum(row["outcome"] == "loss" for row in games)
        total = len(games)
        score = (wins + 0.5 * draws) / total
        identity = games[0]["opponent_identity"]
        if any(row["opponent_identity"] != identity for row in games):
            raise OutcomeOnlyIteration1ScheduleError("opponent identity changes within ledger")
        stats[opponent_id] = {
            "opponent_id": opponent_id, "family": family, "split": "META_TRAIN",
            "policy_sha256": identity["policy_sha256"], "deck_sha256": identity["deck_sha256"],
            "source": identity["source"], "usage_boundary": identity["usage_boundary"],
            "games": total, "wins": wins, "draws": draws, "losses": losses, "faults": 0,
            "score": score, "hard_negative": 1.0 - score, "reliability": 1.0,
            "underexposure": (max_games - total) / max_games,
            "diversity": 1.0 / len([x for x in expected_ids if family_by_id[x] == family]),
            "seat_games": {str(seat): sum(row["seat"] == seat for row in games) for seat in (0, 1)},
            "seat_score": {str(seat): sum(_WDL_SCORE[row["outcome"]] for row in games if row["seat"] == seat) / max(1, sum(row["seat"] == seat for row in games)) for seat in (0, 1)},
        }
    family_raw = {family: sum(0.70 * stats[x]["hard_negative"] + 0.15 * stats[x]["underexposure"] + 0.15 * stats[x]["diversity"] for x in members) for family, members in family_members.items()}
    family_caps = {family: min(0.55, 0.35 * len(members)) for family, members in family_members.items()}
    remaining = set(family_raw); family_weights: dict[str, float] = {}; mass = 1.0
    while remaining:
        denom = sum(max(0.0, float(family_raw[x])) for x in remaining)
        proposed = {x: (mass / len(remaining) if denom <= 0 else mass * max(0.0, float(family_raw[x])) / denom) for x in remaining}
        over = sorted(x for x, value in proposed.items() if value > family_caps[x] + 1e-15)
        if not over:
            family_weights.update(proposed); break
        for x in over:
            family_weights[x] = family_caps[x]; remaining.remove(x); mass -= family_caps[x]
    correction = 1.0 - sum(family_weights.values())
    if abs(correction) > 1e-12:
        eligible = [x for x in family_weights if family_weights[x] + correction <= family_caps[x] + 1e-12]
        if not eligible: raise OutcomeOnlyIteration1ScheduleError("family cap correction is infeasible")
        family_weights[min(eligible, key=lambda x: (-family_raw[x], x))] += correction
    weights: dict[str, float] = {}
    for family, members in sorted(family_members.items()):
        raw = {x: 0.70 * stats[x]["hard_negative"] + 0.15 * stats[x]["underexposure"] + 0.15 * stats[x]["diversity"] for x in members}
        weights.update(_capped_distribution(raw, total=family_weights[family], cap=0.35, field=f"opponent:{family}"))
    family_floor = 1
    extra = _allocate_counts(family_weights, quota - family_floor * len(family_members))
    quotas: dict[str, int] = {}
    for family, members in sorted(family_members.items()):
        quotas.update(_allocate_counts({x: weights[x] for x in members}, family_floor + extra[family]))
    entries = []
    for opponent_id in sorted(expected_ids):
        row = dict(stats[opponent_id]); row.update({"family_weight": family_weights[row["family"]], "raw_score": 0.70 * row["hard_negative"] + 0.15 * row["underexposure"] + 0.15 * row["diversity"], "weight": weights[opponent_id], "quota": quotas[opponent_id], "training_exposure_allowed": False, "teacher_behavior_allowed": False, "evaluation_allowed": True})
        entries.append(row)
    # A zero-quota META_TRAIN member remains in the sealed population, but it
    # contributes no inferred mass because this iteration has no WDL support
    # for it.  Keeping the row makes the population/heldout partition closed.
    for opponent_id in sorted(all_train_ids - expected_ids):
        source_entry = next(item for item in source_schedule["entries"] if item["opponent_id"] == opponent_id)
        entries.append({
            "opponent_id": opponent_id,
            "family": source_entry["family"],
            "split": "META_TRAIN",
            "policy_sha256": source_entry["policy_sha256"],
            "deck_sha256": source_entry["deck_sha256"],
            "source_sha256": source_entry["source_sha256"],
            "source": "public",
            "usage_boundary": "local_eval_only",
            "games": 0, "wins": 0, "draws": 0, "losses": 0, "faults": 0,
            "score": None, "hard_negative": None, "reliability": 0.0,
            "underexposure": 1.0, "diversity": None,
            "seat_games": {"0": 0, "1": 0}, "seat_score": {"0": None, "1": None},
            "family_weight": 0.0, "raw_score": 0.0, "weight": 0.0, "quota": 0,
            "training_exposure_allowed": False, "teacher_behavior_allowed": False,
            "evaluation_allowed": True,
        })
    sources = {
        "candidate_ledger": _source_binding(root, ledger_path, "candidate_terminal_wdl_ledger"),
        "confirmation": _source_binding(root, confirmation_path, "sealed_confirmation_manifest"),
        "parent_schedule": _source_binding(root, schedule_path, "parent_outcome_schedule"),
    }
    manifest: dict[str, object] = {
        "schema_version": SCHEMA_V1, "purpose": PURPOSE_V1, "iteration": 1, "quota": quota,
        "parameters": {"formula": FORMULA_V1, "opponent_cap": 0.35, "family_cap": 0.55, "min_family_quota": 1, "normalization": "candidate_terminal_wdl_only_largest_remainder_v1"},
        "sources": sources,
        "confirmation_identity": {"path": str(confirmation_path.relative_to(root)), "file_sha256": _sha_file(confirmation_path), "confirmation_sha256": confirmation["confirmation_sha256"]},
        "candidate_identity": candidate_identity,
        "entries": entries,
        "train_ids": sorted(all_train_ids),
        "heldout_ids": list(confirmation["heldout_ids"]),
        "source_projection_fields": list(_SOURCE_FIELDS),
        "source_projection_forbidden_fields": [],
        "summary": {"source_games": 384, "candidate_rows": 384, "included_games": sum(x["games"] for x in entries), "heldout_exposure": 0, "faults": 0, "weights_sum": sum(float(x["weight"]) for x in entries), "quota_sum": sum(int(x["quota"]) for x in entries), "action_trace_used": False, "private_fields_used": False, "teacher_labels_used": False, "training_data": False, "evaluation_schedule_only": True},
        "authority": dict(AUTHORITY_FALSE_V1), "research_only": True, "ready_for_evaluation": True,
    }
    manifest["schedule_sha256"] = _semantic_sha(manifest)
    return manifest


def build_outcome_only_iteration1_schedule_v1(*, repo_root: Path | str, candidate_ledger_path: Path | str, confirmation_path: Path | str, quota: int = 96) -> dict[str, object]:
    root = Path(repo_root).resolve()
    ledger = _inside(root, candidate_ledger_path, "candidate ledger")
    confirmation = _inside(root, confirmation_path, "confirmation")
    return {"manifest": _build_manifest(root=root, ledger_path=ledger, confirmation_path=confirmation, quota=quota)}


def verify_outcome_only_iteration1_schedule_v1(manifest: Mapping[str, object], *, repo_root: Path | str) -> dict[str, object]:
    if type(manifest) is not dict or set(manifest) != _MANIFEST_KEYS:
        raise OutcomeOnlyIteration1ScheduleError("iteration-1 manifest schema is not closed")
    if manifest.get("schedule_sha256") != _semantic_sha(manifest):
        raise OutcomeOnlyIteration1ScheduleError("schedule semantic SHA mismatch")
    if manifest.get("authority") != AUTHORITY_FALSE_V1 or manifest.get("research_only") is not True or manifest.get("ready_for_evaluation") is not True:
        raise OutcomeOnlyIteration1ScheduleError("schedule authority/readiness is invalid")
    root = Path(repo_root).resolve()
    bindings = manifest.get("sources")
    if type(bindings) is not dict or set(bindings) != {"candidate_ledger", "confirmation", "parent_schedule"}:
        raise OutcomeOnlyIteration1ScheduleError("schedule sources are not closed")
    paths: dict[str, Path] = {}
    for role, binding in bindings.items():
        if type(binding) is not dict or set(binding) != {"path", "sha256", "role"}:
            raise OutcomeOnlyIteration1ScheduleError("schedule source binding malformed")
        path = _inside(root, str(binding["path"]), f"source:{role}")
        if _sha_file(path) != binding["sha256"]:
            raise OutcomeOnlyIteration1ScheduleError(f"source SHA mismatch: {role}")
        paths[role] = path
    rebuilt = _build_manifest(root=root, ledger_path=paths["candidate_ledger"], confirmation_path=paths["confirmation"], quota=int(manifest["quota"]))
    if rebuilt != manifest:
        raise OutcomeOnlyIteration1ScheduleError("schedule does not reproduce from bound sources")
    return dict(manifest)


__all__ = ["AUTHORITY_FALSE_V1", "FORMULA_V1", "OutcomeOnlyIteration1ScheduleError", "SCHEMA_V1", "build_outcome_only_iteration1_schedule_v1", "verify_outcome_only_iteration1_schedule_v1"]
