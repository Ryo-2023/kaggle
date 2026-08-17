"""Research-only parameter surface for the immutable cg-lethal P1 policy.

The renderer appends small score overlays to the sealed P1 source.  It never
changes option legality, selection cardinality, fallback, deck contents, or
the public ``agent`` contract.  The default configuration is deliberately the
identity overlay so that it can be compared against the exact P1 package.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
import hashlib
import json
from pathlib import Path
import shutil
from typing import Mapping


ROOT = Path(__file__).resolve().parents[3]
BASE_SOURCE_PATH = (
    ROOT
    / "runs/final-sprint-autonomous/cg-policy-screen-v1-retry-safe4-20260814"
    / "candidates/cg-lethal-target-v1/package/main.py"
)
BASE_SOURCE_SHA256 = "1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9"
SCHEMA = "cg-p1-parameter-config-v1"


# The bounds are finite integer grid bounds.  ``attack_damage_weight_milli``
# is scaled by 1000 to keep CEM sampling and candidate identity deterministic.
PARAMETER_BOUNDS: dict[str, tuple[int, int]] = {
    "lethal_bonus": (0, 30000),
    "lethal_hp_margin": (-30, 30),
    "attack_damage_weight_milli": (0, 3000),
    "attack_generic_base": (0, 25000),
    "attack_982_base": (20000, 60000),
    "attack_983_base": (30000, 70000),
    "ability_bonus": (0, 50000),
    "setup_active_riolu": (25000, 60000),
    "setup_bench_riolu": (20000, 60000),
    "evolve_mega_lucario": (30000, 70000),
    "attach_mega_lucario": (25000, 65000),
    "attach_mega_2energy_bonus": (0, 20000),
    "attach_overenergy_penalty": (-60000, 0),
    "search_mega_lucario": (25000, 70000),
    "retreat_good_score": (0, 25000),
}


@dataclass(frozen=True, slots=True)
class P1ParameterConfig:
    """One fully materialized, hashable point on the P1 score surface."""

    lethal_bonus: int = 12000
    lethal_hp_margin: int = 0
    attack_damage_weight_milli: int = 1000
    attack_generic_base: int = 10000
    attack_982_base: int = 36000
    attack_983_base: int = 50000
    ability_bonus: int = 25000
    setup_active_riolu: int = 40000
    setup_bench_riolu: int = 36000
    evolve_mega_lucario: int = 50000
    attach_mega_lucario: int = 45000
    attach_mega_2energy_bonus: int = 10000
    attach_overenergy_penalty: int = -30000
    search_mega_lucario: int = 50000
    retreat_good_score: int = 12000

    @classmethod
    def default(cls) -> "P1ParameterConfig":
        return cls()

    @classmethod
    def from_mapping(cls, values: Mapping[str, object]) -> "P1ParameterConfig":
        if not isinstance(values, Mapping):
            raise ValueError("parameter config must be a mapping")
        names = {field.name for field in fields(cls)}
        unknown = set(values) - names
        if unknown:
            raise ValueError(f"unknown parameter(s): {sorted(unknown)}")
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


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def candidate_id_for_config(config: P1ParameterConfig, *, generation: int, index: int) -> str:
    config.validate()
    if generation < 0 or index < 0:
        raise ValueError("generation and index must be non-negative")
    return f"cg-p1-cem-g{generation:02d}-c{index:02d}-{config.config_sha256()[:12]}"


def _parameter_patch(config: P1ParameterConfig, candidate_id: str) -> str:
    values = json.dumps(config.as_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    encoded_id = json.dumps(candidate_id, ensure_ascii=False)
    config_sha = config.config_sha256()
    return f'''

# RESEARCH_PARAMETERIZATION: cg-p1-cem-v1
# This overlay is generated from the immutable cg-lethal P1 source.
_CG_P1_CEM_PARAMETERS = {values}
_CG_P1_CEM_CONFIG_SHA256 = {config_sha!r}
_CG_P1_CEM_CANDIDATE_ID = {encoded_id}


def _cg_p1_cem_value(name):
    return int(_CG_P1_CEM_PARAMETERS[name])


_CG_P1_CEM_BASE_SETUP_SCORE = _setup_score


def _setup_score(obs, option: object) -> int:
    if getattr(obs, "select", None) is None or getattr(option, "type", None) is None:
        return 0
    try:
        score = int(_CG_P1_CEM_BASE_SETUP_SCORE(obs, option))
        context = getattr(obs.select, "context", None)
        card_id = _option_card_id(obs, option)
        if context == SelectContext.SETUP_ACTIVE_POKEMON and card_id == RIOLU:
            return score + _cg_p1_cem_value("setup_active_riolu") - 40000
        if context == SelectContext.SETUP_BENCH_POKEMON and card_id == RIOLU:
            return score + _cg_p1_cem_value("setup_bench_riolu") - 36000
        return score
    except Exception:
        return 0


_CG_P1_CEM_BASE_EVOLVE_SCORE = _evolve_score


def _evolve_score(obs, option: object) -> int:
    if getattr(obs, "select", None) is None or getattr(option, "type", None) is None:
        return 0
    try:
        score = int(_CG_P1_CEM_BASE_EVOLVE_SCORE(obs, option))
        card_id = _option_card_id(obs, option)
        target = _target_for_option(obs, option)
        if card_id == MEGA_LUCARIO and getattr(target, "id", None) == RIOLU:
            if not getattr(target, "appearThisTurn", False):
                return score + _cg_p1_cem_value("evolve_mega_lucario") - 50000
        return score
    except Exception:
        return 0


_CG_P1_CEM_BASE_ATTACH_SCORE = _attach_score


def _attach_score(obs, option: object) -> int:
    if getattr(obs, "select", None) is None or getattr(option, "type", None) is None:
        return 0
    try:
        score = int(_CG_P1_CEM_BASE_ATTACH_SCORE(obs, option))
        if getattr(option, "type", None) != OptionType.ATTACH:
            return score
        if _option_card_id(obs, option) != FIGHTING:
            return score
        if bool(getattr(getattr(obs, "current", None), "energyAttached", False)):
            return score
        target = _target_for_option(obs, option)
        if getattr(target, "id", None) != MEGA_LUCARIO:
            return score
        energy = _energy_count(target)
        old = 45000 + (10000 if energy >= 2 else 0) + (-30000 if energy >= 3 else 0)
        new = _cg_p1_cem_value("attach_mega_lucario")
        if energy >= 2:
            new += _cg_p1_cem_value("attach_mega_2energy_bonus")
        if energy >= 3:
            new += _cg_p1_cem_value("attach_overenergy_penalty")
        return score + new - old
    except Exception:
        return 0


_CG_P1_CEM_BASE_SEARCH_PRIORITY = _search_priority


def _search_priority(obs, card_id: int | None, effect_id: int | None) -> int:
    try:
        score = int(_CG_P1_CEM_BASE_SEARCH_PRIORITY(obs, card_id, effect_id))
        in_play = {{getattr(card, "id", None) for card in _pokemon(obs)}}
        if card_id == MEGA_LUCARIO and MEGA_LUCARIO not in in_play:
            return score + _cg_p1_cem_value("search_mega_lucario") - 50000
        return score
    except Exception:
        return 0


_CG_P1_CEM_BASE_MAIN_SCORE = _main_score


def _main_score(obs, option: object) -> int:
    if getattr(obs, "select", None) is None or getattr(option, "type", None) is None:
        return 0
    try:
        score = int(_CG_P1_CEM_BASE_MAIN_SCORE(obs, option))
        option_type = getattr(option, "type", None)
        if option_type == OptionType.ATTACK:
            damage = _available_attack_damage(option)
            attack_id = getattr(option, "attackId", None)
            if attack_id == 983:
                score += _cg_p1_cem_value("attack_983_base") - 50000
            elif attack_id == 982:
                score += _cg_p1_cem_value("attack_982_base") - 36000
            else:
                score += _cg_p1_cem_value("attack_generic_base") - 10000
            score += (damage * (_cg_p1_cem_value("attack_damage_weight_milli") - 1000)) // 1000
            active = _opponent(obs).active[0] if _opponent(obs).active else None
            hp = int(getattr(active, "hp", 0)) if active is not None else 0
            old_lethal = hp > 0 and damage >= hp
            new_lethal = hp > 0 and damage >= hp + _cg_p1_cem_value("lethal_hp_margin")
            score += (_cg_p1_cem_value("lethal_bonus") if new_lethal else 0) - (12000 if old_lethal else 0)
            return score
        if option_type == OptionType.ABILITY:
            return score + _cg_p1_cem_value("ability_bonus") - 25000
        if option_type == OptionType.RETREAT:
            powered_bench = any(_energy_count(card) >= 2 for card in (_mine(obs).bench or []))
            if powered_bench:
                return score + _cg_p1_cem_value("retreat_good_score") - 12000
        return score
    except Exception:
        return 0


_CG_P1_CEM_BASE_SCORE = _score


def _score(obs, option: object) -> int:
    if getattr(obs, "select", None) is None or getattr(option, "type", None) is None:
        return 0
    try:
        return int(_CG_P1_CEM_BASE_SCORE(obs, option))
    except Exception:
        return 0


_CG_P1_CEM_BASE_AGENT = agent


def agent(obs_dict: dict) -> list[int]:
    # Keep the public entrypoint last; the engine and fallback remain P1 exact.
    return _CG_P1_CEM_BASE_AGENT(obs_dict)
'''


def render_parameterized_source(
    config: P1ParameterConfig,
    *,
    candidate_id: str,
    source_path: Path | str | None = None,
) -> str:
    """Render one candidate source after validating the immutable P1 parent."""

    config.validate()
    if not isinstance(candidate_id, str) or not candidate_id:
        raise ValueError("candidate_id must be non-empty")
    source = Path(source_path or BASE_SOURCE_PATH).resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    actual = _sha256_file(source)
    if actual != BASE_SOURCE_SHA256:
        raise ValueError(f"P1 source SHA mismatch: {actual} != {BASE_SOURCE_SHA256}")
    original = source.read_text(encoding="utf-8")
    if "_CG_P1_CEM_PARAMETERS" in original:
        raise ValueError("source already contains cg P1 CEM overlay")
    return original.rstrip() + "\n" + _parameter_patch(config, candidate_id)


def materialize_parameterized_package(
    *,
    source_package: Path | str,
    output_package: Path | str,
    config: P1ParameterConfig,
    candidate_id: str,
) -> dict[str, object]:
    """Copy a P1 package and replace only ``main.py`` plus research metadata."""

    source = Path(source_package).resolve()
    target = Path(output_package).resolve()
    source_main = source / "main.py"
    source_deck = source / "deck.csv"
    if not source.is_dir() or not source_main.is_file() or not source_deck.is_file():
        raise ValueError(f"source package is incomplete: {source}")
    if target.exists() or target.is_symlink():
        raise FileExistsError(f"candidate output exists: {target}")
    rendered = render_parameterized_source(config, candidate_id=candidate_id, source_path=source_main)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target)
    (target / "main.py").write_text(rendered, encoding="utf-8")
    manifest = {
        "schema_version": "cg-p1-cem-candidate-v1",
        "candidate_id": candidate_id,
        "config": config.as_dict(),
        "config_sha256": config.config_sha256(),
        "parent_policy_sha256": BASE_SOURCE_SHA256,
        "policy_sha256": _sha256_file(target / "main.py"),
        "deck_sha256": _sha256_file(target / "deck.csv"),
        "research_only": True,
        "submission_branch_modified": False,
    }
    (target / "cg_p1_cem_candidate_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest
