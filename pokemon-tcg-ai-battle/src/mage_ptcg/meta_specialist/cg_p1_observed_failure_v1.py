"""Observed-failure-bound public-state variants for the fixed cg P1 policy.

The candidates target a pattern visible in the P1 telemetry: losses often
continued while a public opponent active Pokemon had a large ``maxHp`` and a
legal attack was available but not selected.  The overlay only reads the
opponent's public active ``hp``/``maxHp`` and attack damage.  It never reads
opponent hand, prize, deck, or policy identity, and every unsupported shape
delegates to the immutable P1 source.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
import shutil


ROOT = Path(__file__).resolve().parents[3]
BASE_SOURCE_PATH = (
    ROOT
    / "runs/final-sprint-autonomous/cg-policy-screen-v1-retry-safe4-20260814"
    / "candidates/cg-lethal-target-v1/package/main.py"
)
BASE_SOURCE_SHA256 = "1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9"
VARIANT_IDS = (
    "cg-p1-heavy-active-attack-v1",
    "cg-p1-very-heavy-active-attack-v1",
    "cg-p1-heavy-active-conserve-v1",
    "cg-p1-abomasnow-pressure-v1",
    "cg-p1-ursaluna-pressure-v1",
)


_PATCHES: dict[str, str] = {
    "cg-p1-heavy-active-attack-v1": r'''

# RESEARCH_VARIANT: cg-p1-heavy-active-attack-v1
# Observed-failure overlay: when the visible opponent active Pokemon is a
# heavy target (maxHp >= 300), prefer a non-lethal legal attack over the exact
# P1 setup/play score.  Private opponent fields are intentionally untouched.
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
        max_hp = int(getattr(active, "maxHp", hp)) if active is not None else 0
        damage = int(_available_attack_damage(option))
        if max_hp >= 300 and hp > 0 and damage > 0 and damage < hp:
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
    "cg-p1-very-heavy-active-attack-v1": r'''

# RESEARCH_VARIANT: cg-p1-very-heavy-active-attack-v1
# Same public-state pressure hypothesis as the heavy variant, restricted to
# maxHp >= 350 to reduce intervention coverage and avoid broad overfit.
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
        max_hp = int(getattr(active, "maxHp", hp)) if active is not None else 0
        damage = int(_available_attack_damage(option))
        if max_hp >= 350 and hp > 0 and damage > 0 and damage < hp:
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
    "cg-p1-heavy-active-conserve-v1": r'''

# RESEARCH_VARIANT: cg-p1-heavy-active-conserve-v1
# Explicit counter-hypothesis to the attack-pressure screen: under the same
# public heavy-target condition, preserve the exact P1 setup/play ordering by
# applying the opposite bounded delta to non-lethal attacks.
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
        max_hp = int(getattr(active, "maxHp", hp)) if active is not None else 0
        damage = int(_available_attack_damage(option))
        if max_hp >= 300 and hp > 0 and damage > 0 and damage < hp:
            return score - 12000
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
    "cg-p1-abomasnow-pressure-v1": r'''

# RESEARCH_VARIANT: cg-p1-abomasnow-pressure-v1
# Meta-targeted public overlay from the P1 ledger: visible Abomasnow-family
# active IDs (721/722/723) were concentrated in a high-weight loss cluster.
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
        active_id = int(getattr(active, "id", -1)) if active is not None else -1
        hp = int(getattr(active, "hp", 0)) if active is not None else 0
        damage = int(_available_attack_damage(option))
        if active_id in {721, 722, 723} and hp > 0 and damage > 0 and damage < hp:
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
    "cg-p1-ursaluna-pressure-v1": r'''

# RESEARCH_VARIANT: cg-p1-ursaluna-pressure-v1
# Meta-targeted public overlay from the P1 ledger: visible Ursaluna-family
# active IDs (65/135/1073/1074) were concentrated in a high-weight loss cluster.
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
        active_id = int(getattr(active, "id", -1)) if active is not None else -1
        hp = int(getattr(active, "hp", 0)) if active is not None else 0
        damage = int(_available_attack_damage(option))
        if active_id in {65, 135, 1073, 1074} and hp > 0 and damage > 0 and damage < hp:
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


def render_observed_failure_variant_v1(candidate_id: str) -> str:
    if candidate_id not in _PATCHES:
        raise ValueError(f"unknown observed-failure candidate: {candidate_id}")
    return _base_source() + _PATCHES[candidate_id]


def materialize_observed_failure_variant_v1(
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
    rendered = render_observed_failure_variant_v1(candidate_id).encode("utf-8")
    (target / "main.py").write_bytes(rendered)
    return {
        "schema_version": "cg-p1-observed-failure-v1",
        "candidate_id": candidate_id,
        "source_package": str(source),
        "output_package": str(target),
        "base_policy_sha256": BASE_SOURCE_SHA256,
        "policy_sha256": _sha256_bytes(rendered),
        "deck_sha256": _sha256_bytes((target / "deck.csv").read_bytes()),
        "public_features": ["opponent.active.hp", "opponent.active.maxHp", "attack.damage"],
        "authority": {
            "training": False,
            "promotion": False,
            "submission": False,
            "longrun": False,
            "teacher": False,
        },
        "research_only": True,
    }


__all__ = [
    "BASE_SOURCE_PATH",
    "BASE_SOURCE_SHA256",
    "VARIANT_IDS",
    "materialize_observed_failure_variant_v1",
    "render_observed_failure_variant_v1",
]
