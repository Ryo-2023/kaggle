"""Build, archive, and verify the standalone Rule Agent v0 submission artifact."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_NAME = "manifest.json"
ARCHIVE_NAME = "submission.tar.gz"
ARTIFACT_SCHEMA_VERSION = 2
ARCHIVE_SCHEMA_VERSION = 1
AGENT_IDENTITY = "rule-agent-v0"
RUNTIME_PATHS = (
    "main.py",
    "deck.csv",
    "agents/__init__.py",
    "agents/rule_agent.py",
)
_SECRET_PATTERNS = (
    re.compile(rb"(?i)\bkaggle[_-]?(?:key|username)\s*[:=]"),
    re.compile(rb"(?i)\bauthorization\s*:"),
    re.compile(rb"(?i)\bcookie\s*:"),
    re.compile(rb"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b"),
    re.compile(rb"/home/[^/\s]+"),
)


class ArtifactValidationError(ValueError):
    """Raised when an artifact cannot be safely built or verified."""


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_relative_path(value: str) -> PurePosixPath:
    """Accept one normalized relative POSIX path and reject traversal."""
    if not isinstance(value, str) or not value:
        raise ArtifactValidationError("artifact path must be a non-empty string")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "." in path.parts or "\\" in value:
        raise ArtifactValidationError(f"unsafe artifact path: {value!r}")
    if path.as_posix() != value:
        raise ArtifactValidationError(f"artifact path must be normalized POSIX: {value!r}")
    return path


def _safe_output_dir(value: str | Path) -> Path:
    path = Path(value)
    if ".." in path.parts:
        raise ArtifactValidationError(f"output path traversal is not allowed: {path}")
    if path.is_symlink():
        raise ArtifactValidationError(f"output directory must not be a symlink: {path}")
    return path


def _source_revision() -> dict[str, Any]:
    def run(*args: str) -> str:
        result = subprocess.run(
            args,
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()

    try:
        commit = run("git", "rev-parse", "HEAD")
        dirty = bool(run("git", "status", "--porcelain=v1"))
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ArtifactValidationError("could not determine source revision") from exc
    return {"commit": commit, "dirty": dirty}


def _runtime_sources() -> list[tuple[str, Path]]:
    sources: list[tuple[str, Path]] = []
    for relative_name in RUNTIME_PATHS:
        relative_path = _safe_relative_path(relative_name)
        source = REPOSITORY_ROOT.joinpath(*relative_path.parts)
        if source.is_symlink() or not source.is_file():
            raise ArtifactValidationError(f"runtime source must be a regular file: {relative_name}")
        sources.append((relative_name, source))
    return sources


def _runtime_file_records(root: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for relative_name in RUNTIME_PATHS:
        file_path = root.joinpath(*_safe_relative_path(relative_name).parts)
        if file_path.is_symlink() or not file_path.is_file():
            raise ArtifactValidationError(f"runtime file must be a regular file: {relative_name}")
        records.append(
            {
                "path": relative_name,
                "sha256": _sha256_file(file_path),
                "size": file_path.stat().st_size,
            }
        )
    return records


def _content_hash(*, files: list[dict[str, Any]], deck_sha256: str) -> str:
    payload = {
        "agent_identity": AGENT_IDENTITY,
        "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
        "deck_identity": {"sha256": deck_sha256},
        "files": files,
    }
    return _sha256_bytes(_canonical_json(payload))


def _write_manifest(artifact_dir: Path, manifest: dict[str, Any]) -> None:
    (artifact_dir / MANIFEST_NAME).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _contains_secret(data: bytes) -> bool:
    return any(pattern.search(data) for pattern in _SECRET_PATTERNS)


def _canonical_tar_info(name: str, size: int) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name=name)
    info.size = size
    info.mode = 0o644
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mtime = 0
    return info


def build_submission_archive(artifact_dir: str | Path) -> dict[str, Any]:
    """Write a canonical tar.gz directly from runtime files; never repack an archive."""
    root = Path(artifact_dir)
    archive_path = root / ARCHIVE_NAME
    if archive_path.exists() or archive_path.is_symlink():
        raise ArtifactValidationError(f"archive path already exists: {archive_path}")
    records = _runtime_file_records(root)
    try:
        with archive_path.open("xb") as raw_handle:
            with gzip.GzipFile(filename="", mode="wb", fileobj=raw_handle, mtime=0) as gzip_handle:
                with tarfile.open(
                    fileobj=gzip_handle,
                    mode="w",
                    format=tarfile.USTAR_FORMAT,
                ) as archive:
                    for record in records:
                        source = root.joinpath(*_safe_relative_path(record["path"]).parts)
                        data = source.read_bytes()
                        archive.addfile(_canonical_tar_info(record["path"], len(data)), io.BytesIO(data))
    except OSError as exc:
        raise ArtifactValidationError(f"could not write archive: {exc}") from exc
    return {
        "format": "tar.gz",
        "path": ARCHIVE_NAME,
        "schema_version": ARCHIVE_SCHEMA_VERSION,
        "sha256": _sha256_file(archive_path),
    }


def _read_archive_members(archive_path: Path) -> list[tuple[tarfile.TarInfo, bytes]]:
    try:
        with tarfile.open(archive_path, mode="r:gz") as archive:
            members = archive.getmembers()
            names: set[str] = set()
            values: list[tuple[tarfile.TarInfo, bytes]] = []
            for member in members:
                _safe_relative_path(member.name)
                if member.name in names:
                    raise ArtifactValidationError(f"archive contains duplicate member: {member.name}")
                names.add(member.name)
                if not member.isreg():
                    raise ArtifactValidationError(
                        f"archive member must be a regular file, not a link or directory: {member.name}"
                    )
                handle = archive.extractfile(member)
                if handle is None:
                    raise ArtifactValidationError(f"archive member cannot be read: {member.name}")
                data = handle.read()
                if len(data) != member.size:
                    raise ArtifactValidationError(f"archive member size mismatch: {member.name}")
                values.append((member, data))
    except (OSError, EOFError, tarfile.TarError) as exc:
        raise ArtifactValidationError(f"could not read tar.gz archive: {exc}") from exc
    return values


def validate_submission_archive(archive_path: str | Path) -> list[dict[str, Any]]:
    """Validate the archive tree and return normalized runtime file records."""
    path = Path(archive_path)
    if path.is_symlink() or not path.is_file():
        raise ArtifactValidationError("submission archive must be a regular file")
    members = _read_archive_members(path)
    names = [member.name for member, _data in members]
    if names != list(RUNTIME_PATHS):
        raise ArtifactValidationError("archive members do not match the Rule v0 runtime package")
    records: list[dict[str, Any]] = []
    for member, data in members:
        if member.mode != 0o644 or member.uid != 0 or member.gid != 0 or member.mtime != 0:
            raise ArtifactValidationError(f"archive member metadata is not canonical: {member.name}")
        if member.uname or member.gname:
            raise ArtifactValidationError(f"archive member ownership names are not canonical: {member.name}")
        if _contains_secret(data):
            raise ArtifactValidationError(f"archive contains secret/private marker: {member.name}")
        records.append({"path": member.name, "sha256": _sha256_bytes(data), "size": len(data)})
    return records


def scan_artifact_secrets(artifact_dir: str | Path) -> list[str]:
    """Return artifact-relative files or archive members containing secret markers."""
    root = Path(artifact_dir)
    findings: list[str] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            findings.append(relative)
        elif path.is_file() and path.name != ARCHIVE_NAME and _contains_secret(path.read_bytes()):
            findings.append(relative)
    archive_path = root / ARCHIVE_NAME
    if archive_path.is_file() and not archive_path.is_symlink():
        try:
            for member, data in _read_archive_members(archive_path):
                if _contains_secret(data):
                    findings.append(f"{ARCHIVE_NAME}:{member.name}")
        except ArtifactValidationError:
            findings.append(ARCHIVE_NAME)
    return findings


def validate_artifact(artifact_dir: str | Path) -> dict[str, Any]:
    """Validate directory manifest, archive hash, runtime hashes, and safety rules."""
    root = Path(artifact_dir)
    if root.is_symlink() or not root.is_dir():
        raise ArtifactValidationError("artifact directory must be an existing non-symlink directory")
    manifest_path = root / MANIFEST_NAME
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ArtifactValidationError("artifact manifest is missing or not a regular file")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ArtifactValidationError("artifact manifest is not valid JSON") from exc
    if not isinstance(manifest, dict):
        raise ArtifactValidationError("artifact manifest must be an object")
    if manifest.get("artifact_schema_version") != ARTIFACT_SCHEMA_VERSION:
        raise ArtifactValidationError("unsupported artifact schema version")
    if manifest.get("agent_identity") != AGENT_IDENTITY:
        raise ArtifactValidationError("unexpected artifact agent identity")

    listed_files = manifest.get("files")
    if not isinstance(listed_files, list) or len(listed_files) != len(RUNTIME_PATHS):
        raise ArtifactValidationError("manifest runtime file list is invalid")
    listed_paths = []
    for record in listed_files:
        if not isinstance(record, dict) or not isinstance(record.get("path"), str):
            raise ArtifactValidationError("manifest file record is invalid")
        _safe_relative_path(record["path"])
        listed_paths.append(record["path"])
    if listed_paths != list(RUNTIME_PATHS):
        raise ArtifactValidationError("manifest runtime files do not match the Rule v0 package")

    actual_files = sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() or path.is_symlink()
    )
    expected_files = sorted([MANIFEST_NAME, ARCHIVE_NAME, *RUNTIME_PATHS])
    if actual_files != expected_files:
        raise ArtifactValidationError("artifact contains unlisted, missing, or symlinked files")

    expected_records = _runtime_file_records(root)
    if listed_files != expected_records:
        raise ArtifactValidationError("artifact file hashes or sizes do not match its manifest")
    deck_identity = manifest.get("deck_identity")
    if not isinstance(deck_identity, dict) or deck_identity.get("sha256") != expected_records[1]["sha256"]:
        raise ArtifactValidationError("artifact deck identity does not match deck.csv")
    if manifest.get("content_hash") != _content_hash(
        files=expected_records, deck_sha256=expected_records[1]["sha256"]
    ):
        raise ArtifactValidationError("artifact content hash does not match runtime files")

    archive = manifest.get("archive")
    if not isinstance(archive, dict) or archive.get("path") != ARCHIVE_NAME:
        raise ArtifactValidationError("artifact archive metadata is invalid")
    archive_path = root / ARCHIVE_NAME
    if archive.get("sha256") != _sha256_file(archive_path):
        raise ArtifactValidationError("tar.gz hash does not match manifest")
    archive_records = validate_submission_archive(archive_path)
    if archive_records != expected_records:
        raise ArtifactValidationError("tar.gz runtime files do not match directory artifact")
    findings = scan_artifact_secrets(root)
    if findings:
        raise ArtifactValidationError(f"artifact contains secret/private markers: {', '.join(findings)}")
    return manifest


def extract_submission_archive(archive_path: str | Path, destination: str | Path) -> list[dict[str, Any]]:
    """Safely materialize a validated archive into an empty destination directory."""
    target = Path(destination)
    if target.exists():
        if target.is_symlink() or not target.is_dir() or any(target.iterdir()):
            raise ArtifactValidationError("archive extraction destination must be an empty directory")
    else:
        target.mkdir(parents=True)
    records = validate_submission_archive(archive_path)
    for member, data in _read_archive_members(Path(archive_path)):
        output = target.joinpath(*_safe_relative_path(member.name).parts)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(data)
        output.chmod(0o644)
    return records


def verify_submission_clean_room(artifact_dir: str | Path) -> dict[str, Any]:
    """Extract only the archive and exercise its entrypoint in an isolated subprocess."""
    root = Path(artifact_dir)
    with tempfile.TemporaryDirectory(prefix="rule-v0-clean-room-") as temporary:
        temporary_root = Path(temporary)
        extracted = temporary_root / "submission"
        outside = temporary_root / "outside"
        outside.mkdir()
        extract_submission_archive(root / ARCHIVE_NAME, extracted)
        script = """
import json
import sys
from pathlib import Path

artifact = Path(sys.argv[1])
repository_root = Path(sys.argv[2])
assert Path.cwd() != repository_root
assert str(repository_root) not in sys.path
sys.path.insert(0, str(artifact))
import main

deck = main.agent({"select": None})
assert len(deck) == 60
mandatory = {"select": {"type": 0, "option": [{"type": 12}, {"type": 999}, {"type": 14}], "minCount": 2, "maxCount": 3}}
first = main.agent(mandatory)
assert first == main.agent(mandatory)
assert len(first) == len(set(first)) == 2
assert all(0 <= index < 3 for index in first)
optional = {"select": {"type": 4, "option": [{"type": 999}], "minCount": 0, "maxCount": 1}}
assert main.agent(optional) == []
for relative_name in ("main.py", "agents/__init__.py", "agents/rule_agent.py"):
    assert str(repository_root) not in (artifact / relative_name).read_text(encoding="utf-8")
print(json.dumps({"deck_size": len(deck), "mandatory": first}, sort_keys=True))
"""
        result = subprocess.run(
            [sys.executable, "-I", "-c", script, str(extracted), str(REPOSITORY_ROOT)],
            cwd=outside,
            capture_output=True,
            text=True,
            check=False,
        )
    if result.returncode != 0:
        raise ArtifactValidationError(f"clean-room verification failed: {result.stderr}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ArtifactValidationError("clean-room verification emitted invalid JSON") from exc


def verify_submission_artifact(artifact_dir: str | Path) -> dict[str, Any]:
    """Run archive and clean-room verification for an already-built artifact."""
    manifest = validate_artifact(artifact_dir)
    clean_room = verify_submission_clean_room(artifact_dir)
    return {"clean_room": clean_room, "content_hash": manifest["content_hash"], "tar_gz_sha256": manifest["archive"]["sha256"]}


def build_submission(output_dir: str | Path) -> dict[str, Any]:
    """Create a non-overwriting directory artifact and canonical final tar.gz."""
    destination = _safe_output_dir(output_dir)
    if destination.exists():
        if not destination.is_dir():
            raise ArtifactValidationError(f"output path is not a directory: {destination}")
        if any(destination.iterdir()):
            raise ArtifactValidationError(f"output directory is not empty: {destination}")
    else:
        destination.mkdir(parents=True)

    try:
        for relative_name, source in _runtime_sources():
            target = destination.joinpath(*_safe_relative_path(relative_name).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            target.chmod(0o644)
        records = _runtime_file_records(destination)
        deck_hash = records[1]["sha256"]
        archive = build_submission_archive(destination)
        manifest = {
            "agent_identity": AGENT_IDENTITY,
            "archive": archive,
            "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
            "build_metadata": {
                "builder": "scripts/build_submission.py",
                "builder_schema_version": ARTIFACT_SCHEMA_VERSION,
                "timestamp_utc": datetime.now(UTC).isoformat(),
            },
            "content_hash": _content_hash(files=records, deck_sha256=deck_hash),
            "deck_identity": {"path": "deck.csv", "sha256": deck_hash},
            "files": records,
            "source_revision": _source_revision(),
        }
        _write_manifest(destination, manifest)
        verify_submission_artifact(destination)
        return manifest
    except Exception:
        shutil.rmtree(destination, ignore_errors=True)
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--output-dir",
        type=Path,
        default=REPOSITORY_ROOT / "artifacts" / "submission" / "rule-v0",
        help="empty directory to create; defaults to artifacts/submission/rule-v0",
    )
    group.add_argument("--verify-dir", type=Path, help="verify an existing artifact directory")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = (
            verify_submission_artifact(args.verify_dir)
            if args.verify_dir is not None
            else build_submission(args.output_dir)
        )
    except ArtifactValidationError as exc:
        print(f"submission build failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
