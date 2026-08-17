"""Safe, immutable opponent-population acquisition primitives for O6.

This module deliberately builds on the O5 canonical JSON/deck helpers while
keeping unknown branch code out of the importer process.  A discovered branch
is data until a separately reviewed permission policy permits an adapter run.
"""
from __future__ import annotations

import fcntl
import hashlib
import fnmatch
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping

import yaml

from mage_ptcg.competition_intelligence.atomic_io import atomic_write_bytes, atomic_write_json
from mage_ptcg.competition_intelligence.canonical import canonical_json_bytes, digest, sha256_hex
from mage_ptcg.competition_intelligence.o5_registry import canonical_deck_hash, parse_exact_deck_text
from mage_ptcg.knowledge.compatibility import runtime_cabt_version

from .errors import OpponentError
from .runtime_closure import build_runtime_closure, build_runtime_contract


SCHEMA_VERSION = "o6-opponent-population-v1"
SOURCE_SCHEMA_VERSION = "o6-source-snapshot-v1"
POPULATION_SCHEMA_VERSION = "o6-population-manifest-v1"
USAGE_SCOPES = ("evaluation", "training_data_generation", "strategy_analysis", "team_redistribution", "public_redistribution", "submission_bundle")
STATES = ("DISCOVERED", "SNAPSHOTTED", "NORMALIZED", "CLASSIFIED", "BUILDABLE", "VALIDATED", "APPROVED", "PUBLISHED", "DEPRECATED", "REVOKED", "QUARANTINED_UNSAFE_CODE", "BLOCKED_PERMISSION", "BLOCKED_RULESET", "BLOCKED_DEPENDENCY", "INVALID_DECK", "UNSUPPORTED_RUNTIME", "INSUFFICIENT_EVIDENCE")
TERMINAL_STATES = set(STATES[8:])
_DANGERS = {
    "network": r"\b(socket|requests|urllib|httpx|aiohttp)\b",
    "subprocess": r"\b(subprocess|os\.system|Popen)\b",
    "dynamic_import": r"\b(__import__|importlib\.)",
    "unsafe_deserialization": r"\b(pickle|yaml\.load)\b",
    "eval_exec": r"\b(eval|exec)\s*\(",
    "credential_environment": r"\b(os\.environ|getenv)\b.*(?:TOKEN|KEY|SECRET|KAGGLE)",
    "absolute_private_path": r"(?:/home/|/Users/|C:\\\\Users\\\\)",
}


PERMISSION_SCHEMA_VERSION = "team-source-policy-v1"


def _bool_map(value: Any, *, name: str) -> dict[str, bool]:
    if not isinstance(value, Mapping):
        raise OpponentError(f"permission policy {name} must be an object")
    if any(type(flag) is not bool for flag in value.values()):
        raise OpponentError(f"permission policy {name} values must be booleans")
    return {scope: bool(value.get(scope, False)) for scope in USAGE_SCOPES}


def load_team_permission_policy(path: str | Path) -> dict[str, Any]:
    """Load the reviewed namespace policy without treating it as validation.

    The content hash is over canonical parsed content, so YAML presentation
    changes do not alter the policy identity.  Future schema versions are
    rejected instead of being interpreted optimistically.
    """
    source = Path(path)
    try:
        raw = yaml.safe_load(source.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise OpponentError(f"cannot load permission policy: {source}") from exc
    if not isinstance(raw, Mapping):
        raise OpponentError("permission policy must be a mapping")
    if raw.get("schema_version") != PERMISSION_SCHEMA_VERSION:
        raise OpponentError("unsupported permission policy schema_version")
    if raw.get("status") not in {"approved", "revoked"}:
        raise OpponentError("permission policy status must be approved or revoked")
    if not isinstance(raw.get("policy_id"), str) or not raw["policy_id"]:
        raise OpponentError("permission policy policy_id is required")
    match = raw.get("source_match")
    if not isinstance(match, Mapping) or not isinstance(match.get("repository_name"), str):
        raise OpponentError("permission policy source_match.repository_name is required")
    patterns = match.get("branch_globs")
    if not isinstance(patterns, list) or not patterns or not all(isinstance(item, str) and item for item in patterns):
        raise OpponentError("permission policy source_match.branch_globs is required")
    remote = match.get("remote", "origin")
    if not isinstance(remote, str) or not remote:
        raise OpponentError("permission policy source_match.remote must be a string")
    allowed = _bool_map(raw.get("allowed"), name="allowed")
    prohibited = _bool_map(raw.get("prohibited"), name="prohibited")
    if any(allowed[scope] and prohibited[scope] for scope in USAGE_SCOPES):
        raise OpponentError("permission policy cannot both allow and prohibit a scope")
    overrides = raw.get("item_overrides", {})
    if not isinstance(overrides, Mapping):
        raise OpponentError("permission policy item_overrides must be an object")
    for branch, item in overrides.items():
        if not isinstance(branch, str) or not isinstance(item, Mapping):
            raise OpponentError("permission policy item override is malformed")
        decision = item.get("decision")
        if decision not in {"allow", "deny"}:
            raise OpponentError("permission policy item override decision must be allow or deny")
    canonical = json.loads(canonical_json_bytes(raw).decode("utf-8"))
    return {
        "path": str(source), "content": canonical,
        "policy_id": raw["policy_id"], "policy_hash": _semantic_hash(canonical, "o6-team-permission-policy"),
        "status": raw["status"], "repository_name": match["repository_name"], "remote": remote,
        "branch_globs": tuple(patterns), "allowed": allowed, "prohibited": prohibited,
        "item_overrides": dict(overrides),
    }


def resolve_team_permission(policy: Mapping[str, Any], *, repository_name: str, remote: str, branch: str, commit_sha: str) -> dict[str, Any]:
    """Return a source-specific, auditable permission decision.

    ``commit_sha`` intentionally does not appear in the match predicate: every
    commit must still pass technical validation, and the evidence records the
    exact SHA to prevent a branch-level approval becoming an activation grant.
    """
    if not re.fullmatch(r"[0-9a-f]{40}", commit_sha):
        raise OpponentError("permission resolution requires a pinned commit SHA")
    normalized = branch.removeprefix(f"{remote}/")
    scopes = {scope: False for scope in USAGE_SCOPES}
    reason = "no matching namespace policy"
    matched = (policy.get("repository_name") == repository_name and policy.get("remote") == remote
               and any(fnmatch.fnmatchcase(normalized, pattern) for pattern in policy.get("branch_globs", ())))
    override = policy.get("item_overrides", {}).get(normalized) or policy.get("item_overrides", {}).get(branch)
    if matched and policy.get("status") == "approved":
        scopes = {scope: bool(policy.get("allowed", {}).get(scope, False)) and not bool(policy.get("prohibited", {}).get(scope, False)) for scope in USAGE_SCOPES}
        reason = "matched approved namespace policy"
    elif matched and policy.get("status") == "revoked":
        reason = "matched revoked namespace policy"
    if override:
        if override.get("decision") == "deny":
            scopes = {scope: False for scope in USAGE_SCOPES}; reason = "item-level denial overrides namespace policy"
        elif override.get("decision") == "allow" and matched and policy.get("status") == "approved":
            extra_allowed = _bool_map(override.get("allowed", {}), name="item override allowed")
            extra_prohibited = _bool_map(override.get("prohibited", {}), name="item override prohibited")
            scopes = {scope: (scopes[scope] or extra_allowed[scope]) and not extra_prohibited[scope] and not bool(policy.get("prohibited", {}).get(scope, False)) for scope in USAGE_SCOPES}
            reason = "matched approved namespace policy with item override"
    # This scope is categorically not a source-permission grant in O6.
    scopes["submission_bundle"] = False
    return {
        "source_branch": branch, "pinned_commit": commit_sha, "matched_policy_id": policy.get("policy_id") if matched else None,
        "policy_hash": policy.get("policy_hash") if matched else None, "allowed_scopes": [scope for scope, yes in scopes.items() if yes],
        "prohibited_scopes": [scope for scope in USAGE_SCOPES if not scopes[scope]], "permission_decision": "APPROVED" if scopes["evaluation"] else "DENIED",
        "reason": reason, "revalidation_required": True,
    }


def _semantic_hash(value: Any, domain: str) -> str:
    """Hash any canonical JSON value; registries are ordered lists, too."""
    return digest(value, domain=domain)


def _safe_relpath(value: str) -> str:
    if "\\" in value:
        raise OpponentError(f"unsafe source path (backslash separator): {value!r}")
    if re.match(r"^[A-Za-z]:", value):
        raise OpponentError(f"unsafe source path (Windows drive path): {value!r}")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or str(path) in {"", "."}:
        raise OpponentError(f"unsafe source path: {value!r}")
    return path.as_posix()


def _git(repo: Path, args: list[str]) -> str:
    completed = subprocess.run(["git", *args], cwd=repo, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if completed.returncode:
        raise OpponentError(f"git read failed: {' '.join(args[:2])}: {completed.stderr.strip()}")
    return completed.stdout


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_remote_file(repo: Path, commit: str, path: str) -> bytes:
    _safe_relpath(path)
    completed = subprocess.run(["git", "show", f"{commit}:{path}"], cwd=repo, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if completed.returncode:
        raise OpponentError(f"cannot read pinned source path {path!r}")
    return completed.stdout


def _tree(repo: Path, commit: str) -> list[dict[str, Any]]:
    raw = _git(repo, ["ls-tree", "-rl", "-z", commit])
    result: list[dict[str, Any]] = []
    for entry in raw.split("\0"):
        if not entry:
            continue
        metadata, sep, path = entry.partition("\t")
        if not sep:
            raise OpponentError("malformed git tree entry")
        parts = metadata.split()
        if len(parts) != 4:
            raise OpponentError("malformed git tree metadata")
        mode, kind, object_id, size = parts
        result.append({"path": _safe_relpath(path), "mode": mode, "kind": kind, "object_id": object_id, "size": int(size) if size != "-" else None})
    return sorted(result, key=lambda item: item["path"])


def deck_record(cards: Iterable[int] | None, *, source_lineage: list[str], observed_cards: list[int] | None = None, ruleset_version: str = "unknown") -> dict[str, Any]:
    values = list(cards or [])
    exact = len(values) == 60 and all(type(card) is int and card > 0 for card in values)
    normalized = sorted(values) if exact else []
    record: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION, "ruleset_version": ruleset_version,
        "exact_or_partial": "EXACT" if exact else "PARTIAL",
        "normalized_card_multiset": normalized if exact else None,
        "observed_cards": sorted(observed_cards if observed_cards is not None else values),
        "unknown_slots": 0 if exact else max(0, 60 - len(values)), "archetype": "UNKNOWN", "variant": "UNKNOWN",
        "legality": "UNVERIFIED" if exact else "PARTIAL_NOT_SUBMITTABLE", "source_lineage": sorted(source_lineage),
        "confidence": "HIGH" if exact else "LOW", "verification_status": "NOT_RUN",
    }
    record["deck_hash"] = canonical_deck_hash(values, card_pool_version=ruleset_version) if exact else None
    record["deck_id"] = _semantic_hash({k: v for k, v in record.items() if k not in {"deck_id", "legality", "verification_status"}}, "o6-deck")
    return record


def _inventory_find(paths: set[str], names: Iterable[str]) -> str | None:
    for name in names:
        if name in paths:
            return name
    return None


@dataclass(frozen=True)
class NamespacePolicy:
    source_namespace: str = "origin/agents/*"
    allowed: tuple[str, ...] = ("evaluation", "strategy_analysis", "team_redistribution")
    review_required: tuple[str, ...] = ("training_data_generation",)
    prohibited: tuple[str, ...] = ("public_redistribution", "submission_bundle")

    def usage(self, *, reviewed: bool = False) -> dict[str, bool]:
        result = {scope: False for scope in USAGE_SCOPES}
        for scope in self.allowed:
            result[scope] = reviewed
        for scope in self.review_required:
            result[scope] = reviewed
        return result

    def as_dict(self) -> dict[str, Any]:
        return {"schema_version": "o6-namespace-policy-v1", "source_namespace": self.source_namespace,
                "allowed": {scope: True for scope in self.allowed},
                "review_required": {scope: True for scope in self.review_required},
                "prohibited": {scope: True for scope in self.prohibited}}


class RegistryStateMachine:
    """Small explicit state machine; automation cannot self-approve sources."""
    _NEXT = {
        "DISCOVERED": {"SNAPSHOTTED", "QUARANTINED_UNSAFE_CODE", "BLOCKED_PERMISSION"},
        "SNAPSHOTTED": {"NORMALIZED", "QUARANTINED_UNSAFE_CODE", "BLOCKED_PERMISSION"},
        "NORMALIZED": {"CLASSIFIED", "INVALID_DECK", "BLOCKED_RULESET", "BLOCKED_PERMISSION"},
        "CLASSIFIED": {"BUILDABLE", "INSUFFICIENT_EVIDENCE", "BLOCKED_PERMISSION"},
        "BUILDABLE": {"VALIDATED", "BLOCKED_DEPENDENCY", "UNSUPPORTED_RUNTIME", "QUARANTINED_UNSAFE_CODE"},
        "VALIDATED": {"DEPRECATED", "REVOKED"},
        "APPROVED": {"PUBLISHED", "DEPRECATED", "REVOKED"},
        "PUBLISHED": {"DEPRECATED", "REVOKED"},
    }
    def transition(self, current: str, target: str, *, explicit_review: bool = False) -> str:
        if current not in STATES or target not in STATES: raise OpponentError("unknown registry state")
        if target == "APPROVED":
            if current != "VALIDATED" or not explicit_review: raise OpponentError("only explicit review may approve a validated source")
            return target
        if target not in self._NEXT.get(current, set()): raise OpponentError(f"invalid registry transition: {current} -> {target}")
        return target


class NativeAgentAdapter:
    """Explicitly-authorized, one-shot subprocess adapter for pinned snapshots.

    It is intentionally not a sandbox claim: no network namespace is created.
    The result reports that limitation so callers can apply a stricter runner.
    """
    def __init__(self, *, timeout_seconds: float = 2.0, max_output_bytes: int = 65_536):
        self.timeout_seconds, self.max_output_bytes = timeout_seconds, max_output_bytes
    def invoke(self, snapshot_root: str | Path, entrypoint: str, observation: Mapping[str, Any], *, configuration: Mapping[str, Any] | None = None, approved: bool = False) -> dict[str, Any]:
        if not approved: raise OpponentError("native adapter requires explicit reviewed approval")
        module_path, sep, attr = entrypoint.partition(":")
        if not sep or not attr: raise OpponentError("entrypoint must be relative_file.py:callable")
        root = Path(snapshot_root).resolve(); source = (root / _safe_relpath(module_path)).resolve()
        if root not in source.parents or source.suffix != ".py" or not source.is_file(): raise OpponentError("entrypoint escapes snapshot root")
        harness = """import importlib.util,inspect,json,os,sys
os.environ['HOME'] = sys.argv[3]
p=sys.argv[1]; name='o6_snapshot_agent'; s=importlib.util.spec_from_file_location(name,p); m=importlib.util.module_from_spec(s); s.loader.exec_module(m)
f=getattr(m,sys.argv[2]); payload=json.loads(sys.stdin.read()); out=f(payload['observation'], payload.get('configuration')) if len(inspect.signature(f).parameters) >= 2 else f(payload['observation'])
print(json.dumps({'selection':out},separators=(',',':')))
"""
        environment = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(root), "PYTHONDONTWRITEBYTECODE": "1", "HOME": os.environ.get("HOME", "")}
        isolated_home = tempfile.mkdtemp(prefix="o6-agent-home-")
        try:
            completed = subprocess.run([sys.executable, "-c", harness, str(source), attr, isolated_home], cwd=root, input=json.dumps({"observation": observation, "configuration": configuration}), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment, timeout=self.timeout_seconds, check=False)
        except subprocess.TimeoutExpired:
            return {"status": "TIMEOUT", "network_isolation": "NETWORK_ISOLATION_UNAVAILABLE"}
        finally:
            shutil.rmtree(isolated_home, ignore_errors=True)
        if len(completed.stdout.encode()) > self.max_output_bytes or len(completed.stderr.encode()) > self.max_output_bytes:
            return {"status": "STDIO_OVERFLOW", "network_isolation": "NETWORK_ISOLATION_UNAVAILABLE"}
        if completed.returncode:
            missing = re.search(r"ModuleNotFoundError: No module named ['\"]([^'\"]+)", completed.stderr)
            return {"status": "BLOCKED_DEPENDENCY" if missing else "PROCESS_ERROR", "missing_dependency": missing.group(1) if missing else None,
                    "network_isolation": "NETWORK_ISOLATION_UNAVAILABLE"}
        try: value = json.loads(completed.stdout)
        except json.JSONDecodeError: return {"status": "INVALID_PROTOCOL", "network_isolation": "NETWORK_ISOLATION_UNAVAILABLE"}
        selection = value.get("selection") if isinstance(value, dict) else None
        if not isinstance(selection, list) or any(type(item) is not int for item in selection): return {"status": "INVALID_SELECTION", "network_isolation": "NETWORK_ISOLATION_UNAVAILABLE"}
        return {"status": "OK", "selection": selection, "network_isolation": "NETWORK_ISOLATION_UNAVAILABLE"}


class TeamBranchCollector:
    """Read-only collector for pinned ``origin/agents/*`` refs.

    It uses only git object reads; it neither checks out nor imports a remote
    branch.  Snapshot contents are inventory hashes, not executable copies.
    """
    def __init__(self, repo: str | Path, *, max_files: int = 2_000, max_file_bytes: int = 2_000_000, max_total_bytes: int = 20_000_000):
        self.repo, self.max_files, self.max_file_bytes, self.max_total_bytes = Path(repo), max_files, max_file_bytes, max_total_bytes

    def discover(self) -> dict[str, str]:
        text = _git(self.repo, ["for-each-ref", "--format=%(refname:short) %(objectname)", "refs/remotes/origin/agents/"])
        result = {}
        for line in text.splitlines():
            name, sha = line.split(maxsplit=1)
            if re.fullmatch(r"origin/agents/[A-Za-z0-9._/-]+", name) and re.fullmatch(r"[0-9a-f]{40}", sha):
                result[name] = sha
        return dict(sorted(result.items()))

    def snapshot(self, branch: str, commit: str, *, prior: Mapping[str, Any] | None = None, permission: Mapping[str, Any] | None = None) -> dict[str, Any]:
        if not re.fullmatch(r"origin/agents/[A-Za-z0-9._/-]+", branch) or not re.fullmatch(r"[0-9a-f]{40}", commit):
            raise OpponentError("branch and commit must be pinned origin/agents refs")
        inventory = _tree(self.repo, commit)
        warnings: list[str] = ["NETWORK_ISOLATION_UNAVAILABLE: static acquisition only; no OS network sandbox claim"]
        total = sum(int(item["size"] or 0) for item in inventory if item["kind"] == "blob")
        if len(inventory) > self.max_files: warnings.append("OVERSIZED_FILE_COUNT")
        if total > self.max_total_bytes: warnings.append("OVERSIZED_TOTAL")
        for item in inventory:
            if item["mode"] == "120000": warnings.append(f"SYMLINK_QUARANTINED:{item['path']}")
            if item["kind"] == "commit": warnings.append(f"SUBMODULE_NOT_FETCHED:{item['path']}")
            if int(item["size"] or 0) > self.max_file_bytes: warnings.append(f"OVERSIZED_FILE:{item['path']}")
        paths = {item["path"] for item in inventory}
        deck_path = _inventory_find(paths, ("deck.csv", "deck.txt"))
        agent_path = _inventory_find(paths, ("agent.py", "main.py"))
        findings = self._scan(commit, [p for p in paths if p.endswith(".py")])
        candidate_manifest = _inventory_find(paths, ("opponent-source.yaml", "opponent-source.yml", "opponent-source.json"))
        content = {"commit_sha": commit, "inventory": inventory, "scan": findings, "deck_path": deck_path, "agent_path": agent_path, "candidate_manifest": candidate_manifest}
        permission = dict(permission or {})
        approved_scopes = set(permission.get("allowed_scopes", ()))
        snapshot = {"schema_version": SOURCE_SCHEMA_VERSION, "source_type": "TEAM_BRANCH", "source_locator": branch,
                    "immutable_ref": commit, "branch_name": branch.removeprefix("origin/"), "commit_sha": commit,
                    "retrieved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "content_hash": _semantic_hash(content, "o6-source-snapshot"),
                    "visibility": "TEAM_SHARED", "usage_scopes": {scope: scope in approved_scopes for scope in USAGE_SCOPES}, "permission_evidence": permission,
                    "owner": "team-branch-reviewed" if approved_scopes else "team-branch-unreviewed",
                    "collector_version": "o6-team-collector-v1", "parser_version": "o6-normalizer-v1", "inventory": inventory,
                    "warnings": sorted(set(warnings)), "scan": findings, "legacy_discovery": {"deck_path": deck_path, "agent_path": agent_path, "candidate_manifest": candidate_manifest},
                    "changed_since_prior": None if prior is None else prior.get("content_hash") != _semantic_hash(content, "o6-source-snapshot")}
        snapshot["source_snapshot_id"] = _semantic_hash({k: v for k, v in snapshot.items() if k not in {"source_snapshot_id", "retrieved_at", "changed_since_prior"}}, "o6-source-id")
        return snapshot

    def _scan(self, commit: str, python_paths: Iterable[str]) -> dict[str, list[str]]:
        findings = {name: [] for name in _DANGERS}
        for path in sorted(python_paths):
            try: text = _read_remote_file(self.repo, commit, path).decode("utf-8", "replace")
            except OpponentError: continue
            for name, pattern in _DANGERS.items():
                if re.search(pattern, text, flags=re.IGNORECASE): findings[name].append(path)
        return {key: value for key, value in findings.items() if value}

    def normalize(self, snapshot: Mapping[str, Any]) -> dict[str, Any]:
        commit = str(snapshot["commit_sha"]); lineage = [str(snapshot["source_snapshot_id"])]
        discovered = dict(snapshot.get("legacy_discovery", {})); deck_path = discovered.get("deck_path"); agent_path = discovered.get("agent_path")
        cards = parse_exact_deck_text(_read_remote_file(self.repo, commit, str(deck_path)).decode("utf-8", "replace")) if deck_path else None
        deck = deck_record(cards, source_lineage=lineage)
        implementation_hash = _sha256(_read_remote_file(self.repo, commit, str(agent_path))) if agent_path else None
        agent = {"schema_version": SCHEMA_VERSION, "implementation_hash": implementation_hash, "config_hash": None,
                 "entrypoint": f"{agent_path}:agent" if agent_path else None, "supported_decks": [deck["deck_id"]] if deck_path else [],
                 "runtime_dependencies": [], "statefulness": "UNKNOWN", "seed_behavior": "UNKNOWN", "fallback_behavior": "UNKNOWN",
                 "score_evidence": [], "source_lineage": lineage, "compatibility_status": "STATIC_ONLY"}
        agent["agent_id"] = _semantic_hash({k: v for k, v in agent.items() if k != "agent_id"}, "o6-agent")
        strategy = {"schema_version": SCHEMA_VERSION, "strategy_evidence_id": _semantic_hash({"source_lineage": lineage, "kind": "UNKNOWN"}, "o6-strategy"),
                    "opening_priorities": [], "phase_transitions": [], "resource_preservation": [], "target_selection": [], "search_policy": [],
                    "evolution_policy": [], "energy_policy": [], "recovery": [], "endgame": [], "matchup_notes": [],
                    "evidence_kind": "UNKNOWN", "confidence": "LOW", "source_lineage": lineage}
        state = "BLOCKED_PERMISSION" if not bool(snapshot.get("usage_scopes", {}).get("evaluation")) else "NORMALIZED"
        if deck["exact_or_partial"] != "EXACT": state = "INVALID_DECK"
        if snapshot.get("scan"): state = "QUARANTINED_UNSAFE_CODE" if any(snapshot["scan"].get(k) for k in ("credential_environment", "eval_exec", "unsafe_deserialization")) else state
        return {"snapshot": dict(snapshot), "deck": deck, "agent": agent, "strategy": strategy, "state": state}

    def materialize(self, commit: str, destination: str | Path) -> Path:
        """Safely materialize a pinned Git tree for a disposable subprocess.

        This is deliberately only called after static and permission checks.
        The extracted source remains in an explicitly supplied non-Git path;
        it is never a submission artifact and no branch is checked out.
        """
        target = Path(destination)
        if target.exists() and any(target.iterdir()):
            raise OpponentError("snapshot materialization destination must be empty")
        target.mkdir(parents=True, exist_ok=True)
        inventory = _tree(self.repo, commit)
        for item in inventory:
            if item["kind"] != "blob" or item["mode"] == "120000":
                continue
            output = target / item["path"]
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(_read_remote_file(self.repo, commit, item["path"]))
            os.chmod(output, 0o700 if item["mode"] == "100755" else 0o600)
        return target


_MANIFEST_IDENTITY_EXCLUDED = {"created_at", "manifest_hash", "display_name"}


class LocalArtifactStore:
    """Content-addressed immutable population store with final-manifest publish.

    ``root`` is caller-supplied (CLI flag or config), never hardcoded; the
    store is safe to point at any durable, non-``/tmp`` filesystem path. All
    mutation is staged then moved into place with ``os.replace`` (atomic on
    the same filesystem), guarded by a per-population advisory file lock so
    two concurrent publishers of the same id cannot interleave.
    """
    def __init__(self, root: str | Path): self.root = Path(root)
    def _path(self, population_id: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9._-]+", population_id): raise OpponentError("unsafe population id")
        return self.root / "snapshots" / population_id
    def _manifest_hash(self, manifest: Mapping[str, Any]) -> str:
        semantic = {k: v for k, v in manifest.items() if k not in _MANIFEST_IDENTITY_EXCLUDED}
        return _semantic_hash(semantic, "o6-population")
    def publish(self, manifest: Mapping[str, Any], payload: Mapping[str, Any], *, approved: bool = False, runtime_files: Mapping[str, bytes] | None = None) -> Path:
        if not approved or manifest.get("approval_status") != "APPROVED": raise OpponentError("publish requires explicit APPROVED manifest")
        population_id = str(manifest.get("population_id", "")); identity_hash = str(manifest.get("population_identity_hash", ""))
        if not identity_hash or population_id != f"team-agents-v1-{identity_hash[:16]}":
            raise OpponentError("population_id must be derived from population_identity_hash; caller-assigned ids are rejected")
        expected = self._manifest_hash(manifest)
        if manifest.get("manifest_hash") != expected: raise OpponentError("population manifest hash mismatch")
        target = self._path(population_id)
        lock_dir = self.root / "locks"; lock_dir.mkdir(parents=True, exist_ok=True)
        with open(lock_dir / f"{population_id}.lock", "w") as lock_file:
            fcntl.flock(lock_file, fcntl.LOCK_EX)
            try:
                if target.exists():
                    existing = json.loads((target / "population_manifest.json").read_text(encoding="utf-8"))
                    if existing.get("manifest_hash") != expected: raise OpponentError("same population id has different content")
                    return target  # idempotent republish of identical content
                staging = self.root / "staging" / f"{population_id}.{os.getpid()}"
                staging.mkdir(parents=True, exist_ok=False)
                try:
                    for name, value in sorted(payload.items()):
                        if not re.fullmatch(r"[A-Za-z0-9._-]+\.json", name): raise OpponentError("unsafe payload name")
                        atomic_write_json(staging / name, value)
                    atomic_write_json(staging / "population_manifest.json", dict(manifest))
                    for relpath, data in sorted((runtime_files or {}).items()):
                        if not relpath.startswith("runtime/"): raise OpponentError("runtime bundle paths must be under runtime/")
                        output = staging / _safe_relpath(relpath)
                        output.parent.mkdir(parents=True, exist_ok=True); output.write_bytes(data)
                    bundle = staging / "bundle.tar.gz"
                    with tarfile.open(bundle, "w:gz") as archive:
                        for path in sorted(staging.glob("*.json")): archive.add(path, arcname=path.name, recursive=False)
                        runtime_dir = staging / "runtime"
                        if runtime_dir.exists():
                            for path in sorted(runtime_dir.rglob("*")):
                                if path.is_file(): archive.add(path, arcname=path.relative_to(staging).as_posix())
                    # Manifest is written to the bundle last: readers that see a
                    # complete bundle.tar.gz + validation_summary.json can trust
                    # the publish already committed the manifest content above.
                    atomic_write_json(staging / "validation_summary.json", {"bundle_sha256": sha256_hex(bundle.read_bytes()), "manifest_hash": expected})
                    target.parent.mkdir(parents=True, exist_ok=True); os.replace(staging, target)
                    index_path = self.root / "store_index.json"
                    index = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else {}
                    index[population_id] = {"manifest_hash": expected, "display_name": manifest.get("display_name"),
                                             "published_at": manifest.get("created_at"), "opponent_count": len(manifest.get("opponent_ids", []))}
                    atomic_write_json(index_path, index)
                    alias = self.root / "aliases" / "latest-approved.json"; atomic_write_json(alias, {"population_id": population_id, "manifest_hash": expected})
                    return target
                finally:
                    if staging.exists(): shutil.rmtree(staging)
            finally:
                fcntl.flock(lock_file, fcntl.LOCK_UN)
    def fetch(self, population_id: str, *, verify_hashes: bool = True) -> Path:
        path = self._path(population_id); manifest_path = path / "population_manifest.json"
        if not manifest_path.exists(): raise OpponentError("population is not available offline")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if verify_hashes:
            if manifest.get("manifest_hash") != self._manifest_hash(manifest): raise OpponentError("corrupt population manifest")
            summary = json.loads((path / "validation_summary.json").read_text(encoding="utf-8"))
            if summary.get("bundle_sha256") != sha256_hex((path / "bundle.tar.gz").read_bytes()): raise OpponentError("corrupt population bundle")
        return path
    def fetch_to_cache(self, population_id: str, cache_dir: str | Path, *, verify_hashes: bool = True) -> Path:
        """Copy a published population into an isolated cache dir and re-verify there.

        This is what a fresh client calls: the cache is a separate directory
        tree from the store (no shared mutable state), and hashes are
        recomputed against the copied bytes, not the store's own copy, so a
        corrupted or tampered cache is rejected rather than silently trusted.
        """
        source = self.fetch(population_id, verify_hashes=verify_hashes)
        # Same on-disk layout as the store itself (``snapshots/<id>``) so a
        # cache directory can be opened with ``LocalArtifactStore(cache_dir)``
        # and served offline through the same fetch()/list()/inspect() path.
        destination = Path(cache_dir) / "snapshots" / population_id
        if destination.exists(): shutil.rmtree(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, destination)
        manifest = json.loads((destination / "population_manifest.json").read_text(encoding="utf-8"))
        if verify_hashes:
            if manifest.get("manifest_hash") != self._manifest_hash(manifest): raise OpponentError("corrupt cache: manifest hash mismatch after copy")
            summary = json.loads((destination / "validation_summary.json").read_text(encoding="utf-8"))
            if summary.get("bundle_sha256") != sha256_hex((destination / "bundle.tar.gz").read_bytes()): raise OpponentError("corrupt cache: bundle hash mismatch after copy")
        return destination
    def export_bundle(self, population_id: str, destination: str | Path) -> Path:
        """Write a single portable ``.tar.gz`` export of a published population."""
        source = self.fetch(population_id, verify_hashes=True)
        out = Path(destination)
        out.parent.mkdir(parents=True, exist_ok=True)
        with tarfile.open(out, "w:gz") as archive:
            for path in sorted(source.rglob("*")):
                if path.is_file(): archive.add(path, arcname=str(Path(population_id) / path.relative_to(source)))
        return out


_CABT_SMOKE_HARNESS = """import importlib.util, inspect, json, os, sys
os.environ['HOME'] = sys.argv[4]
from kaggle_environments import make
root, rel, name = sys.argv[1:4]
sys.path.insert(0, root)
spec = importlib.util.spec_from_file_location('o6_native_agent', os.path.join(root, rel))
module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
agent = getattr(module, name)
def invoke(observation, configuration=None):
    return agent(observation, configuration) if len(inspect.signature(agent).parameters) >= 2 else agent(observation)
deck = invoke({'logs': [], 'current': None, 'select': None})
first = invoke({'logs': [], 'current': None, 'select': None})
environment = make('cabt', configuration={'decks': [deck, deck]})
environment.run([invoke, invoke])
states = [str(state.status) for state in environment.state]
print('O6_VALIDATION=' + json.dumps({'deck_length': len(deck) if isinstance(deck, list) else None, 'deck_replay_equal': deck == first, 'states': states, 'steps': len(environment.steps)}, separators=(',', ':')))
"""


def _run_isolated_cabt_smoke(source_dir: str | Path, module_path: str, callable_name: str, *, timeout_seconds: float) -> dict[str, Any]:
    """Run one isolated-subprocess cabt smoke and return raw, uninterpreted evidence.

    Shared by ``validate_native_record`` (source materialized from pinned git
    objects) and ``run_fresh_client_smoke`` (source extracted from a hash-
    verified runtime bundle); neither caller's permission/state semantics
    live here.
    """
    started = time.monotonic()
    environment = {"PATH": os.environ.get("PATH", ""), "PYTHONDONTWRITEBYTECODE": "1", "HOME": os.environ.get("HOME", "")}
    isolated_home = tempfile.mkdtemp(prefix="o6-cabt-home-")
    try:
        completed = subprocess.run([sys.executable, "-c", _CABT_SMOKE_HARNESS, str(source_dir), module_path, callable_name, isolated_home],
                                    cwd=source_dir, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment, timeout=timeout_seconds, check=False)
    except subprocess.TimeoutExpired:
        return {"outcome": "TIMEOUT", "runtime_seconds": round(time.monotonic() - started, 3)}
    finally:
        shutil.rmtree(isolated_home, ignore_errors=True)
    runtime_seconds = round(time.monotonic() - started, 3)
    lines = [line for line in completed.stdout.splitlines() if line.startswith("O6_VALIDATION=")]
    if completed.returncode or not lines:
        missing = re.search(r"ModuleNotFoundError: No module named ['\"]([^'\"]+)", completed.stderr)
        tail = completed.stderr.strip().splitlines()[-1:] or completed.stdout.strip().splitlines()[-1:]
        return {"outcome": "BLOCKED_DEPENDENCY" if missing else "RUNTIME_ERROR", "missing_dependency": missing.group(1) if missing else None,
                "runtime_error": tail[0][:240] if tail else "no validation sentinel", "runtime_seconds": runtime_seconds}
    return {"outcome": "RAN", "runtime": json.loads(lines[-1].partition("=")[2]), "runtime_seconds": runtime_seconds}


def validate_native_record(collector: TeamBranchCollector, record: Mapping[str, Any], *, scratch_root: str | Path, timeout_seconds: float = 70.0) -> dict[str, Any]:
    """Run the approved static and isolated-runtime gates for one pinned source.

    The subprocess uses the official local ``cabt`` environment and a
    materialized Git-object snapshot.  It never checks out the branch, never
    installs dependencies, and returns only compact machine-readable evidence.
    This validates a source commit, not a moving branch name.
    """
    result = json.loads(json.dumps(record))
    snapshot, deck, agent = result["snapshot"], result["deck"], result["agent"]
    permission = dict(snapshot.get("permission_evidence", {}))
    evidence: dict[str, Any] = {
        "source_branch": snapshot["source_locator"], "pinned_commit": snapshot["commit_sha"],
        "matched_policy_id": permission.get("matched_policy_id"), "policy_hash": permission.get("policy_hash"),
        "allowed_scopes": permission.get("allowed_scopes", []), "prohibited_scopes": permission.get("prohibited_scopes", []),
        "permission_decision": permission.get("permission_decision", "DENIED"), "technical_validation_decision": "NOT_RUN",
        "activation_decision": "NOT_ACTIVATED",
    }
    if permission.get("permission_decision") != "APPROVED":
        result["state"] = "BLOCKED_PERMISSION"; evidence["technical_validation_decision"] = "SKIPPED_PERMISSION"
        result["validation"] = evidence; return result
    if deck.get("exact_or_partial") != "EXACT":
        result["state"] = "INVALID_DECK"; evidence["technical_validation_decision"] = "FAIL_INVALID_DECK"
        result["validation"] = evidence; return result
    entrypoint = agent.get("entrypoint")
    if not isinstance(entrypoint, str):
        result["state"] = "UNSUPPORTED_RUNTIME"; evidence["technical_validation_decision"] = "FAIL_NO_ENTRYPOINT"
        result["validation"] = evidence; return result
    entrypoint_path = entrypoint.partition(":")[0]
    entry_scan = {kind: paths for kind, paths in snapshot.get("scan", {}).items() if entrypoint_path in paths}
    evidence["static_capability_inventory"] = snapshot.get("scan", {})
    if any(entry_scan.get(kind) for kind in ("credential_environment", "eval_exec", "unsafe_deserialization")):
        result["state"] = "QUARANTINED_UNSAFE_CODE"; evidence["technical_validation_decision"] = "FAIL_UNSAFE_ENTRYPOINT"
        result["validation"] = evidence; return result
    source_dir = Path(scratch_root) / snapshot["source_snapshot_id"]
    if source_dir.exists(): shutil.rmtree(source_dir)
    collector.materialize(snapshot["commit_sha"], source_dir)
    module_path, _, callable_name = entrypoint.partition(":")
    try:
        outcome = _run_isolated_cabt_smoke(source_dir, module_path, callable_name, timeout_seconds=timeout_seconds)
    finally:
        shutil.rmtree(source_dir, ignore_errors=True)
    evidence["runtime_seconds"] = outcome.get("runtime_seconds")
    if outcome["outcome"] == "TIMEOUT":
        result["state"] = "UNSUPPORTED_RUNTIME"; evidence["technical_validation_decision"] = "FAIL_TIMEOUT"
    elif outcome["outcome"] in {"BLOCKED_DEPENDENCY", "RUNTIME_ERROR"}:
        result["state"] = "BLOCKED_DEPENDENCY" if outcome["outcome"] == "BLOCKED_DEPENDENCY" else "UNSUPPORTED_RUNTIME"
        evidence.update({"technical_validation_decision": "FAIL_MISSING_DEPENDENCY" if outcome["outcome"] == "BLOCKED_DEPENDENCY" else "FAIL_RUNTIME",
                          "missing_dependency": outcome.get("missing_dependency"), "runtime_error": outcome.get("runtime_error")})
    else:
        runtime = outcome["runtime"]; evidence["runtime"] = runtime
        if runtime["deck_length"] != 60:
            result["state"] = "INVALID_DECK"; evidence["technical_validation_decision"] = "FAIL_RUNTIME_DECK"
        elif runtime["states"] != ["DONE", "DONE"]:
            result["state"] = "UNSUPPORTED_RUNTIME"; evidence["technical_validation_decision"] = "FAIL_CABT"
        else:
            result["state"] = "VALIDATED"; evidence.update({"technical_validation_decision": "PASS", "activation_decision": "VALIDATED", "legal_action_validation": "CABT_SMOKE_PASS", "state_leakage": "PROCESS_ISOLATED", "determinism": "DETERMINISTIC" if runtime["deck_replay_equal"] else "NONDETERMINISTIC"})
    result["agent"]["compatibility_status"] = "VALIDATED_NATIVE" if result["state"] == "VALIDATED" else result["state"]
    result["validation"] = evidence
    return result


def _validation_identity(validation: Mapping[str, Any]) -> dict[str, Any]:
    """Deterministic subset of validation evidence used for population identity.

    Excludes wall-clock timing (``runtime_seconds``) and free-text error tails
    so that re-running validation of the same source at the same commit
    reproduces the same population identity.
    """
    keys = ("permission_decision", "matched_policy_id", "policy_hash", "allowed_scopes", "prohibited_scopes",
            "technical_validation_decision", "activation_decision", "legal_action_validation", "state_leakage", "determinism")
    return {key: validation.get(key) for key in keys}


POPULATION_IDENTITY_SCHEMA_VERSION = "o6-population-identity-v2"


def compute_runtime_bundle_registry_hash(runtime_files: Mapping[str, bytes] | None) -> str | None:
    """Content hash over every published runtime-bundle file's own bytes.

    O6-AUD-001 remediation note: before this function existed,
    ``population_identity_hash`` was computed only from abstract registry
    hashes (deck/agent/strategy/validation), never from the runtime bundle
    bytes actually published under ``runtime/<agent_id>/**``.  That meant
    swapping a "copy everything" bundle for the minimized allow-list closure
    below left the ``population_id`` unchanged -- and because
    :meth:`LocalArtifactStore.publish` treats a matching ``manifest_hash`` as
    an idempotent no-op, republishing under the old id would have silently
    kept serving the old, unminimized bundle forever.  Returns ``None`` (a
    stable, hashed sentinel) when no runtime bundle is being published at
    all, e.g. ``--skip-runtime-bundle``.
    """
    if not runtime_files:
        return None
    return _semantic_hash({relpath: _sha256(data) for relpath, data in runtime_files.items()}, "o6-runtime-bundle-registry")


def compute_population_identity(*, opponent_ids: Iterable[str], source_snapshot_ids: Iterable[str], source_commit_shas: Iterable[str],
                                 deck_registry_hash: str, agent_registry_hash: str, strategy_registry_hash: str, permission_policy_hash: str,
                                 validation_summary_hash: str, adapter_version: str, runtime_contract_version: str, ruleset_version: str,
                                 cabt_version: str, selection_policy: str, build_config_hash: str,
                                 runtime_bundle_registry_hash: str | None = None) -> tuple[str, str, dict[str, Any]]:
    """Derive a Population's identity purely from immutable semantic content.

    ``created_at``, any local/temporary path, and any caller-chosen display
    name never participate here -- identical content always yields the same
    ``population_id`` regardless of member order or who built it, and any
    change to source commits, permission policy, adapter/runtime/ruleset/
    cabt version, or the published runtime bundle's own bytes
    (``runtime_bundle_registry_hash``) yields a different one.
    """
    semantic = {
        "schema_version": POPULATION_IDENTITY_SCHEMA_VERSION,
        "opponent_ids": sorted(opponent_ids),
        "source_snapshot_ids": sorted(source_snapshot_ids),
        "source_commit_shas": sorted(source_commit_shas),
        "deck_registry_hash": deck_registry_hash,
        "agent_registry_hash": agent_registry_hash,
        "strategy_registry_hash": strategy_registry_hash,
        "permission_policy_hash": permission_policy_hash,
        "validation_summary_hash": validation_summary_hash,
        "adapter_version": adapter_version,
        "runtime_contract_version": runtime_contract_version,
        "ruleset_version": ruleset_version,
        "cabt_version": cabt_version,
        "selection_policy": selection_policy,
        "build_config_hash": build_config_hash,
        "runtime_bundle_registry_hash": runtime_bundle_registry_hash,
    }
    identity_hash = _semantic_hash(semantic, "o6-population-identity")
    return f"team-agents-v1-{identity_hash[:16]}", identity_hash, semantic


def build_population(records: Iterable[Mapping[str, Any]], *, permission_policy_hash: str, display_name: str | None = None,
                      adapter_version: str = "o6-native-subprocess-v1", runtime_contract_version: str = "o6-runtime-bundle-v1",
                      selection_policy: str = "all-validated-pinned-team-branches", ruleset_version: str = "unknown",
                      cabt_version: str | None = None, approval_status: str = "PENDING_REVIEW",
                      runtime_files: Mapping[str, bytes] | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build a Population manifest whose identity is derived purely from content.

    There is no ``population_id`` parameter: a caller cannot assign or
    override identity.  ``display_name`` is stored for human readability only
    and is excluded from both the identity hash and ``manifest_hash``.
    ``runtime_files`` (when the caller is about to publish a runtime bundle)
    makes the published bundle bytes themselves part of the identity via
    :func:`compute_runtime_bundle_registry_hash` -- see its docstring for why
    this matters.
    """
    cabt_version = cabt_version or runtime_cabt_version()
    # A policy match or a partial runtime result never enters a published
    # population.  Keep those records in validation evidence, but exclude
    # them from the execution registry until this exact SHA is VALIDATED.
    rows = sorted((dict(row) for row in records if row.get("state") == "VALIDATED"), key=lambda row: str(row["snapshot"]["source_snapshot_id"]))
    if not rows:
        raise OpponentError("cannot build a population without VALIDATED sources")
    opponent_specs = []
    for row in rows:
        deck, agent, snapshot = row["deck"], row["agent"], row["snapshot"]
        spec = {"deck_id": deck["deck_id"], "agent_id": agent["agent_id"], "adapter_version": adapter_version, "pilot_profile": "NATIVE",
                "runtime_contract": "SUBPROCESS_REQUIRED", "required_artifacts": [snapshot["source_snapshot_id"]], "permission_status": row["state"],
                "validation_status": row.get("validation", {}).get("technical_validation_decision", "NOT_RUN"), "determinism_status": row.get("validation", {}).get("determinism", "NOT_RUN")}
        spec["opponent_id"] = _semantic_hash(spec, "o6-opponent")
        opponent_specs.append(spec)
    payload = {"source_manifest.json": [row["snapshot"] for row in rows], "deck_registry.json": [row["deck"] for row in rows],
               "agent_registry.json": [row["agent"] for row in rows], "strategy_evidence.json": [row["strategy"] for row in rows], "opponent_specs.json": opponent_specs}
    deck_registry_hash = _semantic_hash(payload["deck_registry.json"], "o6-decks")
    agent_registry_hash = _semantic_hash(payload["agent_registry.json"], "o6-agents")
    strategy_registry_hash = _semantic_hash(payload["strategy_evidence.json"], "o6-strategy-registry")
    validation_summary_hash = _semantic_hash([_validation_identity(row.get("validation", {})) for row in rows], "o6-validation-summary")
    build_config_hash = _semantic_hash({"adapter_version": adapter_version, "runtime_contract_version": runtime_contract_version}, "o6-build")
    runtime_bundle_registry_hash = compute_runtime_bundle_registry_hash(runtime_files)
    population_id, identity_hash, _identity = compute_population_identity(
        opponent_ids=(item["opponent_id"] for item in opponent_specs),
        source_snapshot_ids=(row["snapshot"]["source_snapshot_id"] for row in rows),
        source_commit_shas=(row["snapshot"]["commit_sha"] for row in rows),
        deck_registry_hash=deck_registry_hash, agent_registry_hash=agent_registry_hash, strategy_registry_hash=strategy_registry_hash,
        permission_policy_hash=permission_policy_hash, validation_summary_hash=validation_summary_hash, adapter_version=adapter_version,
        runtime_contract_version=runtime_contract_version, ruleset_version=ruleset_version, cabt_version=cabt_version,
        selection_policy=selection_policy, build_config_hash=build_config_hash, runtime_bundle_registry_hash=runtime_bundle_registry_hash)
    semantic = {"population_id": population_id, "population_identity_hash": identity_hash, "population_version": "v1", "schema_version": POPULATION_SCHEMA_VERSION,
                "opponent_ids": sorted(item["opponent_id"] for item in opponent_specs), "source_snapshot_ids": sorted(row["snapshot"]["source_snapshot_id"] for row in rows),
                "source_commit_shas": sorted(row["snapshot"]["commit_sha"] for row in rows),
                "deck_registry_hash": deck_registry_hash, "agent_registry_hash": agent_registry_hash, "strategy_registry_hash": strategy_registry_hash,
                "permission_policy_hash": permission_policy_hash, "validation_summary_hash": validation_summary_hash, "selection_policy": selection_policy,
                "adapter_version": adapter_version, "runtime_contract_version": runtime_contract_version,
                "ruleset_version": ruleset_version, "cabt_version": cabt_version, "build_config_hash": build_config_hash,
                "runtime_bundle_registry_hash": runtime_bundle_registry_hash, "approval_status": approval_status}
    manifest = {**semantic, "display_name": display_name, "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "manifest_hash": _semantic_hash(semantic, "o6-population")}
    return manifest, payload


def build_agent_runtime_bundle(collector: TeamBranchCollector, row: Mapping[str, Any], *, scratch_root: str | Path, closure_timeout_seconds: float = 70.0) -> dict[str, bytes]:
    """Materialize one VALIDATED source into a hash-verifiable, minimized runtime payload.

    Returns a flat ``{relative_path: bytes}`` mapping rooted at
    ``runtime/<agent_id>/`` -- content-addressed, safe-relative-path-only,
    ready to be handed to :meth:`LocalArtifactStore.publish` as
    ``runtime_files``.  Only ever called for an already-VALIDATED row; it
    performs no permission or safety re-checks of its own.

    Unlike the O6-AUD-001 baseline (which copied every regular file in the
    pinned source tree), the ``source/**`` files bundled here are exactly
    :func:`runtime_closure.build_runtime_closure`'s allow-list closure: the
    entrypoint, its static+dynamic Python import closure, the declared deck
    artifact, and only the native binary actually ``dlopen``-ed on this
    build host -- docs/tests/report/experiments/data/other-OS-binaries are
    never included.  ``closure_report.json`` and ``runtime_contract.json``
    make that decision auditable without re-deriving it.
    """
    if row.get("state") != "VALIDATED":
        raise OpponentError("runtime bundle requires a VALIDATED source")
    snapshot, deck, agent, validation = row["snapshot"], row["deck"], row["agent"], row.get("validation", {})
    agent_id = str(agent["agent_id"])
    scratch = Path(scratch_root) / f"bundle-{agent_id}"
    if scratch.exists(): shutil.rmtree(scratch)
    collector.materialize(snapshot["commit_sha"], scratch)
    try:
        closure = build_runtime_closure(source_root=scratch, entrypoint=agent["entrypoint"], agent_id=agent_id,
                                         scratch_root=Path(scratch_root) / f"closure-trace-{agent_id}", timeout_seconds=closure_timeout_seconds)
        files: dict[str, bytes] = {f"runtime/{agent_id}/source/{rel}": data for rel, data in closure["files"].items()}
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    closure_report = closure["report"]
    adapter = {"schema_version": "o6-runtime-adapter-v1", "adapter_version": "o6-native-subprocess-v1", "runtime_contract": "SUBPROCESS_REQUIRED",
               "entrypoint": agent["entrypoint"], "invocation_protocol": "one isolated subprocess per game; stdin/stdout JSON handoff via the o6 cabt smoke harness",
               "isolation": {"HOME": "ISOLATED_TEMP_DIR_PER_PROCESS", "cwd": "BUNDLE_SOURCE_ROOT", "PYTHONPATH": "BUNDLE_SOURCE_ROOT",
                             "network_isolation": "NETWORK_ISOLATION_UNAVAILABLE"},
               "no_implicit_pip_install": True, "fail_closed_on_missing_dependency": True, "visibility": "TEAM_INTERNAL_ONLY",
               "usage_scope_reference": snapshot.get("usage_scopes", {})}
    cabt_version = runtime_cabt_version()
    runtime_contract = build_runtime_contract(python_version_required=platform.python_version(), kaggle_environments_version=cabt_version,
                                               cabt_version=cabt_version, required_host_packages=["kaggle_environments"])
    dependencies = {"schema_version": "o6-runtime-dependencies-v1", "python_version": platform.python_version(), "cabt_version": cabt_version,
                     "resolution": "RESOLVED_STDLIB_ONLY_PLUS_HOST_HARNESS" if not closure_report["unresolved_imports"]["unknown_third_party"] else "UNRESOLVED_THIRD_PARTY_IMPORTS_DETECTED",
                     "unresolved_third_party_imports": closure_report["unresolved_imports"]["unknown_third_party"],
                     "note": "the bundled agent code's own direct Python imports are stdlib-only per the runtime closure's import graph; executing any game still "
                             "requires a host-provided kaggle_environments 'cabt' environment (see runtime_contract.json). A module missing at launch fails "
                             "closed as BLOCKED_DEPENDENCY rather than installing anything."}
    runtime_manifest = {"schema_version": "o6-runtime-manifest-v1", "agent_id": agent_id, "opponent_source_snapshot_id": snapshot["source_snapshot_id"],
                         "pinned_commit": snapshot["commit_sha"], "source_branch": snapshot["source_locator"],
                         "permission_reference": {"matched_policy_id": validation.get("matched_policy_id"), "policy_hash": validation.get("policy_hash")},
                         "validation_evidence": _validation_identity(validation), "deck_id": deck["deck_id"]}
    files[f"runtime/{agent_id}/runtime_manifest.json"] = canonical_json_bytes(runtime_manifest)
    files[f"runtime/{agent_id}/adapter.json"] = canonical_json_bytes(adapter)
    files[f"runtime/{agent_id}/dependencies.json"] = canonical_json_bytes(dependencies)
    files[f"runtime/{agent_id}/runtime_contract.json"] = canonical_json_bytes(runtime_contract)
    files[f"runtime/{agent_id}/closure_report.json"] = canonical_json_bytes(closure_report)
    files[f"runtime/{agent_id}/deck/deck.json"] = canonical_json_bytes({"deck_id": deck["deck_id"], "cards": deck["normalized_card_multiset"]})
    file_hashes = {relpath: _sha256(data) for relpath, data in files.items()}
    hashes = {"schema_version": "o6-runtime-hashes-v1", "agent_id": agent_id, "files": file_hashes, "bundle_sha256": _semantic_hash(file_hashes, "o6-runtime-bundle")}
    files[f"runtime/{agent_id}/hashes.json"] = canonical_json_bytes(hashes)
    return files


def safe_extract_tar_gz(archive_path: str | Path, destination: str | Path, *, max_file_bytes: int | None = None,
                         max_total_bytes: int | None = None, max_files: int | None = None,
                         max_compression_ratio: float | None = None) -> list[str]:
    """Extract a population bundle, rejecting traversal, links, and non-regular members.

    Every member name is passed through :func:`_safe_relpath` (rejects
    absolute/Windows/backslash paths and ``..`` segments) before extraction,
    and any symlink, hardlink, device, or FIFO member aborts the whole
    extraction rather than being skipped silently. ``max_file_bytes``/
    ``max_total_bytes``/``max_files``/``max_compression_ratio`` are optional
    (``None`` preserves the original unbounded behaviour, used by the
    Population runtime-bundle path where sizes are already hash-pinned) --
    callers ingesting an untrusted external archive (e.g. a Public Source
    corpus package) should pass concrete limits to also guard against
    resource-exhaustion / zip-bomb-style archives.
    """
    destination = Path(destination); destination.mkdir(parents=True, exist_ok=True)
    extracted: list[str] = []
    with tarfile.open(archive_path, "r:gz") as archive:
        safe_members = []
        total_bytes = 0
        for member in archive.getmembers():
            if member.issym() or member.islnk(): raise OpponentError(f"unsafe archive member (link): {member.name}")
            if not (member.isfile() or member.isdir()): raise OpponentError(f"unsafe archive member type: {member.name}")
            member.name = _safe_relpath(member.name)
            if max_file_bytes is not None and member.isfile() and member.size > max_file_bytes:
                raise OpponentError(f"archive member exceeds max_file_bytes ({member.size} > {max_file_bytes}): {member.name}")
            if member.isfile():
                total_bytes += member.size
                if max_total_bytes is not None and total_bytes > max_total_bytes:
                    raise OpponentError(f"archive total uncompressed size exceeds max_total_bytes ({total_bytes} > {max_total_bytes})")
            safe_members.append(member)
            if member.isfile(): extracted.append(member.name)
        if max_files is not None and len(safe_members) > max_files:
            raise OpponentError(f"archive member count exceeds max_files ({len(safe_members)} > {max_files})")
        if max_compression_ratio is not None:
            compressed_bytes = Path(archive_path).stat().st_size
            ratio = total_bytes / max(compressed_bytes, 1)
            if ratio > max_compression_ratio:
                raise OpponentError(f"archive compression ratio exceeds max_compression_ratio ({ratio:.1f}x > {max_compression_ratio}x)")
        archive.extractall(destination, members=safe_members)
    return sorted(extracted)


def run_fresh_client_smoke(population_dir: str | Path, agent_id: str, *, scratch_root: str | Path, timeout_seconds: float = 90.0) -> dict[str, Any]:
    """Run one agent's isolated cabt smoke purely from a fetched, hash-verified bundle.

    No git repository, no ``origin/agents/*`` checkout, and no reference to the
    live O6 worktree source tree is used: everything the entrypoint needs
    comes from ``population_dir/bundle.tar.gz`` (a Population Snapshot already
    copied into an isolated cache by
    :meth:`LocalArtifactStore.fetch_to_cache`), and every extracted file is
    re-verified against that agent's own ``hashes.json`` before it is ever
    imported into a subprocess.
    """
    population_dir = Path(population_dir)
    bundle = population_dir / "bundle.tar.gz"
    if not bundle.exists(): raise OpponentError("population cache has no bundle.tar.gz; fetch first")
    extract_root = Path(scratch_root) / f"fresh-client-{agent_id}-{os.getpid()}-{int(time.time() * 1000)}"
    if extract_root.exists(): shutil.rmtree(extract_root)
    started_extract = time.monotonic()
    safe_extract_tar_gz(bundle, extract_root)
    extract_seconds = round(time.monotonic() - started_extract, 3)
    try:
        runtime_root = extract_root / "runtime" / agent_id
        hashes_path = runtime_root / "hashes.json"
        if not hashes_path.exists(): raise OpponentError(f"no runtime bundle for agent {agent_id!r} in this population")
        hashes = json.loads(hashes_path.read_text(encoding="utf-8"))
        for relpath, expected in hashes.get("files", {}).items():
            target = extract_root / relpath
            if not target.is_file() or _sha256(target.read_bytes()) != expected:
                raise OpponentError(f"runtime bundle hash mismatch, refusing to execute: {relpath}")
        runtime_manifest = json.loads((runtime_root / "runtime_manifest.json").read_text(encoding="utf-8"))
        adapter = json.loads((runtime_root / "adapter.json").read_text(encoding="utf-8"))
        module_path, _, callable_name = str(adapter["entrypoint"]).partition(":")
        outcome = _run_isolated_cabt_smoke(runtime_root / "source", module_path, callable_name, timeout_seconds=timeout_seconds)
    finally:
        shutil.rmtree(extract_root, ignore_errors=True)
    result = {"agent_id": agent_id, "pinned_commit": runtime_manifest["pinned_commit"], "source_branch": runtime_manifest["source_branch"],
              "bundle_hash_verified": True, "extract_seconds": extract_seconds, "files_extracted_and_verified": len(hashes.get("files", {})),
              "no_source_branch_checkout": True, "no_live_worktree_reference": True, "network_isolation": "NETWORK_ISOLATION_UNAVAILABLE", **outcome}
    if outcome["outcome"] == "RAN":
        runtime = outcome["runtime"]
        result["legal_action_validation"] = "CABT_SMOKE_PASS" if runtime.get("states") == ["DONE", "DONE"] and runtime.get("deck_length") == 60 else "FAIL"
        result["exit_status"] = "OK" if result["legal_action_validation"] == "CABT_SMOKE_PASS" else "FAIL"
    else:
        result["legal_action_validation"] = "NOT_RUN"; result["exit_status"] = outcome["outcome"]
    return result


class PopulationRef:
    def __init__(self, population_id: str): self.population_id = population_id

class PopulationLoader:
    @staticmethod
    def load(ref: PopulationRef, *, artifact_store: str | Path, verify_hashes: bool = True) -> "OpponentRegistry":
        path = LocalArtifactStore(artifact_store).fetch(ref.population_id, verify_hashes=verify_hashes)
        return OpponentRegistry(json.loads((path / "population_manifest.json").read_text(encoding="utf-8")), json.loads((path / "opponent_specs.json").read_text(encoding="utf-8")))

class OpponentRegistry:
    def __init__(self, manifest: Mapping[str, Any], specs: Iterable[Mapping[str, Any]]):
        self.manifest, self._specs = dict(manifest), {str(item["opponent_id"]): dict(item) for item in specs}
    def list(self) -> list[dict[str, Any]]: return [self._specs[key] for key in sorted(self._specs)]
    def inspect(self, opponent_id: str) -> dict[str, Any]:
        if opponent_id not in self._specs: raise OpponentError("unknown opponent")
        return self._specs[opponent_id]
    def build(self, opponent_id: str, *, seed: int) -> dict[str, Any]:
        spec = self.inspect(opponent_id)
        if spec["permission_status"] not in {"VALIDATED", "APPROVED"}: raise OpponentError("opponent is not approved for execution")
        return {"opponent_id": opponent_id, "seed": seed, "runtime_contract": spec["runtime_contract"]}


def collect_public_inbox(path: str | Path) -> list[dict[str, Any]]:
    """Read-only local public evidence inbox; each JSON object stays classified."""
    root = Path(path)
    if not root.exists(): return []
    records = []
    for source in sorted(root.glob("*.json")):
        value = json.loads(source.read_text(encoding="utf-8"))
        if not isinstance(value, dict): raise OpponentError(f"public inbox file is not an object: {source.name}")
        classification = value.get("classification", "OBSERVED_ONLY")
        if classification not in {"EXACT", "DECK_FAITHFUL", "BEHAVIORAL_SURROGATE", "OBSERVED_ONLY"}: raise OpponentError("unsupported public evidence classification")
        records.append({"source": source.name, "classification": classification, "content_hash": _semantic_hash(value, "o6-public-evidence"), "record": value})
    return records


class PublicGitRepositoryCollector:
    """Contract-only public Git collector; live network must be injected by a policy-approved transport."""
    def collect(self, locator: str, *, offline: bool = True) -> dict[str, Any]:
        return {"collector": type(self).__name__, "locator": locator, "status": "OFFLINE" if offline else "TRANSPORT_NOT_CONFIGURED"}


class PublicDeckEvidenceCollector(PublicGitRepositoryCollector):
    pass


class PublicStrategyDocumentCollector(PublicGitRepositoryCollector):
    pass


class LeaderboardSnapshotCollector(PublicGitRepositoryCollector):
    pass


class LocalPublicEvidenceInboxCollector:
    def collect(self, path: str | Path) -> list[dict[str, Any]]:
        return collect_public_inbox(path)
