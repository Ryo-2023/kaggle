from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from mage_ptcg.meta_specialist.cabt_legality_v1 import CABT_LEGALITY_SCHEMA_V1
from mage_ptcg.meta_specialist.local_dataset_v2 import canonical_json_bytes_v2
from mage_ptcg.meta_specialist.submission_deck_qualification_v1 import (
    SubmissionDeckQualificationV1Error,
    build_submission_deck_qualification_v1,
    verify_submission_deck_qualification_v1,
)


ROOT = Path(__file__).resolve().parents[2]


def _write_deck(path: Path) -> tuple[int, ...]:
    cards = tuple(range(1, 61))
    path.write_text("".join(f"{value}\n" for value in cards), encoding="utf-8")
    return cards


def _evidence(cards: tuple[int, ...]) -> str:
    engine = ROOT / "scripts/test_sim.py"
    payload = {
        "schema_version": CABT_LEGALITY_SCHEMA_V1,
        "engine_entry_point": str(engine),
        "engine_source_sha256": hashlib.sha256(engine.read_bytes()).hexdigest(),
        "engine_seed_supported": False,
        "deck_digest": hashlib.sha256(canonical_json_bytes_v2(list(cards))).hexdigest(),
        "card_count": 60,
        "seed": 7,
        "max_steps": 2000,
        "status": "DONE",
        "agent_status": ["DONE", "DONE"],
        "winner": 0,
        "cabt_turn": 1,
        "elapsed_seconds": 0.1,
    }
    return canonical_json_bytes_v2(payload).decode("utf-8")


def test_submission_deck_qualification_build_verify_and_tamper(tmp_path: Path) -> None:
    deck = tmp_path / "deck.csv"
    cards = _write_deck(deck)
    output = tmp_path / "qualification.json"
    payload = build_submission_deck_qualification_v1(
        repo_root=tmp_path,
        deck_path=deck,
        output_path=output,
        source_commit="a" * 40,
        cabt_legality=lambda observed: (observed == cards, _evidence(cards)),
    )
    verified, asset = verify_submission_deck_qualification_v1(output, tmp_path)
    assert verified == payload
    assert asset.usage_boundary == "bundle_allowed"
    assert asset.card_ids == cards
    assert asset.cabt_legality_status == "passed"
    assert verified["authority"] == {
        "training_authority": False,
        "promotion_authority": False,
        "submission_authority": False,
    }

    tampered = json.loads(output.read_text(encoding="utf-8"))
    tampered["qualified_deck_asset"]["card_ids"][0] = 61
    output.write_bytes(canonical_json_bytes_v2(tampered))
    with pytest.raises(SubmissionDeckQualificationV1Error, match="semantic SHA-256"):
        verify_submission_deck_qualification_v1(output, tmp_path)


def test_submission_deck_qualification_rejects_non_done_evidence(tmp_path: Path) -> None:
    deck = tmp_path / "deck.csv"
    cards = _write_deck(deck)
    output = tmp_path / "qualification.json"
    bad = json.loads(_evidence(cards))
    bad["status"] = "STEP_LIMIT"
    with pytest.raises(Exception):
        build_submission_deck_qualification_v1(
            repo_root=tmp_path,
            deck_path=deck,
            output_path=output,
            source_commit="b" * 40,
            cabt_legality=lambda _observed: (
                True,
                canonical_json_bytes_v2(bad).decode("utf-8"),
            ),
        )
    assert not output.exists()
