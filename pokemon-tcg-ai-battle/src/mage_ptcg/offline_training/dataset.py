"""Streaming, sharded canonical dataset with an episode-level deterministic split.

The canonical dataset is a set of immutable gzip-compressed JSONL shards whose
records are the validated ``RuleBCExample`` objects produced by collection.
Each shard is content-addressed; the dataset manifest records the split, the
train-only normalization statistics, provenance, and integrity hashes.

A separate *derived* feature cache (see :func:`build_feature_cache`) is bound to
``dataset_hash`` and ``feature_schema_hash``; it is regenerated on any mismatch
and is never the canonical dataset.
"""

from __future__ import annotations

from dataclasses import dataclass
import gzip
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from mage_ptcg.student.artifact import feature_schema
from mage_ptcg.meta_specialist.cabt_json_contract_v1 import is_ordered_selection
from mage_ptcg.student.dataset import (
    DATASET_SCHEMA_VERSION,
    DatasetValidationError,
    RuleBCExample,
    load_dataset,
)
from mage_ptcg.student.features import state_features_payload
from mage_ptcg.student.model import _action_feature_vector


DATASET_MANIFEST_SCHEMA = "offline-training-v1-dataset-v1"
SHARD_SCHEMA = "offline-training-v1-shard-v1"
SPLITS = ("train", "validation", "test")


class OfflineDatasetError(ValueError):
    """Raised when the canonical dataset cannot be built or read safely."""


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _episode_fraction(source_id: str, split_seed: int) -> float:
    digest = hashlib.sha256(f"{split_seed}:{source_id}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") / float(1 << 64)


def deterministic_episode_split(
    episode_ids: Sequence[str],
    *,
    split_seed: int,
    train_fraction: float,
    validation_fraction: float,
    test_fraction: float,
    group_key_by_episode_id: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Assign whole episodes to train/validation/test with no leakage.

    The assignment is a deterministic function of ``split_seed`` and the episode
    id.  When enough episodes exist every split receives at least one episode.

    ``group_key_by_episode_id``, when given, maps an episode id to a *split*
    group key coarser than the episode itself -- e.g. an O2 seat-swapped
    match pair -- so every episode sharing that key lands in the same split.
    Per-episode identity is unaffected; only the split assignment is grouped.
    """
    unique = sorted(set(episode_ids))
    n = len(unique)
    if n == 0:
        raise OfflineDatasetError("cannot split an empty episode set")

    def group_of(source_id: str) -> str:
        if group_key_by_episode_id is None:
            return source_id
        return group_key_by_episode_id.get(source_id, source_id)

    groups = sorted({group_of(source_id) for source_id in unique})
    g = len(groups)
    ranked_groups = sorted(groups, key=lambda group: (hashlib.sha256(f"{split_seed}:{group}".encode()).hexdigest(), group))
    n_val = round(g * validation_fraction)
    n_test = round(g * test_fraction)
    if g >= 3:
        n_val = max(1, n_val)
        n_test = max(1, n_test)
    # Guarantee a non-empty train partition (of groups).
    while n_val + n_test > g - 1 and (n_val + n_test) > 0:
        if n_test >= n_val and n_test > 0:
            n_test -= 1
        elif n_val > 0:
            n_val -= 1
        else:  # pragma: no cover - defensive
            break
    split_by_group: dict[str, str] = {}
    for index, group in enumerate(ranked_groups):
        if index < n_val:
            split_by_group[group] = "validation"
        elif index < n_val + n_test:
            split_by_group[group] = "test"
        else:
            split_by_group[group] = "train"
    return {source_id: split_by_group[group_of(source_id)] for source_id in unique}


def _decision_hash(example: RuleBCExample) -> str:
    """Compute a stable hash of the decision context (observation state and legal actions)."""
    payload = {
        "public_state": example.public_state,
        "own_private_state": example.own_private_state,
        "legal_actions": [a.get("digest") for a in example.legal_actions]
    }
    return _digest(payload)


@dataclass(frozen=True, slots=True)
class Decision:
    """One training decision with per-candidate feature rows and teacher target."""

    example_id: str
    source_id: str
    split: str
    selection_type: str
    candidate_digests: tuple[str, ...]
    candidate_features: tuple[tuple[float, ...], ...]
    target_indices: tuple[int, ...]
    min_count: int


def _example_rows(example: RuleBCExample) -> tuple[list[list[float]], list[int]]:
    try:
        ordered = is_ordered_selection(example.selection_type, example.selection_context)
    except ValueError as exc:
        raise OfflineDatasetError("dataset example has an unknown CABT selection schema") from exc
    if ordered:
        raise OfflineDatasetError(
            "candidate-wise offline training cannot represent ordered Skill labels"
        )
    state = state_features_payload(example.public_state, example.own_private_state, example.visible_history)
    rows: list[list[float]] = []
    for action in example.legal_actions:
        rows.append([*state, *_action_feature_vector(action)])
    target_digests = set(example.target_action_digests)
    targets = [index for index, action in enumerate(example.legal_actions) if action["digest"] in target_digests]
    return rows, targets


def _all_finite(rows: Sequence[Sequence[float]]) -> bool:
    return all(math.isfinite(value) for row in rows for value in row)


def build_dataset(
    *,
    source_jsonl: str | Path,
    output_dir: str | Path,
    shard_size: int,
    split_seed: int,
    train_fraction: float,
    validation_fraction: float,
    test_fraction: float,
    teacher_id: str,
    trainer_id: str,
    source_collection_hash: str,
    source_plan_hash: str = "NONE",
) -> dict[str, Any]:
    """Build the canonical sharded dataset and manifest from a collection JSONL."""
    destination = Path(output_dir)
    if destination.exists() and any(destination.iterdir()):
        raise OfflineDatasetError("dataset output directory must be new or empty")
    destination.mkdir(parents=True, exist_ok=True)

    examples = load_dataset(source_jsonl)  # re-validates schema + privacy contract

    # Duplicate and conflict handling keyed on episode + decision identity.
    by_identity: dict[tuple[str, str], RuleBCExample] = {}
    conflicting: set[tuple[str, str]] = set()
    for example in examples:
        identity = (example.source_id, example.metadata.get("decision_index", ""))
        prior = by_identity.get(identity)
        if prior is None:
            by_identity[identity] = example
        elif prior.example_id != example.example_id:
            conflicting.add(identity)
    quarantined = sorted(conflicting)
    clean = [
        example
        for identity, example in by_identity.items()
        if identity not in conflicting
    ]
    if not clean:
        raise OfflineDatasetError("no records remain after duplicate/conflict quarantine")

    # Reject non-finite / structurally invalid candidate encodings early.
    for example in clean:
        rows, _targets = _example_rows(example)
        if not rows or not _all_finite(rows):
            raise OfflineDatasetError("dataset record has empty or non-finite candidate features")

    episode_ids = sorted({example.source_id for example in clean})
    # O2 seat-swapped match pairs must stay in the same split; group by
    # o2_pair_id (private metadata) when present, otherwise per-episode
    # grouping is unchanged (legacy self-play is unaffected).
    group_key_by_episode_id: dict[str, str] = {}
    for example in clean:
        pair_id = example.metadata.get("o2_pair_id")
        if pair_id:
            group_key_by_episode_id[example.source_id] = f"o2-pair:{pair_id}"
    assignment = deterministic_episode_split(
        episode_ids,
        split_seed=split_seed,
        train_fraction=train_fraction,
        validation_fraction=validation_fraction,
        test_fraction=test_fraction,
        group_key_by_episode_id=group_key_by_episode_id or None,
    )

    # Detect and quarantine decision-level duplicates that crossed splits.
    hash_to_splits: dict[str, set[str]] = {}
    for example in clean:
        d_hash = _decision_hash(example)
        split = assignment[example.source_id]
        hash_to_splits.setdefault(d_hash, set()).add(split)

    leakage_hashes = {h for h, s in hash_to_splits.items() if len(s) > 1}
    if leakage_hashes:
        leakage_conflicts = set()
        clean_filtered = []
        for example in clean:
            d_hash = _decision_hash(example)
            if d_hash in leakage_hashes:
                identity = (example.source_id, example.metadata.get("decision_index", ""))
                leakage_conflicts.add(identity)
            else:
                clean_filtered.append(example)
        clean = clean_filtered
        conflicting.update(leakage_conflicts)
        quarantined = sorted(conflicting)

        if not clean:
            raise OfflineDatasetError("no records remain after duplicate/conflict and split-leakage quarantine")

    # Stable record ordering: by (source_id, decision_index, example_id).
    def _sort_key(example: RuleBCExample) -> tuple[str, int, str]:
        try:
            decision_index = int(example.metadata.get("decision_index", "0"))
        except (TypeError, ValueError):
            decision_index = 0
        return (example.source_id, decision_index, example.example_id)

    ordered = sorted(clean, key=_sort_key)

    # Write shards.
    shards_meta: list[dict[str, Any]] = []
    shard_records: list[RuleBCExample] = []
    shard_index = 0

    def flush_shard() -> None:
        nonlocal shard_index, shard_records
        if not shard_records:
            return
        name = f"shard-{shard_index:05d}.jsonl.gz"
        path = destination / name
        payload = "".join(_canonical_json(example.to_dict()) + "\n" for example in shard_records)
        raw = payload.encode("utf-8")
        with path.open("wb") as handle:
            with gzip.GzipFile(filename="", mode="wb", fileobj=handle, mtime=0) as gz:
                gz.write(raw)
        digests = [example.source_id for example in shard_records]
        meta = {
            "schema": SHARD_SCHEMA,
            "name": name,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "record_count": len(shard_records),
            "episode_count": len(set(digests)),
            "decision_count": len(shard_records),
            "candidate_count": sum(len(example.legal_actions) for example in shard_records),
            "min_episode_id": min(digests),
            "max_episode_id": max(digests),
            "source_game_hashes": sorted(set(digests)),
        }
        shards_meta.append(meta)
        shard_index += 1
        shard_records = []

    for example in ordered:
        shard_records.append(example)
        if len(shard_records) >= shard_size:
            flush_shard()
    flush_shard()

    # Train-only normalization statistics over candidate feature rows.
    train_examples = [example for example in ordered if assignment[example.source_id] == "train"]
    if not train_examples:
        raise OfflineDatasetError("episode split produced an empty train partition")
    normalization = _train_normalization(train_examples)

    dataset_hash = _digest([example.to_dict() for example in ordered])
    schema = feature_schema()
    split_counts = {name: 0 for name in SPLITS}
    split_episode_ids: dict[str, list[str]] = {name: [] for name in SPLITS}
    for source_id in episode_ids:
        split_episode_ids[assignment[source_id]].append(source_id)
    for example in ordered:
        split_counts[assignment[example.source_id]] += 1

    # Leakage assertion: an episode belongs to exactly one split.
    seen: dict[str, str] = {}
    for split_name in SPLITS:
        for source_id in split_episode_ids[split_name]:
            if source_id in seen:
                raise OfflineDatasetError("episode leakage across splits")
            seen[source_id] = split_name

    manifest: dict[str, Any] = {
        "schema_version": DATASET_MANIFEST_SCHEMA,
        "dataset_schema_version": DATASET_SCHEMA_VERSION,
        "dataset_hash": dataset_hash,
        "feature_schema_version": schema["feature_schema_version"],
        "feature_schema_hash": schema["feature_schema_hash"],
        "feature_dimension": schema["feature_dimension"],
        "shards": shards_meta,
        "shard_count": len(shards_meta),
        "record_count": len(ordered),
        "episode_count": len(episode_ids),
        "candidate_count": sum(len(example.legal_actions) for example in ordered),
        "split_seed": split_seed,
        "split_fractions": {
            "train": train_fraction, "validation": validation_fraction, "test": test_fraction,
        },
        "split_assignment": dict(sorted(assignment.items())),
        "split_decision_counts": split_counts,
        "split_episode_counts": {name: len(split_episode_ids[name]) for name in SPLITS},
        "normalization": normalization,
        "duplicate_conflict_count": len(quarantined),
        "quarantined_identities": [list(item) for item in quarantined],
        "teacher_id": teacher_id,
        "trainer_id": trainer_id,
        "source_collection_hash": source_collection_hash,
        "source_plan_hash": source_plan_hash,
    }
    manifest["manifest_hash"] = _digest({k: v for k, v in manifest.items() if k != "manifest_hash"})
    (destination / "dataset_manifest.json").write_text(_canonical_json(manifest) + "\n", encoding="utf-8")
    return manifest


def _train_normalization(examples: Sequence[RuleBCExample]) -> dict[str, Any]:
    dimension = None
    count = 0
    sums: list[float] = []
    sq_sums: list[float] = []
    for example in examples:
        rows, _targets = _example_rows(example)
        for row in rows:
            if dimension is None:
                dimension = len(row)
                sums = [0.0] * dimension
                sq_sums = [0.0] * dimension
            for index, value in enumerate(row):
                sums[index] += value
                sq_sums[index] += value * value
            count += 1
    if dimension is None or count == 0:
        raise OfflineDatasetError("cannot compute normalization from empty train data")
    means = [total / count for total in sums]
    stds = []
    for index in range(dimension):
        variance = max(0.0, sq_sums[index] / count - means[index] ** 2)
        stds.append(math.sqrt(variance) if variance > 1e-12 else 1.0)
    return {"mean": means, "std": stds, "count": count, "dimension": dimension}


def load_manifest(dataset_dir: str | Path) -> dict[str, Any]:
    path = Path(dataset_dir) / "dataset_manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(manifest, Mapping):
        raise OfflineDatasetError("dataset manifest is corrupt")
    return dict(manifest)


def verify_shards(dataset_dir: str | Path) -> None:
    """Fail closed if any shard's on-disk bytes disagree with its recorded hash."""
    dataset_dir = Path(dataset_dir)
    manifest = load_manifest(dataset_dir)
    for shard in manifest["shards"]:
        path = dataset_dir / shard["name"]
        if not path.is_file():
            raise OfflineDatasetError(f"missing shard {shard['name']}")
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != shard["sha256"]:
            raise OfflineDatasetError(f"corrupt shard {shard['name']}")


def _iter_shard_examples(dataset_dir: Path, shard_name: str) -> Iterator[RuleBCExample]:
    path = dataset_dir / shard_name
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield RuleBCExample.from_dict(json.loads(line))


def iter_examples(dataset_dir: str | Path, split: str) -> Iterator[RuleBCExample]:
    """Stream the raw ``RuleBCExample`` records belonging to one split."""
    if split not in SPLITS:
        raise OfflineDatasetError(f"unknown split {split!r}")
    dataset_dir = Path(dataset_dir)
    manifest = load_manifest(dataset_dir)
    assignment = manifest["split_assignment"]
    for shard in manifest["shards"]:
        for example in _iter_shard_examples(dataset_dir, shard["name"]):
            if assignment.get(example.source_id) == split:
                yield example


def iter_decisions(dataset_dir: str | Path, split: str) -> Iterator[Decision]:
    """Stream decisions for one split without loading the whole dataset into RAM."""
    if split not in SPLITS:
        raise OfflineDatasetError(f"unknown split {split!r}")
    dataset_dir = Path(dataset_dir)
    manifest = load_manifest(dataset_dir)
    assignment = manifest["split_assignment"]
    for shard in manifest["shards"]:
        for example in _iter_shard_examples(dataset_dir, shard["name"]):
            if assignment.get(example.source_id) != split:
                continue
            rows, targets = _example_rows(example)
            yield Decision(
                example_id=example.example_id,
                source_id=example.source_id,
                split=split,
                selection_type=str(example.selection_type),
                candidate_digests=tuple(action["digest"] for action in example.legal_actions),
                candidate_features=tuple(tuple(row) for row in rows),
                target_indices=tuple(targets),
                min_count=example.min_count,
            )


__all__ = [
    "DATASET_MANIFEST_SCHEMA",
    "SHARD_SCHEMA",
    "SPLITS",
    "Decision",
    "OfflineDatasetError",
    "build_dataset",
    "deterministic_episode_split",
    "iter_decisions",
    "iter_examples",
    "load_manifest",
    "verify_shards",
    "load_feature_cache",
    "save_feature_cache",
]


def _compute_normalization_hash(normalization: dict[str, Any]) -> str:
    return _digest({"mean": normalization.get("mean"), "std": normalization.get("std")})


def _get_cache_paths(dataset_dir: Path, split: str, cache_key: str) -> tuple[Path, Path, Path]:
    cache_base = dataset_dir / ".derived_cache" / split / cache_key
    manifest_path = cache_base / "cache_manifest.json"
    npz_path = cache_base / "data.npz"
    meta_path = cache_base / "metadata.json"
    return manifest_path, npz_path, meta_path


def load_feature_cache(dataset_dir: str | Path, split: str) -> list[Decision] | None:
    """Load pre-computed Decision cache to bypass parsing raw JSONL shards."""
    try:
        import numpy as np
    except ImportError:
        return None

    dataset_dir = Path(dataset_dir)
    try:
        manifest = load_manifest(dataset_dir)
    except Exception:
        return None

    shards_sha256 = sorted([s["sha256"] for s in manifest.get("shards", [])])
    normalization = manifest.get("normalization", {})
    norm_hash = _compute_normalization_hash(normalization)
    dataset_hash = manifest.get("dataset_hash")
    feature_schema_version = manifest.get("feature_schema_version")
    feature_schema_hash = manifest.get("feature_schema_hash")

    stable = {
        "dataset_hash": dataset_hash,
        "feature_schema_version": feature_schema_version,
        "feature_schema_hash": feature_schema_hash,
        "normalization_hash": norm_hash,
        "split": split,
        "shards_sha256": shards_sha256,
    }
    cache_key = _digest(stable)
    manifest_path, npz_path, meta_path = _get_cache_paths(dataset_dir, split, cache_key)

    if not manifest_path.is_file() or not npz_path.is_file() or not meta_path.is_file():
        return None

    try:
        cache_meta = json.loads(manifest_path.read_text(encoding="utf-8"))
        if cache_meta.get("cache_key") != cache_key:
            return None
        if cache_meta.get("schema_version") != "offline-training-v1-cache-v1":
            return None

        with np.load(npz_path, allow_pickle=False) as data:
            features = data["features"]
            lengths = data["lengths"]
            targets = data["targets"]
            target_lengths = data["target_lengths"]

        meta_list = json.loads(meta_path.read_text(encoding="utf-8"))
        if len(lengths) != len(meta_list):
            return None

        decisions = []
        feat_offset = 0
        target_offset = 0
        for i, m in enumerate(meta_list):
            fl = int(lengths[i])
            tl = int(target_lengths[i])

            cand_features = tuple(tuple(float(v) for v in row) for row in features[feat_offset : feat_offset + fl])
            target_idx = tuple(int(v) for v in targets[target_offset : target_offset + tl])

            feat_offset += fl
            target_offset += tl

            decisions.append(
                Decision(
                    example_id=m["example_id"],
                    source_id=m["source_id"],
                    split=split,
                    selection_type=m["selection_type"],
                    candidate_digests=tuple(m["candidate_digests"]),
                    candidate_features=cand_features,
                    target_indices=target_idx,
                    min_count=int(m["min_count"]),
                )
            )
        return decisions
    except Exception:
        return None


def save_feature_cache(dataset_dir: str | Path, split: str, decisions: list[Decision]) -> None:
    """Serialize and save Decision list into a fast atomic cache directory."""
    try:
        import numpy as np
    except ImportError:
        return

    import shutil
    import tempfile
    import time

    dataset_dir = Path(dataset_dir)
    try:
        manifest = load_manifest(dataset_dir)
    except Exception:
        return

    shards_sha256 = sorted([s["sha256"] for s in manifest.get("shards", [])])
    normalization = manifest.get("normalization", {})
    norm_hash = _compute_normalization_hash(normalization)
    dataset_hash = manifest.get("dataset_hash")
    feature_schema_version = manifest.get("feature_schema_version")
    feature_schema_hash = manifest.get("feature_schema_hash")

    stable = {
        "dataset_hash": dataset_hash,
        "feature_schema_version": feature_schema_version,
        "feature_schema_hash": feature_schema_hash,
        "normalization_hash": norm_hash,
        "split": split,
        "shards_sha256": shards_sha256,
    }
    cache_key = _digest(stable)
    manifest_path, npz_path, meta_path = _get_cache_paths(dataset_dir, split, cache_key)

    temp_dir_path = tempfile.mkdtemp(dir=str(dataset_dir))
    temp_dir = Path(temp_dir_path)
    try:
        features_list = []
        lengths = []
        targets_list = []
        target_lengths = []
        meta_list = []

        for d in decisions:
            features_list.extend(d.candidate_features)
            lengths.append(len(d.candidate_features))
            targets_list.extend(d.target_indices)
            target_lengths.append(len(d.target_indices))
            meta_list.append({
                "example_id": d.example_id,
                "source_id": d.source_id,
                "selection_type": d.selection_type,
                "candidate_digests": list(d.candidate_digests),
                "min_count": d.min_count,
            })

        features_arr = np.array(features_list, dtype=np.float32)
        lengths_arr = np.array(lengths, dtype=np.int32)
        targets_arr = np.array(targets_list, dtype=np.int32)
        target_lengths_arr = np.array(target_lengths, dtype=np.int32)

        temp_npz = temp_dir / "data.npz"
        np.savez_compressed(
            temp_npz,
            features=features_arr,
            lengths=lengths_arr,
            targets=targets_arr,
            target_lengths=target_lengths_arr
        )

        temp_meta = temp_dir / "metadata.json"
        temp_meta.write_text(json.dumps(meta_list, sort_keys=True, separators=(",", ":")), encoding="utf-8")

        cache_manifest = {
            "schema_version": "offline-training-v1-cache-v1",
            "cache_key": cache_key,
            "record_count": len(decisions),
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        temp_manifest = temp_dir / "cache_manifest.json"
        temp_manifest.write_text(json.dumps(cache_manifest, sort_keys=True, indent=2), encoding="utf-8")

        dest_dir = manifest_path.parent
        if dest_dir.exists():
            shutil.rmtree(dest_dir)
        dest_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(temp_dir), str(dest_dir))
    except Exception:
        if temp_dir.exists():
            shutil.rmtree(temp_dir)
