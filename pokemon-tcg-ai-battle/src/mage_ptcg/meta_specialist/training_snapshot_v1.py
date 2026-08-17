"""Immutable, leakage-safe L1 training snapshot published from sealed envelopes only.

The snapshot is the single input the L3 learner is allowed to read.  It is built
by consuming :class:`TrainingExampleEnvelopeV2` objects, which have already
recomputed and revalidated their canonical semantic loss rows, so this module
never reopens the dataset path and never touches a raw local record.

Splits are assigned over connected ``episode_id_hash``/``near_duplicate_id``
components using the same implementation as the raw-record planner, so a
component can never straddle train, development, and test.  Positions that recur
across a large share of the episodes are exempt from near-duplicate linking:
they are constants of the task rather than leaks, and linking on them collapsed
half of a real corpus into one component.  See
:func:`~mage_ptcg.meta_specialist.local_dataset_v2.ubiquitous_near_duplicate_ids_v2`.

The design's §9.3 duplicate cap is applied here, where a position's multiplicity
across the whole corpus is first known: copies beyond
``MAX_NEAR_DUPLICATE_MULTIPLICITY_V1`` are down-weighted, never dropped.  Each
example keeps its ``pre_cap_quality_weight`` so the cap is verifiable from the
snapshot alone rather than trusted.
"""

from __future__ import annotations

from concurrent.futures import (
    FIRST_COMPLETED,
    ProcessPoolExecutor,
    as_completed,
    wait as futures_wait,
)
import copy
import hashlib
import math
import multiprocessing
import os
from pathlib import Path
import sys
import tempfile
from typing import Any, Mapping, Sequence

from mage_ptcg.meta_specialist.actor_visible_features_v1 import CardVocabularyV1
from mage_ptcg.meta_specialist.local_dataset_v2 import (
    LocalDatasetV2Error,
    assign_grouped_splits_from_keys_v2,
    canonical_json_bytes_v2,
    near_duplicate_ubiquity_threshold_v2,
    parse_canonical_json_bytes_v2,
    ubiquitous_near_duplicate_ids_v2,
)
from mage_ptcg.meta_specialist.training_example_envelope_v2 import (
    MAX_TRAINING_DATASET_SNAPSHOT_BYTES_V2,
    iter_training_example_envelopes_v2,
    reissue_sealed_envelope_v2,
    reject_forbidden_private_fields_v2,
    require_training_example_envelope_v2,
    sealed_envelope_bytes_v2,
)


TRAINING_SNAPSHOT_SCHEMA_V1 = "specialist-training-snapshot-v1"
# A shard is one part of a corpus, not a small corpus.  Its own schema keeps it
# from being read as a standalone snapshot whose corpus-wide checks were skipped;
# see `validate_training_snapshot_v1` and `read_sharded_split_examples_v1`.
TRAINING_SHARD_SCHEMA_V1 = "specialist-training-snapshot-shard-v1"
DEFAULT_SPLIT_NAMES_V1 = ("train", "development", "test")
# Share of grouped components each split receives.  Equal thirds spent two of
# every three collected games on held-out sets: a 300-game teacher corpus costs
# 10-20 minutes of simulation, and θ0 is a behaviour-cloning fit whose quality
# scales with the training half.  The held-out sets only have to size a
# generalisation estimate, which 15% of a few hundred episodes already does.
DEFAULT_SPLIT_WEIGHTS_V1 = (0.70, 0.15, 0.15)
MAX_TRAINING_SNAPSHOT_EXAMPLES_V1 = 1_000_000
MAX_TRAINING_SNAPSHOT_BYTES_V1 = MAX_TRAINING_DATASET_SNAPSHOT_BYTES_V2

# Design §9.3 requires a duplicate cap alongside the matchup cap, so that no one
# thing occupies the dataset.  Byte-identical (model_input, loss_rows) pairs push
# the same gradient direction, so beyond a few copies they only rescale the
# learning rate for that one position.  Measured on a 300-game teacher corpus the
# opening decision occurred 150 times identically, i.e. 150x the influence of any
# position that occurred once.  Copies are down-weighted rather than dropped,
# because §9.3 also requires every valid teacher decision to stay a policy-target
# candidate.  Outcome-derived `value_target` still differs between the copies, so
# scaling uniformly preserves their win/loss ratio.
MAX_NEAR_DUPLICATE_MULTIPLICITY_V1 = 8

_SNAPSHOT_ID_DOMAIN_V1 = "mage_ptcg:specialist-training-snapshot-id:v1"
# A corpus too large for one exact file is stored as ordered chunks; these name
# the whole from the ordered per-chunk identities.  Separate domains so a dataset
# digest can never be mistaken for a manifest digest.
_CORPUS_DIGEST_DOMAIN_V1 = "mage_ptcg:specialist-training-corpus-dataset:v1"
_CORPUS_MANIFEST_ID_DOMAIN_V1 = "mage_ptcg:specialist-training-corpus-manifest-id:v1"
_CORPUS_MANIFEST_HASH_DOMAIN_V1 = "mage_ptcg:specialist-training-corpus-manifest-hash:v1"
_SNAPSHOT_CONTENT_DOMAIN_V1 = "mage_ptcg:specialist-training-snapshot-content:v1"
_EXAMPLE_KEYS_V1 = frozenset({
    "record_id", "episode_id_hash", "near_duplicate_id", "record_content_hash",
    "split", "model_input", "loss_rows", "value_target", "example_quality_weight",
    "pre_cap_quality_weight",
})
_SNAPSHOT_KEYS_V1 = frozenset({
    "schema_version", "snapshot_id", "content_hash", "dataset_snapshot_sha256",
    "manifest_id", "manifest_content_hash", "vocabulary_source_sha256",
    "vocabulary_environment_version", "feature_domain", "feature_schema_hash",
    "qualification_time_utc", "split_names", "split_weights", "split_counts",
    "source_artifacts", "permissions", "duplicate_cap", "examples",
})
_DUPLICATE_CAP_KEYS_V1 = frozenset({
    "max_near_duplicate_multiplicity", "ubiquity_min_episodes",
    "ubiquitous_near_duplicate_ids", "groups_capped", "records_capped",
})
_SOURCE_KEYS_V1 = frozenset({"kind", "artifact_sha256"})
_PERMISSION_KEYS_V1 = frozenset({
    "permission_manifest_id", "permission_content_hash", "permission_trusted_bytes_sha256",
})


class TrainingSnapshotV1Error(ValueError):
    """Raised when a training snapshot cannot be built or verified."""


# `canonical_json_bytes_v2` defaults to the tight *untrusted single-decision*
# node bound (100k nodes).  A snapshot is neither untrusted nor a single
# decision: it is this module's own already-validated aggregate, and the module
# declares a ceiling of `MAX_TRAINING_SNAPSHOT_EXAMPLES_V1` (1,000,000)
# examples.  Hashing it under the single-decision bound made the module unable
# to seal even a few hundred examples -- an internal contradiction between the
# declared ceiling and the identity function, not a deliberate limit.  Deriving
# the bound from the declared ceiling keeps the two from drifting apart again.
_MAX_SNAPSHOT_JSON_NODES_V1 = MAX_TRAINING_SNAPSHOT_EXAMPLES_V1 * 64


def _hash(domain: str, value: object) -> str:
    return hashlib.sha256(
        domain.encode("utf-8")
        + b"\0"
        + canonical_json_bytes_v2(
            value,
            max_nodes=_MAX_SNAPSHOT_JSON_NODES_V1,
            max_bytes=MAX_TRAINING_SNAPSHOT_BYTES_V1,
        )
    ).hexdigest()


def _snapshot_identity(payload: Mapping[str, Any]) -> str:
    identity = {
        key: value for key, value in payload.items()
        if key not in {"snapshot_id", "content_hash"}
    }
    return _hash(_SNAPSHOT_ID_DOMAIN_V1, identity)


def _snapshot_content_hash(payload: Mapping[str, Any]) -> str:
    content = {key: value for key, value in payload.items() if key != "content_hash"}
    return _hash(_SNAPSHOT_CONTENT_DOMAIN_V1, content)


def build_training_snapshot_v1(
    dataset_path: str | Path,
    *,
    manifest: dict[str, object],
    vocabulary: CardVocabularyV1,
    trusted_permissions: Mapping[str, Any],
    qualification_time_utc: str,
    split_names: tuple[str, ...] = DEFAULT_SPLIT_NAMES_V1,
    split_weights: tuple[float, ...] = DEFAULT_SPLIT_WEIGHTS_V1,
) -> dict[str, object]:
    """Build one immutable snapshot from the eligible sealed envelopes of a dataset."""
    if type(vocabulary) is not CardVocabularyV1:
        raise TrainingSnapshotV1Error("vocabulary must be a sealed CardVocabularyV1")
    if type(split_names) is not tuple or len(split_names) < 2:
        raise TrainingSnapshotV1Error("split_names must be at least two names")
    # Fail closed rather than silently falling back to equal shares: a caller that
    # renames the splits must say what share each of its own names should get.
    if type(split_weights) is not tuple or len(split_weights) != len(split_names):
        raise TrainingSnapshotV1Error("split_weights must give one weight per split name")

    examples: list[dict[str, object]] = []
    grouping_keys: list[tuple[str, str, str]] = []
    seen_record_ids: set[str] = set()
    dataset_hashes: set[str] = set()
    manifest_ids: set[tuple[str, str]] = set()
    feature_identities: set[tuple[str, str]] = set()
    source_artifacts: set[tuple[str, str]] = set()
    permissions: set[tuple[str, str, str]] = set()

    for envelope in iter_training_example_envelopes_v2(
        dataset_path,
        manifest=manifest,
        vocabulary=vocabulary,
        trusted_permissions=trusted_permissions,
        qualification_time_utc=qualification_time_utc,
    ):
        require_training_example_envelope_v2(envelope)
        # ``to_dict`` revalidates the sealed payload, including that every loss
        # row's target masses sum to one over a model-bound, sorted token domain.
        payload = envelope.to_dict()
        if len(examples) >= MAX_TRAINING_SNAPSHOT_EXAMPLES_V1:
            raise TrainingSnapshotV1Error("training snapshot exceeds the example cap")

        record_id = payload["record_id"]
        if record_id in seen_record_ids:
            raise TrainingSnapshotV1Error("training snapshot received a duplicate record_id")
        seen_record_ids.add(record_id)

        model_input = payload["model_input"]
        if type(model_input) is not dict:
            raise TrainingSnapshotV1Error("sealed envelope model_input is malformed")
        feature_identities.add(
            (str(model_input["feature_domain"]), str(model_input["feature_schema_hash"]))
        )
        dataset_hashes.add(payload["dataset_snapshot_sha256"])
        manifest_ids.add((payload["manifest_id"], payload["manifest_content_hash"]))
        source_artifacts.add((payload["source_kind"], payload["source_artifact_sha256"]))
        permissions.add((
            payload["permission_manifest_id"],
            payload["permission_content_hash"],
            payload["permission_trusted_bytes_sha256"],
        ))

        example = {
            "record_id": record_id,
            "episode_id_hash": payload["episode_id_hash"],
            "near_duplicate_id": payload["near_duplicate_id"],
            "record_content_hash": payload["record_content_hash"],
            "split": "",
            "model_input": model_input,
            "loss_rows": payload["loss_rows"],
            "value_target": payload["value_target"],
            # Set together below, once every example's near-duplicate multiplicity
            # is known: the cap is a property of the corpus, not of one record.
            "pre_cap_quality_weight": payload["example_quality_weight"],
            "example_quality_weight": payload["example_quality_weight"],
        }
        reject_forbidden_private_fields_v2(example)
        examples.append(example)
        grouping_keys.append(
            (record_id, payload["episode_id_hash"], payload["near_duplicate_id"])
        )

    if not examples:
        raise TrainingSnapshotV1Error("training snapshot needs at least one eligible example")
    if len(dataset_hashes) != 1 or len(manifest_ids) != 1 or len(feature_identities) != 1:
        raise TrainingSnapshotV1Error(
            "training snapshot examples must share one dataset, manifest, and feature identity"
        )

    try:
        assignment = assign_grouped_splits_from_keys_v2(
            tuple(grouping_keys), split_names=split_names, split_weights=split_weights
        )
    except LocalDatasetV2Error as exc:
        raise TrainingSnapshotV1Error(f"grouped split assignment failed: {exc}") from exc
    for example in examples:
        example["split"] = assignment[example["record_id"]]

    duplicate_cap = _apply_duplicate_cap_v1(examples)

    examples.sort(key=lambda item: item["record_id"])
    feature_domain, feature_schema_hash = next(iter(feature_identities))
    manifest_id, manifest_content_hash = next(iter(manifest_ids))
    payload = {
        "schema_version": TRAINING_SNAPSHOT_SCHEMA_V1,
        "snapshot_id": "",
        "content_hash": "",
        "dataset_snapshot_sha256": next(iter(dataset_hashes)),
        "manifest_id": manifest_id,
        "manifest_content_hash": manifest_content_hash,
        "vocabulary_source_sha256": vocabulary.source_sha256,
        "vocabulary_environment_version": vocabulary.environment_version,
        "feature_domain": feature_domain,
        "feature_schema_hash": feature_schema_hash,
        "qualification_time_utc": qualification_time_utc,
        "split_names": list(split_names),
        "split_weights": list(split_weights),
        "split_counts": {
            name: sum(1 for item in examples if item["split"] == name)
            for name in split_names
        },
        "source_artifacts": [
            {"kind": kind, "artifact_sha256": artifact}
            for kind, artifact in sorted(source_artifacts)
        ],
        "permissions": [
            {
                "permission_manifest_id": manifest_ref,
                "permission_content_hash": content,
                "permission_trusted_bytes_sha256": trusted,
            }
            for manifest_ref, content, trusted in sorted(permissions)
        ],
        "duplicate_cap": duplicate_cap,
        "examples": examples,
    }
    payload["snapshot_id"] = _snapshot_identity(payload)
    payload["content_hash"] = _snapshot_content_hash(payload)
    return validate_training_snapshot_v1(payload)


def _duplicate_scale_v1(multiplicity: int) -> float:
    """Factor applied to every copy of a position occurring ``multiplicity`` times."""
    if multiplicity <= MAX_NEAR_DUPLICATE_MULTIPLICITY_V1:
        return 1.0
    return MAX_NEAR_DUPLICATE_MULTIPLICITY_V1 / multiplicity


def _duplicate_cap_facts_v1(
    examples: list[dict[str, Any]],
) -> tuple[dict[str, int], int, frozenset[str]]:
    """Multiplicity per position, the ubiquity threshold, and the ubiquitous keys.

    Derived from the examples themselves so that both the builder and the
    validator reach the same answer without either trusting a declared field.
    """
    multiplicity: dict[str, int] = {}
    keys: list[tuple[str, str, str]] = []
    for example in examples:
        near_duplicate = example["near_duplicate_id"]
        multiplicity[near_duplicate] = multiplicity.get(near_duplicate, 0) + 1
        keys.append((example["record_id"], example["episode_id_hash"], near_duplicate))
    episodes = {episode for _record, episode, _near in keys}
    threshold = near_duplicate_ubiquity_threshold_v2(len(episodes))
    return multiplicity, threshold, ubiquitous_near_duplicate_ids_v2(tuple(keys))


def _apply_duplicate_cap_v1(examples: list[dict[str, Any]]) -> dict[str, object]:
    """Scale identical positions down to the §9.3 duplicate cap, in place."""
    multiplicity, threshold, ubiquitous = _duplicate_cap_facts_v1(examples)
    groups_capped = sum(
        1 for count in multiplicity.values() if count > MAX_NEAR_DUPLICATE_MULTIPLICITY_V1
    )
    records_capped = 0
    for example in examples:
        scale = _duplicate_scale_v1(multiplicity[example["near_duplicate_id"]])
        if scale != 1.0:
            records_capped += 1
        example["example_quality_weight"] = example["pre_cap_quality_weight"] * scale
    return {
        "max_near_duplicate_multiplicity": MAX_NEAR_DUPLICATE_MULTIPLICITY_V1,
        "ubiquity_min_episodes": threshold,
        "ubiquitous_near_duplicate_ids": sorted(ubiquitous),
        "groups_capped": groups_capped,
        "records_capped": records_capped,
    }



SHARD_INDEX_SCHEMA_V1 = "specialist-training-snapshot-index-v1"


def _corpus_digest_v1(domain: str, values: Sequence[str]) -> str:
    """Digest an ordered list of per-chunk identities into one corpus identity."""
    if isinstance(values, (str, bytes)) or not values:
        raise TrainingSnapshotV1Error("a corpus needs at least one dataset chunk")
    digest = hashlib.sha256()
    digest.update(domain.encode("ascii"))
    for value in values:
        if type(value) is not str or len(value) != 64:
            raise TrainingSnapshotV1Error("corpus chunk identities must be 64-hex strings")
        digest.update(b"\0")
        digest.update(value.encode("ascii"))
    return digest.hexdigest()


def corpus_dataset_sha256_v1(chunk_sha256s: Sequence[str]) -> str:
    """One identity for a corpus stored as an ordered sequence of exact files.

    A corpus larger than :data:`MAX_TRAINING_DATASET_SNAPSHOT_BYTES_V2` cannot be
    one file, and ``read_exact_regular_file`` holds whatever it reads in memory,
    so the corpus is stored as several chunks.  Each chunk keeps its own exact
    identity and its own manifest; this digest names the ordered whole.

    It is deliberately *not* equal to the single chunk's hash when there is one
    chunk.  A reader must never have to guess whether the field holds a file hash
    or a digest over hashes -- the shard index lists the chunks either way.
    """
    return _corpus_digest_v1(_CORPUS_DIGEST_DOMAIN_V1, chunk_sha256s)


def build_sharded_training_snapshots_v1(
    dataset_path: str | Path,
    *,
    manifest: dict[str, object],
    vocabulary: CardVocabularyV1,
    trusted_permissions: Mapping[str, Any],
    qualification_time_utc: str,
    output_dir: str | Path,
    shard_max_examples: int,
    split_names: tuple[str, ...] = DEFAULT_SPLIT_NAMES_V1,
    split_weights: tuple[float, ...] = DEFAULT_SPLIT_WEIGHTS_V1,
    on_progress=None,
) -> dict[str, object]:
    """Seal a single-file corpus.  See the chunked form for the invariants."""
    return build_sharded_training_snapshots_from_chunks_v1(
        ((Path(dataset_path), manifest),),
        vocabulary=vocabulary, trusted_permissions=trusted_permissions,
        qualification_time_utc=qualification_time_utc, output_dir=output_dir,
        shard_max_examples=shard_max_examples, split_names=split_names,
        split_weights=split_weights, on_progress=on_progress,
    )


def build_sharded_training_snapshots_from_chunks_v1(
    chunks: Sequence[tuple[Path, dict[str, object]]],
    *,
    vocabulary: CardVocabularyV1,
    trusted_permissions: Mapping[str, Any],
    qualification_time_utc: str,
    output_dir: str | Path,
    shard_max_examples: int,
    split_names: tuple[str, ...] = DEFAULT_SPLIT_NAMES_V1,
    split_weights: tuple[float, ...] = DEFAULT_SPLIT_WEIGHTS_V1,
    on_progress=None,
) -> dict[str, object]:
    """Seal one corpus as several snapshots that together carry every example.

    ## なぜ切り詰めではなく分割か

    上限に収まる先頭 N 局だけを封印すると、`opponent = index % n` と
    `seat = index % 2` の巡回により、残した範囲の相手構成・座席構成が
    「たまたま切れた位置」に依存する。捨てた分は二度と使われないうえ、何を
    捨てたのかが dataset から追えない。全例を保持し、束ねる単位だけを分ける。

    ## split は shard ごとではなく corpus 全体で決める

    shard ごとに split を決めると、同じ episode の決定が shard A の train と
    shard B の test へ分かれうる。leakage を防ぐための grouped split が shard
    境界で破れては意味がない。したがって:

    1. pass 1 で全 envelope の grouping key だけを読み、**全体で** split を
       割り当て、重複 multiplicity を数える
    2. pass 2 で実際の example を作り、pass 1 が決めた split と weight を付けて
       shard へ流す

    pass 1 は hash 3 個しか保持しないので、corpus が何 GB でもメモリは有界である。

    ## corpus が複数 chunk に分かれる理由

    ``read_exact_regular_file`` は読んだ file 全体を bytes として保持するため、
    1 file の corpus は :data:`MAX_TRAINING_DATASET_SNAPSHOT_BYTES_V2` を超えられ
    ない。実測 (t1-alakazam, 3000 局) の corpus は 8.6 GiB でこれを超える。そこで
    corpus を順序付き chunk 列として持ち、**chunk ごとに** exact file identity と
    自分の manifest を持たせる。chunk 単位の検証は一切弱めていない。

    split と重複 cap は chunk 境界ではなく corpus 全体で決める。したがって chunk の
    切り方は学習内容に影響しない。

    返り値は shard を列挙した index であり、`snapshot_index.json` として書かれる。
    """
    if type(shard_max_examples) is not int or shard_max_examples < 1:
        raise TrainingSnapshotV1Error("shard_max_examples must be a positive int")
    ordered_chunks = tuple(chunks)
    if not ordered_chunks:
        raise TrainingSnapshotV1Error("a corpus needs at least one dataset chunk")

    spool_dir = Path(tempfile.mkdtemp(prefix="specialist-corpus-spool-"))
    try:
        results = [
            _derive_chunk_envelopes_v1(
                chunk_path, chunk_manifest,
                spool_path=spool_dir / f"chunk-{position:04d}.jsonl",
                vocabulary=vocabulary, trusted_permissions=trusted_permissions,
                qualification_time_utc=qualification_time_utc,
            )
            for position, (chunk_path, chunk_manifest) in enumerate(ordered_chunks)
        ]
        return _assemble_sharded_corpus_v1(
            results, chunk_paths=[path for path, _manifest in ordered_chunks],
            vocabulary=vocabulary, qualification_time_utc=qualification_time_utc,
            output_dir=output_dir, shard_max_examples=shard_max_examples,
            split_names=split_names, split_weights=split_weights, on_progress=on_progress,
        )
    finally:
        _remove_spool_dir_v1(spool_dir)


def _derive_chunk_in_worker_v1(payload: dict) -> dict[str, object]:
    """Derive one chunk in a worker process, rebuilding its inputs from data.

    The vocabulary and the trusted permission set are rebuilt here rather than
    sent across, because both are capability objects whose issuance registries do
    not survive pickling.  Everything this receives is plain JSON data, so the
    worker reconstructs exactly what the parent would have used.
    """
    import json as _json
    from pathlib import Path as _Path

    from mage_ptcg.meta_specialist.actor_pool_v1 import _build_actor_pool_deck_binding_v1
    from mage_ptcg.meta_specialist.local_dataset_v2 import (
        build_local_dataset_manifest_streaming_v2,
        build_trusted_permission_set_v1,
        canonical_json_bytes_v2 as _canonical,
    )

    chunk_path = _Path(payload["chunk_path"])
    trusted = build_trusted_permission_set_v1((_canonical(payload["permission_manifest"]),))
    qualified, _lock, vocabulary = _build_actor_pool_deck_binding_v1(
        archetype_id=payload["archetype_id"],
        deck_csv_path=_Path(payload["deck_csv_path"]),
        source_commit=payload["source_commit"],
    )

    def records():
        with open(chunk_path, encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    yield _json.loads(line)

    chunk_manifest = build_local_dataset_manifest_streaming_v2(
        records=records(), environment_version=payload["environment_version"],
        deck_fingerprint=qualified.deck_file_sha256, trusted_permissions=trusted,
    )
    return _derive_chunk_envelopes_v1(
        chunk_path, chunk_manifest, spool_path=_Path(payload["spool_path"]),
        vocabulary=vocabulary, trusted_permissions=trusted,
        qualification_time_utc=payload["qualification_time_utc"],
    )


def _seal_chunk_job_v1(payload: dict) -> dict[str, object]:
    """Write one dataset chunk from its source records, then derive it.

    Writing and deriving are fused because both are pure functions of the same
    chunk.  Splitting them would put a barrier between two parallel phases and
    make chunk writing -- roughly 40 seconds per 435 MB -- run in the parent.
    """
    import json as _json
    from pathlib import Path as _Path

    from mage_ptcg.meta_specialist.actor_pool_v1 import _build_actor_pool_deck_binding_v1
    from mage_ptcg.meta_specialist.local_dataset_v2 import (
        build_local_dataset_manifest_streaming_v2,
        build_trusted_permission_set_v1,
        canonical_json_bytes_v2 as _canonical,
    )

    chunk_path = _Path(payload["chunk_path"])
    with open(chunk_path, "wb") as handle:
        for source in payload["record_files"]:
            with open(source, encoding="utf-8") as reader:
                for line in reader:
                    if line.strip():
                        handle.write(_canonical(_json.loads(line)) + b"\n")

    trusted = build_trusted_permission_set_v1((_canonical(payload["permission_manifest"]),))
    qualified, _lock, vocabulary = _build_actor_pool_deck_binding_v1(
        archetype_id=payload["archetype_id"],
        deck_csv_path=_Path(payload["deck_csv_path"]),
        source_commit=payload["source_commit"],
    )

    def records():
        with open(chunk_path, encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    yield _json.loads(line)

    chunk_manifest = build_local_dataset_manifest_streaming_v2(
        records=records(), environment_version=payload["environment_version"],
        deck_fingerprint=qualified.deck_file_sha256, trusted_permissions=trusted,
    )
    derived = _derive_chunk_envelopes_v1(
        chunk_path, chunk_manifest, spool_path=_Path(payload["spool_path"]),
        vocabulary=vocabulary, trusted_permissions=trusted,
        qualification_time_utc=payload["qualification_time_utc"],
    )
    derived["chunk_path"] = str(chunk_path)
    return derived


def partition_record_files_v1(
    record_files: Sequence[Path], *, chunk_max_bytes: int
) -> list[list[str]]:
    """Group per-game record files into chunks under a byte budget.

    Grouping whole files keeps the partition a deterministic function of the
    collection, so the same corpus always chunks the same way, and lets each
    chunk be written by a different process.
    """
    if type(chunk_max_bytes) is not int or chunk_max_bytes < 1:
        raise TrainingSnapshotV1Error("chunk_max_bytes must be a positive int")
    groups: list[list[str]] = []
    current: list[str] = []
    size = 0
    for path in record_files:
        length = path.stat().st_size
        if current and size + length > chunk_max_bytes:
            groups.append(current)
            current = []
            size = 0
        current.append(str(path))
        size += length
    if current:
        groups.append(current)
    if not groups:
        raise TrainingSnapshotV1Error("a corpus needs at least one source record file")
    return groups


def seal_sharded_corpora_v1(
    specs: Sequence[Mapping[str, Any]], *, workers: int, on_progress=None
) -> dict[str, dict[str, object]]:
    """Seal several corpora through **one** pool, so no worker sits idle.

    Sealing each corpus in its own pool wastes the difference between them: the
    lanes measured here range from 3.4 GB to 8.7 GB of records, so a quarter of
    the cores finished early and stopped helping while the largest lane still had
    most of its work left.  One queue removes the problem rather than balancing
    it -- when the small lanes run out of chunks, every worker is already on the
    large one.

    There is also no barrier between the two stages.  A corpus whose chunks are
    all derived has its splits assigned immediately and its shard jobs submitted
    into the same pool, while other corpora are still deriving.

    Ordering is unaffected: results are slotted by submission order, and every
    corpus-wide decision is still made over that corpus's chunks as a whole.

    Returns ``{name: {"index": index | None, "error": str | None}}``.  A corpus
    that fails does not abort the others: sealing takes about an hour, and losing
    three finished corpora because a fourth failed -- with no indication of which
    or why -- is worse than finishing what can be finished and naming the rest.
    """
    if type(workers) is not int or workers < 1:
        raise TrainingSnapshotV1Error("workers must be a positive int")
    ordered = list(specs)
    if not ordered:
        raise TrainingSnapshotV1Error("at least one corpus specification is required")

    spool_root = Path(tempfile.mkdtemp(prefix="specialist-corpora-spool-"))
    plans: list[dict[str, Any]] = []
    for position, spec in enumerate(ordered):
        groups = partition_record_files_v1(
            spec["record_files"], chunk_max_bytes=spec["chunk_max_bytes"]
        )
        destination = Path(spec["output_dir"])
        destination.mkdir(parents=True, exist_ok=True)
        plans.append({
            "spec": spec,
            "groups": groups,
            "derived": [None] * len(groups),
            "chunk_paths": [
                destination / f"dataset-{index:04d}.jsonl" for index in range(len(groups))
            ],
            "shard_outputs": None,
            "shard_context": None,
            "index": None,
            "error": None,
            "spool_dir": spool_root / f"corpus-{position:04d}",
        })
        plans[-1]["spool_dir"].mkdir(parents=True, exist_ok=True)

    total_chunks = sum(len(plan["groups"]) for plan in plans)
    done_chunks = 0
    done_shards = 0
    total_shard_jobs = 0

    def report() -> None:
        if on_progress is not None:
            on_progress({
                "stage": "corpora", "chunks_done": done_chunks, "chunks": total_chunks,
                "shards_done": done_shards, "shard_jobs": total_shard_jobs,
                "lanes": [
                    {
                        "name": plan["spec"].get("name", ""),
                        "chunks": len(plan["groups"]),
                        "chunks_done": sum(1 for item in plan["derived"] if item is not None),
                        "finished": plan["index"] is not None,
                        "error": plan["error"],
                    }
                    for plan in plans
                ],
            })

    try:
        pool_context = multiprocessing.get_context("spawn")
        with ProcessPoolExecutor(
            max_workers=min(workers, max(1, total_chunks)), mp_context=pool_context
        ) as pool:
            pending: dict[Any, tuple[str, int, int]] = {}
            # Round-robin across corpora rather than one corpus at a time.  A
            # corpus cannot start its shard jobs until *all* its chunks are
            # derived, so submitting corpus by corpus leaves the last corpus
            # deriving alone at the end with nothing else to overlap.  Taking one
            # chunk from each in turn brings them to that point together, which
            # keeps the queue full through the tail.  It does not change results:
            # every output is slotted by (corpus, chunk index), never by order.
            deepest = max(len(plan["groups"]) for plan in plans)
            for index in range(deepest):
                for lane, plan in enumerate(plans):
                    if index >= len(plan["groups"]):
                        continue
                    spec = plan["spec"]
                    payload = {
                        "record_files": plan["groups"][index],
                        "chunk_path": str(plan["chunk_paths"][index]),
                        "spool_path": str(plan["spool_dir"] / f"chunk-{index:04d}.jsonl"),
                        "archetype_id": spec["archetype_id"],
                        "deck_csv_path": str(spec["deck_csv_path"]),
                        "source_commit": spec["source_commit"],
                        "permission_manifest": dict(spec["permission_manifest"]),
                        "environment_version": spec["environment_version"],
                        "qualification_time_utc": spec["qualification_time_utc"],
                    }
                    pending[pool.submit(_seal_chunk_job_v1, payload)] = ("chunk", lane, index)
            report()

            while pending:
                finished, _rest = futures_wait(pending, return_when=FIRST_COMPLETED)
                for future in finished:
                    kind, lane, index = pending.pop(future)
                    plan = plans[lane]
                    try:
                        result = future.result()
                    except Exception as exc:
                        # One corpus failing must not discard the others' work.
                        # Sealing several corpora takes about an hour; aborting
                        # everything on the first failure threw away the corpora
                        # that had already succeeded and, worse, reported no
                        # which/why -- the caller saw one traceback with no lane
                        # in it.  Record the failure against its corpus, drop
                        # that corpus's remaining work, and let the rest finish.
                        if plan["error"] is None:
                            plan["error"] = (
                                f"{kind} job {index} failed: "
                                f"{type(exc).__name__}: {exc}"
                            )
                        continue
                    if plan["error"] is not None:
                        continue
                    if kind == "chunk":
                        plan["derived"][index] = result
                        done_chunks += 1
                        if all(item is not None for item in plan["derived"]):
                            spec = plan["spec"]
                            try:
                                jobs, context = _plan_shard_jobs_v1(
                                    plan["derived"], chunk_paths=plan["chunk_paths"],
                                    vocabulary=spec["vocabulary"],
                                    qualification_time_utc=spec["qualification_time_utc"],
                                    output_dir=spec["output_dir"],
                                    shard_max_examples=spec["shard_max_examples"],
                                    split_names=spec.get("split_names", DEFAULT_SPLIT_NAMES_V1),
                                    split_weights=spec.get(
                                        "split_weights", DEFAULT_SPLIT_WEIGHTS_V1
                                    ),
                                )
                            except Exception as exc:
                                plan["error"] = (
                                    f"split assignment failed: {type(exc).__name__}: {exc}"
                                )
                                report()
                                continue
                            plan["shard_context"] = context
                            plan["shard_outputs"] = [None] * len(jobs)
                            total_shard_jobs += len(jobs)
                            for position, job in enumerate(jobs):
                                pending[pool.submit(_build_chunk_shards_v1, job)] = (
                                    "shard", lane, position
                                )
                    else:
                        plan["shard_outputs"][index] = result
                        done_shards += 1
                        if all(item is not None for item in plan["shard_outputs"]):
                            try:
                                plan["index"] = _finish_corpus_index_v1(
                                    plan["shard_outputs"], plan["shard_context"]
                                )
                            except Exception as exc:
                                plan["error"] = (
                                    f"index write failed: {type(exc).__name__}: {exc}"
                                )
                    report()
    finally:
        for plan in plans:
            _remove_spool_dir_v1(plan["spool_dir"])
        try:
            spool_root.rmdir()
        except OSError:
            pass

    return {
        plan["spec"].get("name", str(position)): {
            "index": plan["index"], "error": plan["error"],
        }
        for position, plan in enumerate(plans)
    }


def seal_sharded_corpus_v1(
    chunk_paths: Sequence[Path],
    *,
    archetype_id: str,
    deck_csv_path: str | Path,
    source_commit: str,
    permission_manifest: Mapping[str, Any],
    environment_version: str,
    qualification_time_utc: str,
    output_dir: str | Path,
    shard_max_examples: int,
    workers: int = 1,
    split_names: tuple[str, ...] = DEFAULT_SPLIT_NAMES_V1,
    split_weights: tuple[float, ...] = DEFAULT_SPLIT_WEIGHTS_V1,
    on_progress=None,
) -> dict[str, object]:
    """Seal a chunked corpus, deriving the chunks in parallel.

    Chunk derivation is the dominant cost of sealing and is a pure function of
    one chunk, so it parallelises exactly.  Split assignment, the duplicate cap
    and the corpus identities are still decided over every chunk at once in
    :func:`_assemble_sharded_corpus_v1`, so the result does not depend on the
    worker count or on the order in which chunks happen to finish.
    """
    ordered = [Path(path) for path in chunk_paths]
    if not ordered:
        raise TrainingSnapshotV1Error("a corpus needs at least one dataset chunk")
    if type(workers) is not int or workers < 1:
        raise TrainingSnapshotV1Error("workers must be a positive int")

    spool_dir = Path(tempfile.mkdtemp(prefix="specialist-corpus-spool-"))
    try:
        payloads = [
            {
                "chunk_path": str(path),
                "spool_path": str(spool_dir / f"chunk-{position:04d}.jsonl"),
                "archetype_id": archetype_id,
                "deck_csv_path": str(deck_csv_path),
                "source_commit": source_commit,
                "permission_manifest": dict(permission_manifest),
                "environment_version": environment_version,
                "qualification_time_utc": qualification_time_utc,
            }
            for position, path in enumerate(ordered)
        ]
        if workers == 1 or len(payloads) == 1:
            results = []
            for position, item in enumerate(payloads):
                results.append(_derive_chunk_in_worker_v1(item))
                if on_progress is not None:
                    on_progress({
                        "stage": "chunk", "done": position + 1, "chunks": len(payloads),
                        "examples": sum(int(r["count"]) for r in results),
                    })
        else:
            # Processes rather than threads: envelope issuance keeps a
            # process-global registry, and the parent may already hold torch's
            # threads, which fork does not carry safely.
            context = multiprocessing.get_context("spawn")
            results = [None] * len(payloads)
            with ProcessPoolExecutor(
                max_workers=min(workers, len(payloads)), mp_context=context
            ) as pool:
                futures = {
                    pool.submit(_derive_chunk_in_worker_v1, item): position
                    for position, item in enumerate(payloads)
                }
                completed = 0
                for future in as_completed(futures):
                    # Slot by submission order, never by completion order: the
                    # corpus identity digests the chunks in their given order.
                    results[futures[future]] = future.result()
                    completed += 1
                    if on_progress is not None:
                        on_progress({
                            "stage": "chunk", "done": completed, "chunks": len(payloads),
                            "examples": sum(
                                int(r["count"]) for r in results if r is not None
                            ),
                        })
        # The assembler names the vocabulary each shard was built against; build
        # the same binding here rather than shipping a capability object back.
        from mage_ptcg.meta_specialist.actor_pool_v1 import _build_actor_pool_deck_binding_v1

        _qualified, _lock, vocabulary = _build_actor_pool_deck_binding_v1(
            archetype_id=archetype_id, deck_csv_path=Path(deck_csv_path),
            source_commit=source_commit,
        )
        return _assemble_sharded_corpus_v1(
            results, chunk_paths=ordered, vocabulary=vocabulary,
            qualification_time_utc=qualification_time_utc, output_dir=output_dir,
            shard_max_examples=shard_max_examples, split_names=split_names,
            split_weights=split_weights, workers=workers, on_progress=on_progress,
        )
    finally:
        _remove_spool_dir_v1(spool_dir)


def _remove_spool_dir_v1(spool_dir: Path) -> None:
    """Delete only the spool files this module wrote, then the directory."""
    for entry in sorted(spool_dir.glob("chunk-*.jsonl")):
        entry.unlink(missing_ok=True)
    try:
        spool_dir.rmdir()
    except OSError:
        # Something else is in there; leaving it is safer than removing it.
        pass


def _derive_chunk_envelopes_v1(
    chunk_path: Path, chunk_manifest: dict[str, object], *, spool_path: Path,
    vocabulary: CardVocabularyV1, trusted_permissions: Mapping[str, Any],
    qualification_time_utc: str,
) -> dict[str, object]:
    """Validate one chunk once, spool its sealed envelopes, and return its keys.

    This is the expensive half of sealing (34 records/s measured), and it is the
    only part that touches raw records.  Keeping the sealed bytes means the split
    assignment's second traversal re-issues them at 186 records/s instead of
    deriving them again.  It is a pure function of one chunk, so chunks can be
    derived in parallel.
    """
    keys: list[tuple[str, str, str]] = []
    identity: tuple[str, str, str] | None = None
    sources: set[tuple[str, str]] = set()
    permissions: set[tuple[str, str, str]] = set()
    feature: tuple[str, str] | None = None
    with open(spool_path, "wb") as spool:
        for envelope in iter_training_example_envelopes_v2(
            chunk_path, manifest=chunk_manifest, vocabulary=vocabulary,
            trusted_permissions=trusted_permissions,
            qualification_time_utc=qualification_time_utc,
        ):
            payload = envelope.to_dict()
            keys.append((
                payload["record_id"], payload["episode_id_hash"], payload["near_duplicate_id"]
            ))
            if identity is None:
                identity = (
                    payload["dataset_snapshot_sha256"],
                    payload["manifest_id"],
                    payload["manifest_content_hash"],
                )
                model_input = payload["model_input"]
                feature = (
                    str(model_input["feature_domain"]),
                    str(model_input["feature_schema_hash"]),
                )
            sources.add((payload["source_kind"], payload["source_artifact_sha256"]))
            permissions.add((
                payload["permission_manifest_id"],
                payload["permission_content_hash"],
                payload["permission_trusted_bytes_sha256"],
            ))
            spool.write(sealed_envelope_bytes_v2(envelope) + b"\n")
    return {
        "identity": identity, "keys": keys, "feature": feature,
        # Collected here rather than during the second traversal, so that the
        # second traversal needs nothing from the corpus except its own chunk and
        # can therefore run in parallel.
        "sources": sorted(sources), "permissions": sorted(permissions),
        "spool_path": str(spool_path), "count": len(keys),
    }


def _build_chunk_shards_v1(payload: dict) -> dict[str, object]:
    """Turn one chunk's spooled envelopes into finished shard files.

    Runs in a worker process.  Everything corpus-wide it needs -- the split each
    record was assigned, the multiplicity each position reached, the cap block --
    was decided by the parent over the whole corpus and is passed in already
    narrowed to this chunk, so the worker never sees the rest of the corpus and
    its result cannot depend on how the corpus was chunked.

    Memory is one chunk's examples rather than one shard's: at the default chunk
    size that is roughly 90 MB instead of the 890 MB a 20,000-example shard
    buffer held, which is also why this replaced the single-process second pass.
    """
    from mage_ptcg.meta_specialist.training_example_envelope_v2 import (
        reissue_sealed_envelope_v2 as _reissue,
        reject_forbidden_private_fields_v2 as _reject,
    )

    assignment = payload["assignment"]
    multiplicity = payload["multiplicity"]
    shared = dict(payload["shared"])
    shared["source_artifacts"] = {tuple(item) for item in shared["source_artifacts"]}
    shared["permissions"] = {tuple(item) for item in shared["permissions"]}
    shard_max_examples = payload["shard_max_examples"]
    destination = Path(payload["output_dir"])

    written: list[dict[str, object]] = []
    buffer: list[dict[str, object]] = []

    def flush() -> None:
        if not buffer:
            return
        index = payload["shard_index_start"] + len(written)
        body = _finalize_shard_v1(
            buffer, shared=shared, split_names=tuple(payload["split_names"]),
            split_weights=tuple(payload["split_weights"]),
            duplicate_cap=payload["duplicate_cap"], shard_index=index,
        )
        path = destination / f"snapshot-{index:04d}.json"
        atomic_write_training_snapshot_v1(path, body)
        written.append({
            "path": path.name, "snapshot_id": body["snapshot_id"],
            "examples": len(buffer), "split_counts": dict(body["split_counts"]),
        })
        buffer.clear()

    emitted = 0
    with open(payload["spool_path"], "rb") as spool:
        for line in spool:
            if not line.strip():
                continue
            emitted += 1
            envelope = _reissue(line.rstrip(b"\n"))
            item = envelope.to_dict()
            record_id = item["record_id"]
            pre_cap = item["example_quality_weight"]
            scale = _duplicate_scale_v1(multiplicity[item["near_duplicate_id"]])
            example = {
                "record_id": record_id,
                "episode_id_hash": item["episode_id_hash"],
                "near_duplicate_id": item["near_duplicate_id"],
                "record_content_hash": item["record_content_hash"],
                "split": assignment[record_id],
                "model_input": item["model_input"],
                "loss_rows": item["loss_rows"],
                "value_target": item["value_target"],
                "pre_cap_quality_weight": pre_cap,
                "example_quality_weight": pre_cap * scale,
            }
            _reject(example)
            buffer.append(example)
            if len(buffer) >= shard_max_examples:
                flush()
    flush()
    if emitted != payload["count"]:
        raise TrainingSnapshotV1Error(
            f"spool {payload['spool_path']} holds {emitted} envelopes where "
            f"{payload['count']} were derived"
        )
    return {"shards": written, "examples": emitted}


def _assemble_sharded_corpus_v1(
    results: Sequence[dict[str, Any]], *, chunk_paths: Sequence[Path],
    vocabulary: CardVocabularyV1, qualification_time_utc: str,
    output_dir: str | Path, shard_max_examples: int,
    split_names: tuple[str, ...], split_weights: tuple[float, ...],
    workers: int = 1, on_progress=None,
) -> dict[str, object]:
    """Plan and run one corpus's shard jobs in a pool of this call's own."""
    jobs, context = _plan_shard_jobs_v1(
        results, chunk_paths=chunk_paths, vocabulary=vocabulary,
        qualification_time_utc=qualification_time_utc, output_dir=output_dir,
        shard_max_examples=shard_max_examples, split_names=split_names,
        split_weights=split_weights, on_progress=on_progress,
    )
    if workers == 1 or len(jobs) == 1:
        outputs = [_build_chunk_shards_v1(job) for job in jobs]
    else:
        pool_context = multiprocessing.get_context("spawn")
        outputs = [None] * len(jobs)
        with ProcessPoolExecutor(
            max_workers=min(workers, len(jobs)), mp_context=pool_context
        ) as pool:
            futures = {pool.submit(_build_chunk_shards_v1, job): position
                       for position, job in enumerate(jobs)}
            for future in as_completed(futures):
                outputs[futures[future]] = future.result()
    if on_progress is not None:
        on_progress({
            "stage": "shard", "done": len(jobs), "chunks": len(jobs),
            "shards": sum(len(item["shards"]) for item in outputs),
            "examples": context["examples_total"],
        })
    return _finish_corpus_index_v1(outputs, context)


def _plan_shard_jobs_v1(
    results: Sequence[dict[str, Any]], *, chunk_paths: Sequence[Path],
    vocabulary: CardVocabularyV1, qualification_time_utc: str,
    output_dir: str | Path, shard_max_examples: int,
    split_names: tuple[str, ...], split_weights: tuple[float, ...],
    on_progress=None,
) -> tuple[list[dict[str, object]], dict[str, Any]]:
    """Assign splits over the whole corpus, then plan its shard jobs.

    Every corpus-wide property -- the grouped split, the duplicate cap, the
    corpus identities -- is decided here, from all chunks at once, so how the
    corpus was cut into chunks cannot influence what is learned.  The returned
    jobs are independent of one another and of every other corpus, so a caller
    sealing several corpora can put them all in one pool.
    """
    grouping_keys: list[tuple[str, str, str]] = []
    multiplicity: dict[str, int] = {}
    chunk_identities: list[tuple[str, str, str]] = []
    all_sources: set[tuple[str, str]] = set()
    all_permissions: set[tuple[str, str, str]] = set()
    feature: tuple[str, str] | None = None
    for result in results:
        identity = result["identity"]
        if identity is None:
            continue
        chunk_identities.append(tuple(identity))
        if feature is None:
            feature = tuple(result["feature"])
        all_sources.update(tuple(item) for item in result["sources"])
        all_permissions.update(tuple(item) for item in result["permissions"])
        for key in result["keys"]:
            # Intern the two repeated hashes: a corpus reuses the same
            # near-duplicate and episode ids across many examples, and unpickled
            # strings arrive as distinct objects per chunk.
            record, episode, near = key
            episode = sys.intern(episode)
            near = sys.intern(near)
            grouping_keys.append((record, episode, near))
            multiplicity[near] = multiplicity.get(near, 0) + 1
    if not grouping_keys:
        raise TrainingSnapshotV1Error("training snapshot needs at least one eligible example")
    if len(chunk_identities) != len(chunk_paths):
        # Every chunk must contribute, or the index below could not name which
        # file each identity came from -- and a chunk that yields nothing is a
        # sealing mistake, not something to paper over.
        raise TrainingSnapshotV1Error(
            f"{len(chunk_paths)} dataset chunks were given but only "
            f"{len(chunk_identities)} contributed eligible examples"
        )
    corpus_sha256 = corpus_dataset_sha256_v1([item[0] for item in chunk_identities])
    corpus_manifest_id = _corpus_digest_v1(
        _CORPUS_MANIFEST_ID_DOMAIN_V1, [item[1] for item in chunk_identities]
    )
    corpus_manifest_content_hash = _corpus_digest_v1(
        _CORPUS_MANIFEST_HASH_DOMAIN_V1, [item[2] for item in chunk_identities]
    )

    try:
        assignment = assign_grouped_splits_from_keys_v2(
            tuple(grouping_keys), split_names=split_names, split_weights=split_weights
        )
    except LocalDatasetV2Error as exc:
        raise TrainingSnapshotV1Error(f"grouped split assignment failed: {exc}") from exc

    episodes = {episode for _record, episode, _near in grouping_keys}
    ubiquity_threshold = near_duplicate_ubiquity_threshold_v2(len(episodes))
    ubiquitous = ubiquitous_near_duplicate_ids_v2(tuple(grouping_keys))
    duplicate_cap = {
        "max_near_duplicate_multiplicity": MAX_NEAR_DUPLICATE_MULTIPLICITY_V1,
        "ubiquity_min_episodes": ubiquity_threshold,
        "ubiquitous_near_duplicate_ids": sorted(ubiquitous),
        "groups_capped": sum(
            1 for count in multiplicity.values() if count > MAX_NEAR_DUPLICATE_MULTIPLICITY_V1
        ),
        "records_capped": sum(
            count for count in multiplicity.values()
            if count > MAX_NEAR_DUPLICATE_MULTIPLICITY_V1
        ),
    }
    if on_progress is not None:
        on_progress({"stage": "keys", "examples": len(grouping_keys)})

    # --- pass 2: build shards, one job per chunk -------------------------
    # Each chunk's shards depend only on that chunk's spool plus the corpus-wide
    # decisions above, so the jobs are independent.  Running them in the parent
    # made the second half of sealing single-threaded (measured 284s of a 505s
    # run) *and* set the memory peak, because one shard's worth of examples had
    # to be buffered at once.  Per-chunk jobs fix both.
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    shared = {
        # These three name the corpus, not whichever chunk came first.  The index
        # below carries each chunk's real values so the digests stay reproducible.
        "dataset_snapshot_sha256": corpus_sha256,
        "manifest_id": corpus_manifest_id,
        "manifest_content_hash": corpus_manifest_content_hash,
        "feature_domain": feature[0],
        "feature_schema_hash": feature[1],
        "vocabulary_source_sha256": vocabulary.source_sha256,
        "vocabulary_environment_version": vocabulary.environment_version,
        "qualification_time_utc": qualification_time_utc,
        "source_artifacts": sorted(all_sources),
        "permissions": sorted(all_permissions),
    }

    jobs: list[dict[str, object]] = []
    shard_index = 0
    for result in results:
        chunk_records = {key[0] for key in result["keys"]}
        jobs.append({
            "spool_path": result["spool_path"],
            "count": result["count"],
            # Narrowed to this chunk so a worker never receives the corpus.
            "assignment": {
                record: assignment[record] for record in chunk_records
            },
            "multiplicity": {
                key[2]: multiplicity[key[2]] for key in result["keys"]
            },
            "duplicate_cap": duplicate_cap,
            "shared": shared,
            "output_dir": str(destination),
            "shard_index_start": shard_index,
            "shard_max_examples": shard_max_examples,
            "split_names": list(split_names),
            "split_weights": list(split_weights),
        })
        shard_index += max(1, math.ceil(result["count"] / shard_max_examples))
        # The keys are now only needed as the grouping list built above.
        result["keys"] = []

    context_for_index = {
        "shared": shared, "duplicate_cap": duplicate_cap,
        "chunk_paths": list(chunk_paths), "chunk_identities": chunk_identities,
        "examples_total": len(grouping_keys), "destination": destination,
        "split_names": split_names, "split_weights": split_weights,
    }
    return jobs, context_for_index


def _finish_corpus_index_v1(
    outputs: Sequence[Mapping[str, Any]], context: Mapping[str, Any]
) -> dict[str, object]:
    """Write ``snapshot_index.json`` once every shard job of one corpus is done."""
    shared = context["shared"]
    destination = context["destination"]
    split_names = context["split_names"]
    shards: list[dict[str, object]] = []
    # Chunk order, never completion order: the index must list shards the same
    # way on every run so a corpus is reproducible from it.
    for output in outputs:
        shards.extend(output["shards"])

    index_payload = {
        "schema_version": SHARD_INDEX_SCHEMA_V1,
        "dataset_snapshot_sha256": shared["dataset_snapshot_sha256"],
        "manifest_id": shared["manifest_id"],
        # The corpus identities above are digests over these, in this order, so
        # a reader can recompute them and see exactly which files were sealed.
        "dataset_chunks": [
            {
                "path": str(chunk_path),
                "dataset_snapshot_sha256": dataset_hash,
                "manifest_id": manifest_ref,
                "manifest_content_hash": manifest_hash,
            }
            for chunk_path, (dataset_hash, manifest_ref, manifest_hash)
            in zip(context["chunk_paths"], context["chunk_identities"], strict=True)
        ],
        # θ0 の provenance はここから引かれる (`teacher_ids_in_snapshot_v1`)。
        # index が持たないと、shard 化した corpus から作った θ0 が「どの teacher
        # から蒸留したか」を名乗れなくなる。
        "source_artifacts": [
            {"kind": kind, "artifact_sha256": artifact}
            for kind, artifact in sorted(shared["source_artifacts"])
        ],
        "examples_total": context["examples_total"],
        "split_names": list(split_names),
        "split_weights": list(context["split_weights"]),
        "split_counts": {
            name: sum(int(item["split_counts"].get(name, 0)) for item in shards)
            for name in split_names
        },
        "duplicate_cap": context["duplicate_cap"],
        "shards": shards,
    }
    index_path = destination / "snapshot_index.json"
    body = canonical_json_bytes_v2(
        index_payload, max_nodes=_MAX_SNAPSHOT_JSON_NODES_V1,
        max_bytes=MAX_TRAINING_SNAPSHOT_BYTES_V1,
    )
    index_path.write_bytes(body)
    return index_payload


def _finalize_shard_v1(
    examples: list[dict[str, object]], *, shared: dict, split_names, split_weights,
    duplicate_cap: dict, shard_index: int,
) -> dict[str, object]:
    """Assemble one shard into a snapshot that validates on its own."""
    ordered = sorted(examples, key=lambda item: item["record_id"])
    payload = {
        "schema_version": TRAINING_SHARD_SCHEMA_V1,
        "snapshot_id": "",
        "content_hash": "",
        "dataset_snapshot_sha256": shared["dataset_snapshot_sha256"],
        "manifest_id": shared["manifest_id"],
        "manifest_content_hash": shared["manifest_content_hash"],
        "vocabulary_source_sha256": shared["vocabulary_source_sha256"],
        "vocabulary_environment_version": shared["vocabulary_environment_version"],
        "feature_domain": shared["feature_domain"],
        "feature_schema_hash": shared["feature_schema_hash"],
        "qualification_time_utc": shared["qualification_time_utc"],
        "split_names": list(split_names),
        "split_weights": list(split_weights),
        "split_counts": {
            name: sum(1 for item in ordered if item["split"] == name) for name in split_names
        },
        "source_artifacts": [
            {"kind": kind, "artifact_sha256": artifact}
            for kind, artifact in sorted(shared["source_artifacts"])
        ],
        "permissions": [
            {
                "permission_manifest_id": manifest_ref,
                "permission_content_hash": content,
                "permission_trusted_bytes_sha256": trusted,
            }
            for manifest_ref, content, trusted in sorted(shared["permissions"])
        ],
        # A shard's cap block describes the corpus it belongs to, not the shard:
        # the cap is a property of the whole dataset and a shard cannot recompute
        # it from the slice it happens to hold.
        "duplicate_cap": dict(duplicate_cap),
        "examples": ordered,
    }
    payload["snapshot_id"] = _snapshot_identity(payload)
    payload["content_hash"] = _snapshot_content_hash(payload)
    return payload


def _verify_corpus_wide_properties_v1(
    facts: Sequence[tuple[str, str, str, str, float, float]], index: Mapping[str, Any]
) -> None:
    """Check the two properties a single shard cannot check about itself.

    A shard re-deriving the §9.3 duplicate cap or the grouped split from its own
    slice would reach the wrong answer, so :func:`validate_training_snapshot_v1`
    skips both for shards.  They are checked here instead, over every example of
    every shard, which is where the evidence for them exists.

    ``facts`` carries only the six fields these checks need -- about 100 bytes per
    example rather than the example itself -- and is gathered during the single
    pass the caller already makes over the shards.
    """
    multiplicity: dict[str, int] = {}
    for _record, _episode, near, _split, _pre, _weight in facts:
        multiplicity[near] = multiplicity.get(near, 0) + 1
    episodes = {episode for _record, episode, _near, _split, _pre, _weight in facts}
    ubiquitous = ubiquitous_near_duplicate_ids_v2(
        tuple((record, episode, near) for record, episode, near, _s, _p, _w in facts)
    )

    component_split: dict[str, str] = {}
    for record, episode, near, split_name, pre_cap, weight in facts:
        expected = pre_cap * _duplicate_scale_v1(multiplicity[near])
        if weight != expected:
            raise TrainingSnapshotV1Error(
                f"example {record} carries weight {weight!r} where the corpus-wide "
                f"duplicate cap gives {expected!r}"
            )
        group_keys = [episode] if near in ubiquitous else [episode, near]
        for group_key in group_keys:
            existing = component_split.setdefault(group_key, split_name)
            if existing != split_name:
                raise TrainingSnapshotV1Error(
                    "sharded corpus split leaks one grouping component across splits"
                )

    expected_cap = {
        "max_near_duplicate_multiplicity": MAX_NEAR_DUPLICATE_MULTIPLICITY_V1,
        "ubiquity_min_episodes": near_duplicate_ubiquity_threshold_v2(len(episodes)),
        "ubiquitous_near_duplicate_ids": sorted(ubiquitous),
        "groups_capped": sum(
            1 for count in multiplicity.values() if count > MAX_NEAR_DUPLICATE_MULTIPLICITY_V1
        ),
        "records_capped": sum(
            1 for _r, _e, near, _s, _p, _w in facts
            if _duplicate_scale_v1(multiplicity[near]) != 1.0
        ),
    }
    declared = dict(index["duplicate_cap"])
    declared["ubiquitous_near_duplicate_ids"] = sorted(
        declared["ubiquitous_near_duplicate_ids"]
    )
    if declared != expected_cap:
        raise TrainingSnapshotV1Error(
            "sharded corpus duplicate_cap does not match what its shards derive"
        )


def _read_one_shard_for_split_v1(payload: dict) -> dict[str, object]:
    """Revalidate one shard and return its facts plus the requested split.

    Runs in a worker process.  Revalidating a shard is the dominant cost of
    loading a corpus (measured 4.6 ms per example, or 21 minutes for a 190,000
    example training split), and shards are independent, so this parallelises.
    """
    from mage_ptcg.meta_specialist.training_snapshot_v1 import (
        read_training_snapshot_v1 as _read,
        snapshot_examples_for_split_v1 as _for_split,
    )

    snapshot = _read(Path(payload["path"]))
    facts = [
        (
            example["record_id"], example["episode_id_hash"],
            example["near_duplicate_id"], example["split"],
            example["pre_cap_quality_weight"], example["example_quality_weight"],
        )
        for example in snapshot["examples"]
    ]
    return {"facts": facts, "examples": list(_for_split(snapshot, payload["split"]))}


def read_sharded_split_examples_v1(
    index_path: str | Path, split: str, *, workers: int = 1
) -> tuple[dict, ...]:
    """Read one split's examples across every shard of a sharded corpus.

    Each shard is revalidated on its own, so a truncated or edited shard fails
    rather than silently shrinking the training set.  The corpus-wide duplicate
    cap and grouped split are verified across all shards, because no single shard
    can check either.  Examples come back in shard order, then record_id, so a
    run is reproducible given the same index regardless of ``workers``.

    ``workers`` above 1 revalidates shards in parallel.  It changes only how long
    the read takes; every shard is validated exactly as it would be serially.
    """
    index_file = Path(index_path)
    index = parse_canonical_json_bytes_v2(
        index_file.read_bytes(), max_nodes=_MAX_SNAPSHOT_JSON_NODES_V1,
        max_bytes=MAX_TRAINING_SNAPSHOT_BYTES_V1,
    )
    if index.get("schema_version") != SHARD_INDEX_SCHEMA_V1:
        raise TrainingSnapshotV1Error("not a training snapshot index")
    if split not in index["split_names"]:
        raise TrainingSnapshotV1Error(f"unknown split {split!r}")
    if type(workers) is not int or workers < 1:
        raise TrainingSnapshotV1Error("workers must be a positive int")

    rows = list(index["shards"])
    jobs = [
        {"path": str(index_file.parent / row["path"]), "split": split} for row in rows
    ]
    if workers == 1 or len(jobs) == 1:
        outputs = [_read_one_shard_for_split_v1(job) for job in jobs]
    else:
        context = multiprocessing.get_context("spawn")
        outputs = [None] * len(jobs)
        with ProcessPoolExecutor(
            max_workers=min(workers, len(jobs)), mp_context=context
        ) as pool:
            futures = {pool.submit(_read_one_shard_for_split_v1, job): position
                       for position, job in enumerate(jobs)}
            for future in as_completed(futures):
                # Slot by shard order, not completion order.
                outputs[futures[future]] = future.result()

    collected: list[dict] = []
    facts: list[tuple[str, str, str, str, float, float]] = []
    for output in outputs:
        facts.extend(tuple(item) for item in output["facts"])
        collected.extend(output["examples"])
    _verify_corpus_wide_properties_v1(facts, index)
    declared = int(index["split_counts"].get(split, 0))
    if len(collected) != declared:
        raise TrainingSnapshotV1Error(
            f"index declares {declared} {split} examples but the shards hold "
            f"{len(collected)}; a shard is missing or was edited"
        )
    return tuple(collected)


def _exact_dict(value: object, *, field: str, keys: frozenset[str]) -> dict[str, Any]:
    if type(value) is not dict or set(value) != keys:
        raise TrainingSnapshotV1Error(f"{field} has the wrong closed field set")
    return value


def validate_training_snapshot_v1(value: object) -> dict[str, object]:
    """Revalidate a snapshot's closed shape, split disjointness, and self-hashes.

    Two corpus-wide properties -- that each example's weight matches the §9.3
    duplicate cap, and that no grouping component straddles two splits -- are
    checked here for a standalone snapshot but **not** for a shard, because a
    shard holds only part of the corpus and would re-derive both from a fragment.
    A shard that happened to hold 3 copies of a position repeated 40 times
    corpus-wide would compute a different cap and refuse a correct weight.

    Neither check is dropped: :func:`read_sharded_split_examples_v1` performs both
    across every shard of the corpus, where the evidence for them exists.  A shard
    carries a distinct ``schema_version`` so it can never be presented as a
    standalone snapshot that skipped them.
    """
    payload = _exact_dict(value, field="training snapshot", keys=_SNAPSHOT_KEYS_V1)
    is_shard = payload["schema_version"] == TRAINING_SHARD_SCHEMA_V1
    if payload["schema_version"] != TRAINING_SNAPSHOT_SCHEMA_V1 and not is_shard:
        raise TrainingSnapshotV1Error("training snapshot schema_version is invalid")

    split_names = payload["split_names"]
    if (
        type(split_names) is not list
        or len(split_names) < 2
        or any(type(name) is not str or not name for name in split_names)
        or len(set(split_names)) != len(split_names)
    ):
        raise TrainingSnapshotV1Error("training snapshot split_names must be unique nonempty strings")

    split_weights = payload["split_weights"]
    if (
        type(split_weights) is not list
        or len(split_weights) != len(split_names)
        or any(
            type(weight) is not float or not math.isfinite(weight) or weight <= 0.0
            for weight in split_weights
        )
    ):
        raise TrainingSnapshotV1Error(
            "training snapshot split_weights must be one finite positive float per split name"
        )

    examples = payload["examples"]
    if type(examples) is not list or not examples or len(examples) > MAX_TRAINING_SNAPSHOT_EXAMPLES_V1:
        raise TrainingSnapshotV1Error("training snapshot examples must be a bounded nonempty list")

    # Derived from the examples, never read from the declared block: a snapshot
    # must not be able to exempt a genuine leak by naming it ubiquitous itself.
    multiplicity, ubiquity_threshold, ubiquitous = _duplicate_cap_facts_v1(examples)

    component_split: dict[str, str] = {}
    record_ids: set[str] = set()
    prior_record_id = ""
    for index, raw in enumerate(examples):
        example = _exact_dict(raw, field=f"examples[{index}]", keys=_EXAMPLE_KEYS_V1)
        reject_forbidden_private_fields_v2(example)
        record_id = example["record_id"]
        if type(record_id) is not str or record_id in record_ids:
            raise TrainingSnapshotV1Error("training snapshot record_id must be unique")
        if record_id <= prior_record_id:
            raise TrainingSnapshotV1Error("training snapshot examples must be sorted by record_id")
        prior_record_id = record_id
        record_ids.add(record_id)
        if example["split"] not in split_names:
            raise TrainingSnapshotV1Error("training snapshot example has an unknown split")
        # One connected component must never straddle two splits.  A ubiquitous
        # position is exempt because it is a constant of the task rather than
        # something an episode reveals -- see `ubiquitous_near_duplicate_ids_v2`.
        near_duplicate = example["near_duplicate_id"]
        group_keys = [example["episode_id_hash"]]
        if near_duplicate not in ubiquitous:
            group_keys.append(near_duplicate)
        for group_key in group_keys:
            if type(group_key) is not str:
                raise TrainingSnapshotV1Error("training snapshot grouping hash must be a string")
            if is_shard:
                continue
            existing = component_split.setdefault(group_key, example["split"])
            if existing != example["split"]:
                raise TrainingSnapshotV1Error(
                    "training snapshot split leaks one grouping component across splits"
                )
        # The §9.3 duplicate cap must actually be in the weight the learner reads.
        pre_cap = example["pre_cap_quality_weight"]
        if type(pre_cap) is not float or not (0.0 < pre_cap <= 1.0):
            raise TrainingSnapshotV1Error(
                "training snapshot pre_cap_quality_weight must be a float in (0,1]"
            )
        if is_shard:
            continue
        expected_weight = pre_cap * _duplicate_scale_v1(multiplicity[near_duplicate])
        if example["example_quality_weight"] != expected_weight:
            raise TrainingSnapshotV1Error(
                "training snapshot example_quality_weight does not match the duplicate cap"
            )

    counts = payload["split_counts"]
    expected_counts = {
        name: sum(1 for item in examples if item["split"] == name) for name in split_names
    }
    if type(counts) is not dict or counts != expected_counts:
        raise TrainingSnapshotV1Error("training snapshot split_counts do not match its examples")

    declared_cap = _exact_dict(
        payload["duplicate_cap"], field="duplicate_cap", keys=_DUPLICATE_CAP_KEYS_V1
    )
    if declared_cap["max_near_duplicate_multiplicity"] != MAX_NEAR_DUPLICATE_MULTIPLICITY_V1:
        raise TrainingSnapshotV1Error(
            "training snapshot duplicate_cap declares the wrong multiplicity limit"
        )
    # The remaining four fields count things over the whole corpus.  A shard holds
    # a slice, so re-deriving them here would compare a corpus-wide block against
    # a fragment and reject a correct shard; `read_sharded_split_examples_v1`
    # compares the same block against every shard instead.
    if not is_shard:
        expected_cap = {
            "max_near_duplicate_multiplicity": MAX_NEAR_DUPLICATE_MULTIPLICITY_V1,
            "ubiquity_min_episodes": ubiquity_threshold,
            "ubiquitous_near_duplicate_ids": sorted(ubiquitous),
            "groups_capped": sum(
                1 for count in multiplicity.values() if count > MAX_NEAR_DUPLICATE_MULTIPLICITY_V1
            ),
            "records_capped": sum(
                1 for item in examples
                if _duplicate_scale_v1(multiplicity[item["near_duplicate_id"]]) != 1.0
            ),
        }
        if declared_cap != expected_cap:
            raise TrainingSnapshotV1Error(
                "training snapshot duplicate_cap does not match the examples it describes"
            )

    for field, keys in (("source_artifacts", _SOURCE_KEYS_V1), ("permissions", _PERMISSION_KEYS_V1)):
        rows = payload[field]
        if type(rows) is not list or not rows:
            raise TrainingSnapshotV1Error(f"training snapshot {field} must be a nonempty list")
        for index, row in enumerate(rows):
            _exact_dict(row, field=f"{field}[{index}]", keys=keys)

    if payload["snapshot_id"] != _snapshot_identity(payload):
        raise TrainingSnapshotV1Error("training snapshot snapshot_id does not verify")
    if payload["content_hash"] != _snapshot_content_hash(payload):
        raise TrainingSnapshotV1Error("training snapshot content_hash does not verify")
    return payload


def atomic_write_training_snapshot_v1(path: str | Path, snapshot: object) -> Path:
    """Publish one verified snapshot as canonical bytes without replacing a leaf."""
    payload = validate_training_snapshot_v1(snapshot)
    body = canonical_json_bytes_v2(payload, max_nodes=_MAX_SNAPSHOT_JSON_NODES_V1, max_bytes=MAX_TRAINING_SNAPSHOT_BYTES_V1)
    if len(body) > MAX_TRAINING_SNAPSHOT_BYTES_V1:
        raise TrainingSnapshotV1Error("training snapshot exceeds the publication byte cap")
    destination = Path(os.path.abspath(os.fspath(path)))
    if not destination.name:
        raise TrainingSnapshotV1Error("snapshot path must name a file")
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_text = tempfile.mkstemp(
        prefix=f".{destination.name}.tmp.", dir=destination.parent,
    )
    temporary = Path(temporary_text)
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    parent = os.open(destination.parent, os.O_RDONLY)
    try:
        os.fsync(parent)
    finally:
        os.close(parent)
    return destination


def read_training_snapshot_v1(path: str | Path) -> dict[str, object]:
    """Read and revalidate one published snapshot from exact canonical bytes."""
    body = Path(path).read_bytes()
    if len(body) > MAX_TRAINING_SNAPSHOT_BYTES_V1:
        raise TrainingSnapshotV1Error("training snapshot exceeds the publication byte cap")
    payload = validate_training_snapshot_v1(parse_canonical_json_bytes_v2(body, max_nodes=_MAX_SNAPSHOT_JSON_NODES_V1, max_bytes=MAX_TRAINING_SNAPSHOT_BYTES_V1))
    if canonical_json_bytes_v2(payload, max_nodes=_MAX_SNAPSHOT_JSON_NODES_V1, max_bytes=MAX_TRAINING_SNAPSHOT_BYTES_V1) != body:
        raise TrainingSnapshotV1Error("training snapshot bytes are not canonical")
    return payload


def snapshot_examples_for_split_v1(snapshot: object, split: str) -> tuple[dict[str, object], ...]:
    """Return the detached examples of one split in stable record order.

    Detachment is a deep copy, not a canonical-JSON round trip.  The round trip
    re-serialized and re-parsed every example purely to obtain a fresh object,
    which cost 62s on a 24,087-example snapshot that
    ``validate_training_snapshot_v1`` had *already* validated one line above --
    a full second validation of bytes just produced from validated values.  A
    deep copy gives the same isolation: the payload came from JSON, so it holds
    only JSON types, and the copy is structurally identical.
    """
    payload = validate_training_snapshot_v1(snapshot)
    if split not in payload["split_names"]:  # type: ignore[operator]
        raise TrainingSnapshotV1Error("unknown split name")
    return tuple(
        copy.deepcopy(example)
        for example in payload["examples"]  # type: ignore[union-attr]
        if example["split"] == split
    )


__all__ = [
    "DEFAULT_SPLIT_NAMES_V1", "DEFAULT_SPLIT_WEIGHTS_V1", "MAX_TRAINING_SNAPSHOT_BYTES_V1",
    "MAX_NEAR_DUPLICATE_MULTIPLICITY_V1",
    "MAX_TRAINING_SNAPSHOT_EXAMPLES_V1", "TRAINING_SNAPSHOT_SCHEMA_V1",
    "TRAINING_SHARD_SCHEMA_V1", "seal_sharded_corpus_v1", "seal_sharded_corpora_v1",
    "partition_record_files_v1",
    "TrainingSnapshotV1Error", "atomic_write_training_snapshot_v1",
    "build_training_snapshot_v1", "read_training_snapshot_v1",
    "snapshot_examples_for_split_v1", "validate_training_snapshot_v1",
    "SHARD_INDEX_SCHEMA_V1", "build_sharded_training_snapshots_v1",
    "build_sharded_training_snapshots_from_chunks_v1", "corpus_dataset_sha256_v1",
    "read_sharded_split_examples_v1",
]
