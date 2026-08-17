from __future__ import annotations

from mage_ptcg.opponents.synthetic_stress_v1 import KINDS, make_synthetic_stress_agent, registry

DECK = [1] * 60


def _obs() -> dict:
    return {"select": {"option": [{"type": 14}, {"type": 13}, {"type": 8}, {"type": 9}, {"type": 7}], "minCount": 1, "maxCount": 1}}


def test_synthetic_profiles_are_legal_distinct_and_rule_fallback() -> None:
    actions = {}
    for kind in KINDS:
        policy = make_synthetic_stress_agent(kind=kind, deck=DECK, seed=7)
        action = policy.choose(_obs())
        assert len(action) == 1 and 0 <= action[0] < 5
        actions[kind] = action
        assert policy.choose({"select": {"option": [{"type": 999}], "minCount": 1, "maxCount": 1}}) == [0]
        assert policy.fallback_count == 1
    assert len({tuple(value) for value in actions.values()}) >= 3


def test_seeded_random_and_registry_identity_are_deterministic() -> None:
    first = make_synthetic_stress_agent(kind="legal-random", deck=DECK, seed=11)
    second = make_synthetic_stress_agent(kind="legal-random", deck=DECK, seed=11)
    assert first.choose(_obs()) == second.choose(_obs())
    rows = registry(DECK)
    assert {row["policy_id"] for row in rows} == {"synthetic-" + kind + "-v1" for kind in KINDS}
    assert all(row["privacy"] == "PUBLIC_LEGAL_OPTIONS_ONLY" for row in rows)
