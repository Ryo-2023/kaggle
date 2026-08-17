"""Adversarial privacy tests for the production runtime trace surface."""

from __future__ import annotations

from dataclasses import asdict, fields
import hashlib
import json
from pathlib import Path
from types import MappingProxyType

import pytest

from mage_ptcg.meta_specialist.runtime import RuntimeContractError, RuntimeDecisionTraceV2
from tests.meta_specialist.test_runtime import _observation, _runtime


_PRIVATE_DIGEST = "f" * 64
_PRIVATE_SENTINELS = ("998877", "445566", _PRIVATE_DIGEST)


def _representable_trace(tmp_path: Path) -> RuntimeDecisionTraceV2:
    runtime, _policy, _cards = _runtime(tmp_path)
    runtime({"select": None})
    observation = _observation()
    observation["select"] = {  # type: ignore[index]
        "context": 39, "contextCard": None, "deck": None, "effect": None,
        "maxCount": 1, "minCount": 1,
        "option": [{"type": 0, "number": 1}, {"type": 0, "number": 2}],
        "remainDamageCounter": 0, "remainEnergyCost": 0, "type": 8,
    }
    runtime(observation)
    trace = runtime.traces[0]
    assert trace.trace_variant == "public-v1-representable"
    return trace


def _two_action_trace(tmp_path: Path) -> RuntimeDecisionTraceV2:
    runtime, _policy, _cards = _runtime(tmp_path)
    runtime({"select": None})
    observation = _observation()
    observation["select"] = {  # type: ignore[index]
        "context": 39, "contextCard": None, "deck": None, "effect": None,
        "maxCount": 2, "minCount": 2,
        "option": [{"type": 0, "number": 1}, {"type": 0, "number": 2}],
        "remainDamageCounter": 0, "remainEnergyCost": 0, "type": 8,
    }
    runtime(observation)
    return runtime.traces[0]


def _trace_fields(trace: RuntimeDecisionTraceV2) -> dict[str, object]:
    return {
        "trace_variant": "public-v1-representable",
        "policy_identity": trace.policy_identity,
        "candidate_class": trace.candidate_class,
        "selection_type": trace.selection_type,
        "selection_context": trace.selection_context,
        "min_count": trace.min_count,
        "max_count": trace.max_count,
        "order_semantics": trace.order_semantics,
        "selected_count": trace.selected_count,
        "complete_action_log_probability": trace.complete_action_log_probability,
    }


def _recompute_public_decision_identity(payload: dict[str, object]) -> None:
    identity = {
        "schema_version": payload["schema_version"],
        "public_state_digest": payload["public_state_digest"],
        "public_action_set_digest": payload["public_action_set_digest"],
        "selection_type": payload["selection_type"],
        "selection_context": payload["selection_context"],
        "min_count": payload["min_count"],
        "max_count": payload["max_count"],
        "order_semantics": payload["order_semantics"],
    }
    canonical = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    payload["public_decision_identity"] = hashlib.sha256(
        b"mage_ptcg.meta_specialist.complete_action_trace:v1\0" + canonical
    ).hexdigest()


def _private_injection() -> dict[str, object]:
    return {
        "card_id": 998877,
        "serial": 445566,
        "private_digest": _PRIVATE_DIGEST,
        "option_index": 1,
        "local_action_id": "e" * 64,
    }


def _assert_no_private_sentinels(value: object) -> None:
    rendered = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    assert all(sentinel not in rendered for sentinel in _PRIVATE_SENTINELS)


def test_runtime_trace_has_no_private_identity_or_index_field() -> None:
    names = {item.name for item in fields(RuntimeDecisionTraceV2)}
    forbidden = {"observation", "local_action_id", "action_key", "decision_digest", "option_indices", "card_id", "serial", "private_state"}
    assert not names.intersection(forbidden)


def test_public_projection_is_owned_canonical_bytes_and_every_read_is_detached(tmp_path: Path) -> None:
    trace = _representable_trace(tmp_path)
    first = trace.public_trace
    assert first is not None
    first["selected_public_actions"].append(_private_injection())  # type: ignore[union-attr]
    _assert_no_private_sentinels(trace.to_payload())

    payload = trace.to_payload()
    payload["public_projection"]["selected_public_actions"].append(_private_injection())  # type: ignore[index,union-attr]
    _assert_no_private_sentinels(trace.to_payload())
    _assert_no_private_sentinels(asdict(trace))
    assert repr(trace) == "RuntimeDecisionTraceV2(<redacted>)"


def test_trace_constructor_rejects_arbitrary_mutable_or_nested_proxy_projection(tmp_path: Path) -> None:
    trace = _representable_trace(tmp_path)
    payload = trace.to_payload()["public_projection"]
    payload["selected_public_actions"].append(MappingProxyType(_private_injection()))  # type: ignore[index,union-attr]
    common = {
        "trace_variant": "public-v1-representable",
        "policy_identity": "a" * 64,
        "candidate_class": "checkpointed_specialist",
        "selection_type": 8, "selection_context": 39,
        "min_count": 1, "max_count": 1, "order_semantics": "unordered_set",
        "selected_count": 1, "complete_action_log_probability": -1.0,
    }
    with pytest.raises((RuntimeContractError, TypeError, ValueError)):
        RuntimeDecisionTraceV2(public_trace=payload, **common)  # type: ignore[call-arg]
    with pytest.raises(RuntimeContractError, match="private"):
        RuntimeDecisionTraceV2.from_public_projection(public_trace=payload, **common)


def test_trace_rejects_unknown_nested_selected_action_payload(tmp_path: Path) -> None:
    trace = _representable_trace(tmp_path)
    payload = trace.public_trace
    assert payload is not None
    payload["selected_public_actions"] = [
        {"hiddenRef": "445566", "mystery": "998877"}
    ]
    with pytest.raises(RuntimeContractError, match="selected public action"):
        RuntimeDecisionTraceV2.from_public_projection(
            public_trace=payload, **_trace_fields(trace)
        )


def test_trace_rejects_action_schema_that_disagrees_with_root_selection(tmp_path: Path) -> None:
    trace = _representable_trace(tmp_path)
    payload = trace.public_trace
    assert payload is not None
    payload["selection_context"] = 40
    _recompute_public_decision_identity(payload)
    with pytest.raises(RuntimeContractError, match="root selection"):
        RuntimeDecisionTraceV2.from_public_projection(
            public_trace=payload,
            **{**_trace_fields(trace), "selection_context": 40},
        )


def test_unordered_trace_actions_are_unique_and_canonically_sorted(tmp_path: Path) -> None:
    trace = _two_action_trace(tmp_path)
    payload = trace.public_trace
    assert payload is not None
    actions = payload["selected_public_actions"]
    assert len(actions) == 2  # type: ignore[arg-type]
    payload["selected_public_actions"] = list(reversed(actions))  # type: ignore[arg-type]
    with pytest.raises(RuntimeContractError, match="canonical order"):
        RuntimeDecisionTraceV2.from_public_projection(
            public_trace=payload, **_trace_fields(trace)
        )

    payload = trace.public_trace
    assert payload is not None
    first = payload["selected_public_actions"][0]  # type: ignore[index]
    payload["selected_public_actions"] = [first, first]
    with pytest.raises(RuntimeContractError, match="unique"):
        RuntimeDecisionTraceV2.from_public_projection(
            public_trace=payload, **_trace_fields(trace)
        )


def test_trace_recomputes_declared_public_decision_identity(tmp_path: Path) -> None:
    trace = _representable_trace(tmp_path)
    payload = trace.public_trace
    assert payload is not None
    payload["public_decision_identity"] = "0" * 64
    with pytest.raises(RuntimeContractError, match="public_decision_identity"):
        RuntimeDecisionTraceV2.from_public_projection(
            public_trace=payload, **_trace_fields(trace)
        )


def test_legitimate_frozen_public_projection_round_trips_exactly(tmp_path: Path) -> None:
    trace = _representable_trace(tmp_path)
    first = trace.to_payload()
    second = trace.to_payload()
    assert first == second
    assert json.dumps(first, sort_keys=True, separators=(",", ":")) == json.dumps(second, sort_keys=True, separators=(",", ":"))
