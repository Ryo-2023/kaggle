"""Public active-id counter-candidates for the fixed cg P1 lethal policy."""

from __future__ import annotations

import hashlib
from pathlib import Path
import shutil


ROOT = Path(__file__).resolve().parents[3]
BASE_SOURCE_PATH = ROOT / "runs/final-sprint-autonomous/cg-policy-screen-v1-retry-safe4-20260814/candidates/cg-lethal-target-v1/package/main.py"
BASE_SOURCE_SHA256 = "1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9"
CANDIDATE_IDS = (
    "cg-p1-public-suppress-dragapult-lethal-v1",
    "cg-p1-public-suppress-grimmsnarl-lethal-v1",
    "cg-p1-public-suppress-lucario-lethal-v1",
)


_PATCHES: dict[str, str] = {
    "cg-p1-public-suppress-dragapult-lethal-v1": r'''

# RESEARCH_VARIANT: cg-p1-public-suppress-dragapult-lethal-v1
# Public counter-hypothesis: remove only the P1 lethal bonus against the
# visible Dragapult-family active ids that regressed in paired P1/P0 WDL.
_CG_P1_PUBLIC_BASE_MAIN_SCORE = _main_score
_CG_P1_PUBLIC_BASE_MAIN_SOURCE_SHA256 = "1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9"
_CG_P1_PUBLIC_TARGET_IDS = {119, 120, 121, 184, 235}

def _main_score(obs, option: object) -> int:
    if getattr(obs, "select", None) is None or getattr(option, "type", None) is None:
        return 0
    try:
        score = int(_CG_P1_PUBLIC_BASE_MAIN_SCORE(obs, option))
        if getattr(option, "type", None) != OptionType.ATTACK:
            return score
        opponent = _opponent(obs)
        active = opponent.active[0] if opponent.active else None
        active_id = int(getattr(active, "id", -1)) if active is not None else -1
        hp = int(getattr(active, "hp", 0)) if active is not None else 0
        damage = int(_available_attack_damage(option))
        if active_id in _CG_P1_PUBLIC_TARGET_IDS and hp > 0 and damage >= hp:
            return score - 12000
        return score
    except Exception:
        return 0

_CG_P1_PUBLIC_BASE_SCORE = _score
def _score(obs, option: object) -> int:
    if getattr(obs, "select", None) is None or getattr(option, "type", None) is None:
        return 0
    try:
        return int(_CG_P1_PUBLIC_BASE_SCORE(obs, option))
    except Exception:
        return 0

_CG_P1_PUBLIC_BASE_AGENT = agent
def agent(obs_dict: dict) -> list[int]:
    return _CG_P1_PUBLIC_BASE_AGENT(obs_dict)
''',
    "cg-p1-public-suppress-grimmsnarl-lethal-v1": r'''

# RESEARCH_VARIANT: cg-p1-public-suppress-grimmsnarl-lethal-v1
# Public counter-hypothesis: remove only the P1 lethal bonus against the
# visible Grimmsnarl-family active ids that regressed in paired P1/P0 WDL.
_CG_P1_PUBLIC_BASE_MAIN_SCORE = _main_score
_CG_P1_PUBLIC_BASE_MAIN_SOURCE_SHA256 = "1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9"
_CG_P1_PUBLIC_TARGET_IDS = {112, 646, 647, 648, 860}

def _main_score(obs, option: object) -> int:
    if getattr(obs, "select", None) is None or getattr(option, "type", None) is None:
        return 0
    try:
        score = int(_CG_P1_PUBLIC_BASE_MAIN_SCORE(obs, option))
        if getattr(option, "type", None) != OptionType.ATTACK:
            return score
        opponent = _opponent(obs)
        active = opponent.active[0] if opponent.active else None
        active_id = int(getattr(active, "id", -1)) if active is not None else -1
        hp = int(getattr(active, "hp", 0)) if active is not None else 0
        damage = int(_available_attack_damage(option))
        if active_id in _CG_P1_PUBLIC_TARGET_IDS and hp > 0 and damage >= hp:
            return score - 12000
        return score
    except Exception:
        return 0

_CG_P1_PUBLIC_BASE_SCORE = _score
def _score(obs, option: object) -> int:
    if getattr(obs, "select", None) is None or getattr(option, "type", None) is None:
        return 0
    try:
        return int(_CG_P1_PUBLIC_BASE_SCORE(obs, option))
    except Exception:
        return 0

_CG_P1_PUBLIC_BASE_AGENT = agent
def agent(obs_dict: dict) -> list[int]:
    return _CG_P1_PUBLIC_BASE_AGENT(obs_dict)
''',
    "cg-p1-public-suppress-lucario-lethal-v1": r'''

# RESEARCH_VARIANT: cg-p1-public-suppress-lucario-lethal-v1
# Public counter-hypothesis: remove only the P1 lethal bonus against the
# visible Lucario-family active ids that regressed in paired P1/P0 WDL.
_CG_P1_PUBLIC_BASE_MAIN_SCORE = _main_score
_CG_P1_PUBLIC_BASE_MAIN_SOURCE_SHA256 = "1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9"
_CG_P1_PUBLIC_TARGET_IDS = {675, 676, 677, 678}

def _main_score(obs, option: object) -> int:
    if getattr(obs, "select", None) is None or getattr(option, "type", None) is None:
        return 0
    try:
        score = int(_CG_P1_PUBLIC_BASE_MAIN_SCORE(obs, option))
        if getattr(option, "type", None) != OptionType.ATTACK:
            return score
        opponent = _opponent(obs)
        active = opponent.active[0] if opponent.active else None
        active_id = int(getattr(active, "id", -1)) if active is not None else -1
        hp = int(getattr(active, "hp", 0)) if active is not None else 0
        damage = int(_available_attack_damage(option))
        if active_id in _CG_P1_PUBLIC_TARGET_IDS and hp > 0 and damage >= hp:
            return score - 12000
        return score
    except Exception:
        return 0

_CG_P1_PUBLIC_BASE_SCORE = _score
def _score(obs, option: object) -> int:
    if getattr(obs, "select", None) is None or getattr(option, "type", None) is None:
        return 0
    try:
        return int(_CG_P1_PUBLIC_BASE_SCORE(obs, option))
    except Exception:
        return 0

_CG_P1_PUBLIC_BASE_AGENT = agent
def agent(obs_dict: dict) -> list[int]:
    return _CG_P1_PUBLIC_BASE_AGENT(obs_dict)
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


def render_public_failure_candidate_v1(candidate_id: str) -> str:
    if candidate_id not in _PATCHES:
        raise ValueError(f"unknown public failure candidate: {candidate_id}")
    return _base_source() + _PATCHES[candidate_id]


def materialize_public_failure_candidate_v1(*, source_package: Path | str, output_package: Path | str, candidate_id: str) -> dict[str, object]:
    source = Path(source_package).resolve()
    target = Path(output_package).resolve()
    if candidate_id not in _PATCHES:
        raise ValueError(f"unknown public failure candidate: {candidate_id}")
    if not source.is_dir() or not (source / "main.py").is_file() or not (source / "deck.csv").is_file():
        raise ValueError(f"P1 source package is incomplete: {source}")
    source_sha = _sha256_bytes((source / "main.py").read_bytes())
    if source_sha != BASE_SOURCE_SHA256:
        raise ValueError(f"P1 package main SHA mismatch: {source_sha}")
    if target.exists() or target.is_symlink():
        raise FileExistsError(f"P1 public failure output exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target)
    rendered = render_public_failure_candidate_v1(candidate_id).encode("utf-8")
    (target / "main.py").write_bytes(rendered)
    return {
        "schema_version": "cg-p1-public-failure-candidate-v1",
        "candidate_id": candidate_id,
        "source_package": str(source),
        "output_package": str(target),
        "base_policy_sha256": BASE_SOURCE_SHA256,
        "policy_sha256": _sha256_bytes(rendered),
        "deck_sha256": _sha256_bytes((target / "deck.csv").read_bytes()),
        "public_features": ["opponent.active.id", "opponent.active.hp", "attack.damage"],
        "authority": {"training": False, "promotion": False, "submission": False, "longrun": False, "teacher": False},
        "research_only": True,
        "diagnostic_only": True,
    }


__all__ = ["BASE_SOURCE_SHA256", "CANDIDATE_IDS", "materialize_public_failure_candidate_v1", "render_public_failure_candidate_v1"]
