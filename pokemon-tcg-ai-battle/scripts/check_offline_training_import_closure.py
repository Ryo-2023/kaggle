#!/usr/bin/env python3
"""Check the recursive import closure for Offline Training v1.

Verifies that all internal mage_ptcg modules imported directly or indirectly
by the offline training package exist on disk.
"""

from __future__ import annotations

import ast
import importlib
import os
import sys
from pathlib import Path

# Setup paths
REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"

# Add src to sys.path so we can do relative lookups if needed
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


def get_imports(file_path: Path) -> set[str]:
    """Parse a file using AST and return all imported module names."""
    try:
        content = file_path.read_text(encoding="utf-8")
        tree = ast.parse(content, filename=str(file_path))
    except Exception as exc:
        print(f"[-] Failed to parse {file_path.relative_to(REPO_ROOT)}: {exc}")
        return set()

    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for name in node.names:
                imports.add(name.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.add(node.module)
                # If importing a specific submodule/class/function, also add the full path
                for name in node.names:
                    imports.add(f"{node.module}.{name.name}")
    return imports


def resolve_internal_module_path(module_name: str) -> Path | None:
    """Find the source .py file or __init__.py for a mage_ptcg.* module."""
    parts = module_name.split(".")
    if not parts or parts[0] != "mage_ptcg":
        return None

    # Try exact file mapping
    rel_path = Path(*parts)
    py_file = SRC_DIR / (rel_path.as_posix() + ".py")
    init_file = SRC_DIR / rel_path / "__init__.py"

    if py_file.is_file():
        return py_file
    if init_file.is_file():
        return init_file

    # If not found, try to resolve parent modules (in case it is a class/function import)
    for i in range(1, len(parts)):
        parent_module = ".".join(parts[:-i])
        parent_path = resolve_internal_module_path(parent_module)
        if parent_path and parent_path.is_file():
            return parent_path

    return None


def classify_module(module_name: str) -> str:
    """Classify a module as 'internal', 'stdlib', 'optional', or 'third-party'."""
    base_name = module_name.split(".")[0]
    if base_name == "mage_ptcg":
        return "internal"

    # Standard library check
    stdlib_names = getattr(sys, "stdlib_module_names", set())
    if stdlib_names:
        if base_name in stdlib_names or base_name in sys.builtin_module_names:
            return "stdlib"
    else:
        # Fallback stdlib list
        COMMON_STDLIB = {
            "sys", "os", "pathlib", "json", "hashlib", "time", "random", "math",
            "argparse", "subprocess", "typing", "contextlib", "dataclasses",
            "socket", "tempfile", "gzip", "tarfile", "io", "importlib",
            "collections", "pkgutil", "logging", "abc", "shutil", "traceback",
            "struct", "warnings", "fnmatch", "functools", "inspect", "re"
        }
        if base_name in COMMON_STDLIB or base_name in sys.builtin_module_names:
            return "stdlib"

    # Optional requirements for this pipeline (allowed to fail import under check)
    OPTIONAL = {"torch", "kaggle_environments", "cabt"}
    if base_name in OPTIONAL:
        return "optional"

    return "third-party"


def check_closure() -> bool:
    # 1. Define entry point files
    entry_points: list[Path] = []

    # Add offline_training modules
    offline_training_dir = SRC_DIR / "mage_ptcg" / "offline_training"
    if offline_training_dir.is_dir():
        entry_points.extend(offline_training_dir.glob("**/*.py"))

    # Add key scripts
    script_v1 = REPO_ROOT / "scripts" / "run_offline_training_v1.py"
    if script_v1.is_file():
        entry_points.append(script_v1)

    # Add offline training tests
    test_file = REPO_ROOT / "tests" / "test_offline_training_v1.py"
    if test_file.is_file():
        entry_points.append(test_file)

    # 2. Trace dependencies recursively
    visited_modules: set[str] = set()
    visited_files: set[Path] = set()
    missing_internal: set[str] = set()

    external_by_category: dict[str, set[str]] = {
        "stdlib": set(),
        "optional": set(),
        "third-party": set()
    }

    # Queue of files to analyze: starts with entry points
    queue: list[Path] = [p.resolve() for p in entry_points if p.is_file()]
    visited_files.update(queue)

    print(f"[*] Starting closure analysis with {len(queue)} entry point files...")

    while queue:
        current_file = queue.pop(0)
        imports = get_imports(current_file)

        for imp in imports:
            category = classify_module(imp)
            if category == "internal":
                # Find the source file of this internal module
                resolved_path = resolve_internal_module_path(imp)
                if resolved_path is None:
                    # Could not resolve on disk -> missing
                    missing_internal.add(imp)
                else:
                    resolved_path = resolved_path.resolve()
                    if resolved_path not in visited_files:
                        visited_files.add(resolved_path)
                        queue.append(resolved_path)
            else:
                external_by_category[category].add(imp.split(".")[0])

    # 3. Print report
    print("\n=== OFFLINE TRAINING V1 DEPENDENCY CLOSURE REPORT ===")
    print(f"Analyzed {len(visited_files)} internal files.")

    print("\n[+] Standard Library Modules detected:")
    print("  " + ", ".join(sorted(external_by_category["stdlib"])))

    print("\n[+] Optional Modules detected:")
    print("  " + ", ".join(sorted(external_by_category["optional"])))

    print("\n[+] Third-Party Modules detected:")
    print("  " + ", ".join(sorted(external_by_category["third-party"])))

    # Verify if third-party modules can be imported
    unresolvable_third_party = set()
    for tp in external_by_category["third-party"]:
        try:
            importlib.import_module(tp)
        except ImportError:
            unresolvable_third_party.add(tp)

    if unresolvable_third_party:
        print("\n[!] WARNING: Unresolvable Third-Party Modules (are they installed?):")
        print("  " + ", ".join(sorted(unresolvable_third_party)))

    if missing_internal:
        print("\n[-] ERROR: Missing Internal mage_ptcg Modules:")
        for missing in sorted(missing_internal):
            print(f"  * {missing}")
        return False
    else:
        print("\n[+] SUCCESS: Internal dependency closure is COMPLETE. No missing internal modules.")
        return True


if __name__ == "__main__":
    success = check_closure()
    sys.exit(0 if success else 1)
