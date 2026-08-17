"""Loader/verifier for the trusted sealed production card-vocabulary registry.

``actor_visible_features_v1.CardVocabularyV1`` is the closed, serial-free
value type every specialist feature and runtime boundary consumes.  Nothing
in that module may fabricate a *qualified* instance of it: a vocabulary is
only bundle-safe if its recognized card IDs and source digest were
independently derived from the EN card database this registry pins, and the
registry's own bytes verify against their own recorded content hash.

This module owns that qualification.  It:

* Loads and content-self-verifies
  ``configs/meta_specialist/card_vocabulary_registry_v1.json`` exactly like
  ``seed_registry.py``'s seed-candidate registry: the file pins its own
  ``content_sha256`` over its remaining fields, and this module never trusts
  ``card_database_sha256``/``card_vocabulary_sha256`` at face value -- it
  re-derives both from the *actual current bytes* of
  ``data/raw/EN_Card_Data.csv`` via ``seed_registry.read_en_card_vocabulary``/
  ``bind_en_card_vocabulary`` and requires an exact match.
* Builds exactly one ``CardVocabularyV1`` from that verified state and seals
  the *specific returned object* to a process-local, weakref-tracked
  issuance record -- the same pattern ``decks.py`` uses for
  ``QualifiedDeckAsset`` and ``seed_registry.py`` uses for
  ``EnCardVocabulary``/``SeedCandidate``.  A hand-built, ``copy``'d, or
  ``dataclasses.replace``-derived ``CardVocabularyV1`` is never that sealed
  object -- even with byte-identical fields -- because the issuance record is
  keyed by object identity (``id()``), not by field equality.
* Re-verifies the registry file from disk on every call to
  :func:`require_registry_issued_card_vocabulary_v1`, not just once at load
  time: a registry that goes missing, gets tampered, or is silently swapped
  for a different self-consistent registry after issuance must still fail
  closed for every vocabulary it previously issued.

Nothing here trains, evaluates, or claims competitive strength for any deck
or card; it only answers "is this specific in-memory vocabulary object the
one a verified sealed registry actually issued."
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from threading import Lock
from weakref import ReferenceType, ref

from mage_ptcg.continuous_league.contracts import content_id, require_sha256
from mage_ptcg.meta_specialist.actor_visible_features_v1 import (
    CardVocabularyV1,
    SpecialistFeatureError,
)
from mage_ptcg.meta_specialist.seed_registry import (
    EnCardVocabulary,
    SeedRegistryError,
    read_en_card_vocabulary,
)


SCHEMA_VERSION = "meta-specialist-card-vocabulary-registry-v1"
_CONTENT_DOMAIN = SCHEMA_VERSION
_ISSUANCE_DOMAIN = "meta-specialist-issued-production-card-vocabulary-v1"
_TOP_LEVEL_KEYS = {
    "schema_version",
    "content_sha256",
    "card_database_sha256",
    "card_vocabulary_sha256",
    "card_id_count",
    "environment_version",
    "usage_decision",
    "permission_decision",
}
_MAX_REGISTRY_BYTES = 64 * 1024
# The registry may only ever seal a vocabulary a bundle is actually allowed
# to ship; ``"test-only"``/``"unqualified"`` decisions have no place here --
# a caller that wants those has ``make_test_card_vocabulary_v1`` already.
_ALLOWED_SEALED_DECISIONS = {"bundle_allowed"}

_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CARD_VOCABULARY_REGISTRY_PATH = (
    _REPO_ROOT / "configs" / "meta_specialist" / "card_vocabulary_registry_v1.json"
)
# ``data/`` is gitignored, so a git worktree checked out separately from the
# main clone may not have its own copy; fall back to the main repository's
# checkout, which is expected to be a sibling of this worktree's grandparent
# directory (``<parent>/pokemon-tcg-ai-battle-worktrees/<name>`` -> the
# sibling ``<parent>/pokemon-tcg-ai-battle``).
_EN_CARD_DATABASE_CANDIDATES = (
    _REPO_ROOT / "data" / "raw" / "EN_Card_Data.csv",
    _REPO_ROOT.parent.parent / "pokemon-tcg-ai-battle" / "data" / "raw" / "EN_Card_Data.csv",
)
DEFAULT_EN_CARD_DATABASE_PATH = next(
    (path for path in _EN_CARD_DATABASE_CANDIDATES if path.is_file()),
    _EN_CARD_DATABASE_CANDIDATES[0],
)


class CardVocabularyRegistryError(ValueError):
    """Raised when the trusted sealed card-vocabulary registry fails to verify."""


@dataclass(frozen=True, slots=True)
class CardVocabularyRegistryV1:
    """One verified, content-addressed pin of the production EN card vocabulary."""

    schema_version: str
    content_sha256: str
    card_database_sha256: str
    card_vocabulary_sha256: str
    card_id_count: int
    environment_version: str
    usage_decision: str
    permission_decision: str


def _reject_duplicate_json_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    document: dict[str, object] = {}
    for key, value in pairs:
        if key in document:
            raise CardVocabularyRegistryError(f"duplicate JSON key: {key}")
        document[key] = value
    return document


def _reject_nonfinite_json(value: str) -> object:
    raise CardVocabularyRegistryError(f"non-finite JSON constant is not allowed: {value}")


def _require_exact_keys(value: object, keys: set[str], location: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise CardVocabularyRegistryError(f"{location} must be an object")
    unknown = set(value).difference(keys)
    missing = keys.difference(value)
    if unknown or missing:
        raise CardVocabularyRegistryError(
            f"{location} has invalid keys: unknown={sorted(unknown)}, missing={sorted(missing)}"
        )
    return value


def _require_sha256(value: object, location: str) -> str:
    if not isinstance(value, str):
        raise CardVocabularyRegistryError(f"{location} must be a string")
    try:
        return require_sha256(value, location)
    except ValueError as exc:
        raise CardVocabularyRegistryError(str(exc)) from exc


def load_card_vocabulary_registry_v1(
    path: str | Path = DEFAULT_CARD_VOCABULARY_REGISTRY_PATH,
) -> CardVocabularyRegistryV1:
    """Load and content-self-verify the trusted sealed registry document.

    Mirrors ``seed_registry.load_seed_candidate_registry``'s discipline:
    duplicate JSON keys, non-finite constants, an unknown/missing key, or a
    ``content_sha256`` that does not match the canonical bytes of the rest of
    the document each raise closed rather than being silently accepted.
    """
    registry_path = Path(path)
    try:
        with registry_path.open(encoding="utf-8") as handle:
            payload_text = handle.read(_MAX_REGISTRY_BYTES + 1)
    except OSError as exc:
        raise CardVocabularyRegistryError(
            f"could not read card vocabulary registry {registry_path}: {exc}"
        ) from exc
    if len(payload_text) > _MAX_REGISTRY_BYTES:
        raise CardVocabularyRegistryError("card vocabulary registry exceeds its maximum size")
    try:
        document = json.loads(
            payload_text,
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=_reject_nonfinite_json,
        )
    except json.JSONDecodeError as exc:
        raise CardVocabularyRegistryError(
            f"card vocabulary registry is not valid JSON: {exc}"
        ) from exc

    top = _require_exact_keys(document, _TOP_LEVEL_KEYS, "card vocabulary registry")
    if top["schema_version"] != SCHEMA_VERSION:
        raise CardVocabularyRegistryError("unsupported card vocabulary registry schema_version")
    stored_content_sha256 = _require_sha256(top["content_sha256"], "content_sha256")
    payload = {key: value for key, value in top.items() if key != "content_sha256"}
    try:
        recomputed_content_sha256 = content_id(_CONTENT_DOMAIN, payload)
    except (TypeError, ValueError) as exc:
        raise CardVocabularyRegistryError(
            f"card vocabulary registry is not canonical JSON: {exc}"
        ) from exc
    if stored_content_sha256 != recomputed_content_sha256:
        raise CardVocabularyRegistryError(
            "content_sha256 does not match canonical card vocabulary registry content"
        )

    card_database_sha256 = _require_sha256(top["card_database_sha256"], "card_database_sha256")
    card_vocabulary_sha256 = _require_sha256(
        top["card_vocabulary_sha256"], "card_vocabulary_sha256"
    )
    card_id_count = top["card_id_count"]
    if type(card_id_count) is not int or card_id_count <= 0:
        raise CardVocabularyRegistryError("card_id_count must be a positive int and not bool")
    environment_version = top["environment_version"]
    if (
        not isinstance(environment_version, str)
        or not environment_version
        or len(environment_version) > 256
    ):
        raise CardVocabularyRegistryError("environment_version must be a nonempty string")
    usage_decision = top["usage_decision"]
    permission_decision = top["permission_decision"]
    if (
        usage_decision not in _ALLOWED_SEALED_DECISIONS
        or permission_decision not in _ALLOWED_SEALED_DECISIONS
    ):
        raise CardVocabularyRegistryError(
            "usage_decision/permission_decision must be the sealed bundle_allowed decision"
        )

    return CardVocabularyRegistryV1(
        schema_version=SCHEMA_VERSION,
        content_sha256=stored_content_sha256,
        card_database_sha256=card_database_sha256,
        card_vocabulary_sha256=card_vocabulary_sha256,
        card_id_count=card_id_count,
        environment_version=environment_version,
        usage_decision=usage_decision,
        permission_decision=permission_decision,
    )


def _bind_en_card_vocabulary_from_registry(
    registry: CardVocabularyRegistryV1,
    *,
    card_database_path: str | Path,
) -> EnCardVocabulary:
    """Read the pinned EN CSV and require it to actually match the registry's pins."""
    try:
        vocabulary = read_en_card_vocabulary(card_database_path)
    except SeedRegistryError as exc:
        raise CardVocabularyRegistryError(
            f"could not read the EN card database pinned by the registry: {exc}"
        ) from exc
    if vocabulary.source_sha256 != registry.card_database_sha256:
        raise CardVocabularyRegistryError(
            "EN card database bytes do not match the registry's pinned card_database_sha256"
        )
    if vocabulary.vocabulary_sha256 != registry.card_vocabulary_sha256:
        raise CardVocabularyRegistryError(
            "derived EN card vocabulary digest does not match the registry's "
            "pinned card_vocabulary_sha256"
        )
    if len(vocabulary.card_ids) != registry.card_id_count:
        raise CardVocabularyRegistryError(
            "EN card vocabulary ID count does not match the registry's pinned card_id_count"
        )
    return vocabulary


_ISSUANCE_ATTESTATION = object()
_ISSUED_PRODUCTION_VOCABULARIES: dict[
    int,
    tuple[ReferenceType[CardVocabularyV1], str, str],
] = {}
_ISSUED_PRODUCTION_VOCABULARIES_LOCK = Lock()


def _card_vocabulary_fingerprint(vocabulary: CardVocabularyV1) -> str:
    """Bind every public field of ``vocabulary`` to one factory-issued capability."""
    return content_id(
        _ISSUANCE_DOMAIN,
        {
            "recognized_card_ids": sorted(vocabulary.recognized_card_ids),
            "source_sha256": vocabulary.source_sha256,
            "environment_version": vocabulary.environment_version,
            "usage_decision": vocabulary.usage_decision,
            "test_only": vocabulary.test_only,
            "permission_decision": vocabulary.permission_decision,
        },
    )


def _issue_production_card_vocabulary(
    vocabulary: CardVocabularyV1,
    *,
    registry_content_sha256: str,
) -> CardVocabularyV1:
    fingerprint = _card_vocabulary_fingerprint(vocabulary)
    object.__setattr__(vocabulary, "_issuance_seal", _ISSUANCE_ATTESTATION)
    object_id = id(vocabulary)

    def discard(dead_ref: ReferenceType[CardVocabularyV1]) -> None:
        with _ISSUED_PRODUCTION_VOCABULARIES_LOCK:
            registered = _ISSUED_PRODUCTION_VOCABULARIES.get(object_id)
            # Do not let a delayed weakref callback remove a newer vocabulary
            # after CPython numeric object-ID reuse.
            if registered is not None and registered[0] is dead_ref:
                del _ISSUED_PRODUCTION_VOCABULARIES[object_id]

    vocabulary_ref = ref(vocabulary, discard)
    with _ISSUED_PRODUCTION_VOCABULARIES_LOCK:
        _ISSUED_PRODUCTION_VOCABULARIES[object_id] = (
            vocabulary_ref,
            fingerprint,
            registry_content_sha256,
        )
    return vocabulary


def load_production_card_vocabulary_v1(
    *,
    registry_path: str | Path = DEFAULT_CARD_VOCABULARY_REGISTRY_PATH,
    card_database_path: str | Path = DEFAULT_EN_CARD_DATABASE_PATH,
) -> CardVocabularyV1:
    """Build and issue the single production ``CardVocabularyV1`` this registry pins.

    Every call re-reads and re-verifies both the registry file and the EN
    card database bytes from disk; nothing here is cached across calls.  The
    returned object is sealed to a process-local issuance record -- see
    :func:`require_registry_issued_card_vocabulary_v1` -- so a caller cannot
    reproduce a passing vocabulary via ``dataclasses.replace``, ``copy``, or a
    fresh construction with identical field values.
    """
    registry = load_card_vocabulary_registry_v1(registry_path)
    en_vocabulary = _bind_en_card_vocabulary_from_registry(
        registry, card_database_path=card_database_path
    )
    vocabulary = CardVocabularyV1(
        recognized_card_ids=en_vocabulary.card_ids,
        source_sha256=en_vocabulary.source_sha256,
        environment_version=registry.environment_version,
        usage_decision=registry.usage_decision,
        test_only=False,
        permission_decision=registry.permission_decision,
    )
    return _issue_production_card_vocabulary(
        vocabulary, registry_content_sha256=registry.content_sha256
    )


def require_registry_issued_card_vocabulary_v1(vocabulary: CardVocabularyV1) -> CardVocabularyV1:
    """Verify ``vocabulary`` is the exact object :func:`load_production_card_vocabulary_v1` issued.

    This is the sole function
    ``actor_visible_features_v1.require_production_card_vocabulary_v1``
    delegates to once it has already rejected a wrong type or a
    ``test_only`` vocabulary.  It rejects:

    * any object that is not the literal instance a verified load issued --
      a ``dataclasses.replace``/``copy``/fresh construction, even with
      byte-identical fields, is never that instance and never carries a live
      entry in the process-local issuance registry keyed by its object
      identity;
    * an issued instance whose fields were mutated after issuance (e.g. via
      ``object.__setattr__`` on this frozen dataclass), caught by
      recomputing its fingerprint from its *current* fields and comparing to
      the fingerprint recorded at issuance;
    * any vocabulary at all once the registry file this process trusts is
      missing, unreadable, too large, not canonical JSON, has an invalid
      shape, or its ``content_sha256`` no longer verifies -- checked by
      re-loading the registry fresh on every call (never from a cache), so a
      registry that becomes unavailable or gets tampered with after
      issuance still fails every vocabulary it previously issued;
    * a vocabulary issued from a registry that has since been silently
      swapped for a different, still self-consistent, registry -- caught by
      comparing the freshly reloaded registry's own ``content_sha256`` to the
      one recorded at issuance time.
    """
    if type(vocabulary) is not CardVocabularyV1:
        raise SpecialistFeatureError("vocabulary must be an exact CardVocabularyV1")
    seal = getattr(vocabulary, "_issuance_seal", None)
    with _ISSUED_PRODUCTION_VOCABULARIES_LOCK:
        issuance = _ISSUED_PRODUCTION_VOCABULARIES.get(id(vocabulary))
    if seal is not _ISSUANCE_ATTESTATION or issuance is None or issuance[0]() is not vocabulary:
        raise SpecialistFeatureError(
            "production card vocabulary must be issued by the trusted sealed registry"
        )
    expected_fingerprint, expected_registry_content_sha256 = issuance[1], issuance[2]
    try:
        actual_fingerprint = _card_vocabulary_fingerprint(vocabulary)
    except (TypeError, ValueError) as exc:
        raise SpecialistFeatureError(
            "issued production card vocabulary fields are malformed"
        ) from exc
    if actual_fingerprint != expected_fingerprint:
        raise SpecialistFeatureError(
            "issued production card vocabulary no longer matches its own sealed fields"
        )

    try:
        current_registry = load_card_vocabulary_registry_v1(DEFAULT_CARD_VOCABULARY_REGISTRY_PATH)
    except CardVocabularyRegistryError as exc:
        raise SpecialistFeatureError(
            "production card vocabulary must be issued by the trusted sealed registry: "
            f"{exc}"
        ) from exc
    if current_registry.content_sha256 != expected_registry_content_sha256:
        raise SpecialistFeatureError(
            "production card vocabulary was issued from a trusted sealed registry that "
            "has since changed"
        )
    return vocabulary


__all__ = [
    "CardVocabularyRegistryError",
    "CardVocabularyRegistryV1",
    "DEFAULT_CARD_VOCABULARY_REGISTRY_PATH",
    "DEFAULT_EN_CARD_DATABASE_PATH",
    "SCHEMA_VERSION",
    "load_card_vocabulary_registry_v1",
    "load_production_card_vocabulary_v1",
    "require_registry_issued_card_vocabulary_v1",
]
