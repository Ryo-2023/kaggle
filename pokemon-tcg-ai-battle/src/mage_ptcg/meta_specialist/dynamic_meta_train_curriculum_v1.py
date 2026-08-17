"""Deterministic dynamic curriculum for a fixed, permission-audited opponent set.

The output controls research opponent sampling only.  It never grants teacher
label, behavior-policy, promotion, submission, or external execution
authority.  META_DEV and META_FINAL always receive zero exposure.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

from mage_ptcg.meta_specialist.meta_distribution_v1 import (
    MetaDistributionRowV1,
    load_meta_distribution_manifest_v1,
)


CURRICULUM_SCHEMA_V1 = "meta-specialist-dynamic-meta-train-curriculum-v1"
_ALLOWED_USAGE = frozenset({"local_eval_only", "training_local", "training_local_and_eval"})


class DynamicMetaTrainCurriculumError(ValueError):
    """Raised when a dynamic curriculum would cross a held-out/permission boundary."""


_MANIFEST_KEYS = frozenset(
    {
        "schema_version",
        "purpose",
        "iteration",
        "seed",
        "quota",
        "sources",
        "previous_iteration",
        "outcome_ledger",
        "parameters",
        "entries",
        "summary",
        "consumer_contract",
        "authority",
        "curriculum_sha256",
    }
)
_AUTHORITY = {
    "training_authority": False,
    "promotion_authority": False,
    "submission_authority": False,
    "external_execution_authority": False,
}


@dataclass(frozen=True, slots=True)
class DynamicCurriculumEntryV1:
    opponent_id: str
    family: str
    split: str
    weight: float
    quota: int
    reason: tuple[str, ...]
    lineage: Mapping[str, object]
    statistics: Mapping[str, object]
    training_exposure_allowed: bool
    teacher_behavior_allowed: bool


@dataclass(frozen=True, slots=True)
class DynamicCurriculumPlanV1:
    iteration: int
    seed: str
    quota: int
    entries: tuple[DynamicCurriculumEntryV1, ...]
    authority: Mapping[str, bool]


def _canonical_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise DynamicMetaTrainCurriculumError(
            f"value is not canonical JSON: {exc}"
        ) from exc


def _sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise DynamicMetaTrainCurriculumError(f"cannot hash source: {path}") from exc
    return digest.hexdigest()


def _inside_root(root: Path, value: str | Path, field: str) -> Path:
    candidate = Path(value)
    path = (root / candidate).resolve() if not candidate.is_absolute() else candidate.resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise DynamicMetaTrainCurriculumError(f"{field} escapes repo_root") from exc
    if not path.is_file():
        raise DynamicMetaTrainCurriculumError(f"{field} is not a file: {path}")
    return path


def _strict_json(path: Path, *, canonical: bool = False) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=lambda pairs: _reject_duplicate_pairs(pairs),
            parse_constant=lambda token: _reject_constant(token),
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DynamicMetaTrainCurriculumError(f"invalid JSON: {path}") from exc
    if type(value) is not dict:
        raise DynamicMetaTrainCurriculumError("JSON root must be an object")
    if canonical and raw != _canonical_bytes(value):
        raise DynamicMetaTrainCurriculumError("manifest is not canonical JSON")
    return value


def _reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    output: dict[str, object] = {}
    for key, value in pairs:
        if key in output:
            raise DynamicMetaTrainCurriculumError(f"duplicate JSON key: {key}")
        output[key] = value
    return output


def _reject_constant(token: str) -> object:
    raise DynamicMetaTrainCurriculumError(f"non-finite JSON constant: {token}")


def _atomic_write_new(path: Path, payload: bytes) -> None:
    if path.exists():
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        with temporary.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if path.exists():
            raise FileExistsError(path)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _manifest_digest(body: Mapping[str, object]) -> str:
    digest = hashlib.sha256()
    digest.update((CURRICULUM_SCHEMA_V1 + "\0").encode("utf-8"))
    digest.update(_canonical_bytes(body))
    return digest.hexdigest()


def _capped_distribution(
    raw: Mapping[str, float], *, total: float, cap: float, field: str
) -> dict[str, float]:
    if not raw or total < 0 or not math.isfinite(total):
        raise DynamicMetaTrainCurriculumError(f"{field} distribution is invalid")
    if not 0.0 < cap <= 1.0 or cap * len(raw) + 1e-12 < total:
        raise DynamicMetaTrainCurriculumError(f"{field} cap is infeasible")
    remaining = set(raw)
    result: dict[str, float] = {}
    mass = total
    while remaining:
        denom = sum(max(0.0, float(raw[key])) for key in remaining)
        provisional = {
            key: (mass / len(remaining) if denom <= 0 else mass * max(0.0, float(raw[key])) / denom)
            for key in remaining
        }
        over = sorted(key for key, value in provisional.items() if value > cap + 1e-15)
        if not over:
            result.update(provisional)
            break
        for key in over:
            result[key] = cap
            remaining.remove(key)
            mass -= cap
        if mass < -1e-12:
            raise DynamicMetaTrainCurriculumError(f"{field} cap allocation underflow")
    correction = total - sum(result.values())
    if abs(correction) > 1e-12:
        key = min(result, key=lambda item: (-raw[item], item))
        result[key] += correction
    return result


def _variable_capped_distribution(
    raw: Mapping[str, float], *, caps: Mapping[str, float], total: float, field: str
) -> dict[str, float]:
    if set(raw) != set(caps) or sum(caps.values()) + 1e-12 < total:
        raise DynamicMetaTrainCurriculumError(f"{field} caps are infeasible")
    remaining = set(raw)
    result: dict[str, float] = {}
    mass = total
    while remaining:
        denom = sum(max(0.0, float(raw[key])) for key in remaining)
        provisional = {
            key: (mass / len(remaining) if denom <= 0 else mass * max(0.0, float(raw[key])) / denom)
            for key in remaining
        }
        over = sorted(
            key for key in remaining if provisional[key] > caps[key] + 1e-15
        )
        if not over:
            result.update(provisional)
            break
        for key in over:
            result[key] = caps[key]
            remaining.remove(key)
            mass -= caps[key]
    correction = total - sum(result.values())
    if abs(correction) > 1e-12:
        eligible = [key for key in result if result[key] + correction <= caps[key] + 1e-12]
        if not eligible:
            raise DynamicMetaTrainCurriculumError(f"{field} correction exceeds caps")
        key = min(eligible, key=lambda item: (-raw[item], item))
        result[key] += correction
    return result


def _allocate_counts(weights: Mapping[str, float], quota: int) -> dict[str, int]:
    raw = {key: quota * value / sum(weights.values()) for key, value in weights.items()}
    counts = {key: int(math.floor(value)) for key, value in raw.items()}
    remainder = quota - sum(counts.values())
    order = sorted(weights, key=lambda key: (-(raw[key] - counts[key]), key))
    for key in order[:remainder]:
        counts[key] += 1
    return counts


def build_dynamic_curriculum_plan_v1(
    *,
    rows: Sequence[MetaDistributionRowV1],
    selected_opponent_ids: Sequence[str],
    quota: int,
    seed: str,
    iteration: int,
    outcomes: Sequence[Mapping[str, object]],
    max_opponent_weight: float = 0.35,
    max_family_weight: float = 0.55,
    min_family_quota: int = 1,
) -> DynamicCurriculumPlanV1:
    if type(quota) is not int or quota <= 0:
        raise DynamicMetaTrainCurriculumError("quota must be positive")
    if type(seed) is not str or not seed:
        raise DynamicMetaTrainCurriculumError("seed must be non-empty")
    if type(iteration) is not int or iteration < 0:
        raise DynamicMetaTrainCurriculumError("iteration must be nonnegative")
    if type(min_family_quota) is not int or min_family_quota < 1:
        raise DynamicMetaTrainCurriculumError("min_family_quota must be positive")
    by_id = {row.opponent_id: row for row in rows}
    if len(by_id) != len(rows):
        raise DynamicMetaTrainCurriculumError("meta rows contain duplicate opponent ids")
    selected = tuple(selected_opponent_ids)
    if not selected or len(set(selected)) != len(selected) or any(key not in by_id for key in selected):
        raise DynamicMetaTrainCurriculumError("selected opponent ids are invalid")
    selected_rows = [by_id[key] for key in selected]
    train_rows = [row for row in selected_rows if row.split == "META_TRAIN"]
    if not train_rows:
        raise DynamicMetaTrainCurriculumError("selected pool has no META_TRAIN opponents")
    for row in train_rows:
        if not row.evaluation_allowed:
            raise DynamicMetaTrainCurriculumError(
                f"opponent evaluation permission denied: {row.opponent_id}"
            )
        if row.usage_boundary not in _ALLOWED_USAGE:
            raise DynamicMetaTrainCurriculumError("unknown usage boundary")

    observations: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    for outcome in outcomes:
        if not isinstance(outcome, Mapping):
            raise DynamicMetaTrainCurriculumError("outcome ledger row must be an object")
        opponent_id = outcome.get("opponent_id")
        if type(opponent_id) is not str or opponent_id not in by_id:
            raise DynamicMetaTrainCurriculumError("outcome opponent is outside the selected meta manifest")
        row = by_id[opponent_id]
        if opponent_id not in selected or row.split != "META_TRAIN":
            raise DynamicMetaTrainCurriculumError("held-out opponent appeared in iteration ledger")
        score = outcome.get("candidate_score")
        if type(score) not in (int, float) or isinstance(score, bool) or not math.isfinite(float(score)) or not 0.0 <= float(score) <= 1.0:
            raise DynamicMetaTrainCurriculumError("candidate_score must be finite in [0,1]")
        if type(outcome.get("fault")) is not bool or outcome.get("seat") not in (0, 1):
            raise DynamicMetaTrainCurriculumError("fault/seat ledger fields are invalid")
        observations[opponent_id].append(outcome)

    family_members: dict[str, list[MetaDistributionRowV1]] = defaultdict(list)
    for row in train_rows:
        family_members[row.archetype].append(row)
    if quota < min_family_quota * len(family_members):
        raise DynamicMetaTrainCurriculumError("quota cannot satisfy the family diversity floor")
    exposure_counts = {key: len(value) for key, value in observations.items()}
    max_exposure = max(exposure_counts.values(), default=0)
    raw_scores: dict[str, float] = {}
    statistics: dict[str, dict[str, object]] = {}
    reasons: dict[str, tuple[str, ...]] = {}
    for row in train_rows:
        games = observations.get(row.opponent_id, [])
        seat_exposure = {
            str(seat): sum(1 for game in games if game["seat"] == seat)
            for seat in (0, 1)
        }
        if games:
            candidate_score = sum(float(game["candidate_score"]) for game in games) / len(games)
            fault_rate = sum(bool(game["fault"]) for game in games) / len(games)
            hard = 1.0 - candidate_score
            underexposure = (max_exposure - len(games)) / max(1, max_exposure)
            seat_total = max(1, len(games))
            seat_imbalance = abs(seat_exposure["0"] - seat_exposure["1"]) / seat_total
        else:
            candidate_score = None
            fault_rate = row.observed_fault_rate
            hard = row.hard_negative_score
            underexposure = 1.0 if outcomes else 0.0
            seat_imbalance = 1.0 if outcomes else 0.0
        diversity = 1.0 / len(family_members[row.archetype])
        reliability = max(0.10, 1.0 - fault_rate)
        raw_scores[row.opponent_id] = reliability * (
            0.30 * row.weight
            + 0.40 * hard
            + 0.15 * underexposure
            + 0.10 * diversity
            + 0.05 * seat_imbalance
        )
        statistics[row.opponent_id] = {
            "games": len(games),
            "candidate_score": candidate_score,
            "fault_rate": fault_rate,
            "seat_exposure": seat_exposure,
            "hard_negative": hard,
            "underexposure": underexposure,
            "diversity": diversity,
        }
        reason = ["initial_meta_weight" if not games else "iteration_hard_negative_update"]
        if fault_rate > 0:
            reason.append("fault_reliability_penalty")
        if underexposure > 0:
            reason.append("underexposure_boost")
        reason.append("family_diversity_floor")
        reasons[row.opponent_id] = tuple(reason)

    family_raw = {
        family: sum(raw_scores[row.opponent_id] for row in members)
        for family, members in family_members.items()
    }
    family_weights = _variable_capped_distribution(
        family_raw,
        total=1.0,
        caps={
            family: min(max_family_weight, max_opponent_weight * len(members))
            for family, members in family_members.items()
        },
        field="family weight",
    )
    weights: dict[str, float] = {}
    for family, members in family_members.items():
        member_raw = {row.opponent_id: raw_scores[row.opponent_id] for row in members}
        if max_opponent_weight * len(member_raw) + 1e-12 < family_weights[family]:
            raise DynamicMetaTrainCurriculumError("opponent cap is infeasible within a family")
        weights.update(
            _capped_distribution(
                member_raw,
                total=family_weights[family],
                cap=max_opponent_weight,
                field=f"opponent weight {family}",
            )
        )

    reserved = min_family_quota * len(family_members)
    extra_family = _allocate_counts(family_weights, quota - reserved) if quota > reserved else {family: 0 for family in family_members}
    counts: dict[str, int] = {row.opponent_id: 0 for row in train_rows}
    for family, members in sorted(family_members.items()):
        family_quota = min_family_quota + extra_family[family]
        member_weights = {row.opponent_id: weights[row.opponent_id] for row in members}
        allocated = _allocate_counts(member_weights, family_quota)
        counts.update(allocated)

    entries: list[DynamicCurriculumEntryV1] = []
    for row in sorted(selected_rows, key=lambda item: item.opponent_id):
        is_train = row.split == "META_TRAIN"
        behavior_allowed = (
            is_train
            and row.training_allowed
            and row.behavior_allowed
            and row.usage_boundary in {"training_local", "training_local_and_eval"}
        )
        entries.append(
            DynamicCurriculumEntryV1(
                opponent_id=row.opponent_id,
                family=row.archetype,
                split=row.split,
                weight=weights[row.opponent_id] if is_train else 0.0,
                quota=counts[row.opponent_id] if is_train else 0,
                reason=(reasons[row.opponent_id] if is_train else ("held_out_split_zero_exposure",)),
                lineage={
                    "iteration": iteration,
                    "seed_tiebreak_sha256": hashlib.sha256(
                        f"{seed}\0{iteration}\0{row.opponent_id}".encode("utf-8")
                    ).hexdigest(),
                },
                statistics=(statistics[row.opponent_id] if is_train else {
                    "games": 0,
                    "candidate_score": None,
                    "fault_rate": None,
                    "seat_exposure": {"0": 0, "1": 0},
                }),
                training_exposure_allowed=is_train and row.evaluation_allowed,
                teacher_behavior_allowed=behavior_allowed,
            )
        )
    if abs(sum(entry.weight for entry in entries) - 1.0) > 1e-9 or sum(entry.quota for entry in entries) != quota:
        raise DynamicMetaTrainCurriculumError("curriculum mass or quota did not close")
    if any(entry.weight or entry.quota or entry.training_exposure_allowed for entry in entries if entry.split != "META_TRAIN"):
        raise DynamicMetaTrainCurriculumError("held-out split received training exposure")
    return DynamicCurriculumPlanV1(
        iteration=iteration,
        seed=seed,
        quota=quota,
        entries=tuple(entries),
        authority={
            "training_authority": False,
            "promotion_authority": False,
            "submission_authority": False,
            "external_execution_authority": False,
        },
    )


def _entry_dict(entry: DynamicCurriculumEntryV1) -> dict[str, object]:
    return {
        "opponent_id": entry.opponent_id,
        "family": entry.family,
        "split": entry.split,
        "weight": entry.weight,
        "quota": entry.quota,
        "reason": list(entry.reason),
        "lineage": dict(entry.lineage),
        "statistics": dict(entry.statistics),
        "training_exposure_allowed": entry.training_exposure_allowed,
        "teacher_behavior_allowed": entry.teacher_behavior_allowed,
    }


def _read_outcomes(path: Path | None) -> tuple[dict[str, object], ...]:
    if path is None:
        return ()
    rows: list[dict[str, object]] = []
    try:
        with path.open("rb") as handle:
            for line_number, raw in enumerate(handle, 1):
                if not raw.endswith(b"\n") or raw in (b"\n", b""):
                    raise DynamicMetaTrainCurriculumError(
                        f"outcome ledger framing is invalid at line {line_number}"
                    )
                body = raw[:-1]
                value = json.loads(
                    body.decode("utf-8"),
                    object_pairs_hook=lambda pairs: _reject_duplicate_pairs(pairs),
                    parse_constant=lambda token: _reject_constant(token),
                )
                if type(value) is not dict or body != _canonical_bytes(value):
                    raise DynamicMetaTrainCurriculumError(
                        f"outcome ledger row is not canonical at line {line_number}"
                    )
                if set(value) != {"opponent_id", "candidate_score", "fault", "seat"}:
                    raise DynamicMetaTrainCurriculumError(
                        "outcome ledger row has an invalid closed schema"
                    )
                rows.append(value)
    except OSError as exc:
        raise DynamicMetaTrainCurriculumError("cannot read outcome ledger") from exc
    if not rows:
        raise DynamicMetaTrainCurriculumError("outcome ledger must not be empty")
    return tuple(rows)


def _source_binding(root: Path, path: Path, role: str) -> dict[str, str]:
    return {
        "path": str(path.relative_to(root)),
        "file_sha256": _sha_file(path),
        "role": role,
    }


def _validate_schedule(
    schedule: Mapping[str, object], *, selected_rows: Sequence[MetaDistributionRowV1]
) -> None:
    if (
        schedule.get("research_only") is not True
        or schedule.get("promotion_authority") is not False
    ):
        raise DynamicMetaTrainCurriculumError("meta schedule authority is invalid")
    schedules = schedule.get("schedules")
    if type(schedules) is not dict:
        raise DynamicMetaTrainCurriculumError("meta schedule lacks schedules")
    evaluation = schedules.get("META_TRAIN_EVALUATION")
    if type(evaluation) is not list:
        raise DynamicMetaTrainCurriculumError("meta schedule lacks META_TRAIN_EVALUATION")
    scheduled: set[str] = set()
    for row in evaluation:
        if (
            type(row) is not dict
            or row.get("split") != "META_TRAIN"
            or type(row.get("opponent_id")) is not str
        ):
            raise DynamicMetaTrainCurriculumError("meta schedule evaluation row is invalid")
        scheduled.add(str(row["opponent_id"]))
    train = {row.opponent_id for row in selected_rows if row.split == "META_TRAIN"}
    heldout = {row.opponent_id for row in selected_rows if row.split != "META_TRAIN"}
    if not train.issubset(scheduled) or heldout & scheduled:
        raise DynamicMetaTrainCurriculumError(
            "selected split membership disagrees with the bound meta schedule"
        )


def build_dynamic_curriculum_manifest_v1(
    *,
    repo_root: str | Path,
    meta_manifest_path: str | Path,
    meta_schedule_path: str | Path,
    broad_pool_config_path: str | Path,
    output_manifest_path: str | Path,
    quota: int,
    seed: str,
    iteration: int,
    outcome_ledger_path: str | Path | None = None,
    previous_manifest_path: str | Path | None = None,
    max_opponent_weight: float = 0.35,
    max_family_weight: float = 0.55,
    min_family_quota: int = 1,
) -> dict[str, object]:
    """Build one immutable curriculum iteration without executing games/training."""
    root = Path(repo_root).resolve()
    if not root.is_dir():
        raise DynamicMetaTrainCurriculumError("repo_root must be a directory")
    meta_path = _inside_root(root, meta_manifest_path, "meta manifest")
    schedule_path = _inside_root(root, meta_schedule_path, "meta schedule")
    broad_path = _inside_root(root, broad_pool_config_path, "broad pool config")
    output = Path(output_manifest_path).resolve()
    try:
        output.relative_to(root)
    except ValueError:
        # Tests may use an isolated temporary directory; the output itself does
        # not become a trusted source and may safely live outside repo_root.
        pass
    if output.exists():
        raise FileExistsError(output)
    meta = load_meta_distribution_manifest_v1(meta_path, verify_sources=True)
    broad = _strict_json(broad_path)
    if (
        broad.get("schema_version")
        != "meta-specialist-performance-first-broad-pool-v1"
        or broad.get("local_eval_only") is not True
        or broad.get("promotion_authority") is not False
    ):
        raise DynamicMetaTrainCurriculumError("broad pool permission contract is invalid")
    opponent_ids = broad.get("opponent_ids")
    if (
        type(opponent_ids) is not list
        or not opponent_ids
        or len(opponent_ids) != len(set(opponent_ids))
        or any(type(value) is not str or not value for value in opponent_ids)
    ):
        raise DynamicMetaTrainCurriculumError("broad pool opponent ids are invalid")
    pool_path = _inside_root(root, str(broad.get("pool_manifest_path")), "pool manifest")
    if _sha_file(pool_path) != broad.get("pool_manifest_sha256"):
        raise DynamicMetaTrainCurriculumError("pool manifest SHA-256 mismatch")
    by_id = {row.opponent_id: row for row in meta.rows}
    if any(value not in by_id for value in opponent_ids):
        raise DynamicMetaTrainCurriculumError("broad pool id is absent from meta manifest")
    selected_rows = tuple(by_id[value] for value in opponent_ids)
    schedule = _strict_json(schedule_path)
    _validate_schedule(schedule, selected_rows=selected_rows)

    if iteration == 0:
        if outcome_ledger_path is not None or previous_manifest_path is not None:
            raise DynamicMetaTrainCurriculumError(
                "iteration zero cannot bind an outcome ledger or previous iteration"
            )
        outcome_path = None
        previous_path = None
        previous_binding = None
        outcome_binding = None
    else:
        if outcome_ledger_path is None or previous_manifest_path is None:
            raise DynamicMetaTrainCurriculumError(
                "updated iteration requires both previous manifest and outcome ledger"
            )
        outcome_path = _inside_root(root, outcome_ledger_path, "outcome ledger")
        previous_path = _inside_root(root, previous_manifest_path, "previous curriculum")
        previous = verify_dynamic_curriculum_manifest_v1(previous_path, root)
        if previous["iteration"] + 1 != iteration:
            raise DynamicMetaTrainCurriculumError("curriculum iteration is not consecutive")
        previous_binding = {
            "path": str(previous_path.relative_to(root)),
            "file_sha256": _sha_file(previous_path),
            "curriculum_sha256": previous["curriculum_sha256"],
        }
        outcome_binding = {
            "path": str(outcome_path.relative_to(root)),
            "file_sha256": _sha_file(outcome_path),
        }
    outcomes = _read_outcomes(outcome_path)
    plan = build_dynamic_curriculum_plan_v1(
        rows=selected_rows,
        selected_opponent_ids=tuple(opponent_ids),
        quota=quota,
        seed=seed,
        iteration=iteration,
        outcomes=outcomes,
        max_opponent_weight=max_opponent_weight,
        max_family_weight=max_family_weight,
        min_family_quota=min_family_quota,
    )
    entries = [_entry_dict(entry) for entry in plan.entries]
    selected_counts = {
        split: sum(entry.split == split for entry in plan.entries)
        for split in ("META_DEV", "META_FINAL", "META_TRAIN")
    }
    nonzero_counts = {
        split: sum(
            entry.split == split and (entry.weight > 0.0 or entry.quota > 0)
            for entry in plan.entries
        )
        for split in ("META_DEV", "META_FINAL", "META_TRAIN")
    }
    sources = [
        _source_binding(root, meta_path, "meta_distribution_manifest"),
        _source_binding(root, schedule_path, "meta_schedule"),
        _source_binding(root, broad_path, "common24_broad_pool_config"),
        _source_binding(root, pool_path, "opponent_pool_manifest"),
    ]
    manifest: dict[str, object] = {
        "schema_version": CURRICULUM_SCHEMA_V1,
        "purpose": "META_TRAIN_OPPONENT_ROLLOUT_RESEARCH_ONLY",
        "iteration": iteration,
        "seed": seed,
        "quota": quota,
        "sources": sources,
        "previous_iteration": previous_binding,
        "outcome_ledger": outcome_binding,
        "parameters": {
            "max_opponent_weight": max_opponent_weight,
            "max_family_weight": max_family_weight,
            "min_family_quota": min_family_quota,
        },
        "entries": entries,
        "summary": {
            "selected_by_split": selected_counts,
            "nonzero_exposure_by_split": nonzero_counts,
            "teacher_behavior_eligible_count": sum(
                entry.teacher_behavior_allowed for entry in plan.entries
            ),
            "training_family_count": len(
                {entry.family for entry in plan.entries if entry.split == "META_TRAIN"}
            ),
        },
        "consumer_contract": {
            "row_fields": ["opponent_id", "weight", "quota", "reason", "lineage"],
            "longrun_split": "META_TRAIN",
            "meta_dev_training_exposure": 0,
            "meta_final_training_exposure": 0,
            "teacher_behavior_requires_separate_permission": True,
        },
        "authority": dict(_AUTHORITY),
        "curriculum_sha256": None,
    }
    manifest["curriculum_sha256"] = _manifest_digest(
        {key: value for key, value in manifest.items() if key != "curriculum_sha256"}
    )
    payload = _canonical_bytes(manifest)
    _atomic_write_new(output, payload)
    return json.loads(payload.decode("utf-8"))


def verify_dynamic_curriculum_manifest_v1(
    path: str | Path, repo_root: str | Path
) -> dict[str, object]:
    """Strictly reload sources and reproduce one curriculum manifest."""
    root = Path(repo_root).resolve()
    manifest_path = Path(path).resolve()
    manifest = _strict_json(manifest_path, canonical=True)
    if set(manifest) != _MANIFEST_KEYS or manifest.get("schema_version") != CURRICULUM_SCHEMA_V1:
        raise DynamicMetaTrainCurriculumError("curriculum manifest schema is invalid")
    supplied = manifest.get("curriculum_sha256")
    expected = _manifest_digest(
        {key: value for key, value in manifest.items() if key != "curriculum_sha256"}
    )
    if supplied != expected:
        raise DynamicMetaTrainCurriculumError("curriculum semantic SHA-256 mismatch")
    sources = manifest.get("sources")
    if type(sources) is not list or [row.get("role") for row in sources if type(row) is dict] != [
        "meta_distribution_manifest",
        "meta_schedule",
        "common24_broad_pool_config",
        "opponent_pool_manifest",
    ]:
        raise DynamicMetaTrainCurriculumError("curriculum source bindings are invalid")
    resolved: dict[str, Path] = {}
    for source in sources:
        if type(source) is not dict or set(source) != {"path", "file_sha256", "role"}:
            raise DynamicMetaTrainCurriculumError("curriculum source schema is invalid")
        source_path = _inside_root(root, str(source["path"]), "curriculum source")
        if str(source_path.relative_to(root)) != source["path"] or _sha_file(source_path) != source["file_sha256"]:
            raise DynamicMetaTrainCurriculumError("curriculum source SHA/path mismatch")
        resolved[str(source["role"])] = source_path
    previous = manifest.get("previous_iteration")
    ledger = manifest.get("outcome_ledger")
    if manifest["iteration"] == 0:
        previous_path = None
        ledger_path = None
        if previous is not None or ledger is not None:
            raise DynamicMetaTrainCurriculumError("iteration zero lineage is invalid")
    else:
        if type(previous) is not dict or type(ledger) is not dict:
            raise DynamicMetaTrainCurriculumError("updated curriculum lineage is incomplete")
        previous_path = _inside_root(root, str(previous.get("path")), "previous curriculum")
        ledger_path = _inside_root(root, str(ledger.get("path")), "outcome ledger")
        verified_previous = verify_dynamic_curriculum_manifest_v1(previous_path, root)
        if (
            _sha_file(previous_path) != previous.get("file_sha256")
            or verified_previous.get("curriculum_sha256") != previous.get("curriculum_sha256")
            or _sha_file(ledger_path) != ledger.get("file_sha256")
        ):
            raise DynamicMetaTrainCurriculumError("updated curriculum lineage SHA mismatch")
    parameters = manifest.get("parameters")
    if type(parameters) is not dict or set(parameters) != {
        "max_opponent_weight",
        "max_family_weight",
        "min_family_quota",
    }:
        raise DynamicMetaTrainCurriculumError("curriculum parameters are invalid")

    # Rebuild into a sibling temporary name, compare exact semantics, and then
    # remove it.  This reuses the same source/permission validation as the
    # builder while preserving immutable artifact writes.
    temporary = manifest_path.with_name(f".{manifest_path.name}.verify-{os.getpid()}")
    try:
        rebuilt = build_dynamic_curriculum_manifest_v1(
            repo_root=root,
            meta_manifest_path=resolved["meta_distribution_manifest"],
            meta_schedule_path=resolved["meta_schedule"],
            broad_pool_config_path=resolved["common24_broad_pool_config"],
            output_manifest_path=temporary,
            quota=int(manifest["quota"]),
            seed=str(manifest["seed"]),
            iteration=int(manifest["iteration"]),
            outcome_ledger_path=ledger_path,
            previous_manifest_path=previous_path,
            max_opponent_weight=float(parameters["max_opponent_weight"]),
            max_family_weight=float(parameters["max_family_weight"]),
            min_family_quota=int(parameters["min_family_quota"]),
        )
    finally:
        temporary.unlink(missing_ok=True)
    if rebuilt != manifest:
        raise DynamicMetaTrainCurriculumError("curriculum does not reproduce from sources")
    return manifest


__all__ = [
    "CURRICULUM_SCHEMA_V1",
    "DynamicCurriculumEntryV1",
    "DynamicCurriculumPlanV1",
    "DynamicMetaTrainCurriculumError",
    "build_dynamic_curriculum_manifest_v1",
    "build_dynamic_curriculum_plan_v1",
    "verify_dynamic_curriculum_manifest_v1",
]
