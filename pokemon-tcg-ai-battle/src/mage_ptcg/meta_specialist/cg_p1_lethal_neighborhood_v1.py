"""Observed-failure bounded variants for the fixed cg-lethal P1 policy.

The variants are intentionally derived from the P1 public telemetry: 192
public lethal states were observed and 29 selected a non-lethal action; 18 of
those 29 terminal games were losses.  This module only materializes isolated
research packages.  It never edits the submission package or grants training,
promotion, teacher, or long-run authority.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
import shutil


ROOT = Path(__file__).resolve().parents[3]
BASE_SOURCE_PATH = ROOT / (
    "runs/final-sprint-autonomous/"
    "cg-policy-screen-v1-retry-safe4-20260814/candidates/"
    "cg-lethal-target-v1/package/main.py"
)
BASE_SOURCE_SHA256 = "1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9"

VARIANT_IDS = (
    "cg-lethal-lock-v1",
    "cg-lethal-setup-lock-v1",
    "cg-lethal-resource-first-v1",
)


_PATCHES: dict[str, str] = {
    "cg-lethal-lock-v1": r'''

# RESEARCH_VARIANT: cg-lethal-lock-v1
# OBSERVED_FAILURE: 29/192 public lethal states selected a non-ATTACK action;
# 18/29 corresponding terminal games were losses.
# HYPOTHESIS: when a legal public lethal attack exists, P1 underweights it
# against setup actions.  Exact change: add one bounded +30000 lethal bonus.
_CG_P1_NEIGHBORHOOD_BASE_MAIN_SCORE = _main_score

def _cg_p1_visible_opponent_hp(obs):
    try:
        active = _opponent(obs).active[0] if _opponent(obs).active else None
        hp = int(getattr(active, "hp", 0)) if active is not None else 0
        return hp if hp > 0 else None
    except Exception:
        return None

def _cg_p1_lethal_attacks(obs):
    hp = _cg_p1_visible_opponent_hp(obs)
    if hp is None or getattr(obs, "select", None) is None:
        return ()
    result = []
    for index, option in enumerate(getattr(obs.select, "option", None) or []):
        if getattr(option, "type", None) != OptionType.ATTACK:
            continue
        try:
            damage = _available_attack_damage(option)
        except Exception:
            continue
        if damage >= hp:
            result.append((index, damage))
    return tuple(result)

def _main_score(obs, option: object) -> int:
    try:
        score = int(_CG_P1_NEIGHBORHOOD_BASE_MAIN_SCORE(obs, option))
    except Exception:
        return 0
    if getattr(option, "type", None) != OptionType.ATTACK:
        return score
    try:
        hp = _cg_p1_visible_opponent_hp(obs)
        damage = _available_attack_damage(option)
        if hp is not None and damage >= hp:
            return score + 30000
    except Exception:
        return score
    return score

_CG_P1_NEIGHBORHOOD_BASE_SCORE = _score
def _score(obs, option: object) -> int:
    if getattr(obs, "select", None) is None or getattr(option, "type", None) is None:
        return 0
    try:
        return int(_CG_P1_NEIGHBORHOOD_BASE_SCORE(obs, option))
    except Exception:
        return 0

_CG_P1_NEIGHBORHOOD_BASE_AGENT = agent
def agent(obs_dict: dict) -> list[int]:
    return _CG_P1_NEIGHBORHOOD_BASE_AGENT(obs_dict)
''',
    "cg-lethal-setup-lock-v1": r'''

# RESEARCH_VARIANT: cg-lethal-setup-lock-v1
# OBSERVED_FAILURE: the 29 missed lethal states selected ATTACH=14,
# EVOLVE=7, PLAY=5, ABILITY=3.  This candidate isolates the setup conflict
# rather than changing lethal-versus-PLAY/ABILITY states.
_CG_P1_NEIGHBORHOOD_BASE_MAIN_SCORE = _main_score

def _cg_p1_visible_opponent_hp(obs):
    try:
        active = _opponent(obs).active[0] if _opponent(obs).active else None
        hp = int(getattr(active, "hp", 0)) if active is not None else 0
        return hp if hp > 0 else None
    except Exception:
        return None

def _cg_p1_setup_conflict(obs):
    if getattr(obs, "select", None) is None:
        return False
    setup_types = {OptionType.ATTACH, OptionType.EVOLVE}
    return any(getattr(option, "type", None) in setup_types for option in (getattr(obs.select, "option", None) or []))

def _main_score(obs, option: object) -> int:
    try:
        score = int(_CG_P1_NEIGHBORHOOD_BASE_MAIN_SCORE(obs, option))
    except Exception:
        return 0
    if getattr(option, "type", None) != OptionType.ATTACK or not _cg_p1_setup_conflict(obs):
        return score
    try:
        hp = _cg_p1_visible_opponent_hp(obs)
        damage = _available_attack_damage(option)
        if hp is not None and damage >= hp:
            return score + 30000
    except Exception:
        return score
    return score

_CG_P1_NEIGHBORHOOD_BASE_SCORE = _score
def _score(obs, option: object) -> int:
    if getattr(obs, "select", None) is None or getattr(option, "type", None) is None:
        return 0
    try:
        return int(_CG_P1_NEIGHBORHOOD_BASE_SCORE(obs, option))
    except Exception:
        return 0

_CG_P1_NEIGHBORHOOD_BASE_AGENT = agent
def agent(obs_dict: dict) -> list[int]:
    return _CG_P1_NEIGHBORHOOD_BASE_AGENT(obs_dict)
''',
    "cg-lethal-resource-first-v1": r'''

# RESEARCH_VARIANT: cg-lethal-resource-first-v1
# OBSERVED_FAILURE: 42 public states exposed >=2 legal lethal attacks; 30
# occurred in loss games.  Exact change: among multiple lethal attacks, add
# +16000 to the least-damage lethal attack to preserve higher-cost resources.
_CG_P1_NEIGHBORHOOD_BASE_MAIN_SCORE = _main_score

def _cg_p1_visible_opponent_hp(obs):
    try:
        active = _opponent(obs).active[0] if _opponent(obs).active else None
        hp = int(getattr(active, "hp", 0)) if active is not None else 0
        return hp if hp > 0 else None
    except Exception:
        return None

def _cg_p1_lethal_damage_floor(obs):
    hp = _cg_p1_visible_opponent_hp(obs)
    if hp is None or getattr(obs, "select", None) is None:
        return None
    damages = []
    for option in getattr(obs.select, "option", None) or []:
        if getattr(option, "type", None) != OptionType.ATTACK:
            continue
        try:
            damage = _available_attack_damage(option)
        except Exception:
            continue
        if damage >= hp:
            damages.append(damage)
    if len(damages) < 2:
        return None
    return min(damages)

def _main_score(obs, option: object) -> int:
    try:
        score = int(_CG_P1_NEIGHBORHOOD_BASE_MAIN_SCORE(obs, option))
    except Exception:
        return 0
    if getattr(option, "type", None) != OptionType.ATTACK:
        return score
    try:
        floor = _cg_p1_lethal_damage_floor(obs)
        damage = _available_attack_damage(option)
        if floor is not None and damage == floor:
            return score + 16000
    except Exception:
        return score
    return score

_CG_P1_NEIGHBORHOOD_BASE_SCORE = _score
def _score(obs, option: object) -> int:
    if getattr(obs, "select", None) is None or getattr(option, "type", None) is None:
        return 0
    try:
        return int(_CG_P1_NEIGHBORHOOD_BASE_SCORE(obs, option))
    except Exception:
        return 0

_CG_P1_NEIGHBORHOOD_BASE_AGENT = agent
def agent(obs_dict: dict) -> list[int]:
    return _CG_P1_NEIGHBORHOOD_BASE_AGENT(obs_dict)
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


def render_p1_lethal_variant_source_v1(candidate_id: str) -> str:
    if candidate_id not in _PATCHES:
        raise ValueError(f"unknown P1 lethal neighborhood candidate: {candidate_id}")
    return _base_source() + _PATCHES[candidate_id]


def materialize_p1_lethal_variant_package_v1(
    *,
    source_package: Path | str,
    output_package: Path | str,
    candidate_id: str,
) -> dict[str, object]:
    source = Path(source_package).resolve()
    target = Path(output_package).resolve()
    if candidate_id not in VARIANT_IDS:
        raise ValueError(f"unknown P1 lethal neighborhood candidate: {candidate_id}")
    if not source.is_dir() or not (source / "main.py").is_file() or not (source / "deck.csv").is_file():
        raise ValueError(f"P1 source package is incomplete: {source}")
    source_sha = _sha256_bytes((source / "main.py").read_bytes())
    if source_sha != BASE_SOURCE_SHA256:
        raise ValueError(f"P1 package main SHA mismatch: {source_sha}")
    if target.exists() or target.is_symlink():
        raise FileExistsError(f"P1 variant output exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target)
    rendered = render_p1_lethal_variant_source_v1(candidate_id).encode("utf-8")
    (target / "main.py").write_bytes(rendered)
    return {
        "schema_version": "cg-p1-lethal-neighborhood-v1",
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
    "materialize_p1_lethal_variant_package_v1",
    "render_p1_lethal_variant_source_v1",
]
