"""Materialize a standard local wrapper for a self-owned ``cg`` candidate.

This builder only copies an already-built, hash-bound cg candidate.  It never
uploads to Kaggle and it intentionally does not mark the package as remotely
submission-ready while the remote submission contract is unknown.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import subprocess
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CG_RUNTIME_MEMBERS = (
    "cg/__init__.py",
    "cg/api.py",
    "cg/libcg.so",
    "cg/sim.py",
    "cg/utils.py",
    "deck.csv",
    "main.py",
)
CG_SCHEMA_PREFIX = "meta-specialist-root-cg-"


class CgPackageBuildError(ValueError):
    """Raised when a cg candidate cannot be wrapped without weakening checks."""


def _sha256(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise CgPackageBuildError(f"regular file required: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_member(name: object) -> str:
    if not isinstance(name, str) or not name:
        raise CgPackageBuildError("cg runtime member path is invalid")
    path = PurePosixPath(name)
    if path.is_absolute() or "." in path.parts or ".." in path.parts or "\\" in name:
        raise CgPackageBuildError(f"unsafe cg runtime member path: {name}")
    if path.as_posix() != name:
        raise CgPackageBuildError(f"non-canonical cg runtime member path: {name}")
    return name


def _load_inner_manifest(candidate_root: Path) -> dict[str, Any]:
    manifest_path = candidate_root / "candidate_manifest.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise CgPackageBuildError(f"candidate_manifest.json is missing: {manifest_path}")
    try:
        value = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CgPackageBuildError("candidate_manifest.json is invalid JSON") from exc
    if not isinstance(value, dict):
        raise CgPackageBuildError("candidate manifest must be an object")
    schema = value.get("artifact_schema_version", value.get("schema_version"))
    if not isinstance(schema, str) or not schema.startswith(CG_SCHEMA_PREFIX):
        raise CgPackageBuildError("candidate manifest schema is not a cg schema")
    archive = value.get("archive")
    if not isinstance(archive, dict) or archive.get("path") != "submission.tar.gz":
        raise CgPackageBuildError("candidate archive metadata is invalid")
    members = archive.get("members")
    if tuple(_safe_member(member) for member in members or ()) != CG_RUNTIME_MEMBERS:
        raise CgPackageBuildError("candidate archive members are not canonical")
    files = value.get("files")
    if isinstance(files, dict):
        if set(files) != set(CG_RUNTIME_MEMBERS):
            raise CgPackageBuildError("candidate runtime file set is invalid")
    elif isinstance(files, list):
        if tuple(record.get("path") for record in files if isinstance(record, dict)) != CG_RUNTIME_MEMBERS:
            raise CgPackageBuildError("candidate runtime file order is invalid")
    else:
        raise CgPackageBuildError("candidate runtime file records are invalid")
    return value


def _locate_candidate(source_candidate: Path) -> tuple[Path, Path, Path]:
    source_candidate = source_candidate.resolve()
    if source_candidate.is_symlink() or not source_candidate.is_dir():
        raise CgPackageBuildError(f"candidate root must be a directory: {source_candidate}")
    if (source_candidate / "candidate_manifest.json").is_file():
        candidate_root = source_candidate
        package_root = source_candidate / "package"
    elif (source_candidate.parent / "candidate_manifest.json").is_file():
        candidate_root = source_candidate.parent
        package_root = source_candidate
    else:
        raise CgPackageBuildError("candidate_manifest.json is not adjacent to the candidate package")
    archive_path = candidate_root / "submission.tar.gz"
    if archive_path.is_symlink() or not archive_path.is_file():
        raise CgPackageBuildError(f"candidate archive is missing: {archive_path}")
    if package_root.is_symlink() or not package_root.is_dir():
        raise CgPackageBuildError(f"candidate package directory is missing: {package_root}")
    return candidate_root, package_root, archive_path


def _validate_runtime_files(package_root: Path, inner: dict[str, Any]) -> None:
    files = inner["files"]
    records: list[tuple[str, dict[str, Any]]] = []
    if isinstance(files, dict):
        records = [(name, record) for name, record in files.items()]
    else:
        records = [(record.get("path"), record) for record in files]
    for name, record in records:
        if name not in CG_RUNTIME_MEMBERS or not isinstance(record, dict):
            raise CgPackageBuildError("candidate runtime file record is invalid")
        path = package_root / name
        if record.get("sha256") != _sha256(path) or record.get("size") != path.stat().st_size:
            raise CgPackageBuildError(f"candidate runtime file hash mismatch: {name}")
    if not all((package_root / name).is_file() and not (package_root / name).is_symlink() for name in CG_RUNTIME_MEMBERS):
        raise CgPackageBuildError("candidate package is missing a runtime member")
    deck_values = [
        int(line.strip())
        for line in (package_root / "deck.csv").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if len(deck_values) != 60 or any(value <= 0 for value in deck_values):
        raise CgPackageBuildError("candidate deck must contain 60 positive integer IDs")


def build_cg_package(
    output_dir: Path,
    *,
    source_candidate: Path,
    contract: dict[str, str] | None = None,
    source_head: str | None = None,
    competition_slug: str = "pokemon-tcg-ai-battle",
) -> dict[str, Any]:
    """Wrap one existing cg candidate and return its bound inner manifest."""
    candidate_root, package_root, source_archive = _locate_candidate(Path(source_candidate))
    output = Path(output_dir).resolve()
    if output == candidate_root or output.is_relative_to(candidate_root):
        raise CgPackageBuildError("output directory must be outside the source candidate")
    if output.exists():
        if output.is_symlink() or not output.is_dir() or any(output.iterdir()):
            raise CgPackageBuildError(f"output directory must be absent or empty: {output}")
    output.mkdir(parents=True, exist_ok=True)

    inner = _load_inner_manifest(candidate_root)
    _validate_runtime_files(package_root, inner)
    archive_meta = inner["archive"]
    if archive_meta.get("sha256") != _sha256(source_archive):
        raise CgPackageBuildError("candidate archive hash does not match candidate_manifest.json")

    for name in CG_RUNTIME_MEMBERS:
        destination = output / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(package_root / name, destination)
    shutil.copyfile(source_archive, output / "submission.tar.gz")
    (output / "manifest.json").write_text(
        json.dumps(inner, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    if source_head is None:
        try:
            source_head = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
            ).strip()
        except (OSError, subprocess.CalledProcessError) as exc:
            raise CgPackageBuildError("could not determine source HEAD") from exc
    if not isinstance(source_head, str) or len(source_head) not in {40, 64} or any(
        character not in "0123456789abcdef" for character in source_head
    ):
        raise CgPackageBuildError("source_head must be lowercase git SHA")

    resolved_contract = contract or {
        "submission_method": "UNKNOWN",
        "archive_type": "UNKNOWN",
        "entrypoint": "main.py",
    }
    if set(resolved_contract) != {"submission_method", "archive_type", "entrypoint"}:
        raise CgPackageBuildError("cg contract fields are invalid")
    if resolved_contract["entrypoint"] != "main.py":
        raise CgPackageBuildError("cg contract entrypoint is invalid")
    outer = {
        "schema_version": "kaggle-agent-package-v1",
        "agent_kind": "cg",
        "competition_slug": competition_slug,
        "entrypoint": "main.py",
        "deck_hash": _sha256(output / "deck.csv"),
        "source_head": source_head,
        "private_artifacts_included": False,
        "contract": resolved_contract,
        "builder_result": inner,
    }
    (output / "kaggle-package-manifest.json").write_text(
        json.dumps(outer, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return inner


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-candidate", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        inner = build_cg_package(args.output, source_candidate=args.source_candidate)
    except (CgPackageBuildError, OSError, ValueError) as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps({"status": "BUILT", "builder_result": inner}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
