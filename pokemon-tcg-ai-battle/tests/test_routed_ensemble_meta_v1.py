from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
from types import SimpleNamespace

import pytest

from mage_ptcg.meta_specialist.cg_bestknown_loop_v1 import CgBestKnownLoopError, build_fresh_meta_batch_v1
from mage_ptcg.meta_specialist.cg_weekend_split_v1 import load_weekend_split
from mage_ptcg.observability.cabt_trace import canonical_deck_sha256
from mage_ptcg.opponent_ingest.kaggle_kernel_meta_v1 import load_candidate_agent, write_candidate_wrapper
from mage_ptcg.opponent_ingest.routed_ensemble_meta_v1 import (
    RoutedEnsembleMetaError,
    build_routed_ensemble_split_v1,
    route_parent_index,
    seal_routed_ensemble_meta_v1,
)


def _source(tmp_path: Path, name: str, *, energy: int, payload_text: str | None = None) -> Path:
    root = tmp_path / name
    payload = root / "payload"
    payload.mkdir(parents=True)
    (payload / "original_main.py").write_text(
        payload_text or "def agent(observation, configuration=None):\n    return []\n",
        encoding="utf-8",
    )
    write_candidate_wrapper(name, payload, root / "main.py")
    cards = [energy] * 59 + [1247]
    (root / "deck.csv").write_text("\n".join(str(card) for card in cards) + "\n", encoding="utf-8")
    policy_sha = hashlib.sha256((root / "main.py").read_bytes()).hexdigest()
    source_policy_sha = hashlib.sha256((payload / "original_main.py").read_bytes()).hexdigest()
    deck_sha = canonical_deck_sha256(cards)
    (root / "pool_manifest.json").write_text(
        json.dumps(
            [
                {
                    "id": name,
                    "policy_hash": policy_sha,
                    "source_policy_sha256": source_policy_sha,
                    "canonical_deck_hash": deck_sha,
                    "smoke_ok": True,
                    "source": "test_sealed_source",
                    "source_branch": f"test/{name}",
                    "source_commit": "a" * 40,
                    "usage_boundary": "local_eval_only",
                }
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (root / "SOURCE.md").write_text(
        f"# Test source\n\n- source policy SHA-256: `{source_policy_sha}`\n",
        encoding="utf-8",
    )
    return root


def _direct_source(tmp_path: Path, name: str, *, energy: int) -> Path:
    """Build a smoke-qualified self-owned parent whose entrypoint is root/main.py."""

    root = tmp_path / name
    root.mkdir(parents=True)
    main_text = "def agent(observation, configuration=None):\n    return []\n"
    (root / "main.py").write_text(main_text, encoding="utf-8")
    cards = [energy] * 59 + [1247]
    (root / "deck.csv").write_text("\n".join(str(card) for card in cards) + "\n", encoding="utf-8")
    policy_sha = hashlib.sha256(main_text.encode("utf-8")).hexdigest()
    deck_sha = canonical_deck_sha256(cards)
    (root / "pool_manifest.json").write_text(
        json.dumps(
            [
                {
                    "id": name,
                    "policy_hash": policy_sha,
                    "source_policy_sha256": policy_sha,
                    "canonical_deck_hash": deck_sha,
                    "smoke_ok": True,
                    "source": "test_self_owned_source",
                    "source_branch": f"test/{name}",
                    "source_commit": "b" * 40,
                    "usage_boundary": "local_eval_only",
                }
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (root / "SOURCE.md").write_text(
        f"# Test direct source\n\n- source policy SHA-256: `{policy_sha}`\n",
        encoding="utf-8",
    )
    return root


def _p1(tmp_path: Path) -> Path:
    root = tmp_path / "p1"
    root.mkdir()
    (root / "main.py").write_text("def agent(observation, configuration=None):\n    return []\n", encoding="utf-8")
    (root / "deck.csv").write_text("\n".join(["1"] * 59 + ["1247"]) + "\n", encoding="utf-8")
    return root


def _specs() -> tuple[dict[str, str], ...]:
    return (
        {"id": "hash_ab", "policy_a": "policy_a", "policy_b": "policy_b", "deck_parent": "deck_c", "routing_recipe": "PUBLIC_HASH_V1"},
        {"id": "turn_bc", "policy_a": "policy_b", "policy_b": "deck_c", "deck_parent": "policy_a", "routing_recipe": "TURN_PARITY_V1"},
        {"id": "board_ca", "policy_a": "deck_c", "policy_b": "policy_a", "deck_parent": "policy_b", "routing_recipe": "OPPONENT_BOARD_HASH_V1"},
        {"id": "context_ac", "policy_a": "policy_a", "policy_b": "deck_c", "deck_parent": "policy_b", "routing_recipe": "CONTEXT_TURN_HASH_V1"},
    )


def _action_specs() -> tuple[dict[str, str], ...]:
    return tuple(
        {
            "id": f"action_{index}",
            "policy_a": "policy_a" if index % 2 == 0 else "policy_b",
            "policy_b": "policy_b" if index % 2 == 0 else "policy_a",
            "deck_parent": "deck_c",
            "routing_recipe": recipe,
        }
        for index, recipe in enumerate(
            (
                "ACTION_LEVEL_KO_MIX_V1",
                "ACTION_LEVEL_TEMPO_MIX_V1",
                "ACTION_LEVEL_SETUP_MIX_V1",
                "ACTION_LEVEL_HASH_MIX_V1",
            ),
        )
    )


def test_route_parent_index_uses_only_actor_visible_fields() -> None:
    class Hidden:
        def __getattr__(self, name: str):
            if name in {"hand", "prize", "deck", "discard"}:
                raise AssertionError(f"private field accessed: {name}")
            raise AttributeError(name)

    state = SimpleNamespace(
        turn=3,
        yourIndex=0,
        players=[
            SimpleNamespace(active=[SimpleNamespace(id=1)], bench=[SimpleNamespace(id=2)]),
            SimpleNamespace(active=[SimpleNamespace(id=3)], bench=[None]),
        ],
        stadium=[SimpleNamespace(id=1247)],
    )
    observation = SimpleNamespace(current=state, select=SimpleNamespace(context="PLAY"), private=Hidden())
    assert route_parent_index(observation, "PUBLIC_HASH_V1") in {0, 1}
    assert route_parent_index(observation, "TURN_PARITY_V1") == 1

    semantic_state = SimpleNamespace(
        turn=10,
        yourIndex=0,
        players=[
            SimpleNamespace(active=[SimpleNamespace(id=1)], bench=[]),
            SimpleNamespace(
                active=[SimpleNamespace(id=3, hp=50, maxHp=100, appearThisTurn=False)],
                bench=[SimpleNamespace(id=4), SimpleNamespace(id=5)],
            ),
        ],
        stadium=[],
    )
    semantic_observation = SimpleNamespace(current=semantic_state, select=SimpleNamespace(context="ATTACK"), private=Hidden())
    assert route_parent_index(semantic_observation, "OPPONENT_DAMAGE_SWITCH_V1") == 1
    assert route_parent_index(semantic_observation, "OPPONENT_BOARD_SIZE_SWITCH_V1") == 1
    assert route_parent_index(semantic_observation, "CONTEXT_THREAT_SWITCH_V1") == 1

    empty_bench_state = SimpleNamespace(
        turn=1,
        yourIndex=0,
        players=[
            SimpleNamespace(active=[SimpleNamespace(id=1)], bench=[]),
            SimpleNamespace(active=[SimpleNamespace(id=3, hp=100, maxHp=100)], bench=[]),
        ],
        stadium=[],
    )
    empty_bench_observation = SimpleNamespace(current=empty_bench_state, select=SimpleNamespace(context="PLAY"), private=Hidden())
    assert route_parent_index(empty_bench_observation, "OPPONENT_DAMAGE_SWITCH_V1") == 0
    assert route_parent_index(empty_bench_observation, "OPPONENT_BOARD_SIZE_SWITCH_V1") == 0


def test_action_level_recipe_uses_only_actor_visible_fields() -> None:
    class Hidden:
        def __getattr__(self, name: str):
            if name in {"hand", "prize", "deck", "discard"}:
                raise AssertionError(f"private field accessed: {name}")
            raise AttributeError(name)

    observation = SimpleNamespace(
        current=SimpleNamespace(
            turn=4,
            yourIndex=0,
            players=[
                SimpleNamespace(active=[SimpleNamespace(id=1, hp=90, maxHp=120)], bench=[]),
                SimpleNamespace(active=[SimpleNamespace(id=2, hp=40, maxHp=120)], bench=[None]),
            ],
            stadium=[],
        ),
        select=SimpleNamespace(context="ATTACK"),
        private=Hidden(),
    )
    for recipe in (
        "ACTION_LEVEL_KO_MIX_V1",
        "ACTION_LEVEL_TEMPO_MIX_V1",
        "ACTION_LEVEL_SETUP_MIX_V1",
        "ACTION_LEVEL_HASH_MIX_V1",
        "ACTION_LEVEL_CONSENSUS_MIX_V1",
        "ACTION_LEVEL_CONSENSUS_HASH_V1",
        "ACTION_LEVEL_CONSENSUS_KO_V1",
    ):
        assert route_parent_index(observation, recipe) in {0, 1}


def test_consensus_action_level_recipe_prefers_legal_intersection(tmp_path: Path) -> None:
    parents = {
        "policy_a": _source(
            tmp_path,
            "policy_a",
            energy=1,
            payload_text="def agent(observation, configuration=None):\n    return [0, 1]\n",
        ),
        "policy_b": _source(
            tmp_path,
            "policy_b",
            energy=1,
            payload_text="def agent(observation, configuration=None):\n    return [1, 2]\n",
        ),
        "deck_c": _source(tmp_path, "deck_c", energy=1),
    }
    generated = tmp_path / "generated_consensus"
    seal_routed_ensemble_meta_v1(
        parent_roots=parents,
        specifications=(
            {
                "id": "consensus",
                "policy_a": "policy_a",
                "policy_b": "policy_b",
                "deck_parent": "deck_c",
                "routing_recipe": "ACTION_LEVEL_CONSENSUS_MIX_V1",
            },
            {
                "id": "consensus_2",
                "policy_a": "policy_b",
                "policy_b": "policy_a",
                "deck_parent": "deck_c",
                "routing_recipe": "ACTION_LEVEL_CONSENSUS_MIX_V1",
            },
            {
                "id": "consensus_3",
                "policy_a": "policy_a",
                "policy_b": "policy_b",
                "deck_parent": "deck_c",
                "routing_recipe": "ACTION_LEVEL_CONSENSUS_MIX_V1",
            },
        ),
        output_root=generated,
        source_epoch="consensus-test",
        seed_namespace="seed-consensus-test",
        p1_package=_p1(tmp_path),
    )
    candidate_id = json.loads((generated / "pool_manifest.json").read_text(encoding="utf-8"))[0]["id"]
    agent = load_candidate_agent(generated / candidate_id / "main.py")

    class Hidden:
        def __getattr__(self, name: str):
            if name in {"hand", "prize", "deck", "discard"}:
                raise AssertionError(f"private field accessed: {name}")
            raise AttributeError(name)

    observation = SimpleNamespace(
        current=SimpleNamespace(turn=2, yourIndex=0, players=[SimpleNamespace(active=[], bench=[]), SimpleNamespace(active=[], bench=[])]),
        select=SimpleNamespace(
            option=[SimpleNamespace(type="PLAY"), SimpleNamespace(type="ATTACK"), SimpleNamespace(type="END")],
            minCount=1,
            maxCount=2,
            context="PLAY",
        ),
        private=Hidden(),
    )
    assert agent(observation) == [1]


def test_seal_emits_fresh_smoke_false_routed_candidates(tmp_path: Path) -> None:
    parents = {
        "policy_a": _source(tmp_path, "policy_a", energy=1),
        "policy_b": _source(tmp_path, "policy_b", energy=1),
        "deck_c": _source(tmp_path, "deck_c", energy=1),
    }
    current = tmp_path / "current" / "pool_manifest.json"
    current.parent.mkdir()
    current.write_text("[]\n", encoding="utf-8")
    generated = tmp_path / "generated"
    report = seal_routed_ensemble_meta_v1(
        parent_roots=parents,
        specifications=_specs(),
        output_root=generated,
        source_epoch="routed-test",
        seed_namespace="seed-test",
        p1_package=_p1(tmp_path),
        current_pool_manifest=current,
    )
    assert report["status"] == "SEALED"
    assert report["recipe"] == "ACTOR_VISIBLE_ROUTED_ENSEMBLE_V1"
    assert report["source_kind"] == "internal_actor_visible_routed_ensemble"
    assert report["accepted_count"] == 4
    rows = json.loads((generated / "pool_manifest.json").read_text(encoding="utf-8"))
    assert all(row["smoke_ok"] is False for row in rows)
    assert len({row["policy_hash"] for row in rows}) == 4
    assert len({(row["policy_hash"], row["canonical_deck_hash"]) for row in rows}) == 4
    for row in rows:
        assert (generated / row["id"] / "parent_a" / "payload" / "original_main.py").is_file()
        assert (generated / row["id"] / "parent_b" / "payload" / "original_main.py").is_file()
        assert (generated / row["id"] / "parent_a" / "deck.csv").is_file()
        assert (generated / row["id"] / "parent_b" / "deck.csv").is_file()
        agent = load_candidate_agent(generated / row["id"] / "main.py")
        assert agent(None) == []
    with pytest.raises(CgBestKnownLoopError, match="not smoke-qualified"):
        build_fresh_meta_batch_v1(manifest_path=generated / "fresh_meta.json", pool_manifest_path=generated / "pool_manifest.json")


def test_seal_emits_action_level_mixer_candidates(tmp_path: Path) -> None:
    parents = {
        "policy_a": _source(tmp_path, "policy_a", energy=1),
        "policy_b": _source(tmp_path, "policy_b", energy=1),
        "deck_c": _source(tmp_path, "deck_c", energy=1),
    }
    generated = tmp_path / "generated_action"
    report = seal_routed_ensemble_meta_v1(
        parent_roots=parents,
        specifications=_action_specs(),
        output_root=generated,
        source_epoch="action-test",
        seed_namespace="seed-action-test",
        p1_package=_p1(tmp_path),
    )
    assert report["accepted_count"] == 4
    assert report["recipe"] == "ACTOR_VISIBLE_ACTION_LEVEL_MIX_V1"
    assert report["source_kind"] == "internal_actor_visible_action_level_mixer"
    rows = json.loads((generated / "pool_manifest.json").read_text(encoding="utf-8"))
    assert {row["routing_recipe"] for row in rows} == {
        "ACTION_LEVEL_KO_MIX_V1",
        "ACTION_LEVEL_TEMPO_MIX_V1",
        "ACTION_LEVEL_SETUP_MIX_V1",
        "ACTION_LEVEL_HASH_MIX_V1",
    }
    meta = json.loads((generated / "meta_manifest.json").read_text(encoding="utf-8"))
    assert meta["source_kind"] == "internal_actor_visible_action_level_mixer"
    for row in rows:
        wrapper = (generated / row["id"] / "main.py").read_text(encoding="utf-8")
        assert "_action_level_pick" in wrapper
        assert "private" not in wrapper.lower()
        assert load_candidate_agent(generated / row["id"] / "main.py")(None) == []


def test_split_rebind_requires_smoke_and_binds_promoted_pool(tmp_path: Path) -> None:
    parents = {
        "policy_a": _source(tmp_path, "policy_a", energy=1),
        "policy_b": _source(tmp_path, "policy_b", energy=1),
        "deck_c": _source(tmp_path, "deck_c", energy=1),
    }
    generated = tmp_path / "generated"
    p1 = _p1(tmp_path)
    seal_routed_ensemble_meta_v1(
        parent_roots=parents,
        specifications=_specs(),
        output_root=generated,
        source_epoch="routed-test",
        seed_namespace="seed-test",
        p1_package=p1,
    )
    with pytest.raises(RoutedEnsembleMetaError, match="after smoke promotion"):
        build_routed_ensemble_split_v1(output_root=generated, p1_package=p1)

    promoted = tmp_path / "promoted"
    shutil.copytree(generated, promoted)
    pool_path = promoted / "pool_manifest.json"
    rows = json.loads(pool_path.read_text(encoding="utf-8"))
    for row in rows:
        row["smoke_ok"] = True
    pool_path.write_text(json.dumps(rows, sort_keys=True) + "\n", encoding="utf-8")
    fresh_path = promoted / "fresh_meta.json"
    fresh = json.loads(fresh_path.read_text(encoding="utf-8"))
    fresh["pool_manifest_sha256"] = hashlib.sha256(pool_path.read_bytes()).hexdigest()
    fresh_path.write_text(json.dumps(fresh, sort_keys=True) + "\n", encoding="utf-8")
    (promoted / "meta_manifest.json").unlink()
    (promoted / "cg_historical_split.json").unlink()

    rebound = build_routed_ensemble_split_v1(output_root=promoted, p1_package=p1)
    assert rebound["status"] == "SEALED"
    split = load_weekend_split(promoted / "cg_historical_split.json")
    assert len(split.ids("META_TRAIN")) == 2
    assert len(split.ids("META_DEV")) == 1
    assert len(split.ids("META_FINAL")) == 1


def test_seal_rejects_mismatched_parent_and_deck_parent(tmp_path: Path) -> None:
    parents = {
        "policy_a": _source(tmp_path, "policy_a", energy=1),
        "policy_b": _source(tmp_path, "policy_b", energy=2),
        "deck_c": _source(tmp_path, "deck_c", energy=3),
    }
    with pytest.raises(RoutedEnsembleMetaError, match="deck parent canonical hash"):
        seal_routed_ensemble_meta_v1(
            parent_roots=parents,
            specifications=_specs(),
            output_root=tmp_path / "generated",
            source_epoch="routed-test",
            seed_namespace="seed-test",
            p1_package=_p1(tmp_path),
        )


def test_seal_copies_parent_deck_for_relative_payload_import(tmp_path: Path) -> None:
    payload = (
        "import os\n"
        "file_path = 'deck.csv'\n"
        "if not os.path.exists(file_path):\n"
        "    file_path = '/kaggle_simulations/agent/deck.csv'\n"
        "with open(file_path, 'r', encoding='utf-8') as handle:\n"
        "    _deck = [int(line.strip()) for line in handle if line.strip()]\n"
        "def agent(observation, configuration=None):\n"
        "    return []\n"
    )
    parents = {
        "policy_a": _source(tmp_path, "policy_a", energy=1, payload_text=payload),
        "policy_b": _source(tmp_path, "policy_b", energy=1, payload_text=payload),
        "deck_c": _source(tmp_path, "deck_c", energy=1, payload_text=payload),
    }
    generated = tmp_path / "generated"
    seal_routed_ensemble_meta_v1(
        parent_roots=parents,
        specifications=_specs(),
        output_root=generated,
        source_epoch="routed-test",
        seed_namespace="seed-test",
        p1_package=_p1(tmp_path),
    )
    rows = json.loads((generated / "pool_manifest.json").read_text(encoding="utf-8"))
    for row in rows:
        agent = load_candidate_agent(generated / row["id"] / "main.py")
        assert agent(None) == []


def test_seal_loads_self_owned_direct_main_parent(tmp_path: Path) -> None:
    parents = {
        "direct_a": _direct_source(tmp_path, "direct_a", energy=1),
        "direct_b": _direct_source(tmp_path, "direct_b", energy=1),
        "direct_c": _direct_source(tmp_path, "direct_c", energy=1),
    }
    generated = tmp_path / "generated_direct"
    specifications = tuple(
        {
            "id": f"direct_{index}",
            "policy_a": "direct_a" if index % 2 == 0 else "direct_b",
            "policy_b": "direct_b" if index % 2 == 0 else "direct_c",
            "deck_parent": "direct_c",
            "routing_recipe": recipe,
        }
        for index, recipe in enumerate(("PUBLIC_HASH_V1", "TURN_PARITY_V1", "OPPONENT_BOARD_HASH_V1"), start=1)
    )
    seal_routed_ensemble_meta_v1(
        parent_roots=parents,
        specifications=specifications,
        output_root=generated,
        source_epoch="routed-direct-test",
        seed_namespace="seed-direct-test",
        p1_package=_p1(tmp_path),
    )
    rows = json.loads((generated / "pool_manifest.json").read_text(encoding="utf-8"))
    assert len(rows) == 3
    for row in rows:
        candidate = generated / row["id"]
        assert (candidate / "parent_a" / "main.py").is_file()
        assert (candidate / "parent_b" / "main.py").is_file()
        agent = load_candidate_agent(candidate / "main.py")
        assert agent(None) == []
