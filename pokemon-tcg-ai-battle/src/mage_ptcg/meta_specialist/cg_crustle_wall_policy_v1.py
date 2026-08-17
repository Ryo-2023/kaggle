"""Bounded public-state cg policy for the Crustle hard-negative matchup.

This module renders an isolated source variant from the immutable self-owned
cg P0 policy.  It is research-only: it does not modify ``main.py`` or grant
training, promotion, longrun, or submission authority.
"""

from __future__ import annotations

import hashlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
BASE_SOURCE_PATH = ROOT / "src/mage_ptcg/meta_specialist/root_cg_submission_agent_v1.py"
BASE_SOURCE_SHA256 = "617a23c060084c8b2601800b4f729238563925165f3520628d938eab065aebef"
CANDIDATE_ID = "cg-crustle-wall-v1"


_PATCH = r'''

# RESEARCH_VARIANT: cg-crustle-wall-v1
# Public-state only: when the visible opponent Active is Crustle, prefer a
# non-ex attack and demote Mega Lucario ex attacks, because Crustle's public
# ability prevents damage from opponent ex Pokemon.  All unsupported or
# malformed states use the immutable P0 score exactly.
_CG_POLICY_BASE_MAIN_SCORE = _main_score
_CG_POLICY_BASE_MAIN_SOURCE_SHA256 = "617a23c060084c8b2601800b4f729238563925165f3520628d938eab065aebef"
_CG_CRUSTLE = 345
_CG_NON_EX_ATTACK_IDS = {976, 977, 978, 979, 980, 981}
_CG_EX_ATTACK_IDS = {982, 983}

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
        if getattr(active, "id", None) != _CG_CRUSTLE:
            return score
        attack_id = getattr(option, "attackId", None)
        if attack_id in _CG_NON_EX_ATTACK_IDS:
            return score + 24000
        if attack_id in _CG_EX_ATTACK_IDS:
            return score - 24000
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
    # Keep the public entrypoint last for the sample cg source loader.
    return _CG_POLICY_BASE_AGENT(obs_dict)
'''


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _base_source() -> str:
    source = BASE_SOURCE_PATH.read_text(encoding="utf-8")
    actual = _sha256_bytes(source.encode("utf-8"))
    if actual != BASE_SOURCE_SHA256:
        raise ValueError(f"cg policy base source hash mismatch: {actual}")
    return source


def render_variant_source(candidate_id: str = CANDIDATE_ID) -> str:
    if candidate_id != CANDIDATE_ID:
        raise ValueError(f"unknown cg policy candidate: {candidate_id}")
    return _base_source() + _PATCH


def variant_source_sha256(candidate_id: str = CANDIDATE_ID) -> str:
    return _sha256_bytes(render_variant_source(candidate_id).encode("utf-8"))


def materialize_variant(candidate_id: str, output_path: Path) -> str:
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
    "CANDIDATE_ID",
    "materialize_variant",
    "render_variant_source",
    "variant_source_sha256",
]
