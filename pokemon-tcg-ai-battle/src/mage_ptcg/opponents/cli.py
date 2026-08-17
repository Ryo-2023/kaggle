"""Command-line interface for the stable O6 opponent facade."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from .core import (LocalArtifactStore, OpponentError, PopulationLoader, PopulationRef, TeamBranchCollector,
                   build_agent_runtime_bundle, build_population, collect_public_inbox, load_team_permission_policy,
                   resolve_team_permission, run_fresh_client_smoke, validate_native_record)
from .public_source import (PermissionReviewRequiredError, check_public_source_permissions, import_public_source_corpus,
                             inspect_public_source, list_public_sources, verify_public_source_metadata)


EXIT_OK, EXIT_INPUT, EXIT_BLOCKED, EXIT_ERROR = 0, 2, 3, 4


def _dump(value: Any, as_json: bool) -> None:
    if as_json:
        print(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    else:
        if isinstance(value, dict):
            for key, item in value.items(): print(f"{key}: {item}")
        else: print(value)


def _records(path: Path) -> list[dict[str, Any]]:
    source = path / "team_records.json"
    if not source.exists(): raise OpponentError("run sync-team-branches first")
    value = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(value, list): raise OpponentError("team records are corrupt")
    return value


def _root(args: argparse.Namespace) -> Path:
    return Path(args.output_dir).resolve()


def command_sync(args: argparse.Namespace) -> dict[str, Any]:
    root = _root(args); collector = TeamBranchCollector(args.repo)
    policy = load_team_permission_policy(args.permission_policy)
    prior_path = root / "team_records.json"; prior = {}
    if prior_path.exists():
        prior = {row["snapshot"]["source_locator"]: row["snapshot"] for row in _records(root)}
    try:
        remote_url = subprocess.check_output(["git", "config", "--get", "remote.origin.url"], cwd=args.repo, text=True).strip()
        repository_name = Path(remote_url.removesuffix(".git").replace(":", "/")).name
    except (OSError, subprocess.CalledProcessError):
        repository_name = Path(args.repo).resolve().name
    records = []
    for branch, sha in collector.discover().items():
        permission = resolve_team_permission(policy, repository_name=repository_name, remote="origin", branch=branch, commit_sha=sha)
        records.append(collector.normalize(collector.snapshot(branch, sha, prior=prior.get(branch), permission=permission)))
    result = {"schema_version": "o6-team-sync-report-v1", "discovered": len(records), "new": sum(row["snapshot"]["changed_since_prior"] is None for row in records),
              "changed": sum(row["snapshot"]["changed_since_prior"] is True for row in records), "unchanged": sum(row["snapshot"]["changed_since_prior"] is False for row in records),
              "policy_id": policy["policy_id"], "policy_hash": policy["policy_hash"],
              "branches": [{"branch": row["snapshot"]["source_locator"], "commit": row["snapshot"]["commit_sha"], "snapshot_id": row["snapshot"]["source_snapshot_id"], "state": row["state"], "permission": row["snapshot"]["permission_evidence"]} for row in records]}
    if not args.dry_run:
        root.mkdir(parents=True, exist_ok=True); (root / "team_records.json").write_text(json.dumps(records, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        (root / "team_sync_report.json").write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return result


def command_build(args: argparse.Namespace) -> dict[str, Any]:
    root = _root(args)
    policy = load_team_permission_policy(args.permission_policy)
    # Runtime bundles are built *before* the manifest so their own bytes can
    # participate in population_identity_hash (see
    # compute_runtime_bundle_registry_hash): otherwise swapping in a smaller
    # bundle for the same VALIDATED sources would silently keep the old
    # population_id, and LocalArtifactStore.publish()'s idempotent-republish
    # path would never actually write the new bundle.
    runtime_files: dict[str, bytes] = {}
    if not args.skip_runtime_bundle:
        collector = TeamBranchCollector(args.repo)
        rows = {row["agent"]["agent_id"]: row for row in _records(root) if row.get("state") == "VALIDATED"}
        for row in rows.values():
            runtime_files.update(build_agent_runtime_bundle(collector, row, scratch_root=root / "runtime-scratch"))
    manifest, payload = build_population(_records(root), permission_policy_hash=policy["policy_hash"], display_name=args.display_name,
                                          approval_status="PENDING_REVIEW", runtime_files=runtime_files)
    population_id = manifest["population_id"]
    result = {"manifest": manifest, "payload": payload, "runtime_files": {k: v.hex() for k, v in runtime_files.items()}}
    if not args.dry_run:
        output = root / "builds" / population_id; output.mkdir(parents=True, exist_ok=True)
        (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        (output / "payload.json").write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        for relpath, data in runtime_files.items():
            target = output / relpath; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(data)
    return {"population_id": population_id, "population_identity_hash": manifest["population_identity_hash"], "manifest_hash": manifest["manifest_hash"],
            "opponent_count": len(payload["opponent_specs.json"]), "runtime_bundle_files": len(runtime_files), "approval_status": manifest["approval_status"]}


def command_validate(args: argparse.Namespace) -> dict[str, Any]:
    root = _root(args); collector = TeamBranchCollector(args.repo)
    validated: list[dict[str, Any]] = []
    for row in _records(root):
        validated.append(validate_native_record(collector, row, scratch_root=root / "isolated-runtime", timeout_seconds=args.timeout_seconds))
        # A long or hostile later source must not erase completed evidence for
        # earlier pinned sources.  This is an atomic checkpoint, not approval.
        if not args.dry_run:
            (root / "team_records.json").write_text(json.dumps(validated, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    states: dict[str, int] = {}
    for row in validated: states[row["state"]] = states.get(row["state"], 0) + 1
    if not args.dry_run:
        (root / "validation_summary.json").write_text(json.dumps({"records": validated, "states": states}, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return {"validation": "COMPLETE", "records": len(validated), "states": states,
            "runtime_execution": "ISOLATED_CABT_SMOKE", "network_isolation": "NETWORK_ISOLATION_UNAVAILABLE"}


def _load_build(root: Path, population: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, bytes]]:
    directory = root / "builds" / population
    if not directory.exists(): raise OpponentError(f"no local build for population {population!r}; run build-registry first")
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    payload = json.loads((directory / "payload.json").read_text(encoding="utf-8"))
    runtime_dir = directory / "runtime"
    runtime_files = {str(path.relative_to(directory).as_posix()): path.read_bytes() for path in sorted(runtime_dir.rglob("*")) if path.is_file()} if runtime_dir.exists() else {}
    return manifest, payload, runtime_files


def command_publish(args: argparse.Namespace) -> dict[str, Any]:
    manifest, payload, runtime_files = _load_build(_root(args), args.population)
    if not args.approve:
        raise OpponentError("publish is blocked: explicit --approve is required after human review")
    manifest = dict(manifest); manifest["approval_status"] = "APPROVED"
    from mage_ptcg.competition_intelligence.canonical import digest
    semantic = {k: v for k, v in manifest.items() if k not in {"created_at", "manifest_hash", "display_name"}}
    manifest["manifest_hash"] = digest(semantic, domain="o6-population")
    path = LocalArtifactStore(args.artifact_store).publish(manifest, payload, approved=True, runtime_files=runtime_files)
    return {"published": str(path), "population_id": manifest["population_id"], "population_identity_hash": manifest["population_identity_hash"],
            "manifest_hash": manifest["manifest_hash"], "runtime_bundle_files": len(runtime_files)}


def command_list_populations(args: argparse.Namespace) -> dict[str, Any]:
    root = Path(args.artifact_store); index_path = root / "store_index.json"
    if index_path.exists():
        return {"populations": json.loads(index_path.read_text(encoding="utf-8"))}
    snapshots = root / "snapshots"
    return {"populations": {path.name: {} for path in sorted(snapshots.iterdir())} if snapshots.exists() else {}}


def _cache_dir(args: argparse.Namespace) -> Path:
    return Path(getattr(args, "cache_dir", None) or args.output_dir).resolve()


def command_fetch(args: argparse.Namespace) -> dict[str, Any]:
    cache_dir = _cache_dir(args)
    path = LocalArtifactStore(args.artifact_store).fetch_to_cache(args.population, cache_dir, verify_hashes=True)
    return {"path": str(path), "cache_dir": str(cache_dir), "verified": True, "offline": bool(args.offline)}


def _registry(args: argparse.Namespace):
    return PopulationLoader.load(PopulationRef(args.population), artifact_store=str(_cache_dir(args)), verify_hashes=True)


def command_list(args: argparse.Namespace) -> dict[str, Any]: return {"opponents": _registry(args).list()}
def command_inspect(args: argparse.Namespace) -> dict[str, Any]: return _registry(args).inspect(args.opponent_id)


def command_smoke(args: argparse.Namespace) -> dict[str, Any]:
    registry = _registry(args)
    registry.build(args.opponent_id, seed=args.seed)  # raises OpponentError unless the opponent is approved for execution
    agent_id = registry.inspect(args.opponent_id)["agent_id"]
    cache_dir = _cache_dir(args)
    population_dir = LocalArtifactStore(cache_dir).fetch(args.population, verify_hashes=True)
    return run_fresh_client_smoke(population_dir, agent_id, scratch_root=cache_dir / "smoke-scratch", timeout_seconds=args.timeout_seconds)


def command_evaluate(args: argparse.Namespace) -> dict[str, Any]:
    if not args.candidate_entrypoint: raise OpponentError("--candidate-entrypoint is required")
    registry = _registry(args)
    return {"status": "BLOCKED_RUNTIME_APPROVAL", "candidate_entrypoint": args.candidate_entrypoint, "population": registry.manifest["population_id"], "opponents": len(registry.list())}


def command_public(args: argparse.Namespace) -> dict[str, Any]:
    evidence = collect_public_inbox(args.inbox)
    return {"collector": "LocalPublicEvidenceInboxCollector", "records": len(evidence), "network": "OFFLINE", "evidence": evidence}


def command_export(args: argparse.Namespace) -> dict[str, Any]:
    path = LocalArtifactStore(args.artifact_store).export_bundle(args.population, args.destination)
    return {"exported": str(path), "population_id": args.population}


def command_public_source_import(args: argparse.Namespace) -> dict[str, Any]:
    return import_public_source_corpus(corpus_root=args.corpus, output_dir=_root(args), dry_run=args.dry_run)


def command_public_source_list(args: argparse.Namespace) -> dict[str, Any]:
    return {"sources": list_public_sources(output_dir=_root(args))}


def command_public_source_inspect(args: argparse.Namespace) -> dict[str, Any]:
    return inspect_public_source(output_dir=_root(args), source_id=args.source_id)


def command_public_source_verify_metadata(args: argparse.Namespace) -> dict[str, Any]:
    return verify_public_source_metadata(output_dir=_root(args))


def command_public_source_check_permissions(args: argparse.Namespace) -> dict[str, Any]:
    return check_public_source_permissions(output_dir=_root(args))


def command_verify_league_trajectories(args: argparse.Namespace) -> dict[str, Any]:
    """Dispatch to the independent trajectory verifier in its own subprocess.

    Deliberately a subprocess call, not an import: the verifier
    (mage_ptcg.opponents.independent_trajectory_verifier) must never share a
    Python process with League runner / runtime digest code, or "independent"
    recomputation would just be a shared function called from two call
    sites (see O6-AUD-002 remediation).
    """
    completed = subprocess.run(
        [sys.executable, "-m", "mage_ptcg.opponents.independent_trajectory_verifier", "--evidence", args.evidence, "--json"],
        capture_output=True, text=True, check=False,
    )
    if completed.returncode not in (0, 1):
        raise OpponentError(f"independent verifier crashed: {completed.stderr[-500:]}")
    return json.loads(completed.stdout)


def parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true"); common.add_argument("--dry-run", action="store_true")
    common.add_argument("--output-dir", default="artifacts/o6-opponents"); common.add_argument("--artifact-store", default="artifacts/o6-opponent-store")
    common.add_argument("--cache-dir", default=None, help="isolated fresh-client cache; defaults to --output-dir")
    common.add_argument("--offline", action="store_true"); common.add_argument("--resume", action="store_true")
    result = argparse.ArgumentParser(prog="python -m mage_ptcg.opponents"); sub = result.add_subparsers(dest="command", required=True)
    item = sub.add_parser("sync-team-branches", parents=[common]); item.add_argument("--repo", default="."); item.add_argument("--permission-policy", default="configs/opponents/permissions/pokemon_team_agents_internal_v1.yaml"); item.set_defaults(handler=command_sync)
    item = sub.add_parser("ingest-public", parents=[common]); item.add_argument("--inbox", default="data/public-opponent-inbox"); item.set_defaults(handler=command_public)
    item = sub.add_parser("build-registry", parents=[common]); item.add_argument("--repo", default="."); item.add_argument("--permission-policy", default="configs/opponents/permissions/pokemon_team_agents_internal_v1.yaml"); item.add_argument("--display-name", default=None); item.add_argument("--skip-runtime-bundle", action="store_true"); item.set_defaults(handler=command_build)
    item = sub.add_parser("validate", parents=[common]); item.add_argument("--repo", default="."); item.add_argument("--timeout-seconds", type=float, default=70.0); item.set_defaults(handler=command_validate)
    item = sub.add_parser("publish", parents=[common]); item.add_argument("--population", required=True); item.add_argument("--approve", action="store_true"); item.set_defaults(handler=command_publish)
    item = sub.add_parser("list-populations", parents=[common]); item.set_defaults(handler=command_list_populations)
    item = sub.add_parser("fetch", parents=[common]); item.add_argument("population"); item.set_defaults(handler=command_fetch)
    item = sub.add_parser("list", parents=[common]); item.add_argument("--population", required=True); item.set_defaults(handler=command_list)
    item = sub.add_parser("inspect", parents=[common]); item.add_argument("opponent_id"); item.add_argument("--population", required=True); item.set_defaults(handler=command_inspect)
    item = sub.add_parser("smoke", parents=[common]); item.add_argument("opponent_id"); item.add_argument("--population", required=True); item.add_argument("--seed", type=int, default=93001); item.add_argument("--timeout-seconds", type=float, default=90.0); item.set_defaults(handler=command_smoke)
    item = sub.add_parser("evaluate", parents=[common]); item.add_argument("--candidate-entrypoint", required=True); item.add_argument("--population", required=True); item.set_defaults(handler=command_evaluate)
    item = sub.add_parser("export", parents=[common]); item.add_argument("population"); item.add_argument("--destination", required=True); item.set_defaults(handler=command_export)
    item = sub.add_parser("verify-league-trajectories", help="independently recompute raw public League trajectory digests (O6-AUD-002)")
    item.add_argument("--evidence", required=True); item.add_argument("--json", action="store_true"); item.set_defaults(handler=command_verify_league_trajectories)
    public_source_common = argparse.ArgumentParser(add_help=False)
    public_source_common.add_argument("--json", action="store_true"); public_source_common.add_argument("--dry-run", action="store_true")
    public_source_common.add_argument("--output-dir", default="artifacts/o6-public-sources", help="separate namespace from --output-dir used by Team commands")
    item = sub.add_parser("public-source", help="hardened metadata-only Public Opponent Source intake (Phase B)")
    public_source_sub = item.add_subparsers(dest="public_source_command", required=True)
    sub_item = public_source_sub.add_parser("import", parents=[public_source_common]); sub_item.add_argument("--corpus", required=True, help="path to a Public Source Corpus root (Repository Snapshot directory)"); sub_item.set_defaults(handler=command_public_source_import)
    sub_item = public_source_sub.add_parser("list", parents=[public_source_common]); sub_item.set_defaults(handler=command_public_source_list)
    sub_item = public_source_sub.add_parser("inspect", parents=[public_source_common]); sub_item.add_argument("source_id"); sub_item.set_defaults(handler=command_public_source_inspect)
    sub_item = public_source_sub.add_parser("verify-metadata", parents=[public_source_common]); sub_item.set_defaults(handler=command_public_source_verify_metadata)
    sub_item = public_source_sub.add_parser("check-permissions", parents=[public_source_common]); sub_item.set_defaults(handler=command_public_source_check_permissions)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        outcome = args.handler(args); _dump(outcome, args.json); return EXIT_OK
    except PermissionReviewRequiredError as exc:
        _dump({"error": str(exc), "exit_code": exc.exit_code}, getattr(args, "json", False)); return exc.exit_code
    except OpponentError as exc:
        _dump({"error": str(exc), "exit_code": EXIT_BLOCKED}, getattr(args, "json", False)); return EXIT_BLOCKED
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        _dump({"error": str(exc), "exit_code": EXIT_ERROR}, getattr(args, "json", False)); return EXIT_ERROR
