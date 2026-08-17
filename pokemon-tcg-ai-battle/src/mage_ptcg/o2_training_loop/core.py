"""Fail-closed O2 registries, deterministic plans, execution, and reporting.

Deck legality is delegated to ``main.validate_deck``; hashing and atomic I/O
are delegated to Competition Intelligence.  This module does not reach the
submission runtime and never changes the configured Champion.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import json
import math
from pathlib import Path
import statistics
from typing import Any, Callable, Iterable, Mapping, Sequence

from main import DeckValidationError, validate_deck
from mage_ptcg.competition_intelligence.atomic_io import atomic_write_json
from mage_ptcg.competition_intelligence.canonical import digest

DECK_POOL_SCHEMA = "o2-deck-pool-v1"
OPPONENT_POOL_SCHEMA = "o2-opponent-pool-v1"
MATCH_PLAN_SCHEMA = "o2-match-plan-v1"
MATCH_RESULT_SCHEMA = "o2-match-result-v1"
PROMOTION_REPORT_SCHEMA = "o2-promotion-report-v1"


class O2ContractError(ValueError):
    pass


def _load_mapping(path: str | Path) -> Mapping[str, Any]:
    """Load JSON-form YAML; JSON is a strict YAML subset and needs no parser."""
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise O2ContractError(f"invalid JSON-form YAML {path}: {exc}") from exc
    if not isinstance(value, Mapping):
        raise O2ContractError("registry root must be an object")
    return value


def _required_str(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise O2ContractError(f"{name} must be a non-empty string")
    return value


def _digest(value: object, domain: str) -> str:
    return digest(value, domain=domain)


def deck_content_hash(cards: Sequence[int]) -> str:
    """Hash card multiplicities, independent from source-list order."""
    try:
        validated = validate_deck(cards)
    except DeckValidationError as exc:
        raise O2ContractError(str(exc)) from exc
    return _digest({"card_counts": sorted(Counter(validated).items())}, "o2-deck-content")


@dataclass(frozen=True, slots=True)
class DeckEntry:
    deck_id: str
    deck_version: str
    cards: tuple[int, ...]
    deck_hash: str
    archetype: str
    variant: str
    roles: tuple[str, ...]
    source: str
    permission_scope: str
    valid_from: str | None
    valid_until: str | None
    confidence: str
    provenance: Mapping[str, object]


def load_deck_pool(path: str | Path) -> dict[str, DeckEntry]:
    raw = _load_mapping(path)
    if raw.get("schema_version") != DECK_POOL_SCHEMA:
        raise O2ContractError("unsupported deck pool schema_version")
    rows = raw.get("decks")
    if not isinstance(rows, list) or not rows:
        raise O2ContractError("decks must be a non-empty list")
    required = {"deck_id", "deck_version", "cards", "deck_hash", "archetype", "variant", "roles", "source", "permission_scope", "valid_from", "valid_until", "confidence"}
    result: dict[str, DeckEntry] = {}
    seen_content: dict[str, str] = {}
    for index, item in enumerate(rows):
        if not isinstance(item, Mapping) or not required.issubset(item) or set(item) - (required | {"provenance"}):
            raise O2ContractError(f"deck[{index}] does not match schema")
        deck_id = _required_str(item["deck_id"], "deck_id")
        if deck_id in result:
            raise O2ContractError(f"duplicate deck_id {deck_id!r}")
        cards = item["cards"]
        if not isinstance(cards, list) or any(type(card) is not int for card in cards):
            raise O2ContractError("cards must be integer list")
        content_hash = deck_content_hash(cards)
        if item["deck_hash"] != content_hash:
            raise O2ContractError(f"deck {deck_id!r} deck_hash mismatch")
        if content_hash in seen_content:
            raise O2ContractError(f"duplicate deck content: {deck_id!r} / {seen_content[content_hash]!r}")
        seen_content[content_hash] = deck_id
        roles = item["roles"]
        if not isinstance(roles, list) or any(not isinstance(role, str) or not role for role in roles):
            raise O2ContractError("roles must be non-empty strings")
        provenance = item.get("provenance", {})
        if not isinstance(provenance, Mapping):
            raise O2ContractError("provenance must be an object")
        result[deck_id] = DeckEntry(deck_id, _required_str(item["deck_version"], "deck_version"), tuple(cards), content_hash, _required_str(item["archetype"], "archetype"), _required_str(item["variant"], "variant"), tuple(sorted(roles)), _required_str(item["source"], "source"), _required_str(item["permission_scope"], "permission_scope"), item["valid_from"] if item["valid_from"] is None else _required_str(item["valid_from"], "valid_from"), item["valid_until"] if item["valid_until"] is None else _required_str(item["valid_until"], "valid_until"), _required_str(item["confidence"], "confidence"), dict(provenance))
    return result


_AGENT_KINDS = frozenset({"rule_v0", "student_v0", "bounded_search_v0", "random_legal", "registered"})


@dataclass(frozen=True, slots=True)
class OpponentEntry:
    opponent_id: str
    agent_kind: str
    agent_factory: str
    agent_version: str
    artifact_reference: str | None
    implementation_hash: str
    allowed_decks: tuple[str, ...]
    configuration: Mapping[str, object]
    source: str
    permission_scope: str
    enabled: bool


def load_opponent_pool(path: str | Path, *, deck_ids: Iterable[str]) -> dict[str, OpponentEntry]:
    raw = _load_mapping(path)
    if raw.get("schema_version") != OPPONENT_POOL_SCHEMA:
        raise O2ContractError("unsupported opponent pool schema_version")
    rows = raw.get("opponents")
    if not isinstance(rows, list) or not rows:
        raise O2ContractError("opponents must be a non-empty list")
    required = {"opponent_id", "agent_kind", "agent_factory", "agent_version", "artifact_reference", "implementation_hash", "allowed_decks", "configuration", "source", "permission_scope", "enabled"}
    known_decks, result = set(deck_ids), {}
    for index, item in enumerate(rows):
        if not isinstance(item, Mapping) or set(item) != required:
            raise O2ContractError(f"opponent[{index}] does not match schema")
        oid, kind = _required_str(item["opponent_id"], "opponent_id"), _required_str(item["agent_kind"], "agent_kind")
        if oid in result or kind not in _AGENT_KINDS:
            raise O2ContractError(f"duplicate opponent or unknown agent kind {oid!r}/{kind!r}")
        decks = item["allowed_decks"]
        if not isinstance(decks, list) or not decks or any(not isinstance(deck, str) for deck in decks) or set(decks) - known_decks:
            raise O2ContractError(f"opponent {oid!r} has invalid allowed_decks")
        impl = _required_str(item["implementation_hash"], "implementation_hash").lower()
        if len(impl) != 64 or any(char not in "0123456789abcdef" for char in impl):
            raise O2ContractError("implementation_hash must be sha256 hex")
        if not isinstance(item["configuration"], Mapping) or type(item["enabled"]) is not bool or (item["artifact_reference"] is not None and not isinstance(item["artifact_reference"], str)):
            raise O2ContractError(f"opponent {oid!r} configuration is invalid")
        result[oid] = OpponentEntry(oid, kind, _required_str(item["agent_factory"], "agent_factory"), _required_str(item["agent_version"], "agent_version"), item["artifact_reference"], impl, tuple(sorted(decks)), dict(item["configuration"]), _required_str(item["source"], "source"), _required_str(item["permission_scope"], "permission_scope"), item["enabled"])
    return result


@dataclass(frozen=True, slots=True)
class MatchSpec:
    match_id: str
    plan_hash: str
    seed: int
    player_a_agent: str
    player_b_agent: str
    player_a_deck: str
    player_b_deck: str
    first_player: int
    engine_version: str
    created_from_manifest: str
    pair_id: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {"schema_version": MATCH_PLAN_SCHEMA, "match_id": self.match_id, "plan_hash": self.plan_hash, "seed": self.seed, "player_a_agent": self.player_a_agent, "player_b_agent": self.player_b_agent, "player_a_deck": self.player_a_deck, "player_b_deck": self.player_b_deck, "first_player": self.first_player, "engine_version": self.engine_version, "created_from_manifest": self.created_from_manifest, "pair_id": self.pair_id}


def _identity(payload: Mapping[str, object]) -> tuple[str, str]:
    semantic = {key: value for key, value in payload.items() if key not in {"run_id", "created_at", "output_path"}}
    plan_hash = _digest(semantic, "o2-match-plan")
    return f"match_{plan_hash[:24]}", plan_hash


def build_match_matrix(*, decks: Mapping[str, DeckEntry], opponents: Mapping[str, OpponentEntry], challenger_id: str, opponent_ids: Sequence[str], seeds: Sequence[int], engine_version: str, created_from_manifest: str, paired: bool = True) -> list[MatchSpec]:
    if challenger_id not in opponents or not opponents[challenger_id].enabled or not seeds or any(type(seed) is not int or seed < 0 for seed in seeds):
        raise O2ContractError("challenger must be enabled and seeds must be non-empty non-negative ints")
    if not engine_version or not created_from_manifest:
        raise O2ContractError("engine_version and created_from_manifest are required")
    specs: list[MatchSpec] = []
    challenger = opponents[challenger_id]
    for opponent_id in sorted(opponent_ids):
        opponent = opponents.get(opponent_id)
        if opponent is None or not opponent.enabled:
            raise O2ContractError(f"unknown or disabled opponent {opponent_id!r}")
        for challenger_deck in challenger.allowed_decks:
            for opponent_deck in opponent.allowed_decks:
                for seed in sorted(set(seeds)):
                    pair = _digest({"challenger": challenger_id, "opponent": opponent_id, "challenger_deck": challenger_deck, "opponent_deck": opponent_deck, "seed": seed, "engine": engine_version, "manifest": created_from_manifest}, "o2-seat-pair") if paired else None
                    for seat in ((0, 1) if paired else (0,)):
                        aa, bb = (challenger_id, opponent_id) if seat == 0 else (opponent_id, challenger_id)
                        ad, bd = (challenger_deck, opponent_deck) if seat == 0 else (opponent_deck, challenger_deck)
                        raw = {"seed": seed, "player_a_agent": aa, "player_b_agent": bb, "player_a_deck": ad, "player_b_deck": bd, "first_player": seat, "engine_version": engine_version, "created_from_manifest": created_from_manifest, "pair_id": pair}
                        match_id, plan_hash = _identity(raw)
                        specs.append(MatchSpec(match_id, plan_hash, seed, aa, bb, ad, bd, seat, engine_version, created_from_manifest, pair))
    return sorted(specs, key=lambda item: item.match_id)


def execute_match_plan(specs: Sequence[MatchSpec], *, output_dir: str | Path, backend: Callable[[MatchSpec], Mapping[str, object]], backend_kind: str) -> dict[str, object]:
    if backend_kind not in {"cabt", "fixture_backend"}:
        raise O2ContractError("backend_kind must be cabt or fixture_backend")
    root, records = Path(output_dir), []
    for spec in specs:
        target = root / "matches" / spec.match_id / "normalized.json"
        if target.is_file():
            try: record = json.loads(target.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc: raise O2ContractError(f"corrupt record {spec.match_id}") from exc
            if record.get("plan_hash") != spec.plan_hash: raise O2ContractError(f"plan mismatch for {spec.match_id}")
        else:
            try:
                raw, status = dict(backend(spec)), None
                status = raw.get("status") if isinstance(raw.get("status"), str) else "ERROR"
                record = {"schema_version": MATCH_RESULT_SCHEMA, "match_id": spec.match_id, "plan_hash": spec.plan_hash, "backend_kind": backend_kind, "status": status, "winner": raw.get("winner"), "elapsed_seconds": raw.get("elapsed_seconds"), "timeout": status in {"AGENT_TIMEOUT", "STEP_LIMIT"}, "fallback_events": list(raw.get("fallback_events", [])) if isinstance(raw.get("fallback_events", []), list) else [], "agent_exception": raw.get("agent_exception") if isinstance(raw.get("agent_exception"), str) else None, "termination_reason": raw.get("termination_reason") if isinstance(raw.get("termination_reason"), str) else None, "engine_version": raw.get("engine_version") if isinstance(raw.get("engine_version"), str) else None, "engine_seed_supported": raw.get("engine_seed_supported") if type(raw.get("engine_seed_supported")) is bool else None, "deck_a_hash": raw.get("deck_a_hash") if isinstance(raw.get("deck_a_hash"), str) else None, "deck_b_hash": raw.get("deck_b_hash") if isinstance(raw.get("deck_b_hash"), str) else None, "public_trace": raw.get("public_trace") if isinstance(raw.get("public_trace"), Mapping) else None}
            except Exception as exc:
                record = {"schema_version": MATCH_RESULT_SCHEMA, "match_id": spec.match_id, "plan_hash": spec.plan_hash, "backend_kind": backend_kind, "status": "ERROR", "winner": None, "elapsed_seconds": None, "timeout": False, "fallback_events": [], "agent_exception": type(exc).__name__, "termination_reason": None, "engine_version": None, "engine_seed_supported": None, "deck_a_hash": None, "deck_b_hash": None, "public_trace": None}
            atomic_write_json(target, record)
        records.append(record)
    summary = {"schema_version": "o2-match-batch-v1", "backend_kind": backend_kind, "planned": len(specs), "completed": len(records), "status_counts": dict(sorted(Counter(str(item["status"]) for item in records).items())), "match_ids": sorted(str(item["match_id"]) for item in records)}
    atomic_write_json(root / "batch_manifest.json", summary)
    return summary


def ingest_rule_bc_replay(
    path: str | Path, *, source_id: str, source_envelope: object,
    selection_policy: str = "TRAINING_HIGH_INFORMATION_VERIFIED",
) -> dict[str, object]:
    """Normalize an existing C4 replay and apply its established eligibility gate.

    This is deliberately a reader, not a second replay-identity or dataset
    implementation.  It makes permission and every exclusion reason explicit;
    callers pass the resulting eligible records to the existing O1 offline
    adapter / Offline Training v1 builder rather than copying rows here.
    """
    from mage_ptcg.competition_intelligence.contracts import AllowedUse, SourceEnvelope
    from mage_ptcg.competition_intelligence.decision_eligibility import compute_decision_eligibility
    from mage_ptcg.competition_intelligence.permissions import has_permission
    from mage_ptcg.competition_intelligence.replay_normalize import normalize_rule_bc_jsonl
    if not isinstance(source_envelope, SourceEnvelope):
        raise O2ContractError("source_envelope must be a SourceEnvelope")
    normalized = normalize_rule_bc_jsonl(path, source_id=source_id)
    permitted = has_permission(source_envelope, AllowedUse.TRAINING)
    permission_by_episode = {episode.episode_id: permitted for episode in normalized.episodes}
    eligibility = compute_decision_eligibility(
        normalized.decisions, permission_granted_by_episode=permission_by_episode,
        policy=selection_policy,
    )
    reasons = Counter(reason for item in eligibility for reason in item.training_eligibility_reasons)
    return {
        "schema_version": "o2-replay-ingestion-v1", "source_id": source_id,
        "source_permission_training": permitted, "episode_count": len(normalized.episodes),
        "decision_count": len(normalized.decisions), "eligible_decision_count": sum(item.training_eligible for item in eligibility),
        "excluded_reasons": dict(sorted(reasons.items())), "quarantined_rows": len(normalized.quarantined_rows),
        "normalization": normalized, "eligibility": tuple(eligibility),
    }


def paired_evaluation(specs: Sequence[MatchSpec], records: Sequence[Mapping[str, object]]) -> dict[str, object]:
    by_spec = {item.match_id: item for item in specs}; pairs: dict[str, list[tuple[MatchSpec, Mapping[str, object]]]] = defaultdict(list)
    for record in records:
        spec = by_spec.get(record.get("match_id"))
        if spec and spec.pair_id: pairs[spec.pair_id].append((spec, record))
    outcomes, incomplete = [], 0
    for entries in pairs.values():
        if len(entries) != 2 or {item[0].first_player for item in entries} != {0, 1} or any(item[1].get("status") != "DONE" for item in entries): incomplete += 1; continue
        outcomes.append(statistics.fmean(1.0 if record.get("winner") == spec.first_player else .5 if record.get("winner") == 2 else 0.0 for spec, record in entries))
    n = len(outcomes); mean = statistics.fmean(outcomes) if outcomes else None; se = statistics.stdev(outcomes) / math.sqrt(n) if n > 1 else None
    ci = [max(0., mean - 1.96 * se), min(1., mean + 1.96 * se)] if mean is not None and se is not None else None
    elapsed = sorted(float(row["elapsed_seconds"]) for row in records if isinstance(row.get("elapsed_seconds"), (int, float)))
    def q(frac: float) -> float | None:
        if not elapsed: return None
        pos = (len(elapsed)-1)*frac; lo = int(pos); hi = min(lo+1, len(elapsed)-1); return elapsed[lo]+(elapsed[hi]-elapsed[lo])*(pos-lo)
    return {"schema_version": "o2-paired-evaluation-v1", "paired_games": n, "incomplete_pairs": incomplete, "challenger_win_rate": mean, "paired_win_difference": None if mean is None else mean-.5, "confidence_interval_95": ci, "legality_failures": sum(row.get("status") == "AGENT_INVALID" for row in records), "timeouts": sum(bool(row.get("timeout")) for row in records), "fallbacks": sum(len(row.get("fallback_events", [])) for row in records if isinstance(row.get("fallback_events"), list)), "failed_matches": sum(row.get("status") != "DONE" for row in records), "latency_seconds": {"p50":q(.5),"p95":q(.95),"p99":q(.99)}}


def promotion_report(evaluation: Mapping[str, object], *, champion: str = "Rule Agent v0", minimum_pairs: int = 100) -> dict[str, object]:
    pairs, unsafe, ci = evaluation.get("paired_games"), sum(int(evaluation.get(key, 0) or 0) for key in ("legality_failures", "timeouts", "fallbacks", "failed_matches")), evaluation.get("confidence_interval_95")
    if not isinstance(pairs, int) or pairs < minimum_pairs: decision, reason = "INSUFFICIENT_EVIDENCE", "minimum paired sample not met"
    elif unsafe: decision, reason = "REJECT", "safety or execution failure"
    elif not isinstance(ci, list) or not ci or float(ci[0]) <= .5: decision, reason = "HOLD", "non-inferiority not established"
    else: decision, reason = "RECOMMEND_PROMOTION", "paired confidence interval exceeds parity"
    report = {"schema_version": PROMOTION_REPORT_SCHEMA, "decision": decision, "reason": reason, "champion_before": champion, "champion_after": champion, "automatic_champion_change": False, "evaluation": dict(evaluation)}
    return {**report, "report_hash": _digest(report, "o2-promotion-report")}
