"""Seal public Kaggle kernel outputs as isolated, research-only opponents.

The intake never downloads a kernel and never imports source before the static
boundary has passed.  A kernel tarball is an input artifact, not a submission
artifact: the generated pool is ``local_eval_only`` and is intentionally
ineligible for training, promotion, long runs, or Kaggle submission.
"""

from __future__ import annotations

import ast
from dataclasses import asdict, dataclass
import csv
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys
import tarfile
import tempfile
from typing import Any, Mapping, Sequence

from mage_ptcg.observability.cabt_trace import canonical_deck_sha256
from mage_ptcg.opponent_ingest.pipeline import normalize_deck_text


KAGGLE_KERNEL_META_SCHEMA_V1 = "meta-specialist-cg-kaggle-kernel-meta-intake-v1"
KAGGLE_PUBLIC_SOURCE_V1 = "kaggle_public_kernel"
LOCAL_EVAL_ONLY_V1 = "local_eval_only"

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$")
_ENTRYPOINT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,95}$")
_NETWORK_IMPORTS = frozenset({"requests", "urllib", "httpx", "aiohttp", "socket"})
_SUBPROCESS_IMPORTS = frozenset({"subprocess", "pexpect"})
_DYNAMIC_IMPORTS = frozenset({"importlib", "ctypes"})
_TEXT_SUFFIXES = frozenset({".json", ".jsonl", ".md", ".yaml", ".yml", ".txt", ".csv", ".log"})
_MAX_MEMBER_COUNT = 1024
_MAX_MEMBER_BYTES = 64 * 1024 * 1024
_MAX_TOTAL_BYTES = 256 * 1024 * 1024
_MAX_SCAN_BYTES = 16 * 1024 * 1024
_SECRET = re.compile(r"(?i)(api[_-]?key|secret|password|token)\s*[:=]")
_NETWORK_TEXT = re.compile(r"(?i)\b(?:https?|ftp)://")


class KaggleKernelMetaError(RuntimeError):
    """Raised when a public kernel cannot be sealed safely."""


@dataclass(frozen=True, slots=True)
class KernelSourceSpec:
    """Provenance for one locally acquired Kaggle kernel output."""

    candidate_id: str
    kernel_ref: str
    source_url: str
    tar_path: Path
    tar_sha256: str
    fetched_at_utc: str
    entrypoint_name: str = "agent"

    @classmethod
    def from_mapping(cls, value: Mapping[str, object], *, base_dir: Path | None = None) -> "KernelSourceSpec":
        required = ("candidate_id", "kernel_ref", "source_url", "tar_path", "tar_sha256", "fetched_at_utc")
        missing = [key for key in required if not str(value.get(key, "")).strip()]
        if missing:
            raise KaggleKernelMetaError(f"source spec missing fields: {missing}")
        raw_path = Path(str(value["tar_path"]))
        path = (base_dir / raw_path if base_dir is not None and not raw_path.is_absolute() else raw_path).resolve()
        spec = cls(
            candidate_id=str(value["candidate_id"]),
            kernel_ref=str(value["kernel_ref"]),
            source_url=str(value["source_url"]),
            tar_path=path,
            tar_sha256=str(value["tar_sha256"]).lower(),
            fetched_at_utc=str(value["fetched_at_utc"]),
            entrypoint_name=str(value.get("entrypoint_name", value.get("entrypoint", "agent"))),
        )
        _validate_spec(spec)
        return spec


def _validate_spec(spec: KernelSourceSpec) -> None:
    if not _ID.fullmatch(spec.candidate_id):
        raise KaggleKernelMetaError(f"invalid candidate_id: {spec.candidate_id!r}")
    if not spec.kernel_ref.strip():
        raise KaggleKernelMetaError(f"{spec.candidate_id}: kernel_ref is empty")
    if not spec.source_url.startswith("https://www.kaggle.com/"):
        raise KaggleKernelMetaError(f"{spec.candidate_id}: source_url is not a Kaggle URL")
    if not _SHA256.fullmatch(spec.tar_sha256):
        raise KaggleKernelMetaError(f"{spec.candidate_id}: tar_sha256 must be 64 lowercase hex characters")
    if not _ENTRYPOINT.fullmatch(spec.entrypoint_name):
        raise KaggleKernelMetaError(f"{spec.candidate_id}: invalid entrypoint_name: {spec.entrypoint_name!r}")
    if not spec.tar_path.is_file() or spec.tar_path.is_symlink():
        raise KaggleKernelMetaError(f"{spec.candidate_id}: tar_path is not a regular file: {spec.tar_path}")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _canonical_json(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _write_new(path: Path, data: bytes) -> None:
    if path.exists():
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
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


def _safe_environment_key(value: object) -> bool:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{1,63}", value):
        return False
    blocked = ("TOKEN", "SECRET", "PASSWORD", "CREDENTIAL", "HOME", "PATH", "KAGGLE", "USER", "TEAM")
    return not any(word in value for word in blocked)


def _attribute_chain(node: ast.AST) -> tuple[str, ...]:
    names: list[str] = []
    current: ast.AST | None = node
    while isinstance(current, ast.Attribute):
        names.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        names.append(current.id)
    return tuple(reversed(names))


def scan_source_text(text: str) -> tuple[list[str], tuple[str, ...]]:
    """Return deterministic safety findings and top-level import inventory."""

    try:
        tree = ast.parse(text)
    except SyntaxError:
        return ["syntax_error"], ()
    imports: set[str] = set()
    findings: set[str] = set()
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
                if chain[0] in {"os", "shutil", "pathlib", "Path"} or len(chain) == 1:
                    findings.add("filesystem_write")
            if chain and chain[-1] == "open":
                mode = node.args[1] if len(node.args) > 1 else None
                if isinstance(mode, ast.Constant) and isinstance(mode.value, str) and any(flag in mode.value for flag in ("w", "a", "x")):
                    findings.add("filesystem_write")
            if chain == ("os", "getenv") or chain[-3:] == ("os", "environ", "get"):
                key = node.args[0].value if node.args and isinstance(node.args[0], ast.Constant) else None
                if not _safe_environment_key(key):
                    findings.add("environment_access")
        elif isinstance(node, ast.Subscript):
            if _attribute_chain(node.value) == ("os", "environ"):
                findings.add("environment_access")
        elif isinstance(node, ast.Attribute):
            chain = _attribute_chain(node)
            if chain == ("os", "environ"):
                parent = parents.get(node)
                grandparent = parents.get(parent) if parent is not None else None
                safe_get = (
                    isinstance(parent, ast.Attribute)
                    and parent.attr == "get"
                    and isinstance(grandparent, ast.Call)
                    and grandparent.func is parent
                )
                if not safe_get:
                    findings.add("environment_access")
            if chain[:1] == ("importlib",):
                findings.add("dynamic_import")
    if _SECRET.search(text):
        findings.add("secret_literal")
    if _NETWORK_TEXT.search(text):
        findings.add("network_literal")
    return sorted(findings), tuple(sorted(imports))


def _safe_member_path(name: str) -> PurePosixPath:
    if "\\" in name:
        raise KaggleKernelMetaError(f"unsafe tar member path: {name!r}")
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts:
        raise KaggleKernelMetaError(f"unsafe tar member path: {name!r}")
    return path


def _excluded_member(path: PurePosixPath) -> str | None:
    parts = path.parts
    lowered = [part.lower() for part in parts]
    if "cg" in lowered:
        return "bundled_cg"
    if "__pycache__" in lowered or path.suffix.lower() == ".pyc":
        return "python_cache"
    if path.name.lower().endswith(("submission.tar.gz", "submission.zip")):
        return "submission_archive"
    if path.suffix.lower() == ".ipynb":
        return "notebook_output"
    return None


def safe_extract_kernel_tar(spec: KernelSourceSpec, payload_root: Path | str) -> dict[str, object]:
    """Validate and copy a kernel tar into an isolated payload directory."""

    _validate_spec(spec)
    payload = Path(payload_root).resolve()
    actual_sha = _sha256_file(spec.tar_path)
    if actual_sha != spec.tar_sha256:
        raise KaggleKernelMetaError(f"{spec.candidate_id}: tar SHA-256 mismatch ({actual_sha})")
    payload.mkdir(parents=True, exist_ok=False)
    root_main: bytes | None = None
    deck_bytes: bytes | None = None
    retained: list[str] = []
    excluded: list[dict[str, str]] = []
    python_files: list[str] = []
    declared_bytes = 0
    try:
        with tarfile.open(spec.tar_path, "r:*") as archive:
            members = archive.getmembers()
            if len(members) > _MAX_MEMBER_COUNT:
                raise KaggleKernelMetaError(f"{spec.candidate_id}: tar member count exceeds {_MAX_MEMBER_COUNT}")
            for member in members:
                path = _safe_member_path(member.name)
                declared_bytes += max(0, int(member.size))
                if member.size > _MAX_MEMBER_BYTES or declared_bytes > _MAX_TOTAL_BYTES:
                    raise KaggleKernelMetaError(f"{spec.candidate_id}: tar declared bytes exceed safety limits")
                if member.issym() or member.islnk():
                    raise KaggleKernelMetaError(f"{spec.candidate_id}: tar link member is forbidden: {member.name!r}")
                if not member.isdir() and not member.isfile():
                    raise KaggleKernelMetaError(f"{spec.candidate_id}: tar member type is forbidden: {member.name!r}")
                if member.isdir():
                    continue
                reason = _excluded_member(path)
                if reason is not None:
                    excluded.append({"member": path.as_posix(), "reason": reason})
                    continue
                file_handle = archive.extractfile(member)
                if file_handle is None:
                    raise KaggleKernelMetaError(f"{spec.candidate_id}: cannot read tar member: {member.name!r}")
                data = file_handle.read(member.size + 1)
                if len(data) != member.size:
                    raise KaggleKernelMetaError(f"{spec.candidate_id}: tar member size changed: {member.name!r}")
                if path.parts == ("main.py",):
                    root_main = data
                    destination = payload / "original_main.py"
                elif path.parts == ("deck.csv",):
                    deck_bytes = data
                    continue
                else:
                    destination = payload.joinpath(*path.parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                _write_new(destination, data)
                retained.append(path.as_posix())
                if destination.suffix.lower() == ".py":
                    python_files.append(str(destination.relative_to(payload)))
    except (tarfile.TarError, OSError) as exc:
        raise KaggleKernelMetaError(f"{spec.candidate_id}: tar extraction failed: {exc}") from exc
    if root_main is None or deck_bytes is None:
        missing = [name for name, value in (("main.py", root_main), ("deck.csv", deck_bytes)) if value is None]
        raise KaggleKernelMetaError(f"{spec.candidate_id}: missing root asset(s): {missing}")
    return {
        "root_main_bytes": root_main,
        "deck_bytes": deck_bytes,
        "retained_members": sorted(retained),
        "excluded_members": excluded,
        "python_files": sorted(python_files),
        "declared_member_count": len(members),
        "declared_bytes": declared_bytes,
        "tar_sha256": actual_sha,
    }


def _wrapper_text(candidate_id: str, entrypoint_name: str = "agent") -> str:
    if not _ENTRYPOINT.fullmatch(entrypoint_name):
        raise KaggleKernelMetaError(f"invalid entrypoint_name: {entrypoint_name!r}")
    module_name = "_kaggle_payload_main_" + re.sub(r"[^A-Za-z0-9_]", "_", candidate_id)
    return f'''"""Generated local-evaluation wrapper for {candidate_id}."""
from __future__ import annotations

import importlib.util
import inspect
import os
from pathlib import Path
import sys

_ROOT = Path(__file__).resolve().parent
_PAYLOAD = _ROOT / "payload"
_MODULE_NAME = {module_name!r}


def _shared_engine_root() -> Path:
    for ancestor in (_ROOT, *_ROOT.parents, Path.cwd()):
        if (ancestor / "cg" / "__init__.py").is_file():
            return ancestor
    raise RuntimeError("shared cg engine root is unavailable")


def _under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _drop_payload_modules() -> None:
    pool_root = _ROOT.parent
    payload_roots = [
        child / "payload"
        for child in pool_root.iterdir()
        if child.is_dir() and (child / "payload").is_dir()
    ]
    for name, module in list(sys.modules.items()):
        path = getattr(module, "__file__", None)
        if not path:
            continue
        try:
            resolved = Path(path).resolve()
        except OSError:
            continue
        if any(_under(resolved, root) for root in payload_roots):
            sys.modules.pop(name, None)
    for entry in list(sys.path):
        try:
            resolved = Path(entry).resolve()
        except OSError:
            continue
        if any(resolved == root.resolve() for root in payload_roots):
            sys.path.remove(entry)


_drop_payload_modules()
_ENGINE_ROOT = _shared_engine_root()
if str(_ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(_ENGINE_ROOT))
sys.path.append(str(_PAYLOAD))
_previous_cwd = os.getcwd()
try:
    os.chdir(str(_ROOT))
    _spec = importlib.util.spec_from_file_location(_MODULE_NAME, _PAYLOAD / "original_main.py")
    if _spec is None or _spec.loader is None:
        raise RuntimeError("payload main is unavailable")
    _module = importlib.util.module_from_spec(_spec)
    sys.modules[_MODULE_NAME] = _module
    _spec.loader.exec_module(_module)
finally:
    os.chdir(_previous_cwd)

_ENTRYPOINT_NAME = {entrypoint_name!r}
_PAYLOAD_AGENT = getattr(_module, _ENTRYPOINT_NAME, None)
if _PAYLOAD_AGENT is None or not callable(_PAYLOAD_AGENT):
    raise RuntimeError(f"payload main must expose callable {{_ENTRYPOINT_NAME}}")
try:
    _PAYLOAD_SIGNATURE = inspect.signature(_PAYLOAD_AGENT)
except (TypeError, ValueError):
    _PAYLOAD_SIGNATURE = None


def _payload_accepts_configuration() -> bool:
    if _PAYLOAD_SIGNATURE is None:
        return False
    try:
        _PAYLOAD_SIGNATURE.bind(object(), object())
    except TypeError:
        return False
    return True


_PAYLOAD_HAS_CONFIGURATION = _payload_accepts_configuration()


def agent(observation, configuration=None):
    previous_cwd = os.getcwd()
    try:
        os.chdir(str(_ROOT))
        if configuration is None or not _PAYLOAD_HAS_CONFIGURATION:
            return _PAYLOAD_AGENT(observation)
        return _PAYLOAD_AGENT(observation, configuration)
    finally:
        os.chdir(previous_cwd)
'''


def write_candidate_wrapper(
    candidate_id: str,
    payload_root: Path | str,
    destination: Path | str,
    *,
    entrypoint_name: str = "agent",
) -> str:
    """Write and return a candidate wrapper, without overwriting a file."""

    if not _ID.fullmatch(candidate_id):
        raise KaggleKernelMetaError(f"invalid candidate_id: {candidate_id!r}")
    payload = Path(payload_root).resolve()
    target = Path(destination).resolve()
    if not payload.is_dir():
        raise KaggleKernelMetaError(f"payload root is missing: {payload}")
    text = _wrapper_text(candidate_id, entrypoint_name)
    _write_new(target, text.encode("utf-8"))
    return text


def load_candidate_agent(wrapper_path: Path | str):
    """Load a generated wrapper for an isolated local smoke test."""

    path = Path(wrapper_path).resolve()
    if not path.is_file():
        raise KaggleKernelMetaError(f"candidate wrapper is missing: {path}")
    module_name = "_kaggle_candidate_wrapper_" + _sha256_file(path)[:16]
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise KaggleKernelMetaError(f"could not load candidate wrapper: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        sys.modules.pop(module_name, None)
        raise KaggleKernelMetaError(f"candidate wrapper import failed: {type(exc).__name__}: {exc}") from exc
    agent = getattr(module, "agent", None)
    if not callable(agent):
        raise KaggleKernelMetaError(f"candidate wrapper does not expose callable agent: {path}")
    return agent


def _official_ids(pool_manifest_path: Path) -> set[int]:
    repo_root = pool_manifest_path.parent.parent
    card_path = repo_root / "data/raw/EN_Card_Data.csv"
    if not card_path.is_file():
        return set()
    values: set[int] = set()
    for line in card_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        match = re.match(r"\s*(\d+)\s*,", line)
        if match:
            values.add(int(match.group(1)))
    return values


def _official_ace_spec_ids(pool_manifest_path: Path) -> set[int]:
    """Return ACE SPEC card IDs from the local official catalog when available."""

    repo_root = pool_manifest_path.parent.parent
    card_path = repo_root / "data/raw/EN_Card_Data.csv"
    if not card_path.is_file():
        return set()
    values: set[int] = set()
    try:
        with card_path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                raw_id = row.get("Card ID")
                if not raw_id or str(row.get("Rule", "")).strip().upper() != "ACE SPEC":
                    continue
                try:
                    values.add(int(str(raw_id).strip()))
                except ValueError:
                    continue
    except (OSError, UnicodeError, csv.Error):
        return set()
    return values


def _pool_rows(path: Path) -> list[Mapping[str, object]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise KaggleKernelMetaError(f"current pool manifest is unreadable: {path}") from exc
    rows: object = raw.get("opponents", raw) if isinstance(raw, Mapping) else raw
    if isinstance(rows, Mapping):
        rows = list(rows.values())
    if not isinstance(rows, list):
        raise KaggleKernelMetaError("current pool manifest must contain a list")
    return [row for row in rows if isinstance(row, Mapping)]


def _artifact_hits(roots: Sequence[Path], tokens: Sequence[str]) -> list[str]:
    wanted = tuple(token.encode("ascii") for token in tokens if token)
    if not wanted:
        return []
    hits: list[str] = []
    seen: set[Path] = set()
    for root in roots:
        root = root.resolve()
        paths = [root] if root.is_file() else sorted(root.rglob("*")) if root.exists() else []
        for path in paths:
            if path in seen or not path.is_file() or path.is_symlink() or path.suffix.lower() not in _TEXT_SUFFIXES:
                continue
            seen.add(path)
            try:
                if path.stat().st_size > _MAX_SCAN_BYTES:
                    continue
                data = path.read_bytes()
            except OSError:
                continue
            if any(token in data for token in wanted):
                hits.append(str(path))
    return sorted(hits)


def _entrypoint_reason(text: str, entrypoint_name: str = "agent") -> str | None:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return "missing_agent_entrypoint" if entrypoint_name == "agent" else "missing_entrypoint"
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == entrypoint_name:
            # Existing ``agent`` sources historically passed this gate without
            # signature inspection.  Keep that compatibility, while aliases
            # must be an ordinary one-observation callable so the generated
            # wrapper cannot silently change its call contract.
            if entrypoint_name == "agent":
                return None
            positional = [*node.args.posonlyargs, *node.args.args]
            required = len(positional) - len(node.args.defaults)
            if (
                len(positional) not in (1, 2)
                or required > 1
                or node.args.vararg is not None
                or node.args.kwarg is not None
                or any(default is None for default in node.args.kw_defaults)
            ):
                return "invalid_entrypoint_signature"
            return None
        if isinstance(node, ast.AsyncFunctionDef) and node.name == entrypoint_name:
            return "invalid_entrypoint_signature" if entrypoint_name != "agent" else None
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if entrypoint_name == "agent" and any(
                isinstance(target, ast.Name) and target.id == entrypoint_name for target in targets
            ):
                return None
        if entrypoint_name == "agent" and isinstance(node, ast.ImportFrom):
            # Some otherwise valid submissions keep the implementation in a
            # sibling ``agent.py`` and expose it from the root ``main.py`` via
            # ``from agent import agent``.  Treat only an import that binds a
            # callable named exactly ``agent`` as the standard entrypoint;
            # importing the module itself (``import agent``), a different
            # symbol, or an alias under another name must remain rejected.
            if any(
                alias.name == "agent"
                and (alias.asname is None or alias.asname == entrypoint_name)
                for alias in node.names
            ):
                return None
    return "missing_agent_entrypoint" if entrypoint_name == "agent" else "missing_entrypoint"


def _has_agent_entrypoint(text: str, entrypoint_name: str = "agent") -> bool:
    """Compatibility helper for source gates, including explicit aliases."""

    return _entrypoint_reason(text, entrypoint_name) is None


def _source_note(spec: KernelSourceSpec, evidence: Mapping[str, object]) -> str:
    return (
        "# Public Kaggle kernel source snapshot (research-only)\n\n"
        f"- kernel ref: `{spec.kernel_ref}`\n"
        f"- source URL: `{spec.source_url}`\n"
        f"- fetched at UTC: `{spec.fetched_at_utc}`\n"
        f"- entrypoint name: `{spec.entrypoint_name}`\n"
        f"- tar SHA-256: `{spec.tar_sha256}`\n"
        f"- source policy SHA-256: `{evidence['source_policy_sha256']}`\n"
        f"- staged wrapper policy SHA-256: `{evidence['policy_sha256']}`\n"
        f"- deck bytes SHA-256: `{evidence['deck_bytes_sha256']}`\n"
        f"- canonical deck SHA-256: `{evidence['canonical_deck_hash']}`\n"
        f"- excluded members: `{len(evidence.get('excluded_members', []))}`\n"
        "- usage boundary: `local_eval_only`\n"
        "- submission bundle: prohibited\n"
        "- public kernel score is not performance evidence; only shared-engine CABT may be used.\n"
    )


def seal_kaggle_kernel_meta_v1(
    *,
    specs: Sequence[KernelSourceSpec],
    pool_manifest_path: Path | str,
    output_root: Path | str,
    source_epoch: str,
    seed_namespace: str,
    scan_roots: Sequence[Path | str] = (),
) -> dict[str, object]:
    """Safely seal a batch of public kernel tarballs into a fresh pool."""

    if not source_epoch.strip() or not seed_namespace.strip():
        raise KaggleKernelMetaError("source_epoch and seed_namespace must be non-empty")
    output = Path(output_root).resolve()
    if output.exists():
        raise FileExistsError(f"refusing to overwrite staged intake root: {output}")
    pool_path = Path(pool_manifest_path).resolve()
    rows = _pool_rows(pool_path)
    existing_ids = {str(row.get("id")) for row in rows if row.get("id")}
    # Older pool manifests used ``policy_hash`` for the original source
    # ``main.py`` bytes, while newer intake rows keep the staged wrapper and
    # source policy hashes separately.  Index the legacy value in both sets so
    # a new wrapper cannot hide an already-consumed source identity.
    existing_policy: set[str] = set()
    existing_source_policy: set[str] = set()
    for row in rows:
        for key in ("policy_hash", "policy_sha256", "staged_policy_sha256"):
            value = row.get(key)
            if value:
                existing_policy.add(str(value))
        for key in ("source_policy_sha256", "source_policy_hash", "policy_hash"):
            value = row.get(key)
            if value:
                existing_source_policy.add(str(value))
    existing_decks = {str(row.get("canonical_deck_hash")) for row in rows if row.get("canonical_deck_hash")}
    roots = tuple(Path(root).resolve() for root in scan_roots)
    official_ids = _official_ids(pool_path)
    ace_spec_ids = _official_ace_spec_ids(pool_path)
    report: dict[str, object] = {
        "schema_version": KAGGLE_KERNEL_META_SCHEMA_V1,
        "status": "BLOCKED_NO_SAFE_CANDIDATES",
        "source_epoch": source_epoch,
        "seed_namespace": seed_namespace,
        "accepted_ids": [],
        "rejections": {},
        "current_pool_manifest": str(pool_path),
        "current_pool_manifest_sha256": _sha256_file(pool_path),
        "freshness_scan_roots": [str(root) for root in roots],
        "network_access": False,
        "imports_executed": False,
        "git_mutation": False,
        "authority": {"training_allowed": False, "promotion_allowed": False, "submission_allowed": False, "longrun_allowed": False},
        "research_only": True,
    }
    output.mkdir(parents=True, exist_ok=False)
    accepted: list[dict[str, object]] = []
    accepted_identity: set[tuple[str, str]] = set()
    accepted_source_identity: set[tuple[str, str]] = set()
    rejected: dict[str, list[str]] = {}

    for raw_spec in specs:
        spec = raw_spec if isinstance(raw_spec, KernelSourceSpec) else KernelSourceSpec.from_mapping(raw_spec)  # type: ignore[arg-type]
        reasons: list[str] = []
        evidence: dict[str, object] = {
            "candidate_id": spec.candidate_id,
            "kernel_ref": spec.kernel_ref,
            "source_url": spec.source_url,
            "fetched_at_utc": spec.fetched_at_utc,
            "tar_sha256": spec.tar_sha256,
            "fresh": True,
            "unused_before_run": True,
            "source": KAGGLE_PUBLIC_SOURCE_V1,
            "usage_boundary": LOCAL_EVAL_ONLY_V1,
        }
        if spec.candidate_id in existing_ids:
            reasons.append("candidate_id_reused")
        validation_failed = False
        try:
            _validate_spec(spec)
        except KaggleKernelMetaError as exc:
            reasons.append(str(exc).split(": ", 1)[-1])
            validation_failed = True
        if validation_failed:
            rejected[spec.candidate_id] = reasons
            continue
        with tempfile.TemporaryDirectory(prefix=f"{spec.candidate_id}-", dir=str(output.parent)) as temporary:
            temp_payload = Path(temporary) / "payload"
            try:
                extracted = safe_extract_kernel_tar(spec, temp_payload)
                root_main = extracted["root_main_bytes"]
                deck_bytes = extracted["deck_bytes"]
                assert isinstance(root_main, bytes) and isinstance(deck_bytes, bytes)
                root_text = root_main.decode("utf-8", errors="strict")
                entrypoint_reason = _entrypoint_reason(root_text, spec.entrypoint_name)
                if entrypoint_reason is not None:
                    reasons.append(entrypoint_reason)
                all_findings: set[str] = set()
                imports: set[str] = set()
                for relative in extracted["python_files"]:
                    source_path = temp_payload / str(relative)
                    text = source_path.read_text(encoding="utf-8", errors="strict")
                    findings, source_imports = scan_source_text(text)
                    all_findings.update(findings)
                    imports.update(source_imports)
                if all_findings:
                    reasons.extend(sorted(all_findings))
                normalized = normalize_deck_text(
                    deck_bytes.decode("utf-8", errors="strict"),
                    source_id=spec.kernel_ref,
                    path="deck.csv",
                    official_ids=official_ids,
                )
                if normalized.get("eligibility") != "EXACT_60_VALID":
                    reasons.append("invalid_deck")
                cards = [int(value) for value in deck_bytes.decode("utf-8").replace(",", " ").split()]
                canonical = canonical_deck_sha256(cards) if len(cards) == 60 else ""
                ace_spec_cards = sorted({card for card in cards if card in ace_spec_ids})
                ace_spec_count = sum(1 for card in cards if card in ace_spec_ids)
                if ace_spec_ids and ace_spec_count != 1:
                    reasons.append("invalid_ace_spec_count")
                source_policy_sha = _sha256_bytes(root_main)
                evidence.update(
                    {
                        "source_policy_sha256": source_policy_sha,
                        "entrypoint_name": spec.entrypoint_name,
                        "deck_bytes_sha256": _sha256_bytes(deck_bytes),
                        "canonical_deck_hash": canonical,
                        "ace_spec_card_ids": ace_spec_cards,
                        "ace_spec_count": ace_spec_count if ace_spec_ids else None,
                        "ace_spec_validation": "LOCAL_CATALOG" if ace_spec_ids else "UNAVAILABLE",
                        "static_findings": sorted(all_findings),
                        "imports": sorted(imports),
                        "retained_members": extracted["retained_members"],
                        "excluded_members": extracted["excluded_members"],
                        "declared_member_count": extracted["declared_member_count"],
                        "declared_bytes": extracted["declared_bytes"],
                    }
                )
                wrapper_text = _wrapper_text(spec.candidate_id, spec.entrypoint_name)
                policy_sha = _sha256_bytes(wrapper_text.encode("utf-8"))
                identity = (policy_sha, canonical)
                source_identity = (source_policy_sha, canonical)
                if policy_sha in existing_policy:
                    reasons.append("policy_identity_reused")
                if source_policy_sha in existing_source_policy:
                    reasons.append("source_identity_reused")
                if canonical in existing_decks and policy_sha not in existing_policy:
                    # A different policy over an old deck is useful as a
                    # policy source and is not an exact identity reuse.
                    evidence["deck_identity_note"] = "deck_seen_with_different_policy"
                if identity in accepted_identity:
                    reasons.append("batch_identity_reused")
                if source_identity in accepted_source_identity:
                    reasons.append("batch_identity_reused")
                # A deck may legitimately be shared by distinct policies.  A
                # prior deck canonical hash is therefore provenance context,
                # not an identity collision by itself.  Reject only a reused
                # candidate id or staged policy hash; source-policy reuse is
                # checked separately below.
                if _artifact_hits(roots, (spec.candidate_id, policy_sha)):
                    reasons.append("artifact_identity_reused")
                if _artifact_hits(roots, (source_policy_sha,)):
                    reasons.append("source_identity_reused")
                if not reasons:
                    accepted.append(
                        {
                            "spec": spec,
                            "extracted": extracted,
                            "evidence": evidence,
                            "wrapper_text": wrapper_text,
                            "policy_sha": policy_sha,
                            "canonical": canonical,
                            "source_policy_sha": source_policy_sha,
                            "payload_files": [
                                (str(path.relative_to(temp_payload)), path.read_bytes())
                                for path in sorted(temp_payload.rglob("*"))
                                if path.is_file()
                            ],
                        }
                    )
                    accepted_identity.add(identity)
                    accepted_source_identity.add(source_identity)
                    continue
            except (KaggleKernelMetaError, UnicodeDecodeError, ValueError) as exc:
                reasons.append(str(exc).split(": ", 1)[-1])
        rejected[spec.candidate_id] = sorted(set(reasons)) or ["unsafe_source"]

    accepted.sort(key=lambda row: str(row["spec"].candidate_id))
    pool_rows: list[dict[str, object]] = []
    references: list[dict[str, object]] = []
    for item in accepted:
        spec = item["spec"]
        assert isinstance(spec, KernelSourceSpec)
        candidate_root = output / spec.candidate_id
        candidate_root.mkdir(parents=True, exist_ok=False)
        extracted = item["extracted"]
        payload_root = candidate_root / "payload"
        for relative, data in item["payload_files"]:
            destination = payload_root / str(relative)
            _write_new(destination, data)
        _write_new(candidate_root / "main.py", str(item["wrapper_text"]).encode("utf-8"))
        deck_bytes = extracted["deck_bytes"]
        assert isinstance(deck_bytes, bytes)
        _write_new(candidate_root / "deck.csv", deck_bytes)
        evidence = dict(item["evidence"])
        evidence["policy_sha256"] = item["policy_sha"]
        evidence_path = output / "evidence" / f"{spec.candidate_id}.json"
        _write_json_new(evidence_path, evidence)
        _write_new(candidate_root / "SOURCE.md", _source_note(spec, {**evidence, "policy_sha256": item["policy_sha"]}).encode("utf-8"))
        pool_rows.append(
            {
                "id": spec.candidate_id,
                "canonical_deck_hash": item["canonical"],
                "mean_decision_ms": None,
                "policy_hash": item["policy_sha"],
                "source_policy_sha256": item["source_policy_sha"],
                "smoke_ok": False,
                "source": KAGGLE_PUBLIC_SOURCE_V1,
                "source_branch": f"public_kaggle_kernel/{spec.kernel_ref}",
                "source_commit": spec.tar_sha256,
                "entrypoint_name": spec.entrypoint_name,
                "usage_boundary": LOCAL_EVAL_ONLY_V1,
                "asset_preflight": "STATIC_AND_EXACT_60",
            }
        )
        references.append(
            {
                "id": spec.candidate_id,
                "fresh": True,
                "unused_before_run": True,
                "freshness_evidence_sha256": _sha256_file(evidence_path),
                "freshness_evidence_path": str(Path("evidence") / evidence_path.name),
                "policy_sha256": item["policy_sha"],
                "canonical_deck_hash": item["canonical"],
                "source": KAGGLE_PUBLIC_SOURCE_V1,
                "entrypoint_name": spec.entrypoint_name,
            }
        )

    if pool_rows:
        pool_out = output / "pool_manifest.json"
        _write_json_new(pool_out, pool_rows)
        pool_sha = _sha256_file(pool_out)
        reference_ids = sorted(str(row["id"]) for row in references)
        seed_plan_sha = _sha256_bytes(_canonical_json({"source_epoch": source_epoch, "seed_namespace": seed_namespace, "reference_ids": reference_ids}))
        fresh_payload = {
            "schema_version": "meta-specialist-cg-fresh-meta-batch-v1",
            "batch_id": f"kaggle-{re.sub(r'[^A-Za-z0-9_.-]+', '-', source_epoch)}-{re.sub(r'[^A-Za-z0-9_.-]+', '-', seed_namespace)}",
            "source_epoch": source_epoch,
            "seed_namespace": seed_namespace,
            "seed_plan_sha256": seed_plan_sha,
            "reference_ids": reference_ids,
            "pool_manifest_sha256": pool_sha,
            "freshness_basis": "locally acquired public kernel tar; current pool and configured performance artifact roots identity scan",
            "references": sorted(references, key=lambda row: str(row["id"])),
            "authority": {"training_allowed": False, "promotion_allowed": False, "submission_allowed": False, "longrun_allowed": False},
            "research_only": True,
        }
        _write_json_new(output / "fresh_meta.json", fresh_payload)
        report.update(
            {
                "status": "SEALED",
                "accepted_ids": [str(row["id"]) for row in pool_rows],
                "pool_manifest_path": str(pool_out),
                "pool_manifest_sha256": pool_sha,
                "fresh_meta_path": str(output / "fresh_meta.json"),
                "fresh_meta_sha256": _sha256_file(output / "fresh_meta.json"),
            }
        )
    report["accepted_count"] = len(accepted)
    report["rejected_count"] = len(rejected)
    report["rejections"] = rejected
    _write_json_new(output / "intake_report.json", report)
    return report


def validate_kernel_specs(specs: Sequence[KernelSourceSpec]) -> dict[str, object]:
    """Validate local inputs without extraction or network access."""

    rows: list[dict[str, object]] = []
    for spec in specs:
        _validate_spec(spec)
        rows.append({**asdict(spec), "tar_path": str(spec.tar_path), "actual_tar_sha256": _sha256_file(spec.tar_path)})
        if rows[-1]["actual_tar_sha256"] != spec.tar_sha256:
            raise KaggleKernelMetaError(f"{spec.candidate_id}: tar SHA-256 mismatch")
    return {"schema_version": KAGGLE_KERNEL_META_SCHEMA_V1, "sources": rows, "network_access": False, "imports_executed": False}


__all__ = [
    "KAGGLE_KERNEL_META_SCHEMA_V1",
    "KAGGLE_PUBLIC_SOURCE_V1",
    "KaggleKernelMetaError",
    "KernelSourceSpec",
    "LOCAL_EVAL_ONLY_V1",
    "load_candidate_agent",
    "safe_extract_kernel_tar",
    "scan_source_text",
    "seal_kaggle_kernel_meta_v1",
    "validate_kernel_specs",
    "write_candidate_wrapper",
]
