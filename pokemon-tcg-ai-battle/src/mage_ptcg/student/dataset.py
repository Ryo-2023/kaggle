"""Validated JSONL dataset contract for Rule Agent v0 behavior cloning."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

from agents.rule_agent import choose_rule_indices, rank_rule_indices
from mage_ptcg.decision_state import DecisionStateError, build_decision_state
from mage_ptcg.meta_specialist.cabt_json_contract_v1 import is_ordered_selection
from mage_ptcg.observability.cabt_trace import FORBIDDEN_OBSERVATION_KEYS, canonical_deck_sha256


DATASET_SCHEMA_VERSION = "rule-bc-v1"


class DatasetValidationError(ValueError):
    """Raised when a Student dataset record violates the privacy or identity contract."""


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _redact_identifier(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


def _contains_forbidden_key(value: object) -> bool:
    if isinstance(value, dict):
        return any(key in FORBIDDEN_OBSERVATION_KEYS or _contains_forbidden_key(item) for key, item in value.items())
    if isinstance(value, list):
        return any(_contains_forbidden_key(item) for item in value)
    return False


@dataclass(frozen=True, slots=True)
class RuleBCExample:
    """One decision and its Rule v0 target, persisted as one JSONL object."""

    schema_version: str
    example_id: str
    source_id: str
    public_state: dict[str, Any]
    own_private_state: dict[str, Any]
    visible_history: tuple[str, ...]
    selection_type: str | int | float | bool | None
    selection_context: str | int | float | bool | None
    min_count: int
    max_count: int
    legal_actions: tuple[dict[str, Any], ...]
    target_action_digests: tuple[str, ...]
    teacher_ranking: tuple[tuple[str, int], ...]
    fallback_used: bool
    deck_fingerprint: str
    source_revision: str
    metadata: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["visible_history"] = list(self.visible_history)
        value["legal_actions"] = list(self.legal_actions)
        value["target_action_digests"] = list(self.target_action_digests)
        value["teacher_ranking"] = [list(item) for item in self.teacher_ranking]
        return value

    @classmethod
    def from_dict(cls, value: object) -> "RuleBCExample":
        if not isinstance(value, dict):
            raise DatasetValidationError("dataset record must be an object")
        try:
            example = cls(
                schema_version=value["schema_version"],
                example_id=value["example_id"],
                source_id=value["source_id"],
                public_state=value["public_state"],
                own_private_state=value["own_private_state"],
                visible_history=tuple(value["visible_history"]),
                selection_type=value["selection_type"],
                selection_context=value["selection_context"],
                min_count=value["min_count"],
                max_count=value["max_count"],
                legal_actions=tuple(value["legal_actions"]),
                target_action_digests=tuple(value["target_action_digests"]),
                teacher_ranking=tuple((item[0], item[1]) for item in value["teacher_ranking"]),
                fallback_used=value["fallback_used"],
                deck_fingerprint=value["deck_fingerprint"],
                source_revision=value["source_revision"],
                metadata=value["metadata"],
            )
        except (KeyError, TypeError, IndexError) as exc:
            raise DatasetValidationError("dataset record has an invalid schema") from exc
        validate_example(example)
        return example


def validate_example(example: RuleBCExample) -> None:
    if example.schema_version != DATASET_SCHEMA_VERSION:
        raise DatasetValidationError("unsupported dataset schema version")
    if not example.example_id or not example.source_id:
        raise DatasetValidationError("example_id and source_id are required")
    if _contains_forbidden_key(example.public_state) or _contains_forbidden_key(example.own_private_state):
        raise DatasetValidationError("dataset record contains forbidden observation fields")
    if not (0 <= example.min_count <= example.max_count <= len(example.legal_actions)):
        raise DatasetValidationError("selection bounds are invalid")
    try:
        ordered = is_ordered_selection(
            example.selection_type, example.selection_context
        )
    except ValueError as exc:
        raise DatasetValidationError("selection schema is not recognized") from exc
    digests: list[str] = []
    for action in example.legal_actions:
        if not isinstance(action, dict) or not isinstance(action.get("digest"), str):
            raise DatasetValidationError("legal action must contain its Stable ActionKey digest")
        digests.append(action["digest"])
    # Some unresolved cabt option shapes are semantically indistinguishable
    # under the current ActionKey contract.  They stay as separate *legal
    # candidates*; a target digest then supervises the semantic equivalence
    # class and runtime resolves its final duplicate tie by legal index.
    targets = tuple(example.target_action_digests)
    if len(targets) != len(set(targets)):
        raise DatasetValidationError("teacher target contains duplicate legal actions")
    if not set(targets).issubset(digests):
        raise DatasetValidationError("teacher target is not a legal action")
    if not example.min_count <= len(targets) <= example.max_count:
        raise DatasetValidationError("teacher target violates selection bounds")
    if not ordered and targets != tuple(sorted(targets)):
        raise DatasetValidationError("unordered teacher target must be canonical")
    if any(digest not in digests or type(score) is not int for digest, score in example.teacher_ranking):
        raise DatasetValidationError("teacher ranking does not align with legal actions")
    if set(digest for digest, _score in example.teacher_ranking) != set(digests):
        raise DatasetValidationError("teacher ranking must cover every legal action")
    if not isinstance(example.metadata, dict) or any(not isinstance(value, str) for value in example.metadata.values()):
        raise DatasetValidationError("metadata must be redacted strings")


def build_rule_bc_example(
    observation: object,
    *,
    deck: list[int],
    source_id: str,
    source_revision: str,
    visible_history: tuple[str, ...] = (),
) -> RuleBCExample:
    """Build one deterministic, information-bounded Rule v0 demonstration."""
    try:
        state = build_decision_state(observation, visible_history=visible_history)
    except DecisionStateError as exc:
        raise DatasetValidationError(f"observation cannot form a decision state: {exc}") from exc
    target_indices = choose_rule_indices(observation)
    if target_indices is None:
        raise DatasetValidationError("deck-registration observations are not decision samples")
    target_index_set = set(target_indices)
    if len(target_index_set) != len(target_indices):
        raise DatasetValidationError("Rule v0 returned duplicate selection indices")
    by_index = {action.option_index: action for action in state.legal_actions}
    if not target_index_set.issubset(by_index):
        raise DatasetValidationError("Rule v0 returned a non-legal action")
    ranked = rank_rule_indices(observation)
    rank_by_index = {} if ranked is None else dict(ranked)
    # Optional auxiliary prompts deliberately have an empty Rule ranking.  A
    # neutral complete ranking records that score information was unavailable
    # without inventing a semantic target.
    if set(rank_by_index) != set(by_index):
        rank_by_index = {index: 0 for index in by_index}
        ranked = [(index, 0) for index in sorted(by_index)]
    select = observation["select"] if isinstance(observation, dict) else None
    if not isinstance(select, dict):
        raise DatasetValidationError("observation select must be a mapping")
    try:
        ordered = is_ordered_selection(select.get("type"), select.get("context"))
    except ValueError as exc:
        raise DatasetValidationError("observation select schema is not recognized") from exc
    legal_actions = tuple(
        {
            "digest": action.action_key.digest,
            "payload": action.action_key.to_canonical_payload(),
        }
        for action in state.legal_actions
    )
    selected_digests = tuple(by_index[index].action_key.digest for index in target_indices)
    if not ordered:
        selected_digests = tuple(sorted(selected_digests))
    example_core = {
        "source_id": _redact_identifier(source_id),
        "state_digest": state.digest,
        "targets": list(selected_digests),
    }
    example = RuleBCExample(
        schema_version=DATASET_SCHEMA_VERSION,
        example_id=_digest(example_core),
        source_id=_redact_identifier(source_id),
        public_state=state.actor_view.public_state,
        own_private_state=state.actor_view.own_private_state,
        visible_history=tuple(visible_history),
        selection_type=select.get("type"),
        selection_context=select.get("context"),
        min_count=select["minCount"],
        max_count=select["maxCount"],
        legal_actions=legal_actions,
        target_action_digests=selected_digests,
        teacher_ranking=tuple(
            (by_index[index].action_key.digest, score) for index, score in ranked or ()
        ),
        fallback_used=False,
        deck_fingerprint=canonical_deck_sha256(deck),
        source_revision=source_revision,
        metadata={"source_identifier": _redact_identifier(source_id)},
    )
    validate_example(example)
    return example


def write_dataset(path: str | Path, examples: Iterable[RuleBCExample]) -> int:
    destination = Path(path)
    count = 0
    with destination.open("x", encoding="utf-8") as handle:
        for example in examples:
            validate_example(example)
            handle.write(_canonical_json(example.to_dict()) + "\n")
            count += 1
    return count


def load_dataset(path: str | Path) -> list[RuleBCExample]:
    examples: list[RuleBCExample] = []
    with Path(path).open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                examples.append(RuleBCExample.from_dict(json.loads(line)))
            except (json.JSONDecodeError, DatasetValidationError) as exc:
                raise DatasetValidationError(f"invalid JSONL at line {line_number}: {exc}") from exc
    if not examples:
        raise DatasetValidationError("dataset is empty")
    return examples


def split_examples(examples: Iterable[RuleBCExample], *, validation_percent: int = 20) -> tuple[list[RuleBCExample], list[RuleBCExample]]:
    """Split by redacted source group, preventing trace/episode leakage."""
    if not 1 <= validation_percent < 100:
        raise ValueError("validation_percent must be between 1 and 99")
    train: list[RuleBCExample] = []
    validation: list[RuleBCExample] = []
    for example in examples:
        bucket = int(hashlib.sha256(example.source_id.encode("utf-8")).hexdigest()[:8], 16) % 100
        (validation if bucket < validation_percent else train).append(example)
    if not train or not validation:
        raise DatasetValidationError("group split produced an empty train or validation partition")
    return train, validation


def split_examples_from_assignments(
    examples: Iterable[RuleBCExample], assignments: object
) -> tuple[list[RuleBCExample], list[RuleBCExample]]:
    """Apply an externally attested whole-episode split without re-splitting.

    The C4 actual-data collector owns split assignment.  Training must reject
    a stale, partial, overlapping, or empty assignment rather than silently
    choosing a new partition.
    """
    values = list(examples)
    if not isinstance(assignments, dict) or not assignments:
        raise DatasetValidationError("split manifest assignments are required")
    source_ids = {example.source_id for example in values}
    if set(assignments) != source_ids:
        raise DatasetValidationError("split manifest does not cover exactly the dataset episodes")
    if any(value not in {"train", "validation"} for value in assignments.values()):
        raise DatasetValidationError("split manifest assignment is invalid")
    train = [example for example in values if assignments[example.source_id] == "train"]
    validation = [example for example in values if assignments[example.source_id] == "validation"]
    if not train or not validation:
        raise DatasetValidationError("split manifest produced an empty partition")
    if {item.source_id for item in train}.intersection(item.source_id for item in validation):
        raise DatasetValidationError("split manifest has episode overlap")
    return train, validation


__all__ = [
    "DATASET_SCHEMA_VERSION",
    "DatasetValidationError",
    "RuleBCExample",
    "build_rule_bc_example",
    "load_dataset",
    "split_examples",
    "split_examples_from_assignments",
    "validate_example",
    "write_dataset",
]
