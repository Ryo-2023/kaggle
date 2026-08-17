"""Allowed-use permission checks: default deny for anything not explicitly granted.

A missing or absent permission is always treated as "not granted" (empty
``frozenset``), never inferred from source visibility or convenience. In
particular, ``PUBLIC_OTHER`` sources default to ``{ARCHIVE}`` only —
"the data is public" is never on its own read as "training is allowed".
"""

from __future__ import annotations

from typing import Iterable

from .contracts import AllowedUse, ContractError, SourceEnvelope, SourceKind

# Defaults applied only when a caller explicitly asks for "what would this
# source kind get if nothing else is specified" (e.g. building a fixture or a
# capability report). Ingestion itself must always take allowed_uses from the
# actual permission statement/manifest, never fall back to this table.
DEFAULT_ALLOWED_USES: dict[SourceKind, frozenset[AllowedUse]] = {
    SourceKind.LOCAL_SELFPLAY: frozenset({AllowedUse.ARCHIVE, AllowedUse.ANALYSIS, AllowedUse.TRAINING, AllowedUse.REPORTING}),
    SourceKind.OWN_KAGGLE: frozenset({AllowedUse.ARCHIVE, AllowedUse.ANALYSIS, AllowedUse.TRAINING, AllowedUse.REPORTING}),
    SourceKind.TEAM_SHARED: frozenset(),  # requires an explicit permission_statement; see provenance.py
    SourceKind.PUBLIC_OTHER: frozenset({AllowedUse.ARCHIVE}),
    SourceKind.HUMAN_TEXT: frozenset({AllowedUse.ARCHIVE, AllowedUse.ANALYSIS, AllowedUse.REPORTING}),
}


def has_permission(envelope: SourceEnvelope, required: AllowedUse) -> bool:
    return required in envelope.allowed_uses


def has_all_permissions(envelope: SourceEnvelope, required: Iterable[AllowedUse]) -> bool:
    required_set = frozenset(required)
    return required_set.issubset(envelope.allowed_uses)


def require_permission(envelope: SourceEnvelope, required: AllowedUse, *, purpose: str) -> None:
    """Raise ``PermissionError`` unless ``envelope`` grants ``required``.

    Call this at every point data crosses from "archived" into "used for X"
    (an analysis pass, a snapshot's training split, a report) rather than
    trusting a caller to have checked upstream.
    """
    if not has_permission(envelope, required):
        raise PermissionError(
            f"source {envelope.source_id!r} ({envelope.source_kind.value}) does not grant "
            f"{required.value}, required for {purpose}"
        )


def intersect_allowed_uses(envelopes: Iterable[SourceEnvelope]) -> frozenset[AllowedUse]:
    """The permissions a *set* of sources jointly grant: the intersection.

    Used when a downstream artifact (e.g. a snapshot) mixes multiple sources
    and must only be used for what *every* contributing source allows.
    """
    envelopes = list(envelopes)
    if not envelopes:
        return frozenset()
    result = envelopes[0].allowed_uses
    for envelope in envelopes[1:]:
        result = result & envelope.allowed_uses
    return result


def filter_for_use(envelopes: Iterable[SourceEnvelope], required: AllowedUse) -> list[SourceEnvelope]:
    """Keep only the envelopes that individually grant ``required``."""
    return [envelope for envelope in envelopes if has_permission(envelope, required)]


def validate_allowed_uses_subset(requested: Iterable[str], *, field_name: str = "allowed_uses") -> frozenset[AllowedUse]:
    """Parse and validate a raw iterable of use-name strings into ``AllowedUse``.

    Raises ``ContractError`` (not a bare ``KeyError``/``ValueError``) on any
    unknown use name, so callers get a consistent, informative failure when
    ingesting an untrusted permission manifest (e.g. a team bundle's YAML).
    """
    result: set[AllowedUse] = set()
    for name in requested:
        try:
            result.add(AllowedUse(name))
        except ValueError as exc:
            raise ContractError(f"{field_name} contains an unknown allowed use: {name!r}") from exc
    return frozenset(result)


__all__ = [
    "DEFAULT_ALLOWED_USES",
    "filter_for_use",
    "has_all_permissions",
    "has_permission",
    "intersect_allowed_uses",
    "require_permission",
    "validate_allowed_uses_subset",
]
