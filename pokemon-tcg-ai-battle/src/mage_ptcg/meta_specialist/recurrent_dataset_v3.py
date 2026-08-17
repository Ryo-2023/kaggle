"""Disk-backed full-corpus selection authority for recurrent BC v3.

The static Gate 1 slice is intentionally absent here.  This module qualifies
every sealed teacher line, keeps only fixed-size metadata on disk, derives the
episode/near-duplicate components with an integer union-find, and publishes a
streamable JSONL selection index anchored by a small root manifest.
"""

from __future__ import annotations

from array import array
import base64
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import subprocess
import tempfile
import time
from collections.abc import Iterator, Mapping
from dataclasses import asdict, dataclass

from mage_ptcg.meta_specialist.bc_trainer_v3 import BCExampleV3, RecurrentBCSequenceV3


_SCHEMA = "meta-specialist-recurrent-selection-v4"
_INDEX_SCHEMA = "meta-specialist-recurrent-selection-index-v1"
_SPLIT_SCHEMA = "meta-specialist-recurrent-split-summary-v1"
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_SHARD_RE = re.compile(r"dataset-[0-9]{4}\.jsonl\Z")
_SELECTION_RULE = "all-qualified-sealed-records-by-shard-line-v2-disk-spool"
_ROOT_KEYS = {
    "schema", "lane", "root", "qualification_time_utc", "snapshot_index_sha256",
    "dataset_snapshot_sha256", "teacher_manifest_sha256",
    "trusted_permission_bytes_b64", "trusted_permission_sha256", "vocabulary",
    "selection_rule", "records_total", "selection_index_path", "selection_index_sha256",
    "split", "manifest_sha256",
}
_SPLIT_KEYS = {
    "schema", "validation_fraction", "ubiquitous_keys", "ubiquitous_metadata",
    "components_total", "counts", "overlap_counters", "components_sha256",
}
_INDEX_KEYS = {
    "schema", "shard", "line", "record_id", "content_hash", "raw_line_sha256",
    "component_id", "partition",
}
_PREFLIGHT_V1_SCHEMA = "meta-specialist-recurrent-lane-preflight-v1"
_PREFLIGHT_V2_SCHEMA = "meta-specialist-recurrent-lane-preflight-v2"
_PREFLIGHT_V3_SCHEMA = "meta-specialist-recurrent-lane-preflight-v3"
_PREFLIGHT_SCHEMA = _PREFLIGHT_V3_SCHEMA
_PREFLIGHT_V1_KEYS = {
    "schema", "lane", "command_identity", "source_manifest_path",
    "source_manifest_file_sha256", "source_manifest_sha256", "snapshot_index_sha256",
    "teacher_manifest_sha256", "dataset_snapshot_sha256", "trusted_permission_sha256",
    "vocabulary", "qualification_time_utc", "records_total", "split",
    "original_index_sha256", "rebuilt_index_sha256", "frozen_index_path",
    "frozen_index_sha256", "chunks", "preflight_seconds", "receipt_sha256",
}
_R3_PROJECTION_SCHEMA = "meta-specialist-r3-projection-preflight-v2"
_R3_PROJECTION_KEYS = {
    "schema", "records_checked", "steps_checked", "aggregate_sha256",
}
_PREFLIGHT_V2_KEYS = _PREFLIGHT_V1_KEYS | {"r3_projection"}
_PREFLIGHT_KEYS = _PREFLIGHT_V2_KEYS | {"frozen_snapshot_path"}


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, allow_nan=False, sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _hash(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _nofollow_read_flags_v3() -> int:
    """Never silently weaken symlink protection on a platform lacking O_NOFOLLOW."""
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if type(nofollow) is not int:
        raise ValueError("recurrent verified file access requires O_NOFOLLOW support")
    return os.O_RDONLY | nofollow


def _descriptor_identity_v3(value: object) -> tuple[int, int, int, int, int, int]:
    """Stable descriptor identity; atime is deliberately excluded as a read side effect."""
    return (
        value.st_dev, value.st_ino, value.st_mode, value.st_size, value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _regular_file_bytes_v3(path: Path, *, expected_sha256: str | None, name: str) -> bytes:
    """Read/hash a regular file from one non-symlink descriptor and retain its identity."""
    flags = _nofollow_read_flags_v3()
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ValueError(f"{name} cannot be opened without following a symlink") from exc
    with os.fdopen(descriptor, "rb", closefd=True) as handle:
        before = os.fstat(handle.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise ValueError(f"{name} is not a regular file")
        raw = handle.read()
        after = os.fstat(handle.fileno())
        identity_before = _descriptor_identity_v3(before)
        identity_after = _descriptor_identity_v3(after)
        if identity_before != identity_after or len(raw) != before.st_size:
            raise ValueError(f"{name} descriptor changed during read")
    if expected_sha256 is not None and hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError(f"{name} external file SHA-256 does not match")
    return raw


def _regular_file_sha256_v3(path: Path, *, expected_sha256: str, name: str) -> str:
    """Stream-hash a potentially large sidecar through one O_NOFOLLOW descriptor."""
    _require_digest(expected_sha256, field=f"expected {name} SHA-256")
    try:
        descriptor = os.open(path, _nofollow_read_flags_v3())
    except OSError as exc:
        raise ValueError(f"{name} cannot be opened without following a symlink") from exc
    with os.fdopen(descriptor, "rb", closefd=True) as handle:
        before = os.fstat(handle.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise ValueError(f"{name} is not a regular file")
        digest = hashlib.sha256()
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
        after = os.fstat(handle.fileno())
        if _descriptor_identity_v3(before) != _descriptor_identity_v3(after):
            raise ValueError(f"{name} descriptor changed during SHA-256 read")
    if digest.hexdigest() != expected_sha256:
        raise ValueError(f"{name} external file SHA-256 does not match")
    return digest.hexdigest()


def _require_digest(value: object, *, field: str) -> str:
    if type(value) is not str or _HEX64.fullmatch(value) is None:
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")
    return value


def _strict_root(value: object) -> Path:
    if type(value) is not str or not value:
        raise ValueError("recurrent selection root is invalid")
    return Path(value).resolve()


def _strict_shard_path(root: Path, shard: object) -> Path:
    if type(shard) is not str or _SHARD_RE.fullmatch(shard) is None:
        raise ValueError("recurrent selection shard must be a strict dataset-NNNN.jsonl basename")
    path = root / shard
    if path.parent != root:
        raise ValueError("recurrent selection shard escapes its sealed root")
    return path


def _assert_nonsymlink_directory_path_v3(path: Path, *, name: str) -> Path:
    """Validate every unresolved path component before any ``resolve`` call."""
    unresolved = path if path.is_absolute() else Path.cwd() / path
    if ".." in unresolved.parts:
        raise ValueError(f"{name} contains parent traversal")
    current = Path(unresolved.anchor)
    final_stat: os.stat_result | None = None
    for part in unresolved.parts[1:]:
        current /= part
        try:
            final_stat = os.lstat(current)
        except OSError as exc:
            raise ValueError(f"{name} path component is unavailable") from exc
        if stat.S_ISLNK(final_stat.st_mode):
            raise ValueError(f"{name} path component must not be a symlink")
    if final_stat is None or not stat.S_ISDIR(final_stat.st_mode):
        raise ValueError(f"{name} must be a directory")
    return unresolved


def _assert_nonsymlink_snapshot_children_v3(
    root: Path, snapshot: Mapping[str, object],
) -> None:
    chunks = snapshot.get("dataset_chunks")
    if type(chunks) is not list or not chunks:
        raise ValueError("snapshot index has no closed dataset chunks")
    for chunk in sorted(chunks, key=lambda item: item["path"] if type(item) is dict else ""):
        if type(chunk) is not dict:
            raise ValueError("snapshot chunk is malformed")
        path = _snapshot_chunk_shard_path(root, chunk.get("path"))
        try:
            value = os.lstat(path)
        except OSError as exc:
            raise ValueError("recurrent prepared snapshot child is unavailable") from exc
        if stat.S_ISLNK(value.st_mode) or not stat.S_ISREG(value.st_mode):
            raise ValueError("recurrent prepared snapshot child must be a non-symlink regular file")


def _snapshot_chunk_shard_path(root: Path, declared_path: object) -> Path:
    """Normalize only a sealed snapshot chunk path to its physical basename.

    The snapshot authority may retain its collection-root relative prefix.  As
    in Gate 1, that prefix is not used for filesystem traversal: absolute and
    parent-escaping declarations fail, then the strict dataset basename is
    resolved directly below this recurrent root.
    """
    if type(declared_path) is not str or not declared_path:
        raise ValueError("snapshot dataset chunk path is malformed")
    declared = Path(declared_path)
    if declared.is_absolute() or ".." in declared.parts:
        raise ValueError("snapshot dataset chunk path escapes its sealed root")
    return _strict_shard_path(root, declared.name)


def _read_local_canonical_object(path: Path, *, name: str) -> dict[str, object]:
    from mage_ptcg.meta_specialist.local_dataset_v2 import parse_canonical_json_bytes_v2

    try:
        value = parse_canonical_json_bytes_v2(path.read_bytes())
    except Exception as exc:
        raise ValueError(f"{name} is not canonical bounded JSON") from exc
    if type(value) is not dict:
        raise ValueError(f"{name} must be an object")
    return value


def _read_outer_authority_object(path: Path, *, name: str) -> dict[str, object]:
    """Parse an outer authority strictly while pinning its original file bytes.

    Snapshot and teacher manifests are pre-existing authorities: Gate 1 accepts
    their strict JSON payloads and binds their raw file SHA separately.  Their
    outer formatting is therefore not a permission authority.  Nested
    ``permission_manifest`` bytes remain canonicalized below before trust use.
    """
    from mage_ptcg.meta_specialist.local_dataset_v2 import canonical_json_bytes_v2
    from mage_ptcg.meta_specialist.training_snapshot_v1 import (
        MAX_TRAINING_SNAPSHOT_BYTES_V1,
        MAX_TRAINING_SNAPSHOT_EXAMPLES_V1,
    )

    raw = path.read_bytes()
    if len(raw) > MAX_TRAINING_SNAPSHOT_BYTES_V1:
        raise ValueError(f"{name} exceeds its bounded authority byte limit")
    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result
    def reject_nonfinite(value: str) -> object:
        raise ValueError(f"non-finite JSON value {value!r}")
    try:
        parsed = json.loads(
            raw.decode("utf-8"), object_pairs_hook=reject_duplicates,
            parse_constant=reject_nonfinite,
        )
        # Reuse the repository's finite/depth/node validator, but deliberately
        # do not compare its canonical bytes to the independently SHA-pinned
        # authority file.
        canonical_json_bytes_v2(
            parsed, max_nodes=MAX_TRAINING_SNAPSHOT_EXAMPLES_V1 * 64,
            max_bytes=MAX_TRAINING_SNAPSHOT_BYTES_V1,
        )
    except Exception as exc:
        raise ValueError(f"{name} is not strict bounded JSON") from exc
    if type(parsed) is not dict:
        raise ValueError(f"{name} must be an object")
    return parsed


def _load_authorities(root: Path) -> tuple[dict[str, object], Path, Path, dict[str, object], bytes, object]:
    from mage_ptcg.meta_specialist.local_dataset_v2 import build_trusted_permission_set_v1, canonical_json_bytes_v2

    snapshot_path = root / "snapshot_index.json"
    teacher_path = root / "teacher_dataset_manifest.json"
    if not snapshot_path.is_file() or not teacher_path.is_file():
        raise FileNotFoundError("recurrent root lacks sealed snapshot_index.json or teacher_dataset_manifest.json")
    snapshot = _read_outer_authority_object(snapshot_path, name="snapshot index")
    if snapshot.get("schema_version") != "specialist-training-snapshot-index-v1" or type(snapshot.get("dataset_snapshot_sha256")) is not str:
        raise ValueError("snapshot index has an invalid schema or dataset identity")
    teacher = _read_outer_authority_object(teacher_path, name="teacher manifest")
    permission = teacher.get("permission_manifest")
    if type(permission) is not dict:
        raise ValueError("teacher manifest lacks a trusted permission manifest")
    permission_bytes = canonical_json_bytes_v2(permission)
    try:
        trusted = build_trusted_permission_set_v1((permission_bytes,))
    except Exception as exc:
        raise ValueError("teacher permission manifest is not trusted") from exc
    return snapshot, snapshot_path, teacher_path, permission, permission_bytes, trusted


def _validate_snapshot_bytes(root: Path, snapshot: Mapping[str, object]) -> None:
    from mage_ptcg.meta_specialist.representation_benchmark_v3 import validate_gate_snapshot_v3

    validate_gate_snapshot_v3(root, snapshot)


def _job_scratch_parent(output_path: Path) -> Path:
    """Create a non-symlinked, output-owned parent for all sort spill files."""
    output_parent = output_path.parent.resolve()
    output_parent.mkdir(parents=True, exist_ok=True)
    candidate = output_parent / "recurrent-spool"
    candidate.mkdir(exist_ok=True)
    resolved = candidate.resolve()
    if resolved.parent != output_parent:
        raise ValueError("recurrent scratch parent escapes the output directory")
    return resolved


def _sort(source: Path, destination: Path, *keys: str, scratch: Path) -> None:
    """Use an external stable disk sort; no corpus-sized Python list is created."""
    if shutil.which("sort") is None:
        raise RuntimeError("full recurrent split requires the system sort utility")
    resolved_scratch = scratch.resolve()
    if not resolved_scratch.is_dir():
        raise ValueError("recurrent sort scratch directory is unavailable")
    env = {"LC_ALL": "C", "LANG": "C", "PATH": os.defpath, "TMPDIR": str(resolved_scratch)}
    with destination.open("xb") as handle:
        subprocess.run(
            ["sort", "-s", "-t", "\t", *keys, str(source)],
            check=True, stdout=handle, env=env,
        )


def _tsv(parts: tuple[object, ...]) -> bytes:
    return ("\t".join(str(item) for item in parts) + "\n").encode("ascii")


def _parse_tsv(raw: bytes, *, fields: int, name: str) -> list[str]:
    try:
        parts = raw.rstrip(b"\n").decode("ascii").split("\t")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{name} has non-ASCII metadata") from exc
    if len(parts) != fields or any(not part for part in parts):
        raise ValueError(f"{name} is malformed")
    return parts


def _effective_ubiquitous(snapshot: Mapping[str, object]) -> tuple[frozenset[str], int]:
    cap = snapshot.get("duplicate_cap")
    if type(cap) is not dict or type(cap.get("ubiquitous_near_duplicate_ids")) is not list:
        raise ValueError("snapshot index lacks duplicate-cap authority")
    threshold = cap.get("ubiquity_min_episodes")
    keys = cap["ubiquitous_near_duplicate_ids"]
    if type(threshold) is not int or threshold < 1 or any(type(key) is not str or not key for key in keys):
        raise ValueError("snapshot duplicate-cap authority is malformed")
    return frozenset(keys), threshold


def _stream_metadata_spool(
    root: Path, *, snapshot: Mapping[str, object], permission: Mapping[str, object],
    trusted: object, qualification_time_utc: str, vocabulary: object, destination: Path,
) -> int:
    """Qualify each physical line and write only fixed metadata to disk."""
    from mage_ptcg.meta_specialist.local_dataset_v2 import (
        MAX_LOCAL_RECORD_BYTES_V2,
        parse_canonical_json_bytes_v2,
        require_qualified_training_record_v2,
    )

    chunks = snapshot.get("dataset_chunks")
    if type(chunks) is not list or not chunks:
        raise ValueError("snapshot index has no closed dataset chunks")
    row_id = 0
    with destination.open("xb") as spool:
        for chunk in sorted(chunks, key=lambda item: item["path"] if type(item) is dict else ""):
            if type(chunk) is not dict or type(chunk.get("path")) is not str:
                raise ValueError("snapshot dataset chunk is malformed")
            path = _snapshot_chunk_shard_path(root, chunk["path"])
            with path.open("rb") as source:
                line_no = 0
                while True:
                    raw = source.readline(MAX_LOCAL_RECORD_BYTES_V2 + 2)
                    if not raw:
                        break
                    line_no += 1
                    if not raw.endswith(b"\n") or raw == b"\n" or len(raw) > MAX_LOCAL_RECORD_BYTES_V2 + 1:
                        raise ValueError("snapshot shard has an invalid bounded JSONL line")
                    try:
                        record = parse_canonical_json_bytes_v2(raw[:-1])
                        model_payload, _labels = require_qualified_training_record_v2(
                            record, vocabulary=vocabulary, trusted_permissions=trusted,
                            qualification_time_utc=qualification_time_utc,
                        )
                    except Exception as exc:
                        raise ValueError("full recurrent corpus contains an unqualified record or permission") from exc
                    del model_payload
                    source_info = record.get("source") if type(record) is dict else None
                    if (
                        type(source_info) is not dict
                        or source_info.get("artifact_sha256") != permission.get("artifact_sha256")
                        or source_info.get("permission_manifest_id") != permission.get("permission_manifest_id")
                    ):
                        raise ValueError("qualified recurrent record permission does not match teacher authority")
                    record_id = record.get("record_id")
                    content_hash = record.get("content_hash")
                    episode = record.get("episode_id_hash")
                    near = record.get("near_duplicate_id")
                    if any(type(value) is not str or not value for value in (record_id, content_hash, episode, near)):
                        raise ValueError("qualified recurrent record metadata is invalid")
                    _require_digest(record_id, field="qualified recurrent record_id")
                    _require_digest(content_hash, field="qualified recurrent content_hash")
                    _require_digest(episode, field="qualified recurrent episode_id_hash")
                    _require_digest(near, field="qualified recurrent near_duplicate_id")
                    spool.write(_tsv((row_id, path.name, line_no, record_id, content_hash, hashlib.sha256(raw).hexdigest(), episode, near)))
                    row_id += 1
    if row_id == 0:
        raise ValueError("full recurrent corpus has no qualified records")
    return row_id


def _assert_unique_record_ids(metadata: Path, temp: Path) -> None:
    ordered = temp / "metadata-by-record-id.tsv"
    _sort(metadata, ordered, "-k4,4", scratch=temp)
    previous: str | None = None
    with ordered.open("rb") as handle:
        for raw in handle:
            fields = _parse_tsv(raw, fields=8, name="qualified recurrent metadata")
            if fields[3] == previous:
                raise ValueError("qualified recurrent records have duplicate record IDs")
            previous = fields[3]


def _union(parent: array, left: int, right: int) -> None:
    def find(value: int) -> int:
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = parent[value]
        return value
    left_root, right_root = find(left), find(right)
    if left_root != right_root:
        parent[max(left_root, right_root)] = min(left_root, right_root)


def _build_components(metadata: Path, *, total: int, ubiquitous: frozenset[str], temp: Path) -> array:
    """Union rows by sorted episode and non-ubiquitous near-duplicate keys."""
    parent = array("Q", range(total))
    keys = temp / "group-keys.tsv"
    with metadata.open("rb") as source, keys.open("xb") as output:
        for raw in source:
            row, _shard, _line, _record, _content, _rawsha, episode, near = _parse_tsv(raw, fields=8, name="qualified recurrent metadata")
            output.write(_tsv(("e", episode, row)))
            if near not in ubiquitous:
                output.write(_tsv(("n", near, row)))
    ordered = temp / "group-keys-sorted.tsv"
    _sort(keys, ordered, "-k1,1", "-k2,2", "-k3,3n", scratch=temp)
    previous_key: tuple[str, str] | None = None
    anchor: int | None = None
    previous_row = -1
    rows_in_episode = 0
    episode_min = -1
    for raw in ordered.open("rb"):
        kind, key, row_text = _parse_tsv(raw, fields=3, name="recurrent grouping key")
        row = int(row_text)
        group_key = (kind, key)
        if group_key != previous_key:
            if previous_key is not None and previous_key[0] == "e" and previous_row - episode_min + 1 != rows_in_episode:
                raise ValueError("full recurrent corpus reopens an episode after another episode")
            previous_key, anchor = group_key, row
            previous_row, rows_in_episode, episode_min = row, 1, row
            continue
        assert anchor is not None
        _union(parent, anchor, row)
        if kind == "e":
            previous_row = row
            rows_in_episode += 1
    if previous_key is not None and previous_key[0] == "e" and previous_row - episode_min + 1 != rows_in_episode:
        raise ValueError("full recurrent corpus reopens an episode after another episode")
    for index in range(total):
        root = index
        while parent[root] != root:
            parent[root] = parent[parent[root]]
            root = parent[root]
        parent[index] = root
    return parent


def _component_members(metadata: Path, *, parent: array, temp: Path) -> tuple[Path, Path]:
    with_roots = temp / "metadata-with-roots.tsv"
    with metadata.open("rb") as source, with_roots.open("xb") as output:
        for raw in source:
            fields = _parse_tsv(raw, fields=8, name="qualified recurrent metadata")
            output.write(_tsv((parent[int(fields[0])], *fields)))
    ordered = temp / "metadata-by-component.tsv"
    _sort(with_roots, ordered, "-k1,1n", "-k5,5", scratch=temp)
    members = temp / "component-members.tsv"
    components = temp / "components.tsv"
    current_root: str | None = None
    current_count = 0
    digest: hashlib._Hash | None = None
    first = True
    with ordered.open("rb") as source, members.open("xb") as member_out, components.open("xb") as component_out:
        for raw in source:
            root, row, shard, line, record_id, content, rawsha, episode, near = _parse_tsv(raw, fields=9, name="component metadata")
            if root != current_root:
                if digest is not None:
                    digest.update(b"]")
                    component_out.write(_tsv((digest.hexdigest(), current_root, current_count)))
                current_root, current_count, digest, first = root, 0, hashlib.sha256(), True
                digest.update(b"[")
            assert digest is not None
            if not first:
                digest.update(b",")
            digest.update(json.dumps(record_id, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
            first = False
            current_count += 1
            member_out.write(_tsv((root, row, shard, line, record_id, content, rawsha, episode, near)))
        if digest is not None:
            digest.update(b"]")
            component_out.write(_tsv((digest.hexdigest(), current_root, current_count)))
    return members, components


def _assign_partitions(components: Path, *, total: int, ubiquitous: frozenset[str], threshold: int, temp: Path) -> tuple[Path, dict[str, object]]:
    ordered = temp / "components-by-hash.tsv"
    _sort(components, ordered, "-k1,1", scratch=temp)
    component_total = sum(1 for _ in ordered.open("rb"))
    if component_total < 2:
        raise ValueError("full recurrent split would produce fewer than two leak components")
    target = max(1, int(round(total * 0.2)))
    assignments = temp / "component-assignments.tsv"
    validation = 0
    train = 0
    components_digest = hashlib.sha256()
    with ordered.open("rb") as source, assignments.open("xb") as output:
        for raw in source:
            component_id, root, size_text = _parse_tsv(raw, fields=3, name="recurrent component")
            size = int(size_text)
            if size < 1:
                raise ValueError("recurrent component size is invalid")
            partition = "validation" if validation < target and validation + size < total else "train"
            if partition == "validation":
                validation += size
            else:
                train += size
            output.write(_tsv((root, component_id, partition)))
            components_digest.update(raw)
    if not train or not validation or train + validation != total:
        raise ValueError("full recurrent split would produce an empty or incomplete partition")
    summary: dict[str, object] = {
        "schema": _SPLIT_SCHEMA, "validation_fraction": 0.2,
        "ubiquitous_keys": sorted(ubiquitous),
        "ubiquitous_metadata": {"rule_version": "snapshot-index-duplicate-cap-v1", "threshold": threshold},
        "components_total": component_total,
        "counts": {"train": train, "validation": validation},
        "overlap_counters": {"episode_overlap": 0, "near_duplicate_overlap": 0},
        "components_sha256": components_digest.hexdigest(),
    }
    return assignments, summary


def _write_selection_index(members: Path, assignments: Path, *, destination: Path, temp: Path) -> tuple[int, str]:
    ordered_assignments = temp / "component-assignments-by-root.tsv"
    _sort(assignments, ordered_assignments, "-k1,1n", scratch=temp)
    joined = temp / "selection-unsorted.tsv"
    with members.open("rb") as left, ordered_assignments.open("rb") as right, joined.open("xb") as output:
        assignment = right.readline()
        for raw in left:
            root, row, shard, line, record_id, content, rawsha, _episode, _near = _parse_tsv(raw, fields=9, name="component member")
            while assignment:
                assigned_root, component_id, partition = _parse_tsv(assignment, fields=3, name="component assignment")
                if int(assigned_root) < int(root):
                    assignment = right.readline()
                    continue
                break
            if not assignment or assigned_root != root:
                raise ValueError("component assignment is incomplete")
            output.write(_tsv((shard, line, record_id, content, rawsha, component_id, partition)))
    ordered = temp / "selection-by-physical-order.tsv"
    _sort(joined, ordered, "-k1,1", "-k2,2n", scratch=temp)
    count = 0
    digest = hashlib.sha256()
    with ordered.open("rb") as source, destination.open("xb") as output:
        for raw in source:
            shard, line, record_id, content, rawsha, component_id, partition = _parse_tsv(raw, fields=7, name="recurrent selection")
            body = _canonical({
                "schema": _INDEX_SCHEMA, "shard": shard, "line": int(line),
                "record_id": record_id, "content_hash": content,
                "raw_line_sha256": rawsha, "component_id": component_id,
                "partition": partition,
            }) + b"\n"
            output.write(body)
            digest.update(body)
            count += 1
    return count, digest.hexdigest()


def _atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.tmp-{os.getpid()}")
    try:
        with source.open("rb") as input_handle, temporary.open("xb") as output_handle:
            shutil.copyfileobj(input_handle, output_handle, length=1024 * 1024)
            output_handle.flush()
            os.fsync(output_handle.fileno())
        os.replace(temporary, destination)
        directory_fd = os.open(destination.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


def _compile_selection_index(
    root: Path, *, snapshot: Mapping[str, object], permission: Mapping[str, object],
    trusted: object, qualification_time_utc: str, vocabulary: object,
    destination: Path, temporary_directory: Path,
) -> tuple[int, str, dict[str, object]]:
    """Compile one full selection index using only disk-backed corpus metadata."""
    metadata = temporary_directory / "qualified-metadata.tsv"
    total = _stream_metadata_spool(
        root, snapshot=snapshot, permission=permission, trusted=trusted,
        qualification_time_utc=qualification_time_utc, vocabulary=vocabulary,
        destination=metadata,
    )
    _assert_unique_record_ids(metadata, temporary_directory)
    ubiquitous, threshold = _effective_ubiquitous(snapshot)
    parent = _build_components(metadata, total=total, ubiquitous=ubiquitous, temp=temporary_directory)
    members, components = _component_members(metadata, parent=parent, temp=temporary_directory)
    assignments, summary = _assign_partitions(
        components, total=total, ubiquitous=ubiquitous, threshold=threshold,
        temp=temporary_directory,
    )
    indexed, index_sha = _write_selection_index(
        members, assignments, destination=destination, temp=temporary_directory,
    )
    if indexed != total:
        raise ValueError("full recurrent selection index does not cover every qualified record")
    return total, index_sha, summary


def _atomic_write_json(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        with temporary.open("xb") as handle:
            handle.write(_canonical(payload))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _index_path_for_manifest(path: Path) -> Path:
    if not path.name:
        raise ValueError("recurrent selection manifest path must name a file")
    return path.with_name(f"{path.name}.selection.jsonl")


def _parse_index_entry(raw: bytes) -> dict[str, object]:
    if not raw.endswith(b"\n") or raw == b"\n":
        raise ValueError("recurrent selection index has an invalid line")
    try:
        entry = json.loads(raw[:-1].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("recurrent selection index has invalid JSON") from exc
    if type(entry) is not dict or set(entry) != _INDEX_KEYS or _canonical(entry) != raw[:-1]:
        raise ValueError("recurrent selection index entry has an invalid closed schema")
    if entry["schema"] != _INDEX_SCHEMA or type(entry["line"]) is not int or entry["line"] < 1:
        raise ValueError("recurrent selection index schema/line is invalid")
    if type(entry["shard"]) is not str or _SHARD_RE.fullmatch(entry["shard"]) is None:
        raise ValueError("recurrent selection index shard is invalid")
    for field in ("record_id", "content_hash", "raw_line_sha256", "component_id"):
        _require_digest(entry[field], field=f"recurrent selection index {field}")
    if entry["partition"] not in {"train", "validation"}:
        raise ValueError("recurrent selection index partition is invalid")
    return entry


def _read_index(path: Path) -> Iterator[dict[str, object]]:
    previous: tuple[str, int] | None = None
    with path.open("rb") as handle:
        yield from _read_index_handle(handle)


def _read_index_handle(handle: object) -> Iterator[dict[str, object]]:
    """Read an index through an already pinned descriptor, never by path."""
    previous: tuple[str, int] | None = None
    for raw in handle:  # type: ignore[union-attr]
        entry = _parse_index_entry(raw)
        physical = (entry["shard"], entry["line"])
        if previous is not None and physical <= previous:
            raise ValueError("recurrent selection index must be in strict physical order")
        previous = physical
        yield entry


def _validate_index(path: Path, *, expected_sha: str, expected_records: int, split: Mapping[str, object]) -> None:
    if not path.is_file() or _file_hash(path) != expected_sha:
        raise ValueError("recurrent selection index file SHA-256 does not match root manifest")
    counts = {"train": 0, "validation": 0}
    seen = 0
    for entry in _read_index(path):
        counts[entry["partition"]] += 1
        seen += 1
    if seen != expected_records or counts != split.get("counts"):
        raise ValueError("recurrent selection index rows/counts do not match root manifest")


def _read_root_manifest(
    path: Path, *, raw: bytes | None = None, validate_sidecar: bool = True,
) -> dict[str, object]:
    if raw is None:
        payload = _read_local_canonical_object(path, name="recurrent selection manifest")
    else:
        from mage_ptcg.meta_specialist.local_dataset_v2 import parse_canonical_json_bytes_v2

        try:
            payload = parse_canonical_json_bytes_v2(raw)
        except Exception as exc:
            raise ValueError("recurrent selection manifest is not canonical bounded JSON") from exc
        if type(payload) is not dict:
            raise ValueError("recurrent selection manifest must be an object")
    if set(payload) != _ROOT_KEYS or payload.get("schema") != _SCHEMA:
        raise ValueError("recurrent selection manifest has an invalid closed schema")
    manifest_sha = _require_digest(payload.get("manifest_sha256"), field="recurrent manifest_sha256")
    if _hash({key: value for key, value in payload.items() if key != "manifest_sha256"}) != manifest_sha:
        raise ValueError("recurrent selection manifest self hash does not verify")
    if type(payload["lane"]) is not str or not payload["lane"] or type(payload["qualification_time_utc"]) is not str:
        raise ValueError("recurrent selection lane/time is invalid")
    _strict_root(payload["root"])
    for field in ("snapshot_index_sha256", "dataset_snapshot_sha256", "teacher_manifest_sha256", "trusted_permission_sha256", "selection_index_sha256"):
        _require_digest(payload[field], field=field)
    if payload["selection_rule"] != _SELECTION_RULE or type(payload["records_total"]) is not int or payload["records_total"] < 1:
        raise ValueError("recurrent selection rule/count is invalid")
    index_name = payload["selection_index_path"]
    if type(index_name) is not str or Path(index_name).name != index_name or not index_name.endswith(".selection.jsonl"):
        raise ValueError("recurrent selection index path is invalid")
    split = payload["split"]
    if type(split) is not dict or set(split) != _SPLIT_KEYS or split.get("schema") != _SPLIT_SCHEMA:
        raise ValueError("recurrent selection split summary is invalid")
    if split.get("validation_fraction") != 0.2 or split.get("overlap_counters") != {"episode_overlap": 0, "near_duplicate_overlap": 0}:
        raise ValueError("recurrent selection split summary has invalid leakage metadata")
    if type(split.get("components_total")) is not int or split["components_total"] < 2:
        raise ValueError("recurrent selection split component count is invalid")
    _require_digest(split.get("components_sha256"), field="recurrent components_sha256")
    if type(split.get("counts")) is not dict or set(split["counts"]) != {"train", "validation"} or any(type(value) is not int or value < 1 for value in split["counts"].values()):
        raise ValueError("recurrent selection split counts are invalid")
    if sum(split["counts"].values()) != payload["records_total"]:
        raise ValueError("recurrent selection split counts do not cover records_total")
    if type(split.get("ubiquitous_keys")) is not list or type(split.get("ubiquitous_metadata")) is not dict:
        raise ValueError("recurrent selection ubiquitous metadata is invalid")
    try:
        permission_bytes = base64.b64decode(payload["trusted_permission_bytes_b64"], validate=True)
    except Exception as exc:
        raise ValueError("recurrent selection trusted permission bytes are invalid") from exc
    if hashlib.sha256(permission_bytes).hexdigest() != payload["trusted_permission_sha256"]:
        raise ValueError("recurrent selection trusted permission bytes SHA mismatched")
    if validate_sidecar:
        _validate_index(path.parent / index_name, expected_sha=payload["selection_index_sha256"], expected_records=payload["records_total"], split=split)
    return payload


def _assert_manifest_authorities(
    payload: Mapping[str, object], *, validate_physical_snapshot: bool = True,
) -> tuple[Path, dict[str, object], Mapping[str, object], object, object]:
    from mage_ptcg.meta_specialist.local_dataset_v2 import build_trusted_permission_set_v1
    from mage_ptcg.meta_specialist.representation_benchmark_v3 import _load_production_vocabulary_v3, _production_vocabulary_identity_v3

    root = _strict_root(payload["root"])
    snapshot, snapshot_path, teacher_path, permission, permission_bytes, _trusted = _load_authorities(root)
    if _file_hash(snapshot_path) != payload["snapshot_index_sha256"] or _file_hash(teacher_path) != payload["teacher_manifest_sha256"]:
        raise ValueError("recurrent selection snapshot or teacher authority bytes changed")
    if snapshot["dataset_snapshot_sha256"] != payload["dataset_snapshot_sha256"]:
        raise ValueError("recurrent selection dataset snapshot identity changed")
    pinned = base64.b64decode(payload["trusted_permission_bytes_b64"], validate=True)
    if permission_bytes != pinned:
        raise ValueError("recurrent selection teacher permission bytes changed")
    trusted = build_trusted_permission_set_v1((pinned,))
    vocabulary = _load_production_vocabulary_v3()
    if payload["vocabulary"] != _production_vocabulary_identity_v3():
        raise ValueError("recurrent selection production vocabulary identity changed")
    if validate_physical_snapshot:
        _validate_snapshot_bytes(root, snapshot)
    return root, snapshot, permission, trusted, vocabulary


def build_recurrent_selection_manifest_v3(
    root: str | Path, *, lane: str, qualification_time_utc: str, output_path: str | Path,
) -> dict[str, object]:
    from mage_ptcg.meta_specialist.representation_benchmark_v3 import _load_production_vocabulary_v3, _production_vocabulary_identity_v3

    if type(lane) is not str or not lane or type(qualification_time_utc) is not str:
        raise ValueError("lane and qualification_time_utc are required")
    root_path = Path(root).resolve()
    output = Path(output_path)
    snapshot, snapshot_path, teacher_path, permission, permission_bytes, trusted = _load_authorities(root_path)
    _validate_snapshot_bytes(root_path, snapshot)
    index = _index_path_for_manifest(output)
    scratch_parent = _job_scratch_parent(output)
    with tempfile.TemporaryDirectory(prefix="recurrent-selection-", dir=scratch_parent) as directory:
        job_scratch = Path(directory).resolve()
        if job_scratch.parent != scratch_parent:
            raise ValueError("recurrent selection scratch directory escapes its job parent")
        planned_index = job_scratch / "selection.jsonl"
        total, index_sha, split = _compile_selection_index(
            root_path, snapshot=snapshot, permission=permission, trusted=trusted,
            qualification_time_utc=qualification_time_utc, vocabulary=_load_production_vocabulary_v3(),
            destination=planned_index, temporary_directory=job_scratch,
        )
        _atomic_copy(planned_index, index)
    payload: dict[str, object] = {
        "schema": _SCHEMA, "lane": lane, "root": str(root_path),
        "qualification_time_utc": qualification_time_utc,
        "snapshot_index_sha256": _file_hash(snapshot_path),
        "dataset_snapshot_sha256": snapshot["dataset_snapshot_sha256"],
        "teacher_manifest_sha256": _file_hash(teacher_path),
        "trusted_permission_bytes_b64": base64.b64encode(permission_bytes).decode("ascii"),
        "trusted_permission_sha256": hashlib.sha256(permission_bytes).hexdigest(),
        "vocabulary": _production_vocabulary_identity_v3(),
        "selection_rule": _SELECTION_RULE, "records_total": total,
        "selection_index_path": index.name, "selection_index_sha256": index_sha,
        "split": split,
    }
    payload["manifest_sha256"] = _hash(payload)
    _atomic_write_json(output, payload)
    reloaded = read_recurrent_selection_manifest_v3(output)
    if reloaded != payload:
        raise RuntimeError("recurrent selection manifest atomic reload differs from written bytes")
    return payload


def read_recurrent_selection_manifest_v3(path: str | Path) -> dict[str, object]:
    return _read_root_manifest(Path(path))


@dataclass(frozen=True, slots=True)
class VerifiedRecurrentRecordAuthorityV3:
    """Representation-neutral result of reproducing one full selection/split."""

    manifest_sha256: str
    selection_index_sha256: str
    records_total: int
    lane: str
    split: Mapping[str, object]
    chunks: tuple[Mapping[str, str], ...]


@dataclass(frozen=True, slots=True)
class RecurrentRecordAuthorityRowV3:
    """One requalified physical record joined to its frozen split identity."""

    record: Mapping[str, object]
    model_payload: object
    shard: str
    line: int
    record_id: str
    content_hash: str
    raw_line_sha256: str
    component_id: str
    partition: str


def _generic_record_authorities_v3(
    manifest_path: Path, *, expected_manifest_file_sha256: str,
) -> tuple[dict[str, object], Path, dict[str, object], Mapping[str, object], object, object]:
    """Open only non-symlink authority paths before representation-neutral use."""
    parent = _assert_nonsymlink_directory_path_v3(
        manifest_path.parent, name="recurrent record authority manifest directory",
    )
    manifest_file = parent / manifest_path.name
    raw = _regular_file_bytes_v3(
        manifest_file, expected_sha256=expected_manifest_file_sha256,
        name="recurrent record authority manifest",
    )
    manifest = _read_root_manifest(manifest_file, raw=raw, validate_sidecar=False)
    unresolved_root = _assert_nonsymlink_directory_path_v3(
        Path(manifest["root"]), name="recurrent record authority root",
    )
    root = unresolved_root.resolve()
    for basename, field in (
        ("snapshot_index.json", "snapshot_index_sha256"),
        ("teacher_dataset_manifest.json", "teacher_manifest_sha256"),
    ):
        _regular_file_bytes_v3(
            root / basename, expected_sha256=manifest[field],
            name=f"recurrent record authority {basename}",
        )
    root, snapshot, permission, trusted, vocabulary = _assert_manifest_authorities(
        manifest, validate_physical_snapshot=False,
    )
    _assert_nonsymlink_snapshot_children_v3(root, snapshot)
    _validate_snapshot_bytes(root, snapshot)
    return manifest, root, snapshot, permission, trusted, vocabulary


def verify_recurrent_record_authority_v3(
    manifest_path: str | Path, *, expected_manifest_file_sha256: str,
) -> VerifiedRecurrentRecordAuthorityV3:
    """Reproduce the sealed selection/split once without running an R3 projection."""
    _require_digest(
        expected_manifest_file_sha256,
        field="expected recurrent record authority manifest file SHA-256",
    )
    manifest_file = Path(manifest_path)
    manifest, root, snapshot, permission, trusted, vocabulary = _generic_record_authorities_v3(
        manifest_file, expected_manifest_file_sha256=expected_manifest_file_sha256,
    )
    original_index = manifest_file.parent.resolve() / manifest["selection_index_path"]
    _regular_file_sha256_v3(
        original_index, expected_sha256=manifest["selection_index_sha256"],
        name="recurrent record authority selection index",
    )
    scratch_parent = _job_scratch_parent(manifest_file)
    with tempfile.TemporaryDirectory(prefix="recurrent-record-authority-", dir=scratch_parent) as directory:
        scratch = Path(directory).resolve()
        if scratch.parent != scratch_parent:
            raise ValueError("recurrent record authority scratch directory escapes its owned parent")
        rebuilt = scratch / "rebuilt-selection.jsonl"
        total, rebuilt_sha, split = _compile_selection_index(
            root, snapshot=snapshot, permission=permission, trusted=trusted,
            qualification_time_utc=manifest["qualification_time_utc"], vocabulary=vocabulary,
            destination=rebuilt, temporary_directory=scratch,
        )
        if (
            total != manifest["records_total"]
            or split != manifest["split"]
            or rebuilt_sha != manifest["selection_index_sha256"]
        ):
            raise ValueError("recurrent record authority selection/split cannot be reproduced")
    chunks = tuple(_closed_chunk_receipt_v3(root, snapshot))
    return VerifiedRecurrentRecordAuthorityV3(
        manifest_sha256=str(manifest["manifest_sha256"]),
        selection_index_sha256=str(manifest["selection_index_sha256"]),
        records_total=int(manifest["records_total"]), lane=str(manifest["lane"]),
        split=manifest["split"], chunks=chunks,
    )


def stream_recurrent_record_authority_v3(
    manifest_path: str | Path, *, expected_manifest_file_sha256: str,
    expected_manifest_sha256: str, expected_selection_index_sha256: str,
    expected_records_total: int, expected_split: Mapping[str, object],
    expected_chunks: tuple[Mapping[str, str], ...],
) -> Iterator[RecurrentRecordAuthorityRowV3]:
    """Stream physical selected records without choosing a representation version."""
    _require_digest(expected_manifest_sha256, field="expected recurrent record manifest SHA-256")
    _require_digest(
        expected_selection_index_sha256,
        field="expected recurrent record selection index SHA-256",
    )
    manifest_file = Path(manifest_path)
    manifest, root, snapshot, permission, trusted, vocabulary = _generic_record_authorities_v3(
        manifest_file, expected_manifest_file_sha256=expected_manifest_file_sha256,
    )
    if (
        manifest["manifest_sha256"] != expected_manifest_sha256
        or manifest["selection_index_sha256"] != expected_selection_index_sha256
        or manifest["records_total"] != expected_records_total
        or manifest["split"] != expected_split
        or tuple(_closed_chunk_receipt_v3(root, snapshot)) != expected_chunks
    ):
        raise ValueError("recurrent record authority manifest/index/split identity mismatches")
    index = manifest_file.parent.resolve() / manifest["selection_index_path"]
    entries = _frozen_index_entries_v3(index, expected_sha=expected_selection_index_sha256)
    seen = 0
    for record, model_payload, physical in _iter_requalified_records(
        root, snapshot=snapshot, permission=permission, trusted=trusted,
        qualification_time_utc=manifest["qualification_time_utc"], vocabulary=vocabulary,
    ):
        try:
            entry = next(entries)
        except StopIteration as exc:
            raise ValueError("recurrent record authority index is missing a qualified row") from exc
        if any(entry[field] != physical[field] for field in physical):
            raise ValueError("recurrent record authority index raw line changed")
        seen += 1
        yield RecurrentRecordAuthorityRowV3(
            record=record, model_payload=model_payload,
            shard=str(physical["shard"]), line=int(physical["line"]),
            record_id=str(physical["record_id"]), content_hash=str(physical["content_hash"]),
            raw_line_sha256=str(physical["raw_line_sha256"]),
            component_id=str(entry["component_id"]), partition=str(entry["partition"]),
        )
    try:
        next(entries)
    except StopIteration:
        pass
    else:
        raise ValueError("recurrent record authority index has an extra row")
    if seen != manifest["records_total"]:
        raise ValueError("recurrent record authority count differs from its manifest")


def _files_equal(left: Path, right: Path) -> bool:
    with left.open("rb") as first, right.open("rb") as second:
        while True:
            a = first.read(1024 * 1024)
            b = second.read(1024 * 1024)
            if a != b:
                return False
            if not a:
                return True


def _iter_requalified_records(
    root: Path, *, snapshot: Mapping[str, object], permission: Mapping[str, object],
    trusted: object, qualification_time_utc: str, vocabulary: object,
) -> Iterator[tuple[dict[str, object], object, dict[str, object]]]:
    from mage_ptcg.meta_specialist.local_dataset_v2 import (
        MAX_LOCAL_RECORD_BYTES_V2,
        parse_canonical_json_bytes_v2,
        require_qualified_training_record_v2,
    )

    chunks = snapshot.get("dataset_chunks")
    assert type(chunks) is list
    total_records = 0
    for chunk in sorted(chunks, key=lambda item: item["path"] if type(item) is dict else ""):
        assert type(chunk) is dict
        path = _snapshot_chunk_shard_path(root, chunk["path"])
        expected_sha = _require_digest(chunk.get("dataset_snapshot_sha256"), field="snapshot shard SHA-256")
        flags = _nofollow_read_flags_v3()
        try:
            descriptor = os.open(path, flags)
        except OSError as exc:
            raise ValueError("snapshot shard cannot be opened without following a symlink") from exc
        with os.fdopen(descriptor, "rb", closefd=True) as source:
            before = os.fstat(source.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise ValueError("snapshot shard is not a regular file")
            pre_digest = hashlib.sha256()
            for block in iter(lambda: source.read(1024 * 1024), b""):
                pre_digest.update(block)
            if pre_digest.hexdigest() != expected_sha:
                raise ValueError("snapshot shard physical SHA-256 changed before stream")
            if _descriptor_identity_v3(os.fstat(source.fileno())) != _descriptor_identity_v3(before):
                raise ValueError("snapshot shard descriptor changed during preflight hash")
            source.seek(0)
            eof_digest = hashlib.sha256()
            line_no = 0
            while True:
                raw = source.readline(MAX_LOCAL_RECORD_BYTES_V2 + 2)
                if not raw:
                    break
                eof_digest.update(raw)
                line_no += 1
                if not raw.endswith(b"\n") or raw == b"\n" or len(raw) > MAX_LOCAL_RECORD_BYTES_V2 + 1:
                    raise ValueError("snapshot shard has an invalid bounded JSONL line")
                try:
                    record = parse_canonical_json_bytes_v2(raw[:-1])
                    model_payload, _labels = require_qualified_training_record_v2(
                        record, vocabulary=vocabulary, trusted_permissions=trusted,
                        qualification_time_utc=qualification_time_utc,
                    )
                except Exception as exc:
                    raise ValueError("full recurrent corpus contains an unqualified record or permission") from exc
                source_info = record.get("source") if type(record) is dict else None
                if (
                    type(source_info) is not dict
                    or source_info.get("artifact_sha256") != permission.get("artifact_sha256")
                    or source_info.get("permission_manifest_id") != permission.get("permission_manifest_id")
                ):
                    raise ValueError("qualified recurrent record permission does not match teacher authority")
                total_records += 1
                yield record, model_payload, {
                    "shard": path.name, "line": line_no, "record_id": record["record_id"],
                    "content_hash": record["content_hash"], "raw_line_sha256": hashlib.sha256(raw).hexdigest(),
                }
            after = os.fstat(source.fileno())
            if _descriptor_identity_v3(after) != _descriptor_identity_v3(before):
                raise ValueError("snapshot shard descriptor changed during stream")
            if eof_digest.hexdigest() != expected_sha:
                raise ValueError("snapshot shard EOF SHA-256 mismatches its closed snapshot")
    if total_records != snapshot.get("examples_total"):
        raise ValueError("snapshot shard EOF record count mismatches its closed snapshot")


def _recurrent_steps_for_record(
    record: Mapping[str, object], *, model_payload: object, component_id: str,
    partition: str, vocabulary: object, episode_start: bool,
) -> list[BCExampleV3]:
    from mage_ptcg.meta_specialist.actor_visible_features_v1 import ExtractedSpecialistModelInputV1, build_specialist_step_input_v1
    from mage_ptcg.meta_specialist.local_dataset_v2 import semantic_loss_rows_from_record_v2
    from mage_ptcg.meta_specialist.representation_v3 import representation_v3_from_step_input_v1
    from mage_ptcg.meta_specialist.training_example_envelope_v2 import specialist_model_input_from_training_payload_v2

    model_input = specialist_model_input_from_training_payload_v2(model_payload)
    groups: dict[bytes, list[int]] = {}
    for index, semantic in enumerate(model_input.candidate_rows):
        groups.setdefault(_canonical(semantic.to_dict()), []).append(index)
    offsets: dict[bytes, int] = {}
    local_to_index: dict[str, int] = {}
    actions = record.get("legal_actions")
    if type(actions) is not list:
        raise ValueError("qualified recurrent record legal actions are invalid")
    for action in sorted(actions, key=lambda value: value["local_action_id"] if type(value) is dict else ""):
        if type(action) is not dict:
            raise ValueError("qualified recurrent action is invalid")
        key = _canonical(action["semantic_action"])
        offset = offsets.get(key, 0)
        if key not in groups or offset >= len(groups[key]):
            raise ValueError("recurrent record legal action/model input mismatched")
        local_to_index[action["local_action_id"]] = groups[key][offset]
        offsets[key] = offset + 1
    extracted = ExtractedSpecialistModelInputV1(model_input, record["model_input_id"], local_to_index)
    aliases = {
        key: sorted(local for local, index in local_to_index.items() if _canonical(model_input.candidate_rows[index].to_dict()) == key)
        for key in groups
    }
    episode = record.get("episode_id_hash")
    if type(episode) is not str or not episode:
        raise ValueError("qualified recurrent record episode is invalid")
    result: list[BCExampleV3] = []
    for loss_row in semantic_loss_rows_from_record_v2(record, vocabulary=vocabulary):
        prefix_counts: dict[bytes, int] = {}
        prefix: list[str] = []
        for semantic in loss_row["semantic_prefix"]:
            key = _canonical(semantic)
            offset = prefix_counts.get(key, 0)
            candidates = aliases.get(key, [])
            if offset >= len(candidates):
                raise ValueError("recurrent canonical prefix lacks distinct local aliases")
            prefix.append(candidates[offset])
            prefix_counts[key] = offset + 1
        step_input = build_specialist_step_input_v1(extracted, tuple(prefix))
        expected = [("semantic", _canonical(item.semantic_row.to_dict())) for item in step_input.allowed_semantic_classes]
        if step_input.stop_available:
            expected.append(("stop", b""))
        token_map: dict[tuple[str, bytes], float] = {}
        for token in loss_row["token_masses"]:
            key = ("stop", b"") if token["kind"] == "stop" else ("semantic", _canonical(token["semantic_action"]))
            token_map[key] = float(token["mass"])
        if set(token_map) != set(expected):
            raise ValueError("recurrent canonical teacher masses disagree with rebuilt legality")
        masses = tuple(token_map[key] for key in expected)
        if not math.isclose(math.fsum(masses), 1.0, rel_tol=0.0, abs_tol=1e-12):
            raise ValueError("recurrent canonical teacher masses do not normalize")
        state = representation_v3_from_step_input_v1(model_input, step_input, allow_unbound_selected=True)
        target = max(range(len(masses)), key=lambda index: (masses[index], -index))
        result.append(BCExampleV3(
            state=state, target_index=target, episode_group=episode, quality_weight=1.0,
            model_input=model_input, step_input=step_input, target_masses=masses,
            episode_start=episode_start and not result, component_id=component_id,
            partition=partition,
        ))
    if not result:
        raise ValueError("qualified recurrent record has no canonical loss rows")
    return result


class _PhysicalEpisodeTrackerV3:
    """Detect physical episode reopening with a bounded SQLite-backed closed set."""

    def __init__(self, scratch: Path | None = None) -> None:
        self._owned_directory: tempfile.TemporaryDirectory[str] | None = None
        if scratch is None:
            self._owned_directory = tempfile.TemporaryDirectory(prefix="recurrent-episodes-v3-")
            directory = Path(self._owned_directory.name)
        else:
            directory = Path(scratch)
            if not directory.is_dir():
                raise ValueError("recurrent episode tracker scratch directory is unavailable")
        descriptor, temporary = tempfile.mkstemp(prefix=".episodes-", suffix=".sqlite3", dir=directory)
        os.close(descriptor)
        self.database_path = Path(temporary)
        self._connection = sqlite3.connect(self.database_path)
        self._connection.execute("PRAGMA cache_size=-1024")
        self._connection.execute("PRAGMA temp_store=FILE")
        self._connection.execute("CREATE TABLE closed_episodes (episode TEXT PRIMARY KEY) WITHOUT ROWID")
        self._current_episode: str | None = None

    def advance(self, incoming_episode: str) -> bool:
        """Return whether the physical stream started a new episode."""
        if type(incoming_episode) is not str or not incoming_episode:
            raise ValueError("recurrent physical episode is invalid")
        if incoming_episode == self._current_episode:
            return False
        if self._connection.execute(
            "SELECT 1 FROM closed_episodes WHERE episode = ?", (incoming_episode,),
        ).fetchone() is not None:
            raise ValueError("full recurrent corpus reopens an episode after another episode")
        if self._current_episode is not None:
            self._connection.execute(
                "INSERT INTO closed_episodes (episode) VALUES (?)", (self._current_episode,),
            )
        self._current_episode = incoming_episode
        return True

    def close(self) -> None:
        connection = getattr(self, "_connection", None)
        if connection is not None:
            connection.close()
            self._connection = None
        for suffix in ("", "-journal", "-shm", "-wal"):
            self.database_path.with_name(self.database_path.name + suffix).unlink(missing_ok=True)
        if self._owned_directory is not None:
            self._owned_directory.cleanup()
            self._owned_directory = None

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass


def _yield_verified_recurrent_sequences_v3(
    payload: Mapping[str, object], *, root: Path, snapshot: Mapping[str, object],
    permission: Mapping[str, object], trusted: object, vocabulary: object, index: Path,
    burn_in: int, partition: str | None, index_entries: Iterator[dict[str, object]] | None = None,
) -> Iterator[RecurrentBCSequenceV3]:
    """Lockstep physical records against the rebuilt, not caller-visible, index."""
    entries = _read_index(index) if index_entries is None else index_entries
    current_episode: str | None = None
    current_component: str | None = None
    current_partition: str | None = None
    current_steps: list[BCExampleV3] = []
    episodes = _PhysicalEpisodeTrackerV3()

    def close() -> RecurrentBCSequenceV3 | None:
        if current_episode is None:
            return None
        assert current_component is not None and current_partition is not None
        return RecurrentBCSequenceV3(
            payload["lane"], current_episode, current_component, current_partition,
            tuple(current_steps), burn_in,
        )

    try:
        for record, model_payload, physical in _iter_requalified_records(
            root, snapshot=snapshot, permission=permission, trusted=trusted,
            qualification_time_utc=payload["qualification_time_utc"], vocabulary=vocabulary,
        ):
            try:
                entry = next(entries)
            except StopIteration as exc:
                raise ValueError("recurrent selection index is missing a qualified row") from exc
            if any(entry[field] != physical[field] for field in physical):
                raise ValueError("recurrent selection index raw line changed")
            episode = record["episode_id_hash"]
            if episodes.advance(episode):
                previous = close()
                current_episode = episode
                current_component = entry["component_id"]
                current_partition = entry["partition"]
                current_steps = []
                if previous is not None and (partition is None or previous.partition == partition):
                    yield previous
            elif entry["component_id"] != current_component or entry["partition"] != current_partition:
                raise ValueError("recurrent selection crosses a component or partition inside an episode")
            current_steps.extend(_recurrent_steps_for_record(
                record, model_payload=model_payload, component_id=entry["component_id"],
                partition=entry["partition"], vocabulary=vocabulary, episode_start=not current_steps,
            ))
        try:
            next(entries)
        except StopIteration:
            pass
        else:
            raise ValueError("recurrent selection index has an extra row")
        final = close()
        if final is None:
            raise ValueError("recurrent selection materialized no sequences")
        if partition is None or final.partition == partition:
            yield final
    finally:
        episodes.close()


def _stream_recurrent_selection_v3(
    manifest_path: str | Path, *, expected_manifest_file_sha256: str | None,
    burn_in: int, partition: str | None,
) -> Iterator[RecurrentBCSequenceV3]:
    """Revalidate a full-corpus authority, then emit one physical episode at a time.

    The optional file anchor is deliberately checked before parsing the root
    manifest.  ``materialize_recurrent_selection_v3`` retains its fixture-only
    compatibility path below; production callers must use the public anchored
    stream wrapper.
    """
    if type(burn_in) is not int or burn_in < 0:
        raise ValueError("burn_in must be a nonnegative integer")
    manifest_file = Path(manifest_path)
    if expected_manifest_file_sha256 is not None:
        _require_digest(expected_manifest_file_sha256, field="expected recurrent manifest file SHA-256")
        if _file_hash(manifest_file) != expected_manifest_file_sha256:
            raise ValueError("recurrent selection external manifest file SHA-256 does not match")
    if partition is not None and partition not in {"train", "validation"}:
        raise ValueError("recurrent selection stream partition is invalid")
    payload = read_recurrent_selection_manifest_v3(manifest_file)
    root, snapshot, permission, trusted, vocabulary = _assert_manifest_authorities(payload)
    index = manifest_file.parent / payload["selection_index_path"]
    scratch_parent = _job_scratch_parent(manifest_file)
    with tempfile.TemporaryDirectory(prefix="recurrent-selection-verify-", dir=scratch_parent) as directory:
        job_scratch = Path(directory).resolve()
        if job_scratch.parent != scratch_parent:
            raise ValueError("recurrent selection scratch directory escapes its job parent")
        rebuilt = job_scratch / "rebuilt-selection.jsonl"
        total, rebuilt_sha, rebuilt_split = _compile_selection_index(
            root, snapshot=snapshot, permission=permission, trusted=trusted,
            qualification_time_utc=payload["qualification_time_utc"], vocabulary=vocabulary,
            destination=rebuilt, temporary_directory=job_scratch,
        )
        if total != payload["records_total"] or rebuilt_split != payload["split"] or rebuilt_sha != payload["selection_index_sha256"] or not _files_equal(rebuilt, index):
            raise ValueError("recurrent selection index bytes/rows or split cannot be reproduced")
        # Keep the temporary directory alive for the generator lifetime.  The
        # recompiled file is the authority consumed below; the original sidecar
        # is never re-opened after its byte comparison above.
        yield from _yield_verified_recurrent_sequences_v3(
            payload, root=root, snapshot=snapshot, permission=permission,
            trusted=trusted, vocabulary=vocabulary, index=rebuilt,
            burn_in=burn_in, partition=partition,
        )


def stream_recurrent_selection_v3(
    manifest_path: str | Path, *, expected_manifest_file_sha256: str, burn_in: int,
    partition: str,
) -> Iterator[RecurrentBCSequenceV3]:
    """Yield one selected sealed episode after full authority reproduction.

    The caller-supplied SHA binds the raw root-manifest file itself, closing the
    self-rehashed-manifest hole that a manifest's internal hash cannot close.
    No decoded corpus sequence is retained after it is yielded.
    """
    return _stream_recurrent_selection_v3(
        manifest_path, expected_manifest_file_sha256=expected_manifest_file_sha256,
        burn_in=burn_in, partition=partition,
    )


@dataclass(frozen=True, slots=True)
class PreparedRecurrentLaneV3:
    """Externally anchored, run-local result of one full split reproduction."""

    receipt_path: Path
    expected_receipt_file_sha256: str
    lane: str


def _closed_chunk_receipt_v3(
    root: Path, snapshot: Mapping[str, object], *, verify_physical: bool = True,
) -> list[dict[str, str]]:
    chunks = snapshot.get("dataset_chunks")
    if type(chunks) is not list or not chunks:
        raise ValueError("snapshot index has no closed dataset chunks")
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for chunk in sorted(chunks, key=lambda item: item["path"] if type(item) is dict else ""):
        if type(chunk) is not dict:
            raise ValueError("snapshot chunk is malformed")
        path = _snapshot_chunk_shard_path(root, chunk.get("path"))
        digest = _require_digest(chunk.get("dataset_snapshot_sha256"), field="snapshot chunk SHA-256")
        if path.name in seen:
            raise ValueError("snapshot has duplicate closed shard basenames")
        seen.add(path.name)
        if verify_physical and _file_hash(path) != digest:
            raise ValueError("snapshot closed shard physical SHA-256 changed")
        result.append({"shard": path.name, "sha256": digest})
    return result


def _copy_closed_snapshot_to_scratch_v3(
    root: Path, snapshot: Mapping[str, object], *, destination: Path,
) -> Path:
    """Pin every source shard once and mirror its verified bytes for this pass.

    Projection evidence must be checked before yielding, which requires a
    second projection traversal.  The traversal is over this job-owned closed
    mirror, never over a source path that could be replaced between scans.
    """
    destination.mkdir()
    mirror = destination.resolve()
    if mirror.parent != destination.parent.resolve() or destination.is_symlink():
        raise ValueError("recurrent projection mirror escapes its job directory")
    chunks = snapshot.get("dataset_chunks")
    if type(chunks) is not list or not chunks:
        raise ValueError("snapshot index has no closed dataset chunks")
    seen: set[str] = set()
    for chunk in sorted(chunks, key=lambda item: item["path"] if type(item) is dict else ""):
        if type(chunk) is not dict:
            raise ValueError("snapshot chunk is malformed")
        source_path = _snapshot_chunk_shard_path(root, chunk.get("path"))
        expected_sha = _require_digest(
            chunk.get("dataset_snapshot_sha256"), field="snapshot chunk SHA-256",
        )
        if source_path.name in seen:
            raise ValueError("snapshot has duplicate closed shard basenames")
        seen.add(source_path.name)
        try:
            descriptor = os.open(source_path, _nofollow_read_flags_v3())
        except OSError as exc:
            raise ValueError("snapshot shard cannot be mirrored without following a symlink") from exc
        with os.fdopen(descriptor, "rb", closefd=True) as source, (mirror / source_path.name).open("xb") as output:
            before = os.fstat(source.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise ValueError("snapshot shard mirror source is not a regular file")
            digest = hashlib.sha256()
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
                output.write(block)
            output.flush()
            os.fsync(output.fileno())
            after = os.fstat(source.fileno())
            if _descriptor_identity_v3(before) != _descriptor_identity_v3(after):
                raise ValueError("snapshot shard descriptor changed during projection mirror")
            if digest.hexdigest() != expected_sha:
                raise ValueError("snapshot shard physical SHA-256 changed before projection mirror")
    _fsync_directory_v3(mirror)
    return mirror


def _bounded_r3_projection_reason_v3(exc: Exception) -> str:
    """Summarize one projection rejection without putting an unbounded record into logs."""
    message = str(exc).replace("\n", " ").replace("\r", " ")
    return f"{type(exc).__name__}:{message[:160]}"


def _validate_r3_projection_preflight_v3(
    manifest: Mapping[str, object], *, root: Path, snapshot: Mapping[str, object],
    permission: Mapping[str, object], trusted: object, vocabulary: object,
    index: Path | None = None,
    index_entries: Iterator[dict[str, object]] | None = None,
) -> dict[str, object]:
    """Project every selected physical record before a lane receipt can exist.

    Selection authority deliberately covers all qualified records.  Therefore
    this verifier must not filter projection failures: it consumes the candidate
    frozen index in physical lockstep, aggregates bounded diagnostics, and
    rejects the entire preflight if any R3 relation projection is impossible.
    """
    if (index is None) == (index_entries is None):
        raise ValueError("R3 projection preflight needs exactly one index authority")
    entries = _read_index(index) if index_entries is None else index_entries
    current_episode: str | None = None
    episodes = _PhysicalEpisodeTrackerV3()
    errors: dict[str, dict[str, object]] = {}
    overflow_reason = "other_r3_projection_error"
    records_checked = 0
    steps_checked = 0
    aggregate = hashlib.sha256()

    try:
        for record, model_payload, physical in _iter_requalified_records(
            root, snapshot=snapshot, permission=permission, trusted=trusted,
            qualification_time_utc=manifest["qualification_time_utc"], vocabulary=vocabulary,
        ):
            try:
                entry = next(entries)
            except StopIteration as exc:
                raise ValueError("recurrent preflight selection index is missing a qualified row") from exc
            if any(entry[field] != physical[field] for field in physical):
                raise ValueError("recurrent preflight selection index raw line changed")
            episode = record["episode_id_hash"]
            episode_start = episodes.advance(episode)
            if episode_start:
                current_episode = episode
            records_checked += 1
            try:
                steps = _recurrent_steps_for_record(
                    record, model_payload=model_payload, component_id=entry["component_id"],
                    partition=entry["partition"], vocabulary=vocabulary,
                    episode_start=episode_start,
                )
            except Exception as exc:
                reason = _bounded_r3_projection_reason_v3(exc)
                if reason not in errors and len(errors) >= 16:
                    reason = overflow_reason
                failure = errors.setdefault(reason, {
                    "reason": reason, "count": 0,
                    "first": {
                        "record_id": physical["record_id"], "shard": physical["shard"],
                        "line": physical["line"],
                    },
                })
                failure["count"] = int(failure["count"]) + 1
                continue
            steps_checked += len(steps)
            aggregate.update(_canonical({
                "record_id": physical["record_id"], "content_hash": physical["content_hash"],
                "raw_line_sha256": physical["raw_line_sha256"], "shard": physical["shard"],
                "line": physical["line"], "component_id": entry["component_id"],
                "partition": entry["partition"],
                "steps": [{
                    "state": asdict(step.state), "target_index": step.target_index,
                    "target_masses": list(step.target_masses),
                    "quality_weight": float(step.quality_weight),
                    "episode_group": step.episode_group,
                    "episode_start": step.episode_start,
                    "component_id": step.component_id, "partition": step.partition,
                } for step in steps],
            }))
            aggregate.update(b"\n")
        try:
            next(entries)
        except StopIteration:
            pass
        else:
            raise ValueError("recurrent preflight selection index has an extra row")
        if records_checked != manifest["records_total"]:
            raise ValueError("recurrent preflight projection record count differs from selection authority")
        if errors:
            details = {
                "schema": "meta-specialist-r3-projection-rejections-v1",
                "records_checked": records_checked, "steps_checked": steps_checked,
                "failures": list(errors.values()),
            }
            raise ValueError(
                "recurrent preflight R3 projection rejected: "
                + _canonical(details).decode("utf-8"),
            )
        if steps_checked < records_checked:
            raise ValueError("recurrent preflight R3 projection produced too few canonical steps")
        return {
            "schema": _R3_PROJECTION_SCHEMA, "records_checked": records_checked,
            "steps_checked": steps_checked, "aggregate_sha256": aggregate.hexdigest(),
        }
    finally:
        episodes.close()


def _read_preflight_receipt_v3(path: Path, *, raw: bytes | None = None) -> dict[str, object]:
    if raw is None:
        payload = _read_local_canonical_object(path, name="recurrent lane preflight receipt")
    else:
        from mage_ptcg.meta_specialist.local_dataset_v2 import parse_canonical_json_bytes_v2

        try:
            payload = parse_canonical_json_bytes_v2(raw)
        except Exception as exc:
            raise ValueError("recurrent lane preflight receipt is not canonical bounded JSON") from exc
        if type(payload) is not dict:
            raise ValueError("recurrent lane preflight receipt must be an object")
    schema = payload.get("schema")
    if schema == _PREFLIGHT_V1_SCHEMA:
        expected_keys = _PREFLIGHT_V1_KEYS
    elif schema == _PREFLIGHT_V2_SCHEMA:
        expected_keys = _PREFLIGHT_V2_KEYS
    elif schema == _PREFLIGHT_V3_SCHEMA:
        expected_keys = _PREFLIGHT_KEYS
    else:
        expected_keys = frozenset()
    if set(payload) != expected_keys:
        raise ValueError("recurrent lane preflight receipt has an invalid closed schema")
    receipt_sha = _require_digest(payload.get("receipt_sha256"), field="recurrent preflight receipt_sha256")
    if _hash({key: value for key, value in payload.items() if key != "receipt_sha256"}) != receipt_sha:
        raise ValueError("recurrent lane preflight receipt self hash does not verify")
    if type(payload.get("lane")) is not str or not payload["lane"] or type(payload.get("command_identity")) is not str or not payload["command_identity"]:
        raise ValueError("recurrent lane preflight receipt lane/command identity is invalid")
    if type(payload.get("source_manifest_path")) is not str or not Path(payload["source_manifest_path"]).is_absolute():
        raise ValueError("recurrent lane preflight receipt source manifest path is invalid")
    for field in (
        "source_manifest_file_sha256", "source_manifest_sha256", "snapshot_index_sha256",
        "teacher_manifest_sha256", "dataset_snapshot_sha256", "trusted_permission_sha256",
        "original_index_sha256", "rebuilt_index_sha256", "frozen_index_sha256",
    ):
        _require_digest(payload.get(field), field=f"recurrent preflight {field}")
    if payload["original_index_sha256"] != payload["rebuilt_index_sha256"] or payload["rebuilt_index_sha256"] != payload["frozen_index_sha256"]:
        raise ValueError("recurrent lane preflight index identities disagree")
    if type(payload.get("frozen_index_path")) is not str or Path(payload["frozen_index_path"]).name != payload["frozen_index_path"]:
        raise ValueError("recurrent lane preflight frozen index path is invalid")
    if type(payload.get("records_total")) is not int or payload["records_total"] < 1 or type(payload.get("split")) is not dict:
        raise ValueError("recurrent lane preflight record/split metadata is invalid")
    if type(payload.get("chunks")) is not list or not payload["chunks"]:
        raise ValueError("recurrent lane preflight closed chunks are invalid")
    for chunk in payload["chunks"]:
        if type(chunk) is not dict or set(chunk) != {"shard", "sha256"} or _SHARD_RE.fullmatch(chunk.get("shard", "")) is None:
            raise ValueError("recurrent lane preflight closed chunk is invalid")
        _require_digest(chunk["sha256"], field="recurrent preflight chunk SHA-256")
    if type(payload.get("preflight_seconds")) not in {int, float} or not math.isfinite(float(payload["preflight_seconds"])) or float(payload["preflight_seconds"]) < 0:
        raise ValueError("recurrent lane preflight timing is invalid")
    if schema in {_PREFLIGHT_V2_SCHEMA, _PREFLIGHT_V3_SCHEMA}:
        projection = payload.get("r3_projection")
        if type(projection) is not dict or set(projection) != _R3_PROJECTION_KEYS or projection.get("schema") != _R3_PROJECTION_SCHEMA:
            raise ValueError("recurrent lane preflight R3 projection evidence is invalid")
        if projection.get("records_checked") != payload["records_total"]:
            raise ValueError("recurrent lane preflight R3 projection record count is invalid")
        if (type(projection.get("steps_checked")) is not int
                or projection["steps_checked"] < projection["records_checked"]):
            raise ValueError("recurrent lane preflight R3 projection step count is invalid")
        _require_digest(projection.get("aggregate_sha256"), field="recurrent preflight R3 projection aggregate SHA-256")
    if schema == _PREFLIGHT_V3_SCHEMA:
        frozen_snapshot = payload.get("frozen_snapshot_path")
        if (type(frozen_snapshot) is not str or Path(frozen_snapshot).name != frozen_snapshot
                or frozen_snapshot != "sealed-snapshot"):
            raise ValueError("recurrent lane preflight frozen snapshot path is invalid")
    return payload


def _fsync_directory_v3(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _retire_existing_preflight_authority_v3(
    lane_dir: Path, *, receipt_path: Path, frozen_index: Path, frozen_snapshot: Path,
) -> Path | None:
    """Move an old public receipt/index aside before attempting a replacement.

    The receipt moves first, so interruption cannot leave a public authority
    that still points at a stale frozen index.  Retired bytes remain available
    for diagnosis and recovery; this transition never deletes an artifact.
    """
    legacy_receipt = lane_dir / "recurrent-lane-preflight-v1.json"
    legacy_v2_receipt = lane_dir / "recurrent-lane-preflight-v2.json"
    candidates = (receipt_path, legacy_v2_receipt, legacy_receipt, frozen_index, frozen_snapshot)
    existing: list[Path] = []
    for candidate in candidates:
        try:
            os.lstat(candidate)
        except FileNotFoundError:
            continue
        existing.append(candidate)
    if not existing:
        return None
    retired_root = lane_dir / ".retired-preflights"
    retired_root.mkdir(exist_ok=True)
    resolved_retired_root = retired_root.resolve()
    if retired_root.is_symlink() or resolved_retired_root.parent != lane_dir:
        raise ValueError("recurrent preflight retirement root escapes the output directory")
    archive = Path(tempfile.mkdtemp(prefix="retired-", dir=resolved_retired_root)).resolve()
    if archive.parent != resolved_retired_root:
        raise ValueError("recurrent preflight retirement directory escapes its owned root")
    for source in existing:
        destination = archive / source.name
        os.replace(source, destination)
        _fsync_directory_v3(archive)
        _fsync_directory_v3(lane_dir)
    return archive


def prepare_sealed_recurrent_lane_v3(
    manifest_path: str | Path, *, expected_manifest_file_sha256: str, output_dir: str | Path,
    command_identity: str,
) -> PreparedRecurrentLaneV3:
    """Reproduce one lane's component split once and publish a frozen receipt/index."""
    _require_digest(expected_manifest_file_sha256, field="expected recurrent manifest file SHA-256")
    if type(command_identity) is not str or not command_identity:
        raise ValueError("recurrent preflight command identity is invalid")
    started = time.monotonic()
    manifest_file = Path(manifest_path).resolve()
    manifest_raw = _regular_file_bytes_v3(
        manifest_file, expected_sha256=expected_manifest_file_sha256,
        name="recurrent preflight source manifest",
    )
    manifest = _read_root_manifest(manifest_file, raw=manifest_raw)
    root, snapshot, permission, trusted, vocabulary = _assert_manifest_authorities(manifest)
    chunks = _closed_chunk_receipt_v3(root, snapshot)
    original_index = manifest_file.parent / manifest["selection_index_path"]
    lane_dir = Path(output_dir).resolve()
    lane_dir.mkdir(parents=True, exist_ok=True)
    if lane_dir.is_symlink():
        raise ValueError("recurrent preflight output directory must not be a symlink")
    frozen_index = lane_dir / "sealed-run-index.jsonl"
    frozen_snapshot = lane_dir / "sealed-snapshot"
    receipt_path = lane_dir / "recurrent-lane-preflight-v3.json"
    _retire_existing_preflight_authority_v3(
        lane_dir, receipt_path=receipt_path, frozen_index=frozen_index,
        frozen_snapshot=frozen_snapshot,
    )
    scratch_parent = _job_scratch_parent(frozen_index)
    with tempfile.TemporaryDirectory(prefix="recurrent-preflight-", dir=scratch_parent) as directory:
        job_scratch = Path(directory).resolve()
        if job_scratch.parent != scratch_parent:
            raise ValueError("recurrent preflight scratch directory escapes its job parent")
        rebuilt = job_scratch / "rebuilt-selection.jsonl"
        total, rebuilt_sha, split = _compile_selection_index(
            root, snapshot=snapshot, permission=permission, trusted=trusted,
            qualification_time_utc=manifest["qualification_time_utc"], vocabulary=vocabulary,
            destination=rebuilt, temporary_directory=job_scratch,
        )
        if (total != manifest["records_total"] or split != manifest["split"]
                or rebuilt_sha != manifest["selection_index_sha256"] or not _files_equal(rebuilt, original_index)):
            raise ValueError("recurrent preflight index bytes/rows or split cannot be reproduced")
        staged_snapshot = _copy_closed_snapshot_to_scratch_v3(
            root, snapshot, destination=job_scratch / "closed-snapshot",
        )
        projection = _validate_r3_projection_preflight_v3(
            manifest, root=staged_snapshot, snapshot=snapshot, permission=permission,
            trusted=trusted, vocabulary=vocabulary, index=rebuilt,
        )
        _atomic_copy(rebuilt, frozen_index)
        os.replace(staged_snapshot, frozen_snapshot)
        _fsync_directory_v3(lane_dir)
    _validate_index(
        frozen_index, expected_sha=rebuilt_sha, expected_records=total, split=split,
    )
    payload: dict[str, object] = {
        "schema": _PREFLIGHT_SCHEMA, "lane": manifest["lane"], "command_identity": command_identity,
        "source_manifest_path": str(manifest_file),
        "source_manifest_file_sha256": expected_manifest_file_sha256,
        "source_manifest_sha256": manifest["manifest_sha256"],
        "snapshot_index_sha256": manifest["snapshot_index_sha256"],
        "teacher_manifest_sha256": manifest["teacher_manifest_sha256"],
        "dataset_snapshot_sha256": manifest["dataset_snapshot_sha256"],
        "trusted_permission_sha256": manifest["trusted_permission_sha256"],
        "vocabulary": manifest["vocabulary"], "qualification_time_utc": manifest["qualification_time_utc"],
        "records_total": total, "split": split,
        "original_index_sha256": manifest["selection_index_sha256"],
        "rebuilt_index_sha256": rebuilt_sha, "frozen_index_path": frozen_index.name,
        "frozen_index_sha256": _file_hash(frozen_index), "chunks": chunks,
        "frozen_snapshot_path": frozen_snapshot.name,
        "r3_projection": projection,
        "preflight_seconds": time.monotonic() - started,
    }
    payload["receipt_sha256"] = _hash(payload)
    _atomic_write_json(receipt_path, payload)
    reloaded = _read_preflight_receipt_v3(receipt_path)
    if reloaded != payload:
        raise RuntimeError("recurrent lane preflight atomic receipt reload differs from written bytes")
    return PreparedRecurrentLaneV3(receipt_path, _file_hash(receipt_path), str(manifest["lane"]))


def _frozen_index_entries_v3(path: Path, *, expected_sha: str) -> Iterator[dict[str, object]]:
    """Seal a frozen index into a private spool before any parse can consume it.

    Hash-then-seek on the source descriptor permits a same-inode rewrite after
    the digest pass.  The source is consequently read exactly once through an
    ``O_NOFOLLOW`` descriptor and copied to an anonymous private spool only
    after its digest and descriptor identity verify.  Parsing never reopens or
    seeks the mutable source path.
    """
    flags = _nofollow_read_flags_v3()
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ValueError("recurrent frozen index cannot be opened without following a symlink") from exc
    with os.fdopen(descriptor, "rb", closefd=True) as source:
        before = os.fstat(source.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("recurrent frozen index is not a regular file")
        digest = hashlib.sha256()
        with tempfile.TemporaryFile(mode="w+b", prefix="recurrent-frozen-index-v3-") as spool:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
                spool.write(block)
            after = os.fstat(source.fileno())
            if _descriptor_identity_v3(before) != _descriptor_identity_v3(after):
                raise ValueError("recurrent frozen index descriptor changed during SHA-256 read")
            if digest.hexdigest() != expected_sha:
                raise ValueError("recurrent frozen index SHA-256 changed")
            spool.seek(0)
            yield from _read_index_handle(spool)


def _prepared_stream_authorities_v3(
    receipt_file: Path, *, expected_receipt_file_sha256: str,
) -> tuple[dict[str, object], dict[str, object], Path, Mapping[str, object], Mapping[str, object], object, object, Path]:
    _require_digest(expected_receipt_file_sha256, field="expected recurrent preflight receipt file SHA-256")
    receipt_parent = _assert_nonsymlink_directory_path_v3(
        receipt_file.parent, name="recurrent prepared stream output directory",
    )
    receipt_raw = _regular_file_bytes_v3(
        receipt_file, expected_sha256=expected_receipt_file_sha256,
        name="recurrent preflight receipt",
    )
    receipt = _read_preflight_receipt_v3(receipt_file, raw=receipt_raw)
    if receipt["schema"] != _PREFLIGHT_V3_SCHEMA:
        raise ValueError("recurrent prepared stream requires a sealed-snapshot v3 receipt")
    manifest_file = Path(receipt["source_manifest_path"])
    manifest_raw = _regular_file_bytes_v3(
        manifest_file, expected_sha256=receipt["source_manifest_file_sha256"],
        name="recurrent prepared stream source manifest",
    )
    manifest = _read_root_manifest(manifest_file, raw=manifest_raw, validate_sidecar=False)
    for field in (
        "manifest_sha256", "snapshot_index_sha256", "teacher_manifest_sha256",
        "dataset_snapshot_sha256", "trusted_permission_sha256", "vocabulary",
        "qualification_time_utc", "records_total", "split",
    ):
        receipt_field = "source_manifest_sha256" if field == "manifest_sha256" else field
        if manifest[field] != receipt[receipt_field]:
            raise ValueError("recurrent prepared stream receipt/manifest authority mismatches")
    if manifest["selection_index_sha256"] != receipt["original_index_sha256"]:
        raise ValueError("recurrent prepared stream original index identity changed")
    root, snapshot, permission, trusted, vocabulary = _assert_manifest_authorities(
        manifest, validate_physical_snapshot=False,
    )
    if _closed_chunk_receipt_v3(root, snapshot, verify_physical=False) != receipt["chunks"]:
        raise ValueError("recurrent prepared stream closed shard set changed")
    frozen_entry = receipt_parent / receipt["frozen_snapshot_path"]
    frozen_root = _assert_nonsymlink_directory_path_v3(
        frozen_entry, name="recurrent prepared stream frozen snapshot",
    )
    if (frozen_root.parent != receipt_parent
            or _closed_chunk_receipt_v3(frozen_root, snapshot, verify_physical=False) != receipt["chunks"]):
        raise ValueError("recurrent prepared stream frozen snapshot authority is invalid")
    _assert_nonsymlink_snapshot_children_v3(frozen_root, snapshot)
    frozen = receipt_parent / receipt["frozen_index_path"]
    try:
        frozen_stat = os.lstat(frozen)
    except OSError as exc:
        raise ValueError("recurrent prepared stream frozen index is unavailable") from exc
    if stat.S_ISLNK(frozen_stat.st_mode) or not stat.S_ISREG(frozen_stat.st_mode):
        raise ValueError("recurrent prepared stream frozen index must be a non-symlink regular file")
    _validate_index(
        frozen, expected_sha=receipt["frozen_index_sha256"],
        expected_records=receipt["records_total"], split=receipt["split"],
    )
    return receipt, manifest, frozen_root, snapshot, permission, trusted, vocabulary, frozen


def stream_prepared_recurrent_selection_v3(
    receipt_path: str | Path, *, expected_receipt_file_sha256: str, burn_in: int,
    partition: str,
) -> Iterator[RecurrentBCSequenceV3]:
    """Stream a preflight-frozen lane without recomputing components or splits."""
    if type(burn_in) is not int or burn_in < 0 or partition not in {"train", "validation"}:
        raise ValueError("recurrent prepared stream arguments are invalid")

    def iterator() -> Iterator[RecurrentBCSequenceV3]:
        receipt, manifest, root, snapshot, permission, trusted, vocabulary, frozen = _prepared_stream_authorities_v3(
            Path(receipt_path), expected_receipt_file_sha256=expected_receipt_file_sha256,
        )
        observed_projection = _validate_r3_projection_preflight_v3(
            manifest, root=root, snapshot=snapshot, permission=permission,
            trusted=trusted, vocabulary=vocabulary,
            index_entries=_frozen_index_entries_v3(
                frozen, expected_sha=receipt["frozen_index_sha256"],
            ),
        )
        if observed_projection != receipt["r3_projection"]:
            raise ValueError("recurrent prepared stream R3 projection aggregate differs from receipt")
        yield from _yield_verified_recurrent_sequences_v3(
            manifest, root=root, snapshot=snapshot, permission=permission,
            trusted=trusted, vocabulary=vocabulary, index=frozen,
            burn_in=burn_in, partition=partition,
            index_entries=_frozen_index_entries_v3(
                frozen, expected_sha=receipt["frozen_index_sha256"],
            ),
        )

    return iterator()


def validate_prepared_recurrent_pair_v3(
    train_receipt_path: str | Path, *, train_expected_receipt_file_sha256: str,
    validation_receipt_path: str | Path, validation_expected_receipt_file_sha256: str,
) -> None:
    """Check the preflight split proof without introducing two corpus passes."""
    train, *_ = _prepared_stream_authorities_v3(
        Path(train_receipt_path), expected_receipt_file_sha256=train_expected_receipt_file_sha256,
    )
    validation, *_ = _prepared_stream_authorities_v3(
        Path(validation_receipt_path), expected_receipt_file_sha256=validation_expected_receipt_file_sha256,
    )
    if train != validation:
        raise ValueError("prepared recurrent training/validation receipts differ")
    split = train["split"]
    if split.get("overlap_counters") != {"episode_overlap": 0, "near_duplicate_overlap": 0}:
        raise ValueError("prepared recurrent receipt permits split leakage")


def materialize_recurrent_selection_v3(
    manifest_path: str | Path, *, burn_in: int,
) -> tuple[RecurrentBCSequenceV3, ...]:
    """Fixture compatibility helper; full production runs must use ``stream_*``."""
    sequences = tuple(_stream_recurrent_selection_v3(
        manifest_path, expected_manifest_file_sha256=None, burn_in=burn_in, partition=None,
    ))
    if not sequences:
        raise ValueError("recurrent selection materialized no sequences")
    return sequences


__all__ = [
    "build_recurrent_selection_manifest_v3", "materialize_recurrent_selection_v3",
    "PreparedRecurrentLaneV3", "prepare_sealed_recurrent_lane_v3",
    "RecurrentRecordAuthorityRowV3", "VerifiedRecurrentRecordAuthorityV3",
    "read_recurrent_selection_manifest_v3", "stream_prepared_recurrent_selection_v3",
    "stream_recurrent_record_authority_v3", "stream_recurrent_selection_v3",
    "validate_prepared_recurrent_pair_v3", "verify_recurrent_record_authority_v3",
]
