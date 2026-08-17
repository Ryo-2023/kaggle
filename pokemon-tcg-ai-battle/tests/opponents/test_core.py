import json

import pytest

from mage_ptcg.opponents import LocalArtifactStore, OpponentError, OpponentRegistry
from mage_ptcg.opponents.core import (NativeAgentAdapter, RegistryStateMachine, build_population, collect_public_inbox,
                                      deck_record, load_team_permission_policy, resolve_team_permission, safe_extract_tar_gz)


def _record(*, commit_sha: str = "a" * 40, snapshot_id: str = "source-1", agent_id: str = "agent-1") -> dict:
    snapshot = {"source_snapshot_id": snapshot_id, "source_locator": "origin/agents/example", "commit_sha": commit_sha}
    deck = deck_record(list(range(1, 61)), source_lineage=[snapshot_id])
    agent = {"agent_id": agent_id, "entrypoint": "agent.py:agent"}
    strategy = {"strategy_evidence_id": "strategy-1"}
    validation = {"technical_validation_decision": "PASS", "determinism": "DETERMINISTIC", "legal_action_validation": "CABT_SMOKE_PASS",
                  "state_leakage": "PROCESS_ISOLATED", "permission_decision": "APPROVED", "matched_policy_id": "p", "policy_hash": "h",
                  "allowed_scopes": ["evaluation"], "prohibited_scopes": [], "activation_decision": "VALIDATED"}
    return {"snapshot": snapshot, "deck": deck, "agent": agent, "strategy": strategy, "state": "VALIDATED", "validation": validation}


def test_deck_identity_ignores_card_order_and_partial_is_not_exact():
    first = deck_record(list(range(1, 61)), source_lineage=["source"])
    second = deck_record(list(reversed(range(1, 61))), source_lineage=["source"])
    partial = deck_record([1, 2], source_lineage=["source"])
    assert first["deck_hash"] == second["deck_hash"]
    assert partial["exact_or_partial"] == "PARTIAL"
    assert partial["deck_hash"] is None


def test_population_id_is_content_derived_and_order_independent():
    first, _ = build_population([_record()], permission_policy_hash="policy-h")
    second, _ = build_population([_record()], permission_policy_hash="policy-h", display_name="a-different-human-label")
    assert first["manifest_hash"] == second["manifest_hash"]
    assert first["population_id"] == second["population_id"]
    assert first["population_id"] == f"team-agents-v1-{first['population_identity_hash'][:16]}"

    two_members_a = [_record(agent_id="a1", snapshot_id="s1"), _record(agent_id="a2", snapshot_id="s2", commit_sha="b" * 40)]
    two_members_b = list(reversed(two_members_a))
    reordered, _ = build_population(two_members_b, permission_policy_hash="policy-h")
    original, _ = build_population(two_members_a, permission_policy_hash="policy-h")
    assert reordered["population_id"] == original["population_id"]

    changed_commit, _ = build_population([_record(commit_sha="c" * 40)], permission_policy_hash="policy-h")
    assert changed_commit["population_id"] != first["population_id"]

    changed_policy, _ = build_population([_record()], permission_policy_hash="different-policy-h")
    assert changed_policy["population_id"] != first["population_id"]

    changed_adapter, _ = build_population([_record()], permission_policy_hash="policy-h", adapter_version="o6-native-subprocess-v2")
    assert changed_adapter["population_id"] != first["population_id"]


def test_population_id_is_sensitive_to_runtime_bundle_content():
    """O6-AUD-001 remediation: population_identity_hash must depend on the
    runtime bundle's own bytes, not only on abstract registry hashes --
    otherwise swapping the old 'copy everything' bundle for the minimized
    allow-list closure would silently keep the old population_id, and
    LocalArtifactStore.publish()'s idempotent-republish path would then
    never write the new (smaller) bundle at all."""
    no_bundle, _ = build_population([_record()], permission_policy_hash="policy-h")
    with_bundle_a, _ = build_population([_record()], permission_policy_hash="policy-h", runtime_files={"runtime/agent-1/source/main.py": b"print(1)"})
    with_bundle_b, _ = build_population([_record()], permission_policy_hash="policy-h", runtime_files={"runtime/agent-1/source/main.py": b"print(2)"})
    assert no_bundle["population_id"] != with_bundle_a["population_id"]
    assert with_bundle_a["population_id"] != with_bundle_b["population_id"]
    reordered_same_content, _ = build_population([_record()], permission_policy_hash="policy-h", runtime_files={"runtime/agent-1/source/main.py": b"print(1)"})
    assert with_bundle_a["population_id"] == reordered_same_content["population_id"]


def test_build_population_has_no_caller_id_parameter():
    with pytest.raises(TypeError):
        build_population([_record()], population_id="caller-chosen", permission_policy_hash="policy-h")  # type: ignore[call-arg]


def test_store_refuses_unapproved_and_detects_manifest_corruption(tmp_path):
    manifest, payload = build_population([_record()], permission_policy_hash="policy-h")
    store = LocalArtifactStore(tmp_path)
    with pytest.raises(OpponentError, match="explicit APPROVED"):
        store.publish(manifest, payload)
    manifest = dict(manifest); manifest["approval_status"] = "APPROVED"
    semantic = {key: value for key, value in manifest.items() if key not in {"created_at", "manifest_hash", "display_name"}}
    from mage_ptcg.competition_intelligence.canonical import digest
    manifest["manifest_hash"] = digest(semantic, domain="o6-population")
    population_id = manifest["population_id"]
    path = store.publish(manifest, payload, approved=True)
    assert store.fetch(population_id) == path
    assert store.publish(manifest, payload, approved=True) == path  # idempotent republish
    data = json.loads((path / "population_manifest.json").read_text())
    data["population_id"] = "poisoned"
    (path / "population_manifest.json").write_text(json.dumps(data))
    with pytest.raises(OpponentError, match="corrupt population manifest"):
        store.fetch(population_id)


def test_store_rejects_same_id_different_content(tmp_path):
    manifest, payload = build_population([_record()], permission_policy_hash="policy-h")
    manifest = dict(manifest); manifest["approval_status"] = "APPROVED"
    semantic = {key: value for key, value in manifest.items() if key not in {"created_at", "manifest_hash", "display_name"}}
    from mage_ptcg.competition_intelligence.canonical import digest
    manifest["manifest_hash"] = digest(semantic, domain="o6-population")
    store = LocalArtifactStore(tmp_path)
    store.publish(manifest, payload, approved=True)
    forged = dict(manifest); forged["manifest_hash"] = "0" * 64  # same population_id, tampered hash
    with pytest.raises(OpponentError):
        store.publish(forged, payload, approved=True)


def test_store_fetch_to_cache_is_isolated_and_verified(tmp_path):
    manifest, payload = build_population([_record()], permission_policy_hash="policy-h")
    manifest = dict(manifest); manifest["approval_status"] = "APPROVED"
    from mage_ptcg.competition_intelligence.canonical import digest
    semantic = {key: value for key, value in manifest.items() if key not in {"created_at", "manifest_hash", "display_name"}}
    manifest["manifest_hash"] = digest(semantic, domain="o6-population")
    store_root, cache_root = tmp_path / "store", tmp_path / "cache"
    store = LocalArtifactStore(store_root)
    store.publish(manifest, payload, approved=True, runtime_files={"runtime/agent-1/hashes.json": b"{}"})
    cached = store.fetch_to_cache(manifest["population_id"], cache_root)
    assert cached != store._path(manifest["population_id"])
    assert (cached / "runtime" / "agent-1" / "hashes.json").exists()
    # corrupting only the cache copy must not be silently trusted on a later verified read
    (cached / "population_manifest.json").write_text("{}")
    with pytest.raises(OpponentError):
        LocalArtifactStore(cache_root).fetch(manifest["population_id"], verify_hashes=True)
    # the durable store copy is untouched
    assert store.fetch(manifest["population_id"], verify_hashes=True) == store._path(manifest["population_id"])


def test_safe_extract_rejects_traversal_and_symlinks(tmp_path):
    import tarfile
    archive = tmp_path / "evil.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        import io
        info = tarfile.TarInfo(name="../escape.txt")
        data = b"x"
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    with pytest.raises(OpponentError):
        safe_extract_tar_gz(archive, tmp_path / "out1")

    with tarfile.open(archive, "w:gz") as tar:
        link = tarfile.TarInfo(name="link")
        link.type = tarfile.SYMTYPE
        link.linkname = "/etc/passwd"
        tar.addfile(link)
    with pytest.raises(OpponentError):
        safe_extract_tar_gz(archive, tmp_path / "out2")


def test_unreviewed_opponent_cannot_be_executed():
    record = _record(); record["state"] = "BLOCKED_PERMISSION"
    with pytest.raises(OpponentError, match="without VALIDATED"):
        build_population([record], permission_policy_hash="policy-h")
    manifest, payload = build_population([_record()], permission_policy_hash="policy-h")
    payload["opponent_specs.json"][0]["permission_status"] = "BLOCKED_PERMISSION"
    registry = OpponentRegistry(manifest, payload["opponent_specs.json"])
    with pytest.raises(OpponentError, match="not approved"):
        registry.build(registry.list()[0]["opponent_id"], seed=1)


def test_registry_cannot_auto_approve():
    machine = RegistryStateMachine()
    with pytest.raises(OpponentError, match="explicit review"):
        machine.transition("VALIDATED", "APPROVED")
    assert machine.transition("VALIDATED", "APPROVED", explicit_review=True) == "APPROVED"


def test_adapter_requires_approval_before_importing_snapshot(tmp_path):
    (tmp_path / "agent.py").write_text("def agent(observation):\n    return [0]\n")
    adapter = NativeAgentAdapter()
    with pytest.raises(OpponentError, match="explicit reviewed approval"):
        adapter.invoke(tmp_path, "agent.py:agent", {"select": {}})
    assert adapter.invoke(tmp_path, "agent.py:agent", {"select": {}}, approved=True)["status"] == "OK"


def test_namespace_permission_is_commit_pinned_and_denial_overrides(tmp_path):
    policy_path = tmp_path / "permission.yaml"
    policy_path.write_text(
        """schema_version: team-source-policy-v1
policy_id: tested
status: approved
source_match: {repository_name: repo, remote: origin, branch_globs: [agents/*]}
allowed: {evaluation: true, training_data_generation: true, strategy_analysis: true, team_redistribution: true}
prohibited: {public_redistribution: true, submission_bundle: true}
item_overrides:
  agents/denied: {decision: deny}
""", encoding="utf-8")
    policy = load_team_permission_policy(policy_path)
    allowed = resolve_team_permission(policy, repository_name="repo", remote="origin", branch="origin/agents/future", commit_sha="a" * 40)
    denied = resolve_team_permission(policy, repository_name="repo", remote="origin", branch="origin/agents/denied", commit_sha="b" * 40)
    unmatched = resolve_team_permission(policy, repository_name="repo", remote="origin", branch="origin/other", commit_sha="c" * 40)
    assert allowed["permission_decision"] == "APPROVED"
    assert "training_data_generation" in allowed["allowed_scopes"]
    assert "submission_bundle" not in allowed["allowed_scopes"]
    assert allowed["pinned_commit"] == "a" * 40 and allowed["revalidation_required"] is True
    assert denied["permission_decision"] == "DENIED"
    assert unmatched["permission_decision"] == "DENIED"


def test_public_inbox_classifies_four_fidelity_tiers_and_rejects_unsupported(tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    fixtures = {
        "exact_deck.json": {"classification": "EXACT", "kind": "public_git_repository", "url": "https://example.invalid/repo", "retrieved_at": "2026-07-21T00:00:00Z"},
        "deck_faithful.json": {"classification": "DECK_FAITHFUL", "kind": "reconstructed_deck", "note": "deck list reconstructed from a notebook, not the original commit"},
        "behavioral_surrogate.json": {"classification": "BEHAVIORAL_SURROGATE", "kind": "strategy_document"},
        "observed_only.json": {"classification": "OBSERVED_ONLY", "kind": "leaderboard_snapshot", "score_claim": 950, "rank_claim": None},
    }
    for name, content in fixtures.items():
        (inbox / name).write_text(json.dumps(content), encoding="utf-8")
    records = collect_public_inbox(inbox)
    assert len(records) == len(fixtures)
    classifications = {r["source"]: r["classification"] for r in records}
    assert classifications == {name: content["classification"] for name, content in fixtures.items()}
    assert all(r["content_hash"] for r in records)

    (inbox / "bad.json").write_text(json.dumps({"classification": "RANK_1_VERIFIED"}), encoding="utf-8")
    with pytest.raises(OpponentError, match="unsupported public evidence classification"):
        collect_public_inbox(inbox)

    assert collect_public_inbox(tmp_path / "does-not-exist") == []


def test_permission_loader_rejects_unknown_schema_and_malformed_approval(tmp_path):
    future = tmp_path / "future.yaml"
    future.write_text("schema_version: team-source-policy-v2\n", encoding="utf-8")
    with pytest.raises(OpponentError, match="unsupported"):
        load_team_permission_policy(future)
    malformed = tmp_path / "malformed.yaml"
    malformed.write_text("schema_version: team-source-policy-v1\npolicy_id: p\nstatus: approved\nsource_match: {}\n", encoding="utf-8")
    with pytest.raises(OpponentError, match="repository_name"):
        load_team_permission_policy(malformed)
