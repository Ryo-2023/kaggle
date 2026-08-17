from __future__ import annotations

import csv
import hashlib
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

from mage_ptcg.continuous_league.contracts import load_json
from mage_ptcg.continuous_league.catalog import CatalogEntry
from mage_ptcg.continuous_league import qualification
from mage_ptcg.continuous_league.qualification import qualify_ref, resolve_ref_asset
from mage_ptcg.continuous_league.source_intake import (
    build_qualified_submitted_catalog,
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_catalog_adds_public_deck_pool_to_training_population(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    (repo / "agents").mkdir(parents=True)
    (repo / "main.py").write_text("def agent(obs): return []\n", encoding="utf-8")
    (repo / "deck.csv").write_text("1\n" * 60, encoding="utf-8")
    (repo / "agents" / "rule_agent.py").write_text(
        "def agent(obs):\n    return [0]\n", encoding="utf-8"
    )
    (repo / "agents" / "rule_agent_v1.py").write_text(
        "def agent(obs): return []\n", encoding="utf-8"
    )
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.invalid"],
        cwd=repo,
        check=True,
    )
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", "fixture"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True
    ).strip()
    subprocess.run(
        ["git", "update-ref", "refs/remotes/origin/agents/test", head],
        cwd=repo,
        check=True,
    )

    ledger = tmp_path / "qualification.csv"
    row = {
        "asset_id": "agents/test",
        "ref": "origin/agents/test",
        "branch_tip": head,
        "source_commit": head,
        "deck_id": "deck.csv",
        "deck_hash": _sha(repo / "deck.csv"),
        "policy_id": "test",
        "policy_hash": _sha(repo / "main.py"),
        "entrypoint": "main.py:agent",
        "local_runtime_status": "PROXY_RUNTIME_PASSED",
        "smoke_games": "2",
        "teacher_eligible": "True",
        "official_runtime_evidence": "False",
    }
    with ledger.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(row))
        writer.writeheader()
        writer.writerow(row)

    public_deck = tmp_path / "public-deck.txt"
    public_deck.write_text("2\n" * 60, encoding="utf-8")
    public_deck_hash = hashlib.sha256(b"public-deck-identity").hexdigest()
    pool_path = tmp_path / "pool.json"
    pool_path.write_text(
        json.dumps(
            {
                "schema": "r2d3-deck-opponent-pool-v1",
                "pool_hash": hashlib.sha256(b"pool").hexdigest(),
                "entries": [
                    {
                        "deck_id": "deck-public",
                        "deck_hash": public_deck_hash,
                        "deck_path": str(public_deck),
                        "source_kind": "KAGGLE_PUBLIC_REPLAY",
                        "source_id": "episode:1",
                        "source_commit": None,
                        "episode_id": 1,
                        "team_name": "public-team",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    output = tmp_path / "catalog"
    report = build_qualified_submitted_catalog(
        repo=repo,
        qualification_ledger_path=ledger,
        output_root=output,
        deck_pool_path=pool_path,
        initial_role_map={"agents/test": "BENCHMARK_VISIBLE"},
    )

    catalog = load_json(output / "catalog_snapshot.json")
    public = next(
        entry
        for entry in catalog["entries"]
        if entry["asset_id"] == "deck-pool/deck-public"
    )
    assert report["deck_pool_entries"] == 1
    assert public["policy_kind"] == "rule_v0"
    assert public["role"] == "TRAINING_ACTIVE"
    assert public["deck_hash"] == public_deck_hash
    submitted = next(
        entry for entry in catalog["entries"] if entry["asset_id"] == "agents/test"
    )
    assert submitted["role"] == "BENCHMARK_VISIBLE"


def test_resolve_ref_asset_supports_agent_and_nested_dev_without_checkout(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "refs"
    (repo / "opponents" / "nested").mkdir(parents=True)
    source = "def agent(obs): return []\n"
    deck = "\n".join(str(index) for index in range(1, 61)) + "\n"
    (repo / "main.py").write_text(source, encoding="utf-8")
    (repo / "deck.csv").write_text(deck, encoding="utf-8")
    (repo / "opponents" / "nested" / "main.py").write_text(
        source, encoding="utf-8"
    )
    (repo / "opponents" / "nested" / "deck.csv").write_text(
        deck, encoding="utf-8"
    )
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.invalid"],
        cwd=repo,
        check=True,
    )
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", "fixture"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True
    ).strip()
    for ref in ("refs/remotes/origin/agents/test", "refs/remotes/origin/dev"):
        subprocess.run(["git", "update-ref", ref, head], cwd=repo, check=True)

    agent, _ = resolve_ref_asset(repo, ref="origin/agents/test")
    dev, _ = resolve_ref_asset(
        repo, ref="origin/dev", asset_id="dev/nested"
    )

    assert agent.asset_id == "agents/test"
    assert dev.asset_id == "dev/nested"
    assert agent.source_commit == dev.source_commit == head
    assert agent.policy_hash == dev.policy_hash


def test_qualify_ref_resolves_relative_output_before_worker_snapshot(
    tmp_path: Path, monkeypatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "deck.csv").write_text("1\n" * 60, encoding="utf-8")
    digest = "a" * 64
    asset = SimpleNamespace(
        asset_id="agents/test",
        ref="origin/agents/test",
        source_commit="b" * 40,
        source_lineage="test-lineage",
        exactness="EXACT",
        deck_id="deck.csv",
        deck_hash=digest,
        policy_id=digest,
        policy_hash=digest,
        adapter_hash=digest,
        runtime_config_hash=digest,
        entrypoint="main.py:agent",
    )
    entry = CatalogEntry(
        asset_id=asset.asset_id,
        policy_id=digest,
        deck_id=asset.deck_id,
        source_id=asset.source_lineage,
        policy_kind="submitted_snapshot",
        runtime_path="placeholder",
        deck_path=str(repo / "deck.csv"),
        policy_hash=digest,
        deck_hash=digest,
        source_hash=digest,
        runtime_config_hash=digest,
        role="TRAINING_RESERVE",
    )

    def fake_pin_snapshot(_repo: Path, _asset: object, destination: Path) -> dict[str, str]:
        assert destination.is_absolute()
        destination.mkdir(parents=True)
        deck_path = destination / "deck.csv"
        deck_path.write_text("1\n" * 60, encoding="utf-8")
        (destination / ".submitted_snapshot_manifest.json").write_text(
            "{}\n", encoding="utf-8"
        )
        return {"deck_path": str(deck_path)}

    class FakeExecutor:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def execute(self, _game: object, qualified_entry: CatalogEntry) -> tuple[dict[str, object], None]:
            assert Path(qualified_entry.runtime_path).is_absolute()
            return ({"outcome": "win", "duration_seconds": 0.0, "steps": 1, "winner": 0, "candidate_side": 0}, None)

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(qualification, "resolve_ref_asset", lambda *_args, **_kwargs: (asset, digest))
    monkeypatch.setattr(qualification, "pin_snapshot", fake_pin_snapshot)
    monkeypatch.setattr(
        qualification.CatalogEntry,
        "from_submitted_asset",
        lambda *_args, **_kwargs: entry,
    )
    monkeypatch.setattr(qualification, "CabtMatchExecutor", FakeExecutor)

    result = qualify_ref(
        repo=repo,
        ref=asset.ref,
        output_root=Path("qualification"),
        games=2,
    )

    assert result["status"] == "TRAINING_ELIGIBLE"
    assert (tmp_path / "qualification" / "submitted_asset_registry.csv").is_file()
