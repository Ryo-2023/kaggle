"""Transactional C1-v2 runtime contracts."""

from __future__ import annotations

import ast
import gc
import hashlib
import copy
from dataclasses import replace
import json
from pathlib import Path
import weakref

import pytest

from mage_ptcg.knowledge.model import deck_identity_from_card_ids
from mage_ptcg.meta_specialist.actor_visible_features_v1 import SpecialistStepLogitsV1, make_test_card_vocabulary_v1
from mage_ptcg.meta_specialist.decks import (
    ArchetypeSpec, DeckAssetInput, QualifiedDeckAsset, create_deck_lock,
    qualify_deck_asset,
)
from mage_ptcg.meta_specialist.runtime import (
    MetaSpecialistRuntime, PolicyTelemetrySnapshot, RuntimeConstraintManifest,
    RuntimeContractError, RuntimeDecisionTimeoutError, make_agent,
)


def _observation(*, duplicate: bool = False, empty: bool = False) -> dict[str, object]:
    hand = [] if empty else [
        {"id": 101, "serial": 10, "playerIndex": 0},
        {"id": 101 if duplicate else 102, "serial": 11, "playerIndex": 0},
    ]
    return {
        "current": {"energyAttached": False, "firstPlayer": 0, "looking": None,
            "players": [
                {"active": [], "asleep": False, "bench": [], "benchMax": 5, "burned": False, "confused": False, "deckCount": 60, "discard": [], "hand": hand, "handCount": len(hand), "paralyzed": False, "poisoned": False, "prize": [None] * 6},
                {"active": [], "asleep": False, "bench": [], "benchMax": 5, "burned": False, "confused": False, "deckCount": 60, "discard": [], "hand": None, "handCount": 0, "paralyzed": False, "poisoned": False, "prize": [None] * 6},
            ], "result": -1, "retreated": False, "stadium": [], "stadiumPlayed": False,
            "supporterPlayed": False, "turn": 2, "turnActionCount": 0, "yourIndex": 0},
        "select": {"context": 1, "contextCard": None, "deck": None, "effect": None,
            "maxCount": 0 if empty else 1, "minCount": 0,
            "option": [] if empty else [
                {"type": 3, "area": 2, "index": 0, "playerIndex": 0},
                {"type": 3, "area": 2, "index": 1, "playerIndex": 0},
            ], "remainDamageCounter": 0, "remainEnergyCost": 0, "type": 1},
        "step": 1,
    }


class _Session:
    def __init__(self, owner: "_Policy") -> None:
        self.owner, self.commits, self.aborts = owner, 0, 0
    def logits(self, _model, step):
        self.owner.calls += 1
        return SpecialistStepLogitsV1((0.0,) * len(step.allowed_semantic_classes), 1.0 if step.stop_available else None)
    def commit(self, _outcome) -> None: self.commits += 1
    def abort(self) -> None: self.aborts += 1


class _Policy:
    def __init__(self, identity: str, lineage: str) -> None:
        self.identity, self.lineage, self.calls, self.sessions, self.resets = identity, lineage, 0, [], 0
        self.fallback_count = 0
    def reset(self) -> None: self.resets += 1
    def begin_decision(self):
        session = _Session(self); self.sessions.append(session); return session
    def policy_telemetry(self):
        return PolicyTelemetrySnapshot(self.identity, "checkpointed_specialist", True, self.lineage, None, self.fallback_count)


def _qualified_asset(
    tmp_path: Path, *, cards: tuple[int, ...] | None = None,
) -> QualifiedDeckAsset:
    cards = tuple(range(1, 61)) if cards is None else cards
    path = tmp_path / "runtime-deck.csv"
    path.write_text("\n".join(map(str, cards)) + "\n", encoding="utf-8")
    source = DeckAssetInput.from_path(
        asset_id="runtime-test", archetype_id="test", path=path,
        source_ref="fixture/runtime-deck.csv", source_commit="d" * 40,
        asset_class="deck_only", usage_boundary="bundle_allowed",
        policy_compatibility="specialist-v2", card_database_version="test-db-v1",
    )
    return qualify_deck_asset(
        source, ArchetypeSpec("test", (), (cards[0],), "qualified_not_trained"),
        known_card_ids=set(cards), cabt_legality=lambda _cards: (True, "fixture-cabt-pass"),
    )


def _runtime(
    tmp_path: Path, *, monotonic=None, cards: tuple[int, ...] | None = None,
) -> tuple[MetaSpecialistRuntime, _Policy, tuple[int, ...]]:
    deck = _qualified_asset(tmp_path, cards=cards); cards = deck.card_ids; identity = deck_identity_from_card_ids(cards)
    lock = create_deck_lock(archetype_id="test", selected_deck_identity=identity, compared_deck_identities=(identity,), foundation_init_id="a" * 64, joint_race_schedule_id="b" * 64, equal_transition_budget=1)
    identity = hashlib.sha256(b"policy").hexdigest(); policy = _Policy(identity, lock.policy_lineage_id)
    kwargs = {} if monotonic is None else {"monotonic": monotonic}
    return MetaSpecialistRuntime(deck_asset=deck, deck_lock=lock, vocabulary=make_test_card_vocabulary_v1(range(1, 2000)), policy=policy, expected_policy_identity=identity, constraints=RuntimeConstraintManifest.frozen_v1(), **kwargs), policy, cards


def test_runtime_delivers_locked_deck_once_and_commits_empty_action(tmp_path: Path) -> None:
    runtime, policy, cards = _runtime(tmp_path)
    first = runtime({"select": None})
    assert first == list(cards)
    assert runtime(_observation(empty=True)) == []
    assert runtime.environment_action_count == 1
    assert policy.sessions[0].commits == 1 and policy.sessions[0].aborts == 0
    assert runtime({"select": None}) == []


def test_runtime_decodes_once_and_replays_probability_from_cache(tmp_path: Path) -> None:
    runtime, policy, _cards = _runtime(tmp_path); runtime({"select": None})
    assert runtime(_observation()) == []
    # STOP is selected at the initial prefix; its replay is served from the same cache.
    assert policy.calls == 1
    assert runtime.package_telemetry()["legal_action_count"] == 1


def test_core_runtime_rejects_missing_wrapper_step_without_mutating_input(tmp_path: Path) -> None:
    runtime, policy, _cards = _runtime(tmp_path)
    runtime({"select": None})
    observation = _observation()
    del observation["step"]
    with pytest.raises(RuntimeContractError, match="step"):
        runtime(observation)
    assert "step" not in observation
    assert policy.sessions == []
    assert runtime.package_telemetry()["invalid_count"] == 1


def test_runtime_rejects_action_before_registration_without_opening_session(tmp_path: Path) -> None:
    runtime, policy, _cards = _runtime(tmp_path)
    with pytest.raises(RuntimeContractError): runtime(_observation())
    assert policy.sessions == []


def test_duplicate_public_identity_is_aggregate_only_trace(tmp_path: Path) -> None:
    runtime, _policy, _cards = _runtime(tmp_path); runtime({"select": None}); runtime(_observation(duplicate=True))
    payload = runtime.traces[0].to_payload()
    assert payload["trace_variant"] == "duplicate-public-identity"
    assert "public_projection" not in payload and "option" not in str(payload).lower()


def test_runtime_aborts_exactly_once_on_a_precommit_policy_failure(tmp_path: Path) -> None:
    runtime, policy, _cards = _runtime(tmp_path); runtime({"select": None})
    policy.sessions = []
    class BrokenSession(_Session):
        def logits(self, _model, _step): return object()
    def broken_begin():
        session = BrokenSession(policy); policy.sessions.append(session); return session
    policy.begin_decision = broken_begin  # type: ignore[method-assign]
    with pytest.raises(RuntimeContractError): runtime(_observation())
    assert policy.sessions[0].commits == 0 and policy.sessions[0].aborts == 1
    assert runtime.environment_action_count == 0 and runtime.traces == ()


@pytest.mark.parametrize(
    "field",
    (
        "decision_p95_target_ms", "decision_p99_target_ms",
        "decision_hard_timeout_ms", "game_hard_timeout_ms",
        "peak_rss_limit_kib", "trace_capacity",
    ),
)
@pytest.mark.parametrize("replacement", (True, 100.0))
def test_runtime_constraints_reject_non_exact_integer_types(field: str, replacement: object) -> None:
    constraints = RuntimeConstraintManifest.frozen_v1()
    with pytest.raises(RuntimeContractError):
        replace(constraints, **{field: replacement})


def test_runtime_constraints_reject_all_float_payload_with_recomputed_id() -> None:
    payload = RuntimeConstraintManifest.frozen_v1().to_payload()
    payload.pop("runtime_constraints_id")
    payload["host_dependencies"] = ()
    for name in (
        "decision_p95_target_ms", "decision_p99_target_ms",
        "decision_hard_timeout_ms", "game_hard_timeout_ms",
        "peak_rss_limit_kib", "trace_capacity",
    ):
        payload[name] = float(payload[name])
    identity = hashlib.sha256(
        b"meta-specialist-runtime-constraints-v1\0"
        + json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    with pytest.raises(RuntimeContractError, match="integer"):
        RuntimeConstraintManifest(**payload, runtime_constraints_id=identity)


class _FakeClock:
    def __init__(self, *values: float) -> None: self.values = iter(values)
    def __call__(self) -> float: return next(self.values)


def test_decision_timeout_aborts_before_commit_and_is_classified_transactionally(tmp_path: Path) -> None:
    runtime, policy, _cards = _runtime(
        tmp_path, monotonic=_FakeClock(0.0, 0.0, 0.0, 0.0, 1.001),
    )
    runtime({"select": None})
    with pytest.raises(RuntimeDecisionTimeoutError): runtime(_observation())
    assert policy.sessions[0].aborts == 1 and policy.sessions[0].commits == 0
    telemetry = runtime.package_telemetry()
    assert telemetry["timeout_count"] == 1
    assert telemetry["invalid_count"] == 0
    assert telemetry["legal_decision_count"] == 0 and runtime.traces == ()


def test_callback_validation_elapsed_time_is_inside_decision_deadline(tmp_path: Path) -> None:
    runtime, policy, _cards = _runtime(
        tmp_path, monotonic=_FakeClock(0.0, 0.0, 1.001),
    )
    runtime({"select": None})
    with pytest.raises(RuntimeDecisionTimeoutError):
        runtime(_observation())
    assert policy.sessions == []
    telemetry = runtime.package_telemetry()
    assert telemetry["timeout_count"] == 1 and telemetry["invalid_count"] == 0


def test_package_telemetry_is_fresh_and_identity_fields_cannot_drift(tmp_path: Path) -> None:
    runtime, policy, _cards = _runtime(tmp_path)
    policy.fallback_count = 3
    assert runtime.package_telemetry()["fallback_count"] == 3
    policy.identity = "f" * 64
    with pytest.raises(RuntimeContractError, match="identity"):
        runtime.package_telemetry()


def test_begin_decision_identity_drift_aborts_before_inference(tmp_path: Path) -> None:
    runtime, policy, _cards = _runtime(tmp_path)
    runtime({"select": None})
    original_identity = policy.identity

    def drifting_begin():
        session = _Session(policy)
        policy.sessions.append(session)
        policy.identity = "f" * 64
        return session

    policy.begin_decision = drifting_begin  # type: ignore[method-assign]
    with pytest.raises(RuntimeContractError, match="identity"):
        runtime(_observation())
    assert policy.sessions[0].commits == 0 and policy.sessions[0].aborts == 1
    policy.identity = original_identity
    telemetry = runtime.package_telemetry()
    assert telemetry["invalid_count"] == 1 and telemetry["legal_decision_count"] == 0


def test_logits_identity_drift_aborts_before_commit(tmp_path: Path) -> None:
    runtime, policy, _cards = _runtime(tmp_path)
    runtime({"select": None})
    original_identity = policy.identity

    class DriftingLogitsSession(_Session):
        def logits(self, model, step):
            self.owner.identity = "f" * 64
            return super().logits(model, step)

    def drifting_begin():
        session = DriftingLogitsSession(policy)
        policy.sessions.append(session)
        return session

    policy.begin_decision = drifting_begin  # type: ignore[method-assign]
    with pytest.raises(RuntimeContractError, match="identity"):
        runtime(_observation())
    assert policy.sessions[0].commits == 0 and policy.sessions[0].aborts == 1
    policy.identity = original_identity
    assert runtime.package_telemetry()["invalid_count"] == 1


def test_next_state_token_identity_drift_aborts_before_commit(tmp_path: Path) -> None:
    runtime, policy, _cards = _runtime(tmp_path)
    runtime({"select": None})
    original_identity = policy.identity

    class DriftingTokenSession(_Session):
        @property
        def next_recurrent_state_token(self):
            self.owner.identity = "f" * 64
            return "next"

    def drifting_begin():
        session = DriftingTokenSession(policy)
        policy.sessions.append(session)
        return session

    policy.begin_decision = drifting_begin  # type: ignore[method-assign]
    with pytest.raises(RuntimeContractError, match="identity"):
        runtime(_observation())
    assert policy.sessions[0].commits == 0 and policy.sessions[0].aborts == 1
    policy.identity = original_identity
    assert runtime.package_telemetry()["invalid_count"] == 1


def test_commit_identity_drift_is_quarantined_at_the_next_trust_boundary(
    tmp_path: Path,
) -> None:
    runtime, policy, _cards = _runtime(tmp_path)
    runtime({"select": None})
    original_identity = policy.identity

    class DriftingCommitSession(_Session):
        def commit(self, outcome) -> None:
            super().commit(outcome)
            self.owner.identity = "f" * 64

    def drifting_begin():
        session = DriftingCommitSession(policy)
        policy.sessions.append(session)
        return session

    policy.begin_decision = drifting_begin  # type: ignore[method-assign]
    # Commit is the point of no return: the environment still receives the
    # already-committed action, and the runtime must never abort that session.
    assert runtime(_observation()) == []
    assert policy.sessions[0].commits == 1 and policy.sessions[0].aborts == 0
    assert runtime.environment_action_count == 1
    with pytest.raises(RuntimeContractError, match="identity"):
        runtime.package_telemetry()
    with pytest.raises(RuntimeContractError, match="identity"):
        runtime(_observation())
    assert len(policy.sessions) == 1
    assert policy.sessions[0].commits == 1 and policy.sessions[0].aborts == 0
    policy.identity = original_identity
    telemetry = runtime.package_telemetry()
    assert telemetry["legal_decision_count"] == 1 and telemetry["invalid_count"] == 1


def test_runtime_rejects_a_directly_constructed_qualified_asset(tmp_path: Path) -> None:
    genuine = _qualified_asset(tmp_path)
    forged = QualifiedDeckAsset(
        genuine.asset_id, genuine.archetype_id, genuine.card_ids,
        genuine.deck_identity, genuine.deck_file_sha256, genuine.source_ref,
        genuine.source_commit, genuine.asset_class, genuine.usage_boundary,
        genuine.policy_compatibility, genuine.card_database_version,
        genuine.card_count, genuine.cabt_legality_status, "forged-pass",
    )
    identity = genuine.deck_identity
    lock = create_deck_lock(archetype_id="test", selected_deck_identity=identity, compared_deck_identities=(identity,), foundation_init_id="a" * 64, joint_race_schedule_id="b" * 64, equal_transition_budget=1)
    policy_identity = hashlib.sha256(b"policy").hexdigest()
    policy = _Policy(policy_identity, lock.policy_lineage_id)
    with pytest.raises(RuntimeContractError, match="attestation"):
        MetaSpecialistRuntime(deck_asset=forged, deck_lock=lock, vocabulary=make_test_card_vocabulary_v1(range(1, 2000)), policy=policy, expected_policy_identity=policy_identity, constraints=RuntimeConstraintManifest.frozen_v1())


class _PolicyFactory:
    def __init__(self, policy: _Policy, *, shared: bool) -> None:
        self.policy, self.shared, self.calls = policy, shared, 0
    def new_policy(self):
        self.calls += 1
        if self.shared:
            return self.policy
        return _Policy(self.policy.identity, self.policy.lineage)


class _NonWeakPolicy:
    __slots__ = ("identity", "lineage", "calls", "sessions", "resets", "fallback_count")

    def __init__(self, identity: str, lineage: str) -> None:
        self.identity, self.lineage = identity, lineage
        self.calls, self.sessions, self.resets, self.fallback_count = 0, [], 0, 0

    def reset(self) -> None:
        self.resets += 1

    def begin_decision(self):
        session = _Session(self)  # type: ignore[arg-type]
        self.sessions.append(session)
        return session

    def policy_telemetry(self):
        return PolicyTelemetrySnapshot(
            self.identity, "checkpointed_specialist", True,
            self.lineage, None, self.fallback_count,
        )


def _factory_parts(tmp_path: Path):
    deck = _qualified_asset(tmp_path)
    lock = create_deck_lock(
        archetype_id="test", selected_deck_identity=deck.deck_identity,
        compared_deck_identities=(deck.deck_identity,), foundation_init_id="a" * 64,
        joint_race_schedule_id="b" * 64, equal_transition_budget=1,
    )
    identity = hashlib.sha256(b"factory-policy").hexdigest()
    return deck, lock, identity


def test_make_agent_rejects_a_factory_that_reuses_one_policy_object(tmp_path: Path) -> None:
    deck, lock, identity = _factory_parts(tmp_path)
    factory = _PolicyFactory(_Policy(identity, lock.policy_lineage_id), shared=True)
    kwargs = dict(
        deck_asset=deck, deck_lock=lock,
        vocabulary=make_test_card_vocabulary_v1(range(1, 2000)),
        policy_factory=factory, expected_policy_identity=identity,
        constraints=RuntimeConstraintManifest.frozen_v1(),
    )
    first = make_agent(**kwargs)
    with pytest.raises(RuntimeContractError, match="previously bound"):
        make_agent(**kwargs)
    assert factory.calls == 2
    assert first.agent({"select": None}) == list(deck.card_ids)


def test_make_agent_requires_weak_referenceable_policy_lifecycle(tmp_path: Path) -> None:
    deck, lock, identity = _factory_parts(tmp_path)
    factory = _PolicyFactory(
        _NonWeakPolicy(identity, lock.policy_lineage_id),  # type: ignore[arg-type]
        shared=True,
    )
    with pytest.raises(RuntimeContractError, match="weak-referenceable"):
        make_agent(
            deck_asset=deck, deck_lock=lock,
            vocabulary=make_test_card_vocabulary_v1(range(1, 2000)),
            policy_factory=factory, expected_policy_identity=identity,
            constraints=RuntimeConstraintManifest.frozen_v1(),
        )


def test_make_agent_calls_factory_once_and_gives_fresh_bindings(tmp_path: Path) -> None:
    deck, lock, identity = _factory_parts(tmp_path)
    factory = _PolicyFactory(_Policy(identity, lock.policy_lineage_id), shared=False)
    kwargs = dict(
        deck_asset=deck, deck_lock=lock,
        vocabulary=make_test_card_vocabulary_v1(range(1, 2000)),
        policy_factory=factory, expected_policy_identity=identity,
        constraints=RuntimeConstraintManifest.frozen_v1(),
    )
    first, second = make_agent(**kwargs), make_agent(**kwargs)
    assert factory.calls == 2
    assert first.agent is not second.agent
    first.agent({"select": None})
    assert first.package_telemetry()["legal_decision_count"] == 0
    assert second.package_telemetry()["legal_decision_count"] == 0


def test_policy_identity_registry_releases_100_dead_bindings_and_allows_id_reuse(
    tmp_path: Path,
) -> None:
    import mage_ptcg.meta_specialist.runtime as runtime_module

    deck, lock, identity = _factory_parts(tmp_path)
    factory = _PolicyFactory(_Policy(identity, lock.policy_lineage_id), shared=False)
    kwargs = dict(
        deck_asset=deck, deck_lock=lock,
        vocabulary=make_test_card_vocabulary_v1(range(1, 2000)),
        policy_factory=factory, expected_policy_identity=identity,
        constraints=RuntimeConstraintManifest.frozen_v1(),
    )
    baseline_ids = set(runtime_module._BOUND_POLICY_OBJECTS)
    seen_ids: set[int] = set()
    reused_id = False
    for _index in range(100):
        binding = make_agent(**kwargs)
        policy = binding.agent._policy  # type: ignore[attr-defined]
        policy_id = id(policy)
        reused_id = reused_id or policy_id in seen_ids
        seen_ids.add(policy_id)
        policy_ref = weakref.ref(policy)
        del policy
        del binding
        gc.collect()
        assert policy_ref() is None
        assert set(runtime_module._BOUND_POLICY_OBJECTS).issubset(baseline_ids)
    # CPython's fixed-size allocator should reuse at least one policy address;
    # every replacement was nevertheless accepted because weak cleanup is
    # reference-identity guarded rather than numeric-ID-only.
    assert reused_id


def test_runtime_sources_reject_duplicate_dataclass_fields_and_init_assignments() -> None:
    root = Path(__file__).resolve().parents[2]
    for source_path in (
        root / "src/mage_ptcg/meta_specialist/runtime.py",
        root / "src/mage_ptcg/meta_specialist/runtime_actions_v2.py",
    ):
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                fields = [
                    item.target.id
                    for item in node.body
                    if isinstance(item, ast.AnnAssign)
                    and isinstance(item.target, ast.Name)
                ]
                assert len(fields) == len(set(fields)), (
                    f"duplicate annotated field in {source_path.name}:{node.name}"
                )
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "__init__":
                assignments: list[str] = []
                for item in node.body:
                    targets = item.targets if isinstance(item, ast.Assign) else ()
                    for target in targets:
                        if (
                            isinstance(target, ast.Attribute)
                            and isinstance(target.value, ast.Name)
                            and target.value.id == "self"
                        ):
                            assignments.append(target.attr)
                assert len(assignments) == len(set(assignments)), (
                    f"duplicate self assignment in {source_path.name}:{node.lineno}"
                )


@pytest.mark.parametrize("candidate_count", (61, 64, 67))
def test_runtime_executes_observed_public_v1_tail_without_enumeration(
    tmp_path: Path, candidate_count: int,
) -> None:
    from tests.meta_specialist.test_runtime_actions_v2 import _large_main_observation

    runtime, policy, _cards = _runtime(tmp_path)
    runtime({"select": None})
    selected = runtime(_large_main_observation(candidate_count))
    assert len(selected) == 1 and 0 <= selected[0] < candidate_count
    assert policy.sessions[0].commits == 1 and policy.sessions[0].aborts == 0
    payload = runtime.traces[0].to_payload()
    assert payload["trace_variant"] == "public-v1-option-limit-exceeded"
    assert payload["candidate_count"] == candidate_count


def test_runtime_preserves_skill_order_when_current_options_are_reversed(tmp_path: Path) -> None:
    def ordered_observation(reverse: bool) -> dict[str, object]:
        observation = _observation()
        options = [
            {"type": 15, "cardId": 101, "serial": 10},
            {"type": 15, "cardId": 102, "serial": 11},
        ]
        observation["select"] = {  # type: ignore[index]
            "context": 34, "contextCard": None, "deck": None, "effect": None,
            "maxCount": 2, "minCount": 2,
            "option": list(reversed(options)) if reverse else options,
            "remainDamageCounter": 0, "remainEnergyCost": 0, "type": 5,
        }
        return observation

    first, _first_policy, _ = _runtime(tmp_path)
    second, _second_policy, _ = _runtime(tmp_path)
    first({"select": None}); second({"select": None})
    assert first(ordered_observation(False)) == [0, 1]
    assert second(ordered_observation(True)) == [1, 0]


def test_runtime_commits_publicly_located_skill_card_refs(tmp_path: Path) -> None:
    runtime, policy, _cards = _runtime(tmp_path)
    runtime({"select": None})
    observation = _observation()
    def pokemon(card_id: int, serial: int) -> dict[str, object]:
        return {
            "id": card_id, "serial": serial, "hp": 100, "maxHp": 100,
            "appearThisTurn": False, "energies": [1], "energyCards": [],
            "tools": [], "preEvolution": [],
        }
    observation["current"]["players"][0]["active"] = [  # type: ignore[index]
        pokemon(201, 2001)
    ]
    observation["current"]["players"][1]["active"] = [  # type: ignore[index]
        pokemon(301, 3001)
    ]
    observation["select"] = {  # type: ignore[index]
        "context": 34, "contextCard": None, "deck": None, "effect": None,
        "maxCount": 2, "minCount": 2,
        "option": [
            {"type": 15, "cardId": 201, "serial": 2001},
            {"type": 15, "cardId": 301, "serial": 3001},
        ],
        "remainDamageCounter": 0, "remainEnergyCost": 0, "type": 5,
    }
    assert runtime(observation) == [0, 1]
    assert policy.sessions[0].commits == 1 and policy.sessions[0].aborts == 0
    actions = runtime.traces[0].public_trace["selected_public_actions"]  # type: ignore[index]
    assert len(actions) == 2
    assert all(
        len(action["public_identity"]["source"]["card_ref"]) == 64  # type: ignore[index]
        for action in actions
    )


def test_pinned_936_corpus_executes_through_full_runtime(tmp_path: Path) -> None:
    corpus = Path(
        "/home/bfe-lab-ono/kaggle/handoff-artifacts/"
        "family-agent-activation-remediation-v1/artifacts/turn_telemetry.jsonl"
    )
    if not corpus.is_file():
        pytest.skip("BLOCKED_DEPENDENCY: pinned 936-record telemetry corpus is unavailable")
    assert hashlib.sha256(corpus.read_bytes()).hexdigest() == "de6091a5724334e431d7e3858c9bdc27b046001911ebf912b2a25c34f92e14be"
    runtime, policy, _cards = _runtime(tmp_path)
    runtime({"select": None})
    tail_counts: list[int] = []
    for raw_line in corpus.read_text(encoding="utf-8").splitlines():
        if not raw_line.strip():
            continue
        row = json.loads(raw_line)
        observation = copy.deepcopy(row["public_observation"])
        observation["step"] = 0
        selected = runtime(observation)
        option_count = len(observation["select"]["option"])
        assert len(selected) == len(set(selected))
        assert all(0 <= index < option_count for index in selected)
        if option_count > 60:
            tail_counts.append(option_count)
    variants: dict[str, int] = {}
    for trace in runtime.traces:
        variants[trace.trace_variant] = variants.get(trace.trace_variant, 0) + 1
    assert len(runtime.traces) == 936
    assert variants == {
        "public-v1-representable": 339,
        "duplicate-public-identity": 594,
        "public-v1-option-limit-exceeded": 3,
    }
    assert sorted(tail_counts) == [61, 64, 67]
    assert len(policy.sessions) == 936
    assert all(session.commits == 1 and session.aborts == 0 for session in policy.sessions)
