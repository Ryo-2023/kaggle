"""Privacy-checked trajectory records for legal-action actor-critic training."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Callable, Iterable

from mage_ptcg.meta_specialist.cabt_json_contract_v1 import is_ordered_selection
from mage_ptcg.student.dataset import RuleBCExample
from mage_ptcg.student.features import serialized_action_features, state_features_payload
from mage_ptcg.student.features import ACTION_FEATURE_DIM, FEATURE_VERSION, STATE_FEATURE_DIM


OUTCOMES = {"WIN": 1.0, "LOSS": -1.0, "DRAW": 0.0, "UNKNOWN": 0.0}
VALID_SPLITS = {"train", "validation", "test", "opponent_holdout", "deck_holdout", "teacher_policy_holdout"}


def vocabulary_hash() -> str:
    """Bind all policy artifacts to the feature vocabulary, not just shapes."""
    import hashlib

    payload = {
        "feature_version": FEATURE_VERSION,
        "state_dimension": STATE_FEATURE_DIM,
        "action_dimension": ACTION_FEATURE_DIM,
        "history_dimension": 32,
        "history_encoding": "sha256-public-event-sign-v1",
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


class PolicyDataError(ValueError):
    """A source dataset cannot be used as a policy-learning trajectory."""


def _history_features(history: tuple[str, ...], *, dimension: int = 32, maximum: int = 32) -> list[list[float]]:
    """Hash visible events only; input ordering is retained for the GRU."""
    import hashlib

    values: list[list[float]] = []
    for event in history[-maximum:]:
        digest = hashlib.sha256(event.encode("utf-8")).digest()
        row = [0.0] * dimension
        for offset in range(0, min(len(digest), 16), 2):
            index = digest[offset] % dimension
            row[index] += 1.0 if digest[offset + 1] & 1 else -1.0
        values.append(row)
    return values or [[0.0] * dimension]


@dataclass(frozen=True, slots=True)
class PolicyLearningExample:
    """One actor-visible legal-action decision with a terminal episode target."""

    episode_id: str
    decision_id: str
    split: str
    state: tuple[float, ...]
    history: tuple[tuple[float, ...], ...]
    actions: tuple[tuple[float, ...], ...]
    target_index: int
    terminal_return: float
    opponent_family: str
    teacher_trust: str
    behavior_log_probability: float | None
    decision_index: int
    deck_fingerprint: str
    action_type: str
    rule_proposal_index: int | None

    @property
    def family_target(self) -> str:
        return self.opponent_family or "UNKNOWN"


def from_record(row: dict[str, Any], *, default_decision_index: int = 0) -> PolicyLearningExample:
    try:
        example = RuleBCExample.from_dict(row["rule_bc_example"])
        split = str(row["split"])
        episode_id = str(row["episode_id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise PolicyDataError("record lacks a valid RuleBC example or split") from exc
    if split not in VALID_SPLITS or not episode_id:
        raise PolicyDataError("record has an invalid split or episode id")
    if example.fallback_used:
        raise PolicyDataError("Rule v0 fallback decisions are not actor-critic targets")
    try:
        ordered = is_ordered_selection(example.selection_type, example.selection_context)
    except ValueError as exc:
        raise PolicyDataError("record has an unknown CABT selection schema") from exc
    if ordered:
        raise PolicyDataError(
            "candidate-wise actor-critic cannot represent ordered Skill labels"
        )
    if example.min_count != 1 or example.max_count != 1:
        raise PolicyDataError("actor-critic currently requires a single-action prompt")
    action_rows = [tuple(action_features_from_legal(action)) for action in example.legal_actions]
    targets = [index for index, action in enumerate(example.legal_actions) if action["digest"] in example.target_action_digests]
    if len(targets) != 1:
        # Multi-selection prompts require an autoregressive action-set policy;
        # they are retained by BC but excluded from this single-action actor.
        raise PolicyDataError("actor-critic currently requires exactly one target action")
    outcome = str(row.get("candidate_outcome", "UNKNOWN")).upper()
    if outcome not in OUTCOMES:
        outcome = "UNKNOWN"
    behavior = row.get("behavior_log_probability")
    if behavior is not None and not isinstance(behavior, (int, float)):
        raise PolicyDataError("behavior log probability must be numeric or absent")
    try:
        decision_index = int(row.get("decision_index", default_decision_index))
    except (TypeError, ValueError) as exc:
        raise PolicyDataError("decision index is invalid") from exc
    proposal = row.get("rule_proposal_digests")
    proposal_index: int | None = None
    if proposal is not None:
        if not isinstance(proposal, list) or len(proposal) != 1 or not isinstance(proposal[0], str):
            raise PolicyDataError("rule proposal must be one legal action digest")
        proposal_matches = [index for index, action in enumerate(example.legal_actions) if action["digest"] == proposal[0]]
        if len(proposal_matches) != 1:
            raise PolicyDataError("rule proposal is not a legal action")
        proposal_index = proposal_matches[0]
    return PolicyLearningExample(
        episode_id=episode_id,
        decision_id=str(row.get("state_fingerprint") or example.example_id),
        split=split,
        state=tuple(state_features_payload(example.public_state, example.own_private_state, example.visible_history)),
        history=tuple(tuple(item) for item in _history_features(example.visible_history)),
        actions=tuple(action_rows),
        target_index=targets[0],
        terminal_return=OUTCOMES[outcome],
        opponent_family=str(row.get("family_id") or row.get("opponent_type") or "UNKNOWN"),
        teacher_trust=str(row.get("teacher_trust") or "UNKNOWN"),
        behavior_log_probability=float(behavior) if behavior is not None else None,
        decision_index=decision_index,
        deck_fingerprint=example.deck_fingerprint,
        action_type=str(example.selection_type),
        rule_proposal_index=proposal_index,
    )


def action_features_from_legal(action: dict[str, Any]) -> list[float]:
    """Rebuild the stable action vector from persisted canonical payload."""
    from mage_ptcg.decision_state import DecisionStateError

    payload = action.get("payload")
    if not isinstance(payload, dict):
        raise PolicyDataError("legal action payload is missing")
    try:
        digest = action["digest"]
        return serialized_action_features(payload, digest=digest)
    except (KeyError, TypeError, IndexError, ValueError, DecisionStateError) as exc:
        raise PolicyDataError("legal action payload is invalid") from exc


def load_examples(path: str | Path, *, splits: Iterable[str] | None = None,
                  on_progress: Callable[[int, int], None] | None = None) -> list[PolicyLearningExample]:
    allowed = set(splits) if splits is not None else VALID_SPLITS
    if not allowed.issubset(VALID_SPLITS):
        raise PolicyDataError("requested split is unknown")
    values: list[PolicyLearningExample] = []
    per_episode_index: dict[str, int] = {}
    source = Path(path); total_bytes = source.stat().st_size
    with source.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            # Ask the buffered reader for its byte offset instead of
            # re-encoding every line; the dataset is ~1 GiB and the encode
            # cost dominated the load otherwise.
            if on_progress is not None and (line_number % 128 == 0):
                on_progress(min(handle.buffer.tell(), total_bytes), total_bytes)
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise PolicyDataError(f"invalid JSONL at line {line_number}") from exc
            if not isinstance(row, dict) or row.get("split") not in allowed:
                continue
            episode_id = str(row.get("episode_id", ""))
            index = per_episode_index.get(episode_id, 0)
            try:
                value = from_record(row, default_decision_index=index)
            except PolicyDataError:
                # Optional/multi-select prompts are explicitly not fitted by
                # this policy head, never silently converted into a target.
                continue
            per_episode_index[episode_id] = index + 1
            values.append(value)
    if on_progress is not None:
        on_progress(total_bytes, total_bytes)
    if not values:
        raise PolicyDataError("no usable single-action examples")
    return sorted(values, key=lambda item: (item.episode_id, item.decision_index, item.decision_id))
