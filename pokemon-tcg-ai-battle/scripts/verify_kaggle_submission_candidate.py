#!/usr/bin/env python3
"""Mandatory Kaggle Submission Safety Gate Verifier.

Extracts a submission.tar.gz to a temporary directory, runs strict validation
gates (G1-G6), and outputs a validation manifest JSON.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

EXPECTED_PYTHON = (3, 11)
EXPECTED_KAGGLE_ENVIRONMENTS_VERSION = "1.32.0"
MINIMUM_SMOKE_GAMES = 20
MAX_ARCHIVE_BYTES = 100 * 1024 * 1024
MAX_MEMBER_BYTES = 20 * 1024 * 1024
MAX_TOTAL_MEMBER_BYTES = 100 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 512

_RUNTIME_PROBE = r'''
import importlib.metadata
import json
import platform

print(json.dumps({
    "marker": "RUNTIME_PROBE",
    "python_version": platform.python_version(),
    "kaggle_environments_version": importlib.metadata.version("kaggle-environments"),
}, sort_keys=True))
'''

def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()

def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def _verification_script_sha256() -> str:
    """Return the immutable bytes identity of this verifier."""
    return _sha256(Path(__file__).resolve())

def _contains_secret(data: bytes) -> bool:
    # Standard secret scan markers
    for pattern in (b"kaggle.json", b"KAGGLE_KEY", b"KAGGLE_USERNAME", b"api_key"):
        if pattern in data:
            return True
    return False


def _probe_runtime(python_executable: Path) -> dict[str, str]:
    if not python_executable.is_file():
        raise FileNotFoundError(f"Gate Python executable not found: {python_executable}")
    completed = subprocess.run(
        [str(python_executable), "-I", "-c", _RUNTIME_PROBE],
        capture_output=True,
        text=True,
        shell=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "Gate runtime probe failed: " + (completed.stderr.strip() or completed.stdout.strip())
        )
    try:
        report = json.loads(completed.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        raise RuntimeError("Gate runtime probe returned invalid output") from exc
    if report.get("marker") != "RUNTIME_PROBE":
        raise RuntimeError("Gate runtime probe marker is missing")
    python_version = str(report.get("python_version", ""))
    try:
        major_minor = tuple(int(part) for part in python_version.split(".")[:2])
    except ValueError as exc:
        raise RuntimeError(f"Invalid gate Python version: {python_version!r}") from exc
    if major_minor != EXPECTED_PYTHON:
        raise RuntimeError(
            f"Gate requires Python 3.11, got {python_version or 'UNKNOWN'}"
        )
    kaggle_version = str(report.get("kaggle_environments_version", ""))
    if kaggle_version != EXPECTED_KAGGLE_ENVIRONMENTS_VERSION:
        raise RuntimeError(
            "Gate requires kaggle-environments 1.32.0, "
            f"got {kaggle_version or 'UNKNOWN'}"
        )
    return {
        "python_version": python_version,
        "kaggle_environments_version": kaggle_version,
    }


def verify_archive(
    archive_path: Path,
    competition: str,
    smoke_games: int,
    smoke_seed: int,
    output_path: Path,
    *,
    python_executable: str | Path | None = None,
) -> int:
    archive_path = archive_path.resolve()
    if not archive_path.is_file():
        raise FileNotFoundError(f"Archive not found: {archive_path}")
    if smoke_games < MINIMUM_SMOKE_GAMES:
        raise ValueError(
            f"Safety Gate requires at least {MINIMUM_SMOKE_GAMES} smoke games, got {smoke_games}"
        )
    # Keep a venv launcher path intact: resolving its symlink would bypass the
    # venv and silently probe the base interpreter without its dependencies.
    runtime_python = Path(python_executable or sys.executable).expanduser()
    if not runtime_python.is_absolute():
        runtime_python = Path.cwd() / runtime_python
    runtime_report = _probe_runtime(runtime_python)

    # Calculate initial hash
    initial_hash = _sha256(archive_path)
    archive_size = archive_path.stat().st_size
    if archive_size > MAX_ARCHIVE_BYTES:
        raise ValueError(f"Archive too large: {archive_size} bytes")

    print(f"[*] Starting Safety Gate verification for {archive_path.name}")
    print(f"[*] Archive SHA-256: {initial_hash}")

    temp_dir = Path(tempfile.mkdtemp(prefix="submission_verify_"))
    archive_dir = temp_dir / "kaggle_simulations" / "agent"
    runtime_cwd = temp_dir / "kaggle" / "working"
    archive_dir.mkdir(parents=True)
    runtime_cwd.mkdir(parents=True)
    if archive_dir.resolve() == runtime_cwd.resolve():
        raise RuntimeError("code root and working directory must be distinct")
    try:
        # ---------------------------------------------------------
        # G1: Archive Integrity
        # ---------------------------------------------------------
        print("[*] G1: Checking Archive Integrity...")
        try:
            with tarfile.open(archive_path, "r:gz") as tar:
                members = tar.getmembers()
                if len(members) > MAX_ARCHIVE_MEMBERS:
                    raise ValueError(f"Too many archive members: {len(members)}")
                total_member_bytes = 0
                names = set()
                for member in members:
                    if member.name in names:
                        raise ValueError(f"Duplicate member found: {member.name}")
                    names.add(member.name)

                    path = PurePosixPath(member.name)
                    if (
                        not member.name
                        or path.is_absolute()
                        or ".." in path.parts
                        or "\\" in member.name
                        or path.as_posix() != member.name
                    ):
                        raise ValueError(f"Traversal attempt detected: {member.name}")
                    if member.issym() or member.islnk():
                        raise ValueError(f"Link members are forbidden: {member.name}")
                    if not member.isreg():
                        raise ValueError(f"Only regular file members are allowed: {member.name}")
                    if member.size > MAX_MEMBER_BYTES:
                        raise ValueError(f"Member too large: {member.name} ({member.size} bytes)")
                    total_member_bytes += member.size
                    if total_member_bytes > MAX_TOTAL_MEMBER_BYTES:
                        raise ValueError(
                            f"Archive expands beyond limit: {total_member_bytes} bytes"
                        )

                for member in members:
                    source = tar.extractfile(member)
                    if source is None:
                        raise ValueError(f"Archive member cannot be read: {member.name}")
                    target = archive_dir.joinpath(*PurePosixPath(member.name).parts)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with target.open("xb") as destination:
                        shutil.copyfileobj(source, destination)
        except Exception as e:
            print(f"[!] G1 Failed: {e}", file=sys.stderr)
            raise

        # Check required files
        required_files = ("main.py", "runtime_main.py", "deck.csv", "models/neural-student-v1.json")
        for f in required_files:
            if not (archive_dir / f).is_file():
                raise FileNotFoundError(f"G1 Failed: Missing required file in archive: {f}")

        # Secret scan
        for root, _, files in os.walk(archive_dir):
            for file in files:
                p = Path(root) / file
                data = p.read_bytes()
                if _contains_secret(data):
                    raise ValueError(f"G1 Failed: Secret pattern detected in member: {p.relative_to(archive_dir)}")

        # Collect file hashes
        file_hashes = {}
        for root, _, files in os.walk(archive_dir):
            for file in files:
                p = Path(root) / file
                rel = p.relative_to(archive_dir).as_posix()
                file_hashes[rel] = {
                    "sha256": _sha256(p),
                    "size": p.stat().st_size
                }

        entrypoint_sha256 = file_hashes["main.py"]["sha256"]
        runtime_main_sha256 = file_hashes["runtime_main.py"]["sha256"]
        model_sha256 = file_hashes["models/neural-student-v1.json"]["sha256"]

        # Parse semantic model hash from the model json
        model_json = json.loads((archive_dir / "models/neural-student-v1.json").read_text("utf-8"))
        semantic_model_hash = model_json.get("model_hash")
        if not semantic_model_hash:
            raise ValueError("G1 Failed: model_hash missing from neural-student-v1.json")

        print("[+] G1 PASS")

        # ---------------------------------------------------------
        # G2: Dependency Closure / Local Import
        # ---------------------------------------------------------
        print("[*] G2: Checking Dependency Closure...")
        # We run Python in a subprocess with PYTHONPATH cleared and cwd at the archive root.
        # This guarantees it doesn't import from workspace 'src' or 'runs'.
        g2_code = """
import sys
import os
sys.path.insert(0, os.environ["SUBMISSION_AGENT_ROOT"])
print("sys.path:", sys.path)
try:
    import main
    print("[+] Import of main inside clean room successful.")
except Exception as e:
    print(f"[!] Import failed: {e}")
    sys.exit(1)
"""
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        env["PYTHONNOUSERSITE"] = "1"
        env["SUBMISSION_AGENT_ROOT"] = str(archive_dir)

        proc = subprocess.run(
            [str(runtime_python), "-I", "-c", g2_code],
            cwd=archive_dir,
            env=env,
            capture_output=True,
            text=True
        )
        if proc.returncode != 0:
            print("[!] G2 Failed: Clean room import check failed.", file=sys.stderr)
            print(f"Stdout: {proc.stdout}", file=sys.stderr)
            print(f"Stderr: {proc.stderr}", file=sys.stderr)
            raise RuntimeError(f"G2 Failed: {proc.stderr.strip()}")

        print("[+] G2 PASS")

        # ---------------------------------------------------------
        # G3: Kaggle Raw Exec
        # ---------------------------------------------------------
        print("[*] G3: Checking Kaggle Raw Exec and get_last_callable...")
        g3_code = """
import sys
import os
from kaggle_environments.agent import get_last_callable

main_path = os.path.join(os.environ["SUBMISSION_AGENT_ROOT"], "main.py")
if os.path.abspath(os.getcwd()) == os.path.abspath(os.environ["SUBMISSION_AGENT_ROOT"]):
    raise RuntimeError("G3 cwd must be separate from the archive root")
if os.listdir(os.getcwd()):
    raise RuntimeError("G3 working directory must start empty")
raw_agent = open(main_path, "r", encoding="utf-8").read()

# Compile and exec without __file__ in globals
code_object = compile(raw_agent, main_path, "exec")
env = {"__name__": "__main__"}
try:
    exec(code_object, env)
    print("[+] exec compilation successful without __file__")
except Exception as e:
    print(f"[!] exec failed: {e}")
    sys.exit(2)

# Verify get_last_callable
try:
    callable_agent = get_last_callable(raw_agent, path=main_path)
    if not callable(callable_agent):
        raise ValueError("Returned agent is not callable")
    print("[+] get_last_callable parsed agent successfully")
except Exception as e:
    print(f"[!] get_last_callable failed: {e}")
    sys.exit(3)
"""
        proc = subprocess.run(
            [str(runtime_python), "-I", "-c", g3_code],
            cwd=runtime_cwd,
            env=env,
            capture_output=True,
            text=True
        )
        if proc.returncode != 0:
            print("[!] G3 Failed: Kaggle raw exec / get_last_callable mismatch.", file=sys.stderr)
            print(f"Stdout: {proc.stdout}", file=sys.stderr)
            print(f"Stderr: {proc.stderr}", file=sys.stderr)
            raise RuntimeError(f"G3 Failed: {proc.stderr.strip() or proc.stdout.strip()}")

        print("[+] G3 PASS")

        # ---------------------------------------------------------
        # G4: Initial Lifecycle (Step 0)
        # ---------------------------------------------------------
        print("[*] G4: Checking Initial Lifecycle (Step 0 Observation)...")
        g4_code = """
import sys
import os
import json
from kaggle_environments.agent import get_last_callable

main_path = os.path.join(os.environ["SUBMISSION_AGENT_ROOT"], "main.py")
if os.path.abspath(os.getcwd()) == os.path.abspath(os.environ["SUBMISSION_AGENT_ROOT"]):
    raise RuntimeError("G4 cwd must be separate from the archive root")
if os.listdir(os.getcwd()):
    raise RuntimeError("G4 working directory must start empty")
raw_agent = open(main_path, "r", encoding="utf-8").read()
callable_agent = get_last_callable(raw_agent, path=main_path)

obs = {
  "current": None,
  "logs": [],
  "remainingOverageTime": 600,
  "search_begin_input": None,
  "select": None,
  "step": 0
}

try:
    action = callable_agent(obs)
    if (
        not isinstance(action, list)
        or len(action) != 60
        or any(type(card_id) is not int for card_id in action)
    ):
        raise ValueError(f"Step 0 action must be a list of 60 cards, got: {action}")
    print("[+] Step 0 deck registration successful.")
except Exception as e:
    print(f"[!] Step 0 processing failed: {e}")
    sys.exit(4)
"""
        proc = subprocess.run(
            [str(runtime_python), "-I", "-c", g4_code],
            cwd=runtime_cwd,
            env=env,
            capture_output=True,
            text=True
        )
        if proc.returncode != 0:
            print("[!] G4 Failed: Initial observation check failed.", file=sys.stderr)
            print(f"Stdout: {proc.stdout}", file=sys.stderr)
            print(f"Stderr: {proc.stderr}", file=sys.stderr)
            raise RuntimeError(f"G4 Failed: {proc.stderr.strip() or proc.stdout.strip()}")

        print("[+] G4 PASS")

        # ---------------------------------------------------------
        # G5: Local Validation Episode (using kaggle_environments)
        # ---------------------------------------------------------
        print("[*] G5: Checking Local Validation Episode (Simulating Episode)...")
        g5_code = """
import sys
import os
import json

# Clean path to isolate extracted packages (G5 clean room)
sys.path = [p for p in sys.path if "pokemon-tcg-ai-battle" not in p or ".venv" in p]
agent_root = os.environ["SUBMISSION_AGENT_ROOT"]
if os.path.abspath(os.getcwd()) == os.path.abspath(agent_root):
    raise RuntimeError("G5 cwd must be separate from the archive root")
if os.listdir(os.getcwd()):
    raise RuntimeError("G5 working directory must start empty")
sys.path.insert(0, agent_root)

from kaggle_environments import make

env_make = make(
    "cabt",
    configuration={
        "actTimeout": 0,
        "episodeSteps": 10000000,
        "runTimeout": 2000,
        "seed": 0,
    },
    debug=True,
)

main_path = os.path.join(agent_root, "main.py")
try:
    steps = env_make.run([main_path, main_path])
    agent0_status = env_make.state[0].status
    agent1_status = env_make.state[1].status
    print(f"Episode completed. Steps: {len(steps)}, Statuses: {agent0_status}, {agent1_status}")
    if agent0_status not in ("DONE",) or agent1_status not in ("DONE",):
        raise ValueError(f"Agent validation error: statuses: {agent0_status}, {agent1_status}")

    # Save the output of statuses and steps
    result = {
        "status": "SUCCESS",
        "steps": len(steps),
        "agent_statuses": [agent0_status, agent1_status]
    }
    with open("g5_output.json", "w") as f:
        json.dump(result, f)
except Exception as e:
    print(f"[!] Episode execution crashed: {e}")
    sys.exit(5)
"""
        proc = subprocess.run(
            [str(runtime_python), "-I", "-c", g5_code],
            cwd=runtime_cwd,
            env=env,
            capture_output=True,
            text=True
        )
        if proc.returncode != 0:
            print("[!] G5 Failed: Validation Episode simulation crashed.", file=sys.stderr)
            print(f"Stdout: {proc.stdout}", file=sys.stderr)
            print(f"Stderr: {proc.stderr}", file=sys.stderr)
            # Find the actual error lines to display in the main error report
            crash_err = ""
            for line in (proc.stdout + proc.stderr).splitlines():
                if "crashed" in line or "error" in line.lower() or "exception" in line.lower() or "not found" in line.lower():
                    crash_err += line + "\n"
            raise RuntimeError(f"G5 Failed (code {proc.returncode}):\n{crash_err or proc.stderr.strip() or proc.stdout.strip()}")

        # Read G5 result
        with (runtime_cwd / "g5_output.json").open("r") as f:
            g5_res = json.load(f)
        (runtime_cwd / "g5_output.json").unlink()
        print(f"[+] G5 PASS (Steps: {g5_res['steps']}, Statuses: {g5_res['agent_statuses']})")

        # ---------------------------------------------------------
        # G6: Artifact Runtime Smoke Test (20 games)
        # ---------------------------------------------------------
        print(f"[*] G6: Running Artifact Runtime Smoke Test ({smoke_games} games, seed {smoke_seed})...")
        smoke_output_path = runtime_cwd / "smoke_run_result.json"

        g6_code = f"""
import sys
import os
import json
import builtins
import io
from pathlib import Path

# Clean path to isolate extracted packages (G6 clean room). The archive's own
# vendored ``src/`` (if any) is inserted first so any in-archive package
# resolves to the archived copy, never a workspace/.venv one.
sys.path = [p for p in sys.path if "pokemon-tcg-ai-battle" not in p or ".venv" in p]
agent_root_abs = os.path.abspath(os.environ["SUBMISSION_AGENT_ROOT"])
runtime_cwd_abs = os.path.abspath(os.getcwd())
if runtime_cwd_abs == agent_root_abs:
    raise RuntimeError("G6 cwd must be separate from the archive root")
if os.listdir(runtime_cwd_abs):
    raise RuntimeError("G6 working directory must start empty")
archive_src = os.path.join(agent_root_abs, "src")
sys.path.insert(0, agent_root_abs)
if os.path.isdir(archive_src) and archive_src not in sys.path:
    sys.path.insert(0, archive_src)

# Setup builtins.open hook to capture external file reads
original_open = builtins.open
external_files = set()

# Allow only the extracted archive, Python's trusted runtime, and explicitly
# configured site-packages.  In particular, do not allow all of /tmp: a
# candidate sidecar may also live there.
allowed_prefixes = [
    agent_root_abs,
    runtime_cwd_abs,
    os.path.abspath(sys.prefix),
    os.path.abspath(sys.base_prefix),
    "/usr",
    "/lib",
    "/etc",
    "/proc",
    "/sys",
    "/dev",
]
for p in sys.path:
    if p:
        resolved_path = os.path.abspath(p)
        try:
            if os.path.commonpath((resolved_path, os.path.abspath(sys.prefix))) == os.path.abspath(sys.prefix):
                allowed_prefixes.append(resolved_path)
        except ValueError:
            pass

def _is_allowed(abs_path):
    try:
        return any(os.path.commonpath((abs_path, pref)) == pref for pref in allowed_prefixes)
    except ValueError:
        return False

def _record_external(file):
    try:
        if isinstance(file, int):
            return
        abs_path = os.path.abspath(os.fspath(file))
        if not _is_allowed(abs_path):
            external_files.add(abs_path)
    except Exception:
        pass

def patched_open(file, mode='r', *args, **kwargs):
    _record_external(file)
    return original_open(file, mode, *args, **kwargs)

original_io_open = io.open
def patched_io_open(file, mode='r', *args, **kwargs):
    _record_external(file)
    return original_io_open(file, mode, *args, **kwargs)

original_os_open = os.open
def patched_os_open(file, flags, *args, **kwargs):
    _record_external(file)
    return original_os_open(file, flags, *args, **kwargs)

builtins.open = patched_open
io.open = patched_io_open
os.open = patched_os_open

# Fallback telemetry is only trusted when the patched class itself resolves
# to a file inside the extracted archive. A failed import or a class that
# resolves outside the archive means the signal is unmeasured -- it must
# never be reported as a silent zero.
fallback_reasons = []
selected_count = 0
fallback_telemetry_status = "UNAVAILABLE_FROM_ARCHIVE_RUNTIME"
fallback_telemetry_detail = None
try:
    from mage_ptcg.offline_training.neural_runtime import NeuralRuntimePolicy
    import mage_ptcg.offline_training.neural_runtime as _nr_mod

    _resolved = os.path.abspath(getattr(_nr_mod, "__file__", "") or "")
    if os.path.commonpath((_resolved, agent_root_abs)) != agent_root_abs:
        raise RuntimeError(f"NeuralRuntimePolicy resolved outside the archive: {{_resolved}}")

    original_choose = NeuralRuntimePolicy.choose

    def patched_choose(self, observation):
        res = original_choose(self, observation)
        trace = getattr(self, "last_decision_trace", None)
        if trace:
            if trace.get("status") == "fallback":
                fallback_reasons.append(trace.get("reason"))
            elif trace.get("status") == "selected":
                global selected_count
                selected_count += 1
        return res

    NeuralRuntimePolicy.choose = patched_choose
    fallback_telemetry_status = "AVAILABLE"
except Exception as e:
    fallback_telemetry_status = "UNAVAILABLE_FROM_ARCHIVE_RUNTIME"
    fallback_telemetry_detail = f"{{type(e).__name__}}: {{e}}"

from kaggle_environments import make

smoke_games = {smoke_games}
smoke_seed = {smoke_seed}
main_path = os.path.join(agent_root_abs, "main.py")

wins = 0
losses = 0
draws = 0
crashes = 0
invalid_actions = 0
timeouts = 0

for i in range(smoke_games):
    env_make = make(
        "cabt",
        configuration={{
            "actTimeout": 0,
            "episodeSteps": 10000000,
            "runTimeout": 2000,
            "seed": smoke_seed + i,
        }},
        debug=True,
    )
    try:
        steps = env_make.run([main_path, main_path])
        agent0_status = env_make.state[0].status
        agent1_status = env_make.state[1].status

        if agent0_status == "ERROR" or agent1_status == "ERROR":
            crashes += 1
        elif agent0_status == "TIMEOUT" or agent1_status == "TIMEOUT":
            timeouts += 1
        elif agent0_status == "INVALID" or agent1_status == "INVALID":
            invalid_actions += 1
        elif agent0_status != "DONE" or agent1_status != "DONE":
            crashes += 1

        reward0 = env_make.state[0].reward
        reward1 = env_make.state[1].reward
        if reward0 is not None and reward1 is not None:
            if reward0 > reward1:
                wins += 1
            elif reward0 < reward1:
                losses += 1
            else:
                draws += 1
        else:
            draws += 1

    except Exception as e:
        crashes += 1

# Audit every module the archived runtime actually imported: none of them
# may resolve to a file outside the extracted archive (workspace src/,
# .venv editable installs, candidate-directory sidecars, etc). This closes
# the blind spot of the builtins.open patch, since CPython's import loaders
# read source files without going through builtins.open.
for _name, _mod in list(sys.modules.items()):
    if _name != "main" and _name != "runtime_main" and not _name.startswith(("mage_ptcg", "mage_submission_agents")):
        continue
    _mod_file = getattr(_mod, "__file__", None)
    if not _mod_file:
        continue
    _abs = os.path.abspath(_mod_file)
    try:
        _inside_archive = os.path.commonpath((_abs, agent_root_abs)) == agent_root_abs
    except ValueError:
        _inside_archive = False
    if not _inside_archive:
        external_files.add(_abs)

result = {{
    "gate_status": "CLEAN_PASS" if (crashes == 0 and invalid_actions == 0 and timeouts == 0) else "FAILED",
    "crashes": crashes,
    "invalid_actions": invalid_actions,
    "timeouts": timeouts,
    "wins": wins,
    "losses": losses,
    "draws": draws,
    "fallback_telemetry_status": fallback_telemetry_status,
    "fallback_telemetry_detail": fallback_telemetry_detail,
    "fallback_reasons": fallback_reasons,
    "selected_count": selected_count,
    "external_files_read": sorted(list(external_files))
}}

with open("smoke_run_result.json", "w") as f:
    json.dump(result, f)
"""

        # Run G6 sub-process
        proc = subprocess.run(
            [str(runtime_python), "-I", "-c", g6_code],
            cwd=runtime_cwd,
            env=env,
            capture_output=True,
            text=True
        )
        if proc.returncode != 0:
            print("[!] G6 Failed: execution crashed.", file=sys.stderr)
            print(f"Stdout: {proc.stdout}", file=sys.stderr)
            print(f"Stderr: {proc.stderr}", file=sys.stderr)
            raise RuntimeError(f"G6 Failed: {proc.stderr.strip() or proc.stdout.strip()}")

        # Verify G6 JSON output
        with smoke_output_path.open("r") as f:
            smoke_res = json.load(f)

        if smoke_res.get("gate_status") != "CLEAN_PASS":
            raise ValueError(
                f"G6 Failed: crashes={smoke_res.get('crashes')}, "
                f"invalid={smoke_res.get('invalid_actions')}, timeouts={smoke_res.get('timeouts')}"
            )

        if smoke_res.get("crashes", 0) > 0 or smoke_res.get("invalid_actions", 0) > 0 or smoke_res.get("timeouts", 0) > 0:
            raise ValueError(f"G6 Failed: Unsafe executions found: crashes={smoke_res.get('crashes')}, invalid={smoke_res.get('invalid_actions')}, timeouts={smoke_res.get('timeouts')}")

        fallback_telemetry_status = smoke_res.get("fallback_telemetry_status", "UNAVAILABLE_FROM_ARCHIVE_RUNTIME")
        ext_files = smoke_res.get("external_files_read", [])
        if ext_files:
            raise ValueError(f"G6 Failed: archive runtime read external files: {ext_files}")
        print(f"[+] G6 PASS (Wins: {smoke_res.get('wins')}, Losses: {smoke_res.get('losses')}, fallback_telemetry: {fallback_telemetry_status})")
        if fallback_telemetry_status != "AVAILABLE":
            print(f"[*] G6 note: fallback telemetry unavailable ({smoke_res.get('fallback_telemetry_detail')}); not reported as zero.")

        # ---------------------------------------------------------
        # Final Verification of Archive Mutex
        # ---------------------------------------------------------
        final_hash = _sha256(archive_path)
        if initial_hash != final_hash:
            raise ValueError("Verification Failure: Archive was mutated during verification!")

        # ---------------------------------------------------------
        # G7: Candidate Freeze (Build verification metadata JSON)
        # ---------------------------------------------------------
        # get verification commit
        git_commit = subprocess.run(
            ["git", "-C", str(Path(__file__).resolve().parent.parent), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True
        ).stdout.strip()

        import datetime
        is_archive_only = len(ext_files) == 0
        cwd_decoupled = archive_dir.resolve() != runtime_cwd.resolve()

        manifest = {
            "schema_version": "kaggle-submission-verification-v1",
            "archive_path": str(archive_path),
            "archive_sha256": final_hash,
            "archive_size": archive_size,
            "entrypoint_sha256": entrypoint_sha256,
            "runtime_main_sha256": runtime_main_sha256,
            "model_sha256": model_sha256,
            "semantic_model_hash": semantic_model_hash,
            "verification_script_commit": git_commit,
            "verification_script_sha256": _verification_script_sha256(),
            "python_version": runtime_report["python_version"],
            "kaggle_environments_version": runtime_report["kaggle_environments_version"],
            "verification_timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "gates": {
                "archive_integrity": {"status": "PASS"},
                "dependency_closure": {"status": "PASS"},
                "kaggle_raw_exec": {"status": "PASS"},
                "initial_lifecycle": {"status": "PASS"},
                "local_validation_episode": {
                    "status": "PASS",
                    "agent_statuses": g5_res["agent_statuses"],
                    "steps": g5_res["steps"]
                },
                "artifact_runtime_smoke": {
                    "status": "PASS",
                    "games": smoke_games,
                    "seed": smoke_seed,
                    "crashes": smoke_res.get("crashes", 0),
                    "invalid_actions": smoke_res.get("invalid_actions", 0),
                    "timeouts": smoke_res.get("timeouts", 0),
                    "fallback_telemetry_status": smoke_res.get("fallback_telemetry_status", "UNAVAILABLE_FROM_ARCHIVE_RUNTIME"),
                    "fallback_telemetry_detail": smoke_res.get("fallback_telemetry_detail"),
                    "fallback_reasons": smoke_res.get("fallback_reasons", []),
                    "selected_count": smoke_res.get("selected_count", 0)
                }
            },
            "archive_only_verification": is_archive_only,
            "cwd_decoupled_verification": cwd_decoupled,
            "code_root": "extracted_archive",
            "working_directory": "separate_empty_directory",
            "external_files_read": ext_files,
            "g6_runtime_source": "extracted_archive/main.py",
            "local_submission_ready": bool(is_archive_only and cwd_decoupled),
            "kaggle_validation_passed": False
        }

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"[*] Verification complete. JSON manifest written to {output_path}")
        return 0

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

def main() -> int:
    parser = argparse.ArgumentParser(description="Mandatory Kaggle Submission Safety Gate")
    parser.add_argument("--archive", required=True, type=Path, help="Path to submission.tar.gz")
    parser.add_argument("--competition", default="pokemon-tcg-ai-battle", help="Target competition name")
    parser.add_argument("--smoke-games", type=int, default=20, help="Number of viability games")
    parser.add_argument("--smoke-seed", type=int, default=33000, help="Seed for viability smoke test")
    parser.add_argument("--output", required=True, type=Path, help="Output verification JSON path")
    parser.add_argument(
        "--python-executable",
        type=Path,
        default=Path(sys.executable),
        help="Python 3.11 interpreter containing kaggle-environments 1.32.0",
    )
    args = parser.parse_args()

    try:
        return verify_archive(
            archive_path=args.archive,
            competition=args.competition,
            smoke_games=args.smoke_games,
            smoke_seed=args.smoke_seed,
            output_path=args.output,
            python_executable=args.python_executable,
        )
    except Exception as e:
        print(f"[!] Validation Aborted due to failure: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1

if __name__ == "__main__":
    sys.exit(main())
