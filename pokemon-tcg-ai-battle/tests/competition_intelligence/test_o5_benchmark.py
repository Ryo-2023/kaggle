"""Focused tests for the versioned, immutable O5 Benchmark manifest envelope."""

from __future__ import annotations

import json

import pytest

from mage_ptcg.competition_intelligence.o5_benchmark import (
    O5BenchmarkError,
    O5_BENCHMARK_MANIFEST_SCHEMA_VERSION,
    build_versioned_benchmark_manifest,
)


def _kwargs(**overrides):
    base = dict(
        benchmark_id="o5-benchmark-core-v1",
        benchmark_version="1.0.0",
        benchmark_kind="performance",
        created_at="2026-07-21T00:00:00Z",
        source_snapshot_ids=("registry-snapshot-1",),
        deck_registry_version="o5-deck-archetype-registry-v1",
        policy_pack_version="o5-activation-opponent-factory-v1",
        agent_family_versions={"rule_v0": "1", "random_legal": "1"},
        ruleset_version="unknown",
        cabt_version="1.32.0",
        seed_set=(9000, 9001),
        seat_swap_policy="ALWAYS_SWAP",
        game_count=8,
        time_budget_seconds=600.0,
        candidate_artifact_id="rule_v0",
        candidate_artifact_hash="NOT_APPLICABLE",
        baseline_artifact_ids=("random_legal",),
        environment="local",
        commit="0" * 40,
        active_exact_decks=0,
        runnable_families=0,
        verified_links=0,
    )
    base.update(overrides)
    return base


def test_builds_expected_schema_and_carries_blocked_status():
    manifest = build_versioned_benchmark_manifest((), **_kwargs())
    assert manifest.schema_version == O5_BENCHMARK_MANIFEST_SCHEMA_VERSION
    assert manifest.status == "BENCHMARK_BLOCKED_INSUFFICIENT_ACTIVE_POPULATION"
    assert manifest.sets["current_meta"] == ()
    assert manifest.logical_pair_count == 4
    assert manifest.manifest_hash and manifest.config_hash


def test_manifest_hash_is_deterministic_and_content_addressed():
    a = build_versioned_benchmark_manifest((), **_kwargs())
    b = build_versioned_benchmark_manifest((), **_kwargs())
    assert a.manifest_hash == b.manifest_hash
    c = build_versioned_benchmark_manifest((), **_kwargs(game_count=16))
    assert c.manifest_hash != a.manifest_hash
    assert c.config_hash != a.config_hash


def test_candidate_artifact_hash_binds_the_manifest_hash():
    # A manifest naming candidate_artifact_id="neural_actual_trained" must
    # not collide with a manifest for a *different* checkpoint that
    # happens to share the same human-readable id -- the model hash itself
    # has to participate in the content address.
    a = build_versioned_benchmark_manifest((), **_kwargs(candidate_artifact_id="neural_actual_trained", candidate_artifact_hash="hash-a"))
    b = build_versioned_benchmark_manifest((), **_kwargs(candidate_artifact_id="neural_actual_trained", candidate_artifact_hash="hash-b"))
    assert a.manifest_hash != b.manifest_hash
    assert a.config_hash != b.config_hash


def test_rejects_blank_candidate_artifact_hash():
    with pytest.raises(O5BenchmarkError):
        build_versioned_benchmark_manifest((), **_kwargs(candidate_artifact_hash=""))


def test_performance_kind_excludes_safety_and_adversarial_sets():
    manifest = build_versioned_benchmark_manifest((), **_kwargs(benchmark_kind="performance"))
    assert manifest.sets["core_regression"] != ()
    assert manifest.sets["safety"] == ()
    assert manifest.sets["adversarial"] == ()


def test_safety_kind_excludes_performance_sets():
    manifest = build_versioned_benchmark_manifest((), **_kwargs(benchmark_kind="safety"))
    assert manifest.sets["core_regression"] == ()
    assert manifest.sets["current_meta"] == ()
    assert manifest.sets["safety"] != ()


def test_performance_and_safety_manifests_never_share_a_hash_for_identical_other_inputs():
    performance = build_versioned_benchmark_manifest((), **_kwargs(benchmark_kind="performance"))
    safety = build_versioned_benchmark_manifest((), **_kwargs(benchmark_kind="safety"))
    assert performance.manifest_hash != safety.manifest_hash
    assert performance.sets != safety.sets


def test_rejects_unknown_benchmark_kind():
    with pytest.raises(O5BenchmarkError, match="benchmark_kind"):
        build_versioned_benchmark_manifest((), **_kwargs(benchmark_kind="both"))


def test_rejects_odd_game_count_when_seat_swap_is_always_swap():
    with pytest.raises(O5BenchmarkError, match="even"):
        build_versioned_benchmark_manifest((), **_kwargs(game_count=7))


def test_rejects_empty_seed_set():
    with pytest.raises(O5BenchmarkError, match="seed_set"):
        build_versioned_benchmark_manifest((), **_kwargs(seed_set=()))


def test_rejects_duplicate_seeds():
    # Independent-audit regression: a duplicate seed made the Evaluation
    # Runner replay the same completed games twice and double-count them.
    with pytest.raises(O5BenchmarkError, match="duplicate"):
        build_versioned_benchmark_manifest((), **_kwargs(seed_set=(9000, 9000, 9001)))


def test_rejects_blank_benchmark_id_or_version():
    with pytest.raises(O5BenchmarkError):
        build_versioned_benchmark_manifest((), **_kwargs(benchmark_id=""))
    with pytest.raises(O5BenchmarkError):
        build_versioned_benchmark_manifest((), **_kwargs(benchmark_version=""))


def test_as_dict_round_trips_through_json(tmp_path):
    manifest = build_versioned_benchmark_manifest((), **_kwargs())
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest.as_dict(), sort_keys=True), encoding="utf-8")
    reloaded = json.loads(path.read_text(encoding="utf-8"))
    assert reloaded["manifest_hash"] == manifest.manifest_hash
