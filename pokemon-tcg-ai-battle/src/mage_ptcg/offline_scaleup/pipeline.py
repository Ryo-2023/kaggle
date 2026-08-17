"""Local-only Opponent Factory, resumable League, Dataset, and Student v1 CLI.

The module deliberately delegates legality and actual CABT execution to the
already-reviewed project code.  It adds the durable orchestration boundary:
immutable population/schedule manifests, append-only results, fail-closed
dataset materialization, and a versioned legal-candidate Student artifact.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from contextlib import contextmanager
from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import signal
import statistics
import sys
import tempfile
import threading
import time
from typing import Any, Iterable, Mapping

from mage_ptcg.student.dataset import RuleBCExample, build_rule_bc_example
from mage_ptcg.student.evaluation import evaluate_model
from mage_ptcg.student.model import StudentV0Model, train_model
from mage_ptcg.offline_scaleup.progress import ProgressReporter
from mage_ptcg.offline_scaleup.candidate_runtime import INTERNAL_FAMILY_LOADER, POLICY_LEARNING_LOADER, STUDENT_V2_LOADER, CandidateRuntimeError, _sha256_file, adapter_for, write_trajectory


POPULATION_SCHEMA = "offline-scaleup-population-v2"
SCHEDULE_SCHEMA = "offline-scaleup-schedule-v2"
RESULT_SCHEMA = "offline-scaleup-result-v2"
DATASET_SCHEMA = "offline-scaleup-dataset-v2"
STUDENT_SCHEMA = "offline-scaleup-student-v1"
OPPONENT_TYPES = {"RULE_V0_DECK", "TEAM_NATIVE", "FAMILY_SPECIFIC", "SEARCH_AGENT", "STUDENT_AGENT", "CHAMPION_ARCHIVE"}
TERMINAL = {"DONE", "AGENT_TIMEOUT", "AGENT_ERROR", "AGENT_INVALID", "STEP_LIMIT", "ERROR"}
FORBIDDEN = {"opponent_hand", "hidden_deck", "deck_order", "prize_contents", "future", "raw_observation", "raw_steps"}
RESULT_PREFIX = "OFFLINE_SCALEUP_RESULT:"
TEAM_NATIVE_LOADER = "team_native_subprocess_v1"
FAMILY_LOADER = "family_specific_external_v1"


def default_worker_count() -> int:
    """CPU-affinity-based default: ~80% of usable cores, minimum 1.

    No psutil dependency is pinned in this project, so this is a CPU-only
    approximation; there is no GPU-bound step anywhere in this pipeline.
    """
    try:
        cpu = len(os.sched_getaffinity(0))
    except AttributeError:
        cpu = os.cpu_count() or 1
    return max(1, int(cpu * 0.8))


class ContractError(ValueError):
    """A persisted pipeline contract is malformed or unsafe to consume."""


def _candidate_callback_timeout_seconds() -> float | None:
    """Optional per-callback watchdog for isolated CABT worker processes."""
    value = os.environ.get("OFFLINE_SCALEUP_CANDIDATE_CALLBACK_TIMEOUT_SECONDS")
    if value in (None, "", "0"):
        return None
    try:
        seconds = float(value)
    except ValueError as exc:
        raise ContractError("candidate callback timeout is malformed") from exc
    if seconds <= 0 or not seconds < 1800:
        raise ContractError("candidate callback timeout must be in (0, 1800)")
    return seconds


def candidate_callback_watchdog_mode(seconds: float | None) -> str:
    """Report which watchdog layer is actually active for this process.

    The signal watchdog is a *diagnostic* aid, not the production stop
    boundary: it only works on a process main thread.  The real boundary is
    the outer per-game process timeout in ``_execute_league_job``, which owns
    termination regardless of what this returns.  Reporting the mode keeps a
    silently inactive watchdog from being documented as an enforced one.
    """
    if seconds is None:
        return "disabled"
    if not hasattr(signal, "setitimer"):
        return "unavailable_platform"
    if threading.current_thread() is not threading.main_thread():
        return "unavailable_non_main_thread"
    return "signal_main_thread"


@contextmanager
def _candidate_callback_watchdog(seconds: float | None):
    """Interrupt one candidate callback without changing the global game cap.

    Real CABT workers execute this code on their process main thread.  The
    worker process is already disposable, so an interrupted callback is
    classified as a candidate fault instead of waiting for the outer game
    timeout and losing all attribution.

    Off the main thread the signal layer cannot arm at all.  It degrades to a
    recorded no-op rather than raising, because the outer process timeout
    still bounds the game; the caller must surface the mode so that the
    inactive case is never reported as an enforced watchdog.
    """
    mode = candidate_callback_watchdog_mode(seconds)
    if mode != "signal_main_thread":
        yield
        return
    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.setitimer(signal.ITIMER_REAL, 0)
    def expired(_signum: int, _frame: object) -> None:
        raise TimeoutError(f"candidate callback exceeded {seconds:.3f} seconds")
    signal.signal(signal.SIGALRM, expired)
    started = time.monotonic()
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        # Restore the outer timer at its *remaining* deadline.  Re-arming it
        # with the value captured on entry would silently extend an enclosing
        # deadline by however long this callback ran.
        remaining = previous_timer[0] - (time.monotonic() - started)
        if previous_timer[0] > 0 and remaining > 0:
            signal.setitimer(signal.ITIMER_REAL, remaining, previous_timer[1])
        elif previous_timer[0] > 0:
            signal.setitimer(signal.ITIMER_REAL, 1e-6, previous_timer[1])


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))


def _digest(value: object, domain: str) -> str:
    return hashlib.sha256((domain + "\0" + _canonical(value)).encode()).hexdigest()


def _sha_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(_canonical(value) + "\n", encoding="utf-8")
    os.replace(temp, path)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractError(f"invalid JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ContractError(f"JSON root must be object: {path}")
    return value


def _write_jsonl_once(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(_canonical(dict(value)) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _jsonl(path: Path) -> Iterable[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ContractError(f"bad JSONL {path}:{line_no}") from exc
            if not isinstance(row, dict):
                raise ContractError(f"JSONL object required: {path}:{line_no}")
            rows.append(row)
    return rows


def _stdout_result_contract(stdout: str) -> tuple[dict[str, Any] | None, str | None]:
    """Strict compatibility parser for a prefixed child result line.

    The runner uses dedicated result files, but this parser keeps any future
    stdout adapter deterministic: warnings are ignored, zero or many result
    records are errors, and malformed claimed records are never accepted.
    """
    candidates = [line.removeprefix(RESULT_PREFIX) for line in stdout.splitlines() if line.startswith(RESULT_PREFIX)]
    if not candidates:
        return None, "NO_JSON_OUTPUT"
    if len(candidates) != 1:
        return None, "AMBIGUOUS_JSON_OUTPUT"
    try:
        value = json.loads(candidates[0])
    except json.JSONDecodeError:
        return None, "MALFORMED_JSON_OUTPUT"
    return (value, None) if isinstance(value, dict) else (None, "MALFORMED_JSON_OUTPUT")


def _contains_forbidden(value: object) -> bool:
    if isinstance(value, dict):
        return any(str(key).lower() in FORBIDDEN or _contains_forbidden(item) for key, item in value.items())
    if isinstance(value, list):
        return any(_contains_forbidden(item) for item in value)
    return False


def _deck_cards(deck_path: Path) -> list[int]:
    from main import read_deck_csv, validate_deck
    cards = list(read_deck_csv(deck_path))
    validate_deck(cards)
    if len(cards) != 60:
        raise ContractError("exactly 60 cards are required")
    return cards


def _rule_entry(repo: Path) -> dict[str, Any]:
    deck = repo / "deck.csv"
    cards = _deck_cards(deck)
    runtime = repo / "agents" / "rule_agent.py"
    fp = _digest(sorted(Counter(cards).items()), "deck-multiset")
    digest = _sha_file(runtime)
    return {
        "opponent_id": "rule-v0-current-deck", "opponent_type": "RULE_V0_DECK", "source_path": str(runtime),
        "deck_id": "current-deck", "deck_fingerprint": fp, "runtime_id": "rule-agent-v0",
        "runtime_fingerprint": digest, "agent_digest": digest, "validation_status": "VALIDATED",
        "availability_status": "AVAILABLE", "evaluation_eligibility": "ALLOWED",
        "training_eligibility": "ALLOWED_FOR_VALID_FAULT_FREE_GAMES", "teacher_trust": "TRUSTED",
        "quarantine_reason": None, "family_id": None, "strategy_tags": ["rule", "fallback"],
        "variant_tags": [], "evidence_paths": [str(deck), str(runtime)], "loader": "rule_v0",
        "deck_cards": cards,
    }


def _alakazam_entry(recovery_root: Path) -> dict[str, Any]:
    identity = _read_json(recovery_root / "artifacts" / "timeout_game_identity.json")
    verdict = _read_json(recovery_root / "artifacts" / "final_remediation_verdict.json")
    if verdict.get("verdict") != "READY_FOR_EXPANDED_META_EVALUATION":
        raise ContractError("Alakazam remediation evidence has unexpected verdict")
    return {
        "opponent_id": "alakazam-remediation-runtime-v1", "opponent_type": "FAMILY_SPECIFIC",
        "source_path": str(recovery_root), "deck_id": "deck-74d86ec36fd144b9",
        "deck_fingerprint": identity["deck_card_ids_sha256"], "runtime_id": "alakazam-family-runtime",
        "runtime_fingerprint": identity["runtime_fingerprint"], "agent_digest": identity["playbook_digest"],
        "validation_status": "VALIDATED", "availability_status": "AVAILABLE", "evaluation_eligibility": "ALLOWED",
        "training_eligibility": "ALLOWED_FOR_VALID_FAULT_FREE_GAMES", "teacher_trust": "LIMITED",
        "quarantine_reason": None, "family_id": "ALAKAZAM", "strategy_tags": ["family-specific", "remediated"],
        "variant_tags": [], "evidence_paths": [str(recovery_root / "artifacts" / "final_remediation_verdict.json"),
            str(recovery_root / "artifacts" / "recovery_attempt.json"), str(recovery_root / "artifacts" / "timeout_game_identity.json")],
        "loader": "external-evidence-only", "provenance": {"original_timeout": identity["game_id"],
            "timeout_attribution": verdict.get("timeout_attribution"), "limited_recovery": "legal DONE", "trust_promotion": "requires_stability_run"},
    }


def _family_cards(family_root: Path, deck_id: str) -> tuple[list[int], list[int]]:
    """Resolve an exact planned Family deck; never reconstruct one heuristically."""
    jobs = family_root / "artifacts" / "planned_game_manifest.jsonl"
    for line in jobs.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if row.get("deck_id") == deck_id:
            cards, primary = row.get("card_ids"), row.get("primary_ids")
            if isinstance(cards, list) and len(cards) == 60 and all(type(card) is int for card in cards) and isinstance(primary, list) and all(type(card) is int for card in primary):
                return list(cards), list(primary)
    raise ContractError(f"missing exact Family deck configuration: {deck_id}")


def _family_entry(*, family_root: Path, family_id: str, deck_id: str, evidence_paths: list[Path], trust: str = "LIMITED") -> dict[str, Any]:
    cards, primary_ids = _family_cards(family_root, deck_id)
    freeze = _read_json(family_root / "artifacts" / "runtime_freeze_manifest.json")
    runtime = _digest(freeze, "family-runtime-freeze")
    source = family_root / "family_agent" / "agent.py"
    return {
        "opponent_id": f"family-{family_id.lower()}-{deck_id}", "opponent_type": "FAMILY_SPECIFIC",
        "source_path": str(family_root), "deck_id": deck_id,
        "deck_fingerprint": _digest(sorted(Counter(cards).items()), "deck-multiset"),
        "runtime_id": "family-specific-playbook-v1", "runtime_fingerprint": runtime,
        "agent_digest": _sha_file(source), "validation_status": "VALIDATED",
        "availability_status": "AVAILABLE", "evaluation_eligibility": "ALLOWED",
        "training_eligibility": "ALLOWED_FOR_VALID_FAULT_FREE_GAMES", "teacher_trust": trust,
        "quarantine_reason": None, "family_id": family_id,
        "strategy_tags": ["family-specific", "playbook"], "variant_tags": [],
        "evidence_paths": [str(path) for path in evidence_paths], "loader": FAMILY_LOADER,
        "deck_cards": cards, "provenance": {"primary_ids": primary_ids,
            "family_runtime_root": str(family_root), "preflight_gate": "PASS"},
    }


def _team_native_entries(*, meta_root: Path) -> list[dict[str, Any]]:
    """Load the approved resolver target and its verified snapshot records."""
    module_root = str(meta_root)
    if module_root not in sys.path:
        sys.path.insert(0, module_root)
    from meta_opponent_lab.official_runtime import resolve_current_team_bundle_root
    bundle_root, resolution = resolve_current_team_bundle_root()
    scratch = meta_root / "runtime-scratch"
    spec_path = next(scratch.glob("*/opponent_specs.json"), None)
    if spec_path is None:
        raise ContractError("approved Team Native snapshot records are unavailable")
    agent_registry = {row["agent_id"]: row for row in json.loads((spec_path.parent / "agent_registry.json").read_text(encoding="utf-8"))}
    deck_registry = {row["deck_id"]: row for row in json.loads((spec_path.parent / "deck_registry.json").read_text(encoding="utf-8"))}
    entries: list[dict[str, Any]] = []
    for spec in json.loads(spec_path.read_text(encoding="utf-8")):
        agent, deck = agent_registry.get(spec.get("agent_id")), deck_registry.get(spec.get("deck_id"))
        cards = deck.get("normalized_card_multiset") if isinstance(deck, dict) else None
        if not isinstance(agent, dict) or not isinstance(cards, list) or len(cards) != 60 or any(type(card) is not int for card in cards):
            continue
        if spec.get("permission_status") != "VALIDATED" or spec.get("validation_status") != "PASS" or agent.get("compatibility_status") != "VALIDATED_NATIVE":
            continue
        agent_id = str(spec["agent_id"])
        entries.append({
            "opponent_id": f"team-native-{agent_id[:16]}", "opponent_type": "TEAM_NATIVE", "source_path": str(bundle_root),
            "deck_id": str(spec["deck_id"]), "deck_fingerprint": _digest(sorted(Counter(cards).items()), "deck-multiset"),
            "runtime_id": agent_id, "runtime_fingerprint": str(agent["implementation_hash"]), "agent_digest": str(agent["implementation_hash"]),
            "validation_status": "VALIDATED", "availability_status": "AVAILABLE", "evaluation_eligibility": "ALLOWED",
            "training_eligibility": "ALLOWED_FOR_VALID_FAULT_FREE_GAMES", "teacher_trust": "LIMITED", "quarantine_reason": None,
            "family_id": None, "strategy_tags": ["team-native", "subprocess"], "variant_tags": [],
            "evidence_paths": [str(spec_path), str(spec_path.parent / "agent_registry.json"), str(bundle_root / "bundle.tar.gz")],
            "loader": TEAM_NATIVE_LOADER, "deck_cards": list(cards),
            "provenance": {"approved_resolution": resolution, "agent_id": agent_id, "entrypoint": agent["entrypoint"],
                "runtime_contract": spec["runtime_contract"], "adapter_version": spec["adapter_version"]},
        })
    return sorted(entries, key=lambda entry: entry["opponent_id"])


def build_expanded_population(*, repo: Path, old_population_path: Path, output: Path, meta_root: Path,
                              family_root: Path, recovery_root: Path) -> dict[str, Any]:
    """Create a new immutable snapshot, preserving the prior snapshot byte-for-byte."""
    old_bytes = old_population_path.read_bytes()
    old = _read_json(old_population_path)
    validate_population(old)
    family_evidence = [family_root / "artifacts" / "preflight_gate_result.json", family_root / "artifacts" / "runtime_freeze_manifest.json"]
    additions = [*_team_native_entries(meta_root=meta_root),
        _family_entry(family_root=family_root, family_id="MEGA_LUCARIO_EX", deck_id="deck-0ec8de046577ad94", evidence_paths=family_evidence),
        _family_entry(family_root=family_root, family_id="MEGA_ABOMASNOW_EX", deck_id="deck-2e7428b334577cbe", evidence_paths=family_evidence),
        _family_entry(family_root=family_root, family_id="ALAKAZAM", deck_id="deck-74d86ec36fd144b9", evidence_paths=[*family_evidence, recovery_root / "artifacts" / "final_remediation_verdict.json"])]
    # Replace only the old evidence-only Alakazam record with the executable
    # adapter. Every other prior member is retained verbatim.
    retained = [entry for entry in old["entries"] if entry["opponent_id"] != "alakazam-remediation-runtime-v1"]
    entries = [*retained, *additions]
    duplicates: list[dict[str, str]] = []
    seen: dict[tuple[str, str], str] = {}
    unique: list[dict[str, Any]] = []
    for entry in sorted(entries, key=lambda item: item["opponent_id"]):
        pair = (entry["runtime_fingerprint"], entry["deck_fingerprint"])
        if pair in seen:
            duplicates.append({"opponent_id": entry["opponent_id"], "alias_of": seen[pair], "reason": "RUNTIME_DECK_DUPLICATE"})
            continue
        seen[pair] = entry["opponent_id"]; unique.append(entry); _validate_entry(entry)
    semantic = [{key: value for key, value in item.items() if key not in {"source_path", "evidence_paths"}} for item in unique]
    payload = {"schema_version": POPULATION_SCHEMA, "entries": unique, "semantic_population_digest": _digest(semantic, "population"),
        "alias_count": len(duplicates), "created_by": "offline-scaleup-population-diversity-expansion-v1",
        "parent_population_id": old["population_id"], "parent_population_digest": old["semantic_population_digest"],
        "old_snapshot_sha256": hashlib.sha256(old_bytes).hexdigest(), "duplicates": duplicates}
    payload["population_id"] = "population-" + payload["semantic_population_digest"][:16]
    if output.exists():
        raise ContractError("expanded population output already exists")
    _atomic_json(output, payload)
    if old_population_path.read_bytes() != old_bytes:
        raise ContractError("old population changed while building expanded snapshot")
    return payload


def _student_v2_entry(*, rule_entry: Mapping[str, Any], model_dir: Path, device: str, opponent_id: str) -> dict[str, Any]:
    """Bind an offline Student v2 checkpoint to the exact canonical deck.

    The candidate is bound to the same deck fingerprint as ``rule-v0-current-deck``:
    Student v2 was trained on self-play over the team's own deck, never a
    different one, so binding to any other deck would misrepresent the
    checkpoint under evaluation.
    """
    checkpoint = model_dir / "best.pt"
    digest = _sha256_file(checkpoint)
    return {
        "opponent_id": opponent_id, "opponent_type": "STUDENT_AGENT", "source_path": str(model_dir),
        "deck_id": rule_entry["deck_id"], "deck_fingerprint": rule_entry["deck_fingerprint"],
        "runtime_id": "student-v2-candidate-ranker", "runtime_fingerprint": digest, "agent_digest": digest,
        "validation_status": "VALIDATED", "availability_status": "AVAILABLE", "evaluation_eligibility": "ALLOWED",
        "training_eligibility": "ALLOWED_FOR_VALID_FAULT_FREE_GAMES", "teacher_trust": "LIMITED",
        "quarantine_reason": None, "family_id": None, "strategy_tags": ["student-v2", "gpu-candidate-ranker"],
        "variant_tags": [], "evidence_paths": [str(model_dir / "training_summary.json"), str(checkpoint)],
        "loader": STUDENT_V2_LOADER, "deck_cards": list(rule_entry["deck_cards"]),
        "provenance": {"model_dir": str(model_dir), "model_sha256": digest, "device": device},
    }


def add_student_v2_entry(*, old_population_path: Path, output: Path, model_dir: Path, device: str = "cuda",
                          opponent_id: str = "student-v2-run-a") -> dict[str, Any]:
    """Append one Student v2 candidate entry to an existing immutable population.

    The prior snapshot is preserved byte-for-byte; only a new semantic digest
    and population id are computed over the appended entry set.
    """
    old_bytes = old_population_path.read_bytes()
    old = _read_json(old_population_path)
    validate_population(old)
    rule_entry = next((entry for entry in old["entries"] if entry.get("opponent_id") == "rule-v0-current-deck"), None)
    if rule_entry is None:
        raise ContractError("population has no rule-v0-current-deck anchor to bind the Student v2 deck to")
    new_entry = _student_v2_entry(rule_entry=rule_entry, model_dir=model_dir, device=device, opponent_id=opponent_id)
    _validate_entry(new_entry)
    entries = [*old["entries"], new_entry]
    pairs: dict[tuple[str, str], str] = {}
    for item in entries:
        pair = (item["runtime_fingerprint"], item["deck_fingerprint"])
        if pair in pairs:
            raise ContractError("Student v2 entry collides with an existing runtime x deck identity")
        pairs[pair] = item["opponent_id"]
    ordered = sorted(entries, key=lambda item: item["opponent_id"])
    semantic = [{key: value for key, value in item.items() if key not in {"source_path", "evidence_paths"}} for item in ordered]
    payload = {"schema_version": POPULATION_SCHEMA, "entries": ordered, "semantic_population_digest": _digest(semantic, "population"),
               "alias_count": 0, "created_by": "offline-scaleup-student-v2-candidate-addition-v1",
               "parent_population_id": old["population_id"], "parent_population_digest": old["semantic_population_digest"],
               "old_snapshot_sha256": hashlib.sha256(old_bytes).hexdigest()}
    payload["population_id"] = "population-" + payload["semantic_population_digest"][:16]
    if output.exists():
        raise ContractError("Student v2 population output already exists")
    _atomic_json(output, payload)
    if old_population_path.read_bytes() != old_bytes:
        raise ContractError("old population changed while adding the Student v2 entry")
    return payload


def add_policy_learning_entry(*, old_population_path: Path, output: Path, model_dir: Path, device: str = "cpu",
                              opponent_id: str = "policy-learning-actor-critic-a",
                              action_mode: str = "argmax") -> dict[str, Any]:
    """Bind a trained recurrent actor-critic to the canonical candidate deck.

    This registration is candidate-only.  It does not promote a model, alter
    Rule v0, or infer compatibility with a deck absent from training evidence.
    """
    old_bytes = old_population_path.read_bytes(); old = _read_json(old_population_path); validate_population(old)
    rule_entry = next((entry for entry in old["entries"] if entry.get("opponent_id") == "rule-v0-current-deck"), None)
    if rule_entry is None:
        raise ContractError("population has no rule-v0-current-deck anchor")
    checkpoint = model_dir / "best.pt"; summary = model_dir / "training_summary.json"
    if not checkpoint.is_file() or not summary.is_file():
        raise ContractError("policy-learning model artifact is incomplete")
    try:
        document = _read_json(summary)
    except ContractError as exc:
        raise ContractError("policy-learning training summary is malformed") from exc
    if document.get("schema") not in {"policy-learning-offline-awr-v1", "policy-learning-offline-awr-v2", "policy-learning-ppo-pilot-v1"}:
        raise ContractError("policy-learning training summary schema is unsupported")
    # The action-selection rule is part of the candidate's identity, not a
    # property of the checkpoint schema.  Recording it here keeps a BC and a
    # PPO checkpoint comparable under one stated mode.
    if action_mode not in {"argmax", "sample"}:
        raise ContractError("policy-learning action mode must be 'argmax' or 'sample'")
    digest = _sha256_file(checkpoint)
    entry = {"opponent_id": opponent_id, "opponent_type": "STUDENT_AGENT", "source_path": str(model_dir),
             "deck_id": rule_entry["deck_id"], "deck_fingerprint": rule_entry["deck_fingerprint"],
             "runtime_id": "recurrent-legal-action-actor-critic", "runtime_fingerprint": digest, "agent_digest": digest,
             "validation_status": "VALIDATED", "availability_status": "AVAILABLE", "evaluation_eligibility": "ALLOWED",
             "training_eligibility": "ALLOWED_FOR_VALID_FAULT_FREE_GAMES", "teacher_trust": "LIMITED", "quarantine_reason": None,
             "family_id": None, "strategy_tags": ["policy-learning", "recurrent", "actor-critic"], "variant_tags": [],
             "evidence_paths": [str(summary), str(checkpoint)], "loader": POLICY_LEARNING_LOADER, "deck_cards": list(rule_entry["deck_cards"]),
             "provenance": {"model_dir": str(model_dir), "model_sha256": digest, "device": device, "training_schema": document["schema"],
                            "action_mode": action_mode}}
    _validate_entry(entry)
    if any(item["opponent_id"] == opponent_id or (item["runtime_fingerprint"], item["deck_fingerprint"]) == (digest, entry["deck_fingerprint"]) for item in old["entries"]):
        raise ContractError("policy-learning entry collides with existing population identity")
    ordered = sorted([*old["entries"], entry], key=lambda item: item["opponent_id"])
    semantic = [{key: value for key, value in item.items() if key not in {"source_path", "evidence_paths"}} for item in ordered]
    payload = {"schema_version": POPULATION_SCHEMA, "entries": ordered, "semantic_population_digest": _digest(semantic, "population"),
               "alias_count": 0, "created_by": "offline-scaleup-policy-learning-candidate-addition-v1",
               "parent_population_id": old["population_id"], "parent_population_digest": old["semantic_population_digest"],
               "old_snapshot_sha256": hashlib.sha256(old_bytes).hexdigest()}
    payload["population_id"] = "population-" + payload["semantic_population_digest"][:16]
    if output.exists():
        raise ContractError("policy-learning population output already exists")
    _atomic_json(output, payload)
    if old_population_path.read_bytes() != old_bytes:
        raise ContractError("old population changed while adding policy-learning entry")
    return payload


def _taxonomy_rule_entries(repo: Path, taxonomy_root: Path) -> list[dict[str, Any]]:
    """Import only exact-60, prior-CABT-evidenced decks; no repair or guessing."""
    source = taxonomy_root / "artifacts" / "deck_instance_registry.json"
    validity_source = taxonomy_root / "artifacts" / "deck_validity_registry.json"
    rows = json.loads(source.read_text(encoding="utf-8")); validity = {item["deck_id"]: item for item in json.loads(validity_source.read_text(encoding="utf-8"))}
    runtime = repo / "agents" / "rule_agent.py"; agent = _sha_file(runtime); output = []
    for row in rows:
        deck_id, cards = row.get("deck_id"), row.get("cards")
        evidence = validity.get(deck_id, {})
        if not isinstance(deck_id, str) or evidence.get("legality_status") != "PRIOR_CABT_VALID_EVIDENCE_MATCHED" or not isinstance(cards, list):
            continue
        expanded = [item["card_id"] for item in cards for _ in range(item["count"])]
        if len(expanded) != 60 or any(type(card) is not int for card in expanded):
            continue
        output.append({"opponent_id": f"rule-v0-{deck_id}", "opponent_type": "RULE_V0_DECK", "source_path": str(source), "deck_id": deck_id,
                       "deck_fingerprint": _digest(sorted(Counter(expanded).items()), "deck-multiset"), "runtime_id": "rule-agent-v0", "runtime_fingerprint": agent, "agent_digest": agent,
                       "validation_status": "VALIDATED", "availability_status": "AVAILABLE", "evaluation_eligibility": "ALLOWED", "training_eligibility": "ALLOWED_FOR_VALID_FAULT_FREE_GAMES",
                       "teacher_trust": "TRUSTED", "quarantine_reason": None, "family_id": None, "strategy_tags": ["rule", "taxonomy-v2"], "variant_tags": [],
                       "evidence_paths": [str(source), str(validity_source)], "loader": "rule_v0", "deck_cards": expanded})
    return output


def _validate_entry(entry: Mapping[str, Any]) -> None:
    required = {"opponent_id", "opponent_type", "source_path", "deck_id", "deck_fingerprint", "runtime_id", "runtime_fingerprint", "agent_digest", "validation_status", "availability_status", "evaluation_eligibility", "training_eligibility", "teacher_trust", "quarantine_reason", "family_id", "strategy_tags", "variant_tags", "evidence_paths"}
    if set(entry) - (required | {"loader", "provenance", "deck_cards"}) or not required.issubset(entry):
        raise ContractError("registry entry schema mismatch")
    if entry["opponent_type"] not in OPPONENT_TYPES or not isinstance(entry["opponent_id"], str):
        raise ContractError("invalid opponent type or id")
    for field in ("deck_fingerprint", "runtime_fingerprint", "agent_digest"):
        if not isinstance(entry[field], str) or len(entry[field]) != 64:
            raise ContractError(f"invalid {field}")
    if entry["validation_status"] == "VALIDATED" and entry["availability_status"] != "AVAILABLE":
        raise ContractError("validated entries must explicitly state availability")
    if entry.get("loader") == "rule_v0" and (not isinstance(entry.get("deck_cards"), list) or len(entry["deck_cards"]) != 60 or any(type(card) is not int for card in entry["deck_cards"])):
        raise ContractError("Rule v0 registry entry requires an exact 60-card deck")


def build_population(*, repo: Path, output: Path, recovery_root: Path, taxonomy_root: Path = Path("/home/bfe-lab-ono/kaggle/handoff-artifacts/deck-agent-asset-consolidation-taxonomy-v2")) -> dict[str, Any]:
    current = _rule_entry(repo)
    # The local deck is the executable canonical representative when its
    # taxonomy copy has the same runtime×deck identity.
    taxonomy = [item for item in _taxonomy_rule_entries(repo, taxonomy_root) if item["deck_fingerprint"] != current["deck_fingerprint"]]
    entries = [current, *taxonomy, _alakazam_entry(recovery_root)]
    for entry in entries:
        _validate_entry(entry)
    ids = [item["opponent_id"] for item in entries]
    if len(ids) != len(set(ids)):
        raise ContractError("duplicate opponent id")
    pairs: dict[tuple[str, str], str] = {}
    aliases: list[dict[str, str]] = []
    for item in entries:
        pair = (item["runtime_fingerprint"], item["deck_fingerprint"])
        if pair in pairs:
            aliases.append({"opponent_id": item["opponent_id"], "alias_of": pairs[pair]})
        pairs[pair] = item["opponent_id"]
    if aliases:
        raise ContractError("runtime × deck duplicate/alias entries are rejected")
    ordered = sorted(entries, key=lambda item: item["opponent_id"])
    semantic = [{key: value for key, value in item.items() if key not in {"source_path", "evidence_paths"}} for item in ordered]
    payload = {"schema_version": POPULATION_SCHEMA, "entries": ordered, "semantic_population_digest": _digest(semantic, "population"),
               "alias_count": 0, "created_by": "offline-scaleup-v2"}
    payload["population_id"] = "population-" + payload["semantic_population_digest"][:16]
    _atomic_json(output, payload)
    return payload


def validate_population(population: Mapping[str, Any]) -> dict[str, Any]:
    if population.get("schema_version") != POPULATION_SCHEMA or not isinstance(population.get("entries"), list):
        raise ContractError("unsupported population")
    entries = population["entries"]
    for item in entries:
        if not isinstance(item, dict):
            raise ContractError("population entry is not object")
        _validate_entry(item)
    ids = [item["opponent_id"] for item in entries]
    if len(ids) != len(set(ids)):
        raise ContractError("duplicate opponent ID")
    return {"schema_version": "offline-scaleup-population-validation-v1", "valid": True, "entries": len(entries),
            "by_type": dict(sorted(Counter(item["opponent_type"] for item in entries).items())),
            "semantic_population_digest": population["semantic_population_digest"]}


def build_schedule(population: Mapping[str, Any], *, candidate: str, opponents: list[str], games: int, base_seed: int, allow_unbalanced: bool = False) -> dict[str, Any]:
    if games <= 0 or (games % 2 and not allow_unbalanced):
        raise ContractError("games must be positive and even to balance sides unless diagnostic mode is explicit")
    entries = {item["opponent_id"]: item for item in population["entries"]}
    if candidate not in entries or not opponents or any(item not in entries for item in opponents):
        raise ContractError("candidate/opponent is absent from immutable population")
    jobs: list[dict[str, Any]] = []
    for opponent in sorted(opponents):
        for repetition in range((games + 1) // 2):
            for candidate_side in (0, 1):
                core = {"population": population["semantic_population_digest"], "candidate": candidate, "opponent": opponent,
                        "candidate_side": candidate_side, "repetition": repetition, "seed": base_seed + len(jobs)}
                if len(jobs) < games * len(opponents): jobs.append({**core, "game_id": "game-" + _digest(core, "game")[:24]})
    digest = _digest(jobs, "schedule")
    return {"schema_version": SCHEDULE_SCHEMA, "schedule_digest": digest, "population_digest": population["semantic_population_digest"],
            "candidate": candidate, "opponents": sorted(opponents), "planned_games": len(jobs), "engine_seed_supported": "UNKNOWN_UNTIL_RUNTIME", "games": jobs}


def _fixture_result(job: Mapping[str, Any]) -> dict[str, Any]:
    return {"status": "DONE", "winner": int(job["candidate_side"]), "elapsed_seconds": 0.0, "steps": 1,
            "candidate_fault": False, "mapping_valid": True, "score_identity_valid": True, "legal": True, "teacher_samples": []}


def _engine_failure_scope(raw: Mapping[str, Any], candidate_side: int) -> dict[str, Any]:
    """Classify CABT's per-seat terminal state without assigning blame by guesswork."""
    statuses = raw.get("agent_status")
    if not isinstance(statuses, list) or len(statuses) != 2:
        return {"agent_status": None, "candidate_agent_status": None,
                "opponent_agent_status": None, "engine_failure_scope": "UNAVAILABLE"}
    normalized = [str(status) for status in statuses]
    candidate_status, opponent_status = normalized[candidate_side], normalized[1 - candidate_side]
    failed = {"ERROR", "INVALID", "TIMEOUT"}
    if candidate_status in failed and opponent_status in failed:
        scope = "BOTH_SEATS"
    elif candidate_status in failed:
        scope = "CANDIDATE_SEAT"
    elif opponent_status in failed:
        scope = "OPPONENT_SEAT"
    elif str(raw.get("status")) != "DONE":
        scope = "ENGINE_OR_UNKNOWN"
    else:
        scope = "NONE"
    return {"agent_status": normalized, "candidate_agent_status": candidate_status,
            "opponent_agent_status": opponent_status, "engine_failure_scope": scope}


def _cabt_result(job: Mapping[str, Any], population: Mapping[str, Any], repo: Path,
                 trajectory_root: Path | None = None, diagnostic_root: Path | None = None) -> dict[str, Any]:
    """Run one bound candidate adapter against a population opponent.

    Candidate choices are executed by the registered adapter and captured in
    the same callback.  A capture failure remains a candidate-side fault; it
    is never replaced by a Rule-v0 label.
    """
    entries = {item["opponent_id"]: item for item in population["entries"]}
    candidate, opponent = entries[str(job["candidate"])], entries[str(job["opponent"])]
    from main import make_rule_agent
    from scripts.test_sim import run_match
    candidate_deck = list(candidate["deck_cards"]); opponent_deck = list(opponent["deck_cards"])
    samples: list[dict[str, Any]] = []
    candidate_side = int(job["candidate_side"])
    decisions: list[dict[str, Any]] = []
    adapter = adapter_for(candidate); adapter.prepare(candidate_deck)
    expected = {"candidate_runtime_id": adapter.teacher_identity, "candidate_adapter_type": adapter.adapter_type,
                "teacher_id": adapter.teacher_identity, "teacher_type": adapter.teacher_type,
                "teacher_trust": adapter.teacher_trust, "teacher_runtime_fingerprint": adapter.runtime_fingerprint,
                "candidate_deck_fingerprint": adapter.deck_fingerprint,
                "telemetry_capability_digest": _digest(adapter.telemetry_capabilities, "telemetry-capabilities")}
    for field, value in expected.items():
        if field in job and job[field] != value:
            adapter.close()
            raise CandidateRuntimeError("TEACHER_DECK_BINDING_FAILURE" if field == "candidate_deck_fingerprint" else "TEACHER_RUNTIME_LOAD_FAILURE", f"job {field} does not match bound candidate runtime")
    candidate_error: CandidateRuntimeError | None = None

    cleanups: list[Any] = []

    def plain(deck: list[int], _seed: int):
        return make_rule_agent(deck=deck)

    def external(entry: Mapping[str, Any]):
        loader = entry.get("loader")
        if loader == "rule_v0":
            return plain
        if loader == TEAM_NATIVE_LOADER:
            def native(_deck: list[int], _seed: int):
                from mage_ptcg.opponents.league_runtime import NativeAgentWorker, cleanup_native_participant, prepare_native_participant
                prepared = prepare_native_participant(entry["source_path"], str(entry["runtime_id"]), scratch_root=Path(tempfile.gettempdir()) / "offline-scaleup-native")
                worker = NativeAgentWorker(prepared["source_root"], prepared["entrypoint"], decision_timeout_seconds=8.0)
                cleanups.append(lambda: (worker.close(), cleanup_native_participant(prepared)))
                return worker
            return native
        if loader == INTERNAL_FAMILY_LOADER:
            def internal_family(deck: list[int], _seed: int):
                config = entry.get("provenance", {}).get("family_config")
                if not isinstance(config, Mapping):
                    raise ContractError("internal Family entry has no config")
                from mage_ptcg.family_agents import ConfigDrivenFamilyAgent
                return ConfigDrivenFamilyAgent(deck=deck, config=config).as_agent()
            return internal_family
        if loader == FAMILY_LOADER:
            def family(deck: list[int], _seed: int):
                root = str(entry["source_path"])
                if root not in sys.path:
                    sys.path.insert(0, root)
                from family_agent.agent import FamilySpecificAgent
                from family_agent.strategy import load_intended_strategy_registry
                deck_id = str(entry["deck_id"])
                weights = load_intended_strategy_registry([deck_id])[deck_id]["intended_strategy_weights"]
                fallback = make_rule_agent(deck=deck)
                primary_ids = entry.get("provenance", {}).get("primary_ids")
                if not isinstance(primary_ids, list):
                    raise ContractError("Family entry has no exact primary_ids")
                return FamilySpecificAgent(str(entry["family_id"]), deck_id, deck, primary_ids, fallback, weights).as_agent()
            return family
        raise ContractError(f"selected population member has no approved executable loader: {loader}")

    def captured(deck: list[int], _seed: int):
        if deck != candidate_deck:
            raise CandidateRuntimeError("TEACHER_DECK_BINDING_FAILURE", "candidate deck differs from job manifest")
        def capture(observation: object, configuration: object = None) -> object:
            nonlocal candidate_error
            del configuration
            try:
                started = time.perf_counter_ns()
                with _candidate_callback_watchdog(_candidate_callback_timeout_seconds()):
                    choice = adapter.decide(observation)
                    captured = adapter.capture(observation, choice, game_id=str(job["game_id"]), candidate_side=candidate_side, deck=candidate_deck)
                if captured is not None:
                    sample, decision = captured; decision["decision_latency_us"] = (time.perf_counter_ns() - started) / 1_000
                    samples.append(sample); decisions.append(decision)
                return choice
            except TimeoutError as exc:
                candidate_error = CandidateRuntimeError("TEACHER_CALLBACK_TIMEOUT", str(exc))
                raise candidate_error from exc
            except CandidateRuntimeError as exc:
                candidate_error = exc
                raise
            except Exception as exc:
                # CABT records callback exceptions only as a seat-level ERROR.
                # Preserve the concrete wrapper failure and re-raise so the
                # engine still owns terminal classification; no fallback is
                # permitted after a candidate runtime exception.
                candidate_error = CandidateRuntimeError(
                    "TEACHER_RUNTIME_UNEXPECTED_EXCEPTION",
                    f"{type(exc).__name__}: {str(exc)[:240]}",
                )
                raise
        return capture

    decks = (candidate_deck, opponent_deck) if candidate_side == 0 else (opponent_deck, candidate_deck)
    opponent_factory = external(opponent)
    factories = (captured, opponent_factory) if candidate_side == 0 else (opponent_factory, captured)
    # test_sim owns the project-standard terminal classification and does not
    # require O6's public-trajectory projection, which is not an execution
    # prerequisite for this private offline Rule-v0 league.
    diagnostic_directory = diagnostic_root / str(job["game_id"]) if diagnostic_root is not None else None
    with tempfile.TemporaryDirectory(prefix="offline-scaleup-cabt-") as temporary:
        directory = Path(temporary)
        paths = []
        for index, deck in enumerate(decks):
            path = directory / f"deck-{index}.csv"
            path.write_text("\n".join(str(card) for card in deck) + "\n", encoding="utf-8")
            paths.append(path)
        try:
            raw = run_match(deck_a_path=paths[0], deck_b_path=paths[1], agent_a_name="rule", agent_b_name="rule", seed=int(job["seed"]), output_dir=directory / "result", save_html=False, save_result=False, agent_a_factory=factories[0], agent_b_factory=factories[1])
        finally:
            for cleanup in reversed(cleanups):
                cleanup()
            adapter.close()
    status = str(raw.get("status", "ERROR"))
    engine = _engine_failure_scope(raw, candidate_side)
    engine_result_path = None
    if status != "DONE" and diagnostic_directory is not None:
        diagnostic_directory.mkdir(parents=True, exist_ok=True)
        path = diagnostic_directory / "engine_result.json"
        # run_match returns terminal metadata only here; observations and raw
        # engine steps are deliberately not persisted by this diagnostic path.
        _atomic_json(path, {"schema_version": "offline-scaleup-engine-diagnostic-v1", "game_id": job["game_id"],
                            "candidate_side": candidate_side, "raw_result": raw, "engine": engine})
        engine_result_path = str(path)
    candidate_fault = candidate_error is not None
    attribution = "CANDIDATE" if candidate_fault else "NONE" if status == "DONE" else "UNRESOLVED_TIMEOUT" if status in {"AGENT_TIMEOUT", "STEP_LIMIT"} else "UNRESOLVED_AGENT_FAILURE"
    trajectory_path = None; trajectory_digest = None
    if trajectory_root is not None and status == "DONE":
        path = trajectory_root / f"{job['game_id']}.jsonl"
        for decision in decisions:
            decision["source_game_result"] = status
        try:
            trajectory_digest = write_trajectory(path, decisions, {"game_id": job["game_id"], "teacher_identity": adapter.teacher_identity,
                "teacher_type": adapter.teacher_type, "teacher_trust": adapter.teacher_trust, "runtime_fingerprint": adapter.runtime_fingerprint,
                "candidate_deck_fingerprint": adapter.deck_fingerprint, "source_game_result": status,
                "decision_count": len(decisions)})
            trajectory_path = str(path)
        except OSError as exc:
            raise CandidateRuntimeError("TEACHER_TRAJECTORY_WRITE_FAILURE", str(exc)[:300]) from exc
    callback_latencies = [float(item["decision_latency_us"]) for item in decisions
                          if isinstance(item.get("decision_latency_us"), (int, float))]
    callback_timing = {"count": len(callback_latencies), "p50_us": statistics.median(callback_latencies) if callback_latencies else None,
                       "p95_us": sorted(callback_latencies)[max(0, int(.95 * len(callback_latencies)) - 1)] if callback_latencies else None,
                       "max_us": max(callback_latencies, default=None)}
    return {"status": status, "winner": raw.get("winner"), "elapsed_seconds": raw.get("elapsed_seconds"), "steps": raw.get("steps"),
            # play_game exposes no seat-specific fault proof for these paths;
            # preserve the fault without turning uncertainty into candidate blame.
            "candidate_fault": candidate_fault, "fault_attribution": attribution, "mapping_valid": candidate_error is None or candidate_error.code != "TEACHER_ACTION_MAPPING_FAILURE",
            "score_identity_valid": True, "legal": status == "DONE", "teacher_samples": samples,
            "engine_seed_supported": raw.get("engine_seed_support", "UNKNOWN"), "trajectory_path": trajectory_path,
            "trajectory_digest": trajectory_digest, "decision_count": len(decisions), "captured_decision_count": len(decisions),
            # This records the candidate callback envelope (inference plus
            # public-only capture) for every completed game.  CABT does not
            # expose opponent/engine per-step timings; those remain explicitly
            # unavailable rather than being guessed from total elapsed time.
            "candidate_callback_timing_us": callback_timing,
            # The signal watchdog is diagnostic and main-thread only; the
            # production stop boundary is the outer per-game process timeout.
            # Record which layer was actually armed so an inactive watchdog is
            # never documented as an enforced one.
            "candidate_callback_watchdog_mode": candidate_callback_watchdog_mode(_candidate_callback_timeout_seconds()),
            "skipped_decision_count": 0, "mapping_failure_count": int(candidate_error is not None and candidate_error.code == "TEACHER_ACTION_MAPPING_FAILURE"), "illegal_selection_count": int(candidate_error is not None and candidate_error.code == "TEACHER_ILLEGAL_ACTION"), "fallback_count": sum(bool(item.get("fallback_used")) for item in decisions),
            # ``fallback_count`` above only sees captured decision rows.  A
            # legal empty answer to an optional prompt persists no row, so a
            # Rule-v0 delegation on that path would otherwise be invisible.
            # These counters keep the two cases distinguishable.
            "decision_counters": {key: int(value) for key, value in sorted(adapter.decision_counters.items())},
            "candidate_error_code": candidate_error.code if candidate_error else None,
            "candidate_error": str(candidate_error)[:300] if candidate_error else None,
            **engine, "engine_result_path": engine_result_path,
            "teacher_metadata": {"teacher_identity": adapter.teacher_identity, "teacher_type": adapter.teacher_type,
            "teacher_trust": adapter.teacher_trust, "runtime_fingerprint": adapter.runtime_fingerprint, "adapter_type": adapter.adapter_type}}


WORKER_RESULT_SENTINEL = "@@OFFLINE_SCALEUP_WORKER_RESULT@@"


def _serve_main(args: argparse.Namespace) -> int:
    """Play games for a parent process over stdin/stdout until told to stop.

    One request per line names an already-written job file and the result path
    to fill.  The reply is a single sentinel-prefixed line so that anything a
    game prints on stdout stays ordinary log output instead of corrupting the
    framing.  Every game still writes the same result contract a one-shot
    worker writes, and the parent still verifies it.
    """
    population = _read_json(Path(args.population))
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        request = json.loads(line)
        job = _read_json(Path(request["job"])); result_path = Path(request["result_path"])
        trajectory_root = Path(request["trajectory_root"]) if request.get("trajectory_root") else None
        diagnostic_root = Path(request["diagnostic_root"]) if request.get("diagnostic_root") else None
        try:
            outcome = _fixture_result(job) if args.executor == "fixture" else _cabt_result(
                job, population, Path(args.repo), trajectory_root, diagnostic_root)
            _atomic_json(result_path, {"schema_version": "offline-scaleup-worker-result-v1", "ok": True, "game_id": job["game_id"], "outcome": outcome})
            reply = {"game_id": job["game_id"], "returncode": 0}
        except Exception as exc:
            _atomic_json(result_path, {"schema_version": "offline-scaleup-worker-result-v1", "ok": False, "game_id": job.get("game_id"), "error_type": type(exc).__name__, "error_code": exc.code if isinstance(exc, CandidateRuntimeError) else None, "error": str(exc)[:500]})
            reply = {"game_id": job.get("game_id"), "returncode": 2}
        sys.stdout.write(WORKER_RESULT_SENTINEL + _canonical(reply) + "\n"); sys.stdout.flush()
    return 0


class _PersistentWorker:
    """A reusable game-playing child process with a bounded lifetime.

    Interpreter start plus importing torch and the CABT stack costs about a
    second, and the one-process-per-game design paid it for every game.  The
    child is discarded after ``reuse_games`` games and immediately after any
    game that did not finish cleanly, so a damaged interpreter can never be
    carried into the next game.
    """

    def __init__(self, *, population_path: Path, repo: Path, executor: str, reuse_games: int) -> None:
        self.population_path, self.repo, self.executor, self.reuse_games = population_path, repo, executor, reuse_games
        self.process: Any = None; self.games = 0; self.log: list[str] = []

    def key(self) -> tuple[str, str, str, int]:
        return (str(self.population_path), str(self.repo), self.executor, self.reuse_games)

    def _start(self) -> None:
        import subprocess
        command = [sys.executable, "-m", "mage_ptcg.offline_scaleup", "serve", "--population", str(self.population_path),
                   "--repo", str(self.repo), "--executor", self.executor]
        # stderr is merged into the framed stdout stream: the sentinel keeps
        # the reply identifiable, and a separate undrained stderr pipe would
        # deadlock a chatty game once its buffer filled.
        self.process = subprocess.Popen(command, cwd=self.repo, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, text=True, bufsize=1,
                                        env={**os.environ, "PYTHONPATH": os.pathsep.join((str(self.repo), str(self.repo / "src"))),
                                             "PYTHONDONTWRITEBYTECODE": "1", "LITELLM_LOCAL_MODEL_COST_MAP": "True"})
        self.games = 0; self.log = []

    def close(self) -> None:
        process, self.process = self.process, None
        if process is None:
            return
        try:
            if process.stdin and not process.stdin.closed:
                process.stdin.close()
            process.wait(timeout=10)
        except Exception:
            process.kill()
            try: process.wait(timeout=10)
            except Exception: pass

    def play(self, request: dict[str, Any], *, timeout: float) -> tuple[dict[str, Any] | None, str]:
        """Run one game; return (reply, stdout tail). ``None`` means timeout."""
        import selectors
        if self.process is None or self.process.poll() is not None:
            self.close(); self._start()
        assert self.process is not None and self.process.stdin is not None and self.process.stdout is not None
        try:
            self.process.stdin.write(_canonical(request) + "\n"); self.process.stdin.flush()
        except (BrokenPipeError, ValueError):
            self.close(); return None, "".join(self.log[-20:])
        selector = selectors.DefaultSelector(); selector.register(self.process.stdout, selectors.EVENT_READ)
        deadline = time.monotonic() + timeout
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not selector.select(remaining):
                    self.close(); return None, "".join(self.log[-20:])
                line = self.process.stdout.readline()
                if not line:
                    self.close(); return None, "".join(self.log[-20:])
                if line.startswith(WORKER_RESULT_SENTINEL):
                    self.games += 1
                    reply = json.loads(line[len(WORKER_RESULT_SENTINEL):])
                    tail = "".join(self.log[-20:]); self.log = []
                    if self.games >= self.reuse_games or reply.get("returncode") != 0:
                        # A failed game may have left this interpreter in an
                        # unknown state; never reuse it for the next game.
                        self.close()
                    return reply, tail
                self.log.append(line)
        finally:
            selector.close()


# One child per caller thread.  The CABT league gives every pool worker its
# own process, but the fixture executor drives jobs from a thread pool in a
# single process; a shared child would interleave two games on one pipe.
_LEAGUE_WORKER = threading.local()


def _persistent_worker(*, population_path: Path, repo: Path, executor: str, reuse_games: int) -> _PersistentWorker:
    worker = getattr(_LEAGUE_WORKER, "worker", None)
    key = (str(population_path), str(repo), executor, reuse_games)
    if worker is None or worker.key() != key:
        if worker is not None: worker.close()
        worker = _PersistentWorker(population_path=population_path, repo=repo, executor=executor, reuse_games=reuse_games)
        _LEAGUE_WORKER.worker = worker
    return worker


def _worker_main(args: argparse.Namespace) -> int:
    job = _read_json(Path(args.job)); population = _read_json(Path(args.population))
    try:
        outcome = _fixture_result(job) if args.executor == "fixture" else _cabt_result(
            job, population, Path(args.repo), Path(args.trajectory_root) if args.trajectory_root else None,
            Path(args.diagnostic_root) if args.diagnostic_root else None,
        )
        _atomic_json(Path(args.result_path), {"schema_version": "offline-scaleup-worker-result-v1", "ok": True, "game_id": job["game_id"], "outcome": outcome})
        return 0
    except Exception as exc:
        _atomic_json(Path(args.result_path), {"schema_version": "offline-scaleup-worker-result-v1", "ok": False, "game_id": job.get("game_id"), "error_type": type(exc).__name__, "error_code": exc.code if isinstance(exc, CandidateRuntimeError) else None, "error": str(exc)[:500]})
        return 2


def _write_failure_diagnostic(*, root: Path | None, game_id: str, outcome: Mapping[str, Any],
                              fault: Mapping[str, Any]) -> None:
    if root is None or outcome.get("status") == "DONE":
        return
    directory = root / game_id
    directory.mkdir(parents=True, exist_ok=True)
    _atomic_json(directory / "worker_transport.json", {
        "schema_version": "offline-scaleup-worker-diagnostic-v1", "game_id": game_id,
        "outcome": dict(outcome), "fault": dict(fault),
    })
    for key in ("stdout_tail", "stderr_tail"):
        value = fault.get(key)
        if isinstance(value, str) and value:
            (directory / f"worker_{key.removesuffix('_tail')}.log").write_text(value, encoding="utf-8")


def _run_worker(job: Mapping[str, Any], *, population_path: Path, repo: Path, executor: str, timeout: float,
                scratch: Path, trajectory_root: Path | None = None,
                diagnostic_root: Path | None = None, reuse_games: int = 1) -> tuple[dict[str, Any], dict[str, Any]]:
    import subprocess
    job_directory = scratch / str(job["game_id"])
    job_directory.mkdir(parents=True, exist_ok=True)
    job_path = job_directory / "job.json"
    result_path = job_directory / "worker_result.json"
    result_path.unlink(missing_ok=True)
    _atomic_json(job_path, job)
    command = [sys.executable, "-m", "mage_ptcg.offline_scaleup", "worker", "--job", str(job_path), "--population", str(population_path), "--repo", str(repo), "--executor", executor, "--result-path", str(result_path)]
    if trajectory_root is not None: command.extend(("--trajectory-root", str(trajectory_root)))
    if diagnostic_root is not None: command.extend(("--diagnostic-root", str(diagnostic_root)))
    started = time.monotonic()
    try:
        if reuse_games > 1:
            # The same child plays several games, so interpreter start and the
            # torch/CABT import cost are paid once per generation rather than
            # once per game.  Everything below this branch is unchanged: the
            # per-game result contract is still verified from the file the
            # child wrote.
            request: dict[str, Any] = {"job": str(job_path), "result_path": str(result_path)}
            if trajectory_root is not None: request["trajectory_root"] = str(trajectory_root)
            if diagnostic_root is not None: request["diagnostic_root"] = str(diagnostic_root)
            worker = _persistent_worker(population_path=population_path, repo=repo, executor=executor, reuse_games=reuse_games)
            reply, tail = worker.play(request, timeout=timeout)
            if reply is None:
                raise subprocess.TimeoutExpired(cmd="mage_ptcg.offline_scaleup serve", timeout=timeout)
            details = {"returncode": int(reply.get("returncode", 2)), "stdout_bytes": len(tail.encode()), "stderr_bytes": 0,
                       "stdout_tail": tail[-2000:], "stderr_tail": "", "result_path": str(result_path), "worker_mode": "persistent"}
        else:
            completed = subprocess.run(command, cwd=repo, capture_output=True, text=True, timeout=timeout,
                                       env={**os.environ, "PYTHONPATH": os.pathsep.join((str(repo), str(repo / "src"))),
                                            "PYTHONDONTWRITEBYTECODE": "1",
                                            # CABT never calls an LLM.  Force the
                                            # bundled local LiteLLM metadata so a
                                            # fresh isolated game cannot block on
                                            # an unrelated network price lookup.
                                            "LITELLM_LOCAL_MODEL_COST_MAP": "True"})
            details = {"returncode": completed.returncode, "stdout_bytes": len(completed.stdout.encode()), "stderr_bytes": len(completed.stderr.encode()), "stdout_tail": completed.stdout[-2000:], "stderr_tail": completed.stderr[-2000:], "result_path": str(result_path), "worker_mode": "isolated"}
        if not result_path.exists() or result_path.stat().st_size == 0:
            outcome = {"status": "ERROR", "legal": False, "candidate_fault": False, "fault_attribution": "UNRESOLVED_WORKER_FAILURE", "mapping_valid": False, "score_identity_valid": False, "teacher_samples": []}; fault = {"kind": "NO_RESULT_FILE", "attribution": "UNRESOLVED_WORKER_FAILURE", **details}; _write_failure_diagnostic(root=diagnostic_root, game_id=str(job["game_id"]), outcome=outcome, fault=fault); return outcome, fault
        try:
            response = _read_json(result_path)
        except ContractError as exc:
            outcome = {"status": "ERROR", "legal": False, "candidate_fault": False, "fault_attribution": "UNRESOLVED_WORKER_FAILURE", "mapping_valid": False, "score_identity_valid": False, "teacher_samples": []}; fault = {"kind": "RESULT_FILE_DECODE_ERROR", "message": str(exc), "attribution": "UNRESOLVED_WORKER_FAILURE", **details}; _write_failure_diagnostic(root=diagnostic_root, game_id=str(job["game_id"]), outcome=outcome, fault=fault); return outcome, fault
        if response.get("schema_version") != "offline-scaleup-worker-result-v1" or response.get("game_id") != job["game_id"]:
            outcome = {"status": "ERROR", "legal": False, "candidate_fault": False, "fault_attribution": "UNRESOLVED_WORKER_FAILURE", "mapping_valid": False, "score_identity_valid": False, "teacher_samples": []}; fault = {"kind": "RESULT_CONTRACT_ERROR", "attribution": "UNRESOLVED_WORKER_FAILURE", **details}; _write_failure_diagnostic(root=diagnostic_root, game_id=str(job["game_id"]), outcome=outcome, fault=fault); return outcome, fault
        if details["returncode"] != 0 or response.get("ok") is not True or not isinstance(response.get("outcome"), dict):
            code = response.get("error_code")
            candidate_fault = isinstance(code, str) and code.startswith("TEACHER_")
            outcome = {"status": "ERROR", "legal": False, "candidate_fault": candidate_fault, "fault_attribution": "CANDIDATE" if candidate_fault else "UNRESOLVED_WORKER_FAILURE", "mapping_valid": code != "TEACHER_ACTION_MAPPING_FAILURE", "score_identity_valid": False, "teacher_samples": [], "candidate_error_code": code, "candidate_error": response.get("error")}; fault = {"kind": code or "WORKER_ERROR", "error_type": response.get("error_type"), "message": response.get("error"), "attribution": "CANDIDATE" if candidate_fault else "UNRESOLVED_WORKER_FAILURE", **details}; _write_failure_diagnostic(root=diagnostic_root, game_id=str(job["game_id"]), outcome=outcome, fault=fault); return outcome, fault
        outcome = dict(response["outcome"])
        if outcome.get("status") == "DONE" and trajectory_root is not None:
            trajectory = outcome.get("trajectory_path"); expected = outcome.get("trajectory_digest")
            if not isinstance(trajectory, str) or not isinstance(expected, str):
                return {"status": "ERROR", "legal": False, "candidate_fault": True, "fault_attribution": "CANDIDATE", "mapping_valid": False, "score_identity_valid": False, "teacher_samples": [], "candidate_error_code": "TRAJECTORY_MISSING"}, {"kind": "TRAJECTORY_MISSING", "attribution": "CANDIDATE", **details}
            actual_path = Path(trajectory)
            actual = hashlib.sha256(actual_path.read_bytes()).hexdigest() if actual_path.is_file() else None
            if actual != expected:
                return {"status": "ERROR", "legal": False, "candidate_fault": True, "fault_attribution": "CANDIDATE", "mapping_valid": False, "score_identity_valid": False, "teacher_samples": [], "candidate_error_code": "TRAJECTORY_DIGEST_MISMATCH"}, {"kind": "TRAJECTORY_DIGEST_MISMATCH", "attribution": "CANDIDATE", **details}
            try:
                trajectory_rows = [json.loads(value) for value in actual_path.read_text(encoding="utf-8").splitlines() if value.strip()]
                header = trajectory_rows[0]
                metadata = header.get("metadata") if isinstance(header, dict) else None
                if not isinstance(metadata, dict) or metadata.get("game_id") != job["game_id"] or metadata.get("runtime_fingerprint") != job.get("teacher_runtime_fingerprint", outcome.get("teacher_metadata", {}).get("runtime_fingerprint")) or metadata.get("decision_count") != outcome.get("decision_count") or len(trajectory_rows) - 1 != outcome.get("decision_count"):
                    raise ValueError("trajectory header or count mismatch")
            except (OSError, ValueError, json.JSONDecodeError, IndexError):
                return {"status": "ERROR", "legal": False, "candidate_fault": True, "fault_attribution": "CANDIDATE", "mapping_valid": False, "score_identity_valid": False, "teacher_samples": [], "candidate_error_code": "TRAJECTORY_CONTRACT_MISMATCH"}, {"kind": "TRAJECTORY_CONTRACT_MISMATCH", "attribution": "CANDIDATE", **details}
        fault = {"kind": "COMPLETED", **details}
        _write_failure_diagnostic(root=diagnostic_root, game_id=str(job["game_id"]), outcome=outcome, fault=fault)
        return outcome, fault
    except subprocess.TimeoutExpired:
        outcome = {"status": "AGENT_TIMEOUT", "legal": False, "candidate_fault": False, "fault_attribution": "UNRESOLVED_TIMEOUT", "mapping_valid": False, "score_identity_valid": False, "teacher_samples": []}; fault = {"kind": "HARD_TIMEOUT", "attribution": "UNRESOLVED_TIMEOUT", "timeout_seconds": timeout}; _write_failure_diagnostic(root=diagnostic_root, game_id=str(job["game_id"]), outcome=outcome, fault=fault); return outcome, fault
    finally:
        job_path.unlink(missing_ok=True)


def summarize_run(run_dir: Path, *, workers: int | None = None) -> dict[str, Any]:
    schedule = _read_json(run_dir / "schedule.json")
    rows = list(_jsonl(run_dir / "game_results.jsonl"))
    ids = [str(row.get("game_id")) for row in rows]
    duplicate = len(ids) - len(set(ids))
    terminal = [row for row in rows if row.get("status") in TERMINAL]
    faults = Counter(str(row.get("fault", {}).get("kind", "NONE")) for row in rows)
    valid = [row for row in terminal if row.get("status") == "DONE" and row.get("legal") is True and not row.get("candidate_fault")]
    latencies = [float(row["elapsed_seconds"]) for row in rows if isinstance(row.get("elapsed_seconds"), (int, float))]
    timing_path = run_dir / "wall_clock_timing.json"
    timing = _read_json(timing_path) if timing_path.is_file() else {}
    segments = timing.get("segments", []) if isinstance(timing, Mapping) else []
    wall_clock_seconds = sum(float(segment["wall_clock_seconds"]) for segment in segments
                             if isinstance(segment, Mapping) and isinstance(segment.get("wall_clock_seconds"), (int, float)))
    sum_worker_game_seconds = sum(latencies)
    wall_clock_throughput = (len(rows) / wall_clock_seconds) if wall_clock_seconds > 0 else None
    summary = {"schema_version": "offline-scaleup-run-summary-v2", "run_id": run_dir.name, "phase": "league", "planned": schedule["planned_games"], "completed": len(rows),
               "terminal": len(terminal), "missing": schedule["planned_games"] - len(set(ids)), "valid_legal_games": len(valid),
               "legal_games": sum(row.get("legal") is True for row in terminal), "candidate_faults": sum(bool(row.get("candidate_fault")) for row in rows),
               "mapping_failures": sum(row.get("mapping_valid") is False for row in rows), "score_identity_failures": sum(row.get("score_identity_valid") is False for row in rows),
               "duplicate_completion": duplicate, "fault_counts": dict(sorted(faults.items())),
               # Aggregated from per-game ``decision_counters``.  Runs collected
               # before this contract report zero here; that is an absence of
               # measurement, not evidence of zero delegation.
               **{key: sum(int(row.get("decision_counters", {}).get(key, 0)) for row in rows)
                  for key in ("optional_prompt_count", "optional_declined_count", "captured_decision_count",
                              "uncaptured_fallback_count", "actual_fallback_decisions")},
               "decision_counters_recorded": sum(isinstance(row.get("decision_counters"), Mapping) for row in rows),
               # Kept for artifact compatibility only.  It is an inverse of
               # summed per-game durations, not a wall-clock throughput.
               "throughput_games_per_second": round(len(rows) / sum_worker_game_seconds, 5) if sum_worker_game_seconds > 0 else None,
               "wall_clock_games_per_second": round(wall_clock_throughput, 5) if wall_clock_throughput is not None else None,
               "wall_clock_seconds_per_game": round(wall_clock_seconds / len(rows), 5) if rows and wall_clock_seconds > 0 else None,
               "sum_worker_game_seconds": round(sum_worker_game_seconds, 5) if latencies else None,
               "effective_parallelism": round(sum_worker_game_seconds / wall_clock_seconds, 5) if wall_clock_seconds > 0 else None,
               "latency_seconds": {"p50": statistics.median(latencies) if latencies else None, "p95": sorted(latencies)[max(0, int(.95 * len(latencies)) - 1)] if latencies else None}}
    gate = summary["completed"] == summary["planned"] and summary["legal_games"] == summary["planned"] and not any(summary[key] for key in ("candidate_faults", "mapping_failures", "score_identity_failures", "duplicate_completion"))
    summary["gate"] = "PASS" if gate else "BLOCKED"
    _atomic_json(run_dir / "run_summary.json", summary)
    _atomic_json(run_dir / "fault_summary.json", {"schema_version": "offline-scaleup-fault-summary-v1", "run_id": run_dir.name, "fault_counts": summary["fault_counts"], "sample_limit": 5})
    throughput = summary["wall_clock_games_per_second"] or summary["throughput_games_per_second"]
    remaining = max(0, summary["planned"] - summary["completed"])
    eta_seconds = (remaining / throughput) if throughput else (0 if remaining == 0 else None)
    elapsed_seconds = (summary["completed"] / throughput) if throughput else None
    fault_total = sum(count for kind, count in summary["fault_counts"].items() if kind not in ("NONE", "COMPLETED"))
    _atomic_json(run_dir / "progress_summary.json", {"phase": "league", "run_id": summary["run_id"], "completed": summary["completed"],
        "planned": summary["planned"], "percent": round(100.0 * summary["completed"] / summary["planned"], 2) if summary["planned"] else 0.0,
        "valid": summary["valid_legal_games"], "legal": summary["legal_games"], "faults": fault_total, "elapsed_seconds": elapsed_seconds,
        "throughput": throughput, "eta_seconds": eta_seconds, "workers": workers, "updated_at": time.time(), "gate": summary["gate"]})
    (run_dir / "next_command.txt").write_text("resume-league" if summary["gate"] != "PASS" else "export-dataset", encoding="utf-8")
    return summary


def _execute_league_job(job: Mapping[str, Any], *, population_path: str, repo: str, executor: str,
                        timeout: float, max_attempts: int, run_dir: str, worker_reuse_games: int = 1) -> dict[str, Any]:
    """Execute one game in a process that has no parent worker threads.

    CABT itself remains isolated in its existing subprocess.  The additional
    spawn boundary is intentional: Python must not fork that subprocess from
    a ThreadPool worker after native CABT dependencies have been imported.

    Each worker runs a CPU candidate policy.  Without an explicit cap every
    worker's intra-op pool sizes itself to the whole machine, so N workers
    request N x cores threads and contend instead of scaling.  Environment
    variables alone are not enough here because a spawned child may import
    torch after they were read, so set the cap directly as well.
    """
    for variable in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ.setdefault(variable, "1")
    try:
        import torch
        torch.set_num_threads(1)
    except ImportError:
        pass
    worker_started = time.time()
    submitted = job.get("_queue_submitted_at_unix")
    queue_wait = max(0.0, worker_started - float(submitted)) if isinstance(submitted, (int, float)) else None
    try:
        worker_fd_count = len(os.listdir("/proc/self/fd"))
    except OSError:
        worker_fd_count = None
    try:
        import resource
        worker_max_rss_kib = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    except (ImportError, AttributeError):
        worker_max_rss_kib = None
    root = Path(run_dir)
    attempts: list[dict[str, Any]] = []
    outcome: dict[str, Any] = {}
    for attempt in range(1, max_attempts + 1):
        trajectory_root = root / "trajectories" if executor == "cabt" else None
        outcome, fault = _run_worker(
            job, population_path=Path(population_path), repo=Path(repo), executor=executor,
            timeout=timeout, scratch=root / "scratch", trajectory_root=trajectory_root,
            diagnostic_root=root / "failed_game_details" if executor == "cabt" else None,
            reuse_games=worker_reuse_games,
        )
        attempts.append({"attempt": attempt, **fault})
        if outcome.get("status") == "DONE" or fault["kind"] != "HARD_TIMEOUT":
            break
    public_job = {key: value for key, value in job.items() if not str(key).startswith("_")}
    return {"schema_version": RESULT_SCHEMA, **public_job, **outcome, "attempt_history": attempts,
            "fault": attempts[-1], "worker_queue_wait_seconds": queue_wait,
            "worker_fd_count_at_start": worker_fd_count, "worker_max_rss_kib_at_start": worker_max_rss_kib,
            "completed_at_unix": time.time()}


def run_league(*, run_dir: Path, population_path: Path, repo: Path, executor: str, timeout: float, max_attempts: int,
                workers: int = 2, progress: bool | None = None, progress_interval_seconds: float | None = None,
                start_method: str = "spawn", worker_recycle_games: int = 32,
                stop_after: int | None = None, worker_reuse_games: int = 1) -> dict[str, Any]:
    wall_started = time.monotonic()
    schedule = _read_json(run_dir / "schedule.json"); population = _read_json(population_path)
    if schedule.get("population_digest") != population.get("semantic_population_digest"):
        raise ContractError("schedule/population digest mismatch")
    records_path = run_dir / "game_results.jsonl"; existing = list(_jsonl(records_path)); completed = {str(row.get("game_id")) for row in existing}
    attempts_path = run_dir / "attempts.jsonl"
    if len(completed) != len(existing):
        raise ContractError("duplicate completion already exists; refusing resume")
    if workers < 1:
        raise ContractError("workers must be positive")
    if start_method != "spawn":
        raise ContractError("CABT league start method is fixed to spawn")
    if worker_recycle_games < 1:
        raise ContractError("worker_recycle_games must be positive")
    if worker_reuse_games < 1:
        raise ContractError("worker_reuse_games must be positive")
    if stop_after is not None and stop_after < 1:
        raise ContractError("stop_after must be positive when specified")
    all_pending = [job for job in schedule["games"] if job["game_id"] not in completed]
    intentional_pause = stop_after is not None and len(all_pending) > stop_after
    pending = all_pending[:stop_after] if stop_after is not None else all_pending

    valid_count = sum(1 for row in existing if row.get("status") == "DONE" and row.get("legal") is True and not row.get("candidate_fault"))
    legal_count = sum(1 for row in existing if row.get("legal") is True)
    fault_count = sum(1 for row in existing if row.get("fault", {}).get("kind") not in (None, "NONE", "COMPLETED"))
    reporter = ProgressReporter(phase=run_dir.name, total=schedule["planned_games"], initial=len(completed), run_id=run_dir.name,
                                 workers=workers, unit="game", progress=progress, interval_seconds=progress_interval_seconds,
                                 summary_path=run_dir / "progress_summary.json")
    reporter.update(0, valid=valid_count, legal=legal_count, faults=fault_count)
    # Fixture execution stays in-process for test injection.  Real CABT uses
    # spawn workers, so each worker launches the CABT child from its main
    # thread.  Recreate the whole pool after a bounded generation rather than
    # using ProcessPoolExecutor.max_tasks_per_child: CPython can stall while
    # replacing the sole worker exactly at that limit.
    from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
    import multiprocessing

    def persist(record: Mapping[str, Any]) -> None:
        for attempt in record["attempt_history"]:
            _write_jsonl_once(attempts_path, {"schema_version": "offline-scaleup-attempt-v1",
                                              "game_id": record["game_id"], **attempt})
        if record["game_id"] in completed:
            raise ContractError("duplicate terminal completion rejected")
        _write_jsonl_once(records_path, record); completed.add(record["game_id"])
        _atomic_json(run_dir / "checkpoint.json", {"schedule_digest": schedule["schedule_digest"],
                                                     "completed_game_ids": sorted(completed),
                                                     "start_method": start_method,
                                                     "worker_recycle_games": worker_recycle_games,
                                                     "worker_reuse_games": worker_reuse_games})

    try:
        if executor == "cabt":
            context = multiprocessing.get_context(start_method)
            generation_size = workers * worker_recycle_games
            generation_metrics_path = run_dir / "pool_generation_metrics.jsonl"
            for generation, start in enumerate(range(0, len(pending), generation_size), 1):
                generation_jobs = pending[start:start + generation_size]
                generation_started = time.monotonic(); generation_records: list[Mapping[str, Any]] = []
                reporter.update(0, valid=valid_count, legal=legal_count, faults=fault_count,
                                pool_generation=generation)
                with ProcessPoolExecutor(max_workers=workers, mp_context=context) as pool:
                    futures = {pool.submit(_execute_league_job, {**job, "_queue_submitted_at_unix": time.time()}, population_path=str(population_path), repo=str(repo),
                                           executor=executor, timeout=timeout, max_attempts=max_attempts,
                                           run_dir=str(run_dir), worker_reuse_games=worker_reuse_games): job for job in generation_jobs}
                    for future in as_completed(futures):
                        record = future.result(); generation_records.append(record); persist(record)
                        if record.get("status") == "DONE" and record.get("legal") is True and not record.get("candidate_fault"):
                            valid_count += 1
                        if record.get("legal") is True:
                            legal_count += 1
                        if record.get("fault", {}).get("kind") not in (None, "NONE", "COMPLETED"):
                            fault_count += 1
                        reporter.update(1, valid=valid_count, legal=legal_count, faults=fault_count,
                                        pool_generation=generation)
                queue_waits = [float(record["worker_queue_wait_seconds"]) for record in generation_records
                               if isinstance(record.get("worker_queue_wait_seconds"), (int, float))]
                rss_values = [int(record["worker_max_rss_kib_at_start"]) for record in generation_records
                              if isinstance(record.get("worker_max_rss_kib_at_start"), int)]
                fd_values = [int(record["worker_fd_count_at_start"]) for record in generation_records
                             if isinstance(record.get("worker_fd_count_at_start"), int)]
                _write_jsonl_once(generation_metrics_path, {"schema_version": "offline-scaleup-pool-generation-v1", "generation": generation,
                    "jobs": len(generation_jobs), "wall_clock_seconds": round(max(0.0, time.monotonic() - generation_started), 6),
                    "queue_wait_seconds": {"p50": statistics.median(queue_waits) if queue_waits else None,
                                            "p95": sorted(queue_waits)[max(0, int(.95 * len(queue_waits)) - 1)] if queue_waits else None},
                    "worker_rss_kib": {"max": max(rss_values) if rss_values else None},
                    "worker_fd_count": {"max": max(fd_values) if fd_values else None}})
        else:
            # Keep unit-test monkeypatches local while retaining the preexisting
            # bounded-thread fixture behaviour.
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {pool.submit(_execute_league_job, job, population_path=str(population_path), repo=str(repo),
                                       executor=executor, timeout=timeout, max_attempts=max_attempts,
                                       run_dir=str(run_dir), worker_reuse_games=worker_reuse_games): job for job in pending}
                for future in as_completed(futures):
                    record = future.result(); persist(record)
                    if record.get("status") == "DONE" and record.get("legal") is True and not record.get("candidate_fault"):
                        valid_count += 1
                    if record.get("legal") is True:
                        legal_count += 1
                    if record.get("fault", {}).get("kind") not in (None, "NONE", "COMPLETED"):
                        fault_count += 1
                    reporter.update(1, valid=valid_count, legal=legal_count, faults=fault_count)
    finally:
        reporter.close()
    wall_elapsed = max(0.0, time.monotonic() - wall_started)
    timing_path = run_dir / "wall_clock_timing.json"
    existing_timing = _read_json(timing_path) if timing_path.is_file() else {}
    segments = existing_timing.get("segments", []) if isinstance(existing_timing, Mapping) else []
    if pending:
        segments.append({"wall_clock_seconds": wall_elapsed, "completed_before": len(existing), "submitted_games": len(pending),
                         "completed_after": len(completed), "workers": workers})
    _atomic_json(timing_path, {"schema_version": "offline-scaleup-wall-clock-v1", "segments": segments})
    summary = summarize_run(run_dir, workers=workers)
    if intentional_pause:
        _atomic_json(run_dir / "intentional_pause.json", {
            "schema_version": "offline-scaleup-intentional-pause-v1", "completed": summary["completed"],
            "planned": summary["planned"], "remaining": summary["planned"] - summary["completed"],
            "next_command": "resume-league", "schedule_digest": schedule["schedule_digest"],
        })
    elif summary["gate"] != "PASS":
        _atomic_json(run_dir / "run_failure.json", {"schema_version": "offline-scaleup-run-failure-v1", "stage": "run-league", "exception_type": "LeagueGateFailure", "message": "league completed with non-passing gate", "game_id": next((row.get("game_id") for row in list(_jsonl(records_path)) if row.get("status") != "DONE"), None), "returncode": 2, "completed": summary["completed"], "planned": summary["planned"], "schedule_digest": schedule["schedule_digest"], "population_digest": schedule["population_digest"], "resumable": True, "next_command": "resume-league"})
    return summary


def _teacher_dataset_record(game: Mapping[str, Any], sample: Mapping[str, Any], population_digest: str) -> dict[str, Any]:
    example = RuleBCExample.from_dict(sample)
    metadata = game.get("teacher_metadata") if isinstance(game.get("teacher_metadata"), Mapping) else {}
    candidate_side = int(game["candidate_side"])
    winner = game.get("winner")
    if game.get("status") != "DONE":
        candidate_outcome = "UNKNOWN"
    elif winner == candidate_side:
        candidate_outcome = "WIN"
    elif winner in (0, 1):
        candidate_outcome = "LOSS"
    elif winner == -1:
        candidate_outcome = "DRAW"
    else:
        # A completed engine result without a documented winner encoding is
        # not evidence for either class.  Preserve it rather than guessing.
        candidate_outcome = "UNKNOWN"
    record = {"schema_version": DATASET_SCHEMA, "episode_id": game["game_id"], "game_id": game["game_id"], "turn": 0, "phase": "OBSERVED",
              "state_fingerprint": example.example_id, "deck_fingerprint": example.deck_fingerprint,
              "opponent_fingerprint": _digest({"opponent": game["opponent"]}, "opponent"), "candidate_side": candidate_side,
              "teacher_identity": metadata.get("teacher_identity", game["candidate"]),
              "teacher_type": metadata.get("teacher_type", "RULE_V0_DECK"), "teacher_trust": metadata.get("teacher_trust", "TRUSTED"),
              "runtime_fingerprint": metadata.get("runtime_fingerprint", _digest({"candidate": game["candidate"]}, "runtime")), "legal_action_candidates": list(example.legal_actions),
              "selected_action": list(example.target_action_digests), "selected_action_key": list(example.target_action_digests),
              "state_features": {"public_state": example.public_state, "own_private_state": example.own_private_state, "visible_history": list(example.visible_history)},
              "action_features": list(example.legal_actions), "family": None, "strategy": "rule", "variant": None, "rule_score": list(example.teacher_ranking),
              "terminal_result": game["status"], "source_winner": winner, "candidate_outcome": candidate_outcome,
              "fault_class": game["fault"]["kind"], "legality": True,
              "provenance": {"population_digest": population_digest, "source_revision": example.source_revision},
              "rule_bc_example": example.to_dict()}
    for key in ("behavior_log_probability", "actor_policy_version", "vocabulary_hash", "rule_proposal_digests"):
        if key in sample:
            record[key] = sample[key]
    if _contains_forbidden(record):
        raise ContractError("privacy violation in dataset materialization")
    legal = {item["digest"] for item in example.legal_actions}
    if not set(example.target_action_digests).issubset(legal):
        raise ContractError("selected action is not legal")
    return record


def _valid_terminal_games(run_dir: Path) -> list[dict[str, Any]]:
    rows = list(_jsonl(run_dir / "game_results.jsonl"))
    return [game for game in rows if game.get("status") == "DONE" and game.get("legal") is True
            and not game.get("candidate_fault") and game.get("mapping_valid") and game.get("score_identity_valid")]


def export_dataset(*, run_dir: Path, output: Path, progress: bool | None = None, progress_interval_seconds: float | None = None) -> dict[str, Any]:
    population_digest = _read_json(run_dir / "schedule.json")["population_digest"]
    valid_games = _valid_terminal_games(run_dir)
    reporter = ProgressReporter(phase="export-dataset", total=len(valid_games), run_id=run_dir.name, unit="game",
                                 progress=progress, interval_seconds=progress_interval_seconds)
    records: list[dict[str, Any]] = []
    for game in valid_games:
        for sample in game.get("teacher_samples", []):
            records.append(_teacher_dataset_record(game, sample, population_digest))
        reporter.update(1)
    reporter.close()
    if not records:
        raise ContractError("no valid teacher decisions available; league may be valid but has no observable choices")
    episodes = sorted({str(record["episode_id"]) for record in records})
    # The whole episode, never a decision row, owns its split.  The first two
    # stable hash ranks reserve test/validation whenever enough episodes exist.
    ranked = sorted(episodes, key=lambda item: _digest(item, "split"))
    assignments = {episode: "train" for episode in episodes}
    if len(ranked) >= 3:
        assignments[ranked[0]], assignments[ranked[1]] = "test", "validation"
    elif len(ranked) == 2:
        assignments[ranked[0]], assignments[ranked[1]] = "train", "validation"
    for record in records:
        record["split"] = assignments[record["episode_id"]]
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise ContractError("dataset output already exists")
    for record in records:
        _write_jsonl_once(output, record)
    counts = Counter(assignments.values())
    summary = {"schema_version": "offline-scaleup-dataset-summary-v2", "records": len(records), "episodes": len(episodes), "splits": dict(counts),
               "illegal_selected_actions": 0, "quarantined_teacher_records": 0, "episode_leakage": 0, "opponent_holdout_leakage": 0, "deck_holdout_leakage": 0,
               "parse_valid": True, "dataset": str(output)}
    _atomic_json(output.with_suffix(".summary.json"), summary)
    return summary


def _load_v1_examples(dataset: Path, split: str | None = None) -> list[RuleBCExample]:
    examples = []
    for row in _jsonl(dataset):
        if row.get("schema_version") != DATASET_SCHEMA or (split is not None and row.get("split") != split):
            continue
        examples.append(RuleBCExample.from_dict(row["rule_bc_example"]))
    if not examples:
        raise ContractError(f"dataset has no {split or 'usable'} examples")
    return examples


def _population_entries_by_id(population: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["opponent_id"]: item for item in population["entries"]}


def select_opponent_holdout(population: Mapping[str, Any], present_opponent_ids: set[str]) -> str:
    """Deterministically pick 1 TEAM_NATIVE/FAMILY_SPECIFIC opponent id actually present in the run."""
    entries = _population_entries_by_id(population)
    candidates = sorted(
        opponent_id for opponent_id in present_opponent_ids
        if entries.get(opponent_id, {}).get("opponent_type") in {"TEAM_NATIVE", "FAMILY_SPECIFIC"}
    )
    if not candidates:
        raise ContractError("no TEAM_NATIVE or FAMILY_SPECIFIC opponent is present in this run for opponent-holdout selection")
    return min(candidates, key=lambda opponent_id: _digest(opponent_id, "opponent-holdout-selection"))


def select_deck_holdout(population: Mapping[str, Any], present_opponent_ids: set[str], opponent_holdout_id: str) -> str:
    """Deterministically pick 1 RULE_V0_DECK deck fingerprint, excluding the opponent-holdout's own deck."""
    entries = _population_entries_by_id(population)
    excluded_fingerprint = entries[opponent_holdout_id]["deck_fingerprint"]
    candidates = sorted({
        entries[opponent_id]["deck_fingerprint"] for opponent_id in present_opponent_ids
        if entries.get(opponent_id, {}).get("opponent_type") == "RULE_V0_DECK"
        and entries[opponent_id]["deck_fingerprint"] != excluded_fingerprint
    })
    if not candidates:
        raise ContractError("no RULE_V0_DECK opponent deck fingerprint is available for deck-holdout selection")
    return min(candidates, key=lambda fingerprint: _digest(fingerprint, "deck-holdout-selection"))


MIN_SPLIT_EPISODES = {"train": 500, "validation": 50, "test": 50, "opponent_holdout": 50, "deck_holdout": 50}


def _stratified_remainder_assignment(cells: Mapping[tuple[str, int], list[str]]) -> dict[str, str]:
    """Deterministically split each (opponent, side) cell 80/10/10; rounding remainder goes to test."""
    assignment: dict[str, str] = {}
    for key in sorted(cells):
        episodes = sorted(cells[key], key=lambda episode_id: _digest((key, episode_id), "remaining-split-order"))
        total = len(episodes)
        train_n = int(total * 0.8)
        validation_n = int(total * 0.1)
        for episode_id in episodes[:train_n]:
            assignment[episode_id] = "train"
        for episode_id in episodes[train_n:train_n + validation_n]:
            assignment[episode_id] = "validation"
        for episode_id in episodes[train_n + validation_n:]:
            assignment[episode_id] = "test"
    return assignment


def build_split_manifest(*, run_dir: Path, population_path: Path) -> dict[str, Any]:
    """Compute the deterministic 5-cohort split for one already-completed, Gate-PASS league run."""
    run_summary = _read_json(run_dir / "run_summary.json")
    if run_summary.get("gate") != "PASS":
        raise ContractError("run gate must PASS before dataset split remediation")
    schedule = _read_json(run_dir / "schedule.json")
    population = _read_json(population_path)
    if schedule.get("population_digest") != population.get("semantic_population_digest"):
        raise ContractError("population snapshot does not match the run schedule's population digest")
    entries = _population_entries_by_id(population)
    valid_games = _valid_terminal_games(run_dir)
    if len(valid_games) != run_summary["completed"]:
        raise ContractError("run contains non-valid games; dataset split remediation requires a fully valid run")
    episode_opponent: dict[str, str] = {}
    episode_side: dict[str, int] = {}
    for game in valid_games:
        opponent_id = str(game["opponent"])
        if opponent_id not in entries:
            raise ContractError(f"opponent {opponent_id} is absent from the supplied population snapshot")
        episode_id = str(game["game_id"])
        episode_opponent[episode_id] = opponent_id
        episode_side[episode_id] = int(game["candidate_side"])
    present_ids = set(episode_opponent.values())
    opponent_holdout_id = select_opponent_holdout(population, present_ids)
    deck_holdout_fingerprint = select_deck_holdout(population, present_ids, opponent_holdout_id)
    opponent_holdout_episodes = {ep for ep, opp in episode_opponent.items() if opp == opponent_holdout_id}
    deck_holdout_episodes = {
        ep for ep, opp in episode_opponent.items()
        if entries[opp]["opponent_type"] == "RULE_V0_DECK"
        and entries[opp]["deck_fingerprint"] == deck_holdout_fingerprint
        and ep not in opponent_holdout_episodes
    }
    reserved = opponent_holdout_episodes | deck_holdout_episodes
    cells: dict[tuple[str, int], list[str]] = defaultdict(list)
    for episode_id, opponent_id in episode_opponent.items():
        if episode_id in reserved:
            continue
        cells[(opponent_id, episode_side[episode_id])].append(episode_id)
    assignment = _stratified_remainder_assignment(cells)
    for episode_id in opponent_holdout_episodes:
        assignment[episode_id] = "opponent_holdout"
    for episode_id in deck_holdout_episodes:
        assignment[episode_id] = "deck_holdout"
    if set(assignment) != set(episode_opponent):
        raise ContractError("split assignment left an episode unassigned")
    return {"schema_version": "offline-scaleup-dataset-split-manifest-v2", "run_id": run_dir.name,
            "population_digest": population["semantic_population_digest"],
            "opponent_holdout_id": opponent_holdout_id, "deck_holdout_fingerprint": deck_holdout_fingerprint,
            "episode_count": len(episode_opponent), "split_counts": dict(sorted(Counter(assignment.values()).items())),
            "episode_assignment": assignment, "episode_opponent": episode_opponent, "episode_side": episode_side}


def validate_split_gate(manifest: Mapping[str, Any]) -> dict[str, Any]:
    counts = manifest["split_counts"]
    failures = [f"{name}<{minimum} (actual={counts.get(name, 0)})" for name, minimum in MIN_SPLIT_EPISODES.items() if counts.get(name, 0) < minimum]
    return {"schema_version": "offline-scaleup-split-gate-v1", "counts": counts, "failures": failures,
            "gate": "PASS" if not failures else "BLOCKED"}


def _build_episode_records(args: tuple[dict[str, Any], str]) -> list[dict[str, Any]]:
    game, population_digest = args
    return [_teacher_dataset_record(game, sample, population_digest) for sample in game.get("teacher_samples", [])]


def _group(records: list[dict[str, Any]], field: str) -> dict[Any, list[dict[str, Any]]]:
    out: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        out[record[field]].append(record)
    return out


def _composition_report(records: list[dict[str, Any]], manifest: Mapping[str, Any]) -> dict[str, Any]:
    per_split: dict[str, dict[str, Any]] = {}
    for split_name in ("train", "validation", "test", "opponent_holdout", "deck_holdout"):
        split_records = [r for r in records if r["split"] == split_name]
        episodes = {r["episode_id"] for r in split_records}
        per_split[split_name] = {
            "episodes": len(episodes), "records": len(split_records),
            "unique_opponents": len({manifest["episode_opponent"][str(e)] for e in episodes}),
            "unique_decks": len({r["opponent_deck_fingerprint"] for r in split_records}),
            "sides": dict(sorted(Counter(manifest["episode_side"][str(e)] for e in episodes).items())),
        }
    return {"schema_version": "offline-scaleup-dataset-composition-v2", "records_total": len(records),
            "episodes_total": manifest["episode_count"], "splits": per_split}


def _teacher_distribution_report(records: list[dict[str, Any]]) -> dict[str, Any]:
    def _by(field: str) -> dict[str, dict[str, int]]:
        out: dict[str, dict[str, int]] = {}
        for value, group in _group(records, field).items():
            out[str(value)] = {"episodes": len({r["episode_id"] for r in group}), "records": len(group)}
        return out

    identities = {r["teacher_identity"] for r in records}
    types = {r["teacher_type"] for r in records}
    trusts = {r["teacher_trust"] for r in records}
    all_rule_v0 = identities == {"rule-v0-current-deck"} and types == {"RULE_V0_DECK"} and trusts == {"TRUSTED"}
    return {"schema_version": "offline-scaleup-dataset-teacher-distribution-v2",
            "teacher_identity": _by("teacher_identity"), "teacher_type": _by("teacher_type"), "teacher_trust": _by("teacher_trust"),
            "opponent_type": _by("opponent_type"), "opponent_id": _by("opponent_id"), "candidate_side": _by("candidate_side"),
            "deck_fingerprint": _by("opponent_deck_fingerprint"), "split": _by("split"),
            "single_teacher_finding": {
                "all_teachers_rule_v0": all_rule_v0,
                "statement": ("全教師記録の teacher_identity/teacher_type/teacher_trust は rule-v0-current-deck / RULE_V0_DECK / TRUSTED のみである。"
                              "これは実データを集計した事実でありエラーではない。Student v1 は Rule v0 の Behavior Cloning baseline である。"
                              "FAMILY_SPECIFIC / TEAM_NATIVE の opponent は状態分布を多様化する対戦相手であり、教師方策ではない。") if all_rule_v0 else
                             "教師分布が単一ではない。teacher_identity/teacher_type別の内訳を確認すること。"}}


def _leakage_report(manifest: Mapping[str, Any], records: list[dict[str, Any]]) -> dict[str, Any]:
    episode_to_splits: dict[str, set[str]] = defaultdict(set)
    for record in records:
        episode_to_splits[str(record["episode_id"])].add(record["split"])
    episode_leakage = sum(1 for splits in episode_to_splits.values() if len(splits) > 1)
    unassigned = sum(1 for episode_id in manifest["episode_opponent"] if episode_id not in manifest["episode_assignment"])
    opponent_holdout_leakage = sum(1 for r in records if r["split"] != "opponent_holdout" and r["opponent_id"] == manifest["opponent_holdout_id"])
    deck_holdout_leakage = sum(1 for r in records if r["split"] not in {"deck_holdout", "opponent_holdout"}
                                and r["opponent_deck_fingerprint"] == manifest["deck_holdout_fingerprint"] and r["opponent_type"] == "RULE_V0_DECK")
    return {"schema_version": "offline-scaleup-dataset-leakage-check-v2", "episode_leakage": episode_leakage,
            "opponent_holdout_leakage": opponent_holdout_leakage, "deck_holdout_leakage": deck_holdout_leakage,
            "unassigned_episodes": unassigned, "duplicate_split_episodes": episode_leakage}


def _quality_report(records: list[dict[str, Any]], gate: Mapping[str, Any]) -> dict[str, Any]:
    illegal = 0
    for record in records:
        legal_digests = {item["digest"] for item in record["legal_action_candidates"]}
        if not set(record["selected_action"]).issubset(legal_digests):
            illegal += 1
    quarantined = sum(1 for r in records if r["teacher_trust"] not in {"TRUSTED", "LIMITED"})
    provenance_missing = sum(1 for r in records if not r.get("provenance", {}).get("population_digest") or not r.get("provenance", {}).get("source_revision"))
    return {"schema_version": "offline-scaleup-dataset-quality-report-v2", "parse_valid": True, "records": len(records),
            "illegal_selected_actions": illegal, "quarantined_teacher_records": quarantined,
            "provenance_missing": provenance_missing, "split_gate": gate}


def export_dataset_v2(*, run_dir: Path, population_path: Path, artifact_root: Path, workers: int | None = None,
                       show_progress: bool = True, progress: bool | None = None, progress_interval_seconds: float | None = None) -> dict[str, Any]:
    resolved_progress = progress if progress is not None else (None if show_progress else False)
    manifest = build_split_manifest(run_dir=run_dir, population_path=population_path)
    gate = validate_split_gate(manifest)
    population = _read_json(population_path)
    entries = _population_entries_by_id(population)
    dataset_path = artifact_root / "datasets" / "stability-900-split-v2.jsonl"
    if dataset_path.exists():
        raise ContractError("dataset output already exists")
    valid_games = _valid_terminal_games(run_dir)
    jobs = [(game, manifest["population_digest"]) for game in valid_games]
    resolved_workers = workers if workers is not None else default_worker_count()
    build_reporter = ProgressReporter(phase="dataset-build", total=len(jobs), run_id=run_dir.name, workers=resolved_workers,
                                       unit="episode", progress=resolved_progress, interval_seconds=progress_interval_seconds)
    per_game_records: list[list[dict[str, Any]]]
    if resolved_workers <= 1 or len(jobs) < 2:
        per_game_records = []
        for job in jobs:
            per_game_records.append(_build_episode_records(job))
            build_reporter.update(1)
    else:
        from concurrent.futures import ProcessPoolExecutor, as_completed
        per_game_records = [[] for _ in jobs]
        with ProcessPoolExecutor(max_workers=resolved_workers) as pool:
            futures = {pool.submit(_build_episode_records, job): index for index, job in enumerate(jobs)}
            for future in as_completed(futures):
                per_game_records[futures[future]] = future.result()
                build_reporter.update(1)
    build_reporter.close()
    records: list[dict[str, Any]] = []
    for game, game_records in zip(valid_games, per_game_records):
        episode_id = str(game["game_id"])
        opponent_id = manifest["episode_opponent"][episode_id]
        entry = entries[opponent_id]
        split = manifest["episode_assignment"][episode_id]
        for record in game_records:
            record["split"] = split
            record["opponent_id"] = opponent_id
            record["opponent_type"] = entry["opponent_type"]
            record["opponent_deck_fingerprint"] = entry["deck_fingerprint"]
            record["family_id"] = entry.get("family_id")
            records.append(record)
    if not records:
        raise ContractError("no valid teacher decisions available for split dataset")
    write_reporter = ProgressReporter(phase="dataset-write", total=len(records), run_id=run_dir.name, unit="record",
                                       progress=resolved_progress, interval_seconds=progress_interval_seconds)
    for record in records:
        _write_jsonl_once(dataset_path, record)
        write_reporter.update(1, split=record["split"])
    write_reporter.close()
    composition = _composition_report(records, manifest)
    teacher_distribution = _teacher_distribution_report(records)
    leakage = _leakage_report(manifest, records)
    quality = _quality_report(records, gate)
    reasons = list(gate["failures"])
    if leakage["episode_leakage"] or leakage["opponent_holdout_leakage"] or leakage["deck_holdout_leakage"] or leakage["unassigned_episodes"]:
        reasons.append("leakage_detected")
    if quality["illegal_selected_actions"] or quality["provenance_missing"]:
        reasons.append("quality_check_failed")
    verdict_value = "READY_FOR_STUDENT_V1_TRAINING" if gate["gate"] == "PASS" and not reasons else \
        ("READY_AFTER_LIMITED_DATASET_FIX" if gate["gate"] == "PASS" else "DATASET_SPLIT_REWORK_REQUIRED")
    verdict = {"schema_version": "offline-scaleup-dataset-split-remediation-verdict-v1", "verdict": verdict_value,
               "reasons": reasons, "split_counts": manifest["split_counts"], "gate_minimums": MIN_SPLIT_EPISODES,
               "opponent_holdout_id": manifest["opponent_holdout_id"], "deck_holdout_fingerprint": manifest["deck_holdout_fingerprint"],
               "cabt_rerun": 0, "dataset": str(dataset_path)}
    _atomic_json(artifact_root / "artifacts" / "dataset_split_manifest_v2.json", manifest)
    _atomic_json(artifact_root / "artifacts" / "dataset_composition_v2.json", composition)
    _atomic_json(artifact_root / "artifacts" / "dataset_teacher_distribution_v2.json", teacher_distribution)
    _atomic_json(artifact_root / "artifacts" / "dataset_leakage_check_v2.json", leakage)
    _atomic_json(artifact_root / "artifacts" / "dataset_quality_report_v2.json", quality)
    _atomic_json(artifact_root / "artifacts" / "dataset_split_remediation_verdict.json", verdict)
    return {"dataset": str(dataset_path), "manifest": str(artifact_root / "artifacts" / "dataset_split_manifest_v2.json"),
            "gate": gate["gate"], "verdict": verdict_value, "records": len(records), "episodes": manifest["episode_count"],
            "split_counts": manifest["split_counts"]}


def train_student_v1(*, dataset: Path, model_dir: Path, epochs: int, learning_rate: float,
                      progress: bool | None = None, progress_interval_seconds: float | None = None) -> dict[str, Any]:
    train = _load_v1_examples(dataset, "train")
    validation = _load_v1_examples(dataset, "validation")
    reporter = ProgressReporter(phase="train-student-v1", total=epochs, run_id=model_dir.name, unit="epoch",
                                 progress=progress, interval_seconds=progress_interval_seconds)

    def on_epoch(epoch_index: int, total_epochs: int, train_loss: float, weights: tuple[float, ...], bias: float) -> None:
        snapshot = StudentV0Model(weights, bias)
        validation_metrics = evaluate_model(snapshot, validation)
        reporter.update(1, train_loss=round(train_loss, 6), validation_loss=round(validation_metrics["holdout_loss"], 6),
                         top1_fidelity=round(validation_metrics["teacher_top1_fidelity"], 4))

    model = train_model(train, epochs=epochs, learning_rate=learning_rate, on_epoch=on_epoch)
    reporter.close()
    model_dir.mkdir(parents=True, exist_ok=True)
    model_path = model_dir / "student_v1_model.json"
    if model_path.exists():
        model_path.unlink()
    model.export(model_path)
    metrics = evaluate_model(model, validation)
    report = {"schema_version": STUDENT_SCHEMA, "model_type": "legal-candidate-linear-ranking", "model_version": "student-v1",
              "feature_schema_version": "student-v0-feature-v1", "training_examples": len(train), "validation_examples": len(validation),
              "validation": metrics, "legal_rate": 1.0, "fallback": "Rule Agent v0", "model_size_bytes": model_path.stat().st_size,
              "checkpoint_resume": "deterministic full-batch retrain; no optimizer state", "device": "CPU (GPU optional external trainer is not required for this model)"}
    _atomic_json(model_dir / "training_summary.json", report)
    (model_dir / "next_command.txt").write_text("evaluate-holdout", encoding="utf-8")
    return report


HOLDOUT_SPLITS = ("test", "opponent_holdout", "deck_holdout")
HOLDOUT_SCHEMA = "offline-scaleup-student-v1-holdout-evaluation-v2"


def _holdout_rows(dataset: Path) -> tuple[dict[str, list[dict[str, Any]]], dict[str, int], set[str]]:
    """Load the immutable dataset once and reject malformed holdout inputs.

    The old evaluator silently selected ``test`` while treating the other
    two holdouts as prose.  Keeping the raw rows here preserves the episode,
    opponent, deck, and teacher identity needed to attest each evaluation.
    """
    rows_by_split = {split: [] for split in (*HOLDOUT_SPLITS, "validation")}
    all_counts: Counter[str] = Counter()
    parse_errors: set[str] = set()
    for row in _jsonl(dataset):
        if row.get("schema_version") != DATASET_SCHEMA:
            continue
        split = row.get("split")
        if not isinstance(split, str):
            parse_errors.add("missing_split")
            continue
        all_counts[split] += 1
        if split in rows_by_split:
            try:
                RuleBCExample.from_dict(row["rule_bc_example"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ContractError(f"invalid {split} holdout record: {exc}") from exc
            rows_by_split[split].append(row)
    if parse_errors:
        raise ContractError("dataset contains a record without a usable split")
    missing = [split for split in HOLDOUT_SPLITS if not rows_by_split[split]]
    if missing:
        raise ContractError(f"dataset has no required holdout split(s): {', '.join(missing)}")
    return rows_by_split, dict(sorted(all_counts.items())), parse_errors


def _holdout_group_metrics(model: StudentV0Model, rows: list[dict[str, Any]], key: str,
                           fields: tuple[str, ...]) -> dict[str, dict[str, Any]]:
    groups: dict[str, list[RuleBCExample]] = defaultdict(list)
    for row in rows:
        value = row.get(key)
        groups[str(value) if value is not None else "UNKNOWN"].append(RuleBCExample.from_dict(row["rule_bc_example"]))
    result: dict[str, dict[str, Any]] = {}
    for value, examples in sorted(groups.items()):
        metrics = evaluate_model(model, examples)
        result[value] = {field: metrics[field] for field in fields}
    return result


def _teacher_distribution(rows: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    return {
        field: dict(sorted(Counter(str(row.get(field, "UNKNOWN")) for row in rows).items()))
        for field in ("teacher_identity", "teacher_type", "teacher_trust")
    }


def _evaluate_holdout_split(*, model: StudentV0Model, split: str, rows: list[dict[str, Any]],
                            output: Path, progress: bool | None,
                            progress_interval_seconds: float | None) -> dict[str, Any]:
    examples = [RuleBCExample.from_dict(row["rule_bc_example"]) for row in rows]
    reporter = ProgressReporter(phase=f"evaluate-holdout:{split}", total=len(examples),
                                run_id=Path(output).stem, unit="record", progress=progress,
                                interval_seconds=progress_interval_seconds)

    def on_example(index: int, total: int, stats: dict[str, int]) -> None:
        reporter.update(1, split=split, legal_rate=round(stats["legal"] / (index + 1), 4),
                        top1_fidelity=round(stats["top1"] / (index + 1), 4), fallback=stats["fallback"])

    try:
        metrics = evaluate_model(model, examples, on_example=on_example)
    finally:
        reporter.close()
    return {
        **metrics,
        "unique_episodes": len({str(row.get("episode_id", row.get("game_id", "UNKNOWN"))) for row in rows}),
        "candidate_side": _holdout_group_metrics(
            model, rows, "candidate_side", ("examples", "teacher_top1_fidelity", "legal_action_rate", "fallback_rate")),
        "opponent_id_top1": _holdout_group_metrics(model, rows, "opponent_id", ("examples", "teacher_top1_fidelity")),
        "opponent_type_top1": _holdout_group_metrics(model, rows, "opponent_type", ("examples", "teacher_top1_fidelity")),
        "deck_fingerprint_top1": _holdout_group_metrics(model, rows, "opponent_deck_fingerprint", ("examples", "teacher_top1_fidelity")),
        "teacher": _teacher_distribution(rows),
    }


def _holdout_integrity(*, rows_by_split: Mapping[str, list[dict[str, Any]]], expected_counts: Mapping[str, int],
                       artifact_root: Path) -> dict[str, Any]:
    manifest_path = artifact_root / "artifacts" / "dataset_split_manifest_v2.json"
    manifest = _read_json(manifest_path) if manifest_path.exists() else None
    episode_splits: dict[str, set[str]] = defaultdict(set)
    assignment_mismatches = 0
    for split, rows in rows_by_split.items():
        for row in rows:
            episode = str(row.get("episode_id", row.get("game_id", "UNKNOWN")))
            episode_splits[episode].add(split)
            if manifest is not None and split in HOLDOUT_SPLITS:
                assignments = manifest.get("episode_assignment", {})
                if not isinstance(assignments, dict) or assignments.get(episode) != split:
                    assignment_mismatches += 1
    split_contamination = sum(1 for splits in episode_splits.values() if len(splits) > 1) + assignment_mismatches
    opponent_expected = manifest.get("opponent_holdout_id") if manifest is not None else None
    deck_expected = manifest.get("deck_holdout_fingerprint") if manifest is not None else None
    opponent_mismatch = sum(1 for row in rows_by_split["opponent_holdout"]
                            if opponent_expected is not None and row.get("opponent_id") != opponent_expected)
    deck_mismatch = sum(1 for row in rows_by_split["deck_holdout"]
                        if deck_expected is not None and row.get("opponent_deck_fingerprint") != deck_expected)
    return {
        "schema_version": "offline-scaleup-student-v1-holdout-integrity-v2",
        "parse_success": True,
        "model_load_success": True,
        "expected_record_counts": {split: expected_counts.get(split, 0) for split in HOLDOUT_SPLITS},
        "observed_record_counts": {split: len(rows_by_split[split]) for split in HOLDOUT_SPLITS},
        "record_count_mismatch": sum(int(len(rows_by_split[split]) != expected_counts.get(split, 0)) for split in HOLDOUT_SPLITS),
        "split_contamination": split_contamination,
        "test_contamination": assignment_mismatches,
        "opponent_holdout_identity": opponent_expected,
        "opponent_holdout_identity_mismatch": opponent_mismatch,
        "deck_holdout_fingerprint": deck_expected,
        "deck_holdout_fingerprint_mismatch": deck_mismatch,
        "evaluation_exceptions": 0,
    }


def _holdout_comparison(validation: Mapping[str, Any], metrics: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    comparisons: dict[str, Any] = {}
    validation_types = validation.get("selection_type_top1", {})
    for split in HOLDOUT_SPLITS:
        current = metrics[split]
        type_delta = {
            key: (value - validation_types[key]) * 100.0
            for key, value in current["selection_type_top1"].items() if key in validation_types
        }
        comparisons[split] = {
            "top1_delta_pp": (current["teacher_top1_fidelity"] - validation["teacher_top1_fidelity"]) * 100.0,
            "top3_delta_pp": (current["teacher_top3_fidelity"] - validation["teacher_top3_fidelity"]) * 100.0,
            "loss_delta": current["holdout_loss"] - validation["holdout_loss"],
            "selection_type_top1_delta_pp": type_delta,
        }
    return {"schema_version": "offline-scaleup-student-v1-holdout-comparison-v2", "baseline": "validation", "splits": comparisons}


def _holdout_verdict(*, metrics: Mapping[str, Mapping[str, Any]], comparison: Mapping[str, Any],
                     integrity: Mapping[str, Any], model_load_success: bool) -> dict[str, Any]:
    failures: list[str] = []
    if not model_load_success:
        failures.append("model_load_failed")
    for key in ("record_count_mismatch", "split_contamination", "opponent_holdout_identity_mismatch",
                "deck_holdout_fingerprint_mismatch", "evaluation_exceptions"):
        if integrity[key] != 0:
            failures.append(key)
    for split in HOLDOUT_SPLITS:
        if metrics[split]["legal_action_rate"] != 1.0:
            failures.append(f"{split}:legal_action_rate")
        if metrics[split]["fallback_rate"] != 0.0:
            failures.append(f"{split}:fallback_rate")
    if failures:
        verdict = "INVALID_HOLDOUT_EVIDENCE"
    else:
        degraded = [split for split in HOLDOUT_SPLITS if comparison["splits"][split]["top1_delta_pp"] < -10.0]
        collapsed_types = [
            f"{split}:{kind}" for split in HOLDOUT_SPLITS
            for kind, count in metrics[split]["selection_type_examples"].items()
            if count and metrics[split]["selection_type_top1"][kind] == 0.0
        ]
        if degraded or collapsed_types:
            verdict = "GENERALIZATION_DEGRADATION_OBSERVED"
            failures.extend([*(f"{split}:top1_delta_below_-10pp" for split in degraded), *collapsed_types])
        else:
            verdict = "GENERALIZATION_BASELINE_VALIDATED"
    return {"schema_version": "offline-scaleup-student-v1-holdout-verdict-v2", "verdict": verdict,
            "gate": "PASS" if verdict == "GENERALIZATION_BASELINE_VALIDATED" else "FAIL",
            "reasons": failures, "criteria": {"minimum_legal_action_rate": 1.0, "maximum_top1_drop_pp": 10.0,
            "fallback_rate": 0.0, "split_contamination": 0}}


def _holdout_markdown(*, metrics: Mapping[str, Mapping[str, Any]], comparison: Mapping[str, Any], verdict: Mapping[str, Any]) -> str:
    lines = ["# Student v1 Holdout Evaluation", "", f"判定: `{verdict['verdict']}`", "",
             "| split | records | episodes | legal | fallback | top-1 | top-3 | validation 比 top-1 差分 |",
             "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for split in HOLDOUT_SPLITS:
        item = metrics[split]
        delta = comparison["splits"][split]["top1_delta_pp"]
        lines.append(f"| {split} | {item['examples']} | {item['unique_episodes']} | {item['legal_action_rate']:.4f} | {item['fallback_rate']:.4f} | {item['teacher_top1_fidelity']:.4f} | {item['teacher_top3_fidelity']:.4f} | {delta:+.2f} pp |")
    lines.extend(["", "固定済み Student v1 を再学習、CABT 再実行、Dataset 再生成なしで評価した。", ""])
    return "\n".join(lines)


def evaluate_holdout(*, dataset: Path, model_path: Path, output: Path, artifact_root: Path | None = None,
                     progress: bool | None = None, progress_interval_seconds: float | None = None) -> dict[str, Any]:
    root = artifact_root if artifact_root is not None else output.parent.parent
    rows_by_split, record_counts, _parse_errors = _holdout_rows(dataset)
    model = StudentV0Model.load(model_path)
    metrics = {
        split: _evaluate_holdout_split(model=model, split=split, rows=rows_by_split[split], output=output,
                                       progress=progress, progress_interval_seconds=progress_interval_seconds)
        for split in HOLDOUT_SPLITS
    }
    training = _read_json(model_path.parent / "training_summary.json")
    validation = training.get("validation")
    if not isinstance(validation, dict) or validation.get("examples") != len(rows_by_split["validation"]):
        raise ContractError("training summary validation metrics do not match the fixed dataset")
    integrity = _holdout_integrity(rows_by_split=rows_by_split, expected_counts=record_counts, artifact_root=root)
    comparison = _holdout_comparison(validation, metrics)
    verdict = _holdout_verdict(metrics=metrics, comparison=comparison, integrity=integrity, model_load_success=True)
    report = {"schema_version": HOLDOUT_SCHEMA, "model": str(model_path), "model_size_bytes": model_path.stat().st_size,
              "validation": validation, "splits": metrics, "gate": verdict["gate"], "verdict": verdict["verdict"]}
    _atomic_json(output, report)
    _atomic_json(root / "artifacts" / "student_v1_holdout_metrics.json", {"schema_version": HOLDOUT_SCHEMA, "splits": metrics})
    _atomic_json(root / "artifacts" / "student_v1_holdout_comparison.json", comparison)
    _atomic_json(root / "artifacts" / "student_v1_holdout_integrity.json", integrity)
    _atomic_json(root / "artifacts" / "student_v1_holdout_verdict.json", verdict)
    _atomic_json(root / "summaries" / "latest_holdout_summary.json", {"schema_version": HOLDOUT_SCHEMA, "gate": verdict["gate"],
                 "verdict": verdict["verdict"], "splits": {split: {key: metrics[split][key] for key in ("examples", "legal_action_rate", "fallback_rate", "teacher_top1_fidelity", "teacher_top3_fidelity")} for split in HOLDOUT_SPLITS}})
    doc_path = root / "docs" / "student_v1_holdout_evaluation.md"; doc_path.parent.mkdir(parents=True, exist_ok=True)
    doc_path.write_text(_holdout_markdown(metrics=metrics, comparison=comparison, verdict=verdict), encoding="utf-8")
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="offline-scaleup")
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build-population"); build.add_argument("--repo", type=Path, default=Path.cwd()); build.add_argument("--output", type=Path, required=True); build.add_argument("--recovery-root", type=Path, required=True); build.add_argument("--taxonomy-root", type=Path, default=Path("/home/bfe-lab-ono/kaggle/handoff-artifacts/deck-agent-asset-consolidation-taxonomy-v2"))
    expanded = sub.add_parser("build-expanded-population"); expanded.add_argument("--repo", type=Path, default=Path.cwd()); expanded.add_argument("--old-population", type=Path, required=True); expanded.add_argument("--output", type=Path, required=True); expanded.add_argument("--meta-root", type=Path, required=True); expanded.add_argument("--family-root", type=Path, required=True); expanded.add_argument("--recovery-root", type=Path, required=True)
    policy_entry = sub.add_parser("add-policy-learning-entry"); policy_entry.add_argument("--old-population", type=Path, required=True); policy_entry.add_argument("--output", type=Path, required=True); policy_entry.add_argument("--model-dir", type=Path, required=True); policy_entry.add_argument("--device", default="cpu"); policy_entry.add_argument("--opponent-id", default="policy-learning-actor-critic-a"); policy_entry.add_argument("--action-mode", choices=("argmax", "sample"), default="argmax")
    validate = sub.add_parser("validate-population"); validate.add_argument("--population", type=Path, required=True)
    schedule = sub.add_parser("build-schedule"); schedule.add_argument("--population", type=Path, required=True); schedule.add_argument("--output", type=Path, required=True); schedule.add_argument("--candidate", required=True); schedule.add_argument("--opponent", action="append", required=True); schedule.add_argument("--games", type=int, required=True); schedule.add_argument("--base-seed", type=int, default=71000); schedule.add_argument("--allow-unbalanced-diagnostic", action="store_true")
    for name in ("run-league", "resume-league"):
        run = sub.add_parser(name); run.add_argument("--run-dir", type=Path, required=True); run.add_argument("--population", type=Path, required=True); run.add_argument("--repo", type=Path, default=Path.cwd()); run.add_argument("--executor", choices=("cabt", "fixture"), default="cabt"); run.add_argument("--timeout", type=float, default=180.0); run.add_argument("--max-attempts", type=int, default=2); run.add_argument("--workers", type=int, default=2)
        run.add_argument("--start-method", choices=("spawn",), default="spawn")
        # A generation ends only after its slowest game.  Eight games per
        # worker made a long CABT game create an idle tail thirteen times in
        # an 800-game rollout.  Thirty-two retains bounded worker lifetime
        # while leaving only four such tails at the Gate 5 rollout size.
        run.add_argument("--worker-recycle-games", type=int, default=32)
        # 1 keeps the historical one-fresh-process-per-game isolation.  A
        # higher value lets one child play that many games before it is
        # discarded, which removes a ~1s interpreter/import cost per game;
        # the child is also discarded immediately after any unclean game.
        run.add_argument("--worker-reuse-games", type=int, default=1)
        run.add_argument("--stop-after", type=int, default=None, help="intentional partial run for resume verification")
        run.add_argument("--progress", action="store_true"); run.add_argument("--no-progress", action="store_true"); run.add_argument("--progress-interval-seconds", type=float, default=None)
    summary = sub.add_parser("summarize-league"); summary.add_argument("--run-dir", type=Path, required=True)
    verify = sub.add_parser("verify-run"); verify.add_argument("--run-dir", type=Path, required=True)
    export = sub.add_parser("export-dataset"); export.add_argument("--run-dir", type=Path, required=True); export.add_argument("--output", type=Path, required=True)
    export.add_argument("--progress", action="store_true"); export.add_argument("--no-progress", action="store_true"); export.add_argument("--progress-interval-seconds", type=float, default=None)
    export_v2 = sub.add_parser("export-dataset-v2-split")
    export_v2.add_argument("--run-dir", type=Path, required=True)
    export_v2.add_argument("--population", type=Path, required=True)
    export_v2.add_argument("--artifact-root", type=Path, required=True)
    export_v2.add_argument("--workers", type=int, default=None)
    export_v2.add_argument("--progress", action="store_true"); export_v2.add_argument("--no-progress", action="store_true"); export_v2.add_argument("--progress-interval-seconds", type=float, default=None)
    train = sub.add_parser("train-student-v1"); train.add_argument("--dataset", type=Path, required=True); train.add_argument("--model-dir", type=Path, required=True); train.add_argument("--epochs", type=int, default=120); train.add_argument("--learning-rate", type=float, default=.15)
    train.add_argument("--progress", action="store_true"); train.add_argument("--no-progress", action="store_true"); train.add_argument("--progress-interval-seconds", type=float, default=None)
    evaluate = sub.add_parser("evaluate-holdout"); evaluate.add_argument("--dataset", type=Path, required=True); evaluate.add_argument("--model", type=Path, required=True); evaluate.add_argument("--output", type=Path, required=True); evaluate.add_argument("--artifact-root", type=Path)
    evaluate.add_argument("--progress", action="store_true"); evaluate.add_argument("--no-progress", action="store_true"); evaluate.add_argument("--progress-interval-seconds", type=float, default=None)
    serve = sub.add_parser("serve"); serve.add_argument("--population", required=True); serve.add_argument("--repo", required=True); serve.add_argument("--executor", choices=("cabt", "fixture"), required=True)
    worker = sub.add_parser("worker"); worker.add_argument("--job", required=True); worker.add_argument("--population", required=True); worker.add_argument("--repo", required=True); worker.add_argument("--executor", choices=("cabt", "fixture"), required=True); worker.add_argument("--result-path", required=True); worker.add_argument("--trajectory-root"); worker.add_argument("--diagnostic-root")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser(); args = parser.parse_args(argv)
    try:
        if args.command == "worker": return _worker_main(args)
        if args.command == "serve": return _serve_main(args)
        if args.command == "build-population": result = build_population(repo=args.repo.resolve(), output=args.output, recovery_root=args.recovery_root, taxonomy_root=args.taxonomy_root)
        elif args.command == "build-expanded-population": result = build_expanded_population(repo=args.repo.resolve(), old_population_path=args.old_population, output=args.output, meta_root=args.meta_root, family_root=args.family_root, recovery_root=args.recovery_root)
        elif args.command == "add-policy-learning-entry": result = add_policy_learning_entry(old_population_path=args.old_population, output=args.output, model_dir=args.model_dir, device=args.device, opponent_id=args.opponent_id, action_mode=args.action_mode)
        elif args.command == "validate-population": result = validate_population(_read_json(args.population))
        elif args.command == "build-schedule":
            result = build_schedule(_read_json(args.population), candidate=args.candidate, opponents=args.opponent, games=args.games, base_seed=args.base_seed, allow_unbalanced=args.allow_unbalanced_diagnostic); _atomic_json(args.output, result)
        elif args.command in {"run-league", "resume-league"}:
            progress = False if args.no_progress else (True if args.progress else None)
            result = run_league(run_dir=args.run_dir, population_path=args.population, repo=args.repo.resolve(), executor=args.executor, timeout=args.timeout, max_attempts=args.max_attempts, workers=args.workers, progress=progress, progress_interval_seconds=args.progress_interval_seconds, start_method=args.start_method, worker_recycle_games=args.worker_recycle_games, stop_after=args.stop_after, worker_reuse_games=args.worker_reuse_games)
            print(_canonical(result)); return 0 if result["gate"] == "PASS" else 2
        elif args.command in {"summarize-league", "verify-run"}: result = summarize_run(args.run_dir)
        elif args.command == "export-dataset":
            progress = False if args.no_progress else (True if args.progress else None)
            result = export_dataset(run_dir=args.run_dir, output=args.output, progress=progress, progress_interval_seconds=args.progress_interval_seconds)
        elif args.command == "export-dataset-v2-split":
            progress = False if args.no_progress else (True if args.progress else None)
            result = export_dataset_v2(run_dir=args.run_dir, population_path=args.population, artifact_root=args.artifact_root, workers=args.workers, progress=progress, progress_interval_seconds=args.progress_interval_seconds)
            print(_canonical(result)); return 0 if result["gate"] == "PASS" else 2
        elif args.command == "train-student-v1":
            progress = False if args.no_progress else (True if args.progress else None)
            result = train_student_v1(dataset=args.dataset, model_dir=args.model_dir, epochs=args.epochs, learning_rate=args.learning_rate, progress=progress, progress_interval_seconds=args.progress_interval_seconds)
        elif args.command == "evaluate-holdout":
            progress = False if args.no_progress else (True if args.progress else None)
            result = evaluate_holdout(dataset=args.dataset, model_path=args.model, output=args.output, artifact_root=args.artifact_root, progress=progress, progress_interval_seconds=args.progress_interval_seconds)
            print(_canonical(result)); return 0 if result["gate"] == "PASS" else 2
        else: raise ContractError("unknown command")
        print(_canonical(result)); return 0
    except (ContractError, OSError, ValueError) as exc:
        print(_canonical({"error": type(exc).__name__, "message": str(exc)}), file=sys.stderr); return 2
