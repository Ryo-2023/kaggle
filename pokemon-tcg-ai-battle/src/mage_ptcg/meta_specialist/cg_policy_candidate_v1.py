"""Bounded, self-owned cg policy variants for research-only paired screens.

The variants are rendered by appending a small public-state override to the
immutable ``root_cg_submission_agent_v1.py`` source.  They are not imported by
the production entrypoint and grant no training, promotion, or submission
authority.
"""

from __future__ import annotations

import hashlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
BASE_SOURCE_PATH = ROOT / "src/mage_ptcg/meta_specialist/root_cg_submission_agent_v1.py"
BASE_SOURCE_SHA256 = "617a23c060084c8b2601800b4f729238563925165f3520628d938eab065aebef"
VARIANT_IDS = (
    "cg-lethal-target-v1",
    "cg-retreat-damage-v1",
    "cg-attach-threshold-v1",
    "cg-overkill-conservation-v1",
)


_PATCHES: dict[str, str] = {
    "cg-lethal-target-v1": r'''

# RESEARCH_VARIANT: cg-lethal-target-v1
# Public-state only: add a bounded bonus when the selected attack can KO the
# opponent's visible active Pokemon.  Non-attacks and malformed state use the
# immutable cg P0 score exactly.
_CG_POLICY_BASE_MAIN_SCORE = _main_score
_CG_POLICY_BASE_MAIN_SOURCE_SHA256 = "617a23c060084c8b2601800b4f729238563925165f3520628d938eab065aebef"

def _main_score(obs, option: object) -> int:
    # The engine may briefly expose a Struct without ``type`` while building
    # a selection.  Treat that shape as unsupported instead of allowing the
    # research overlay to turn a legal fallback into an agent fault.
    if getattr(obs, "select", None) is None or getattr(option, "type", None) is None:
        return 0
    try:
        score = _CG_POLICY_BASE_MAIN_SCORE(obs, option)
    except Exception:
        return 0
    if getattr(option, "type", None) != OptionType.ATTACK:
        return score
    try:
        opponent = _opponent(obs)
        active = opponent.active[0] if opponent.active else None
        hp = int(getattr(active, "hp", 0)) if active is not None else 0
        damage = _available_attack_damage(option)
        if hp > 0 and damage >= hp:
            return score + 12000
    except Exception:
        return score
    return score

_CG_POLICY_BASE_SCORE = _score

def _score(obs, option: object) -> int:
    # Keep the original scorer for well-formed options.  The explicit guard
    # covers the engine's transient untyped Struct before the immutable scorer
    # can dereference ``option.type``.
    if getattr(obs, "select", None) is None or getattr(option, "type", None) is None:
        return 0
    try:
        return int(_CG_POLICY_BASE_SCORE(obs, option))
    except Exception:
        return 0

_CG_POLICY_BASE_AGENT = agent

def agent(obs_dict: dict) -> list[int]:
    # Kaggle's source loader selects the last callable in the file.  Keep the
    # public entrypoint last while delegating to the immutable agent body.
    return _CG_POLICY_BASE_AGENT(obs_dict)
''',
    "cg-retreat-damage-v1": r'''

# RESEARCH_VARIANT: cg-retreat-damage-v1
# Public-state only: reinforce the existing retreat preference only when the
# visible active Pokemon has substantial damage and a powered bench exists.
_CG_POLICY_BASE_MAIN_SCORE = _main_score
_CG_POLICY_BASE_MAIN_SOURCE_SHA256 = "617a23c060084c8b2601800b4f729238563925165f3520628d938eab065aebef"

def _main_score(obs, option: object) -> int:
    # Fail closed for the engine's transient untyped Struct shape.
    if getattr(obs, "select", None) is None or getattr(option, "type", None) is None:
        return 0
    try:
        score = _CG_POLICY_BASE_MAIN_SCORE(obs, option)
    except Exception:
        return 0
    if getattr(option, "type", None) != OptionType.RETREAT:
        return score
    try:
        active = _mine(obs).active[0] if _mine(obs).active else None
        if active is None:
            return score
        damage = _damage(active)
        powered_bench = any(_energy_count(card) >= 2 for card in (_mine(obs).bench or []))
        if damage >= 100 and powered_bench:
            return score + 12000
    except Exception:
        return score
    return score

_CG_POLICY_BASE_SCORE = _score

def _score(obs, option: object) -> int:
    # Keep the original scorer for well-formed options and fail closed for
    # transient untyped Struct values emitted during engine setup.
    if getattr(obs, "select", None) is None or getattr(option, "type", None) is None:
        return 0
    try:
        return int(_CG_POLICY_BASE_SCORE(obs, option))
    except Exception:
        return 0

_CG_POLICY_BASE_AGENT = agent

def agent(obs_dict: dict) -> list[int]:
    # Keep ``agent`` as the final callable for Kaggle's source loader.
    return _CG_POLICY_BASE_AGENT(obs_dict)
''',
    "cg-attach-threshold-v1": r'''

# RESEARCH_VARIANT: cg-attach-threshold-v1
# Actor-visible state only: prefer the Fighting-energy attachment that reaches
# the Mega Lucario attack threshold.  All other options use the immutable cg
# P0 score and malformed engine state fails closed.
_CG_POLICY_BASE_MAIN_SCORE = _main_score
_CG_POLICY_BASE_MAIN_SOURCE_SHA256 = "617a23c060084c8b2601800b4f729238563925165f3520628d938eab065aebef"

def _main_score(obs, option: object) -> int:
    if getattr(obs, "select", None) is None or getattr(option, "type", None) is None:
        return 0
    try:
        score = _CG_POLICY_BASE_MAIN_SCORE(obs, option)
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

_CG_POLICY_BASE_SCORE = _score

def _score(obs, option: object) -> int:
    if getattr(obs, "select", None) is None or getattr(option, "type", None) is None:
        return 0
    try:
        return int(_CG_POLICY_BASE_SCORE(obs, option))
    except Exception:
        return 0

_CG_POLICY_BASE_AGENT = agent

def agent(obs_dict: dict) -> list[int]:
    return _CG_POLICY_BASE_AGENT(obs_dict)
''',
    "cg-overkill-conservation-v1": r'''

# RESEARCH_VARIANT: cg-overkill-conservation-v1
# Public opponent-active HP and attack damage only: conserve attack value when
# a legal attack would substantially overkill the visible active Pokemon.
# Lethal attacks remain preferred; the bounded penalty only separates lethal
# choices by excess damage.
_CG_POLICY_BASE_MAIN_SCORE = _main_score
_CG_POLICY_BASE_MAIN_SOURCE_SHA256 = "617a23c060084c8b2601800b4f729238563925165f3520628d938eab065aebef"

def _main_score(obs, option: object) -> int:
    if getattr(obs, "select", None) is None or getattr(option, "type", None) is None:
        return 0
    try:
        score = _CG_POLICY_BASE_MAIN_SCORE(obs, option)
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

_CG_POLICY_BASE_SCORE = _score

def _score(obs, option: object) -> int:
    if getattr(obs, "select", None) is None or getattr(option, "type", None) is None:
        return 0
    try:
        return int(_CG_POLICY_BASE_SCORE(obs, option))
    except Exception:
        return 0

_CG_POLICY_BASE_AGENT = agent

def agent(obs_dict: dict) -> list[int]:
    return _CG_POLICY_BASE_AGENT(obs_dict)
''',
}


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _base_source() -> str:
    source = BASE_SOURCE_PATH.read_text(encoding="utf-8")
    actual = _sha256_bytes(source.encode("utf-8"))
    if actual != BASE_SOURCE_SHA256:
        raise ValueError(f"cg policy base source hash mismatch: {actual}")
    return source


def render_variant_source(candidate_id: str) -> str:
    """Render one immutable-source-bound policy variant."""
    if candidate_id not in _PATCHES:
        raise ValueError(f"unknown cg policy candidate: {candidate_id}")
    return _base_source() + _PATCHES[candidate_id]


def variant_source_sha256(candidate_id: str) -> str:
    return _sha256_bytes(render_variant_source(candidate_id).encode("utf-8"))


def materialize_variant(candidate_id: str, output_path: Path) -> str:
    """Write a variant source once and return its SHA; refuse clobbering."""
    source = render_variant_source(candidate_id)
    output_path = Path(output_path)
    if output_path.exists() or output_path.is_symlink():
        raise FileExistsError(f"variant source already exists: {output_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(source, encoding="utf-8")
    return _sha256_bytes(source.encode("utf-8"))


__all__ = [
    "BASE_SOURCE_PATH",
    "BASE_SOURCE_SHA256",
    "VARIANT_IDS",
    "materialize_variant",
    "render_variant_source",
    "variant_source_sha256",
]
