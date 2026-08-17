"""Hash-bound META_TRAIN population schedule for the cg alternating loop.

The schedule is an evaluation-only view of the existing opponent population.
It selects a deterministic upper-meta subset from the immutable distribution
manifest, verifies every id against the pool manifest, and never turns
``local_eval_only`` assets into behavior or teacher data.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import tempfile
from typing import Any, Mapping

from .meta_distribution_v1 import load_meta_distribution_manifest_v1


SCHEMA_V1 = "meta-specialist-cg-population-schedule-v1"
ALLOWED_SPLIT = "META_TRAIN"


class CgPopulationScheduleError(ValueError):
    """Raised when a population schedule is not closed or permission-safe."""


def _sha256(path: Path | str) -> str:
    source = Path(path)
    if source.is_symlink() or not source.is_file():
        raise CgPopulationScheduleError(f"regular source file required: {source}")
    return hashlib.sha256(source.read_bytes()).hexdigest()


def _load_pool(path: Path | str) -> tuple[dict[str, Mapping[str, Any]], str]:
    source = Path(path)
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CgPopulationScheduleError(f"cannot read pool manifest: {source}") from exc
    if not isinstance(raw, list) or not raw:
        raise CgPopulationScheduleError("pool manifest must be a non-empty list")
    result: dict[str, Mapping[str, Any]] = {}
    for row in raw:
        if not isinstance(row, Mapping) or not isinstance(row.get("id"), str):
            raise CgPopulationScheduleError("pool row must contain string id")
        asset_id = str(row["id"])
        if asset_id in result:
            raise CgPopulationScheduleError(f"duplicate pool id: {asset_id}")
        result[asset_id] = row
    return result, _sha256(source)


@dataclass(frozen=True, slots=True)
class CgPopulationScheduleV1:
    schema_version: str
    manifest_path: str
    manifest_sha256: str
    pool_manifest_path: str
    pool_manifest_sha256: str
    split: str
    selection_rule: str
    reference_ids: tuple[str, ...]
    weights: Mapping[str, float]
    usage_boundaries: Mapping[str, str]
    pool_ids: tuple[str, ...]
    evaluation_only: bool
    behavior_allowed: bool
    teacher_labels_saved: bool
    research_only: bool

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_V1:
            raise CgPopulationScheduleError("wrong schedule schema")
        if self.split != ALLOWED_SPLIT:
            raise CgPopulationScheduleError("only META_TRAIN evaluation schedules are allowed")
        if not self.reference_ids or len(self.reference_ids) != len(set(self.reference_ids)):
            raise CgPopulationScheduleError("reference_ids must be unique and non-empty")
        if tuple(sorted(self.reference_ids)) != self.reference_ids:
            raise CgPopulationScheduleError("reference_ids must be canonical sorted order")
        if any(item not in self.pool_ids for item in self.reference_ids):
            raise CgPopulationScheduleError("schedule references an id absent from pool")
        if set(self.weights) != set(self.reference_ids) or set(self.usage_boundaries) != set(self.reference_ids):
            raise CgPopulationScheduleError("schedule metadata keys do not match reference_ids")
        if any(self.usage_boundaries[item] != "local_eval_only" for item in self.reference_ids):
            raise CgPopulationScheduleError("cg population schedule may only use local_eval_only evaluation rows")
        if not self.evaluation_only or self.behavior_allowed or self.teacher_labels_saved or not self.research_only:
            raise CgPopulationScheduleError("schedule grants forbidden behavior/training authority")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "manifest_path": self.manifest_path,
            "manifest_sha256": self.manifest_sha256,
            "pool_manifest_path": self.pool_manifest_path,
            "pool_manifest_sha256": self.pool_manifest_sha256,
            "split": self.split,
            "selection_rule": self.selection_rule,
            "reference_ids": list(self.reference_ids),
            "weights": {key: float(self.weights[key]) for key in sorted(self.weights)},
            "usage_boundaries": {key: self.usage_boundaries[key] for key in sorted(self.usage_boundaries)},
            "pool_ids": list(self.pool_ids),
            "evaluation_only": self.evaluation_only,
            "behavior_allowed": self.behavior_allowed,
            "teacher_labels_saved": self.teacher_labels_saved,
            "research_only": self.research_only,
        }


def build_cg_population_schedule_v1(
    *,
    manifest_path: Path | str,
    pool_manifest_path: Path | str,
    split: str = ALLOWED_SPLIT,
    count: int = 24,
) -> CgPopulationScheduleV1:
    if split != ALLOWED_SPLIT:
        raise CgPopulationScheduleError("only META_TRAIN may feed this evaluation schedule")
    if type(count) is not int or count <= 0:
        raise CgPopulationScheduleError("count must be a positive integer")
    manifest_file = Path(manifest_path).resolve()
    pool_file = Path(pool_manifest_path).resolve()
    manifest = load_meta_distribution_manifest_v1(manifest_file, verify_sources=True)
    pool, pool_sha = _load_pool(pool_file)
    # Filter the source distribution by the runtime-qualified pool before
    # ranking.  A high-weight manifest row may be present in the distribution
    # while its local asset is not smoke-qualified (for example a benchmark
    # row with an intentionally failed plumbing smoke).  Such a row must not
    # displace an executable META_TRAIN opponent.
    rows = [
        row
        for row in manifest.rows
        if row.split == split
        and row.evaluation_allowed
        and row.opponent_id in pool
        and pool[row.opponent_id].get("smoke_ok") is True
        and pool[row.opponent_id].get("usage_boundary") == "local_eval_only"
    ]
    if len(rows) < count:
        raise CgPopulationScheduleError(
            f"not enough smoke-qualified evaluation rows in {split}: {len(rows)} < {count}"
        )
    # Weight is the primary current-meta signal.  Strength and id provide
    # deterministic tie-breaks without peeking at private behavior.
    rows.sort(key=lambda row: (-float(row.weight), -float(row.observed_strength), row.opponent_id))
    chosen = rows[:count]
    reference_ids = tuple(sorted(row.opponent_id for row in chosen))
    by_id = {row.opponent_id: row for row in chosen}
    return CgPopulationScheduleV1(
        schema_version=SCHEMA_V1,
        manifest_path=str(manifest_file),
        manifest_sha256=_sha256(manifest_file),
        pool_manifest_path=str(pool_file),
        pool_manifest_sha256=pool_sha,
        split=split,
        selection_rule="top_weight_then_observed_strength_then_id",
        reference_ids=reference_ids,
        weights={item: float(by_id[item].weight) for item in reference_ids},
        usage_boundaries={item: str(pool[item]["usage_boundary"]) for item in reference_ids},
        pool_ids=tuple(sorted(pool)),
        evaluation_only=True,
        behavior_allowed=False,
        teacher_labels_saved=False,
        research_only=True,
    )


def save_cg_population_schedule_v1(schedule: CgPopulationScheduleV1, path: Path | str) -> str:
    if type(schedule) is not CgPopulationScheduleV1:
        raise CgPopulationScheduleError("schedule must be exact CgPopulationScheduleV1")
    target = Path(path)
    if target.exists() or target.is_symlink():
        raise FileExistsError(f"refusing to overwrite schedule: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(schedule.to_dict(), ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.tmp-", dir=target.parent)
    try:
        with open(fd, "wb", closefd=True) as handle:
            handle.write(raw)
            handle.flush()
        Path(temporary).replace(target)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return hashlib.sha256(raw).hexdigest()


def load_cg_population_schedule_v1(path: Path | str, *, verify_sources: bool = True) -> CgPopulationScheduleV1:
    target = Path(path)
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CgPopulationScheduleError(f"cannot read schedule: {target}") from exc
    if not isinstance(raw, Mapping):
        raise CgPopulationScheduleError("schedule root must be an object")
    schedule = CgPopulationScheduleV1(
        schema_version=str(raw.get("schema_version")),
        manifest_path=str(raw.get("manifest_path")),
        manifest_sha256=str(raw.get("manifest_sha256")),
        pool_manifest_path=str(raw.get("pool_manifest_path")),
        pool_manifest_sha256=str(raw.get("pool_manifest_sha256")),
        split=str(raw.get("split")),
        selection_rule=str(raw.get("selection_rule")),
        reference_ids=tuple(str(item) for item in raw.get("reference_ids", ())),
        weights={str(key): float(value) for key, value in dict(raw.get("weights", {})).items()},
        usage_boundaries={str(key): str(value) for key, value in dict(raw.get("usage_boundaries", {})).items()},
        pool_ids=tuple(str(item) for item in raw.get("pool_ids", ())),
        evaluation_only=bool(raw.get("evaluation_only")),
        behavior_allowed=bool(raw.get("behavior_allowed")),
        teacher_labels_saved=bool(raw.get("teacher_labels_saved")),
        research_only=bool(raw.get("research_only")),
    )
    if verify_sources:
        if _sha256(schedule.manifest_path) != schedule.manifest_sha256:
            raise CgPopulationScheduleError("meta manifest SHA mismatch")
        if _sha256(schedule.pool_manifest_path) != schedule.pool_manifest_sha256:
            raise CgPopulationScheduleError("pool manifest SHA mismatch")
        rebuilt = build_cg_population_schedule_v1(
            manifest_path=schedule.manifest_path,
            pool_manifest_path=schedule.pool_manifest_path,
            split=schedule.split,
            count=len(schedule.reference_ids),
        )
        if rebuilt.to_dict() != schedule.to_dict():
            raise CgPopulationScheduleError("schedule is not a deterministic rebuild")
    return schedule


__all__ = [
    "ALLOWED_SPLIT",
    "CgPopulationScheduleError",
    "CgPopulationScheduleV1",
    "build_cg_population_schedule_v1",
    "load_cg_population_schedule_v1",
    "save_cg_population_schedule_v1",
]
