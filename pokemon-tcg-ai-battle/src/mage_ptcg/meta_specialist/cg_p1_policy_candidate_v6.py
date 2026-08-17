"""Hash-bound attack-cooldown variant for the fixed P1 cg policy.

The research-only overlay prefers Mega Lucario's Aura Jab when Mega Brave
would not KO the visible Active Pokémon and Aura Jab can put discarded
Fighting Energy back onto an unpowered Fighting bench target.  Every other
state delegates to the immutable P1 source.
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
VARIANT_IDS = ("cg-p1-aura-jab-cooldown-safe-v1",)


_PATCHES: dict[str, str] = {
    "cg-p1-aura-jab-cooldown-safe-v1": r'''

# RESEARCH_VARIANT: cg-p1-aura-jab-cooldown-safe-v1
# AURA_JAB = attack 982; MEGA_BRAVE = attack 983.
# Prefer Aura Jab when Mega Brave would not KO the visible active Pokémon,
# discarded Fighting Energy can be recovered, and a visible Fighting bench
# target is still unpowered.  All unsupported state uses exact P1 scoring.
_CG_P1_ATTACK_BASE_MAIN_SCORE = _main_score

def _cg_p1_aura_jab_cooldown_safe(obs) -> bool:
    try:
        active = _opponent(obs).active[0] if _opponent(obs).active else None
        hp = int(getattr(active, "hp", 0)) if active is not None else 0
        if hp <= 0:
            return False
        if _discard_ids(obs).count(FIGHTING) < 1:
            return False
        bench_ids = {RIOLU, MEGA_LUCARIO, MAKUHITA, HARIYAMA}
        if not any(
            getattr(card, "id", None) in bench_ids and _energy_count(card) == 0
            for card in list(_mine(obs).bench or [])
        ):
            return False
        legal_aura_jab = False
        legal_mega_brave = False
        for choice in list(getattr(obs.select, "option", None) or []):
            if getattr(choice, "type", None) != OptionType.ATTACK:
                continue
            attack_id = getattr(choice, "attackId", None)
            if attack_id == 982:
                legal_aura_jab = True
            elif attack_id == 983:
                legal_mega_brave = True
        if not legal_aura_jab or not legal_mega_brave:
            return False
        return _available_attack_damage(next(
            choice for choice in obs.select.option
            if getattr(choice, "type", None) == OptionType.ATTACK
            and getattr(choice, "attackId", None) == 983
        )) < hp
    except Exception:
        return False

def _main_score(obs, option: object) -> int:
    try:
        score = int(_CG_P1_ATTACK_BASE_MAIN_SCORE(obs, option))
        if getattr(option, "type", None) != OptionType.ATTACK:
            return score
        if getattr(option, "attackId", None) != 982:
            return score
        if _cg_p1_aura_jab_cooldown_safe(obs):
            return score + 12000
        return score
    except Exception:
        return 1000

_CG_P1_ATTACK_BASE_AGENT = agent

def agent(obs_dict: dict) -> list[int]:
    return _CG_P1_ATTACK_BASE_AGENT(obs_dict)
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


def render_p1_variant_source_v6(candidate_id: str) -> str:
    if candidate_id not in _PATCHES:
        raise ValueError(f"unknown P1 policy candidate: {candidate_id}")
    return _base_source() + _PATCHES[candidate_id]


def materialize_p1_variant_package_v6(
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
    rendered = render_p1_variant_source_v6(candidate_id).encode("utf-8")
    (target / "main.py").write_bytes(rendered)
    return {
        "schema_version": "cg-p1-policy-candidate-v6",
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
    "materialize_p1_variant_package_v6",
    "render_p1_variant_source_v6",
]
