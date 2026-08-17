from __future__ import annotations

import ast
from copy import copy, deepcopy
from dataclasses import replace
import gc
import hashlib
import json
import os
from pathlib import Path
import pickle
import re
import stat
import weakref

import pytest

from mage_ptcg.continuous_league.contracts import content_id
from mage_ptcg.knowledge.model import deck_identity_from_card_ids
from mage_ptcg.meta_specialist.decks import DeckAssetInput, load_archetype_registry
from mage_ptcg.meta_specialist.seed_registry import (
    AcquiredSeedBlobV1,
    EnCardVocabulary,
    SeedRegistryError,
    acquire_seed_candidate_blob,
    bind_en_card_vocabulary,
    build_deck_asset_input,
    canonical_multiset_sha256,
    load_seed_candidate_registry,
    materialize_seed_candidate,
    read_en_card_vocabulary,
)


ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "configs/meta_specialist/seed_candidates_v1.json"
ARCHETYPES = ROOT / "configs/meta_specialist/archetypes_v1.json"
CONTENT_DOMAIN = "meta-specialist-seed-candidates-v1"
# 2026-08-05: リポジトリ所有者が公開由来の archaludon seed
# (raw_deck_sha256 42165967…) の学習利用を承認し、permission_status の変更と
# training_permission_unknown blocker の解除を行った。
# docs/decisions/2026-08-05-archaludon-training-permission.md を参照。
# pin はその判断と一緒に意図的に更新している。下の
# test_owner_approval_is_recorded_on_exactly_one_seed が新しい内容の中身を
# 直接検査するので、pin の貼り替えだけで別の変更を隠すことはできない。
EXPECTED_CONTENT_SHA256 = "d903277c3d4495e868f5f9a086325f47fb4d235777db0233eb5b4654b4e02551"
PINNED_CARD_DATABASE_SHA256 = (
    "a0ea63cf7adcb65d35436ce0eb390de6e2e35654a7c67c065a45f4abaa00f373"
)
PINNED_CARD_VOCABULARY_SHA256 = (
    "1ed4232a08f985dfc4b27eb7f6fba1871522a9f469cc9771727aca670eb2dbb9"
)
PINNED_EN_CARD_IDS = frozenset(range(1, 1268))
_EN_CARD_DATABASE_CANDIDATES = (
    ROOT / "data/raw/EN_Card_Data.csv",
    ROOT.parent.parent / "pokemon-tcg-ai-battle/data/raw/EN_Card_Data.csv",
)
PINNED_EN_CARD_DATABASE = next(
    (path for path in _EN_CARD_DATABASE_CANDIDATES if path.is_file()),
    _EN_CARD_DATABASE_CANDIDATES[0],
)
EXPECTED_DECKS = {
    "alakazam": (
        "deck-a30ed887aa341c3710cb",
        "deck-955e9457f2c039ceb975",
        "deck-d16b9760433c462aaaa0",
    ),
    "grimmsnarl_froslass_munkidori": (
        "deck-a3c1ad601869cd688cf7",
        "deck-15542189243ad04f0ce7",
        "deck-b4e53552dfcfed6ac274",
    ),
    "crustle_mega_kangaskhan": (
        "deck-d5cd1abd52f2f4d468a7",
        "deck-febe6a678e370ddaa0f5",
        "deck-84c63a6e9be0c0024521",
    ),
    "rocket_mewtwo_spidops": (
        "deck-1b51a87d7e6b95970bec",
        "deck-cb72fb2655ce44724aa8",
        "deck-2fd00ce1bf7fc3fc917b",
    ),
    "archaludon": (
        "deck-c1f964260b4b5dde6408",
        "deck-a3acb43f162dcc1f3000",
        "deck-47ebcea88dd280927efd",
    ),
}
UNMATERIALIZED = {
    "deck-b4e53552dfcfed6ac274": (
        316,
        "75f7832fd37433cd31fa4a5bed9e4524b098964c3d3478ba5debde1d56f1295e",
    ),
    "deck-febe6a678e370ddaa0f5": (
        286,
        "751345583f6d00f823dab619b06e561ba8deb8aa217fa3594d99538fe972d81c",
    ),
    "deck-84c63a6e9be0c0024521": (
        302,
        "53b972eb7a6b65efbab958b3dbebb3fd793e083e33a6064500be7fc942320752",
    ),
    "deck-cb72fb2655ce44724aa8": (
        62,
        "86b4c856399a6e879ce25da1196acf95ca72fccd8f9111d6ae26046813c36ddb",
    ),
    "deck-c1f964260b4b5dde6408": (
        121,
        "8f7c66c1ec9ce78d32b522f19fb071e41da52435ff07157045c56d186ef4ffa0",
    ),
}


def _document() -> dict[str, object]:
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def _known_card_ids(document: dict[str, object] | None = None) -> set[int]:
    source = _document() if document is None else document
    return {
        card_id
        for candidate in source["candidates"]
        for card_id in candidate["card_ids"]
        if type(card_id) is int and card_id > 0
    }


def _refresh_content_hash(document: dict[str, object]) -> None:
    payload = {key: value for key, value in document.items() if key != "content_sha256"}
    document["content_sha256"] = content_id(CONTENT_DOMAIN, payload)


def _write_document(tmp_path: Path, document: dict[str, object]) -> Path:
    path = tmp_path / "seed-candidates.json"
    path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return path


def _write_card_database(path: Path, card_ids) -> Path:
    path.write_text(
        "Card ID\n" + "\n".join(map(str, sorted(card_ids))) + "\n",
        encoding="utf-8",
    )
    return path


def _pinned_vocabulary() -> EnCardVocabulary:
    return read_en_card_vocabulary(PINNED_EN_CARD_DATABASE)


def _load(
    path: Path,
    *,
    card_vocabulary: EnCardVocabulary | None = None,
):
    return load_seed_candidate_registry(
        path,
        archetypes=load_archetype_registry(ARCHETYPES),
        card_vocabulary=_pinned_vocabulary() if card_vocabulary is None else card_vocabulary,
    )


def _candidate(registry, runtime_id: str, priority: int):
    return registry.candidates_by_runtime[runtime_id][priority - 1]


def _deck_bytes(candidate) -> bytes:
    return ("\n".join(map(str, candidate.card_ids)) + "\n").encode("utf-8")


def _acquire(candidate) -> AcquiredSeedBlobV1:
    return acquire_seed_candidate_blob(
        candidate,
        byte_provider=lambda source_ref, source_commit, source_path: _deck_bytes(candidate),
    )


def test_production_registry_pins_exact_five_lanes_and_three_seeds_each() -> None:
    document = _document()
    registry = _load(CONFIG)

    assert registry.schema_version == CONTENT_DOMAIN
    assert registry.content_sha256 == EXPECTED_CONTENT_SHA256
    assert document["content_sha256"] == EXPECTED_CONTENT_SHA256
    assert registry.card_database_sha256 == PINNED_CARD_DATABASE_SHA256
    assert registry.card_vocabulary_sha256 == PINNED_CARD_VOCABULARY_SHA256
    assert tuple(registry.candidates_by_runtime) == tuple(EXPECTED_DECKS)
    assert {
        runtime_id: tuple(candidate.deck_identity for candidate in candidates)
        for runtime_id, candidates in registry.candidates_by_runtime.items()
    } == EXPECTED_DECKS
    assert len(registry.candidates) == 15
    assert all(
        tuple(candidate.priority for candidate in candidates) == (1, 2, 3)
        for candidates in registry.candidates_by_runtime.values()
    )
    assert all(
        candidate.candidate_status == "registered_unqualified"
        and candidate.cabt_status == "NOT_RUN_REGISTERED_UNQUALIFIED"
        and len(candidate.card_ids) == 60
        and candidate.deck_identity == deck_identity_from_card_ids(candidate.card_ids)
        and candidate.canonical_multiset_sha256
        == canonical_multiset_sha256(candidate.card_ids)
        and re.fullmatch(r"[0-9a-f]{40}", candidate.source_commit)
        for candidate in registry.candidates
    )


def test_alakazam_p1_uses_pinned_source_bytes_not_ambient_baseline() -> None:
    candidate = _candidate(_load(CONFIG), "alakazam", 1)

    assert candidate.raw_deck_sha256 == (
        "167d43335013f7b68441356d750dab335088171c1ab929e083deb85a2c79e5b1"
    )
    assert not candidate.raw_deck_sha256.startswith("e92d571")
    assert candidate.source_ref == "origin/agents/nihei-alakazam"
    assert candidate.source_commit == "54bdd3b78632dc9dee03a59b302036c79c2bc518"
    assert candidate.source_path == "deck.csv"
    assert hashlib.sha256(_deck_bytes(candidate)).hexdigest() == candidate.raw_deck_sha256


def test_unmaterialized_rows_keep_exact_cards_but_remain_nonqualifiable() -> None:
    registry = _load(CONFIG)
    rows = {
        candidate.deck_identity: candidate
        for candidate in registry.candidates
        if candidate.materialization_status == "unmaterialized_meta_row"
    }

    assert set(rows) == set(UNMATERIALIZED)
    assert sum(
        candidate.materialization_status == "materialized_git_blob"
        for candidate in registry.candidates
    ) == 10
    assert all(
        candidate.raw_deck_sha256 == hashlib.sha256(_deck_bytes(candidate)).hexdigest()
        and candidate.asset_class
        == "materialized_deck_csv_deduplicated_by_canonical_multiset"
        for candidate in registry.candidates
        if candidate.materialization_status == "materialized_git_blob"
    )
    for deck_identity, (line_number, record_sha256) in UNMATERIALIZED.items():
        candidate = rows[deck_identity]
        assert len(candidate.card_ids) == 60
        assert candidate.raw_deck_sha256 is None
        assert candidate.source_path is None
        assert candidate.meta_jsonl_locator is not None
        assert candidate.meta_jsonl_locator.path == "data/meta/decks.jsonl"
        assert candidate.meta_jsonl_locator.line_number == line_number
        assert candidate.meta_jsonl_locator.record_sha256 == record_sha256
        assert candidate.permission_status == (
            "META_DERIVED_DECK_ONLY_TRAINING_PERMISSION_UNKNOWN"
        )
        assert "materialization_authority_unknown" in candidate.blocker_codes
        assert "raw_deck_bytes_unavailable" in candidate.blocker_codes


def test_registry_resource_has_no_external_inventory_or_claim_wording_dependency() -> None:
    resource = CONFIG.read_text(encoding="utf-8")
    module = (ROOT / "src/mage_ptcg/meta_specialist/seed_registry.py").read_text(
        encoding="utf-8"
    )

    assert "/tmp/" not in resource
    assert "/tmp/" not in module
    assert re.search(r"strong|champion", resource, re.IGNORECASE) is None


def test_loader_rejects_hash_drift_before_accepting_tampered_content(tmp_path: Path) -> None:
    document = _document()
    document["candidates"][0]["card_ids"][-1] = 66
    path = _write_document(tmp_path, document)

    with pytest.raises(SeedRegistryError, match="content_sha256"):
        _load(path)


def test_loader_rejects_subset_or_superset_claiming_the_pinned_en_database(
    tmp_path: Path,
) -> None:
    for index, untrusted_ids in enumerate(
        (
            _known_card_ids(),
            PINNED_EN_CARD_IDS.union({999999}),
        )
    ):
        untrusted_vocabulary = read_en_card_vocabulary(
            _write_card_database(tmp_path / f"untrusted-{index}.csv", untrusted_ids)
        )

        with pytest.raises(SeedRegistryError, match="card vocabulary"):
            _load(CONFIG, card_vocabulary=untrusted_vocabulary)


def test_loader_revalidates_vocabulary_ids_and_source_attestation(
    tmp_path: Path,
) -> None:
    forged_ids = _pinned_vocabulary()
    object.__setattr__(forged_ids, "card_ids", frozenset(_known_card_ids()))
    with pytest.raises(SeedRegistryError, match="card vocabulary attestation"):
        _load(CONFIG, card_vocabulary=forged_ids)

    wrong_source = read_en_card_vocabulary(
        _write_card_database(tmp_path / "same-ids-different-bytes.csv", PINNED_EN_CARD_IDS)
    )
    assert wrong_source.vocabulary_sha256 == PINNED_CARD_VOCABULARY_SHA256
    with pytest.raises(SeedRegistryError, match="card_database_sha256"):
        _load(CONFIG, card_vocabulary=wrong_source)


def test_loader_rejects_manually_constructed_exact_vocabulary() -> None:
    manually_constructed = EnCardVocabulary(
        card_ids=PINNED_EN_CARD_IDS,
        source_sha256=PINNED_CARD_DATABASE_SHA256,
        vocabulary_sha256=PINNED_CARD_VOCABULARY_SHA256,
    )

    with pytest.raises(SeedRegistryError, match="exact EN CSV bytes"):
        _load(CONFIG, card_vocabulary=manually_constructed)


def test_explicit_en_csv_reader_binds_exact_bytes_and_unique_ids(tmp_path: Path) -> None:
    source = tmp_path / "EN_Card_Data.csv"
    payload = b"Card ID,Card Name\n1,One\n2,Two\n2,Two duplicate\n"
    source.write_bytes(payload)

    vocabulary = read_en_card_vocabulary(source)

    assert vocabulary.card_ids == frozenset({1, 2})
    assert vocabulary.source_sha256 == hashlib.sha256(payload).hexdigest()
    assert vocabulary.vocabulary_sha256 == bind_en_card_vocabulary(
        {1, 2},
        source_sha256=vocabulary.source_sha256,
    ).vocabulary_sha256


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (b"Card ID,Card ID\n1,2\n", "exactly one Card ID"),
        (b"Card ID\n+1\n", "canonical positive decimal"),
        (b"Card ID\n01\n", "canonical positive decimal"),
        (b"Card ID\n 1\n", "canonical positive decimal"),
        (b"Card ID\n1 \n", "canonical positive decimal"),
    ],
)
def test_en_vocabulary_requires_one_header_and_canonical_positive_decimal_ids(
    tmp_path: Path,
    payload: bytes,
    message: str,
) -> None:
    source = tmp_path / "cards.csv"
    source.write_bytes(payload)

    with pytest.raises(SeedRegistryError, match=message):
        read_en_card_vocabulary(source)


def test_en_vocabulary_reads_one_bounded_no_follow_regular_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _write_card_database(tmp_path / "cards.csv", {1, 2, 3})
    original_open = os.open
    open_calls = 0

    def counting_open(target, flags, *args, **kwargs):
        nonlocal open_calls
        if Path(target) == source:
            open_calls += 1
        return original_open(target, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", counting_open)
    vocabulary = read_en_card_vocabulary(source)

    assert open_calls == 1
    assert vocabulary.card_ids == frozenset({1, 2, 3})

    symlink = tmp_path / "cards-link.csv"
    symlink.symlink_to(source)
    with pytest.raises(SeedRegistryError, match="regular|no-follow"):
        read_en_card_vocabulary(symlink)

    oversize = tmp_path / "oversize.csv"
    oversize.write_bytes(b"Card ID\n1\n")
    with oversize.open("r+b") as handle:
        handle.truncate(16 * 1024 * 1024 + 1)
    with pytest.raises(SeedRegistryError, match="maximum|size"):
        read_en_card_vocabulary(oversize)


@pytest.mark.parametrize("kind", ["directory", "fifo", "device"])
def test_en_vocabulary_rejects_nonregular_authority_paths(
    tmp_path: Path,
    kind: str,
) -> None:
    if kind == "directory":
        path = tmp_path / "directory"
        path.mkdir()
    elif kind == "fifo":
        if not hasattr(os, "mkfifo"):
            pytest.skip("FIFO is unavailable on this platform")
        path = tmp_path / "cards.fifo"
        os.mkfifo(path)
    else:
        path = Path("/dev/null")
        if not path.exists():
            pytest.skip("device fixture is unavailable on this platform")

    with pytest.raises(SeedRegistryError, match="regular"):
        read_en_card_vocabulary(path)


@pytest.mark.parametrize("scope", ["top", "candidate"])
def test_loader_rejects_unknown_keys_in_closed_schema(tmp_path: Path, scope: str) -> None:
    document = _document()
    target = document if scope == "top" else document["candidates"][0]
    target["unexpected"] = True
    _refresh_content_hash(document)

    with pytest.raises(SeedRegistryError, match="invalid keys"):
        _load(_write_document(tmp_path, document))


def test_loader_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    path = tmp_path / "duplicate.json"
    path.write_text(
        '{"schema_version":"meta-specialist-seed-candidates-v1",'
        '"schema_version":"meta-specialist-seed-candidates-v1",'
        '"content_sha256":"' + "0" * 64 + '",'
        '"card_database_sha256":"' + "0" * 64 + '","candidates":[]}',
        encoding="utf-8",
    )

    with pytest.raises(SeedRegistryError, match="duplicate JSON key"):
        _load(path)


@pytest.mark.parametrize(
    ("replacement", "message"),
    [
        ([741] * 59, "exactly 60"),
        ([741] * 59 + [True], "positive ints"),
        ([741] * 59 + [0], "positive ints"),
        ([741] * 59 + [-1], "positive ints"),
        ([741] * 59 + [999999], "unknown card IDs"),
    ],
)
def test_loader_rejects_invalid_or_unknown_card_ids(
    tmp_path: Path, replacement: list[int], message: str
) -> None:
    document = _document()
    document["candidates"][0]["card_ids"] = replacement
    _refresh_content_hash(document)

    with pytest.raises(SeedRegistryError, match=message):
        _load(_write_document(tmp_path, document))


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("canonical_multiset_sha256", "0" * 64, "canonical_multiset_sha256"),
        ("deck_identity", "deck-" + "0" * 20, "deck_identity"),
    ],
)
def test_loader_recomputes_canonical_deck_identity(
    tmp_path: Path, field: str, value: str, message: str
) -> None:
    document = _document()
    document["candidates"][0][field] = value
    _refresh_content_hash(document)

    with pytest.raises(SeedRegistryError, match=message):
        _load(_write_document(tmp_path, document))


def test_loader_requires_each_seed_to_keep_its_registered_lane_core(tmp_path: Path) -> None:
    document = _document()
    candidate = document["candidates"][0]
    candidate["card_ids"] = [
        305 if card_id == 741 else card_id for card_id in candidate["card_ids"]
    ]
    candidate["canonical_multiset_sha256"] = canonical_multiset_sha256(candidate["card_ids"])
    candidate["deck_identity"] = deck_identity_from_card_ids(candidate["card_ids"])
    _refresh_content_hash(document)

    with pytest.raises(SeedRegistryError, match="missing registered core"):
        _load(_write_document(tmp_path, document))


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda c: c.update({"raw_deck_sha256": None}), "raw_deck_sha256"),
        (lambda c: c.update({"source_path": None}), "source_path"),
        (
            lambda c: c.update(
                {
                    "meta_jsonl_locator": {
                        "path": "data/meta/decks.jsonl",
                        "line_number": 1,
                        "record_sha256": "0" * 64,
                    }
                }
            ),
            "source_path or meta_jsonl_locator",
        ),
        (lambda c: c.update({"source_commit": "A" * 40}), "source_commit"),
    ],
)
def test_loader_enforces_materialized_source_invariants(
    tmp_path: Path, mutate, message: str
) -> None:
    document = _document()
    mutate(document["candidates"][0])
    _refresh_content_hash(document)

    with pytest.raises(SeedRegistryError, match=message):
        _load(_write_document(tmp_path, document))


def test_loader_enforces_unmaterialized_source_invariants(tmp_path: Path) -> None:
    document = _document()
    row = next(
        candidate
        for candidate in document["candidates"]
        if candidate["materialization_status"] == "unmaterialized_meta_row"
    )
    row["raw_deck_sha256"] = "0" * 64
    _refresh_content_hash(document)

    with pytest.raises(SeedRegistryError, match="must be null"):
        _load(_write_document(tmp_path, document))


def test_loader_rejects_duplicate_lane_priority_and_deck_identity(tmp_path: Path) -> None:
    priority_document = _document()
    priority_document["candidates"][1]["priority"] = 1
    _refresh_content_hash(priority_document)
    with pytest.raises(SeedRegistryError, match="duplicate lane priority"):
        _load(_write_document(tmp_path, priority_document))

    deck_document = _document()
    first = deepcopy(deck_document["candidates"][0])
    first["priority"] = 2
    deck_document["candidates"][1] = first
    _refresh_content_hash(deck_document)
    with pytest.raises(SeedRegistryError, match="duplicate canonical deck"):
        _load(_write_document(tmp_path, deck_document))


def test_loader_rejects_performance_claim_wording_even_with_valid_hash(tmp_path: Path) -> None:
    document = _document()
    document["candidates"][0]["blocker_codes"][0] = "strong_candidate"
    _refresh_content_hash(document)

    with pytest.raises(SeedRegistryError, match="claim wording"):
        _load(_write_document(tmp_path, document))


def test_acquisition_then_publication_uses_exact_blob_and_selects_output_after_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = _candidate(_load(CONFIG), "alakazam", 1)
    exact_bytes = _deck_bytes(candidate)
    calls: list[tuple[str, str, str]] = []
    provider_cwd = tmp_path / "provider-cwd"
    provider_cwd.mkdir()
    output_selected = False

    def provider(source_ref: str, source_commit: str, source_path: str) -> bytes:
        assert not output_selected
        calls.append((source_ref, source_commit, source_path))
        monkeypatch.chdir(provider_cwd)
        return exact_bytes

    acquired_blob = acquire_seed_candidate_blob(candidate, byte_provider=provider)
    output = tmp_path / "materialized" / "deck.csv"
    output_selected = True
    result = materialize_seed_candidate(candidate, output, acquired_blob=acquired_blob)

    assert result == output
    assert output.read_bytes() == exact_bytes
    assert acquired_blob.payload == exact_bytes
    assert acquired_blob.deck_identity == candidate.deck_identity
    assert calls == [
        (
            "origin/agents/nihei-alakazam",
            "54bdd3b78632dc9dee03a59b302036c79c2bc518",
            "deck.csv",
        )
    ]


def test_acquisition_rejects_hash_mismatch_before_any_output_is_selected(
    tmp_path: Path,
) -> None:
    candidate = _candidate(_load(CONFIG), "alakazam", 1)
    unrelated = tmp_path / "deck.csv"
    unrelated.write_bytes(b"existing")

    with pytest.raises(SeedRegistryError, match="raw byte SHA-256"):
        acquire_seed_candidate_blob(
            candidate,
            byte_provider=lambda source_ref, source_commit, source_path: _deck_bytes(candidate)
            + b"\n",
        )

    assert unrelated.read_bytes() == b"existing"
    assert list(tmp_path.glob(".deck.csv.*")) == []


def test_acquisition_rejects_hash_matching_bytes_with_wrong_card_order(
    tmp_path: Path,
) -> None:
    document = _document()
    reordered = list(document["candidates"][0]["card_ids"])
    reordered[0], reordered[12] = reordered[12], reordered[0]
    payload = ("\n".join(map(str, reordered)) + "\n").encode("utf-8")
    document["candidates"][0]["raw_deck_sha256"] = hashlib.sha256(payload).hexdigest()
    _refresh_content_hash(document)
    candidate = _candidate(_load(_write_document(tmp_path, document)), "alakazam", 1)

    with pytest.raises(SeedRegistryError, match="card order"):
        acquire_seed_candidate_blob(
            candidate,
            byte_provider=lambda source_ref, source_commit, source_path: payload,
        )


def test_acquisition_fails_closed_before_provider_for_unknown_permission() -> None:
    registry = _load(CONFIG)
    public_candidate = _candidate(registry, "alakazam", 2)
    meta_candidate = _candidate(registry, "rocket_mewtwo_spidops", 2)
    calls = 0

    def provider(source_ref: str, source_commit: str, source_path: str) -> bytes:
        nonlocal calls
        calls += 1
        return b""

    with pytest.raises(SeedRegistryError, match="permission-approved"):
        acquire_seed_candidate_blob(public_candidate, byte_provider=provider)
    with pytest.raises(SeedRegistryError, match="separate materialization authority"):
        acquire_seed_candidate_blob(meta_candidate, byte_provider=provider)
    assert calls == 0


def test_acquisition_rejects_a_forged_unissued_candidate_before_provider() -> None:
    public_candidate = _candidate(_load(CONFIG), "alakazam", 2)
    forged = replace(
        public_candidate,
        permission_status=(
            "TEAM_INTERNAL_POLICY_MATCH_CONDITIONAL_TECHNICAL_VALIDATION_REQUIRED"
        ),
    )
    calls = 0

    def provider(source_ref: str, source_commit: str, source_path: str) -> bytes:
        nonlocal calls
        calls += 1
        return _deck_bytes(forged)

    with pytest.raises(SeedRegistryError, match="content-hash-verified registry"):
        acquire_seed_candidate_blob(forged, byte_provider=provider)

    assert calls == 0


def test_factory_issuance_is_bound_to_the_exact_candidate_and_vocabulary_objects(
    tmp_path: Path,
) -> None:
    vocabulary = _pinned_vocabulary()
    copied_vocabulary = copy(vocabulary)

    assert copied_vocabulary is not vocabulary
    with pytest.raises(SeedRegistryError, match="exact EN CSV bytes"):
        _load(CONFIG, card_vocabulary=copied_vocabulary)

    candidate = _candidate(_load(CONFIG, card_vocabulary=vocabulary), "alakazam", 1)
    copied_candidate = copy(candidate)
    provider_calls = 0

    def provider(source_ref: str, source_commit: str, source_path: str) -> bytes:
        nonlocal provider_calls
        provider_calls += 1
        return _deck_bytes(candidate)

    assert copied_candidate is not candidate
    with pytest.raises(SeedRegistryError, match="content-hash-verified registry"):
        acquire_seed_candidate_blob(copied_candidate, byte_provider=provider)

    assert provider_calls == 0


def test_factory_issuance_registries_release_dead_objects_without_identity_leaks(
    tmp_path: Path,
) -> None:
    from mage_ptcg.meta_specialist import seed_registry as registry_module

    source = _write_card_database(tmp_path / "cards.csv", {1, 2, 3})
    vocabulary_objects = [read_en_card_vocabulary(source) for _ in range(64)]
    vocabulary_refs = [weakref.ref(value) for value in vocabulary_objects]
    registries = [_load(CONFIG) for _ in range(8)]
    candidate_refs = [
        weakref.ref(candidate)
        for registry in registries
        for candidate in registry.candidates
    ]
    candidate = registries[0].candidates[0]
    acquired_blobs = [_acquire(candidate) for _ in range(32)]
    acquired_blob_refs = [weakref.ref(value) for value in acquired_blobs]

    del vocabulary_objects
    del acquired_blobs
    del candidate
    del registries
    gc.collect()

    assert all(reference() is None for reference in vocabulary_refs)
    assert all(reference() is None for reference in candidate_refs)
    assert all(reference() is None for reference in acquired_blob_refs)
    assert all(
        issued[0]() is not None
        for issued in registry_module._ISSUED_VOCABULARIES.values()
    )
    assert all(
        issued[0]() is not None
        for issued in registry_module._ISSUED_CANDIDATES.values()
    )
    assert all(
        issued[0]() is not None
        for issued in registry_module._ISSUED_ACQUIRED_BLOBS.values()
    )


def test_acquisition_rehashes_an_issued_candidate_before_provider() -> None:
    candidate = _candidate(_load(CONFIG), "alakazam", 2)
    object.__setattr__(
        candidate,
        "permission_status",
        "TEAM_INTERNAL_POLICY_MATCH_CONDITIONAL_TECHNICAL_VALIDATION_REQUIRED",
    )
    calls = 0

    def provider(source_ref: str, source_commit: str, source_path: str) -> bytes:
        nonlocal calls
        calls += 1
        return _deck_bytes(candidate)

    with pytest.raises(SeedRegistryError, match="no longer matches its registry"):
        acquire_seed_candidate_blob(candidate, byte_provider=provider)

    assert calls == 0


def test_acquisition_rejects_candidate_mutation_during_provider() -> None:
    registry = _load(CONFIG)
    internal_candidate = _candidate(registry, "alakazam", 1)
    public_candidate = _candidate(registry, "alakazam", 2)

    def provider(source_ref: str, source_commit: str, source_path: str) -> bytes:
        object.__setattr__(
            internal_candidate,
            "raw_deck_sha256",
            public_candidate.raw_deck_sha256,
        )
        object.__setattr__(internal_candidate, "card_ids", public_candidate.card_ids)
        return _deck_bytes(public_candidate)

    with pytest.raises(SeedRegistryError, match="changed during byte provider"):
        acquire_seed_candidate_blob(internal_candidate, byte_provider=provider)


def test_acquisition_uses_pre_callback_scalars_after_mutation_is_restored() -> None:
    registry = _load(CONFIG)
    internal_candidate = _candidate(registry, "alakazam", 1)
    public_candidate = _candidate(registry, "alakazam", 2)
    original_raw_sha256 = internal_candidate.raw_deck_sha256
    original_card_ids = internal_candidate.card_ids

    def provider(source_ref: str, source_commit: str, source_path: str) -> bytes:
        object.__setattr__(
            internal_candidate,
            "raw_deck_sha256",
            public_candidate.raw_deck_sha256,
        )
        object.__setattr__(internal_candidate, "card_ids", public_candidate.card_ids)
        object.__setattr__(internal_candidate, "raw_deck_sha256", original_raw_sha256)
        object.__setattr__(internal_candidate, "card_ids", original_card_ids)
        return _deck_bytes(public_candidate)

    with pytest.raises(SeedRegistryError, match="raw byte SHA-256"):
        acquire_seed_candidate_blob(internal_candidate, byte_provider=provider)


def test_acquisition_wraps_provider_exception_without_output_artifacts(tmp_path: Path) -> None:
    candidate = _candidate(_load(CONFIG), "alakazam", 1)

    def provider(source_ref: str, source_commit: str, source_path: str) -> bytes:
        raise RuntimeError("provider exploded")

    with pytest.raises(SeedRegistryError, match="provider failed"):
        acquire_seed_candidate_blob(candidate, byte_provider=provider)

    assert list(tmp_path.iterdir()) == []


def test_materialization_rejects_manual_copy_deepcopy_and_pickle_blob_forgeries(
    tmp_path: Path,
) -> None:
    candidate = _candidate(_load(CONFIG), "alakazam", 1)
    acquired_blob = _acquire(candidate)
    manual = AcquiredSeedBlobV1(
        deck_identity=acquired_blob.deck_identity,
        source_ref=acquired_blob.source_ref,
        source_commit=acquired_blob.source_commit,
        source_path=acquired_blob.source_path,
        raw_deck_sha256=acquired_blob.raw_deck_sha256,
        card_ids=acquired_blob.card_ids,
        payload=acquired_blob.payload,
    )
    for forged in (
        manual,
        copy(acquired_blob),
        deepcopy(acquired_blob),
        pickle.loads(pickle.dumps(acquired_blob)),
    ):
        assert forged is not acquired_blob
        with pytest.raises(SeedRegistryError, match="issued acquired seed blob"):
            materialize_seed_candidate(
                candidate,
                tmp_path / "forged.csv",
                acquired_blob=forged,
            )

    assert not (tmp_path / "forged.csv").exists()


def test_materialization_rejects_mutated_or_mismatched_acquired_blob(
    tmp_path: Path,
) -> None:
    registry = _load(CONFIG)
    candidate = _candidate(registry, "alakazam", 1)
    other_candidate = _candidate(registry, "grimmsnarl_froslass_munkidori", 1)
    acquired_blob = _acquire(candidate)
    output = tmp_path / "deck.csv"
    output.write_bytes(b"existing")

    with pytest.raises(SeedRegistryError, match="does not match candidate"):
        materialize_seed_candidate(
            other_candidate,
            output,
            acquired_blob=acquired_blob,
        )

    object.__setattr__(acquired_blob, "payload", acquired_blob.payload + b"\n")
    with pytest.raises(SeedRegistryError, match="attestation"):
        materialize_seed_candidate(candidate, output, acquired_blob=acquired_blob)

    assert output.read_bytes() == b"existing"
    assert list(tmp_path.glob(".deck.csv.*")) == []


def test_materialization_rejects_candidate_mutation_after_acquisition_before_io(
    tmp_path: Path,
) -> None:
    candidate = _candidate(_load(CONFIG), "alakazam", 1)
    acquired_blob = _acquire(candidate)
    object.__setattr__(candidate, "source_path", "other.csv")

    with pytest.raises(SeedRegistryError, match="no longer matches its registry"):
        materialize_seed_candidate(
            candidate,
            tmp_path / "deck.csv",
            acquired_blob=acquired_blob,
        )

    assert list(tmp_path.iterdir()) == []


def test_materialization_rejects_simulated_acquired_blob_identity_reuse_collision(
    tmp_path: Path,
) -> None:
    from mage_ptcg.meta_specialist import seed_registry as registry_module

    candidate = _candidate(_load(CONFIG), "alakazam", 1)
    acquired_blob = _acquire(candidate)
    forged = copy(acquired_blob)
    with registry_module._ISSUED_ACQUIRED_BLOBS_LOCK:
        original_issuance = registry_module._ISSUED_ACQUIRED_BLOBS[id(acquired_blob)]
        registry_module._ISSUED_ACQUIRED_BLOBS[id(forged)] = original_issuance
    try:
        with pytest.raises(SeedRegistryError, match="issued acquired seed blob"):
            materialize_seed_candidate(
                candidate,
                tmp_path / "reused.csv",
                acquired_blob=forged,
            )
    finally:
        with registry_module._ISSUED_ACQUIRED_BLOBS_LOCK:
            registry_module._ISSUED_ACQUIRED_BLOBS.pop(id(forged), None)

    assert not (tmp_path / "reused.csv").exists()


def test_materialization_is_idempotent_for_existing_byte_identical_regular_file(
    tmp_path: Path,
) -> None:
    candidate = _candidate(_load(CONFIG), "alakazam", 1)
    acquired_blob = _acquire(candidate)
    output = tmp_path / "deck.csv"
    output.write_bytes(acquired_blob.payload)
    output.chmod(0o640)
    before = output.stat()

    result = materialize_seed_candidate(candidate, output, acquired_blob=acquired_blob)

    after = output.stat()
    assert result == output
    assert (after.st_dev, after.st_ino, after.st_mode) == (
        before.st_dev,
        before.st_ino,
        before.st_mode,
    )
    assert output.read_bytes() == acquired_blob.payload
    assert list(tmp_path.glob(".deck.csv.*")) == []


@pytest.mark.parametrize("collision_kind", ("different", "symlink", "directory", "fifo"))
def test_materialization_preserves_and_rejects_every_existing_nonidentical_leaf(
    tmp_path: Path,
    collision_kind: str,
) -> None:
    candidate = _candidate(_load(CONFIG), "alakazam", 1)
    acquired_blob = _acquire(candidate)
    output = tmp_path / "deck.csv"
    victim = tmp_path / "victim.csv"
    expected_lstat: tuple[int, int] | None = None
    if collision_kind == "different":
        output.write_bytes(b"existing")
    elif collision_kind == "symlink":
        victim.write_bytes(b"victim")
        output.symlink_to(victim)
    elif collision_kind == "directory":
        output.mkdir()
        (output / "child").write_bytes(b"child")
    else:
        os.mkfifo(output)
    linked = output.lstat()
    expected_lstat = (linked.st_mode, linked.st_ino)

    with pytest.raises(SeedRegistryError, match="existing output_path"):
        materialize_seed_candidate(candidate, output, acquired_blob=acquired_blob)

    preserved = output.lstat()
    assert (preserved.st_mode, preserved.st_ino) == expected_lstat
    if collision_kind == "different":
        assert output.read_bytes() == b"existing"
    elif collision_kind == "symlink":
        assert output.is_symlink()
        assert victim.read_bytes() == b"victim"
    elif collision_kind == "directory":
        assert (output / "child").read_bytes() == b"child"
    else:
        assert stat.S_ISFIFO(preserved.st_mode)
    assert list(tmp_path.glob(".deck.csv.*")) == []


def test_materialization_collision_race_never_replaces_the_racer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mage_ptcg.meta_specialist import seed_registry as registry_module

    candidate = _candidate(_load(CONFIG), "alakazam", 1)
    acquired_blob = _acquire(candidate)
    output = tmp_path / "deck.csv"
    original_link = os.link

    def racing_link(source, destination, *args, **kwargs):
        output.write_bytes(b"racer")
        return original_link(source, destination, *args, **kwargs)

    monkeypatch.setattr(registry_module.os, "link", racing_link)

    with pytest.raises(SeedRegistryError, match="existing output_path"):
        materialize_seed_candidate(candidate, output, acquired_blob=acquired_blob)

    assert output.read_bytes() == b"racer"
    assert list(tmp_path.glob(".deck.csv.*")) == []


def test_materialization_freezes_relative_destination_before_publication_changes_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mage_ptcg.meta_specialist import seed_registry as registry_module

    candidate = _candidate(_load(CONFIG), "alakazam", 1)
    acquired_blob = _acquire(candidate)
    source_cwd = tmp_path / "source-cwd"
    racing_cwd = tmp_path / "racing-cwd"
    source_cwd.mkdir()
    racing_cwd.mkdir()
    monkeypatch.chdir(source_cwd)
    original_link = os.link

    def chdir_link(source, destination, *args, **kwargs):
        monkeypatch.chdir(racing_cwd)
        return original_link(source, destination, *args, **kwargs)

    monkeypatch.setattr(registry_module.os, "link", chdir_link)
    result = materialize_seed_candidate(
        candidate,
        Path("deck.csv"),
        acquired_blob=acquired_blob,
    )

    assert result == source_cwd / "deck.csv"
    assert result.read_bytes() == acquired_blob.payload
    assert not (racing_cwd / "deck.csv").exists()


def test_materialization_post_publish_directory_fsync_failure_preserves_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mage_ptcg.meta_specialist import seed_registry as registry_module

    candidate = _candidate(_load(CONFIG), "alakazam", 1)
    acquired_blob = _acquire(candidate)
    output = tmp_path / "deck.csv"
    original_fsync = os.fsync
    failed = False

    def failing_directory_fsync(descriptor: int) -> None:
        nonlocal failed
        if stat.S_ISDIR(os.fstat(descriptor).st_mode) and not failed:
            failed = True
            raise OSError("directory fsync fault")
        original_fsync(descriptor)

    monkeypatch.setattr(registry_module.os, "fsync", failing_directory_fsync)

    with pytest.raises(SeedRegistryError, match="durably publish"):
        materialize_seed_candidate(candidate, output, acquired_blob=acquired_blob)

    assert failed
    assert output.read_bytes() == acquired_blob.payload
    assert list(tmp_path.glob(".deck.csv.*")) == []


def test_materialization_post_publish_temp_cleanup_failure_is_bounded_and_sanitized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mage_ptcg.meta_specialist import seed_registry as registry_module

    candidate = _candidate(_load(CONFIG), "alakazam", 1)
    acquired_blob = _acquire(candidate)
    output = tmp_path / "deck.csv"
    original_unlink = os.unlink

    def failing_temp_unlink(path, *args, **kwargs):
        if isinstance(path, str) and path.startswith(".deck.csv.tmp."):
            raise OSError("cleanup-secret")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(registry_module.os, "unlink", failing_temp_unlink)

    with pytest.raises(SeedRegistryError, match="temporary cleanup failed") as raised:
        materialize_seed_candidate(candidate, output, acquired_blob=acquired_blob)

    assert "cleanup-secret" not in str(raised.value)
    assert ".deck.csv.tmp." not in str(raised.value)
    assert output.read_bytes() == acquired_blob.payload
    temporary_files = list(tmp_path.glob(".deck.csv.tmp.*"))
    assert len(temporary_files) == 1
    assert temporary_files[0].read_bytes() == acquired_blob.payload
    assert list(tmp_path.glob("*.rollback.*")) == []
    assert list(tmp_path.glob("*.quarantine.*")) == []


def test_materialization_pre_publish_fsync_and_cleanup_fault_preserves_primary_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mage_ptcg.meta_specialist import seed_registry as registry_module

    candidate = _candidate(_load(CONFIG), "alakazam", 1)
    acquired_blob = _acquire(candidate)
    output = tmp_path / "deck.csv"
    original_fsync = os.fsync
    original_unlink = os.unlink

    def failing_file_fsync(descriptor: int) -> None:
        if stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise OSError("primary-secret")
        original_fsync(descriptor)

    def failing_temp_unlink(path, *args, **kwargs):
        if isinstance(path, str) and path.startswith(".deck.csv.tmp."):
            raise OSError("cleanup-secret")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(registry_module.os, "fsync", failing_file_fsync)
    monkeypatch.setattr(registry_module.os, "unlink", failing_temp_unlink)

    with pytest.raises(SeedRegistryError, match="prepare materialization temporary file") as raised:
        materialize_seed_candidate(candidate, output, acquired_blob=acquired_blob)

    assert isinstance(raised.value.__cause__, OSError)
    assert "primary-secret" in str(raised.value.__cause__)
    assert "primary-secret" not in str(raised.value)
    assert "cleanup-secret" not in str(raised.value)
    assert not output.exists()
    assert len(list(tmp_path.glob(".deck.csv.tmp.*"))) == 1


def test_seed_materializer_source_contains_no_callback_rollback_or_quarantine_design() -> None:
    source = (ROOT / "src/mage_ptcg/meta_specialist/seed_registry.py").read_text(
        encoding="utf-8"
    )

    assert "byte_provider" not in source[source.index("def materialize_seed_candidate") :]
    assert "rollback" not in source.lower()
    assert "quarantine" not in source.lower()


def test_materialized_builder_requires_explicit_matching_path(tmp_path: Path) -> None:
    candidate = _candidate(_load(CONFIG), "alakazam", 1)
    output = tmp_path / "deck.csv"
    materialize_seed_candidate(
        candidate,
        output,
        acquired_blob=_acquire(candidate),
    )

    asset = build_deck_asset_input(
        candidate,
        materialized_path=output,
        card_database_version="fixture-en-v1",
    )

    assert asset.path == output
    assert asset.card_ids == candidate.card_ids
    assert asset.deck_identity == candidate.deck_identity
    assert asset.deck_file_sha256 == candidate.raw_deck_sha256
    assert asset.source_ref == "origin/agents/nihei-alakazam:deck.csv"
    assert asset.source_commit == candidate.source_commit
    assert asset.asset_class == "deck_only"
    assert asset.usage_boundary == "local_eval_only"

    with pytest.raises(SeedRegistryError, match="explicit materialized_path"):
        build_deck_asset_input(
            candidate,
            materialized_path=None,
            card_database_version="fixture-en-v1",
        )


def test_builder_rejects_unmaterialized_row_and_file_drift(tmp_path: Path) -> None:
    registry = _load(CONFIG)
    meta_candidate = _candidate(registry, "archaludon", 1)
    with pytest.raises(SeedRegistryError, match="materialized_git_blob"):
        build_deck_asset_input(
            meta_candidate,
            materialized_path=tmp_path / "absent.csv",
            card_database_version="fixture-en-v1",
        )

    candidate = _candidate(registry, "alakazam", 1)
    path = tmp_path / "drift.csv"
    path.write_bytes(_deck_bytes(candidate) + b"\n")
    with pytest.raises(SeedRegistryError, match="raw byte SHA-256"):
        build_deck_asset_input(
            candidate,
            materialized_path=path,
            card_database_version="fixture-en-v1",
        )


def test_builder_reads_one_snapshot_and_rejects_replacement_before_return(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = _load(CONFIG)
    candidate = _candidate(registry, "alakazam", 1)
    replacement_candidate = _candidate(registry, "alakazam", 2)
    path = tmp_path / "deck.csv"
    materialize_seed_candidate(
        candidate,
        path,
        acquired_blob=_acquire(candidate),
    )
    replacement = tmp_path / "replacement.csv"
    replacement.write_bytes(_deck_bytes(replacement_candidate))
    original_from_snapshot = DeckAssetInput.from_snapshot.__func__
    open_calls = 0
    original_open = os.open

    def counting_open(target, flags, *args, **kwargs):
        nonlocal open_calls
        if Path(target) == path:
            open_calls += 1
        return original_open(target, flags, *args, **kwargs)

    def racing_from_snapshot(cls, *, snapshot, **kwargs):
        asset = original_from_snapshot(cls, snapshot=snapshot, **kwargs)
        replacement.replace(path)
        return asset

    monkeypatch.setattr(os, "open", counting_open)
    monkeypatch.setattr(DeckAssetInput, "from_snapshot", classmethod(racing_from_snapshot))

    with pytest.raises(SeedRegistryError, match="snapshot path changed"):
        build_deck_asset_input(
            candidate,
            materialized_path=path,
            card_database_version="fixture-en-v1",
        )

    assert open_calls == 1


def test_builder_rejects_a_manually_reconstructed_candidate(tmp_path: Path) -> None:
    candidate = _candidate(_load(CONFIG), "alakazam", 1)
    reconstructed = replace(candidate)
    path = tmp_path / "deck.csv"
    path.write_bytes(_deck_bytes(reconstructed))

    with pytest.raises(SeedRegistryError, match="content-hash-verified registry"):
        build_deck_asset_input(
            reconstructed,
            materialized_path=path,
            card_database_version="fixture-en-v1",
        )


def test_materializer_has_no_subprocess_git_or_network_library_dependency() -> None:
    source_path = ROOT / "src/mage_ptcg/meta_specialist/seed_registry.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    forbidden = {"subprocess", "git", "requests", "urllib", "httpx", "socket"}
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split(".", 1)[0])

    assert imports.isdisjoint(forbidden)


def test_owner_approval_is_recorded_on_exactly_one_seed() -> None:
    """所有者承認が、意図した 1 件だけに、正しい形で入っていること。

    content hash の pin を貼り替えるだけでは通らないよう、承認の中身を直接見る。
    公開由来の資産を team internal と偽っていないことも併せて固定する。
    """
    document = _document()
    approved = [
        candidate for candidate in document["candidates"]
        if candidate["permission_status"] == "PUBLIC_SOURCE_TRAINING_APPROVED_BY_REPOSITORY_OWNER"
    ]

    assert len(approved) == 1, f"承認は 1 件のはず: {[c['runtime_id'] for c in approved]}"
    seed = approved[0]
    assert seed["runtime_id"] == "archaludon"
    assert seed["raw_deck_sha256"].startswith("42165967")
    assert seed["source_path"] == "opponents/tomatomato_archaludon/deck.csv"
    # 出所は公開である。team internal の usage を流用していないこと。
    assert seed["usage_boundary"] == "public_source_training_approved_local_only"
    assert "training_permission_unknown" not in seed["blocker_codes"]


def test_owner_approval_does_not_clear_other_blockers() -> None:
    """承認が消してよいのは permission blocker だけである。"""
    document = _document()
    seed = next(
        c for c in document["candidates"]
        if c["permission_status"] == "PUBLIC_SOURCE_TRAINING_APPROVED_BY_REPOSITORY_OWNER"
    )

    # cabt legality / runtime qualification はそれぞれの証拠で解除される。
    assert "competition_legality_not_confirmed" in seed["blocker_codes"]


def test_no_other_seed_silently_gained_training_permission() -> None:
    """他の公開 seed は training_permission_unknown を保ったままであること。"""
    document = _document()
    public = [
        c for c in document["candidates"]
        if c["permission_status"] == "DECK_ONLY_OR_LOCAL_EVAL_SOURCE_TRAINING_PERMISSION_UNKNOWN"
    ]

    assert public, "公開由来 seed が 1 件も残っていないのは想定外"
    for candidate in public:
        assert "training_permission_unknown" in candidate["blocker_codes"], (
            f"{candidate['runtime_id']} が承認なしに permission blocker を失っている"
        )
