"""Build a TRAIN-only difficulty-calibrated heterogeneous meta pool.

This module operates at pool level: each selected candidate keeps its original
deck and policy bytes, while the output pool mixes several sealed source
families.  The calibration ledger is used only to choose a balanced research
panel; it never grants training, promotion, submission, or long-run authority.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import shutil
from typing import Any, Mapping, Sequence

from mage_ptcg.meta_specialist.cg_bestknown_loop_v1 import FRESH_META_SCHEMA_V1
from mage_ptcg.observability.cabt_trace import canonical_deck_sha256
from mage_ptcg.opponent_ingest.kaggle_kernel_meta_v1 import LOCAL_EVAL_ONLY_V1
from scripts.parallel_cabt_evaluator_v1 import evaluation_implementation_sha256_v1


CALIBRATED_META_POOL_SCHEMA_V1 = "meta-specialist-cg-calibrated-pool-v1"
CALIBRATED_SOURCE_V1 = "internal_calibrated_heterogeneous_panel"
CALIBRATED_RECIPE_V1 = "TRAIN_ONLY_DIFFICULTY_CALIBRATED_HETEROGENEOUS_POOL_V1"
_SHA_CHARS = frozenset("0123456789abcdef")


class CalibratedMetaPoolError(ValueError):
    """Raised when a calibration source cannot be sealed fail-closed."""


def _canonical_json(value: object) -> bytes:
    try:
        return (
            json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise CalibratedMetaPoolError("payload is not canonical JSON") from exc


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise CalibratedMetaPoolError(f"regular file required: {path}")
    return _sha256_bytes(path.read_bytes())


def _sha_value(value: object, name: str) -> str:
    if type(value) is not str or len(value) != 64 or any(char not in _SHA_CHARS for char in value):
        raise CalibratedMetaPoolError(f"{name} must be a lowercase SHA-256")
    return value


def _write_new(path: Path, value: object) -> None:
    if path.exists():
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(_canonical_json(value))
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _read_json(path: Path, label: str) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CalibratedMetaPoolError(f"{label} is unreadable: {path}") from exc


def _text(value: object, name: str) -> str:
    if type(value) is not str or not value.strip():
        raise CalibratedMetaPoolError(f"{name} must be a non-empty string")
    return value


def _parse_deck(path: Path, candidate_id: str) -> tuple[list[int], str]:
    if path.is_symlink() or not path.is_file():
        raise CalibratedMetaPoolError(f"{candidate_id}: deck.csv must be a regular file")
    try:
        cards = [int(token) for token in path.read_text(encoding="utf-8").split()]
    except (OSError, UnicodeError, ValueError) as exc:
        raise CalibratedMetaPoolError(f"{candidate_id}: deck.csv is not an integer list") from exc
    if len(cards) != 60:
        raise CalibratedMetaPoolError(f"{candidate_id}: deck.csv must contain exactly 60 cards")
    return cards, canonical_deck_sha256(cards)


def _source_family(row: Mapping[str, object]) -> str:
    explicit = row.get("source_family")
    if type(explicit) is str and explicit.strip():
        return explicit.strip()
    recipe = row.get("derivation_recipe")
    if type(recipe) is str and recipe.strip():
        return recipe.split(":", 1)[0]
    return f"{row.get('source', 'unknown')}:{row.get('canonical_deck_hash', 'unknown')}"


@dataclass(slots=True)
class _Candidate:
    candidate_id: str
    root: Path
    row: dict[str, object]
    family: str
    source_manifest_sha256: str
    deck_hash: str
    policy_sha256: str


@dataclass(slots=True)
class _Stats:
    wins: int = 0
    losses: int = 0
    draws: int = 0
    faults: int = 0
    seats: set[int] = field(default_factory=set)
    seat_counts: dict[int, int] = field(default_factory=lambda: {0: 0, 1: 0})

    @property
    def games(self) -> int:
        return self.wins + self.losses + self.draws

    @property
    def score(self) -> float:
        return self.wins / self.games if self.games else 0.0

    def as_dict(self) -> dict[str, object]:
        return {
            "wins": self.wins,
            "losses": self.losses,
            "draws": self.draws,
            "faults": self.faults,
            "games": self.games,
            "score": self.score,
            "seats": sorted(self.seats),
            "seat_counts": {str(key): value for key, value in sorted(self.seat_counts.items())},
        }


def _pool_rows(root: Path) -> tuple[list[dict[str, object]], str]:
    manifest = root / "pool_manifest.json"
    if not manifest.is_file():
        raise CalibratedMetaPoolError(f"source pool manifest is missing: {manifest}")
    raw = _read_json(manifest, "pool_manifest.json")
    rows: object = raw
    if isinstance(raw, Mapping):
        rows = raw.get("opponents", raw)
    if isinstance(rows, Mapping):
        rows = list(rows.values())
    if not isinstance(rows, list) or not rows:
        raise CalibratedMetaPoolError(f"source pool must contain a non-empty list: {manifest}")
    normalized: list[dict[str, object]] = []
    for item in rows:
        if not isinstance(item, Mapping):
            raise CalibratedMetaPoolError(f"source pool row is not an object: {manifest}")
        normalized.append(dict(item))
    return normalized, _sha256_file(manifest)


def _load_candidates(source_roots: Sequence[Path | str], consumed_ids: set[str], consumed_policy: set[str]) -> tuple[dict[str, _Candidate], dict[str, list[str]]]:
    if not source_roots:
        raise CalibratedMetaPoolError("at least one source root is required")
    candidates: dict[str, _Candidate] = {}
    rejected: dict[str, list[str]] = {}
    identity: dict[tuple[str, str], str] = {}
    for source_root_value in source_roots:
        source_root = Path(source_root_value).resolve()
        if not source_root.is_dir():
            raise CalibratedMetaPoolError(f"source root is not a directory: {source_root}")
        rows, manifest_sha = _pool_rows(source_root)
        for row in rows:
            candidate_id = _text(row.get("id"), "candidate id")
            reasons: list[str] = []
            candidate_dir = source_root / candidate_id
            main_path = candidate_dir / "main.py"
            deck_path = candidate_dir / "deck.csv"
            if candidate_id in candidates:
                raise CalibratedMetaPoolError(f"duplicate candidate id across source roots: {candidate_id}")
            if candidate_id in consumed_ids:
                reasons.append("candidate_id_consumed")
            if row.get("smoke_ok") is not True:
                reasons.append("source_not_smoke_qualified")
            if row.get("usage_boundary") != LOCAL_EVAL_ONLY_V1:
                reasons.append("source_crosses_local_eval_boundary")
            if main_path.is_symlink() or not main_path.is_file() or deck_path.is_symlink() or not deck_path.is_file():
                reasons.append("candidate_assets_missing_or_symlinked")
            if reasons:
                rejected[candidate_id] = sorted(set(reasons))
                continue
            policy_sha = _sha256_file(main_path)
            if row.get("policy_hash") != policy_sha:
                raise CalibratedMetaPoolError(f"{candidate_id}: policy_hash mismatch")
            if policy_sha in consumed_policy:
                rejected[candidate_id] = ["policy_identity_consumed"]
                continue
            _cards, deck_hash = _parse_deck(deck_path, candidate_id)
            if row.get("canonical_deck_hash") != deck_hash:
                raise CalibratedMetaPoolError(f"{candidate_id}: canonical deck hash mismatch")
            pair = (policy_sha, deck_hash)
            previous = identity.get(pair)
            if previous is not None:
                raise CalibratedMetaPoolError(f"duplicate policy+deck identity: {previous} and {candidate_id}")
            identity[pair] = candidate_id
            candidates[candidate_id] = _Candidate(
                candidate_id=candidate_id,
                root=source_root,
                row=row,
                family=_source_family(row),
                source_manifest_sha256=manifest_sha,
                deck_hash=deck_hash,
                policy_sha256=policy_sha,
            )
    if not candidates:
        raise CalibratedMetaPoolError("no unused, smoke-qualified candidates remain")
    return candidates, rejected


def _read_calibration_ledger(path: Path, candidates: Mapping[str, _Candidate], stats: dict[str, _Stats]) -> None:
    if path.is_symlink() or not path.is_file():
        raise CalibratedMetaPoolError(f"calibration ledger must be a regular file: {path}")
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise CalibratedMetaPoolError(f"calibration ledger is unreadable: {path}") from exc
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise CalibratedMetaPoolError(f"calibration ledger JSON error at {path}:{line_number}") from exc
        if not isinstance(raw, Mapping):
            raise CalibratedMetaPoolError(f"calibration ledger row is not an object at {path}:{line_number}")
        metadata = raw.get("metadata") if isinstance(raw.get("metadata"), Mapping) else {}
        scope = raw.get("split") or metadata.get("calibration_scope") or metadata.get("split")
        if scope in {"META_DEV", "META_FINAL", "DEV", "FINAL"}:
            raise CalibratedMetaPoolError("TRAIN-only calibration ledger contains holdout rows")
        if scope is not None and scope not in {"META_TRAIN", "TRAIN", "TRAIN_ONLY"}:
            raise CalibratedMetaPoolError(f"calibration ledger has unsupported scope: {scope}")
        candidate_id = raw.get("opponent_id")
        if type(candidate_id) is not str or candidate_id not in candidates:
            raise CalibratedMetaPoolError(f"calibration ledger references unknown candidate: {candidate_id!r}")
        seat = raw.get("seat")
        if type(seat) is not int or seat not in {0, 1}:
            raise CalibratedMetaPoolError(f"calibration ledger seat must be 0 or 1: {candidate_id}")
        current = stats.setdefault(candidate_id, _Stats())
        current.seats.add(seat)
        current.seat_counts[seat] += 1
        outcome = raw.get("outcome")
        if raw.get("status") == "DONE" and raw.get("raw_status") == "DONE" and raw.get("fault_detail") in (None, "") and outcome in {"win", "loss", "draw"}:
            if outcome == "win":
                current.wins += 1
            elif outcome == "loss":
                current.losses += 1
            else:
                current.draws += 1
        else:
            current.faults += 1


def _select_candidates(
    candidates: Mapping[str, _Candidate],
    stats: Mapping[str, _Stats],
    rejected: dict[str, list[str]],
    *,
    target_score: float,
    score_floor: float,
    score_ceiling: float,
    requested_count: int,
    min_families: int,
    family_cap: int,
    min_games_per_candidate: int,
) -> list[_Candidate]:
    eligible: list[tuple[tuple[float, int, str, str], _Candidate]] = []
    for candidate_id, candidate in candidates.items():
        current = stats.get(candidate_id)
        if current is None:
            rejected[candidate_id] = ["missing_train_calibration"]
            continue
        reasons: list[str] = []
        if current.faults:
            reasons.append("calibration_fault")
        if current.games < min_games_per_candidate:
            reasons.append("insufficient_calibration_games")
        if current.seats != {0, 1}:
            reasons.append("missing_seat_support")
        if current.score < score_floor or current.score > score_ceiling:
            reasons.append("outside_target_difficulty_band")
        if reasons:
            rejected[candidate_id] = sorted(set(reasons))
            continue
        key = (abs(current.score - target_score), -current.games, candidate.family, candidate_id)
        eligible.append((key, candidate))
    if requested_count <= 0 or min_families <= 0 or family_cap <= 0 or min_games_per_candidate <= 0:
        raise CalibratedMetaPoolError("selection limits must be positive")
    if requested_count < min_families:
        raise CalibratedMetaPoolError("requested_count must be at least min_families")
    families: dict[str, list[tuple[tuple[float, int, str, str], _Candidate]]] = defaultdict(list)
    for item in eligible:
        families[item[1].family].append(item)
    if len(families) < min_families:
        raise CalibratedMetaPoolError(f"only {len(families)} eligible source families remain; need {min_families}")
    for values in families.values():
        values.sort(key=lambda item: item[0])
    family_order = sorted(families, key=lambda family: families[family][0][0])
    selected: list[_Candidate] = []
    family_counts: dict[str, int] = defaultdict(int)
    selected_ids: set[str] = set()
    for family in family_order[:min_families]:
        candidate = families[family][0][1]
        selected.append(candidate)
        selected_ids.add(candidate.candidate_id)
        family_counts[family] += 1
    for _key, candidate in sorted(eligible, key=lambda item: item[0]):
        if len(selected) >= requested_count:
            break
        if candidate.candidate_id in selected_ids or family_counts[candidate.family] >= family_cap:
            continue
        selected.append(candidate)
        selected_ids.add(candidate.candidate_id)
        family_counts[candidate.family] += 1
    if len(selected) < requested_count:
        raise CalibratedMetaPoolError(f"only {len(selected)} candidates satisfy family cap {family_cap}; need {requested_count}")
    selected.sort(key=lambda candidate: candidate.candidate_id)
    return selected


def _copy_tree_no_symlink(source: Path, destination: Path) -> None:
    if destination.exists():
        raise FileExistsError(destination)
    if source.is_symlink() or not source.is_dir():
        raise CalibratedMetaPoolError(f"candidate directory must be a regular directory: {source}")
    destination.mkdir(parents=True, exist_ok=False)
    for child in sorted(source.iterdir(), key=lambda path: path.name):
        target = destination / child.name
        if child.is_symlink():
            raise CalibratedMetaPoolError(f"candidate asset symlink is forbidden: {child}")
        if child.is_dir():
            _copy_tree_no_symlink(child, target)
        elif child.is_file():
            shutil.copy2(child, target)
        else:
            raise CalibratedMetaPoolError(f"unsupported candidate asset: {child}")


def _relative(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path.resolve())


def build_calibrated_meta_pool_v1(
    *,
    source_roots: Sequence[Path | str],
    calibration_ledger_paths: Sequence[Path | str],
    output_root: Path | str,
    p1_package: Path | str,
    source_epoch: str,
    seed_namespace: str,
    target_score: float = 0.15,
    score_floor: float = 0.02,
    score_ceiling: float = 0.35,
    requested_count: int = 12,
    min_families: int = 3,
    family_cap: int = 4,
    min_games_per_candidate: int = 2,
    consumed_ids: Sequence[str] = (),
    consumed_policy_sha256: Sequence[str] = (),
) -> dict[str, object]:
    """Materialize a fresh calibrated pool from unused sealed source roots."""

    if not str(source_epoch).strip() or not str(seed_namespace).strip():
        raise CalibratedMetaPoolError("source_epoch and seed_namespace must be non-empty")
    if not calibration_ledger_paths:
        raise CalibratedMetaPoolError("at least one calibration ledger is required")
    if not 0.0 <= score_floor <= score_ceiling <= 1.0:
        raise CalibratedMetaPoolError("score band must satisfy 0 <= floor <= ceiling <= 1")
    if not 0.0 <= target_score <= 1.0:
        raise CalibratedMetaPoolError("target_score must be between 0 and 1")
    output = Path(output_root).resolve()
    if output.exists():
        raise FileExistsError(output)
    consumed_id_set = {str(value) for value in consumed_ids}
    if len(consumed_id_set) != len(tuple(consumed_ids)):
        raise CalibratedMetaPoolError("consumed_ids must be unique")
    consumed_policy_set = {_sha_value(value, "consumed_policy_sha256") for value in consumed_policy_sha256}
    if len(consumed_policy_set) != len(tuple(consumed_policy_sha256)):
        raise CalibratedMetaPoolError("consumed_policy_sha256 must be unique")
    candidates, rejected = _load_candidates(source_roots, consumed_id_set, consumed_policy_set)
    stats: dict[str, _Stats] = {}
    for ledger_value in calibration_ledger_paths:
        _read_calibration_ledger(Path(ledger_value).resolve(), candidates, stats)
    selected = _select_candidates(
        candidates,
        stats,
        rejected,
        target_score=float(target_score),
        score_floor=float(score_floor),
        score_ceiling=float(score_ceiling),
        requested_count=int(requested_count),
        min_families=int(min_families),
        family_cap=int(family_cap),
        min_games_per_candidate=int(min_games_per_candidate),
    )
    p1_root = Path(p1_package).resolve()
    p1_main, p1_deck = p1_root / "main.py", p1_root / "deck.csv"
    if p1_main.is_symlink() or not p1_main.is_file() or p1_deck.is_symlink() or not p1_deck.is_file():
        raise CalibratedMetaPoolError("P1 package must contain regular main.py and deck.csv")
    output.mkdir(parents=True, exist_ok=False)
    evidence_dir = output / "evidence"
    rows: list[dict[str, object]] = []
    references: list[dict[str, object]] = []
    meta_rows: list[dict[str, object]] = []
    selected_families: list[str] = []
    for candidate in selected:
        target = output / candidate.candidate_id
        _copy_tree_no_symlink(candidate.root / candidate.candidate_id, target)
        policy_path = target / "main.py"
        deck_path = target / "deck.csv"
        policy_sha = _sha256_file(policy_path)
        _cards, deck_hash = _parse_deck(deck_path, candidate.candidate_id)
        if policy_sha != candidate.policy_sha256 or deck_hash != candidate.deck_hash:
            raise CalibratedMetaPoolError(f"copied candidate identity changed: {candidate.candidate_id}")
        current = stats[candidate.candidate_id]
        source_sha = _sha256_bytes(
            _canonical_json(
                {
                    "candidate_id": candidate.candidate_id,
                    "family": candidate.family,
                    "origin_source": candidate.row.get("source"),
                    "origin_pool_manifest_sha256": candidate.source_manifest_sha256,
                    "policy_sha256": policy_sha,
                    "canonical_deck_hash": deck_hash,
                    "calibration": current.as_dict(),
                }
            )
        )
        evidence = {
            "schema_version": CALIBRATED_META_POOL_SCHEMA_V1,
            "candidate_id": candidate.candidate_id,
            "fresh": True,
            "unused_before_run": True,
            "source": CALIBRATED_SOURCE_V1,
            "derivation_recipe": CALIBRATED_RECIPE_V1,
            "source_family": candidate.family,
            "origin_source": candidate.row.get("source"),
            "origin_root": str(candidate.root),
            "origin_pool_manifest_sha256": candidate.source_manifest_sha256,
            "policy_sha256": policy_sha,
            "canonical_deck_hash": deck_hash,
            "source_sha256": source_sha,
            "calibration_scope": "META_TRAIN",
            "calibration": current.as_dict(),
            "private_fields_used": [],
            "usage_boundary": LOCAL_EVAL_ONLY_V1,
            "runtime_smoke_required": True,
            "authority": {"training_allowed": False, "promotion_allowed": False, "submission_allowed": False, "longrun_allowed": False},
        }
        evidence_path = evidence_dir / f"{candidate.candidate_id}.json"
        _write_new(evidence_path, evidence)
        row = {
            "id": candidate.candidate_id,
            "canonical_deck_hash": deck_hash,
            "policy_hash": policy_sha,
            "source_policy_sha256": candidate.row.get("source_policy_sha256", policy_sha),
            "source": CALIBRATED_SOURCE_V1,
            "origin_source": candidate.row.get("source"),
            "origin_pool_manifest_sha256": candidate.source_manifest_sha256,
            "source_family": candidate.family,
            "derivation_recipe": CALIBRATED_RECIPE_V1,
            "calibration_score": current.score,
            "calibration_games": current.games,
            "calibration_seat_counts": {str(key): value for key, value in sorted(current.seat_counts.items())},
            "smoke_ok": False,
            "runtime_smoke_required": True,
            "usage_boundary": LOCAL_EVAL_ONLY_V1,
            "asset_preflight": "STATIC_AND_EXACT_60",
            "mean_decision_ms": candidate.row.get("mean_decision_ms"),
        }
        rows.append(row)
        references.append(
            {
                "id": candidate.candidate_id,
                "fresh": True,
                "unused_before_run": True,
                "freshness_evidence_sha256": _sha256_file(evidence_path),
                "freshness_evidence_path": str(Path("evidence") / evidence_path.name),
                "policy_sha256": policy_sha,
                "canonical_deck_hash": deck_hash,
                "source": CALIBRATED_SOURCE_V1,
                "source_sha256": source_sha,
            }
        )
        meta_rows.append(
            {
                "opponent_id": candidate.candidate_id,
                "archetype": f"Calibrated:{candidate.family}",
                "deck_sha256": deck_hash,
                "policy_sha256": policy_sha,
                "source_sha256": source_sha,
                "weight": 1.0,
                "usage_boundary": LOCAL_EVAL_ONLY_V1,
                "training_exposure": 0,
                "source": CALIBRATED_SOURCE_V1,
                "source_family": candidate.family,
                "derivation_recipe": CALIBRATED_RECIPE_V1,
                "calibration_score": current.score,
            }
        )
        selected_families.append(candidate.family)
    rows.sort(key=lambda item: str(item["id"]))
    references.sort(key=lambda item: str(item["id"]))
    meta_rows.sort(key=lambda item: str(item["opponent_id"]))
    pool_path = output / "pool_manifest.json"
    _write_new(pool_path, rows)
    pool_sha = _sha256_file(pool_path)
    reference_ids = [str(row["id"]) for row in rows]
    seed_plan_sha = _sha256_bytes(_canonical_json({"source_epoch": source_epoch, "seed_namespace": seed_namespace, "reference_ids": reference_ids}))
    fresh_path = output / "fresh_meta.json"
    _write_new(
        fresh_path,
        {
            "schema_version": FRESH_META_SCHEMA_V1,
            "batch_id": f"calibrated-{source_epoch}-{seed_namespace}",
            "source_epoch": source_epoch,
            "seed_namespace": seed_namespace,
            "seed_plan_sha256": seed_plan_sha,
            "pool_manifest_sha256": pool_sha,
            "reference_ids": reference_ids,
            "references": references,
            "freshness_basis": "new TRAIN-only calibrated pool from unused smoke-qualified source identities; runtime smoke pending",
            "calibration": {"scope": "META_TRAIN", "target_score": target_score, "score_floor": score_floor, "score_ceiling": score_ceiling, "requested_count": requested_count, "min_families": min_families, "family_cap": family_cap, "min_games_per_candidate": min_games_per_candidate},
            "authority": {"training_allowed": False, "promotion_allowed": False, "submission_allowed": False, "longrun_allowed": False},
            "research_only": True,
        },
    )
    meta_path = output / "meta_manifest.json"
    _write_new(meta_path, {"schema_version": "cg-calibrated-meta-distribution-v1", "research_only": True, "source_kind": CALIBRATED_SOURCE_V1, "rows": meta_rows})
    ids = reference_ids
    if len(ids) < 3:
        raise CalibratedMetaPoolError("at least three selected candidates are required for train/dev/final separation")
    meta_by_id = {str(row["opponent_id"]): row for row in meta_rows}

    def split_row(candidate_id: str) -> dict[str, object]:
        item = meta_by_id[candidate_id]
        return {key: item[key] for key in ("opponent_id", "archetype", "deck_sha256", "policy_sha256", "source_sha256", "weight", "usage_boundary", "training_exposure")}

    split_path = output / "cg_historical_split.json"
    _write_new(
        split_path,
        {
            "schema_version": "cg-weekend-meta-splits-v1",
            "research_only": True,
            "candidate_exclusion_ids": [],
            "bindings": {"p1_policy_sha256": _sha256_file(p1_main), "p1_deck_sha256": _sha256_file(p1_deck), "meta_manifest_sha256": _sha256_file(meta_path), "pool_manifest_sha256": pool_sha, "evaluator_sha256": evaluation_implementation_sha256_v1()},
            "sources": {"meta_manifest_path": _relative(meta_path, output), "pool_manifest_path": _relative(pool_path, output)},
            "evaluation_contract": {"both_seats": True, "fault_inclusive": True, "training_exposure": 0, "teacher_labels_saved": False, "final_results_read_during_search": False},
            "train_blocks": [ids[:-2]],
            "splits": {"META_TRAIN": [split_row(item) for item in ids[:-2]], "META_DEV": [split_row(ids[-2])], "META_FINAL": [split_row(ids[-1])]},
            "notes": ["TRAIN-only baseline calibration selected this pool; DEV and FINAL were not read during selection.", "Every selected candidate keeps its original deck and policy bytes; heterogeneity is pool-level only.", "Runtime smoke promotion is required before CEM."],
        },
    )
    report = {
        "schema_version": CALIBRATED_META_POOL_SCHEMA_V1,
        "status": "SEALED",
        "source_epoch": source_epoch,
        "seed_namespace": seed_namespace,
        "recipe": CALIBRATED_RECIPE_V1,
        "source_kind": CALIBRATED_SOURCE_V1,
        "selected_count": len(rows),
        "selected_ids": reference_ids,
        "selected_families": sorted(set(selected_families)),
        "rejected": rejected,
        "pool_manifest_path": str(pool_path),
        "pool_manifest_sha256": pool_sha,
        "fresh_meta_path": str(fresh_path),
        "fresh_meta_sha256": _sha256_file(fresh_path),
        "meta_manifest_path": str(meta_path),
        "meta_manifest_sha256": _sha256_file(meta_path),
        "split_path": str(split_path),
        "split_sha256": _sha256_file(split_path),
        "calibration_ledger_paths": [str(Path(value).resolve()) for value in calibration_ledger_paths],
        "calibration_scope": "META_TRAIN",
        "authority": {"training_allowed": False, "promotion_allowed": False, "submission_allowed": False, "longrun_allowed": False},
        "research_only": True,
        "runtime_smoke_required": True,
    }
    _write_new(output / "intake_report.json", report)
    return report


def build_calibrated_meta_split_v1(*, output_root: Path | str, p1_package: Path | str) -> dict[str, object]:
    """Rebind the calibrated META split after the runtime smoke promotion."""

    root = Path(output_root).resolve()
    pool_path = root / "pool_manifest.json"
    fresh_path = root / "fresh_meta.json"
    if not pool_path.is_file() or not fresh_path.is_file():
        raise CalibratedMetaPoolError("promoted calibrated root must contain pool_manifest.json and fresh_meta.json")
    raw_pool = _read_json(pool_path, "pool_manifest.json")
    if not isinstance(raw_pool, list) or not raw_pool or any(not isinstance(row, Mapping) for row in raw_pool):
        raise CalibratedMetaPoolError("promoted pool manifest must contain rows")
    rows = [dict(row) for row in raw_pool]
    if any(row.get("smoke_ok") is not True for row in rows):
        raise CalibratedMetaPoolError("calibrated split can be rebound only after smoke promotion")
    fresh = _read_json(fresh_path, "fresh_meta.json")
    if not isinstance(fresh, Mapping) or not isinstance(fresh.get("references"), list):
        raise CalibratedMetaPoolError("fresh_meta.references must be a list")
    ids = sorted(str(row.get("id")) for row in rows)
    refs = {str(item.get("id")): item for item in fresh["references"] if isinstance(item, Mapping)}
    if set(refs) != set(ids):
        raise CalibratedMetaPoolError("fresh_meta references do not cover promoted pool")
    pool_sha = _sha256_file(pool_path)
    if fresh.get("pool_manifest_sha256") != pool_sha:
        raise CalibratedMetaPoolError("fresh_meta pool manifest SHA mismatch")
    meta_path = root / "meta_manifest.json"
    split_path = root / "cg_historical_split.json"
    report_path = root / "split_report.json"
    if meta_path.exists() or split_path.exists() or report_path.exists():
        raise FileExistsError("calibrated split artifacts already exist")
    meta_rows: list[dict[str, object]] = []
    for row in rows:
        candidate_id = str(row["id"])
        ref = refs[candidate_id]
        source_sha = _sha_value(ref.get("source_sha256"), f"{candidate_id}.source_sha256")
        meta_rows.append(
            {
                "opponent_id": candidate_id,
                "archetype": f"Calibrated:{row.get('source_family', 'unknown')}",
                "deck_sha256": str(row["canonical_deck_hash"]),
                "policy_sha256": str(row["policy_hash"]),
                "source_sha256": source_sha,
                "weight": 1.0,
                "usage_boundary": LOCAL_EVAL_ONLY_V1,
                "training_exposure": 0,
                "source": CALIBRATED_SOURCE_V1,
                "source_family": row.get("source_family"),
                "derivation_recipe": CALIBRATED_RECIPE_V1,
                "calibration_score": row.get("calibration_score"),
            }
        )
    _write_new(meta_path, {"schema_version": "cg-calibrated-meta-distribution-v1", "research_only": True, "source_kind": CALIBRATED_SOURCE_V1, "rows": meta_rows})
    p1_root = Path(p1_package).resolve()
    p1_main, p1_deck = p1_root / "main.py", p1_root / "deck.csv"
    if p1_main.is_symlink() or not p1_main.is_file() or p1_deck.is_symlink() or not p1_deck.is_file():
        raise CalibratedMetaPoolError("P1 package must contain regular main.py and deck.csv")
    by_id = {str(row["opponent_id"]): row for row in meta_rows}

    def split_row(candidate_id: str) -> dict[str, object]:
        item = by_id[candidate_id]
        return {key: item[key] for key in ("opponent_id", "archetype", "deck_sha256", "policy_sha256", "source_sha256", "weight", "usage_boundary", "training_exposure")}

    if len(ids) < 3:
        raise CalibratedMetaPoolError("at least three promoted candidates are required for train/dev/final separation")
    train_ids, dev_ids, final_ids = ids[:-2], [ids[-2]], [ids[-1]]
    split_payload = {
        "schema_version": "cg-weekend-meta-splits-v1",
        "research_only": True,
        "candidate_exclusion_ids": [],
        "bindings": {"p1_policy_sha256": _sha256_file(p1_main), "p1_deck_sha256": _sha256_file(p1_deck), "meta_manifest_sha256": _sha256_file(meta_path), "pool_manifest_sha256": pool_sha, "evaluator_sha256": evaluation_implementation_sha256_v1()},
        "sources": {"meta_manifest_path": _relative(meta_path, root), "pool_manifest_path": _relative(pool_path, root)},
        "evaluation_contract": {"both_seats": True, "fault_inclusive": True, "training_exposure": 0, "teacher_labels_saved": False, "final_results_read_during_search": False},
        "train_blocks": [train_ids],
        "splits": {"META_TRAIN": [split_row(item) for item in train_ids], "META_DEV": [split_row(item) for item in dev_ids], "META_FINAL": [split_row(item) for item in final_ids]},
        "notes": ["TRAIN-only difficulty calibration selected this heterogeneous pool.", "Every candidate retains its original deck and policy bytes; mixing is pool-level only.", "DEV and FINAL are unused holdouts and must not be read during CEM search."],
    }
    _write_new(split_path, split_payload)
    report = {
        "schema_version": CALIBRATED_META_POOL_SCHEMA_V1,
        "status": "SEALED",
        "pool_root": str(root),
        "pool_manifest_sha256": pool_sha,
        "fresh_meta_path": str(fresh_path),
        "fresh_meta_sha256": _sha256_file(fresh_path),
        "meta_manifest_path": str(meta_path),
        "meta_manifest_sha256": _sha256_file(meta_path),
        "split_path": str(split_path),
        "split_sha256": _sha256_file(split_path),
        "split_ids": {"META_TRAIN": train_ids, "META_DEV": dev_ids, "META_FINAL": final_ids},
        "authority": {"training_allowed": False, "promotion_allowed": False, "submission_allowed": False, "longrun_allowed": False},
        "research_only": True,
    }
    _write_new(report_path, report)
    return report


__all__ = [
    "CALIBRATED_META_POOL_SCHEMA_V1",
    "CALIBRATED_SOURCE_V1",
    "CALIBRATED_RECIPE_V1",
    "CalibratedMetaPoolError",
    "build_calibrated_meta_pool_v1",
    "build_calibrated_meta_split_v1",
]
