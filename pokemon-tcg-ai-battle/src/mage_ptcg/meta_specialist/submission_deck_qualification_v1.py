"""Hash-bound qualification artifact for one submission-owned deck.

The builder is the only path that executes CABT.  The verifier reconstructs
the exact qualified asset from immutable deck bytes and a source-bound CABT
evidence record; it grants no promotion or submission authority by itself.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
from typing import Callable

from mage_ptcg.meta_specialist.cabt_legality_v1 import (
    CABT_LEGALITY_SCHEMA_V1,
    DEFAULT_MAX_STEPS_V1,
    DEFAULT_PROBE_SEED_V1,
    make_cabt_legality_v1,
)
from mage_ptcg.meta_specialist.card_vocabulary_registry_v1 import (
    load_production_card_vocabulary_v1,
)
from mage_ptcg.meta_specialist.decks import (
    ArchetypeSpec,
    DeckAssetInput,
    QualifiedDeckAsset,
    qualify_deck_asset,
    require_qualified_deck_asset,
)
from mage_ptcg.meta_specialist.local_dataset_v2 import canonical_json_bytes_v2


SCHEMA_V1 = "meta-specialist-submission-deck-qualification-v1"
PURPOSE_V1 = "SUBMISSION_OWNED_DECK_QUALIFICATION"
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_TOP_KEYS = frozenset(
    {
        "schema_version", "purpose", "deck_path", "source_commit",
        "archetype", "qualified_deck_asset", "production_vocabulary",
        "cabt_evidence_sha256", "authority", "qualification_sha256",
    }
)
_QUALIFIED_KEYS = frozenset(
    {
        "asset_id", "archetype_id", "card_ids", "deck_identity",
        "deck_file_sha256", "source_ref", "source_commit", "asset_class",
        "usage_boundary", "policy_compatibility", "card_database_version",
        "card_count", "cabt_legality_status", "cabt_legality_evidence",
    }
)
_AUTHORITY = {
    "training_authority": False,
    "promotion_authority": False,
    "submission_authority": False,
}


class SubmissionDeckQualificationV1Error(ValueError):
    """Raised when deck qualification provenance is incomplete or altered."""


def _sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _semantic(value: object) -> str:
    return hashlib.sha256(
        SCHEMA_V1.encode("ascii") + b"\0" + canonical_json_bytes_v2(value)
    ).hexdigest()


def _qualified_payload(asset: QualifiedDeckAsset) -> dict[str, object]:
    require_qualified_deck_asset(asset)
    return {
        "asset_id": asset.asset_id,
        "archetype_id": asset.archetype_id,
        "card_ids": list(asset.card_ids),
        "deck_identity": asset.deck_identity,
        "deck_file_sha256": asset.deck_file_sha256,
        "source_ref": asset.source_ref,
        "source_commit": asset.source_commit,
        "asset_class": asset.asset_class,
        "usage_boundary": asset.usage_boundary,
        "policy_compatibility": asset.policy_compatibility,
        "card_database_version": asset.card_database_version,
        "card_count": asset.card_count,
        "cabt_legality_status": asset.cabt_legality_status,
        "cabt_legality_evidence": asset.cabt_legality_evidence,
    }


def _parse_canonical(path: Path) -> dict[str, object]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SubmissionDeckQualificationV1Error("qualification artifact is unreadable") from exc
    if type(value) is not dict or canonical_json_bytes_v2(value) != raw:
        raise SubmissionDeckQualificationV1Error("qualification artifact is not canonical JSON")
    return value


def _atomic_new(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        with temporary.open("xb") as handle:
            handle.write(canonical_json_bytes_v2(value))
            handle.flush()
            os.fsync(handle.fileno())
        if path.exists():
            raise FileExistsError(path)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def build_submission_deck_qualification_v1(
    *,
    repo_root: str | Path,
    deck_path: str | Path,
    output_path: str | Path,
    source_commit: str,
    archetype_id: str = "submission_root_deck",
    core_card_ids: tuple[int, ...] | None = None,
    seed: int = DEFAULT_PROBE_SEED_V1,
    max_steps: int = DEFAULT_MAX_STEPS_V1,
    cabt_legality: Callable[[tuple[int, ...]], tuple[bool, str]] | None = None,
) -> dict[str, object]:
    """Execute one real CABT qualification and publish a new sealed artifact."""
    root = Path(repo_root).resolve()
    deck = Path(deck_path)
    deck = (root / deck).resolve() if not deck.is_absolute() else deck.resolve()
    output = Path(output_path)
    output = (root / output).resolve() if not output.is_absolute() else output.resolve()
    if root not in deck.parents or root not in output.parents:
        raise SubmissionDeckQualificationV1Error("deck/output must remain inside repo root")
    if output.exists():
        raise FileExistsError(output)
    vocabulary = load_production_card_vocabulary_v1()
    input_asset = DeckAssetInput.from_path(
        asset_id="submission-owned-root-deck-v1",
        archetype_id=archetype_id,
        path=deck,
        source_ref=str(deck.relative_to(root)),
        source_commit=source_commit,
        asset_class="deck_only",
        usage_boundary="bundle_allowed",
        policy_compatibility="student-v3-set",
        card_database_version=vocabulary.environment_version,
    )
    core = core_card_ids or (input_asset.card_ids[0],)
    archetype = ArchetypeSpec(archetype_id, (), core, "qualified_not_trained")
    qualified = qualify_deck_asset(
        input_asset,
        archetype,
        known_card_ids=vocabulary.recognized_card_ids,
        cabt_legality=(
            cabt_legality
            if cabt_legality is not None
            else make_cabt_legality_v1(seed=seed, max_steps=max_steps)
        ),
    )
    evidence_sha = hashlib.sha256(qualified.cabt_legality_evidence.encode("utf-8")).hexdigest()
    payload: dict[str, object] = {
        "schema_version": SCHEMA_V1,
        "purpose": PURPOSE_V1,
        "deck_path": str(deck.relative_to(root)),
        "source_commit": source_commit,
        "archetype": {
            "runtime_id": archetype.runtime_id,
            "core_card_ids": list(archetype.core_card_ids),
            "candidate_status": archetype.candidate_status,
        },
        "qualified_deck_asset": _qualified_payload(qualified),
        "production_vocabulary": vocabulary.to_manifest_dict(),
        "cabt_evidence_sha256": evidence_sha,
        "authority": dict(_AUTHORITY),
        "qualification_sha256": None,
    }
    payload["qualification_sha256"] = _semantic(
        {key: value for key, value in payload.items() if key != "qualification_sha256"}
    )
    try:
        _atomic_new(output, payload)
        verify_submission_deck_qualification_v1(output, root)
    except BaseException:
        output.unlink(missing_ok=True)
        raise
    return payload


def verify_submission_deck_qualification_v1(
    path: str | Path, repo_root: str | Path
) -> tuple[dict[str, object], QualifiedDeckAsset]:
    """Verify primary evidence and reissue the process-local qualified capability."""
    root = Path(repo_root).resolve()
    artifact_path = Path(path).resolve()
    payload = _parse_canonical(artifact_path)
    if set(payload) != _TOP_KEYS:
        raise SubmissionDeckQualificationV1Error("qualification has an invalid closed schema")
    if (
        payload.get("schema_version") != SCHEMA_V1
        or payload.get("purpose") != PURPOSE_V1
        or payload.get("authority") != _AUTHORITY
    ):
        raise SubmissionDeckQualificationV1Error("qualification schema/purpose/authority mismatch")
    supplied = payload.get("qualification_sha256")
    if type(supplied) is not str or _SHA.fullmatch(supplied) is None or supplied != _semantic(
        {key: value for key, value in payload.items() if key != "qualification_sha256"}
    ):
        raise SubmissionDeckQualificationV1Error("qualification semantic SHA-256 mismatch")
    relative = payload.get("deck_path")
    if type(relative) is not str or Path(relative).is_absolute():
        raise SubmissionDeckQualificationV1Error("qualification deck path is invalid")
    deck = (root / relative).resolve()
    if root not in deck.parents or str(deck.relative_to(root)) != relative:
        raise SubmissionDeckQualificationV1Error("qualification deck path escapes repo root")
    qualified_payload = payload.get("qualified_deck_asset")
    if type(qualified_payload) is not dict or set(qualified_payload) != _QUALIFIED_KEYS:
        raise SubmissionDeckQualificationV1Error("qualified deck payload is invalid")
    if (
        qualified_payload.get("usage_boundary") != "bundle_allowed"
        or qualified_payload.get("cabt_legality_status") != "passed"
        or qualified_payload.get("source_commit") != payload.get("source_commit")
        or qualified_payload.get("source_ref") != relative
        or qualified_payload.get("deck_file_sha256") != _sha_file(deck)
    ):
        raise SubmissionDeckQualificationV1Error("qualified deck primary binding mismatch")
    vocabulary = load_production_card_vocabulary_v1()
    if payload.get("production_vocabulary") != vocabulary.to_manifest_dict():
        raise SubmissionDeckQualificationV1Error("production vocabulary binding mismatch")
    evidence = qualified_payload.get("cabt_legality_evidence")
    if type(evidence) is not str or hashlib.sha256(evidence.encode()).hexdigest() != payload.get(
        "cabt_evidence_sha256"
    ):
        raise SubmissionDeckQualificationV1Error("CABT evidence SHA-256 mismatch")
    try:
        evidence_payload = json.loads(evidence)
    except json.JSONDecodeError as exc:
        raise SubmissionDeckQualificationV1Error("CABT evidence is invalid JSON") from exc
    cards = qualified_payload.get("card_ids")
    expected_deck_digest = hashlib.sha256(canonical_json_bytes_v2(cards)).hexdigest()
    engine_path = Path(__file__).resolve().parents[3] / "scripts/test_sim.py"
    if (
        type(evidence_payload) is not dict
        or evidence_payload.get("schema_version") != CABT_LEGALITY_SCHEMA_V1
        or evidence_payload.get("status") != "DONE"
        or evidence_payload.get("card_count") != 60
        or evidence_payload.get("deck_digest") != expected_deck_digest
        or evidence_payload.get("engine_source_sha256") != _sha_file(engine_path)
        or any(
            value not in {"DONE", "ACTIVE", "INACTIVE"}
            for value in evidence_payload.get("agent_status", [])
        )
    ):
        raise SubmissionDeckQualificationV1Error("CABT primary evidence is not a legal DONE game")
    archetype_payload = payload.get("archetype")
    if type(archetype_payload) is not dict or set(archetype_payload) != {
        "runtime_id", "core_card_ids", "candidate_status"
    }:
        raise SubmissionDeckQualificationV1Error("qualification archetype is invalid")
    asset_input = DeckAssetInput.from_path(
        asset_id=str(qualified_payload["asset_id"]),
        archetype_id=str(qualified_payload["archetype_id"]),
        path=deck,
        source_ref=relative,
        source_commit=str(payload["source_commit"]),
        asset_class=str(qualified_payload["asset_class"]),
        usage_boundary=str(qualified_payload["usage_boundary"]),
        policy_compatibility=str(qualified_payload["policy_compatibility"]),
        card_database_version=str(qualified_payload["card_database_version"]),
    )
    archetype = ArchetypeSpec(
        str(archetype_payload["runtime_id"]),
        (),
        tuple(archetype_payload["core_card_ids"]),
        str(archetype_payload["candidate_status"]),
    )
    qualified = qualify_deck_asset(
        asset_input,
        archetype,
        known_card_ids=vocabulary.recognized_card_ids,
        cabt_legality=lambda _cards: (True, evidence),
    )
    if _qualified_payload(qualified) != qualified_payload:
        raise SubmissionDeckQualificationV1Error("qualified deck reconstruction mismatch")
    return payload, qualified


__all__ = [
    "PURPOSE_V1", "SCHEMA_V1", "SubmissionDeckQualificationV1Error",
    "build_submission_deck_qualification_v1",
    "verify_submission_deck_qualification_v1",
]
