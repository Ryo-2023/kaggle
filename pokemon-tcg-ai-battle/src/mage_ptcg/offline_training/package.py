"""Separate Kaggle package for the neural Student, with clean-room verification.

This builder is deliberately independent of the approved Rule-v0 and linear
Student packages.  It never changes the source repository's default agent; only
the package's own ``main.py`` selects the neural Student, always with a Rule
Agent v0 fallback.  The archive excludes torch, training code, checkpoints, raw
datasets, private traces, and absolute paths.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any


PACKAGE_IDENTITY = "neural-student-v1-rule-v0-fallback"
ARCHIVE_NAME = "submission.tar.gz"
MANIFEST_NAME = "manifest.json"
MODEL_MEMBER = "models/neural-student-v1.json"

RUNTIME_PATHS: tuple[str, ...] = (
    "main.py",
    "runtime_main.py",
    "deck.csv",
    "mage_submission_agents/__init__.py",
    "mage_submission_agents/rule_agent.py",
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
    "src/mage_ptcg/offline_training/__init__.py",
    "src/mage_ptcg/offline_training/export.py",
    "src/mage_ptcg/offline_training/neural_runtime.py",
)

_MAIN_TEMPLATE = '''"""Kaggle entrypoint: neural Student v1 with deterministic Rule Agent v0 fallback.

Only this package selects the neural Student.  A missing, corrupt, or
incompatible model is indistinguishable from no model: deck registration always
succeeds and every decision falls back to Rule Agent v0.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REQUIRED = (
    "main.py",
    "runtime_main.py",
    "deck.csv",
    "models/neural-student-v1.json",
    "mage_submission_agents/rule_agent.py",
    "src/mage_ptcg/offline_training/neural_runtime.py",
)


def _is_package_root(candidate):
    return candidate.is_dir() and all((candidate / item).is_file() for item in _REQUIRED)


def _root_candidates():
    seen = set()

    def add(source_name):
        if not source_name or str(source_name).startswith("<"):
            return None
        candidate = Path(source_name).resolve().parent
        key = str(candidate)
        if key in seen:
            return None
        seen.add(key)
        return candidate

    if "__file__" in globals():
        candidate = add(__file__)
        if candidate is not None:
            yield candidate
    candidate = add(getattr(sys._getframe().f_code, "co_filename", ""))
    if candidate is not None:
        yield candidate
    # Kaggle's raw-exec source path is stable even though process cwd is not.
    # The fixed location remains a validated fallback, never an assumption.
    kaggle_candidate = Path("/kaggle_simulations/agent")
    if str(kaggle_candidate) not in seen:
        yield kaggle_candidate


_CHECKED_ROOTS = tuple(_root_candidates())
_ROOT = next((candidate for candidate in _CHECKED_ROOTS if _is_package_root(candidate)), None)
if _ROOT is None:
    raise RuntimeError(
        "submission package root could not be resolved; checked candidates: "
        + ", ".join(str(candidate) for candidate in _CHECKED_ROOTS)
    )
for _entry in (str(_ROOT), str(_ROOT / "src")):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import runtime_main
from mage_ptcg.offline_training.neural_runtime import NeuralRuntimePolicy

_MODEL_PATH = _ROOT / "models" / "neural-student-v1.json"
_DECK_PATH = _ROOT / "deck.csv"


def make_neural_agent(*, deck=None, deck_path=None, model_path=None):
    supply_deck = runtime_main._deck_supplier(deck, deck_path)
    fallback = runtime_main.make_rule_agent(deck=deck, deck_path=deck_path)
    policy = None
    try:
        policy = NeuralRuntimePolicy.load(model_path)
    except (ImportError, OSError, ValueError):
        policy = None

    def neural_agent(obs_dict):
        try:
            if runtime_main._selection_contract(obs_dict) is None:
                return supply_deck()
            if policy is not None:
                selection = policy.choose(obs_dict)
                if selection is not None:
                    return selection
        except Exception:
            pass
        return fallback(obs_dict)

    neural_agent.__name__ = "neural_student_v1_with_rule_v0_fallback"
    return neural_agent


_AGENT = make_neural_agent(deck_path=_DECK_PATH, model_path=_MODEL_PATH)


def agent(obs_dict):
    return _AGENT(obs_dict)
'''


class PackageError(ValueError):
    """Raised when the package cannot be built or fails clean-room verification."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_path(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if not name or path.is_absolute() or ".." in path.parts or path.as_posix() != name:
        raise PackageError(f"unsafe archive path: {name!r}")
    return path


def _contains_secret(data: bytes) -> bool:
    from scripts.build_submission import _contains_secret as check

    return bool(check(data))


def _collect_files(repository_root: Path, export_path: Path) -> list[tuple[str, bytes]]:
    from mage_ptcg.offline_training.export import load_export

    load_export(export_path)  # fail closed on a malformed export
    files: list[tuple[str, bytes]] = []
    for name in RUNTIME_PATHS:
        if name == "main.py":
            files.append((name, _MAIN_TEMPLATE.encode("utf-8")))
            continue
        if name == "runtime_main.py":
            source = repository_root / "main.py"
        elif name.startswith("mage_submission_agents/"):
            source = repository_root / "agents" / name.removeprefix("mage_submission_agents/")
        else:
            source = repository_root.joinpath(*_safe_path(name).parts)
        if not source.is_file() or source.is_symlink():
            raise PackageError(f"runtime source must be a regular file: {name}")
        data = source.read_bytes()
        data = data.replace(b"from agents", b"from mage_submission_agents")
        files.append((name, data))
    files.append((MODEL_MEMBER, export_path.read_bytes()))
    for name, data in files:
        _safe_path(name)
        if _contains_secret(data):
            raise PackageError(f"runtime source contains a secret marker: {name}")
    return files


def _write_tar(destination: Path, files: list[tuple[str, bytes]]) -> str:
    archive_path = destination / ARCHIVE_NAME
    with archive_path.open("xb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz:
            with tarfile.open(fileobj=gz, mode="w", format=tarfile.USTAR_FORMAT) as archive:
                for name, data in files:
                    info = tarfile.TarInfo(name)
                    info.size, info.mode, info.uid, info.gid, info.mtime = len(data), 0o644, 0, 0, 0
                    info.uname = info.gname = ""
                    archive.addfile(info, io.BytesIO(data))
    return _sha256(archive_path.read_bytes())


def build_package(
    *,
    export_path: str | Path,
    output_dir: str | Path,
    repository_root: str | Path,
    build_commit: str,
) -> dict[str, Any]:
    """Build the neural Student package and verify it in a clean room."""
    from mage_ptcg.offline_training.export import load_export

    repository_root = Path(repository_root)
    export_path = Path(export_path)
    document = load_export(export_path)
    destination = Path(output_dir)
    if destination.exists() and any(destination.iterdir()):
        raise PackageError("package output directory must be new or empty")
    destination.mkdir(parents=True, exist_ok=True)

    files = _collect_files(repository_root, export_path)
    for name, data in files:
        target = destination.joinpath(*_safe_path(name).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    archive_hash = _write_tar(destination, files)
    manifest = {
        "package_identity": PACKAGE_IDENTITY,
        "package_schema_version": 1,
        "build_commit": build_commit,
        "model_hash": document["model_hash"],
        "feature_schema_hash": document["feature_schema_hash"],
        "model_purpose": document.get("model_purpose"),
        "archive_sha256": archive_hash,
        "files": [
            {"path": name, "sha256": _sha256(data), "size": len(data)} for name, data in files
        ],
    }
    (destination / MANIFEST_NAME).write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    report = clean_room_verify(destination)
    manifest["clean_room"] = report
    return manifest


def measure_legality(agent, observations) -> dict[str, Any]:
    """Count legality over executed decisions; never fabricate an unmeasured rate.

    A choice is legal when it is a list of distinct in-range integer indices
    whose length satisfies the observation's min/max selection bounds.  With
    zero executed cases the rate is ``None`` (unmeasured), never 1.0.
    """
    executed = legal = illegal = exceptions = 0
    for observation in observations:
        executed += 1
        select = observation.get("select") if isinstance(observation, dict) else None
        options = select.get("option") if isinstance(select, dict) else None
        option_count = len(options) if isinstance(options, list) else 0
        minimum = select.get("minCount") if isinstance(select, dict) else None
        maximum = select.get("maxCount") if isinstance(select, dict) else None
        low = minimum if isinstance(minimum, int) else 1
        high = maximum if isinstance(maximum, int) else option_count
        try:
            choice = agent(observation)
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:  # counted, never propagated: this is a measurement
            exceptions += 1
            continue
        valid = (
            isinstance(choice, list)
            and all(isinstance(index, int) and 0 <= index < option_count for index in choice)
            and len(choice) == len(set(choice))
            and max(low, 0) <= len(choice) <= high
        )
        if valid:
            legal += 1
        else:
            illegal += 1
    return {
        "executed_cases": executed,
        "legal_cases": legal,
        "illegal_cases": illegal,
        "exception_cases": exceptions,
        "legal_action_rate": (legal / executed) if executed else None,
    }


_INFERENCE_SCRIPT = r'''
import json
import sys
from pathlib import Path
artifact = Path(sys.argv[1])
repo_flag = sys.argv[2]
sys.path.insert(0, str(artifact))
import main
# The source repository must not be importable from within the clean room.
assert repo_flag not in sys.path, "repository leaked onto sys.path"


def observation(options, min_count=1, max_count=1, hand_id=1):
    player = {"active": [], "asleep": False, "bench": [], "benchMax": 5, "burned": False,
              "confused": False, "deckCount": 53, "discard": [], "hand": [{"id": hand_id}],
              "handCount": 1, "paralyzed": False, "poisoned": False, "prize": [None] * 6}
    import copy
    return {"current": {"energyAttached": False, "firstPlayer": 0,
                        "players": [player, copy.deepcopy(player)], "result": -1, "retreated": False,
                        "stadium": [], "stadiumPlayed": False, "supporterPlayed": False,
                        "turn": 1, "turnActionCount": 0, "yourIndex": 0},
            "select": {"type": 0, "context": 0, "option": options,
                       "minCount": min_count, "maxCount": max_count}, "step": 1}


CATALOG = [{"type": 14}, {"type": 13, "attackId": 1}, {"type": 13, "attackId": 2}, {"type": 7, "index": 0}, {"type": 12}]
CASES = []
for variant in range(4):
    rotated = CATALOG[variant:] + CATALOG[:variant]
    CASES.append(observation(rotated[:2], hand_id=variant + 1))
    CASES.append(observation(rotated[:3], hand_id=variant + 1))

agent = main.make_neural_agent(deck=[1] * 60, model_path=artifact / "models/neural-student-v1.json")
fallback_agent = main.make_neural_agent(deck=[1] * 60, model_path=artifact / "missing.json")

# Measured legality over every executed decision (neural + fallback lanes).
executed = legal = illegal = exceptions = fallbacks = 0
for case_index, obs in enumerate(CASES):
    active, is_fallback = (fallback_agent, True) if case_index >= len(CASES) - 2 else (agent, False)
    executed += 1
    option_count = len(obs["select"]["option"])
    try:
        choice = active(obs)
    except Exception:
        exceptions += 1
        continue
    if is_fallback:
        fallbacks += 1
    if isinstance(choice, list) and choice and len(choice) == len(set(choice)) and all(
        isinstance(i, int) and 0 <= i < option_count for i in choice
    ) and len(choice) <= obs["select"]["maxCount"]:
        legal += 1
    else:
        illegal += 1

# Determinism on the first case.
assert agent(CASES[0]) == agent(CASES[0]), "nondeterministic choice"
# Modules must resolve inside the artifact, never the source repository.
import runtime_main
assert Path(runtime_main.__file__).resolve().is_relative_to(artifact), "runtime import escaped the artifact"
print("CLEAN_ROOM_RESULT " + json.dumps({
    "executed_cases": executed, "legal_cases": legal, "illegal_cases": illegal,
    "exception_cases": exceptions, "fallback_cases": fallbacks,
}, sort_keys=True))
print("CLEAN_ROOM_OK")
'''


def clean_room_verify(package_dir: str | Path) -> dict[str, Any]:
    """Extract only the tarball into a fresh temp dir outside the repo and verify it."""
    root = Path(package_dir)
    manifest = json.loads((root / MANIFEST_NAME).read_text(encoding="utf-8"))
    if manifest.get("package_identity") != PACKAGE_IDENTITY:
        raise PackageError("unexpected package identity")
    expected_members = [record["path"] for record in manifest["files"]]
    if manifest.get("archive_sha256") != _sha256((root / ARCHIVE_NAME).read_bytes()):
        raise PackageError("archive hash mismatch")
    repository_root = str(Path(__file__).resolve().parents[3])
    with tempfile.TemporaryDirectory(prefix="neural-student-v1-clean-room-") as temporary:
        extracted = Path(temporary) / "submission"
        extracted.mkdir()
        seen: set[str] = set()
        with tarfile.open(root / ARCHIVE_NAME, "r:gz") as archive:
            members = archive.getmembers()
            if [member.name for member in members] != expected_members:
                raise PackageError("archive member list does not match manifest")
            for member in members:
                _safe_path(member.name)
                if member.name in seen:
                    raise PackageError("duplicate archive member")
                seen.add(member.name)
                if member.issym() or member.islnk():
                    raise PackageError("archive contains a link member")
                if not member.isreg():
                    raise PackageError("archive member is not a regular file")
                if member.mode not in (0o644,) or member.uid != 0 or member.gid != 0 or member.mtime != 0:
                    raise PackageError("archive contains non-canonical member metadata")
                data = archive.extractfile(member)
                if data is None:
                    raise PackageError("archive member cannot be read")
                target = extracted.joinpath(*_safe_path(member.name).parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data.read())
        if not (extracted / "main.py").is_file() or not (extracted / "deck.csv").is_file():
            raise PackageError("archive is missing root main.py or deck.csv")
        model_path = extracted.joinpath(*_safe_path(MODEL_MEMBER).parts)
        if not model_path.is_file():
            raise PackageError("archive is missing the runtime model")
        from mage_ptcg.offline_training.export import load_export

        document = load_export(model_path)
        if document["model_hash"] != manifest["model_hash"]:
            raise PackageError("model hash mismatch in archive")
        if document["feature_schema_hash"] != manifest["feature_schema_hash"]:
            raise PackageError("feature schema mismatch in archive")
        result = subprocess.run(
            [sys.executable, "-I", "-c", _INFERENCE_SCRIPT, str(extracted), repository_root],
            cwd=temporary, capture_output=True, text=True, check=False,
        )
    if result.returncode != 0 or "CLEAN_ROOM_OK" not in result.stdout:
        raise PackageError(f"clean-room verification failed: {result.stderr or result.stdout}")
    counters: dict[str, Any] | None = None
    for line in result.stdout.splitlines():
        if line.startswith("CLEAN_ROOM_RESULT "):
            counters = json.loads(line[len("CLEAN_ROOM_RESULT "):])
    if counters is None:
        raise PackageError("clean-room verification produced no measured counters")
    executed = int(counters.get("executed_cases", 0))
    legal = int(counters.get("legal_cases", 0))
    if executed <= 0:
        # An unmeasured rate must never be reported as 1.0.
        raise PackageError("clean-room verification executed zero decision cases")
    if legal != executed:
        raise PackageError(
            "clean-room verification observed non-legal decisions: "
            f"legal={legal} executed={executed}"
        )
    return {
        "verified": True,
        "member_count": len(expected_members),
        "executed_cases": executed,
        "legal_cases": legal,
        "illegal_cases": int(counters.get("illegal_cases", 0)),
        "exception_cases": int(counters.get("exception_cases", 0)),
        "fallback_cases": int(counters.get("fallback_cases", 0)),
        "legal_action_rate": legal / executed,
        "archive_sha256": manifest["archive_sha256"],
    }


__all__ = [
    "ARCHIVE_NAME",
    "MANIFEST_NAME",
    "MODEL_MEMBER",
    "PACKAGE_IDENTITY",
    "PackageError",
    "build_package",
    "clean_room_verify",
    "measure_legality",
]
