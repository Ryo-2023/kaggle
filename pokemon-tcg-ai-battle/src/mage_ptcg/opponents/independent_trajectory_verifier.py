"""Independent, process-isolated recomputation of O6 League trajectory digests.

O6-AUD-002 (HIGH) named the exact failure mode this guards against: calling
the same digest function twice is not independent recomputation. This
module therefore does NOT import ``mage_ptcg.opponents.public_trajectory_projection``
or ``mage_ptcg.opponents.public_trajectory_evidence`` (the runtime writer),
``mage_ptcg.opponents.trajectory`` (the runtime digest code),
``mage_ptcg.opponents.league_runtime`` or ``mage_ptcg.league.actual_runner``
(the League runner), or ``mage_ptcg.competition_intelligence.canonical``
(the runtime's canonical-JSON helper). It re-implements canonical JSON
serialization, the domain-prefixed SHA-256 digest scheme, the recursive
privacy scan, and JSON-Schema conformance checking directly, so that a bug
unique to one implementation cannot silently "confirm" itself.

Sharing the JSON Schema *artifact* (``public_trajectory_schema_v1.json``,
data, not logic) and the third-party ``jsonschema`` library (generic, not
O6-specific) with the runtime writer is fine -- what must never be shared is
the *payload construction* logic (the allow-list projection) or the digest
*function*.

It is meant to run in its own subprocess (see ``cli.py``'s
``verify-league-trajectories`` command and
``scripts/run_o6_team_league.py``'s finalize step), never imported into the
writer process -- ``tests/opponents/test_independent_trajectory_verifier.py``
enforces the import boundary via AST inspection and also exercises this
module through an actual subprocess invocation, not just an in-process call.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

import jsonschema

from .errors import OpponentError

_DOMAIN_PREFIX = "mage_ptcg:competition_intelligence"  # duplicated literal by design; see module docstring

_PUBLIC_TRAJECTORY_SCHEMA = json.loads((Path(__file__).parent / "public_trajectory_schema_v1.json").read_text(encoding="utf-8"))
_SCHEMA_VALIDATOR = jsonschema.Draft202012Validator(_PUBLIC_TRAJECTORY_SCHEMA)

# Independently-written denylist: intentionally NOT the same implementation as
# mage_ptcg.opponents.privacy_gate (which the runtime writer uses) -- a bug shared
# between writer and verifier privacy logic must not be able to "confirm" itself.
_INDEPENDENT_FORBIDDEN_KEY_PATTERN = re.compile(
    r"(hidden|secret|private|credential|password|token|api[_-]?key|rng[_-]?state|random[_-]?state|"
    r"engine[_-]?internal|hostname|username|\bpid\b|process[_-]?id|environ|env[_-]?var|debug[_-]?dump|"
    r"internal[_-]?state|memory[_-]?address|^logs$|^search_begin_input$|^hand$|^deck$)",
    re.IGNORECASE,
)
_INDEPENDENT_FORBIDDEN_VALUE_PATTERN = re.compile(
    r"(object at 0x[0-9a-fA-F]+|/home/[^\s\"']+|/Users/[^\s\"']+|/root/[^\s\"']+|/tmp/[^\s\"']+)"
)


class MalformedTrajectoryError(OpponentError):
    """Public trajectory evidence is missing, corrupt, or structurally invalid."""


def _independent_canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def independent_digest(value: Any, *, domain: str) -> str:
    prefix = f"{_DOMAIN_PREFIX}:{domain}:v1\0".encode("utf-8")
    return hashlib.sha256(prefix + _independent_canonical_json_bytes(value)).hexdigest()


def _independent_privacy_walk(value: Any, *, path: str) -> tuple[str, str] | None:
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                return (path, "non-string key")
            if _INDEPENDENT_FORBIDDEN_KEY_PATTERN.search(key):
                return (f"{path}.{key}", f"forbidden field name pattern: {key!r}")
            if key == "hand" and child is not None:
                return (f"{path}.{key}", "raw hand contents present")
            found = _independent_privacy_walk(child, path=f"{path}.{key}")
            if found:
                return found
        return None
    if isinstance(value, list):
        for index, child in enumerate(value):
            found = _independent_privacy_walk(child, path=f"{path}[{index}]")
            if found:
                return found
        return None
    if isinstance(value, str) and _INDEPENDENT_FORBIDDEN_VALUE_PATTERN.search(value):
        return (path, f"forbidden value pattern: {value[:80]!r}")
    return None


def independent_privacy_scan(event: Any) -> dict[str, Any]:
    """Verifier's own recursive privacy scan -- does not import ``privacy_gate``."""
    violation = _independent_privacy_walk(event, path="$")
    if violation is None:
        return {"status": "PASS", "violation": None}
    path, reason = violation
    return {"status": "REJECTED", "violation": {"path": path, "reason": reason}}


def validate_event_schema(event: Any) -> list[str]:
    """Independent JSON-Schema conformance check against the shared schema artifact."""
    return [f"{'.'.join(str(p) for p in error.path)}: {error.message}" for error in _SCHEMA_VALIDATOR.iter_errors(event)]


def parse_public_trajectory_jsonl_gz(path: Path) -> list[dict[str, Any]]:
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            lines = [line for line in handle if line.strip()]
    except OSError as exc:
        raise MalformedTrajectoryError(f"cannot read {path}: {exc}") from exc
    events = []
    for line_number, line in enumerate(lines):
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise MalformedTrajectoryError(f"{path}: malformed JSON on line {line_number}: {exc}") from exc
    return events


def _ordered_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not events:
        raise MalformedTrajectoryError("no events to reconstruct")
    ordered = sorted(events, key=lambda e: e.get("step_index", -1))
    if [e.get("step_index") for e in ordered] != list(range(len(ordered))):
        raise MalformedTrajectoryError("step_index sequence has gaps or duplicates")
    if ordered[0].get("event_type") != "INITIAL_PUBLIC_STATE":
        raise MalformedTrajectoryError("first event is not INITIAL_PUBLIC_STATE")
    if ordered[-1].get("event_type") != "TERMINAL_PUBLIC_STATE":
        raise MalformedTrajectoryError("last event is not TERMINAL_PUBLIC_STATE (missing terminal)")
    for event in ordered:
        if "public_payload" not in event:
            raise MalformedTrajectoryError(f"event at step {event.get('step_index')} has no public_payload")
    return ordered


def recompute_digests(events: list[dict[str, Any]]) -> dict[str, Any]:
    ordered = _ordered_events(events)
    schema_version = ordered[0].get("schema_version")
    action_trace = [
        {"step": e["step_index"], "seat_direction": e.get("seat_direction"), "action": e["public_payload"].get("action")}
        for e in ordered if e["public_payload"].get("action") is not None
    ]
    return {
        "initial_observation_digest": independent_digest({"schema_version": schema_version, "payload": ordered[0]["public_payload"]}, domain="o6-trajectory-initial"),
        "terminal_observation_digest": independent_digest({"schema_version": schema_version, "payload": ordered[-1]["public_payload"]}, domain="o6-trajectory-terminal"),
        "action_trace_digest": independent_digest({"schema_version": schema_version, "trace": action_trace}, domain="o6-trajectory-actions"),
        "complete_trajectory_digest": independent_digest({"schema_version": schema_version, "events": [
            {"event_type": e["event_type"], "step_index": e["step_index"], "seat_direction": e.get("seat_direction"), "public_payload": e["public_payload"]}
            for e in ordered
        ]}, domain="o6-trajectory-complete"),
    }


def verify_game(game_dir: Path) -> dict[str, Any]:
    game_dir = Path(game_dir)
    result: dict[str, Any] = {"game_dir_id": game_dir.name, "malformed": False, "privacy_valid": True, "schema_valid": True, "match": False}
    try:
        manifest = json.loads((game_dir / "trajectory_manifest.json").read_text(encoding="utf-8"))
        hashes = json.loads((game_dir / "hashes.json").read_text(encoding="utf-8"))
        jsonl_path = game_dir / "public_projection_trajectory.jsonl.gz"
        actual_hash = hashlib.sha256(jsonl_path.read_bytes()).hexdigest()
        if actual_hash != hashes.get("files", {}).get("public_projection_trajectory.jsonl.gz"):
            raise MalformedTrajectoryError("public_projection_trajectory.jsonl.gz hash mismatch (tamper or corruption)")
        events = parse_public_trajectory_jsonl_gz(jsonl_path)
        schema_errors: list[str] = []
        for event in events:
            schema_errors.extend(validate_event_schema(event))
            if independent_privacy_scan(event)["status"] != "PASS":
                result["privacy_valid"] = False
        if schema_errors:
            result["schema_valid"] = False
            result["schema_errors"] = schema_errors[:20]
        ordered = _ordered_events(events)
        independent = recompute_digests(ordered)
        runtime = manifest.get("runtime_digests", {})
        digest_match = all(independent[key] == runtime.get(key) for key in independent)
        result.update({
            "event_count": len(events), "runtime_digests": runtime, "independent_digests": independent,
            "match": digest_match and result["schema_valid"] and result["privacy_valid"],
        })
    except MalformedTrajectoryError as exc:
        result["malformed"] = True
        result["error"] = str(exc)
    except (OSError, json.JSONDecodeError, KeyError) as exc:
        result["malformed"] = True
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def verify_league_evidence(evidence_root: Path) -> dict[str, Any]:
    evidence_root = Path(evidence_root)
    games_dir = evidence_root / "games"
    game_dirs = sorted((p for p in games_dir.iterdir() if p.is_dir()), key=lambda p: p.name) if games_dir.exists() else []
    per_game = [verify_game(path) for path in game_dirs]
    verified = [g for g in per_game if not g["malformed"] and g["match"]]
    mismatches = [g for g in per_game if not g["malformed"] and not g["match"] and g["schema_valid"] and g["privacy_valid"]]
    malformed = [g for g in per_game if g["malformed"]]
    privacy_violations = [g for g in per_game if not g["malformed"] and not g["privacy_valid"]]
    schema_violations = [g for g in per_game if not g["malformed"] and not g["schema_valid"]]
    complete_digests = [g["independent_digests"]["complete_trajectory_digest"] for g in verified]
    initial_digests = [g["independent_digests"]["initial_observation_digest"] for g in verified]
    action_digests = [g["independent_digests"]["action_trace_digest"] for g in verified]
    terminal_digests = [g["independent_digests"]["terminal_observation_digest"] for g in verified]
    return {
        "schema_version": "o6-independent-verification-summary-v1",
        "game_count": len(game_dirs),
        "parsed_event_count": sum(g.get("event_count", 0) for g in per_game),
        "independently_verified_count": len(verified),
        "digest_mismatches": len(mismatches),
        "malformed_trajectories": len(malformed),
        "privacy_violations": len(privacy_violations),
        "schema_violations": len(schema_violations),
        "unique_initial_observations": len(set(initial_digests)),
        "unique_action_traces": len(set(action_digests)),
        "unique_terminal_observations": len(set(terminal_digests)),
        "unique_complete_trajectories": len(set(complete_digests)),
        "per_game": {g["game_dir_id"]: g for g in per_game},
    }


def _independent_run_root_sha256(run_dir: Path, *, exclude: set[str]) -> str:
    """Independently-written mirror of league_integrity_chain.compute_run_root_sha256 -- not imported."""
    entries: dict[str, str] = {}
    for path in Path(run_dir).rglob("*"):
        if not path.is_file():
            continue
        relpath = path.relative_to(run_dir).as_posix()
        if relpath in exclude:
            continue
        entries[relpath] = hashlib.sha256(path.read_bytes()).hexdigest()
    canonical = json.dumps(entries, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def verify_game_integrity(game_dir: Path) -> dict[str, Any]:
    game_dir = Path(game_dir)
    result: dict[str, Any] = {"game_dir_id": game_dir.name, "hashes_valid": False, "mismatched_files": []}
    try:
        hashes = json.loads((game_dir / "hashes.json").read_text(encoding="utf-8"))
        mismatches = []
        for relpath, expected in hashes.get("files", {}).items():
            target = game_dir / relpath
            actual = hashlib.sha256(target.read_bytes()).hexdigest() if target.is_file() else None
            if actual != expected:
                mismatches.append(relpath)
        result["hashes_valid"] = not mismatches
        result["mismatched_files"] = mismatches
    except (OSError, json.JSONDecodeError) as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def verify_run_chain(run_dir: Path, *, trusted_root_registry: Path | None = None, expected_root_sha256: str | None = None) -> dict[str, Any]:
    """Full integrity-chain verification: trajectory content + hashes + manifest/summary/root + external anchor.

    Requires exactly one form of external anchor
    (``trusted_root_registry`` or ``expected_root_sha256``); without one this
    returns ``UNANCHORED_EVIDENCE`` rather than a false sense of PASS/FAIL --
    a run directory can always be internally self-consistent while being a
    wholesale substitution, so an anchor outside the run directory is
    mandatory for this mode (see O6-AUD-002-INTEGRITY-001).
    """
    run_dir = Path(run_dir)
    if trusted_root_registry is None and expected_root_sha256 is None:
        return {"status": "UNANCHORED_EVIDENCE", "reason": "neither --trusted-root-registry nor --expected-root-sha256 was provided"}

    anchor_root_sha256 = expected_root_sha256
    anchor_source = "expected_root_sha256"
    if trusted_root_registry is not None:
        try:
            registry = json.loads(Path(trusted_root_registry).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return {"status": "UNANCHORED_EVIDENCE", "reason": f"cannot read trusted root registry: {exc}"}
        run_manifest_path = run_dir / "run_manifest.json"
        run_id = None
        if run_manifest_path.exists():
            try:
                run_id = json.loads(run_manifest_path.read_text(encoding="utf-8")).get("run_id")
            except json.JSONDecodeError:
                run_id = None
        entry = next((e for e in registry.get("trusted_roots", []) if e.get("run_id") == run_id), None)
        if entry is None or entry.get("status") != "TRUSTED":
            return {"status": "UNANCHORED_EVIDENCE", "reason": f"no TRUSTED entry for run_id={run_id!r} in {trusted_root_registry}"}
        anchor_root_sha256 = entry["run_root_sha256"]
        anchor_source = "trusted_root_registry"

    actual_root_sha256 = _independent_run_root_sha256(run_dir, exclude={"run_root.sha256"})
    root_hash_match = actual_root_sha256 == anchor_root_sha256

    # The on-disk run_root.sha256 file is excluded from its own hash (it cannot reference its
    # own bytes) so tampering *only* that file's content would otherwise go undetected -- the
    # tree hash and the external anchor would both still agree with each other. Explicitly
    # require the in-tree file to match the independently recomputed value too.
    run_root_file_path = run_dir / "run_root.sha256"
    run_root_file_valid = False
    if run_root_file_path.is_file():
        try:
            run_root_file_valid = run_root_file_path.read_text(encoding="utf-8").strip() == actual_root_sha256
        except OSError:
            run_root_file_valid = False

    base = verify_league_evidence(run_dir)
    per_game_integrity = (
        {p.name: verify_game_integrity(p) for p in sorted((run_dir / "games").iterdir()) if p.is_dir()}
        if (run_dir / "games").exists() else {}
    )
    hashes_all_valid = bool(per_game_integrity) and all(g["hashes_valid"] for g in per_game_integrity.values())
    status = "PASS" if (
        root_hash_match and run_root_file_valid and hashes_all_valid and base["digest_mismatches"] == 0
        and base["malformed_trajectories"] == 0 and base["privacy_violations"] == 0 and base["schema_violations"] == 0
    ) else "FAIL"
    return {
        "status": status, "anchor_source": anchor_source, "expected_root_sha256": anchor_root_sha256,
        "actual_root_sha256": actual_root_sha256, "root_hash_match": root_hash_match,
        "run_root_file_valid": run_root_file_valid,
        "per_game_integrity": per_game_integrity, "trajectory_verification": base,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m mage_ptcg.opponents.independent_trajectory_verifier")
    parser.add_argument("--evidence", required=True)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--mode", choices=["trajectory", "full"], default="trajectory")
    parser.add_argument("--trusted-root-registry", type=Path, default=None)
    parser.add_argument("--expected-root-sha256", default=None)
    args = parser.parse_args(argv)
    if args.mode == "trajectory":
        summary = verify_league_evidence(Path(args.evidence))
        ok = (summary["digest_mismatches"] == 0 and summary["malformed_trajectories"] == 0
              and summary["privacy_violations"] == 0 and summary["schema_violations"] == 0)
    else:
        summary = verify_run_chain(Path(args.evidence), trusted_root_registry=args.trusted_root_registry, expected_root_sha256=args.expected_root_sha256)
        ok = summary["status"] == "PASS"
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    else:
        for key, value in summary.items():
            if key not in ("per_game", "per_game_integrity", "trajectory_verification"):
                print(f"{key}: {value}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
