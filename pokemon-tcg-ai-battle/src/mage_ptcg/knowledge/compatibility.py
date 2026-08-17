"""Explicit, inspectable Knowledge Pack runtime compatibility checks."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Sequence
from importlib.metadata import version

from .model import ACTION_KEY_SCHEMA_VERSION, KnowledgePack, SCHEMA_VERSION, deck_identity_from_card_ids


DEFAULT_CARD_POOL_ID = "competition-card-pool-unverified"
DEFAULT_CARD_POOL_VERSION = "unverified"
DEFAULT_CABT_VERSION = "kaggle-environments-1.32.0"


@dataclass(frozen=True, slots=True)
class RuntimeCompatibility:
    """The exact runtime contracts that a pack must match before use."""

    schema_version: str
    action_key_schema_version: str
    cabt_version: str
    card_pool_id: str
    card_pool_version: str
    deck_id: str


@dataclass(frozen=True, slots=True)
class CompatibilityReport:
    """Compatibility outcome retaining machine-readable mismatch reasons."""

    compatible: bool
    reasons: tuple[str, ...]


def check_compatibility(pack: KnowledgePack, target: RuntimeCompatibility) -> CompatibilityReport:
    """Compare all v0 hard compatibility fields without mutating a snapshot."""
    comparisons = (
        ("schema_version", pack.manifest.schema_version, target.schema_version),
        ("action_key_schema_version", pack.manifest.action_key_schema_version, target.action_key_schema_version),
        ("cabt_version", pack.manifest.cabt_version, target.cabt_version),
        ("card_pool_id", pack.manifest.card_pool_id, target.card_pool_id),
        ("card_pool_version", pack.manifest.card_pool_version, target.card_pool_version),
        ("deck_id", pack.manifest.deck_id, target.deck_id),
    )
    reasons = tuple(f"{name}: expected {expected!r}, got {actual!r}" for name, actual, expected in comparisons if actual != expected)
    return CompatibilityReport(compatible=not reasons, reasons=reasons)


def runtime_cabt_version() -> str:
    """Return the installed cabt wrapper version, or the pinned runtime baseline.

    Some unit-test and submission-bundle environments intentionally omit the
    Python ``kaggle-environments`` distribution.  In that case the repository's
    pinned cabt baseline is used explicitly; a pack cannot provide this value.
    """
    try:
        return f"kaggle-environments-{version('kaggle-environments')}"
    except Exception:
        # Distribution metadata is external environment state.  A broken
        # backend must fail closed to the pinned baseline so the optional
        # Knowledge layer cannot prevent the baseline agent from starting.
        return DEFAULT_CABT_VERSION


def runtime_compatibility_for_deck(card_ids: Sequence[int]) -> RuntimeCompatibility:
    """Build the non-self-referential Knowledge runtime target for one deck."""
    return RuntimeCompatibility(
        schema_version=SCHEMA_VERSION,
        action_key_schema_version=ACTION_KEY_SCHEMA_VERSION,
        cabt_version=runtime_cabt_version(),
        card_pool_id=DEFAULT_CARD_POOL_ID,
        card_pool_version=DEFAULT_CARD_POOL_VERSION,
        deck_id=deck_identity_from_card_ids(card_ids),
    )
