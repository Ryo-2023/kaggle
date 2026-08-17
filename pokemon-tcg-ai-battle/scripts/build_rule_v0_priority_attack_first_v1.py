"""Materialize a research-only Rule v0 priority variant.

The generated module delegates deck registration to the repository's existing
``main.make_deterministic_agent`` factory.  It is intentionally a separate
source artifact: production ``main.py`` and ``agents/rule_agent.py`` remain
unchanged, while the generated policy can be hash-bound into the alternating
evaluator.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path


PRIORITY_ATTACK_FIRST_V1 = (
    "ATTACK",
    "PLAY",
    "ATTACH",
    "EVOLVE",
    "ABILITY",
    "END",
)
VARIANT_V1 = "rule-v0-main-priority-attack-first-v1"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _generated_source(repo_root: Path) -> str:
    root_literal = json.dumps(str(repo_root.resolve()), ensure_ascii=False)
    return f'''"""Research-only Rule v0 priority variant: ATTACK first."""

from __future__ import annotations

import importlib.util
from pathlib import Path


_REPO_ROOT = Path({root_literal})
_ROOT_MAIN_PATH = _REPO_ROOT / "main.py"
_SPEC = importlib.util.spec_from_file_location(
    "_mage_rule_v0_priority_root_main", _ROOT_MAIN_PATH
)
if _SPEC is None or _SPEC.loader is None:
    raise ImportError("cannot load the hash-bound root main.py")
_ROOT_MAIN = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_ROOT_MAIN)
_BASE_AGENT = _ROOT_MAIN.make_deterministic_agent(
    deck_path=_REPO_ROOT / "deck.csv"
)


def agent(obs_dict: dict) -> list[int]:
    """Return legal indices using the fixed deterministic priority order."""
    return list(_BASE_AGENT(obs_dict))


agent.__name__ = "rule_v0_main_priority_attack_first_v1"
'''


def _write_exclusive(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(f"refusing to replace existing policy: {{path}}")
    temporary = path.with_name(f".{{path.name}}.tmp-{{os.getpid()}}")
    try:
        with temporary.open("xb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def build_rule_v0_priority_attack_first_source_v1(
    *, output_path: Path | str, repo_root: Path | str
) -> dict[str, object]:
    """Write one immutable policy source and return its identity manifest."""

    root = Path(repo_root).resolve()
    main_path = root / "main.py"
    deck_path = root / "deck.csv"
    if not main_path.is_file() or not deck_path.is_file():
        raise FileNotFoundError("repo_root must contain main.py and deck.csv")
    output = Path(output_path).resolve()
    raw = _generated_source(root).encode("utf-8")
    _write_exclusive(output, raw)
    return {
        "schema_version": "meta-specialist-rule-v0-priority-source-v1",
        "variant": VARIANT_V1,
        "priority": list(PRIORITY_ATTACK_FIRST_V1),
        "source_path": str(output),
        "policy_sha256": hashlib.sha256(raw).hexdigest(),
        "root_main_sha256": _sha256(main_path),
        "root_deck_sha256": _sha256(deck_path),
        "production_mutated": False,
        "research_only": True,
        "authority": {
            "execute_allowed": False,
            "training_allowed": False,
            "promotion_allowed": False,
            "submission_allowed": False,
            "longrun_allowed": False,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)
    manifest = build_rule_v0_priority_attack_first_source_v1(
        output_path=args.output,
        repo_root=args.repo_root,
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
