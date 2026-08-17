"""Read-only intake of permitted internal ``agents/*`` branch snapshots.

The intake is deliberately narrower than the generic opponent discovery
pipeline.  A candidate is a root ``main.py`` and ``deck.csv`` read from the
same immutable commit.  No checkout or import is performed; the output is an
isolated, research-only pool that can be smoke-checked before a CABT runner is
allowed to use it.
"""

from __future__ import annotations

import ast
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from mage_ptcg.observability.cabt_trace import canonical_deck_sha256

from .pipeline import normalize_deck_text


FRESH_INTERNAL_META_SCHEMA_V1 = "meta-specialist-cg-fresh-internal-meta-intake-v1"
INTERNAL_SOURCE_V1 = "internal_agents"
LOCAL_EVAL_ONLY_V1 = "local_eval_only"
DEFAULT_REF_GLOB_V1 = "refs/remotes/origin/agents/*"
DEFAULT_EXCLUDED_REFS_V1 = frozenset({"refs/remotes/origin/agents/ono-cg-lethal-v1"})
_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_SHA64 = re.compile(r"^[0-9a-f]{64}$")
_SECRET = re.compile(r"(?i)(api[_-]?key|secret|password|token)\s*[:=]")
_NETWORK_TEXT = re.compile(r"(?i)\b(https?|ftp)://")
_NETWORK_IMPORTS = frozenset({"requests", "urllib", "httpx", "aiohttp", "socket"})
_SUBPROCESS_IMPORTS = frozenset({"subprocess", "pexpect"})
_DYNAMIC_IMPORTS = frozenset({"importlib", "ctypes"})
_TEXT_SUFFIXES = frozenset({".json", ".jsonl", ".md", ".yaml", ".yml", ".txt", ".csv", ".log"})
_MAX_SCAN_BYTES = 16 * 1024 * 1024


class FreshInternalMetaError(RuntimeError):
    """Raised when a source snapshot cannot be sealed safely."""


@dataclass(frozen=True, slots=True)
class FreshInternalMetaCandidate:
    """One policy/deck pair proven to originate from one branch commit."""

    candidate_id: str
    ref: str
    source_branch: str
    source_commit: str
    source_policy_sha256: str
    policy_sha256: str
    deck_bytes_sha256: str
    canonical_deck_hash: str
    imports: tuple[str, ...]
    source: str = INTERNAL_SOURCE_V1
    usage_boundary: str = LOCAL_EVAL_ONLY_V1
    localization_patch: str = "NONE"
    readonly_telemetry_patch: str = "NONE"

    def to_pool_row(self) -> dict[str, object]:
        # ``smoke_ok`` here means the immutable asset/preflight contract has
        # passed.  A CABT game smoke is a separate gate recorded by the caller
        # before policy CEM uses the batch.
        return {
            "canonical_deck_hash": self.canonical_deck_hash,
            "id": self.candidate_id,
            "mean_decision_ms": None,
            "policy_hash": self.policy_sha256,
            "source_policy_sha256": self.source_policy_sha256,
            "smoke_ok": True,
            "source": self.source,
            "source_branch": self.source_branch,
            "source_commit": self.source_commit,
            "usage_boundary": self.usage_boundary,
            "localization_patch": self.localization_patch,
            "readonly_telemetry_patch": self.readonly_telemetry_patch,
            "asset_preflight": "STATIC_AND_EXACT_60",
        }


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _canonical_json(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _write_new(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(path, flags, 0o644)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _write_json_new(path: Path, value: object) -> None:
    _write_new(path, _canonical_json(value))


def _git(repo: Path, args: Sequence[str], *, binary: bool = False) -> str | bytes:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise FreshInternalMetaError(f"git read failed ({' '.join(args[:3])}): {detail}")
    return completed.stdout if binary else completed.stdout.decode("utf-8")


def _ref_rows(repo: Path, ref_glob: str) -> list[tuple[str, str]]:
    raw = _git(repo, ["for-each-ref", f"--format=%(refname)\t%(objectname)\t%(objecttype)", ref_glob])
    rows: list[tuple[str, str]] = []
    for line in str(raw).splitlines():
        parts = line.split("\t")
        if len(parts) == 3 and parts[2] == "commit" and fnmatch.fnmatch(parts[0], ref_glob):
            rows.append((parts[0], parts[1]))
    return sorted(set(rows))


def _history_rows(repo: Path, ref_rows: Sequence[tuple[str, str]], history_depth: int) -> list[tuple[str, str]]:
    """Return first-parent historical snapshots for explicitly selected refs.

    This is intentionally opt-in.  The normal intake considers only the
    immutable ref heads; historical intake is a source-acquisition lane that
    may expose an older, otherwise unconsumed policy/deck pair.  First-parent
    traversal avoids silently importing unrelated merge-side snapshots.
    """

    if history_depth <= 0:
        return list(ref_rows)
    rows: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for ref, head in ref_rows:
        raw = _git(repo, ["rev-list", "--first-parent", f"--max-count={history_depth}", head])
        for commit in str(raw).splitlines():
            commit = commit.strip()
            if _SHA40.fullmatch(commit):
                item = (ref, commit)
                if item not in seen:
                    rows.append(item)
                    seen.add(item)
    return rows


def _tree_blob_ids(repo: Path, commit: str) -> dict[str, str]:
    raw = _git(repo, ["ls-tree", "-r", "-z", commit], binary=True)
    assert isinstance(raw, bytes)
    result: dict[str, str] = {}
    for entry in raw.split(b"\0"):
        if not entry:
            continue
        metadata, separator, path = entry.partition(b"\t")
        if not separator:
            continue
        fields = metadata.split()
        if len(fields) == 3 and fields[1] == b"blob":
            result[path.decode("utf-8", errors="strict")] = fields[2].decode("ascii")
    return result


def _show(repo: Path, commit: str, path: str) -> bytes:
    value = _git(repo, ["show", f"{commit}:{path}"], binary=True)
    assert isinstance(value, bytes)
    return value


def _official_ids(repo: Path) -> set[int]:
    path = repo / "data/raw/EN_Card_Data.csv"
    if not path.is_file():
        return set()
    values: set[int] = set()
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        match = re.match(r"\s*(\d+)\s*,", line)
        if match:
            values.add(int(match.group(1)))
    return values


def _name_for_ref(ref: str) -> str:
    marker = "/agents/"
    branch = ref.split(marker, 1)[1] if marker in ref else ref.rsplit("/", 1)[-1]
    return branch or "unknown"


def _candidate_id(branch: str, commit: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_.-]+", "-", branch).strip("-") or "agent"
    return f"internal_{safe}_{commit[:12]}"


_LOCAL_DECK_HELPER = '''\n\n# LOCAL_EVAL_SIDECAR_PATCH_V1\nfrom pathlib import Path as _LocalEvalPath\n\ndef _local_eval_deck_path():\n    try:\n        candidate = _LocalEvalPath(__file__).resolve().with_name("deck.csv")\n    except (NameError, OSError):\n        return "deck.csv"\n    return str(candidate) if candidate.is_file() else "deck.csv"\n'''


def _localize_policy(policy_bytes: bytes) -> tuple[bytes, str, int]:
    """Make common Kaggle cwd-relative deck reads self-contained locally."""

    text = policy_bytes.decode("utf-8", errors="strict")
    pattern = re.compile(r"(?m)^(?P<indent>\s*)file_path\s*=\s*(?P<quote>['\"])deck\.csv(?P=quote)\s*$")
    localized, count = pattern.subn(r"\g<indent>file_path = _local_eval_deck_path()", text)
    if count == 0:
        return policy_bytes, "NONE", 0
    if "LOCAL_EVAL_SIDECAR_PATCH_V1" not in localized:
        marker = "from __future__ import annotations"
        if marker in localized:
            localized = localized.replace(marker, marker + _LOCAL_DECK_HELPER, 1)
        else:
            localized = _LOCAL_DECK_HELPER.lstrip("\n") + "\n" + localized
    return localized.encode("utf-8"), "LOCAL_DECK_SIDECAR_V1", count


_READONLY_TELEMETRY_MARKER = "LOCAL_READONLY_TELEMETRY_V1"


def _strip_readonly_telemetry(policy_bytes: bytes) -> tuple[bytes, str, int]:
    """Remove one explicitly recognized optional telemetry file side effect.

    Some permitted internal snapshots keep useful in-memory planning telemetry
    but optionally append it to a path supplied by ``GRIMMSNARL_PLAN_TELEMETRY``.
    That path is outside the candidate sandbox, so the default intake rejects
    it.  The opt-in sanitizer is intentionally exact: it only matches the
    known ``_shadow_telemetry`` shape, preserves the in-memory append, and
    refuses to rewrite an unrecognized function.
    """

    text = policy_bytes.decode("utf-8", errors="strict")
    function = re.compile(
        r"(?ms)^(?P<header>def _shadow_telemetry[^\n]*:\n)"
        r"(?P<body>.*?)(?=^def _raw_overage\b)"
    ).search(text)
    if function is None:
        return policy_bytes, "NONE", 0
    body = function.group("body")
    if (
        "GRIMMSNARL_PLAN_TELEMETRY" not in body
        or not re.search(r"\bopen\s*\(\s*path\s*,\s*['\"]a['\"]", body)
        or not re.search(r"\bhandle\.write\s*\(", body)
        or "runtime.telemetry.append(record)" not in body
    ):
        return policy_bytes, "NONE", 0
    replacement = (
        f"{function.group('header')}"
        f"    # {_READONLY_TELEMETRY_MARKER}: keep in-memory telemetry only.\n"
        "    record = dict(event)\n"
        "    record.setdefault(\"match_id\", runtime.match_serial)\n"
        "    runtime.telemetry.append(record)\n\n"
    )
    localized = text[: function.start()] + replacement + text[function.end() :]
    return localized.encode("utf-8"), _READONLY_TELEMETRY_MARKER, 1


def _attribute_chain(node: ast.AST) -> tuple[str, ...]:
    names: list[str] = []
    current: ast.AST | None = node
    while isinstance(current, ast.Attribute):
        names.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        names.append(current.id)
    return tuple(reversed(names))


def _safe_environment_key(value: object) -> bool:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{1,63}", value):
        return False
    blocked_words = ("TOKEN", "SECRET", "PASSWORD", "CREDENTIAL", "HOME", "PATH", "KAGGLE", "USER", "TEAM")
    return not any(word in value for word in blocked_words)


def _static_findings(text: str) -> tuple[list[str], tuple[str, ...], tuple[str, ...]]:
    """Return security findings and a deterministic import inventory.

    Text comments containing words such as ``evaluation`` are not findings;
    dangerous operations are recognized from AST nodes.  This avoids the
    generic ingest scanner's false positive on a harmless ``ab-eval`` label.
    """

    try:
        tree = ast.parse(text)
    except SyntaxError:
        return ["syntax_error"], (), ()
    imports: set[str] = set()
    findings: set[str] = set()
    environment_keys: set[str] = set()
    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".", 1)[0]
                imports.add(root)
                if root in _NETWORK_IMPORTS:
                    findings.add("network_import")
                if root in _SUBPROCESS_IMPORTS:
                    findings.add("subprocess_import")
                if root in _DYNAMIC_IMPORTS:
                    findings.add("dynamic_import")
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".", 1)[0]
            if root:
                imports.add(root)
                if root in _NETWORK_IMPORTS:
                    findings.add("network_import")
                if root in _SUBPROCESS_IMPORTS:
                    findings.add("subprocess_import")
                if root in _DYNAMIC_IMPORTS:
                    findings.add("dynamic_import")
        elif isinstance(node, ast.Call):
            chain = _attribute_chain(node.func)
            if chain and chain[-1] in {"eval", "exec", "__import__"}:
                findings.add("dynamic_execution")
            if chain in {("os", "system"), ("subprocess", "Popen"), ("subprocess", "run"), ("subprocess", "call")}:
                findings.add("subprocess_call")
            if chain and chain[-1] in {"write_text", "write_bytes"}:
                findings.add("filesystem_write")
            elif chain and chain[-1] in {"unlink", "rmtree", "remove"}:
                # ``list.remove`` and similar gameplay bookkeeping are not
                # filesystem mutations.  Restrict the destructive-method
                # check to known filesystem namespaces/types.
                if chain[0] in {"os", "shutil", "pathlib", "Path"} or len(chain) == 1:
                    findings.add("filesystem_write")
            if chain and chain[-1] == "open":
                for argument in node.args[1:2]:
                    if isinstance(argument, ast.Constant) and isinstance(argument.value, str) and any(mode in argument.value for mode in ("w", "a", "x")):
                        findings.add("filesystem_write")
            if chain == ("os", "getenv") or chain[-3:] == ("os", "environ", "get"):
                key = node.args[0].value if node.args and isinstance(node.args[0], ast.Constant) else None
                if _safe_environment_key(key):
                    environment_keys.add(str(key))
                else:
                    findings.add("environment_access")
        elif isinstance(node, ast.Subscript):
            if _attribute_chain(node.value) == ("os", "environ"):
                findings.add("environment_access")
        elif isinstance(node, ast.Attribute):
            chain = _attribute_chain(node)
            if chain == ("os", "environ"):
                parent = parents.get(node)
                grandparent = parents.get(parent) if parent is not None else None
                safe_get = isinstance(parent, ast.Attribute) and parent.attr == "get" and isinstance(grandparent, ast.Call) and grandparent.func is parent
                if not safe_get:
                    findings.add("environment_access")
            if chain[:1] == ("importlib",):
                findings.add("dynamic_import")
    if _SECRET.search(text):
        findings.add("secret_literal")
    if _NETWORK_TEXT.search(text):
        findings.add("network_literal")
    return sorted(findings), tuple(sorted(imports)), tuple(sorted(environment_keys))


def _read_pool_rows(path: Path) -> list[Mapping[str, Any]]:
    try:
        decoded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FreshInternalMetaError(f"current pool manifest is unreadable: {path}") from exc
    rows: object = decoded.get("opponents", decoded) if isinstance(decoded, Mapping) else decoded
    if isinstance(rows, Mapping):
        rows = list(rows.values())
    if not isinstance(rows, list):
        raise FreshInternalMetaError("current pool manifest must contain a list")
    return [row for row in rows if isinstance(row, Mapping)]


def _source_commits_from_sidecars(pool_path: Path, rows: Iterable[Mapping[str, Any]]) -> set[str]:
    commits: set[str] = set()
    for row in rows:
        for key in ("source_commit", "commit"):
            value = row.get(key)
            if isinstance(value, str) and _SHA40.fullmatch(value):
                commits.add(value)
        opponent_id = row.get("id")
        if not isinstance(opponent_id, str):
            continue
        sidecar = pool_path.parent / opponent_id / "SOURCE.md"
        if not sidecar.is_file():
            continue
        text = sidecar.read_text(encoding="utf-8", errors="ignore")
        commits.update(re.findall(r"(?i)(?<![0-9a-f])([0-9a-f]{40})(?![0-9a-f])", text))
    return commits


def _ledger_tokens(path: Path | None) -> set[str]:
    if path is None:
        return set()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FreshInternalMetaError(f"consumed ledger is unreadable: {path}") from exc
    keys = {"id", "ref", "source_commit", "commit", "policy_sha256", "canonical_deck_hash", "deck_bytes_sha256"}
    found: set[str] = set()

    def walk(node: object, key: str | None = None) -> None:
        if isinstance(node, Mapping):
            for name, value in node.items():
                walk(value, str(name))
        elif isinstance(node, list):
            for value in node:
                walk(value, key)
        elif key in keys and isinstance(node, str):
            found.add(node)

    walk(payload)
    return found


def _artifact_hits(roots: Sequence[Path], tokens: Sequence[str]) -> list[str]:
    wanted = tuple(token.encode("ascii") for token in tokens if token)
    if not wanted:
        return []
    hits: list[str] = []
    seen: set[Path] = set()
    for root in roots:
        root = root.resolve()
        if not root.exists():
            continue
        paths = [root] if root.is_file() else sorted(root.rglob("*"))
        for path in paths:
            if not path.is_file() or path.is_symlink() or path in seen:
                continue
            seen.add(path)
            # ``runs/`` may contain model checkpoints and multi-gigabyte raw
            # logs.  Freshness identity is recorded in text manifests, so do
            # not read opaque/binary payloads during a source intake scan.
            if path.suffix.lower() not in _TEXT_SUFFIXES:
                continue
            try:
                if path.stat().st_size > _MAX_SCAN_BYTES:
                    continue
                data = path.read_bytes()
            except OSError:
                continue
            if any(token in data for token in wanted):
                hits.append(str(path))
    return sorted(hits)


def _candidate_from_ref(
    repo: Path,
    ref: str,
    commit: str,
    official_ids: set[int],
    *,
    readonly_telemetry_allowed: bool = False,
) -> tuple[FreshInternalMetaCandidate | None, list[str], dict[str, Any]]:
    branch = _name_for_ref(ref)
    blobs = _tree_blob_ids(repo, commit)
    if "main.py" not in blobs or "deck.csv" not in blobs:
        return None, ["missing_root_asset"], {"ref": ref, "source_branch": f"agents/{branch}", "source_commit": commit}
    policy_source_bytes = _show(repo, commit, "main.py")
    deck_bytes = _show(repo, commit, "deck.csv")
    policy_bytes = policy_source_bytes
    readonly_telemetry_patch = "NONE"
    readonly_telemetry_count = 0
    if readonly_telemetry_allowed:
        policy_bytes, readonly_telemetry_patch, readonly_telemetry_count = _strip_readonly_telemetry(policy_bytes)
    policy_bytes, localization_patch, localization_count = _localize_policy(policy_bytes)
    policy_text = policy_bytes.decode("utf-8", errors="strict")
    deck_text = deck_bytes.decode("utf-8", errors="strict")
    findings, imports, environment_keys = _static_findings(policy_text)
    normalized = normalize_deck_text(deck_text, source_id=f"{commit}:deck.csv", path="deck.csv", official_ids=official_ids)
    reasons = list(findings)
    if normalized.get("eligibility") != "EXACT_60_VALID":
        reasons.append("invalid_deck")
    evidence: dict[str, Any] = {
        "ref": ref,
        "source_branch": f"agents/{branch}",
        "source_commit": commit,
        "main_blob": blobs["main.py"],
        "deck_blob": blobs["deck.csv"],
        "source_policy_sha256": _sha256_bytes(policy_source_bytes),
        "policy_sha256": _sha256_bytes(policy_bytes),
        "deck_bytes_sha256": _sha256_bytes(deck_bytes),
        "readonly_telemetry_patch": readonly_telemetry_patch,
        "readonly_telemetry_replacement_count": readonly_telemetry_count,
        "localization_patch": localization_patch,
        "localization_replacement_count": localization_count,
        "imports": list(imports),
        "environment_keys": list(environment_keys),
        "static_findings": list(findings),
        "deck_eligibility": normalized.get("eligibility"),
        "canonical_deck_hash": None,
    }
    if reasons:
        return None, sorted(set(reasons), key=reasons.index), evidence
    cards = [int(value) for value in deck_text.replace(",", " ").split()]
    canonical = canonical_deck_sha256(cards)
    candidate = FreshInternalMetaCandidate(
        candidate_id=_candidate_id(branch, commit),
        ref=ref,
        source_branch=f"agents/{branch}",
        source_commit=commit,
        source_policy_sha256=_sha256_bytes(policy_source_bytes),
        policy_sha256=_sha256_bytes(policy_bytes),
        deck_bytes_sha256=_sha256_bytes(deck_bytes),
        canonical_deck_hash=canonical,
        imports=imports,
        localization_patch=localization_patch,
        readonly_telemetry_patch=readonly_telemetry_patch,
    )
    evidence["canonical_deck_hash"] = canonical
    return candidate, [], evidence


def seal_fresh_internal_meta_v1(
    *,
    repo: Path | str,
    pool_manifest_path: Path | str,
    output_root: Path | str,
    source_epoch: str,
    seed_namespace: str,
    ref_glob: str = DEFAULT_REF_GLOB_V1,
    excluded_refs: Sequence[str] = (),
    readonly_telemetry_refs: Sequence[str] = (),
    consumed_ledger_path: Path | str | None = None,
    scan_roots: Sequence[Path | str] | None = None,
    include_refs: Sequence[str] = (),
    history_depth: int = 0,
    max_candidates: int | None = None,
) -> dict[str, object]:
    """Discover and seal permitted branch snapshots without changing the repo."""

    repo_path = Path(repo).resolve()
    pool_path = Path(pool_manifest_path).resolve()
    output = Path(output_root).resolve()
    if output.exists():
        raise FileExistsError(f"refusing to overwrite staged intake root: {output}")
    if not source_epoch.strip() or not seed_namespace.strip():
        raise FreshInternalMetaError("source_epoch and seed_namespace must be non-empty")
    if history_depth < 0:
        raise FreshInternalMetaError("history_depth must be non-negative")
    if max_candidates is not None and max_candidates <= 0:
        raise FreshInternalMetaError("max_candidates must be positive when provided")
    rows = _read_pool_rows(pool_path)
    existing_policy = {str(row.get("policy_hash")) for row in rows if isinstance(row.get("policy_hash"), str)}
    existing_commits = _source_commits_from_sidecars(pool_path, rows)
    ledger_tokens = _ledger_tokens(Path(consumed_ledger_path).resolve() if consumed_ledger_path is not None else None)
    roots = tuple(Path(root).resolve() for root in (scan_roots or ()))
    excluded = set(DEFAULT_EXCLUDED_REFS_V1).union(str(ref) for ref in excluded_refs)
    included = {str(ref) for ref in include_refs}
    ref_rows = _ref_rows(repo_path, ref_glob)
    if included:
        ref_rows = [item for item in ref_rows if item[0] in included]
        missing = sorted(included - {ref for ref, _commit in ref_rows})
        if missing:
            raise FreshInternalMetaError(f"included refs are not present: {missing}")
    source_rows = _history_rows(repo_path, ref_rows, history_depth)
    readonly_telemetry_allowed_refs = {str(ref) for ref in readonly_telemetry_refs}
    official_ids = _official_ids(repo_path)
    output.mkdir(parents=True, exist_ok=False)

    accepted: list[tuple[FreshInternalMetaCandidate, dict[str, Any]]] = []
    rejection_map: dict[str, list[str]] = {}
    accepted_identity: set[tuple[str, str]] = set()
    for ref, commit in source_rows:
        branch = _name_for_ref(ref)
        if ref in excluded:
            rejection_map[branch] = ["ref_excluded"]
            continue
        candidate, reasons, evidence = _candidate_from_ref(
            repo_path,
            ref,
            commit,
            official_ids,
            readonly_telemetry_allowed=ref in readonly_telemetry_allowed_refs,
        )
        if candidate is None:
            rejection_map[f"{branch}@{commit[:12]}" if history_depth else branch] = reasons
            continue
        identity_tokens = (candidate.source_commit, candidate.source_policy_sha256, candidate.policy_sha256, candidate.canonical_deck_hash, candidate.deck_bytes_sha256)
        reuse: list[str] = []
        if candidate.source_commit in existing_commits:
            reuse.append("source_commit_reused")
        if candidate.policy_sha256 in existing_policy:
            reuse.append("policy_identity_reused")
        # A deck duplicate with a different policy is intentionally retained:
        # policy identity is part of the opponent instance, and the existing
        # pool already uses this pattern for policy A/B comparisons.  Only an
        # exact policy/source identity is stale.
        if any(token in ledger_tokens for token in identity_tokens):
            reuse.append("consumed_ledger_reused")
        hits = _artifact_hits(roots, identity_tokens)
        if hits:
            reuse.append("artifact_identity_reused")
            evidence["artifact_hits"] = hits
        identity = (candidate.policy_sha256, candidate.canonical_deck_hash)
        if history_depth and identity in accepted_identity:
            reuse.append("batch_identity_reused")
        if reuse:
            rejection_map[f"{branch}@{commit[:12]}" if history_depth else branch] = reuse
            continue
        accepted.append((candidate, evidence))
        accepted_identity.add(identity)
        if max_candidates is not None and len(accepted) >= max_candidates:
            break

    accepted.sort(key=lambda item: item[0].candidate_id)
    for candidate, evidence in accepted:
        directory = output / candidate.candidate_id
        directory.mkdir(parents=True, exist_ok=False)
        source_policy_bytes = _show(repo_path, candidate.source_commit, "main.py")
        readonly_policy, readonly_patch, readonly_count = _strip_readonly_telemetry(source_policy_bytes) if candidate.readonly_telemetry_patch != "NONE" else (source_policy_bytes, "NONE", 0)
        localized_policy, localization_patch, localization_count = _localize_policy(readonly_policy)
        if (
            _sha256_bytes(localized_policy) != candidate.policy_sha256
            or localization_patch != candidate.localization_patch
            or readonly_patch != candidate.readonly_telemetry_patch
            or readonly_count != (1 if candidate.readonly_telemetry_patch != "NONE" else 0)
        ):
            raise FreshInternalMetaError(f"localized policy changed during sealing: {candidate.candidate_id}")
        _write_new(directory / "main.py", localized_policy)
        _write_new(directory / "deck.csv", _show(repo_path, candidate.source_commit, "deck.csv"))
        source_note = (
            "# Internal source snapshot (research-only)\n\n"
            f"- branch: `{candidate.source_branch}`\n"
            f"- ref: `{candidate.ref}`\n"
            f"- commit: `{candidate.source_commit}`\n"
            f"- source policy SHA-256: `{candidate.source_policy_sha256}`\n"
            f"- staged policy SHA-256: `{candidate.policy_sha256}`\n"
            f"- deck bytes SHA-256: `{candidate.deck_bytes_sha256}`\n"
            f"- canonical deck SHA-256: `{candidate.canonical_deck_hash}`\n"
            f"- localization patch: `{candidate.localization_patch}` ({localization_count} replacement(s))\n"
            f"- readonly telemetry patch: `{candidate.readonly_telemetry_patch}` ({readonly_count} replacement(s))\n"
            "- usage boundary: `local_eval_only`\n"
            "- submission bundle: prohibited\n"
            "- static source intake only; CABT smoke remains a separate gate\n"
        )
        _write_new(directory / "SOURCE.md", source_note.encode("utf-8"))
        evidence["candidate_id"] = candidate.candidate_id
        evidence["fresh"] = True
        evidence["unused_before_run"] = True
        evidence["source"] = candidate.source
        evidence["usage_boundary"] = candidate.usage_boundary
        _write_json_new(output / "evidence" / f"{candidate.candidate_id}.json", evidence)

    pool_rows = [candidate.to_pool_row() for candidate, _ in accepted]
    pool_path_out = output / "pool_manifest.json"
    if pool_rows:
        _write_json_new(pool_path_out, pool_rows)
    report: dict[str, object] = {
        "schema_version": FRESH_INTERNAL_META_SCHEMA_V1,
        "status": "SEALED" if accepted else "BLOCKED_NO_SAFE_CANDIDATES",
        "source_epoch": source_epoch,
        "seed_namespace": seed_namespace,
        "ref_glob": ref_glob,
        "include_refs": sorted(included),
        "history_depth": history_depth,
        "max_candidates": max_candidates,
        "accepted_count": len(accepted),
        "accepted_ids": [candidate.candidate_id for candidate, _ in accepted],
        "rejected_count": len(rejection_map),
        "rejections": rejection_map,
        "current_pool_manifest": str(pool_path),
        "current_pool_manifest_sha256": _sha256_file(pool_path),
        "git_mutation": False,
        "imports_executed": False,
        "network_access": False,
        "authority": {"training_allowed": False, "promotion_allowed": False, "submission_allowed": False, "longrun_allowed": False},
        "research_only": True,
    }
    if not accepted:
        _write_json_new(output / "intake_report.json", report)
        return report

    pool_sha = _sha256_file(pool_path_out)
    references: list[dict[str, object]] = []
    for candidate, evidence in accepted:
        evidence_path = output / "evidence" / f"{candidate.candidate_id}.json"
        references.append({
            "id": candidate.candidate_id,
            "fresh": True,
            "unused_before_run": True,
            "freshness_evidence_sha256": _sha256_file(evidence_path),
            "freshness_evidence_path": str(Path("evidence") / evidence_path.name),
            "policy_sha256": candidate.policy_sha256,
            "canonical_deck_hash": candidate.canonical_deck_hash,
            "source": candidate.source,
        })
    reference_ids = sorted(reference["id"] for reference in references)
    seed_plan_sha = _sha256_bytes(_canonical_json({"source_epoch": source_epoch, "seed_namespace": seed_namespace, "reference_ids": reference_ids}))
    fresh_payload = {
        "schema_version": "meta-specialist-cg-fresh-meta-batch-v1",
        "batch_id": f"internal-{re.sub(r'[^a-zA-Z0-9_.-]+', '-', source_epoch)}-{re.sub(r'[^a-zA-Z0-9_.-]+', '-', seed_namespace)}",
        "source_epoch": source_epoch,
        "seed_namespace": seed_namespace,
        "seed_plan_sha256": seed_plan_sha,
        "reference_ids": reference_ids,
        "pool_manifest_sha256": pool_sha,
        "freshness_basis": (
            "same-commit root main.py+deck.csv; current pool, consumed ledger, and "
            "configured artifact roots identity scan"
            if history_depth == 0
            else "first-parent historical same-commit root main.py+deck.csv; current pool, consumed ledger, and configured artifact roots identity scan"
        ),
        "references": sorted(references, key=lambda row: str(row["id"])),
        "authority": {"training_allowed": False, "promotion_allowed": False, "submission_allowed": False, "longrun_allowed": False},
        "research_only": True,
    }
    _write_json_new(output / "fresh_meta.json", fresh_payload)
    report["pool_manifest_path"] = str(pool_path_out)
    report["pool_manifest_sha256"] = pool_sha
    report["fresh_meta_path"] = str(output / "fresh_meta.json")
    report["fresh_meta_sha256"] = _sha256_file(output / "fresh_meta.json")
    _write_json_new(output / "intake_report.json", report)
    return report


__all__ = [
    "DEFAULT_EXCLUDED_REFS_V1",
    "DEFAULT_REF_GLOB_V1",
    "FRESH_INTERNAL_META_SCHEMA_V1",
    "FreshInternalMetaCandidate",
    "FreshInternalMetaError",
    "seal_fresh_internal_meta_v1",
]
