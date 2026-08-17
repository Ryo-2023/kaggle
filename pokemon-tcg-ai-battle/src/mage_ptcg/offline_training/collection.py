"""Collection adapter over the existing resumable actual-cabt collector.

This module never re-implements collection.  It calls
:func:`mage_ptcg.dataops.collector.collect_actual_dataset`, which already
provides deterministic seeds, per-game immutable records, idempotent resume,
privacy scanning, and the episode-group split.

Two sources are supported:

* ``actual`` -- run real cabt self-play; refused when the engine is
  unavailable so a fixture is never silently reported as actual data.
* ``fixture`` -- drive the same collector with a deterministic synthetic match
  runner so the whole pipeline is exercisable without the engine.  The produced
  ``public_summary`` carries ``collection_source == "fixture"`` and
  ``actual_cabt`` is ``ACTUAL_CABT_NOT_RUN``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from mage_ptcg.dataops import collect_actual_dataset


_FIXTURE_READY = {"status": "READY", "engine_seed_supported": False, "actual_execution_allowed": True}


def _card(card_id: int) -> dict[str, object]:
    return {
        "id": card_id, "serial": 0, "playerIndex": 0, "hp": 100, "maxHp": 100,
        "appearThisTurn": False, "energies": [], "energyCards": [], "tools": [], "preEvolution": [],
    }


def _player(card_id: int) -> dict[str, object]:
    return {
        "active": [], "asleep": False, "bench": [], "benchMax": 5, "burned": False,
        "confused": False, "deckCount": 53, "discard": [], "hand": [_card(card_id)],
        "handCount": 1, "paralyzed": False, "poisoned": False, "prize": [object() for _ in range(6)],
    }


def _observation(options: list[object], *, your_index: int, turn: int) -> dict[str, object]:
    return {
        "current": {
            "energyAttached": False, "firstPlayer": 0,
            "players": [_player(100 + turn), _player(700 + turn)],
            "result": -1, "retreated": False, "stadium": [], "stadiumPlayed": False,
            "supporterPlayed": False, "turn": turn, "turnActionCount": 3, "yourIndex": your_index,
        },
        "select": {"context": 0, "maxCount": 1, "minCount": 1, "option": options, "type": 0},
        "step": turn,
    }


def _fixture_options(count: int, variant: int) -> list[object]:
    """Deterministic, varied legal option sets so Rule v0 has distinct targets."""
    catalog: list[object] = [
        {"type": 14},
        {"type": 13, "attackId": 1},
        {"type": 13, "attackId": 2},
        {"type": 7, "index": 0},
        {"type": 12},
    ]
    rotated = catalog[variant % len(catalog):] + catalog[: variant % len(catalog)]
    return rotated[:count]


def _make_fixture_runner(*, decisions_per_seat: int, option_count: int):
    def runner(**kwargs: Any) -> Mapping[str, object]:
        seed = int(kwargs["seed"])
        seat0 = kwargs["agent_a_factory"]([1] * 60, seed)
        seat1 = kwargs["agent_b_factory"]([1] * 60, seed + 1)
        for step in range(decisions_per_seat):
            variant = (seed + step) % 5
            options = _fixture_options(option_count, variant)
            seat0(_observation(options, your_index=0, turn=step + 1))
            seat1(_observation(list(reversed(options)), your_index=1, turn=step + 1))
        seat0({"registration": "deck"})  # non-decision prompt, must be skipped
        return {"status": "DONE", "winner": 0, "elapsed_seconds": 0.01}

    return runner


class CollectionUnavailableError(RuntimeError):
    """Raised when actual collection is requested but the engine is unavailable."""


def run_collection(
    *,
    source: str,
    run_id: str,
    games: int,
    base_seed: int,
    output_root: str | Path,
    canonical_base_sha: str,
    deck_path: str | Path,
    repository_root: str | Path,
    max_steps: int = 10_000,
    validation_percent: int = 20,
    split_seed: int = 0,
    fixture_decisions_per_seat: int = 3,
    fixture_option_count: int = 3,
) -> dict[str, Any]:
    """Run collection and return the collector's public summary, annotated."""
    if source == "actual":
        from scripts.cabt_capability import diagnose_cabt_capability

        report = dict(diagnose_cabt_capability())
        if report.get("status") != "READY":
            raise CollectionUnavailableError("ACTUAL_CABT_NOT_RUN")
        summary = collect_actual_dataset(
            run_id=run_id, games=games, base_seed=base_seed, output_root=output_root,
            canonical_base_sha=canonical_base_sha, deck_path=deck_path,
            repository_root=repository_root, max_steps=max_steps,
            validation_percent=validation_percent, split_seed=split_seed,
            capability_report=report,
        )
        summary = dict(summary)
        summary["collection_source"] = "actual"
        summary["actual_cabt"] = "ACTUAL_CABT_RUN"
        return summary

    if source != "fixture":
        raise ValueError(f"unknown collection source {source!r}")

    runner = _make_fixture_runner(
        decisions_per_seat=fixture_decisions_per_seat, option_count=fixture_option_count
    )
    summary = collect_actual_dataset(
        run_id=run_id, games=games, base_seed=base_seed, output_root=output_root,
        canonical_base_sha=canonical_base_sha, deck_path=deck_path,
        repository_root=repository_root, max_steps=max_steps,
        validation_percent=validation_percent, split_seed=split_seed,
        match_runner=runner, capability_report=dict(_FIXTURE_READY),
        source_revision="offline-training-v1-fixture",
    )
    summary = dict(summary)
    summary["collection_source"] = "fixture"
    summary["actual_cabt"] = "ACTUAL_CABT_NOT_RUN"
    return summary


def collection_dataset_path(output_root: str | Path, run_id: str) -> Path:
    return Path(output_root) / run_id / "private_dataset" / "rule-bc-v1.jsonl"


__all__ = [
    "CollectionUnavailableError",
    "collection_dataset_path",
    "run_collection",
]
