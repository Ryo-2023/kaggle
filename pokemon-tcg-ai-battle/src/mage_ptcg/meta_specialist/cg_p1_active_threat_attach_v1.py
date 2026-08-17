"""Hash-bound active-threat attachment surface for the fixed P1 cg policy.

This research-only overlay gives a small preference to attaching Fighting
Energy to the visible active Pokémon when that active is healthy but still at
one energy and the opponent's visible active has at least two energy.  It is
deliberately narrower than the previously screened global Mega Lucario
attachment threshold and falls back to the exact P1 scorer on every other
state.
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
VARIANT_IDS = ("cg-p1-active-threat-attach-v1",)
ATTACH_BONUS = 6000


_PATCHES: dict[str, str] = {
    "cg-p1-active-threat-attach-v1": r'''

# RESEARCH_VARIANT: cg-p1-active-threat-attach-v1
# Prefer a Fighting Energy attachment to the visible active target only when
# it is healthy, has exactly one energy, and the visible opposing active has
# at least two energy.  This is a diagnostic surface; all other states use
# the exact P1 scorer.
_CG_P1_ACTIVE_THREAT_BASE_MAIN_SCORE = _main_score

def _cg_p1_active_threat_attach(obs, option) -> bool:
    try:
        if getattr(option, "type", None) != OptionType.ATTACH:
            return False
        if _option_card_id(obs, option) != FIGHTING:
            return False
        if bool(getattr(getattr(obs, "current", None), "energyAttached", False)):
            return False
        target = _target_for_option(obs, option)
        active = _mine(obs).active[0] if _mine(obs).active else None
        opponent = _opponent(obs).active[0] if _opponent(obs).active else None
        if target is None or active is None or opponent is None or target is not active:
            return False
        active_hp = int(getattr(active, "hp", 0))
        active_max_hp = int(getattr(active, "maxHp", 0))
        if active_hp <= 0 or active_max_hp <= 0 or active_hp != active_max_hp:
            return False
        if _energy_count(active) != 1:
            return False
        return _energy_count(opponent) >= 2
    except Exception:
        return False

def _main_score(obs, option: object) -> int:
    try:
        score = int(_CG_P1_ACTIVE_THREAT_BASE_MAIN_SCORE(obs, option))
        if _cg_p1_active_threat_attach(obs, option):
            return score + 6000
        return score
    except Exception:
        return 0

_CG_P1_ACTIVE_THREAT_BASE_SCORE = _score

def _score(obs, option: object) -> int:
    try:
        return int(_CG_P1_ACTIVE_THREAT_BASE_SCORE(obs, option))
    except Exception:
        return 0

_CG_P1_ACTIVE_THREAT_BASE_AGENT = agent

def agent(obs_dict: dict) -> list[int]:
    return _CG_P1_ACTIVE_THREAT_BASE_AGENT(obs_dict)
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
    if _sha256_bytes((source / "main.py").read_bytes()) != BASE_SOURCE_SHA256:
        raise ValueError("P1 package main SHA mismatch")
    if target.exists() or target.is_symlink():
        raise FileExistsError(f"P1 variant output exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target)
    rendered = render_p1_variant_source_v1(candidate_id).encode("utf-8")
    (target / "main.py").write_bytes(rendered)
    return {
        "schema_version": "cg-p1-active-threat-attach-v1",
        "candidate_id": candidate_id,
        "source_package": str(source),
        "output_package": str(target),
        "base_policy_sha256": BASE_SOURCE_SHA256,
        "policy_sha256": _sha256_bytes(rendered),
        "deck_sha256": _sha256_bytes((target / "deck.csv").read_bytes()),
        "research_only": True,
    }


__all__ = [
    "ATTACH_BONUS",
    "BASE_SOURCE_PATH",
    "BASE_SOURCE_SHA256",
    "VARIANT_IDS",
    "materialize_p1_variant_package_v1",
    "render_p1_variant_source_v1",
]
