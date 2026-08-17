"""Deterministic, fail-closed ActionKey adapter for curated teacher rules.

The curated pack ships 22 natural-language teacher rules.  This adapter maps
each rule onto the *existing* stable public ActionKey candidate contract used
by :mod:`mage_ptcg.distillation.knowledge` and reports a per-rule
:class:`TeacherApplication`.  It never invents a new ActionKey schema, never
interprets a rule's prose, and never selects a candidate a rule cannot bind
unambiguously.

Two facts drive the design and the honest coverage numbers:

* The persisted public ActionKey exposes only the coarse ``semantic_operation``
  family (``PLAY``/``ATTACH``/``EVOLVE``/``ABILITY``/``ATTACK``/``END``) and
  deliberately redacts the actor's card identity.  Teacher rules that need a
  specific card, energy state, or cross-action lookahead therefore cannot be
  bound structurally and fail closed as ``SKIPPED_UNSUPPORTED``.
* A rule is applied to a candidate only when the offline builder has attached
  an explicit, public attestation (``applicable_rule_ids`` +
  ``observable_condition_met``) to that candidate.  The adapter consumes that
  attestation; it does not guess it from the rule text.

Because both guards are conservative, real C4 fixtures (which carry no
attestation) produce zero applied rules.  That is a truthful reflection of the
redacted public projection, not a coverage failure.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Mapping

from .contracts import digest
from .knowledge import (
    CuratedKnowledge,
    CuratedKnowledgeError,
    TeacherRule,
    _ensure_public,
    _formula_delta,
    _legal_candidates,
)


ADAPTER_VERSION = "c5-actionkey-adapter-v1"

# A candidate whose attested delta is at or below this bound, or that names a
# hard constraint, is removed from the selectable set before any soft scoring.
HARD_REJECT_THRESHOLD = -1000.0


class RuleSupportClass(str, Enum):
    """Static representability of a teacher rule under the public ActionKey."""

    DIRECTLY_SUPPORTED = "DIRECTLY_SUPPORTED"
    CONDITION_ONLY = "CONDITION_ONLY"
    AMBIGUOUS = "AMBIGUOUS"
    UNSUPPORTED = "UNSUPPORTED"


class TeacherStatus(str, Enum):
    """Outcome of adapting one teacher rule to one decision."""

    APPLIED = "APPLIED"
    SKIPPED_NO_MATCH = "SKIPPED_NO_MATCH"
    SKIPPED_AMBIGUOUS = "SKIPPED_AMBIGUOUS"
    SKIPPED_UNSUPPORTED = "SKIPPED_UNSUPPORTED"
    SKIPPED_PRIVATE = "SKIPPED_PRIVATE"
    SKIPPED_CONFLICT = "SKIPPED_CONFLICT"
    FALLBACK_RULE_V0 = "FALLBACK_RULE_V0"


# Canonical, human-curated classification of the 22 registry teacher rules.
# The table is the source of truth; the loader never overrides it from prose.
# Each entry records *why* the rule is or is not representable so the evidence
# report cannot silently drift from the code.
_SUPPORT_TABLE: dict[str, tuple[RuleSupportClass, str]] = {
    "TR-000001": (RuleSupportClass.AMBIGUOUS, "tie-break by option index; forbidden order dependence"),
    "TR-000002": (RuleSupportClass.CONDITION_ONLY, "minCount==0 is structural but scoped to a card-specific selector"),
    "TR-000003": (RuleSupportClass.UNSUPPORTED, "per-card SETUP score needs redacted card identity"),
    "TR-000004": (RuleSupportClass.UNSUPPORTED, "prose family ordering, not a portable delta"),
    "TR-000005": (RuleSupportClass.UNSUPPORTED, "evolution-line identity and ability sequencing"),
    "TR-000006": (RuleSupportClass.UNSUPPORTED, "RETREAT has no stable ActionKey family; card identity"),
    "TR-000007": (RuleSupportClass.UNSUPPORTED, "needs card identity and cross-action ATTACK lookahead"),
    "TR-000008": (RuleSupportClass.CONDITION_ONLY, "opponent handCount is public but target card identity is redacted"),
    "TR-000009": (RuleSupportClass.UNSUPPORTED, "card identity and bench-state semantics"),
    "TR-000010": (RuleSupportClass.DIRECTLY_SUPPORTED, "portable numeric penalty (-1) via explicit condition attestation"),
    "TR-000011": (RuleSupportClass.CONDITION_ONLY, "deckCount is own-visible but per-card suppression is redacted"),
    "TR-000012": (RuleSupportClass.UNSUPPORTED, "RETREAT has no stable ActionKey family; card and hp semantics"),
    "TR-000013": (RuleSupportClass.CONDITION_ONLY, "deckCount is own-visible but per-card suppression is redacted"),
    "TR-000014": (RuleSupportClass.UNSUPPORTED, "per-card ability score formula"),
    "TR-000015": (RuleSupportClass.CONDITION_ONLY, "turn-order selection is structural but NO-option value semantics are unresolved"),
    "TR-000016": (RuleSupportClass.CONDITION_ONLY, "mulligan selection is structural but YES-option value semantics are unresolved"),
    "TR-000017": (RuleSupportClass.UNSUPPORTED, "per-card SETUP score formula"),
    "TR-000018": (RuleSupportClass.UNSUPPORTED, "readiness-tier formula needs energy and pokemon state"),
    "TR-000019": (RuleSupportClass.UNSUPPORTED, "multi-family MAIN macro ordering"),
    "TR-000020": (RuleSupportClass.CONDITION_ONLY, "deckCount/prize are public but the deck-removal effect is not in the ActionKey"),
    "TR-000021": (RuleSupportClass.AMBIGUOUS, "generic score-descending with index tie-break"),
    "TR-000022": (RuleSupportClass.UNSUPPORTED, "multi-step macro, not a single ActionKey selection"),
}

# Known synonyms/aliases normalized to the registry's canonical teacher token.
# Only notation is normalized here; no rule meaning is inferred.
_ACTION_TYPE_ALIASES: dict[str, str] = {
    "PLAY": "PLAY_POKEMON",
    "PLAY_BASIC": "PLAY_BASIC_POKEMON",
    "SETUP_ACTIVE": "SETUP_ACTIVE_POKEMON",
    "DECLINE_OPTIONAL": "DECLINE_OPTIONAL_SELECTION",
    "ACTIVATE": "ACTIVATE_ABILITY",
    "DRAW": "DRAW_OR_SEARCH",
    "SEARCH": "DRAW_OR_SEARCH",
}

# Canonical teacher token -> coarse public ActionKey ``semantic_operation``
# family.  ``None`` means the token has no stable family and can only match a
# candidate whose attested ``action_type`` equals the token itself.
_TEACHER_TO_FAMILY: dict[str, str | None] = {
    "PLAY_POKEMON": "PLAY",
    "PLAY_BASIC_POKEMON": "PLAY",
    "PLAY_BOSSES_ORDERS": "PLAY",
    "PLAY_XEROSICS_MACHINATIONS": "PLAY",
    "SETUP_ACTIVE_POKEMON": "PLAY",
    "EVOLVE": "EVOLVE",
    "ATTACH": "ATTACH",
    "ACTIVATE_ABILITY": "ABILITY",
    "ACTIVATE_DUDUNSPARCE": "ABILITY",
    "DRAW_OR_SEARCH": "ABILITY",
}


def normalize_action_type(raw: object) -> str | None:
    """Normalize notation only: strip, upper-case, and apply known aliases.

    Returns ``None`` for a missing or non-string value.  Never infers meaning.
    """
    if not isinstance(raw, str) or not raw.strip():
        return None
    token = "_".join(raw.strip().upper().split())
    return _ACTION_TYPE_ALIASES.get(token, token)


def classify_teacher_rule(rule: TeacherRule) -> RuleSupportClass:
    """Return the canonical support class for a registry teacher rule."""
    entry = _SUPPORT_TABLE.get(rule.teacher_id)
    if entry is None:
        # An unknown teacher id must never be treated as supported.
        return RuleSupportClass.UNSUPPORTED
    return entry[0]


def support_reason(teacher_id: str) -> str:
    entry = _SUPPORT_TABLE.get(teacher_id)
    return entry[1] if entry is not None else "unknown teacher id"


@dataclass(frozen=True, slots=True)
class TeacherApplication:
    """Result of adapting one teacher rule to one decision."""

    teacher_id: str
    status: TeacherStatus
    matched_candidate_ids: tuple[str, ...]
    score_adjustments: tuple[tuple[str, float], ...]
    hard_rejections: tuple[str, ...]
    skip_reason: str | None
    provenance: Mapping[str, object]

    def to_payload(self) -> dict[str, object]:
        return {
            "teacher_id": self.teacher_id,
            "status": self.status.value,
            "matched_candidate_ids": list(self.matched_candidate_ids),
            "score_adjustments": [list(item) for item in self.score_adjustments],
            "hard_rejections": list(self.hard_rejections),
            "skip_reason": self.skip_reason,
            "provenance": dict(self.provenance),
        }


@dataclass(frozen=True, slots=True)
class DecisionAdaptation:
    """Aggregated per-decision result over all curated teacher rules."""

    applications: tuple[TeacherApplication, ...]
    hard_rejections: tuple[str, ...]
    rule_v0_fallback: bool
    fallback_reason: str | None
    metrics: Mapping[str, int]


def _provenance(rule: TeacherRule, normalized: str | None) -> dict[str, object]:
    return {
        "teacher_id": rule.teacher_id,
        "canonical_rule_id": rule.canonical_rule_id,
        "support_class": classify_teacher_rule(rule).value,
        "support_reason": support_reason(rule.teacher_id),
        "action_type_normalized": normalized,
        "adapter_version": ADAPTER_VERSION,
    }


def _skip(rule: TeacherRule, normalized: str | None, status: TeacherStatus, reason: str) -> TeacherApplication:
    return TeacherApplication(rule.teacher_id, status, (), (), (), reason, _provenance(rule, normalized))


def _candidate_hard_rejected(candidate: Mapping[str, object], hard_ids: frozenset[str]) -> bool:
    violations = candidate.get("hard_constraint_violations")
    if isinstance(violations, (list, tuple)) and any(item in hard_ids for item in violations):
        return True
    delta = candidate.get("curated_score_delta")
    if isinstance(delta, (int, float)) and not isinstance(delta, bool) and float(delta) <= HARD_REJECT_THRESHOLD:
        return True
    return bool(candidate.get("hard_reject") is True)


def _matches(candidate: Mapping[str, object], rule: TeacherRule, normalized: str | None) -> bool:
    """Attestation-based match; mirrors knowledge._applicable with normalization."""
    ids = candidate.get("applicable_rule_ids")
    if not (isinstance(ids, (list, tuple)) and rule.teacher_id in ids):
        return False
    if candidate.get("observable_condition_met") is not True:
        return False
    if normalized == "ANY_SELECTION":
        return True
    candidate_type = normalize_action_type(candidate.get("action_type"))
    if candidate_type is not None and normalized is not None and candidate_type == normalized:
        return True
    # Fall back to coarse public family equality when the candidate exposes only
    # the redacted semantic_operation rather than a fine-grained action_type.
    family = _TEACHER_TO_FAMILY.get(normalized or "")
    candidate_family = candidate.get("semantic_operation")
    return family is not None and isinstance(candidate_family, str) and candidate_family == family


def adapt_teacher_rule(
    rule: TeacherRule,
    observation: Mapping[str, object],
    legal_candidates: Iterable[Mapping[str, object]],
    *,
    hard_constraint_ids: frozenset[str] = frozenset(),
) -> TeacherApplication:
    """Map one teacher rule onto legal candidates, failing closed on any doubt.

    The result is independent of candidate ordering: matched ids and score
    adjustments are always sorted by ``action_id``.
    """
    normalized = normalize_action_type(rule.candidate_action_type)
    candidate_values = list(legal_candidates)

    # Fail closed on private/credential-shaped inputs before any matching.
    try:
        _ensure_public(observation)
        _ensure_public(candidate_values)
    except CuratedKnowledgeError as exc:
        return _skip(rule, normalized, TeacherStatus.SKIPPED_PRIVATE, str(exc))

    # Validate the legal candidate set (unknown selection type, ambiguous or
    # out-of-range mapping, empty set) using the existing hard-truth contract.
    # A registration observation has no ``select`` and is reported separately.
    try:
        legal, _minimum, _maximum = _legal_candidates(observation, candidate_values)
    except CuratedKnowledgeError as exc:
        reason = str(exc)
        if "ambiguous" in reason:
            return _skip(rule, normalized, TeacherStatus.SKIPPED_AMBIGUOUS, reason)
        if "registration" in reason:
            return _skip(rule, normalized, TeacherStatus.SKIPPED_UNSUPPORTED, reason)
        if "forbidden" in reason:
            return _skip(rule, normalized, TeacherStatus.SKIPPED_PRIVATE, reason)
        return _skip(rule, normalized, TeacherStatus.SKIPPED_UNSUPPORTED, reason)

    support = classify_teacher_rule(rule)
    if support is RuleSupportClass.AMBIGUOUS:
        return _skip(rule, normalized, TeacherStatus.SKIPPED_AMBIGUOUS, support_reason(rule.teacher_id))
    if support in (RuleSupportClass.UNSUPPORTED, RuleSupportClass.CONDITION_ONLY):
        return _skip(rule, normalized, TeacherStatus.SKIPPED_UNSUPPORTED, support_reason(rule.teacher_id))

    # DIRECTLY_SUPPORTED: apply only where an explicit public attestation binds
    # this rule to the candidate and the score formula is a portable delta.
    matched: list[str] = []
    hard_rejections: list[str] = []
    adjustments: dict[str, float] = {}
    for candidate in legal:
        if not _matches(candidate, rule, normalized):
            continue
        action_id = str(candidate["action_id"])
        if _candidate_hard_rejected(candidate, hard_constraint_ids):
            hard_rejections.append(action_id)
            continue
        delta = _formula_delta(rule.score_formula, candidate)
        if delta is None:
            continue
        matched.append(action_id)
        adjustments[action_id] = adjustments.get(action_id, 0.0) + float(delta)

    provenance = _provenance(rule, normalized)
    if matched:
        return TeacherApplication(
            rule.teacher_id,
            TeacherStatus.APPLIED,
            tuple(sorted(matched)),
            tuple(sorted(adjustments.items())),
            tuple(sorted(hard_rejections)),
            None,
            provenance,
        )
    if hard_rejections:
        # Every candidate this rule could have bound was removed by a hard
        # constraint, so the rule cannot legally apply.
        return TeacherApplication(
            rule.teacher_id,
            TeacherStatus.SKIPPED_CONFLICT,
            (),
            (),
            tuple(sorted(hard_rejections)),
            "all matched candidates were hard-rejected",
            provenance,
        )
    return _skip(rule, normalized, TeacherStatus.SKIPPED_NO_MATCH, "no attested candidate matched")


def adapt_decision(
    observation: Mapping[str, object],
    legal_candidates: Iterable[Mapping[str, object]],
    knowledge: CuratedKnowledge,
) -> DecisionAdaptation:
    """Run every curated teacher rule over one decision and aggregate metrics.

    Hard constraints always win: any candidate a hard constraint rejects is
    excluded before soft scoring, and never appears in a matched set.  When no
    rule applies, the decision falls back to Rule Agent v0 (the caller keeps the
    v0 label); the adapter never fabricates a selection.
    """
    hard_ids = frozenset(item.constraint_id for item in knowledge.hard_constraints)
    candidate_values = list(legal_candidates)

    hard_rejections: set[str] = set()
    try:
        _ensure_public(observation)
        _ensure_public(candidate_values)
        legal, _minimum, _maximum = _legal_candidates(observation, candidate_values)
        for candidate in legal:
            if _candidate_hard_rejected(candidate, hard_ids):
                hard_rejections.add(str(candidate["action_id"]))
    except CuratedKnowledgeError:
        # A malformed or private decision means the whole decision falls back to
        # Rule v0; every rule is reported as such and no label is produced.
        applications = tuple(
            _skip(rule, normalize_action_type(rule.candidate_action_type), TeacherStatus.FALLBACK_RULE_V0, "decision-level fallback to rule v0")
            for rule in knowledge.teacher_rules
        )
        metrics = _metrics(applications, hard_rejections=(), fallback=True)
        return DecisionAdaptation(applications, (), True, "decision-level guard failed", metrics)

    applications = tuple(
        adapt_teacher_rule(rule, observation, candidate_values, hard_constraint_ids=hard_ids)
        for rule in knowledge.teacher_rules
    )
    applied = any(app.status is TeacherStatus.APPLIED for app in applications)
    metrics = _metrics(applications, hard_rejections=tuple(sorted(hard_rejections)), fallback=not applied)
    return DecisionAdaptation(
        applications,
        tuple(sorted(hard_rejections)),
        not applied,
        None if applied else "no teacher rule applied; retain rule v0",
        metrics,
    )


def _metrics(applications: tuple[TeacherApplication, ...], *, hard_rejections: tuple[str, ...], fallback: bool) -> dict[str, int]:
    status_counts: Counter[TeacherStatus] = Counter(app.status for app in applications)
    candidate_matches = sum(len(app.matched_candidate_ids) for app in applications)
    return {
        "teacher_rules_loaded": len(applications),
        "teacher_rules_considered": len(applications),
        "teacher_rules_applied": status_counts[TeacherStatus.APPLIED],
        "teacher_rules_skipped_no_match": status_counts[TeacherStatus.SKIPPED_NO_MATCH],
        "teacher_rules_skipped_ambiguous": status_counts[TeacherStatus.SKIPPED_AMBIGUOUS],
        "teacher_rules_skipped_unsupported": status_counts[TeacherStatus.SKIPPED_UNSUPPORTED],
        "teacher_rules_skipped_private": status_counts[TeacherStatus.SKIPPED_PRIVATE],
        "teacher_rules_skipped_conflict": status_counts[TeacherStatus.SKIPPED_CONFLICT],
        "candidate_matches": candidate_matches,
        "hard_constraint_rejections": len(hard_rejections),
        "rule_v0_fallbacks": 1 if fallback else 0,
    }


def classification_summary(knowledge: CuratedKnowledge) -> dict[str, object]:
    """Static, order-independent classification of the loaded teacher rules."""
    by_class: Counter[str] = Counter()
    normalized = 0
    entries: list[dict[str, object]] = []
    for rule in knowledge.teacher_rules:
        support = classify_teacher_rule(rule)
        by_class[support.value] += 1
        canonical = normalize_action_type(rule.candidate_action_type)
        raw_upper = "_".join(str(rule.candidate_action_type).strip().upper().split())
        if canonical is not None and canonical != raw_upper:
            normalized += 1
        entries.append({
            "teacher_id": rule.teacher_id,
            "support_class": support.value,
            "action_type": rule.candidate_action_type,
            "action_type_normalized": canonical,
            "reason": support_reason(rule.teacher_id),
        })
    return {
        "adapter_version": ADAPTER_VERSION,
        "teacher_rules": len(knowledge.teacher_rules),
        "directly_supported": by_class.get(RuleSupportClass.DIRECTLY_SUPPORTED.value, 0),
        "normalized": normalized,
        "condition_only": by_class.get(RuleSupportClass.CONDITION_ONLY.value, 0),
        "ambiguous": by_class.get(RuleSupportClass.AMBIGUOUS.value, 0),
        "unsupported": by_class.get(RuleSupportClass.UNSUPPORTED.value, 0),
        "rules": sorted(entries, key=lambda item: item["teacher_id"]),
    }


def adapter_config_hash() -> str:
    """Stable digest of the adapter's declarative configuration.

    Any change to the classification table, aliases, or family map changes this
    hash, so a rebuilt manifest is only byte-identical under an identical policy.
    """
    config = {
        "adapter_version": ADAPTER_VERSION,
        "hard_reject_threshold": HARD_REJECT_THRESHOLD,
        "support_table": {key: [value[0].value, value[1]] for key, value in sorted(_SUPPORT_TABLE.items())},
        "action_type_aliases": dict(sorted(_ACTION_TYPE_ALIASES.items())),
        "teacher_to_family": dict(sorted(_TEACHER_TO_FAMILY.items(), key=lambda item: item[0])),
    }
    return digest(config, domain="actionkey-adapter-config")


def _record_to_decision(record: Mapping[str, object]) -> tuple[dict[str, object], list[dict[str, object]]]:
    """Reconstruct a public selection contract from a persisted C5 record.

    C5 records key candidates by stable ``action_id`` and drop the transient
    ``option_index``.  A deterministic index is derived from the action-id sort
    so the legal-candidate contract holds without depending on stored order.
    """
    selection = record["selection"]
    if not isinstance(selection, Mapping):
        raise CuratedKnowledgeError("record selection is malformed")
    legal_actions = record["legal_actions"]
    if not isinstance(legal_actions, list):
        raise CuratedKnowledgeError("record legal_actions is malformed")
    ordered = sorted(legal_actions, key=lambda item: str(item.get("action_id")))
    candidates: list[dict[str, object]] = []
    for index, item in enumerate(ordered):
        payload = item.get("public_payload") if isinstance(item, Mapping) else None
        semantic = payload.get("semantic_operation") if isinstance(payload, Mapping) else None
        candidate: dict[str, object] = {"option_index": index, "action_id": item.get("action_id")}
        if isinstance(semantic, str):
            candidate["semantic_operation"] = semantic
        candidates.append(candidate)
    observation = {
        "select": {
            "type": selection.get("type"),
            "context": selection.get("context"),
            "option": [{} for _ in candidates],
            "minCount": selection.get("min_count"),
            "maxCount": selection.get("max_count"),
        }
    }
    return observation, candidates


def adapt_records(records: Iterable[Mapping[str, object]], knowledge: CuratedKnowledge) -> dict[str, object]:
    """Adapt every teacher rule over persisted C5 decision records.

    Returns a deterministic, order-independent manifest.  Only ``APPLIED`` rules
    contribute an ``applied_binding``; skipped rules have zero effect.  Real C4
    fixtures carry no attestation, so ``teacher_rules_applied`` is honestly 0.
    """
    totals: Counter[str] = Counter()
    decisions_with_applied = 0
    applied_bindings: list[dict[str, object]] = []
    record_count = 0
    for record in records:
        record_count += 1
        observation, candidates = _record_to_decision(record)
        result = adapt_decision(observation, candidates, knowledge)
        totals.update(result.metrics)
        if not result.rule_v0_fallback:
            decisions_with_applied += 1
        for app in result.applications:
            if app.status is TeacherStatus.APPLIED:
                applied_bindings.append({
                    "record_id": record.get("record_id"),
                    "teacher_id": app.teacher_id,
                    "canonical_rule_id": app.provenance.get("canonical_rule_id"),
                    "matched_candidate_ids": list(app.matched_candidate_ids),
                    "score_adjustments": [list(item) for item in app.score_adjustments],
                })
    # ``teacher_rules_loaded`` in the summed counter double-counts across
    # decisions; report the static rule count and expose the summed pairs
    # separately so decision-level and rule-level tallies never merge.
    classification = classification_summary(knowledge)
    # A (decision, rule) pair is either applied or skipped; decision-level Rule
    # v0 fallback is tracked separately and is not a per-pair skip bucket.
    skipped_pairs = (
        totals["teacher_rules_skipped_no_match"]
        + totals["teacher_rules_skipped_ambiguous"]
        + totals["teacher_rules_skipped_unsupported"]
        + totals["teacher_rules_skipped_private"]
        + totals["teacher_rules_skipped_conflict"]
    )
    metrics = {
        "teacher_rules_loaded": len(knowledge.teacher_rules),
        "teacher_rules_applied": totals["teacher_rules_applied"],
        "teacher_rules_skipped": skipped_pairs,
        "decision_rule_pairs_considered": totals["teacher_rules_considered"],
        "decision_rule_pairs_no_match": totals["teacher_rules_skipped_no_match"],
        "decision_rule_pairs_ambiguous": totals["teacher_rules_skipped_ambiguous"],
        "decision_rule_pairs_unsupported": totals["teacher_rules_skipped_unsupported"],
        "decision_rule_pairs_private": totals["teacher_rules_skipped_private"],
        "decision_rule_pairs_conflict": totals["teacher_rules_skipped_conflict"],
        "candidate_matches": totals["candidate_matches"],
        "hard_constraint_rejections": totals["hard_constraint_rejections"],
        "rule_v0_fallbacks": totals["rule_v0_fallbacks"],
    }
    return {
        "adapter_version": ADAPTER_VERSION,
        "adapter_config_hash": adapter_config_hash(),
        "teacher_registry_only": True,
        "decisions_considered": record_count,
        "decisions_with_applied_rule": decisions_with_applied,
        "classification": {key: classification[key] for key in ("directly_supported", "normalized", "condition_only", "ambiguous", "unsupported")},
        "metrics": dict(sorted(metrics.items())),
        "applied_bindings": sorted(applied_bindings, key=lambda item: (str(item["record_id"]), item["teacher_id"])),
    }


__all__ = [
    "ADAPTER_VERSION", "DecisionAdaptation", "HARD_REJECT_THRESHOLD", "RuleSupportClass",
    "TeacherApplication", "TeacherStatus", "adapt_decision", "adapt_records", "adapt_teacher_rule",
    "adapter_config_hash", "classification_summary", "classify_teacher_rule", "normalize_action_type",
    "support_reason",
]
