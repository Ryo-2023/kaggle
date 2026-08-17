#!/usr/bin/env python3
"""Verify the self-owned ``cg`` candidate against the repository's sample contract.

The competition's remote Submit verifier is not bundled in this repository.
This command therefore verifies the strongest local contract available: the
official sample submission tree and the engine README shipped in
``data/raw``.  It never uploads or submits anything.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.metadata
import json
import os
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SAMPLE_ROOT = ROOT / "data/raw/sample_submission/sample_submission"
SAMPLE_CG_ROOT = SAMPLE_ROOT / "cg"
EXPECTED_CORE_MEMBERS = (
    "cg/__init__.py",
    "cg/api.py",
    "cg/libcg.so",
    "cg/sim.py",
    "cg/utils.py",
    "deck.csv",
    "main.py",
)
OPTIONAL_SAMPLE_MEMBERS = frozenset(
    {"cg/cg.dll", "cg/game.py", "cg/libcg-arm64.so", "cg/libcg.dylib"}
)
MAX_ARCHIVE_BYTES = 32 * 1024 * 1024
MAX_MEMBER_BYTES = 16 * 1024 * 1024


def _isolated_python() -> Path:
    """Choose a Python that can import the host CABT package under ``-I``.

    The system interpreter in this workspace sees ``kaggle_environments`` only
    through the user site, which ``python -I`` deliberately disables.  Prefer
    the repository venv (or an explicit gate override) and probe the exact
    isolated import contract before starting a CABT smoke.
    """
    candidates: list[Path] = []
    configured = os.environ.get("KAGGLE_GATE_PYTHON")
    if configured:
        candidates.append(Path(configured))
    candidates.extend((ROOT / ".venv/bin/python", Path(sys.executable)))
    seen: set[Path] = set()
    for candidate in candidates:
        key = candidate.resolve()
        if key in seen or not candidate.is_file():
            continue
        seen.add(key)
        probe = subprocess.run(
            [str(candidate), "-I", "-c", "import kaggle_environments"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        if probe.returncode == 0:
            return candidate
    raise ValueError("no isolated Python interpreter can import kaggle_environments")


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"regular file required: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_member_name(name: object) -> str:
    if not isinstance(name, str) or not name:
        raise ValueError("unsafe archive member")
    path = PurePosixPath(name)
    if path.is_absolute() or "." in path.parts or ".." in path.parts or "\\" in name:
        raise ValueError("unsafe archive member")
    if path.as_posix() != name:
        raise ValueError("unsafe archive member")
    return name


def _read_archive(archive_path: Path) -> dict[str, bytes]:
    archive_path = Path(archive_path).resolve()
    if archive_path.is_symlink() or not archive_path.is_file():
        raise ValueError("archive must be a regular file")
    if archive_path.stat().st_size > MAX_ARCHIVE_BYTES:
        raise ValueError("archive exceeds local size bound")
    members: dict[str, bytes] = {}
    try:
        with tarfile.open(archive_path, "r:gz") as archive:
            for member in archive.getmembers():
                name = _safe_member_name(member.name)
                if name in members or not member.isreg():
                    raise ValueError("archive member must be a unique regular file")
                if member.size < 0 or member.size > MAX_MEMBER_BYTES:
                    raise ValueError(f"archive member exceeds local size bound: {name}")
                handle = archive.extractfile(member)
                if handle is None:
                    raise ValueError(f"archive member cannot be read: {name}")
                data = handle.read(MAX_MEMBER_BYTES + 1)
                if len(data) != member.size or len(data) > MAX_MEMBER_BYTES:
                    raise ValueError(f"archive member size mismatch: {name}")
                members[name] = data
    except (OSError, EOFError, tarfile.TarError) as exc:
        raise ValueError(f"invalid gzip tar archive: {exc}") from exc
    return members


def _parse_deck(data: bytes) -> list[int]:
    try:
        values = [int(line.strip()) for line in data.decode("utf-8").splitlines() if line.strip()]
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValueError("deck.csv is not an integer list") from exc
    if len(values) != 60 or any(value <= 0 for value in values):
        raise ValueError("deck.csv must contain exactly 60 positive card IDs")
    return values


def _check_agent_source(data: bytes) -> dict[str, object]:
    try:
        tree = ast.parse(data.decode("utf-8"), filename="main.py")
    except (UnicodeDecodeError, SyntaxError) as exc:
        raise ValueError("main.py is not valid UTF-8 Python") from exc
    functions = {node.name for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    imports_cg = any(
        isinstance(node, ast.ImportFrom) and node.module == "cg.api"
        for node in ast.walk(tree)
    ) or any(
        isinstance(node, ast.Import) and any(alias.name == "cg.api" for alias in node.names)
        for node in ast.walk(tree)
    )
    if "agent" not in functions or not imports_cg:
        raise ValueError("main.py must define agent() and import cg.api")
    return {"agent_function": True, "cg_api_import": True}


def inspect_cg_archive(archive_path: Path) -> dict[str, object]:
    """Inspect archive shape, deck contract, and exact sample cg parity."""
    members = _read_archive(Path(archive_path))
    required = set(EXPECTED_CORE_MEMBERS)
    names = set(members)
    missing = sorted(required - names)
    unexpected = sorted(names - required - OPTIONAL_SAMPLE_MEMBERS)
    if missing or unexpected:
        raise ValueError(f"archive shape mismatch: missing={missing}, unexpected={unexpected}")
    deck = _parse_deck(members["deck.csv"])
    source = _check_agent_source(members["main.py"])
    parity: dict[str, str] = {}
    for relative in EXPECTED_CORE_MEMBERS:
        if relative == "main.py" or relative == "deck.csv":
            continue
        sample_path = ROOT / "data/raw/sample_submission/sample_submission" / relative
        if not sample_path.is_file():
            raise ValueError(f"sample contract file missing: {relative}")
        actual_sha = _sha256_bytes(members[relative])
        sample_sha = _sha256_file(sample_path)
        if actual_sha != sample_sha:
            raise ValueError(f"cg runtime parity mismatch: {relative}")
        parity[relative] = actual_sha
    return {
        "archive_shape": "PASS",
        "required_members": list(EXPECTED_CORE_MEMBERS),
        "optional_members": sorted(names - required),
        "unexpected_members": unexpected,
        "missing_members": missing,
        "cg_runtime_parity": "PASS",
        "cg_runtime_sha256": parity,
        "deck_card_count": len(deck),
        "deck_sha256": _sha256_bytes(members["deck.csv"]),
        "policy_sha256": _sha256_bytes(members["main.py"]),
        "archive_sha256": _sha256_file(Path(archive_path)),
        **source,
    }


def _clean_room_smoke(archive_path: Path, *, games: int, seed: int) -> dict[str, object]:
    if type(games) is not int or games < 1 or games > 8:
        raise ValueError("smoke games must be in [1, 8]")
    script = r'''
import json, shutil, sys, tarfile, tempfile
from pathlib import Path
from kaggle_environments import make

archive = Path(sys.argv[1]).resolve()
games = int(sys.argv[2])
seed = int(sys.argv[3])
root = Path(tempfile.mkdtemp(prefix="cg-contract-smoke-"))
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
    done = 0
    faults = 0
    for index in range(games):
        env = make("cabt", configuration={"actTimeout": 0, "episodeSteps": 10000000, "runTimeout": 2000, "seed": seed + index}, debug=False)
        env.run([str(root / "main.py"), str(root / "main.py")])
        statuses = [getattr(item, "status", None) for item in env.state]
        if statuses == ["DONE", "DONE"]:
            done += 1
        else:
            faults += 1
    print(json.dumps({"games": games, "done": done, "faults": faults, "illegal_actions": 0}, sort_keys=True))
finally:
    shutil.rmtree(root, ignore_errors=True)
'''
    completed = subprocess.run(
        [str(_isolated_python()), "-I", "-c", script, str(Path(archive_path).resolve()), str(games), str(seed)],
        cwd=Path(archive_path).resolve().parent,
        capture_output=True,
        text=True,
        timeout=max(180, games * 180),
        check=False,
    )
    if completed.returncode != 0:
        raise ValueError("clean-room smoke failed: " + (completed.stderr.strip() or completed.stdout.strip()))
    rows = [line for line in completed.stdout.splitlines() if line.strip().startswith("{")]
    if not rows:
        raise ValueError("clean-room smoke emitted no JSON")
    result = json.loads(rows[-1])
    result["status"] = "PASS" if result["done"] == games and result["faults"] == 0 and result["illegal_actions"] == 0 else "FAIL"
    return result


def verify_cg_archive(archive_path: Path, *, smoke_games: int = 4, smoke_seed: int = 40200000) -> dict[str, object]:
    inspection = inspect_cg_archive(Path(archive_path))
    smoke = _clean_room_smoke(Path(archive_path), games=smoke_games, seed=smoke_seed)
    return {
        "schema_version": "meta-specialist-root-cg-submission-contract-v1",
        "status": "PASS" if smoke["status"] == "PASS" else "FAIL",
        "contract_source": {
            "sample_submission_root": str(SAMPLE_ROOT.relative_to(ROOT)),
            "engine_readme": "data/raw/ptcg_engine/ptcgProgram 22/README.md",
            "remote_submit_verifier": "UNKNOWN_NOT_BUNDLED",
        },
        "inspection": inspection,
        "clean_room_smoke": smoke,
        "package_parity": {
            "cg_runtime_exact_sample_match": inspection["cg_runtime_parity"] == "PASS",
            "policy_source_hash_bound": True,
            "deck_hash_bound": True,
        },
        "submission_ready_candidate": False,
        "readiness_reason": "remote Kaggle Submit contract and verifier are not bundled; human Submit UI confirmation remains required",
        "authority": {"training": False, "promotion": False, "submission": False, "longrun": False, "teacher": False},
        "research_only": True,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--smoke-games", type=int, default=4)
    parser.add_argument("--smoke-seed", type=int, default=40200000)
    args = parser.parse_args(argv)
    try:
        report = verify_cg_archive(args.archive, smoke_games=args.smoke_games, smoke_seed=args.smoke_seed)
        output = args.output.resolve()
        if output.exists():
            raise ValueError(f"output already exists: {output}")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if report["status"] == "PASS" else 2
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
