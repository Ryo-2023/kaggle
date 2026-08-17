from pathlib import Path

from mage_ptcg.optimization.alakazam_deck_search import (
    AlakazamDeckSearchError,
    load_mutations,
    load_slot_catalog,
    mutate_deck,
    validate_catalog_counts,
)


ROOT = Path(__file__).parents[1]
BASELINE = [int(line) for line in (ROOT / "configs" / "alakazam" / "baseline_v1.json").read_text().split('"deck": [', 1)[1].split(']', 1)[0].split(',')]


def test_catalog_has_exclusive_60_copy_accounting() -> None:
    catalog = load_slot_catalog(ROOT / "configs" / "alakazam" / "slot_catalog_v2.json")
    counts = validate_catalog_counts(BASELINE, catalog)
    assert counts["copies"] == 60
    assert sum(counts[key] for key in counts if key.endswith("_copies")) == 60


def test_every_pre_registered_mutation_is_one_legal_exchange() -> None:
    catalog = load_slot_catalog(ROOT / "configs" / "alakazam" / "slot_catalog_v2.json")
    _registry, mutations = load_mutations(ROOT / "configs" / "alakazam" / "flex_candidates_v2.json")
    assert len(mutations) == 16
    assert len({mutation.candidate_id for mutation in mutations}) == 16
    for mutation in mutations:
        candidate = mutate_deck(BASELINE, mutation, catalog)
        assert len(candidate) == 60
        assert candidate.count(mutation.remove) == BASELINE.count(mutation.remove) - 1
        assert candidate.count(mutation.add) == BASELINE.count(mutation.add) + 1


def test_ace_spec_second_copy_is_rejected_before_cabt() -> None:
    catalog = load_slot_catalog(ROOT / "configs" / "alakazam" / "slot_catalog_v2.json")
    _registry, mutations = load_mutations(ROOT / "configs" / "alakazam" / "flex_candidates_v2.json")
    illegal = next(mutation for mutation in mutations if mutation.candidate_id == "hammer_to_rare_candy")
    assert mutate_deck(BASELINE, illegal, catalog).count(1079) == 4
    from mage_ptcg.optimization.alakazam_deck_search import DeckMutation
    ace_spec = DeckMutation("invalid", 1081, 13, "", "", "", "")
    try:
        mutate_deck(BASELINE, ace_spec, catalog)
    except AlakazamDeckSearchError:
        pass
    else:
        raise AssertionError("second ACE SPEC must be rejected")
