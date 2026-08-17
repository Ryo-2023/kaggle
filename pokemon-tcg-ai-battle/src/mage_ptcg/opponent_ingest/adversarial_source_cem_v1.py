"""Self-owned adversarial meta-source generation primitives.

The source-side CEM is deliberately separate from the policy-side CEM.  It
uses the same sealed, actor-visible P1 parameter surface, but its black-box
objective is the *opponent* score against the immutable P1 package.  Only
terminal WDL, seat, and opponent identity are consumed by the objective; the
source is never trained from action labels or private traces.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Mapping, Sequence

from mage_ptcg.meta_specialist.cg_p1_parameterization_v1 import (
    P1ParameterConfig,
    materialize_parameterized_package,
)


SCHEMA_V1 = "meta-specialist-adversarial-source-cem-v1"
SOURCE_V1 = "self_owned_adversarial_source_cem"
USAGE_BOUNDARY_V1 = "local_eval_only"
DEFAULT_SEAT_GAP_LIMIT_V1 = 0.05
_SHA64 = re.compile(r"^[0-9a-f]{64}$")
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$")


class AdversarialSourceError(ValueError):
    """Raised when an adversarial source artifact is not closed or safe."""


def _sha(value: object, field: str) -> str:
    if type(value) is not str or _SHA64.fullmatch(value) is None:
        raise AdversarialSourceError(f"{field} must be a lowercase SHA-256")
    return value


def source_candidate_id_v1(
    config: P1ParameterConfig, *, generation: int, index: int
) -> str:
    """Return a domain-separated deterministic identity for one source point."""

    if not isinstance(config, P1ParameterConfig):
        raise AdversarialSourceError("config must be P1ParameterConfig")
    config.validate()
    if type(generation) is not int or generation < 0:
        raise AdversarialSourceError("generation must be a non-negative integer")
    if type(index) is not int or index < 0:
        raise AdversarialSourceError("index must be a non-negative integer")
    return (
        f"adversarial-source-g{generation:02d}-c{index:02d}-"
        f"{config.config_sha256()[:12]}"
    )


def aggregate_source_rows_v1(
    rows: Sequence[Mapping[str, object]],
    *,
    candidate_policy_id: str,
    opponent_id: str,
    seat_gap_limit: float = DEFAULT_SEAT_GAP_LIMIT_V1,
) -> dict[str, object]:
    """Aggregate an opponent candidate using terminal WDL only.

    ``rows`` may contain a rich evaluator row, but this function intentionally
    reads only ``policy_id``, ``opponent_id``, ``seat`` and ``outcome``.  The
    objective is the candidate's score rate minus fault rate.  A candidate is
    valid only when it has no faults and both seats are populated without a
    complete seat collapse; the measured seat gap is retained for later
    independent confirmation.
    """

    if not isinstance(candidate_policy_id, str) or not candidate_policy_id:
        raise AdversarialSourceError("candidate_policy_id must be non-empty")
    if not isinstance(opponent_id, str) or not opponent_id:
        raise AdversarialSourceError("opponent_id must be non-empty")
    if type(seat_gap_limit) not in (int, float) or not 0.0 <= float(seat_gap_limit) <= 1.0:
        raise AdversarialSourceError("seat_gap_limit must be in [0,1]")

    selected = [
        row
        for row in rows
        if isinstance(row, Mapping)
        and row.get("policy_id") == candidate_policy_id
        and row.get("opponent_id") == opponent_id
    ]
    if not selected:
        raise AdversarialSourceError("source evaluation has no matching rows")

    counts = {"win": 0, "draw": 0, "loss": 0, "fault": 0}
    by_seat = {
        "0": {"win": 0, "draw": 0, "loss": 0, "fault": 0},
        "1": {"win": 0, "draw": 0, "loss": 0, "fault": 0},
    }
    for row in selected:
        outcome = str(row.get("outcome", "fault"))
        if outcome not in counts:
            outcome = "fault"
        counts[outcome] += 1
        seat = str(row.get("seat", "-1"))
        if seat in by_seat:
            by_seat[seat][outcome] += 1

    requested = len(selected)
    faults = counts["fault"]
    source_score = (counts["win"] + 0.5 * counts["draw"]) / requested
    fault_rate = faults / requested
    seat_rates: dict[str, float | None] = {}
    for seat, seat_counts in by_seat.items():
        seat_games = sum(seat_counts.values())
        seat_rates[seat] = (
            (seat_counts["win"] + 0.5 * seat_counts["draw"]) / seat_games
            if seat_games
            else None
        )
    populated = [value for value in seat_rates.values() if value is not None]
    seat_gap = abs(populated[0] - populated[1]) if len(populated) == 2 else None
    seat_collapse = bool(populated and min(populated) < 0.02)
    valid = faults == 0 and len(populated) == 2 and not seat_collapse
    objective = source_score - fault_rate
    return {
        "requested_games": requested,
        "wins": counts["win"],
        "draws": counts["draw"],
        "losses": counts["loss"],
        "faults": faults,
        "source_score": source_score,
        "fault_rate": fault_rate,
        "objective": objective,
        "valid": valid,
        "seat_collapse": seat_collapse,
        "seat_rates": seat_rates,
        "seat_gap": seat_gap,
        "seat_safe": seat_gap is None or seat_gap <= float(seat_gap_limit),
        "action_trace_used": False,
        "private_fields_used": False,
        "teacher_labels_used": False,
    }


def build_source_pool_row_v1(
    *,
    candidate_id: str,
    policy_sha256: str,
    canonical_deck_hash: str,
    smoke_ok: bool,
) -> dict[str, object]:
    """Build the closed manifest row used when a source passes smoke."""

    if not isinstance(candidate_id, str) or _ID.fullmatch(candidate_id) is None:
        raise AdversarialSourceError("candidate_id is malformed")
    _sha(policy_sha256, "policy_sha256")
    _sha(canonical_deck_hash, "canonical_deck_hash")
    if type(smoke_ok) is not bool:
        raise AdversarialSourceError("smoke_ok must be boolean")
    return {
        "id": candidate_id,
        "policy_hash": policy_sha256,
        "canonical_deck_hash": canonical_deck_hash,
        "source": SOURCE_V1,
        "usage_boundary": USAGE_BOUNDARY_V1,
        "smoke_ok": smoke_ok,
    }


def materialize_adversarial_source_package(
    *,
    source_package: Path | str,
    output_package: Path | str,
    config: P1ParameterConfig,
    candidate_id: str,
) -> dict[str, object]:
    """Render one source candidate from the sealed P1 package.

    The generated package keeps the exact P1 deck and legal agent contract;
    only the existing parameterized score overlay and a source provenance
    manifest are added.  The package is research-only and never submission
    eligible by this function.
    """

    source = Path(source_package).resolve()
    target = Path(output_package).resolve()
    if target.exists() or target.is_symlink():
        raise AdversarialSourceError(f"output package already exists: {target}")
    if not isinstance(candidate_id, str) or _ID.fullmatch(candidate_id) is None:
        raise AdversarialSourceError("candidate_id is malformed")
    config.validate()
    try:
        base = materialize_parameterized_package(
            source_package=source,
            output_package=target,
            config=config,
            candidate_id=candidate_id,
        )
    except (OSError, ValueError) as exc:
        raise AdversarialSourceError(f"cannot materialize source package: {exc}") from exc
    main_sha = hashlib.sha256((target / "main.py").read_bytes()).hexdigest()
    deck_sha = hashlib.sha256((target / "deck.csv").read_bytes()).hexdigest()
    manifest = {
        "schema_version": SCHEMA_V1,
        "candidate_id": candidate_id,
        "parameter_config": config.as_dict(),
        "parameter_config_sha256": config.config_sha256(),
        "parent_policy_sha256": base["parent_policy_sha256"],
        "policy_sha256": main_sha,
        "deck_file_sha256": deck_sha,
        "source": SOURCE_V1,
        "usage_boundary": USAGE_BOUNDARY_V1,
        "objective_direction": "maximize_source_score_against_p1",
        "action_trace_used": False,
        "private_fields_used": False,
        "teacher_labels_used": False,
        "research_only": True,
    }
    (target / "adversarial_source_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


__all__ = [
    "SCHEMA_V1",
    "SOURCE_V1",
    "USAGE_BOUNDARY_V1",
    "AdversarialSourceError",
    "aggregate_source_rows_v1",
    "build_source_pool_row_v1",
    "materialize_adversarial_source_package",
    "source_candidate_id_v1",
]
