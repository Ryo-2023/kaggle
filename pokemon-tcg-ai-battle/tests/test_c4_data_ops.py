"""C4 data-ops contracts: actor-visible capture, private binding, split, privacy."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from mage_ptcg.dataops import (
    ActualEpisodeLineageInput,
    DataOpsError,
    LineageValidationError,
    build_decision_artifacts,
    collect_actual_dataset,
    scan_public_artifact,
    split_by_episode_group,
    validate_run,
)
from mage_ptcg.dataops.collector import _validate_episode_lineage_inputs
from scripts.accept_c4_actual_training_bundle import (
    BundleAcceptanceError,
    _validate_private_binding,
    accept_bundle,
    training_commands,
)
from scripts.export_c4_actual_training_bundle import BundleExportError, export_bundle
from mage_ptcg.dataops import collector as collector_module
from mage_ptcg.observability.cabt_trace import canonical_deck_sha256
from mage_ptcg.student import dataset as dataset_module
from mage_ptcg.student.dataset import RuleBCExample, load_dataset
from mage_ptcg.student.features import STATE_FEATURE_DIM
from main import read_deck_csv

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DECK_PATH = REPOSITORY_ROOT / "deck.csv"
_READY = {"status": "READY", "engine_seed_supported": False, "actual_execution_allowed": True}
_OPTIONS = [{"type": 14}, {"type": 13, "attackId": 1}, {"type": 7, "index": 0}]


# --------------------------------------------------------------------------- #
# Synthetic actor-visible observations (mirrors tests/test_student_v0.py)
# --------------------------------------------------------------------------- #


def _card(card_id: int) -> dict[str, object]:
    return {"id": card_id, "serial": 0, "playerIndex": 0, "hp": 100, "maxHp": 100, "appearThisTurn": False, "energies": [], "energyCards": [], "tools": [], "preEvolution": []}


def _player(card_id: int) -> dict[str, object]:
    return {"active": [], "asleep": False, "bench": [], "benchMax": 5, "burned": False, "confused": False, "deckCount": 53, "discard": [], "hand": [_card(card_id)], "handCount": 1, "paralyzed": False, "poisoned": False, "prize": [object() for _ in range(6)]}


def _observation(options: list[object], *, your_index: int = 0, minimum: int = 1, maximum: int = 1, select_type: int = 0, context: int = 0) -> dict[str, object]:
    return {
        "current": {"energyAttached": False, "firstPlayer": 0, "players": [_player(100), _player(700)], "result": -1, "retreated": False, "stadium": [], "stadiumPlayed": False, "supporterPlayed": False, "turn": 2, "turnActionCount": 3, "yourIndex": your_index},
        "select": {"context": context, "maxCount": maximum, "minCount": minimum, "option": options, "type": select_type},
        "step": 7,
    }


def _fake_runner(*, decisions_per_seat: int = 2, options: list[object] | None = None, seeds: list[int] | None = None):
    picked = options if options is not None else _OPTIONS

    def runner(**kwargs):
        if seeds is not None:
            seeds.append(int(kwargs["seed"]))
        seat0 = kwargs["agent_a_factory"]([1] * 60, int(kwargs["seed"]))
        seat1 = kwargs["agent_b_factory"]([1] * 60, int(kwargs["seed"]) + 1)
        for _ in range(decisions_per_seat):
            seat0(_observation(picked, your_index=0))
            seat1(_observation(picked, your_index=1))
        seat0({"registration": "deck"})  # non-decision prompt, must be skipped
        return {"status": "DONE", "winner": 0, "elapsed_seconds": 0.1}

    return runner


def _collect(tmp_path: Path, *, run_id: str = "run", games: int = 3, base_seed: int = 100, runner=None, **overrides) -> dict[str, object]:
    return collect_actual_dataset(
        run_id=run_id,
        games=games,
        base_seed=base_seed,
        output_root=tmp_path,
        canonical_base_sha="a" * 40,
        deck_path=DECK_PATH,
        repository_root=REPOSITORY_ROOT,
        match_runner=runner if runner is not None else _fake_runner(),
        capability_report=_READY,
        source_revision="test-revision",
        **overrides,
    )


def _stub_opponent_factory(deck, seed):
    def agent(observation: dict) -> list[int]:
        select = observation.get("select") if isinstance(observation, dict) else None
        if not isinstance(select, dict):
            return []
        options = select.get("option") or []
        return [0] if options else []

    return agent


def _o2_lineage_entries(match_ids: list[str]) -> list[ActualEpisodeLineageInput]:
    entries = []
    for index, match_id in enumerate(match_ids):
        seat = index % 2
        entries.append(
            ActualEpisodeLineageInput(
                match_id=match_id,
                plan_hash=f"planhash-{index // 2}",
                match_spec_hash=f"spechash-{index}",
                backend_kind="cabt",
                requested_seed=9300 + index,
                engine_seed_supported=False,
                seat_index=seat,
                player_side="A" if seat == 0 else "B",
                own_agent_id="rule-agent-v0",
                opponent_agent_id="random-legal-v0",
                own_implementation_hash="f" * 64,
                opponent_implementation_hash="a" * 64,
                own_deck_hash=canonical_deck_sha256(read_deck_csv(DECK_PATH)),
                opponent_deck_hash=canonical_deck_sha256(read_deck_csv(DECK_PATH)),
                pair_id=f"pair-{index // 2}",
            )
        )
    return entries


def _collect_o2(tmp_path: Path, *, match_ids: list[str], run_id: str = "run", base_seed: int = 9300, runner=None) -> dict[str, object]:
    entries = _o2_lineage_entries(match_ids)
    return collect_actual_dataset(
        run_id=run_id,
        games=len(entries),
        base_seed=base_seed,
        output_root=tmp_path,
        canonical_base_sha="a" * 40,
        deck_path=DECK_PATH,
        repository_root=REPOSITORY_ROOT,
        match_runner=runner if runner is not None else _fake_runner(),
        capability_report=_READY,
        source_revision="test-revision",
        episode_lineage_inputs=entries,
        opponent_deck_path=DECK_PATH,
        opponent_agent_factory=_stub_opponent_factory,
    )


# --------------------------------------------------------------------------- #
# O2 lineage input validator (pure, no execution)
# --------------------------------------------------------------------------- #


def _lineage(**overrides: object) -> ActualEpisodeLineageInput:
    base: dict[str, object] = dict(
        match_id="match_abc123", plan_hash="planhash1", match_spec_hash="specHash1",
        backend_kind="cabt", requested_seed=1000, engine_seed_supported=False,
        seat_index=0, player_side="A", own_agent_id="rule-agent-v0",
        opponent_agent_id="random-legal-v0",
        own_implementation_hash="f" * 64, opponent_implementation_hash="a" * 64,
        own_deck_hash="OWNDECKHASH", opponent_deck_hash="OPPDECKHASH", pair_id="pair1",
    )
    base.update(overrides)
    return ActualEpisodeLineageInput(**base)


def test_lineage_validator_accepts_a_consistent_batch() -> None:
    entries = [
        _lineage(match_id="m1", seat_index=0, player_side="A"),
        _lineage(match_id="m2", seat_index=1, player_side="B", pair_id="pair2"),
    ]
    _validate_episode_lineage_inputs(entries, games=2, own_deck_fingerprint="OWNDECKHASH", opponent_deck_fingerprint="OPPDECKHASH")


def test_lineage_validator_rejects_count_mismatch() -> None:
    entries = [_lineage(match_id="m1")]
    with pytest.raises(LineageValidationError, match="lineage_count_mismatch"):
        _validate_episode_lineage_inputs(entries, games=2, own_deck_fingerprint="OWNDECKHASH", opponent_deck_fingerprint="OPPDECKHASH")


def test_lineage_validator_rejects_duplicate_match_id() -> None:
    entries = [_lineage(match_id="m1"), _lineage(match_id="m1", seat_index=1, player_side="B")]
    with pytest.raises(LineageValidationError, match="duplicate_match_id"):
        _validate_episode_lineage_inputs(entries, games=2, own_deck_fingerprint="OWNDECKHASH", opponent_deck_fingerprint="OPPDECKHASH")


def test_lineage_validator_rejects_missing_match_id() -> None:
    entries = [_lineage(match_id=""), _lineage(match_id="m2", seat_index=1, player_side="B")]
    with pytest.raises(LineageValidationError, match="missing_match_id"):
        _validate_episode_lineage_inputs(entries, games=2, own_deck_fingerprint="OWNDECKHASH", opponent_deck_fingerprint="OPPDECKHASH")


def test_lineage_validator_rejects_seat_mismatch() -> None:
    entries = [_lineage(match_id="m1", seat_index=0, player_side="B")]
    with pytest.raises(LineageValidationError, match="seat_mismatch"):
        _validate_episode_lineage_inputs(entries, games=1, own_deck_fingerprint="OWNDECKHASH", opponent_deck_fingerprint="OPPDECKHASH")


def test_lineage_validator_rejects_opponent_mismatch_across_batch() -> None:
    entries = [
        _lineage(match_id="m1", seat_index=0, player_side="A"),
        _lineage(match_id="m2", seat_index=1, player_side="B", opponent_agent_id="student-v0"),
    ]
    with pytest.raises(LineageValidationError, match="opponent_mismatch"):
        _validate_episode_lineage_inputs(entries, games=2, own_deck_fingerprint="OWNDECKHASH", opponent_deck_fingerprint="OPPDECKHASH")


def test_lineage_validator_rejects_own_agent_mismatch_across_batch() -> None:
    entries = [
        _lineage(match_id="m1", seat_index=0, player_side="A"),
        _lineage(match_id="m2", seat_index=1, player_side="B", own_agent_id="student-v0"),
    ]
    with pytest.raises(LineageValidationError, match="own_agent_mismatch"):
        _validate_episode_lineage_inputs(entries, games=2, own_deck_fingerprint="OWNDECKHASH", opponent_deck_fingerprint="OPPDECKHASH")


def test_lineage_validator_rejects_own_implementation_hash_mismatch() -> None:
    entries = [
        _lineage(match_id="m1", seat_index=0, player_side="A"),
        _lineage(match_id="m2", seat_index=1, player_side="B", own_implementation_hash="0" * 64),
    ]
    with pytest.raises(LineageValidationError, match="own_implementation_hash_mismatch"):
        _validate_episode_lineage_inputs(entries, games=2, own_deck_fingerprint="OWNDECKHASH", opponent_deck_fingerprint="OPPDECKHASH")


def test_lineage_validator_rejects_opponent_implementation_hash_mismatch() -> None:
    entries = [
        _lineage(match_id="m1", seat_index=0, player_side="A"),
        _lineage(match_id="m2", seat_index=1, player_side="B", opponent_implementation_hash="0" * 64),
    ]
    with pytest.raises(LineageValidationError, match="opponent_implementation_hash_mismatch"):
        _validate_episode_lineage_inputs(entries, games=2, own_deck_fingerprint="OWNDECKHASH", opponent_deck_fingerprint="OPPDECKHASH")


def test_lineage_validator_rejects_own_deck_hash_mismatch() -> None:
    entries = [_lineage(match_id="m1", seat_index=0, player_side="A")]
    with pytest.raises(LineageValidationError, match="own_deck_hash_mismatch"):
        _validate_episode_lineage_inputs(entries, games=1, own_deck_fingerprint="DIFFERENT", opponent_deck_fingerprint="OPPDECKHASH")


def test_lineage_validator_rejects_opponent_deck_hash_mismatch() -> None:
    entries = [_lineage(match_id="m1", seat_index=0, player_side="A")]
    with pytest.raises(LineageValidationError, match="opponent_deck_hash_mismatch"):
        _validate_episode_lineage_inputs(entries, games=1, own_deck_fingerprint="OWNDECKHASH", opponent_deck_fingerprint="DIFFERENT")


def test_lineage_validator_rejects_fixture_backend() -> None:
    entries = [_lineage(match_id="m1", seat_index=0, player_side="A", backend_kind="fixture_backend")]
    with pytest.raises(LineageValidationError, match="fixture_backend_rejected"):
        _validate_episode_lineage_inputs(entries, games=1, own_deck_fingerprint="OWNDECKHASH", opponent_deck_fingerprint="OPPDECKHASH")


# --------------------------------------------------------------------------- #
# Per-decision artifact construction
# --------------------------------------------------------------------------- #


def test_decision_artifacts_build_actor_visible_row_and_binding() -> None:
    artifacts = build_decision_artifacts(
        _observation(_OPTIONS, your_index=0),
        deck=[1] * 60,
        episode_group_id="run-g0",
        decision_index=0,
        seat=0,
        source_revision="test",
        trace_provenance_hash="deadbeef",
    )
    example = artifacts.example
    assert isinstance(example, RuleBCExample)
    assert example.schema_version == "rule-bc-v1"
    # Actor-visible only: own hand present, opponent identity absent.
    assert "hand_card_ids" in example.own_private_state
    assert "hand_card_ids" not in example.public_state["opponent"]
    assert "hand" not in example.public_state["opponent"]
    # Non-decision observation fields are never persisted.
    encoded = json.dumps(example.to_dict())
    assert "logs" not in encoded and "search_begin_input" not in encoded
    # Binding carries the private candidate-to-option mapping and the chosen index.
    binding = artifacts.binding
    assert binding["legal_candidate_count"] == len(example.legal_actions)
    assert binding["candidates"] and all("option_index" in c and "digest" in c for c in binding["candidates"])
    assert set(binding["chosen_action_digests"]) == set(example.target_action_digests)
    assert binding["teacher_source"] == "Rule Agent v0"


def _ordered_skill_observation() -> dict[str, object]:
    return _observation(
        [
            {"type": 15, "cardId": 101, "serial": 1001},
            {"type": 15, "cardId": 102, "serial": 1002},
        ],
        minimum=2,
        maximum=2,
        select_type=5,
        context=34,
    )


def _ordered_skill_artifacts(monkeypatch: pytest.MonkeyPatch):
    choose_reversed = lambda _observation: [1, 0]
    monkeypatch.setattr(collector_module, "choose_rule_indices", choose_reversed)
    monkeypatch.setattr(dataset_module, "choose_rule_indices", choose_reversed)
    return build_decision_artifacts(
        _ordered_skill_observation(),
        deck=[1] * 60,
        episode_group_id="ordered-skill",
        decision_index=0,
        seat=0,
        source_revision="test",
        trace_provenance_hash="deadbeef",
    )


def _unordered_pair_artifacts(monkeypatch: pytest.MonkeyPatch):
    choose_reversed = lambda _observation: [1, 0]
    monkeypatch.setattr(collector_module, "choose_rule_indices", choose_reversed)
    monkeypatch.setattr(dataset_module, "choose_rule_indices", choose_reversed)
    return build_decision_artifacts(
        _observation(
            [{"type": 1}, {"type": 2}],
            minimum=2,
            maximum=2,
            select_type=9,
            context=41,
        ),
        deck=[1] * 60,
        episode_group_id="unordered-pair",
        decision_index=0,
        seat=0,
        source_revision="test",
        trace_provenance_hash="deadbeef",
    )


def test_decision_artifacts_preserve_ordered_skill_choice_sequence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifacts = _ordered_skill_artifacts(monkeypatch)

    assert artifacts.binding["chosen_option_indices"] == [1, 0]
    assert artifacts.binding["chosen_action_digests"] == list(
        artifacts.example.target_action_digests
    )
    assert artifacts.binding["teacher_chosen_action_digests"] == list(
        artifacts.example.target_action_digests
    )


def test_decision_artifacts_reject_seat_mismatch() -> None:
    with pytest.raises(DataOpsError):
        build_decision_artifacts(
            _observation(_OPTIONS, your_index=1),  # actor 1
            deck=[1] * 60,
            episode_group_id="run-g0",
            decision_index=0,
            seat=0,  # collected as seat 0
            source_revision="test",
            trace_provenance_hash="deadbeef",
        )


# --------------------------------------------------------------------------- #
# Collection smoke: rows, bindings, chosen targets, separation
# --------------------------------------------------------------------------- #


def test_collection_smoke_produces_rows_bindings_and_chosen_targets(tmp_path: Path) -> None:
    summary = _collect(tmp_path, games=4)
    run_dir = tmp_path / "run"
    dataset_path = run_dir / "private_dataset" / "rule-bc-v1.jsonl"
    bindings_path = run_dir / "private_dataset" / "private_bindings.jsonl"

    assert summary["status"] == "PASS"
    assert summary["episode_count"] >= 2
    assert summary["decision_count"] > 0
    assert summary["candidate_count"] > 0
    assert summary["private_binding_count"] > 0
    assert summary["chosen_target_count"] == summary["decision_count"]
    assert summary["privacy_scan_executed"] is True
    assert summary["privacy_violations"] == 0

    examples = load_dataset(dataset_path)  # RuleBCExample compatibility
    binds = [json.loads(line) for line in bindings_path.read_text().splitlines() if line.strip()]
    assert len(examples) == len(binds) == summary["decision_count"]
    for example, bind in zip(examples, binds):
        assert set(bind["chosen_action_digests"]) == set(example.target_action_digests)


# --------------------------------------------------------------------------- #
# O2 lineage mode: execution, binding/manifest propagation, backward compat
# --------------------------------------------------------------------------- #


def test_legacy_collect_actual_dataset_is_unchanged_without_lineage_inputs(tmp_path: Path) -> None:
    summary = _collect(tmp_path, run_id="run", games=4)
    run_dir = tmp_path / "run"
    binds = [json.loads(line) for line in (run_dir / "private_dataset" / "private_bindings.jsonl").read_text().splitlines() if line.strip()]
    assert binds
    for bind in binds:
        assert bind["episode_group_id"].startswith("run-g")
        assert "o2_lineage" not in bind
    assert summary["o2_lineage_present"] is False
    assert summary["o2_plan_hashes"] == []
    assert summary["o2_match_ids"] == []
    manifest = json.loads((run_dir / "dataset_manifest.json").read_text())
    assert manifest["o2_lineage_present"] is False


def test_o2_lineage_mode_tags_bindings_and_uses_match_id_as_episode_group(tmp_path: Path) -> None:
    match_ids = ["match_aaa000", "match_bbb111", "match_ccc222", "match_ddd333"]
    summary = _collect_o2(tmp_path, match_ids=match_ids)
    run_dir = tmp_path / "run"
    binds = [json.loads(line) for line in (run_dir / "private_dataset" / "private_bindings.jsonl").read_text().splitlines() if line.strip()]
    rows = [json.loads(line) for line in (run_dir / "private_dataset" / "rule-bc-v1.jsonl").read_text().splitlines() if line.strip()]
    assert binds and rows
    assert {bind["episode_group_id"] for bind in binds} == set(match_ids)
    for bind in binds:
        lineage = bind["o2_lineage"]
        assert lineage["match_id"] == bind["episode_group_id"]
        assert lineage["backend_kind"] == "cabt"
        assert lineage["own_agent_id"] == "rule-agent-v0"
        assert lineage["opponent_agent_id"] == "random-legal-v0"
        assert lineage["seat_index"] in (0, 1)
    for row in rows:
        assert row["metadata"]["o2_match_id"] in match_ids
        assert row["metadata"]["episode_group_id"] == row["metadata"]["o2_match_id"]
    assert summary["o2_lineage_present"] is True
    assert set(summary["o2_match_ids"]) == set(match_ids)
    assert len(summary["o2_plan_hashes"]) == 2  # 4 matches share plan_hash pairwise
    manifest = json.loads((run_dir / "dataset_manifest.json").read_text())
    assert manifest["o2_lineage_present"] is True
    assert set(manifest["o2_match_ids"]) == set(match_ids)


def test_o2_lineage_mode_rejects_count_mismatch(tmp_path: Path) -> None:
    entries = _o2_lineage_entries(["match_a", "match_b"])
    with pytest.raises(LineageValidationError, match="lineage_count_mismatch"):
        collect_actual_dataset(
            run_id="run", games=3, base_seed=9300, output_root=tmp_path,
            canonical_base_sha="a" * 40, deck_path=DECK_PATH, repository_root=REPOSITORY_ROOT,
            match_runner=_fake_runner(), capability_report=_READY, source_revision="test-revision",
            episode_lineage_inputs=entries, opponent_deck_path=DECK_PATH,
            opponent_agent_factory=_stub_opponent_factory,
        )


def test_o2_lineage_mode_requires_opponent_wiring(tmp_path: Path) -> None:
    entries = _o2_lineage_entries(["match_a", "match_b"])
    with pytest.raises(LineageValidationError, match="o2_mode_requires_opponent_wiring"):
        collect_actual_dataset(
            run_id="run", games=2, base_seed=9300, output_root=tmp_path,
            canonical_base_sha="a" * 40, deck_path=DECK_PATH, repository_root=REPOSITORY_ROOT,
            match_runner=_fake_runner(), capability_report=_READY, source_revision="test-revision",
            episode_lineage_inputs=entries,
        )


def test_o2_match_id_and_c4_episode_id_are_the_same_value(tmp_path: Path) -> None:
    match_ids = ["match_lookup1", "match_lookup2"]
    summary = _collect_o2(tmp_path, match_ids=match_ids)
    run_dir = tmp_path / "run"
    binds = [json.loads(line) for line in (run_dir / "private_dataset" / "private_bindings.jsonl").read_text().splitlines() if line.strip()]
    rows = [json.loads(line) for line in (run_dir / "private_dataset" / "rule-bc-v1.jsonl").read_text().splitlines() if line.strip()]
    match_id_set = set(match_ids)
    assert {b["episode_group_id"] for b in binds} == match_id_set  # O2 match_id -> C4 episode ID
    assert {r["metadata"]["episode_group_id"] for r in rows} == match_id_set  # decision -> match_id
    assert {r["metadata"]["o2_match_id"] for r in rows} == match_id_set
    assert set(summary["o2_match_ids"]) == match_id_set  # dataset_manifest/public_summary -> match_ids


def test_public_summary_never_carries_seat_or_implementation_hash(tmp_path: Path) -> None:
    summary = _collect_o2(tmp_path, match_ids=["match_priv1", "match_priv2"])
    dumped = json.dumps(summary)
    assert "own_implementation_hash" not in dumped
    assert "opponent_implementation_hash" not in dumped
    assert "seat_index" not in dumped
    assert "own_deck_hash" not in dumped
    assert "opponent_deck_hash" not in dumped
    manifest_dumped = json.dumps(json.loads((tmp_path / "run" / "dataset_manifest.json").read_text()))
    assert "own_implementation_hash" not in manifest_dumped
    assert "seat_index" not in manifest_dumped
    assert scan_public_artifact(summary)["privacy_violations"] == 0


def test_o2_seat_swapped_pair_members_are_never_split_across_train_and_validation(tmp_path: Path) -> None:
    # 8 matches = 4 seat-swapped pairs; enough episodes for a real split.
    match_ids = [f"match_pair{i}" for i in range(8)]
    _collect_o2(tmp_path, match_ids=match_ids)
    binds = [
        json.loads(line)
        for line in (tmp_path / "run" / "private_dataset" / "private_bindings.jsonl").read_text().splitlines()
        if line.strip()
    ]
    pair_by_match = {bind["episode_group_id"]: bind["o2_lineage"]["pair_id"] for bind in binds}
    split_manifest = json.loads((tmp_path / "run" / "split_manifest.json").read_text())
    assignments = split_manifest["assignments"]  # source_id (redacted) -> split; use match_id groups instead
    # Map each raw match_id to its assigned split via the row metadata's source_id.
    rows = [
        json.loads(line)
        for line in (tmp_path / "run" / "private_dataset" / "rule-bc-v1.jsonl").read_text().splitlines()
        if line.strip()
    ]
    split_by_match_id: dict[str, str] = {}
    for row in rows:
        match_id = row["metadata"]["o2_match_id"]
        split_by_match_id[match_id] = assignments.get(row["source_id"])
    split_by_pair: dict[str, set[str]] = {}
    for match_id, pair_id in pair_by_match.items():
        split_by_pair.setdefault(pair_id, set()).add(split_by_match_id.get(match_id))
    overlapping = {pair: splits for pair, splits in split_by_pair.items() if len(splits) > 1}
    assert overlapping == {}, f"seat-swapped pair(s) split across train/validation: {overlapping}"


def test_generated_rows_are_rulebc_compatible(tmp_path: Path) -> None:
    _collect(tmp_path, games=4)
    examples = load_dataset(tmp_path / "run" / "private_dataset" / "rule-bc-v1.jsonl")
    assert all(isinstance(example, RuleBCExample) for example in examples)
    # Every row round-trips through the exact trainer contract (from_dict is
    # what load_dataset uses and what the trainer consumes).
    for example in examples:
        restored = RuleBCExample.from_dict(example.to_dict())
        assert restored.schema_version == "rule-bc-v1"


def test_exported_smoke_bundle_is_consumer_validate_only_and_keeps_bindings_private(tmp_path: Path) -> None:
    _collect(tmp_path, games=4)
    source = tmp_path / "run"
    source_binding_hash = hashlib.sha256((source / "private_dataset" / "private_bindings.jsonl").read_bytes()).hexdigest()
    bundle_root = tmp_path / "bundle"
    exported = export_bundle(run_root=source, output_root=bundle_root)
    assert exported["status"] == "PASS"
    assert exported["artifact_purpose"] == "TEST_FIXTURE"
    assert exported["performance_eligible"] is False
    assert sorted(path.name for path in bundle_root.iterdir()) == [
        "dataset_manifest.json", "public_summary.json", "rule-bc-v1.jsonl", "split_manifest.json",
    ]
    manifest = json.loads((bundle_root / "dataset_manifest.json").read_text())
    assert manifest["dataset_file"] == "rule-bc-v1.jsonl"
    assert manifest["private_binding"]["sha256"] == source_binding_hash
    assert "candidates" not in (bundle_root / "dataset_manifest.json").read_text()
    assert scan_public_artifact(manifest)["privacy_violations"] == 0
    accepted = accept_bundle(bundle_root)
    assert accepted.public_result()["accepted"] is True
    with pytest.raises(BundleAcceptanceError, match="TEST_FIXTURE"):
        training_commands(accepted, tmp_path / "training")
        assert restored == example


def test_ineligible_source_is_rejected_when_actual_training_export_is_required(tmp_path: Path) -> None:
    _collect(tmp_path, games=4)
    with pytest.raises(BundleExportError, match="eligibility"):
        export_bundle(
            run_root=tmp_path / "run",
            output_root=tmp_path / "bundle",
            require_actual_training=True,
        )


def test_eligible_actual_source_exports_consumer_trainable_actual_bundle(tmp_path: Path) -> None:
    summary = _collect(tmp_path, games=24, runner=_fake_runner(decisions_per_seat=21))
    assert summary["artifact_purpose"] == "ACTUAL_TRAINING"
    bundle_root = tmp_path / "bundle"
    exported = export_bundle(
        run_root=tmp_path / "run",
        output_root=bundle_root,
        require_actual_training=True,
    )
    assert exported["artifact_purpose"] == "ACTUAL_TRAINING"
    assert exported["performance_eligible"] is True
    manifest = json.loads((bundle_root / "dataset_manifest.json").read_text())
    assert manifest["source_kind"] == "ACTUAL_CABT_RULE_BC"
    assert manifest["canonical_base_sha"] == "a" * 40
    assert manifest["private_binding"]["path_role"] == "private_bindings"
    assert not (bundle_root / "private_bindings.jsonl").exists()
    accepted = accept_bundle(bundle_root)
    assert accepted.public_result()["artifact_purpose"] == "ACTUAL_TRAINING"
    commands = training_commands(accepted, tmp_path / "training")
    assert "--split-manifest" in commands[0]


# --------------------------------------------------------------------------- #
# Episode-group split
# --------------------------------------------------------------------------- #


def test_episode_group_split_has_no_overlap_and_keeps_episodes_whole(tmp_path: Path) -> None:
    _collect(tmp_path, games=4)
    split_manifest = json.loads((tmp_path / "run" / "split_manifest.json").read_text())
    assert split_manifest["split_overlap_count"] == 0
    assert split_manifest["duplicate_decision_count"] == 0
    assert split_manifest["train_episode_count"] >= 1
    assert split_manifest["validation_episode_count"] >= 1
    total = split_manifest["train_episode_count"] + split_manifest["validation_episode_count"]
    assert total == split_manifest["episode_count"]


def test_split_by_episode_group_partitions_are_disjoint() -> None:
    examples = [
        build_decision_artifacts(
            _observation(_OPTIONS), deck=[1] * 60, episode_group_id=f"ep-{index}",
            decision_index=0, seat=0, source_revision="t", trace_provenance_hash="h",
        ).example
        for index in range(5)
    ]
    split = split_by_episode_group(examples, seed=0, validation_percent=20)
    assert set(split["train_ids"]).isdisjoint(split["validation_ids"])
    assert split["manifest"]["split_overlap_count"] == 0


def test_split_requires_two_episodes() -> None:
    example = build_decision_artifacts(
        _observation(_OPTIONS), deck=[1] * 60, episode_group_id="only",
        decision_index=0, seat=0, source_revision="t", trace_provenance_hash="h",
    ).example
    with pytest.raises(DataOpsError):
        split_by_episode_group([example], seed=0)


# --------------------------------------------------------------------------- #
# Validation guards (out-of-range, non-finite, duplicate, feature dim)
# --------------------------------------------------------------------------- #


def _rewrite_jsonl(path: Path, rows: list[object]) -> None:
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def test_validate_run_accepts_a_clean_run(tmp_path: Path) -> None:
    _collect(tmp_path, games=4)
    report = validate_run(tmp_path / "run")
    assert report["valid"] is True
    assert report["privacy_violations"] == 0


def test_validate_run_rejects_chosen_index_out_of_range(tmp_path: Path) -> None:
    _collect(tmp_path, games=4)
    bindings_path = tmp_path / "run" / "private_dataset" / "private_bindings.jsonl"
    binds = [json.loads(line) for line in bindings_path.read_text().splitlines() if line.strip()]
    binds[0]["chosen_option_indices"] = [999]
    _rewrite_jsonl(bindings_path, binds)
    with pytest.raises(DataOpsError, match="out of range"):
        validate_run(tmp_path / "run")


@pytest.mark.parametrize(
    "field",
    ("chosen_action_digests", "teacher_chosen_action_digests"),
)
def test_validate_run_rejects_reordered_ordered_skill_targets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
) -> None:
    """SkillOrder target order is semantic, unlike unordered select labels."""
    artifacts = _ordered_skill_artifacts(monkeypatch)
    private_dataset = tmp_path / "run" / "private_dataset"
    private_dataset.mkdir(parents=True)
    _rewrite_jsonl(private_dataset / "rule-bc-v1.jsonl", [artifacts.example.to_dict()])
    binding_path = private_dataset / "private_bindings.jsonl"
    _rewrite_jsonl(binding_path, [artifacts.binding])
    assert validate_run(tmp_path / "run")["valid"] is True

    tampered = dict(artifacts.binding)
    tampered[field] = list(reversed(tampered[field]))
    _rewrite_jsonl(binding_path, [tampered])
    with pytest.raises(DataOpsError, match="chosen"):
        validate_run(tmp_path / "run")


@pytest.mark.parametrize("field", ("chosen_option_indices", "chosen_action_digests"))
def test_validate_run_rejects_duplicate_unordered_selected_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
) -> None:
    """Unordered set comparison cannot hide repeated selected values."""
    artifacts = _unordered_pair_artifacts(monkeypatch)
    private_dataset = tmp_path / "run" / "private_dataset"
    private_dataset.mkdir(parents=True)
    _rewrite_jsonl(private_dataset / "rule-bc-v1.jsonl", [artifacts.example.to_dict()])
    binding_path = private_dataset / "private_bindings.jsonl"
    _rewrite_jsonl(binding_path, [artifacts.binding])
    assert validate_run(tmp_path / "run")["valid"] is True

    tampered = deepcopy(artifacts.binding)
    tampered[field].append(tampered[field][0])
    _rewrite_jsonl(binding_path, [tampered])
    with pytest.raises(DataOpsError, match="chosen"):
        validate_run(tmp_path / "run")


@pytest.mark.parametrize(
    "field",
    ("chosen_action_digests", "teacher_chosen_action_digests"),
)
def test_bundle_validator_rejects_reordered_ordered_skill_targets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
) -> None:
    artifacts = _ordered_skill_artifacts(monkeypatch)
    binding_path = tmp_path / "private-bindings.jsonl"
    _rewrite_jsonl(binding_path, [artifacts.binding])
    manifest = {
        "private_binding": {
            "sha256": hashlib.sha256(binding_path.read_bytes()).hexdigest(),
            "record_count": 1,
            "trainer_input": False,
            "path": binding_path.name,
        }
    }
    _validate_private_binding(tmp_path, manifest, (artifacts.example,))

    tampered = dict(artifacts.binding)
    tampered[field] = list(reversed(tampered[field]))
    _rewrite_jsonl(binding_path, [tampered])
    manifest["private_binding"]["sha256"] = hashlib.sha256(binding_path.read_bytes()).hexdigest()
    with pytest.raises(BundleAcceptanceError, match="target"):
        _validate_private_binding(tmp_path, manifest, (artifacts.example,))


def test_bundle_validator_rejects_reordered_ordered_skill_option_indices(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An ordered private binding must bind each persisted index to its digest."""
    artifacts = _ordered_skill_artifacts(monkeypatch)
    binding_path = tmp_path / "private-bindings.jsonl"
    _rewrite_jsonl(binding_path, [artifacts.binding])
    manifest = {
        "private_binding": {
            "sha256": hashlib.sha256(binding_path.read_bytes()).hexdigest(),
            "record_count": 1,
            "trainer_input": False,
            "path": binding_path.name,
        }
    }
    _validate_private_binding(tmp_path, manifest, (artifacts.example,))

    tampered = dict(artifacts.binding)
    tampered["chosen_option_indices"] = list(
        reversed(tampered["chosen_option_indices"])
    )
    _rewrite_jsonl(binding_path, [tampered])
    manifest["private_binding"]["sha256"] = hashlib.sha256(
        binding_path.read_bytes()
    ).hexdigest()
    with pytest.raises(BundleAcceptanceError, match="target"):
        _validate_private_binding(tmp_path, manifest, (artifacts.example,))


@pytest.mark.parametrize(
    "tamper",
    (
        "duplicate_chosen_index",
        "extra_chosen_digest",
        "extra_teacher_digest",
        "duplicate_candidate_index",
        "non_integer_candidate_index",
        "non_string_candidate_digest",
    ),
)
def test_bundle_validator_closes_selected_and_candidate_binding_shapes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tamper: str,
) -> None:
    artifacts = _ordered_skill_artifacts(monkeypatch)
    tampered = deepcopy(artifacts.binding)
    candidates = tampered["candidates"]
    if tamper == "duplicate_chosen_index":
        tampered["chosen_option_indices"].append(0)
    elif tamper == "extra_chosen_digest":
        tampered["chosen_action_digests"].append("0" * 64)
    elif tamper == "extra_teacher_digest":
        tampered["teacher_chosen_action_digests"].append("0" * 64)
    elif tamper == "duplicate_candidate_index":
        candidates.append(deepcopy(candidates[0]))
        tampered["legal_candidate_count"] = len(candidates)
    elif tamper == "non_integer_candidate_index":
        candidates[0]["option_index"] = "0"
    elif tamper == "non_string_candidate_digest":
        candidates[0]["digest"] = 0
    else:  # pragma: no cover - parametrization is closed above
        raise AssertionError(f"unexpected tamper case: {tamper}")

    binding_path = tmp_path / "private-bindings.jsonl"
    _rewrite_jsonl(binding_path, [tampered])
    manifest = {
        "private_binding": {
            "sha256": hashlib.sha256(binding_path.read_bytes()).hexdigest(),
            "record_count": 1,
            "trainer_input": False,
            "path": binding_path.name,
        }
    }
    with pytest.raises(BundleAcceptanceError):
        _validate_private_binding(tmp_path, manifest, (artifacts.example,))


def test_validate_run_rejects_non_finite_value(tmp_path: Path) -> None:
    _collect(tmp_path, games=4)
    dataset_path = tmp_path / "run" / "private_dataset" / "rule-bc-v1.jsonl"
    rows = [json.loads(line) for line in dataset_path.read_text().splitlines() if line.strip()]
    rows[0]["public_state"]["turn"] = float("nan")
    dataset_path.write_text("".join(json.dumps(row, allow_nan=True) + "\n" for row in rows), encoding="utf-8")
    with pytest.raises(DataOpsError, match="non-finite"):
        validate_run(tmp_path / "run")


def test_validate_run_rejects_duplicate_decision(tmp_path: Path) -> None:
    _collect(tmp_path, games=4)
    bindings_path = tmp_path / "run" / "private_dataset" / "private_bindings.jsonl"
    binds = [json.loads(line) for line in bindings_path.read_text().splitlines() if line.strip()]
    # Force two bindings in the same episode to share a decision index.
    group = binds[0]["episode_group_id"]
    same = [b for b in binds if b["episode_group_id"] == group]
    assert len(same) >= 2
    same[1]["decision_index"] = same[0]["decision_index"]
    _rewrite_jsonl(bindings_path, binds)
    with pytest.raises(DataOpsError, match="duplicate"):
        validate_run(tmp_path / "run")


def test_validate_run_rejects_feature_dimension_mismatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _collect(tmp_path, games=4)
    monkeypatch.setattr(collector_module, "state_features_payload", lambda *a, **k: [0.0] * (STATE_FEATURE_DIM - 1))
    with pytest.raises(DataOpsError, match="feature dimension"):
        validate_run(tmp_path / "run")


# --------------------------------------------------------------------------- #
# Privacy scanning and public/private separation
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("unsafe", "category"),
    [
        ({"raw_observation": {"hand": [{"id": 700}]}}, "raw_observation"),
        ({"card_id": 700}, "raw_card_identity"),
        ({"hand_card_ids": [700]}, "own_hand_identity"),
        ({"opponent_hand": [{"id": 1}]}, "opponent_hidden_information"),
        ({"prize": [1, 2]}, "opponent_hidden_information"),
        ({"candidates": [{"payload": {}}]}, "private_candidate_binding"),
        ({"detail": "/home/private/secret"}, "absolute_path"),
        ({"logs": ["x"]}, "opaque_observation_field"),
        ({"contact": "a@b.com"}, "email"),
    ],
)
def test_privacy_scan_detects_categories_without_retaining_values(unsafe: dict[str, object], category: str) -> None:
    result = scan_public_artifact(unsafe)
    assert result["privacy_scan_executed"] is True
    assert result["privacy_violations"] >= 1
    assert category in result["privacy_violation_categories"]


def test_public_summary_is_separated_from_private_binding(tmp_path: Path) -> None:
    _collect(tmp_path, games=4)
    run_dir = tmp_path / "run"
    public_summary = json.loads((run_dir / "public_summary.json").read_text())
    # Public summary is clean under the scanner.
    assert scan_public_artifact(public_summary)["privacy_violations"] == 0
    encoded = json.dumps(public_summary)
    for leak in ("hand_card_ids", "candidates", "payload", "canonical_payload", "raw_observation"):
        assert leak not in encoded
    # The private binding does retain candidate payloads (own-card identity).
    binds = (run_dir / "private_dataset" / "private_bindings.jsonl").read_text()
    assert "candidates" in binds and "payload" in binds


# --------------------------------------------------------------------------- #
# Resumability
# --------------------------------------------------------------------------- #


def test_resume_does_not_re_execute_completed_games(tmp_path: Path) -> None:
    seeds_first: list[int] = []
    seeds_second: list[int] = []
    _collect(tmp_path, games=3, runner=_fake_runner(seeds=seeds_first))
    _collect(tmp_path, games=3, runner=_fake_runner(seeds=seeds_second))
    assert len(seeds_first) == 3
    assert seeds_second == []  # every game already completed; none re-run


def test_resume_rejects_a_different_config(tmp_path: Path) -> None:
    _collect(tmp_path, games=3)
    with pytest.raises(DataOpsError, match="different_config"):
        _collect(tmp_path, games=4, split_seed=1)  # execution config changes -> rejected


def test_resume_extends_without_reexecuting_completed_games(tmp_path: Path) -> None:
    first: list[int] = []
    second: list[int] = []
    _collect(tmp_path, games=3, runner=_fake_runner(seeds=first))
    _collect(tmp_path, games=5, runner=_fake_runner(seeds=second))
    assert first == [100, 101, 102]
    assert second == [103, 104]


def test_engineering_gate_promotes_only_a_valid_actual_training_bundle(tmp_path: Path) -> None:
    summary = _collect(tmp_path, games=24, runner=_fake_runner(decisions_per_seat=21))
    assert summary["artifact_purpose"] == "ACTUAL_TRAINING"
    assert summary["performance_eligible"] is True
    # Producer output is intentionally not a consumer bundle root.  The
    # explicit exporter owns the TEST_FIXTURE/ACTUAL_TRAINED purpose mapping.
    assert validate_run(tmp_path / "run")["valid"] is True


# --------------------------------------------------------------------------- #
# Compute manifest and submission-safety invariants
# --------------------------------------------------------------------------- #


def test_compute_manifest_records_devices_without_host_identity(tmp_path: Path) -> None:
    _collect(tmp_path, games=4)
    summary = json.loads((tmp_path / "run" / "public_summary.json").read_text())
    compute = summary["compute"]
    assert isinstance(compute["cpu_count"], int)
    assert isinstance(compute["cuda_available"], bool)
    assert compute["recommended_training_device"] == "cpu"
    encoded = json.dumps(compute)
    assert "/home/" not in encoded and "@" not in encoded


def test_collection_never_modifies_main_or_deck(tmp_path: Path) -> None:
    def sha(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    before = (sha(REPOSITORY_ROOT / "main.py"), sha(DECK_PATH))
    _collect(tmp_path, games=3)
    after = (sha(REPOSITORY_ROOT / "main.py"), sha(DECK_PATH))
    assert before == after
