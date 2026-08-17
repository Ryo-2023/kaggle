"""Teacher corpus revalidation and split/weight manifest generation."""

from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
from collections.abc import Iterable, Mapping


def build_teacher_manifest_v3(records: Iterable[Mapping[str, object]], *, lane: str) -> dict[str, object]:
    rows = list(records)
    if not lane or not rows:
        raise ValueError("lane and records are required")
    statuses = Counter()
    episodes: set[str] = set()
    near_duplicates: set[str] = set()
    teachers: set[str] = set()
    weights: list[float] = []
    for record in rows:
        episode = record.get("episode_id_hash")
        near = record.get("near_duplicate_id")
        teacher = record.get("teacher")
        if not isinstance(episode, str) or not isinstance(near, str) or not isinstance(teacher, Mapping):
            raise ValueError("teacher record identity is malformed")
        status = teacher.get("status")
        teacher_id = teacher.get("teacher_id", "unknown")
        weight = teacher.get("quality_weight", 1.0)
        if not isinstance(status, str) or not isinstance(teacher_id, str) or not isinstance(weight, (int, float)) or weight <= 0:
            raise ValueError("teacher status/id/weight is malformed")
        statuses[status] += 1
        episodes.add(episode)
        near_duplicates.add(near)
        teachers.add(teacher_id)
        weights.append(float(weight))
    payload = {
        "schema": "meta-specialist-teacher-revalidation-v3",
        "lane": lane,
        "record_count": len(rows),
        "status_counts": dict(sorted(statuses.items())),
        "episode_count": len(episodes),
        "near_duplicate_count": len(near_duplicates),
        "teacher_ids": sorted(teachers),
        "quality_weight_min": min(weights),
        "quality_weight_max": max(weights),
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {**payload, "manifest_sha256": digest}


def revalidate_teacher_root_v3(root: str | Path, *, lane: str, limit: int | None = None) -> dict[str, object]:
    root_path = Path(root)
    records: list[Mapping[str, object]] = []
    for path in sorted(root_path.glob("dataset-*.jsonl")):
        for line in path.open(encoding="utf-8"):
            records.append(json.loads(line))
            if limit is not None and len(records) >= limit:
                return build_teacher_manifest_v3(records, lane=lane)
    return build_teacher_manifest_v3(records, lane=lane)


__all__ = ["build_teacher_manifest_v3", "revalidate_teacher_root_v3"]
