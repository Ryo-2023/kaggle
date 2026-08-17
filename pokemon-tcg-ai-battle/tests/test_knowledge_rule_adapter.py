"""C2a Knowledge Pack soft-prior integration tests for both Rule agents."""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import pytest

from agents.rule_agent import choose_rule_indices, rank_rule_indices
from agents.rule_agent_v1 import RuleAgentV1
from mage_ptcg.decision_state import build_action_key, build_decision_state
import mage_ptcg.knowledge.compatibility as knowledge_compatibility
from mage_ptcg.knowledge import (
    ActionPrior,
    KnowledgeConfidence,
    KnowledgeManifest,
    KnowledgePack,
    KnowledgeRuleAdapter,
    RuntimeCompatibility,
    build_team_deck_pack,
    content_hash,
    serialize_pack,
)
from mage_ptcg.knowledge.compatibility import DEFAULT_CABT_VERSION
from mage_ptcg.knowledge.adapter import _safe_action_keys
from main import make_rule_agent, make_rule_agent_v1


def _deck(path: Path) -> Path:
    path.write_text("\n".join(["1"] * 60) + "\n", encoding="utf-8")
    return path


def _pack_with_exact_play_prior(tmp_path: Path) -> KnowledgePack:
    base = build_team_deck_pack(_deck(tmp_path / "deck.csv"), source="fixture")
    action = build_action_key(selection_type=0, context=0, option={"type": 7, "index": 1})
    prior = ActionPrior(
        rule_id="exact-second-play",
        score=5.0,
        priority=5,
        confidence=KnowledgeConfidence(1.0, 1.0, 1.0),
        source_ref="test fixture",
        action_key_digest=action.digest,
    )
    priors = tuple(sorted((*base.action_priors, prior), key=lambda item: item.rule_id))
    content = {
        "action_priors": [item.to_payload() for item in priors],
        "compatibility": {
            "action_key_schema_version": base.manifest.action_key_schema_version,
            "cabt_version": base.manifest.cabt_version,
            "card_pool_id": base.manifest.card_pool_id,
            "card_pool_version": base.manifest.card_pool_version,
            "schema_version": base.manifest.schema_version,
        },
        "source": base.manifest.source,
        "team_deck": base.team_deck.to_payload(),
    }
    digest = content_hash(content)
    manifest = replace(base.manifest, content_hash=digest, pack_id=f"knowledge-pack-v0-{digest[:20]}")
    return KnowledgePack(manifest=manifest, team_deck=base.team_deck, action_priors=priors)


def _main_observation() -> dict:
    return {
        "select": {
            "type": 0,
            "context": 0,
            "option": [{"type": 7, "index": 0}, {"type": 7, "index": 1}, {"type": 14}],
            "minCount": 1,
            "maxCount": 1,
        }
    }


def _tool_observation() -> dict:
    def card(card_id: int, serial: int, *, tools: list[dict] | None = None) -> dict:
        return {
            "id": card_id,
            "serial": serial,
            "playerIndex": 0,
            "hp": 100,
            "maxHp": 100,
            "appearThisTurn": False,
            "energies": [],
            "energyCards": [],
            "tools": tools if tools is not None else [],
            "preEvolution": [],
        }

    def player(*, active: list[dict] | None = None) -> dict:
        return {
            "active": active if active is not None else [],
            "asleep": False,
            "bench": [],
            "benchMax": 5,
            "burned": False,
            "confused": False,
            "deckCount": 53,
            "discard": [],
            "hand": [card(701, 1)],
            "handCount": 1,
            "paralyzed": False,
            "poisoned": False,
            "prize": [object() for _ in range(6)],
        }

    options = [
        {"type": 4, "area": 4, "index": 0, "playerIndex": 0, "toolIndex": 0},
        {"type": 4, "area": 4, "index": 0, "playerIndex": 0, "toolIndex": 1},
    ]
    return {
        "current": {
            "energyAttached": False,
            "firstPlayer": 0,
            "players": [
                player(active=[card(201, 2, tools=[card(301, 3), card(302, 4)])]),
                player(),
            ],
            "result": -1,
            "retreated": False,
            "stadium": [],
            "stadiumPlayed": False,
            "supporterPlayed": False,
            "turn": 2,
            "turnActionCount": 3,
            "yourIndex": 0,
        },
        "select": {
            "type": 2,
            "context": 28,
            "option": options,
            "minCount": 1,
            "maxCount": 1,
        },
        "step": 7,
    }


def _adapter(pack: KnowledgePack) -> KnowledgeRuleAdapter:
    return KnowledgeRuleAdapter.create(
        pack,
        RuntimeCompatibility(
            schema_version=pack.manifest.schema_version,
            action_key_schema_version=pack.manifest.action_key_schema_version,
            cabt_version=pack.manifest.cabt_version,
            card_pool_id=pack.manifest.card_pool_id,
            card_pool_version=pack.manifest.card_pool_version,
            deck_id=pack.team_deck.deck_id,
        ),
    )


def _pack_with_manifest_values(base: KnowledgePack, **values: str) -> KnowledgePack:
    manifest = replace(base.manifest, **values)
    content = {
        "action_priors": [item.to_payload() for item in base.action_priors],
        "compatibility": {
            "action_key_schema_version": manifest.action_key_schema_version,
            "cabt_version": manifest.cabt_version,
            "card_pool_id": manifest.card_pool_id,
            "card_pool_version": manifest.card_pool_version,
            "schema_version": manifest.schema_version,
        },
        "source": manifest.source,
        "team_deck": base.team_deck.to_payload(),
    }
    digest = content_hash(content)
    manifest = replace(manifest, content_hash=digest, pack_id=f"knowledge-pack-v0-{digest[:20]}")
    return KnowledgePack(manifest=manifest, team_deck=base.team_deck, action_priors=base.action_priors)


def _write_schema_mismatch_pack(base: KnowledgePack, path: Path) -> Path:
    payload = json.loads(serialize_pack(base).decode("utf-8"))
    payload["manifest"]["schema_version"] = "knowledge-pack-v99"
    content = {
        "action_priors": payload["action_priors"],
        "compatibility": {
            **base.content_payload()["compatibility"],
            "schema_version": "knowledge-pack-v99",
        },
        "source": payload["manifest"]["source"],
        "team_deck": payload["team_deck"],
    }
    digest = content_hash(content)
    payload["manifest"]["content_hash"] = digest
    payload["manifest"]["pack_id"] = f"knowledge-pack-v0-{digest[:20]}"
    path.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    return path


def test_runtime_cabt_version_uses_installed_distribution_version(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(knowledge_compatibility, "version", lambda _name: "9.9.9")

    assert knowledge_compatibility.runtime_cabt_version() == "kaggle-environments-9.9.9"


def test_broken_runtime_metadata_falls_back_without_crashing_factories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken_version(_name: str) -> str:
        raise RuntimeError("broken metadata")

    monkeypatch.setattr(knowledge_compatibility, "version", broken_version)
    pack = _pack_with_manifest_values(
        _pack_with_exact_play_prior(tmp_path), action_key_schema_version="decision-state-v99"
    )
    observation = _main_observation()
    baseline_v0 = make_rule_agent(deck=[1] * 60)(observation)
    baseline_v1 = make_rule_agent_v1(deck=[1] * 60)(observation)

    assert knowledge_compatibility.runtime_cabt_version() == DEFAULT_CABT_VERSION
    assert make_rule_agent(deck=[1] * 60, knowledge_pack=pack)(observation) == baseline_v0
    assert make_rule_agent_v1(deck=[1] * 60, knowledge_pack=pack)(observation) == baseline_v1


def test_soft_prior_reorders_only_a_rule_v0_score_tie_and_stays_legal(tmp_path: Path) -> None:
    pack = _pack_with_exact_play_prior(tmp_path)
    observation = _main_observation()
    baseline = choose_rule_indices(observation)
    result = _adapter(pack).reorder_ties(observation, baseline or [], rank_rule_indices(observation))

    assert baseline == [0]
    assert result == [1]
    assert all(0 <= index < 3 for index in result)
    assert len(result) == len(set(result)) == 1


def test_toolcard_prior_uses_verified_decision_state_identity(tmp_path: Path) -> None:
    observation = _tool_observation()
    second = build_decision_state(observation).legal_actions[1].action_key
    base = build_team_deck_pack(_deck(tmp_path / "deck.csv"), source="fixture")
    prior = ActionPrior(
        rule_id="exact-second-tool",
        score=5.0,
        priority=5,
        confidence=KnowledgeConfidence(1.0, 1.0, 1.0),
        source_ref="test fixture",
        action_key_digest=second.digest,
    )
    pack = _pack_with_priors(base, (prior,))

    assert _adapter(pack).reorder_ties(
        observation,
        [0],
        [(0, 400), (1, 400)],
    ) == [1]


def test_skill_prior_uses_board_resolved_public_identity() -> None:
    """Skill prior construction must not silently fall back to a redacted direct key."""
    observation = _tool_observation()
    observation["select"] = {
        "type": 5,
        "context": 34,
        "option": [{"type": 15, "cardId": 201, "serial": 2}],
        "minCount": 1,
        "maxCount": 1,
    }

    key = _safe_action_keys(observation)[0]

    assert key.to_public_trace_payload()["public_identity"]["source"]["kind"] == "public_card"


def test_non_tied_rule_v0_safety_decision_cannot_be_reversed(tmp_path: Path) -> None:
    pack = _pack_with_exact_play_prior(tmp_path)
    observation = _main_observation()
    observation["select"]["option"] = [{"type": 9}, {"type": 7, "index": 1}]
    baseline = choose_rule_indices(observation)

    assert _adapter(pack).reorder_ties(observation, baseline or [], rank_rule_indices(observation)) == baseline == [0]


def test_factories_preserve_no_pack_behavior_and_fallback_on_deck_mismatch(tmp_path: Path) -> None:
    pack = _pack_with_exact_play_prior(tmp_path)
    observation = _main_observation()
    deck = [1] * 60
    assert make_rule_agent(deck=deck)(observation) == [0]
    assert make_rule_agent_v1(deck=deck)(observation) == make_rule_agent_v1(deck=deck)(observation)
    assert make_rule_agent(deck=deck, knowledge_pack=pack)(observation) == [1]
    assert make_rule_agent(deck=[2] * 60, knowledge_pack=pack)(observation) == [0]
    assert make_rule_agent_v1(deck=[2] * 60, knowledge_pack=pack)(observation) == make_rule_agent_v1(
        deck=[2] * 60
    )(observation)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("action_key_schema_version", "decision-state-v99"),
        ("cabt_version", "kaggle-environments-v99"),
        ("card_pool_id", "wrong-pool"),
        ("card_pool_version", "wrong-version"),
    ],
)
def test_factories_fallback_for_each_runtime_metadata_mismatch(
    tmp_path: Path, field: str, value: str
) -> None:
    pack = _pack_with_manifest_values(_pack_with_exact_play_prior(tmp_path), **{field: value})
    observation = _main_observation()
    baseline_v0 = make_rule_agent(deck=[1] * 60)(observation)
    baseline_v1 = make_rule_agent_v1(deck=[1] * 60)(observation)

    assert make_rule_agent(deck=[1] * 60, knowledge_pack=pack)(observation) == baseline_v0
    assert make_rule_agent_v1(deck=[1] * 60, knowledge_pack=pack)(observation) == baseline_v1


def test_factories_fallback_for_invalid_knowledge_schema(tmp_path: Path) -> None:
    schema_mismatch = _write_schema_mismatch_pack(
        _pack_with_exact_play_prior(tmp_path), tmp_path / "schema-mismatch.json"
    )
    observation = _main_observation()
    baseline_v0 = make_rule_agent(deck=[1] * 60)(observation)
    baseline_v1 = make_rule_agent_v1(deck=[1] * 60)(observation)

    assert make_rule_agent(deck=[1] * 60, knowledge_pack=schema_mismatch)(observation) == baseline_v0
    assert make_rule_agent_v1(deck=[1] * 60, knowledge_pack=schema_mismatch)(observation) == baseline_v1


def test_adapter_error_and_hidden_fields_do_not_escape_or_change_output(tmp_path: Path) -> None:
    pack = _pack_with_exact_play_prior(tmp_path)

    class GuardedObservation(dict):
        def get(self, key, default=None):
            if key in {"opponentHand", "logs", "search_begin_input"}:
                raise AssertionError(f"forbidden read: {key}")
            return super().get(key, default)

    observation = GuardedObservation(_main_observation())
    observation["opponentHand"] = ["secret"]
    assert _adapter(pack).reorder_ties(observation, [0], [(0, 400), (1, 400), (2, -1000)]) == [1]
    broken = KnowledgeRuleAdapter.create(None, None)
    assert broken.reorder_ties(observation, [0], [(0, 400)]) == [0]


def test_neutral_prior_keeps_baseline_order_for_unmatched_and_equal_ties(tmp_path: Path) -> None:
    base = _pack_with_exact_play_prior(tmp_path)
    unmatched = _pack_with_manifest_values(base)
    unmatched_prior = ActionPrior(
        rule_id="unmatched",
        score=9.0,
        priority=9,
        confidence=KnowledgeConfidence(1.0, 1.0, 1.0),
        source_ref="test fixture",
        option_type=999,
    )
    unmatched = _pack_with_priors(unmatched, (unmatched_prior,))
    equal = _pack_with_priors(
        base, tuple(prior for prior in base.action_priors if prior.rule_id == "rule-v0-main-play-tie-break")
    )
    observation = _main_observation()
    baseline = choose_rule_indices(observation)
    ranked = rank_rule_indices(observation)

    assert _adapter(unmatched).reorder_ties(observation, baseline or [], ranked) == baseline
    assert _adapter(equal).reorder_ties(observation, baseline or [], ranked) == baseline


def _pack_with_priors(base: KnowledgePack, priors: tuple[ActionPrior, ...]) -> KnowledgePack:
    ordered = tuple(sorted(priors, key=lambda item: item.rule_id))
    content = {
        "action_priors": [item.to_payload() for item in ordered],
        "compatibility": base.content_payload()["compatibility"],
        "source": base.manifest.source,
        "team_deck": base.team_deck.to_payload(),
    }
    digest = content_hash(content)
    manifest = replace(base.manifest, content_hash=digest, pack_id=f"knowledge-pack-v0-{digest[:20]}")
    return KnowledgePack(manifest=manifest, team_deck=base.team_deck, action_priors=ordered)


def test_min_max_determinism_and_rule_v1_reset_remain_safe(tmp_path: Path) -> None:
    pack = _pack_with_exact_play_prior(tmp_path)
    adapter = _adapter(pack)
    observation = _main_observation()
    observation["select"]["minCount"] = 2
    observation["select"]["maxCount"] = 2
    baseline = choose_rule_indices(observation)
    first = adapter.reorder_ties(observation, baseline or [], rank_rule_indices(observation))
    second = adapter.reorder_ties(observation, baseline or [], rank_rule_indices(observation))
    assert first == second
    assert len(first) == 2 and len(first) == len(set(first))
    agent = RuleAgentV1(knowledge_adapter=adapter)
    assert agent.choose({}) is None
    assert agent.last_source == "reset"


@pytest.mark.parametrize("candidate", [[3], [0, 0], [], [0, 1, 2]])
def test_rule_v1_rejects_adversarial_knowledge_candidates(candidate: list[int]) -> None:
    class AdversarialAdapter:
        def reorder_ties(self, observation: object, baseline: list[int], ranked: object) -> list[int]:
            return candidate

    observation = _main_observation()
    observation["select"]["option"] = [{"type": 7, "index": 0}, {"type": 7, "index": 1}]
    observation["select"]["minCount"] = 1
    observation["select"]["maxCount"] = 2
    baseline = choose_rule_indices(observation)
    agent = RuleAgentV1(knowledge_adapter=AdversarialAdapter())  # type: ignore[arg-type]

    assert agent.choose(observation) == baseline
    assert agent.last_source == "knowledge_invalid_candidate_fallback"


def test_rule_v1_adapter_exception_falls_back_to_baseline() -> None:
    class RaisingAdapter:
        def reorder_ties(self, observation: object, baseline: list[int], ranked: object) -> list[int]:
            raise ValueError("injected adapter fault")

    observation = _main_observation()
    baseline = choose_rule_indices(observation)
    assert RuleAgentV1(knowledge_adapter=RaisingAdapter()).choose(observation) == baseline  # type: ignore[arg-type]
