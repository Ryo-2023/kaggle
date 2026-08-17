"""Streaming recurrent v4 authority with exact READY teacher-quality joins."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile
import time

from mage_ptcg.meta_specialist.recurrent_dataset_v3 import (
    _PhysicalEpisodeTrackerV3,
    RecurrentRecordAuthorityRowV3,
    stream_recurrent_record_authority_v3,
    verify_recurrent_record_authority_v3,
)
from mage_ptcg.meta_specialist.representation_v4 import (
    RelationalStateV4,
    representation_v4_from_step_input_v1,
)
from mage_ptcg.meta_specialist.teacher_quality_v2 import (
    TeacherQualityOverlayRowV2,
    read_teacher_quality_manifest_v2,
    stream_ready_teacher_quality_overlay_v2,
)


_RECEIPT_SCHEMA = "meta-specialist-recurrent-lane-preflight-v4"
_QUALITY_SCHEMA = "meta-specialist-recurrent-quality-row-v4"
_PROJECTION_SCHEMA = "meta-specialist-recurrent-projection-v4"
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")


def _object_sha(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _file_sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _require_sha(value: object, name: str) -> str:
    if type(value) is not str or _HEX64.fullmatch(value) is None:
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return value


def _quality_weight(value: object) -> float:
    if type(value) is bool or type(value) not in {int, float} or not math.isfinite(float(value)):
        raise ValueError("quality_weight must be finite")
    weight = float(value)
    if weight <= 0.0:
        raise ValueError("quality_weight must be positive")
    if weight == 1.0:
        raise ValueError("default quality_weight 1.0 is forbidden in production v4")
    if weight > 1.0:
        raise ValueError("quality_weight must not exceed 1")
    return weight


def _reach_mass(value: object) -> float:
    if type(value) is bool or type(value) not in {int, float} or not math.isfinite(float(value)):
        raise ValueError("reach_mass must be finite")
    mass = float(value)
    if not 0.0 < mass <= 1.0:
        raise ValueError("reach_mass must be in (0, 1]")
    return mass


@dataclass(frozen=True, slots=True)
class RecurrentBCStepV4:
    state: RelationalStateV4
    target_index: int
    episode_group: str
    quality_weight: float
    model_input: object
    step_input: object
    target_masses: tuple[float, ...]
    reach_mass: float
    episode_start: bool
    component_id: str
    partition: str
    record_id: str
    content_hash: str
    research_only: bool = False
    supervision_weight: float = 1.0

    def __post_init__(self) -> None:
        if type(self.state) is not RelationalStateV4:
            raise ValueError("v4 recurrent step state is invalid")
        if type(self.target_index) is not int or not 0 <= self.target_index < len(self.target_masses):
            raise ValueError("v4 recurrent target index is invalid")
        if (
            not self.target_masses
            or any(type(value) is not float or not math.isfinite(value) or value < 0 for value in self.target_masses)
            or not math.isclose(math.fsum(self.target_masses), 1.0, rel_tol=0.0, abs_tol=1e-12)
        ):
            raise ValueError("v4 recurrent target masses are invalid")
        if type(self.research_only) is not bool:
            raise ValueError("v4 recurrent research_only must be bool")
        if (
            type(self.supervision_weight) is bool
            or type(self.supervision_weight) not in {int, float}
            or not math.isfinite(float(self.supervision_weight))
            or not 0.0 <= float(self.supervision_weight) <= 1.0
        ):
            raise ValueError("v4 recurrent supervision_weight must be finite in [0, 1]")
        if self.research_only:
            if (
                type(self.quality_weight) is bool or type(self.quality_weight) not in {int, float}
                or not math.isfinite(float(self.quality_weight)) or not 0.0 < float(self.quality_weight) <= 1.0
            ):
                raise ValueError("research-only quality_weight must be in (0, 1]")
        else:
            _quality_weight(self.quality_weight)
        _reach_mass(self.reach_mass)
        _require_sha(self.record_id, "v4 recurrent record_id")
        _require_sha(self.content_hash, "v4 recurrent content_hash")
        if not self.episode_group or not self.component_id or self.partition not in {"train", "validation"}:
            raise ValueError("v4 recurrent step authority identity is invalid")
        if type(self.episode_start) is not bool:
            raise ValueError("v4 recurrent episode_start must be bool")


@dataclass(frozen=True, slots=True)
class RecurrentBCSequenceV4:
    lane: str
    episode_group: str
    component_id: str
    partition: str
    steps: tuple[RecurrentBCStepV4, ...]
    burn_in: int
    research_only: bool = False

    def __post_init__(self) -> None:
        if (
            not self.lane or not self.episode_group or not self.component_id
            or self.partition not in {"train", "validation"}
            or not self.steps or type(self.burn_in) is not int or self.burn_in < 0
            or type(self.research_only) is not bool
        ):
            raise ValueError("v4 recurrent sequence authority is invalid")
        if any(
            type(step) is not RecurrentBCStepV4
            or step.episode_group != self.episode_group
            or step.component_id != self.component_id
            or step.partition != self.partition
            or step.research_only != self.research_only
            for step in self.steps
        ):
            raise ValueError("v4 recurrent sequence crosses an authority boundary")


@dataclass(frozen=True, slots=True)
class PreparedRecurrentLaneV4:
    receipt_path: Path
    expected_receipt_file_sha256: str
    lane: str


def _external_sort_v4(source: Path, destination: Path, *keys: str, scratch: Path) -> None:
    if shutil.which("sort") is None:
        raise RuntimeError("recurrent v4 disk join requires the system sort utility")
    environment = {"LC_ALL": "C", "LANG": "C", "PATH": os.defpath, "TMPDIR": str(scratch.resolve())}
    with destination.open("xb") as output:
        subprocess.run(
            ["sort", "-s", "-t", "\t", *keys, str(source)], check=True,
            stdout=output, env=environment,
        )


def _source_tsv_row(raw: bytes) -> tuple[int, str, str]:
    try:
        ordinal_text, record_id, content_hash = raw.rstrip(b"\n").decode("ascii").split("\t")
        ordinal = int(ordinal_text)
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValueError("recurrent v4 source join row is malformed") from exc
    if ordinal < 0:
        raise ValueError("recurrent v4 source ordinal is invalid")
    _require_sha(record_id, "recurrent v4 source record_id")
    _require_sha(content_hash, "recurrent v4 source content_hash")
    return ordinal, record_id, content_hash


def _join_quality_overlay_v4(
    source_metadata: Path, overlay_rows: Iterator[TeacherQualityOverlayRowV2], *,
    destination: Path, scratch: Path,
) -> dict[str, object]:
    """Disk-sort source IDs, exact merge-join READY rows, restore physical order."""
    sorted_source = scratch / "v4-source-by-record.tsv"
    joined = scratch / "v4-quality-by-ordinal.tsv"
    _external_sort_v4(source_metadata, sorted_source, "-k2,2", scratch=scratch)
    overlay = iter(overlay_rows)
    current_overlay = next(overlay, None)
    previous_source: str | None = None
    previous_overlay: str | None = None
    with sorted_source.open("rb") as source, joined.open("xb") as output:
        for raw in source:
            ordinal, record_id, content_hash = _source_tsv_row(raw)
            if record_id == previous_source:
                raise ValueError("recurrent v4 source has duplicate record_id")
            previous_source = record_id
            if current_overlay is None:
                raise ValueError("teacher-quality overlay is missing a selected record")
            if current_overlay.record_id == previous_overlay:
                raise ValueError("teacher-quality overlay has duplicate record_id")
            if current_overlay.record_id < record_id:
                raise ValueError("teacher-quality overlay contains an extra record")
            if current_overlay.record_id > record_id:
                raise ValueError("teacher-quality overlay is missing a selected record")
            if current_overlay.content_hash != content_hash:
                raise ValueError("teacher-quality overlay content_hash mismatches selection")
            weight = _quality_weight(current_overlay.quality_weight)
            if current_overlay.exclusion_reason is not None:
                raise ValueError("READY teacher-quality overlay contains an exclusion")
            output.write(f"{ordinal}\t{record_id}\t{content_hash}\t{format(weight, '.15g')}\n".encode("ascii"))
            previous_overlay = current_overlay.record_id
            current_overlay = next(overlay, None)
    if current_overlay is not None:
        if current_overlay.record_id == previous_overlay:
            raise ValueError("teacher-quality overlay has duplicate record_id")
        raise ValueError("teacher-quality overlay contains an extra record")
    physical = scratch / "v4-quality-physical.tsv"
    _external_sort_v4(joined, physical, "-k1,1n", scratch=scratch)
    digest = hashlib.sha256()
    histogram: Counter[str] = Counter()
    count = 0
    with physical.open("rb") as source, destination.open("xb") as output:
        for raw in source:
            ordinal, record_id, content_hash, weight_text = raw.rstrip(b"\n").decode("ascii").split("\t")
            if int(ordinal) != count:
                raise ValueError("recurrent v4 quality join has a physical-order gap")
            weight = _quality_weight(float(weight_text))
            body = _canonical({
                "schema": _QUALITY_SCHEMA, "ordinal": count, "record_id": record_id,
                "content_hash": content_hash, "quality_weight": weight,
            }) + b"\n"
            output.write(body)
            digest.update(body)
            histogram[format(weight, ".15g")] += 1
            count += 1
    if count == 0:
        raise ValueError("recurrent v4 quality join is empty")
    return {
        "row_count": count, "file_sha256": digest.hexdigest(),
        "weight_histogram": dict(sorted(histogram.items())),
    }


@dataclass(frozen=True, slots=True)
class _ProjectedStepDraftV4:
    state: RelationalStateV4
    target_index: int
    episode_group: str
    model_input: object
    step_input: object
    target_masses: tuple[float, ...]
    reach_mass: float
    episode_start: bool
    component_id: str
    partition: str
    record_id: str
    content_hash: str


def _project_record_steps_v4(
    row: RecurrentRecordAuthorityRowV3, *, vocabulary: object, episode_start: bool,
) -> list[_ProjectedStepDraftV4]:
    from mage_ptcg.meta_specialist.actor_visible_features_v1 import (
        ExtractedSpecialistModelInputV1,
        build_specialist_step_input_v1,
    )
    from mage_ptcg.meta_specialist.local_dataset_v2 import semantic_loss_rows_from_record_v2
    from mage_ptcg.meta_specialist.training_example_envelope_v2 import (
        specialist_model_input_from_training_payload_v2,
    )

    record = row.record
    model_input = specialist_model_input_from_training_payload_v2(row.model_payload)
    groups: dict[bytes, list[int]] = {}
    for index, semantic in enumerate(model_input.candidate_rows):
        groups.setdefault(_canonical(semantic.to_dict()), []).append(index)
    offsets: dict[bytes, int] = {}
    local_to_index: dict[str, int] = {}
    actions = record.get("legal_actions")
    if type(actions) is not list:
        raise ValueError("qualified recurrent v4 legal actions are invalid")
    for action in sorted(actions, key=lambda value: value["local_action_id"] if type(value) is dict else ""):
        if type(action) is not dict:
            raise ValueError("qualified recurrent v4 action is invalid")
        key = _canonical(action["semantic_action"])
        offset = offsets.get(key, 0)
        if key not in groups or offset >= len(groups[key]):
            raise ValueError("recurrent v4 legal action/model input mismatched")
        local_to_index[str(action["local_action_id"])] = groups[key][offset]
        offsets[key] = offset + 1
    extracted = ExtractedSpecialistModelInputV1(model_input, record["model_input_id"], local_to_index)
    aliases = {
        key: sorted(
            local for local, index in local_to_index.items()
            if _canonical(model_input.candidate_rows[index].to_dict()) == key
        )
        for key in groups
    }
    episode = record.get("episode_id_hash")
    if type(episode) is not str or not episode:
        raise ValueError("qualified recurrent v4 episode is invalid")
    result: list[_ProjectedStepDraftV4] = []
    for loss_row in semantic_loss_rows_from_record_v2(record, vocabulary=vocabulary):
        reach_mass = _reach_mass(loss_row.get("reach_mass"))
        prefix_counts: dict[bytes, int] = {}
        prefix: list[str] = []
        for semantic in loss_row["semantic_prefix"]:
            key = _canonical(semantic)
            offset = prefix_counts.get(key, 0)
            candidates = aliases.get(key, [])
            if offset >= len(candidates):
                raise ValueError("recurrent v4 canonical prefix lacks distinct local aliases")
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
            raise ValueError("recurrent v4 teacher masses disagree with rebuilt legality")
        masses = tuple(token_map[key] for key in expected)
        if not math.isclose(math.fsum(masses), 1.0, rel_tol=0.0, abs_tol=1e-12):
            raise ValueError("recurrent v4 teacher masses do not normalize")
        state = representation_v4_from_step_input_v1(
            model_input, step_input, allow_unbound_selected=True,
        )
        target = max(range(len(masses)), key=lambda index: (masses[index], -index))
        result.append(_ProjectedStepDraftV4(
            state, target, episode, model_input, step_input, masses, reach_mass,
            episode_start and not result, row.component_id, row.partition,
            row.record_id, row.content_hash,
        ))
    if not result:
        raise ValueError("qualified recurrent v4 record has no canonical loss rows")
    return result


def _projection_payload_v4(row: RecurrentRecordAuthorityRowV3, steps: Sequence[_ProjectedStepDraftV4]) -> bytes:
    return _canonical({
        "record_id": row.record_id, "content_hash": row.content_hash,
        "raw_line_sha256": row.raw_line_sha256, "shard": row.shard, "line": row.line,
        "component_id": row.component_id, "partition": row.partition,
        "steps": [{
            "state": step.state.public_feature_dict(), "target_index": step.target_index,
            "target_masses": list(step.target_masses), "reach_mass": step.reach_mass,
            "episode_group": step.episode_group,
            "episode_start": step.episode_start, "component_id": step.component_id,
            "partition": step.partition, "record_id": step.record_id,
            "content_hash": step.content_hash,
        } for step in steps],
    }) + b"\n"


def _representation_implementation_sha_v4() -> str:
    from mage_ptcg.meta_specialist import representation_v4

    path = Path(representation_v4.__file__ or "")
    if not path.is_file():
        raise ValueError("representation v4 implementation file is unavailable")
    return _file_sha(path)


def _assert_nonsymlink_directory_v4(path: Path, *, name: str) -> Path:
    unresolved = path if path.is_absolute() else Path.cwd() / path
    if ".." in unresolved.parts:
        raise ValueError(f"{name} contains parent traversal")
    current = Path(unresolved.anchor)
    last: os.stat_result | None = None
    for part in unresolved.parts[1:]:
        current /= part
        try:
            last = os.lstat(current)
        except OSError as exc:
            raise ValueError(f"{name} path component is unavailable") from exc
        if stat.S_ISLNK(last.st_mode):
            raise ValueError(f"{name} path component must not be a symlink")
    if last is None or not stat.S_ISDIR(last.st_mode):
        raise ValueError(f"{name} must be a directory")
    return unresolved.resolve()


def _read_regular_v4(path: Path, *, expected_sha: str, name: str) -> bytes:
    _require_sha(expected_sha, f"expected {name}")
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if type(nofollow) is not int:
        raise ValueError("recurrent v4 authority access requires O_NOFOLLOW")
    try:
        descriptor = os.open(path, os.O_RDONLY | nofollow)
    except OSError as exc:
        raise ValueError(f"{name} cannot be opened without following a symlink") from exc
    with os.fdopen(descriptor, "rb", closefd=True) as handle:
        before = os.fstat(handle.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise ValueError(f"{name} is not a regular file")
        raw = handle.read()
        after = os.fstat(handle.fileno())
        identity = lambda item: (item.st_dev, item.st_ino, item.st_mode, item.st_size, item.st_mtime_ns)
        if identity(before) != identity(after) or len(raw) != before.st_size:
            raise ValueError(f"{name} changed during read")
    if hashlib.sha256(raw).hexdigest() != expected_sha:
        raise ValueError(f"{name} external file SHA-256 does not match")
    return raw


def _atomic_copy_v4(source: Path, destination: Path) -> None:
    temporary = destination.with_name(f".{destination.name}.tmp-{os.getpid()}")
    try:
        with source.open("rb") as input_handle, temporary.open("xb") as output:
            shutil.copyfileobj(input_handle, output, length=1024 * 1024)
            output.flush(); os.fsync(output.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_json_v4(path: Path, payload: Mapping[str, object]) -> None:
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        with temporary.open("xb") as output:
            output.write(_canonical(payload)); output.flush(); os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _retire_v4_authority(output: Path, receipt: Path, quality: Path) -> None:
    existing = [path for path in (receipt, quality) if path.exists() or path.is_symlink()]
    if not existing:
        return
    retired = output / ".retired-v4"
    retired.mkdir(exist_ok=True)
    if retired.is_symlink() or retired.resolve().parent != output:
        raise ValueError("recurrent v4 retirement root escapes output")
    archive = Path(tempfile.mkdtemp(prefix="retired-", dir=retired.resolve())).resolve()
    for path in existing:
        os.replace(path, archive / path.name)


def prepare_sealed_recurrent_lane_v4(
    selection_manifest_path: str | Path, *, expected_selection_manifest_file_sha256: str,
    teacher_quality_manifest_path: str | Path,
    expected_teacher_quality_manifest_file_sha256: str,
    expected_teacher_quality_manifest_sha256: str,
    output_dir: str | Path, command_identity: str,
) -> PreparedRecurrentLaneV4:
    """Publish v4 projection and exact physical quality evidence only after full preflight."""
    for value, name in (
        (expected_selection_manifest_file_sha256, "selection manifest file"),
        (expected_teacher_quality_manifest_file_sha256, "teacher-quality manifest file"),
        (expected_teacher_quality_manifest_sha256, "teacher-quality manifest"),
    ):
        _require_sha(value, name)
    if type(command_identity) is not str or not command_identity:
        raise ValueError("recurrent v4 command identity is invalid")
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    output = _assert_nonsymlink_directory_v4(output, name="recurrent v4 output directory")
    receipt_path = output / "recurrent-lane-preflight-v4.json"
    quality_path = output / "recurrent-quality-v4.jsonl"
    _retire_v4_authority(output, receipt_path, quality_path)
    started = time.monotonic()
    selection_input = Path(selection_manifest_path)
    teacher_input = Path(teacher_quality_manifest_path)
    selection_parent = _assert_nonsymlink_directory_v4(
        selection_input.parent, name="recurrent v4 selection directory",
    )
    teacher_parent = _assert_nonsymlink_directory_v4(
        teacher_input.parent, name="recurrent v4 teacher-quality directory",
    )
    selection_path = selection_parent / selection_input.name
    teacher_path = teacher_parent / teacher_input.name
    source = verify_recurrent_record_authority_v3(
        selection_path, expected_manifest_file_sha256=expected_selection_manifest_file_sha256,
    )
    teacher = read_teacher_quality_manifest_v2(
        teacher_path,
        expected_manifest_file_sha256=expected_teacher_quality_manifest_file_sha256,
        expected_manifest_sha256=expected_teacher_quality_manifest_sha256,
    )
    if teacher.get("status") != "READY" or teacher.get("theta0_allowed") is not True or teacher.get("authority_gap") is not None:
        raise ValueError("teacher-quality authority is not READY for recurrent v4")
    overlay_ref = teacher.get("overlay")
    if type(overlay_ref) is not dict:
        raise ValueError("teacher-quality authority lacks an overlay reference")
    overlay_file_sha = _require_sha(
        overlay_ref.get("file_sha256"), "teacher-quality overlay file SHA-256",
    )
    if (
        type(overlay_ref.get("row_count")) is not int
        or overlay_ref["row_count"] != teacher.get("row_count")
        or teacher["row_count"] != source.records_total
    ):
        raise ValueError("teacher-quality overlay count does not cover the recurrent v4 source")
    representation_sha = _representation_implementation_sha_v4()
    from mage_ptcg.meta_specialist.representation_benchmark_v3 import _load_production_vocabulary_v3
    vocabulary = _load_production_vocabulary_v3()
    with tempfile.TemporaryDirectory(prefix="recurrent-v4-", dir=output) as directory:
        scratch = Path(directory).resolve()
        metadata = scratch / "physical-source.tsv"
        projection_digest = hashlib.sha256()
        records = steps_checked = 0
        episodes = _PhysicalEpisodeTrackerV3(scratch)
        try:
            with metadata.open("xb") as spool:
                for row in stream_recurrent_record_authority_v3(
                    selection_path,
                    expected_manifest_file_sha256=expected_selection_manifest_file_sha256,
                    expected_manifest_sha256=source.manifest_sha256,
                    expected_selection_index_sha256=source.selection_index_sha256,
                    expected_records_total=source.records_total, expected_split=source.split,
                    expected_chunks=source.chunks,
                ):
                    episode = row.record.get("episode_id_hash")
                    if type(episode) is not str or not episode:
                        raise ValueError("recurrent v4 source episode is invalid")
                    episode_start = episodes.advance(episode)
                    projected = _project_record_steps_v4(row, vocabulary=vocabulary, episode_start=episode_start)
                    projection_digest.update(_projection_payload_v4(row, projected))
                    steps_checked += len(projected)
                    spool.write(f"{records}\t{row.record_id}\t{row.content_hash}\n".encode("ascii"))
                    records += 1
        finally:
            episodes.close()
        if records != source.records_total or steps_checked < records:
            raise ValueError("recurrent v4 projection does not cover the sealed source")
        staged_quality = scratch / "quality.jsonl"
        quality = _join_quality_overlay_v4(
            metadata,
            stream_ready_teacher_quality_overlay_v2(
                teacher_path,
                expected_manifest_file_sha256=expected_teacher_quality_manifest_file_sha256,
                expected_manifest_sha256=expected_teacher_quality_manifest_sha256,
            ),
            destination=staged_quality, scratch=scratch,
        )
        if quality["row_count"] != records:
            raise ValueError("recurrent v4 quality join does not cover every selected record")
        _atomic_copy_v4(staged_quality, quality_path)
        if _file_sha(quality_path) != quality["file_sha256"]:
            raise RuntimeError("recurrent v4 quality sidecar atomic copy does not verify")
    projection = {
        "schema": _PROJECTION_SCHEMA, "records_checked": records,
        "steps_checked": steps_checked, "aggregate_sha256": projection_digest.hexdigest(),
        "representation_implementation_sha256": representation_sha,
    }
    payload: dict[str, object] = {
        "schema": _RECEIPT_SCHEMA, "lane": source.lane,
        "command_identity": command_identity, "records_total": records,
        "split": source.split, "chunks": list(source.chunks),
        "source": {
            "manifest_path": str(selection_path),
            "manifest_file_sha256": expected_selection_manifest_file_sha256,
            "manifest_sha256": source.manifest_sha256,
            "selection_index_sha256": source.selection_index_sha256,
        },
        "teacher_quality": {
            "manifest_path": str(teacher_path),
            "manifest_file_sha256": expected_teacher_quality_manifest_file_sha256,
            "manifest_sha256": expected_teacher_quality_manifest_sha256,
            "overlay_file_sha256": overlay_file_sha,
            "row_count": teacher.get("row_count"),
        },
        "quality_sidecar": {
            "basename": quality_path.name, **quality,
        },
        "projection": projection,
        "preflight_seconds": time.monotonic() - started,
    }
    payload["receipt_sha256"] = _object_sha(payload)
    _atomic_json_v4(receipt_path, payload)
    if _read_receipt_v4(receipt_path, expected_file_sha256=_file_sha(receipt_path)) != payload:
        raise RuntimeError("recurrent v4 receipt atomic reload differs from written bytes")
    return PreparedRecurrentLaneV4(receipt_path, _file_sha(receipt_path), source.lane)


def _read_receipt_v4(path: Path, *, expected_file_sha256: str) -> dict[str, object]:
    raw = _read_regular_v4(path, expected_sha=expected_file_sha256, name="recurrent v4 receipt")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("recurrent v4 receipt is not JSON") from exc
    keys = {
        "schema", "lane", "command_identity", "records_total", "split", "chunks",
        "source", "teacher_quality", "quality_sidecar", "projection",
        "preflight_seconds", "receipt_sha256",
    }
    if type(payload) is not dict or set(payload) != keys or _canonical(payload) != raw:
        raise ValueError("recurrent v4 receipt has an invalid closed schema")
    if payload["schema"] != _RECEIPT_SCHEMA:
        raise ValueError("recurrent v4 receipt schema is invalid")
    claimed = _require_sha(payload["receipt_sha256"], "recurrent v4 receipt self SHA-256")
    if _object_sha({key: value for key, value in payload.items() if key != "receipt_sha256"}) != claimed:
        raise ValueError("recurrent v4 receipt self SHA-256 does not match")
    if type(payload["lane"]) is not str or not payload["lane"] or type(payload["command_identity"]) is not str or not payload["command_identity"]:
        raise ValueError("recurrent v4 receipt lane/command identity is invalid")
    if type(payload["records_total"]) is not int or payload["records_total"] < 1:
        raise ValueError("recurrent v4 receipt record count is invalid")
    split = payload["split"]
    if (
        type(split) is not dict
        or set(split) != {
            "schema", "validation_fraction", "ubiquitous_keys", "ubiquitous_metadata",
            "components_total", "counts", "overlap_counters", "components_sha256",
        }
        or split.get("schema") != "meta-specialist-recurrent-split-summary-v1"
        or type(split.get("counts")) is not dict
        or set(split["counts"]) != {"train", "validation"}
        or any(type(value) is not int or value < 1 for value in split["counts"].values())
        or sum(split["counts"].values()) != payload["records_total"]
        or split.get("overlap_counters") != {"episode_overlap": 0, "near_duplicate_overlap": 0}
    ):
        raise ValueError("recurrent v4 receipt split authority is invalid")
    chunks = payload["chunks"]
    if type(chunks) is not list or not chunks:
        raise ValueError("recurrent v4 receipt chunk authority is invalid")
    seen_chunks: set[str] = set()
    previous_chunk = ""
    for chunk in chunks:
        if (
            type(chunk) is not dict or set(chunk) != {"shard", "sha256"}
            or type(chunk["shard"]) is not str or not re.fullmatch(r"dataset-[0-9]{4}\.jsonl", chunk["shard"])
            or chunk["shard"] in seen_chunks or chunk["shard"] <= previous_chunk
        ):
            raise ValueError("recurrent v4 receipt chunk authority is invalid")
        seen_chunks.add(chunk["shard"])
        previous_chunk = chunk["shard"]
        _require_sha(chunk["sha256"], "recurrent v4 receipt chunk SHA-256")
    source = payload["source"]
    if type(source) is not dict or set(source) != {
        "manifest_path", "manifest_file_sha256", "manifest_sha256", "selection_index_sha256",
    } or type(source["manifest_path"]) is not str or not Path(source["manifest_path"]).is_absolute():
        raise ValueError("recurrent v4 receipt source authority is invalid")
    for field in ("manifest_file_sha256", "manifest_sha256", "selection_index_sha256"):
        _require_sha(source[field], f"recurrent v4 source {field}")
    teacher = payload["teacher_quality"]
    if type(teacher) is not dict or set(teacher) != {
        "manifest_path", "manifest_file_sha256", "manifest_sha256", "overlay_file_sha256", "row_count",
    } or type(teacher["manifest_path"]) is not str or not Path(teacher["manifest_path"]).is_absolute():
        raise ValueError("recurrent v4 receipt teacher-quality authority is invalid")
    for field in ("manifest_file_sha256", "manifest_sha256", "overlay_file_sha256"):
        _require_sha(teacher[field], f"recurrent v4 teacher-quality {field}")
    if type(teacher["row_count"]) is not int or teacher["row_count"] != payload["records_total"]:
        raise ValueError("recurrent v4 teacher-quality count is invalid")
    quality = payload["quality_sidecar"]
    if type(quality) is not dict or set(quality) != {
        "basename", "row_count", "file_sha256", "weight_histogram",
    } or quality["basename"] != "recurrent-quality-v4.jsonl":
        raise ValueError("recurrent v4 quality sidecar descriptor is invalid")
    _require_sha(quality["file_sha256"], "recurrent v4 quality sidecar SHA-256")
    histogram = quality["weight_histogram"]
    if (
        type(quality["row_count"]) is not int or quality["row_count"] != payload["records_total"]
        or type(histogram) is not dict or not histogram
        or any(type(key) is not str or type(value) is not int or value < 1 for key, value in histogram.items())
        or sum(histogram.values()) != payload["records_total"]
    ):
        raise ValueError("recurrent v4 quality sidecar counts are invalid")
    for key in histogram:
        _quality_weight(float(key))
    projection = payload["projection"]
    if type(projection) is not dict or set(projection) != {
        "schema", "records_checked", "steps_checked", "aggregate_sha256",
        "representation_implementation_sha256",
    } or projection["schema"] != _PROJECTION_SCHEMA:
        raise ValueError("recurrent v4 projection descriptor is invalid")
    if (
        projection["records_checked"] != payload["records_total"]
        or type(projection["steps_checked"]) is not int
        or projection["steps_checked"] < payload["records_total"]
    ):
        raise ValueError("recurrent v4 projection counts are invalid")
    _require_sha(projection["aggregate_sha256"], "recurrent v4 projection aggregate")
    _require_sha(projection["representation_implementation_sha256"], "recurrent v4 representation implementation")
    if type(payload["preflight_seconds"]) not in {int, float} or not math.isfinite(float(payload["preflight_seconds"])) or payload["preflight_seconds"] < 0:
        raise ValueError("recurrent v4 preflight timing is invalid")
    return payload


def _quality_rows_v4(path: Path, *, expected_sha: str) -> Iterator[dict[str, object]]:
    """Parse only an anonymous sealed copy of the quality sidecar.

    The public sidecar is opened once with ``O_NOFOLLOW`` and copied while it
    is hashed.  This avoids the unsafe hash/seek/parse pattern: a rewrite of
    the same inode after the digest pass cannot change rows later consumed by
    the learner, and a source path is never reopened.
    """
    _require_sha(expected_sha, "expected recurrent v4 quality sidecar")
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if type(nofollow) is not int:
        raise ValueError("recurrent v4 authority access requires O_NOFOLLOW")
    try:
        descriptor = os.open(path, os.O_RDONLY | nofollow)
    except OSError as exc:
        raise ValueError("recurrent v4 quality sidecar cannot be opened without following a symlink") from exc
    with os.fdopen(descriptor, "rb", closefd=True) as source:
        before = os.fstat(source.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("recurrent v4 quality sidecar is not a regular file")
        digest = hashlib.sha256()
        identity = lambda item: (
            item.st_dev, item.st_ino, item.st_mode, item.st_size,
            item.st_mtime_ns, item.st_ctime_ns,
        )
        with tempfile.TemporaryFile(mode="w+b", prefix="recurrent-quality-v4-") as spool:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
                spool.write(block)
            if identity(before) != identity(os.fstat(source.fileno())) or digest.hexdigest() != expected_sha:
                raise ValueError("recurrent v4 quality sidecar SHA-256 or descriptor changed")
            spool.seek(0)
            previous = -1
            for line in spool:
                if not line.endswith(b"\n") or line == b"\n" or b"\r" in line:
                    raise ValueError("recurrent v4 quality sidecar is not canonical LF JSONL")
                try:
                    item = json.loads(line[:-1].decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise ValueError("recurrent v4 quality sidecar row is invalid JSON") from exc
                if type(item) is not dict or set(item) != {
                    "schema", "ordinal", "record_id", "content_hash", "quality_weight",
                } or _canonical(item) + b"\n" != line:
                    raise ValueError("recurrent v4 quality sidecar row has an invalid closed schema")
                if item["schema"] != _QUALITY_SCHEMA or type(item["ordinal"]) is not int or item["ordinal"] != previous + 1:
                    raise ValueError("recurrent v4 quality sidecar physical order is invalid")
                _require_sha(item["record_id"], "recurrent v4 quality record_id")
                _require_sha(item["content_hash"], "recurrent v4 quality content_hash")
                item["quality_weight"] = _quality_weight(item["quality_weight"])
                previous = item["ordinal"]
                yield item
            if previous < 0:
                raise ValueError("recurrent v4 quality sidecar is empty")


def stream_prepared_recurrent_selection_v4(
    receipt_path: str | Path, *, expected_receipt_file_sha256: str,
    burn_in: int, partition: str,
) -> Iterator[RecurrentBCSequenceV4]:
    """Revalidate source, READY authority, and physical quality sidecar each pass."""
    if type(burn_in) is not int or burn_in < 0 or partition not in {"train", "validation"}:
        raise ValueError("recurrent v4 prepared stream arguments are invalid")

    def iterator() -> Iterator[RecurrentBCSequenceV4]:
        receipt_input = Path(receipt_path)
        parent = _assert_nonsymlink_directory_v4(
            receipt_input.parent, name="recurrent v4 prepared directory",
        )
        receipt = _read_receipt_v4(
            parent / receipt_input.name, expected_file_sha256=expected_receipt_file_sha256,
        )
        if _representation_implementation_sha_v4() != receipt["projection"]["representation_implementation_sha256"]:
            raise ValueError("recurrent v4 representation implementation changed after preflight")
        teacher_ref = receipt["teacher_quality"]
        teacher_path = Path(teacher_ref["manifest_path"])
        _assert_nonsymlink_directory_v4(
            teacher_path.parent, name="recurrent v4 prepared teacher-quality directory",
        )
        teacher = read_teacher_quality_manifest_v2(
            teacher_path,
            expected_manifest_file_sha256=teacher_ref["manifest_file_sha256"],
            expected_manifest_sha256=teacher_ref["manifest_sha256"],
        )
        overlay_ref = teacher.get("overlay")
        if (
            teacher.get("status") != "READY" or teacher.get("theta0_allowed") is not True
            or teacher.get("authority_gap") is not None or teacher.get("row_count") != receipt["records_total"]
            or type(overlay_ref) is not dict
            or overlay_ref.get("file_sha256") != teacher_ref["overlay_file_sha256"]
        ):
            raise ValueError("recurrent v4 prepared teacher-quality authority changed or is not READY")
        quality_ref = receipt["quality_sidecar"]
        quality_rows = _quality_rows_v4(
            parent / quality_ref["basename"], expected_sha=quality_ref["file_sha256"],
        )
        source_ref = receipt["source"]
        source_path = Path(source_ref["manifest_path"])
        _assert_nonsymlink_directory_v4(
            source_path.parent, name="recurrent v4 prepared source directory",
        )
        source_rows = stream_recurrent_record_authority_v3(
            source_path,
            expected_manifest_file_sha256=source_ref["manifest_file_sha256"],
            expected_manifest_sha256=source_ref["manifest_sha256"],
            expected_selection_index_sha256=source_ref["selection_index_sha256"],
            expected_records_total=receipt["records_total"], expected_split=receipt["split"],
            expected_chunks=tuple(receipt["chunks"]),
        )
        from mage_ptcg.meta_specialist.representation_benchmark_v3 import _load_production_vocabulary_v3
        vocabulary = _load_production_vocabulary_v3()
        digest = hashlib.sha256()
        histogram: Counter[str] = Counter()
        records = steps_checked = 0
        current_episode: str | None = None
        episodes = _PhysicalEpisodeTrackerV3()
        current_component: str | None = None
        current_partition: str | None = None
        current_steps: list[RecurrentBCStepV4] = []

        def close() -> RecurrentBCSequenceV4 | None:
            if current_episode is None:
                return None
            assert current_component is not None and current_partition is not None
            return RecurrentBCSequenceV4(
                str(receipt["lane"]), current_episode, current_component,
                current_partition, tuple(current_steps), burn_in,
            )

        try:
            for row in source_rows:
                try:
                    quality = next(quality_rows)
                except StopIteration as exc:
                    raise ValueError("recurrent v4 quality sidecar is missing a source record") from exc
                if (
                    quality["ordinal"] != records or quality["record_id"] != row.record_id
                    or quality["content_hash"] != row.content_hash
                ):
                    raise ValueError("recurrent v4 source/quality sidecar lockstep mismatch")
                episode = row.record.get("episode_id_hash")
                if type(episode) is not str or not episode:
                    raise ValueError("recurrent v4 stream episode is invalid")
                episode_start = episodes.advance(episode)
                previous: RecurrentBCSequenceV4 | None = None
                if episode_start:
                    if current_episode is not None:
                        previous = close()
                    current_episode = episode
                    current_component = row.component_id
                    current_partition = row.partition
                    current_steps = []
                elif row.component_id != current_component or row.partition != current_partition:
                    raise ValueError("recurrent v4 stream crosses component/partition inside an episode")
                projected = _project_record_steps_v4(row, vocabulary=vocabulary, episode_start=episode_start)
                digest.update(_projection_payload_v4(row, projected))
                steps_checked += len(projected)
                weight = float(quality["quality_weight"])
                histogram[format(weight, ".15g")] += 1
                current_steps.extend(RecurrentBCStepV4(
                    state=step.state, target_index=step.target_index,
                    episode_group=step.episode_group, quality_weight=weight,
                    model_input=step.model_input, step_input=step.step_input,
                    target_masses=step.target_masses, reach_mass=step.reach_mass,
                    episode_start=step.episode_start,
                    component_id=step.component_id, partition=step.partition,
                    record_id=step.record_id, content_hash=step.content_hash,
                    supervision_weight=float(getattr(step, "supervision_weight", 1.0)),
                ) for step in projected)
                records += 1
                if previous is not None and previous.partition == partition:
                    yield previous
            try:
                next(quality_rows)
            except StopIteration:
                pass
            else:
                raise ValueError("recurrent v4 quality sidecar has an extra record")
            if (
                records != receipt["records_total"]
                or steps_checked != receipt["projection"]["steps_checked"]
                or digest.hexdigest() != receipt["projection"]["aggregate_sha256"]
                or dict(sorted(histogram.items())) != quality_ref["weight_histogram"]
            ):
                raise ValueError("recurrent v4 prepared projection/quality aggregate changed")
            final = close()
            if final is None:
                raise ValueError("recurrent v4 prepared stream produced no sequences")
            if final.partition == partition:
                yield final
        finally:
            episodes.close()

    return iterator()


def validate_prepared_recurrent_pair_v4(
    train_receipt_path: str | Path, *, train_expected_receipt_file_sha256: str,
    validation_receipt_path: str | Path, validation_expected_receipt_file_sha256: str,
) -> None:
    train_path = Path(train_receipt_path)
    validation_path = Path(validation_receipt_path)
    train = _read_receipt_v4(train_path, expected_file_sha256=train_expected_receipt_file_sha256)
    validation = _read_receipt_v4(
        validation_path, expected_file_sha256=validation_expected_receipt_file_sha256,
    )
    if train != validation:
        raise ValueError("prepared recurrent v4 training/validation receipts differ")
    if train["split"].get("overlap_counters") != {"episode_overlap": 0, "near_duplicate_overlap": 0}:
        raise ValueError("prepared recurrent v4 receipt permits split leakage")


__all__ = [
    "PreparedRecurrentLaneV4", "RecurrentBCSequenceV4", "RecurrentBCStepV4",
    "prepare_sealed_recurrent_lane_v4", "stream_prepared_recurrent_selection_v4",
    "validate_prepared_recurrent_pair_v4",
]
