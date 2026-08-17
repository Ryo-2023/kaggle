"""Hash-bound public policy variants for the fixed P1 cg package.

The variants are research-only overlays on the immutable ``cg-lethal-target-v1``
source.  They change one public score surface at a time and leave malformed or
unsupported observations on the exact P1 implementation.
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
    "cg-p1-search-priority-v3",
    "cg-p1-gust-ko-v3",
    "cg-p1-carmine-tempo-v1",
    "cg-p1-carmine-tempo-v2",
)


_PATCHES: dict[str, str] = {
    "cg-p1-search-priority-v3": r'''

# RESEARCH_VARIANT: cg-p1-search-priority-v3
# Public search-state overlay: when Mega Lucario is not visible, give the
# existing search cards one bounded bonus.  All other choices use exact P1.
_CG_P1_BASE_SEARCH_PRIORITY = _search_priority

def _search_priority(obs, card_id: int | None, effect_id: int | None) -> int:
    try:
        score = int(_CG_P1_BASE_SEARCH_PRIORITY(obs, card_id, effect_id))
        if card_id in {DUSK_BALL, PREMIUM_POWER, POKE_PAD} and not _has(obs, MEGA_LUCARIO):
            return score + 12000
        return score
    except Exception:
        return 1000

_CG_P1_SEARCH_BASE_AGENT = agent

def agent(obs_dict: dict) -> list[int]:
    return _CG_P1_SEARCH_BASE_AGENT(obs_dict)
''',
    "cg-p1-gust-ko-v3": r'''

# RESEARCH_VARIANT: cg-p1-gust-ko-v3
# Public active-HP overlay: when the visible opponent active is in KO range,
# prefer Boss's Orders among legal PLAY options.  Unsupported state uses P1.
_CG_P1_BASE_PLAY_SCORE = _play_score

def _play_score(obs, option: object) -> int:
    try:
        score = int(_CG_P1_BASE_PLAY_SCORE(obs, option))
        if getattr(option, "type", None) != OptionType.PLAY:
            return score
        if _option_card_id(obs, option) != BOSS:
            return score
        opponent = _opponent(obs)
        active = opponent.active[0] if opponent.active else None
        hp = int(getattr(active, "hp", 0)) if active is not None else 0
        if 0 < hp <= 150 and not obs.current.supporterPlayed:
            return score + 12000
        return score
    except Exception:
        return 1000

_CG_P1_GUST_BASE_AGENT = agent

def agent(obs_dict: dict) -> list[int]:
    return _CG_P1_GUST_BASE_AGENT(obs_dict)
''',
    "cg-p1-carmine-tempo-v1": r'''

# RESEARCH_VARIANT: cg-p1-carmine-tempo-v1
# Public turn-conditioned draw overlay: during the first two turn markers,
# prefer a legal Carmine play.  The turn marker, supporter-used flag, and card
# identity are public to the policy; malformed state remains exact P1.
_CG_P1_BASE_PLAY_SCORE = _play_score

def _play_score(obs, option: object) -> int:
    try:
        score = int(_CG_P1_BASE_PLAY_SCORE(obs, option))
        if getattr(option, "type", None) != OptionType.PLAY:
            return score
        if _option_card_id(obs, option) != CARMINE:
            return score
        turn = int(getattr(obs.current, "turn", 0))
        if turn <= 2 and not obs.current.supporterPlayed:
            return score + 6000
        return score
    except Exception:
        return 1000

_CG_P1_CARMINE_TEMPO_BASE_AGENT = agent

def agent(obs_dict: dict) -> list[int]:
    return _CG_P1_CARMINE_TEMPO_BASE_AGENT(obs_dict)
''',
    "cg-p1-carmine-tempo-v2": r'''

# RESEARCH_VARIANT: cg-p1-carmine-tempo-v2
# Same public turn-conditioned draw hypothesis with a stronger +12000 bonus.
_CG_P1_BASE_PLAY_SCORE = _play_score

def _play_score(obs, option: object) -> int:
    try:
        score = int(_CG_P1_BASE_PLAY_SCORE(obs, option))
        if getattr(option, "type", None) != OptionType.PLAY:
            return score
        if _option_card_id(obs, option) != CARMINE:
            return score
        turn = int(getattr(obs.current, "turn", 0))
        if turn <= 2 and not obs.current.supporterPlayed:
            return score + 12000
        return score
    except Exception:
        return 1000

_CG_P1_CARMINE_TEMPO_BASE_AGENT = agent

def agent(obs_dict: dict) -> list[int]:
    return _CG_P1_CARMINE_TEMPO_BASE_AGENT(obs_dict)
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


def render_p1_variant_source_v2(candidate_id: str) -> str:
    if candidate_id not in _PATCHES:
        raise ValueError(f"unknown P1 policy candidate: {candidate_id}")
    return _base_source() + _PATCHES[candidate_id]


def materialize_p1_variant_package_v2(
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
    rendered = render_p1_variant_source_v2(candidate_id).encode("utf-8")
    (target / "main.py").write_bytes(rendered)
    return {
        "schema_version": "cg-p1-policy-candidate-v2",
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
    "materialize_p1_variant_package_v2",
    "render_p1_variant_source_v2",
]
