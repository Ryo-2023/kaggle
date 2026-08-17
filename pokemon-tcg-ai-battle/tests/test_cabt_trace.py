"""Tests for the privacy-safe cabt observation trace (v0)."""

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

from mage_ptcg.observability.cabt_trace import (
    ActorVisibleAttestationWriter,
    FORBIDDEN_OBSERVATION_KEYS,
    MalformedObservationError,
    TraceWriter,
    find_forbidden_keys,
    make_traced_agent,
    normalize_decision_record,
    normalize_option,
    normalize_player_view,
)

from scripts.cabt_trace import (
    TEMP_FILE_PREFIX,
    TraceExecutionError,
    TraceOutputExistsError,
    run_trace,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]

_BANNED_TIME_KEYS = ("ts", "timestamp", "time", "uuid", "created_at", "wall_clock", "wall_clock_time")


def _collect_keys(value: Any) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        for key, val in value.items():
            keys.add(key)
            keys |= _collect_keys(val)
    elif isinstance(value, list):
        for item in value:
            keys |= _collect_keys(item)
    return keys


def _make_select(*, select_type=0, context=0, min_count=1, max_count=1, options):
    return {
        "type": select_type,
        "context": context,
        "contextCard": None,
        "deck": None,
        "effect": None,
        "minCount": min_count,
        "maxCount": max_count,
        "option": options,
        "remainDamageCounter": 0,
        "remainEnergyCost": 0,
    }


def _make_player(
    *,
    hand=None,
    hand_count=0,
    deck_count=60,
    prize_count=6,
    active=None,
    bench=None,
    discard=None,
    bench_max=5,
):
    return {
        "active": active if active is not None else [],
        "asleep": False,
        "bench": bench if bench is not None else [],
        "benchMax": bench_max,
        "burned": False,
        "confused": False,
        "deckCount": deck_count,
        "discard": discard if discard is not None else [],
        "hand": hand if hand is not None else [],
        "handCount": hand_count,
        "paralyzed": False,
        "poisoned": False,
        "prize": [{"id": 900 + i, "serial": i, "playerIndex": 0} for i in range(prize_count)],
    }


def _make_current(*, your_index, players, turn=1, turn_action_count=1, first_player=0, result=-1):
    return {
        "energyAttached": False,
        "firstPlayer": first_player,
        "looking": None,
        "players": players,
        "result": result,
        "retreated": False,
        "stadium": [],
        "stadiumPlayed": False,
        "supporterPlayed": False,
        "turn": turn,
        "turnActionCount": turn_action_count,
        "yourIndex": your_index,
    }


def _make_observation(
    *,
    select=None,
    current=None,
    step=1,
    logs=None,
    search_begin_input="OPAQUE_ENGINE_TOKEN_ABC123",
    remaining_overage_time=600,
):
    return {
        "current": current,
        "logs": logs if logs is not None else [],
        "remainingOverageTime": remaining_overage_time,
        "search_begin_input": search_begin_input,
        "select": select,
        "step": step,
    }


def _decision_observation(
    *,
    acting_seat=0,
    options=None,
    select_type=0,
    self_hand=None,
    opp_hand=None,
    self_prize=6,
    opp_prize=6,
    self_active=None,
    opp_active=None,
):
    options = options if options is not None else [{"type": 14}]
    self_player = _make_player(hand=self_hand, hand_count=len(self_hand or []), prize_count=self_prize, active=self_active)
    opp_player = _make_player(hand=opp_hand, hand_count=0 if opp_hand is None else len(opp_hand), prize_count=opp_prize, active=opp_active)
    players = [None, None]
    players[acting_seat] = self_player
    players[1 - acting_seat] = opp_player
    current = _make_current(your_index=acting_seat, players=players)
    select = _make_select(select_type=select_type, options=options)
    return _make_observation(select=select, current=current)


class _FakeWriter:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    def write(self, record: dict[str, Any]) -> None:
        self.records.append(record)


class _FakeDoneEnvironment:
    """Minimal fake mirroring the deck-registration leg of the real cabt contract."""

    def __init__(self) -> None:
        self.done = True
        self.state = [{"status": "DONE"}, {"status": "DONE"}]

    def run(self, agents):
        for agent in agents:
            agent({"select": None, "current": None, "logs": [], "remainingOverageTime": 600, "search_begin_input": "x", "step": 0})
        return [[{}, {}]]


class _FakeFailingEnvironment:
    """Writes real decision records, then reports a non-DONE terminal status."""

    def __init__(self) -> None:
        self.done = True
        self.state = [{"status": "INVALID"}, {"status": "DONE"}]

    def run(self, agents):
        observation = _decision_observation()
        for agent in agents:
            agent(observation)
        return [[{}, {}]]


class _FakeDoneEnvironmentThatRaces(_FakeDoneEnvironment):
    """Simulates a concurrent process creating the destination mid-execution."""

    def __init__(self, destination: Path) -> None:
        super().__init__()
        self._destination = destination

    def run(self, agents):
        result = super().run(agents)
        self._destination.write_text("concurrently-created-by-another-process\n", encoding="utf-8")
        return result


def _temp_trace_files(directory: Path) -> list[Path]:
    return sorted(directory.glob(f"{TEMP_FILE_PREFIX}*"))


# 1. acting seat comes from current.yourIndex
def test_decision_record_seat_comes_from_current_your_index() -> None:
    writer = _FakeWriter()
    observation = _decision_observation(acting_seat=1)
    traced = make_traced_agent(lambda obs: [0], seat=0, episode_index=0, writer=writer)

    traced(observation)

    assert writer.records[0]["seat"] == 1


# 2. wrapped agent is invoked exactly once
def test_wrapped_agent_invoked_exactly_once() -> None:
    writer = _FakeWriter()
    calls = []

    def agent(obs):
        calls.append(obs)
        return [0]

    traced = make_traced_agent(agent, seat=0, episode_index=0, writer=writer)
    traced(_decision_observation())

    assert len(calls) == 1


# 3. action is returned unchanged
def test_action_returned_unchanged() -> None:
    writer = _FakeWriter()
    action = [0]
    traced = make_traced_agent(lambda obs: action, seat=0, episode_index=0, writer=writer)

    result = traced(_decision_observation())

    assert result is action


# 4. one record is emitted and flushed
def test_exactly_one_record_emitted_and_flushed(tmp_path: Path) -> None:
    output = tmp_path / "trace.jsonl"
    with TraceWriter(output) as writer:
        traced = make_traced_agent(lambda obs: [0], seat=0, episode_index=0, writer=writer)
        traced(_decision_observation())

        # Read via a second handle before the writer closes to prove the flush happened.
        lines = output.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1
        assert json.loads(lines[0])["record_type"] == "decision"


# 5. deck registration is identified correctly
def test_deck_registration_identified_when_select_is_none() -> None:
    writer = _FakeWriter()
    deck = list(range(1, 61))
    traced = make_traced_agent(lambda obs: deck, seat=0, episode_index=0, writer=writer)

    traced(_make_observation(select=None, current=None))

    record = writer.records[0]
    assert record["record_type"] == "deck_registration"
    assert record["deck_size"] == 60
    assert record["deck_card_ids"] == deck
    assert isinstance(record["deck_sha256"], str) and len(record["deck_sha256"]) == 64


# 6. decision records are identified correctly
def test_decision_record_identified_when_select_present() -> None:
    writer = _FakeWriter()
    traced = make_traced_agent(lambda obs: [0], seat=0, episode_index=0, writer=writer)

    traced(_decision_observation())

    assert writer.records[0]["record_type"] == "decision"


# 7. opponent hand is never serialized
def test_opponent_hand_never_serialized() -> None:
    observation = _decision_observation(
        acting_seat=0,
        opp_hand=[{"id": 5, "serial": 1, "playerIndex": 1}, {"id": 6, "serial": 2, "playerIndex": 1}],
    )
    record = normalize_decision_record(
        observation, [0], seat=0, episode_index=0, decision_index=0, seat_decision_index=0
    )

    opponent = record["opponent"]
    assert "hand_card_ids" not in opponent
    assert "hand" not in opponent
    assert opponent["hand_count"] == 2
    dumped = json.dumps(record)
    assert '"id": 5' not in dumped and '"id":5' not in dumped


# 8. both prize zones are count-only
def test_both_prize_zones_are_count_only() -> None:
    observation = _decision_observation(acting_seat=0, self_prize=3, opp_prize=4)
    record = normalize_decision_record(
        observation, [0], seat=0, episode_index=0, decision_index=0, seat_decision_index=0
    )

    assert record["self"]["prize_count"] == 3
    assert record["opponent"]["prize_count"] == 4
    assert "prize" not in record["self"]
    assert "prize" not in record["opponent"]


# 9. search_begin_input is recursively absent
def test_search_begin_input_recursively_absent() -> None:
    observation = _decision_observation()
    observation["search_begin_input"] = "SUPER_SECRET_RESUME_TOKEN"
    record = normalize_decision_record(
        observation, [0], seat=0, episode_index=0, decision_index=0, seat_decision_index=0
    )

    assert find_forbidden_keys(record) == []
    assert "search_begin_input" not in _collect_keys(record)
    assert "SUPER_SECRET_RESUME_TOKEN" not in json.dumps(record)


# 10. logs is recursively absent
def test_logs_recursively_absent() -> None:
    observation = _decision_observation()
    observation["logs"] = [{"type": "Result", "reason": 3}]
    record = normalize_decision_record(
        observation, [0], seat=0, episode_index=0, decision_index=0, seat_decision_index=0
    )

    assert "logs" not in _collect_keys(record)


# 11. remainingOverageTime is recursively absent
def test_remaining_overage_time_recursively_absent() -> None:
    observation = _decision_observation()
    observation["remainingOverageTime"] = 42
    record = normalize_decision_record(
        observation, [0], seat=0, episode_index=0, decision_index=0, seat_decision_index=0
    )

    assert "remainingOverageTime" not in _collect_keys(record)


def test_forbidden_observation_keys_constant_matches_spec() -> None:
    assert set(FORBIDDEN_OBSERVATION_KEYS) == {"search_begin_input", "logs", "remainingOverageTime"}


# 12. raw unknown option values are not serialized
def test_unknown_option_raw_values_not_serialized() -> None:
    option = {"type": 7, "index": 6, "mysterySecretField": "raw-value-should-not-appear"}

    normalized = normalize_option(option, 0)

    assert "mysterySecretField" not in normalized["fields"]
    assert "raw-value-should-not-appear" not in json.dumps(normalized)
    assert "mysterySecretField" in normalized["unknown_keys"]


# 13. unknown option key names are sorted and retained
def test_unknown_option_key_names_sorted_and_retained() -> None:
    option = {"type": 1, "zeta": 1, "alpha": 2}

    normalized = normalize_option(option, 0)

    assert normalized["unknown_keys"] == ["alpha", "zeta"]


# 14. known option scalar fields are retained
def test_known_option_scalar_fields_retained() -> None:
    option = {"type": 8, "area": 2, "index": 0, "inPlayArea": 4, "inPlayIndex": 0}

    normalized = normalize_option(option, 2)

    assert normalized["option_index"] == 2
    assert normalized["type"] == 8
    assert normalized["type_name"] == "ATTACH"
    assert normalized["fields"] == {"area": 2, "index": 0, "inPlayArea": 4, "inPlayIndex": 0}
    assert normalized["unknown_keys"] == []


# 15. option indices preserve original legal-option ordering
def test_option_indices_preserve_original_legal_option_order() -> None:
    options = [{"type": 14}, {"type": 7, "index": 3}, {"type": 13, "attackId": 99}]
    observation = _decision_observation(options=options)

    record = normalize_decision_record(
        observation, [0], seat=0, episode_index=0, decision_index=0, seat_decision_index=0
    )

    assert [o["option_index"] for o in record["options"]] == [0, 1, 2]
    assert [o["type"] for o in record["options"]] == [14, 7, 13]


# 16. multi-select actions remain lists and preserve order
def test_multiselect_action_remains_list_and_preserves_order() -> None:
    options = [{"type": 8}, {"type": 8}, {"type": 8}, {"type": 14}]
    observation = _decision_observation(options=options, select_type=0)
    action = [3, 1, 2]

    record = normalize_decision_record(
        observation, action, seat=0, episode_index=0, decision_index=0, seat_decision_index=0
    )

    assert record["action"] == [3, 1, 2]
    assert isinstance(record["action"], list)


# 17. wall-clock time and random UUIDs are absent
def test_no_wallclock_or_uuid_keys_present() -> None:
    observation = _decision_observation()
    record = normalize_decision_record(
        observation, [0], seat=0, episode_index=0, decision_index=0, seat_decision_index=0
    )

    present_keys = _collect_keys(record)
    assert present_keys.isdisjoint(_BANNED_TIME_KEYS)


# 18. identical input produces byte-equivalent normalized JSON
def test_identical_input_produces_byte_equivalent_json() -> None:
    observation = _decision_observation()
    kwargs = dict(seat=0, episode_index=0, decision_index=0, seat_decision_index=0)

    first = normalize_decision_record(copy.deepcopy(observation), [0], **kwargs)
    second = normalize_decision_record(copy.deepcopy(observation), [0], **kwargs)

    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


# 19. normalizers do not mutate input
def test_normalizers_do_not_mutate_input() -> None:
    observation = _decision_observation()
    action = [0]
    observation_copy = copy.deepcopy(observation)
    action_copy = copy.deepcopy(action)

    normalize_decision_record(
        observation, action, seat=0, episode_index=0, decision_index=0, seat_decision_index=0
    )

    assert observation == observation_copy
    assert action == action_copy


# 20. output collision fails closed
def test_output_collision_fails_closed(tmp_path: Path) -> None:
    output = tmp_path / "trace.jsonl"
    output.write_text("not-empty-preexisting-content\n", encoding="utf-8")

    def forbidden_make(*args: object, **kwargs: object) -> Any:
        raise AssertionError("make_environment must not be called when the output collides")

    with pytest.raises(TraceOutputExistsError):
        run_trace(
            deck_a_path=REPOSITORY_ROOT / "deck.csv",
            deck_b_path=REPOSITORY_ROOT / "deck.csv",
            agent_a_name="random",
            agent_b_name="deterministic",
            matches=1,
            base_seed=0,
            output_path=output,
            overwrite=False,
            make_environment=forbidden_make,
        )

    assert output.read_text(encoding="utf-8") == "not-empty-preexisting-content\n"


# 21. overwrite works
def test_overwrite_replaces_existing_output(tmp_path: Path) -> None:
    output = tmp_path / "trace.jsonl"
    output.write_text("not-empty-preexisting-content\n", encoding="utf-8")

    result = run_trace(
        deck_a_path=REPOSITORY_ROOT / "deck.csv",
        deck_b_path=REPOSITORY_ROOT / "deck.csv",
        agent_a_name="random",
        agent_b_name="deterministic",
        matches=1,
        base_seed=0,
        output_path=output,
        overwrite=True,
        make_environment=lambda *args, **kwargs: _FakeDoneEnvironment(),
    )

    assert result["matches"] == 1
    lines = output.read_text(encoding="utf-8").splitlines()
    assert lines, "overwrite must produce a fresh trace"
    for line in lines:
        parsed = json.loads(line)
        assert parsed["record_type"] == "deck_registration"
    assert output.read_text(encoding="utf-8") != "not-empty-preexisting-content\n"
    assert _temp_trace_files(tmp_path) == []


def test_actual_trace_manifest_has_provenance_without_private_paths(tmp_path: Path) -> None:
    output = tmp_path / "trace.jsonl"
    manifest = tmp_path / "trace-manifest.json"

    result = run_trace(
        deck_a_path=REPOSITORY_ROOT / "deck.csv",
        deck_b_path=REPOSITORY_ROOT / "deck.csv",
        agent_a_name="rule",
        agent_b_name="deterministic",
        matches=1,
        base_seed=123,
        output_path=output,
        manifest_path=manifest,
        make_environment=lambda *args, **kwargs: _FakeDoneEnvironment(),
    )

    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert result["manifest"] == payload
    assert payload["actual"] is True
    assert payload["environment_loader"] == "kaggle_environments.make"
    assert payload["environment_name"] == "cabt"
    assert payload["config"] == {"agent_a": "rule", "agent_b": "deterministic", "base_seed": 123, "matches": 1}
    assert len(payload["trace_sha256"]) == len(payload["config_hash"]) == 64
    assert str(tmp_path) not in json.dumps(payload, sort_keys=True)


# Atomic output publication: overwrite=True + invalid deck preserves the old
# destination byte-for-byte, and leaves no temporary file behind.
def test_overwrite_true_invalid_deck_preserves_old_destination_byte_for_byte(tmp_path: Path) -> None:
    output = tmp_path / "trace.jsonl"
    original_content = "not-empty-preexisting-content\n"
    output.write_text(original_content, encoding="utf-8")
    invalid_deck = tmp_path / "invalid-deck.csv"
    invalid_deck.write_text("1\n2\n", encoding="utf-8")  # only 2 cards, not 60

    def forbidden_make(*args: object, **kwargs: object) -> Any:
        raise AssertionError("make_environment must not be called when deck validation fails")

    with pytest.raises(Exception):
        run_trace(
            deck_a_path=invalid_deck,
            deck_b_path=REPOSITORY_ROOT / "deck.csv",
            agent_a_name="random",
            agent_b_name="deterministic",
            matches=1,
            base_seed=0,
            output_path=output,
            overwrite=True,
            make_environment=forbidden_make,
        )

    assert output.read_text(encoding="utf-8") == original_content
    assert _temp_trace_files(tmp_path) == []


# Atomic output publication: overwrite=True + a non-DONE episode (that already
# wrote real records to a temp file) preserves the old destination byte-for-byte.
def test_overwrite_true_failed_episode_preserves_old_destination_byte_for_byte(tmp_path: Path) -> None:
    output = tmp_path / "trace.jsonl"
    original_content = "not-empty-preexisting-content\n"
    output.write_text(original_content, encoding="utf-8")

    with pytest.raises(TraceExecutionError):
        run_trace(
            deck_a_path=REPOSITORY_ROOT / "deck.csv",
            deck_b_path=REPOSITORY_ROOT / "deck.csv",
            agent_a_name="random",
            agent_b_name="deterministic",
            matches=1,
            base_seed=0,
            output_path=output,
            overwrite=True,
            make_environment=lambda *args, **kwargs: _FakeFailingEnvironment(),
        )

    assert output.read_text(encoding="utf-8") == original_content
    assert _temp_trace_files(tmp_path) == []


# The same failed episode with no preexisting destination leaves no final
# destination at all (no partial trace exposed under the requested name).
def test_failed_episode_with_no_preexisting_destination_leaves_no_final_destination(
    tmp_path: Path,
) -> None:
    output = tmp_path / "trace.jsonl"
    assert not output.exists()

    with pytest.raises(TraceExecutionError):
        run_trace(
            deck_a_path=REPOSITORY_ROOT / "deck.csv",
            deck_b_path=REPOSITORY_ROOT / "deck.csv",
            agent_a_name="random",
            agent_b_name="deterministic",
            matches=1,
            base_seed=0,
            output_path=output,
            overwrite=False,
            make_environment=lambda *args, **kwargs: _FakeFailingEnvironment(),
        )

    assert not output.exists()
    assert _temp_trace_files(tmp_path) == []


# Handled failures always remove the temporary file, regardless of where in
# the pipeline the failure occurred (here: environment/dependency creation).
def test_handled_environment_creation_failure_removes_temporary_file(tmp_path: Path) -> None:
    output = tmp_path / "trace.jsonl"

    def raising_make(*args: object, **kwargs: object) -> Any:
        raise RuntimeError("simulated environment creation failure")

    with pytest.raises(RuntimeError):
        run_trace(
            deck_a_path=REPOSITORY_ROOT / "deck.csv",
            deck_b_path=REPOSITORY_ROOT / "deck.csv",
            agent_a_name="random",
            agent_b_name="deterministic",
            matches=1,
            base_seed=0,
            output_path=output,
            overwrite=False,
            make_environment=raising_make,
        )

    assert not output.exists()
    assert _temp_trace_files(tmp_path) == []


# Successful execution leaves no temporary files (fresh destination, no
# preexisting file to overwrite).
def test_successful_execution_leaves_no_temporary_files(tmp_path: Path) -> None:
    output = tmp_path / "trace.jsonl"

    run_trace(
        deck_a_path=REPOSITORY_ROOT / "deck.csv",
        deck_b_path=REPOSITORY_ROOT / "deck.csv",
        agent_a_name="random",
        agent_b_name="deterministic",
        matches=1,
        base_seed=0,
        output_path=output,
        overwrite=False,
        make_environment=lambda *args, **kwargs: _FakeDoneEnvironment(),
    )

    assert output.exists()
    assert _temp_trace_files(tmp_path) == []


# overwrite=False continues to preserve existing output even when the
# collision only appears *during* execution (a same-name file created by
# another process while this run was still in progress).
def test_overwrite_false_rechecks_collision_immediately_before_publication(tmp_path: Path) -> None:
    output = tmp_path / "trace.jsonl"
    assert not output.exists()

    with pytest.raises(TraceOutputExistsError):
        run_trace(
            deck_a_path=REPOSITORY_ROOT / "deck.csv",
            deck_b_path=REPOSITORY_ROOT / "deck.csv",
            agent_a_name="random",
            agent_b_name="deterministic",
            matches=1,
            base_seed=0,
            output_path=output,
            overwrite=False,
            make_environment=lambda *args, **kwargs: _FakeDoneEnvironmentThatRaces(output),
        )

    assert output.read_text(encoding="utf-8") == "concurrently-created-by-another-process\n"
    assert _temp_trace_files(tmp_path) == []


# 22. malformed observations fail clearly without leaking raw contents
def test_malformed_observation_missing_option_list_fails_clearly() -> None:
    observation = _decision_observation()
    del observation["select"]["option"]

    with pytest.raises(MalformedObservationError) as excinfo:
        normalize_decision_record(
            observation, [0], seat=0, episode_index=0, decision_index=0, seat_decision_index=0
        )

    assert "OPAQUE_ENGINE_TOKEN_ABC123" not in str(excinfo.value)
    assert len(str(excinfo.value)) < 200


def test_malformed_observation_missing_current_fails_clearly() -> None:
    observation = _decision_observation()
    observation["current"] = None

    with pytest.raises(MalformedObservationError):
        normalize_decision_record(
            observation, [0], seat=0, episode_index=0, decision_index=0, seat_decision_index=0
        )


def test_normalize_player_view_never_exposes_opponent_hand_even_if_present() -> None:
    player = _make_player(hand=[{"id": 1, "serial": 1, "playerIndex": 1}], hand_count=1)

    view = normalize_player_view(player, is_self=False)

    assert "hand_card_ids" not in view
    assert view["hand_count"] == 1


def test_public_trace_redacts_actor_hand_and_candidate_identity() -> None:
    observation = _decision_observation(
        self_hand=[{"id": 675, "serial": 0, "playerIndex": 0}],
        options=[{"type": 7, "index": 0}],
    )
    record = normalize_decision_record(
        observation, [0], seat=0, episode_index=0, decision_index=0, seat_decision_index=0
    )
    encoded = json.dumps(record, sort_keys=True)
    assert "hand_card_ids" not in encoded
    assert "675" not in encoded
    assert "card_id" not in encoded
    assert "private_source" not in encoded
    assert record["public_candidate_attestations"]
    assert set(record["public_candidate_attestations"][0]) == {
        "candidate_public_id", "semantic_operation"
    }


def test_public_trace_resolves_toolcard_through_verified_public_board() -> None:
    host = {
        "id": 201,
        "serial": 2001,
        "playerIndex": 0,
        "hp": 100,
        "maxHp": 100,
        "appearThisTurn": False,
        "energies": [],
        "energyCards": [],
        "tools": [
            {
                "id": 301,
                "serial": 3001,
                "playerIndex": 0,
                "hp": 0,
                "maxHp": 0,
                "appearThisTurn": False,
                "energies": [],
                "energyCards": [],
                "tools": [],
                "preEvolution": [],
            }
        ],
        "preEvolution": [],
    }
    observation = _decision_observation(
        select_type=2,
        options=[
            {
                "type": 4,
                "area": 4,
                "index": 0,
                "playerIndex": 0,
                "toolIndex": 0,
            }
        ],
        self_active=[host],
    )
    observation["select"]["context"] = 28

    record = normalize_decision_record(
        observation,
        [0],
        seat=0,
        episode_index=0,
        decision_index=0,
        seat_decision_index=0,
    )

    assert record["public_candidate_attestations"][0]["semantic_operation"] == "TOOL_CARD"
    encoded = json.dumps(record["public_candidate_attestations"], sort_keys=True)
    assert all(raw_value not in encoded for raw_value in ("301", "3001", "toolIndex"))


def test_actor_visible_attestation_writer_is_separate_from_public_trace(tmp_path: Path) -> None:
    path = tmp_path / "private-binding.jsonl"
    payload = {
        "teacher_id": "TR-000010", "canonical_rule_id": "CR-000032",
        "candidate_public_id": "public-id", "condition_evaluated": True,
        "condition_result": "TR000010_MATCH", "binding_status": "TR000010_MATCH",
        "binding_reason": "TR000010_DUPLICATE_LINE_CHECK",
        "binder_version": "fixture", "provenance_category": "actor-visible-redacted-offline-only",
    }
    with ActorVisibleAttestationWriter(path) as writer:
        writer.write(payload)
    assert json.loads(path.read_text(encoding="utf-8")) == payload


@pytest.fixture(scope="module")
def real_cabt_smoke_records(tmp_path_factory: pytest.TempPathFactory) -> list[dict[str, Any]]:
    output = tmp_path_factory.mktemp("cabt-trace-smoke") / "smoke.jsonl"
    run_trace(
        deck_a_path=REPOSITORY_ROOT / "deck.csv",
        deck_b_path=REPOSITORY_ROOT / "deck.csv",
        agent_a_name="random",
        agent_b_name="deterministic",
        matches=2,
        base_seed=100,
        output_path=output,
        overwrite=False,
    )
    return [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]


_CABT_AVAILABLE = importlib.util.find_spec("kaggle_environments") is not None


# 23. real cabt smoke creates valid JSONL records
@pytest.mark.skipif(not _CABT_AVAILABLE, reason="kaggle-environments with cabt is not installed")
def test_real_cabt_smoke_creates_valid_jsonl_records(real_cabt_smoke_records: list[dict[str, Any]]) -> None:
    assert len(real_cabt_smoke_records) > 0
    for record in real_cabt_smoke_records:
        assert record["schema_version"] == 1
        assert record["source"] == "official_cabt_agent_observation"


# 24. every smoke record satisfies privacy invariants
@pytest.mark.skipif(not _CABT_AVAILABLE, reason="kaggle-environments with cabt is not installed")
def test_real_cabt_smoke_records_satisfy_privacy_invariants(
    real_cabt_smoke_records: list[dict[str, Any]]
) -> None:
    for record in real_cabt_smoke_records:
        assert find_forbidden_keys(record) == []
        if record["record_type"] == "decision":
            assert "hand_card_ids" not in record["opponent"]
            assert "hand" not in record["opponent"]
            assert "prize" not in record["self"]
            assert "prize" not in record["opponent"]


# 25. real smoke contains both deck-registration and decision records
@pytest.mark.skipif(not _CABT_AVAILABLE, reason="kaggle-environments with cabt is not installed")
def test_real_cabt_smoke_contains_both_record_types(
    real_cabt_smoke_records: list[dict[str, Any]]
) -> None:
    record_types = {record["record_type"] for record in real_cabt_smoke_records}
    assert record_types == {"deck_registration", "decision"}
