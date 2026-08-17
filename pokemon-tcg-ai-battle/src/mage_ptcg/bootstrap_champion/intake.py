"""Read-only conversion from existing catalog artifacts to bootstrap assets."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from mage_ptcg.continuous_league.catalog import CatalogSnapshot
from mage_ptcg.continuous_league.contracts import content_id, require_sha256
from mage_ptcg.observability.cabt_trace import canonical_deck_sha256
from mage_ptcg.policy_learning.r2d3.candidate import deck_hash as catalog_deck_hash

from .contracts import (
    BootstrapContractError,
    DeckAsset,
    DeckCompatibility,
    PolicyAsset,
)


@dataclass(frozen=True, slots=True)
class BootstrapAssetRegistry:
    decks: tuple[DeckAsset, ...]
    policies: tuple[PolicyAsset, ...]
    source_registry_id: str

    def __post_init__(self) -> None:
        try:
            require_sha256(self.source_registry_id, "source_registry_id")
        except ValueError as exc:
            raise BootstrapContractError(str(exc)) from exc
        if len({deck.deck_hash for deck in self.decks}) != len(self.decks):
            raise BootstrapContractError("Bootstrap deck registry contains duplicate deck hashes")
        if len({policy.policy_id for policy in self.policies}) != len(self.policies):
            raise BootstrapContractError("Bootstrap policy registry contains duplicate policy IDs")

    @property
    def asset_registry_id(self) -> str:
        return content_id(
            "bootstrap-asset-registry-v1",
            {
                "source_registry_id": self.source_registry_id,
                "decks": [deck.to_dict() for deck in self.decks],
                "policies": [policy.to_dict() for policy in self.policies],
            },
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "bootstrap-asset-registry-v1",
            "asset_registry_id": self.asset_registry_id,
            "source_registry_id": self.source_registry_id,
            "decks": [deck.to_dict() for deck in self.decks],
            "policies": [policy.to_dict() for policy in self.policies],
        }

    def with_public_decks(self, path: Path) -> "BootstrapAssetRegistry":
        public = tuple(_load_public_decks(path))
        existing = {deck.deck_hash for deck in self.decks}
        merged = tuple(sorted((*self.decks, *(deck for deck in public if deck.deck_hash not in existing)), key=lambda item: item.deck_hash))
        return BootstrapAssetRegistry(merged, self.policies, self.source_registry_id)


def _load_public_decks(path: Path) -> Iterable[DeckAsset]:
    path = Path(path)
    if not path.is_file():
        raise BootstrapContractError(f"deck asset registry is missing: {path}")
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise BootstrapContractError(f"invalid deck asset registry {path}:{line_number}") from exc
            if not isinstance(row, dict):
                raise BootstrapContractError("deck asset registry row must be an object")
            # Explicitly reject degraded reconstruction.  It may be useful as
            # an opponent, but must not become the claimed initial deck.
            # ``opponent_ingest`` uses EXACT_60_VALID/deck_digest/path while
            # older hand-authored registries use exact/deck_hash/deck_path.
            is_ingested_exact = row.get("eligibility") == "EXACT_60_VALID"
            if row.get("exact") is not True and not is_ingested_exact:
                continue
            try:
                snapshot_path = Path(str(row.get("deck_path", row.get("path", ""))))
                if not snapshot_path.is_absolute():
                    snapshot_path = (Path.cwd() / snapshot_path).resolve()
                deck_hash = str(row.get("deck_hash", row.get("deck_digest", "")))
                source_id = str(row["source_id"])
                # An ingest registry describes a Git/blob asset, not a copy of
                # its cards.  We only admit it if the exact referenced snapshot
                # is available now and independently matches the digest; this
                # prevents silently substituting the current branch's deck.
                if is_ingested_exact and not snapshot_path.is_file():
                    raise BootstrapContractError(
                        "ingested exact deck has no available immutable snapshot: "
                        f"{snapshot_path}"
                    )
                yield DeckAsset(
                    deck_id=str(row.get("deck_id", deck_hash)),
                    deck_hash=deck_hash,
                    snapshot_path=str(snapshot_path),
                    source_id=source_id,
                    source_hash=str(
                        row.get(
                            "source_hash",
                            content_id(
                                "bootstrap-ingested-deck-source-v1",
                                {"source_id": source_id, "deck_digest": deck_hash},
                            ),
                        )
                    ),
                )
            except KeyError as exc:
                raise BootstrapContractError(f"deck asset registry row misses {exc.args[0]}") from exc


def registry_from_catalog(
    catalog: CatalogSnapshot,
    *,
    deck_asset_registry: Path | None = None,
) -> BootstrapAssetRegistry:
    """Convert a pinned catalog snapshot without copying or modifying it."""

    def normalized_deck(entry: Any) -> DeckAsset:
        path = Path(entry.deck_path)
        if not path.is_file():
            raise BootstrapContractError(f"catalog deck snapshot is missing: {path}")
        try:
            cards = [int(line.strip()) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        except ValueError as exc:
            raise BootstrapContractError(f"catalog deck snapshot is not an integer CSV: {path}") from exc
        # Catalogs identify the ordered list consumed by a runtime policy;
        # R2D3 replay and Bootstrap step zero identify the same legal deck as
        # an unordered 60-card composition.  Verify the former, then store
        # the latter at the learner-facing boundary.
        composition_hash = canonical_deck_sha256(cards)
        # Older test/hand-authored catalog artifacts already used the
        # composition hash.  Accept that representation too, but reject any
        # hash that cannot be derived from the immutable snapshot.
        if (
            entry.deck_hash not in {catalog_deck_hash(cards), composition_hash}
            and entry.policy_kind not in {"rule_v0", "rule_v1"}
        ):
            raise BootstrapContractError("catalog deck hash differs from its snapshot")
        return DeckAsset(
            deck_id=entry.deck_id,
            deck_hash=composition_hash,
            snapshot_path=entry.deck_path,
            source_id=entry.source_id,
            source_hash=entry.source_hash,
        )

    deck_by_hash: dict[str, DeckAsset] = {}
    policies: list[PolicyAsset] = []
    for entry in catalog.entries:
        if not entry.enabled:
            continue
        deck = normalized_deck(entry)
        deck_by_hash.setdefault(deck.deck_hash, deck)
        compatibility = (
            DeckCompatibility.ARBITRARY_LEGAL_DECK
            if entry.policy_kind in {"rule_v0", "rule_v1"}
            else DeckCompatibility.EXACT_DECK
        )
        policies.append(
            PolicyAsset(
                policy_id=entry.policy_id,
                policy_hash=entry.policy_hash,
                policy_kind=entry.policy_kind,
                runtime_path=entry.runtime_path,
                adapter_hash=content_id(
                    "bootstrap-catalog-adapter-v1",
                    {"policy_kind": entry.policy_kind, "runtime_path": entry.runtime_path},
                ),
                runtime_config_hash=entry.runtime_config_hash,
                compatibility=compatibility,
                exact_deck_hash=(deck.deck_hash if compatibility is DeckCompatibility.EXACT_DECK else None),
                source_id=entry.source_id,
                source_hash=entry.source_hash,
            )
        )
    unique_policies = {policy.policy_id: policy for policy in policies}
    registry = BootstrapAssetRegistry(
        tuple(sorted(deck_by_hash.values(), key=lambda item: item.deck_hash)),
        tuple(sorted(unique_policies.values(), key=lambda item: item.policy_id)),
        catalog.catalog_snapshot_id,
    )
    return registry.with_public_decks(deck_asset_registry) if deck_asset_registry else registry
