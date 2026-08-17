#!/usr/bin/env python3
"""Audit qualified teacher physical-action -> V4 semantic -> physical round trips.

This is an offline, read-only audit.  It deliberately does not collect teacher
games, train a checkpoint, evaluate CABT games, or modify any production
adapter.  The audit replays the same state-aware C1/V4 semantic legality and
private runtime decoder used by training/runtime callers, then reports which
edge cases are present in the sealed corpora and which require a fixture.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
import math
from pathlib import Path
import sys
import time
from typing import Any, Iterable

from mage_ptcg.meta_specialist.actor_visible_features_v1 import (
    CardVocabularyV1,
    choose_lexicographic_alias_v1,
    extract_specialist_model_input_v1,
)
from mage_ptcg.meta_specialist.actor_visible_v2 import (
    deserialize_actor_visible_decision_state_v2,
)
from mage_ptcg.meta_specialist.card_vocabulary_registry_v1 import (
    load_production_card_vocabulary_v1,
)
from mage_ptcg.meta_specialist.cabt_json_contract_v1 import is_ordered_selection
from mage_ptcg.meta_specialist.local_dataset_v2 import (
    _state_payload_from_record,
    validate_local_record_v2,
)
from mage_ptcg.meta_specialist.runtime_actions_v2 import (
    RuntimeDecisionEnvelope,
    semantic_runtime_complete_action_from_runtime_action_v2,
)
from mage_ptcg.meta_specialist.training_example_envelope_v2 import (
    semantic_action_from_training_payload_v2,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CORPORA = (
    ROOT / "runs/meta-specialist-teacher-records/archaludon-teacher-tomatomato-24",
    ROOT / "runs/meta-specialist-teacher-records/archaludon-teacher-tomatomato-96",
    ROOT / "runs/meta-specialist-teacher-records/archaludon-teacher-lucifer19-48",
)


def _canonical_semantic(row: object) -> bytes:
    return row.canonical_bytes  # SemanticActionV1 intentionally exposes this.


def _record_files(corpus: Path) -> Iterable[Path]:
    records = corpus / "records"
    if not records.is_dir():
        raise FileNotFoundError(f"teacher records directory does not exist: {records}")
    return sorted(records.glob("game-*.jsonl"))


def _read_json_lines(path: Path) -> Iterable[tuple[int, dict[str, Any]]]:
    with path.open("rb") as handle:
        for line_number, raw in enumerate(handle, 1):
            if not raw.endswith(b"\n"):
                raise ValueError(f"{path}:{line_number}: record line is not newline terminated")
            value = json.loads(raw.decode("utf-8"))
            if type(value) is not dict:
                raise ValueError(f"{path}:{line_number}: record is not an object")
            yield line_number, value


def _increment_histogram(counter: Counter[str], value: object) -> None:
    counter[str(value)] += 1


def _roundtrip_record(
    record: dict[str, Any], *, vocabulary: CardVocabularyV1, counters: Counter[str], histograms: dict[str, Counter[str]],
) -> None:
    """Replay every teacher mass row through the V4 decoder and physical bridge."""
    # This is intentionally called even though the record was sealed.  The
    # audit must verify the authority boundary independently of the collector.
    validate_local_record_v2(record, vocabulary=vocabulary)
    information_state = record["information_state"]
    legal_actions = record["legal_actions"]
    state = deserialize_actor_visible_decision_state_v2(
        _state_payload_from_record(information_state, legal_actions)
    )
    extracted = extract_specialist_model_input_v1(state, vocabulary)
    envelope = RuntimeDecisionEnvelope.from_actor_visible_state(
        state, vocabulary=vocabulary
    )
    local_ids = {row["local_action_id"] for row in legal_actions}
    semantic_by_local_id = {
        row["local_action_id"]: semantic_action_from_training_payload_v2(
            row["semantic_action"], field="legal_action.semantic_action"
        )
        for row in legal_actions
    }

    # The stored feature row must be exactly what the typed C1 binding projects.
    for action in state.legal_actions:
        expected = semantic_by_local_id[action.local_action_id]
        rebuilt = semantic_action_from_training_payload_v2(
            extracted.model_input.candidate_rows[
                extracted.local_action_id_to_candidate_row_index[action.local_action_id]
            ].to_dict(),
            field="rebuilt.semantic_action",
        )
        if expected != rebuilt:
            raise ValueError("stored semantic candidate differs from rebuilt C1/V4 row")

    schema = (
        state.information_view.selection_type,
        state.information_view.selection_context,
    )
    ordered = is_ordered_selection(*schema)
    _increment_histogram(histograms["selection_schema"], f"{schema[0]}:{schema[1]}")
    _increment_histogram(histograms["min_max"], f"{state.information_view.min_count}:{state.information_view.max_count}")
    counters["ordered_records" if ordered else "unordered_records"] += 1

    # Alias groups are the complete semantic-key collision report.  A key is
    # duplicate only when distinct physical local IDs share one semantic row.
    aliases: dict[bytes, list[str]] = defaultdict(list)
    for local_id, semantic in semantic_by_local_id.items():
        aliases[_canonical_semantic(semantic)].append(local_id)
    duplicate_groups = [ids for ids in aliases.values() if len(ids) > 1]
    counters["duplicate_semantic_key_groups"] += len(duplicate_groups)
    counters["records_with_duplicate_semantic_key"] += bool(duplicate_groups)
    counters["max_alias_multiplicity"] = max(
        counters["max_alias_multiplicity"],
        max((len(ids) for ids in aliases.values()), default=0),
    )

    teacher = record["teacher"]
    mass_rows = teacher["mass_rows"]
    if teacher["target_kind"] != "hard_selection":
        counters["non_hard_teacher_records"] += 1
    for mass_row in mass_rows:
        selection = tuple(mass_row["selection"])
        if any(local_id not in local_ids for local_id in selection):
            raise ValueError("teacher selection references a missing physical legal action")
        counters["teacher_mass_rows"] += 1
        if not selection:
            counters["empty_selection_rows"] += 1
        if ordered:
            semantic_target = tuple(semantic_by_local_id[local_id] for local_id in selection)
        else:
            semantic_target = tuple(sorted(
                (semantic_by_local_id[local_id] for local_id in selection),
                key=lambda row: row.canonical_bytes,
            ))
        counters["selected_alias_rows"] += sum(
            len(aliases[_canonical_semantic(semantic_by_local_id[local_id])]) > 1
            for local_id in selection
        )
        counters["selected_end_rows"] += any(row.option_type == 14 for row in semantic_target)
        counters["selected_retreat_rows"] += any(row.option_type == 12 for row in semantic_target)

        # This is the semantic -> physical half of the V4 decoder.  Alias
        # selection is deterministic and occurs only after the semantic class
        # has been selected.  The resulting private action is then revalidated
        # against the current legal option indices.
        decoded_local_ids: tuple[str, ...] = ()
        for semantic_row in semantic_target:
            decoded_local_ids = (*decoded_local_ids, choose_lexicographic_alias_v1(
                extracted, decoded_local_ids, semantic_row,
            ))
        if ordered:
            physical_same = decoded_local_ids == selection
        else:
            # Unordered selections are physical multisets.  A different order
            # is not an alias failure, while a different local-ID multiset is
            # a deterministic alias substitution that still must preserve the
            # semantic class multiset.
            physical_same = tuple(sorted(decoded_local_ids)) == tuple(sorted(selection))
            if physical_same and decoded_local_ids != selection:
                counters["decoded_unordered_reordered_rows"] += 1
        if physical_same:
            counters["decoded_physical_exact_rows"] += 1
        else:
            counters["decoded_physical_alias_substitution_rows"] += 1
        action = envelope.complete_action(decoded_local_ids)
        envelope.decode_option_indices(action)
        decoded = semantic_runtime_complete_action_from_runtime_action_v2(
            envelope, action,
        )
        if decoded.semantic_selection != semantic_target:
            raise ValueError("V4 semantic decoder changed the teacher target semantics")
        counters["roundtrip_rows"] += 1
        if len(selection) < state.information_view.min_count or len(selection) > state.information_view.max_count:
            raise ValueError("teacher selection violates min_count/max_count")
        # The final STOP is implicit in every complete action.  This explicitly
        # records whether the stop endpoint was legal at the reconstructed path.
        stop_input = envelope.build_step_input(decoded_local_ids)
        if not stop_input.stop_available:
            raise ValueError("decoded complete action does not expose legal final STOP")
        counters["legal_final_stop_rows"] += 1


def _fixture_smoke() -> dict[str, Any]:
    """Run edge fixtures absent (or rare) in the sealed corpus.

    Existing runtime tests cover the same frozen decoder, but keeping these
    fixtures in the audit result makes the corpus coverage gap explicit.
    """
    from mage_ptcg.meta_specialist.actor_visible_features_v1 import make_test_card_vocabulary_v1
    from mage_ptcg.meta_specialist.actor_visible_v2 import build_actor_visible_decision_state_v2

    def card(card_id: int, serial: int, owner: int) -> dict[str, int]:
        return {"id": card_id, "serial": serial, "playerIndex": owner}

    def pokemon(card_id: int, serial: int) -> dict[str, Any]:
        return {
            "id": card_id, "serial": serial, "hp": 100, "maxHp": 120,
            "appearThisTurn": False, "energies": [1], "energyCards": [],
            "tools": [], "preEvolution": [],
        }

    def player(hand: Any, *, active: list[Any] | None = None) -> dict[str, Any]:
        return {
            "active": [] if active is None else active, "asleep": False, "bench": [],
            "benchMax": 5, "burned": False, "confused": False, "deckCount": 53,
            "discard": [], "hand": hand, "handCount": len(hand) if isinstance(hand, list) else 0,
            "paralyzed": False, "poisoned": False, "prize": [None] * 6,
        }

    def base() -> dict[str, Any]:
        hand = [card(101, 1001, 0), card(102, 1002, 0)]
        return {
            "current": {
                "energyAttached": False, "firstPlayer": 0, "looking": None,
                "players": [player(hand, active=[pokemon(201, 2001)]), player(None, active=[pokemon(301, 3001)])],
                "result": -1, "retreated": False, "stadium": [], "stadiumPlayed": False,
                "supporterPlayed": False, "turn": 2, "turnActionCount": 3, "yourIndex": 0,
            },
            "select": {
                "context": 1, "contextCard": None, "deck": None, "effect": None,
                "maxCount": 2, "minCount": 0,
                "option": [
                    {"type": 3, "area": 2, "index": 0, "playerIndex": 0},
                    {"type": 3, "area": 2, "index": 1, "playerIndex": 0},
                ],
                "remainDamageCounter": 0, "remainEnergyCost": 0, "type": 1,
            },
            "step": 7,
        }

    vocabulary = make_test_card_vocabulary_v1(range(1, 2_000))
    result: dict[str, Any] = {"status": "PASS", "cases": {}}

    def run_case(name: str, observation: dict[str, Any], selections: list[tuple[int, ...]]) -> None:
        state = build_actor_visible_decision_state_v2(observation)
        envelope = RuntimeDecisionEnvelope.from_actor_visible_state(state, vocabulary=vocabulary)
        extracted = extract_specialist_model_input_v1(state, vocabulary)
        semantic_by_id = {
            action.local_action_id: extracted.model_input.candidate_rows[
                extracted.local_action_id_to_candidate_row_index[action.local_action_id]
            ]
            for action in state.legal_actions
        }
        checked = 0
        for selection_indices in selections:
            selected_ids = tuple(state.legal_actions[index].local_action_id for index in selection_indices)
            if envelope._order_semantics == "ordered_sequence":
                target = tuple(semantic_by_id[item] for item in selected_ids)
            else:
                target = tuple(sorted((semantic_by_id[item] for item in selected_ids), key=lambda row: row.canonical_bytes))
            aliases: tuple[str, ...] = ()
            for row in target:
                aliases = (*aliases, choose_lexicographic_alias_v1(extracted, aliases, row))
            action = envelope.complete_action(aliases)
            envelope.decode_option_indices(action)
            decoded = semantic_runtime_complete_action_from_runtime_action_v2(envelope, action)
            if decoded.semantic_selection != target:
                raise ValueError(f"fixture {name} semantic mismatch")
            checked += 1
        result["cases"][name] = {"status": "PASS", "rows": checked, "order_semantics": envelope._order_semantics}

    empty = base()
    run_case("empty_selection", empty, [()])
    duplicate = base()
    duplicate["current"]["players"][0]["hand"] = [card(101, 1001, 0), card(101, 1002, 0)]
    duplicate["current"]["players"][0]["handCount"] = 2
    run_case("duplicate_semantic_alias", duplicate, [(), (0,), (1,)])
    ordered = base()
    ordered["select"] = {
        "context": 34, "contextCard": None, "deck": None, "effect": None,
        "maxCount": 2, "minCount": 0,
        "option": [
            {"type": 15, "cardId": 101, "serial": 1001},
            {"type": 15, "cardId": 102, "serial": 1002},
        ],
        "remainDamageCounter": 0, "remainEnergyCost": 0, "type": 5,
    }
    run_case("ordered_prefix", ordered, [(0, 1), (1, 0), ()])
    retreated = base()
    retreated["select"] = {
        "context": 0, "contextCard": None, "deck": None, "effect": None,
        "maxCount": 1, "minCount": 1, "option": [{"type": 12}],
        "remainDamageCounter": 0, "remainEnergyCost": 0, "type": 0,
    }
    run_case("retreat", retreated, [(0,)])
    ended = base()
    ended["select"] = {
        "context": 0, "contextCard": None, "deck": None, "effect": None,
        "maxCount": 1, "minCount": 1, "option": [{"type": 14}],
        "remainDamageCounter": 0, "remainEnergyCost": 0, "type": 0,
    }
    run_case("end_option", ended, [(0,)])
    return result


def audit(corpora: Iterable[Path]) -> dict[str, Any]:
    vocabulary = load_production_card_vocabulary_v1()
    started = time.monotonic()
    totals = Counter()
    histograms: dict[str, Counter[str]] = {
        "selection_schema": Counter(), "min_max": Counter(), "teacher_id": Counter(),
        "failure": Counter(),
    }
    corpus_reports: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    last_progress = started
    for corpus in corpora:
        corpus = corpus.resolve()
        report_before = Counter(totals)
        records = files = 0
        for path in _record_files(corpus):
            files += 1
            for line_number, record in _read_json_lines(path):
                records += 1
                totals["records_seen"] += 1
                teacher_id = record.get("teacher", {}).get("teacher_id")
                _increment_histogram(histograms["teacher_id"], teacher_id)
                try:
                    _roundtrip_record(record, vocabulary=vocabulary, counters=totals, histograms=histograms)
                    totals["records_passed"] += 1
                except Exception as exc:  # audit all records; report every category without aborting the corpus.
                    totals["records_failed"] += 1
                    failure_key = type(exc).__name__ + ":" + str(exc)
                    histograms["failure"][failure_key] += 1
                    if len(failures) < 100:
                        failures.append({
                            "corpus": str(corpus), "path": str(path), "line": line_number,
                            "record_id": record.get("record_id"), "error_type": type(exc).__name__,
                            "error": str(exc),
                        })
                now = time.monotonic()
                if now - last_progress >= 10.0:
                    print(json.dumps({"stage": "teacher_projection_roundtrip", "records_seen": totals["records_seen"], "records_passed": totals["records_passed"], "records_failed": totals["records_failed"]}, ensure_ascii=False, sort_keys=True), file=sys.stderr, flush=True)
                    last_progress = now
        delta = Counter(totals)
        for key, value in report_before.items():
            delta[key] -= value
        corpus_reports.append({
            "corpus": str(corpus), "record_files": files, "records": records,
            "records_passed": delta["records_passed"], "records_failed": delta["records_failed"],
            "teacher_id": sorted({str(record.get("teacher", {}).get("teacher_id")) for path in _record_files(corpus) for _line, record in _read_json_lines(path)}),
        })
    return {
        "schema": "meta-specialist-teacher-projection-roundtrip-audit-v1",
        "status": "PASS" if totals["records_failed"] == 0 else "FAIL",
        "scope": {
            "corpora": [str(Path(path).resolve()) for path in corpora],
            "production_vocabulary": vocabulary.to_manifest_dict(),
            "decoder": "mage_ptcg.meta_specialist.runtime_actions_v2.RuntimeDecisionEnvelope + semantic_runtime_complete_action_from_runtime_action_v2",
            "contract": "physical teacher action -> semantic class -> V4 shared legality/decoder -> current physical legal action",
        },
        "totals": dict(sorted(totals.items())),
        "histograms": {key: dict(sorted(value.items())) for key, value in sorted(histograms.items())},
        "corpora": corpus_reports,
        "fixture_smoke": _fixture_smoke(),
        "failures": failures,
        "elapsed_seconds": round(time.monotonic() - started, 3),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", action="append", type=Path, dest="corpora", help="qualified teacher corpus root; repeatable")
    parser.add_argument("--output", type=Path, required=True, help="JSON report path")
    args = parser.parse_args()
    corpora = tuple(args.corpora or DEFAULT_CORPORA)
    report = audit(corpora)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_name(f".{args.output.name}.tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(args.output)
    print(json.dumps({"status": report["status"], "records_seen": report["totals"].get("records_seen", 0), "records_failed": report["totals"].get("records_failed", 0), "output": str(args.output.resolve())}, ensure_ascii=False, sort_keys=True))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
