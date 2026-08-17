"""Allow-list Runtime Closure Builder for O6 native agent bundles.

O6-AUD-001 (HIGH): the old bundle builder copied every regular file under a
pinned source tree, including docs/tests/report/experiments/data and all
four cross-platform native binaries. This module replaces that "copy
everything" step with an allow-list closure computed from three independent
signals, combined:

1. A static Python import graph walked from the entrypoint file (``ast``,
   no execution).
2. A dynamic runtime trace of the *actual* files opened and native
   libraries ``dlopen``-ed while running one real deck-build + one full
   CABT game, captured with ``sys.addaudithook`` (``"open"`` and
   ``"ctypes.dlopen"`` events) inside the same kind of isolated subprocess
   already used for CABT smoke validation.
3. A small set of explicit, always-required artifacts (entrypoint file,
   top-level ``deck.csv``/``deck.txt``).

Every candidate from (1)-(3) is still passed through a hard deny-list
category check (docs/tests/report/experiments/data/cache/vcs/credential/
foreign-platform-binary/...): a source that somehow made e.g. a ``tests/``
file statically or dynamically reachable would be recorded as ``blocked``,
never silently bundled. This makes the allow-list the source of truth while
keeping the deny-list as defense in depth, per docs/plan/... Phase A intent.

Known limitation (documented, not hidden): the dynamic trace only covers
the exact code path exercised by one deck-build call plus one full game: a
source with a rarely-hit runtime branch that imports a module rarely enough
to bypass the static graph too (e.g. via ``importlib.import_module`` with a
string built from data) could be missed by both signals. There is no
practical purely-static Python solution to that; if it ever matters, a
future improvement is to play a small batch of additional CABT smoke games
with more RNG diversity before freezing the closure.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from .errors import OpponentError

CLOSURE_REPORT_SCHEMA_VERSION = "o6-runtime-closure-report-v1"
RUNTIME_CONTRACT_SCHEMA_VERSION = "o6-runtime-contract-v1"

# host-provided at interpreter start: never candidates for bundling, and
# never reported as "unresolved_third_party" (that bucket is reserved for
# names that are *not* recognizable stdlib, i.e. a real signal of a missing
# host package declaration).
_STDLIB_MODULE_NAMES: frozenset[str] = frozenset(getattr(sys, "stdlib_module_names", ())) | {
    "__future__", "_thread", "_socket", "posixpath", "ntpath", "genericpath",
}

_NATIVE_BINARY_SUFFIXES = {".so", ".dll", ".dylib"}

# (category, matcher) — matcher receives the tuple of path parts (posix,
# relative to source root) and returns True if this file belongs to the
# category.  Order matters only for reporting; every candidate is checked
# against every category.
def _forbidden_category(relpath: PurePosixPath) -> str | None:
    parts = relpath.parts
    lower_parts = [p.lower() for p in parts]
    name = parts[-1] if parts else ""
    lower_name = name.lower()
    if parts and parts[0] == ".git":
        return "vcs"
    if any(p in {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"} for p in parts):
        return "cache"
    if "docs" in lower_parts[:-1] or (lower_parts and lower_parts[0] == "docs"):
        return "docs"
    if "tests" in lower_parts[:-1] or (lower_parts and lower_parts[0] == "tests") or re.match(r"test_.*\.py$", lower_name):
        return "tests"
    if "report" in lower_parts[:-1] or (lower_parts and lower_parts[0] == "report"):
        return "report"
    if "experiments" in lower_parts[:-1] or (lower_parts and lower_parts[0] == "experiments"):
        return "experiments"
    if "data" in lower_parts[:-1] or (lower_parts and lower_parts[0] == "data"):
        return "data"
    if lower_name.endswith(".ipynb"):
        return "notebooks"
    if re.search(r"(credential|secret|kaggle\.json$|\.env$|\.pem$|id_rsa)", lower_name):
        return "credential"
    if lower_name in {"readme.md", "readme.rst", "readme.txt", "readme"}:
        return "readme"
    if lower_name in {".bash_history", ".zsh_history", ".python_history"}:
        return "shell_history"
    return None


def _resolve_module_to_files(module_name: str, root: Path) -> list[Path] | None:
    """Resolve a dotted module name to its file plus package ``__init__.py`` chain.

    Returns ``None`` if the module does not resolve to any file under
    ``root`` (i.e. it is stdlib or a third-party host package).
    """
    parts = module_name.split(".")
    chain: list[Path] = []
    package_dir = root
    for index, part in enumerate(parts):
        is_last = index == len(parts) - 1
        candidate_dir = package_dir / part
        if is_last:
            py_file = candidate_dir.with_suffix(".py")
            if py_file.is_file():
                chain.append(py_file)
                return chain
            init_file = candidate_dir / "__init__.py"
            if init_file.is_file():
                chain.append(init_file)
                return chain
            return None
        init_file = candidate_dir / "__init__.py"
        if not init_file.is_file():
            return None
        chain.append(init_file)
        package_dir = candidate_dir
    return None


def _resolve_relative_import(current_file: Path, level: int, module: str | None, root: Path) -> list[Path] | None:
    package_dir = current_file.parent
    for _ in range(level - 1):
        package_dir = package_dir.parent
    if package_dir != root and root not in package_dir.parents and package_dir != root:
        pass  # allow; still bounded by root below
    if module:
        resolved = _resolve_module_to_files(module, package_dir) if package_dir == root else None
        if resolved is not None:
            return resolved
        # package_dir may itself be nested; resolve module relative to it directly.
        candidate = package_dir.joinpath(*module.split("."))
        py_file = candidate.with_suffix(".py")
        if py_file.is_file():
            return [py_file]
        init_file = candidate / "__init__.py"
        if init_file.is_file():
            return [init_file]
        return None
    init_file = package_dir / "__init__.py"
    return [init_file] if init_file.is_file() else None


def static_import_closure(entry_file: Path, root: Path) -> tuple[set[Path], dict[str, list[str]]]:
    """Walk the static Python import graph from ``entry_file``.

    Returns ``(resolved_local_files, unresolved)`` where ``unresolved`` is
    ``{"stdlib": [...], "unknown_third_party": [...]}`` -- the latter is a
    genuine signal that the source needs an undeclared host package.
    """
    root = root.resolve()
    visited: set[Path] = set()
    stack = [entry_file.resolve()]
    unresolved_stdlib: set[str] = set()
    unresolved_unknown: set[str] = set()
    while stack:
        current = stack.pop()
        if current in visited or not current.is_file():
            continue
        visited.add(current)
        try:
            tree = ast.parse(current.read_text(encoding="utf-8", errors="replace"), filename=str(current))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    resolved = _resolve_module_to_files(alias.name, root)
                    if resolved:
                        stack.extend(resolved)
                    elif top in _STDLIB_MODULE_NAMES:
                        unresolved_stdlib.add(alias.name)
                    else:
                        unresolved_unknown.add(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.level and node.level > 0:
                    resolved = _resolve_relative_import(current, node.level, node.module, root)
                    if resolved:
                        stack.extend(resolved)
                    else:
                        unresolved_unknown.add(f"{'.' * node.level}{node.module or ''}")
                elif node.module:
                    top = node.module.split(".")[0]
                    resolved = _resolve_module_to_files(node.module, root)
                    if resolved:
                        stack.extend(resolved)
                    elif top in _STDLIB_MODULE_NAMES:
                        unresolved_stdlib.add(node.module)
                    else:
                        unresolved_unknown.add(node.module)
    return visited, {"stdlib": sorted(unresolved_stdlib), "unknown_third_party": sorted(unresolved_unknown)}


_CLOSURE_TRACE_HARNESS = r"""import importlib.util, inspect, json, os, sys
os.environ['HOME'] = sys.argv[4]
root, rel, name, _home = sys.argv[1:5]
root = os.path.abspath(root)
opened, dlopened = set(), set()
def _hook(event, args):
    if event == 'open' and args:
        p = args[0]
        if isinstance(p, bytes):
            p = p.decode('utf-8', 'replace')
        if isinstance(p, str):
            ap = os.path.abspath(p)
            if ap.startswith(root + os.sep):
                opened.add(ap)
    elif event in ('ctypes.dlopen', 'ctypes.dlsym', 'ctypes.dlsym/handle') and args:
        p = args[0]
        if isinstance(p, bytes):
            p = p.decode('utf-8', 'replace')
        if isinstance(p, str) and os.path.isabs(p) and p.startswith(root + os.sep):
            dlopened.add(p)
sys.addaudithook(_hook)
from kaggle_environments import make
sys.path.insert(0, root)
spec = importlib.util.spec_from_file_location('o6_closure_agent', os.path.join(root, rel))
module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
agent = getattr(module, name)
def invoke(observation, configuration=None):
    return agent(observation, configuration) if len(inspect.signature(agent).parameters) >= 2 else agent(observation)
deck = invoke({'logs': [], 'current': None, 'select': None})
first = invoke({'logs': [], 'current': None, 'select': None})
environment = make('cabt', configuration={'decks': [deck, deck]})
environment.run([invoke, invoke])
states = [str(state.status) for state in environment.state]
config_keys = sorted(environment.configuration.keys()) if hasattr(environment.configuration, 'keys') else []
result = {
    'deck_length': len(deck) if isinstance(deck, list) else None,
    'deck_replay_equal': deck == first,
    'states': states,
    'steps': len(environment.steps),
    'opened_paths': sorted(os.path.relpath(p, root) for p in opened),
    'dlopened_paths': sorted(os.path.relpath(p, root) for p in dlopened),
    'cabt_configuration_keys': config_keys,
}
print('O6_CLOSURE_TRACE=' + json.dumps(result, separators=(',', ':')))
"""


def _run_closure_trace(source_dir: Path, module_path: str, callable_name: str, *, timeout_seconds: float) -> dict[str, Any]:
    """Run the instrumented harness once; return raw, uninterpreted evidence.

    Mirrors the isolation discipline of ``core._run_isolated_cabt_smoke``
    (own temp ``HOME``, ``cwd`` pinned to the materialized source, no
    implicit dependency installation) but additionally reports the dynamic
    file-open / dlopen trace instead of only pass/fail evidence.
    """
    started = time.monotonic()
    environment = {"PATH": os.environ.get("PATH", ""), "PYTHONDONTWRITEBYTECODE": "1", "HOME": os.environ.get("HOME", "")}
    isolated_home = tempfile.mkdtemp(prefix="o6-closure-home-")
    try:
        completed = subprocess.run(
            [sys.executable, "-c", _CLOSURE_TRACE_HARNESS, str(source_dir), module_path, callable_name, isolated_home],
            cwd=source_dir, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment,
            timeout=timeout_seconds, check=False)
    except subprocess.TimeoutExpired:
        return {"outcome": "TIMEOUT", "runtime_seconds": round(time.monotonic() - started, 3)}
    finally:
        shutil.rmtree(isolated_home, ignore_errors=True)
    runtime_seconds = round(time.monotonic() - started, 3)
    lines = [line for line in completed.stdout.splitlines() if line.startswith("O6_CLOSURE_TRACE=")]
    if completed.returncode or not lines:
        missing = re.search(r"ModuleNotFoundError: No module named ['\"]([^'\"]+)", completed.stderr)
        tail = completed.stderr.strip().splitlines()[-1:] or completed.stdout.strip().splitlines()[-1:]
        return {"outcome": "BLOCKED_DEPENDENCY" if missing else "RUNTIME_ERROR", "missing_dependency": missing.group(1) if missing else None,
                "runtime_error": tail[0][:240] if tail else "no closure trace sentinel", "runtime_seconds": runtime_seconds}
    return {"outcome": "RAN", "trace": json.loads(lines[-1].partition("=")[2]), "runtime_seconds": runtime_seconds}


def _elf_metadata(path: Path) -> dict[str, Any]:
    info: dict[str, Any] = {"elf_interpreter": None, "required_shared_libraries": [], "glibc_symbol_versions": [], "inspection_tool": None, "inspection_note": None}
    if shutil.which("readelf") is None:
        info["inspection_note"] = "readelf not available on this host; ELF metadata not collected"
        return info
    info["inspection_tool"] = "readelf"
    interp = subprocess.run(["readelf", "-p", ".interp", str(path)], capture_output=True, text=True, check=False)
    match = re.search(r"\[\s*\d+\]\s+(\S+)", interp.stdout)
    if match:
        info["elf_interpreter"] = match.group(1)
    else:
        info["inspection_note"] = "no .interp section: this is a dlopen()-loaded shared object, not a linked executable"
    needed = subprocess.run(["readelf", "-d", str(path)], capture_output=True, text=True, check=False)
    info["required_shared_libraries"] = sorted(set(re.findall(r"\(NEEDED\)\s+Shared library: \[([^\]]+)\]", needed.stdout)))
    verneed = subprocess.run(["readelf", "-V", str(path)], capture_output=True, text=True, check=False)
    info["glibc_symbol_versions"] = sorted(set(re.findall(r"(GLIBC_[0-9.]+)", verneed.stdout)))
    return info


def _native_binary_metadata(path: Path, *, relpath: str) -> dict[str, Any]:
    data = path.read_bytes()
    metadata: dict[str, Any] = {
        "path": relpath, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data),
        "build_host_platform_system": platform.system(), "build_host_platform_machine": platform.machine(),
        "runtime_usage": "CONFIRMED_DLOPEN_DURING_CLOSURE_TRACE",
    }
    if data[:4] == b"\x7fELF":
        metadata["format"] = "ELF"
        metadata.update(_elf_metadata(path))
    else:
        metadata["format"] = "OTHER"
        metadata["inspection_note"] = "not an ELF object on this host; PE/.dll and Mach-O/.dylib metadata are not parsed here"
    return metadata


def build_runtime_closure(*, source_root: Path, entrypoint: str, agent_id: str, scratch_root: Path, timeout_seconds: float = 70.0) -> dict[str, Any]:
    """Compute the minimal allow-list runtime closure for one materialized source tree.

    Returns ``{"files": {relpath: bytes}, "report": {...}}``. ``files`` keys
    are posix-relative to ``source_root`` and are exactly the set that
    should be bundled under ``runtime/<agent_id>/source/``. ``report`` is
    the machine-readable required/optional/excluded/blocked/unresolved
    record plus native binary and dynamic-trace evidence.
    """
    source_root = Path(source_root).resolve()
    module_path, _, callable_name = entrypoint.partition(":")
    if not callable_name:
        raise OpponentError("entrypoint must be relative_file.py:callable")
    entry_file = source_root / module_path
    if not entry_file.is_file():
        raise OpponentError(f"entrypoint file not found in materialized source: {module_path}")

    all_files = sorted(p for p in source_root.rglob("*") if p.is_file())
    all_relpaths = {p.relative_to(source_root).as_posix(): p for p in all_files}

    static_files, unresolved = static_import_closure(entry_file, source_root)
    static_relpaths = {p.relative_to(source_root).as_posix() for p in static_files if source_root in p.parents or p == source_root}

    scratch_root = Path(scratch_root)
    scratch_root.mkdir(parents=True, exist_ok=True)
    trace_outcome = _run_closure_trace(source_root, module_path, callable_name, timeout_seconds=timeout_seconds)
    if trace_outcome["outcome"] != "RAN":
        raise OpponentError(f"closure trace did not run to completion: {trace_outcome['outcome']} ({trace_outcome.get('runtime_error') or trace_outcome.get('missing_dependency')})")
    trace = trace_outcome["trace"]
    dynamic_relpaths = {p for p in trace.get("opened_paths", []) if p in all_relpaths}
    dlopen_relpaths = {p for p in trace.get("dlopened_paths", []) if p in all_relpaths}

    explicit_required: set[str] = {module_path}
    for deck_name in ("deck.csv", "deck.txt"):
        if deck_name in all_relpaths:
            explicit_required.add(deck_name)

    candidate_required = static_relpaths | dynamic_relpaths | dlopen_relpaths | explicit_required
    required_reasons: dict[str, list[str]] = {}
    for relpath in candidate_required:
        reasons = []
        if relpath == module_path:
            reasons.append("entrypoint")
        if relpath in explicit_required and relpath != module_path:
            reasons.append("declared_deck_artifact")
        if relpath in static_relpaths:
            reasons.append("static_import_closure")
        if relpath in dynamic_relpaths:
            reasons.append("dynamic_file_open_trace")
        if relpath in dlopen_relpaths:
            reasons.append("dynamic_ctypes_dlopen_trace")
        required_reasons[relpath] = reasons

    blocked: list[dict[str, str]] = []
    included: set[str] = set()
    for relpath in sorted(candidate_required):
        category = _forbidden_category(PurePosixPath(relpath))
        if category is not None:
            blocked.append({"path": relpath, "category": category, "reason": "reachable via import/dynamic-trace/deck-declaration but matches a hard deny-list category"})
            continue
        included.add(relpath)

    native_included: list[dict[str, Any]] = []
    native_excluded: list[dict[str, Any]] = []
    for relpath in sorted(all_relpaths):
        if PurePosixPath(relpath).suffix.lower() not in _NATIVE_BINARY_SUFFIXES:
            continue
        if relpath in included:
            native_included.append(_native_binary_metadata(all_relpaths[relpath], relpath=relpath))
        else:
            native_excluded.append({"path": relpath, "reason": "not dlopen()-ed during the closure trace on this build host" if relpath not in dlopen_relpaths else "blocked by deny-list category"})

    excluded: list[dict[str, str]] = []
    for relpath in sorted(all_relpaths):
        if relpath in included:
            continue
        if any(entry["path"] == relpath for entry in blocked):
            continue
        category = _forbidden_category(PurePosixPath(relpath)) or "not_in_runtime_closure"
        excluded.append({"path": relpath, "category": category})

    files = {relpath: all_relpaths[relpath].read_bytes() for relpath in sorted(included)}

    report = {
        "schema_version": CLOSURE_REPORT_SCHEMA_VERSION,
        "agent_id": agent_id,
        "entrypoint": entrypoint,
        "host_platform": {"system": platform.system(), "machine": platform.machine()},
        "required": sorted(included),
        "required_reasons": required_reasons,
        "optional": [],
        "excluded": excluded,
        "blocked": blocked,
        "unresolved_imports": unresolved,
        "native_binaries": {"included": native_included, "excluded_not_used_on_build_host": native_excluded},
        "dynamic_trace": {
            "opened_paths": sorted(dynamic_relpaths), "dlopened_paths": sorted(dlopen_relpaths),
            "cabt_configuration_keys": trace.get("cabt_configuration_keys", []), "runtime_seconds": trace_outcome.get("runtime_seconds"),
        },
        "file_count_before": len(all_relpaths), "file_count_after": len(included),
    }
    return {"files": files, "report": report}


def build_runtime_contract(*, python_version_required: str, kaggle_environments_version: str, cabt_version: str, required_host_packages: Iterable[str]) -> dict[str, Any]:
    """Declare what a fresh client's host must provide to execute this bundle.

    The bundled agent code's own direct Python imports are stdlib-only
    (verified by the closure's ``unresolved_imports.unknown_third_party``
    being empty); executing it at all requires a host-provided
    ``kaggle_environments`` distribution exposing the ``cabt`` environment,
    a compatible interpreter, and (transitively, via ``ctypes``) a
    compatible native runtime for the bundled ``.so``/``.dll``/``.dylib``.
    That combination is why this is classified
    ``SOURCE_PORTABLE_HOST_CONTRACT_REQUIRED`` rather than
    ``SELF_CONTAINED``: the bundle is complete and self-contained for its
    *own* code, but cannot execute without a compatible host runtime.
    """
    return {
        "schema_version": RUNTIME_CONTRACT_SCHEMA_VERSION,
        "classification": "SOURCE_PORTABLE_HOST_CONTRACT_REQUIRED",
        "python_version_required": python_version_required,
        "kaggle_environments_version_required": kaggle_environments_version,
        "cabt_version_required": cabt_version,
        "required_host_packages": sorted(set(required_host_packages)),
        "version_constraint_policy": "exact runtime major.minor Python match recommended; kaggle_environments/cabt must expose the identical 'cabt' environment id used to build this bundle",
        "availability_probe": "import kaggle_environments; kaggle_environments.make('cabt', configuration={'decks': [[], []]})",
        "incompatible_host_behavior": "fail_closed: a missing or import-incompatible host package makes the isolated subprocess exit non-zero, which the harness reports as BLOCKED_DEPENDENCY; no implicit pip install or fallback interpreter is attempted",
        "not_self_contained_reason": "bundled agent code has zero unresolved third-party Python imports (stdlib only), but game execution itself is always driven through a host-provided kaggle_environments 'cabt' sandbox, which is never vendored into the bundle",
    }
