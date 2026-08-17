"""Verify local candidate packages; this command never submits to Kaggle."""
from __future__ import annotations

import argparse
import ast
import hashlib
import io
import json
import os
from pathlib import PurePosixPath
import subprocess
import sys
import tarfile
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from mage_ptcg.student.artifact import load_validated_artifact
from mage_ptcg.meta_specialist.submission_privacy import (
    SubmissionPrivacyError,
    parse_submission_json_member,
)
from scripts.build_submission import _contains_secret, verify_submission_artifact
from scripts.build_student_submission import (
    _MAX_ARCHIVE_BYTES,
    _MAX_MEMBER_BYTES,
    _read_bounded_file,
    StudentArtifactError,
    verify_student_submission,
)


# Local-import-closure allowlist for the Student Kaggle package: a (source
# path, imported module) pair whose import is provably unreachable during
# Student inference may be listed here with a reason. It is empty because the
# one known case (mage_ptcg.distillation.actor_visible_attestation) was
# removed from the packaged cabt_trace.py copy instead of being allowlisted;
# see scripts.kaggle_student_entrypoint.render_student_cabt_trace.
OPTIONAL_LOCAL_IMPORTS: dict[tuple[str, str], str] = {}

# These local prefixes are never permitted in the optional allowlist above:
# a Student package must never depend on Rule v1, Knowledge, or the solver.
_FORBIDDEN_OPTIONAL_MODULE_MARKERS = ("rule_agent_v1", "mage_ptcg.knowledge", "mage_ptcg.solver")

_LOCAL_IMPORT_PREFIXES = ("mage_submission_agents", "mage_ptcg")

KAGGLE_PACKAGE_MANIFEST_NAME = "kaggle-package-manifest.json"
_COMMON_PACKAGE_MANIFEST_FIELDS = frozenset(
    {
        "schema_version",
        "agent_kind",
        "competition_slug",
        "entrypoint",
        "deck_hash",
        "source_head",
        "private_artifacts_included",
        "contract",
        "builder_result",
    }
)
_STUDENT_PACKAGE_MANIFEST_FIELDS = frozenset(
    {
        "model_hash",
        "artifact_purpose",
        "performance_eligible",
        "fallback_policy",
    }
)
_CONTRACT_FIELDS = frozenset(
    {"submission_method", "archive_type", "entrypoint"}
)


@dataclass(frozen=True)
class _PackageSidecarSnapshot:
    sidecar_bytes: bytes
    root_member_bytes: tuple[tuple[str, bytes], ...]

    def member_bytes(self, name: str) -> bytes:
        for member_name, data in self.root_member_bytes:
            if member_name == name:
                return data
        raise ValueError(f"kaggle package snapshot is missing {name}")


def _is_lower_hex(value: object, lengths: frozenset[int]) -> bool:
    return (
        isinstance(value, str)
        and len(value) in lengths
        and all(character in "0123456789abcdef" for character in value)
    )


def _read_package_file(path: Path, *, message: str) -> bytes:
    try:
        return _read_bounded_file(path, limit=_MAX_MEMBER_BYTES, message=message)
    except StudentArtifactError as exc:
        raise ValueError(message) from exc


def _safe_snapshot_member_path(name: object) -> str:
    if not isinstance(name, str) or not name:
        raise ValueError("kaggle package member path is invalid")
    path = PurePosixPath(name)
    if (
        path.is_absolute()
        or "." in path.parts
        or ".." in path.parts
        or "\\" in name
        or path.as_posix() != name
    ):
        raise ValueError("kaggle package member path is unsafe")
    return name


def _read_snapshot_member(root: Path, name: str, *, limit: int) -> bytes:
    path = root.joinpath(*PurePosixPath(name).parts)
    parent = path.parent
    while parent != root:
        if parent.is_symlink():
            raise ValueError(f"kaggle package member path is a symlink: {name}")
        parent = parent.parent
    if root.is_symlink() or path.is_symlink() or not path.is_file():
        raise ValueError(f"kaggle package member must be a regular file: {name}")
    try:
        return _read_bounded_file(
            path,
            limit=limit,
            message=f"kaggle package member exceeds byte bound: {name}",
        )
    except StudentArtifactError as exc:
        raise ValueError(f"kaggle package member exceeds byte bound: {name}") from exc


def _snapshot_manifest_member_names(inner_manifest: dict[str, Any]) -> tuple[str, ...]:
    files = inner_manifest.get("files")
    if isinstance(files, dict):
        # The existing self-owned cg candidate manifest records files as a
        # path-keyed mapping, while the Rule/Student artifacts use a list of
        # records.  Both shapes are accepted here; the cg adapter performs the
        # stricter canonical-order and hash checks later.
        records = []
        for name, record in files.items():
            if not isinstance(record, dict) or "path" in record:
                raise ValueError("inner artifact manifest file record is invalid")
            records.append({"path": name, **record})
    elif isinstance(files, list):
        records = files
    else:
        raise ValueError("inner artifact manifest files are invalid")
    names: list[str] = []
    seen: set[str] = set()
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("inner artifact manifest file record is invalid")
        name = _safe_snapshot_member_path(record.get("path"))
        if name in {"manifest.json", "submission.tar.gz"} or name in seen:
            raise ValueError("inner artifact manifest file path is invalid")
        seen.add(name)
        names.append(name)
    return tuple(names)


def _load_kaggle_package_manifest_snapshot(
    root: Path,
) -> tuple[dict[str, Any], _PackageSidecarSnapshot]:
    """Load and bind the local-only wrapper manifest before parking it."""
    path = root / KAGGLE_PACKAGE_MANIFEST_NAME
    if path.is_symlink() or not path.is_file():
        raise ValueError("kaggle package manifest must be a regular file")
    sidecar_bytes = _read_package_file(
        path,
        message="kaggle package manifest exceeds byte bound",
    )
    try:
        value = parse_submission_json_member(
            KAGGLE_PACKAGE_MANIFEST_NAME,
            sidecar_bytes,
        )
    except (OSError, SubmissionPrivacyError) as exc:
        raise ValueError(f"kaggle package manifest is invalid: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("kaggle package manifest must be an object")
    kind = value.get("agent_kind")
    expected_fields = _COMMON_PACKAGE_MANIFEST_FIELDS
    if kind == "student":
        expected_fields |= _STUDENT_PACKAGE_MANIFEST_FIELDS
    elif kind not in {"rule", "cg"}:
        raise ValueError("kaggle package manifest agent_kind is invalid")
    if set(value) != expected_fields:
        raise ValueError("kaggle package manifest top-level fields are invalid")
    if value["schema_version"] != "kaggle-agent-package-v1":
        raise ValueError("kaggle package manifest schema_version is invalid")
    if value["competition_slug"] != "pokemon-tcg-ai-battle":
        raise ValueError("kaggle package manifest competition_slug is invalid")
    if value["entrypoint"] != "main.py":
        raise ValueError("kaggle package manifest entrypoint is invalid")
    if value["private_artifacts_included"] is not False:
        raise ValueError("kaggle package manifest privacy declaration is invalid")
    if not _is_lower_hex(value["deck_hash"], frozenset({64})):
        raise ValueError("kaggle package manifest deck_hash is invalid")
    if not _is_lower_hex(value["source_head"], frozenset({40, 64})):
        raise ValueError("kaggle package manifest source_head is invalid")

    contract = value["contract"]
    if not isinstance(contract, dict) or set(contract) != _CONTRACT_FIELDS:
        raise ValueError("kaggle package manifest contract fields are invalid")
    if (
        any(
            not isinstance(contract[field], str) or not contract[field]
            for field in ("submission_method", "archive_type")
        )
        or contract["entrypoint"] != value["entrypoint"]
    ):
        raise ValueError("kaggle package manifest contract is invalid")

    deck_path = root / "deck.csv"
    if deck_path.is_symlink() or not deck_path.is_file():
        raise ValueError("kaggle package deck must be a regular file")
    deck_bytes = _read_package_file(
        deck_path,
        message="kaggle package deck exceeds byte bound",
    )
    if hashlib.sha256(deck_bytes).hexdigest() != value["deck_hash"]:
        raise ValueError("kaggle package manifest deck_hash does not match deck.csv")

    inner_path = root / "manifest.json"
    if inner_path.is_symlink() or not inner_path.is_file():
        raise ValueError("inner artifact manifest must be a regular file")
    inner_manifest_bytes = _read_package_file(
        inner_path,
        message="inner artifact manifest exceeds byte bound",
    )
    try:
        inner_manifest = parse_submission_json_member(
            "manifest.json",
            inner_manifest_bytes,
        )
    except (OSError, SubmissionPrivacyError) as exc:
        raise ValueError(f"inner artifact manifest is invalid: {exc}") from exc
    if not isinstance(inner_manifest, dict) or value["builder_result"] != inner_manifest:
        raise ValueError(
            "kaggle package manifest builder_result does not match manifest.json"
        )

    if kind == "student":
        if not _is_lower_hex(value["model_hash"], frozenset({64})):
            raise ValueError("kaggle package manifest model_hash is invalid")
        if (
            value["artifact_purpose"] != "ACTUAL_TRAINED"
            or value["performance_eligible"] is not True
            or value["fallback_policy"] != "Rule Agent v0"
        ):
            raise ValueError("kaggle package Student identity is invalid")
    archive_bytes = _read_snapshot_member(
        root,
        "submission.tar.gz",
        limit=_MAX_ARCHIVE_BYTES,
    )
    root_members: dict[str, bytes] = {
        "deck.csv": deck_bytes,
        "manifest.json": inner_manifest_bytes,
        "submission.tar.gz": archive_bytes,
    }
    for name in _snapshot_manifest_member_names(inner_manifest):
        if name in root_members:
            continue
        root_members[name] = _read_snapshot_member(
            root,
            name,
            limit=_MAX_MEMBER_BYTES,
        )
    return value, _PackageSidecarSnapshot(
        sidecar_bytes=sidecar_bytes,
        root_member_bytes=tuple(root_members.items()),
    )


def _load_kaggle_package_manifest(root: Path) -> dict[str, Any]:
    """Compatibility wrapper for callers interested only in parsed metadata."""
    return _load_kaggle_package_manifest_snapshot(root)[0]


def _require_snapshot_match(
    root: Path,
    snapshot: _PackageSidecarSnapshot,
    *,
    sidecar_path: Path | None,
    stage: str,
) -> None:
    if sidecar_path is not None:
        if sidecar_path.is_symlink() or not sidecar_path.is_file():
            raise ValueError(f"kaggle package manifest changed {stage}")
        if _read_package_file(
            sidecar_path,
            message=f"kaggle package manifest changed {stage}",
        ) != snapshot.sidecar_bytes:
            raise ValueError(f"kaggle package manifest changed {stage}")
    for name, expected in snapshot.root_member_bytes:
        limit = _MAX_ARCHIVE_BYTES if name == "submission.tar.gz" else _MAX_MEMBER_BYTES
        try:
            actual = _read_snapshot_member(root, name, limit=limit)
        except ValueError as exc:
            raise ValueError(f"kaggle package {name} changed {stage}") from exc
        if actual != expected:
            raise ValueError(f"kaggle package {name} changed {stage}")


def _restore_verified_sidecar(
    root: Path,
    sidecar: Path,
    parked: Path,
    snapshot: _PackageSidecarSnapshot,
) -> None:
    """Restore the parked regular file without replacing an occupied target."""
    parked_matches_snapshot = (
        not parked.is_symlink()
        and parked.is_file()
        and _read_package_file(
            parked,
            message="kaggle package manifest restoration failed",
        )
        == snapshot.sidecar_bytes
    )
    restore: Path | None = None
    if parked_matches_snapshot:
        if sidecar.exists() or sidecar.is_symlink():
            raise ValueError("kaggle package manifest restoration destination is occupied")
        try:
            os.link(parked, sidecar, follow_symlinks=False)
        except FileExistsError as exc:
            raise ValueError("kaggle package manifest restoration destination is occupied") from exc
        except OSError as exc:
            raise ValueError("kaggle package manifest restoration failed") from exc
    else:
        # The parked file was altered, so preserve the original immutable
        # snapshot in an exclusive staging file before touching the destination.
        # If the destination is obstructed, this staging copy remains a
        # recoverable exact original instead of deleting the only good bytes.
        restore = root.parent / f".{root.name}.{KAGGLE_PACKAGE_MANIFEST_NAME}.restore"
        if restore.exists() or restore.is_symlink():
            raise ValueError("kaggle package manifest restoration path is occupied")
        descriptor = -1
        try:
            descriptor = os.open(
                restore,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            with os.fdopen(descriptor, "wb") as output:
                descriptor = -1
                if output.write(snapshot.sidecar_bytes) != len(snapshot.sidecar_bytes):
                    raise OSError("could not write complete sidecar snapshot")
                output.flush()
                os.fsync(output.fileno())
        except FileExistsError as exc:
            raise ValueError("kaggle package manifest restoration destination is occupied") from exc
        except OSError as exc:
            if descriptor >= 0:
                os.close(descriptor)
            raise ValueError("kaggle package manifest restoration failed") from exc
        if _read_package_file(
            restore,
            message="kaggle package manifest restoration failed",
        ) != snapshot.sidecar_bytes:
            raise ValueError("kaggle package manifest restoration failed")
        if sidecar.exists() or sidecar.is_symlink():
            raise ValueError("kaggle package manifest restoration destination is occupied")
        try:
            os.link(restore, sidecar, follow_symlinks=False)
        except FileExistsError as exc:
            raise ValueError("kaggle package manifest restoration destination is occupied") from exc
        except OSError as exc:
            raise ValueError("kaggle package manifest restoration failed") from exc
    if sidecar.is_symlink() or not sidecar.is_file() or _read_package_file(
        sidecar,
        message="kaggle package manifest restoration failed",
    ) != snapshot.sidecar_bytes:
        raise ValueError("kaggle package manifest restoration failed")
    try:
        if parked.exists() or parked.is_symlink():
            parked.unlink()
        if restore is not None:
            restore.unlink()
    except OSError as exc:
        raise ValueError("kaggle package manifest restoration cleanup failed") from exc


def _park_verified_sidecar(
    root: Path,
    sidecar: Path,
    parked: Path,
    snapshot: _PackageSidecarSnapshot,
) -> None:
    """Move the sidecar out of the verifier's inventory without replacement.

    ``Path.replace`` is unsafe here: a process can create the deterministic
    parking pathname after an existence check, and POSIX rename would silently
    replace that unrelated file.  Linking is an exclusive destination create;
    only after the parked hard link is verified do we unlink the source.  Thus
    every failure before the unlink leaves the original sidecar in place, and a
    racing parked target is never overwritten.
    """
    try:
        os.link(sidecar, parked, follow_symlinks=False)
    except FileExistsError as exc:
        raise ValueError("kaggle package manifest parking path is occupied") from exc
    except OSError as exc:
        raise ValueError("kaggle package manifest parking failed") from exc
    try:
        _require_snapshot_match(
            root,
            snapshot,
            sidecar_path=parked,
            stage="while parking",
        )
    except ValueError:
        # The original sidecar has not been unlinked yet.  Do not remove the
        # parked pathname: a concurrent actor could have replaced it after the
        # link, and preserving it is safer than deleting an unknown file.
        raise
    try:
        sidecar.unlink()
    except OSError as exc:
        # Keep both verified hard links rather than risking a lossy rollback.
        # The source remains the exact original and the caller can retry after
        # clearing the now-occupied parking path.
        raise ValueError("kaggle package manifest parking failed") from exc


def _verify_without_package_sidecar(
    root: Path,
    verify: Callable[[Path], dict[str, object]],
    *,
    snapshot: _PackageSidecarSnapshot,
) -> dict[str, object]:
    sidecar = root / KAGGLE_PACKAGE_MANIFEST_NAME
    parked = root.parent / f".{root.name}.{KAGGLE_PACKAGE_MANIFEST_NAME}.check"
    _require_snapshot_match(
        root,
        snapshot,
        sidecar_path=sidecar,
        stage="before parking",
    )
    _park_verified_sidecar(root, sidecar, parked, snapshot)
    try:
        _require_snapshot_match(
            root,
            snapshot,
            sidecar_path=parked,
            stage="while parked",
        )
        return verify(root)
    finally:
        parked_error: ValueError | None = None
        try:
            _require_snapshot_match(
                root,
                snapshot,
                sidecar_path=parked,
                stage="while parked",
            )
        except ValueError as exc:
            parked_error = exc
        _restore_verified_sidecar(root, sidecar, parked, snapshot)
        _require_snapshot_match(
            root,
            snapshot,
            sidecar_path=sidecar,
            stage="after restoring",
        )
        if parked_error is not None:
            raise parked_error


_CG_RUNTIME_MEMBERS = (
    "cg/__init__.py",
    "cg/api.py",
    "cg/libcg.so",
    "cg/sim.py",
    "cg/utils.py",
    "deck.csv",
    "main.py",
)
_CG_OPTIONAL_SAMPLE_MEMBERS = frozenset(
    {"cg/cg.dll", "cg/game.py", "cg/libcg-arm64.so", "cg/libcg.dylib"}
)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _verify_cg_runtime_artifact(
    root: Path,
    outer_manifest: dict[str, Any],
) -> dict[str, object]:
    """Verify the self-owned cg package while the outer sidecar is parked.

    The normal Rule v0 builder intentionally rejects this different runtime
    schema.  This adapter keeps the outer ``kaggle-agent-package-v1`` contract
    unchanged while binding the inner cg manifest, exact official runtime
    bytes, canonical archive shape, and a clean-room CABT smoke.
    """
    inner_path = root / "manifest.json"
    archive_path = root / "submission.tar.gz"
    if inner_path.is_symlink() or not inner_path.is_file():
        raise ValueError("cg inner manifest must be a regular file")
    if archive_path.is_symlink() or not archive_path.is_file():
        raise ValueError("cg submission archive must be a regular file")
    try:
        inner = json.loads(inner_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("cg inner manifest is invalid JSON") from exc
    inner_schema = (
        inner.get("artifact_schema_version")
        if isinstance(inner, dict)
        else None
    )
    if inner_schema is None and isinstance(inner, dict):
        # Current cg policy-screen manifests predate the generic artifact
        # field name and use ``schema_version`` instead.
        inner_schema = inner.get("schema_version")
    if not isinstance(inner, dict) or not isinstance(inner_schema, str) or not inner_schema.startswith(
        "meta-specialist-root-cg-"
    ):
        raise ValueError("cg inner manifest schema is invalid")
    if outer_manifest.get("builder_result") != inner:
        raise ValueError("outer builder_result does not match cg inner manifest")
    archive_meta = inner.get("archive")
    if not isinstance(archive_meta, dict) or archive_meta.get("path") != "submission.tar.gz":
        raise ValueError("cg archive path is invalid")

    raw_records = inner.get("files")
    if isinstance(raw_records, dict):
        if set(raw_records) != set(_CG_RUNTIME_MEMBERS):
            raise ValueError("cg runtime file set is invalid")
        records = []
        for name in _CG_RUNTIME_MEMBERS:
            record = raw_records[name]
            if not isinstance(record, dict) or "path" in record:
                raise ValueError("cg runtime file record is invalid")
            records.append({"path": name, **record})
    elif isinstance(raw_records, list):
        records = raw_records
    else:
        raise ValueError("cg runtime file records are invalid")
    if tuple(
        record.get("path") if isinstance(record, dict) else None
        for record in records
    ) != _CG_RUNTIME_MEMBERS:
        raise ValueError("cg runtime file order is invalid")

    required_paths = {
        "manifest.json",
        "submission.tar.gz",
        *_CG_RUNTIME_MEMBERS,
    }
    actual_paths: set[str] = set()
    for path in root.rglob("*"):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            raise ValueError(f"cg package member must not be a symlink: {relative}")
        if path.is_file():
            actual_paths.add(relative)
    unknown_paths = actual_paths - required_paths - _CG_OPTIONAL_SAMPLE_MEMBERS
    missing_paths = required_paths - actual_paths
    if unknown_paths or missing_paths:
        raise ValueError(
            f"cg package inventory mismatch: missing={sorted(missing_paths)}, "
            f"unknown={sorted(unknown_paths)}, "
            f"actual={sorted(actual_paths)}"
        )

    for record in records:
        if not isinstance(record, dict):
            raise ValueError("cg runtime file record is invalid")
        path_value = record.get("path")
        if path_value not in _CG_RUNTIME_MEMBERS:
            raise ValueError("cg runtime file path is invalid")
        path = root.joinpath(*PurePosixPath(path_value).parts)
        data = path.read_bytes()
        if record.get("size") != len(data) or record.get("sha256") != _sha256_bytes(data):
            raise ValueError(f"cg runtime file hash/size mismatch: {path_value}")

    deck_bytes = (root / "deck.csv").read_bytes()
    main_bytes = (root / "main.py").read_bytes()
    deck_identity = inner.get("deck_identity")
    deck_identity_hash = (
        deck_identity.get("sha256")
        if isinstance(deck_identity, dict)
        else inner.get("deck_sha256")
    )
    if deck_identity_hash != _sha256_bytes(deck_bytes):
        raise ValueError("cg deck identity mismatch")
    if inner.get("source_deck_sha256") != _sha256_bytes(deck_bytes):
        raise ValueError("cg source deck identity mismatch")
    if inner.get("policy_source_sha256") != _sha256_bytes(main_bytes):
        raise ValueError("cg policy identity mismatch")
    try:
        tree = ast.parse(main_bytes.decode("utf-8"), filename="main.py")
    except (UnicodeDecodeError, SyntaxError) as exc:
        raise ValueError("cg main.py is invalid Python") from exc
    functions = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    imports_cg = any(
        isinstance(node, ast.ImportFrom) and node.module == "cg.api"
        for node in ast.walk(tree)
    ) or any(
        isinstance(node, ast.Import)
        and any(alias.name == "cg.api" for alias in node.names)
        for node in ast.walk(tree)
    )
    if "agent" not in functions or not imports_cg:
        raise ValueError("cg main.py must define agent() and import cg.api")

    archive_bytes = archive_path.read_bytes()
    archive_meta = inner.get("archive")
    if archive_meta.get("sha256") != _sha256_bytes(archive_bytes):
        raise ValueError("cg archive hash mismatch")
    try:
        with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as archive:
            members = archive.getmembers()
            if tuple(member.name for member in members) != _CG_RUNTIME_MEMBERS:
                raise ValueError("cg archive member order mismatch")
            for member, expected_name in zip(members, _CG_RUNTIME_MEMBERS):
                if not member.isreg() or member.name != expected_name:
                    raise ValueError("cg archive member is not a regular canonical file")
                if member.uid != 0 or member.gid != 0 or member.mtime != 0 or member.mode != 0o644:
                    raise ValueError("cg archive metadata is not canonical")
                handle = archive.extractfile(member)
                if handle is None or handle.read() != root.joinpath(*PurePosixPath(expected_name).parts).read_bytes():
                    raise ValueError(f"cg archive content mismatch: {expected_name}")
    except (OSError, EOFError, tarfile.TarError) as exc:
        raise ValueError("cg submission archive is invalid") from exc

    from scripts.verify_root_cg_submission_candidate_v1 import verify_cg_archive

    cg_check = verify_cg_archive(archive_path, smoke_games=4, smoke_seed=41200000)
    smoke = cg_check.get("clean_room_smoke", {})
    return {
        "status": "PASS" if cg_check.get("status") == "PASS" else "FAIL",
        "runtime": "cg",
        "archive_sha256": _sha256_bytes(archive_bytes),
        "deck_sha256": _sha256_bytes(deck_bytes),
        "policy_sha256": _sha256_bytes(main_bytes),
        "clean_room_smoke": smoke,
        "cg_runtime_parity": cg_check.get("inspection", {}).get("cg_runtime_parity"),
    }


def _verify_package_runtime(root: Path, outer_manifest: dict[str, Any]) -> dict[str, object]:
    inner_path = root / "manifest.json"
    try:
        inner = json.loads(inner_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("inner manifest cannot be loaded") from exc
    if isinstance(inner, dict) and (
        str(inner.get("artifact_schema_version", "")).startswith("meta-specialist-root-cg-")
        or str(inner.get("schema_version", "")).startswith("meta-specialist-root-cg-")
    ):
        return _verify_cg_runtime_artifact(root, outer_manifest)
    return verify_submission_artifact(root)


def _matches_local_prefix(module: str) -> bool:
    return any(module == prefix or module.startswith(prefix + ".") for prefix in _LOCAL_IMPORT_PREFIXES)


def _collect_local_import_modules(source: str) -> list[str]:
    """Return every absolute-import module name (top-level or nested) that
    references a first-party submission prefix, via AST (not string search)."""
    tree = ast.parse(source)
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            modules.append(node.module)
    return [module for module in modules if _matches_local_prefix(module)]


def _module_resolves(package_root: Path, module: str) -> bool:
    """Check whether a dotted module resolves to a file under the archive.

    ``mage_submission_agents*`` resolves relative to the archive root;
    ``mage_ptcg*`` resolves relative to the archive's ``src/`` directory,
    matching the sys.path layout the Student runtime sets up before import.
    """
    parts = module.split(".")
    base = package_root if parts[0] == "mage_submission_agents" else package_root / "src"
    module_file = base.joinpath(*parts).with_suffix(".py")
    package_init = base.joinpath(*parts, "__init__.py")
    return module_file.is_file() or package_init.is_file()


def local_import_closure(package_root: Path) -> dict:
    """Verify every first-party import reachable from the shipped .py files
    resolves inside the archive, unless explicitly allowlisted as optional."""
    missing_required: list[dict[str, str]] = []
    allowed_optional: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    for _source, imported_module in OPTIONAL_LOCAL_IMPORTS:
        if any(marker in imported_module for marker in _FORBIDDEN_OPTIONAL_MODULE_MARKERS):
            raise ValueError(f"forbidden module cannot be allowlisted as optional: {imported_module}")

    for py_file in sorted(package_root.rglob("*.py")):
        relative = py_file.relative_to(package_root).as_posix()
        source = _read_bounded_file(
            py_file,
            limit=_MAX_MEMBER_BYTES,
            message=f"local import source exceeds byte bound: {relative}",
        ).decode("utf-8")
        for module in _collect_local_import_modules(source):
            key = (relative, module)
            if key in seen:
                continue
            seen.add(key)
            if _module_resolves(package_root, module):
                continue
            if key in OPTIONAL_LOCAL_IMPORTS:
                allowed_optional.append(
                    {"source": relative, "module": module, "reason": OPTIONAL_LOCAL_IMPORTS[key]}
                )
            else:
                missing_required.append({"source": relative, "module": module})

    return {
        "status": "PASS" if not missing_required else "FAIL",
        "missing_required": missing_required,
        "allowed_optional": allowed_optional,
    }


def _archive_only_student_smoke(archive_bytes: bytes, expected_hash: str, stress_games: int = 0) -> dict[str, object]:
    """Run the smoke test from already-verified immutable archive bytes."""
    import sys
    import os
    import subprocess
    import tempfile
    import tarfile
    import json

    # 仮想環境の python を特定
    venv_python = None
    for p in sys.path:
        if ".venv" in p:
            path_parts = Path(p).parts
            if "site-packages" in path_parts:
                idx = path_parts.index(".venv")
                if path_parts[0] == '/':
                    venv_dir = Path('/') / Path(*path_parts[1:idx+1])
                else:
                    venv_dir = Path(*path_parts[:idx+1])
                venv_python = venv_dir / "bin" / "python"
                break
    if not venv_python or not venv_python.exists():
        potential_venv = Path("/home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle/.venv/bin/python")
        if potential_venv.exists():
            venv_python = potential_venv
        else:
            venv_python = Path(sys.executable)

    with tempfile.TemporaryDirectory(prefix="student-preflight-") as temporary:
        sandbox = Path(temporary)
        extracted = sandbox / "package"
        extracted.mkdir()

        if not isinstance(archive_bytes, bytes) or len(archive_bytes) > _MAX_ARCHIVE_BYTES:
            raise ValueError("student smoke archive exceeds byte bound")
        # The caller has verified this exact immutable byte snapshot before the
        # smoke starts; never re-read the mutable artifact root here.
        with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as archive:
            archive.extractall(extracted, filter="data")

        # Task 7: ローカルimport閉包検査(展開済みアーカイブの内容に対して実行)
        import_closure_report = local_import_closure(extracted)

        temp_home = sandbox / "temp_home"
        temp_home.mkdir()

        clean_env = os.environ.copy()
        clean_env.pop("PYTHONPATH", None)
        clean_env["PYTHONNOUSERSITE"] = "1"
        clean_env["HOME"] = str(temp_home)

        main_path = extracted / "main.py"

        def run_isolated(script_content: str) -> dict:
            run_dir = sandbox / "run_cwd"
            if run_dir.exists():
                import shutil
                shutil.rmtree(run_dir)
            run_dir.mkdir()

            res = subprocess.run(
                [str(venv_python), "-I", "-c", script_content, str(extracted), str(ROOT), str(stress_games)],
                cwd=run_dir,
                env=clean_env,
                capture_output=True,
                text=True,
                check=False
            )
            if res.returncode != 0:
                raise ValueError(f"subprocess failed (exit={res.returncode}):\nSTDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}")
            try:
                lines = [l for l in res.stdout.splitlines() if l.strip().startswith("{")]
                if not lines:
                    raise ValueError(f"No JSON output. stdout:\n{res.stdout}\nstderr:\n{res.stderr}")
                return json.loads(lines[-1])
            except Exception as e:
                raise ValueError(f"JSON parse error: {e}. stdout:\n{res.stdout}")

        # Gate A: Exact callable loader
        gate_a_script = r'''
import sys
import json
from pathlib import Path
from kaggle_environments.agent import get_last_callable

artifact, repository = (Path(value).resolve() for value in sys.argv[1:3])
main_path = artifact / "main.py"
source = main_path.read_text(encoding="utf-8")

selected = get_last_callable(source, path=str(main_path))
if selected.__name__ != "agent":
    raise ValueError(f"Expected agent but got {selected.__name__}")

# Check provenance
runtime_main_module = sys.modules.get("runtime_main")
if not runtime_main_module:
    raise ValueError("runtime_main module was not imported")
runtime_main_path = Path(runtime_main_module.__file__).resolve()

package_root = Path(selected.__globals__["PACKAGE_ROOT"]).resolve()

if not runtime_main_path.is_relative_to(artifact):
    raise ValueError(f"runtime_main provenance leak: {runtime_main_path}")
if package_root != artifact:
    raise ValueError(f"package_root mismatch: {package_root} vs {artifact}")
if str(repository) in sys.path:
    raise ValueError("repository in sys.path")

print(json.dumps({"status": "PASS", "gate": "A"}))
'''
        report_a = run_isolated(gate_a_script)

        # Gate B: File path agent specification in CABT
        gate_b_script = r'''
import sys
import json
from pathlib import Path
from kaggle_environments import make

artifact, repository = (Path(value).resolve() for value in sys.argv[1:3])
main_path = artifact / "main.py"
deck = [int(line.strip()) for line in (artifact / "deck.csv").read_text(encoding="utf-8").splitlines() if line.strip()]

# Pure file-path execution on both seats without pre-importing runtime_main
env = make("cabt", configuration={"decks": [deck, deck], "episodeSteps": 10000})
env.run([str(main_path), str(main_path)])
statuses = [getattr(state, "status", None) for state in env.state]

# Outcome-level findings (bad statuses) are reported, not raised: the readiness
# computation in main() decides PREFLIGHT_ONLY vs READY_TO_SUBMIT from this data.
print(json.dumps({"status": "PASS", "gate": "B", "statuses": statuses}))
'''
        report_b = run_isolated(gate_b_script)

        # Gate C0: Student seat 0 vs Rule seat 1
        gate_c0_script = r'''
import sys
import json
from pathlib import Path
from kaggle_environments.agent import get_last_callable
from kaggle_environments import make

artifact, repository = (Path(value).resolve() for value in sys.argv[1:3])
main_path = artifact / "main.py"
source = main_path.read_text(encoding="utf-8")

selected = get_last_callable(source, path=str(main_path))
telemetry_fn = selected.__globals__["package_telemetry"]

# Provenance verification
runtime_main_module = sys.modules["runtime_main"]
runtime_main_path = Path(runtime_main_module.__file__).resolve()
package_root = Path(selected.__globals__["PACKAGE_ROOT"]).resolve()
model_module = sys.modules.get("mage_ptcg.student.model")
model_module_path = Path(model_module.__file__).resolve() if model_module else None

if not runtime_main_path.is_relative_to(artifact):
    raise ValueError(f"runtime_main provenance leak in C0: {runtime_main_path}")
if package_root != artifact:
    raise ValueError(f"package_root mismatch in C0: {package_root}")
if model_module_path and not model_module_path.is_relative_to(artifact):
    raise ValueError(f"model module provenance leak in C0: {model_module_path}")
if str(repository) in sys.path:
    raise ValueError("repository in sys.path in C0")

sys.path.insert(0, str(artifact))
import runtime_main
rule_agent = runtime_main.make_rule_agent()

deck = [int(line.strip()) for line in (artifact / "deck.csv").read_text(encoding="utf-8").splitlines() if line.strip()]

env = make("cabt", configuration={"decks": [deck, deck], "episodeSteps": 10000})
env.run([selected, rule_agent])
statuses = [getattr(state, "status", None) for state in env.state]

telemetry = telemetry_fn()
print(json.dumps({
    "status": "PASS",
    "gate": "C0",
    "model_loaded": telemetry["model_loaded"],
    "model_hash": telemetry["model_hash"],
    "fallback_count": telemetry["fallback_count"],
    "crash_count": telemetry["crash_count"],
    "invalid_count": telemetry["invalid_count"],
    "timeout_count": telemetry["timeout_count"],
    "inference_requested": telemetry["inference_requested"],
    "inference_completed": telemetry["inference_completed"],
    "student_selection_count": telemetry["student_selection_count"],
    "legal_decision_count": telemetry["legal_decision_count"],
    "legal_action_count": telemetry["legal_action_count"],
    "statuses": statuses,
}))
'''
        # Outcome-level findings (model not loaded, fallback/crash/invalid > 0)
        # are reported, not raised here: main()'s readiness computation decides.
        report_c0 = run_isolated(gate_c0_script)

        # Gate C1: Rule seat 0 vs Student seat 1
        gate_c1_script = r'''
import sys
import json
from pathlib import Path
from kaggle_environments.agent import get_last_callable
from kaggle_environments import make

artifact, repository = (Path(value).resolve() for value in sys.argv[1:3])
main_path = artifact / "main.py"
source = main_path.read_text(encoding="utf-8")

selected = get_last_callable(source, path=str(main_path))
telemetry_fn = selected.__globals__["package_telemetry"]

# Provenance verification
runtime_main_module = sys.modules["runtime_main"]
runtime_main_path = Path(runtime_main_module.__file__).resolve()
package_root = Path(selected.__globals__["PACKAGE_ROOT"]).resolve()
model_module = sys.modules.get("mage_ptcg.student.model")
model_module_path = Path(model_module.__file__).resolve() if model_module else None

if not runtime_main_path.is_relative_to(artifact):
    raise ValueError(f"runtime_main provenance leak in C1: {runtime_main_path}")
if package_root != artifact:
    raise ValueError(f"package_root mismatch in C1: {package_root}")
if model_module_path and not model_module_path.is_relative_to(artifact):
    raise ValueError(f"model module provenance leak in C1: {model_module_path}")
if str(repository) in sys.path:
    raise ValueError("repository in sys.path in C1")

sys.path.insert(0, str(artifact))
import runtime_main
rule_agent = runtime_main.make_rule_agent()

deck = [int(line.strip()) for line in (artifact / "deck.csv").read_text(encoding="utf-8").splitlines() if line.strip()]

env = make("cabt", configuration={"decks": [deck, deck], "episodeSteps": 10000})
env.run([rule_agent, selected])
statuses = [getattr(state, "status", None) for state in env.state]

telemetry = telemetry_fn()
print(json.dumps({
    "status": "PASS",
    "gate": "C1",
    "model_loaded": telemetry["model_loaded"],
    "model_hash": telemetry["model_hash"],
    "fallback_count": telemetry["fallback_count"],
    "crash_count": telemetry["crash_count"],
    "invalid_count": telemetry["invalid_count"],
    "timeout_count": telemetry["timeout_count"],
    "inference_requested": telemetry["inference_requested"],
    "inference_completed": telemetry["inference_completed"],
    "student_selection_count": telemetry["student_selection_count"],
    "legal_decision_count": telemetry["legal_decision_count"],
    "legal_action_count": telemetry["legal_action_count"],
    "statuses": statuses,
}))
'''
        # Outcome-level findings (model not loaded, fallback/crash/invalid > 0)
        # are reported, not raised here: main()'s readiness computation decides.
        report_c1 = run_isolated(gate_c1_script)

        # Stress Test
        stress_report = {}
        if stress_games > 0:
            stress_script = r'''
import sys
import json
from pathlib import Path
from kaggle_environments.agent import get_last_callable
from kaggle_environments import make

artifact, repository = (Path(value).resolve() for value in sys.argv[1:3])
stress_games = int(sys.argv[3])
main_path = artifact / "main.py"
source = main_path.read_text(encoding="utf-8")

sys.path.insert(0, str(artifact))
import runtime_main
rule_agent = runtime_main.make_rule_agent()

deck = [int(line.strip()) for line in (artifact / "deck.csv").read_text(encoding="utf-8").splitlines() if line.strip()]

fresh_games = int(stress_games * 0.8)
persistent_games = stress_games - fresh_games

if stress_games > 0:
    if fresh_games == 0:
        fresh_games = max(1, stress_games // 2)
        persistent_games = stress_games - fresh_games

fresh_seat0 = fresh_games // 2
fresh_seat1 = fresh_games - fresh_seat0

persist_seat0 = persistent_games // 2
persist_seat1 = persistent_games - persist_seat0

results = []
fresh_model_hashes = []

def run_game(agent_fn, is_student_seat0, is_fresh):
    telemetry_fn = agent_fn.__globals__["package_telemetry"]
    before = telemetry_fn().copy()

    env = make("cabt", configuration={"decks": [deck, deck], "episodeSteps": 10000})
    agents = [agent_fn, rule_agent] if is_student_seat0 else [rule_agent, agent_fn]
    env.run(agents)

    after = telemetry_fn()
    statuses = [getattr(state, "status", None) for state in env.state]

    keys = (
        "inference_requested",
        "inference_completed",
        "student_selection_count",
        "fallback_count",
        "invalid_count",
        "crash_count",
        "timeout_count",
        "legal_decision_count",
        "legal_action_count",
    )
    delta = {k: after[k] - before[k] for k in keys}
    if is_fresh:
        fresh_model_hashes.append(after["model_hash"])
    return delta, statuses

# 1. Fresh-load Seat 0
for _ in range(fresh_seat0):
    selected = get_last_callable(source, path=str(main_path))
    delta, statuses = run_game(selected, is_student_seat0=True, is_fresh=True)
    results.append({"mode": "fresh", "seat": 0, "delta": delta, "statuses": statuses})

# 2. Fresh-load Seat 1
for _ in range(fresh_seat1):
    selected = get_last_callable(source, path=str(main_path))
    delta, statuses = run_game(selected, is_student_seat0=False, is_fresh=True)
    results.append({"mode": "fresh", "seat": 1, "delta": delta, "statuses": statuses})

# 3. Persistent Seat 0
if persist_seat0 > 0:
    selected = get_last_callable(source, path=str(main_path))
    for index in range(persist_seat0):
        delta, statuses = run_game(selected, is_student_seat0=True, is_fresh=(index == 0))
        results.append({"mode": "persistent", "seat": 0, "delta": delta, "statuses": statuses})

# 4. Persistent Seat 1
if persist_seat1 > 0:
    selected = get_last_callable(source, path=str(main_path))
    for index in range(persist_seat1):
        delta, statuses = run_game(selected, is_student_seat0=False, is_fresh=(index == 0))
        results.append({"mode": "persistent", "seat": 1, "delta": delta, "statuses": statuses})

aggregated = {
    "inference_requested": 0,
    "inference_completed": 0,
    "student_selection_count": 0,
    "fallback_count": 0,
    "invalid_count": 0,
    "crash_count": 0,
    "timeout_count": 0,
    "legal_decision_count": 0,
    "legal_action_count": 0,
}
total_games = len(results)
terminal_errors = 0
all_statuses_done = True

for r in results:
    d = r["delta"]
    for k in aggregated:
        if k in d:
            aggregated[k] += d[k]
    if any(s in {"ERROR", "INVALID", "TIMEOUT"} for s in r["statuses"]):
        terminal_errors += 1
    if any(s != "DONE" for s in r["statuses"]):
        all_statuses_done = False

# Outcome-level findings (terminal errors, nonzero counters) are reported, not
# raised here: main()'s readiness computation decides PREFLIGHT_ONLY vs READY.
print(json.dumps({
    "status": "PASS",
    "gate": "STRESS",
    "total_games": total_games,
    "aggregated_telemetry": aggregated,
    "terminal_errors": terminal_errors,
    "all_statuses_done": all_statuses_done,
    "fresh_model_hashes": sorted(set(fresh_model_hashes)),
}))
'''
            stress_report = run_isolated(stress_script)

        return {
            "gate_a": report_a,
            "gate_b": report_b,
            "gate_c0": report_c0,
            "gate_c1": report_c1,
            "stress": stress_report,
            "local_import_closure": import_closure_report,
            "expected_model_hash": expected_hash,
            "model_loaded": report_c0.get("model_loaded"),
            "model_hash": report_c0.get("model_hash"),
            "fallback_count": report_c0.get("fallback_count", 0) + report_c1.get("fallback_count", 0),
            "invalid_count": report_c0.get("invalid_count", 0) + report_c1.get("invalid_count", 0),
            "crash_count": report_c0.get("crash_count", 0) + report_c1.get("crash_count", 0),
            "timeout_count": report_c0.get("timeout_count", 0) + report_c1.get("timeout_count", 0),
            "statuses": report_c0.get("statuses", []) + report_c1.get("statuses", []),
        }


def _privacy_scan(root: Path | _PackageSidecarSnapshot) -> int:
    if isinstance(root, _PackageSidecarSnapshot):
        return sum(
            _contains_secret(data)
            for name, data in root.root_member_bytes
            if name != "submission.tar.gz"
        ) + _contains_secret(root.sidecar_bytes)
    return sum(
        _contains_secret(
            _read_bounded_file(
                path,
                limit=_MAX_MEMBER_BYTES,
                message=f"privacy scan member exceeds byte bound: {path.name}",
            )
        )
        for path in root.rglob("*")
        if path.is_file() and path.name != "submission.tar.gz"
    )


def _load_snapshot_student_artifact(
    snapshot: _PackageSidecarSnapshot,
) -> tuple[object, dict[str, object]]:
    """Validate the model pair from a private copy of the verified bytes."""
    model_bytes = snapshot.member_bytes("models/student-v0.json")
    manifest_bytes = snapshot.member_bytes("student-model-manifest.json")
    with tempfile.TemporaryDirectory(prefix="student-snapshot-artifact-") as temporary:
        root = Path(temporary)
        model_path = root / "student-v0.json"
        manifest_path = root / "student-model-manifest.json"
        model_path.write_bytes(model_bytes)
        manifest_path.write_bytes(manifest_bytes)
        return load_validated_artifact(model_path, manifest_path)


def compute_readiness(
    check: dict[str, object], manifest: dict[str, object], stress_games_requested: int
) -> tuple[str, list[str]]:
    """Pure READY_TO_SUBMIT / PREFLIGHT_ONLY judgment from already-collected facts.

    Takes no subprocess or filesystem action; it only reads the ``check``
    (as produced by ``_archive_only_student_smoke``) and ``manifest`` dicts.
    This lets Task 11's regression tests exercise every blocker condition with
    hand-crafted data, without re-running kaggle_environments games.
    """
    blockers: list[str] = []
    if manifest.get("agent_kind") == "cg":
        if not isinstance(check, dict) or check.get("status") != "PASS":
            blockers.append("cg_runtime_status_not_PASS")
        contract = manifest.get("contract")
        if not isinstance(contract, dict) or contract.get("submission_method") == "UNKNOWN":
            blockers.append("remote_contract_confirmation_required")
        return ("READY_TO_SUBMIT" if not blockers else "PREFLIGHT_ONLY"), blockers
    if manifest.get("agent_kind") != "student":
        return "READY_TO_SUBMIT", blockers

    cabt = check.get("archive_only_actual_cabt")
    if not isinstance(cabt, dict):
        return "PREFLIGHT_ONLY", ["archive_only_actual_cabt_missing"]

    def gate(name: str) -> dict[str, object]:
        value = cabt.get(name)
        return value if isinstance(value, dict) else {}

    gate_a, gate_b, gate_c0, gate_c1 = gate("gate_a"), gate("gate_b"), gate("gate_c0"), gate("gate_c1")
    closure = cabt.get("local_import_closure")
    expected_hash = manifest.get("model_hash")

    for key, g in (("gate_a", gate_a), ("gate_b", gate_b), ("gate_c0", gate_c0), ("gate_c1", gate_c1)):
        if g.get("status") != "PASS":
            blockers.append(f"{key}_status_not_PASS")

    if gate_b.get("statuses") != ["DONE", "DONE"]:
        blockers.append("gate_b_statuses_not_DONE")
    if gate_c0.get("statuses") != ["DONE", "DONE"]:
        blockers.append("gate_c0_statuses_not_DONE")
    if gate_c1.get("statuses") != ["DONE", "DONE"]:
        blockers.append("gate_c1_statuses_not_DONE")

    for key, g in (("gate_c0", gate_c0), ("gate_c1", gate_c1)):
        if not g.get("model_loaded"):
            blockers.append(f"{key}_model_not_loaded")
        for counter in ("fallback_count", "invalid_count", "crash_count", "timeout_count"):
            if g.get(counter, 0) != 0:
                blockers.append(f"{key}_{counter}_nonzero")

    if not isinstance(expected_hash, str) or not expected_hash:
        blockers.append("expected_model_hash_unavailable")
    for key, g in (("gate_c0", gate_c0), ("gate_c1", gate_c1)):
        reported_hash = g.get("model_hash")
        if not reported_hash:
            blockers.append(f"{key}_model_hash_missing")
        elif reported_hash != expected_hash:
            blockers.append(f"{key}_model_hash_mismatch")

    if stress_games_requested >= 100:
        stress = cabt.get("stress")
        if not isinstance(stress, dict) or not stress:
            blockers.append("stress_report_missing")
        else:
            if stress.get("total_games", 0) != stress_games_requested:
                blockers.append("stress_total_games_mismatch")
            if stress.get("terminal_errors", 0) != 0:
                blockers.append("stress_terminal_errors_nonzero")
            if not stress.get("all_statuses_done"):
                blockers.append("stress_statuses_not_all_DONE")

            fresh_hashes = stress.get("fresh_model_hashes") or []
            if not fresh_hashes:
                blockers.append("stress_fresh_model_hash_missing")
            elif any(value != expected_hash for value in fresh_hashes):
                blockers.append("stress_fresh_model_hash_mismatch")

            agg = stress.get("aggregated_telemetry")
            agg = agg if isinstance(agg, dict) else {}
            for counter in ("fallback_count", "invalid_count", "crash_count", "timeout_count"):
                if agg.get(counter, 0) != 0:
                    blockers.append(f"stress_{counter}_nonzero")

            inference_requested = agg.get("inference_requested", 0)
            inference_completed = agg.get("inference_completed", 0)
            student_selection_count = agg.get("student_selection_count", 0)
            legal_decision_count = agg.get("legal_decision_count", 0)

            if inference_requested <= 0:
                blockers.append("inference_requested_not_positive")
            if inference_completed <= 0:
                blockers.append("inference_completed_not_positive")
            if student_selection_count <= 0:
                blockers.append("student_selection_count_not_positive")
            if legal_decision_count != inference_requested:
                blockers.append("legal_decision_count_mismatch")

            legal_action_rate = (
                legal_decision_count / inference_requested if inference_requested > 0 else 0.0
            )
            if legal_action_rate != 1.0:
                blockers.append("legal_action_rate_not_1")
    else:
        blockers.append("stress_games_below_100")

    if not isinstance(closure, dict) or closure.get("status") != "PASS":
        blockers.append("local_import_closure_not_PASS")

    privacy_violations = check.get("privacy_violations")
    if privacy_violations != 0:
        blockers.append("privacy_violations_nonzero")

    readiness = "READY_TO_SUBMIT" if not blockers else "PREFLIGHT_ONLY"
    return readiness, blockers


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--stress-games", type=int, default=0)
    args = parser.parse_args(argv)
    try:
        root = args.artifact.parent if args.artifact.name.endswith(".gz") else args.artifact
        manifest, snapshot = _load_kaggle_package_manifest_snapshot(root)
        if manifest["agent_kind"] in {"rule", "cg"}:
            check: dict[str, object] = _verify_without_package_sidecar(
                root,
                lambda candidate_root: _verify_package_runtime(candidate_root, manifest),
                snapshot=snapshot,
            )
        elif manifest["agent_kind"] == "student":
            check = _verify_without_package_sidecar(
                root,
                verify_student_submission,
                snapshot=snapshot,
            )
            _model, artifact_manifest = _load_snapshot_student_artifact(snapshot)
            if artifact_manifest.get("model_hash") != manifest.get("model_hash"):
                raise ValueError("model_hash_mismatch")
            check["archive_only_actual_cabt"] = _archive_only_student_smoke(
                snapshot.member_bytes("submission.tar.gz"),
                str(manifest["model_hash"]),
                stress_games=args.stress_games,
            )
            check["privacy_violations"] = _privacy_scan(snapshot)
            if check["privacy_violations"] != 0:
                raise ValueError("privacy_scan_failed")
        else:
            raise ValueError("agent_kind_invalid")

        readiness, blockers = compute_readiness(check, manifest, args.stress_games)
        # Re-run the closed-inventory verification after every long smoke step.
        # This binds the emitted result to the complete immutable snapshot and
        # also rejects any unmanifested path introduced while the sidecar was
        # restored for the smoke.
        _verify_without_package_sidecar(
            root,
            (lambda candidate_root: _verify_package_runtime(candidate_root, manifest))
            if manifest["agent_kind"] in {"rule", "cg"}
            else verify_student_submission,
            snapshot=snapshot,
        )
        result_json: dict[str, object] = {
            "status": "PASS",
            "check": check,
            "readiness": readiness,
            "readiness_blockers": blockers,
            "contract_confirmation": "CONTRACT_CONFIRMATION_REQUIRED" if manifest["contract"]["submission_method"] == "UNKNOWN" else "CONFIRMED",
        }
        print(json.dumps(result_json, sort_keys=True))
        return 0
    except Exception as exc:
        import traceback
        traceback.print_exc()
        print(json.dumps({"status": "BLOCKED", "reason": type(exc).__name__}))
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
