"""Fail-closed, offline-only consumer for curated C5 knowledge.

The curated pack is evidence for offline dataset construction, not a
submission dependency.  Its natural-language rules are only applied when an
adapter supplies an unambiguous ActionKey mapping and explicitly attests that
the rule's observable condition holds.  Otherwise the caller retains Rule
Agent v0's label.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from mage_ptcg.observability.cabt_trace import FORBIDDEN_OBSERVATION_KEYS

from .contracts import DecisionDatasetError


CURATED_SCHEMA_VERSION = "team-knowledge-curated-v1"
_EXPECTED_TEACHER_COUNT = 22
_EXPECTED_HARD_IDS = frozenset(f"HC-{number:06d}" for number in range(1, 6))
_ALLOWED_TEACHER_DECISION = "ACCEPT_TEACHER_RULE"
_ALLOWED_HARD_DECISION = "ACCEPT_HARD_CONSTRAINT"
_ALLOWED_SEARCH_DECISION = "ACCEPT_SEARCH_PRIOR"
_ALLOWED_EVALUATION_DECISION = "ACCEPT_EVALUATION_WEIGHT"
_PRIVATE_KEYS = frozenset({
    "token", "email", "cookie", "header", "authorization", "signed_url",
    "search_begin_input", "raw_observation", "private_action_key_digest",
    "action_key_core", "opponent_hand", "opponent_hand_ids", "opponent_deck",
})


class CuratedKnowledgeError(DecisionDatasetError):
    """Raised when curated knowledge cannot be safely consumed."""


@dataclass(frozen=True, slots=True)
class TeacherRule:
    teacher_id: str
    canonical_rule_id: str
    candidate_action_type: str
    observable_condition: str
    scope: tuple[str, ...]
    score_formula: str | None
    priority: str | None
    source_evidence_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class HardConstraint:
    constraint_id: str
    observable_condition: str
    requirement: str


@dataclass(frozen=True, slots=True)
class SearchPrior:
    rule_id: str
    candidate_action_type: str
    observable_condition: str
    score_formula: str | None
    priority: str | None


@dataclass(frozen=True, slots=True)
class EvaluationWeight:
    evaluation_id: str
    wins: int
    losses: int
    draws: int
    games: int
    rate: float
    opponents: tuple[object, ...]
    heterogeneous: bool


@dataclass(frozen=True, slots=True)
class CuratedKnowledge:
    teacher_rules: tuple[TeacherRule, ...]
    hard_constraints: tuple[HardConstraint, ...]
    search_priors: tuple[SearchPrior, ...]
    evaluation_weights: tuple[EvaluationWeight, ...]
    load_metrics: Mapping[str, int]


@dataclass(frozen=True, slots=True)
class PriorApplication:
    """A deterministic offline decision, or a Rule v0 fallback reason."""

    selected_action_ids: tuple[str, ...] | None
    fallback_reason: str | None
    candidate_scores: tuple[tuple[str, float], ...]
    metrics: Mapping[str, int]


def _read_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CuratedKnowledgeError(f"invalid curated JSON: {path}") from exc
    if not isinstance(value, dict):
        raise CuratedKnowledgeError(f"curated JSON must be an object: {path}")
    return value


def _validate_pack_version(directory: Path) -> None:
    summary = _read_json(directory / "summary.json")
    if summary.get("schema_version") != CURATED_SCHEMA_VERSION:
        raise CuratedKnowledgeError("unsupported curated knowledge schema version")


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise CuratedKnowledgeError(f"cannot read curated JSONL: {path}") from exc
    result: list[dict[str, object]] = []
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise CuratedKnowledgeError(f"malformed JSONL row {line_number}: {path.name}") from exc
        if not isinstance(item, dict):
            raise CuratedKnowledgeError(f"non-object JSONL row {line_number}: {path.name}")
        result.append(item)
    return result


def _required_string(row: Mapping[str, object], field: str, *, context: str) -> str:
    value = row.get(field)
    if not isinstance(value, str) or not value.strip():
        raise CuratedKnowledgeError(f"{context} is missing {field}")
    return value


def _string_list(row: Mapping[str, object], field: str, *, context: str) -> tuple[str, ...]:
    value = row.get(field)
    if not isinstance(value, list) or not value or not all(isinstance(item, str) and item for item in value):
        raise CuratedKnowledgeError(f"{context} has invalid {field}")
    return tuple(value)


def _canonical_reference_ids(directory: Path) -> set[str]:
    # The curator intentionally registers a small number of macro IDs through
    # the canonical_rule_id field.  This verifies linkage without treating any
    # of these source rows as an additional teacher rule.
    sources = (
        ("canonical_rules.jsonl", "rule_id"),
        ("combos.jsonl", "combo_id"),
        ("macros.jsonl", "macro_id"),
        ("matchup_rules.jsonl", "matchup_rule_id"),
    )
    result: set[str] = set()
    for filename, field in sources:
        for row in _read_jsonl(directory / filename):
            value = row.get(field)
            if isinstance(value, str) and value:
                result.add(value)
    return result


def load_teacher_rules(directory: str | Path) -> tuple[TeacherRule, ...]:
    """Load exactly the registry's 22 teacher rules, never canonical rows."""
    root = Path(directory)
    _validate_pack_version(root)
    references = _canonical_reference_ids(root)
    rules: list[TeacherRule] = []
    identifiers: set[str] = set()
    for row in _read_jsonl(root / "executable_teacher_registry.jsonl"):
        context = "teacher registry row"
        if row.get("decision") != _ALLOWED_TEACHER_DECISION:
            raise CuratedKnowledgeError("teacher registry contains a non-accepted decision")
        teacher_id = _required_string(row, "teacher_id", context=context)
        if teacher_id in identifiers:
            raise CuratedKnowledgeError(f"duplicate teacher_id: {teacher_id}")
        identifiers.add(teacher_id)
        canonical_rule_id = _required_string(row, "canonical_rule_id", context=context)
        if canonical_rule_id not in references:
            raise CuratedKnowledgeError(f"teacher {teacher_id} references missing canonical_rule_id")
        condition = _required_string(row, "observable_condition", context=context)
        action_type = _required_string(row, "candidate_action_type", context=context)
        scope = _string_list(row, "deck_scope", context=context)
        evidence = _string_list(row, "source_evidence_ids", context=context)
        score_formula, priority = row.get("score_formula"), row.get("priority")
        if score_formula is not None and not isinstance(score_formula, str):
            raise CuratedKnowledgeError(f"teacher {teacher_id} has invalid score_formula")
        if priority is not None and not isinstance(priority, str):
            raise CuratedKnowledgeError(f"teacher {teacher_id} has invalid priority")
        if not (isinstance(score_formula, str) and score_formula.strip()) and not (isinstance(priority, str) and priority.strip()):
            raise CuratedKnowledgeError(f"teacher {teacher_id} is missing score_formula/priority")
        rules.append(TeacherRule(teacher_id, canonical_rule_id, action_type, condition, tuple(sorted(scope)), score_formula, priority, tuple(sorted(evidence))))
    if len(rules) != _EXPECTED_TEACHER_COUNT:
        raise CuratedKnowledgeError(f"expected exactly {_EXPECTED_TEACHER_COUNT} distinct teacher rules")
    return tuple(sorted(rules, key=lambda item: item.teacher_id))


def load_hard_constraints(directory: str | Path) -> tuple[HardConstraint, ...]:
    root = Path(directory)
    _validate_pack_version(root)
    constraints: list[HardConstraint] = []
    identifiers: set[str] = set()
    for row in _read_jsonl(root / "hard_constraints.jsonl"):
        if row.get("decision") != _ALLOWED_HARD_DECISION:
            raise CuratedKnowledgeError("hard constraint has an unknown decision")
        identifier = _required_string(row, "constraint_id", context="hard constraint")
        if identifier in identifiers:
            raise CuratedKnowledgeError(f"duplicate hard constraint: {identifier}")
        identifiers.add(identifier)
        constraints.append(HardConstraint(identifier, _required_string(row, "observable_condition", context=identifier), _required_string(row, "requirement", context=identifier)))
    if identifiers != _EXPECTED_HARD_IDS:
        raise CuratedKnowledgeError("hard constraints must be exactly HC-000001 through HC-000005")
    return tuple(sorted(constraints, key=lambda item: item.constraint_id))


def load_search_priors(directory: str | Path) -> tuple[SearchPrior, ...]:
    root = Path(directory)
    _validate_pack_version(root)
    priors: list[SearchPrior] = []
    identifiers: set[str] = set()
    for row in _read_jsonl(root / "canonical_rules.jsonl"):
        # ACCEPT_TEACHER_RULE is provenance only; HOLD rows, including the
        # unresolved contradictions, never cross the policy boundary.
        if row.get("decision") != _ALLOWED_SEARCH_DECISION:
            continue
        identifier = _required_string(row, "rule_id", context="search prior")
        if identifier in identifiers:
            raise CuratedKnowledgeError(f"duplicate search prior: {identifier}")
        identifiers.add(identifier)
        score_formula, priority = row.get("score_formula"), row.get("priority")
        if score_formula is not None and not isinstance(score_formula, str):
            raise CuratedKnowledgeError(f"search prior {identifier} has invalid score_formula")
        if priority is not None and not isinstance(priority, str):
            raise CuratedKnowledgeError(f"search prior {identifier} has invalid priority")
        priors.append(SearchPrior(identifier, _required_string(row, "candidate_action_type", context=identifier), _required_string(row, "observable_condition", context=identifier), score_formula, priority))
    return tuple(sorted(priors, key=lambda item: item.rule_id))


def evaluation_weight(record: Mapping[str, object]) -> EvaluationWeight | None:
    """Recompute a soft-only rate from W-L-D; never use source ``win_rate``."""
    if record.get("decision") != _ALLOWED_EVALUATION_DECISION:
        return None
    identifier = _required_string(record, "evaluation_id", context="evaluation record")
    if record.get("kaggle_score") is not None and not all(type(record.get(key)) is int for key in ("wins", "losses", "draws")):
        return None
    values = {key: record.get(key) for key in ("wins", "losses", "draws", "games")}
    if not all(type(value) is int and value >= 0 for value in values.values()):
        return None
    wins, losses, draws, games = (int(values[key]) for key in ("wins", "losses", "draws", "games"))
    if games == 0:
        return None
    if wins + losses + draws != games:
        raise CuratedKnowledgeError(f"evaluation {identifier} has inconsistent W-L-D and games")
    opponents = record.get("opponents")
    if not isinstance(opponents, list):
        raise CuratedKnowledgeError(f"evaluation {identifier} has invalid opponents provenance")
    subject_policy = record.get("subject_policy")
    heterogeneous = isinstance(subject_policy, str) and "heterogeneous" in subject_policy
    return EvaluationWeight(identifier, wins, losses, draws, games, (wins + 0.5 * draws) / games, tuple(opponents), heterogeneous)


def load_evaluation_weights(directory: str | Path) -> tuple[tuple[EvaluationWeight, ...], Mapping[str, int]]:
    root = Path(directory)
    _validate_pack_version(root)
    metrics: Counter[str] = Counter()
    weights: list[EvaluationWeight] = []
    for row in _read_jsonl(root / "evaluation_registry.jsonl"):
        metrics["evaluation_records_loaded"] += 1
        if row.get("decision") != _ALLOWED_EVALUATION_DECISION:
            metrics["evaluation_records_skipped_non_accepted"] += 1
            continue
        try:
            weight = evaluation_weight(row)
        except CuratedKnowledgeError:
            metrics["evaluation_records_skipped_invalid_wld"] += 1
            continue
        if weight is None:
            metrics["evaluation_records_skipped_unweighted"] += 1
            continue
        weights.append(weight)
        metrics["evaluation_weights_created"] += 1
    return tuple(sorted(weights, key=lambda item: item.evaluation_id)), dict(sorted(metrics.items()))


def load_curated_knowledge(directory: str | Path) -> CuratedKnowledge:
    teachers = load_teacher_rules(directory)
    constraints = load_hard_constraints(directory)
    priors = load_search_priors(directory)
    weights, weight_metrics = load_evaluation_weights(directory)
    metrics = Counter(weight_metrics)
    metrics.update({"teacher_rules_loaded": len(teachers), "hard_constraints_loaded": len(constraints), "search_priors_loaded": len(priors)})
    return CuratedKnowledge(teachers, constraints, priors, weights, dict(sorted(metrics.items())))


def _selection_contract(observation: Mapping[str, object]) -> tuple[list[object], int, int] | None:
    select = observation.get("select")
    if select is None:
        return None
    if not isinstance(select, Mapping):
        raise CuratedKnowledgeError("unknown selection type")
    options, minimum, maximum, selection_type = select.get("option"), select.get("minCount"), select.get("maxCount"), select.get("type")
    # C4 attests the two selector forms currently exposed by the public
    # fixture contract.  A new cabt selector requires an explicit adapter;
    # treating it as a familiar action is unsafe.
    if not isinstance(options, list) or type(minimum) is not int or type(maximum) is not int or selection_type not in {0, 1} or not 0 <= minimum <= maximum <= len(options):
        raise CuratedKnowledgeError("unknown selection type")
    return options, minimum, maximum


def _ensure_public(value: object, *, path: str = "$") -> None:
    """Reject hidden-state and credential-shaped values before rule matching."""
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str):
                raise CuratedKnowledgeError(f"non-string observation key at {path}")
            if key in FORBIDDEN_OBSERVATION_KEYS or key.lower() in _PRIVATE_KEYS:
                raise CuratedKnowledgeError(f"forbidden observation field {key!r}")
            _ensure_public(child, path=f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _ensure_public(child, path=f"{path}[{index}]")


def _legal_candidates(observation: Mapping[str, object], candidates: Iterable[Mapping[str, object]]) -> tuple[list[dict[str, object]], int, int]:
    contract = _selection_contract(observation)
    if contract is None:
        raise CuratedKnowledgeError("registration is not an action")
    options, minimum, maximum = contract
    values: list[dict[str, object]] = []
    seen_indices: set[int] = set()
    seen_ids: set[str] = set()
    for candidate in candidates:
        index = candidate.get("option_index", candidate.get("index"))
        action_id = candidate.get("action_id")
        if type(index) is not int or index < 0 or index >= len(options) or not isinstance(action_id, str) or not action_id:
            raise CuratedKnowledgeError("unknown or out-of-range ActionKey mapping")
        if index in seen_indices or action_id in seen_ids:
            raise CuratedKnowledgeError("ambiguous ActionKey mapping")
        seen_indices.add(index)
        seen_ids.add(action_id)
        values.append(dict(candidate))
    if not values or len(values) < minimum or seen_indices != set(range(len(options))):
        raise CuratedKnowledgeError("empty legal candidate set")
    return values, minimum, maximum


def _applicable(candidate: Mapping[str, object], rule_id: str, action_type: str) -> bool:
    # No natural-language condition is interpreted.  An offline adapter must
    # bind it explicitly to visible fields and the stable ActionKey candidate.
    ids = candidate.get("applicable_rule_ids")
    candidate_type = candidate.get("action_type")
    return (isinstance(ids, (list, tuple)) and rule_id in ids and candidate.get("observable_condition_met") is True and (action_type == "ANY_SELECTION" or candidate_type == action_type))


def _formula_delta(formula: str | None, candidate: Mapping[str, object]) -> float | None:
    # Only a fully specified numeric penalty is portable without guessing card
    # names or natural-language state predicates.  Other formulas are skipped.
    if formula == "-1":
        return -1.0
    explicit = candidate.get("curated_score_delta")
    return float(explicit) if isinstance(explicit, (int, float)) and not isinstance(explicit, bool) else None


def apply_priors(observation: Mapping[str, object], candidates: Iterable[Mapping[str, object]], knowledge: CuratedKnowledge) -> PriorApplication:
    """Apply only attested offline rules after the cabt-equivalent contract.

    Any malformed mapping, empty/ambiguous candidate set, or inability to fill
    the selection bounds returns ``fallback_reason`` rather than a guess.
    """
    metrics: Counter[str] = Counter()
    candidate_values = list(candidates)
    try:
        _ensure_public(observation)
        _ensure_public(candidate_values)
        legal, minimum, maximum = _legal_candidates(observation, candidate_values)
    except CuratedKnowledgeError as exc:
        reason = str(exc)
        if "ambiguous" in reason:
            metrics["ambiguous_action_mappings"] += 1
        else:
            metrics["unknown_action_mappings"] += 1
        if "selection" not in reason:
            metrics["hard_constraint_rejections"] += 1
        metrics["fallback_count"] += 1
        return PriorApplication(None, reason, (), dict(metrics))

    scores = {str(item["action_id"]): 0.0 for item in legal}
    for rule in knowledge.teacher_rules:
        applied = False
        for candidate in legal:
            if _applicable(candidate, rule.teacher_id, rule.candidate_action_type):
                delta = _formula_delta(rule.score_formula, candidate)
                if delta is not None:
                    scores[str(candidate["action_id"])] += delta
                    applied = True
        metrics["teacher_rules_applied" if applied else "teacher_rules_skipped"] += 1
    for prior in knowledge.search_priors:
        applied = False
        for candidate in legal:
            if _applicable(candidate, prior.rule_id, prior.candidate_action_type):
                delta = _formula_delta(prior.score_formula, candidate)
                if delta is not None:
                    scores[str(candidate["action_id"])] += delta
                    applied = True
        metrics["search_priors_applied" if applied else "search_priors_skipped"] += 1

    # Hard constraints are already applied by _legal_candidates before all
    # soft scoring.  Select only non-negative candidates, then deterministically
    # fill a required minimum from the remaining legal candidates.
    ordered = sorted(legal, key=lambda item: (-scores[str(item["action_id"])], int(item.get("option_index", item.get("index"))), str(item["action_id"])))
    selected = [item for item in ordered if scores[str(item["action_id"])] >= 0][:maximum]
    if len(selected) < minimum:
        selected = ordered[:minimum]
    if not minimum <= len(selected) <= maximum:
        metrics["hard_constraint_rejections"] += 1
        metrics["fallback_count"] += 1
        return PriorApplication(None, "hard constraint prevented a legal selection", tuple(sorted(scores.items())), dict(metrics))
    return PriorApplication(tuple(str(item["action_id"]) for item in selected), None, tuple(sorted(scores.items())), dict(metrics))


__all__ = [
    "CURATED_SCHEMA_VERSION", "CuratedKnowledge", "CuratedKnowledgeError", "EvaluationWeight", "HardConstraint",
    "PriorApplication", "SearchPrior", "TeacherRule", "apply_priors", "evaluation_weight", "load_curated_knowledge",
    "load_evaluation_weights", "load_hard_constraints", "load_search_priors", "load_teacher_rules",
]
