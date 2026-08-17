"""CLI for the Competition Intelligence sidecar.

Follows ``mage_ptcg.offline_training.cli``'s conventions: argparse
subparsers, a ``doctor`` command, JSON summaries on stdout via ``_emit``, and
non-zero exit codes with a JSON error on stderr for expected failures.

All O1-2..O1-4 commands are wired up here: ``doctor``, ``ingest-local``,
``rebuild-catalog`` (O1-0/O1-1), plus ``normalize``, ``analyze``,
``archive-note``, ``import-knowledge``, ``build-knowledge-snapshot``,
``build-snapshot``, ``export-offline-dataset``, ``materialize-dataset``,
``report`` (O1-2..O1-4). Business logic lives in ``pipeline.py``; this
module only parses arguments and formats output.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from . import catalog as catalog_module
from . import pipeline as pipeline_module
from .claim_bundle import CLAIM_BUNDLE_SCHEMA_VERSION
from .config import CONFIG_SCHEMA_VERSION, ConfigError, load_config
from .contracts import (
    DECISION_RECORD_SCHEMA_VERSION,
    DECK_OBSERVATION_SCHEMA_VERSION,
    EPISODE_RECORD_SCHEMA_VERSION,
    INTELLIGENCE_SNAPSHOT_SCHEMA_VERSION,
    KNOWLEDGE_CLAIM_SCHEMA_VERSION,
    SOURCE_ENVELOPE_SCHEMA_VERSION,
)
from .contracts import ContractError
from .contradiction import CONTRADICTION_SCHEMA_VERSION
from .dataset_materialization import (
    DATASET_AUDIT_SCHEMA_VERSION,
    DATASET_SCHEMA_VERSION,
    DATASET_STATISTICS_SCHEMA_VERSION,
)
from .external_capability import CAPABILITY_REPORT_SCHEMA_VERSION
from .external_schema import EXTERNAL_SCHEMA_VERSION
from .external_transport import EXTERNAL_ACTIONS
from .fingerprint import (
    DECK_FINGERPRINT_SCHEMA_VERSION,
    JOINT_FINGERPRINT_SCHEMA_VERSION,
    POLICY_FINGERPRINT_SCHEMA_VERSION,
)
from .failure_hypothesis import FAILURE_HYPOTHESIS_SCHEMA_VERSION
from .knowledge_snapshot import KNOWLEDGE_SNAPSHOT_SCHEMA_VERSION
from .leakage_audit import LEAKAGE_AUDIT_SCHEMA_VERSION
from .local_ingest import IngestError, ingest_local_file
from .matchup_stats import MATCHUP_STATISTICS_SCHEMA_VERSION
from .offline_adapter import DatasetExportError
from .runstate import DEFAULT_RUN_ROOT, MANIFEST_SCHEMA_VERSION, RunPaths, RunStateError
from .team_bundle import TEAM_BUNDLE_SCHEMA_VERSION
from .meta import META_SCHEMA_VERSION
from .surrogate import SURROGATE_SCHEMA_VERSION
from .benchmark import BENCHMARK_SCHEMA_VERSION


class CliError(RuntimeError):
    """Raised for a CLI-level failure that should print a clean JSON error."""


def _emit(summary: dict[str, Any]) -> None:
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2))


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _main_py_source() -> str | None:
    main_path = _repo_root() / "main.py"
    try:
        return main_path.read_text(encoding="utf-8")
    except OSError:
        return None


def _cmd_doctor(args: argparse.Namespace) -> int:
    report: dict[str, Any] = {"schema_versions": {
        "config": CONFIG_SCHEMA_VERSION,
        "run_manifest": MANIFEST_SCHEMA_VERSION,
        "catalog": catalog_module.CATALOG_SCHEMA_VERSION,
        "source_envelope": SOURCE_ENVELOPE_SCHEMA_VERSION,
        "episode_record": EPISODE_RECORD_SCHEMA_VERSION,
        "decision_record": DECISION_RECORD_SCHEMA_VERSION,
        "deck_observation": DECK_OBSERVATION_SCHEMA_VERSION,
        "knowledge_claim": KNOWLEDGE_CLAIM_SCHEMA_VERSION,
        "intelligence_snapshot": INTELLIGENCE_SNAPSHOT_SCHEMA_VERSION,
        "deck_fingerprint": DECK_FINGERPRINT_SCHEMA_VERSION,
        "policy_fingerprint": POLICY_FINGERPRINT_SCHEMA_VERSION,
        "joint_fingerprint": JOINT_FINGERPRINT_SCHEMA_VERSION,
        "matchup_statistics": MATCHUP_STATISTICS_SCHEMA_VERSION,
        "failure_hypothesis": FAILURE_HYPOTHESIS_SCHEMA_VERSION,
        "claim_bundle": CLAIM_BUNDLE_SCHEMA_VERSION,
        "knowledge_contradiction": CONTRADICTION_SCHEMA_VERSION,
        "knowledge_snapshot": KNOWLEDGE_SNAPSHOT_SCHEMA_VERSION,
        "leakage_audit": LEAKAGE_AUDIT_SCHEMA_VERSION,
        "report": pipeline_module.REPORT_SCHEMA_VERSION,
        "external_capability_report": CAPABILITY_REPORT_SCHEMA_VERSION,
        "external_schema_drift_report": EXTERNAL_SCHEMA_VERSION,
        "team_bundle": TEAM_BUNDLE_SCHEMA_VERSION,
        "meta_snapshot": META_SCHEMA_VERSION,
        "opponent_surrogate": SURROGATE_SCHEMA_VERSION,
        "benchmark": BENCHMARK_SCHEMA_VERSION,
        "dataset": DATASET_SCHEMA_VERSION,
        "dataset_audit": DATASET_AUDIT_SCHEMA_VERSION,
        "dataset_statistics": DATASET_STATISTICS_SCHEMA_VERSION,
    }}

    config_ok = True
    config_error: str | None = None
    if args.config:
        try:
            load_config(args.config)
        except (ConfigError, OSError, json.JSONDecodeError) as exc:
            config_ok = False
            config_error = str(exc)
    report["config"] = {"checked": bool(args.config), "valid": config_ok, "error": config_error}

    run_root = Path(args.run_root) if args.run_root else DEFAULT_RUN_ROOT
    writable = False
    write_error: str | None = None
    try:
        run_root.mkdir(parents=True, exist_ok=True)
        probe = run_root / f".doctor-write-probe-{os.getpid()}.tmp"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        writable = True
    except OSError as exc:
        write_error = str(exc)
    report["run_root"] = {"path": str(run_root), "writable": writable, "error": write_error}

    try:
        import mage_ptcg.competition  # noqa: F401  (existing C2b package this sidecar extends)
        competition_probe_importable = True
    except ImportError:
        competition_probe_importable = False
    report["existing_competition_probe_importable"] = competition_probe_importable

    main_source = _main_py_source()
    report["runtime_isolation"] = {
        "main_py_found": main_source is not None,
        "main_py_references_competition_intelligence": (
            "competition_intelligence" in main_source if main_source is not None else None
        ),
        "note": "static substring check only; see tests/test_competition_intelligence_runtime_isolation.py for the dynamic import-graph check",
    }

    protected_baseline_path = Path(args.protected_baseline) if args.protected_baseline else None
    report["protected_files_baseline"] = {
        "path": str(protected_baseline_path) if protected_baseline_path else None,
        "present": protected_baseline_path.exists() if protected_baseline_path else False,
    }

    ok = config_ok and writable and not report["runtime_isolation"]["main_py_references_competition_intelligence"]
    report["ok"] = bool(ok)
    _emit(report)
    return 0 if ok else 1


def _cmd_ingest_local(args: argparse.Namespace) -> int:
    allowed_uses = args.allowed_uses.split(",") if args.allowed_uses else None
    try:
        result = ingest_local_file(
            args.run_dir,
            args.input,
            source_id=args.source_id,
            source_kind=args.source_kind,
            allowed_uses=allowed_uses,
            owner_scope=args.owner_scope,
            visibility=args.visibility,
            acquired_at=args.acquired_at,
            origin_reference=args.origin_reference,
        )
    except IngestError as exc:
        raise CliError(str(exc)) from exc
    _emit(result)
    return 0 if result["status"] in ("ARCHIVED", "QUARANTINED") else 1


def _cmd_rebuild_catalog(args: argparse.Namespace) -> int:
    try:
        result = catalog_module.rebuild_catalog(args.run_dir)
    except catalog_module.CatalogError as exc:
        raise CliError(str(exc)) from exc
    _emit({"run_dir": args.run_dir, **result})
    return 0


def _cmd_normalize(args: argparse.Namespace) -> int:
    try:
        result = pipeline_module.run_normalize(
            args.run_dir, offline_training_run=args.offline_training_run, source_id=args.source_id
        )
    except pipeline_module.PipelineError as exc:
        raise CliError(str(exc)) from exc
    _emit(result)
    return 0


def _cmd_normalize_live_own(args: argparse.Namespace) -> int:
    try:
        result = pipeline_module.run_normalize_live_own(args.run_dir, source_run_root=args.source_run_dir)
    except pipeline_module.PipelineError as exc:
        raise CliError(str(exc)) from exc
    _emit(result)
    return 0 if result.get("quarantine_count", 0) == 0 else 1


def _cmd_analyze(args: argparse.Namespace) -> int:
    try:
        result = pipeline_module.run_analyze(args.run_dir)
    except pipeline_module.PipelineError as exc:
        raise CliError(str(exc)) from exc
    _emit(result)
    return 0


def _cmd_archive_note(args: argparse.Namespace) -> int:
    from . import archive as archive_module

    text = Path(args.text_file).read_text(encoding="utf-8") if args.text_file else args.text
    if text is None:
        raise CliError("one of --text or --text-file is required")
    allowed_uses = args.allowed_uses.split(",") if args.allowed_uses else ("ARCHIVE", "ANALYSIS", "REPORTING")
    try:
        result = pipeline_module.run_archive_note(
            args.run_dir, text=text, source_id=args.source_id, acquired_at=args.acquired_at or _now(),
            origin_reference=args.origin_reference, allowed_uses=allowed_uses,
        )
    except (archive_module.ArchiveError, ContractError) as exc:
        raise CliError(str(exc)) from exc
    _emit(result)
    return 0


def _cmd_import_knowledge(args: argparse.Namespace) -> int:
    from .claim_bundle import ClaimBundleError
    from .knowledge_registry import KnowledgeRegistryError

    try:
        result = pipeline_module.run_import_knowledge(
            args.run_dir, bundle_path=args.bundle, raw_source_id=args.raw_source_id,
            created_at=args.created_at or _now(),
        )
    except (ClaimBundleError, KnowledgeRegistryError, pipeline_module.PipelineError) as exc:
        raise CliError(str(exc)) from exc
    _emit(result)
    return 0


def _cmd_build_knowledge_snapshot(args: argparse.Namespace) -> int:
    from .contracts import ContractError

    try:
        result = pipeline_module.run_build_knowledge_snapshot(
            args.run_dir, cutoff_time=args.cutoff, created_at=args.created_at or _now()
        )
    except (ContractError, pipeline_module.PipelineError) as exc:
        raise CliError(str(exc)) from exc
    _emit(result)
    return 0


def _cmd_build_snapshot(args: argparse.Namespace) -> int:
    from .contracts import ContractError
    from .snapshot_builder import SnapshotBuildError

    try:
        result = pipeline_module.run_build_snapshot(
            args.run_dir, cutoff_time=args.cutoff, created_at=args.created_at or _now(),
            base_commit=args.base_commit, seed=args.seed, require_cutoff=args.require_cutoff,
            knowledge_snapshot_hash=args.knowledge_snapshot_hash,
        )
    except (ContractError, SnapshotBuildError, pipeline_module.PipelineError) as exc:
        raise CliError(str(exc)) from exc
    _emit(result)
    return 0 if result.get("leakage_audit_passed") in (True, None) else 1


def _cmd_export_offline_dataset(args: argparse.Namespace) -> int:
    try:
        result = pipeline_module.run_export_offline_dataset(
            args.run_dir, snapshot_id=args.snapshot_id, offline_training_run=args.offline_training_run,
            output_path=args.output, split=args.split,
        )
    except (DatasetExportError, pipeline_module.PipelineError) as exc:
        raise CliError(str(exc)) from exc
    _emit(result)
    return 0


def _cmd_materialize_dataset(args: argparse.Namespace) -> int:
    from .dataset_materialization import DatasetMaterializationError

    try:
        result = pipeline_module.run_materialize_dataset(
            args.run_dir, offline_training_run=args.offline_training_run, created_at=args.created_at or _now(),
            sources=args.sources, baseline=args.baseline, snapshot_id=args.snapshot_id, split=args.split,
            knowledge_snapshot_id=args.knowledge_snapshot_id, training_policy=args.training_policy,
        )
    except (DatasetExportError, DatasetMaterializationError, pipeline_module.PipelineError) as exc:
        raise CliError(str(exc)) from exc
    _emit(result)
    return 0


def _cmd_report(args: argparse.Namespace) -> int:
    try:
        result = pipeline_module.run_report(args.run_dir)
    except pipeline_module.PipelineError as exc:
        raise CliError(str(exc)) from exc
    _emit(result)
    return 0


def _cmd_probe_external(args: argparse.Namespace) -> int:
    try:
        result = pipeline_module.run_probe_external(
            args.run_dir, target=args.target, mode=args.mode, fixture_path=args.fixture_file,
            recordings_dir=args.recordings_dir, timeout=args.timeout,
        )
    except pipeline_module.PipelineError as exc:
        raise CliError(str(exc)) from exc
    _emit(result)
    return 0


def _cmd_ingest_kaggle(args: argparse.Namespace) -> int:
    from .external_acquisition import AcquisitionError

    if args.dry_run:
        _emit({"dry_run": True, "action": args.action, "target": args.target, "mode": args.mode})
        return 0
    allowed_uses = args.allowed_uses.split(",") if args.allowed_uses else []
    try:
        result = pipeline_module.run_ingest_kaggle(
            args.run_dir, action=args.action, target=args.target, allowed_uses=allowed_uses, mode=args.mode,
            fixture_path=args.fixture_file, recordings_dir=args.recordings_dir, timeout=args.timeout,
        )
    except (AcquisitionError, pipeline_module.PipelineError) as exc:
        raise CliError(str(exc)) from exc
    _emit(result)
    return 0 if result["status"] in ("ARCHIVED", "UNAVAILABLE", "QUARANTINED") else 1


def _cmd_ingest_public(args: argparse.Namespace) -> int:
    from .external_acquisition import AcquisitionError

    if args.dry_run:
        _emit({"dry_run": True, "action": args.action, "target": args.target, "mode": args.mode})
        return 0
    allowed_uses = args.allowed_uses.split(",") if args.allowed_uses else []
    try:
        result = pipeline_module.run_ingest_public(
            args.run_dir, action=args.action, target=args.target, allowed_uses=allowed_uses, mode=args.mode,
            fixture_path=args.fixture_file, recordings_dir=args.recordings_dir, timeout=args.timeout,
        )
    except (AcquisitionError, ContractError, pipeline_module.PipelineError) as exc:
        raise CliError(str(exc)) from exc
    _emit(result)
    return 0 if result["status"] in ("ARCHIVED", "UNAVAILABLE", "QUARANTINED") else 1


def _cmd_ingest_team(args: argparse.Namespace) -> int:
    from .team_bundle import TeamBundleError

    if args.dry_run:
        _emit({"dry_run": True, "bundle_root": args.bundle_root})
        return 0
    cli_uses = args.allowed_uses.split(",") if args.allowed_uses else None
    try:
        result = pipeline_module.run_ingest_team(
            args.run_dir, bundle_root=args.bundle_root, cli_requested_uses=cli_uses, created_at=args.created_at,
        )
    except TeamBundleError as exc:
        raise CliError(str(exc)) from exc
    _emit(result)
    return 0 if result["status"] in ("ARCHIVED", "ALREADY_IMPORTED", "QUARANTINED") else 1


def _cmd_schema_report(args: argparse.Namespace) -> int:
    try:
        result = pipeline_module.run_schema_report(args.run_dir, source_kind=args.source_kind, action=args.action)
    except pipeline_module.PipelineError as exc:
        raise CliError(str(exc)) from exc
    _emit(result)
    return 0


def _cmd_build_meta_snapshot(args: argparse.Namespace) -> int:
    _emit(pipeline_module.run_build_meta_snapshot(args.run_dir, cutoff_time=args.cutoff))
    return 0


def _cmd_drift_report(args: argparse.Namespace) -> int:
    _emit(pipeline_module.run_drift_report(args.run_dir, previous_meta_snapshot_id=args.previous, current_meta_snapshot_id=args.current))
    return 0


def _cmd_build_surrogate(args: argparse.Namespace) -> int:
    _emit(pipeline_module.run_build_surrogate(args.run_dir, cutoff_time=args.cutoff))
    return 0


def _cmd_run_intelligence_cycle(args: argparse.Namespace) -> int:
    _emit(pipeline_module.run_intelligence_cycle(args.run_dir, offline_training_run=args.offline_training_run,
          source_id=args.source_id, cutoff_time=args.cutoff, created_at=args.created_at or _now(),
          base_commit=args.base_commit, seed=args.seed))
    return 0


def _load_json_object(path: str | None, *, default: Any) -> Any:
    if path is None:
        return default
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CliError(f"invalid JSON input {path!r}") from exc


def _o5_registry(args: argparse.Namespace):
    from .o5_registry import DeckArchetypeRegistry
    return DeckArchetypeRegistry(args.registry_dir)


def _cmd_o5_acquire_environment(args: argparse.Namespace) -> int:
    from .o5_registry import EnvironmentTopDeckCollector, EnvironmentTopDeckPolicy
    from .rules_attestation import load_rules_attestation

    leaderboard = _load_json_object(args.leaderboard_json, default=[])
    submissions = _load_json_object(args.submissions_json, default={})
    episodes = _load_json_object(args.episodes_json, default={})
    if not isinstance(leaderboard, list) or not isinstance(submissions, dict) or not isinstance(episodes, dict):
        raise CliError("O5 environment inputs must be leaderboard list, submissions object, and episodes object")
    policy = EnvironmentTopDeckPolicy.load(args.policy) if args.policy else EnvironmentTopDeckPolicy()
    attestation = load_rules_attestation(args.rules_attestation) if args.rules_attestation else None
    registry = _o5_registry(args)
    result = EnvironmentTopDeckCollector().collect(
        leaderboard, submissions, episodes, policy, attestation, registry=registry,
    )
    registry.save()
    payload = result.as_dict()
    payload.update({"manifest_path": str(registry.path), "resume_state": {"known_observations": len(registry.known_observation_ids)}})
    _emit(payload)
    return 0


def _cmd_o5_inventory_branches(args: argparse.Namespace) -> int:
    from .o5_registry import TeamBranchInventoryImporter

    registry = _o5_registry(args)
    refs = tuple(args.refs) if args.refs else None
    result = TeamBranchInventoryImporter(Path(args.repo_root).resolve()).inventory(registry, refs)
    registry.save()
    result["resume_state"] = {"branch_inventories": len(registry.data["branch_inventories"])}
    _emit(result)
    return 0


def _cmd_o5_reconcile(args: argparse.Namespace) -> int:
    registry = _o5_registry(args)
    stats = registry.reconcile()
    registry.save()
    _emit({"manifest_path": str(registry.path), "unique_decks": len(stats), "deck_source_statistics": stats})
    return 0


def _cmd_o5_coverage(args: argparse.Namespace) -> int:
    from .o5_registry import coverage_report

    registry = _o5_registry(args)
    report = coverage_report(registry)
    registry.save()
    report["manifest_path"] = str(registry.path)
    _emit(report)
    return 0


def _cmd_o5_diagnose_parser(args: argparse.Namespace) -> int:
    from .o5_payload import PayloadExtractionError, archive_raw_response, extract_structured_payload
    stdout = Path(args.stdout).read_bytes()
    stderr = Path(args.stderr).read_bytes() if args.stderr else b""
    archive = archive_raw_response(args.archive_dir, stdout=stdout, stderr=stderr, exit_code=args.exit_code, cli_version=args.cli_version)
    try:
        candidate = extract_structured_payload(stdout, stderr)
    except PayloadExtractionError as exc:
        _emit({"status": "PARSER_BLOCKED", "blocker": str(exc), "archive": archive, "manifest_path": archive["manifest_path"]})
        return 1
    _emit({"status": "PARSED_UNTRUSTED", "archive": archive, "envelope_kind": candidate.envelope_kind, "schema_fingerprint": candidate.schema_fingerprint, "manifest_path": archive["manifest_path"]})
    return 0


def _cmd_o5_review_packets(args: argparse.Namespace) -> int:
    from .o5_activation import RulesUseGate, write_review_packets
    registry = _o5_registry(args)
    packet = write_review_packets(args.output_dir, rules_gate=RulesUseGate.unverified(), pending_artifacts=registry.data.get("branch_artifacts", []))
    _emit({"status": "RULES_BLOCKED", "rules_review_status": "UNVERIFIED", "manifest_path": packet["capability_matrix"], **packet})
    return 0


def _cmd_o5_build_benchmark(args: argparse.Namespace) -> int:
    from .o5_activation import build_benchmark_manifest
    from .o5_registry import coverage_report
    registry = _o5_registry(args)
    report = coverage_report(registry)
    manifest = build_benchmark_manifest((), active_exact_decks=int(report["eligible_exact_decks"]), runnable_families=int(report["runnable_agents"]), verified_links=int(report["verified_agent_deck_links"]))
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    _emit({"status": manifest["status"], "manifest_path": str(destination), "resume_state": "NOT_REQUIRED", "benchmark_hash": manifest["content_hash"]})
    return 0


def _cmd_o5_blocked_activation(args: argparse.Namespace) -> int:
    from .o5_registry import coverage_report
    registry = _o5_registry(args)
    report = coverage_report(registry)
    _emit({"status": "RULES_BLOCKED" if report["rules_gate_status"] == "UNVERIFIED_RULES_CONSTRAINT" else "PERMISSION_BLOCKED", "manifest_path": str(registry.path), "resume_state": "rerun_after_attestation_or_permission", "coverage": report})
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="run_competition_intelligence", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    doctor_p = sub.add_parser("doctor", help="check repo/runtime/config health without touching a run")
    doctor_p.add_argument("--config", type=str, default=None)
    doctor_p.add_argument("--run-root", type=str, default=None)
    doctor_p.add_argument("--protected-baseline", type=str, default=None)
    doctor_p.set_defaults(func=_cmd_doctor)

    ingest_p = sub.add_parser("ingest-local", help="archive one local file as a provenance-tracked raw source")
    ingest_p.add_argument("--run-dir", type=str, required=True)
    ingest_p.add_argument("--input", type=str, required=True)
    ingest_p.add_argument("--source-id", type=str, default=None)
    ingest_p.add_argument("--source-kind", type=str, default="LOCAL_SELFPLAY")
    ingest_p.add_argument("--allowed-uses", type=str, default=None, help="comma-separated AllowedUse names")
    ingest_p.add_argument("--owner-scope", type=str, default="self")
    ingest_p.add_argument("--visibility", type=str, default="private")
    ingest_p.add_argument(
        "--acquired-at", type=str, required=True,
        help="ISO-8601 timestamp the source itself declares as acquired -- required, never auto-filled with the current time (it is part of the SourceEnvelope's content-derived identity)",
    )
    ingest_p.add_argument("--origin-reference", type=str, default=None, help="defaults to a redacted form of --input")
    ingest_p.set_defaults(func=_cmd_ingest_local)

    rebuild_p = sub.add_parser("rebuild-catalog", help="rebuild the non-canonical SQLite catalog from canonical artifacts")
    rebuild_p.add_argument("--run-dir", type=str, required=True)
    rebuild_p.set_defaults(func=_cmd_rebuild_catalog)

    normalize_p = sub.add_parser("normalize", help="normalize an existing Offline Training run into Episode/Decision records")
    normalize_p.add_argument("--run-dir", type=str, required=True)
    normalize_p.add_argument("--offline-training-run", type=str, required=True, help="path to an existing offline_training run directory")
    normalize_p.add_argument("--source-id", type=str, required=True, help="SourceEnvelope id this data was ingested under")
    normalize_p.set_defaults(func=_cmd_normalize)

    normalize_live_p = sub.add_parser(
        "normalize-live-own",
        help="normalize verified archived OWN_KAGGLE Replay payloads into a separate actor-visible derivative run",
    )
    normalize_live_p.add_argument("--run-dir", type=str, required=True, help="output derivative run; source raw bytes are not copied")
    normalize_live_p.add_argument("--source-run-dir", type=str, required=True, help="read-only O4 acquisition run containing source manifests/raw")
    normalize_live_p.set_defaults(func=_cmd_normalize_live_own)

    analyze_p = sub.add_parser("analyze", help="compute deck/policy/joint fingerprints, matchup stats, failure hypotheses")
    analyze_p.add_argument("--run-dir", type=str, required=True)
    analyze_p.set_defaults(func=_cmd_analyze)

    archive_note_p = sub.add_parser("archive-note", help="archive one raw human-text note as a provenance-tracked HUMAN_TEXT source")
    archive_note_p.add_argument("--run-dir", type=str, required=True)
    archive_note_p.add_argument("--text", type=str, default=None)
    archive_note_p.add_argument("--text-file", type=str, default=None)
    archive_note_p.add_argument("--source-id", type=str, required=True)
    archive_note_p.add_argument("--origin-reference", type=str, required=True)
    archive_note_p.add_argument("--acquired-at", type=str, default=None, help="ISO-8601 timestamp; defaults to now")
    archive_note_p.add_argument("--allowed-uses", type=str, default=None, help="comma-separated AllowedUse names; defaults to ARCHIVE,ANALYSIS,REPORTING")
    archive_note_p.set_defaults(func=_cmd_archive_note)

    import_knowledge_p = sub.add_parser("import-knowledge", help="import a Claim Bundle (YAML/JSON) into the Knowledge Registry")
    import_knowledge_p.add_argument("--run-dir", type=str, required=True)
    import_knowledge_p.add_argument("--bundle", type=str, required=True)
    import_knowledge_p.add_argument("--raw-source-id", type=str, required=True)
    import_knowledge_p.add_argument("--created-at", type=str, default=None, help="ISO-8601 timestamp; defaults to now")
    import_knowledge_p.set_defaults(func=_cmd_import_knowledge)

    build_knowledge_snapshot_p = sub.add_parser("build-knowledge-snapshot", help="build an immutable Knowledge Snapshot from the current registry")
    build_knowledge_snapshot_p.add_argument("--run-dir", type=str, required=True)
    build_knowledge_snapshot_p.add_argument("--cutoff", type=str, required=True, help="ISO-8601 cutoff timestamp")
    build_knowledge_snapshot_p.add_argument("--created-at", type=str, default=None)
    build_knowledge_snapshot_p.set_defaults(func=_cmd_build_knowledge_snapshot)

    build_snapshot_p = sub.add_parser("build-snapshot", help="build an immutable Intelligence Snapshot from normalized episodes/decisions")
    build_snapshot_p.add_argument("--run-dir", type=str, required=True)
    build_snapshot_p.add_argument("--cutoff", type=str, required=True, help="ISO-8601 cutoff timestamp")
    build_snapshot_p.add_argument("--base-commit", type=str, required=True)
    build_snapshot_p.add_argument("--created-at", type=str, default=None)
    build_snapshot_p.add_argument("--seed", type=int, default=0)
    build_snapshot_p.add_argument("--require-cutoff", action="store_true")
    build_snapshot_p.add_argument("--knowledge-snapshot-hash", type=str, default=None)
    build_snapshot_p.set_defaults(func=_cmd_build_snapshot)

    export_p = sub.add_parser("export-offline-dataset", help="export a selection-only, Offline Training-compatible dataset from a snapshot's split")
    export_p.add_argument("--run-dir", type=str, required=True)
    export_p.add_argument("--snapshot-id", type=str, required=True)
    export_p.add_argument("--offline-training-run", type=str, required=True)
    export_p.add_argument("--output", type=str, required=True)
    export_p.add_argument("--split", type=str, default="train", choices=("train", "validation", "test"))
    export_p.set_defaults(func=_cmd_export_offline_dataset)

    materialize_p = sub.add_parser(
        "materialize-dataset",
        help="materialize a deterministic, audited offline dataset (shards + manifest + audit + statistics) from a snapshot, or a pre-O1 baseline",
    )
    materialize_p.add_argument("--run-dir", type=str, required=True)
    materialize_p.add_argument("--offline-training-run", type=str, required=True)
    materialize_p.add_argument("--created-at", type=str, default=None, help="ISO-8601 timestamp; defaults to now")
    materialize_p.add_argument("--sources", type=str, default="both", choices=("replay", "knowledge", "both"))
    materialize_p.add_argument("--baseline", action="store_true", help="reproduce the pre-O1 dataset: unfiltered, sources forced to 'replay'")
    materialize_p.add_argument("--snapshot-id", type=str, default=None, help="Intelligence Snapshot id (required unless --baseline)")
    materialize_p.add_argument("--split", type=str, default="train", choices=("train", "validation", "test"))
    materialize_p.add_argument("--knowledge-snapshot-id", type=str, default=None, help="Knowledge Snapshot id (required when --sources includes 'knowledge')")
    materialize_p.add_argument(
        "--training-policy", type=str, default=None,
        choices=("ANALYSIS_ALL_PERMITTED", "TRAINING_HIGH_INFORMATION", "TRAINING_VERIFIED", "TRAINING_HIGH_INFORMATION_VERIFIED"),
        help="decision-level training-eligibility gate for the replay shard; defaults to the strictest (TRAINING_HIGH_INFORMATION_VERIFIED) when omitted, never 'export everything'",
    )
    materialize_p.set_defaults(func=_cmd_materialize_dataset)

    report_p = sub.add_parser("report", help="deterministic JSON summary of a run's current state")
    report_p.add_argument("--run-dir", type=str, required=True)
    report_p.set_defaults(func=_cmd_report)

    def _add_transport_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("--mode", type=str, default="unavailable", choices=("unavailable", "fixture", "recorded", "live"),
                        help="transport mode; 'live' is opt-in only and disabled by default")
        p.add_argument("--fixture-file", type=str, default=None, help="JSON file of canned responses (mode=fixture)")
        p.add_argument("--recordings-dir", type=str, default=None, help="directory of recorded per-action responses (mode=recorded)")
        p.add_argument("--timeout", type=float, default=20.0)

    probe_external_p = sub.add_parser("probe-external", help="probe external (Kaggle) capability without ingesting anything")
    probe_external_p.add_argument("--run-dir", type=str, required=True)
    probe_external_p.add_argument("--target", type=str, required=True, help="competition slug or submission/episode identifier")
    _add_transport_args(probe_external_p)
    probe_external_p.set_defaults(func=_cmd_probe_external)

    ingest_kaggle_p = sub.add_parser("ingest-kaggle", help="ingest one own-Kaggle artifact (submissions/episodes/replay/logs)")
    ingest_kaggle_p.add_argument("--run-dir", type=str, required=True)
    ingest_kaggle_p.add_argument("--action", type=str, required=True, choices=EXTERNAL_ACTIONS)
    ingest_kaggle_p.add_argument("--target", type=str, required=True)
    ingest_kaggle_p.add_argument("--allowed-uses", type=str, default=None, help="comma-separated AllowedUse names")
    ingest_kaggle_p.add_argument("--dry-run", action="store_true")
    _add_transport_args(ingest_kaggle_p)
    ingest_kaggle_p.set_defaults(func=_cmd_ingest_kaggle)

    ingest_public_p = sub.add_parser("ingest-public", help="ingest one PUBLIC_OTHER artifact (public files/logs/leaderboard)")
    ingest_public_p.add_argument("--run-dir", type=str, required=True)
    ingest_public_p.add_argument("--action", type=str, required=True, choices=EXTERNAL_ACTIONS)
    ingest_public_p.add_argument("--target", type=str, required=True)
    ingest_public_p.add_argument("--allowed-uses", type=str, default=None,
                                  help="comma-separated AllowedUse names; TRAINING/REDISTRIBUTION are always rejected for PUBLIC_OTHER")
    ingest_public_p.add_argument("--dry-run", action="store_true")
    _add_transport_args(ingest_public_p)
    ingest_public_p.set_defaults(func=_cmd_ingest_public)

    ingest_team_p = sub.add_parser("ingest-team", help="securely import a Team Bundle directory")
    ingest_team_p.add_argument("--run-dir", type=str, required=True)
    ingest_team_p.add_argument("--bundle-root", type=str, required=True)
    ingest_team_p.add_argument("--allowed-uses", type=str, default=None,
                                help="comma-separated AllowedUse names to request; must not exceed what the bundle grants")
    ingest_team_p.add_argument("--dry-run", action="store_true")
    ingest_team_p.add_argument(
        "--created-at", type=str, required=True,
        help="ISO-8601 timestamp the bundle itself declares as created -- required, never auto-filled with the current time",
    )
    ingest_team_p.set_defaults(func=_cmd_ingest_team)

    schema_report_p = sub.add_parser("schema-report", help="report the persisted trust-on-first-use schema baseline for a (source_kind, action)")
    schema_report_p.add_argument("--run-dir", type=str, required=True)
    schema_report_p.add_argument("--source-kind", type=str, required=True)
    schema_report_p.add_argument("--action", type=str, required=True, choices=EXTERNAL_ACTIONS)
    schema_report_p.set_defaults(func=_cmd_schema_report)

    meta_p = sub.add_parser("build-meta-snapshot", help="build immutable deterministic meta posterior")
    meta_p.add_argument("--run-dir", required=True)
    meta_p.add_argument("--cutoff", required=True)
    meta_p.set_defaults(func=_cmd_build_meta_snapshot)
    drift_p = sub.add_parser("drift-report", help="compare two immutable meta snapshots")
    drift_p.add_argument("--run-dir", required=True)
    drift_p.add_argument("--previous", required=True)
    drift_p.add_argument("--current", required=True)
    drift_p.set_defaults(func=_cmd_drift_report)
    surrogate_p = sub.add_parser("build-surrogate", help="build smoothed empirical opponent policy; never a training source")
    surrogate_p.add_argument("--run-dir", required=True)
    surrogate_p.add_argument("--cutoff", required=True)
    surrogate_p.set_defaults(func=_cmd_build_surrogate)
    cycle_p = sub.add_parser("run-intelligence-cycle", help="one-shot resumable analysis cycle; no auto training/promotion/submission")
    cycle_p.add_argument("--run-dir", required=True)
    cycle_p.add_argument("--offline-training-run", required=True)
    cycle_p.add_argument("--source-id", required=True)
    cycle_p.add_argument("--cutoff", required=True)
    cycle_p.add_argument("--base-commit", required=True)
    cycle_p.add_argument("--created-at", default=None)
    cycle_p.add_argument("--seed", type=int, default=0)
    cycle_p.set_defaults(func=_cmd_run_intelligence_cycle)

    o5_p = sub.add_parser("o5", help="O5 Deck Archetype Registry acquisition and coverage commands")
    o5_sub = o5_p.add_subparsers(dest="o5_command", required=True)

    def _add_o5_registry_arg(command: argparse.ArgumentParser) -> None:
        command.add_argument("--registry-dir", required=True, help="directory containing deck_archetype_registry.json")

    env_p = o5_sub.add_parser("acquire-environment-top-decks", help="register existing O3/O4 archived environment replay observations")
    _add_o5_registry_arg(env_p)
    env_p.add_argument("--leaderboard-json", default=None, help="redacted leaderboard snapshot JSON list")
    env_p.add_argument("--submissions-json", default=None, help="submission metadata JSON object keyed by submission id")
    env_p.add_argument("--episodes-json", default=None, help="archived replay-derived episode JSON object keyed by submission id")
    env_p.add_argument("--policy", default=None, help="O5 environment acquisition policy JSON")
    env_p.add_argument("--rules-attestation", default=None, help="human rules attestation JSON; absent stays CAPTURE_ONLY")
    env_p.set_defaults(func=_cmd_o5_acquire_environment)

    for name, help_text in (("inventory-team-branches", "inventory all local/origin refs through git objects without checkout"), ("import-team-branches", "inventory/import team branch provenance without copying agent code")):
        branch_p = o5_sub.add_parser(name, help=help_text)
        _add_o5_registry_arg(branch_p)
        branch_p.add_argument("--repo-root", default=str(_repo_root()))
        branch_p.add_argument("--refs", nargs="*", default=None, help="optional explicit refs; default scans all local and origin refs")
        branch_p.set_defaults(func=_cmd_o5_inventory_branches)

    reconcile_p = o5_sub.add_parser("reconcile-deck-sources", help="deduplicate exact decks and recompute source statistics")
    _add_o5_registry_arg(reconcile_p)
    reconcile_p.set_defaults(func=_cmd_o5_reconcile)
    coverage_p = o5_sub.add_parser("report-archetype-coverage", help="emit O5 cross-source coverage and permission report")
    _add_o5_registry_arg(coverage_p)
    coverage_p.set_defaults(func=_cmd_o5_coverage)

    diagnose_p = o5_sub.add_parser("diagnose-environment-parser", help="archive raw stdout/stderr then fail-closed parse a structured response")
    diagnose_p.add_argument("--stdout", required=True)
    diagnose_p.add_argument("--stderr", default=None)
    diagnose_p.add_argument("--archive-dir", required=True)
    diagnose_p.add_argument("--exit-code", type=int, default=None)
    diagnose_p.add_argument("--cli-version", default=None)
    diagnose_p.set_defaults(func=_cmd_o5_diagnose_parser)
    for name, help_text in (("build-rules-review-packet", "write an unverified rules review packet"), ("build-team-permission-review-packet", "write hashed pending team-permission review material")):
        packet_p = o5_sub.add_parser(name, help=help_text)
        _add_o5_registry_arg(packet_p)
        packet_p.add_argument("--output-dir", required=True)
        packet_p.set_defaults(func=_cmd_o5_review_packets)
    benchmark_p = o5_sub.add_parser("build-benchmark", help="write a deterministic ready-or-blocked O5 benchmark manifest")
    _add_o5_registry_arg(benchmark_p)
    benchmark_p.add_argument("--output", required=True)
    benchmark_p.set_defaults(func=_cmd_o5_build_benchmark)
    for name in ("probe-capabilities", "activate-permitted-sources", "validate-agents", "classify-active-decks", "build-policy-packs", "build-opponent-population", "evaluate-population", "report-activation"):
        blocked_p = o5_sub.add_parser(name, help="report the permission-aware O5 activation state")
        _add_o5_registry_arg(blocked_p)
        blocked_p.set_defaults(func=_cmd_o5_blocked_activation)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except (CliError, RunStateError, ConfigError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


__all__ = ["CliError", "build_parser", "main"]
