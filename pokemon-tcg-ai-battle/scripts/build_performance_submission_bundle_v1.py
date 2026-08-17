"""Performance-first submission bundle audit and archive-only smoke harness.

This module is deliberately separate from ``main.py`` and from research
checkpoint code.  It records the two candidate routes called out by the final
sprint:

* the currently wired Rule Agent v0 plus the repository root deck; and
* the coherent Wave6 V4 checkpoint plus its Archaludon subject deck.

Only the first route is currently packageable.  The V4 route is audited and
reported as *not* submission-ready when the production entrypoint, vocabulary,
or dependency closure are not proven.  No Kaggle API or submit operation is
present in this file.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
import tempfile
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
ROOT_DECK = REPOSITORY_ROOT / "deck.csv"
WAVE6_DEFAULT_CHECKPOINT = (
    REPOSITORY_ROOT
    / "runs/meta-specialist-v4-archaludon-longrun-wave6-current"
    / "archaludon-training-checkpoints/seed-0/best-recurrent-bc-v4.pt"
)
ARCHALUDON_DEFAULT_DECK = REPOSITORY_ROOT / "opponents/public_archaludon_cinderace_r7/deck.csv"
ROOT_DECK_QUALIFICATION = (
    REPOSITORY_ROOT
    / "runs/final-sprint-autonomous/submission-root-deck-qualification-v1/qualification.json"
)


class BundleBuildError(ValueError):
    """Raised when a candidate cannot be safely audited or packaged."""


def _sha256(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise BundleBuildError(f"source must be a regular file: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_deck(path: Path) -> list[int]:
    if path.is_symlink() or not path.is_file():
        raise BundleBuildError(f"deck must be a regular file: {path}")
    values: list[int] = []
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        value = raw.strip()
        if not value:
            continue
        try:
            parsed = int(value)
        except ValueError as exc:
            raise BundleBuildError(f"deck line {line_number} is not an integer") from exc
        if parsed <= 0:
            raise BundleBuildError(f"deck line {line_number} is not a positive card ID")
        values.append(parsed)
    if len(values) != 60:
        raise BundleBuildError(f"deck must contain exactly 60 cards, got {len(values)}")
    return values


def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=REPOSITORY_ROOT, capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def _repo_path(path: Path) -> Path:
    """Resolve user-facing relative paths from the repository, not process CWD."""
    candidate = Path(path)
    return candidate if candidate.is_absolute() else REPOSITORY_ROOT / candidate


def _verified_deck_qualification(
    deck_path: Path, qualification_path: Path
) -> dict[str, object]:
    """Require the exact bundle-allowed deck capability and real CABT evidence."""
    from mage_ptcg.meta_specialist.submission_deck_qualification_v1 import (
        SubmissionDeckQualificationV1Error,
        verify_submission_deck_qualification_v1,
    )

    qualification_path = _repo_path(Path(qualification_path))
    try:
        payload, qualified = verify_submission_deck_qualification_v1(
            qualification_path, REPOSITORY_ROOT
        )
    except (SubmissionDeckQualificationV1Error, OSError, ValueError) as exc:
        raise BundleBuildError(f"deck qualification verification failed: {exc}") from exc
    if (
        qualified.deck_file_sha256 != _sha256(deck_path)
        or qualified.usage_boundary != "bundle_allowed"
        or qualified.cabt_legality_status != "passed"
    ):
        raise BundleBuildError("deck qualification does not bind the requested deck")
    return {
        "file_sha256": _sha256(qualification_path),
        "qualification_sha256": payload["qualification_sha256"],
        "deck_identity": qualified.deck_identity,
    }


@dataclass(frozen=True, slots=True)
class SubmissionAudit:
    """A compact, source-bound audit record for one candidate pair."""

    candidate_id: str
    policy_route: str
    policy_sha256: str | None
    deck_path: str
    deck_sha256: str
    deck_card_count: int
    coherent_pair: bool
    submission_ready: bool
    blockers: tuple[str, ...]
    branch: str
    head: str
    dirty: bool

    def to_payload(self) -> dict[str, object]:
        return asdict(self) | {"blockers": list(self.blockers)}


def audit_current_submission() -> SubmissionAudit:
    """Audit the exact root route without importing any research checkpoint."""
    main_path = REPOSITORY_ROOT / "main.py"
    rule_path = REPOSITORY_ROOT / "agents/rule_agent.py"
    init_path = REPOSITORY_ROOT / "agents/__init__.py"
    blockers: list[str] = []
    for path in (main_path, ROOT_DECK, rule_path, init_path):
        if path.is_symlink() or not path.is_file():
            blockers.append(f"missing_or_nonregular:{path.relative_to(REPOSITORY_ROOT)}")
    deck = _read_deck(ROOT_DECK)
    try:
        _verified_deck_qualification(ROOT_DECK, ROOT_DECK_QUALIFICATION)
    except BundleBuildError as exc:
        blockers.append(f"deck_qualification_failed:{exc}")
    policy_payload = b"".join(path.read_bytes() for path in (main_path, init_path, rule_path))
    return SubmissionAudit(
        candidate_id="rule-v0-root-deck",
        policy_route="main._DEFAULT_AGENT -> make_rule_agent -> agents.choose_rule_indices",
        policy_sha256=hashlib.sha256(policy_payload).hexdigest(),
        deck_path="deck.csv",
        deck_sha256=_sha256(ROOT_DECK),
        deck_card_count=len(deck),
        coherent_pair=True,
        submission_ready=not blockers,
        blockers=tuple(blockers),
        branch=_git("branch", "--show-current"),
        head=_git("rev-parse", "HEAD"),
        dirty=bool(_git("status", "--porcelain=v1")),
    )


def audit_wave6_v4(
    *, checkpoint: Path = WAVE6_DEFAULT_CHECKPOINT,
    deck_path: Path = ARCHALUDON_DEFAULT_DECK,
) -> SubmissionAudit:
    """Audit Wave6+Archaludon identity and explicitly report missing runtime links."""
    checkpoint = _repo_path(Path(checkpoint))
    deck_path = _repo_path(Path(deck_path))
    deck = _read_deck(deck_path)
    checkpoint_sha = _sha256(checkpoint)
    expected_deck_sha = "42165967b565dd42ec426ecccfe79bfa7d72aa8306590e149dface0ee8bd530e"
    blockers = [
        "production_entrypoint_not_connected",
        "production_card_vocabulary_gate",
        "runtime_dependency_closure_unvendored",
    ]
    seed = checkpoint.parent.name.replace("seed-", "seed")
    candidate_id = f"wave6-v4-{seed}-archaludon"
    return SubmissionAudit(
        candidate_id=candidate_id,
        policy_route=(
            "research-only SpecialistNeuralPolicyV4Factory; "
            "root main._DEFAULT_AGENT does not load this checkpoint"
        ),
        policy_sha256=checkpoint_sha,
        deck_path=str(deck_path.relative_to(REPOSITORY_ROOT)),
        deck_sha256=_sha256(deck_path),
        deck_card_count=len(deck),
        coherent_pair=_sha256(deck_path) == expected_deck_sha,
        submission_ready=False,
        blockers=tuple(blockers),
        branch=_git("branch", "--show-current"),
        head=_git("rev-parse", "HEAD"),
        dirty=bool(_git("status", "--porcelain=v1")),
    )


def _copy_rule_runtime(destination: Path, deck_path: Path) -> None:
    """Stage only the Rule v0 runtime closure and one explicit deck file."""
    _read_deck(deck_path)
    for relative in ("main.py", "agents/__init__.py", "agents/rule_agent.py"):
        source = REPOSITORY_ROOT / relative
        if source.is_symlink() or not source.is_file():
            raise BundleBuildError(f"runtime source must be a regular file: {relative}")
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        target.chmod(0o644)
    shutil.copyfile(deck_path, destination / "deck.csv")
    (destination / "deck.csv").chmod(0o644)


def build_rule_v0_bundle(
    output_dir: Path,
    *,
    deck_path: Path = ROOT_DECK,
    archive_smoke_games: int = 0,
    archive_smoke_seed: int = 33000,
    deck_qualification: Path = ROOT_DECK_QUALIFICATION,
) -> dict[str, object]:
    """Build a standalone Rule v0 archive and run structural clean-room checks.

    ``archive_smoke_games=0`` keeps unit tests fast.  Set it to one or more to
    execute the archive-only CABT harness in a subprocess with the extracted
    archive as the only project code root.
    """
    output_dir = Path(output_dir)
    deck_path = _repo_path(Path(deck_path))
    qualification = _verified_deck_qualification(deck_path, deck_qualification)
    if output_dir.exists() and (output_dir.is_symlink() or not output_dir.is_dir() or any(output_dir.iterdir())):
        raise BundleBuildError("output directory must be absent or empty and non-symlink")
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        _copy_rule_runtime(output_dir, Path(deck_path))
        from scripts import build_submission as builder

        records = builder._runtime_file_records(output_dir)
        archive = builder.build_submission_archive(output_dir)
        deck_hash = records[1]["sha256"]
        manifest = {
            "agent_identity": "rule-agent-v0",
            "archive": archive,
            "artifact_schema_version": builder.ARTIFACT_SCHEMA_VERSION,
            "build_metadata": {
                "builder": "scripts/build_performance_submission_bundle_v1.py",
                "builder_schema_version": 1,
            },
            "content_hash": builder._content_hash(files=records, deck_sha256=deck_hash),
            "deck_identity": {"path": "deck.csv", "sha256": deck_hash},
            "files": records,
            "source_revision": builder._source_revision(),
        }
        builder._write_manifest(output_dir, manifest)
        verification = builder.verify_submission_artifact(output_dir)
        result: dict[str, object] = {
            "candidate_id": "rule-v0-root-deck" if Path(deck_path).resolve() == ROOT_DECK.resolve() else "rule-v0-custom-deck",
            "policy_sha256": hashlib.sha256(
                b"".join((output_dir / name).read_bytes() for name in ("main.py", "agents/__init__.py", "agents/rule_agent.py"))
            ).hexdigest(),
            "deck_sha256": deck_hash,
            "deck_card_count": len(_read_deck(output_dir / "deck.csv")),
            "deck_qualification_file_sha256": qualification["file_sha256"],
            "deck_qualification_sha256": qualification["qualification_sha256"],
            "qualified_deck_identity": qualification["deck_identity"],
            "archive_sha256": archive["sha256"],
            "archive_size_bytes": (output_dir / "submission.tar.gz").stat().st_size,
            "archive_members": records,
            "archive_only_structural": True,
            "clean_room": verification["clean_room"],
            "faults": 0,
            "legality": "representative_clean_room_pass",
        }
        if archive_smoke_games:
            result["archive_only_smoke"] = run_archive_only_smoke(
                output_dir / "submission.tar.gz", games=archive_smoke_games, seed=archive_smoke_seed
            )
        return result
    except Exception as exc:
        shutil.rmtree(output_dir, ignore_errors=True)
        if isinstance(exc, BundleBuildError):
            raise
        raise BundleBuildError(f"Rule v0 bundle build failed: {exc}") from exc


def _percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * quantile))))
    return ordered[index]


def run_archive_only_smoke(
    archive_path: Path,
    *,
    games: int = 1,
    seed: int = 33000,
    timeout_seconds: int = 120,
) -> dict[str, object]:
    """Run actual CABT games from extracted archive bytes in a clean subprocess."""
    if type(games) is not int or games < 1 or games > 32:
        raise BundleBuildError("archive-only smoke games must be in [1, 32]")
    archive_path = Path(archive_path)
    if archive_path.is_symlink() or not archive_path.is_file():
        raise BundleBuildError("archive must be a regular file")
    script = r'''
import json, os, sys, time
from pathlib import Path

archive_root = Path(sys.argv[1]).resolve()
games = int(sys.argv[2])
seed = int(sys.argv[3])
# Remove only repository source entries.  The virtualenv's site-packages path
# may itself contain the repository directory name; filtering by substring
# would accidentally remove ``kaggle_environments`` and make the clean-room
# smoke fail before the archive entrypoint is imported.
repo_root = Path(sys.argv[4]).resolve()
sys.path[:] = [
    p for p in sys.path
    if not (p and Path(p).resolve() in {repo_root, repo_root / "src"})
]
sys.path.insert(0, str(archive_root))
import main
from kaggle_environments import make

latencies = []
illegal_actions = 0

def legal(action, observation):
    select = observation.get("select") if isinstance(observation, dict) else None
    if select is None:
        return isinstance(action, list) and len(action) == 60 and all(type(x) is int for x in action)
    options = select.get("option")
    minimum, maximum = select.get("minCount"), select.get("maxCount")
    return (
        isinstance(action, list) and isinstance(options, list)
        and type(minimum) is int and type(maximum) is int
        and minimum <= len(action) <= maximum and len(action) == len(set(action))
        and all(type(i) is int and 0 <= i < len(options) for i in action)
    )

def timed(observation):
    global illegal_actions
    started = time.perf_counter()
    action = main.agent(observation)
    latencies.append((time.perf_counter() - started) * 1000.0)
    if not legal(action, observation):
        illegal_actions += 1
    return action

wins = losses = draws = faults = 0
statuses = []
for index in range(games):
    try:
        env = make("cabt", configuration={"actTimeout": 0, "episodeSteps": 10000000, "runTimeout": 2000, "seed": seed + index}, debug=False)
        steps = env.run([timed, timed])
        state = env.state
        status = [getattr(item, "status", None) for item in state]
        statuses.append(status)
        if status != ["DONE", "DONE"]:
            faults += 1
            continue
        reward = [getattr(item, "reward", None) for item in state]
        if reward[0] is None or reward[1] is None:
            draws += 1
        elif reward[0] > reward[1]:
            wins += 1
        elif reward[0] < reward[1]:
            losses += 1
        else:
            draws += 1
    except Exception as exc:
        faults += 1
        statuses.append(["EXCEPTION", type(exc).__name__])

print(json.dumps({
    "games": games, "wins": wins, "losses": losses, "draws": draws,
    "faults": faults, "illegal_actions": illegal_actions,
    "statuses": statuses, "latency_ms": {
        "count": len(latencies), "p50": (sorted(latencies)[int(round((len(latencies)-1)*0.50))] if latencies else None),
        "p95": (sorted(latencies)[int(round((len(latencies)-1)*0.95))] if latencies else None),
        "p99": (sorted(latencies)[int(round((len(latencies)-1)*0.99))] if latencies else None),
        "max": max(latencies) if latencies else None,
    },
    "archive_only": True,
    "external_project_code_root": str(archive_root),
}, sort_keys=True))
'''
    with tempfile.TemporaryDirectory(prefix="performance-bundle-smoke-") as temporary:
        temp = Path(temporary)
        from scripts.build_submission import extract_submission_archive, validate_submission_archive

        extracted = temp / "agent"
        extract_submission_archive(archive_path, extracted)
        validate_submission_archive(archive_path)
        result = subprocess.run(
            [
                sys.executable, "-I", "-c", script,
                str(extracted), str(games), str(seed), str(REPOSITORY_ROOT),
            ],
            cwd=temp,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
        if result.returncode != 0:
            raise BundleBuildError(
                "archive-only smoke subprocess failed: "
                + (result.stderr.strip() or result.stdout.strip())
            )
        lines = [line for line in result.stdout.splitlines() if line.strip()]
        try:
            payload = json.loads(lines[-1])
        except (IndexError, json.JSONDecodeError) as exc:
            raise BundleBuildError("archive-only smoke emitted no JSON result") from exc
    payload["archive_sha256"] = _sha256(archive_path)
    payload["legality"] = "pass" if payload["illegal_actions"] == 0 else "fail"
    payload["status"] = "PASS" if payload["faults"] == 0 and payload["illegal_actions"] == 0 else "FAIL"
    return payload


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("audit-current")
    wave = sub.add_parser("audit-wave6")
    wave.add_argument("--checkpoint", type=Path, default=WAVE6_DEFAULT_CHECKPOINT)
    wave.add_argument("--deck", type=Path, default=ARCHALUDON_DEFAULT_DECK)
    build = sub.add_parser("build-rule")
    build.add_argument("--output-dir", type=Path, required=True)
    build.add_argument("--deck", type=Path, default=ROOT_DECK)
    build.add_argument("--archive-smoke-games", type=int, default=0)
    build.add_argument("--archive-smoke-seed", type=int, default=33000)
    smoke = sub.add_parser("archive-smoke")
    smoke.add_argument("--archive", type=Path, required=True)
    smoke.add_argument("--games", type=int, default=1)
    smoke.add_argument("--seed", type=int, default=33000)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "audit-current":
            payload = audit_current_submission().to_payload()
        elif args.command == "audit-wave6":
            payload = audit_wave6_v4(checkpoint=args.checkpoint, deck_path=args.deck).to_payload()
        elif args.command == "build-rule":
            payload = build_rule_v0_bundle(
                args.output_dir, deck_path=args.deck,
                archive_smoke_games=args.archive_smoke_games,
                archive_smoke_seed=args.archive_smoke_seed,
            )
        else:
            payload = run_archive_only_smoke(args.archive, games=args.games, seed=args.seed)
    except (BundleBuildError, OSError, subprocess.SubprocessError) as exc:
        print(json.dumps({"status": "ERROR", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
