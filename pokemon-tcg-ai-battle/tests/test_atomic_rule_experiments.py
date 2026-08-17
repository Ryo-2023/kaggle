from __future__ import annotations

from pathlib import Path

import pytest

from main import read_deck_csv
from mage_ptcg.optimization.atomic_rules import AtomicRule, AtomicRuleError, candidates
from mage_ptcg.optimization.outcome import deck_digest


def _deck() -> list[int]: return list(read_deck_csv(Path("deck.csv")))


def test_atomic_candidates_are_unique_exact_deck_and_one_rule_each() -> None:
    rows = candidates(_deck())
    assert len(rows) == 4 and len({row.rule_id for row in rows}) == 4 and len({row.config_hash for row in rows}) == 4
    assert all(row.exact_deck_hash == deck_digest(_deck()) and row.max_overrides_per_game == 1 for row in rows)


def test_atomic_rule_rejects_retired_or_multiple_semantic_rule_names() -> None:
    row = candidates(_deck())[0]
    with pytest.raises(AtomicRuleError):
        AtomicRule("sparse-cem-b-00", "rule-v0", row.exact_deck_hash, row.family, row.family_rule_id, row.phase, "0", 2, 1, "bad").validate()
    with pytest.raises(AtomicRuleError):
        AtomicRule("new", "rule-v0", row.exact_deck_hash, row.family, "SETUP_BASIC+EVOLVE_ANCHOR", row.phase, "0", 2, 1, "bad").validate()
