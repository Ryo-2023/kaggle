"""Actual-cabt Team League runtime for O6 Population members.

A fresh, disposable, isolated subprocess is spawned per Native Team Agent
*per game* (never reused across games): this removes an entire class of
cross-match state-leakage risk (module-level globals, native library state)
by construction, at the cost of one interpreter start per game. Rule Agent
v0 is trusted local code and runs host-side without isolation.

This module only orchestrates match execution; win/loss/draw classification
and league bookkeeping (schedule, side-swap, resume, per-seat attribution,
Wilson-eligible raw counts) are delegated to
:mod:`mage_ptcg.league.actual_runner`, which already implements them.
"""
from __future__ import annotations

import json
import os
import selectors
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Mapping

from .core import OpponentError, _sha256, safe_extract_tar_gz  # type: ignore[attr-defined]

_WORKER_HARNESS = r"""
import importlib.util, inspect, json, os, sys
root, rel, name, home = sys.argv[1:5]
os.environ['HOME'] = home
sys.path.insert(0, root)
spec = importlib.util.spec_from_file_location('o6_league_agent', os.path.join(root, rel))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
agent = getattr(module, name)
def invoke(observation, configuration=None):
    return agent(observation, configuration) if len(inspect.signature(agent).parameters) >= 2 else agent(observation)
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    if line == '__STOP__':
        break
    payload = json.loads(line)
    try:
        result = invoke(payload.get('observation'), payload.get('configuration'))
        sys.stdout.write(json.dumps({'ok': True, 'selection': result}, separators=(',', ':')) + '\n')
    except Exception as exc:
        sys.stdout.write(json.dumps({'ok': False, 'error_type': type(exc).__name__, 'error': str(exc)[:200]}, separators=(',', ':')) + '\n')
    sys.stdout.flush()
"""


class NativeAgentWorker:
    """One isolated subprocess for exactly one game; never reused across games.

    Communicates over stdin/stdout JSON lines so a single subprocess serves
    every decision of one game without re-paying interpreter/module-import
    cost per decision, while still giving each *game* a brand-new process
    (own HOME, own module globals, own native-library state) -- this is the
    "process-per-match" isolation path.
    """

    def __init__(self, source_root: str | Path, entrypoint: str, *, decision_timeout_seconds: float = 8.0):
        module_path, _, callable_name = entrypoint.partition(":")
        if not callable_name:
            raise OpponentError("entrypoint must be relative_file.py:callable")
        self.decision_timeout_seconds = decision_timeout_seconds
        self._home = tempfile.mkdtemp(prefix="o6-league-home-")
        # Resolve to absolute *before* handing it to both `cwd=` and the
        # harness argv: subprocess.Popen(cwd=...) resolves a relative path
        # against the parent's cwd, but the harness script then re-joins the
        # same string against the child's own (already-relocated) cwd via
        # os.path.join(root, rel) -- a relative source_root would silently
        # double, producing a FileNotFoundError instead of importing the
        # agent. This was invisible as long as every caller happened to pass
        # an absolute path.
        source_root = Path(source_root).resolve()
        env = {"PATH": os.environ.get("PATH", ""), "PYTHONDONTWRITEBYTECODE": "1", "HOME": os.environ.get("HOME", "")}
        self._process = subprocess.Popen(
            [sys.executable, "-c", _WORKER_HARNESS, str(source_root), module_path, callable_name, self._home],
            cwd=source_root, text=True, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, bufsize=1)
        self._selector = selectors.DefaultSelector()
        self._selector.register(self._process.stdout, selectors.EVENT_READ)
        self.decisions = 0

    def __call__(self, observation: Any, configuration: Any = None) -> Any:
        if self._process.poll() is not None:
            raise OpponentError(f"league worker exited before decision (returncode={self._process.returncode})")
        self._process.stdin.write(json.dumps({"observation": observation, "configuration": configuration}, separators=(",", ":")) + "\n")
        self._process.stdin.flush()
        if not self._selector.select(timeout=self.decision_timeout_seconds):
            raise TimeoutError(f"league worker decision exceeded {self.decision_timeout_seconds}s")
        line = self._process.stdout.readline()
        if not line:
            stderr_tail = (self._process.stderr.read() or "")[-300:]
            raise OpponentError(f"league worker exited unexpectedly: {stderr_tail}")
        response = json.loads(line)
        self.decisions += 1
        if not response.get("ok"):
            raise OpponentError(f"league worker raised {response.get('error_type')}: {response.get('error')}")
        return response["selection"]

    def close(self) -> None:
        try:
            if self._process.poll() is None:
                try:
                    self._process.stdin.write("__STOP__\n"); self._process.stdin.flush()
                except (BrokenPipeError, OSError):
                    pass
                try:
                    self._process.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    self._process.kill(); self._process.wait(timeout=2.0)
        finally:
            self._selector.close()
            shutil.rmtree(self._home, ignore_errors=True)


def prepare_native_participant(population_cache_dir: str | Path, agent_id: str, *, scratch_root: str | Path) -> dict[str, Any]:
    """Hash-verify and extract one agent's runtime bundle for League use.

    Reuses the same bundle-hash-then-extract discipline as
    ``run_fresh_client_smoke``: no game is played against unverified bytes.
    """
    bundle = Path(population_cache_dir) / "bundle.tar.gz"
    extract_root = Path(scratch_root) / f"league-src-{agent_id}-{os.getpid()}-{int(time.time() * 1000)}"
    safe_extract_tar_gz(bundle, extract_root)
    runtime_root = extract_root / "runtime" / agent_id
    hashes = json.loads((runtime_root / "hashes.json").read_text(encoding="utf-8"))
    for relpath, expected in hashes.get("files", {}).items():
        target = extract_root / relpath
        if not target.is_file() or _sha256(target.read_bytes()) != expected:
            shutil.rmtree(extract_root, ignore_errors=True)
            raise OpponentError(f"league runtime bundle hash mismatch: {relpath}")
    adapter = json.loads((runtime_root / "adapter.json").read_text(encoding="utf-8"))
    return {"extract_root": extract_root, "source_root": runtime_root / "source", "entrypoint": adapter["entrypoint"]}


def cleanup_native_participant(prepared: Mapping[str, Any]) -> None:
    shutil.rmtree(prepared["extract_root"], ignore_errors=True)


def play_game(*, deck_a: list[int], deck_b: list[int], call_a: Callable[..., Any], call_b: Callable[..., Any], max_steps: int = 10_000) -> dict[str, Any]:
    """Run exactly one real cabt game between two already-prepared callables.

    O6-AUD-002 remediation: also returns ``trajectory`` (initial/action/
    terminal/complete digests, timestamp/path-independent -- see
    :mod:`mage_ptcg.opponents.trajectory`), ``canonical_steps`` (the exact
    canonicalized per-step per-seat records), ``public_trajectory_events``
    (the strict allow-list projection of those steps -- see
    :mod:`mage_ptcg.opponents.public_trajectory_projection` -- which is what
    ``trajectory`` is actually computed from and what a caller should persist
    as evidence, so raw observations never need to be re-derived from
    ``environment.steps`` a second time), and ``engine_seed_support``,
    determined live from this game's own ``environment.configuration``
    rather than assumed, so League evidence can distinguish genuinely
    different games from repeated identical trajectories and can state
    cabt's actual seed capability instead of implying the recorded ``seed``
    controls anything.
    """
    from kaggle_environments import make
    from scripts.test_sim import _classify_terminal_state, _terminal_details  # local, trusted reuse of existing winner/status logic

    from .public_trajectory_projection import PublicSchemaUnknownFieldError, build_public_trajectory_events
    from .trajectory import canonical_step_seat, compute_trajectory_digests, determine_engine_seed_capability

    def _canonical_steps_or_none(steps: Any) -> list[list[dict[str, Any]]] | None:
        if not steps:
            return None
        return [[canonical_step_seat(seat) for seat in step] for step in steps]

    def _public_events_or_none(canonical_steps: list[list[dict[str, Any]]] | None) -> list[dict[str, Any]] | None:
        if not canonical_steps:
            return None
        try:
            return build_public_trajectory_events(canonical_steps)
        except PublicSchemaUnknownFieldError:
            return None

    started = time.monotonic()
    environment = None
    try:
        environment = make("cabt", configuration={"decks": [deck_a, deck_b]})
        environment.run([call_a, call_b])
    except (TimeoutError, OpponentError) as exc:
        canonical_steps = _canonical_steps_or_none(getattr(environment, "steps", None)) if environment is not None else None
        public_events = _public_events_or_none(canonical_steps)
        trajectory = None
        if public_events is not None:
            try:
                trajectory = compute_trajectory_digests(public_events)
            except OpponentError:
                trajectory = None
        engine_seed_support = determine_engine_seed_capability(environment.configuration.keys()) if environment is not None else None
        return {"status": "AGENT_TIMEOUT" if isinstance(exc, TimeoutError) else "AGENT_ERROR", "winner": None,
                "elapsed_seconds": round(time.monotonic() - started, 3), "agent_status": None, "error": str(exc)[:200],
                "trajectory": trajectory, "engine_seed_support": engine_seed_support,
                "canonical_steps": canonical_steps, "public_trajectory_events": public_events}
    statuses = [str(state.status) for state in environment.state]
    winner, _reason, _turn = _terminal_details(environment)
    status = _classify_terminal_state(statuses=statuses, winner=winner, steps=len(environment.steps), max_steps=max_steps)
    canonical_steps = _canonical_steps_or_none(environment.steps)
    public_events = _public_events_or_none(canonical_steps)
    trajectory = compute_trajectory_digests(public_events) if public_events is not None else None
    engine_seed_support = determine_engine_seed_capability(environment.configuration.keys())
    return {"status": status, "winner": winner if status == "DONE" else None,
            "elapsed_seconds": round(time.monotonic() - started, 3), "agent_status": statuses, "steps": len(environment.steps),
            "trajectory": trajectory, "engine_seed_support": engine_seed_support,
            "canonical_steps": canonical_steps, "public_trajectory_events": public_events}
