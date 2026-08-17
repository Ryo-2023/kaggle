"""Render a self-owned policy surface from the public root cg policy.

The renderer is intentionally bound to the standalone public-state root
policy, not to the cg-lethal P1 source.  It only adds score terms that can be
computed from the observation exposed by ``cg.api``; option legality,
selection cardinality, and the root policy's exception fallback remain
unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
import hashlib
import json
from pathlib import Path
from typing import Mapping


ROOT = Path(__file__).resolve().parents[3]
BASE_SOURCE_PATH = ROOT / "src/mage_ptcg/meta_specialist/root_cg_submission_agent_v1.py"
BASE_SOURCE_SHA256 = "617a23c060084c8b2601800b4f729238563925165f3520628d938eab065aebef"
SCHEMA = "cg-independent-parameter-config-v1"


PARAMETER_BOUNDS: dict[str, tuple[int, int]] = {
    "lethal_bonus": (0, 30000),
    "nonlethal_attack_penalty": (-30000, 0),
    "damaged_active_attack_bonus": (0, 30000),
    "low_hand_ability_bonus": (0, 30000),
    "low_hand_supporter_bonus": (0, 30000),
    "retreat_damaged_active_bonus": (0, 30000),
    "retreat_energy_reserve_bonus": (0, 20000),
    "energy_reserve_penalty": (-30000, 0),
    "search_before_evolve_bonus": (0, 30000),
    "bench_threat_bonus": (0, 30000),
}


@dataclass(frozen=True, slots=True)
class IndependentCgParameterConfig:
    """One bounded point on the independently rendered root policy surface."""

    lethal_bonus: int = 12000
    nonlethal_attack_penalty: int = -4000
    damaged_active_attack_bonus: int = 8000
    low_hand_ability_bonus: int = 8000
    low_hand_supporter_bonus: int = 5000
    retreat_damaged_active_bonus: int = 12000
    retreat_energy_reserve_bonus: int = 6000
    energy_reserve_penalty: int = -6000
    search_before_evolve_bonus: int = 7000
    bench_threat_bonus: int = 4000

    @classmethod
    def default(cls) -> "IndependentCgParameterConfig":
        return cls()

    @classmethod
    def from_mapping(cls, values: Mapping[str, object]) -> "IndependentCgParameterConfig":
        if not isinstance(values, Mapping):
            raise ValueError("independent parameter config must be a mapping")
        names = {field.name for field in fields(cls)}
        unknown = set(values) - names
        if unknown:
            raise ValueError(f"unknown independent parameter(s): {sorted(unknown)}")
        merged = cls.default().as_dict()
        merged.update(values)
        config = cls(**merged)
        config.validate()
        return config

    def as_dict(self) -> dict[str, int]:
        return {field.name: int(getattr(self, field.name)) for field in fields(self)}

    def validate(self) -> None:
        for name, (lower, upper) in PARAMETER_BOUNDS.items():
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"parameter {name} must be an integer")
            if not lower <= value <= upper:
                raise ValueError(
                    f"parameter {name} out of bounds: {value} not in [{lower}, {upper}]"
                )

    def canonical_json(self) -> str:
        self.validate()
        return json.dumps(
            {"schema_version": SCHEMA, "parameters": self.as_dict()},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    def config_sha256(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()


def candidate_id_for_config(
    config: IndependentCgParameterConfig,
    *,
    generation: int,
    index: int,
) -> str:
    config.validate()
    if type(generation) is not int or generation < 0:
        raise ValueError("generation must be a non-negative integer")
    if type(index) is not int or index < 0:
        raise ValueError("index must be a non-negative integer")
    return f"cg-independent-g{generation:02d}-c{index:02d}-{config.config_sha256()[:12]}"


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _parameter_patch(config: IndependentCgParameterConfig, candidate_id: str) -> str:
    values = json.dumps(config.as_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    config_sha = config.config_sha256()
    encoded_id = json.dumps(candidate_id, ensure_ascii=False)
    return f'''

# RESEARCH_INDEPENDENT_LINEAGE: root-cg-public-state-v1
# The parent is the standalone public root policy, never the cg-lethal P1.
_CG_INDEPENDENT_BASE_SOURCE_SHA256 = {BASE_SOURCE_SHA256!r}
_CG_INDEPENDENT_PARAMETERS = {values}
_CG_INDEPENDENT_CONFIG_SHA256 = {config_sha!r}
_CG_INDEPENDENT_CANDIDATE_ID = {encoded_id}


def _cg_independent_value(name):
    return int(_CG_INDEPENDENT_PARAMETERS[name])


_CG_INDEPENDENT_BASE_MAIN_SCORE = _main_score


def _main_score(obs, option: object) -> int:
    score = int(_CG_INDEPENDENT_BASE_MAIN_SCORE(obs, option))
    try:
        option_type = getattr(option, "type", None)
        active = _mine(obs).active[0] if _mine(obs).active else None
        opponent_active = _opponent(obs).active[0] if _opponent(obs).active else None
        hand_size = len(_mine(obs).hand or [])
        if option_type == OptionType.ATTACK:
            damage = _available_attack_damage(option)
            hp = int(getattr(opponent_active, "hp", 0)) if opponent_active is not None else 0
            if hp > 0 and damage >= hp:
                score += _cg_independent_value("lethal_bonus")
            elif hp > 0:
                score += _cg_independent_value("nonlethal_attack_penalty")
            if active is not None and _damage(active) > 0:
                score += _cg_independent_value("damaged_active_attack_bonus")
        elif option_type == OptionType.ABILITY and hand_size <= 3:
            score += _cg_independent_value("low_hand_ability_bonus")
        elif option_type == OptionType.PLAY:
            card_id = _option_card_id(obs, option)
            if card_id in _SUPPORTERS and hand_size <= 3:
                score += _cg_independent_value("low_hand_supporter_bonus")
            if card_id in _POKEMON_IDS and opponent_active is not None and _damage(opponent_active) > 0:
                score += _cg_independent_value("bench_threat_bonus")
        elif option_type == OptionType.RETREAT:
            if active is not None and _damage(active) > 0:
                score += _cg_independent_value("retreat_damaged_active_bonus")
            if any(_energy_count(card) >= 2 for card in (_mine(obs).bench or [])):
                score += _cg_independent_value("retreat_energy_reserve_bonus")
        elif option_type == OptionType.ATTACH:
            target = _target_for_option(obs, option)
            if _energy_count(target) >= 2:
                score += _cg_independent_value("energy_reserve_penalty")
    except Exception:
        return score
    return score


_CG_INDEPENDENT_BASE_NON_MAIN_SCORE = _non_main_score


def _non_main_score(obs, option: object) -> int:
    score = int(_CG_INDEPENDENT_BASE_NON_MAIN_SCORE(obs, option))
    try:
        context = getattr(getattr(obs, "select", None), "context", None)
        card_id = _option_card_id(obs, option)
        if context in {{SelectContext.TO_HAND, SelectContext.TO_FIELD, SelectContext.TO_BENCH,
                       SelectContext.CARD, SelectContext.LOOK}}:
            if card_id in {{RIOLU, MEGA_LUCARIO}} and not _has(obs, card_id):
                score += _cg_independent_value("search_before_evolve_bonus")
        elif context in {{SelectContext.SWITCH, SelectContext.TO_ACTIVE}}:
            target = _card_for_option(obs, option)
            if _energy_count(target) >= 2:
                score += _cg_independent_value("retreat_energy_reserve_bonus")
    except Exception:
        return score
    return score


_CG_INDEPENDENT_BASE_AGENT = agent


def agent(obs_dict: dict) -> list[int]:
    return _CG_INDEPENDENT_BASE_AGENT(obs_dict)
'''


def render_independent_source(
    config: IndependentCgParameterConfig,
    *,
    candidate_id: str,
    source_path: Path | str | None = None,
) -> str:
    """Render a candidate after checking the immutable root-policy parent."""

    if not isinstance(config, IndependentCgParameterConfig):
        raise ValueError("config must be IndependentCgParameterConfig")
    config.validate()
    if not isinstance(candidate_id, str) or not candidate_id.strip():
        raise ValueError("candidate_id must be non-empty")
    source = Path(source_path or BASE_SOURCE_PATH).resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    actual = _sha256_file(source)
    if actual != BASE_SOURCE_SHA256:
        raise ValueError(f"root source SHA mismatch: {actual} != {BASE_SOURCE_SHA256}")
    original = source.read_text(encoding="utf-8")
    if "RESEARCH_INDEPENDENT_LINEAGE" in original or "_CG_INDEPENDENT_PARAMETERS" in original:
        raise ValueError("source already contains independent policy overlay")
    return original.rstrip() + "\n" + _parameter_patch(config, candidate_id)


__all__ = [
    "BASE_SOURCE_PATH",
    "BASE_SOURCE_SHA256",
    "IndependentCgParameterConfig",
    "PARAMETER_BOUNDS",
    "SCHEMA",
    "candidate_id_for_config",
    "render_independent_source",
]
