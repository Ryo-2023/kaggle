# O6-AUD-002 Raw Trajectory Evidence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task in this same session (inline execution chosen — the implementer already holds full design context from the audit review; a fresh subagent per task would have to re-derive the cabt observation schema, digest algorithm, and evidence layout from scratch for each task, which costs more than it saves for this tightly-coupled feature).

**Goal:** Close O6-AUD-002 (HIGH) by persisting raw, privacy-gated, canonical public League trajectories to disk, adding a process-isolated independent digest verifier that never imports the runtime digest code or League runner, and re-running the Team League for real so the reported 60/60 unique-trajectory claim is independently reproducible instead of asserted.

**Architecture:** A "runtime" write path (`raw_trajectory_evidence.py`, called from `scripts/run_o6_team_league.py`) persists gzip-compressed ordered JSONL events (`INITIAL_PUBLIC_OBSERVATION` / `PUBLIC_ACTION` / `TERMINAL_PUBLIC_OBSERVATION`) per game after a fail-closed privacy scan (`privacy_gate.py`). A separate "independent verifier" module (`independent_trajectory_verifier.py`) that imports **no** League/runtime/digest code re-parses those raw files from scratch, re-implements canonical-JSON + SHA-256 hashing itself, and is invoked only via `subprocess` (own Python process) from both the CLI and the league script's finalize step — so "independent" is enforced by process and import boundaries, not just by calling a shared function twice. Final league statistics (uniqueness, Wilson, Bradley-Terry) are rebuilt from the **independently recomputed** digests, and the whole run aborts (does not emit a "final" summary) on any mismatch, privacy violation, or malformed file.

**Tech Stack:** Python 3.12, stdlib only for the independent verifier (`gzip`, `json`, `hashlib`, `re`), existing `kaggle_environments`/cabt via the repo's shared `.venv`.

## Global Constraints

- No new branch/worktree. Only worktree `/home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle-o6-opponents-v1` on `feature/o6-opponent-intelligence-platform-v1` is touched.
- Never merge/push/rebase/reset canonical, never touch Champion/default Agent/Kaggle submission/protected files (`main.py`, `deck.csv`, `agents/rule_agent*.py`, `src/mage_ptcg/evaluation/promotion.py`).
- Existing evidence under `docs/evidence/o6-opponent-intelligence-v1*` and `docs/evidence/o6-opponent-intelligence-v2*` is immutable — only new files may be added there (a legacy note), nothing there is edited or deleted.
- New raw evidence lives in a new versioned directory `docs/evidence/o6-opponent-intelligence-v3/` (git-tracked).
- Digest inputs exclude: game_id, pair_id, execution index, requested/bookkeeping seed, any timestamp, filesystem path, filename, hostname, username, PID, RNG/engine internal state, the digest itself, private/hidden observation content.
- `.venv` in this worktree is a symlink to the sibling canonical worktree's `.venv` (already created, gitignored) — reused for all verification commands in this plan.

---

### Task 1: Public-only privacy gate

**Files:**
- Create: `src/mage_ptcg/opponents/privacy_gate.py`
- Test: `tests/opponents/test_privacy_gate.py`

**Interfaces:**
- Produces: `PUBLIC_ONLY_GATE_SCHEMA_VERSION: str`; `class PrivacyViolation(OpponentError)`; `def scan_public_only(value: Any) -> dict[str, Any]` (non-raising, returns `{"schema_version", "status": "PASS"|"REJECTED", "violation": None | {"path": str, "reason": str}}`); `def assert_public_only(value: Any) -> None` (raises `PrivacyViolation` on any REJECTED result — this is the fail-closed enforcement point raw_trajectory_evidence.py calls before writing anything).
- Consumes: nothing repo-internal besides `OpponentError` from `.errors`.

Empirically confirmed cabt schema facts this module relies on (verified by running a real game locally):
- A step's per-seat `observation` has `current: None` and `select: None` for the seat **not** acting that step; the acting seat's `observation["current"]["players"]` is a 2-element list with `observation["current"]["yourIndex"]` telling which index is "self".
- The non-acting-side `players[i]["hand"]` is `None` (engine-redacted) for `i != yourIndex`; own-side `hand` is a (possibly empty) list. This is the concrete "hidden opponent hand" invariant to enforce, not just a keyword guess.

Implementation:
```python
"""Fail-closed scanner: reject anything that is not clean public cabt trajectory data.

Used both when *writing* raw evidence (fail-closed: refuse to persist) and
again, independently, when *verifying* it (re-check, do not just trust the
writer). Unknown/unrecognized suspicious content is rejected, not passed
through -- see module docstring in the spec this implements
(O6-AUD-002 remediation, section 4).
"""
from __future__ import annotations

import re
from typing import Any

from .errors import OpponentError

PUBLIC_ONLY_GATE_SCHEMA_VERSION = "o6-public-only-gate-v1"

_FORBIDDEN_KEY_PATTERN = re.compile(
    r"(hidden|secret|private|credential|password|token|api[_-]?key|"
    r"rng[_-]?state|random[_-]?state|seed[_-]?state|engine[_-]?internal|"
    r"hostname|username|\bpid\b|process[_-]?id|environ|env[_-]?var|"
    r"debug[_-]?dump|internal[_-]?state|memory[_-]?address)",
    re.IGNORECASE,
)
_FORBIDDEN_VALUE_PATTERN = re.compile(
    r"(object at 0x[0-9a-fA-F]+"
    r"|<[\w\.]+\s+object\s+at\s+0x[0-9a-fA-F]+>"
    r"|/home/[^\s\"']+|/Users/[^\s\"']+|/root/[^\s\"']+|/tmp/[^\s\"']+"
    r"|[A-Za-z]:\\\\[^\s\"']+"
    r"|\bAKIA[0-9A-Z]{16}\b)"
)


class PrivacyViolation(OpponentError):
    """A candidate public-trajectory payload contains non-public content."""


def _check_opponent_hand_redacted(observation: Any, *, path: str) -> tuple[str, str] | None:
    if not isinstance(observation, dict):
        return None
    current = observation.get("current")
    if not isinstance(current, dict):
        return None
    your_index = current.get("yourIndex")
    players = current.get("players")
    if not isinstance(players, list) or not isinstance(your_index, int):
        return None
    for index, player in enumerate(players):
        if index == your_index or not isinstance(player, dict):
            continue
        if player.get("hand") is not None:
            return (f"{path}.current.players[{index}].hand", "opponent hand is not redacted (expected null)")
    return None


def _walk(value: Any, *, path: str) -> tuple[str, str] | None:
    if isinstance(value, dict):
        hand_violation = _check_opponent_hand_redacted(value, path=path)
        if hand_violation:
            return hand_violation
        for key, child in value.items():
            if not isinstance(key, str):
                return (path, "non-string key")
            if _FORBIDDEN_KEY_PATTERN.search(key):
                return (f"{path}.{key}", f"forbidden field name pattern: {key!r}")
            found = _walk(child, path=f"{path}.{key}")
            if found:
                return found
        return None
    if isinstance(value, list):
        for index, child in enumerate(value):
            found = _walk(child, path=f"{path}[{index}]")
            if found:
                return found
        return None
    if isinstance(value, str) and _FORBIDDEN_VALUE_PATTERN.search(value):
        return (path, f"forbidden value pattern in string: {value[:80]!r}")
    return None


def scan_public_only(value: Any) -> dict[str, Any]:
    violation = _walk(value, path="$")
    if violation is None:
        return {"schema_version": PUBLIC_ONLY_GATE_SCHEMA_VERSION, "status": "PASS", "violation": None}
    path, reason = violation
    return {"schema_version": PUBLIC_ONLY_GATE_SCHEMA_VERSION, "status": "REJECTED", "violation": {"path": path, "reason": reason}}


def assert_public_only(value: Any) -> None:
    result = scan_public_only(value)
    if result["status"] != "PASS":
        violation = result["violation"] or {}
        raise PrivacyViolation(f"public-only gate rejected {violation.get('path')}: {violation.get('reason')}")
```

Tests (`tests/opponents/test_privacy_gate.py`) — write all of these as failing tests first, then implement:
```python
from __future__ import annotations

import pytest

from mage_ptcg.opponents.privacy_gate import PrivacyViolation, assert_public_only, scan_public_only


def _step(your_index=0, opp_hand=None, action=None, extra_current=None):
    current = {"yourIndex": your_index, "players": [
        {"hand": [] if your_index == 0 else opp_hand, "deckCount": 60},
        {"hand": opp_hand if your_index == 0 else [], "deckCount": 60},
    ]}
    if extra_current:
        current.update(extra_current)
    return {"observation": {"current": current, "select": {}}, "action": action, "status": "ACTIVE"}


def test_public_only_fixture_accepted():
    assert scan_public_only(_step())["status"] == "PASS"


def test_hidden_opponent_hand_rejected():
    result = scan_public_only(_step(opp_hand=[{"id": 1, "name": "Pikachu"}]))
    assert result["status"] == "REJECTED"
    assert "hand" in result["violation"]["path"]
    with pytest.raises(PrivacyViolation):
        assert_public_only(_step(opp_hand=[{"id": 1}]))


def test_deck_order_key_name_rejected():
    result = scan_public_only({"hidden_deck_order": [1, 2, 3]})
    assert result["status"] == "REJECTED"


def test_engine_internal_state_key_rejected():
    result = scan_public_only({"engine_internal_state": {"rng_state": [1, 2]}})
    assert result["status"] == "REJECTED"


def test_python_repr_value_rejected():
    result = scan_public_only({"note": "<CabtEngine object at 0x7f1234abcd00>"})
    assert result["status"] == "REJECTED"


def test_absolute_path_value_rejected():
    result = scan_public_only({"note": "/home/bfe-lab-ono/kaggle/secret.json"})
    assert result["status"] == "REJECTED"


def test_unknown_sensitive_field_fails_closed():
    result = scan_public_only({"credential_bundle": "whatever"})
    assert result["status"] == "REJECTED"


def test_nested_list_scanned():
    result = scan_public_only({"actions": [{"note": "ok"}, {"note": "object at 0x1234"}]})
    assert result["status"] == "REJECTED"
```

- [ ] Write the failing tests above to `tests/opponents/test_privacy_gate.py`.
- [ ] Run: `cd /home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle-o6-opponents-v1 && PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m pytest -q tests/opponents/test_privacy_gate.py` — expect `ModuleNotFoundError: mage_ptcg.opponents.privacy_gate`.
- [ ] Create `src/mage_ptcg/opponents/privacy_gate.py` with the implementation above.
- [ ] Re-run the same pytest command — expect all pass.
- [ ] Commit (part of Task 1+2 combined commit, see Task 2).

---

### Task 2: Raw trajectory event schema + writer

**Files:**
- Modify: `src/mage_ptcg/opponents/trajectory.py` (rename `_canonical_step_seat`→`canonical_step_seat`, `_strip_volatile_observation`→`strip_volatile_observation`; both become public so the writer can reuse identical canonicalization without duplicating it on the *write* side — duplication is only required for the *independent verifier*, see Task 3)
- Create: `src/mage_ptcg/opponents/raw_trajectory_evidence.py`
- Test: `tests/opponents/test_raw_trajectory_evidence.py`

**Interfaces:**
- Consumes: `mage_ptcg.opponents.trajectory.canonical_step_seat`, `.strip_volatile_observation`; `mage_ptcg.opponents.privacy_gate.assert_public_only`; `mage_ptcg.competition_intelligence.canonical.sha256_hex`; `mage_ptcg.competition_intelligence.atomic_io.atomic_write_json`.
- Produces: `RAW_TRAJECTORY_SCHEMA_VERSION`, `EVENT_INITIAL="INITIAL_PUBLIC_OBSERVATION"`, `EVENT_ACTION="PUBLIC_ACTION"`, `EVENT_TERMINAL="TERMINAL_PUBLIC_OBSERVATION"`; `def build_raw_events(canonical_steps: Sequence[Sequence[Mapping]]) -> list[dict]`; `def persist_game_evidence(evidence_root: Path, game_dir_id: str, *, canonical_steps, runtime_digests: Mapping, metadata: Mapping) -> dict` (returns the written `trajectory_manifest.json` content); `class ImmutableEvidenceConflict(OpponentError)`; `def write_immutable_json(path: Path, value: Any) -> None` (no-op if identical content already on disk, raises `ImmutableEvidenceConflict` if different content already exists at that path — this is the "same run ID/different content rejected" tamper guard used later by the league script).

Event shape (every event carries the *same* per-step canonical payload under `public_step` so the independent verifier can reconstruct `canonical_steps` losslessly just by taking `public_step` from every event in order):
```python
{
    "schema_version": RAW_TRAJECTORY_SCHEMA_VERSION,
    "event_type": "INITIAL_PUBLIC_OBSERVATION",  # or PUBLIC_ACTION / TERMINAL_PUBLIC_OBSERVATION
    "step_index": 0,
    "acting_seat": None,  # or 0/1: the seat index whose "action" is truthy this step
    "public_step": [{"observation": ..., "action": ..., "status": ...}, {"observation": ..., "action": ..., "status": ...}],
    "legal_action_check": {"seat_0_status": "ACTIVE", "seat_1_status": "ACTIVE"},
}
```

```python
"""Raw, privacy-gated, canonical public League trajectory persistence (O6-AUD-002).

This is the *runtime write* side: it may (and does) reuse the same
canonicalization helpers the runtime digest computation uses, because the
requirement this repairs is "no raw bytes exist to re-derive the digest
from" -- not "the writer must not know how digests are computed". The
*independent verifier* (mage_ptcg.opponents.independent_trajectory_verifier)
is the module that must NOT share code with this one; see its docstring.
"""
from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from mage_ptcg.competition_intelligence.atomic_io import atomic_write_json
from mage_ptcg.competition_intelligence.canonical import canonical_json_bytes, sha256_hex

from .errors import OpponentError
from .privacy_gate import assert_public_only
from .trajectory import canonical_step_seat, strip_volatile_observation  # noqa: F401  (re-export for callers)

RAW_TRAJECTORY_SCHEMA_VERSION = "o6-raw-public-trajectory-v1"
TRAJECTORY_MANIFEST_SCHEMA_VERSION = "o6-raw-trajectory-manifest-v1"
EVENT_INITIAL = "INITIAL_PUBLIC_OBSERVATION"
EVENT_ACTION = "PUBLIC_ACTION"
EVENT_TERMINAL = "TERMINAL_PUBLIC_OBSERVATION"


class ImmutableEvidenceConflict(OpponentError):
    """Attempted to overwrite already-persisted evidence with different content."""


def _acting_seat(step: Sequence[Mapping[str, Any]]) -> int | None:
    for index, seat in enumerate(step):
        if seat.get("action"):
            return index
    return None


def build_raw_events(canonical_steps: Sequence[Sequence[Mapping[str, Any]]]) -> list[dict[str, Any]]:
    if not canonical_steps:
        raise OpponentError("cannot build raw trajectory events from empty steps")
    events: list[dict[str, Any]] = []
    last = len(canonical_steps) - 1
    for step_index, step in enumerate(canonical_steps):
        event_type = EVENT_INITIAL if step_index == 0 else EVENT_TERMINAL if step_index == last else EVENT_ACTION
        events.append({
            "schema_version": RAW_TRAJECTORY_SCHEMA_VERSION,
            "event_type": event_type,
            "step_index": step_index,
            "acting_seat": _acting_seat(step),
            "public_step": list(step),
            "legal_action_check": {f"seat_{i}_status": seat.get("status") for i, seat in enumerate(step)},
        })
    return events


def write_immutable_json(path: Path, value: Any) -> None:
    new_bytes = canonical_json_bytes(value)
    if path.exists():
        if path.read_bytes() != new_bytes if False else json.loads(path.read_text(encoding="utf-8")) != value:
            # tolerate re-serialization formatting differences; compare by value not bytes
            if canonical_json_bytes(json.loads(path.read_text(encoding="utf-8"))) != new_bytes:
                raise ImmutableEvidenceConflict(f"refusing to overwrite immutable evidence with different content: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _write_gzip_jsonl(path: Path, events: list[dict[str, Any]]) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = "\n".join(json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":")) for event in events) + "\n"
    raw_bytes = lines.encode("utf-8")
    with gzip.GzipFile(filename="", mode="wb", fileobj=open(path, "wb"), mtime=0) as handle:
        handle.write(raw_bytes)
    return path.read_bytes()


def persist_game_evidence(evidence_root: Path, game_dir_id: str, *, canonical_steps: Sequence[Sequence[Mapping[str, Any]]],
                           runtime_digests: Mapping[str, Any], metadata: Mapping[str, Any]) -> dict[str, Any]:
    events = build_raw_events(canonical_steps)
    for event in events:
        assert_public_only(event)  # fail closed: raises before anything is written
    game_dir = Path(evidence_root) / "games" / game_dir_id
    jsonl_gz_path = game_dir / "public_trajectory.jsonl.gz"
    compressed = _write_gzip_jsonl(jsonl_gz_path, events)
    manifest = {
        "schema_version": TRAJECTORY_MANIFEST_SCHEMA_VERSION,
        "game_dir_id": game_dir_id,
        "event_count": len(events),
        "runtime_digests": dict(runtime_digests),
        "privacy_validation": {"status": "PASS", "events_checked": len(events)},
    }
    write_immutable_json(game_dir / "trajectory_manifest.json", manifest)
    write_immutable_json(game_dir / "game_metadata.json", dict(metadata))
    (game_dir / "trajectory_digest.txt").write_text(json.dumps(dict(runtime_digests), sort_keys=True) + "\n", encoding="utf-8")
    hashes = {
        "schema_version": "o6-raw-evidence-hashes-v1",
        "files": {
            "public_trajectory.jsonl.gz": sha256_hex(compressed),
            "trajectory_manifest.json": sha256_hex((game_dir / "trajectory_manifest.json").read_bytes()),
            "game_metadata.json": sha256_hex((game_dir / "game_metadata.json").read_bytes()),
        },
    }
    write_immutable_json(game_dir / "hashes.json", hashes)
    return manifest
```
(Fix the accidental dead `if False else` clause in `write_immutable_json` while implementing — write it simply as: compare `json.loads(existing text)` to `value`, and if unequal raise `ImmutableEvidenceConflict`.)

Tests (`tests/opponents/test_raw_trajectory_evidence.py`):
```python
from __future__ import annotations

import gzip
import json

import pytest

from mage_ptcg.opponents.privacy_gate import PrivacyViolation
from mage_ptcg.opponents.raw_trajectory_evidence import (
    EVENT_ACTION, EVENT_INITIAL, EVENT_TERMINAL, ImmutableEvidenceConflict,
    build_raw_events, persist_game_evidence, write_immutable_json,
)


def _canonical_steps():
    def seat(action, your_index=0, opp_hand=None):
        return {"observation": {"current": {"yourIndex": your_index, "players": [
            {"hand": [] if your_index == 0 else opp_hand}, {"hand": opp_hand if your_index == 0 else []},
        ]}}, "action": action, "status": "ACTIVE"}
    return [
        [seat([]), seat([])],
        [seat([1, 2]), seat([])],
        [seat([]), seat([3])],
        [{**seat([]), "status": "DONE"}, {**seat([]), "status": "DONE"}],
    ]


def test_initial_action_terminal_events_written_in_order():
    events = build_raw_events(_canonical_steps())
    assert [e["event_type"] for e in events] == [EVENT_INITIAL, EVENT_ACTION, EVENT_ACTION, EVENT_TERMINAL]
    assert [e["step_index"] for e in events] == [0, 1, 2, 3]


def test_acting_seat_recorded():
    events = build_raw_events(_canonical_steps())
    assert events[1]["acting_seat"] == 0
    assert events[2]["acting_seat"] == 1
    assert events[0]["acting_seat"] is None  # no action at the initial step


def test_persist_writes_expected_files(tmp_path):
    manifest = persist_game_evidence(
        tmp_path, "pair__match0", canonical_steps=_canonical_steps(),
        runtime_digests={"complete_trajectory_digest": "abc123"},
        metadata={"game_id": "pair#0", "requested_seed": 71000},
    )
    game_dir = tmp_path / "games" / "pair__match0"
    assert (game_dir / "public_trajectory.jsonl.gz").exists()
    assert (game_dir / "trajectory_manifest.json").exists()
    assert (game_dir / "game_metadata.json").exists()
    assert (game_dir / "trajectory_digest.txt").exists()
    assert (game_dir / "hashes.json").exists()
    with gzip.open(game_dir / "public_trajectory.jsonl.gz", "rt", encoding="utf-8") as handle:
        lines = [json.loads(line) for line in handle if line.strip()]
    assert len(lines) == 4
    assert manifest["runtime_digests"]["complete_trajectory_digest"] == "abc123"


def test_privacy_violation_blocks_persist_entirely(tmp_path):
    poisoned = _canonical_steps()
    poisoned[1][0]["observation"]["current"]["players"][1]["hand"] = [{"id": 99}]  # opponent hand leaked
    with pytest.raises(PrivacyViolation):
        persist_game_evidence(tmp_path, "poisoned", canonical_steps=poisoned, runtime_digests={}, metadata={})
    assert not (tmp_path / "games" / "poisoned").exists()


def test_write_immutable_json_idempotent(tmp_path):
    target = tmp_path / "x.json"
    write_immutable_json(target, {"a": 1})
    write_immutable_json(target, {"a": 1})  # same content: no-op, no error
    assert json.loads(target.read_text()) == {"a": 1}


def test_write_immutable_json_rejects_tamper(tmp_path):
    target = tmp_path / "x.json"
    write_immutable_json(target, {"a": 1})
    with pytest.raises(ImmutableEvidenceConflict):
        write_immutable_json(target, {"a": 2})
```

- [ ] Rename the two private helpers in `trajectory.py` to public names; update their two call sites in the same file (`compute_trajectory_digests`) accordingly. Run `tests/opponents/test_trajectory.py` — expect unchanged pass (pure rename).
- [ ] Write the failing tests above to `tests/opponents/test_raw_trajectory_evidence.py`. Run — expect `ModuleNotFoundError`.
- [ ] Create `src/mage_ptcg/opponents/raw_trajectory_evidence.py` (fixing the `write_immutable_json` comparison logic to the clean value-comparison form described above, not the dead-code sketch).
- [ ] Run `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m pytest -q tests/opponents/test_raw_trajectory_evidence.py tests/opponents/test_trajectory.py tests/opponents/test_privacy_gate.py` — expect all pass.
- [ ] `git add src/mage_ptcg/opponents/privacy_gate.py src/mage_ptcg/opponents/raw_trajectory_evidence.py src/mage_ptcg/opponents/trajectory.py tests/opponents/test_privacy_gate.py tests/opponents/test_raw_trajectory_evidence.py tests/opponents/test_trajectory.py`
- [ ] Commit: `feat(o6): persist raw public league trajectories`

---

### Task 3: Independent digest verifier (process-isolated)

**Files:**
- Create: `src/mage_ptcg/opponents/independent_trajectory_verifier.py`
- Test: `tests/opponents/test_independent_trajectory_verifier.py`

**Interfaces:**
- Consumes: only `gzip`, `json`, `hashlib`, `argparse`, `pathlib`, `sys` from stdlib, plus `mage_ptcg.opponents.privacy_gate` (allowed — it's a shared safety check, not the runtime digest algorithm or the League runner) and `mage_ptcg.opponents.errors.OpponentError`. **Must not** import `mage_ptcg.opponents.trajectory`, `mage_ptcg.opponents.league_runtime`, `mage_ptcg.league.actual_runner`, `mage_ptcg.competition_intelligence.canonical`, or `scripts.run_o6_team_league` — enforced by a dedicated test that greps the module source.
- Produces: `class MalformedTrajectoryError(OpponentError)`; `def parse_public_trajectory_jsonl_gz(path) -> list[dict]`; `def reconstruct_canonical_steps(events) -> list[list[dict]]`; `def independent_digest(value, *, domain) -> str`; `def recompute_digests(canonical_steps) -> dict`; `def verify_game(game_dir: Path) -> dict`; `def verify_league_evidence(evidence_root: Path) -> dict`; `def main(argv=None) -> int` (argparse CLI: `--evidence PATH --json`).

```python
"""Independent, process-isolated recomputation of O6 League trajectory digests.

O6-AUD-002 (HIGH) named the exact failure mode this guards against: "calling
the same function twice is not independent recomputation". This module
therefore does NOT import mage_ptcg.opponents.trajectory (the runtime digest
code) or anything from mage_ptcg.league / scripts.run_o6_team_league (the
League runner). It re-implements canonical JSON serialization and the
domain-prefixed SHA-256 digest scheme directly from the same written spec
mage_ptcg.competition_intelligence.canonical documents (sorted keys, fixed
separators, "{DOMAIN_PREFIX}:{domain}:v1\\0" prefix) so that a bug unique to
one implementation cannot silently "confirm" itself. It is meant to be run
in its own subprocess (see cli.py's verify-league-trajectories command and
scripts/run_o6_team_league.py's finalize step), never imported into the
writer process.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from .errors import OpponentError
from .privacy_gate import scan_public_only

_DOMAIN_PREFIX = "mage_ptcg:competition_intelligence"  # duplicated literal by design; see module docstring


class MalformedTrajectoryError(OpponentError):
    pass


def _independent_canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def independent_digest(value: Any, *, domain: str) -> str:
    prefix = f"{_DOMAIN_PREFIX}:{domain}:v1\0".encode("utf-8")
    return hashlib.sha256(prefix + _independent_canonical_json_bytes(value)).hexdigest()


def parse_public_trajectory_jsonl_gz(path: Path) -> list[dict[str, Any]]:
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            lines = [line for line in handle if line.strip()]
    except OSError as exc:
        raise MalformedTrajectoryError(f"cannot read {path}: {exc}") from exc
    events = []
    for line_number, line in enumerate(lines):
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise MalformedTrajectoryError(f"{path}: malformed JSON on line {line_number}: {exc}") from exc
    return events


def reconstruct_canonical_steps(events: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    if not events:
        raise MalformedTrajectoryError("no events to reconstruct")
    ordered = sorted(events, key=lambda e: e.get("step_index", -1))
    if [e.get("step_index") for e in ordered] != list(range(len(ordered))):
        raise MalformedTrajectoryError("step_index sequence has gaps or duplicates")
    if ordered[0].get("event_type") != "INITIAL_PUBLIC_OBSERVATION":
        raise MalformedTrajectoryError("first event is not INITIAL_PUBLIC_OBSERVATION")
    if ordered[-1].get("event_type") != "TERMINAL_PUBLIC_OBSERVATION":
        raise MalformedTrajectoryError("last event is not TERMINAL_PUBLIC_OBSERVATION (missing terminal)")
    steps = []
    for event in ordered:
        step = event.get("public_step")
        if not isinstance(step, list):
            raise MalformedTrajectoryError(f"event at step {event.get('step_index')} has no public_step")
        steps.append(step)
    return steps


def _action_trace(canonical_steps: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    return [
        {"step": step_index, "seat": seat_index, "action": seat.get("action")}
        for step_index, step in enumerate(canonical_steps)
        for seat_index, seat in enumerate(step)
        if seat.get("action")
    ]


def recompute_digests(canonical_steps: list[list[dict[str, Any]]]) -> dict[str, Any]:
    return {
        "initial_observation_digest": independent_digest(canonical_steps[0], domain="o6-trajectory-initial"),
        "terminal_observation_digest": independent_digest(canonical_steps[-1], domain="o6-trajectory-terminal"),
        "action_trace_digest": independent_digest(_action_trace(canonical_steps), domain="o6-trajectory-actions"),
        "complete_trajectory_digest": independent_digest(canonical_steps, domain="o6-trajectory-complete"),
    }


def verify_game(game_dir: Path) -> dict[str, Any]:
    game_dir = Path(game_dir)
    result: dict[str, Any] = {"game_dir_id": game_dir.name, "malformed": False, "privacy_valid": True, "match": False}
    try:
        manifest = json.loads((game_dir / "trajectory_manifest.json").read_text(encoding="utf-8"))
        hashes = json.loads((game_dir / "hashes.json").read_text(encoding="utf-8"))
        jsonl_path = game_dir / "public_trajectory.jsonl.gz"
        actual_hash = hashlib.sha256(jsonl_path.read_bytes()).hexdigest()
        if actual_hash != hashes["files"]["public_trajectory.jsonl.gz"]:
            raise MalformedTrajectoryError("public_trajectory.jsonl.gz hash mismatch (tamper or corruption)")
        events = parse_public_trajectory_jsonl_gz(jsonl_path)
        for event in events:
            if scan_public_only(event)["status"] != "PASS":
                result["privacy_valid"] = False
        canonical_steps = reconstruct_canonical_steps(events)
        independent = recompute_digests(canonical_steps)
        runtime = manifest.get("runtime_digests", {})
        match = all(independent[key] == runtime.get(key) for key in independent)
        result.update({
            "event_count": len(events), "runtime_digests": runtime, "independent_digests": independent,
            "match": match, "schema_valid": True,
        })
    except MalformedTrajectoryError as exc:
        result["malformed"] = True
        result["error"] = str(exc)
    return result


def verify_league_evidence(evidence_root: Path) -> dict[str, Any]:
    evidence_root = Path(evidence_root)
    games_dir = evidence_root / "games"
    game_dirs = sorted(p for p in games_dir.iterdir() if p.is_dir()) if games_dir.exists() else []
    per_game = [verify_game(path) for path in game_dirs]
    verified = [g for g in per_game if not g["malformed"] and g["match"] and g["privacy_valid"]]
    mismatches = [g for g in per_game if not g["malformed"] and not g["match"]]
    malformed = [g for g in per_game if g["malformed"]]
    privacy_violations = [g for g in per_game if not g["malformed"] and not g["privacy_valid"]]
    complete_digests = [g["independent_digests"]["complete_trajectory_digest"] for g in verified]
    initial_digests = [g["independent_digests"]["initial_observation_digest"] for g in verified]
    action_digests = [g["independent_digests"]["action_trace_digest"] for g in verified]
    terminal_digests = [g["independent_digests"]["terminal_observation_digest"] for g in verified]
    return {
        "schema_version": "o6-independent-verification-summary-v1",
        "game_count": len(game_dirs),
        "parsed_event_count": sum(g.get("event_count", 0) for g in per_game),
        "independently_verified_count": len(verified),
        "digest_mismatches": len(mismatches),
        "malformed_trajectories": len(malformed),
        "privacy_violations": len(privacy_violations),
        "unique_initial_observations": len(set(initial_digests)),
        "unique_action_traces": len(set(action_digests)),
        "unique_terminal_observations": len(set(terminal_digests)),
        "unique_complete_trajectories": len(set(complete_digests)),
        "per_game": {g["game_dir_id"]: g for g in per_game},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m mage_ptcg.opponents.independent_trajectory_verifier")
    parser.add_argument("--evidence", required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    summary = verify_league_evidence(Path(args.evidence))
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    else:
        for key, value in summary.items():
            if key != "per_game":
                print(f"{key}: {value}")
    return 0 if (summary["digest_mismatches"] == 0 and summary["malformed_trajectories"] == 0 and summary["privacy_violations"] == 0) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
```

Tests (`tests/opponents/test_independent_trajectory_verifier.py`):
```python
from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

from mage_ptcg.opponents.raw_trajectory_evidence import persist_game_evidence
from mage_ptcg.opponents import independent_trajectory_verifier as verifier
from mage_ptcg.opponents.trajectory import compute_trajectory_digests


def _canonical_steps():
    def seat(action, status="ACTIVE"):
        return {"observation": {"current": {"yourIndex": 0, "players": [{"hand": []}, {"hand": None}]}}, "action": action, "status": status}
    return [
        [seat([]), seat([])],
        [seat([1]), seat([])],
        [{**seat([]), "status": "DONE"}, {**seat([]), "status": "DONE"}],
    ]


FORBIDDEN_IMPORT_MODULES = {
    "mage_ptcg.opponents.trajectory", "mage_ptcg.opponents.league_runtime",
    "mage_ptcg.league.actual_runner", "mage_ptcg.competition_intelligence.canonical",
    "scripts.run_o6_team_league",
}


def test_verifier_source_does_not_import_runtime_digest_or_league_runner():
    source = Path(verifier.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert imported.isdisjoint(FORBIDDEN_IMPORT_MODULES), imported & FORBIDDEN_IMPORT_MODULES


def test_independent_digest_matches_runtime_digest_for_identical_content():
    steps = _canonical_steps()
    runtime = compute_trajectory_digests(steps)
    independent = verifier.recompute_digests(steps)
    assert independent["complete_trajectory_digest"] == runtime["complete_trajectory_digest"]
    assert independent["initial_observation_digest"] == runtime["initial_observation_digest"]
    assert independent["action_trace_digest"] == runtime["action_trace_digest"]
    assert independent["terminal_observation_digest"] == runtime["terminal_observation_digest"]


def test_end_to_end_persist_then_independently_verify_matches(tmp_path):
    steps = _canonical_steps()
    runtime_digests = compute_trajectory_digests(steps)
    persist_game_evidence(tmp_path, "g0", canonical_steps=steps, runtime_digests=runtime_digests, metadata={"game_id": "g0"})
    result = verifier.verify_game(tmp_path / "games" / "g0")
    assert result["match"] is True
    assert result["malformed"] is False
    assert result["privacy_valid"] is True


def test_tampered_runtime_digest_produces_mismatch_not_silent_pass(tmp_path):
    steps = _canonical_steps()
    persist_game_evidence(tmp_path, "g0", canonical_steps=steps, runtime_digests={"complete_trajectory_digest": "not-the-real-digest",
                           "initial_observation_digest": "x", "action_trace_digest": "y", "terminal_observation_digest": "z"}, metadata={})
    result = verifier.verify_game(tmp_path / "games" / "g0")
    assert result["match"] is False


def test_missing_terminal_event_rejected(tmp_path):
    steps = _canonical_steps()
    runtime_digests = compute_trajectory_digests(steps)
    persist_game_evidence(tmp_path, "g0", canonical_steps=steps, runtime_digests=runtime_digests, metadata={})
    # Truncate the raw file to drop the terminal event line.
    import gzip
    path = tmp_path / "games" / "g0" / "public_trajectory.jsonl.gz"
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        lines = [line for line in handle if line.strip()][:-1]
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    # hashes.json now points at the old (correct) hash -> hash-mismatch path catches it first;
    # this test asserts the malformed/mismatch is caught, not silently accepted.
    result = verifier.verify_game(tmp_path / "games" / "g0")
    assert result["malformed"] is True or result["match"] is False


def test_cli_runs_as_separate_subprocess(tmp_path):
    steps = _canonical_steps()
    runtime_digests = compute_trajectory_digests(steps)
    persist_game_evidence(tmp_path, "g0", canonical_steps=steps, runtime_digests=runtime_digests, metadata={})
    completed = subprocess.run(
        [sys.executable, "-m", "mage_ptcg.opponents.independent_trajectory_verifier", "--evidence", str(tmp_path), "--json"],
        cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True,
        env={"PYTHONPATH": "src", "PYTHONDONTWRITEBYTECODE": "1", "PATH": "/usr/bin:/bin"},
    )
    assert completed.returncode == 0, completed.stderr
    assert '"independently_verified_count":1' in completed.stdout.replace(" ", "")
```

- [ ] Write the failing tests above to `tests/opponents/test_independent_trajectory_verifier.py`. Run — expect `ModuleNotFoundError`.
- [ ] Create `src/mage_ptcg/opponents/independent_trajectory_verifier.py` exactly as above.
- [ ] Run `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m pytest -q tests/opponents/test_independent_trajectory_verifier.py -v` — debug the subprocess test's `env` (it needs the venv's `PATH` for `sys.executable` to resolve shared libs; adjust to inherit `os.environ` merged with `PYTHONPATH` override rather than a bare dict if the sandboxed subprocess fails to start) until all pass.
- [ ] `git add src/mage_ptcg/opponents/independent_trajectory_verifier.py tests/opponents/test_independent_trajectory_verifier.py`
- [ ] Commit: `feat(o6): add independent trajectory digest verifier`

---

### Task 4: Wire raw persistence into League runtime + CLI subcommand

**Files:**
- Modify: `src/mage_ptcg/opponents/league_runtime.py` (`play_game` also returns `canonical_steps`)
- Modify: `src/mage_ptcg/opponents/cli.py` (new `verify-league-trajectories` subcommand that shells out to the verifier in its own subprocess)
- Modify: `scripts/run_o6_team_league.py` (persist raw evidence per game; finalize step re-derives statistics from independently-verified digests only; writes `league_run_manifest.json`, `trajectory_summary.json`, `checksums.sha256`)
- Test: extend `tests/opponents/test_core.py` or add `tests/opponents/test_cli_verify_league_trajectories.py`; extend `tests/test_actual_league_runner.py`-adjacent coverage is not needed (that module is untouched) but add `tests/test_run_o6_team_league.py` if one does not already exist — check first with `find . -iname "test_run_o6_team_league*"`.

**Interfaces:**
- Consumes Task 1-3 outputs directly: `persist_game_evidence`, `verify_league_evidence` (via subprocess only from the script/CLI, never imported into a module that also imports `trajectory.py` in the same process as the verifier — the script CAN import both, since the script itself is the orchestrator, but the *verifier's own module* still must not import runtime code, which Task 3's test already locks down).
- Produces: `play_game(...)` result dict gains a `"canonical_steps"` key (list[list[dict]] or `None` on total failure) alongside the existing `"trajectory"` digest dict.

`league_runtime.py` change — in `play_game`, before building the return dict on both the success and fault paths, add:
```python
from .trajectory import canonical_step_seat  # add to existing trajectory import line

canonical_steps = [[canonical_step_seat(seat) for seat in step] for step in environment.steps] if environment is not None and getattr(environment, "steps", None) else None
```
and include `"canonical_steps": canonical_steps` in both returned dicts (fault-path and success-path).

`cli.py` change — add:
```python
def command_verify_league_trajectories(args: argparse.Namespace) -> dict[str, Any]:
    completed = subprocess.run(
        [sys.executable, "-m", "mage_ptcg.opponents.independent_trajectory_verifier", "--evidence", args.evidence, "--json"],
        capture_output=True, text=True, check=False,
    )
    if completed.returncode not in (0, 1):
        raise OpponentError(f"independent verifier crashed: {completed.stderr[-500:]}")
    return json.loads(completed.stdout)
```
and in `parser()`:
```python
item = sub.add_parser("verify-league-trajectories", parents=[common]); item.add_argument("--evidence", required=True); item.set_defaults(handler=command_verify_league_trajectories)
```

`scripts/run_o6_team_league.py` changes:
- Add `--evidence-root` arg, default `REPOSITORY_ROOT / "docs/evidence/o6-opponent-intelligence-v3"`.
- In `run_pair`'s `play()` closure, after building `evidence_record`, if `raw.get("canonical_steps")`:
  ```python
  game_dir_id = f"{pair_id}__match{match_index}".replace("__vs__", "_vs_")
  game_metadata = {
      "schema_version": "o6-raw-game-metadata-v1", "game_id": evidence_record["game_id"], "pair_id": pair_id,
      "participant_a": name_a, "participant_b": name_b, "seat_0_participant": seat_0_participant,
      "seat_1_participant": seat_1_participant, "execution_index": match_index,
      "requested_seed": schedule.get("seed"), "engine_seed_capability": raw.get("engine_seed_support"),
      "runtime_duration_seconds": raw.get("elapsed_seconds"), "latency_seconds": raw.get("elapsed_seconds"),
      "fault": status != "DONE", "timeout": status in {"AGENT_TIMEOUT", "STEP_LIMIT"},
      "crash": status in {"ERROR", "AGENT_ERROR"}, "fallback_usage": 0,
      "winner": winner, "winner_participant": winner_participant, "status": status,
  }
  persist_game_evidence(
      args_evidence_root, game_dir_id, canonical_steps=raw["canonical_steps"],
      runtime_digests={k: trajectory.get(k) for k in ("initial_observation_digest", "action_trace_digest", "terminal_observation_digest", "complete_trajectory_digest")},
      metadata=game_metadata,
  )
  ```
  (thread `evidence_root` through `run_pair`'s signature the same way `output_dir` already is.)
- After the existing per-pair loop in `main()` finishes (after the `for index, (a, b) in enumerate(pairs)` loop, before building `summary`):
  ```python
  verify_result = json.loads(subprocess.run(
      [sys.executable, "-m", "mage_ptcg.opponents.independent_trajectory_verifier", "--evidence", str(evidence_root), "--json"],
      capture_output=True, text=True, check=True, cwd=REPOSITORY_ROOT,
      env={**os.environ, "PYTHONPATH": str(SRC_ROOT), "PYTHONDONTWRITEBYTECODE": "1"},
  ).stdout)
  if verify_result["digest_mismatches"] or verify_result["malformed_trajectories"] or verify_result["privacy_violations"]:
      raise SystemExit(f"independent verification failed: {verify_result}")
  independent_by_game_id = {rec["game_metadata_game_id_placeholder"]: rec for rec in verify_result["per_game"].values()}  # see note below
  ```
  Note: `verify_game`'s per-game result is keyed by `game_dir_id`, not the original `pair_id#index` `game_id`; when rebuilding `verified_trajectory_records` below, look up by re-deriving `game_dir_id` from each `evidence_record` the same way the `play()` closure did (`f"{pair_id}__match{match_index}".replace(...)`), rather than adding a redundant field. Rebuild each trajectory record's four digest fields from `verify_result["per_game"][game_dir_id]["independent_digests"]` before calling `pair_win_rate_statistics` / `aggregate_trajectory_uniqueness` / `fit_bradley_terry` / `deduplicate_by_trajectory`, so final statistics are grounded in independently-recomputed digests, matching section 8/9 of the remediation spec.
- Bump `league_summary.json`'s `schema_version` to `"o6-team-league-summary-v3"` and add `"independent_verification"` (the full `verify_result` minus `per_game` to keep the summary compact, with `per_game` written to a separate `trajectory_summary.json` instead) plus `"digest_basis": "independently_verified"`.
- After writing `league_summary.json`, write `league_run_manifest.json` (participants, pairs, `requested_games_per_pair`, `population_id`, `population_identity_hash`, `environment_version`, `engine_seed_support_status`, a content-derived `league_run_id`) and `trajectory_summary.json` (the full `verify_result`, including `per_game`).
- Finally, compute `checksums.sha256` over every file in `evidence_root` except itself (sorted relative POSIX paths, `sha256sum`-compatible two-space format), and write it last.

- [ ] Check for an existing `tests/test_run_o6_team_league.py`: `find /home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle-o6-opponents-v1 -iname "test_run_o6_team_league*"`. If absent, add a focused test that runs `run_pair`/`main` against a tiny fake `play_game`-equivalent (monkeypatch `mage_ptcg.opponents.league_runtime.play_game` to return fixed 3-step canonical trajectories) to check: raw evidence files land under `--evidence-root`, `league_summary.json` schema_version is v3, and a forced digest mismatch (monkeypatch the independent verifier's `verify_league_evidence` to return a mismatch) makes `main()` raise/exit non-zero instead of writing a "final" `league_summary.json`.
- [ ] Implement the `league_runtime.py`, `cli.py`, and `run_o6_team_league.py` changes above.
- [ ] Run `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m pytest -q tests/opponents tests/test_run_o6_team_league.py` (plus any newly added file) — all pass.
- [ ] `git add -A -- src/mage_ptcg/opponents scripts/run_o6_team_league.py tests` (review `git status --short` first to confirm no unrelated files are swept in)
- [ ] Commit: `test(o6): cover trajectory evidence and privacy boundaries` (bundle Task 3+4 test coverage here if not already committed piecemeal — use judgement to keep commits meaningful, not mechanically one-per-task).

---

### Task 5: Run the new 60-game Team League for real

Not a code task — an operational step using the code from Tasks 1-4.

- [ ] Confirm participants: `find /home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle-o6-opponents-v1/artifacts -iname "opponent_specs.json"` or check `docs/evidence/o6-opponent-intelligence-v2/population_manifest.json` for the population ref used by the prior run's `--population` / `--artifact-store` args (reuse the same population/artifact-store — the audit's `O6-FINAL-001` about the manifest being stale from an approval transition is explicitly a non-blocker; do not attempt to fix population approval status as part of this task).
- [ ] Run:
  ```bash
  cd /home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle-o6-opponents-v1
  PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python scripts/run_o6_team_league.py \
    --artifact-store <same as prior run> --population <same population id as prior run> \
    --cache-dir artifacts/o6-league-v3-cache --output-dir docs/evidence/o6-opponent-intelligence-v3/league \
    --evidence-root docs/evidence/o6-opponent-intelligence-v3 --games-per-pair 10 --base-seed 81000
  ```
  (`--base-seed` deliberately different from the legacy run's `71000` so this is unambiguously a distinct execution, not a replay.)
- [ ] While it runs (expect several minutes for 60 real subprocess-isolated games), do not interrupt it; if it fails partway, use its existing resume support (same `output_dir`, re-run the same command) rather than starting over, consistent with `run_actual_league`'s resumable design.
- [ ] After completion, inspect `docs/evidence/o6-opponent-intelligence-v3/league_summary.json`'s `independent_verification` block and `docs/evidence/o6-opponent-intelligence-v3/trajectory_summary.json` — confirm `digest_mismatches == 0`, `malformed_trajectories == 0`, `privacy_violations == 0`, `game_count == 60` (or record and investigate any shortfall before proceeding — do not paper over a shortfall in the final report).
- [ ] Do not commit `artifacts/o6-league-v3-cache/` (scratch/cache; check `.gitignore` covers it, or add a scoped ignore entry if not — `artifacts/o6-league-v3-cache/` matches no existing ignore pattern, so add one alongside the existing `artifacts/matches/` etc. block in `.gitignore`).

---

### Task 6: Legacy evidence note + docs

**Files:**
- Create: `docs/evidence/o6-opponent-intelligence-v2/LEGACY_NOTE.md` (new file, does not modify existing v2 evidence)
- Modify (append-only new section, do not alter existing prose): `docs/evidence/o6-opponent-intelligence-v2.md`
- Modify: `docs/runbooks/o6-team-quickstart.md`, `docs/status/current_status.md`, `docs/status/handoff.md`, add a short section to `docs/runbooks/` for the League runbook if one exists (`find docs/runbooks -iname "*league*"`) or fold into `o6-team-quickstart.md` if not.
- Test: `python scripts/docs/validate_docs.py`

Content requirements (all must appear, in both the machine-readable `trajectory_summary.json`/`league_summary.json` and the Markdown docs, using the actual numbers from Task 5's real run — never placeholder numbers):
- Legacy run (`o6-opponent-intelligence-v2`) is `legacy_digest_only` / `independently_unverifiable` — no raw trajectory exists for it, so its "60 unique" claim cannot be independently reproduced; treat it as historical/descriptive only.
- New run (`o6-opponent-intelligence-v3`) has raw public trajectory + independent verifier; state the actual `independently_verified_count`, `digest_mismatches`, `unique_complete_trajectories`, `effective_independent_sample_size` from the real run.
- `engine_seed_support_status = ENGINE_SEED_UNSUPPORTED` (or whatever the live run actually reports — re-verify, do not assume it is unchanged from before).
- Privacy boundary: public-only gate description, one line on what it structurally enforces (opponent hand redaction) vs. pattern-based checks (paths/repr/credentials).
- Wilson/Bradley-Terry: both explicitly `descriptive_only`; unique-trajectory Wilson suppressed below N=5; `statistically_supported_ranking: false`.

- [ ] Write `LEGACY_NOTE.md` and the new doc sections using the real numbers from Task 5 (not before Task 5 completes).
- [ ] Run `python scripts/docs/validate_docs.py` (this needs the `.venv`; from the O6 worktree: `.venv/bin/python scripts/docs/validate_docs.py`) — fix any structural violations it reports.
- [ ] `git add docs/evidence/o6-opponent-intelligence-v2/LEGACY_NOTE.md docs/evidence/o6-opponent-intelligence-v2.md docs/runbooks docs/status/current_status.md docs/status/handoff.md docs/evidence/o6-opponent-intelligence-v3`
- [ ] Commit: `docs(o6): record independently verified league evidence`

---

### Task 7: Full verification sweep

- [ ] `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m pytest -q tests/opponents`
- [ ] `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m pytest -q tests/competition_intelligence tests/test_run_o5_benchmark_cli.py tests/test_actual_league_runner.py tests/test_actual_league_cli.py`
- [ ] `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m pytest -q`
- [ ] `.venv/bin/python scripts/docs/validate_docs.py`
- [ ] `git diff --check`
- [ ] Confirm protected files unchanged: `git diff --stat 6d0481f -- main.py deck.csv agents/rule_agent.py agents/rule_agent_v1.py src/mage_ptcg/evaluation/promotion.py` (expect empty).
- [ ] Confirm Team Population ID / bundle hashes unchanged unless Task 5 deliberately used a rebuilt population (it should not — Task 5 reuses the existing published population; verify `docs/evidence/o6-opponent-intelligence-v3/league_summary.json`'s `population_identity_hash` equals the value already recorded in `docs/evidence/o6-opponent-intelligence-v2/population_manifest.json`).
- [ ] `git status --short` — confirm clean tree after final commit, and note any remaining untracked files that predate this session (do not delete them without asking).

---

## Self-Review Notes

- Every audit requirement in the task prompt sections 2-11 maps to a task above: raw schema → Task 2, privacy gate → Task 1, digest separation → Task 3, real re-run → Task 5, unique/stat recompute on independent basis → Task 4's finalize step, docs → Task 6, tests → Tasks 1-4's test lists collectively cover raw evidence/digest/privacy/uniqueness/independent-verifier/statistics categories from section 11 (uniqueness-specific tests such as "different game_id only remains duplicate" are exercised implicitly by Task 4's finalize step discarding game_id/execution_index from the digest input — add one explicit unit test for this in Task 2 or 3 if not already covered by `test_trajectory.py`'s existing `test_private_state_and_unrelated_fields_do_not_participate`-style tests).
- No task claims Kaggle submission, merge, push, Champion, or default-agent changes — none are in scope.
