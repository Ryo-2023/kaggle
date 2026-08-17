"""Knowledge Claim registry: persistence + lifecycle transitions (O1-3).

Claims are immutable; a lifecycle transition
(``KnowledgeClaim.with_transition``) produces a new object which is
*appended* to the registry log rather than overwriting the previous version
-- this preserves full transition history and never deletes a
``REJECTED``/``DEPRECATED`` claim. ``latest_claims()`` reduces the log to
"current" state (last version per ``claim_id`` wins) for callers that only
need the present, while the full log remains available for lineage/audit.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Mapping

from .atomic_io import append_jsonl_line, atomic_write_bytes
from .canonical import canonical_json_bytes
from .contracts import AllowedUse, ClaimStatus, ContractError, EvidenceBasis, EvidenceGrade, KnowledgeClaim
from .permissions import has_permission
from .provenance import read_source_manifest
from .runstate import RunPaths

KNOWLEDGE_CLAIMS_FILENAME = "knowledge_claims.jsonl"


class KnowledgeRegistryError(ValueError):
    """Raised for an invalid registry operation (unknown claim, illegal transition, corrupt log entry)."""


def _claim_to_payload(claim: KnowledgeClaim) -> dict[str, object]:
    payload = claim.content_payload()
    payload["content_hash"] = claim.content_hash()
    return payload


def _claim_from_payload(payload: Mapping[str, object]) -> KnowledgeClaim:
    fields = {key: value for key, value in payload.items() if key != "content_hash"}
    try:
        claim = KnowledgeClaim(
            schema_version=fields["schema_version"],
            claim_id=fields["claim_id"],
            raw_source_id=fields["raw_source_id"],
            claim_type=fields["claim_type"],
            scope=fields["scope"],
            preconditions=tuple(fields["preconditions"]),
            recommendation=fields["recommendation"],
            expected_effect=fields.get("expected_effect"),
            evidence_grade=EvidenceGrade(fields["evidence_grade"]),
            status=ClaimStatus(fields["status"]),
            validity=fields["validity"],
            support=fields["support"],
            freshness=fields["freshness"],
            supporting_artifacts=tuple(fields["supporting_artifacts"]),
            contradicting_claims=tuple(fields["contradicting_claims"]),
            created_at=fields["created_at"],
            updated_at=fields["updated_at"],
            evidence_basis=EvidenceBasis(fields["evidence_basis"]),
            training_eligible=fields["training_eligible"],
            runtime_eligible=fields["runtime_eligible"],
            supersedes=tuple(fields["supersedes"]),
        )
    except (KeyError, ContractError, ValueError) as exc:
        raise KnowledgeRegistryError(f"corrupt knowledge claim log entry: {exc}") from exc
    expected_hash = payload.get("content_hash")
    if expected_hash is not None and expected_hash != claim.content_hash():
        raise KnowledgeRegistryError(f"claim {claim.claim_id!r} content_hash mismatch on load from registry log")
    return claim


def claims_log_path(run_root: str | Path) -> Path:
    return RunPaths(Path(run_root)).derived / KNOWLEDGE_CLAIMS_FILENAME


def append_claim_version(run_root: str | Path, claim: KnowledgeClaim) -> None:
    """Append one immutable version of a claim to the registry log."""
    append_jsonl_line(claims_log_path(run_root), _claim_to_payload(claim))


def iter_claim_versions(run_root: str | Path) -> Iterator[KnowledgeClaim]:
    """Yield every claim version ever appended, in append order (oldest first)."""
    path = claims_log_path(run_root)
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped:
                continue
            yield _claim_from_payload(json.loads(stripped))


def latest_claims(run_root: str | Path) -> dict[str, KnowledgeClaim]:
    """The most recent version of every ``claim_id`` (last append wins)."""
    latest: dict[str, KnowledgeClaim] = {}
    for claim in iter_claim_versions(run_root):
        latest[claim.claim_id] = claim
    return latest


def _append_claims_atomically(run_root: str | Path, claims_to_append: list[KnowledgeClaim]) -> None:
    """Append every claim in ``claims_to_append`` as a single atomic file replace.

    Reads the current log bytes, appends the new lines in memory, and writes
    the whole result back via ``atomic_write_bytes`` (temp sibling -> fsync ->
    ``os.replace``) in one call -- there is no window in which some but not
    all of a batch is visible on disk, unlike appending each claim with a
    separate ``os.write`` call.
    """
    path = claims_log_path(run_root)
    existing_bytes = path.read_bytes() if path.exists() else b""
    new_lines = b"".join(canonical_json_bytes(_claim_to_payload(claim)) + b"\n" for claim in claims_to_append)
    atomic_write_bytes(path, existing_bytes + new_lines)


@dataclass(frozen=True, slots=True)
class ImportClaimsResult:
    """Distinguishes what a batch *asked* to import from what was actually newly appended.

    ``duplicate_skipped_claim_ids`` (batch-internal and/or already-registered
    idempotent no-ops, see ``import_claims``) is the independent-audit
    finding's duplicate-count visibility: a caller summarizing "N claims
    imported" must not silently count a no-op skip as a fresh import.
    """

    claim_ids: tuple[str, ...]
    appended_claim_ids: tuple[str, ...]
    duplicate_skipped_claim_ids: tuple[str, ...]


def import_claims(run_root: str | Path, claims: list[KnowledgeClaim]) -> ImportClaimsResult:
    """Idempotently, atomically import a batch of newly-raw claims.

    The full batch is validated (preflight) against itself and against the
    existing registry *before* anything is written -- independent-audit
    finding: the previous implementation only checked incoming claims against
    the existing registry, so two claims sharing a ``claim_id`` within the
    *same* batch were never compared against each other, silently keeping
    whichever happened to be appended last.

    - A ``claim_id`` repeated within the batch, or colliding with an existing
      ``claim_id``, with **identical** content (``content_hash`` match) is an
      idempotent no-op: it is not re-appended, and the call succeeds (a
      re-import of exactly what is already present is unambiguous, unlike a
      re-import with different content).
    - A ``claim_id`` repeated within the batch, or colliding with an existing
      ``claim_id``, with **conflicting** content raises
      ``KnowledgeRegistryError`` and appends nothing at all -- not even the
      non-conflicting claims in the same batch -- so the registry's bytes
      and content hash are provably unchanged on any failure (see
      ``_append_claims_atomically``).
    - Appended claims are written in ``claim_id`` order, independent of the
      order they appeared in the input batch.
    """
    existing = latest_claims(run_root)

    by_id: dict[str, list[KnowledgeClaim]] = {}
    for claim in claims:
        by_id.setdefault(claim.claim_id, []).append(claim)

    batch_conflicts = sorted(
        claim_id for claim_id, group in by_id.items() if len({c.content_hash() for c in group}) > 1
    )
    if batch_conflicts:
        raise KnowledgeRegistryError(
            f"claim_id(s) appear multiple times in the same import batch with conflicting content: {batch_conflicts}"
        )

    registry_conflicts = sorted(
        claim_id for claim_id, group in by_id.items()
        if claim_id in existing and existing[claim_id].content_hash() != group[0].content_hash()
    )
    if registry_conflicts:
        raise KnowledgeRegistryError(
            f"claim_id(s) already exist in registry with different content: {registry_conflicts}"
        )

    to_append = sorted(
        (group[0] for claim_id, group in by_id.items() if claim_id not in existing),
        key=lambda claim: claim.claim_id,
    )
    if to_append:
        _append_claims_atomically(run_root, to_append)

    appended_ids = frozenset(claim.claim_id for claim in to_append)
    return ImportClaimsResult(
        claim_ids=tuple(sorted(by_id)),
        appended_claim_ids=tuple(claim.claim_id for claim in to_append),
        duplicate_skipped_claim_ids=tuple(sorted(claim_id for claim_id in by_id if claim_id not in appended_ids)),
    )


def transition_claim(
    run_root: str | Path,
    claim_id: str,
    new_status: ClaimStatus,
    *,
    updated_at: str,
    training_eligible: bool | None = None,
    runtime_eligible: bool | None = None,
) -> KnowledgeClaim:
    """Apply a validated lifecycle transition and append the resulting new version.

    Granting ``training_eligible``/``runtime_eligible`` (only possible when
    ``new_status`` is ``SUPPORTED``, see ``KnowledgeClaim.with_transition``)
    re-checks the claim's ``raw_source_id`` against its archived
    ``SourceEnvelope`` permission at the moment eligibility is granted --
    defense in depth alongside the ``ANALYSIS`` check already done at import
    time in ``pipeline.run_import_knowledge``.
    """
    current = latest_claims(run_root).get(claim_id)
    if current is None:
        raise KnowledgeRegistryError(f"unknown claim_id {claim_id!r}")
    if training_eligible or runtime_eligible:
        try:
            envelope = read_source_manifest(Path(run_root), current.raw_source_id)
        except OSError as exc:
            raise KnowledgeRegistryError(
                f"cannot grant eligibility for claim {claim_id!r}: no archived source manifest for "
                f"raw_source_id {current.raw_source_id!r}: {exc}"
            ) from exc
        if training_eligible and not has_permission(envelope, AllowedUse.TRAINING):
            raise KnowledgeRegistryError(
                f"cannot set training_eligible=True for claim {claim_id!r}: source {current.raw_source_id!r} "
                "does not grant TRAINING"
            )
        if runtime_eligible and not has_permission(envelope, AllowedUse.REPORTING):
            raise KnowledgeRegistryError(
                f"cannot set runtime_eligible=True for claim {claim_id!r}: source {current.raw_source_id!r} "
                "does not grant REPORTING"
            )
    try:
        moved = current.with_transition(
            new_status, updated_at=updated_at, training_eligible=training_eligible, runtime_eligible=runtime_eligible
        )
    except ContractError as exc:
        raise KnowledgeRegistryError(str(exc)) from exc
    append_claim_version(run_root, moved)
    return moved


__all__ = [
    "KNOWLEDGE_CLAIMS_FILENAME",
    "ImportClaimsResult",
    "KnowledgeRegistryError",
    "append_claim_version",
    "claims_log_path",
    "import_claims",
    "iter_claim_versions",
    "latest_claims",
    "transition_claim",
]
