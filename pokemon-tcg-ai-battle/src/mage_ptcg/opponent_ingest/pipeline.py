"""Read-only source discovery and safe candidate-registry construction.

This package intentionally never imports a discovered agent.  It records
external code as quarantined metadata until a separate reviewed adapter and
CABT validation path explicitly activates it.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping

import yaml

from mage_ptcg.competition_intelligence.atomic_io import atomic_write_json
from mage_ptcg.competition_intelligence.o5_registry import canonical_deck_hash

SCHEMA = "opponent-ingest-v1"
_DANGERS = {
    "secret": r"(?i)(api[_-]?key|secret|password|token)\s*[:=]",
    "network": r"\b(requests|urllib|httpx|aiohttp|socket)\b",
    "subprocess": r"\b(subprocess|os\.system|Popen)\b",
    "filesystem_write": r"\b(open\([^\n]*['\"](?:w|a|x)|write_text|write_bytes|unlink|rmtree)\b",
    "environment": r"\b(os\.environ|getenv)\b",
    "dynamic_execution": r"\b(eval|exec|__import__|importlib\.)\b",
}
_DECK_SUFFIXES = {".csv", ".json", ".yaml", ".yml", ".md", ".py", ".ipynb"}


class IngestError(RuntimeError):
    pass


def _digest(data: bytes | str) -> str:
    return hashlib.sha256(data.encode() if isinstance(data, str) else data).hexdigest()


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def _safe_rel(value: str) -> str:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "\\" in value:
        raise IngestError(f"unsafe path: {value!r}")
    return path.as_posix()


def _git(repo: Path, args: list[str]) -> str:
    completed = subprocess.run(["git", *args], cwd=repo, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if completed.returncode:
        raise IngestError(f"git read failed: {' '.join(args[:2])}: {completed.stderr.strip()}")
    return completed.stdout


def discover_git_refs(repo: Path) -> list[dict[str, Any]]:
    """Inventory refs through plumbing only; it never changes HEAD/worktree."""
    fmt = "%(refname)\t%(objectname)\t%(objecttype)"
    rows = []
    for line in _git(repo, ["for-each-ref", f"--format={fmt}"]).splitlines():
        ref, commit, kind = line.split("\t")
        if kind == "commit":
            rows.append({"ref": ref, "commit": commit, "kind": "git_ref"})
    head = _git(repo, ["rev-parse", "HEAD"]).strip()
    rows.append({"ref": "HEAD", "commit": head, "kind": "git_ref"})
    return sorted({(r["ref"], r["commit"]): r for r in rows}.values(), key=lambda x: (x["ref"], x["commit"]))


def _tree_paths(repo: Path, commit: str) -> list[tuple[str, str]]:
    raw = _git(repo, ["ls-tree", "-r", "-z", commit])
    result = []
    for entry in raw.split("\0"):
        if not entry:
            continue
        meta, _, path = entry.partition("\t")
        parts = meta.split()
        if len(parts) != 3 or parts[1] != "blob":
            continue
        result.append((_safe_rel(path), parts[2]))
    return result


def _extract_card_ids(text: str) -> list[int]:
    # Strictly accept a standalone 60-value sequence; do not guess card names.
    tokens = [x for x in re.split(r"[\s,]+", text.strip()) if x]
    if len(tokens) != 60 or not all(re.fullmatch(r"[1-9]\d*", x) for x in tokens):
        return []
    return [int(x) for x in tokens]


def normalize_deck_text(text: str, *, source_id: str, path: str, official_ids: set[int]) -> dict[str, Any]:
    cards = _extract_card_ids(text)
    if not cards:
        return {"source_id": source_id, "path": path, "eligibility": "INVALID_COUNT", "card_count": 0, "deck_digest": None}
    unresolved = sorted({card for card in cards if official_ids and card not in official_ids})
    digest = canonical_deck_hash(cards, card_pool_version="official-local")
    return {"source_id": source_id, "path": path, "eligibility": "CARD_ID_UNRESOLVED" if unresolved else "EXACT_60_VALID",
            "card_count": 60, "cards": sorted(cards), "duplicate_card_count": sum(n - 1 for n in Counter(cards).values() if n > 1),
            "unresolved_cards": unresolved, "ambiguous_cards": [], "unavailable_cards": [], "deck_digest": digest}


def classify_deck(row: Mapping[str, Any]) -> dict[str, Any]:
    cards = set(row.get("cards", []))
    # Anchors are evidence, never a forced single-family label.
    anchors = {"MEGA_LUCARIO_EX": {741, 742, 743}, "MEGA_ABOMASNOW_EX": {1147}, "ALAKAZAM": {13, 66}}
    families = sorted(name for name, ids in anchors.items() if ids & cards)
    primary = families[0] if len(families) == 1 else ("HYBRID" if families else "UNKNOWN")
    return {**dict(row), "primary_family": primary, "family_membership": families, "secondary_family": None,
            "family_confidence": "EVIDENCE_ANCHOR" if families else "UNKNOWN", "strategy_mixture": families or ["unknown"],
            "variant_membership": [], "mechanic_tags": [], "anchor_cards": sorted(cards & set().union(*anchors.values())) if anchors else [],
            "win_condition_core": [], "dependency_core": [], "engine_core": [], "energy_package": [], "tech_flex": [], "unknown_hybrid": primary in {"UNKNOWN", "HYBRID"}}


def audit_agent_text(text: str, *, source_id: str, path: str) -> dict[str, Any]:
    findings = {name: bool(re.search(pattern, text)) for name, pattern in _DANGERS.items()}
    try:
        tree = ast.parse(text)
        imports = sorted({alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom)) for alias in node.names})
    except SyntaxError:
        imports = ["SYNTAX_ERROR"]
        findings["syntax_error"] = True
    suspicious = sorted(name for name, yes in findings.items() if yes)
    status = "QUARANTINED" if suspicious else "CANDIDATE"
    return {"agent_id": f"agent-{_digest(source_id + ':' + path)[:16]}", "source_id": source_id, "path": path,
            "runtime_type": "PYTHON_SOURCE", "imports": imports, "static_findings": suspicious,
            "quarantine_reason": ",".join(suspicious) if suspicious else None, "activation_eligibility": "QUARANTINED" if suspicious else "MANUAL_REVIEW_REQUIRED",
            "runtime_fingerprint": _digest(text), "expected_signature": "unknown", "network_dependency": findings["network"], "filesystem_writes": findings["filesystem_write"]}


def _official_ids(repo: Path) -> set[int]:
    path = repo / "data/raw/EN_Card_Data.csv"
    if not path.exists():
        return set()
    return {int(x) for x in re.findall(r"(?m)^\s*(\d+)\s*,", path.read_text(encoding="utf-8", errors="ignore"))}


def _source_from_file(path: Path, root: Path, trust: str = "LOCAL_REPRODUCIBLE") -> dict[str, Any]:
    content = path.read_bytes()
    return {"source_id": f"local-{_digest(str(path.resolve()))[:16]}", "source_type": "manual_drop" if "incoming" in path.parts else "local_file",
            "source_url": None, "repository": str(root), "commit": None, "path": str(path.relative_to(root)), "content_digest": _digest(content),
            "license_usage_evidence": "local-only", "visibility": "local-only", "trust_class": trust, "fetch_status": "AVAILABLE"}


def _source_from_git(repo: Path, ref: str, commit: str, path: str, blob: str) -> dict[str, Any]:
    return {"source_id": f"git-{_digest(commit + ':' + path)[:16]}", "source_type": "git_ref", "source_url": ref, "repository": str(repo), "commit": commit, "path": path,
            "content_digest": blob, "license_usage_evidence": "local ref", "visibility": "local-only", "trust_class": "LOCAL_REPRODUCIBLE", "fetch_status": "AVAILABLE"}


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    data = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for row in rows)
    from mage_ptcg.competition_intelligence.atomic_io import atomic_write_bytes
    atomic_write_bytes(path, data.encode())


def run_ingestion(repo: Path, artifact_root: Path, config: Mapping[str, Any], *, mode: str = "incremental") -> dict[str, Any]:
    """Run discovery without network, Git mutation, imports, or activation."""
    artifact_root = artifact_root.resolve(); artifacts = artifact_root / "artifacts"; state_dir = artifact_root / "state"
    for directory in (artifacts, state_dir, artifact_root / "incoming/decks", artifact_root / "incoming/agents", artifact_root / "incoming/submissions", artifact_root / "incoming/notebooks", artifact_root / "incoming/metadata"):
        directory.mkdir(parents=True, exist_ok=True)
    lock = state_dir / "ingestion.lock"
    if lock.exists():
        try:
            lock_pid = int(lock.read_text(encoding="ascii").strip())
            os.kill(lock_pid, 0)
        except (ValueError, ProcessLookupError):
            lock.unlink()
        except PermissionError:
            raise IngestError("another ingestion run is active")
        else:
            raise IngestError("another ingestion run is active")
    lock.write_text(str(os.getpid()), encoding="ascii")
    try:
        prior = _read_json(state_dir / "source_watermarks.json", {})
        refs = discover_git_refs(repo)
        candidates: list[dict[str, Any]] = []
        for ref in refs:
            # The complete tree is inventoried by ``ls-tree``; only likely
            # deck/agent assets are content-read.  This keeps a large history
            # incremental rather than repeatedly decoding every source file.
            for path, blob in _tree_paths(repo, ref["commit"]):
                lower = path.lower()
                asset_path = (
                    path in {"main.py", "deck.csv"}
                    or "deck" in lower
                    or "agent" in lower
                    or "opponent" in lower
                    or path.startswith(("submissions/", "dist/"))
                )
                if asset_path and Path(path).suffix.lower() in _DECK_SUFFIXES:
                    candidates.append(_source_from_git(repo, ref["ref"], ref["commit"], path, blob))
        incoming = artifact_root / "incoming"
        for path in sorted(p for p in incoming.rglob("*") if p.is_file()):
            candidates.append(_source_from_file(path, artifact_root))
        sources = {row["source_id"]: row for row in candidates}
        changed = sorted(sources.values(), key=lambda x: x["source_id"]) if mode == "full" else [row for row in sources.values() if prior.get(row["source_id"]) != row["content_digest"]]
        decks, agents = [], []
        official_ids = _official_ids(repo)
        for source in sorted(changed, key=lambda x: x["source_id"]):
            suffix = Path(source["path"]).suffix.lower()
            if source["source_type"] == "git_ref":
                data = subprocess.run(["git", "show", f"{source['commit']}:{source['path']}"], cwd=repo, stdout=subprocess.PIPE, check=True).stdout
            else:
                data = (artifact_root / source["path"]).read_bytes()
            text = data.decode("utf-8", errors="ignore")
            if suffix in _DECK_SUFFIXES:
                deck = classify_deck(normalize_deck_text(text, source_id=source["source_id"], path=source["path"], official_ids=official_ids))
                decks.append(deck)
            if suffix == ".py":
                agents.append(audit_agent_text(text, source_id=source["source_id"], path=source["path"]))
        deck_registry_path = artifacts / "deck_asset_registry.jsonl"
        old_decks = [json.loads(x) for x in deck_registry_path.read_text(encoding="utf-8").splitlines() if x] if deck_registry_path.exists() else []
        all_decks = {f"{r.get('source_id')}:{r.get('path')}": r for r in old_decks}; all_decks.update({f"{r['source_id']}:{r['path']}": r for r in decks})
        agent_registry_path = artifacts / "agent_asset_registry.jsonl"
        old_agents = [json.loads(x) for x in agent_registry_path.read_text(encoding="utf-8").splitlines() if x] if agent_registry_path.exists() else []
        all_agents = {str(row["agent_id"]): row for row in old_agents}; all_agents.update({str(row["agent_id"]): row for row in agents})
        unique = {}
        for row in all_decks.values():
            if row.get("eligibility") == "EXACT_60_VALID": unique.setdefault(row["deck_digest"], row)
        bindings = []
        for deck in sorted(unique.values(), key=lambda x: x["deck_digest"]):
            bindings.append({"binding_id": f"binding-{_digest(deck['deck_digest'])[:16]}", "deck_id": deck["deck_digest"], "deck_digest": deck["deck_digest"], "agent_id": None,
                             "runtime_fingerprint": None, "family": deck["primary_family"], "variant": "UNKNOWN", "compatibility_evidence": "deck-only",
                             "required_cards_packages": [], "missing_dependencies": [], "binding_status": "RULE_V0_ONLY", "trust_class": "LOCAL_REPRODUCIBLE", "activation_eligibility": "NOT_ACTIVE"})
        registry = []
        for source in sorted(sources.values(), key=lambda x: x["source_id"]):
            previous = prior.get(source["source_id"]); registry.append({**source, "discovered_at": _now(), "last_checked_at": _now(), "previous_digest": previous, "changed_since_previous": previous != source["content_digest"], "quarantine_reason": None})
        _write_jsonl(artifacts / "deck_asset_registry.jsonl", sorted(all_decks.values(), key=lambda x: (x.get("deck_digest") or "", x["source_id"], x["path"])))
        _write_jsonl(artifacts / "agent_asset_registry.jsonl", sorted(all_agents.values(), key=lambda x: x["agent_id"]))
        _write_jsonl(artifacts / "deck_agent_binding_registry.jsonl", bindings)
        _write_jsonl(artifacts / "quarantine_registry.jsonl", [a for a in sorted(all_agents.values(), key=lambda x: x["agent_id"]) if a["activation_eligibility"] == "QUARANTINED"])
        atomic_write_json(artifacts / "source_registry.json", {"schema_version": SCHEMA, "sources": registry})
        atomic_write_json(artifacts / "family_registry.json", {"families": sorted({d["primary_family"] for d in unique.values() if d["primary_family"] not in {"UNKNOWN", "HYBRID"}})})
        sampling = config.get("sampling", {"rule_v0_max_fraction": 0.2, "team_native_fraction": 0.2, "family_specific_fraction": 0.6})
        candidate = {"schema_version": SCHEMA, "activation_policy": "CANDIDATE_ONLY_NO_AUTOPROMOTION", "entries": bindings, "sampling": sampling}
        atomic_write_json(artifacts / "candidate_population.json", candidate)
        report = {"schema_version": SCHEMA, "mode": mode, "scanned_local_refs": len(refs), "scanned_remote_refs": sum(r["ref"].startswith("refs/remotes/") for r in refs), "source_count": len(registry), "changed_source_count": len(changed), "new_deck_count": len(decks), "unique_exact_60_deck_count": len(unique), "discovered_agent_count": len(all_agents), "quarantined_agent_count": sum(a["activation_eligibility"] == "QUARANTINED" for a in all_agents.values()), "verified_native_binding_count": 0, "verified_family_binding_count": 0, "rule_v0_only_binding_count": len(bindings), "git_mutation": False, "active_promotion": False}
        atomic_write_json(artifacts / "source_change_report.json", report)
        atomic_write_json(artifacts / "family_coverage_report.json", {"family_count": len({d["primary_family"] for d in unique.values() if d["primary_family"] not in {"UNKNOWN", "HYBRID"}}), "report": report})
        atomic_write_json(artifacts / "population_sampling_report.json", {"sampling": sampling, "rule_v0_cap_enforced": float(sampling.get("rule_v0_max_fraction", 0.2)) <= 0.2})
        verdict = "READY_WITH_LIMITED_FAMILY_COVERAGE" if bindings else "ASSET_DISCOVERY_COMPLETE_NO_SAFE_ACTIVATIONS"
        atomic_write_json(artifacts / "validation_summary.json", {"legal_games": None, "reason": "no automatic execution of discovered code", "pass": True})
        atomic_write_json(artifacts / "final_readiness.json", {"verdict": verdict, "report": report, "reason": "all discovered external agents remain fail-closed until manual approval and CABT validation"})
        atomic_write_json(state_dir / "source_watermarks.json", {row["source_id"]: row["content_digest"] for row in registry})
        atomic_write_json(state_dir / "ingestion_state.json", {"schema_version": SCHEMA, "last_completed_at": _now(), "mode": mode})
        atomic_write_json(state_dir / "heartbeat.json", {"at": _now(), "status": "success", "changed_source_count": len(changed)})
        atomic_write_json(state_dir / "last_success.json", report)
        snapshot_id = _digest(json.dumps(report, sort_keys=True))[:16]; snapshot = artifact_root / "snapshots" / snapshot_id
        snapshot.mkdir(parents=True, exist_ok=True); atomic_write_json(snapshot / "manifest.json", {"snapshot_id": snapshot_id, "report": report})
        return {**report, "verdict": verdict, "snapshot_id": snapshot_id}
    except Exception as exc:
        state_dir.mkdir(parents=True, exist_ok=True); atomic_write_json(state_dir / "last_failure.json", {"at": _now(), "error": str(exc)[:500]}); raise
    finally:
        lock.unlink(missing_ok=True)
