from __future__ import annotations

import json
from pathlib import Path

from mage_ptcg.bootstrap_champion.candidates import build_joint_candidates
from mage_ptcg.bootstrap_champion.contracts import DeckCompatibility
from mage_ptcg.bootstrap_champion.intake import BootstrapAssetRegistry, registry_from_catalog
from mage_ptcg.continuous_league.catalog import CatalogEntry, CatalogSnapshot
from mage_ptcg.observability.cabt_trace import canonical_deck_sha256
from mage_ptcg.policy_learning.r2d3.candidate import deck_hash as catalog_deck_hash


def _sha(character: str) -> str:
    return character * 64


def _deck(path: Path, card: int) -> str:
    cards = [card] * 60
    path.write_text("\n".join(map(str, cards)) + "\n", encoding="utf-8")
    return canonical_deck_sha256(cards)


def test_catalog_assets_and_public_decks_make_only_compatible_pairs(tmp_path: Path) -> None:
    exact_path = tmp_path / "exact.csv"
    public_path = tmp_path / "public.csv"
    exact_hash = _deck(exact_path, 1)
    public_hash = _deck(public_path, 2)
    catalog = CatalogSnapshot.build(
        [
            CatalogEntry(
                asset_id="rule",
                policy_id="rule-policy",
                deck_id="rule-deck",
                source_id="local",
                policy_kind="rule_v0",
                runtime_path="builtin:rule_v0",
                deck_path=str(exact_path),
                policy_hash=_sha("a"),
                deck_hash=exact_hash,
                source_hash=_sha("b"),
                runtime_config_hash=_sha("c"),
                role="TRAINING_ACTIVE",
            ),
            CatalogEntry(
                asset_id="submitted",
                policy_id="native-policy",
                deck_id="native-deck",
                source_id="team",
                policy_kind="submitted_snapshot",
                runtime_path=str(tmp_path / "native-manifest.json"),
                deck_path=str(exact_path),
                policy_hash=_sha("d"),
                deck_hash=exact_hash,
                source_hash=_sha("e"),
                runtime_config_hash=_sha("f"),
                role="TRAINING_ACTIVE",
            ),
        ]
    )
    deck_registry = tmp_path / "deck_asset_registry.jsonl"
    deck_registry.write_text(
        json.dumps(
            {
                "deck_id": "public",
                "deck_hash": public_hash,
                "deck_path": str(public_path),
                "source_id": "kaggle-public",
                "source_hash": _sha("9"),
                "exact": True,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    registry = registry_from_catalog(catalog, deck_asset_registry=deck_registry)
    candidates = build_joint_candidates(registry, simulator_contract_hash=_sha("8"))

    assert len(candidates) == 3
    assert {candidate.policy.compatibility for candidate in candidates} == {
        DeckCompatibility.ARBITRARY_LEGAL_DECK,
        DeckCompatibility.EXACT_DECK,
    }
    assert {(item.deck.deck_hash, item.policy.policy_id) for item in candidates} == {
        (exact_hash, "rule-policy"),
        (public_hash, "rule-policy"),
        (exact_hash, "native-policy"),
    }


def test_degraded_public_deck_is_not_turned_into_a_candidate(tmp_path: Path) -> None:
    path = tmp_path / "deck.csv"
    deck_hash = _deck(path, 3)
    registry = BootstrapAssetRegistry(
        decks=(),
        policies=(),
        source_registry_id=_sha("1"),
    )
    deck_registry = tmp_path / "deck_asset_registry.jsonl"
    deck_registry.write_text(
        json.dumps(
            {
                "deck_id": "degraded",
                "deck_hash": deck_hash,
                "deck_path": str(path),
                "source_id": "kaggle-public",
                "source_hash": _sha("2"),
                "exact": False,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    assert registry.with_public_decks(deck_registry).decks == ()


def test_opponent_ingest_exact_deck_registry_row_is_accepted_without_reconstruction(
    tmp_path: Path,
) -> None:
    """Consume the immutable schema emitted by opponent_ingest directly."""

    deck_path = tmp_path / "ingested.csv"
    deck_hash = _deck(deck_path, 4)
    registry = BootstrapAssetRegistry(
        decks=(),
        policies=(),
        source_registry_id=_sha("1"),
    )
    deck_registry = tmp_path / "deck_asset_registry.jsonl"
    deck_registry.write_text(
        json.dumps(
            {
                "source_id": "git-public-deck",
                "path": str(deck_path),
                "eligibility": "EXACT_60_VALID",
                "deck_digest": deck_hash,
                "cards": [4] * 60,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    assets = registry.with_public_decks(deck_registry)

    assert len(assets.decks) == 1
    assert assets.decks[0].deck_hash == deck_hash
    assert assets.decks[0].snapshot_path == str(deck_path)


def test_catalog_ordered_deck_hash_is_normalized_for_bootstrap_and_learner(
    tmp_path: Path,
) -> None:
    deck_path = tmp_path / "catalog.csv"
    cards = [5] * 60
    deck_path.write_text("\n".join(map(str, cards)) + "\n", encoding="utf-8")
    catalog = CatalogSnapshot.build(
        [
            CatalogEntry(
                asset_id="rule",
                policy_id="rule-policy",
                deck_id="rule-deck",
                source_id="local",
                policy_kind="rule_v0",
                runtime_path="builtin:rule_v0",
                deck_path=str(deck_path),
                policy_hash=_sha("a"),
                # The established continuous-league catalog uses the ordered
                # runtime-deck hash, while the learner uses composition hash.
                deck_hash=catalog_deck_hash(cards),
                source_hash=_sha("b"),
                runtime_config_hash=_sha("c"),
                role="TRAINING_ACTIVE",
            )
        ]
    )

    registry = registry_from_catalog(catalog)

    assert registry.decks[0].deck_hash == canonical_deck_sha256(cards)


def test_rule_deck_pool_uses_the_verified_snapshot_when_catalog_deck_hash_is_stale(
    tmp_path: Path,
) -> None:
    deck_path = tmp_path / "rule-deck.csv"
    cards = [6] * 60
    deck_path.write_text("\n".join(map(str, cards)) + "\n", encoding="utf-8")
    catalog = CatalogSnapshot.build(
        [
            CatalogEntry(
                asset_id="rule", policy_id="rule-policy", deck_id="rule-deck",
                source_id="deck-pool", policy_kind="rule_v0",
                runtime_path="builtin:rule_v0", deck_path=str(deck_path),
                policy_hash=_sha("a"), deck_hash=_sha("b"), source_hash=_sha("c"),
                runtime_config_hash=_sha("d"), role="TRAINING_ACTIVE",
            )
        ]
    )

    registry = registry_from_catalog(catalog)

    assert registry.decks[0].deck_hash == canonical_deck_sha256(cards)
