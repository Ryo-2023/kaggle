"""Fresh-process smoke tests against the pinned native CABT engine."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest


_CABT_ARCHIVE = Path(
    "/home/bfe-lab-ono/kaggle/handoff-artifacts/"
    "canonical-champion-rebaseline-v1/_work/main_archive"
)
_CABT_API_SHA256 = "593f1298e52a635f90f8f505a52113e9af114f444c293404e37906f18ee06ced"
_LEGAL_DECK_SHA256 = "167d43335013f7b68441356d750dab335088171c1ab929e083deb85a2c79e5b1"
_NATIVE_CG_ADAPTER_ID = "native-cg-game-observation-step-v1"
_OPPONENT_ADAPTER_ID = "deterministic-minimum-legal-cabt-opponent-v1"
_RESULT_PREFIX = "TASK4_CABT_RESULT="


# The native verifier owns process-global battle state.  Each invocation below
# therefore executes exactly one game in a disposable interpreter and always
# calls battle_finish once for every successful battle_start.
_CABT_CHILD = r"""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

worktree = Path(sys.argv[1]).resolve()
archive = Path(sys.argv[2]).resolve()
deck_path = Path(sys.argv[3]).resolve()
runtime_root = Path(sys.argv[4]).resolve()
expected_cwd = Path(sys.argv[5]).resolve()
specialist_seat = int(sys.argv[6])
expected_api_sha = sys.argv[7]
expected_deck_sha = sys.argv[8]
adapter_id = sys.argv[9]

if Path.cwd().resolve() != expected_cwd:
    raise RuntimeError("CABT child did not start from the requested arbitrary cwd")
if hashlib.sha256((archive / "cg" / "api.py").read_bytes()).hexdigest() != expected_api_sha:
    raise RuntimeError("CABT api.py SHA-256 drifted")
if hashlib.sha256(deck_path.read_bytes()).hexdigest() != expected_deck_sha:
    raise RuntimeError("legal deck seed SHA-256 drifted")

sys.path.insert(0, str(archive))
sys.path.insert(0, str(worktree))
sys.path.insert(0, str(worktree / "src"))

helper_path = worktree / "tests" / "meta_specialist" / "test_runtime.py"
spec = importlib.util.spec_from_file_location("task4_runtime_test_helpers", helper_path)
if spec is None or spec.loader is None:
    raise RuntimeError("could not load Task 4 runtime test helpers")
helpers = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = helpers
spec.loader.exec_module(helpers)

from cg.game import battle_finish, battle_select, battle_start

if adapter_id != "native-cg-game-observation-step-v1":
    raise RuntimeError("unsupported low-level native cg observation adapter")

def adapt_native_cg_observation(observation, step):
    # Bridge pinned low-level cg.game output to the wrapper-level C1 v2 shape.
    if type(observation) is not dict or type(step) is not int or step < 0:
        raise RuntimeError("native cg adapter received invalid input")
    if "step" in observation:
        raise RuntimeError("pinned native cg contract unexpectedly supplied wrapper step")
    adapted = dict(observation)
    adapted["step"] = step
    return adapted

cards = tuple(int(value) for value in deck_path.read_text(encoding="utf-8").splitlines() if value.strip())
if len(cards) != 60:
    raise RuntimeError("pinned CABT seed is not a 60-card deck")

specialist_root = runtime_root / f"specialist-seat-{specialist_seat}"
specialist_root.mkdir(parents=True, exist_ok=False)
runtime, policy, registered_cards = helpers._runtime(specialist_root, cards=cards)
if runtime({"select": None}) != list(cards) or registered_cards != cards:
    raise RuntimeError("specialist runtime did not register the pinned deck")

def deterministic_legal_opponent(observation):
    select = observation.get("select")
    if type(select) is not dict or type(select.get("option")) is not list:
        raise RuntimeError("opponent received malformed native CABT selection")
    minimum = select.get("minCount")
    maximum = select.get("maxCount")
    if (
        type(minimum) is not int or type(maximum) is not int
        or minimum < 0 or minimum > maximum or maximum > len(select["option"])
    ):
        raise RuntimeError("opponent received invalid native CABT bounds")
    return list(range(minimum))

started = False
steps = 0
seat_steps = [0, 0]
try:
    observation, _start_data = battle_start(list(cards), list(cards))
    if observation is None:
        raise RuntimeError("native CABT battle_start returned no observation")
    started = True
    while observation["current"]["result"] < 0:
        # This is a failure guard, never a successful truncation path.  The
        # parent process also applies the frozen five-minute wall-clock gate.
        if steps >= 10_000:
            raise RuntimeError("native CABT game did not reach DONE before the failure guard")
        acting_seat = observation["current"]["yourIndex"]
        if type(acting_seat) is not int or acting_seat not in (0, 1):
            raise RuntimeError("native CABT produced an invalid acting seat")
        if acting_seat == specialist_seat:
            selection = runtime(adapt_native_cg_observation(observation, steps))
        else:
            selection = deterministic_legal_opponent(observation)
        observation = battle_select(selection)
        steps += 1
        seat_steps[acting_seat] += 1
    result = observation["current"]["result"]
    if type(result) is not int or result not in (0, 1, 2):
        raise RuntimeError("native CABT produced an invalid terminal result")
finally:
    if started:
        battle_finish()

telemetry = runtime.package_telemetry()
if telemetry["invalid_count"] != 0:
    raise RuntimeError("specialist recorded an invalid runtime decision")
if telemetry["crash_count"] != 0:
    raise RuntimeError("specialist recorded a runtime crash")
if telemetry["timeout_count"] != 0:
    raise RuntimeError("specialist recorded a runtime timeout")
if telemetry["legal_decision_count"] != seat_steps[specialist_seat]:
    raise RuntimeError("specialist commit count does not match its CABT selection count")
if any(count == 0 for count in seat_steps):
    raise RuntimeError("both specialist and distinct opponent must act")
if any(session.commits != 1 or session.aborts != 0 for session in policy.sessions):
    raise RuntimeError("a CABT runtime transaction was not committed exactly once")

print("TASK4_CABT_RESULT=" + json.dumps({
    "status": "DONE",
    "specialist_seat": specialist_seat,
    "result": result,
    "steps": steps,
    "truncated": False,
    "invalid": telemetry["invalid_count"],
    "crash": telemetry["crash_count"],
    "timeout": telemetry["timeout_count"],
    "specialist_legal_decisions": telemetry["legal_decision_count"],
    "seat_decisions": seat_steps,
    "observation_adapter_id": adapter_id,
    "opponent_adapter_id": "deterministic-minimum-legal-cabt-opponent-v1",
    "cwd": str(Path.cwd().resolve()),
}, sort_keys=True))
"""


def _require_pinned_cabt() -> tuple[Path, Path]:
    api_path = _CABT_ARCHIVE / "cg" / "api.py"
    game_path = _CABT_ARCHIVE / "cg" / "game.py"
    library_path = _CABT_ARCHIVE / "cg" / "libcg.so"
    deck_path = _CABT_ARCHIVE / "deck.csv"
    missing = [path for path in (api_path, game_path, library_path, deck_path) if not path.is_file()]
    if missing:
        pytest.skip(
            "BLOCKED_DEPENDENCY: pinned native CABT bundle is unavailable: "
            + ", ".join(str(path) for path in missing)
        )
    assert hashlib.sha256(api_path.read_bytes()).hexdigest() == _CABT_API_SHA256
    assert hashlib.sha256(deck_path.read_bytes()).hexdigest() == _LEGAL_DECK_SHA256
    return _CABT_ARCHIVE, deck_path


@pytest.mark.parametrize("specialist_seat", (0, 1))
def test_real_cabt_smoke_reaches_done_with_fresh_seat_bindings(
    tmp_path: Path, specialist_seat: int,
) -> None:
    archive, deck_path = _require_pinned_cabt()
    worktree = Path(__file__).resolve().parents[2]
    arbitrary_cwd = tmp_path / f"arbitrary-cwd-seat-{specialist_seat}"
    runtime_root = tmp_path / f"bindings-seat-{specialist_seat}"
    arbitrary_cwd.mkdir()
    runtime_root.mkdir()
    completed = subprocess.run(
        [
            sys.executable, "-I", "-c", _CABT_CHILD,
            str(worktree), str(archive), str(deck_path), str(runtime_root),
            str(arbitrary_cwd), str(specialist_seat), _CABT_API_SHA256,
            _LEGAL_DECK_SHA256, _NATIVE_CG_ADAPTER_ID,
        ],
        cwd=arbitrary_cwd,
        text=True,
        capture_output=True,
        timeout=300,
        check=False,
    )
    assert completed.returncode == 0, (
        f"fresh CABT child failed for seat {specialist_seat}\n"
        f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
    )
    result_lines = [
        line for line in completed.stdout.splitlines() if line.startswith(_RESULT_PREFIX)
    ]
    assert len(result_lines) == 1, completed.stdout
    result = json.loads(result_lines[0][len(_RESULT_PREFIX):])
    assert result == {
        "status": "DONE",
        "specialist_seat": specialist_seat,
        "result": result["result"],
        "steps": result["steps"],
        "truncated": False,
        "invalid": 0,
        "crash": 0,
        "timeout": 0,
        "specialist_legal_decisions": result["specialist_legal_decisions"],
        "seat_decisions": result["seat_decisions"],
        "observation_adapter_id": _NATIVE_CG_ADAPTER_ID,
        "opponent_adapter_id": _OPPONENT_ADAPTER_ID,
        "cwd": str(arbitrary_cwd.resolve()),
    }
    assert result["result"] in (0, 1, 2)
    assert type(result["steps"]) is int and result["steps"] > 0
    assert len(result["seat_decisions"]) == 2
    assert all(type(count) is int and count > 0 for count in result["seat_decisions"])
    assert sum(result["seat_decisions"]) == result["steps"]
    assert result["specialist_legal_decisions"] == result["seat_decisions"][specialist_seat]
