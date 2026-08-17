"""Contracts for the hash-anchored V4 shadow evaluator."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


def _load_runner():
    script = Path(__file__).resolve().parents[2] / "scripts" / "measure_v4_checkpoint_strength_shadow.py"
    spec = importlib.util.spec_from_file_location("measure_v4_checkpoint_strength_shadow", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_manifest(tmp_path: Path) -> tuple[Path, str, dict[str, object]]:
    deck = tmp_path / "deck.csv"
    policy = tmp_path / "main.py"
    source = tmp_path / "SOURCE.md"
    deck.write_text("1\n" * 60, encoding="utf-8")
    policy.write_text("def agent(obs): return []\n", encoding="utf-8")
    source.write_text("local only\n", encoding="utf-8")
    candidate = {
        "id": "shadow-a",
        "deck_path": str(deck),
        "policy_path": str(policy),
        "source_metadata_path": str(source),
        "deck_file_sha256": hashlib.sha256(deck.read_bytes()).hexdigest(),
        "policy_sha256": hashlib.sha256(policy.read_bytes()).hexdigest(),
        "source_metadata_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "canonical_deck_sha256": hashlib.sha256(b"canonical-deck").hexdigest(),
    }
    payload = {"schema_version": "meta-specialist-v4-shadow-pool-v1", "candidates": [candidate]}
    path = tmp_path / "shadow.json"
    raw = json.dumps(payload, sort_keys=True).encode("utf-8")
    path.write_bytes(raw)
    return path, hashlib.sha256(raw).hexdigest(), candidate


def test_shadow_manifest_is_hash_and_asset_bound(tmp_path: Path) -> None:
    runner = _load_runner()
    path, manifest_sha, candidate = _write_manifest(tmp_path)
    resolved = runner._load_shadow_pool_manifest(path, manifest_sha)
    assert resolved[0]["opponent_id"] == "shadow-a"
    assert resolved[0]["deck_path"] == Path(candidate["deck_path"]).resolve()


def test_shadow_manifest_rejects_tampered_asset(tmp_path: Path) -> None:
    runner = _load_runner()
    path, manifest_sha, _candidate = _write_manifest(tmp_path)
    (tmp_path / "main.py").write_text("def agent(obs): return [1]\n", encoding="utf-8")
    with pytest.raises(ValueError, match="asset hash mismatch"):
        runner._load_shadow_pool_manifest(path, manifest_sha)


def test_shadow_manifest_accepts_frozen_v2_schema(tmp_path: Path) -> None:
    runner = _load_runner()
    path, manifest_sha, candidate = _write_manifest(tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["schema_version"] = "meta-specialist-v4-shadow-pool-v2"
    raw = json.dumps(payload, sort_keys=True).encode("utf-8")
    path.write_bytes(raw)
    resolved = runner._load_shadow_pool_manifest(path, hashlib.sha256(raw).hexdigest())
    assert resolved[0]["opponent_id"] == "shadow-a"
    assert resolved[0]["policy_path"] == Path(candidate["policy_path"]).resolve()
