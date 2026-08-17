from __future__ import annotations

import ast
from dataclasses import replace
import gc
import json
from pathlib import Path
import subprocess
import sys
import weakref

import pytest

from mage_ptcg.meta_specialist.decks import load_archetype_registry


ROOT = Path(__file__).resolve().parents[2]


def _imports_top_level_main(node: ast.AST) -> bool:
    """Identify static or dynamic imports of the archive-shadowed root module."""
    if isinstance(node, ast.Import):
        return any(alias.name == "main" for alias in node.names)
    if isinstance(node, ast.ImportFrom):
        return node.level == 0 and node.module == "main"
    if not isinstance(node, ast.Call) or not node.args:
        return False
    target = node.func
    is_dynamic_import = (
        isinstance(target, ast.Name)
        and target.id in {"__import__", "import_module"}
    ) or (
        isinstance(target, ast.Attribute)
        and target.attr == "import_module"
    )
    return (
        is_dynamic_import
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value == "main"
    )


def test_meta_specialist_production_modules_do_not_import_top_level_main() -> None:
    """Catches a direct or dynamic dependency on archive-shadowed ``main``."""
    offenders = []
    for source_path in sorted((ROOT / "src/mage_ptcg/meta_specialist").glob("*.py")):
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
        if any(_imports_top_level_main(node) for node in ast.walk(tree)):
            offenders.append(source_path.name)

    assert offenders == []


def test_meta_specialist_parses_an_explicit_deck_with_poisoned_root_main(
    tmp_path: Path,
) -> None:
    """Catches import-closure regressions when packaged ``main.py`` shadows the legacy one."""
    deck_path = tmp_path / "explicit-deck.csv"
    deck_path.write_text("\n".join(map(str, range(1, 61))) + "\n", encoding="utf-8")
    poison_root = tmp_path / "poison"
    poison_root.mkdir()
    (poison_root / "main.py").write_text(
        'raise AssertionError("poisoned top-level main was imported")\n', encoding="utf-8"
    )
    script = "\n".join(
        [
            "import sys",
            f"sys.path[:0] = {[str(poison_root), str(ROOT / 'src')]!r}",
            "from mage_ptcg.meta_specialist.decks import DeckAssetInput",
            "asset = DeckAssetInput.from_path(",
            "    asset_id='fixture', archetype_id='alakazam',",
            f"    path={str(deck_path)!r}, source_ref='origin/fixture:deck.csv',",
            "    source_commit='a' * 40, asset_class='deck_only',",
            "    usage_boundary='local_eval_only', policy_compatibility='deck_only',",
            "    card_database_version='fixture-v1',",
            ")",
            "assert asset.card_ids == tuple(range(1, 61))",
            "assert 'main' not in sys.modules",
        ]
    )

    result = subprocess.run(
        [sys.executable, "-I", "-c", script],
        capture_output=True,
        check=False,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr


def test_registry_has_exact_five_lanes_and_priority_order() -> None:
    """Catches a registry that changes the fixed initial lane plan."""
    registry = load_archetype_registry(
        ROOT / "configs/meta_specialist/archetypes_v1.json"
    )

    assert tuple(registry.archetypes) == (
        "alakazam",
        "grimmsnarl_froslass_munkidori",
        "crustle_mega_kangaskhan",
        "rocket_mewtwo_spidops",
        "archaludon",
    )
    assert registry.primary_order == (
        "grimmsnarl_froslass_munkidori",
        "alakazam",
        "crustle_mega_kangaskhan",
    )
    assert registry.replacement_order == (
        "rocket_mewtwo_spidops",
        "archaludon",
    )
    assert {
        runtime_id: (
            spec.aliases,
            spec.core_card_ids,
            spec.candidate_status,
        )
        for runtime_id, spec in registry.archetypes.items()
    } == {
        "alakazam": ((), (741, 742, 743), "registered_unqualified"),
        "grimmsnarl_froslass_munkidori": (
            ("grimmsnarl_froslass",),
            (104, 112, 646, 647, 648, 860),
            "registered_unqualified",
        ),
        "crustle_mega_kangaskhan": (
            (),
            (344, 345, 756),
            "registered_unqualified",
        ),
        "rocket_mewtwo_spidops": (
            (),
            (400, 401, 431),
            "registered_unqualified",
        ),
        "archaludon": ((), (169, 190), "registered_unqualified"),
    }


@pytest.mark.parametrize(
    "document",
    [
        '''{"schema_version":"meta-specialist-archetypes-v1","schema_version":"meta-specialist-archetypes-v1","primary_order":[],"replacement_order":[],"archetypes":[]}''',
        '''{"schema_version":"meta-specialist-archetypes-v1","primary_order":[],"replacement_order":[],"archetypes":[{"runtime_id":"alakazam","runtime_id":"other","aliases":[],"core_card_ids":[741],"candidate_status":"registered_unqualified"}]}''',
    ],
)
def test_registry_rejects_duplicate_json_keys(tmp_path: Path, document: str) -> None:
    """Catches decoder overwrite semantics for duplicate registry JSON keys."""
    from mage_ptcg.meta_specialist.decks import ArchetypeRegistryError

    path = tmp_path / "duplicate-key-registry.json"
    path.write_text(document, encoding="utf-8")

    with pytest.raises(ArchetypeRegistryError, match="duplicate JSON key"):
        load_archetype_registry(path)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda document: document.update({"unexpected": True}),
        lambda document: document["archetypes"][0].update({"unexpected": True}),
        lambda document: document["archetypes"][0].update({"core_card_ids": [741, True, 743]}),
        lambda document: document["archetypes"][0].update({"core_card_ids": [742, 741, 743]}),
        lambda document: document["archetypes"][0].update({"core_card_ids": [741, 741, 743]}),
        lambda document: document.update({"replacement_order": ["alakazam"]}),
        lambda document: document.update({"replacement_order": ["unregistered"]}),
    ],
)
def test_registry_rejects_unknown_or_ambiguous_strict_values(
    tmp_path: Path, mutate
) -> None:
    """Catches permissive parsing of keys, card IDs, or priority ownership."""
    from mage_ptcg.meta_specialist.decks import ArchetypeRegistryError

    source = ROOT / "configs/meta_specialist/archetypes_v1.json"
    document = json.loads(source.read_text(encoding="utf-8"))
    mutate(document)
    path = tmp_path / "invalid-registry.json"
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ArchetypeRegistryError):
        load_archetype_registry(path)


@pytest.fixture
def alakazam_spec():
    return load_archetype_registry(
        ROOT / "configs/meta_specialist/archetypes_v1.json"
    ).archetypes["alakazam"]


@pytest.fixture
def valid_cards() -> list[int]:
    return [741, 742, 743] + list(range(1000, 1057))


@pytest.fixture
def alakazam_asset(tmp_path: Path, valid_cards: list[int]):
    from mage_ptcg.meta_specialist.decks import DeckAssetInput

    deck = tmp_path / "deck.csv"
    deck.write_text("\n".join(map(str, valid_cards)) + "\n", encoding="utf-8")
    return DeckAssetInput.from_path(
        asset_id="alakazam-seed-a",
        archetype_id="alakazam",
        path=deck,
        source_ref="origin/agents/nihei-alakazam:deck.csv",
        source_commit="a" * 40,
        asset_class="deck_only",
        usage_boundary="local_eval_only",
        policy_compatibility="deck_only",
        card_database_version="fixture-v1",
    )


@pytest.fixture
def qualified_asset(alakazam_asset, alakazam_spec):
    from mage_ptcg.meta_specialist.decks import qualify_deck_asset

    return qualify_deck_asset(
        alakazam_asset,
        alakazam_spec,
        known_card_ids=set(alakazam_asset.card_ids),
        cabt_legality=lambda values: (True, "fixture-cabt-pass"),
    )


def test_qualification_records_multiset_and_exact_file_identity(
    alakazam_asset, alakazam_spec
) -> None:
    """Catches qualification that loses either byte or multiset deck identity."""
    from mage_ptcg.meta_specialist.decks import qualify_deck_asset

    qualified = qualify_deck_asset(
        alakazam_asset,
        alakazam_spec,
        known_card_ids=set(alakazam_asset.card_ids),
        cabt_legality=lambda values: (True, "fixture-cabt-pass"),
    )

    assert qualified.card_count == 60
    assert qualified.deck_identity.startswith("deck-")
    assert len(qualified.deck_file_sha256) == 64
    assert qualified.cabt_legality_status == "passed"
    assert qualified.cabt_legality_evidence == "fixture-cabt-pass"


@pytest.mark.parametrize(
    ("field_name", "replacement"),
    (
        ("asset_id", "forged-asset"),
        ("archetype_id", "forged-archetype"),
        ("deck_identity", "deck-" + "0" * 20),
        ("deck_file_sha256", "0" * 64),
        ("source_ref", "forged/source"),
        ("source_commit", "b" * 40),
        ("asset_class", "runnable_rule"),
        ("usage_boundary", "teacher_only"),
        ("policy_compatibility", "forged-policy"),
        ("card_database_version", "forged-card-db"),
        ("path", "replacement-path"),
        ("card_ids", (999,) * 60),
        ("deck_file_bytes", b"forged deck bytes"),
    ),
)
def test_qualification_rejects_every_callback_mutation_of_the_validated_asset(
    alakazam_asset,
    alakazam_spec,
    field_name: str,
    replacement: object,
) -> None:
    """CABT cannot alter a validated input before the factory issues a capability."""
    from mage_ptcg.meta_specialist.decks import DeckQualificationError, qualify_deck_asset

    def mutating_cabt(_cards: tuple[int, ...]) -> tuple[bool, str]:
        object.__setattr__(alakazam_asset, field_name, replacement)
        return True, "callback-mutated-asset"

    with pytest.raises(DeckQualificationError, match="changed during CABT legality"):
        qualify_deck_asset(
            alakazam_asset,
            alakazam_spec,
            known_card_ids=set(alakazam_asset.card_ids),
            cabt_legality=mutating_cabt,
        )


def test_qualification_rejects_callback_mutate_restore_when_the_path_binding_changes(
    alakazam_asset,
    alakazam_spec,
    tmp_path: Path,
) -> None:
    """Restoring fields cannot hide a callback-time replacement of exact deck bytes."""
    from mage_ptcg.meta_specialist.decks import DeckQualificationError, qualify_deck_asset

    original_identity = alakazam_asset.deck_identity
    replacement = tmp_path / "replacement.csv"
    replacement.write_bytes(alakazam_asset.deck_file_bytes)

    def mutate_restore_cabt(_cards: tuple[int, ...]) -> tuple[bool, str]:
        object.__setattr__(alakazam_asset, "deck_identity", "deck-" + "0" * 20)
        object.__setattr__(alakazam_asset, "deck_identity", original_identity)
        replacement.replace(alakazam_asset.path)
        return True, "callback-replaced-path"

    with pytest.raises(DeckQualificationError, match="changed during CABT legality"):
        qualify_deck_asset(
            alakazam_asset,
            alakazam_spec,
            known_card_ids=set(alakazam_asset.card_ids),
            cabt_legality=mutate_restore_cabt,
        )


def test_qualified_asset_attestation_replays_structural_deck_invariants(
    qualified_asset,
) -> None:
    """A factory seal cannot turn a 59-card or forged-identity record into a capability."""
    from mage_ptcg.meta_specialist import decks as decks_module
    from mage_ptcg.meta_specialist.decks import DeckQualificationError

    structurally_forged = replace(
        qualified_asset,
        card_count=59,
        deck_identity="deck-" + "0" * 20,
    )

    with pytest.raises(DeckQualificationError, match="structural invariants"):
        decks_module._attest_qualified_deck_asset(structurally_forged)


def test_only_qualification_factory_issues_a_runtime_verifiable_asset(
    qualified_asset,
) -> None:
    """Direct construction cannot counterfeit CABT qualification authority."""
    from mage_ptcg.meta_specialist.decks import require_qualified_deck_asset

    assert require_qualified_deck_asset(qualified_asset) is qualified_asset


def test_runtime_qualification_attestation_rejects_constructor_replace_and_copy_attacks(
    qualified_asset,
) -> None:
    """The attestation is object- and content-bound, not a copyable field claim."""
    from mage_ptcg.meta_specialist.decks import (
        DeckQualificationError,
        QualifiedDeckAsset,
        require_qualified_deck_asset,
    )

    direct = QualifiedDeckAsset(
        qualified_asset.asset_id, qualified_asset.archetype_id,
        qualified_asset.card_ids, qualified_asset.deck_identity,
        qualified_asset.deck_file_sha256, qualified_asset.source_ref,
        qualified_asset.source_commit, qualified_asset.asset_class,
        qualified_asset.usage_boundary, qualified_asset.policy_compatibility,
        qualified_asset.card_database_version, qualified_asset.card_count,
        qualified_asset.cabt_legality_status, qualified_asset.cabt_legality_evidence,
    )
    replaced = replace(qualified_asset, source_ref="forged/source")
    copied_seal = getattr(qualified_asset, "_qualification_attestation")
    object.__setattr__(direct, "_qualification_attestation", copied_seal)

    raw_allocated = object.__new__(QualifiedDeckAsset)
    for field_name in (
        "asset_id", "archetype_id", "card_ids", "deck_identity",
        "deck_file_sha256", "source_ref", "source_commit", "asset_class",
        "usage_boundary", "policy_compatibility", "card_database_version",
        "card_count", "cabt_legality_status", "cabt_legality_evidence",
    ):
        object.__setattr__(
            raw_allocated, field_name, getattr(qualified_asset, field_name),
        )
    object.__setattr__(raw_allocated, "_qualification_attestation", copied_seal)

    for forged in (direct, replaced, raw_allocated):
        with pytest.raises(DeckQualificationError, match="attestation"):
            require_qualified_deck_asset(forged)

    object.__setattr__(qualified_asset, "source_ref", "mutated/source")
    with pytest.raises(DeckQualificationError, match="attestation"):
        require_qualified_deck_asset(qualified_asset)


def test_qualified_asset_attestation_registry_releases_128_dead_assets(
    alakazam_asset, alakazam_spec,
) -> None:
    """Factory authority remains object-bound without retaining every old seed."""
    import mage_ptcg.meta_specialist.decks as decks_module

    baseline_ids = set(decks_module._QUALIFIED_ASSET_REGISTRY)
    assets = [
        decks_module.qualify_deck_asset(
            alakazam_asset,
            alakazam_spec,
            known_card_ids=set(alakazam_asset.card_ids),
            cabt_legality=lambda _values: (True, "fixture-cabt-pass"),
        )
        for _ in range(128)
    ]
    references = [weakref.ref(asset) for asset in assets]
    assert len(decks_module._QUALIFIED_ASSET_REGISTRY) >= len(baseline_ids) + 128
    for asset in assets:
        assert decks_module.require_qualified_deck_asset(asset) is asset
    del asset
    del assets
    gc.collect()
    assert all(reference() is None for reference in references)
    assert set(decks_module._QUALIFIED_ASSET_REGISTRY).issubset(baseline_ids)

    seen_ids: set[int] = set()
    reused_id = False
    for _index in range(100):
        transient = decks_module.qualify_deck_asset(
            alakazam_asset,
            alakazam_spec,
            known_card_ids=set(alakazam_asset.card_ids),
            cabt_legality=lambda _values: (True, "fixture-cabt-pass"),
        )
        transient_id = id(transient)
        reused_id = reused_id or transient_id in seen_ids
        seen_ids.add(transient_id)
        assert decks_module.require_qualified_deck_asset(transient) is transient
        transient_ref = weakref.ref(transient)
        del transient
        assert transient_ref() is None
    assert reused_id
    assert set(decks_module._QUALIFIED_ASSET_REGISTRY).issubset(baseline_ids)


def test_qualification_rejects_byte_only_and_stored_identity_tampering(
    alakazam_asset, alakazam_spec
) -> None:
    """Catches byte drift independently from forged card multiset metadata."""
    from mage_ptcg.meta_specialist.decks import DeckQualificationError, qualify_deck_asset

    alakazam_asset.path.write_bytes(alakazam_asset.path.read_bytes() + b"\n")
    with pytest.raises(DeckQualificationError, match="exact file SHA-256"):
        qualify_deck_asset(
            alakazam_asset,
            alakazam_spec,
            known_card_ids=set(alakazam_asset.card_ids),
            cabt_legality=lambda values: (True, "fixture-cabt-pass"),
        )


def test_qualification_rejects_forged_multiset_identity_and_card_ids(
    alakazam_asset, alakazam_spec
) -> None:
    """Catches an asset whose stored multiset identity or cards no longer match its file."""
    from mage_ptcg.meta_specialist.decks import DeckQualificationError, qualify_deck_asset

    forged_identity = replace(alakazam_asset, deck_identity="deck-" + "0" * 20)
    with pytest.raises(DeckQualificationError, match="multiset identity"):
        qualify_deck_asset(
            forged_identity,
            alakazam_spec,
            known_card_ids=set(alakazam_asset.card_ids),
            cabt_legality=lambda values: (True, "fixture-cabt-pass"),
        )
    forged_cards = replace(
        alakazam_asset,
        card_ids=(744,) + alakazam_asset.card_ids[1:],
    )
    with pytest.raises(DeckQualificationError, match="card IDs do not match"):
        qualify_deck_asset(
            forged_cards,
            alakazam_spec,
            known_card_ids=set(forged_cards.card_ids).union(alakazam_asset.card_ids),
            cabt_legality=lambda values: (True, "fixture-cabt-pass"),
        )


def test_production_qualification_requires_operational_legality(
    alakazam_asset, alakazam_spec
) -> None:
    """Catches a production asset accepted without an operational CABT result."""
    from mage_ptcg.meta_specialist.decks import DeckQualificationError, qualify_deck_asset

    with pytest.raises(DeckQualificationError, match="CABT legality"):
        qualify_deck_asset(
            alakazam_asset,
            alakazam_spec,
            known_card_ids=set(alakazam_asset.card_ids),
            cabt_legality=None,
        )


def test_duplicate_canonical_decks_are_one_seed(qualified_asset) -> None:
    """Catches counting the same card multiset twice by changing its provenance."""
    from mage_ptcg.meta_specialist.decks import (
        DeckQualificationError,
        reject_duplicate_seed_decks,
    )

    duplicate = replace(qualified_asset, asset_id="same-bytes-new-source")
    with pytest.raises(DeckQualificationError, match="duplicate canonical deck"):
        reject_duplicate_seed_decks((qualified_asset, duplicate))


@pytest.mark.parametrize(
    ("asset_class", "usage_boundary"),
    [
        ("unreviewed_binary", "local_eval_only"),
        ("deck_only", "redistributable"),
    ],
)
def test_qualification_rejects_unpermitted_provenance_enums(
    alakazam_asset, alakazam_spec, asset_class: str, usage_boundary: str
) -> None:
    """Catches seed provenance outside the fixed asset and distribution policy."""
    from mage_ptcg.meta_specialist.decks import DeckQualificationError, qualify_deck_asset

    asset = replace(
        alakazam_asset,
        asset_class=asset_class,
        usage_boundary=usage_boundary,
    )
    with pytest.raises(DeckQualificationError, match="not permitted"):
        qualify_deck_asset(
            asset,
            alakazam_spec,
            known_card_ids=set(asset.card_ids),
            cabt_legality=lambda values: (True, "fixture-cabt-pass"),
        )


def test_asset_path_must_be_explicit() -> None:
    """Catches a fallback that silently reads repository-root deck.csv."""
    from mage_ptcg.meta_specialist.decks import DeckAssetInput, DeckQualificationError

    with pytest.raises(DeckQualificationError, match="explicit"):
        DeckAssetInput.from_path(
            asset_id="fixture",
            archetype_id="alakazam",
            path=None,
            source_ref="origin/example:deck.csv",
            source_commit="a" * 40,
            asset_class="deck_only",
            usage_boundary="local_eval_only",
            policy_compatibility="deck_only",
            card_database_version="fixture-v1",
        )


@pytest.mark.parametrize(
    ("cards", "known_card_ids", "core_card_ids", "source_commit", "cobt_result"),
    [
        ([741, 742, 743] + list(range(1000, 1056)), None, None, None, None),
        ([741, 742, 743] + list(range(1000, 1058)), None, None, None, None),
        ([True] + list(range(1000, 1059)), None, None, None, None),
        ([0] + list(range(1000, 1059)), None, None, None, None),
        ([-1] + list(range(1000, 1059)), None, None, None, None),
        ([741, 742, 743] + list(range(1000, 1057)), {741, 742, 743}, None, None, None),
        ([741, 742, 999] + list(range(1000, 1057)), None, (741, 742, 743), None, None),
        ([741, 742, 743] + list(range(1000, 1057)), None, None, "A" * 40, None),
        ([741, 742, 743] + list(range(1000, 1057)), None, None, None, (True, "")),
    ],
)
def test_qualification_fails_closed_for_invalid_deck_or_evidence(
    tmp_path: Path,
    alakazam_spec,
    cards: list[int],
    known_card_ids: set[int] | None,
    core_card_ids: tuple[int, ...] | None,
    source_commit: str | None,
    cobt_result: tuple[bool, str] | None,
) -> None:
    """Catches invalid cards, mutable provenance, missing core, or empty CABT evidence."""
    from mage_ptcg.meta_specialist.decks import (
        DeckAssetInput,
        DeckQualificationError,
        qualify_deck_asset,
    )

    deck = tmp_path / "invalid.csv"
    deck.write_text("\n".join(map(str, cards)) + "\n", encoding="utf-8")
    try:
        asset = DeckAssetInput.from_path(
            asset_id="fixture",
            archetype_id="alakazam",
            path=deck,
            source_ref="origin/example:deck.csv",
            source_commit=source_commit or "a" * 40,
            asset_class="deck_only",
            usage_boundary="local_eval_only",
            policy_compatibility="deck_only",
            card_database_version="fixture-v1",
        )
    except DeckQualificationError:
        return
    spec = replace(alakazam_spec, core_card_ids=core_card_ids) if core_card_ids else alakazam_spec
    with pytest.raises(DeckQualificationError):
        qualify_deck_asset(
            asset,
            spec,
            known_card_ids=known_card_ids or set(cards),
            cabt_legality=(lambda values: cobt_result) if cobt_result else (lambda values: (True, "ok")),
        )
