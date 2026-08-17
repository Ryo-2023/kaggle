from __future__ import annotations

import json

from mage_ptcg.opponents.self_owned_action_adapter_v1 import adapt_action_v1


def _observation(*, options, minimum=1, maximum=1):
    return {
        "current": {"turn": 3, "yourIndex": 0},
        "select": {
            "type": 0,
            "context": 0,
            "option": list(options),
            "minCount": minimum,
            "maxCount": maximum,
        },
    }


def test_initial_deck_action_is_not_perturbed():
    deck = list(range(60))
    observation = {"current": None, "select": None}
    assert adapt_action_v1(deck, observation, salt="x", perturbation_rate=1.0) == deck


def test_forced_perturbation_keeps_single_selection_legal_and_same_option_type():
    observation = _observation(options=[{"type": 14}, {"type": 14}, {"type": 13}])
    result = adapt_action_v1([0], observation, salt="x", perturbation_rate=1.0)
    assert result == [1]


def test_multi_selection_preserves_count_uniqueness_and_option_bounds():
    observation = _observation(
        options=[{"type": 4}, {"type": 4}, {"type": 7}, {"type": 4}],
        minimum=2,
        maximum=3,
    )
    result = adapt_action_v1([0, 2], observation, salt="x", perturbation_rate=1.0)
    assert len(result) == 2
    assert len(set(result)) == 2
    assert all(0 <= index < 4 for index in result)
    assert {observation["select"]["option"][index]["type"] for index in result} == {4, 7}


def test_invalid_base_action_falls_back_to_required_prefix():
    observation = _observation(options=[{"type": 14}, {"type": 13}], minimum=1, maximum=2)
    assert adapt_action_v1([99, 0], observation, salt="x", perturbation_rate=0.0) == [0]


def test_source_generator_embeds_base_policy_and_records_lineage(tmp_path):
    from mage_ptcg.opponent_ingest.self_owned_adapter_v1 import generate_self_owned_adapter_v1

    base = tmp_path / "base"
    base.mkdir()
    (base / "main.py").write_text("def agent(observation):\n    return [0]\n", encoding="utf-8")
    (base / "deck.csv").write_text("1\n" * 60, encoding="utf-8")
    output = tmp_path / "generated"
    report = generate_self_owned_adapter_v1(
        base_candidate_root=base,
        output_root=output,
        adapter_id="adapter-test",
        salt="test-salt",
        perturbation_rate=0.25,
    )
    assert report["adapter_id"] == "adapter-test"
    assert (output / "main.py").is_file()
    assert "def _base_agent(" in (output / "main.py").read_text(encoding="utf-8")
    assert (output / "deck.csv").read_text(encoding="utf-8").split() == ["1"] * 60


def test_generated_pool_is_hash_bound_and_research_only(tmp_path):
    from mage_ptcg.opponent_ingest.self_owned_adapter_v1 import generate_self_owned_adapter_v1, seal_self_owned_adapter_pool_v1

    base = tmp_path / "base"
    base.mkdir()
    (base / "main.py").write_text("def agent(observation):\n    return [0]\n", encoding="utf-8")
    (base / "deck.csv").write_text("1\n" * 60, encoding="utf-8")
    package = tmp_path / "package"
    generate_self_owned_adapter_v1(
        base_candidate_root=base,
        output_root=package,
        adapter_id="adapter-pool-test",
        salt="test-salt",
        perturbation_rate=0.25,
    )
    pool = tmp_path / "pool"
    report = seal_self_owned_adapter_pool_v1(
        candidate_package_root=package,
        output_root=pool,
        source_epoch="test-epoch",
        seed_namespace="test-seed",
    )
    assert report["research_only"] is True
    assert json.loads((pool / "pool_manifest.json").read_text(encoding="utf-8"))[0]["smoke_ok"] is False
    fresh = json.loads((pool / "fresh_meta.json").read_text(encoding="utf-8"))
    assert fresh["authority"]["promotion_allowed"] is False
    reference = fresh["references"][0]
    assert reference["freshness_evidence_path"] == "evidence/adapter-pool-test.json"
    assert (pool / reference["freshness_evidence_path"]).is_file()
