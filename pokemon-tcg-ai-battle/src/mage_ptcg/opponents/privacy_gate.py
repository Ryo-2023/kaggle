"""Fail-closed scanner: reject anything that is not clean public cabt trajectory data.

Used both when *writing* raw evidence (fail-closed: refuse to persist) and
again, independently, when *verifying* it (re-check, do not just trust the
writer). Unrecognized suspicious content is rejected, not passed through
(O6-AUD-002 remediation, section 4: privacy/public-only gate).

Two complementary checks:

* a structural check tied to the actual cabt observation shape (confirmed by
  running a real game locally): the acting seat's
  ``observation["current"]["players"]`` is indexed by ``yourIndex``, and the
  non-acting player's ``hand`` is engine-redacted to ``None``. If it is ever
  non-null, that is a genuine opponent-hand leak, not a false positive.
* a generic denylist (key-name and string-value patterns) for content that
  should never appear in trajectory evidence regardless of schema: engine
  internals, Python object reprs, absolute filesystem paths, credentials.

The denylist is necessarily incomplete against a determined attacker; it is
scoped to catch the categories the remediation spec names, and documented as
such rather than claimed to be exhaustive.
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
    r"|<[\w.]+\s+object\s+at\s+0x[0-9a-fA-F]+>"
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
    """Non-raising scan; returns a structured PASS/REJECTED result."""
    violation = _walk(value, path="$")
    if violation is None:
        return {"schema_version": PUBLIC_ONLY_GATE_SCHEMA_VERSION, "status": "PASS", "violation": None}
    path, reason = violation
    return {"schema_version": PUBLIC_ONLY_GATE_SCHEMA_VERSION, "status": "REJECTED", "violation": {"path": path, "reason": reason}}


def assert_public_only(value: Any) -> None:
    """Fail-closed enforcement point: raises before any caller may persist ``value``."""
    result = scan_public_only(value)
    if result["status"] != "PASS":
        violation = result["violation"] or {}
        raise PrivacyViolation(f"public-only gate rejected {violation.get('path')}: {violation.get('reason')}")
