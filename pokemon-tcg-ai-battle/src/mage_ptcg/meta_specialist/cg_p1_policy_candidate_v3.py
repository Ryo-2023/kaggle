"""Hash-bound Supporter-priority variants for the fixed P1 cg policy.

These overlays are research-only.  They append one actor-visible Supporter
condition to the immutable ``cg-lethal-target-v1`` source and preserve the
exact P1 scorer for every unsupported or malformed observation.
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
    "cg-p1-lillie-early-v1",
    "cg-p1-boss-ko-v1",
    "cg-p1-carmine-lowhand-v1",
)


_PATCHES: dict[str, str] = {
    "cg-p1-lillie-early-v1": r'''

# RESEARCH_VARIANT: cg-p1-lillie-early-v1
# Prefer Lillie during the opening turns when a legal Supporter play exists.
_CG_P1_SUPPORTER_BASE_PLAY_SCORE = _play_score

def _play_score(obs, option: object) -> int:
    try:
        score = int(_CG_P1_SUPPORTER_BASE_PLAY_SCORE(obs, option))
        if getattr(option, "type", None) != OptionType.PLAY:
            return score
        if _option_card_id(obs, option) != LILLIE:
            return score
        current = getattr(obs, "current", None)
        if current is None or bool(getattr(current, "supporterPlayed", False)):
            return score
        turn = getattr(current, "turn", None)
        if type(turn) is int and turn <= 2:
            return score + 8000
        return score
    except Exception:
        return 1000

_CG_P1_SUPPORTER_BASE_AGENT = agent

def agent(obs_dict: dict) -> list[int]:
    return _CG_P1_SUPPORTER_BASE_AGENT(obs_dict)
''',
    "cg-p1-boss-ko-v1": r'''

# RESEARCH_VARIANT: cg-p1-boss-ko-v1
# Prefer Boss's Orders when the visible opponent active is in KO range.
_CG_P1_SUPPORTER_BASE_PLAY_SCORE = _play_score

def _play_score(obs, option: object) -> int:
    try:
        score = int(_CG_P1_SUPPORTER_BASE_PLAY_SCORE(obs, option))
        if getattr(option, "type", None) != OptionType.PLAY:
            return score
        if _option_card_id(obs, option) != BOSS:
            return score
        current = getattr(obs, "current", None)
        if current is None or bool(getattr(current, "supporterPlayed", False)):
            return score
        opponent = _opponent(obs)
        active = opponent.active[0] if opponent.active else None
        hp = int(getattr(active, "hp", 0)) if active is not None else 0
        if 0 < hp <= 150:
            return score + 12000
        return score
    except Exception:
        return 1000

_CG_P1_SUPPORTER_BASE_AGENT = agent

def agent(obs_dict: dict) -> list[int]:
    return _CG_P1_SUPPORTER_BASE_AGENT(obs_dict)
''',
    "cg-p1-carmine-lowhand-v1": r'''

# RESEARCH_VARIANT: cg-p1-carmine-lowhand-v1
# Prefer Carmine after the opening when the actor's own hand is low.
_CG_P1_SUPPORTER_BASE_PLAY_SCORE = _play_score

def _play_score(obs, option: object) -> int:
    try:
        score = int(_CG_P1_SUPPORTER_BASE_PLAY_SCORE(obs, option))
        if getattr(option, "type", None) != OptionType.PLAY:
            return score
        if _option_card_id(obs, option) != CARMINE:
            return score
        current = getattr(obs, "current", None)
        if current is None or bool(getattr(current, "supporterPlayed", False)):
            return score
        turn = getattr(current, "turn", None)
        hand = getattr(_mine(obs), "hand", None)
        hand_count = len(hand) if hand is not None else None
        if type(turn) is int and turn >= 3 and isinstance(hand_count, int) and hand_count <= 4:
            return score + 6000
        return score
    except Exception:
        return 1000

_CG_P1_SUPPORTER_BASE_AGENT = agent

def agent(obs_dict: dict) -> list[int]:
    return _CG_P1_SUPPORTER_BASE_AGENT(obs_dict)
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


def render_p1_variant_source_v3(candidate_id: str) -> str:
    if candidate_id not in _PATCHES:
        raise ValueError(f"unknown P1 policy candidate: {candidate_id}")
    return _base_source() + _PATCHES[candidate_id]


def materialize_p1_variant_package_v3(
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
    rendered = render_p1_variant_source_v3(candidate_id).encode("utf-8")
    (target / "main.py").write_bytes(rendered)
    return {
        "schema_version": "cg-p1-policy-candidate-v3",
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
    "materialize_p1_variant_package_v3",
    "render_p1_variant_source_v3",
]
