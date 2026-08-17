"""CABT legality probe: a `passed` verdict may only come from a completed real game."""

from __future__ import annotations

from pathlib import Path

import pytest

from mage_ptcg.meta_specialist.cabt_legality_v1 import (
    CABT_LEGALITY_SCHEMA_V1,
    CabtLegalityV1Error,
    make_cabt_legality_v1,
    probe_deck_legality_v1,
)
from mage_ptcg.meta_specialist.local_dataset_v2 import parse_canonical_json_bytes_v2


ROOT = Path(__file__).resolve().parents[2]
IDENTITY = ("/fake/engine.py", "e" * 64)


def _deck() -> tuple[int, ...]:
    text = (ROOT / "deck.csv").read_text(encoding="utf-8")
    return tuple(int(line) for line in text.split() if line.strip().isdigit())


def _fake_run_match(**expected):
    calls: list[dict] = []

    def run_match(**kwargs):
        calls.append(kwargs)
        return dict(expected)

    return run_match, calls


def test_a_completed_faultless_game_is_the_only_route_to_legal() -> None:
    run_match, calls = _fake_run_match(
        status="DONE", winner=0, cabt_turn=41, agent_status=["DONE", "DONE"],
        engine_seed_supported=False,
    )
    outcome = probe_deck_legality_v1(
        _deck(), run_match=run_match, engine_identity=IDENTITY
    )

    assert outcome.legal is True
    assert len(calls) == 1  # the engine really was invoked
    evidence = parse_canonical_json_bytes_v2(outcome.evidence.encode("utf-8"))
    assert evidence["schema_version"] == CABT_LEGALITY_SCHEMA_V1
    assert evidence["status"] == "DONE"
    assert evidence["card_count"] == 60
    assert evidence["engine_source_sha256"] == IDENTITY[1]
    assert len(evidence["deck_digest"]) == 64


@pytest.mark.parametrize(
    "result",
    [
        {"status": "HARD_TIMEOUT", "agent_status": ["DONE", "DONE"]},
        {"status": "DONE", "agent_status": ["AGENT_ERROR", "DONE"]},
        {"status": "DONE", "agent_status": ["DONE", "INVALID"]},
    ],
)
def test_a_timeout_or_agent_fault_is_never_legal(result) -> None:
    run_match, _calls = _fake_run_match(**result)
    outcome = probe_deck_legality_v1(
        _deck(), run_match=run_match, engine_identity=IDENTITY
    )
    assert outcome.legal is False
    assert outcome.evidence.strip()


def test_an_engine_exception_is_reported_not_swallowed_into_legal() -> None:
    def exploding(**_kwargs):
        raise RuntimeError("engine died")

    outcome = probe_deck_legality_v1(
        _deck(), run_match=exploding, engine_identity=IDENTITY
    )
    assert outcome.legal is False
    assert outcome.status == "ENGINE_ERROR"
    assert "engine died" not in outcome.evidence  # the message is not leaked verbatim
    assert parse_canonical_json_bytes_v2(outcome.evidence.encode("utf-8"))["error"] == "RuntimeError"


def test_an_injected_engine_must_declare_its_identity() -> None:
    run_match, _calls = _fake_run_match(status="DONE", agent_status=["DONE", "DONE"])
    with pytest.raises(CabtLegalityV1Error, match="explicit engine identity"):
        probe_deck_legality_v1(_deck(), run_match=run_match)


def test_malformed_card_ids_are_rejected_before_any_game() -> None:
    run_match, calls = _fake_run_match(status="DONE", agent_status=["DONE", "DONE"])
    for bad in ((), (0, 1), (1, "2")):
        with pytest.raises(CabtLegalityV1Error):
            probe_deck_legality_v1(bad, run_match=run_match, engine_identity=IDENTITY)
    assert calls == []


def test_callback_shape_matches_the_qualification_contract() -> None:
    run_match, _calls = _fake_run_match(
        status="DONE", winner=1, cabt_turn=30, agent_status=["DONE", "DONE"]
    )
    legality = make_cabt_legality_v1(run_match=run_match, engine_identity=IDENTITY)
    verdict = legality(_deck())

    # decks.qualify_deck_asset requires exactly (True, nonempty str).
    assert isinstance(verdict, tuple) and len(verdict) == 2
    assert verdict[0] is True and isinstance(verdict[1], str) and verdict[1].strip()


def test_real_engine_plays_the_shipped_deck_to_a_legal_conclusion() -> None:
    """End-to-end against the actual simulator, not a double."""
    pytest.importorskip("kaggle_environments")
    try:
        outcome = probe_deck_legality_v1(_deck(), seed=1, max_steps=2000)
    except CabtLegalityV1Error as exc:
        pytest.skip(f"CABT engine unavailable in this environment: {exc}")

    assert outcome.status == "DONE", outcome.evidence
    assert outcome.legal is True
    evidence = parse_canonical_json_bytes_v2(outcome.evidence.encode("utf-8"))
    assert evidence["engine_entry_point"].endswith("test_sim.py")
    assert len(evidence["engine_source_sha256"]) == 64
    assert outcome.elapsed_seconds > 0.0
