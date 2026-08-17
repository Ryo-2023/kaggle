"""Outcome-only hard-negative schedule sidecar.

This module consumes only terminal WDL, seed/seat/opponent identity, and
hash-bound source metadata.  It deliberately does not read public traces,
actions, private state, or teacher labels.  The output is a research-only
opponent sampling schedule; it is not a training dataset and grants no
execution, training, promotion, submission, or long-run authority.
"""

from __future__ import annotations

from collections import defaultdict
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping, Sequence

from mage_ptcg.meta_specialist.meta_distribution_v1 import (
    MetaDistributionError,
    load_meta_distribution_manifest_v1,
)


SCHEMA_V1 = "meta-specialist-outcome-only-hard-negative-v1"
PURPOSE_V1 = "META_TRAIN_OUTCOME_ONLY_HARD_NEGATIVE_SCHEDULE_RESEARCH_ONLY"
FORMULA_V1 = "reliability*(0.70*hard_negative+0.15*underexposure+0.15*diversity)"
AUTHORITY_FALSE_V1 = {
    "training_authority": False,
    "promotion_authority": False,
    "submission_authority": False,
    "external_execution_authority": False,
    "longrun_authority": False,
}
_MANIFEST_KEYS = frozenset(
    {
        "schema_version",
        "purpose",
        "iteration",
        "seed",
        "quota",
        "parameters",
        "sources",
        "subject_identity",
        "pool_identity",
        "entries",
        "excluded_heldout",
        "summary",
        "authority",
        "research_only",
        "schedule_sha256",
    }
)
_FORBIDDEN_SOURCE_KEY_FRAGMENTS = (
    "action",
    "teacher",
    "private",
    "trace",
    "trajectory",
    "hand",
    "prize",
    "future",
    "rng",
    "legal",
    "selected",
    "observation",
    "option",
)
_OUTCOME_SCORE = {"win": 1.0, "draw": 0.5, "loss": 0.0}
_ALLOWED_LEDGER_KEYS = frozenset(
    {
        "checkpoint_tensor_sha256",
        "deck_sha256",
        "fault_kind",
        "game_id",
        "opponent_id",
        "opponent_identity",
        "outcome",
        "policy_sha256",
        "repetition",
        "schema_version",
        "seat",
        "seed",
        "status",
        "steps",
        "terminal_reason",
        "winner",
    }
)
_ALLOWED_OPPONENT_IDENTITY_KEYS = frozenset(
    {
        "canonical_deck_sha256",
        "deck_file_sha256",
        "policy_sha256",
        "source",
        "usage_boundary",
    }
)


class OutcomeOnlyHardNegativeError(ValueError):
    """Raised when an outcome-only source or schedule is not closed."""


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
        raise OutcomeOnlyHardNegativeError(f"value is not canonical JSON: {exc}") from exc


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise OutcomeOnlyHardNegativeError(f"cannot hash source: {path}") from exc
    return digest.hexdigest()


def _semantic_sha(value: object) -> str:
    return hashlib.sha256(
        (SCHEMA_V1 + "\0").encode("ascii") + _canonical_bytes(value)
    ).hexdigest()


def _strict_json(path: Path, *, canonical: bool = False) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=lambda token: (_ for _ in ()).throw(
                OutcomeOnlyHardNegativeError(f"non-finite JSON constant: {token}")
            ),
        )
    except OutcomeOnlyHardNegativeError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise OutcomeOnlyHardNegativeError(f"invalid JSON source: {path}") from exc
    if type(value) is not dict:
        raise OutcomeOnlyHardNegativeError(f"JSON root must be an object: {path}")
    if canonical:
        expected = _canonical_bytes(value) + b"\n"
        if raw != expected:
            raise OutcomeOnlyHardNegativeError(f"JSON is not canonical: {path}")
    return value


def _reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise OutcomeOnlyHardNegativeError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _resolve_file(root: Path, value: str | Path, field: str) -> Path:
    candidate = Path(value)
    path = (root / candidate).resolve() if not candidate.is_absolute() else candidate.resolve()
    if not path.is_file():
        raise OutcomeOnlyHardNegativeError(f"{field} is not a file: {path}")
    return path


def _display_path(root: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(root))
    except ValueError:
        return str(path.resolve())


def _source_binding(root: Path, path: Path, role: str) -> dict[str, str]:
    return {"path": _display_path(root, path), "sha256": _sha256_file(path), "role": role}


def _current_evaluator_sha256() -> str:
    """Recompute the exact evaluator closure used by the existing runner."""
    try:
        from scripts.parallel_cabt_evaluator_v1 import evaluator_implementation_sha256_v1

        value = evaluator_implementation_sha256_v1()
    except (ImportError, OSError, RuntimeError) as exc:
        raise OutcomeOnlyHardNegativeError(
            f"cannot bind evaluator implementation: {exc}"
        ) from exc
    if type(value) is not str or len(value) != 64:
        raise OutcomeOnlyHardNegativeError("evaluator implementation SHA is malformed")
    return value


def _scan_forbidden_keys(value: object, *, path: str = "") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            name = str(key).lower()
            if any(fragment in name for fragment in _FORBIDDEN_SOURCE_KEY_FRAGMENTS):
                raise OutcomeOnlyHardNegativeError(
                    f"source contains forbidden action/private/teacher field: {path}.{key}"
                )
            _scan_forbidden_keys(child, path=f"{path}.{key}" if path else str(key))
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _scan_forbidden_keys(child, path=f"{path}[{index}]")


def _read_ledger(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        with path.open("rb") as handle:
            for line_number, raw in enumerate(handle, 1):
                if not raw.endswith(b"\n") or raw == b"\n":
                    raise OutcomeOnlyHardNegativeError(
                        f"ledger framing is invalid at line {line_number}"
                    )
                try:
                    value = json.loads(raw[:-1].decode("utf-8"))
                except (UnicodeError, json.JSONDecodeError) as exc:
                    raise OutcomeOnlyHardNegativeError(
                        f"ledger JSON is invalid at line {line_number}"
                    ) from exc
                if type(value) is not dict:
                    raise OutcomeOnlyHardNegativeError("ledger row must be an object")
                if set(value) != _ALLOWED_LEDGER_KEYS:
                    raise OutcomeOnlyHardNegativeError(
                        "ledger row has forbidden fields outside the closed schema"
                    )
                identity = value.get("opponent_identity")
                if type(identity) is not dict or set(identity) != _ALLOWED_OPPONENT_IDENTITY_KEYS:
                    raise OutcomeOnlyHardNegativeError(
                        "ledger opponent identity has forbidden fields outside the closed schema"
                    )
                _scan_forbidden_keys(value, path=f"ledger[{line_number}]")
                rows.append(value)
    except OutcomeOnlyHardNegativeError:
        raise
    except OSError as exc:
        raise OutcomeOnlyHardNegativeError(f"cannot read ledger: {path}") from exc
    if not rows:
        raise OutcomeOnlyHardNegativeError("ledger must not be empty")
    return rows


def _finite_unit(value: object, field: str) -> float:
    if type(value) not in (int, float) or isinstance(value, bool):
        raise OutcomeOnlyHardNegativeError(f"{field} must be numeric")
    number = float(value)
    if not math.isfinite(number) or not 0.0 <= number <= 1.0:
        raise OutcomeOnlyHardNegativeError(f"{field} must be finite in [0,1]")
    return number


def _capped_distribution(
    raw: Mapping[str, float], *, total: float, cap: float, field: str
) -> dict[str, float]:
    if not raw or not 0.0 < cap <= 1.0 or cap * len(raw) + 1e-12 < total:
        raise OutcomeOnlyHardNegativeError(f"{field} cap is infeasible")
    remaining = set(raw)
    result: dict[str, float] = {}
    mass = float(total)
    while remaining:
        denominator = sum(max(0.0, float(raw[key])) for key in remaining)
        provisional = {
            key: (mass / len(remaining) if denominator <= 0 else mass * max(0.0, float(raw[key])) / denominator)
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
    correction = total - sum(result.values())
    if abs(correction) > 1e-12:
        eligible = [key for key in result if result[key] + correction <= cap + 1e-12]
        if not eligible:
            raise OutcomeOnlyHardNegativeError(f"{field} correction exceeds cap")
        result[min(eligible, key=lambda key: (-raw[key], key))] += correction
    return {key: result[key] for key in sorted(result)}


def _allocate_counts(weights: Mapping[str, float], quota: int) -> dict[str, int]:
    total = sum(weights.values())
    if quota < 0 or total <= 0.0:
        raise OutcomeOnlyHardNegativeError("quota allocation inputs are invalid")
    raw = {key: quota * value / total for key, value in weights.items()}
    counts = {key: int(math.floor(value)) for key, value in raw.items()}
    remainder = quota - sum(counts.values())
    order = sorted(weights, key=lambda key: (-(raw[key] - counts[key]), key))
    for key in order[:remainder]:
        counts[key] += 1
    return counts


def _validate_parameters(
    *, quota: int, max_opponent_weight: float, max_family_weight: float, min_family_quota: int
) -> None:
    if type(quota) is not int or quota <= 0:
        raise OutcomeOnlyHardNegativeError("quota must be a positive integer")
    if type(min_family_quota) is not int or min_family_quota < 1:
        raise OutcomeOnlyHardNegativeError("min_family_quota must be positive")
    for value, name in (
        (max_opponent_weight, "max_opponent_weight"),
        (max_family_weight, "max_family_weight"),
    ):
        if type(value) not in (int, float) or isinstance(value, bool) or not math.isfinite(float(value)):
            raise OutcomeOnlyHardNegativeError(f"{name} must be finite")
        if not 0.0 < float(value) <= 1.0:
            raise OutcomeOnlyHardNegativeError(f"{name} must be in (0,1]")


def _manifest_sources(
    *,
    root: Path,
    ledger: Path,
    summary: Path,
    meta_manifest: Path,
    pool_manifest: Path,
    config: Path,
    checkpoint: Path,
    subject_deck: Path,
) -> dict[str, dict[str, str]]:
    return {
        "ledger": _source_binding(root, ledger, "v4_wdl_ledger"),
        "summary": _source_binding(root, summary, "v4_summary"),
        "meta_manifest": _source_binding(root, meta_manifest, "meta_distribution_manifest"),
        "pool_manifest": _source_binding(root, pool_manifest, "opponent_pool_manifest"),
        "config": _source_binding(root, config, "broad_pool_config"),
        "checkpoint": _source_binding(root, checkpoint, "subject_checkpoint"),
        "subject_deck": _source_binding(root, subject_deck, "subject_deck"),
    }


def _parse_pool(path: Path) -> dict[str, dict[str, Any]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise OutcomeOnlyHardNegativeError(f"opponent pool is invalid: {path}") from exc
    if type(raw) is not list or not raw:
        raise OutcomeOnlyHardNegativeError("opponent pool must be a non-empty list")
    result: dict[str, dict[str, Any]] = {}
    for item in raw:
        if type(item) is not dict or type(item.get("id")) is not str:
            raise OutcomeOnlyHardNegativeError("opponent pool row identity is invalid")
        identifier = str(item["id"])
        if identifier in result:
            raise OutcomeOnlyHardNegativeError("opponent pool has duplicate ids")
        for field in ("policy_hash", "canonical_deck_hash", "source", "usage_boundary"):
            if field not in item:
                raise OutcomeOnlyHardNegativeError(f"opponent pool row lacks {field}")
        result[identifier] = item
    return result


def _parse_source(
    *, root: Path, ledger_path: Path, summary_path: Path, meta_manifest_path: Path, pool_manifest_path: Path,
    repetitions_per_seat: int,
) -> tuple[dict[str, Any], list[dict[str, Any]], Any, dict[str, dict[str, Any]], dict[str, Any]]:
    summary = _strict_json(summary_path)
    if summary.get("schema_version") != "meta-specialist-v4-public-trace-meta-train-v1":
        raise OutcomeOnlyHardNegativeError("unsupported V4 summary schema")
    if summary.get("ledger_sha256") != _sha256_file(ledger_path):
        raise OutcomeOnlyHardNegativeError("summary ledger SHA-256 mismatch")
    if int(summary.get("requested_games", -1)) != int(summary.get("completed_games", -2)):
        raise OutcomeOnlyHardNegativeError("summary game completion is incomplete")
    if int(summary.get("faults", -1)) != 0 or float(summary.get("fault_rate", 1.0)) != 0.0:
        raise OutcomeOnlyHardNegativeError("hard-negative schedule requires fault-free source")
    if summary.get("native_action_labels_saved") is not False or summary.get("teacher_labels_saved") is not False:
        raise OutcomeOnlyHardNegativeError("source contains native/teacher labels")
    if summary.get("private_fields_saved") is not False:
        raise OutcomeOnlyHardNegativeError("source contains private fields")
    for authority in ("training_authority", "behavior_authority", "promotion_authority", "submission_authority", "longrun_allowed"):
        if summary.get(authority) is not False:
            raise OutcomeOnlyHardNegativeError(f"summary authority is not false: {authority}")
    if type(summary.get("base_seed")) is not int or type(summary.get("games_per_seat")) is not int:
        raise OutcomeOnlyHardNegativeError("summary seed/repetition contract is missing")
    if summary["games_per_seat"] != repetitions_per_seat:
        raise OutcomeOnlyHardNegativeError("summary games_per_seat does not match requested source")
    config_path = _resolve_file(root, str(summary.get("config_path")), "summary config_path")
    if _sha256_file(config_path) != summary.get("config_sha256"):
        raise OutcomeOnlyHardNegativeError("summary config SHA-256 mismatch")
    pool_path_from_summary = _resolve_file(root, str(summary.get("pool_manifest_path")), "summary pool_manifest_path")
    if pool_path_from_summary.resolve() != pool_manifest_path.resolve() or _sha256_file(pool_manifest_path) != summary.get("pool_manifest_sha256"):
        raise OutcomeOnlyHardNegativeError("summary pool manifest binding mismatch")
    checkpoint = summary.get("checkpoint")
    if type(checkpoint) is not dict:
        raise OutcomeOnlyHardNegativeError("summary checkpoint identity is missing")
    checkpoint_path = _resolve_file(root, str(checkpoint.get("path")), "summary checkpoint")
    if _sha256_file(checkpoint_path) != checkpoint.get("file_sha256"):
        raise OutcomeOnlyHardNegativeError("summary checkpoint SHA-256 mismatch")
    subject_deck = _resolve_file(root, str(summary.get("subject_deck_csv")), "summary subject deck")
    if _sha256_file(subject_deck) != summary.get("subject_deck_file_sha256"):
        raise OutcomeOnlyHardNegativeError("summary subject deck SHA-256 mismatch")

    try:
        meta = load_meta_distribution_manifest_v1(meta_manifest_path, verify_sources=True)
    except (MetaDistributionError, OSError, ValueError) as exc:
        raise OutcomeOnlyHardNegativeError(f"meta manifest verification failed: {exc}") from exc
    if not meta.research_only or meta.training_authority or meta.promotion_authority or meta.submission_authority:
        raise OutcomeOnlyHardNegativeError("meta manifest authority is not research-only")
    pool = _parse_pool(pool_manifest_path)
    rows = _read_ledger(ledger_path)
    expected_ids = summary.get("opponent_ids")
    if type(expected_ids) is not list or not expected_ids or len(expected_ids) != len(set(expected_ids)):
        raise OutcomeOnlyHardNegativeError("summary opponent_ids are invalid")
    if len(rows) != int(summary["completed_games"]):
        raise OutcomeOnlyHardNegativeError("ledger row count differs from summary")
    seen: set[str] = set()
    by_id = {row.opponent_id: row for row in meta.rows}
    counts: dict[str, int] = defaultdict(int)
    seat_counts: dict[str, dict[str, int]] = defaultdict(lambda: {"0": 0, "1": 0})
    observations: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for ordinal, row in enumerate(rows):
        game_id = row.get("game_id")
        opponent_id = row.get("opponent_id")
        if type(game_id) is not str or not game_id or game_id in seen:
            raise OutcomeOnlyHardNegativeError("ledger game_id is missing or duplicated")
        seen.add(game_id)
        if type(opponent_id) is not str or opponent_id not in expected_ids or opponent_id not in by_id:
            raise OutcomeOnlyHardNegativeError("ledger opponent is outside the sealed meta pool")
        if row.get("status") != "DONE" or row.get("fault_kind") is not None:
            raise OutcomeOnlyHardNegativeError("ledger contains a non-DONE/fault row")
        outcome = row.get("outcome")
        if outcome not in _OUTCOME_SCORE:
            raise OutcomeOnlyHardNegativeError("ledger outcome is not terminal WDL")
        seat = row.get("seat")
        repetition = row.get("repetition")
        seed = row.get("seed")
        if seat not in (0, 1) or type(repetition) is not int or not 0 <= repetition < repetitions_per_seat:
            raise OutcomeOnlyHardNegativeError("ledger seat/repetition is invalid")
        if type(seed) is not int or seed != int(summary["base_seed"]) + ordinal:
            raise OutcomeOnlyHardNegativeError("ledger seed schedule is not deterministic")
        identity = row.get("opponent_identity")
        if type(identity) is not dict:
            raise OutcomeOnlyHardNegativeError("ledger opponent identity is missing")
        pool_row = pool.get(opponent_id)
        meta_row = by_id[opponent_id]
        if pool_row is None:
            raise OutcomeOnlyHardNegativeError("ledger opponent is absent from pool manifest")
        if identity.get("policy_sha256") != pool_row.get("policy_hash") or identity.get("canonical_deck_sha256") != pool_row.get("canonical_deck_hash"):
            raise OutcomeOnlyHardNegativeError("ledger opponent policy/deck identity mismatch")
        if identity.get("source") != pool_row.get("source") or identity.get("usage_boundary") != pool_row.get("usage_boundary"):
            raise OutcomeOnlyHardNegativeError("ledger opponent permission identity mismatch")
        if row.get("policy_sha256") != checkpoint.get("file_sha256"):
            raise OutcomeOnlyHardNegativeError("ledger subject policy identity mismatch")
        if row.get("deck_sha256") != summary.get("subject_deck_file_sha256"):
            raise OutcomeOnlyHardNegativeError("ledger subject deck identity mismatch")
        if identity.get("deck_file_sha256") != meta_row.deck_sha256 or identity.get("policy_sha256") != meta_row.policy_sha256:
            raise OutcomeOnlyHardNegativeError("ledger identity differs from meta distribution")
        if row.get("deck_sha256") != summary.get("subject_deck_file_sha256"):
            raise OutcomeOnlyHardNegativeError("ledger subject deck SHA mismatch")
        counts[opponent_id] += 1
        seat_counts[opponent_id][str(seat)] += 1
        observations[opponent_id].append({"outcome": outcome, "seat": seat, "seed": seed, "game_id": game_id})
    if set(expected_ids) != set(counts):
        raise OutcomeOnlyHardNegativeError("summary opponent ids differ from ledger ids")
    expected_per_opponent = 2 * repetitions_per_seat
    for opponent_id in expected_ids:
        if counts[opponent_id] != expected_per_opponent or seat_counts[opponent_id] != {"0": repetitions_per_seat, "1": repetitions_per_seat}:
            raise OutcomeOnlyHardNegativeError("ledger opponent/seat support is incomplete")
    return summary, rows, meta, pool, {
        "config": config_path,
        "checkpoint": checkpoint_path,
        "subject_deck": subject_deck,
        "evaluator_sha256": _current_evaluator_sha256(),
    }


def _derive_manifest(
    *,
    root: Path,
    ledger_path: Path,
    summary_path: Path,
    meta_manifest_path: Path,
    pool_manifest_path: Path,
    quota: int,
    seed: str,
    max_opponent_weight: float,
    max_family_weight: float,
    min_family_quota: int,
    repetitions_per_seat: int,
) -> dict[str, Any]:
    _validate_parameters(
        quota=quota,
        max_opponent_weight=max_opponent_weight,
        max_family_weight=max_family_weight,
        min_family_quota=min_family_quota,
    )
    if type(seed) is not str or not seed:
        raise OutcomeOnlyHardNegativeError("seed must be a non-empty string")
    summary, rows, meta, pool, paths = _parse_source(
        root=root,
        ledger_path=ledger_path,
        summary_path=summary_path,
        meta_manifest_path=meta_manifest_path,
        pool_manifest_path=pool_manifest_path,
        repetitions_per_seat=repetitions_per_seat,
    )
    by_meta = {row.opponent_id: row for row in meta.rows}
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["opponent_id"])].append(row)
    train_ids = sorted(opponent_id for opponent_id in grouped if by_meta[opponent_id].split == "META_TRAIN")
    heldout_ids = sorted(opponent_id for opponent_id in grouped if by_meta[opponent_id].split != "META_TRAIN")
    if len(train_ids) != 20 or len(heldout_ids) != 4:
        raise OutcomeOnlyHardNegativeError("source does not contain expected 20/4 train/heldout split")
    family_members: dict[str, list[str]] = defaultdict(list)
    stats: dict[str, dict[str, Any]] = {}
    max_games = max(len(grouped[item]) for item in train_ids)
    for opponent_id in train_ids:
        meta_row = by_meta[opponent_id]
        games = grouped[opponent_id]
        wins = sum(row["outcome"] == "win" for row in games)
        draws = sum(row["outcome"] == "draw" for row in games)
        losses = sum(row["outcome"] == "loss" for row in games)
        total = wins + draws + losses
        score = (wins + 0.5 * draws) / total
        family = meta_row.archetype
        family_members[family].append(opponent_id)
        stats[opponent_id] = {
            "opponent_id": opponent_id,
            "family": family,
            "split": meta_row.split,
            "pair_id": meta_row.pair_id,
            "policy_sha256": meta_row.policy_sha256,
            "deck_sha256": meta_row.deck_sha256,
            "source_sha256": meta_row.source_sha256,
            "usage_boundary": meta_row.usage_boundary,
            "evaluation_allowed": meta_row.evaluation_allowed,
            "training_exposure_allowed": False,
            "teacher_behavior_allowed": False,
            "games": total,
            "wins": wins,
            "draws": draws,
            "losses": losses,
            "faults": 0,
            "score": score,
            "hard_negative": 1.0 - score,
            "reliability": 1.0,
            "underexposure": (max_games - total) / max_games if max_games else 0.0,
            "diversity": 1.0 / len([item for item in train_ids if by_meta[item].archetype == family]),
            "seat_games": {
                "0": sum(row["seat"] == 0 for row in games),
                "1": sum(row["seat"] == 1 for row in games),
            },
            "seat_score": {
                str(seat): (
                    sum(_OUTCOME_SCORE[row["outcome"]] for row in games if row["seat"] == seat)
                    / max(1, sum(row["seat"] == seat for row in games))
                )
                for seat in (0, 1)
            },
        }
    family_raw = {
        family: sum(
            1.0 * (
                0.70 * stats[opponent_id]["hard_negative"]
                + 0.15 * stats[opponent_id]["underexposure"]
                + 0.15 * stats[opponent_id]["diversity"]
            )
            for opponent_id in members
        )
        for family, members in family_members.items()
    }
    family_caps = {
        family: min(max_family_weight, max_opponent_weight * len(members))
        for family, members in family_members.items()
    }
    # A variable cap is needed because singleton families cannot exceed the
    # opponent cap while larger families may use the family cap.
    remaining = set(family_raw)
    family_weights = {}
    mass = 1.0
    while remaining:
        denominator = sum(max(0.0, family_raw[key]) for key in remaining)
        provisional = {
            key: (mass / len(remaining) if denominator <= 0 else mass * max(0.0, family_raw[key]) / denominator)
            for key in remaining
        }
        over = sorted(key for key, value in provisional.items() if value > family_caps[key] + 1e-15)
        if not over:
            family_weights.update(provisional)
            break
        for key in over:
            family_weights[key] = family_caps[key]
            remaining.remove(key)
            mass -= family_caps[key]
    correction = 1.0 - sum(family_weights.values())
    if abs(correction) > 1e-12:
        eligible = [key for key in family_weights if family_weights[key] + correction <= family_caps[key] + 1e-12]
        if not eligible:
            raise OutcomeOnlyHardNegativeError("family cap correction is infeasible")
        key = min(eligible, key=lambda item: (-family_raw[item], item))
        family_weights[key] += correction
    family_weights = {key: family_weights[key] for key in sorted(family_weights)}
    weights: dict[str, float] = {}
    for family, members in sorted(family_members.items()):
        member_raw = {opponent_id: stats[opponent_id]["reliability"] * (0.70 * stats[opponent_id]["hard_negative"] + 0.15 * stats[opponent_id]["underexposure"] + 0.15 * stats[opponent_id]["diversity"]) for opponent_id in members}
        weights.update(_capped_distribution(member_raw, total=family_weights[family], cap=max_opponent_weight, field=f"opponent:{family}"))
    reserved = min_family_quota * len(family_members)
    if quota < reserved:
        raise OutcomeOnlyHardNegativeError("quota cannot satisfy family floor")
    extra = _allocate_counts(family_weights, quota - reserved) if quota > reserved else {key: 0 for key in family_members}
    quotas: dict[str, int] = {opponent_id: 0 for opponent_id in train_ids}
    for family, members in sorted(family_members.items()):
        family_quota = min_family_quota + extra[family]
        quotas.update(_allocate_counts({key: weights[key] for key in members}, family_quota))
    for opponent_id in train_ids:
        stats[opponent_id].update({"family_weight": family_weights[stats[opponent_id]["family"]], "raw_score": stats[opponent_id]["reliability"] * (0.70 * stats[opponent_id]["hard_negative"] + 0.15 * stats[opponent_id]["underexposure"] + 0.15 * stats[opponent_id]["diversity"]), "weight": weights[opponent_id], "quota": quotas[opponent_id]})
    sources = _manifest_sources(
        root=root,
        ledger=ledger_path,
        summary=summary_path,
        meta_manifest=meta_manifest_path,
        pool_manifest=pool_manifest_path,
        config=paths["config"],
        checkpoint=paths["checkpoint"],
        subject_deck=paths["subject_deck"],
    )
    body: dict[str, Any] = {
        "schema_version": SCHEMA_V1,
        "purpose": PURPOSE_V1,
        "iteration": 0,
        "seed": seed,
        "quota": quota,
        "parameters": {
            "formula": FORMULA_V1,
            "opponent_cap": max_opponent_weight,
            "family_cap": max_family_weight,
            "min_family_quota": min_family_quota,
            "repetitions_per_seat": repetitions_per_seat,
            "normalization": "family_variable_cap_then_opponent_cap_largest_remainder_v1",
        },
        "sources": sources,
        "subject_identity": {
            "policy_sha256": summary["checkpoint"]["file_sha256"],
            "checkpoint_tensor_sha256": summary["checkpoint"]["tensor_state_sha256"],
            "deck_sha256": summary["subject_deck_file_sha256"],
            "evaluator_sha256": paths["evaluator_sha256"],
            "engine_seed_supported": summary.get("engine_seed_supported"),
        },
        "pool_identity": {
            "opponent_ids": sorted(summary["opponent_ids"]),
            "pool_manifest_sha256": _sha256_file(pool_manifest_path),
            "source_usage_boundary": "local_eval_only",
        },
        "entries": [stats[opponent_id] for opponent_id in train_ids],
        "excluded_heldout": [
            {
                "opponent_id": opponent_id,
                "split": by_meta[opponent_id].split,
                "games": len(grouped[opponent_id]),
                "weight": 0.0,
                "quota": 0,
                "reason": "HELDOUT_SPLIT_EXCLUDED_FROM_OUTCOME_SCHEDULE",
            }
            for opponent_id in heldout_ids
        ],
        "summary": {
            "source_games": len(rows),
            "included_games": sum(stats[item]["games"] for item in train_ids),
            "excluded_games": sum(len(grouped[item]) for item in heldout_ids),
            "included_opponents": len(train_ids),
            "excluded_opponents": len(heldout_ids),
            "faults": 0,
            "weights_sum": sum(weights.values()),
            "quota_sum": sum(quotas.values()),
            "family_count": len(family_members),
            "source_score_rate": (summary["wins"] + 0.5 * summary["draws"]) / summary["score_denominator_games"],
            "action_trace_used": False,
            "teacher_labels_used": False,
            "private_fields_used": False,
            "training_data": False,
            "evaluation_schedule_only": True,
        },
        "authority": dict(AUTHORITY_FALSE_V1),
        "research_only": True,
    }
    if abs(sum(float(row["weight"]) for row in body["entries"]) - 1.0) > 1e-9 or sum(int(row["quota"]) for row in body["entries"]) != quota:
        raise OutcomeOnlyHardNegativeError("schedule mass or quota did not close")
    body["schedule_sha256"] = _semantic_sha(body)
    return body


def _atomic_write_new(path: Path, payload: bytes) -> None:
    if path.exists():
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.tmp-", dir=path.parent)
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with temporary.open("wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            raise
        finally:
            temporary.unlink(missing_ok=True)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def build_outcome_only_hard_negative_schedule_v1(
    *,
    repo_root: Path | str,
    ledger_path: Path | str,
    summary_path: Path | str,
    meta_manifest_path: Path | str,
    pool_manifest_path: Path | str,
    output_manifest_path: Path | str,
    quota: int = 96,
    seed: str = "outcome-only-v1",
    max_opponent_weight: float = 0.35,
    max_family_weight: float = 0.55,
    min_family_quota: int = 1,
    repetitions_per_seat: int = 2,
) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    if not root.is_dir():
        raise OutcomeOnlyHardNegativeError("repo_root must be a directory")
    ledger = _resolve_file(root, ledger_path, "ledger_path")
    summary = _resolve_file(root, summary_path, "summary_path")
    meta_manifest = _resolve_file(root, meta_manifest_path, "meta_manifest_path")
    pool_manifest = _resolve_file(root, pool_manifest_path, "pool_manifest_path")
    output = Path(output_manifest_path).resolve()
    if output.exists():
        raise FileExistsError(output)
    payload = _derive_manifest(
        root=root,
        ledger_path=ledger,
        summary_path=summary,
        meta_manifest_path=meta_manifest,
        pool_manifest_path=pool_manifest,
        quota=quota,
        seed=seed,
        max_opponent_weight=max_opponent_weight,
        max_family_weight=max_family_weight,
        min_family_quota=min_family_quota,
        repetitions_per_seat=repetitions_per_seat,
    )
    raw = _canonical_bytes(payload) + b"\n"
    _atomic_write_new(output, raw)
    return json.loads(raw.decode("utf-8"))


def verify_outcome_only_hard_negative_schedule_v1(
    path: Path | str, repo_root: Path | str
) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    manifest_path = Path(path).resolve()
    manifest = _strict_json(manifest_path, canonical=True)
    if set(manifest) != _MANIFEST_KEYS or manifest.get("schema_version") != SCHEMA_V1:
        raise OutcomeOnlyHardNegativeError("schedule manifest schema is invalid")
    if manifest.get("purpose") != PURPOSE_V1 or manifest.get("research_only") is not True:
        raise OutcomeOnlyHardNegativeError("schedule purpose/research_only contract is invalid")
    if manifest.get("authority") != AUTHORITY_FALSE_V1:
        raise OutcomeOnlyHardNegativeError("schedule authority must remain false")
    supplied = manifest.get("schedule_sha256")
    expected = _semantic_sha({key: value for key, value in manifest.items() if key != "schedule_sha256"})
    if supplied != expected:
        raise OutcomeOnlyHardNegativeError("schedule semantic SHA-256 mismatch")
    sources = manifest.get("sources")
    if type(sources) is not dict or set(sources) != {"ledger", "summary", "meta_manifest", "pool_manifest", "config", "checkpoint", "subject_deck"}:
        raise OutcomeOnlyHardNegativeError("schedule source bindings are invalid")
    resolved: dict[str, Path] = {}
    for role, binding in sources.items():
        if type(binding) is not dict or set(binding) != {"path", "sha256", "role"}:
            raise OutcomeOnlyHardNegativeError("schedule source binding is malformed")
        bound_value = Path(str(binding["path"]))
        if bound_value.is_absolute():
            raise OutcomeOnlyHardNegativeError(f"source path escapes repo_root: {role}")
        source = _resolve_file(root, bound_value, f"source:{role}")
        try:
            source.relative_to(root)
        except ValueError as exc:
            raise OutcomeOnlyHardNegativeError(f"source path escapes repo_root: {role}") from exc
        if _sha256_file(source) != binding["sha256"]:
            raise OutcomeOnlyHardNegativeError(f"source SHA mismatch: {role}")
        resolved[role] = source
    parameters = manifest.get("parameters")
    if type(parameters) is not dict:
        raise OutcomeOnlyHardNegativeError("schedule parameters are missing")
    rebuilt = _derive_manifest(
        root=root,
        ledger_path=resolved["ledger"],
        summary_path=resolved["summary"],
        meta_manifest_path=resolved["meta_manifest"],
        pool_manifest_path=resolved["pool_manifest"],
        quota=int(manifest["quota"]),
        seed=str(manifest["seed"]),
        max_opponent_weight=float(parameters["opponent_cap"]),
        max_family_weight=float(parameters["family_cap"]),
        min_family_quota=int(parameters["min_family_quota"]),
        repetitions_per_seat=int(parameters["repetitions_per_seat"]),
    )
    if rebuilt != manifest:
        raise OutcomeOnlyHardNegativeError("schedule does not reproduce from bound sources")
    return manifest


__all__ = [
    "AUTHORITY_FALSE_V1",
    "FORMULA_V1",
    "OutcomeOnlyHardNegativeError",
    "SCHEMA_V1",
    "build_outcome_only_hard_negative_schedule_v1",
    "verify_outcome_only_hard_negative_schedule_v1",
]
