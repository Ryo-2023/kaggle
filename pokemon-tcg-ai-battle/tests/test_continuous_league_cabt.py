from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from main import read_deck_csv
from mage_ptcg.continuous_league.benchmark import ScheduledGame, SubjectDeck
from mage_ptcg.continuous_league.cabt import CabtMatchExecutor
from mage_ptcg.continuous_league.candidate_runtime import load_runtime_policy
from mage_ptcg.continuous_league.catalog import CatalogEntry
from mage_ptcg.continuous_league.checkpoint_stream import publish_checkpoint
from mage_ptcg.continuous_league.contracts import content_id
from mage_ptcg.policy_learning.r2d3.checkpoint import save_checkpoint
from mage_ptcg.policy_learning.r2d3.model import (
    R2D3ModelConfig,
    RecurrentDistributionalQ,
)


torch = pytest.importorskip("torch")
ROOT = Path(__file__).resolve().parents[1]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_model_only_runtime_completes_one_official_cabt_game(tmp_path: Path) -> None:
    deck_path = ROOT / "deck.csv"
    deck = list(read_deck_csv(deck_path))
    config = R2D3ModelConfig(hidden_size=16, atoms=5)
    model = RecurrentDistributionalQ(config)
    optimizer = torch.optim.AdamW(model.parameters(), lr=1e-4)
    checkpoint = tmp_path / "checkpoint.pt"
    save_checkpoint(
        checkpoint,
        model=model,
        target=model,
        optimizer=optimizer,
        population_hash=content_id("test", "population"),
        replay_manifest_hash=content_id("test", "replay"),
        step=0,
    )
    published = publish_checkpoint(
        checkpoint_path=checkpoint,
        output_root=tmp_path / "stream",
        model_config=config,
        deck=deck,
    )
    runtime_id = published["runtime_policy_id"]
    runtime = load_runtime_policy(
        tmp_path / "stream" / "runtime_policies" / runtime_id
    )
    opponent_deck_path = tmp_path / "opponent-deck.csv"
    opponent_deck_path.write_text(
        "\n".join(str(card) for card in reversed(deck)) + "\n",
        encoding="utf-8",
    )
    opponent = CatalogEntry(
        asset_id="rule-v0-alternate-deck",
        policy_id="rule-v0",
        deck_id="alternate-deck",
        source_id="deck-pool:test",
        policy_kind="rule_v0",
        runtime_path="builtin:rule_v0",
        deck_path=str(opponent_deck_path),
        policy_hash=_sha(ROOT / "agents" / "rule_agent.py"),
        deck_hash=_sha(opponent_deck_path),
        source_hash=content_id("test", "repository"),
        runtime_config_hash=content_id("test", "rule-v0"),
        role="BENCHMARK_VISIBLE",
        archetype_id="RULE",
    )
    executor = CabtMatchExecutor(
        runtime_policy=runtime,
        subject_decks=(
            SubjectDeck("subject", str(deck_path), _sha(deck_path)),
        ),
        output_root=tmp_path / "matches",
        scratch_root=tmp_path / "scratch",
        max_steps=10_000,
        save_failures_html=False,
    )
    game_key = content_id("test", "cabt-game")
    result, policy = executor.execute(
        ScheduledGame(
            benchmark_id=content_id("test", "benchmark"),
            runtime_policy_id=runtime_id,
            subject_deck_id="subject",
            opponent_instance_id=opponent.opponent_instance_id,
            seat="subject_first",
            repetition_index=0,
            execution_block="smoke",
            env_seed=74_000,
            game_key=game_key,
        ),
        opponent,
    )
    assert result["outcome"] in {"win", "loss", "draw"}
    assert result["steps"] > 0
    assert policy.traces


def test_model_only_runtime_can_be_the_cabt_opponent(tmp_path: Path) -> None:
    deck_path = ROOT / "deck.csv"
    deck = list(read_deck_csv(deck_path))
    config = R2D3ModelConfig(hidden_size=16, atoms=5)
    model = RecurrentDistributionalQ(config)
    optimizer = torch.optim.AdamW(model.parameters(), lr=1e-4)
    checkpoint = tmp_path / "checkpoint.pt"
    save_checkpoint(
        checkpoint,
        model=model,
        target=model,
        optimizer=optimizer,
        population_hash=content_id("test", "population"),
        replay_manifest_hash=content_id("test", "replay"),
        step=0,
    )
    published = publish_checkpoint(
        checkpoint_path=checkpoint,
        output_root=tmp_path / "stream",
        model_config=config,
        deck=deck,
    )
    runtime_id = published["runtime_policy_id"]
    runtime_path = tmp_path / "stream" / "runtime_policies" / runtime_id
    runtime = load_runtime_policy(runtime_path)
    opponent = CatalogEntry(
        asset_id=f"runtime-policy-{runtime_id}",
        policy_id=runtime_id,
        deck_id="runtime-deck",
        source_id="training-checkpoint:test",
        policy_kind="runtime_policy",
        runtime_path=str(runtime_path),
        deck_path=str(deck_path),
        policy_hash=runtime_id,
        deck_hash=runtime.manifest["deck_hash"],
        source_hash=runtime.manifest["training_checkpoint_id"],
        runtime_config_hash=content_id("test", "runtime-policy"),
        role="TRAINING_ACTIVE",
        archetype_id="R2D3_HISTORY",
    )
    executor = CabtMatchExecutor(
        runtime_policy=runtime,
        subject_decks=(
            SubjectDeck("subject", str(deck_path), _sha(deck_path)),
        ),
        output_root=tmp_path / "matches",
        scratch_root=tmp_path / "scratch",
        max_steps=10_000,
        save_failures_html=False,
    )
    game_key = content_id("test", "runtime-vs-runtime")
    result, policy = executor.execute(
        ScheduledGame(
            benchmark_id=content_id("test", "benchmark"),
            runtime_policy_id=runtime_id,
            subject_deck_id="subject",
            opponent_instance_id=opponent.opponent_instance_id,
            seat="opponent_first",
            repetition_index=0,
            execution_block="smoke",
            env_seed=74_001,
            game_key=game_key,
        ),
        opponent,
    )
    assert result["outcome"] in {"win", "loss", "draw"}
    assert result["steps"] > 0
    assert result["candidate_side"] == 1
    assert policy.traces


def test_submitted_snapshot_manifest_is_rebased_before_worker_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catalog-relative snapshots must not be resolved from a game scratch dir."""
    import mage_ptcg.continuous_league.cabt as cabt

    snapshot_root = tmp_path / "catalog" / "runtime_snapshots" / "submitted"
    snapshot_root.mkdir(parents=True)
    deck_path = snapshot_root / "deck.csv"
    deck_path.write_text("1\n", encoding="utf-8")
    policy_path = snapshot_root / "main.py"
    policy_path.write_text("def agent(_observation): return []\n", encoding="utf-8")
    manifest_path = snapshot_root / ".submitted_snapshot_manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "asset_id": "agents/example",
                "source_commit": "abc123",
                "snapshot_root": "runs/catalog/runtime_snapshots/submitted",
                "deck_path": "runs/catalog/runtime_snapshots/submitted/deck.csv",
                "entrypoint": "main.py:agent",
                "adapter_type": "isolated_jsonl_python_v1",
                "deck_hash": _sha(deck_path),
                "policy_hash": _sha(policy_path),
                "source_lineage": "origin/agents/example",
                "deck_family": "TEST",
            }
        ),
        encoding="utf-8",
    )
    entry = CatalogEntry(
        asset_id="submitted-example",
        policy_id="submitted-policy",
        deck_id="submitted-deck",
        source_id="origin/agents/example",
        policy_kind="submitted_snapshot",
        runtime_path=str(manifest_path),
        deck_path=str(deck_path),
        policy_hash=_sha(policy_path),
        deck_hash=_sha(deck_path),
        source_hash=content_id("test", "submitted-source"),
        runtime_config_hash=content_id("test", "submitted-runtime"),
        role="BENCHMARK_VISIBLE",
    )
    created_specs = []

    class CapturingWorker:
        def __init__(self, spec, *, scratch_root):
            created_specs.append((spec, scratch_root))

    monkeypatch.setattr(cabt, "SubmittedAgentWorker", CapturingWorker)
    executor = CabtMatchExecutor(
        runtime_policy=object(),
        subject_decks=(),
        output_root=tmp_path / "matches",
        scratch_root=tmp_path / "scratch",
    )
    game = ScheduledGame(
        benchmark_id=content_id("test", "benchmark"),
        runtime_policy_id=content_id("test", "runtime"),
        subject_deck_id="subject",
        opponent_instance_id=entry.opponent_instance_id,
        seat="subject_first",
        repetition_index=0,
        execution_block="main",
        env_seed=71_000,
        game_key=content_id("test", "submitted-game"),
    )

    factory = executor._opponent_factory(game, entry, [])
    factory([], 0)

    spec, scratch_root = created_specs[0]
    assert spec.snapshot_root == snapshot_root.resolve()
    assert spec.deck_path == deck_path.resolve()
    assert scratch_root == tmp_path / "scratch"
