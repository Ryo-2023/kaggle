"""Build and clean-room verify an optional standalone C4 Student v0 artifact.

This is separate from the approved Rule-v0 submission builder: it never
changes the default Champion package and requires an explicit exported model.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
from typing import Any
import zlib


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))
if str(REPOSITORY_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from scripts.kaggle_student_runtime import render_student_runtime
from mage_ptcg.meta_specialist.submission_privacy import (
    SubmissionPrivacyError,
    parse_submission_json_member,
    validate_submission_members,
)

ARCHIVE_NAME = "submission.tar.gz"
MANIFEST_NAME = "manifest.json"
MODEL_MEMBER = "models/student-v0.json"
RUNTIME_PATHS = (
    "main.py",
    "deck.csv",
    "agents/__init__.py",
    "agents/rule_agent.py",
    "src/mage_ptcg/__init__.py",
    "src/mage_ptcg/decision_state.py",
    "src/mage_ptcg/meta_specialist/__init__.py",
    "src/mage_ptcg/meta_specialist/cabt_json_contract_v1.py",
    "src/mage_ptcg/observability/__init__.py",
    "src/mage_ptcg/observability/cabt_trace.py",
    "src/mage_ptcg/student/__init__.py",
    "src/mage_ptcg/student/dataset.py",
    "src/mage_ptcg/student/features.py",
    "src/mage_ptcg/student/model.py",
    "src/mage_ptcg/student/runtime.py",
)
# 提出物内では汎用名 agents/ を使わず固有名 mage_submission_agents/ を使用する。
# Kaggle 環境の kaggle_environments/envs/lux_ai_s3/agents.py との衝突を回避するため。
_SUBMISSION_AGENT_PREFIX = "mage_submission_agents"
KAGGLE_STUDENT_RUNTIME_PATHS = (
    "main.py",
    "runtime_main.py",
    "deck.csv",
    f"{_SUBMISSION_AGENT_PREFIX}/__init__.py",
    f"{_SUBMISSION_AGENT_PREFIX}/rule_agent.py",
    *(name for name in RUNTIME_PATHS[1:]
      if name != "src/mage_ptcg/student/dataset.py"
      and not name.startswith("agents/")
      and name != "deck.csv"),
)
_KAGGLE_STUDENT_EXTRA_PATHS = (
    "student-model-manifest.json",
    "student-package-manifest.json",
)
_SUPPORTED_MEMBER_PROFILES = (
    (*RUNTIME_PATHS, MODEL_MEMBER),
    (*KAGGLE_STUDENT_RUNTIME_PATHS, MODEL_MEMBER),
    (*KAGGLE_STUDENT_RUNTIME_PATHS, MODEL_MEMBER, *_KAGGLE_STUDENT_EXTRA_PATHS),
)
_OUTER_MANIFEST_FIELDS = frozenset(
    {"agent_identity", "artifact_schema_version", "archive_sha256", "files"}
)
_MAX_RUNTIME_SOURCE_BYTES = 1 * 1024 * 1024
_MAX_DECK_BYTES = 1 * 1024 * 1024
_MAX_MEMBER_BYTES = 8 * 1024 * 1024
_MAX_TOTAL_MEMBER_BYTES = 32 * 1024 * 1024
_MAX_ARCHIVE_BYTES = 32 * 1024 * 1024
_MAX_ARCHIVE_MEMBERS = 64
_TAR_BLOCK_BYTES = 512
_TAR_RECORD_BYTES = 20 * _TAR_BLOCK_BYTES


class StudentArtifactError(ValueError):
    pass


def _member_byte_limit(name: str) -> int:
    """Return the bounded payload budget for one allowlisted submission role."""
    suffix = PurePosixPath(name).suffix.lower()
    if suffix == ".py":
        return _MAX_RUNTIME_SOURCE_BYTES
    if name == "deck.csv":
        return _MAX_DECK_BYTES
    return _MAX_MEMBER_BYTES


def _require_payload_size(name: str, data: bytes, *, message: str) -> None:
    if len(data) > _member_byte_limit(name):
        raise StudentArtifactError(f"{message}: {name}")


def _read_bounded_file(path: Path, *, limit: int, message: str) -> bytes:
    """Read a regular submission file only after a size preflight and cap."""
    try:
        preflight = path.stat()
    except OSError as exc:
        raise StudentArtifactError(message) from exc
    if not stat.S_ISREG(preflight.st_mode) or preflight.st_size < 0 or preflight.st_size > limit:
        raise StudentArtifactError(message)
    descriptor = -1
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or opened.st_size < 0 or opened.st_size > limit:
            os.close(descriptor)
            descriptor = -1
            raise StudentArtifactError(message)
        with os.fdopen(descriptor, "rb") as source:
            descriptor = -1
            data = source.read(limit + 1)
    except OSError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        raise StudentArtifactError(message) from exc
    if len(data) > limit:
        raise StudentArtifactError(message)
    return data


def _validate_submission_member_profile(members: list[tuple[str, bytes]]) -> None:
    names = tuple(name for name, _data in members)
    if names not in _SUPPORTED_MEMBER_PROFILES:
        raise StudentArtifactError("submission privacy: unsupported member profile")
    if sum(len(data) for _name, data in members) > _MAX_TOTAL_MEMBER_BYTES:
        raise StudentArtifactError("submission privacy: total member byte bound exceeded")
    try:
        validate_submission_members(members, allowed_members=names)
    except SubmissionPrivacyError as exc:
        raise StudentArtifactError(f"submission privacy: {exc}") from exc


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_path(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if not name or path.is_absolute() or ".." in path.parts or path.as_posix() != name:
        raise StudentArtifactError(f"unsafe artifact path: {name!r}")
    return path


def _files(
    model_path: Path,
    *,
    runtime_paths: tuple[str, ...] = RUNTIME_PATHS,
    generated_main: bytes | None = None,
    generated_files: dict[str, bytes] | None = None,
    extra_files: dict[str, bytes] | None = None,
) -> list[tuple[str, bytes]]:
    if not model_path.is_file() or model_path.is_symlink():
        raise StudentArtifactError("Student model must be a regular file")
    sources: list[tuple[str, Path | bytes]] = []
    for name in runtime_paths:
        if generated_files and name in generated_files:
            sources.append((name, generated_files[name]))
        elif name == "main.py" and generated_main is not None:
            sources.append((name, generated_main))
        elif name == "runtime_main.py":
            # main.py を丸ごとコピーせず、Student提出専用の最小runtimeを生成する。
            sources.append((name, render_student_runtime().encode("utf-8")))
        elif name.startswith(f"{_SUBMISSION_AGENT_PREFIX}/"):
            # mage_submission_agents/* → agents/* からマッピング
            repo_name = name.replace(_SUBMISSION_AGENT_PREFIX, "agents", 1)
            sources.append((name, REPOSITORY_ROOT.joinpath(*_safe_path(repo_name).parts)))
        else:
            sources.append((name, REPOSITORY_ROOT.joinpath(*_safe_path(name).parts)))
    sources.append((MODEL_MEMBER, model_path))
    for name, data in sorted((extra_files or {}).items()):
        _safe_path(name)
        if type(data) is not bytes:
            raise StudentArtifactError("submission privacy: extra file payload must be bytes")
        sources.append((name, data))
    from scripts.build_submission import _contains_secret

    member_bytes: list[tuple[str, bytes]] = []
    for name, source in sources:
        if isinstance(source, Path):
            if not source.is_file() or source.is_symlink():
                raise StudentArtifactError(f"runtime source must be a regular file: {name}")
            data = _read_bounded_file(
                source,
                limit=_member_byte_limit(name),
                message=f"runtime source exceeds byte bound: {name}",
            )
        else:
            data = source
        _require_payload_size(name, data, message="runtime source exceeds byte bound")
        if _contains_secret(data):
            raise StudentArtifactError(f"runtime source contains a secret marker: {name}")
        member_bytes.append((name, data))
    _validate_submission_member_profile(member_bytes)
    from mage_ptcg.student.model import ModelValidationError, StudentV0Model

    model_bytes = next(data for name, data in member_bytes if name == MODEL_MEMBER)
    try:
        StudentV0Model.from_dict(json.loads(model_bytes.decode("utf-8")))
    except (UnicodeDecodeError, json.JSONDecodeError, ModelValidationError) as exc:
        raise ModelValidationError(f"could not load Student v0 model: {exc}") from exc
    return member_bytes


def _record_bytes(name: str, data: bytes) -> dict[str, Any]:
    return {"path": name, "sha256": _sha256(data), "size": len(data)}


def _write_tar(destination: Path, files: list[tuple[str, bytes]]) -> str:
    archive_path = destination / ARCHIVE_NAME
    archive_bytes = _canonical_archive_bytes(files)
    with archive_path.open("xb") as raw:
        raw.write(archive_bytes)
    return _sha256(archive_bytes)


def _canonical_archive_bytes(files: list[tuple[str, bytes]]) -> bytes:
    """Render the one allowed gzip/USTAR representation of member snapshots."""
    raw = io.BytesIO()
    total_size = 0
    with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz:
        with tarfile.open(fileobj=gz, mode="w", format=tarfile.USTAR_FORMAT) as archive:
            for name, data in files:
                _require_payload_size(name, data, message="archive member exceeds byte bound")
                total_size += len(data)
                if total_size > _MAX_TOTAL_MEMBER_BYTES:
                    raise StudentArtifactError("archive total member byte bound exceeded")
                info = tarfile.TarInfo(name)
                info.size, info.mode, info.uid, info.gid, info.mtime = len(data), 0o644, 0, 0, 0
                info.uname = info.gname = ""
                archive.addfile(info, io.BytesIO(data))
    archive_bytes = raw.getvalue()
    if len(archive_bytes) > _MAX_ARCHIVE_BYTES:
        raise StudentArtifactError("archive exceeds byte bound")
    return archive_bytes


def _canonical_tar_byte_limit(expected_records: list[dict[str, Any]]) -> int:
    """Bound a raw USTAR stream from the trusted manifest before decompression."""
    if len(expected_records) > _MAX_ARCHIVE_MEMBERS:
        raise StudentArtifactError("archive member count exceeds byte bound")
    total = 2 * _TAR_BLOCK_BYTES
    total_member_bytes = 0
    for record in expected_records:
        if not isinstance(record, dict):
            raise StudentArtifactError("archive expected record is invalid")
        name, size = record.get("path"), record.get("size")
        if not isinstance(name, str) or type(size) is not int or size < 0:
            raise StudentArtifactError("archive expected record is invalid")
        _safe_path(name)
        if size > _member_byte_limit(name):
            raise StudentArtifactError("archive expected record exceeds byte bound")
        total_member_bytes += size
        if total_member_bytes > _MAX_TOTAL_MEMBER_BYTES:
            raise StudentArtifactError("archive total member byte bound exceeded")
        total += _TAR_BLOCK_BYTES + ((size + _TAR_BLOCK_BYTES - 1) // _TAR_BLOCK_BYTES) * _TAR_BLOCK_BYTES
    return ((total + _TAR_RECORD_BYTES - 1) // _TAR_RECORD_BYTES) * _TAR_RECORD_BYTES


def _bounded_gzip_tar_bytes(archive_bytes: bytes, *, limit: int) -> bytes:
    """Decompress exactly one gzip member without exceeding the raw tar budget."""
    decompressor = zlib.decompressobj(wbits=16 + zlib.MAX_WBITS)
    pending = archive_bytes
    output = bytearray()
    try:
        while pending:
            chunk = decompressor.decompress(pending, limit - len(output) + 1)
            output.extend(chunk)
            if len(output) > limit:
                raise StudentArtifactError("archive decompressed byte bound exceeded")
            if decompressor.eof:
                if decompressor.unused_data:
                    raise StudentArtifactError(
                        "archive is not canonical: trailing or concatenated gzip data"
                    )
                return bytes(output)
            pending = decompressor.unconsumed_tail
            if not pending:
                break
    except zlib.error as exc:
        raise StudentArtifactError("archive gzip stream is invalid") from exc
    raise StudentArtifactError("archive gzip stream is truncated")


def _ustar_member_name(header: bytes) -> str:
    name = header[:100].split(b"\0", 1)[0]
    prefix = header[345:500].split(b"\0", 1)[0]
    raw = prefix + (b"/" if prefix and name else b"") + name
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise StudentArtifactError("archive USTAR member name is invalid") from exc


def _ustar_member_size(header: bytes) -> int:
    field = header[124:136].rstrip(b"\0 ")
    if not field:
        return 0
    if any(byte < ord("0") or byte > ord("7") for byte in field):
        raise StudentArtifactError("archive USTAR member size is invalid")
    return int(field, 8)


def _preflight_canonical_ustar(
    archive_bytes: bytes,
    expected_records: list[dict[str, Any]],
) -> bytes:
    """Reject metadata extensions and decompression bombs before ``tarfile``.

    The submission format is the builder's canonical gzip-compressed USTAR
    stream.  Parsing only the fixed-size headers here makes PAX/global/GNU
    extension records fail before ``tarfile`` can allocate their payloads.
    """
    raw_tar = _bounded_gzip_tar_bytes(
        archive_bytes,
        limit=_canonical_tar_byte_limit(expected_records),
    )
    offset = 0
    zero_block = b"\0" * _TAR_BLOCK_BYTES
    while offset < len(raw_tar):
        header = raw_tar[offset : offset + _TAR_BLOCK_BYTES]
        if len(header) != _TAR_BLOCK_BYTES:
            raise StudentArtifactError("archive USTAR header is truncated")
        if header == zero_block:
            if any(raw_tar[offset:]):
                raise StudentArtifactError("archive contains non-zero data after end marker")
            return raw_tar
        typeflag = header[156:157]
        if typeflag in {b"x", b"g", b"L", b"K"}:
            raise StudentArtifactError("archive contains forbidden extended metadata")
        if typeflag not in {b"0", b"\0"}:
            raise StudentArtifactError("archive contains non-canonical member type")
        if header[257:263] != b"ustar\0" or header[263:265] != b"00":
            raise StudentArtifactError("archive is not canonical USTAR")
        size = _ustar_member_size(header)
        padded_size = ((size + _TAR_BLOCK_BYTES - 1) // _TAR_BLOCK_BYTES) * _TAR_BLOCK_BYTES
        offset += _TAR_BLOCK_BYTES + padded_size
        if offset > len(raw_tar):
            raise StudentArtifactError("archive member payload is truncated")
    raise StudentArtifactError("archive is missing its USTAR end marker")


def _extract(
    archive_source: Path | bytes,
    destination: Path,
    expected_records: list[dict[str, Any]],
) -> None:
    expected_members = [record["path"] for record in expected_records]
    archive_bytes = (
        _read_bounded_file(
            archive_source,
            limit=_MAX_ARCHIVE_BYTES,
            message="archive exceeds byte bound",
        )
        if isinstance(archive_source, Path)
        else archive_source
    )
    if len(archive_bytes) > _MAX_ARCHIVE_BYTES:
        raise StudentArtifactError("archive exceeds byte bound")
    raw_tar = _preflight_canonical_ustar(archive_bytes, expected_records)
    with tarfile.open(fileobj=io.BytesIO(raw_tar), mode="r:") as archive:
        extracted_members: list[tuple[str, bytes]] = []
        members: list[tarfile.TarInfo] = []
        for expected in expected_records:
            member = archive.next()
            if member is None or member.name != expected["path"]:
                raise StudentArtifactError("archive member list is unexpected")
            _safe_path(member.name)
            if not member.isreg() or member.mode != 0o644 or member.uid != 0 or member.gid != 0 or member.mtime != 0:
                raise StudentArtifactError("archive contains non-canonical member metadata")
            if member.size != expected["size"] or member.size > _member_byte_limit(member.name):
                raise StudentArtifactError(
                    "submission privacy: archive member does not match manifest record "
                    "(header size invalid)"
                )
            data = archive.extractfile(member)
            if data is None:
                raise StudentArtifactError("archive member cannot be read")
            payload = data.read(member.size + 1)
            if len(payload) != member.size:
                raise StudentArtifactError("archive member cannot be read")
            members.append(member)
            extracted_members.append((member.name, payload))
        if archive.next() is not None:
            raise StudentArtifactError("archive member list is unexpected")
        _validate_submission_member_profile(extracted_members)
        archive_records = [
            _record_bytes(name, payload) for name, payload in extracted_members
        ]
        if archive_records != expected_records:
            raise StudentArtifactError("archive member does not match manifest record")
        for member, payload in zip(members, (data for _name, data in extracted_members), strict=True):
            target = destination.joinpath(*_safe_path(member.name).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
    post_write_members = [
        (
            name,
            _read_bounded_file(
                destination.joinpath(*_safe_path(name).parts),
                limit=_member_byte_limit(name),
                message=f"post-extraction member exceeds byte bound: {name}",
            ),
        )
        for name in expected_members
    ]
    _validate_submission_member_profile(post_write_members)
    post_write_records = [
        _record_bytes(name, payload) for name, payload in post_write_members
    ]
    if post_write_records != expected_records:
        raise StudentArtifactError(
            "post-extraction member does not match manifest record"
        )


def _validated_manifest_records(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise StudentArtifactError("manifest files are invalid")
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    total_size = 0
    for record in value:
        if not isinstance(record, dict) or set(record) != {"path", "sha256", "size"}:
            raise StudentArtifactError("manifest file record is invalid")
        name = record["path"]
        digest = record["sha256"]
        size = record["size"]
        if not isinstance(name, str):
            raise StudentArtifactError("manifest file path is invalid")
        _safe_path(name)
        if name in seen:
            raise StudentArtifactError("manifest file path is duplicated")
        seen.add(name)
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            raise StudentArtifactError("manifest file digest is invalid")
        if type(size) is not int or size < 0:
            raise StudentArtifactError("manifest file size is invalid")
        if size > _member_byte_limit(name):
            raise StudentArtifactError("manifest file size exceeds byte bound")
        total_size += size
        if total_size > _MAX_TOTAL_MEMBER_BYTES:
            raise StudentArtifactError("manifest total file size exceeds byte bound")
        records.append({"path": name, "sha256": digest, "size": size})
    return records


def _is_lower_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _load_outer_manifest(root: Path) -> dict[str, Any]:
    manifest_path = root / MANIFEST_NAME
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise StudentArtifactError("manifest is unavailable or invalid")
    try:
        manifest_bytes = _read_bounded_file(
            manifest_path,
            limit=_MAX_MEMBER_BYTES,
            message="manifest exceeds byte bound",
        )
    except OSError as exc:
        raise StudentArtifactError("manifest is unavailable or invalid") from exc
    try:
        value = parse_submission_json_member(MANIFEST_NAME, manifest_bytes)
    except SubmissionPrivacyError as exc:
        raise StudentArtifactError(f"manifest privacy: {exc}") from exc
    if not isinstance(value, dict) or set(value) != _OUTER_MANIFEST_FIELDS:
        raise StudentArtifactError("outer manifest top-level fields are invalid")
    if (
        type(value["artifact_schema_version"]) is not int
        or value["artifact_schema_version"] != 1
    ):
        raise StudentArtifactError("outer manifest schema version is invalid")
    if not _is_lower_sha256(value["archive_sha256"]):
        raise StudentArtifactError("outer manifest archive hash is invalid")
    return value


def _validate_closed_inventory(root: Path, expected_members: list[str]) -> None:
    if root.is_symlink() or not root.is_dir():
        raise StudentArtifactError("artifact root violates the closed inventory")
    expected_files = {
        *expected_members,
        MANIFEST_NAME,
        ARCHIVE_NAME,
    }
    expected_directories: set[str] = set()
    for name in expected_files:
        for parent in PurePosixPath(name).parents:
            if parent.as_posix() != ".":
                expected_directories.add(parent.as_posix())
    try:
        entries = list(root.rglob("*"))
    except OSError as exc:
        raise StudentArtifactError("artifact closed inventory cannot be read") from exc
    for entry in entries:
        relative = entry.relative_to(root).as_posix()
        if entry.is_symlink():
            raise StudentArtifactError("artifact closed inventory contains a symlink")
        if entry.is_dir():
            if relative not in expected_directories:
                raise StudentArtifactError(
                    "artifact closed inventory contains an unmanifested directory"
                )
        elif entry.is_file():
            if relative not in expected_files:
                raise StudentArtifactError(
                    "artifact closed inventory contains an unmanifested file"
                )
        else:
            raise StudentArtifactError(
                "artifact closed inventory contains a non-regular entry"
            )


def verify_student_submission(artifact_dir: str | Path) -> dict[str, Any]:
    root = Path(artifact_dir)
    manifest = _load_outer_manifest(root)
    if manifest["agent_identity"] != "student-v0-rule-v0-fallback":
        raise StudentArtifactError("unexpected Student artifact identity")
    files = _validated_manifest_records(manifest["files"])
    expected_members = [record["path"] for record in files]
    _validate_closed_inventory(root, expected_members)
    root_members = [
        (
            name,
            _read_bounded_file(
                root.joinpath(*_safe_path(name).parts),
                limit=_member_byte_limit(name),
                message=f"root member exceeds byte bound: {name}",
            ),
        )
        for name in expected_members
    ]
    _validate_submission_member_profile(root_members)
    actual = [_record_bytes(name, data) for name, data in root_members]
    if actual != files:
        raise StudentArtifactError("manifest file hashes do not match")
    from scripts.build_submission import _contains_secret

    if any(_contains_secret(data) for _name, data in root_members):
        raise StudentArtifactError("artifact contains a secret marker")
    archive_bytes = _read_bounded_file(
        root / ARCHIVE_NAME,
        limit=_MAX_ARCHIVE_BYTES,
        message="archive exceeds byte bound",
    )
    if manifest.get("archive_sha256") != _sha256(archive_bytes):
        raise StudentArtifactError("archive hash does not match")
    with tempfile.TemporaryDirectory(prefix="student-v0-clean-room-") as temporary:
        extracted = Path(temporary) / "submission"
        extracted.mkdir()
        _extract(archive_bytes, extracted, files)
        if archive_bytes != _canonical_archive_bytes(root_members):
            raise StudentArtifactError("archive is not canonical")
        script = """
import sys
from pathlib import Path
artifact = Path(sys.argv[1])
sys.path.insert(0, str(artifact))
import main
agent = main.make_student_agent(deck=[1] * 60, model_path=artifact / 'models/student-v0.json')
obs = {'current': {'energyAttached': False, 'firstPlayer': 0, 'players': [
 {'active': [], 'asleep': False, 'bench': [], 'benchMax': 5, 'burned': False, 'confused': False, 'deckCount': 53, 'discard': [], 'hand': [{'id': 1}], 'handCount': 1, 'paralyzed': False, 'poisoned': False, 'prize': [None] * 6},
 {'active': [], 'asleep': False, 'bench': [], 'benchMax': 5, 'burned': False, 'confused': False, 'deckCount': 53, 'discard': [], 'hand': [{'id': 2}], 'handCount': 1, 'paralyzed': False, 'poisoned': False, 'prize': [None] * 6}], 'result': -1, 'retreated': False, 'stadium': [], 'stadiumPlayed': False, 'supporterPlayed': False, 'turn': 1, 'turnActionCount': 0, 'yourIndex': 0}, 'select': {'type': 0, 'context': 0, 'option': [{'type': 14}, {'type': 7, 'index': 0}], 'minCount': 1, 'maxCount': 1}, 'step': 1}
choice = agent(obs)
assert choice == agent(obs) and choice == [1]
assert main.make_student_agent(deck=[1] * 60, model_path=artifact / 'missing.json')(obs) == [1]
"""
        result = subprocess.run([sys.executable, "-I", "-c", script, str(extracted)], cwd=Path(temporary), capture_output=True, text=True, check=False)
    if result.returncode:
        raise StudentArtifactError(f"clean-room verification failed: {result.stderr}")
    model_record = next(record for record in actual if record["path"] == MODEL_MEMBER)
    return {"files": len(actual), "model_bytes": model_record["size"]}


def build_student_submission(
    model_path: str | Path,
    output_dir: str | Path,
    *,
    runtime_paths: tuple[str, ...] = RUNTIME_PATHS,
    generated_main: bytes | None = None,
    generated_files: dict[str, bytes] | None = None,
    extra_files: dict[str, bytes] | None = None,
) -> dict[str, Any]:
    destination = Path(output_dir)
    if destination.exists() and any(destination.iterdir()):
        raise StudentArtifactError("output directory must be new or empty")
    destination.mkdir(parents=True, exist_ok=True)
    try:
        files = _files(Path(model_path), runtime_paths=runtime_paths, generated_main=generated_main, generated_files=generated_files, extra_files=extra_files)
        for name, data in files:
            target = destination.joinpath(*_safe_path(name).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        archive_hash = _write_tar(destination, files)
        manifest = {"agent_identity": "student-v0-rule-v0-fallback", "artifact_schema_version": 1, "archive_sha256": archive_hash, "files": [_record_bytes(name, data) for name, data in files]}
        (destination / MANIFEST_NAME).write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        verify_student_submission(destination)
        return manifest
    except Exception:
        shutil.rmtree(destination, ignore_errors=True)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        print(json.dumps(build_student_submission(args.model, args.output_dir), sort_keys=True))
    except (OSError, ValueError, StudentArtifactError) as exc:
        print(f"Student submission build failed: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
