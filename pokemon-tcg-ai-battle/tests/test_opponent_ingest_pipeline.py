from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from mage_ptcg.opponent_ingest.pipeline import IngestError, audit_agent_text, discover_git_refs, normalize_deck_text, run_ingestion


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"; repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "agents").mkdir(); (repo / "data/raw").mkdir(parents=True)
    (repo / "agents/a.py").write_text("def agent(x): return []\n", encoding="utf-8")
    (repo / "main.py").write_text("def agent(x): return []\n", encoding="utf-8")
    (repo / "deck.csv").write_text(",".join(str(i) for i in range(1, 61)), encoding="utf-8")
    (repo / "data/raw/EN_Card_Data.csv").write_text("\n".join(f"{i},card" for i in range(1, 61)), encoding="utf-8")
    subprocess.run(["git", "add", "agents/a.py", "main.py", "deck.csv", "data/raw/EN_Card_Data.csv"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "fixture"], cwd=repo, check=True, capture_output=True)
    return repo


def test_deck_normalization_and_unresolved_identity():
    valid = normalize_deck_text(",".join(str(i) for i in range(1, 61)), source_id="s", path="d.csv", official_ids=set(range(1, 61)))
    assert valid["eligibility"] == "EXACT_60_VALID" and valid["deck_digest"]
    unresolved = normalize_deck_text(",".join(str(i) for i in range(1, 60)) + ",999", source_id="s", path="d.csv", official_ids=set(range(1, 61)))
    assert unresolved["eligibility"] == "CARD_ID_UNRESOLVED"


def test_suspicious_agent_is_quarantined():
    agent = audit_agent_text("import requests\nos.system('x')\n", source_id="s", path="bad.py")
    assert agent["activation_eligibility"] == "QUARANTINED"
    assert {"network", "subprocess"} <= set(agent["static_findings"])


def test_incremental_skip_and_no_checkout(tmp_path: Path):
    repo = _repo(tmp_path); artifact = tmp_path / "artifact"; head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    first = run_ingestion(repo, artifact, {"sampling": {"rule_v0_max_fraction": .2}}, mode="incremental")
    second = run_ingestion(repo, artifact, {"sampling": {"rule_v0_max_fraction": .2}}, mode="incremental")
    assert first["changed_source_count"] > 0 and second["changed_source_count"] == 0
    assert second["discovered_agent_count"] == first["discovered_agent_count"]
    assert subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip() == head
    assert json.loads((artifact / "artifacts/candidate_population.json").read_text())["activation_policy"] == "CANDIDATE_ONLY_NO_AUTOPROMOTION"
    agents = [
        json.loads(line)
        for line in (artifact / "artifacts/agent_asset_registry.jsonl").read_text().splitlines()
    ]
    assert any(agent["path"] == "main.py" for agent in agents)


def test_stale_lock_recovered_but_live_lock_fails(tmp_path: Path):
    repo = _repo(tmp_path); artifact = tmp_path / "artifact"; lock = artifact / "state/ingestion.lock"; lock.parent.mkdir(parents=True); lock.write_text(str(os.getpid()))
    with pytest.raises(IngestError): run_ingestion(repo, artifact, {"stale_lock_seconds": 3600})
    lock.write_text("999999", encoding="ascii")
    result = run_ingestion(repo, artifact, {"stale_lock_seconds": 3600})
    assert result["source_count"] > 0
