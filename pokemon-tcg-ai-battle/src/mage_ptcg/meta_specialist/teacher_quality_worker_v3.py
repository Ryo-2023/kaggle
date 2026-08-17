"""Sealed source snapshots and isolated fresh workers for teacher-quality v3.

Every attempt imports only the verified snapshot closure through an inherited
directory descriptor; no policy or engine import may fall back to the live
worktree.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
import hashlib
import importlib.util
import json
import math
import os
import errno
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import traceback
from typing import TYPE_CHECKING, Iterator, Mapping

if TYPE_CHECKING:
    from mage_ptcg.meta_specialist.teacher_quality_evidence_v3 import CampaignPlanV3


_MANIFEST_NAME = "source-manifest.json"
_SNAPSHOT_SCHEMA = "meta-specialist-teacher-quality-source-snapshot-v3"
_WORKER_REQUEST_SCHEMA = "meta-specialist-teacher-quality-worker-request-v3"
_WORKER_RESPONSE_SCHEMA = "meta-specialist-teacher-quality-worker-response-v3"
_HEX = frozenset("0123456789abcdef")
_OUTCOMES = frozenset({"win", "loss", "draw"})
_IGNORED_SOURCE_DIRECTORY_NAMES = frozenset({"__pycache__"})
_IGNORED_SOURCE_FILE_SUFFIXES = (".pyc", ".pyo")
_FAULT_KEYS = frozenset({
    "kind", "exception_class", "message", "source_exception", "exit_code",
    "traceback_sha256",
})
_GAME_REQUEST_KEYS = frozenset({
    "engine_path", "opponent_policy_path", "subject_deck_path", "opponent_deck_path",
    "environment_seed", "max_steps",
})
_GAME_RESPONSE_KEYS = frozenset({
    "engine_status", "winner", "subject_seat", "subject_outcome",
})

WorkerResponseV3 = dict[str, object]


@dataclass(frozen=True, slots=True)
class SourceSnapshotV3:
    """A published source tree with an owned root-directory capability.

    ``root`` and ``manifest_path`` are reporting metadata only.  Consumers
    must use ``root_fd`` (for example with ``pass_fds``) or the safe read
    helper below.  The owner must call :meth:`close` when the snapshot is no
    longer needed; repeated calls are harmless.
    """

    root: Path
    manifest_path: Path
    file_sha256: str
    tree_sha256: str
    root_fd: int = field(repr=False, compare=False)
    _closed: list[bool] = field(default_factory=lambda: [False], repr=False, compare=False)

    def close(self) -> None:
        if self._closed[0]:
            return
        os.close(self.root_fd)
        self._closed[0] = True


@dataclass(frozen=True, slots=True)
class _SnapshotEntryV3:
    source: Path | None
    relative_path: str
    expected_sha256: str | None
    name: str
    source_root_fd: int | None = field(default=None, repr=False, compare=False)
    source_parts: tuple[str, ...] = field(default=(), repr=False, compare=False)
    source_parent_identities: tuple[tuple[int, int, int, int, int, int], ...] = field(
        default=(), repr=False, compare=False,
    )
    source_identity: tuple[int, int, int, int, int, int] | None = field(
        default=None, repr=False, compare=False,
    )


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _require_sha(value: object, *, field: str) -> str:
    if type(value) is not str or len(value) != 64 or any(character not in _HEX for character in value):
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")
    return value


def _relative_path(value: object, *, field: str) -> str:
    if type(value) is not str or not value or "\\" in value:
        raise ValueError(f"{field} must be a contained relative snapshot path")
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"{field} must be a contained relative snapshot path")
    canonical = path.as_posix()
    if canonical != value:
        raise ValueError(f"{field} must be a contained relative snapshot path")
    return canonical


def _file_identity(value: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        value.st_dev, value.st_ino, value.st_mode, value.st_size,
        value.st_mtime_ns, value.st_ctime_ns,
    )


def _require_no_follow() -> int:
    if not hasattr(os, "O_NOFOLLOW"):
        raise ValueError("teacher-quality source snapshots require O_NOFOLLOW")
    return os.O_NOFOLLOW


def _open_verified_directory_at(
    parent_descriptor: int, name: str, *, expected: os.stat_result, label: str,
) -> int:
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY | os.O_DIRECTORY | _require_no_follow(),
            dir_fd=parent_descriptor,
        )
    except OSError as exc:
        raise ValueError(f"{label} cannot be opened without following a symlink") from exc
    try:
        actual = os.fstat(descriptor)
        if _file_identity(actual) != _file_identity(expected):
            raise ValueError(f"{label} changed while being snapshotted")
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _open_directory_path_no_follow(path: Path, *, label: str) -> int:
    """Open an absolute directory one verified no-follow component at a time."""
    absolute = Path(os.path.abspath(os.fspath(path)))
    parts = absolute.parts
    if not absolute.is_absolute() or not parts:
        raise ValueError(f"{label} must be an absolute source directory")
    try:
        descriptor = os.open(absolute.anchor, os.O_RDONLY | os.O_DIRECTORY | _require_no_follow())
    except OSError as exc:
        raise ValueError(f"{label} cannot be opened") from exc
    try:
        for component in parts[1:]:
            try:
                child_stat = os.stat(component, dir_fd=descriptor, follow_symlinks=False)
            except OSError as exc:
                raise ValueError(f"{label} cannot be inspected") from exc
            if stat.S_ISLNK(child_stat.st_mode) or not stat.S_ISDIR(child_stat.st_mode):
                raise ValueError(f"{label} contains a symlink or non-directory component")
            child_descriptor = _open_verified_directory_at(
                descriptor, component, expected=child_stat, label=label,
            )
            os.close(descriptor)
            descriptor = child_descriptor
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _path_component(value: str, *, field: str) -> str:
    if not value or "/" in value or "\\" in value or value in {".", ".."}:
        raise ValueError(f"{field} is not safe for a snapshot path")
    return value


def _identity_component(value: str, *, field: str) -> str:
    _path_component(value, field=field)
    return _sha(value.encode("utf-8"))[:16]


def _directory_entries_from_fd(
    root_descriptor: int, directory_descriptor: int, prefix: tuple[str, ...],
    parent_identities: tuple[tuple[int, int, int, int, int, int], ...], *, name: str,
) -> Iterator[_SnapshotEntryV3]:
    scan_descriptor: int | None = None
    try:
        scan_descriptor = os.dup(directory_descriptor)
        with os.scandir(scan_descriptor) as iterator:
            children = sorted(
                ((item.name, item.stat(follow_symlinks=False)) for item in iterator),
                key=lambda item: item[0],
            )
    except OSError as exc:
        raise ValueError(f"{name} cannot be enumerated while being snapshotted") from exc
    finally:
        if scan_descriptor is not None:
            os.close(scan_descriptor)
    for child_name, child_stat in children:
        # Python bytecode is a volatile execution cache, not source authority.
        # Keep this intentionally narrow: unknown generated files still enter
        # the closure and therefore cannot be silently ignored.
        if stat.S_ISDIR(child_stat.st_mode) and child_name in _IGNORED_SOURCE_DIRECTORY_NAMES:
            continue
        if stat.S_ISREG(child_stat.st_mode) and child_name.endswith(_IGNORED_SOURCE_FILE_SUFFIXES):
            continue
        if stat.S_ISLNK(child_stat.st_mode):
            raise ValueError(f"{name} contains a symlink and cannot enter the source snapshot")
        if stat.S_ISDIR(child_stat.st_mode):
            child_descriptor = _open_verified_directory_at(
                directory_descriptor, child_name, expected=child_stat, label=name,
            )
            try:
                yield from _directory_entries_from_fd(
                    root_descriptor, child_descriptor, (*prefix, child_name),
                    (*parent_identities, _file_identity(child_stat)), name=name,
                )
            finally:
                os.close(child_descriptor)
        elif stat.S_ISREG(child_stat.st_mode):
            yield _SnapshotEntryV3(
                None, "", None, name, source_root_fd=root_descriptor,
                source_parts=(*prefix, child_name), source_parent_identities=parent_identities,
                source_identity=_file_identity(child_stat),
            )
        else:
            raise ValueError(f"{name} contains a non-regular file")


def _tree_entries(source_root: Path, relative_root: str, *, name: str) -> list[_SnapshotEntryV3]:
    relative_root = _relative_path(relative_root, field="snapshot tree root")
    entries: list[_SnapshotEntryV3] = []
    root_descriptor = _open_directory_path_no_follow(Path(source_root), label=name)
    try:
        for entry in _directory_entries_from_fd(root_descriptor, root_descriptor, (), (), name=name):
            suffix = PurePosixPath(*entry.source_parts).as_posix()
            entries.append(_SnapshotEntryV3(
                None, f"{relative_root}/{suffix}", None, name,
                source_root_fd=root_descriptor, source_parts=entry.source_parts,
                source_parent_identities=entry.source_parent_identities,
                source_identity=entry.source_identity,
            ))
        return entries
    except BaseException:
        os.close(root_descriptor)
        raise


def _package_root(entry_point: Path, *, name: str) -> Path:
    """Return the outermost import package that owns ``entry_point``."""
    directory = entry_point.parent
    package_root: Path | None = None
    while True:
        initializer = directory / "__init__.py"
        try:
            initializer_stat = os.lstat(initializer)
        except FileNotFoundError:
            break
        except OSError as exc:
            raise ValueError(f"{name} package closure cannot be inspected") from exc
        if stat.S_ISLNK(initializer_stat.st_mode) or not stat.S_ISREG(initializer_stat.st_mode):
            raise ValueError(f"{name} package closure is not a regular source tree")
        package_root = directory
        directory = directory.parent
    return package_root or entry_point.parent


def _engine_entries(plan: "CampaignPlanV3") -> list[_SnapshotEntryV3]:
    entry_point = Path(plan.engine_entry_point)
    package_root = _package_root(entry_point, name="engine module")
    identity = _identity_component(plan.engine_sha256, field="engine SHA-256")
    if package_root == entry_point.parent:
        return [_SnapshotEntryV3(
            entry_point, f"inputs/engine/{identity}/{_path_component(entry_point.name, field='engine entry point name')}",
            plan.engine_sha256, "engine module",
        )]
    relative_entry = entry_point.relative_to(package_root).as_posix()
    entries = _tree_entries(package_root, f"inputs/engine/{identity}/package", name="engine package")
    expected_entry = f"inputs/engine/{identity}/package/{relative_entry}"
    return [
        _SnapshotEntryV3(
            item.source, item.relative_path,
            plan.engine_sha256 if item.relative_path == expected_entry else None, item.name,
            source_root_fd=item.source_root_fd, source_parts=item.source_parts,
            source_parent_identities=item.source_parent_identities,
            source_identity=item.source_identity,
        )
        for item in entries
    ]


def _resolve_required_snapshot_entries(plan: "CampaignPlanV3") -> tuple[_SnapshotEntryV3, ...]:
    """Resolve only frozen campaign inputs plus the collector's executable source."""
    root = _repo_root()
    source_tree = root / "src" / "mage_ptcg"
    entries = _tree_entries(source_tree, "src/mage_ptcg", name="mage_ptcg source tree")
    # Generic frozen panel policies import the simulator package directly.
    # Seal its Python and platform-native bytes through the same descriptor
    # traversal as every other source tree; no child may import live ``cg``.
    entries.extend(_tree_entries(root / "cg", "cg", name="cg source tree"))
    # Rule v0's public entry point imports this package at module import time.
    # It is part of the sealed Rule v0 closure, never a fallback to the host tree.
    entries.extend(_tree_entries(root / "agents", "agents", name="Rule v0 agent source tree"))
    # Several frozen local-eval opponents import ``agents.generic_agent``.
    # The snapshot-root ``agents`` package is deliberately the one shared by
    # Rule v0, so add the vendor implementation beneath that same sealed
    # package rather than adding another sys.path entry with a colliding name.
    vendor_agents = root / "vendor_opponent_pilots" / "agents"
    entries.extend(_tree_entries(
        vendor_agents, "vendor_opponent_pilots/agents", name="vendor opponent agent source tree",
    ))
    entries.append(_SnapshotEntryV3(
        vendor_agents / "generic_agent.py", "agents/generic_agent.py", None,
        "vendor generic agent merged into sealed agents package",
    ))
    for source, relative, name in (
        (root / "scripts" / "test_sim.py", "scripts/test_sim.py", "CABT test runner"),
        (root / "main.py", "main.py", "Rule v0 baseline source"),
    ):
        entries.append(_SnapshotEntryV3(source, relative, None, name))

    entries.extend(_engine_entries(plan))
    entries.extend((
        _SnapshotEntryV3(Path(plan.schedule_path), "inputs/opponent-schedule.json", plan.schedule_sha256, "opponent schedule"),
        _SnapshotEntryV3(Path(plan.pool_root) / "pool_manifest.json", "inputs/pool-manifest.json", plan.pool_manifest_sha256, "opponent pool manifest"),
        _SnapshotEntryV3(Path(plan.baseline_entry_point), "inputs/rule-v0/main.py", plan.baseline_policy_sha256, "Rule v0 baseline entry point"),
    ))

    teachers: dict[str, object] = {}
    for game in plan.logical_games:
        if game.arm == "teacher":
            teachers.setdefault(game.lane, game.teacher_instance)
    for lane in plan.lanes:
        teacher = teachers.get(lane.lane)
        if teacher is None:
            raise ValueError(f"frozen teacher identity is missing for lane {lane.lane}")
        lane_component = _identity_component(lane.lane, field="lane")
        teacher_component = _identity_component(lane.teacher_id, field="teacher id")
        entries.extend((
            _SnapshotEntryV3(Path(lane.subject_deck_path), f"inputs/lanes/{lane_component}/subject-deck.csv", lane.expected_subject_deck_sha256, f"{lane.lane} subject deck"),
            _SnapshotEntryV3(Path(teacher.policy_path), f"inputs/teachers/{teacher_component}/policy.py", teacher.policy_hash, f"{lane.lane} teacher policy"),
            _SnapshotEntryV3(Path(teacher.deck_csv_path), f"inputs/teachers/{teacher_component}/deck.csv", lane.expected_subject_deck_sha256, f"{lane.lane} teacher deck"),
        ))
    for opponent in plan.panel:
        instance = opponent._instance
        component = _identity_component(opponent.opponent_id, field="panel opponent id")
        entries.extend((
            _SnapshotEntryV3(Path(instance.policy_path), f"inputs/panel/{component}/policy.py", opponent.policy_sha256, f"{opponent.opponent_id} opponent policy"),
            _SnapshotEntryV3(Path(instance.deck_csv_path), f"inputs/panel/{component}/deck.csv", opponent.deck_sha256, f"{opponent.opponent_id} opponent deck"),
        ))

    resolved: dict[str, _SnapshotEntryV3] = {}
    for entry in entries:
        relative_path = _relative_path(entry.relative_path, field="snapshot entry path")
        existing = resolved.get(relative_path)
        if existing is not None:
            if existing.source != entry.source or existing.expected_sha256 != entry.expected_sha256:
                raise ValueError(f"snapshot entry path collision at {relative_path}")
            continue
        resolved[relative_path] = entry
    return tuple(resolved[key] for key in sorted(resolved))


def _private_directory(path: Path, *, name: str) -> None:
    path.mkdir(parents=True, mode=0o700, exist_ok=True)
    try:
        path_stat = os.lstat(path)
    except OSError as exc:
        raise ValueError(f"{name} cannot be inspected") from exc
    if stat.S_ISLNK(path_stat.st_mode) or not stat.S_ISDIR(path_stat.st_mode):
        raise ValueError(f"{name} must be a non-symlink directory")
    os.chmod(path, 0o700)


def _private_snapshot_parent(staging: Path, destination: Path) -> None:
    """Create every destination parent below ``staging`` with mode 0700."""
    try:
        relative = destination.relative_to(staging)
    except ValueError as exc:
        raise ValueError("snapshot entry path escapes the staging directory") from exc
    current = staging
    for component in relative.parts:
        current = current / component
        _private_directory(current, name="source snapshot destination directory")


@contextmanager
def _private_staging_directory(staging_root: Path) -> Iterator[Path]:
    _private_directory(staging_root, name="source snapshot staging root")
    staging = Path(tempfile.mkdtemp(prefix=".source-snapshot-", dir=staging_root))
    os.chmod(staging, 0o700)
    try:
        yield staging
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _write_all(descriptor: int, raw: bytes) -> None:
    view = memoryview(raw)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError("short write while building source snapshot")
        view = view[written:]


def _open_source_regular_file(entry: _SnapshotEntryV3) -> int:
    """Open an input file without returning to a path-based tree traversal."""
    if entry.source_root_fd is not None:
        descriptor = os.dup(entry.source_root_fd)
        try:
            if len(entry.source_parts) < 1 or len(entry.source_parent_identities) != len(entry.source_parts) - 1:
                raise ValueError(f"{entry.name} source entry identity is invalid")
            for component, expected_identity in zip(entry.source_parts[:-1], entry.source_parent_identities):
                try:
                    child_stat = os.stat(component, dir_fd=descriptor, follow_symlinks=False)
                except OSError as exc:
                    raise ValueError(f"{entry.name} changed while being snapshotted") from exc
                if stat.S_ISLNK(child_stat.st_mode) or not stat.S_ISDIR(child_stat.st_mode):
                    raise ValueError(f"{entry.name} contains a symlink or non-directory component")
                if _file_identity(child_stat) != expected_identity:
                    raise ValueError(f"{entry.name} changed while being snapshotted")
                child_descriptor = _open_verified_directory_at(
                    descriptor, component, expected=child_stat, label=entry.name,
                )
                os.close(descriptor)
                descriptor = child_descriptor
            try:
                source_descriptor = os.open(
                    entry.source_parts[-1], os.O_RDONLY | _require_no_follow(), dir_fd=descriptor,
                )
            except OSError as exc:
                raise ValueError(f"{entry.name} cannot be opened without following a symlink") from exc
            return source_descriptor
        finally:
            os.close(descriptor)
    if entry.source is None:
        raise ValueError(f"{entry.name} source entry is missing")
    parent_descriptor = _open_directory_path_no_follow(entry.source.parent, label=entry.name)
    try:
        try:
            return os.open(entry.source.name, os.O_RDONLY | _require_no_follow(), dir_fd=parent_descriptor)
        except OSError as exc:
            raise ValueError(f"{entry.name} cannot be opened without following a symlink") from exc
    finally:
        os.close(parent_descriptor)


def _copy_verified_regular_file(entry: _SnapshotEntryV3, staging: Path) -> dict[str, object]:
    relative_path = _relative_path(entry.relative_path, field="snapshot entry path")
    target = staging.joinpath(*PurePosixPath(relative_path).parts)
    try:
        target.relative_to(staging)
    except ValueError as exc:
        raise ValueError("snapshot entry path escapes the staging directory") from exc
    _private_snapshot_parent(staging, target.parent)
    source_descriptor = _open_source_regular_file(entry)
    try:
        before = os.fstat(source_descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError(f"{entry.name} is not a regular file")
        if entry.source_identity is not None and _file_identity(before) != entry.source_identity:
            raise ValueError(f"{entry.name} changed while being snapshotted")
        try:
            destination_descriptor = os.open(
                target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _require_no_follow(), 0o600,
            )
        except OSError as exc:
            raise ValueError(f"snapshot entry {relative_path} cannot be created") from exc
        digest = hashlib.sha256()
        size = 0
        try:
            while chunk := os.read(source_descriptor, 1024 * 1024):
                digest.update(chunk)
                size += len(chunk)
                _write_all(destination_descriptor, chunk)
            os.fsync(destination_descriptor)
        finally:
            os.close(destination_descriptor)
        after = os.fstat(source_descriptor)
        if _file_identity(before) != _file_identity(after):
            raise ValueError(f"{entry.name} changed while being snapshotted")
    finally:
        os.close(source_descriptor)
    actual_sha256 = digest.hexdigest()
    if entry.expected_sha256 is not None and actual_sha256 != entry.expected_sha256:
        raise ValueError(f"{entry.name} SHA-256 does not match the frozen campaign")
    return {"path": relative_path, "sha256": actual_sha256, "size": size}


def _tree_sha(rows: list[dict[str, object]]) -> str:
    return _sha(_canonical(rows))


def _manifest(plan: "CampaignPlanV3", rows: list[dict[str, object]]) -> bytes:
    return _canonical({
        "schema": _SNAPSHOT_SCHEMA,
        "campaign_id": plan.campaign_id,
        "entries": rows,
    })


def _atomic_write(path: Path, raw: bytes) -> None:
    _private_directory(path.parent, name="source snapshot destination directory")
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(descriptor, 0o600)
        _write_all(descriptor, raw)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    try:
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _opened_snapshot(
    root: Path, manifest_sha256: str, tree_sha256: str,
) -> SourceSnapshotV3:
    try:
        descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | _require_no_follow())
    except OSError as exc:
        raise ValueError("published snapshot root cannot be opened without following a symlink") from exc
    try:
        if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise ValueError("published snapshot root is not a directory")
        return SourceSnapshotV3(root, root / _MANIFEST_NAME, manifest_sha256, tree_sha256, descriptor)
    except BaseException:
        os.close(descriptor)
        raise


def _publish_snapshot(staging: Path, staging_root: Path, manifest_sha256: str, tree_sha256: str) -> SourceSnapshotV3:
    destination = staging_root / f"source-snapshot-{tree_sha256}"
    try:
        os.rename(staging, destination)
    except OSError as exc:
        if exc.errno not in {errno.EEXIST, errno.ENOTEMPTY}:
            raise
        existing = _opened_snapshot(destination, manifest_sha256, tree_sha256)
        try:
            verified = verify_source_snapshot_v3(existing)
            if verified.file_sha256 != manifest_sha256:
                raise ValueError("existing source snapshot does not match the manifest being published")
        except BaseException:
            existing.close()
            raise
        shutil.rmtree(staging, ignore_errors=True)
        return existing
    return _opened_snapshot(destination, manifest_sha256, tree_sha256)


def seal_teacher_quality_source_snapshot_v3(*, plan: "CampaignPlanV3", staging_root: Path) -> SourceSnapshotV3:
    """Copy the campaign's immutable source closure through verified descriptors."""
    entries = _resolve_required_snapshot_entries(plan)
    try:
        root = Path(staging_root)
        with _private_staging_directory(root) as staging:
            rows = [_copy_verified_regular_file(entry, staging) for entry in entries]
            rows.sort(key=lambda row: str(row["path"]))
            manifest = _manifest(plan, rows)
            manifest_path = staging / _MANIFEST_NAME
            _atomic_write(manifest_path, manifest)
            tree_sha256 = _tree_sha(rows)
            result = _publish_snapshot(staging, root, _sha(manifest), tree_sha256)
        return result
    finally:
        for descriptor in {entry.source_root_fd for entry in entries if entry.source_root_fd is not None}:
            os.close(descriptor)


def _read_stable_regular_file_at(
    directory_descriptor: int, name: str, *, expected: os.stat_result | None = None,
) -> bytes:
    try:
        descriptor = os.open(name, os.O_RDONLY | _require_no_follow(), dir_fd=directory_descriptor)
    except OSError as exc:
        raise ValueError(f"snapshot entry {name} cannot be opened without following a symlink") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError(f"snapshot entry {name} is not a regular file")
        if expected is not None and _file_identity(before) != _file_identity(expected):
            raise ValueError(f"snapshot entry {name} changed while being verified")
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, 1024 * 1024):
            chunks.append(chunk)
        after = os.fstat(descriptor)
        if _file_identity(before) != _file_identity(after):
            raise ValueError(f"snapshot entry {name} changed while being verified")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _strict_manifest(snapshot: SourceSnapshotV3, raw: bytes) -> tuple[dict[str, object], list[dict[str, object]]]:
    if _sha(raw) != _require_sha(snapshot.file_sha256, field="snapshot manifest SHA-256"):
        raise ValueError("snapshot manifest SHA-256 does not match")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        if isinstance(exc, ValueError) and str(exc) == "duplicate snapshot manifest key":
            raise
        raise ValueError("snapshot manifest is not strict JSON") from exc
    if type(value) is not dict or frozenset(value) != {"schema", "campaign_id", "entries"}:
        raise ValueError("snapshot manifest has an invalid closed key set")
    if value["schema"] != _SNAPSHOT_SCHEMA or type(value["campaign_id"]) is not str or not value["campaign_id"]:
        raise ValueError("snapshot manifest schema or campaign identity is invalid")
    rows = value["entries"]
    if type(rows) is not list or not rows:
        raise ValueError("snapshot manifest entries are invalid")
    if raw != _canonical(value):
        raise ValueError("snapshot manifest is not canonical JSON")
    previous = ""
    seen: set[str] = set()
    for row in rows:
        if type(row) is not dict or frozenset(row) != {"path", "sha256", "size"}:
            raise ValueError("snapshot entry has an invalid closed key set")
        path = _relative_path(row["path"], field="snapshot entry path")
        _require_sha(row["sha256"], field="snapshot entry SHA-256")
        if type(row["size"]) is not int or row["size"] < 0:
            raise ValueError("snapshot entry size is invalid")
        if path <= previous or path in seen:
            raise ValueError("snapshot manifest entries are not uniquely sorted")
        previous = path
        seen.add(path)
    return value, rows


def _reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate snapshot manifest key")
        result[key] = value
    return result


def _snapshot_root_descriptor(snapshot: SourceSnapshotV3) -> int:
    if type(snapshot.root_fd) is not int or snapshot.root_fd < 0 or snapshot._closed[0]:
        raise ValueError("snapshot root descriptor is closed or invalid")
    try:
        root_stat = os.fstat(snapshot.root_fd)
    except OSError as exc:
        raise ValueError("snapshot root descriptor is closed or invalid") from exc
    if not stat.S_ISDIR(root_stat.st_mode):
        raise ValueError("snapshot root descriptor is not a directory")
    return snapshot.root_fd


def _read_snapshot_manifest(snapshot: SourceSnapshotV3) -> list[dict[str, object]]:
    root_descriptor = _snapshot_root_descriptor(snapshot)
    try:
        manifest_stat = os.stat(_MANIFEST_NAME, dir_fd=root_descriptor, follow_symlinks=False)
    except OSError as exc:
        raise ValueError("snapshot entry source-manifest.json cannot be inspected") from exc
    if stat.S_ISLNK(manifest_stat.st_mode):
        raise ValueError("snapshot entry source-manifest.json is a symlink")
    manifest_raw = _read_stable_regular_file_at(
        root_descriptor, _MANIFEST_NAME, expected=manifest_stat,
    )
    _manifest_value, rows = _strict_manifest(snapshot, manifest_raw)
    return rows


def _open_snapshot_directory(
    parent_descriptor: int, name: str, *, expected: os.stat_result,
) -> int:
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY | os.O_DIRECTORY | _require_no_follow(),
            dir_fd=parent_descriptor,
        )
    except OSError as exc:
        raise ValueError(f"snapshot entry {name} cannot be opened without following a symlink") from exc
    try:
        actual = os.fstat(descriptor)
        if _file_identity(actual) != _file_identity(expected):
            raise ValueError(f"snapshot entry {name} changed while being verified")
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _walk_snapshot(
    directory_descriptor: int, prefix: str, *, expected: dict[str, dict[str, object]], observed: set[str],
) -> None:
    scan_descriptor: int | None = None
    try:
        scan_descriptor = os.dup(directory_descriptor)
        with os.scandir(scan_descriptor) as iterator:
            children = sorted(
                ((item.name, item.stat(follow_symlinks=False)) for item in iterator),
                key=lambda item: item[0],
            )
    except OSError as exc:
        raise ValueError("snapshot entry tree cannot be enumerated") from exc
    finally:
        if scan_descriptor is not None:
            os.close(scan_descriptor)
    for child_name, child_stat in children:
        relative = f"{prefix}/{child_name}" if prefix else child_name
        if stat.S_ISLNK(child_stat.st_mode):
            raise ValueError(f"snapshot entry {relative} is a symlink")
        if stat.S_ISDIR(child_stat.st_mode):
            child_descriptor = _open_snapshot_directory(
                directory_descriptor, child_name, expected=child_stat,
            )
            try:
                _walk_snapshot(child_descriptor, relative, expected=expected, observed=observed)
            finally:
                os.close(child_descriptor)
        elif stat.S_ISREG(child_stat.st_mode):
            if relative == _MANIFEST_NAME:
                continue
            row = expected.get(relative)
            if row is None:
                raise ValueError(f"snapshot entry {relative} is not declared by the manifest")
            observed.add(relative)
            raw = _read_stable_regular_file_at(
                directory_descriptor, child_name, expected=child_stat,
            )
            if len(raw) != row["size"] or _sha(raw) != row["sha256"]:
                raise ValueError(f"snapshot entry {relative} does not match its manifest")
        else:
            raise ValueError(f"snapshot entry {relative} is not a regular file")


def verify_source_snapshot_v3(snapshot: SourceSnapshotV3) -> SourceSnapshotV3:
    """Fail closed unless the exact manifest-declared tree is still present."""
    root_descriptor = _snapshot_root_descriptor(snapshot)
    rows = _read_snapshot_manifest(snapshot)
    expected = {str(row["path"]): row for row in rows}
    observed: set[str] = set()
    _walk_snapshot(root_descriptor, "", expected=expected, observed=observed)
    if observed != set(expected):
        raise ValueError("snapshot entry tree does not exactly match the manifest")
    tree_sha256 = _tree_sha(rows)
    if tree_sha256 != _require_sha(snapshot.tree_sha256, field="snapshot tree SHA-256"):
        raise ValueError("snapshot tree SHA-256 does not match")
    return snapshot


def read_source_snapshot_entry_v3(snapshot: SourceSnapshotV3, relative_path: str) -> bytes:
    """Read one manifest-declared file using only the owned root descriptor."""
    path = _relative_path(relative_path, field="snapshot entry path")
    verify_source_snapshot_v3(snapshot)
    if path == _MANIFEST_NAME:
        root_descriptor = _snapshot_root_descriptor(snapshot)
        manifest_stat = os.stat(_MANIFEST_NAME, dir_fd=root_descriptor, follow_symlinks=False)
        if stat.S_ISLNK(manifest_stat.st_mode):
            raise ValueError("snapshot entry source-manifest.json is a symlink")
        raw = _read_stable_regular_file_at(root_descriptor, _MANIFEST_NAME, expected=manifest_stat)
        _strict_manifest(snapshot, raw)
        return raw
    rows = _read_snapshot_manifest(snapshot)
    expected = {str(row["path"]): row for row in rows}
    row = expected.get(path)
    if row is None:
        raise ValueError(f"snapshot entry {path} is not declared by the manifest")
    descriptor = os.dup(_snapshot_root_descriptor(snapshot))
    try:
        parts = PurePosixPath(path).parts
        for component in parts[:-1]:
            try:
                child_stat = os.stat(component, dir_fd=descriptor, follow_symlinks=False)
            except OSError as exc:
                raise ValueError(f"snapshot entry {path} cannot be inspected") from exc
            if stat.S_ISLNK(child_stat.st_mode) or not stat.S_ISDIR(child_stat.st_mode):
                raise ValueError(f"snapshot entry {path} has an invalid parent directory")
            child_descriptor = _open_snapshot_directory(descriptor, component, expected=child_stat)
            os.close(descriptor)
            descriptor = child_descriptor
        try:
            file_stat = os.stat(parts[-1], dir_fd=descriptor, follow_symlinks=False)
        except OSError as exc:
            raise ValueError(f"snapshot entry {path} cannot be inspected") from exc
        if stat.S_ISLNK(file_stat.st_mode):
            raise ValueError(f"snapshot entry {path} is a symlink")
        raw = _read_stable_regular_file_at(descriptor, parts[-1], expected=file_stat)
    finally:
        os.close(descriptor)
    if len(raw) != row["size"] or _sha(raw) != row["sha256"]:
        raise ValueError(f"snapshot entry {path} does not match its manifest")
    return raw


def _require_nonnegative_int(value: object, *, field: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{field} must be a nonnegative integer")
    return value


def _require_worker_request(value: object) -> dict[str, object]:
    base_keys = {
        "schema", "campaign_id", "logical_game_id", "retry_index", "subject_seat",
        "agent_sampling_seed", "policy_path", "snapshot",
    }
    if type(value) is not dict or frozenset(value) not in {
        frozenset(base_keys), frozenset((*base_keys, "game")),
    }:
        raise ValueError("worker request has an invalid closed key set")
    if value["schema"] != _WORKER_REQUEST_SCHEMA:
        raise ValueError("worker request schema is invalid")
    if type(value["campaign_id"]) is not str or not value["campaign_id"]:
        raise ValueError("worker request campaign_id is invalid")
    _require_sha(value["logical_game_id"], field="worker request logical_game_id")
    _require_nonnegative_int(value["retry_index"], field="worker request retry_index")
    if value["subject_seat"] not in {0, 1} or type(value["subject_seat"]) is not int:
        raise ValueError("worker request subject_seat must be 0 or 1")
    _require_nonnegative_int(value["agent_sampling_seed"], field="worker request agent_sampling_seed")
    _relative_path(value["policy_path"], field="worker request policy_path")
    snapshot = value["snapshot"]
    if type(snapshot) is not dict or frozenset(snapshot) != {
        "manifest_sha256", "tree_sha256", "root_fd",
    }:
        raise ValueError("worker request snapshot has an invalid closed key set")
    _require_sha(snapshot["manifest_sha256"], field="worker request snapshot manifest_sha256")
    _require_sha(snapshot["tree_sha256"], field="worker request snapshot tree_sha256")
    if type(snapshot["root_fd"]) is not int or snapshot["root_fd"] < 0:
        raise ValueError("worker request snapshot root_fd is invalid")
    if "game" in value:
        game = value["game"]
        if type(game) is not dict or frozenset(game) != _GAME_REQUEST_KEYS:
            raise ValueError("worker request game has an invalid closed key set")
        for name in ("engine_path", "opponent_policy_path", "subject_deck_path", "opponent_deck_path"):
            _relative_path(game[name], field=f"worker request game {name}")
        _require_nonnegative_int(game["environment_seed"], field="worker request game environment_seed")
        if type(game["max_steps"]) is not int or game["max_steps"] <= 0:
            raise ValueError("worker request game max_steps must be positive")
    return value


def _read_closed_request(path: Path) -> tuple[dict[str, object], bytes]:
    """Read one exact canonical request; paths never supply snapshot authority."""
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ValueError("worker request cannot be read") from exc
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        if isinstance(exc, ValueError) and str(exc) == "duplicate snapshot manifest key":
            raise ValueError("worker request has duplicate keys") from exc
        raise ValueError("worker request is not strict JSON") from exc
    request = _require_worker_request(value)
    if raw != _canonical(request):
        raise ValueError("worker request is not canonical JSON")
    return request, raw


def _snapshot_from_worker_request(request: Mapping[str, object]) -> SourceSnapshotV3:
    snapshot = request["snapshot"]
    assert isinstance(snapshot, dict)  # established by _require_worker_request
    descriptor = snapshot["root_fd"]
    assert isinstance(descriptor, int)
    # ``root`` is reporting metadata only.  All verifier reads below are
    # relative to this inherited directory descriptor.
    result = SourceSnapshotV3(
        Path(f"/proc/self/fd/{descriptor}"),
        Path(f"/proc/self/fd/{descriptor}/{_MANIFEST_NAME}"),
        str(snapshot["manifest_sha256"]), str(snapshot["tree_sha256"]), descriptor,
    )
    verify_source_snapshot_v3(result)
    return result


def build_teacher_quality_worker_request_v3(
    *, snapshot: SourceSnapshotV3, campaign_id: str, logical_game_id: str,
    retry_index: int, subject_seat: int, agent_sampling_seed: int, policy_path: str,
    game: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Build the sole request form accepted by a fresh worker.

    The inherited directory FD is deliberately the only snapshot locator.
    Neither this payload nor its child process command contains the original
    worktree path.
    """
    verify_source_snapshot_v3(snapshot)
    request: dict[str, object] = {
        "schema": _WORKER_REQUEST_SCHEMA,
        "campaign_id": campaign_id,
        "logical_game_id": logical_game_id,
        "retry_index": retry_index,
        "subject_seat": subject_seat,
        "agent_sampling_seed": agent_sampling_seed,
        "policy_path": policy_path,
        "snapshot": {
            "manifest_sha256": snapshot.file_sha256,
            "tree_sha256": snapshot.tree_sha256,
            "root_fd": snapshot.root_fd,
        },
    }
    if game is not None:
        request["game"] = dict(game)
    _require_worker_request(request)
    # Ensure a path selected by the parent is also declared by the sealed tree.
    read_source_snapshot_entry_v3(snapshot, policy_path)
    if game is not None:
        for name in ("engine_path", "opponent_policy_path", "subject_deck_path", "opponent_deck_path"):
            read_source_snapshot_entry_v3(snapshot, str(game[name]))
    return request


def _seed_process_rngs(seed: int) -> dict[str, int]:
    """Initialize every supported process-global RNG before policy import."""
    random = __import__("random")
    random.seed(seed)
    try:
        numpy = __import__("numpy")
    except ImportError:
        numpy = None
    if numpy is not None:
        numpy.random.seed(seed)
    try:
        torch = __import__("torch")
    except ImportError:
        torch = None
    if torch is not None:
        torch.manual_seed(seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(seed)
    # The closed response records the requested process-global namespaces.  A
    # missing optional library is harmless to an importing policy and still
    # cannot be misrepresented as an engine seed attestation.
    return {"python": seed, "numpy": seed, "torch": seed}


def _fault_response(
    *, request_sha256: str, snapshot_sha256: str, seed: int, kind: str,
    exception: BaseException | None, exit_code: int | None, elapsed_seconds: float,
    source_exception: str | None = None,
) -> WorkerResponseV3:
    if exception is None:
        exception_class = "WorkerProtocolError"
        message = source_exception or kind
        trace = source_exception or kind
    else:
        exception_class = type(exception).__name__
        message = str(exception) or exception_class
        trace = "".join(traceback.format_exception(type(exception), exception, exception.__traceback__))
    return {
        "schema": _WORKER_RESPONSE_SCHEMA,
        "request_sha256": request_sha256,
        "snapshot_sha256": snapshot_sha256,
        "rng": {"python": seed, "numpy": seed, "torch": seed},
        "engine_randomness": "unattested",
        "fault": {
            "kind": kind,
            "exception_class": exception_class,
            "message": message,
            "source_exception": source_exception or f"{exception_class}: {message}",
            "exit_code": exit_code,
            "traceback_sha256": _sha(trace.encode("utf-8")),
        },
        "elapsed_seconds": elapsed_seconds,
    }


def _completed_response(
    *, request_sha256: str, snapshot_sha256: str, rng: dict[str, int],
    outcome: str, elapsed_seconds: float, game: dict[str, object] | None = None,
) -> WorkerResponseV3:
    response: WorkerResponseV3 = {
        "schema": _WORKER_RESPONSE_SCHEMA,
        "request_sha256": request_sha256,
        "snapshot_sha256": snapshot_sha256,
        "rng": rng,
        "engine_randomness": "unattested",
        "outcome": outcome,
        "elapsed_seconds": elapsed_seconds,
    }
    if game is not None:
        response["game"] = game
    return response


def _require_worker_response(value: object) -> WorkerResponseV3:
    if type(value) is not dict:
        raise ValueError("worker response is not an object")
    base = {
        "schema", "request_sha256", "snapshot_sha256", "rng", "engine_randomness",
        "elapsed_seconds",
    }
    present = frozenset(value)
    if present not in {
        frozenset((*base, "outcome")), frozenset((*base, "outcome", "game")),
        frozenset((*base, "fault")),
    }:
        raise ValueError("worker response has an invalid closed key set")
    if value["schema"] != _WORKER_RESPONSE_SCHEMA:
        raise ValueError("worker response schema is invalid")
    _require_sha(value["request_sha256"], field="worker response request_sha256")
    _require_sha(value["snapshot_sha256"], field="worker response snapshot_sha256")
    rng = value["rng"]
    if type(rng) is not dict or frozenset(rng) != {"python", "numpy", "torch"}:
        raise ValueError("worker response rng has an invalid closed key set")
    for name, seed in rng.items():
        _require_nonnegative_int(seed, field=f"worker response rng {name}")
    if value["engine_randomness"] != "unattested":
        raise ValueError("worker response cannot claim an unattested engine seed")
    elapsed = value["elapsed_seconds"]
    if (
        type(elapsed) not in {int, float} or type(elapsed) is bool
        or not math.isfinite(float(elapsed)) or elapsed < 0
    ):
        raise ValueError("worker response elapsed_seconds is invalid")
    if "outcome" in value:
        if value["outcome"] not in _OUTCOMES:
            raise ValueError("worker response outcome is invalid")
        if "game" in value:
            game = value["game"]
            if type(game) is not dict or frozenset(game) != _GAME_RESPONSE_KEYS:
                raise ValueError("worker response game has an invalid closed key set")
            if game["engine_status"] != "DONE" or game["winner"] not in {0, 1, 2}:
                raise ValueError("worker response game terminal result is invalid")
            if type(game["subject_seat"]) is not int or game["subject_seat"] not in {0, 1}:
                raise ValueError("worker response game subject seat is invalid")
            winner = game["winner"]
            expected_outcome = "draw" if winner == 2 else "win" if winner == game["subject_seat"] else "loss"
            if game["subject_outcome"] != expected_outcome or value["outcome"] != expected_outcome:
                raise ValueError("worker response game subject outcome is inconsistent")
    else:
        fault = value["fault"]
        if type(fault) is not dict or frozenset(fault) != _FAULT_KEYS:
            raise ValueError("worker response fault has an invalid closed key set")
        for name in ("kind", "exception_class", "message", "source_exception"):
            if type(fault[name]) is not str or not fault[name]:
                raise ValueError(f"worker response fault {name} is invalid")
        if fault["exit_code"] is not None and type(fault["exit_code"]) is not int:
            raise ValueError("worker response fault exit_code is invalid")
        _require_sha(fault["traceback_sha256"], field="worker response fault traceback_sha256")
    return value


def _load_module_from_snapshot(snapshot: SourceSnapshotV3, relative_path: str, *, module_name: str):
    # Re-read via the verifier before import; importlib receives a proc-FD
    # spelling of the same descriptor capability, never a worktree path.
    read_source_snapshot_entry_v3(snapshot, relative_path)
    location = f"/proc/self/fd/{snapshot.root_fd}/{relative_path}"
    parent = str(Path(location).parent)
    if parent not in sys.path:
        sys.path.insert(0, parent)
    spec = importlib.util.spec_from_file_location(module_name, location)
    if spec is None or spec.loader is None:
        raise ValueError("snapshot module cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_policy_from_snapshot(snapshot: SourceSnapshotV3, policy_path: str, *, module_name: str):
    module = _load_module_from_snapshot(snapshot, policy_path, module_name=module_name)
    policy = getattr(module, "agent", None)
    if not callable(policy):
        raise ValueError("snapshot policy does not expose callable agent")
    return policy


def _run_one_snapshot_policy(request: Mapping[str, object], snapshot: SourceSnapshotV3) -> str:
    # Task 3 supplies the actual CABT game bridge.  This protocol-level probe
    # intentionally executes only the sealed policy and records no engine seed.
    policy_path = request["policy_path"]
    assert isinstance(policy_path, str)
    policy = _load_policy_from_snapshot(snapshot, policy_path, module_name="_teacher_quality_subject_probe_v3")
    result = policy({})
    verify_source_snapshot_v3(snapshot)
    if type(result) is str and result in _OUTCOMES:
        return result
    if type(result) is dict and frozenset(result) == {"outcome"} and result["outcome"] in _OUTCOMES:
        return str(result["outcome"])
    raise ValueError("snapshot policy result must be a closed outcome")


def _closed_engine_result(value: object, *, subject_seat: int) -> tuple[str, dict[str, object]]:
    if type(value) is not dict or frozenset(value) != {"status", "winner"}:
        raise ValueError("snapshot engine result must be a closed terminal result")
    if value["status"] != "DONE" or value["winner"] not in {0, 1, 2}:
        raise ValueError("snapshot engine result must be DONE with winner 0, 1, or 2")
    winner = value["winner"]
    assert isinstance(winner, int)
    outcome = "draw" if winner == 2 else "win" if winner == subject_seat else "loss"
    return outcome, {
        "engine_status": "DONE", "winner": winner,
        "subject_seat": subject_seat, "subject_outcome": outcome,
    }


def _deck_selecting_agent(policy, deck_path: Path):
    deck = [int(value) for value in deck_path.read_text(encoding="utf-8").split()]
    if len(deck) != 60:
        raise ValueError("snapshot bridge deck must contain exactly 60 cards")

    def agent(observation):
        if type(observation) is not dict:
            raise ValueError("snapshot engine gave policy a non-object observation")
        if observation.get("select") is None:
            return list(deck)
        return policy(observation)

    return agent


def _run_one_snapshot_game(request: Mapping[str, object], snapshot: SourceSnapshotV3) -> tuple[str, dict[str, object]]:
    game = request.get("game")
    if type(game) is not dict:
        raise ValueError("worker request has no game bridge")
    subject_seat = request["subject_seat"]
    assert isinstance(subject_seat, int)
    subject_policy_path = request["policy_path"]
    assert isinstance(subject_policy_path, str)
    engine_path = game["engine_path"]
    opponent_policy_path = game["opponent_policy_path"]
    subject_deck_path = game["subject_deck_path"]
    opponent_deck_path = game["opponent_deck_path"]
    environment_seed = game["environment_seed"]
    max_steps = game["max_steps"]
    assert all(isinstance(item, str) for item in (engine_path, opponent_policy_path, subject_deck_path, opponent_deck_path))
    assert isinstance(environment_seed, int) and isinstance(max_steps, int)
    subject = _load_policy_from_snapshot(snapshot, subject_policy_path, module_name="_teacher_quality_subject_v3")
    opponent = _load_policy_from_snapshot(snapshot, opponent_policy_path, module_name="_teacher_quality_opponent_v3")
    engine = _load_module_from_snapshot(snapshot, engine_path, module_name="_teacher_quality_engine_v3")
    read_source_snapshot_entry_v3(snapshot, subject_deck_path)
    read_source_snapshot_entry_v3(snapshot, opponent_deck_path)
    subject_deck = Path(f"/proc/self/fd/{snapshot.root_fd}/{subject_deck_path}")
    opponent_deck = Path(f"/proc/self/fd/{snapshot.root_fd}/{opponent_deck_path}")
    bridge = getattr(engine, "run_teacher_quality_game_v3", None)
    verify_source_snapshot_v3(snapshot)
    if callable(bridge):
        result = bridge(
            subject_agent=subject, opponent_agent=opponent,
            subject_deck_path=subject_deck, opponent_deck_path=opponent_deck,
            subject_seat=subject_seat, environment_seed=environment_seed, max_steps=max_steps,
        )
    else:
        run_match = getattr(engine, "run_match", None)
        if not callable(run_match):
            raise ValueError("snapshot engine exposes no teacher-quality game bridge")
        with tempfile.TemporaryDirectory(prefix=".teacher-quality-v3-engine-") as output_dir:
            subject_factory = lambda _deck, _seed: _deck_selecting_agent(subject, subject_deck)
            opponent_factory = lambda _deck, _seed: _deck_selecting_agent(opponent, opponent_deck)
            kwargs = {
                "deck_a_path": subject_deck if subject_seat == 0 else opponent_deck,
                "deck_b_path": opponent_deck if subject_seat == 0 else subject_deck,
                "agent_a_name": "sealed-subject" if subject_seat == 0 else "sealed-opponent",
                "agent_b_name": "sealed-opponent" if subject_seat == 0 else "sealed-subject",
                "seed": environment_seed, "output_dir": output_dir, "max_steps": max_steps,
                "save_html": False, "save_result": False,
                "agent_a_factory": subject_factory if subject_seat == 0 else opponent_factory,
                "agent_b_factory": opponent_factory if subject_seat == 0 else subject_factory,
            }
            result = run_match(**kwargs)
    return _closed_engine_result(result, subject_seat=subject_seat)


def worker_main(request_path: str) -> int:
    """Child entrypoint: emit exactly one canonical response record."""
    started = time.monotonic()
    request_sha256 = "0" * 64
    snapshot_sha256 = "0" * 64
    seed = 0
    try:
        request, raw = _read_closed_request(Path(request_path))
        request_sha256 = _sha(raw)
        snapshot = _snapshot_from_worker_request(request)
        snapshot_sha256 = snapshot.file_sha256
        seed = request["agent_sampling_seed"]
        assert isinstance(seed, int)
        rng = _seed_process_rngs(seed)
        if "game" in request:
            outcome, game = _run_one_snapshot_game(request, snapshot)
        else:
            outcome = _run_one_snapshot_policy(request, snapshot)
            game = None
        response = _completed_response(
            request_sha256=request_sha256, snapshot_sha256=snapshot_sha256, rng=rng,
            outcome=outcome, elapsed_seconds=time.monotonic() - started, game=game,
        )
    except BaseException as exc:
        response = _fault_response(
            request_sha256=request_sha256, snapshot_sha256=snapshot_sha256, seed=seed,
            kind="worker_exception", exception=exc, exit_code=None,
            elapsed_seconds=time.monotonic() - started,
        )
    sys.stdout.buffer.write(_canonical(response) + b"\n")
    sys.stdout.buffer.flush()
    return 0


def _parent_fault(
    *, request_sha256: str, snapshot_sha256: str, seed: int, kind: str,
    elapsed_seconds: float, exit_code: int | None = None, source_exception: str,
) -> WorkerResponseV3:
    return _fault_response(
        request_sha256=request_sha256, snapshot_sha256=snapshot_sha256, seed=seed,
        kind=kind, exception=None, exit_code=exit_code, elapsed_seconds=elapsed_seconds,
        source_exception=source_exception,
    )


def _isolated_snapshot_bootstrap(root_fd: int, manifest_sha256: str, tree_sha256: str) -> str:
    """Verify the sealed child authority before importing its policy or engine."""
    root = f"/proc/self/fd/{root_fd}"
    return (
        "import importlib,os,stat,sys;from pathlib import Path;"
        f"root_fd={root_fd};"
        f"manifest_sha256={manifest_sha256!r};tree_sha256={tree_sha256!r};"
        "root_stat=os.fstat(root_fd);"
        "assert stat.S_ISDIR(root_stat.st_mode);"
        "assert os.path.samestat(os.stat('.'),root_stat);"
        f"sys.path.insert(0,{root!r});"
        f"sys.path.insert(0,{(root + '/src')!r});"
        "worker=importlib.import_module('mage_ptcg.meta_specialist.teacher_quality_worker_v3');"
        "worker_path=getattr(worker,'__file__',None);"
        "assert isinstance(worker_path,str) and worker_path.startswith(" + repr(root + "/src/") + ");"
        "snapshot=worker.SourceSnapshotV3(Path(" + repr(root) + "),Path(" + repr(root + "/source-manifest.json") + "),manifest_sha256,tree_sha256,root_fd);"
        "worker.verify_source_snapshot_v3(snapshot);"
    )


def _sealed_snapshot_cwd(root_fd: int) -> str:
    """Return the proc-FD spelling of a verified snapshot-root capability."""
    if type(root_fd) is not int or root_fd < 0:
        raise ValueError("snapshot root descriptor is closed or invalid")
    root = f"/proc/self/fd/{root_fd}"
    try:
        descriptor_stat = os.fstat(root_fd)
        path_stat = os.stat(root)
    except OSError as exc:
        raise ValueError("snapshot root descriptor is closed or invalid") from exc
    if not stat.S_ISDIR(descriptor_stat.st_mode) or not os.path.samestat(descriptor_stat, path_stat):
        raise ValueError("snapshot root descriptor is not a directory")
    return root


def validate_snapshot_engine_import_v3(
    *, snapshot: SourceSnapshotV3, engine_path: str,
) -> dict[str, str]:
    """Verify the default-engine import boundary without starting a CABT game.

    This is intentionally an import/setup probe: it executes the engine module
    but never calls its game runner.  The child has no original-worktree path
    in ``sys.path`` and inherits only the sealed root directory descriptor.
    """
    verify_source_snapshot_v3(snapshot)
    _relative_path(engine_path, field="engine import path")
    read_source_snapshot_entry_v3(snapshot, engine_path)
    descriptor = snapshot.root_fd
    root = f"/proc/self/fd/{descriptor}"
    bootstrap = _isolated_snapshot_bootstrap(descriptor, snapshot.file_sha256, snapshot.tree_sha256)
    bootstrap += (
        "import importlib.util,json,sys;"
        f"location={root + '/' + engine_path!r};"
        "spec=importlib.util.spec_from_file_location('_teacher_quality_engine_import_probe_v3',location);"
        "assert spec is not None and spec.loader is not None;"
        "module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);"
        "worker.verify_source_snapshot_v3(snapshot);"
        "main=sys.modules.get('main');"
        "assert main is not None;"
        "main_path=getattr(main,'__file__',None);"
        "assert isinstance(main_path,str) and main_path.startswith(" + repr(root + "/") + ");"
        "result={'schema':'meta-specialist-teacher-quality-engine-import-v3',"
        f"'engine_path':{engine_path!r},'main_path':main_path}};"
        "sys.stdout.buffer.write(json.dumps(result,sort_keys=True,separators=(',',':')).encode()+b'\\n')"
    )
    completed = subprocess.run(
        [sys.executable, "-I", "-B", "-c", bootstrap],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        check=False, timeout=30.0, pass_fds=(descriptor,), close_fds=True,
        cwd=_sealed_snapshot_cwd(descriptor),
    )
    if completed.returncode != 0:
        raise ValueError(
            "snapshot engine import setup failed; "
            f"stderr_sha256={_sha(completed.stderr)}"
        )
    if completed.stdout.count(b"\n") != 1 or not completed.stdout.endswith(b"\n"):
        raise ValueError("snapshot engine import setup emitted unexpected stdout")
    raw = completed.stdout[:-1]
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError("snapshot engine import setup emitted malformed JSON") from exc
    if (
        type(value) is not dict
        or frozenset(value) != {"schema", "engine_path", "main_path"}
        or value["schema"] != "meta-specialist-teacher-quality-engine-import-v3"
        or value["engine_path"] != engine_path
        or type(value["main_path"]) is not str
        or not value["main_path"].startswith(root + "/")
        or raw != _canonical(value)
    ):
        raise ValueError("snapshot engine import setup response is invalid")
    return {"engine_path": engine_path, "main_path": str(value["main_path"])}


def validate_snapshot_policy_imports_v3(
    *, snapshot: SourceSnapshotV3, policy_paths: tuple[str, ...],
) -> list[str]:
    """Import every frozen policy in one isolated non-CABT child probe.

    Loading both Rule v0 and generic-panel policies in one interpreter catches
    the otherwise latent ``agents`` package collision before an evidence
    campaign writes any attempt rows.  The child has only the snapshot root
    capability and the snapshot ``src`` directory on ``sys.path``.
    """
    verify_source_snapshot_v3(snapshot)
    if not policy_paths or len(set(policy_paths)) != len(policy_paths):
        raise ValueError("snapshot policy import probe paths must be nonempty and unique")
    for path in policy_paths:
        _relative_path(path, field="policy import path")
        read_source_snapshot_entry_v3(snapshot, path)
    descriptor = snapshot.root_fd
    root = f"/proc/self/fd/{descriptor}"
    probe = (
        "import importlib.util,json,sys\n"
        f"root={root!r}\n"
        f"paths={list(policy_paths)!r}\n"
        "loaded=[]\n"
        "for index,path in enumerate(paths):\n"
        "    location=root + '/' + path\n"
        "    spec=importlib.util.spec_from_file_location('_teacher_quality_policy_import_%d_v3' % index,location)\n"
        "    assert spec is not None and spec.loader is not None\n"
        "    module=importlib.util.module_from_spec(spec)\n"
        "    spec.loader.exec_module(module)\n"
        "    assert callable(getattr(module,'agent',None))\n"
        "    loaded.append(path)\n"
        "worker.verify_source_snapshot_v3(snapshot)\n"
        "result={'schema':'meta-specialist-teacher-quality-policy-import-v3','paths':loaded}\n"
        "sys.stdout.buffer.write(json.dumps(result,sort_keys=True,separators=(',',':')).encode()+b'\\n')\n"
    )
    completed = subprocess.run(
        [sys.executable, "-I", "-B", "-c", _isolated_snapshot_bootstrap(
            descriptor, snapshot.file_sha256, snapshot.tree_sha256,
        ) + "exec(" + repr(probe) + ")"],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        check=False, timeout=30.0, pass_fds=(descriptor,), close_fds=True,
        cwd=_sealed_snapshot_cwd(descriptor),
    )
    if completed.returncode != 0:
        raise ValueError(
            "snapshot policy import preflight failed; "
            f"stderr_sha256={_sha(completed.stderr)}"
        )
    if completed.stdout.count(b"\n") != 1 or not completed.stdout.endswith(b"\n"):
        raise ValueError("snapshot policy import preflight emitted unexpected stdout")
    raw = completed.stdout[:-1]
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError("snapshot policy import preflight emitted malformed JSON") from exc
    if (
        type(value) is not dict
        or frozenset(value) != {"schema", "paths"}
        or value["schema"] != "meta-specialist-teacher-quality-policy-import-v3"
        or value["paths"] != list(policy_paths)
        or raw != _canonical(value)
    ):
        raise ValueError("snapshot policy import preflight response is invalid")
    return list(policy_paths)


def run_teacher_quality_attempt_worker_v3(
    request_path: Path, *, timeout_seconds: float = 30.0,
) -> WorkerResponseV3:
    """Run one isolated worker and fail closed on every protocol violation."""
    if type(timeout_seconds) not in {int, float} or type(timeout_seconds) is bool or timeout_seconds <= 0:
        raise ValueError("worker timeout_seconds must be positive")
    request, raw_request = _read_closed_request(Path(request_path))
    request_sha256 = _sha(raw_request)
    snapshot = _snapshot_from_worker_request(request)
    snapshot_sha256 = snapshot.file_sha256
    seed = request["agent_sampling_seed"]
    assert isinstance(seed, int)
    started = time.monotonic()
    descriptor = snapshot.root_fd
    # -I removes cwd/PYTHONPATH influence.  Isolated Python cannot resolve a
    # package with ``-m`` before a snapshot path is installed, so the tiny
    # bootstrap performs the exact equivalent run_module dispatch after adding
    # only the inherited snapshot source root.
    bootstrap = (
        _isolated_snapshot_bootstrap(descriptor, snapshot.file_sha256, snapshot.tree_sha256)
        + "import runpy;"
        "runpy.run_module('mage_ptcg.meta_specialist.teacher_quality_worker_v3',run_name='__main__')"
    )
    command = [sys.executable, "-I", "-B", "-c", bootstrap, str(request_path)]
    try:
        completed = subprocess.run(
            command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            check=False, timeout=float(timeout_seconds), pass_fds=(descriptor,), close_fds=True,
            cwd=_sealed_snapshot_cwd(descriptor),
        )
    except subprocess.TimeoutExpired as exc:
        return _parent_fault(
            request_sha256=request_sha256, snapshot_sha256=snapshot_sha256, seed=seed,
            kind="worker_timeout", elapsed_seconds=time.monotonic() - started,
            source_exception=f"worker exceeded {timeout_seconds} seconds: {exc}",
        )
    except OSError as exc:
        return _parent_fault(
            request_sha256=request_sha256, snapshot_sha256=snapshot_sha256, seed=seed,
            kind="worker_spawn_error", elapsed_seconds=time.monotonic() - started,
            source_exception=f"{type(exc).__name__}: {exc}",
        )
    elapsed = time.monotonic() - started
    if completed.returncode != 0:
        return _parent_fault(
            request_sha256=request_sha256, snapshot_sha256=snapshot_sha256, seed=seed,
            kind="worker_nonzero_exit", elapsed_seconds=elapsed, exit_code=completed.returncode,
            source_exception=f"worker exited {completed.returncode}; stderr_sha256={_sha(completed.stderr)}",
        )
    if completed.stdout.count(b"\n") != 1 or not completed.stdout.endswith(b"\n"):
        return _parent_fault(
            request_sha256=request_sha256, snapshot_sha256=snapshot_sha256, seed=seed,
            kind="worker_protocol_error", elapsed_seconds=elapsed,
            source_exception=f"unexpected worker stdout sha256={_sha(completed.stdout)}",
        )
    raw_response = completed.stdout[:-1]
    try:
        response = json.loads(raw_response.decode("utf-8"), object_pairs_hook=_reject_duplicates)
        response = _require_worker_response(response)
        if raw_response != _canonical(response):
            raise ValueError("worker response is not canonical JSON")
        if response["request_sha256"] != request_sha256:
            raise ValueError("worker response request identity does not match")
        if response["snapshot_sha256"] != snapshot_sha256:
            raise ValueError("worker response snapshot identity does not match")
        if response["rng"] != {"python": seed, "numpy": seed, "torch": seed}:
            raise ValueError("worker response RNG provenance does not match")
        if "game" in request and "outcome" in response:
            game = response.get("game")
            if type(game) is not dict or game.get("subject_seat") != request["subject_seat"]:
                raise ValueError("worker response game identity does not match")
        return response
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        return _parent_fault(
            request_sha256=request_sha256, snapshot_sha256=snapshot_sha256, seed=seed,
            kind="worker_protocol_error", elapsed_seconds=elapsed,
            source_exception=f"{type(exc).__name__}: {exc}; stdout_sha256={_sha(completed.stdout)}",
        )


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("teacher-quality worker requires one request path")
    raise SystemExit(worker_main(sys.argv[1]))


__all__ = [
    "SourceSnapshotV3", "WorkerResponseV3", "build_teacher_quality_worker_request_v3",
    "read_source_snapshot_entry_v3", "run_teacher_quality_attempt_worker_v3",
    "seal_teacher_quality_source_snapshot_v3", "verify_source_snapshot_v3",
    "validate_snapshot_engine_import_v3", "validate_snapshot_policy_imports_v3",
]
