"""Hash-bound, permission-aware meta distribution for autonomous research.

This module intentionally does not collect teacher labels or change any native
agent.  It turns the existing census and native-pair ranking artifacts into a
closed schedule that can be consumed by evaluation and, only for explicitly
authorized rows, by teacher collection.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


SCHEMA_V1 = "meta-specialist-meta-distribution-v1"
SPLITS_V1 = frozenset({"META_TRAIN", "META_DEV", "META_FINAL"})
_SHA_CHARS = frozenset("0123456789abcdef")
_ALLOWED_USAGE_BOUNDARIES = frozenset({"local_eval_only", "training_local", "training_local_and_eval"})
_WEIGHT_COMPONENT_TARGETS = {"top_meta": 0.60, "hard_negative": 0.25, "diversity": 0.15}


class MetaDistributionError(ValueError):
    """Raised when a meta manifest is not closed or permission-safe."""


def _sha256(path: Path | str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _sha(value: object, name: str) -> str:
    if type(value) is not str or len(value) != 64 or any(c not in _SHA_CHARS for c in value):
        raise MetaDistributionError(f"{name} must be a lowercase SHA-256 hex string")
    return value


def _text(value: object, name: str) -> str:
    if type(value) is not str or not value.strip():
        raise MetaDistributionError(f"{name} must be a non-empty string")
    return value


def _finite_unit(value: object, name: str) -> float:
    if type(value) not in (int, float):
        raise MetaDistributionError(f"{name} must be numeric")
    result = float(value)
    if result != result or result in (float("inf"), float("-inf")) or not 0.0 <= result <= 1.0:
        raise MetaDistributionError(f"{name} must be finite in [0,1]")
    return result


def _require_bool(value: object, name: str) -> bool:
    if type(value) is not bool:
        raise MetaDistributionError(f"{name} must be bool")
    return value


def _normalize(values: Mapping[str, float]) -> dict[str, float]:
    total = sum(max(0.0, float(value)) for value in values.values())
    if total <= 0.0:
        if not values:
            return {}
        uniform = 1.0 / len(values)
        return {key: uniform for key in values}
    return {key: max(0.0, float(value)) / total for key, value in values.items()}


@dataclass(frozen=True, slots=True)
class MetaSourceArtifactV1:
    path: str
    sha256: str
    role: str

    def __post_init__(self) -> None:
        _text(self.path, "source.path")
        _sha(self.sha256, "source.sha256")
        _text(self.role, "source.role")


@dataclass(frozen=True, slots=True)
class MetaDistributionRowV1:
    opponent_id: str
    pair_id: str
    deck_sha256: str
    policy_sha256: str
    archetype: str
    runtime_class: str
    source: str
    source_sha256: str
    usage_boundary: str
    evaluation_allowed: bool
    training_allowed: bool
    behavior_allowed: bool
    submission_allowed: bool
    observed_strength: float
    observed_games: int
    observed_fault_rate: float
    frequency_proxy: float
    hard_negative_score: float
    diversity_contribution: float
    top_meta_component: float
    hard_negative_component: float
    diversity_component: float
    weight: float
    split: str
    runtime_status: str
    evidence_status: str

    def __post_init__(self) -> None:
        _text(self.opponent_id, "opponent_id")
        _text(self.pair_id, "pair_id")
        _sha(self.deck_sha256, "deck_sha256")
        _sha(self.policy_sha256, "policy_sha256")
        _text(self.archetype, "archetype")
        _text(self.runtime_class, "runtime_class")
        _text(self.source, "source")
        _sha(self.source_sha256, "source_sha256")
        if self.usage_boundary not in _ALLOWED_USAGE_BOUNDARIES:
            raise MetaDistributionError(f"unsupported usage_boundary: {self.usage_boundary}")
        for name in ("evaluation_allowed", "training_allowed", "behavior_allowed", "submission_allowed"):
            _require_bool(getattr(self, name), name)
        for name in (
            "observed_strength",
            "observed_fault_rate",
            "frequency_proxy",
            "hard_negative_score",
            "diversity_contribution",
            "top_meta_component",
            "hard_negative_component",
            "diversity_component",
            "weight",
        ):
            _finite_unit(getattr(self, name), name)
        if type(self.observed_games) is not int or self.observed_games < 0:
            raise MetaDistributionError("observed_games must be a nonnegative integer")
        if self.split not in SPLITS_V1:
            raise MetaDistributionError(f"unsupported split: {self.split}")
        _text(self.runtime_status, "runtime_status")
        _text(self.evidence_status, "evidence_status")


@dataclass(frozen=True, slots=True)
class MetaScheduleRowV1:
    opponent_id: str
    split: str
    count: int
    normalized_weight: float
    training_allowed: bool

    def __post_init__(self) -> None:
        _text(self.opponent_id, "schedule.opponent_id")
        if self.split not in SPLITS_V1:
            raise MetaDistributionError(f"unsupported schedule split: {self.split}")
        if type(self.count) is not int or self.count <= 0:
            raise MetaDistributionError("schedule.count must be positive")
        _finite_unit(self.normalized_weight, "schedule.normalized_weight")
        _require_bool(self.training_allowed, "schedule.training_allowed")


@dataclass(frozen=True, slots=True)
class MetaDistributionManifestV1:
    schema_version: str
    candidate_id: str
    sources: tuple[MetaSourceArtifactV1, ...]
    rows: tuple[MetaDistributionRowV1, ...]
    component_targets: Mapping[str, float]
    split_ids: Mapping[str, tuple[str, ...]]
    training_authority: bool
    promotion_authority: bool
    submission_authority: bool
    research_only: bool
    notes: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_V1:
            raise MetaDistributionError("wrong meta distribution schema")
        _text(self.candidate_id, "candidate_id")
        if not self.sources:
            raise MetaDistributionError("at least one source artifact is required")
        if not self.rows:
            raise MetaDistributionError("at least one meta row is required")
        for key, expected in _WEIGHT_COMPONENT_TARGETS.items():
            if key not in self.component_targets:
                raise MetaDistributionError(f"missing component target: {key}")
            if abs(float(self.component_targets[key]) - expected) > 1e-9:
                raise MetaDistributionError(f"component target mismatch: {key}")
        for name in ("training_authority", "promotion_authority", "submission_authority", "research_only"):
            _require_bool(getattr(self, name), name)
        if not self.research_only:
            raise MetaDistributionError("meta manifest must remain research_only")
        if self.training_authority or self.promotion_authority or self.submission_authority:
            raise MetaDistributionError("authority flags must remain false")
        ids = [row.opponent_id for row in self.rows]
        if len(ids) != len(set(ids)):
            raise MetaDistributionError("duplicate opponent_id in manifest")
        if abs(sum(row.weight for row in self.rows) - 1.0) > 1e-6:
            raise MetaDistributionError("row weights must sum to one")
        for split in SPLITS_V1:
            expected = tuple(sorted(row.opponent_id for row in self.rows if row.split == split))
            actual = tuple(sorted(self.split_ids.get(split, ())))
            if expected != actual:
                raise MetaDistributionError(f"split ids mismatch for {split}")
        if set().union(*(set(self.split_ids.get(split, ())) for split in SPLITS_V1)) != set(ids):
            raise MetaDistributionError("split ids do not cover rows")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "candidate_id": self.candidate_id,
            "sources": [asdict(source) for source in self.sources],
            "rows": [asdict(row) for row in self.rows],
            "component_targets": dict(self.component_targets),
            "split_ids": {key: list(value) for key, value in sorted(self.split_ids.items())},
            "training_authority": self.training_authority,
            "promotion_authority": self.promotion_authority,
            "submission_authority": self.submission_authority,
            "research_only": self.research_only,
            "notes": list(self.notes),
        }


def _load_json(path: Path | str) -> Mapping[str, Any]:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MetaDistributionError(f"cannot read JSON: {path}") from exc
    if not isinstance(raw, Mapping):
        raise MetaDistributionError(f"JSON root must be an object: {path}")
    return raw


def _census_rows(census_path: Path | str) -> dict[str, Mapping[str, Any]]:
    raw = _load_json(census_path)
    assets = raw.get("assets")
    if not isinstance(assets, list) or not assets:
        raise MetaDistributionError("census assets must be a non-empty list")
    result: dict[str, Mapping[str, Any]] = {}
    for asset in assets:
        if not isinstance(asset, Mapping):
            raise MetaDistributionError("census asset must be an object")
        asset_id = _text(asset.get("agent_id"), "census.asset.agent_id")
        if asset_id in result:
            raise MetaDistributionError(f"duplicate census asset: {asset_id}")
        usage = _text(asset.get("usage_boundary"), f"census[{asset_id}].usage_boundary")
        if usage not in _ALLOWED_USAGE_BOUNDARIES:
            raise MetaDistributionError(f"unsupported usage_boundary: {usage}")
        result[asset_id] = asset
    return result


def _ranking_rows(path: Path | str) -> dict[str, Mapping[str, Any]]:
    raw = _load_json(path)
    rows = raw.get("ranking")
    if not isinstance(rows, list):
        raise MetaDistributionError(f"ranking must contain ranking list: {path}")
    result: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            raise MetaDistributionError("ranking row must be an object")
        asset_id = _text(row.get("asset_id"), "ranking.asset_id")
        result[asset_id] = row
    return result


def _ranking_summary(paths: Sequence[Path | str]) -> tuple[dict[str, tuple[float, int, float]], dict[str, tuple[float, int]]]:
    per_asset: dict[str, list[tuple[float, int, float]]] = {}
    hard_by_opponent: dict[str, list[tuple[float, int]]] = {}
    for path in paths:
        for asset_id, row in _ranking_rows(path).items():
            completed = int(row.get("completed_games", 0) or 0)
            score = row.get("score_rate")
            faults = row.get("fault_rate", 0.0)
            if type(score) not in (int, float):
                continue
            score_float = max(0.0, min(1.0, float(score)))
            fault_float = max(0.0, min(1.0, float(faults or 0.0)))
            per_asset.setdefault(asset_id, []).append((score_float, max(0, completed), fault_float))
            opponents = row.get("opponents", {})
            if isinstance(opponents, Mapping):
                for opponent_id, opponent_row in opponents.items():
                    if not isinstance(opponent_row, Mapping):
                        continue
                    opponent_score = opponent_row.get("score_rate")
                    opponent_games = int(opponent_row.get("completed_games", 0) or 0)
                    if type(opponent_score) in (int, float) and opponent_games > 0:
                        hard_by_opponent.setdefault(str(opponent_id), []).append(
                            (1.0 - max(0.0, min(1.0, float(opponent_score))), opponent_games)
                        )
    summary: dict[str, tuple[float, int, float]] = {}
    for asset_id, values in per_asset.items():
        denominator = sum(games for _, games, _ in values)
        if denominator <= 0:
            summary[asset_id] = (0.5, 0, 1.0)
            continue
        score = sum(value * games for value, games, _ in values) / denominator
        fault = sum(value * games for _, games, value in values) / denominator
        summary[asset_id] = (score, denominator, fault)
    hard_summary: dict[str, tuple[float, int]] = {}
    for opponent_id, values in hard_by_opponent.items():
        denominator = sum(games for _, games in values)
        hard_summary[opponent_id] = (
            sum(value * games for value, games in values) / denominator,
            denominator,
        )
    return summary, hard_summary


def _runtime_class(asset: Mapping[str, Any]) -> str:
    if not bool(asset.get("smoke_ok", False)):
        return "quarantined"
    status = str(asset.get("runtime_status", ""))
    mean_ms = asset.get("mean_decision_ms")
    if "slow" in status or "very_slow" in status or (isinstance(mean_ms, (int, float)) and float(mean_ms) > 100.0):
        return "slow_native"
    return "native_fast"


def _training_allowed(asset: Mapping[str, Any]) -> bool:
    return str(asset.get("training_usable", "")).startswith("yes_")


def _component_normalize(values: Mapping[str, float]) -> dict[str, float]:
    return _normalize({key: max(0.0, value) for key, value in values.items()})


def build_meta_distribution_manifest_v1(
    census_path: Path | str,
    ranking_paths: Sequence[Path | str],
    *,
    candidate_id: str,
    dev_ids: Sequence[str],
    final_ids: Sequence[str],
) -> MetaDistributionManifestV1:
    """Build an immutable distribution from existing artifacts only."""
    candidate_id = _text(candidate_id, "candidate_id")
    if not ranking_paths:
        raise MetaDistributionError("ranking_paths must not be empty")
    census = _census_rows(census_path)
    if candidate_id not in census:
        raise MetaDistributionError(f"candidate_id not found in census: {candidate_id}")
    dev = tuple(dict.fromkeys(str(item) for item in dev_ids))
    final = tuple(dict.fromkeys(str(item) for item in final_ids))
    if set(dev) & set(final):
        raise MetaDistributionError("META_DEV and META_FINAL overlap")
    unknown = (set(dev) | set(final)) - set(census)
    if unknown:
        raise MetaDistributionError(f"unknown split ids: {sorted(unknown)}")
    ranking_summary, hard_summary = _ranking_summary(ranking_paths)
    ids = sorted(census)
    observed = {asset_id: ranking_summary.get(asset_id, (0.5, 0, 1.0)) for asset_id in ids}
    known_scores = [value[0] for value in observed.values() if value[1] > 0]
    lo = min(known_scores) if known_scores else 0.5
    hi = max(known_scores) if known_scores else 0.5
    span = hi - lo
    strength_rank: dict[str, float] = {}
    for asset_id in ids:
        score, games, _ = observed[asset_id]
        strength_rank[asset_id] = (score - lo) / span if span > 1e-12 and games > 0 else 0.5
    archetype_counts: dict[str, int] = {}
    for asset in census.values():
        archetype_counts[str(asset.get("archetype", "Other/Unknown"))] = archetype_counts.get(
            str(asset.get("archetype", "Other/Unknown")), 0
        ) + 1
    frequency_raw: dict[str, float] = {}
    diversity_raw: dict[str, float] = {}
    hard_raw: dict[str, float] = {}
    top_raw: dict[str, float] = {}
    for asset_id in ids:
        asset = census[asset_id]
        archetype = str(asset.get("archetype", "Other/Unknown"))
        prevalence = archetype_counts[archetype] / len(ids)
        frequency_raw[asset_id] = 0.7 * strength_rank[asset_id] + 0.3 * prevalence
        diversity_raw[asset_id] = 1.0 / archetype_counts[archetype]
        if asset_id == candidate_id:
            hard_raw[asset_id] = 0.0
        else:
            hard_raw[asset_id] = hard_summary.get(asset_id, (max(0.0, 1.0 - observed[asset_id][0]), 0))[0]
        top_raw[asset_id] = 0.6 * strength_rank[asset_id] + 0.4 * frequency_raw[asset_id]
    top_component = _component_normalize(top_raw)
    hard_component = _component_normalize(hard_raw)
    diversity_component = _component_normalize(diversity_raw)
    weights = {
        asset_id: (
            _WEIGHT_COMPONENT_TARGETS["top_meta"] * top_component[asset_id]
            + _WEIGHT_COMPONENT_TARGETS["hard_negative"] * hard_component[asset_id]
            + _WEIGHT_COMPONENT_TARGETS["diversity"] * diversity_component[asset_id]
        )
        for asset_id in ids
    }
    # Preserve a deterministic exact sum despite binary floating-point rounding.
    weights[ids[-1]] += 1.0 - sum(weights.values())
    sources = [
        MetaSourceArtifactV1(str(Path(census_path).resolve()), _sha256(census_path), "census"),
        *[
            MetaSourceArtifactV1(str(Path(path).resolve()), _sha256(path), "native_ranking")
            for path in ranking_paths
        ],
    ]
    rows: list[MetaDistributionRowV1] = []
    for asset_id in ids:
        asset = census[asset_id]
        if asset_id in set(dev):
            split = "META_DEV"
        elif asset_id in set(final):
            split = "META_FINAL"
        else:
            split = "META_TRAIN"
        score, games, fault = observed[asset_id]
        training = _training_allowed(asset)
        evaluation = bool(asset.get("smoke_ok", False))
        behavior = training and str(asset.get("usage_boundary")) != "local_eval_only"
        rows.append(
            MetaDistributionRowV1(
                opponent_id=asset_id,
                pair_id=str(asset.get("pair_id", f"{asset_id}::unknown")),
                deck_sha256=_sha(asset.get("deck_sha256_raw_file"), f"{asset_id}.deck_sha256_raw_file"),
                policy_sha256=_sha(asset.get("policy_sha256_raw_main_py"), f"{asset_id}.policy_sha256_raw_main_py"),
                archetype=str(asset.get("archetype", "Other/Unknown")),
                runtime_class=_runtime_class(asset),
                source=str(asset.get("source", "unknown")),
                source_sha256=_sha(asset.get("source_sha256"), f"{asset_id}.source_sha256"),
                usage_boundary=str(asset.get("usage_boundary")),
                evaluation_allowed=evaluation,
                training_allowed=training,
                behavior_allowed=behavior,
                submission_allowed=False,
                observed_strength=score,
                observed_games=games,
                observed_fault_rate=fault,
                frequency_proxy=frequency_raw[asset_id],
                hard_negative_score=hard_raw[asset_id],
                diversity_contribution=diversity_raw[asset_id] / max(diversity_raw.values()),
                top_meta_component=top_component[asset_id],
                hard_negative_component=hard_component[asset_id],
                diversity_component=diversity_component[asset_id],
                weight=weights[asset_id],
                split=split,
                runtime_status=str(asset.get("runtime_status", "unmeasured")),
                evidence_status="observed" if games > 0 else "unmeasured_or_runtime_infeasible",
            )
        )
    split_ids = {split: tuple(sorted(row.opponent_id for row in rows if row.split == split)) for split in SPLITS_V1}
    return MetaDistributionManifestV1(
        schema_version=SCHEMA_V1,
        candidate_id=candidate_id,
        sources=tuple(sources),
        rows=tuple(rows),
        component_targets=dict(_WEIGHT_COMPONENT_TARGETS),
        split_ids=split_ids,
        training_authority=False,
        promotion_authority=False,
        submission_authority=False,
        research_only=True,
        notes=(
            "Weights are an observed-pool proxy, not a claim about Kaggle prevalence.",
            "META_TRAIN includes all rows structurally, but teacher collection requires training_allowed=true.",
            "local_eval_only rows remain evaluation-only and cannot supply labels or behavior policy data.",
            "META_FINAL must not be used for candidate selection before the final gate.",
        ),
    )


def build_meta_schedule_v1(
    manifest: MetaDistributionManifestV1,
    *,
    split: str,
    quota: int,
    require_training_permission: bool = False,
) -> tuple[MetaScheduleRowV1, ...]:
    if split not in SPLITS_V1:
        raise MetaDistributionError(f"unsupported schedule split: {split}")
    if type(quota) is not int or quota <= 0:
        raise MetaDistributionError("quota must be a positive integer")
    rows = [row for row in manifest.rows if row.split == split and row.evaluation_allowed]
    if require_training_permission:
        rows = [row for row in rows if row.training_allowed]
        if not rows:
            raise MetaDistributionError(f"no rows with training permission in {split}")
    if not rows:
        raise MetaDistributionError(f"no evaluation rows in {split}")
    if require_training_permission and any(not row.training_allowed for row in rows):
        raise MetaDistributionError("training permission filtering failed")
    total = sum(row.weight for row in rows)
    if total <= 0.0:
        raise MetaDistributionError("schedule weight mass is zero")
    normalized = {row.opponent_id: row.weight / total for row in rows}
    raw_counts = {asset_id: normalized[asset_id] * quota for asset_id in normalized}
    counts = {asset_id: int(raw_counts[asset_id]) for asset_id in raw_counts}
    remainder = quota - sum(counts.values())
    order = sorted(normalized, key=lambda asset_id: (-(raw_counts[asset_id] - counts[asset_id]), asset_id))
    for asset_id in order[:remainder]:
        counts[asset_id] += 1
    output = tuple(
        MetaScheduleRowV1(
            opponent_id=asset_id,
            split=split,
            count=counts[asset_id],
            normalized_weight=normalized[asset_id],
            training_allowed=next(row.training_allowed for row in rows if row.opponent_id == asset_id),
        )
        for asset_id in sorted(counts)
        if counts[asset_id] > 0
    )
    if sum(row.count for row in output) != quota:
        raise MetaDistributionError("schedule quota allocation mismatch")
    return output


def save_meta_distribution_manifest_v1(manifest: MetaDistributionManifestV1, path: Path | str) -> str:
    if type(manifest) is not MetaDistributionManifestV1:
        raise MetaDistributionError("manifest must be exact MetaDistributionManifestV1")
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    temporary = destination.with_name(destination.name + ".tmp")
    temporary.write_text(payload, encoding="utf-8")
    temporary.replace(destination)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_meta_distribution_manifest_v1(
    path: Path | str, *, verify_sources: bool = True
) -> MetaDistributionManifestV1:
    raw = _load_json(path)
    if raw.get("schema_version") != SCHEMA_V1:
        raise MetaDistributionError("wrong meta manifest schema")
    sources_raw = raw.get("sources")
    rows_raw = raw.get("rows")
    if not isinstance(sources_raw, list) or not isinstance(rows_raw, list):
        raise MetaDistributionError("manifest sources/rows must be lists")
    sources = tuple(MetaSourceArtifactV1(**dict(source)) for source in sources_raw)
    if verify_sources:
        for source in sources:
            source_path = Path(source.path)
            if not source_path.is_file() or _sha256(source_path) != source.sha256:
                raise MetaDistributionError(f"source SHA mismatch: {source.path}")
    rows = tuple(MetaDistributionRowV1(**dict(row)) for row in rows_raw)
    split_ids_raw = raw.get("split_ids")
    if not isinstance(split_ids_raw, Mapping):
        raise MetaDistributionError("split_ids must be an object")
    split_ids = {str(key): tuple(str(value) for value in values) for key, values in split_ids_raw.items()}
    return MetaDistributionManifestV1(
        schema_version=str(raw["schema_version"]),
        candidate_id=str(raw["candidate_id"]),
        sources=sources,
        rows=rows,
        component_targets=dict(raw["component_targets"]),
        split_ids=split_ids,
        training_authority=bool(raw["training_authority"]),
        promotion_authority=bool(raw["promotion_authority"]),
        submission_authority=bool(raw["submission_authority"]),
        research_only=bool(raw["research_only"]),
        notes=tuple(str(note) for note in raw.get("notes", ())),
    )

