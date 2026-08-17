from __future__ import annotations

import json
from pathlib import Path

import pytest

from mage_ptcg.bootstrap_champion.contracts import (
    BootstrapChampionManifest,
    BootstrapCheckpointManifest,
    BootstrapContractError,
    DeckAsset,
    DeckCompatibility,
    InitializationMode,
    JointCandidate,
    PolicyAsset,
    write_manifest,
)
from mage_ptcg.observability.cabt_trace import canonical_deck_sha256


def _sha(character: str) -> str:
    return character * 64


def _deck(path: Path, character: str = "a") -> DeckAsset:
    cards = [1] * 60
    path.write_text("\n".join(map(str, cards)) + "\n", encoding="utf-8")
    return DeckAsset(
        deck_id="deck-a",
        deck_hash=canonical_deck_sha256(cards),
        snapshot_path=str(path),
        source_id="local-test",
        source_hash=_sha("b"),
    )


def _policy(*, exact_deck_hash: str | None = None) -> PolicyAsset:
    exact = exact_deck_hash is not None
    return PolicyAsset(
        policy_id="policy-a",
        policy_hash=_sha("c"),
        policy_kind="rule_v0",
        runtime_path="builtin:rule_v0",
        adapter_hash=_sha("d"),
        runtime_config_hash=_sha("e"),
        compatibility=(
            DeckCompatibility.EXACT_DECK
            if exact
            else DeckCompatibility.ARBITRARY_LEGAL_DECK
        ),
        exact_deck_hash=exact_deck_hash,
        source_id="local-test",
        source_hash=_sha("f"),
    )


def test_deck_asset_rejects_snapshot_that_is_not_sixty_cards(tmp_path: Path) -> None:
    path = tmp_path / "short.csv"
    path.write_text("\n".join(["1"] * 59) + "\n", encoding="utf-8")

    with pytest.raises(BootstrapContractError, match="60"):
        DeckAsset(
            deck_id="short",
            deck_hash=_sha("a"),
            snapshot_path=str(path),
            source_id="test",
            source_hash=_sha("b"),
        )


def test_deck_asset_rejects_hash_that_does_not_match_snapshot(tmp_path: Path) -> None:
    path = tmp_path / "deck.csv"
    path.write_text("\n".join(["1"] * 60) + "\n", encoding="utf-8")

    with pytest.raises(BootstrapContractError, match="hash"):
        DeckAsset(
            deck_id="wrong-hash",
            deck_hash=_sha("a"),
            snapshot_path=str(path),
            source_id="test",
            source_hash=_sha("b"),
        )


def test_policy_rejects_invalid_deck_compatibility_contract() -> None:
    with pytest.raises(BootstrapContractError, match="exact_deck_hash"):
        PolicyAsset(
            policy_id="exact",
            policy_hash=_sha("c"),
            policy_kind="rule_v0",
            runtime_path="builtin:rule_v0",
            adapter_hash=_sha("d"),
            runtime_config_hash=_sha("e"),
            compatibility=DeckCompatibility.EXACT_DECK,
            exact_deck_hash=None,
            source_id="test",
            source_hash=_sha("f"),
        )
    with pytest.raises(BootstrapContractError, match="must not"):
        PolicyAsset(
            policy_id="generic",
            policy_hash=_sha("c"),
            policy_kind="rule_v0",
            runtime_path="builtin:rule_v0",
            adapter_hash=_sha("d"),
            runtime_config_hash=_sha("e"),
            compatibility=DeckCompatibility.ARBITRARY_LEGAL_DECK,
            exact_deck_hash=_sha("a"),
            source_id="test",
            source_hash=_sha("f"),
        )


def test_joint_candidate_identity_does_not_depend_on_paths(tmp_path: Path) -> None:
    first = _deck(tmp_path / "first.csv")
    second_path = tmp_path / "second.csv"
    second_path.write_text("\n".join(["1"] * 60) + "\n", encoding="utf-8")
    second = DeckAsset(
        deck_id=first.deck_id,
        deck_hash=first.deck_hash,
        snapshot_path=str(second_path),
        source_id=first.source_id,
        source_hash=first.source_hash,
    )

    exact_policy = _policy(exact_deck_hash=first.deck_hash)
    assert JointCandidate(first, exact_policy, _sha("9")).candidate_id == JointCandidate(
        second, exact_policy, _sha("9")
    ).candidate_id


def test_champion_manifest_detects_tampering_and_refuses_output_replacement(
    tmp_path: Path,
) -> None:
    candidate = JointCandidate(_deck(tmp_path / "deck.csv"), _policy(), _sha("9"))
    manifest = BootstrapChampionManifest.build(
        candidate_registry_id=_sha("1"),
        screen_benchmark_id=_sha("2"),
        validation_benchmark_id=_sha("3"),
        candidate=candidate,
        initialization_mode=InitializationMode.TEACHER_DISTILLATION,
        score_summary={"opponent_equal_score_rate": 0.5, "fault_count": 0},
    )
    restored = BootstrapChampionManifest.from_dict(manifest.to_dict())
    assert restored.bootstrap_champion_id == manifest.bootstrap_champion_id

    tampered = manifest.to_dict()
    tampered["candidate_id"] = _sha("0")
    with pytest.raises(BootstrapContractError, match="identity mismatch"):
        BootstrapChampionManifest.from_dict(tampered)

    output = tmp_path / "champion.json"
    write_manifest(output, manifest.to_dict())
    write_manifest(output, manifest.to_dict())
    with pytest.raises(BootstrapContractError, match="different content"):
        write_manifest(output, {**manifest.to_dict(), "score_summary": {"x": 1}})
    assert json.loads(output.read_text(encoding="utf-8"))["bootstrap_champion_id"] == manifest.bootstrap_champion_id


def test_bootstrap_checkpoint_manifest_requires_one_initialization_provenance() -> None:
    manifest = BootstrapCheckpointManifest.build(
        bootstrap_champion_id=_sha("1"),
        initialization_mode=InitializationMode.DIRECT_CHECKPOINT,
        model_config_hash=_sha("2"),
        action_schema_hash=_sha("3"),
        deck_hash=_sha("4"),
        online_weights_sha256=_sha("5"),
        source_checkpoint_id=_sha("6"),
    )

    assert BootstrapCheckpointManifest.from_dict(manifest.to_dict()) == manifest
    with pytest.raises(BootstrapContractError, match="teacher_dataset_id"):
        BootstrapCheckpointManifest.build(
            bootstrap_champion_id=_sha("1"),
            initialization_mode=InitializationMode.TEACHER_DISTILLATION,
            model_config_hash=_sha("2"),
            action_schema_hash=_sha("3"),
            deck_hash=_sha("4"),
            online_weights_sha256=_sha("5"),
        )
