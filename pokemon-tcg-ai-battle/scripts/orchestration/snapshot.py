"""Content-complete workspace snapshots for reproducible Git worktrees."""

from __future__ import annotations

import hashlib
import json
import shutil
import stat
import subprocess
import uuid
from pathlib import Path
from typing import Any, Iterable

from .events import atomic_write_json, utc_now
from .policy import normalize_relative, path_matches


class SnapshotError(RuntimeError):
    """Raised when a workspace cannot be snapshotted or reconstructed."""


_SENSITIVE_NAMES = {".env", "kaggle.json", "credentials", "credentials.json"}
_EXCLUDED_PREFIXES = (".orchestrator/", ".git/", "data/", "submissions/", "models/", "outputs/")
_EXCLUDED_SUFFIXES = (".pt", ".pth", ".ckpt", ".pkl", ".joblib", ".parquet", ".zip")
_MAX_UNTRACKED_BYTES = 5 * 1024 * 1024


def git(root: Path, *args: str, input_bytes: bytes | None = None) -> bytes:
    """Run a trusted Control Plane Git command without a shell."""

    result = subprocess.run(
        ["git", *args],
        cwd=root,
        input=input_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        raise SnapshotError(result.stderr.decode(errors="replace").strip())
    return result.stdout


def file_digest(path: Path) -> str:
    """Hash a path including its absence, type, target, or regular contents."""

    digest = hashlib.sha256()
    if path.is_symlink():
        digest.update(b"symlink\0")
        digest.update(path.readlink().as_posix().encode())
    elif path.is_file():
        digest.update(b"file\0")
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    elif path.is_dir():
        digest.update(b"dir\0")
    else:
        digest.update(b"missing\0")
    return digest.hexdigest()


def tree_digest(root: Path, patterns: Iterable[str]) -> str:
    """Hash all present files matching repository-relative policy patterns."""

    digest = hashlib.sha256()
    candidates: set[str] = set()
    for path in root.rglob("*"):
        if ".git" in path.parts or ".orchestrator" in path.parts or path.is_dir():
            continue
        relative = path.relative_to(root).as_posix()
        if path_matches(relative, patterns):
            candidates.add(relative)
    for pattern in patterns:
        normalized = normalize_relative(pattern)
        if not any(char in normalized for char in "*?["):
            candidates.add(normalized)
    for relative in sorted(candidates):
        digest.update(relative.encode())
        digest.update(b"\0")
        digest.update(file_digest(root / relative).encode())
        digest.update(b"\0")
    return digest.hexdigest()


def _path_record(root: Path, relative: str) -> dict[str, Any]:
    path = root / relative
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return {"path": relative, "kind": "missing", "sha256": file_digest(path)}
    if stat.S_ISREG(mode):
        return {
            "path": relative,
            "kind": "file",
            "executable": bool(mode & 0o100),
            "sha256": file_digest(path),
        }
    if stat.S_ISLNK(mode):
        return {
            "path": relative,
            "kind": "symlink",
            "link_target": path.readlink().as_posix(),
            "sha256": file_digest(path),
        }
    return {
        "path": relative,
        "kind": "unsupported",
        "file_type": stat.S_IFMT(mode),
        "sha256": file_digest(path),
    }


def path_manifest(root: Path, patterns: Iterable[str]) -> list[dict[str, Any]]:
    """Describe paths using Git-significant type, executable bit, and content."""

    normalized_patterns = tuple(normalize_relative(pattern) for pattern in patterns)
    candidates: set[str] = set()
    for path in root.rglob("*"):
        if ".git" in path.parts or ".orchestrator" in path.parts or path.is_dir():
            continue
        relative = path.relative_to(root).as_posix()
        if path_matches(relative, normalized_patterns):
            candidates.add(relative)
    for pattern in normalized_patterns:
        if not any(char in pattern for char in "*?["):
            candidates.add(pattern)
    return [_path_record(root, relative) for relative in sorted(candidates)]


def compose_workspace_digest(head: str, index_digest: str, content_digest: str) -> str:
    """Bind repository identity, index state, and visible workspace content."""

    value = json.dumps(
        {"head": head, "index_digest": index_digest, "content_digest": content_digest},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(value).hexdigest()


def compose_content_digest(
    head: str, tracked_patch_sha256: str, untracked_manifest: list[dict[str, Any]]
) -> str:
    """Bind visible tracked changes and the complete non-ignored untracked baseline."""

    payload = {
        "head": head,
        "tracked_patch_sha256": tracked_patch_sha256,
        "untracked": untracked_manifest,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def workspace_state(root: Path, allowed_paths: Iterable[str]) -> dict[str, Any]:
    """Return a canonical Git workspace state excluding ignored control artifacts."""

    allowed = tuple(allowed_paths)
    head = git(root, "rev-parse", "HEAD").decode().strip()
    index_patch = git(
        root,
        "diff",
        "--no-ext-diff",
        "--no-textconv",
        "--cached",
        "--binary",
        "--full-index",
        "HEAD",
    )
    tracked_patch = git(
        root,
        "diff",
        "--no-ext-diff",
        "--no-textconv",
        "--binary",
        "--full-index",
        "HEAD",
    )
    tracked = [
        item.decode("utf-8", errors="surrogateescape")
        for item in git(root, "ls-files", "-z").split(b"\0")
        if item
    ]
    untracked = [
        item.decode("utf-8", errors="surrogateescape")
        for item in git(root, "ls-files", "--others", "--exclude-standard", "-z").split(b"\0")
        if item
    ]
    untracked_manifest = [_path_record(root, relative) for relative in sorted(untracked)]
    tracked_patch_sha256 = hashlib.sha256(tracked_patch).hexdigest()
    content_digest = compose_content_digest(head, tracked_patch_sha256, untracked_manifest)
    index_digest = hashlib.sha256(index_patch).hexdigest()
    non_allowed_paths = sorted(
        relative
        for relative in {*tracked, *untracked}
        if not path_matches(relative, allowed)
    )
    non_allowed_manifest = [_path_record(root, relative) for relative in non_allowed_paths]
    non_allowed_digest = hashlib.sha256(
        json.dumps(non_allowed_manifest, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return {
        "head": head,
        "index_digest": index_digest,
        "tracked_patch_sha256": tracked_patch_sha256,
        "untracked_manifest": untracked_manifest,
        "content_digest": content_digest,
        "workspace_digest": compose_workspace_digest(head, index_digest, content_digest),
        "non_allowed_digest": non_allowed_digest,
    }


def review_workspace_state(root: Path) -> dict[str, Any]:
    """Capture the complete review worktree, including Git and symlink metadata."""

    head = git(root, "rev-parse", "HEAD").decode().strip()
    index = git(root, "diff", "--cached", "--binary", "--full-index", "HEAD")
    tracked = [
        item.decode("utf-8", errors="surrogateescape")
        for item in git(root, "ls-files", "-z").split(b"\0")
        if item
    ]
    untracked = [
        item.decode("utf-8", errors="surrogateescape")
        for item in git(root, "ls-files", "--others", "--exclude-standard", "-z").split(b"\0")
        if item
    ]
    tracked_manifest = [_path_record(root, relative) for relative in sorted(tracked)]
    untracked_manifest = [_path_record(root, relative) for relative in sorted(untracked)]
    physical_manifest = [
        _path_record(root, path.relative_to(root).as_posix())
        for path in sorted(root.rglob("*"))
        if ".git" not in path.relative_to(root).parts and not path.is_dir()
    ]
    symlinks = [
        item
        for item in [*tracked_manifest, *untracked_manifest]
        if item.get("kind") == "symlink"
    ]
    status = git(root, "status", "--porcelain=v1", "-z")
    tracked_contents_digest = hashlib.sha256(
        json.dumps(tracked_manifest, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    untracked_digest = hashlib.sha256(
        json.dumps(untracked_manifest, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    symlink_digest = hashlib.sha256(
        json.dumps(symlinks, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    physical_tree_digest = hashlib.sha256(
        json.dumps(physical_manifest, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    index_digest = hashlib.sha256(index).hexdigest()
    status_digest = hashlib.sha256(status).hexdigest()
    fields = {
        "head": head,
        "index_digest": index_digest,
        "tracked_contents_digest": tracked_contents_digest,
        "untracked_files_digest": untracked_digest,
        "symlink_metadata_digest": symlink_digest,
        "git_status_porcelain_digest": status_digest,
        "physical_tree_digest": physical_tree_digest,
    }
    return {
        **fields,
        "worktree_digest": hashlib.sha256(
            json.dumps(fields, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
    }


def _exclude_reason(relative: str, path: Path) -> str | None:
    name = path.name.lower()
    lowered = relative.lower()
    if name in _SENSITIVE_NAMES or "credential" in name or "secret" in name:
        return "sensitive_name"
    if lowered.startswith(_EXCLUDED_PREFIXES):
        return "excluded_area"
    if lowered.endswith(_EXCLUDED_SUFFIXES):
        return "dataset_model_or_archive"
    if path.is_symlink():
        return "symlink_not_snapshotted"
    if path.stat().st_size > _MAX_UNTRACKED_BYTES:
        return "size_limit"
    try:
        sample = path.read_bytes()[:65536]
    except OSError:
        return "unreadable"
    secret_markers = (
        b"-----BEGIN PRIVATE KEY-----",
        b"KAGGLE_KEY=",
        b"KAGGLE_USERNAME=",
        b"AWS_SECRET_ACCESS_KEY=",
    )
    if any(marker in sample for marker in secret_markers):
        return "secret_scan"
    return None


class WorkspaceSnapshot:
    """Create and materialize a dirty-workspace snapshot around a Git commit."""

    def __init__(self, root: Path, store_root: Path):
        self.root = root.resolve()
        self.store_root = store_root

    def create(self, source_patterns: Iterable[str] = ()) -> tuple[str, dict[str, Any]]:
        """Persist HEAD, binary tracked diff, and eligible untracked contents."""

        source_patterns = tuple(source_patterns)
        head = git(self.root, "rev-parse", "HEAD").decode().strip()
        source_workspace = workspace_state(self.root, source_patterns)
        tracked_patch = git(
            self.root,
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--binary",
            "--full-index",
            "HEAD",
        )
        untracked_raw = git(
            self.root, "ls-files", "--others", "--exclude-standard", "-z"
        ).split(b"\0")
        records: list[dict[str, Any]] = []
        included: list[tuple[str, Path]] = []
        for raw in untracked_raw:
            if not raw:
                continue
            relative = raw.decode("utf-8", errors="surrogateescape")
            path = self.root / relative
            reason = _exclude_reason(relative, path)
            record: dict[str, Any] = {
                "path": relative,
                "size": path.lstat().st_size,
                "sha256": file_digest(path),
                "included": reason is None,
            }
            if reason:
                record["reason"] = reason
            else:
                included.append((relative, path))
            records.append(record)
        identity = hashlib.sha256()
        identity.update(head.encode())
        identity.update(tracked_patch)
        identity.update(json.dumps(records, sort_keys=True).encode())
        snapshot_id = f"snap-{identity.hexdigest()[:16]}-{uuid.uuid4().hex[:8]}"
        directory = self.store_root / snapshot_id
        (directory / "untracked").mkdir(parents=True, exist_ok=False)
        (directory / "tracked.patch").write_bytes(tracked_patch)
        for relative, source in included:
            target = directory / "untracked" / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target, follow_symlinks=False)
        atomic_write_json(directory / "untracked_manifest.json", {"files": records})
        manifest = {
            "snapshot_id": snapshot_id,
            "head_commit": head,
            "created_at": utc_now(),
            "tracked_patch_sha256": hashlib.sha256(tracked_patch).hexdigest(),
            "source_digest": tree_digest(self.root, source_patterns),
            "source_patterns": list(source_patterns),
            "source_workspace": source_workspace,
            "excluded_files": [record for record in records if not record["included"]],
            "secret_scan": {
                "mode": "name and first-64KiB marker scan",
                "limitations": "not a complete credential classifier",
            },
        }
        atomic_write_json(directory / "manifest.json", manifest)
        return snapshot_id, manifest

    def load_manifest(self, snapshot_id: str) -> dict[str, Any]:
        """Load a snapshot manifest."""

        with (self.store_root / snapshot_id / "manifest.json").open(encoding="utf-8") as handle:
            value = json.load(handle)
        if not isinstance(value, dict):
            raise SnapshotError("snapshot manifest is not an object")
        return value

    def materialize(self, snapshot_id: str, destination: Path) -> None:
        """Create a detached worktree and replay the exact stored workspace content."""

        directory = self.store_root / snapshot_id
        manifest = self.load_manifest(snapshot_id)
        destination.parent.mkdir(parents=True, exist_ok=True)
        git(
            self.root,
            "-c",
            "core.hooksPath=/dev/null",
            "worktree",
            "add",
            "--detach",
            "--force",
            str(destination),
            manifest["head_commit"],
        )
        patch = (directory / "tracked.patch").read_bytes()
        if patch:
            git(destination, "apply", "--binary", "--whitespace=nowarn", "-", input_bytes=patch)
        untracked_root = directory / "untracked"
        if untracked_root.exists():
            for source in untracked_root.rglob("*"):
                if source.is_dir():
                    continue
                target = destination / source.relative_to(untracked_root)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target, follow_symlinks=False)
