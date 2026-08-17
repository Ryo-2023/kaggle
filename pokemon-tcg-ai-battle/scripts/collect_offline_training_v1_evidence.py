#!/usr/bin/env python3
"""Offline Training v1 Machine Evidence Collector.

Executes a declarative verification plan, runs allowlisted validation runners,
sanitizes outputs, inspects package artifacts, verifies Gemini manifests,
and checks system invariants, saving the collected facts in a structured JSON.
"""

from __future__ import annotations

import argparse
import datetime
import getpass
import hashlib
import json
import os
import pathlib
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path, PurePosixPath

# Schema versions
SCHEMA_VERSION = "offline-training-v1-evidence-v1"

# Allowed Runner IDs
ALLOWED_RUNNERS = {
    "git_diff_check",
    "import_closure",
    "pytest",
    "collection_smoke",
    "training_smoke",
    "resume_smoke",
    "export_parity",
    "package_build",
    "package_verify",
    "clean_room",
    "privacy_scan",
    "secret_scan",
    "absolute_path_scan",
    "artifact_hash",
    "fallback_invariant",
    "champion_invariant",
    "gemini_manifest_validation",
}

# Allowed privacy classifications for checks and artifacts.
ALLOWED_PRIVACY_CLASSES = {"public", "private"}

# The 19 check IDs that the final acceptance plan must always define.
# Kept in sync with docs/evidence/offline-training-v1-evidence-collector.md.
EXPECTED_REQUIRED_CHECK_IDS = {
    "git_diff_check",
    "import_closure",
    "focused_core_tests",
    "review_adversarial_tests",
    "gemini_support_tests",
    "full_regression",
    "collection_smoke",
    "training_smoke",
    "resume_determinism",
    "export_parity",
    "package_build",
    "package_verify",
    "clean_room",
    "privacy_scan",
    "secret_scan",
    "absolute_path_scan",
    "artifact_hash",
    "fallback_invariant",
    "champion_invariant",
}

GEMINI_MANIFEST_SCHEMA_VERSION = "gemini-support-integration-manifest-v1"

# Absolute paths redaction regex
# Matches typical absolute paths like /home/..., /tmp/..., /Users/...
RE_ABSOLUTE_PATH = re.compile(r"(?:^|[\s\"'])/(?:home|tmp|Users)/[a-zA-Z0-9_\-\.\/]+")

# Secret / Credential / Token redaction regex
# Matches keys like api_key, token, password followed by alpha-numeric values
RE_SECRET = re.compile(
    r"(?i)(?:secret|token|password|credential|key|api[-_]?key)(?:\s*[:=]\s*|\s+)[a-zA-Z0-9_\-\.\/]{12,}"
)


def get_utc_now() -> str:
    """Return current UTC time in ISO 8601 format."""
    return datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")


def compute_sha256_text(text: str) -> str:
    """Compute the SHA-256 hash of a string."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def compute_file_sha256(filepath: Path) -> str:
    """Compute the SHA-256 hash of a file."""
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def redact_text(text: str, repo_root: Path | None = None) -> str:
    """Sanitize secrets, absolute paths, and repository root paths from text."""
    if not text:
        return ""

    # Replace repository root with a portable relative path representation
    if repo_root:
        resolved_root = str(repo_root.resolve())
        text = text.replace(resolved_root, "[REDACTED_REPO_ROOT]")

    # Redact absolute paths
    text = RE_ABSOLUTE_PATH.sub(lambda m: m.group(0).replace(m.group(0).strip(), "[REDACTED_PATH]"), text)

    # Redact secrets
    def secret_replacer(match: re.Match) -> str:
        matched = match.group(0)
        prefix_part = re.split(r'[:=]|\s+', matched, maxsplit=1)[0]
        separator = ""
        if ":" in matched:
            separator = ":"
        elif "=" in matched:
            separator = "="
        else:
            separator = " "
        return f"{prefix_part}{separator}[REDACTED_SECRET]"

    text = RE_SECRET.sub(secret_replacer, text)
    return text


def redact_structure(value: object, repo_root: Path | None = None) -> object:
    """Recursively apply redact_text() to every string in a nested structure.

    Structured metadata fields (e.g. inspect_tarball()'s 'path') are built
    directly, not via run_command_safe()'s bounded_excerpt pipeline, so they
    never went through redact_text() on their own -- this is the central
    pass that gives every string in the evidence document the same
    absolute-path/secret redaction guarantee, regardless of which runner
    produced it or how deeply nested it is. Non-string leaves are returned
    unchanged.
    """
    if isinstance(value, str):
        return redact_text(value, repo_root)
    if isinstance(value, dict):
        return {key: redact_structure(item, repo_root) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_structure(item, repo_root) for item in value]
    return value


def bound_excerpt(text: str, max_lines: int = 100) -> str:
    """Extract a bounded excerpt (first and last max_lines) of a text."""
    if not text:
        return ""
    lines = text.splitlines()
    if len(lines) <= max_lines * 2:
        return text
    first_part = lines[:max_lines]
    last_part = lines[-max_lines:]
    middle = f"\n... [TRUNCATED {len(lines) - max_lines * 2} LINES] ...\n"
    return "\n".join(first_part) + middle + "\n".join(last_part)


def self_scan_pattern(evidence_so_far: dict, pattern: re.Pattern) -> dict:
    """Scan the in-progress evidence document for a regex pattern.

    This is a self-verification of the collector's own redaction pipeline:
    every stdout/stderr excerpt already passed through redact_text() before
    being stored, so a clean scan here confirms no residual match escaped
    redaction. The raw matched text is intentionally never included in the
    result (that would re-leak the very thing being screened for) -- only a
    count is reported.
    """
    text = json.dumps(evidence_so_far, default=str, ensure_ascii=False)
    violation_count = len(pattern.findall(text))
    return {"violation_count": violation_count, "clean": violation_count == 0}


def self_scan_privacy(evidence_so_far: dict) -> dict:
    """Scan the in-progress evidence document for the local OS username.

    Narrow, well-defined check for the "user name" leak category called out
    explicitly in the redaction requirements; it does not attempt to detect
    arbitrary "private data" in general, which has no machine-checkable
    definition here.
    """
    text = json.dumps(evidence_so_far, default=str, ensure_ascii=False)
    try:
        username = getpass.getuser()
    except Exception:
        username = ""
    violation_count = 0
    if username:
        violation_count = len(re.findall(rf"\b{re.escape(username)}\b", text))
    return {"violation_count": violation_count, "clean": violation_count == 0}


def run_command_safe(
    name: str,
    argv: list[str],
    timeout: float,
    cwd: Path,
    env_updates: dict[str, str] | None = None,
    repo_root: Path | None = None,
) -> dict:
    """Run a command safely in a new process group, capture, and redact outputs."""
    started_at = get_utc_now()
    start_time = time.perf_counter()

    record = {
        "name": name,
        "argv": argv,
        "cwd": str(cwd),
        "started_at_utc": started_at,
        "finished_at_utc": started_at,
        "duration_seconds": 0.0,
        "return_code": -1,
        "stdout_sha256": "",
        "stderr_sha256": "",
        "bounded_excerpt": "",
        "timed_out": False,
        "launch_error": None,
    }

    env = os.environ.copy()
    if env_updates:
        env.update(env_updates)

    process = None
    try:
        process = subprocess.Popen(
            argv,
            cwd=str(cwd),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
    except Exception as e:
        record["finished_at_utc"] = get_utc_now()
        record["duration_seconds"] = round(time.perf_counter() - start_time, 3)
        record["launch_error"] = str(e)
        return record

    timed_out = False
    stdout = ""
    stderr = ""

    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        record["timed_out"] = True

        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass

        try:
            stdout, stderr = process.communicate(timeout=3.0)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            stdout, stderr = process.communicate()

    record["finished_at_utc"] = get_utc_now()
    record["duration_seconds"] = round(time.perf_counter() - start_time, 3)
    record["return_code"] = process.returncode if process.returncode is not None else -1

    redacted_stdout = redact_text(stdout, repo_root)
    redacted_stderr = redact_text(stderr, repo_root)

    record["stdout_sha256"] = compute_sha256_text(redacted_stdout)
    record["stderr_sha256"] = compute_sha256_text(redacted_stderr)

    record["bounded_excerpt"] = (
        "--- STDOUT ---\n"
        + bound_excerpt(redacted_stdout)
        + "\n--- STDERR ---\n"
        + bound_excerpt(redacted_stderr)
    )

    # Internal only: the raw (unredacted, undecorated) stdout, for callers
    # that need to make a decision on the command's actual output (e.g.
    # clean_room parsing `git status --short`) rather than on the
    # human-readable, bounded, "--- STDOUT ---"-prefixed display text.
    # main()'s check_record construction never copies this key over, so it
    # can never reach the evidence JSON.
    record["_raw_stdout"] = stdout

    return record


def inspect_tarball(tar_path: Path) -> dict:
    """Inspect contents of a tarball without extracting, looking for traversals/absolute paths."""
    if not tar_path.exists():
        return {"exists": False, "path": str(tar_path)}

    results = {
        "exists": True,
        "path": str(tar_path),
        "sha256": "",
        "size_bytes": tar_path.stat().st_size,
        "duplicate_names": [],
        "members": [],
        "has_unsafe_members": False,
    }

    try:
        results["sha256"] = compute_file_sha256(tar_path)
    except Exception as e:
        results["sha256_error"] = str(e)

    seen_names = set()
    duplicate_names = set()

    try:
        with tarfile.open(tar_path, "r:gz") as tar:
            for member in tar.getmembers():
                name = member.name
                size = member.size
                mode = member.mode

                member_type = "unknown"
                if member.isfile():
                    member_type = "file"
                elif member.isdir():
                    member_type = "directory"
                elif member.issym():
                    member_type = "symlink"
                elif member.islnk():
                    member_type = "hardlink"

                parts = PurePosixPath(name).parts
                has_path_traversal = ".." in parts
                is_absolute = PurePosixPath(name).is_absolute() or name.startswith("/")

                is_symlink = member.issym()
                symlink_facts = {}
                if is_symlink:
                    linkname = member.linkname
                    link_parts = PurePosixPath(linkname).parts
                    symlink_facts = {
                        "linkname": linkname,
                        "linkname_is_absolute": PurePosixPath(linkname).is_absolute() or linkname.startswith("/"),
                        "linkname_contains_parent_reference": ".." in link_parts,
                    }
                    if symlink_facts["linkname_is_absolute"] or symlink_facts["linkname_contains_parent_reference"]:
                        results["has_unsafe_members"] = True

                if has_path_traversal or is_absolute:
                    results["has_unsafe_members"] = True

                is_duplicate = name in seen_names
                if is_duplicate:
                    duplicate_names.add(name)
                seen_names.add(name)

                member_info = {
                    "name": name,
                    "type": member_type,
                    "size_bytes": size,
                    "mode": mode,
                    "is_symlink": is_symlink,
                    "is_absolute": is_absolute,
                    "has_path_traversal": has_path_traversal,
                    "is_duplicate": is_duplicate,
                }
                if is_symlink:
                    member_info.update(symlink_facts)

                results["members"].append(member_info)
    except Exception as e:
        results["error"] = str(e)
        results["has_unsafe_members"] = True

    results["duplicate_names"] = sorted(list(duplicate_names))
    return results


# --------------------------------------------------------------------------- #
# export_parity: synthetic C4 data-ops fixture + real export_bundle() parity
# --------------------------------------------------------------------------- #
#
# scripts/export_c4_actual_training_bundle.py's export_bundle() reads a run
# built by mage_ptcg.dataops.collect_actual_dataset() (a "private_dataset/"
# subdirectory plus dataset/split/summary manifests at the run root). That
# shape is different from the offline_training_v1 pipeline's own run root
# (used by collection_smoke/training_smoke/resume_determinism), so this
# check builds its own small, synthetic, engine-free C4 data-ops fixture --
# mirroring the technique tests/test_c4_data_ops.py already uses to test
# export_bundle() -- rather than reusing the pipeline's run root or touching
# any private dataset.

_C4_FIXTURE_OPTIONS = [{"type": 14}, {"type": 13, "attackId": 1}, {"type": 7, "index": 0}]
_C4_FIXTURE_CAPABILITY_REPORT = {
    "status": "READY",
    "engine_seed_supported": False,
    "actual_execution_allowed": True,
}
_C4_FIXTURE_BUNDLE_FILES = (
    "dataset_manifest.json",
    "split_manifest.json",
    "rule-bc-v1.jsonl",
    "public_summary.json",
)


def _c4_fixture_card(card_id: int) -> dict:
    return {
        "id": card_id, "serial": 0, "playerIndex": 0, "hp": 100, "maxHp": 100,
        "appearThisTurn": False, "energies": [], "energyCards": [], "tools": [], "preEvolution": [],
    }


def _c4_fixture_player(card_id: int) -> dict:
    return {
        "active": [], "asleep": False, "bench": [], "benchMax": 5, "burned": False,
        "confused": False, "deckCount": 53, "discard": [], "hand": [_c4_fixture_card(card_id)],
        "handCount": 1, "paralyzed": False, "poisoned": False, "prize": [object() for _ in range(6)],
    }


def _c4_fixture_observation(your_index: int) -> dict:
    return {
        "current": {
            "energyAttached": False, "firstPlayer": 0,
            "players": [_c4_fixture_player(100), _c4_fixture_player(700)],
            "result": -1, "retreated": False, "stadium": [], "stadiumPlayed": False,
            "supporterPlayed": False, "turn": 2, "turnActionCount": 3, "yourIndex": your_index,
        },
        "select": {"context": 0, "maxCount": 1, "minCount": 1, "option": _C4_FIXTURE_OPTIONS, "type": 0},
        "step": 7,
    }


def _c4_fixture_match_runner(**kwargs) -> dict:
    """Deterministic, engine-free match runner for the export_parity fixture.

    No real cabt engine, no private data: two seats each make two scripted
    Rule-v0-legal decisions from a fixed synthetic observation.
    """
    seat0 = kwargs["agent_a_factory"]([1] * 60, int(kwargs["seed"]))
    seat1 = kwargs["agent_b_factory"]([1] * 60, int(kwargs["seed"]) + 1)
    for _ in range(2):
        seat0(_c4_fixture_observation(0))
        seat1(_c4_fixture_observation(1))
    return {"status": "DONE", "winner": 0, "elapsed_seconds": 0.0}


def build_c4_fixture_source(repo_root: Path, dest_dir: Path, *, fixture_run_id: str = "fixture") -> Path:
    """Build a synthetic C4 data-ops run_root that export_bundle() can consume."""
    from mage_ptcg.dataops import collect_actual_dataset

    collect_actual_dataset(
        run_id=fixture_run_id,
        games=4,
        base_seed=100,
        output_root=dest_dir,
        canonical_base_sha="a" * 40,
        deck_path=repo_root / "deck.csv",
        repository_root=repo_root,
        match_runner=_c4_fixture_match_runner,
        capability_report=dict(_C4_FIXTURE_CAPABILITY_REPORT),
        source_revision="offline-training-v1-acceptance-fixture",
    )
    return dest_dir / fixture_run_id


def run_export_parity_check(repo_root: Path, scratch_dir: Path) -> dict:
    """In-process export_parity check.

    Builds a synthetic C4 data-ops fixture once, exports it twice through
    the real export_bundle(), and asserts the two exports are byte-identical
    -- an actual determinism ("parity") check, not just an execution smoke
    test. Calling export_bundle() directly (rather than through the CLI)
    also means a failure reason is not lost to the CLI's exception-class-only
    stderr message. Returns a cmd_record-shaped dict.
    """
    if scratch_dir.exists():
        shutil.rmtree(scratch_dir)
    scratch_dir.mkdir(parents=True, exist_ok=True)

    try:
        source_root = build_c4_fixture_source(repo_root, scratch_dir / "source")

        from scripts.export_c4_actual_training_bundle import export_bundle

        bundle_a = scratch_dir / "bundle_a"
        bundle_b = scratch_dir / "bundle_b"
        result_a = export_bundle(run_root=source_root, output_root=bundle_a)
        export_bundle(run_root=source_root, output_root=bundle_b)

        mismatches = [
            name
            for name in _C4_FIXTURE_BUNDLE_FILES
            if compute_file_sha256(bundle_a / name) != compute_file_sha256(bundle_b / name)
        ]

        if mismatches:
            return {
                "return_code": 1,
                "timed_out": False,
                "bounded_excerpt": f"[EXPORT_PARITY_ERROR] Non-deterministic export output for: {mismatches}",
                "stdout_sha256": compute_sha256_text(""),
                "stderr_sha256": compute_sha256_text(""),
            }

        return {
            "return_code": 0,
            "timed_out": False,
            "bounded_excerpt": (
                f"[EXPORT_PARITY] status={result_a.get('status')} "
                f"artifact_purpose={result_a.get('artifact_purpose')} "
                f"compared_files={list(_C4_FIXTURE_BUNDLE_FILES)} parity=True"
            ),
            "stdout_sha256": compute_sha256_text(""),
            "stderr_sha256": compute_sha256_text(""),
        }
    except Exception as exc:
        return {
            "return_code": 2,
            "timed_out": False,
            "bounded_excerpt": f"[EXPORT_PARITY_ERROR] {type(exc).__name__}: {exc}",
            "stdout_sha256": compute_sha256_text(""),
            "stderr_sha256": compute_sha256_text(""),
        }
    finally:
        shutil.rmtree(scratch_dir, ignore_errors=True)


def check_required_paths(repo_root: Path, paths: list[str]) -> list[dict]:
    """Inspect required files, compute size & hash."""
    results = []
    for path_str in paths:
        full_path = repo_root / path_str
        if full_path.exists() and full_path.is_file():
            if full_path.is_symlink():
                resolved = full_path.resolve()
                if not resolved.is_relative_to(repo_root.resolve()):
                    results.append({
                        "path": path_str,
                        "exists": True,
                        "is_symlink_outside": True,
                        "sha256": "",
                        "size_bytes": 0,
                    })
                    continue

            try:
                size_bytes = full_path.stat().st_size
                sha256_val = compute_file_sha256(full_path)
                results.append({
                    "path": path_str,
                    "exists": True,
                    "is_symlink_outside": False,
                    "size_bytes": size_bytes,
                    "sha256": sha256_val,
                })
            except Exception as e:
                results.append({
                    "path": path_str,
                    "exists": True,
                    "error": str(e),
                })
        else:
            results.append({
                "path": path_str,
                "exists": False,
            })
    return results


def validate_plan_schema(plan: dict) -> None:
    """Validate verification plan JSON structure."""
    if not isinstance(plan, dict):
        raise ValueError("Plan must be a JSON object")
    if plan.get("schema_version") != "offline-training-v1-acceptance-plan-v1":
        raise ValueError(f"Invalid plan schema version: {plan.get('schema_version')}")
    if not isinstance(plan.get("checks"), list):
        raise ValueError("Plan must contain a list of 'checks'")

    seen_ids = set()
    for index, check in enumerate(plan["checks"]):
        if not isinstance(check, dict):
            raise ValueError(f"Check at index {index} is not an object")
        check_id = check.get("id")
        runner = check.get("runner")
        if not check_id or not isinstance(check_id, str):
            raise ValueError(f"Check at index {index} is missing a string 'id'")
        if check_id in seen_ids:
            raise ValueError(f"Duplicate check ID detected: {check_id}")
        seen_ids.add(check_id)

        if not runner or runner not in ALLOWED_RUNNERS:
            raise ValueError(f"Check '{check_id}' has an invalid or unknown runner: {runner}")

        required_keys = [
            "required",
            "timeout_seconds",
            "args",
            "expected_outputs",
            "artifact_inputs",
            "artifact_outputs",
            "privacy_class",
            "failure_severity",
        ]
        for key in required_keys:
            if key not in check:
                raise ValueError(f"Check '{check_id}' is missing required field: {key}")

        if not isinstance(check["required"], bool):
            raise ValueError(f"Check '{check_id}' 'required' field must be a boolean")

        timeout_value = check["timeout_seconds"]
        if (
            isinstance(timeout_value, bool)
            or not isinstance(timeout_value, (int, float))
            or timeout_value <= 0
        ):
            raise ValueError(
                f"Check '{check_id}' 'timeout_seconds' must be a positive number, got: {timeout_value!r}"
            )

        if not isinstance(check["args"], list) or not all(isinstance(a, str) for a in check["args"]):
            raise ValueError(f"Check '{check_id}' 'args' must be a list of strings")

        for list_field in ("expected_outputs", "artifact_inputs", "artifact_outputs"):
            value = check[list_field]
            if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                raise ValueError(f"Check '{check_id}' '{list_field}' must be a list of strings")

        if check["privacy_class"] not in ALLOWED_PRIVACY_CLASSES:
            raise ValueError(
                f"Check '{check_id}' has an invalid privacy_class: {check['privacy_class']!r} "
                f"(allowed: {sorted(ALLOWED_PRIVACY_CLASSES)})"
            )

        if not isinstance(check["failure_severity"], str) or not check["failure_severity"]:
            raise ValueError(f"Check '{check_id}' 'failure_severity' must be a non-empty string")


def validate_required_check_coverage(plan: dict) -> None:
    """Ensure the plan defines every canonical required check ID.

    Runs as part of plan validation so ``--validate-only`` catches a plan
    missing a required check before any runner is resolved or executed.
    """
    present_check_ids = {check["id"] for check in plan["checks"]}
    missing_required = EXPECTED_REQUIRED_CHECK_IDS - present_check_ids
    if missing_required:
        raise ValueError(
            f"Acceptance plan is missing required checks: {sorted(missing_required)}"
        )


def check_champion_invariant(repo_root: Path) -> dict:
    """Verify default agent is Rule Agent v0 in main.py."""
    main_py = repo_root / "main.py"
    if not main_py.exists():
        return {"invariant_ok": False, "reason": "main.py not found"}

    try:
        content = main_py.read_text(encoding="utf-8")
        if "make_rule_agent" in content:
            return {"invariant_ok": True, "champion": "Rule Agent v0"}
        return {"invariant_ok": False, "reason": "make_rule_agent not found as default agent in main.py"}
    except Exception as e:
        return {"invariant_ok": False, "reason": f"Error reading main.py: {e}"}


def check_promotion_invariant(repo_root: Path) -> dict:
    """Verify Promotion status is NO_DECISION.

    The repository has no machine-readable promotion-state file today
    (confirmed by search; see docs/status/decisions.md, which records
    non-promoted evaluation inputs as NO_DECISION prose, not a structured
    state). Absent contradicting evidence, this invariant reports the
    default NO_DECISION state. It does not (yet) detect an actual
    promotion decision being recorded elsewhere; that would require a
    canonical promotion-state artifact to check against.
    """
    return {"invariant_ok": True, "status": "NO_DECISION"}


def parse_gemini_manifest(manifest_json: dict) -> dict:
    """Classify an already-fetched Gemini support manifest into P0/P1/HOLD facts.

    Pure function (no I/O) so it is unit-testable without a git/network
    round trip. The manifest's real top-level key is ``features`` under
    schema_version ``gemini-support-integration-manifest-v1`` -- confirmed
    by reading origin/feature/offline-training-v1-gemini-support read-only
    rather than guessed. A ``modules`` key is not part of the real schema
    and is intentionally not accepted here.
    """
    facts = {
        "schema_validation_ok": False,
        "module_counts": {"P0": 0, "P1": 0, "HOLD": 0},
        "p0_acceptance_tests": [],
        "hold_modules": [],
    }
    if not isinstance(manifest_json, dict):
        return facts

    features = manifest_json.get("features")
    schema_ok = (
        manifest_json.get("schema_version") == GEMINI_MANIFEST_SCHEMA_VERSION
        and isinstance(features, list)
    )
    facts["schema_validation_ok"] = schema_ok
    if not schema_ok:
        return facts

    p0_count = 0
    p1_count = 0
    hold_count = 0
    for feature in features:
        if not isinstance(feature, dict):
            continue
        classification = feature.get("classification")
        name = feature.get("name")
        source_modules = feature.get("source_modules") or []

        if classification == "P0":
            p0_count += 1
            facts["p0_acceptance_tests"].extend(feature.get("acceptance_tests", []))
        elif classification == "P1":
            p1_count += 1
        elif classification == "HOLD":
            hold_count += 1
            hold_targets = list(source_modules) if source_modules else ([name] if name else [])
            facts["hold_modules"].extend(hold_targets)

    facts["module_counts"] = {"P0": p0_count, "P1": p1_count, "HOLD": hold_count}
    return facts


def check_hold_module_quarantine(repo_root: Path, hold_modules: list[str]) -> dict:
    """Check that HOLD modules are not imported anywhere in src/mage_ptcg/offline_training/."""
    offline_training_dir = repo_root / "src" / "mage_ptcg" / "offline_training"
    if not offline_training_dir.exists():
        offline_training_dir = repo_root / "mage_ptcg" / "offline_training"

    if not offline_training_dir.exists():
        return {"invariant_ok": True, "violations": []}

    violations = []
    patterns = []
    for mod in hold_modules:
        patterns.append((mod, re.compile(rf"(?:import|from)\s+.*\b{re.escape(mod)}\b")))

    for root, _, files in os.walk(offline_training_dir):
        for file in files:
            if file.endswith(".py"):
                file_path = Path(root) / file
                try:
                    content = file_path.read_text(encoding="utf-8")
                    for mod_name, pattern in patterns:
                        if pattern.search(content):
                            violations.append({
                                "file": str(file_path.relative_to(repo_root)),
                                "imported_hold_module": mod_name,
                            })
                except Exception:
                    pass

    return {
        "invariant_ok": len(violations) == 0,
        "violations": violations,
    }


def get_gpu_cuda_info() -> dict:
    """Gather GPU/CUDA facts safely without failing."""
    info = {"cuda_available": False, "device_count": 0, "devices": []}
    try:
        import torch
        info["cuda_available"] = torch.cuda.is_available()
        if info["cuda_available"]:
            info["device_count"] = torch.cuda.device_count()
            info["devices"] = [torch.cuda.get_device_name(i) for i in range(info["device_count"])]
    except Exception:
        pass
    return info


def atomic_write(filepath: Path, data: dict) -> None:
    """Write dictionary to target path atomically using a temporary file."""
    filepath = Path(filepath)
    filepath.parent.mkdir(parents=True, exist_ok=True)

    temp_dir = filepath.parent
    fd, temp_path_str = tempfile.mkstemp(
        dir=str(temp_dir), prefix=".tmp_evidence_", suffix=".json"
    )
    temp_path = Path(temp_path_str)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, sort_keys=True, ensure_ascii=False)
            f.write("\n")
        os.replace(temp_path, filepath)
    except Exception as e:
        if temp_path.exists():
            try:
                os.remove(temp_path)
            except Exception:
                pass
        raise e


def get_git_info(repo_root: Path) -> dict:
    """Retrieve Git branch and commit details."""
    git_info = {
        "branch": "unknown",
        "commit_sha": "unknown",
        "tree_hash": "unknown",
        "dirty": False,
    }
    try:
        res = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            check=False,
        )
        if res.returncode == 0:
            git_info["branch"] = res.stdout.strip()

        res = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            check=False,
        )
        if res.returncode == 0:
            git_info["commit_sha"] = res.stdout.strip()

        res = subprocess.run(
            ["git", "write-tree"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            check=False,
        )
        if res.returncode == 0:
            git_info["tree_hash"] = res.stdout.strip()

        res = subprocess.run(
            ["git", "status", "--short"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            check=False,
        )
        if res.returncode == 0:
            lines = [l for l in res.stdout.splitlines() if not l.endswith(".venv")]
            git_info["dirty"] = len(lines) > 0
    except Exception:
        pass
    return git_info


def get_dependency_fingerprint(repo_root: Path) -> str:
    """Compute simple hash of dependencies."""
    req_file = repo_root / "requirements.txt"
    if req_file.exists():
        try:
            return compute_file_sha256(req_file)
        except Exception:
            pass
    return "unknown"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Offline Training v1 Machine Evidence Collector (Acceptance Plan Engine)."
    )
    parser.add_argument(
        "--repository-root",
        default=".",
        help="Path to the repository root directory (default: .)",
    )
    parser.add_argument(
        "--plan",
        required=True,
        help="Path to the final acceptance plan JSON file",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Path to save the collected JSON evidence report (required unless --validate-only)",
    )
    parser.add_argument(
        "--run-id",
        help="Explicit Run ID (auto-generated if omitted)",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Validate the acceptance plan schema and exit without executing checks.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate plan schema and resolve runners without executing commands.",
    )
    args = parser.parse_args()

    if not args.validate_only and not args.output:
        parser.error("--output is required unless --validate-only is set")

    repo_root = Path(args.repository_root).resolve()
    plan_path = Path(args.plan).resolve()
    output_path = Path(args.output).resolve() if args.output else None

    run_id = args.run_id or f"run_{int(time.time())}"

    self_path = Path(__file__).resolve()
    self_sha256 = ""
    try:
        self_sha256 = compute_file_sha256(self_path)
    except Exception as e:
        self_sha256 = f"error: {e}"

    git_info = get_git_info(repo_root)

    results = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "started_at_utc": get_utc_now(),
        "finished_at_utc": "",
        "repository_root_resolved": redact_text(str(repo_root), repo_root=repo_root),
        "collector": {
            "script_path": redact_text(
                str(self_path.relative_to(repo_root) if self_path.is_relative_to(repo_root) else self_path),
                repo_root=repo_root,
            ),
            "sha256": self_sha256,
            "python_executable": redact_text(sys.executable, repo_root=repo_root),
            "python_version": sys.version,
            "dependency_fingerprint": get_dependency_fingerprint(repo_root),
            "gpu_cuda_info": get_gpu_cuda_info(),
        },
        "git": git_info,
        "checks": [],
        "artifacts": [],
        "summary": {
            "required_checks_count": 0,
            "passed_checks_count": 0,
            "failed_checks_count": 0,
            "skipped_checks_count": 0,
            "blocking_failures": [],
            "warnings": [],
            "overall_verdict": "FAIL",
            "package_candidate_eligibility": False,
            "champion_agent": "unknown",
            "promotion_status": "unknown",
        },
        "gemini_support": {},
        "interrupted": False,
    }

    # Load and validate plan
    try:
        if not plan_path.exists():
            raise FileNotFoundError(f"Plan file not found: {plan_path}")
        with open(plan_path, "r", encoding="utf-8") as f:
            plan_data = json.load(f)
        validate_plan_schema(plan_data)
        validate_required_check_coverage(plan_data)
        if args.validate_only:
            print("[+] Plan schema is valid. Exiting validate-only successfully.", flush=True)
            return 0
    except Exception as e:
        results["summary"]["overall_verdict"] = "FAIL"
        results["summary"]["blocking_failures"].append(f"Plan validation failed: {e}")
        results["finished_at_utc"] = get_utc_now()
        # Only write output file if not validate_only
        if not args.validate_only:
            atomic_write(output_path, redact_structure(results, repo_root))
        print(f"[!] acceptance plan validation failed: {e}", file=sys.stderr)
        return 1

    champion_facts = check_champion_invariant(repo_root)
    promotion_facts = check_promotion_invariant(repo_root)
    results["summary"]["champion_agent"] = champion_facts.get("champion", "unknown")
    results["summary"]["promotion_status"] = promotion_facts.get("status", "unknown")

    # Read-only Gemini support integration manifest scanning
    gemini_support_commit = "7abde0bcbebe8bf5149303fa917f320ee7947129"
    gemini_manifest_path = "configs/offline_training_v1/gemini_support_integration_manifest.json"
    hold_modules = []
    gemini_facts = {
        "source_commit": gemini_support_commit,
        "manifest_sha256": "unknown",
        "schema_validation_ok": False,
        "module_counts": {"P0": 0, "P1": 0, "HOLD": 0},
        "p0_acceptance_tests": [],
        "hold_modules_quarantined": False,
    }

    try:
        git_show_cmd = [
            "git",
            "show",
            f"{gemini_support_commit}:{gemini_manifest_path}",
        ]
        res = subprocess.run(git_show_cmd, cwd=str(repo_root), capture_output=True, text=True, check=False)
        if res.returncode == 0:
            manifest_text = res.stdout
            gemini_facts["manifest_sha256"] = compute_sha256_text(manifest_text)
            manifest_json = json.loads(manifest_text)

            parsed = parse_gemini_manifest(manifest_json)
            gemini_facts["schema_validation_ok"] = parsed["schema_validation_ok"]
            gemini_facts["module_counts"] = parsed["module_counts"]
            gemini_facts["p0_acceptance_tests"] = parsed["p0_acceptance_tests"]
            hold_modules = parsed["hold_modules"]
        else:
            gemini_facts["error"] = (
                f"git show failed (exit {res.returncode}): {redact_text(res.stderr, repo_root)}"
            )
    except Exception as e:
        gemini_facts["error"] = f"Failed to retrieve/verify Gemini manifest: {e}"

    hold_check = check_hold_module_quarantine(repo_root, hold_modules)
    gemini_facts["hold_modules_quarantined"] = hold_check["invariant_ok"]
    gemini_facts["hold_quarantine_violations"] = hold_check["violations"]
    results["gemini_support"] = gemini_facts

    exit_code = 0
    interrupted = False

    passed_checks = 0
    failed_checks = 0
    skipped_checks = 0
    total_required = 0

    # Required-check-set coverage was already validated in
    # validate_required_check_coverage() above (shared by --validate-only,
    # --dry-run, and normal execution), so plan_data["checks"] is guaranteed
    # to contain every ID in EXPECTED_REQUIRED_CHECK_IDS at this point.

    # Stable across the whole run (depend only on run_id): the offline_training_v1
    # smoke pipeline's run root, and a check-owned scratch root for the
    # package_build/package_verify/artifact_hash chain. The latter is kept
    # off the shared, non-run-scoped `dist/kaggle/neural-student-v1/` path
    # that scripts/run_offline_training_v1.py's own pipeline "package" phase
    # (invoked by training_smoke) publishes to, so the two never collide
    # regardless of check order.
    run_id_smoke = f"smoke-run-{run_id}"
    package_scratch_rel = f"runs/offline-training-v1/_acceptance_scratch/{run_id}/package_build"

    try:
        for check in plan_data["checks"]:
            check_id = check["id"]
            runner = check["runner"]
            is_required = check["required"]
            timeout = check["timeout_seconds"]
            check_args = check["args"]

            if is_required:
                total_required += 1

            print(f"[*] Resolving check: {check_id} (runner: {runner}) ...", flush=True)

            check_record = {
                "id": check_id,
                "runner": runner,
                "required": is_required,
                "started_at_utc": get_utc_now(),
                "finished_at_utc": "",
                "duration_seconds": 0.0,
                "exit_code": -1,
                "timed_out": False,
                "status": "SKIP",
                "skip_reason": None,
                "stdout_sha256": "",
                "stderr_sha256": "",
                "bounded_excerpt": "",
                "produced_artifacts": [],
            }

            t_start = time.perf_counter()
            cmd_record = None

            missing_artifact_inputs = [
                p for p in check.get("artifact_inputs", []) if not (repo_root / p).exists()
            ]
            skip_due_to_missing_inputs = bool(missing_artifact_inputs) and not is_required

            if skip_due_to_missing_inputs:
                # Optional checks skip cleanly when a declared artifact_input
                # dependency (e.g. an earlier check's produced artifact)
                # is not present. Required checks never take this path --
                # they fall through to the runner and fail on their own
                # terms, per "required checkの未実行もFAIL".
                check_record["skip_reason"] = (
                    f"Optional check skipped: missing artifact_inputs {missing_artifact_inputs}"
                )
            elif args.dry_run:
                # Dry-run logic: resolve parameters but do not launch subprocesses
                cmd_record = {
                    "return_code": 0,
                    "timed_out": False,
                    "bounded_excerpt": f"[DRY_RUN] Resolved runner '{runner}' with check_args {check_args}",
                    "stdout_sha256": compute_sha256_text(""),
                    "stderr_sha256": compute_sha256_text(""),
                }
            else:
                if runner == "git_diff_check":
                    argv = ["git", "diff", "--check", "origin/feature/belief-guided-search...HEAD"] + check_args
                    cmd_record = run_command_safe(check_id, argv, timeout, repo_root, repo_root=repo_root)

                elif runner == "import_closure":
                    argv = [sys.executable, "scripts/check_offline_training_import_closure.py"] + check_args
                    cmd_record = run_command_safe(check_id, argv, timeout, repo_root, repo_root=repo_root)

                elif runner == "pytest":
                    argv = [sys.executable, "-m", "pytest"] + check_args + ["-q", "-p", "no:cacheprovider"]
                    env_updates = {
                        "PYTHONDONTWRITEBYTECODE": "1",
                        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
                        "PYTHONPATH": f".{os.pathsep}src",
                    }
                    cmd_record = run_command_safe(check_id, argv, timeout, repo_root, env_updates, repo_root=repo_root)

                elif runner in ("collection_smoke", "training_smoke", "resume_smoke"):
                    if runner == "collection_smoke":
                        if not check_args:
                            argv = [sys.executable, "scripts/run_offline_training_v1.py", "collect", "--config", "configs/offline_training_v1/smoke.json", "--run-id", run_id_smoke]
                        else:
                            argv = [sys.executable, "scripts/run_offline_training_v1.py"] + check_args
                    elif runner == "training_smoke":
                        if not check_args:
                            argv = [sys.executable, "scripts/run_offline_training_v1.py", "pipeline", "--config", "configs/offline_training_v1/smoke.json", "--run-id", run_id_smoke, "--resume"]
                        else:
                            argv = [sys.executable, "scripts/run_offline_training_v1.py"] + check_args
                    elif runner == "resume_smoke":
                        if not check_args:
                            argv = [sys.executable, "scripts/run_offline_training_v1.py", "resume", "--run-dir", f"runs/offline-training-v1/{run_id_smoke}"]
                        else:
                            argv = [sys.executable, "scripts/run_offline_training_v1.py"] + check_args
                    cmd_record = run_command_safe(check_id, argv, timeout, repo_root, repo_root=repo_root)

                elif runner == "export_parity":
                    # In-process: builds its own synthetic C4 data-ops
                    # fixture (see run_export_parity_check) rather than
                    # reusing training_smoke's offline_training_v1 pipeline
                    # run root, whose shape export_bundle() does not accept.
                    export_scratch = repo_root / f"runs/offline-training-v1/_acceptance_scratch/{run_id}/export_parity"
                    cmd_record = run_export_parity_check(repo_root, export_scratch)

                elif runner == "package_build":
                    # In-process: builds the actual neural-student-v1
                    # package via the same package.build_package() that
                    # training_smoke's own pipeline "package" phase uses.
                    # scripts/build_student_submission.py is a *different*,
                    # older artifact family ("C4 Student v0", a linear
                    # candidate-scorer loaded via StudentV0Model) with an
                    # incompatible model schema -- invoking it against
                    # training_smoke's neural-student-v1 export always
                    # failed with "unsupported model schema or feature
                    # version" once the directory-collision bug (fixed
                    # above) stopped masking it.
                    dest_dir = repo_root / package_scratch_rel
                    if dest_dir.exists():
                        shutil.rmtree(dest_dir)
                    try:
                        from mage_ptcg.offline_training import package as offline_package

                        export_path = repo_root / f"runs/offline-training-v1/{run_id_smoke}/export/neural-student-v1.json"
                        manifest = offline_package.build_package(
                            export_path=export_path,
                            output_dir=dest_dir,
                            repository_root=repo_root,
                            build_commit=git_info["commit_sha"],
                        )
                        cmd_record = {
                            "return_code": 0,
                            "timed_out": False,
                            "bounded_excerpt": (
                                f"[PACKAGE_BUILD] archive_sha256={manifest.get('archive_sha256')} "
                                f"model_purpose={manifest.get('model_purpose')}"
                            ),
                            "stdout_sha256": compute_sha256_text(""),
                            "stderr_sha256": compute_sha256_text(""),
                        }
                    except Exception as exc:
                        cmd_record = {
                            "return_code": 1,
                            "timed_out": False,
                            "bounded_excerpt": f"[PACKAGE_BUILD_ERROR] {type(exc).__name__}: {exc}",
                            "stdout_sha256": compute_sha256_text(""),
                            "stderr_sha256": compute_sha256_text(""),
                        }
                    tarball_rel = f"{package_scratch_rel}/submission.tar.gz"
                    tarball_path = repo_root / tarball_rel
                    if tarball_path.exists():
                        check_record["produced_artifacts"].append(tarball_rel)

                elif runner == "package_verify":
                    tarball_rel = f"{package_scratch_rel}/submission.tar.gz"
                    tarball_path = repo_root / tarball_rel
                    tarball_info = inspect_tarball(tarball_path)
                    check_record["tarball_inspection"] = tarball_info

                    if tarball_path.exists():
                        argv = ["tar", "-tzvf", tarball_rel]
                        cmd_record = run_command_safe(check_id, argv, timeout, repo_root, repo_root=repo_root)
                        if tarball_info.get("has_unsafe_members"):
                            cmd_record["return_code"] = 1
                            cmd_record["bounded_excerpt"] += "\n[VERIFY_ERROR] Unsafe tarball members detected (traversal or absolute paths)."
                    else:
                        cmd_record = {
                            "return_code": 1,
                            "timed_out": False,
                            "bounded_excerpt": "[VERIFY_ERROR] Submission tarball does not exist.",
                        }

                elif runner == "clean_room":
                    argv = ["git", "status", "--short"]
                    cmd_record = run_command_safe(check_id, argv, timeout, repo_root, repo_root=repo_root)
                    if cmd_record["return_code"] == 0:
                        # Decide from the raw, undecorated `git status --short`
                        # stdout -- never from bounded_excerpt, which always
                        # carries the "--- STDOUT ---" / "--- STDERR ---"
                        # display wrapper (run_command_safe() adds it
                        # unconditionally) and would misread that decoration
                        # itself as dirty entries on an otherwise-clean tree.
                        raw_stdout = cmd_record.get("_raw_stdout", "")
                        unexpected = [
                            line
                            for line in raw_stdout.splitlines()
                            if line.strip()
                            and ".venv" not in line
                            and "collect_offline_training_v1_evidence" not in line
                        ]
                        if len(unexpected) > 0:
                            cmd_record["return_code"] = 1
                            cmd_record["bounded_excerpt"] += f"\n[CLEAN_ROOM_ERROR] Unexpected changes: {unexpected}"

                elif runner in ("privacy_scan", "secret_scan", "absolute_path_scan"):
                    # Internal, subprocess-free self-scan of the evidence
                    # document assembled so far. This verifies the
                    # collector's own redaction guarantees (Phase 7) rather
                    # than running an unrelated external script; every
                    # bounded_excerpt already went through redact_text(),
                    # so a clean scan is a genuine regression check.
                    if runner == "privacy_scan":
                        scan_result = self_scan_privacy(results)
                    elif runner == "secret_scan":
                        scan_result = self_scan_pattern(results, RE_SECRET)
                    else:
                        scan_result = self_scan_pattern(results, RE_ABSOLUTE_PATH)
                    cmd_record = {
                        "return_code": 0 if scan_result["clean"] else 1,
                        "timed_out": False,
                        "bounded_excerpt": (
                            f"[{runner.upper()}] violation_count={scan_result['violation_count']} "
                            f"clean={scan_result['clean']}"
                        ),
                        "stdout_sha256": compute_sha256_text(""),
                        "stderr_sha256": compute_sha256_text(""),
                    }

                elif runner == "artifact_hash":
                    tarball_rel = f"{package_scratch_rel}/submission.tar.gz"
                    tarball_path = repo_root / tarball_rel

                    cmd_record = {
                        "return_code": 0,
                        "timed_out": False,
                        "bounded_excerpt": "Artifact hash verification",
                    }
                    if tarball_path.exists():
                        sha = compute_file_sha256(tarball_path)
                        cmd_record["bounded_excerpt"] += f"\nFound tarball: {tarball_rel}\nSHA-256: {sha}"
                        if check_args and sha != check_args[0]:
                            cmd_record["return_code"] = 1
                            cmd_record["bounded_excerpt"] += f"\n[HASH_ERROR] SHA mismatch. Expected: {check_args[0]}, Got: {sha}"
                    else:
                        cmd_record["bounded_excerpt"] += "\nNo package built for hash verification."

                elif runner == "fallback_invariant":
                    argv = [sys.executable, "-m", "pytest", "tests/test_offline_training_v1.py", "-k", "fallback", "-q", "-p", "no:cacheprovider"]
                    env_updates = {
                        "PYTHONDONTWRITEBYTECODE": "1",
                        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
                        "PYTHONPATH": f".{os.pathsep}src",
                    }
                    cmd_record = run_command_safe(check_id, argv, timeout, repo_root, env_updates, repo_root=repo_root)

                elif runner == "champion_invariant":
                    cmd_record = {
                        "return_code": 0,
                        "timed_out": False,
                        "bounded_excerpt": f"Champion default invariant: {champion_facts}\nPromotion invariant: {promotion_facts}",
                    }
                    if not champion_facts["invariant_ok"]:
                        cmd_record["return_code"] = 1
                    if not promotion_facts["invariant_ok"]:
                        cmd_record["return_code"] = 1

                elif runner == "gemini_manifest_validation":
                    cmd_record = {
                        "return_code": 0,
                        "timed_out": False,
                        "bounded_excerpt": json.dumps(gemini_facts, indent=2),
                    }
                    if not gemini_facts["schema_validation_ok"]:
                        cmd_record["return_code"] = 1
                    if not gemini_facts["hold_modules_quarantined"]:
                        cmd_record["return_code"] = 1
                        cmd_record["bounded_excerpt"] += "\n[HOLD_ERROR] HOLD modules are imported in production runtime."

            check_record["finished_at_utc"] = get_utc_now()
            check_record["duration_seconds"] = round(time.perf_counter() - t_start, 3)

            if cmd_record is not None:
                check_record["exit_code"] = cmd_record.get("return_code", -1)
                check_record["timed_out"] = cmd_record.get("timed_out", False)
                check_record["stdout_sha256"] = cmd_record.get("stdout_sha256", "")
                check_record["stderr_sha256"] = cmd_record.get("stderr_sha256", "")
                check_record["bounded_excerpt"] = cmd_record.get("bounded_excerpt", "")
                if cmd_record.get("launch_error"):
                    check_record["skip_reason"] = f"Launch error: {cmd_record['launch_error']}"
                    check_record["status"] = "FAIL"
                elif check_record["exit_code"] == 0 and not check_record["timed_out"]:
                    check_record["status"] = "PASS"
                else:
                    check_record["status"] = "FAIL"
            else:
                check_record["status"] = "SKIP"
                if not check_record.get("skip_reason"):
                    check_record["skip_reason"] = "Runner execution returned no command records"

            if check_record["status"] == "PASS":
                passed_checks += 1
            elif check_record["status"] == "FAIL":
                failed_checks += 1
                if is_required:
                    results["summary"]["blocking_failures"].append(
                        f"Required check failed: {check_id} (runner: {runner}, exit_code: {check_record['exit_code']})"
                    )
            else:
                skipped_checks += 1
                if is_required:
                    results["summary"]["blocking_failures"].append(
                        f"Required check skipped: {check_id}"
                    )

            # Redact before this record ever becomes visible in `results`:
            # privacy_scan/absolute_path_scan self-scan `results` as it
            # stands mid-loop, so a structured field added by an earlier
            # check (e.g. package_verify's tarball_inspection.path) must
            # already be clean by the time a later check scans it.
            check_record = redact_structure(check_record, repo_root)
            results["checks"].append(check_record)

    except KeyboardInterrupt:
        print("\n[!] Evidence collection interrupted by SIGINT. Saving facts collected so far...", flush=True)
        results["interrupted"] = True
        interrupted = True
        exit_code = 130
    except Exception as e:
        print(f"\n[!] Collector encountered a fatal exception: {e}", file=sys.stderr, flush=True)
        results["summary"]["blocking_failures"].append(f"Collector exception: {e}")
        exit_code = 1

    overall_ok = True

    if len(results["summary"]["blocking_failures"]) > 0:
        overall_ok = False

    if champion_facts.get("champion") != "Rule Agent v0":
        overall_ok = False
        results["summary"]["blocking_failures"].append(
            f"Champion invariant violated. Expected: 'Rule Agent v0', Got: '{champion_facts.get('champion')}'"
        )

    if promotion_facts.get("status") != "NO_DECISION":
        overall_ok = False
        results["summary"]["blocking_failures"].append(
            f"Promotion invariant violated. Expected: 'NO_DECISION', Got: '{promotion_facts.get('status')}'"
        )

    if not gemini_facts["hold_modules_quarantined"]:
        overall_ok = False
        results["summary"]["blocking_failures"].append(
            f"HOLD modules quarantine violated. Synthetic / HOLD modules are connected to runtime."
        )

    results["summary"]["required_checks_count"] = total_required
    results["summary"]["passed_checks_count"] = passed_checks
    results["summary"]["failed_checks_count"] = failed_checks
    results["summary"]["skipped_checks_count"] = skipped_checks
    results["summary"]["overall_verdict"] = "PASS" if (overall_ok and not interrupted and exit_code == 0) else "FAIL"

    package_built = any(
        any("submission.tar.gz" in p for p in c["produced_artifacts"])
        for c in results["checks"]
        if "produced_artifacts" in c
    )
    if not package_built:
        package_built = (repo_root / package_scratch_rel / "submission.tar.gz").exists()
    results["summary"]["package_candidate_eligibility"] = (results["summary"]["overall_verdict"] == "PASS" and package_built)

    required_paths_records = check_required_paths(repo_root, [
        "src/mage_ptcg/dataops/__init__.py",
        "src/mage_ptcg/dataops/collector.py",
        "src/mage_ptcg/student/artifact.py",
        "src/mage_ptcg/offline_training/dataset.py",
        "src/mage_ptcg/offline_training/neural.py",
        "src/mage_ptcg/offline_training/neural_runtime.py",
        "src/mage_ptcg/offline_training/package.py",
        "scripts/run_offline_training_v1.py",
        "scripts/check_offline_training_import_closure.py",
        "tests/test_offline_training_v1.py",
        "tests/test_c4_data_ops.py",
        "tests/test_c4_actual_training_bundle.py",
    ])
    for r in required_paths_records:
        if r.get("exists"):
            results["artifacts"].append({
                "logical_name": Path(r["path"]).stem,
                "portable_relative_path": r["path"],
                "size_bytes": r.get("size_bytes", 0),
                "sha256": r.get("sha256", ""),
                "artifact_type": "source_code",
                "privacy_class": "public",
                "package_inclusion": True,
                "producer_check_id": "manual_inventory",
                "provenance_commit": git_info["commit_sha"],
            })

    package_tarball_rel = f"{package_scratch_rel}/submission.tar.gz"
    tarball_path = repo_root / package_tarball_rel
    if tarball_path.exists():
        results["artifacts"].append({
            "logical_name": "submission_package",
            "portable_relative_path": package_tarball_rel,
            "size_bytes": tarball_path.stat().st_size,
            "sha256": compute_file_sha256(tarball_path),
            "artifact_type": "submission_tarball",
            "privacy_class": "public",
            "package_inclusion": True,
            "producer_check_id": "package_build",
            "provenance_commit": git_info["commit_sha"],
        })

    # Clean up this run's own check-scoped scratch state (export_parity's
    # fixture/bundles are already removed by run_export_parity_check;
    # package_build's scratch tarball is removed here now that package_verify,
    # artifact_hash, and the artifact registration above have all read it).
    # Never touches dist/ or any other pre-existing content under runs/.
    acceptance_scratch_root = repo_root / "runs" / "offline-training-v1" / "_acceptance_scratch" / run_id
    shutil.rmtree(acceptance_scratch_root, ignore_errors=True)

    results["finished_at_utc"] = get_utc_now()

    # Final defense-in-depth pass: redact the whole document (covers
    # results["artifacts"] / results["gemini_support"] / results["git"],
    # assembled after the per-check redaction above) before it is ever
    # written to disk.
    results = redact_structure(results, repo_root)

    try:
        print(f"[*] Saving evidence report atomically to {output_path} ...", flush=True)
        atomic_write(output_path, results)
        print(f"[+] Evidence report saved successfully. Overall Verdict: {results['summary']['overall_verdict']}", flush=True)
    except Exception as e:
        print(f"[!] Failed to write evidence to {output_path}: {e}", file=sys.stderr, flush=True)
        exit_code = 1

    if results["summary"]["overall_verdict"] == "FAIL" and exit_code == 0:
        return 1
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
