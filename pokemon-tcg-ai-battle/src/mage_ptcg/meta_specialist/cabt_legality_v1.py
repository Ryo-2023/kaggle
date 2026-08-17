"""Real CABT legality probe for seed decks.

`decks.qualify_deck_asset` needs a `CabtLegality` callback and will only mark a
deck `passed` when that callback returns `(True, evidence)`.  This module is the
only implementation, and it reaches that outcome exclusively by running a real
game through the shipped simulator entry point.

The evidence string is a canonical JSON record of what was actually executed:
the engine entry point and its exact bytes, the deck digest, the seed, the
terminal status, the winner, the turn count, and the wall time.  There is no
path here that produces `True` without a completed game, because a fabricated
"legal" verdict would silently defeat every downstream qualification gate.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import tempfile
import time
from typing import Any, Callable

from mage_ptcg.meta_specialist.local_dataset_v2 import canonical_json_bytes_v2


CABT_LEGALITY_SCHEMA_V1 = "specialist-cabt-legality-v1"
DEFAULT_PROBE_SEED_V1 = 20260803
DEFAULT_MAX_STEPS_V1 = 4_000
_TERMINAL_OK_V1 = "DONE"

RunMatchFn = Callable[..., dict[str, Any]]


class CabtLegalityV1Error(ValueError):
    """Raised when the legality probe cannot be executed as specified."""


@dataclass(frozen=True, slots=True)
class CabtProbeOutcomeV1:
    """One executed probe; `legal` is only ever set from a completed game."""

    legal: bool
    evidence: str
    status: str
    winner: object
    turns: object
    elapsed_seconds: float


def _load_run_match() -> tuple[RunMatchFn, str, str]:
    """Import the shipped simulator entry point and pin its exact bytes."""
    try:
        from scripts.test_sim import run_match  # noqa: PLC0415 - engine is optional at import time
    except Exception as exc:  # pragma: no cover - environment without the engine
        raise CabtLegalityV1Error(
            "CABT engine entry point scripts.test_sim.run_match is unavailable"
        ) from exc
    module_path = Path(getattr(run_match, "__globals__", {}).get("__file__", ""))
    if not module_path.is_file():
        raise CabtLegalityV1Error("could not locate the CABT engine source file")
    digest = hashlib.sha256(module_path.read_bytes()).hexdigest()
    return run_match, str(module_path), digest


def _write_deck(directory: Path, card_ids: tuple[int, ...]) -> Path:
    if type(card_ids) is not tuple or not card_ids:
        raise CabtLegalityV1Error("card_ids must be a nonempty tuple")
    if any(type(value) is not int or value < 1 for value in card_ids):
        raise CabtLegalityV1Error("card_ids must be positive ints")
    path = directory / "deck.csv"
    path.write_text("\n".join(str(value) for value in card_ids) + "\n", encoding="utf-8")
    return path


def probe_deck_legality_v1(
    card_ids: tuple[int, ...],
    *,
    opponent_card_ids: tuple[int, ...] | None = None,
    seed: int = DEFAULT_PROBE_SEED_V1,
    max_steps: int = DEFAULT_MAX_STEPS_V1,
    run_match: RunMatchFn | None = None,
    engine_identity: tuple[str, str] | None = None,
) -> CabtProbeOutcomeV1:
    """Run one real game with this deck and report exactly what happened."""
    if run_match is None:
        run_match, module_path, module_digest = _load_run_match()
    else:
        if engine_identity is None:
            raise CabtLegalityV1Error(
                "an injected run_match must be accompanied by an explicit engine identity"
            )
        module_path, module_digest = engine_identity

    deck_digest = hashlib.sha256(
        canonical_json_bytes_v2([int(value) for value in card_ids])
    ).hexdigest()
    started = time.time()
    with tempfile.TemporaryDirectory(prefix="specialist-cabt-probe-") as scratch:
        root = Path(scratch)
        subject_dir = root / "subject"
        subject_dir.mkdir(parents=True, exist_ok=True)
        subject = _write_deck(subject_dir, card_ids)
        opponent_dir = root / "opponent"
        opponent_dir.mkdir(parents=True, exist_ok=True)
        opponent = _write_deck(
            opponent_dir, card_ids if opponent_card_ids is None else opponent_card_ids
        )
        output = root / "out"
        output.mkdir(parents=True, exist_ok=True)
        try:
            result = run_match(
                deck_a_path=str(subject),
                deck_b_path=str(opponent),
                agent_a_name="rule",
                agent_b_name="rule",
                seed=seed,
                output_dir=str(output),
                save_html=False,
                save_result=False,
                max_steps=max_steps,
            )
        except Exception as exc:
            elapsed = time.time() - started
            return CabtProbeOutcomeV1(
                legal=False,
                evidence=canonical_json_bytes_v2({
                    "schema_version": CABT_LEGALITY_SCHEMA_V1,
                    "engine_entry_point": module_path,
                    "engine_source_sha256": module_digest,
                    "deck_digest": deck_digest,
                    "seed": seed,
                    "status": "ENGINE_ERROR",
                    "error": type(exc).__name__,
                    "elapsed_seconds": round(elapsed, 6),
                }).decode("utf-8"),
                status="ENGINE_ERROR",
                winner=None,
                turns=None,
                elapsed_seconds=elapsed,
            )

    elapsed = time.time() - started
    if type(result) is not dict:
        raise CabtLegalityV1Error("run_match must return a mapping")
    status = str(result.get("status"))
    agent_status = result.get("agent_status")
    # A completed game is necessary but not sufficient: an agent fault means the
    # deck was never actually played to a legal conclusion.
    faulted = isinstance(agent_status, (list, tuple)) and any(
        str(value) not in {"DONE", "ACTIVE", "INACTIVE"} for value in agent_status
    )
    legal = status == _TERMINAL_OK_V1 and not faulted
    evidence = canonical_json_bytes_v2({
        "schema_version": CABT_LEGALITY_SCHEMA_V1,
        "engine_entry_point": module_path,
        "engine_source_sha256": module_digest,
        "engine_seed_supported": bool(result.get("engine_seed_supported", False)),
        "deck_digest": deck_digest,
        "card_count": len(card_ids),
        "seed": seed,
        "max_steps": max_steps,
        "status": status,
        "agent_status": [str(value) for value in agent_status]
        if isinstance(agent_status, (list, tuple))
        else None,
        "winner": result.get("winner"),
        "cabt_turn": result.get("cabt_turn"),
        "elapsed_seconds": round(elapsed, 6),
    }).decode("utf-8")
    return CabtProbeOutcomeV1(
        legal=legal,
        evidence=evidence,
        status=status,
        winner=result.get("winner"),
        turns=result.get("cabt_turn"),
        elapsed_seconds=elapsed,
    )


def make_cabt_legality_v1(
    *,
    seed: int = DEFAULT_PROBE_SEED_V1,
    max_steps: int = DEFAULT_MAX_STEPS_V1,
    run_match: RunMatchFn | None = None,
    engine_identity: tuple[str, str] | None = None,
) -> Callable[[tuple[int, ...]], tuple[bool, str]]:
    """Return a `decks.CabtLegality` callback backed by a real game."""

    def legality(card_ids: tuple[int, ...]) -> tuple[bool, str]:
        outcome = probe_deck_legality_v1(
            card_ids,
            seed=seed,
            max_steps=max_steps,
            run_match=run_match,
            engine_identity=engine_identity,
        )
        return outcome.legal, outcome.evidence

    return legality


__all__ = [
    "CABT_LEGALITY_SCHEMA_V1", "CabtLegalityV1Error", "CabtProbeOutcomeV1",
    "DEFAULT_MAX_STEPS_V1", "DEFAULT_PROBE_SEED_V1", "make_cabt_legality_v1",
    "probe_deck_legality_v1",
]
