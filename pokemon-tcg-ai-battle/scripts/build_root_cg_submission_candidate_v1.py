"""Build a self-owned ``cg.api`` candidate package and clean-room smoke it.

This is a research-only package builder for the current Rule-v0 root deck.  It
does not call Kaggle submission APIs and does not modify ``main.py``.  The
archive is intentionally limited to the runtime-closure files discovered by
``build_runtime_closure``; the official Rule-v0/student verifier is not used
because this candidate has a different, explicit native ``cg`` runtime shape.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import importlib.metadata
import io
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tarfile
import tempfile
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SOURCE_AGENT = ROOT / "src/mage_ptcg/meta_specialist/root_cg_submission_agent_v1.py"
SOURCE_DECK = ROOT / "deck.csv"
CG_ROOT = ROOT / "cg"
CG_RUNTIME_FILES = ("__init__.py", "api.py", "sim.py", "utils.py", "libcg.so")
SCHEMA = "meta-specialist-root-cg-submission-candidate-v1"


class CandidateBuildError(ValueError):
    """Raised when a candidate cannot be materialized or verified."""


def _sha256(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise CandidateBuildError(f"regular file required: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _deck_values(path: Path) -> list[int]:
    values = [int(line.strip()) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(values) != 60 or any(value <= 0 for value in values):
        raise CandidateBuildError(f"deck must contain 60 positive integer IDs: {path}")
    return values


def _stage_source(
    stage: Path,
    *,
    source_deck: Path = SOURCE_DECK,
    source_agent: Path = SOURCE_AGENT,
) -> None:
    stage.mkdir(parents=True, exist_ok=True)
    source_agent = Path(source_agent).resolve()
    if source_agent.is_symlink() or not source_agent.is_file():
        raise CandidateBuildError(f"regular source policy required: {source_agent}")
    shutil.copyfile(source_agent, stage / "main.py")
    source_deck = Path(source_deck).resolve()
    if source_deck.is_symlink() or not source_deck.is_file():
        raise CandidateBuildError(f"regular source deck required: {source_deck}")
    _deck_values(source_deck)
    shutil.copyfile(source_deck, stage / "deck.csv")
    cg_stage = stage / "cg"
    cg_stage.mkdir(parents=True, exist_ok=True)
    for name in CG_RUNTIME_FILES:
        source = CG_ROOT / name
        if source.is_symlink() or not source.is_file():
            raise CandidateBuildError(f"missing cg runtime file: {source}")
        shutil.copyfile(source, cg_stage / name)


def _tar_info(name: str, size: int) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name=name)
    info.size = size
    info.mode = 0o644
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mtime = 0
    return info


def _write_archive(package_root: Path, archive_path: Path, relative_paths: list[str]) -> str:
    if archive_path.exists() or archive_path.is_symlink():
        raise CandidateBuildError(f"archive already exists: {archive_path}")
    with archive_path.open("xb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz:
            with tarfile.open(fileobj=gz, mode="w", format=tarfile.USTAR_FORMAT) as archive:
                for relative in relative_paths:
                    path = PurePosixPath(relative)
                    if path.is_absolute() or ".." in path.parts or "\\" in relative:
                        raise CandidateBuildError(f"unsafe archive path: {relative}")
                    source = package_root.joinpath(*path.parts)
                    data = source.read_bytes()
                    archive.addfile(_tar_info(relative, len(data)), io.BytesIO(data))
    return _sha256(archive_path)


def _clean_room_smoke(archive_path: Path, *, games: int, seed: int) -> dict[str, Any]:
    if type(games) is not int or games < 1 or games > 8:
        raise CandidateBuildError("smoke games must be in [1, 8]")
    archive_path = Path(archive_path).resolve()
    smoke = r'''
import json, os, sys, tarfile, tempfile, time
from pathlib import Path
from kaggle_environments import make

archive = Path(sys.argv[1]).resolve()
games = int(sys.argv[2])
seed = int(sys.argv[3])
root = Path(tempfile.mkdtemp(prefix="root-cg-package-smoke-"))
try:
    with tarfile.open(archive, "r:gz") as handle:
        members = handle.getmembers()
        for member in members:
            if not member.isfile() or member.name.startswith("/") or ".." in Path(member.name).parts:
                raise RuntimeError("unsafe archive member")
        handle.extractall(root, members=members)
    sys.path.insert(0, str(root))
    import main
    deck = main.agent({"select": None})
    if not isinstance(deck, list) or len(deck) != 60 or any(type(value) is not int for value in deck):
        raise RuntimeError("deck registration contract failed")
    illegal = 0
    faults = 0
    done = 0
    steps_total = 0
    for index in range(games):
        env = make("cabt", configuration={"actTimeout": 0, "episodeSteps": 10000000, "runTimeout": 2000, "seed": seed + index}, debug=False)
        steps = env.run([str(root / "main.py"), str(root / "main.py")])
        statuses = [getattr(item, "status", None) for item in env.state]
        steps_total += len(steps)
        if statuses == ["DONE", "DONE"]:
            done += 1
        else:
            faults += 1
    print(json.dumps({"games": games, "done": done, "faults": faults, "illegal_actions": illegal, "steps_total": steps_total}, sort_keys=True))
finally:
    import shutil
    shutil.rmtree(root, ignore_errors=True)
'''
    completed = subprocess.run(
        [sys.executable, "-I", "-c", smoke, str(archive_path), str(games), str(seed)],
        cwd=archive_path.parent,
        capture_output=True,
        text=True,
        timeout=max(180, games * 180),
        check=False,
    )
    if completed.returncode != 0:
        raise CandidateBuildError("clean-room smoke failed: " + (completed.stderr.strip() or completed.stdout.strip()))
    lines = [line for line in completed.stdout.splitlines() if line.strip().startswith("{")]
    if not lines:
        raise CandidateBuildError("clean-room smoke emitted no JSON result")
    result = json.loads(lines[-1])
    result["status"] = "PASS" if result["faults"] == 0 and result["illegal_actions"] == 0 and result["done"] == games else "FAIL"
    return result


def build_candidate(
    output_dir: Path,
    *,
    source_deck: Path = SOURCE_DECK,
    source_agent: Path = SOURCE_AGENT,
    candidate_id: str = "root-cg-self-owned-v1",
    smoke_games: int = 2,
    smoke_seed: int = 40100000,
) -> dict[str, Any]:
    output_dir = Path(output_dir)
    if output_dir.exists() and (output_dir.is_symlink() or not output_dir.is_dir() or any(output_dir.iterdir())):
        raise CandidateBuildError("output directory must be absent or empty")
    output_dir.mkdir(parents=True, exist_ok=True)
    package_root = output_dir / "package"
    source_deck = Path(source_deck).resolve()
    source_agent = Path(source_agent).resolve()
    _stage_source(package_root, source_deck=source_deck, source_agent=source_agent)
    _deck_values(package_root / "deck.csv")

    from mage_ptcg.opponents.runtime_closure import build_runtime_closure, build_runtime_contract

    closure = build_runtime_closure(
        source_root=package_root,
        entrypoint="main.py:agent",
        agent_id="root-cg-submission-candidate-v1",
        scratch_root=output_dir / "closure-trace",
        timeout_seconds=120.0,
    )
    report = closure["report"]
    expected = {"main.py", "deck.csv", "cg/__init__.py", "cg/api.py", "cg/sim.py", "cg/utils.py", "cg/libcg.so"}
    if set(report["required"]) != expected or report["blocked"] or report["unresolved_imports"]["unknown_third_party"]:
        raise CandidateBuildError("runtime closure is not the expected self-owned cg set")

    relative_paths = sorted(expected)
    archive_path = output_dir / "submission.tar.gz"
    archive_sha = _write_archive(package_root, archive_path, relative_paths)
    runtime_contract = build_runtime_contract(
        python_version_required=f"{sys.version_info.major}.{sys.version_info.minor}",
        kaggle_environments_version=importlib.metadata.version("kaggle-environments"),
        cabt_version=importlib.metadata.version("kaggle-environments"),
        required_host_packages=["kaggle_environments"],
    )
    smoke = _clean_room_smoke(archive_path, games=smoke_games, seed=smoke_seed)
    files = {relative: {"sha256": _sha256(package_root / relative), "size": (package_root / relative).stat().st_size} for relative in relative_paths}
    manifest = {
        "schema_version": SCHEMA,
        "candidate_id": candidate_id,
        "policy_kind": "self_owned_public_cg_rule_overlay",
        "deck_sha256": _sha256(package_root / "deck.csv"),
        "source_deck_sha256": _sha256(source_deck),
        "deck_card_count": len(_deck_values(package_root / "deck.csv")),
        "policy_source_sha256": _sha256(source_agent),
        "runtime_closure": report,
        "runtime_contract": runtime_contract,
        "files": files,
        "archive": {"path": "submission.tar.gz", "sha256": archive_sha, "members": relative_paths},
        "smoke": smoke,
        "usage_boundary": "submission_compatible_local_eval_candidate_only",
        "authority": {"training": False, "promotion": False, "submission": False, "longrun": False, "teacher": False},
        "official_rule_v0_student_verifier": "not_applicable_different_cg_runtime_shape",
        "submission_ready": False,
    }
    manifest_path = output_dir / "candidate_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    manifest["manifest_sha256"] = _sha256(manifest_path)
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-deck", type=Path, default=SOURCE_DECK)
    parser.add_argument("--source-agent", type=Path, default=SOURCE_AGENT)
    parser.add_argument("--candidate-id", default="root-cg-self-owned-v1")
    parser.add_argument("--smoke-games", type=int, default=2)
    parser.add_argument("--smoke-seed", type=int, default=40100000)
    args = parser.parse_args(argv)
    try:
        result = build_candidate(
            args.output,
            source_deck=args.source_deck,
            source_agent=args.source_agent,
            candidate_id=args.candidate_id,
            smoke_games=args.smoke_games,
            smoke_seed=args.smoke_seed,
        )
    except (CandidateBuildError, OSError, ValueError) as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps({"status": "BUILT", "manifest": result}, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
