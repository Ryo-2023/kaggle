"""Bounded public-state variants layered on the fixed P1 cg policy.

The P1 lethal-target source is immutable.  Each variant appends one small
public-state score adjustment and keeps every unsupported or malformed state on
the P1 exact fallback.  These packages are research-only and never replace
the submission branch.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil


ROOT = Path(__file__).resolve().parents[3]
BASE_SOURCE_PATH = ROOT / "runs/final-sprint-autonomous/cg-policy-screen-v1-retry-safe4-20260814/candidates/cg-lethal-target-v1/package/main.py"
BASE_SOURCE_SHA256 = "1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9"
VARIANT_IDS = (
    "cg-lethal-retreat-damage-v2",
    "cg-lethal-attach-threshold-v2",
    "cg-lethal-overkill-conservation-v2",
)


_PATCHES: dict[str, str] = {
    "cg-lethal-retreat-damage-v2": r'''

# RESEARCH_VARIANT: cg-lethal-retreat-damage-v2
# P1 base + one bounded public retreat preference.  The visible active HP and
# powered bench are public; every other state follows the exact P1 scorer.
_CG_P1_BASE_MAIN_SCORE = _main_score
_CG_P1_BASE_MAIN_SOURCE_SHA256 = "1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9"

def _main_score(obs, option: object) -> int:
    if getattr(obs, "select", None) is None or getattr(option, "type", None) is None:
        return 0
    try:
        score = int(_CG_P1_BASE_MAIN_SCORE(obs, option))
    except Exception:
        return 0
    if getattr(option, "type", None) != OptionType.RETREAT:
        return score
    try:
        active = _mine(obs).active[0] if _mine(obs).active else None
        damage = _damage(active)
        powered_bench = any(_energy_count(card) >= 2 for card in (_mine(obs).bench or []))
        if active is not None and damage >= 100 and powered_bench:
            return score + 12000
    except Exception:
        return score
    return score

_CG_P1_BASE_SCORE = _score

def _score(obs, option: object) -> int:
    if getattr(obs, "select", None) is None or getattr(option, "type", None) is None:
        return 0
    try:
        return int(_CG_P1_BASE_SCORE(obs, option))
    except Exception:
        return 0

_CG_P1_BASE_AGENT = agent

def agent(obs_dict: dict) -> list[int]:
    return _CG_P1_BASE_AGENT(obs_dict)
''',
    "cg-lethal-attach-threshold-v2": r'''

# RESEARCH_VARIANT: cg-lethal-attach-threshold-v2
# P1 base + one bounded public attachment preference for a one-energy Mega
# Lucario.  Unsupported targets use the exact P1 scorer.
_CG_P1_BASE_MAIN_SCORE = _main_score
_CG_P1_BASE_MAIN_SOURCE_SHA256 = "1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9"

def _main_score(obs, option: object) -> int:
    if getattr(obs, "select", None) is None or getattr(option, "type", None) is None:
        return 0
    try:
        score = int(_CG_P1_BASE_MAIN_SCORE(obs, option))
    except Exception:
        return 0
    if getattr(option, "type", None) != OptionType.ATTACH:
        return score
    try:
        if _option_card_id(obs, option) != FIGHTING:
            return score
        target = _target_for_option(obs, option)
        if getattr(target, "id", None) == MEGA_LUCARIO and _energy_count(target) == 1:
            return score + 12000
    except Exception:
        return score
    return score

_CG_P1_BASE_SCORE = _score

def _score(obs, option: object) -> int:
    if getattr(obs, "select", None) is None or getattr(option, "type", None) is None:
        return 0
    try:
        return int(_CG_P1_BASE_SCORE(obs, option))
    except Exception:
        return 0

_CG_P1_BASE_AGENT = agent

def agent(obs_dict: dict) -> list[int]:
    return _CG_P1_BASE_AGENT(obs_dict)
''',
    "cg-lethal-overkill-conservation-v2": r'''

# RESEARCH_VARIANT: cg-lethal-overkill-conservation-v2
# P1 lethal target remains the base.  A bounded penalty only separates attacks
# with substantial visible overkill; all malformed/publicly unsupported state
# follows the exact P1 scorer.
_CG_P1_BASE_MAIN_SCORE = _main_score
_CG_P1_BASE_MAIN_SOURCE_SHA256 = "1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9"

def _main_score(obs, option: object) -> int:
    if getattr(obs, "select", None) is None or getattr(option, "type", None) is None:
        return 0
    try:
        score = int(_CG_P1_BASE_MAIN_SCORE(obs, option))
    except Exception:
        return 0
    if getattr(option, "type", None) != OptionType.ATTACK:
        return score
    try:
        opponent = _opponent(obs)
        active = opponent.active[0] if opponent.active else None
        hp = int(getattr(active, "hp", 0)) if active is not None else 0
        damage = _available_attack_damage(option)
        excess = max(0, damage - hp)
        if hp > 0 and excess > 100:
            return score - min(12000, excess * 20)
    except Exception:
        return score
    return score

_CG_P1_BASE_SCORE = _score

def _score(obs, option: object) -> int:
    if getattr(obs, "select", None) is None or getattr(option, "type", None) is None:
        return 0
    try:
        return int(_CG_P1_BASE_SCORE(obs, option))
    except Exception:
        return 0

_CG_P1_BASE_AGENT = agent

def agent(obs_dict: dict) -> list[int]:
    return _CG_P1_BASE_AGENT(obs_dict)
''',
}


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _base_source() -> str:
    if not BASE_SOURCE_PATH.is_file():
        raise FileNotFoundError(BASE_SOURCE_PATH)
    source = BASE_SOURCE_PATH.read_text(encoding="utf-8")
    actual = _sha256_bytes(source.encode("utf-8"))
    if actual != BASE_SOURCE_SHA256:
        raise ValueError(f"P1 base source hash mismatch: {actual}")
    return source


def render_p1_variant_source_v1(candidate_id: str) -> str:
    if candidate_id not in _PATCHES:
        raise ValueError(f"unknown P1 policy candidate: {candidate_id}")
    return _base_source() + _PATCHES[candidate_id]


def materialize_p1_variant_package_v1(
    *,
    source_package: Path | str,
    output_package: Path | str,
    candidate_id: str,
) -> dict[str, object]:
    source = Path(source_package).resolve()
    target = Path(output_package).resolve()
    if not source.is_dir() or not (source / "main.py").is_file() or not (source / "deck.csv").is_file():
        raise ValueError(f"P1 source package is incomplete: {source}")
    source_sha = _sha256_bytes((source / "main.py").read_bytes())
    if source_sha != BASE_SOURCE_SHA256:
        raise ValueError(f"P1 package main SHA mismatch: {source_sha}")
    if target.exists() or target.is_symlink():
        raise FileExistsError(f"P1 variant output exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target)
    rendered = render_p1_variant_source_v1(candidate_id).encode("utf-8")
    (target / "main.py").write_bytes(rendered)
    return {
        "schema_version": "cg-p1-policy-candidate-v1",
        "candidate_id": candidate_id,
        "source_package": str(source),
        "output_package": str(target),
        "base_policy_sha256": BASE_SOURCE_SHA256,
        "policy_sha256": _sha256_bytes(rendered),
        "deck_sha256": _sha256_bytes((target / "deck.csv").read_bytes()),
        "research_only": True,
    }


__all__ = [
    "BASE_SOURCE_PATH",
    "BASE_SOURCE_SHA256",
    "VARIANT_IDS",
    "materialize_p1_variant_package_v1",
    "render_p1_variant_source_v1",
]
