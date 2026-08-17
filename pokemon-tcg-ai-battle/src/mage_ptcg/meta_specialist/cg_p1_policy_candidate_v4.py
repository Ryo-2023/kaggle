"""Hash-bound item-tempo variants for the fixed P1 cg policy.

The variants are research-only overlays on the immutable
``cg-lethal-target-v1`` source.  They use only information already exposed to
the actor: legal options, visible active Pokémon, own active/bench state, and
the actor's hand.  Unsupported or malformed observations preserve the P1
score and fail closed.
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
    "cg-p1-gravity-stage2-lethal-v1",
    "cg-p1-premium-power-lethal-v1",
    "cg-p1-switch-powered-bench-v1",
)


_PATCHES: dict[str, str] = {
    "cg-p1-gravity-stage2-lethal-v1": r'''

# RESEARCH_VARIANT: cg-p1-gravity-stage2-lethal-v1
# If Gravity Mountain can make a visible Stage 2 active reachable in one
# legal attack, prefer that item.  ``preEvolution`` is actor-visible and the
# 30-point reach is the card's documented effect.
_CG_P1_ITEM_BASE_PLAY_SCORE = _play_score

def _cg_p1_stage2_active(obs) -> bool:
    try:
        opponent = _opponent(obs)
        active = opponent.active[0] if opponent.active else None
        pre_evolution = getattr(active, "preEvolution", None)
        return isinstance(pre_evolution, list) and len(pre_evolution) >= 2
    except Exception:
        return False

def _cg_p1_reachable_with_bonus(obs, bonus: int) -> bool:
    try:
        opponent = _opponent(obs)
        active = opponent.active[0] if opponent.active else None
        hp = int(getattr(active, "hp", 0)) if active is not None else 0
        if hp <= 0:
            return False
        for attack_option in list(getattr(obs.select, "option", None) or []):
            if getattr(attack_option, "type", None) != OptionType.ATTACK:
                continue
            damage = _available_attack_damage(attack_option)
            if damage < hp <= damage + bonus:
                return True
    except Exception:
        return False
    return False

def _play_score(obs, option: object) -> int:
    try:
        score = int(_CG_P1_ITEM_BASE_PLAY_SCORE(obs, option))
        if getattr(option, "type", None) != OptionType.PLAY:
            return score
        if _option_card_id(obs, option) != GRAVITY:
            return score
        if getattr(obs.current, "stadiumPlayed", False):
            return score
        if _cg_p1_stage2_active(obs) and _cg_p1_reachable_with_bonus(obs, 30):
            return score + 12000
        return score
    except Exception:
        return 1000

_CG_P1_ITEM_BASE_AGENT = agent

def agent(obs_dict: dict) -> list[int]:
    return _CG_P1_ITEM_BASE_AGENT(obs_dict)
''',
    "cg-p1-premium-power-lethal-v1": r'''

# RESEARCH_VARIANT: cg-p1-premium-power-lethal-v1
# If Premium Power's documented +30 damage turns a legal attack into a KO,
# prefer the item.  The check is ``damage + bonus`` against visible HP.
_CG_P1_ITEM_BASE_PLAY_SCORE = _play_score

def _cg_p1_reachable_with_bonus(obs, bonus: int) -> bool:
    try:
        opponent = _opponent(obs)
        active = opponent.active[0] if opponent.active else None
        hp = int(getattr(active, "hp", 0)) if active is not None else 0
        if hp <= 0:
            return False
        for attack_option in list(getattr(obs.select, "option", None) or []):
            if getattr(attack_option, "type", None) != OptionType.ATTACK:
                continue
            damage = _available_attack_damage(attack_option)
            if damage < hp <= damage + bonus:
                return True
    except Exception:
        return False
    return False

def _play_score(obs, option: object) -> int:
    try:
        score = int(_CG_P1_ITEM_BASE_PLAY_SCORE(obs, option))
        if getattr(option, "type", None) != OptionType.PLAY:
            return score
        if _option_card_id(obs, option) != PREMIUM_POWER:
            return score
        if _cg_p1_reachable_with_bonus(obs, 30):
            return score + 12000
        return score
    except Exception:
        return 1000

_CG_P1_ITEM_BASE_AGENT = agent

def agent(obs_dict: dict) -> list[int]:
    return _CG_P1_ITEM_BASE_AGENT(obs_dict)
''',
    "cg-p1-switch-powered-bench-v1": r'''

# RESEARCH_VARIANT: cg-p1-switch-powered-bench-v1
# Prefer Switch when the active Pokémon is damaged and a visible powered
# bench target exists.  ``powered bench`` means at least two attached energy.
_CG_P1_ITEM_BASE_PLAY_SCORE = _play_score

def _cg_p1_powered_bench(obs) -> bool:
    try:
        mine = _mine(obs)
        active = mine.active[0] if mine.active else None
        if active is None:
            return False
        damage = int(getattr(active, "maxHp", 0)) - int(getattr(active, "hp", 0))
        if damage <= 0:
            return False
        return any(_energy_count(card) >= 2 for card in list(mine.bench or []))
    except Exception:
        return False

def _play_score(obs, option: object) -> int:
    try:
        score = int(_CG_P1_ITEM_BASE_PLAY_SCORE(obs, option))
        if getattr(option, "type", None) != OptionType.PLAY:
            return score
        if _option_card_id(obs, option) != SWITCH:
            return score
        if _cg_p1_powered_bench(obs):
            return score + 12000
        return score
    except Exception:
        return 1000

_CG_P1_ITEM_BASE_AGENT = agent

def agent(obs_dict: dict) -> list[int]:
    return _CG_P1_ITEM_BASE_AGENT(obs_dict)
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


def render_p1_variant_source_v4(candidate_id: str) -> str:
    if candidate_id not in _PATCHES:
        raise ValueError(f"unknown P1 policy candidate: {candidate_id}")
    return _base_source() + _PATCHES[candidate_id]


def materialize_p1_variant_package_v4(
    *,
    source_package: Path | str,
    output_package: Path | str,
    candidate_id: str,
) -> dict[str, object]:
    source = Path(source_package).resolve()
    target = Path(output_package).resolve()
    if not source.is_dir() or not (source / "main.py").is_file() or not (source / "deck.csv").is_file():
        raise ValueError(f"P1 source package is incomplete: {source}")
    if _sha256_bytes((source / "main.py").read_bytes()) != BASE_SOURCE_SHA256:
        raise ValueError("P1 package main SHA mismatch")
    if target.exists() or target.is_symlink():
        raise FileExistsError(f"P1 variant output exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target)
    rendered = render_p1_variant_source_v4(candidate_id).encode("utf-8")
    (target / "main.py").write_bytes(rendered)
    return {
        "schema_version": "cg-p1-policy-candidate-v4",
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
    "materialize_p1_variant_package_v4",
    "render_p1_variant_source_v4",
]
