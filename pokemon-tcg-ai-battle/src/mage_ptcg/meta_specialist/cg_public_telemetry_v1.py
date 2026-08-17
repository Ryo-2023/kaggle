"""Privacy-safe public telemetry for self-owned ``cg`` policies.

The collector is deliberately separate from the submission package.  It
records only the bounded public projection already used by ``cabt_trace``;
hand/deck/prize contents, opaque engine inputs, and raw observations are never
written.  Telemetry failures are reported to the caller so a research runner
can fail closed without changing policy behaviour.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
from collections.abc import Mapping, Sequence
from typing import Any

from mage_ptcg.observability.cabt_trace import (
    FORBIDDEN_OBSERVATION_KEYS,
    find_forbidden_keys,
    normalize_decision_record,
)


SCHEMA = "cg-public-telemetry-v1"
_FORBIDDEN_EXACT_KEYS = frozenset(
    {
        *FORBIDDEN_OBSERVATION_KEYS,
        "hand",
        "deck",
        "prize",
        "raw_observation",
        "search_begin_input",
    }
)


class CgPublicTelemetryError(ValueError):
    """Raised when a telemetry record or package cannot be made safely."""


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise CgPublicTelemetryError(f"regular file required: {path}")
    return _sha256_bytes(path.read_bytes())


def _assert_public_payload(value: object) -> None:
    """Reject exact private/opaque keys in a normalized payload."""

    def walk(node: object) -> None:
        if isinstance(node, Mapping):
            for key, child in node.items():
                if str(key) in _FORBIDDEN_EXACT_KEYS:
                    raise CgPublicTelemetryError(
                        f"telemetry payload contains forbidden key: {key}"
                    )
                walk(child)
        elif isinstance(node, (list, tuple)):
            for child in node:
                walk(child)

    walk(value)
    forbidden = find_forbidden_keys(value)
    if forbidden:
        raise CgPublicTelemetryError(
            f"telemetry payload contains forbidden observation keys: {forbidden}"
        )


def build_public_telemetry_record_v1(
    observation: Mapping[str, object],
    action: Sequence[int],
    *,
    seat: int,
    game_id: str,
    candidate_id: str,
) -> dict[str, object]:
    """Build one bounded, privacy-safe record from a cg agent invocation.

    The deck-registration callback is intentionally reduced to its size only;
    the selected 60 card IDs are never persisted.  Decision rows delegate to
    the tested ``cabt_trace`` public projection and add only research identity
    metadata.
    """

    if not isinstance(observation, Mapping):
        raise CgPublicTelemetryError("observation must be a mapping")
    if type(seat) is not int or seat not in (0, 1):
        raise CgPublicTelemetryError("seat must be 0 or 1")
    if not isinstance(game_id, str) or not game_id:
        raise CgPublicTelemetryError("game_id must be non-empty")
    if not isinstance(candidate_id, str) or not candidate_id:
        raise CgPublicTelemetryError("candidate_id must be non-empty")
    action_list = list(action)
    if not all(type(value) is int for value in action_list):
        raise CgPublicTelemetryError("action must contain integer indices")

    if observation.get("select") is None:
        record: dict[str, object] = {
            "schema_version": SCHEMA,
            "record_type": "deck_registration_redacted",
            "game_id": game_id,
            "candidate_id": candidate_id,
            "seat": seat,
            "deck_size": len(action_list),
        }
    else:
        try:
            normalized = normalize_decision_record(
                observation,
                action_list,
                seat=seat,
                episode_index=0,
                decision_index=0,
                seat_decision_index=0,
                engine_seed_supported=False,
            )
        except Exception as exc:  # normalize errors are fail-closed for telemetry
            raise CgPublicTelemetryError(
                f"public projection failed: {type(exc).__name__}"
            ) from exc
        record = {
            **normalized,
            "schema_version": SCHEMA,
            "record_type": "decision",
            "game_id": game_id,
            "candidate_id": candidate_id,
            "seat": seat,
        }
    _assert_public_payload(record)
    return record


def append_public_telemetry_record_v1(path: Path | str, record: Mapping[str, object]) -> str:
    """Append one canonical JSONL row and return its line SHA."""

    _assert_public_payload(record)
    destination = Path(path).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(dict(record), ensure_ascii=False, sort_keys=True, allow_nan=False).encode("utf-8") + b"\n"
    with destination.open("ab") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
    return _sha256_bytes(raw)


def _wrapper_source(candidate_id: str) -> str:
    encoded_id = json.dumps(candidate_id, ensure_ascii=False)
    return f'''"""Research-only public telemetry wrapper for {candidate_id}."""

from __future__ import annotations

import os

from cg_base import agent as _base_agent
from mage_ptcg.meta_specialist.cg_public_telemetry_v1 import (
    append_public_telemetry_record_v1,
    build_public_telemetry_record_v1,
)


_CANDIDATE_ID = {encoded_id}


def agent(observation):
    action = _base_agent(observation)
    path = os.environ.get("CG_PUBLIC_TELEMETRY_PATH")
    if path:
        try:
            record = build_public_telemetry_record_v1(
                observation,
                action,
                seat=int(os.environ.get("CG_PUBLIC_TELEMETRY_SEAT", "0")),
                game_id=os.environ.get("CG_PUBLIC_TELEMETRY_GAME_ID", "unknown"),
                candidate_id=_CANDIDATE_ID,
            )
            append_public_telemetry_record_v1(path, record)
        except Exception:
            # Telemetry is observational only; never alter the candidate's
            # legal action or turn result when a projection is unavailable.
            pass
    return action
'''


def materialize_telemetry_package_v1(
    *,
    source_package: Path | str,
    output_package: Path | str,
    candidate_id: str,
) -> dict[str, object]:
    """Copy a package and replace only ``main.py`` with a telemetry wrapper."""

    source = Path(source_package).resolve()
    target = Path(output_package).resolve()
    if not source.is_dir() or not (source / "main.py").is_file() or not (source / "deck.csv").is_file():
        raise CgPublicTelemetryError(f"source package is incomplete: {source}")
    if target.exists() or target.is_symlink():
        raise FileExistsError(f"telemetry package output exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target)
    source_main = target / "main.py"
    original = source_main.read_bytes()
    source_main.unlink()
    (target / "cg_base.py").write_bytes(original)
    wrapper = _wrapper_source(candidate_id).encode("utf-8")
    (target / "main.py").write_bytes(wrapper)
    return {
        "schema_version": SCHEMA,
        "candidate_id": candidate_id,
        "source_package": str(source),
        "output_package": str(target),
        "source_policy_sha256": _sha256_bytes(original),
        "wrapper_policy_sha256": _sha256_bytes(wrapper),
        "deck_sha256": _sha256_file(target / "deck.csv"),
        "research_only": True,
    }


__all__ = [
    "SCHEMA",
    "CgPublicTelemetryError",
    "append_public_telemetry_record_v1",
    "build_public_telemetry_record_v1",
    "materialize_telemetry_package_v1",
]
