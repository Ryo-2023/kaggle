"""Immutable, hash-bound META split contract for the cg P1 CEM campaign."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[3]
SPLIT_NAMES = ("META_TRAIN", "META_DEV", "META_FINAL")
SCHEMA = "cg-weekend-meta-splits-v1"


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


@dataclass(frozen=True, slots=True)
class WeekendSplit:
    path: Path
    rows_by_split: dict[str, tuple[dict[str, Any], ...]]
    train_blocks: tuple[tuple[str, ...], ...]
    metadata: dict[str, Any]
    config_sha256: str

    def ids(self, split: str) -> tuple[str, ...]:
        if split not in self.rows_by_split:
            raise KeyError(split)
        return tuple(str(row["opponent_id"]) for row in self.rows_by_split[split])

    def row(self, opponent_id: str) -> dict[str, Any]:
        for rows in self.rows_by_split.values():
            for row in rows:
                if row["opponent_id"] == opponent_id:
                    return dict(row)
        raise KeyError(opponent_id)

    def weights(self, split: str) -> dict[str, float]:
        rows = self.rows_by_split[split]
        return {str(row["opponent_id"]): float(row["weight"]) for row in rows}


def _require_hash(value: object, name: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise ValueError(f"{name} must be a lowercase SHA-256 hex string")
    return value


def _validate_row(row: object, split: str) -> dict[str, Any]:
    if not isinstance(row, Mapping):
        raise ValueError(f"{split} row must be an object")
    required = {
        "opponent_id",
        "archetype",
        "deck_sha256",
        "policy_sha256",
        "source_sha256",
        "weight",
        "usage_boundary",
        "training_exposure",
    }
    missing = required - set(row)
    if missing:
        raise ValueError(f"{split} row missing fields: {sorted(missing)}")
    opponent_id = row["opponent_id"]
    if not isinstance(opponent_id, str) or not opponent_id:
        raise ValueError(f"{split} opponent_id must be non-empty")
    for name in ("deck_sha256", "policy_sha256", "source_sha256"):
        _require_hash(row[name], f"{split}.{opponent_id}.{name}")
    weight = row["weight"]
    if isinstance(weight, bool) or not isinstance(weight, (int, float)) or not 0 < float(weight) <= 1:
        raise ValueError(f"{split}.{opponent_id}.weight must be in (0, 1]")
    if row["usage_boundary"] != "local_eval_only":
        raise ValueError(f"{split}.{opponent_id} must be local_eval_only")
    if row["training_exposure"] != 0:
        raise ValueError(f"{split}.{opponent_id} training exposure must be 0")
    result = dict(row)
    result["split"] = split
    return result


def _resolve_source_path(value: object, *, split_parent: Path) -> Path:
    """Resolve repo-relative sources, with a colocated-artifact fallback.

    Historical split files normally bind sources from the repository root.  A
    sealed research pool may instead carry its manifests beside the split;
    accepting that layout keeps the split self-contained without weakening the
    subsequent SHA-256 binding checks.
    """

    candidate = (ROOT / str(value)).resolve()
    if candidate.is_file():
        return candidate
    return (split_parent / str(value)).resolve()


def _verify_sources(
    raw: Mapping[str, Any],
    rows_by_split: Mapping[str, tuple[dict[str, Any], ...]],
    *,
    split_parent: Path,
) -> None:
    source = raw["sources"]
    bindings = raw["bindings"]
    if not isinstance(source, Mapping):
        raise ValueError("sources must be an object")
    if not isinstance(bindings, Mapping):
        raise ValueError("bindings must be an object")
    meta_path = _resolve_source_path(source["meta_manifest_path"], split_parent=split_parent)
    pool_path = _resolve_source_path(source["pool_manifest_path"], split_parent=split_parent)
    if _sha256_file(meta_path) != _require_hash(bindings["meta_manifest_sha256"], "meta_manifest_sha256"):
        raise ValueError("META manifest SHA mismatch")
    if _sha256_file(pool_path) != _require_hash(bindings["pool_manifest_sha256"], "pool_manifest_sha256"):
        raise ValueError("opponent pool SHA mismatch")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta_rows = {str(row["opponent_id"]): row for row in meta.get("rows", [])}
    pool = {str(row["id"]): row for row in json.loads(pool_path.read_text(encoding="utf-8"))}
    for split_rows in rows_by_split.values():
        for row in split_rows:
            oid = str(row["opponent_id"])
            source_row = meta_rows.get(oid)
            pool_row = pool.get(oid)
            if source_row is None or pool_row is None:
                raise ValueError(f"opponent is absent from bound source manifests: {oid}")
            for field in ("deck_sha256", "policy_sha256", "source_sha256"):
                if str(source_row.get(field)) != row[field]:
                    raise ValueError(f"{oid} {field} does not match META manifest")
            if str(pool_row.get("policy_hash")) != row["policy_sha256"]:
                raise ValueError(f"{oid} policy hash does not match pool manifest")
            if pool_row.get("smoke_ok") is not True:
                raise ValueError(f"{oid} is not smoke_ok")


def load_weekend_split(path: Path | str, *, verify_sources: bool = True) -> WeekendSplit:
    target = Path(path).resolve()
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read split config: {target}") from exc
    if not isinstance(raw, Mapping) or raw.get("schema_version") != SCHEMA:
        raise ValueError("unexpected weekend split schema")
    raw_splits = raw.get("splits")
    if not isinstance(raw_splits, Mapping) or set(raw_splits) != set(SPLIT_NAMES):
        raise ValueError("splits must contain META_TRAIN, META_DEV, META_FINAL exactly")
    rows_by_split: dict[str, tuple[dict[str, Any], ...]] = {}
    all_ids: list[str] = []
    for split in SPLIT_NAMES:
        rows = tuple(_validate_row(row, split) for row in raw_splits[split])
        if not rows:
            raise ValueError(f"{split} cannot be empty")
        rows_by_split[split] = rows
        all_ids.extend(row["opponent_id"] for row in rows)
    if len(all_ids) != len(set(all_ids)):
        raise ValueError("META split opponent IDs overlap")
    exclusions = raw.get("candidate_exclusion_ids")
    if not isinstance(exclusions, list) or not all(isinstance(value, str) for value in exclusions):
        raise ValueError("candidate_exclusion_ids must be a string list")
    if set(exclusions) & set(all_ids):
        raise ValueError("candidate exclusion appears in a weekend split")
    blocks_raw = raw.get("train_blocks")
    if not isinstance(blocks_raw, list) or not blocks_raw:
        raise ValueError("train_blocks must be a non-empty list")
    blocks: list[tuple[str, ...]] = []
    for block in blocks_raw:
        if not isinstance(block, list) or not block or not all(isinstance(value, str) for value in block):
            raise ValueError("each train block must be a non-empty string list")
        blocks.append(tuple(block))
    train_ids = set(row["opponent_id"] for row in rows_by_split["META_TRAIN"])
    block_ids = [oid for block in blocks for oid in block]
    if len(block_ids) != len(set(block_ids)) or set(block_ids) != train_ids:
        raise ValueError("train blocks must partition META_TRAIN exactly")
    bindings = raw.get("bindings")
    if not isinstance(bindings, Mapping):
        raise ValueError("bindings must be an object")
    for name in ("p1_policy_sha256", "p1_deck_sha256", "meta_manifest_sha256", "pool_manifest_sha256", "evaluator_sha256"):
        _require_hash(bindings.get(name), name)
    if verify_sources:
        _verify_sources(raw, rows_by_split, split_parent=target.parent)
    return WeekendSplit(
        path=target,
        rows_by_split=rows_by_split,
        train_blocks=tuple(blocks),
        metadata=dict(raw),
        config_sha256=_sha256_file(target),
    )
