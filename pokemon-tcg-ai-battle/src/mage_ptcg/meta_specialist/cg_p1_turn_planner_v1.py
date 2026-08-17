"""Self-owned turn-planning surface over the sealed cg P1 policy.

The P1 source scores each legal option independently.  This research-only
surface adds a small, deterministic public-state turn objective: an attachment,
evolution, switch, or retreat is rewarded when it makes a visible attacker
ready.  It does not call the search API, read opaque observation fields, or
copy a public opponent implementation.  The parent package and root deck are
never modified; callers materialize an isolated candidate package.
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
SCHEMA = "cg-p1-turn-planner-config-v1"

FIGHTING = 6
MAKUHITA, HARIYAMA = 673, 674
LUNATONE, SOLROCK = 675, 676
RIOLU, MEGA_LUCARIO = 677, 678
SWITCH = 1123

PARAMETER_BOUNDS: dict[str, tuple[int, int]] = {
    "ready_attach_bonus": (0, 50000),
    "ready_evolve_bonus": (0, 50000),
    "retreat_ready_bonus": (0, 60000),
    "switch_ready_bonus": (0, 60000),
    "damaged_retreat_bonus": (0, 40000),
    "solrock_ready_bonus": (0, 40000),
}


@dataclass(frozen=True, slots=True)
class TurnPlannerConfig:
    """Bounded integer weights for one public-state turn planner."""

    ready_attach_bonus: int = 28000
    ready_evolve_bonus: int = 30000
    retreat_ready_bonus: int = 42000
    switch_ready_bonus: int = 36000
    damaged_retreat_bonus: int = 18000
    solrock_ready_bonus: int = 16000

    @classmethod
    def default(cls) -> "TurnPlannerConfig":
        return cls()

    @classmethod
    def from_mapping(cls, values: Mapping[str, object]) -> "TurnPlannerConfig":
        if not isinstance(values, Mapping):
            raise ValueError("turn-planner config must be a mapping")
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
                raise ValueError(f"parameter {name} out of bounds: {value}")

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


def candidate_id_for_config(config: TurnPlannerConfig, *, generation: int, index: int) -> str:
    config.validate()
    if type(generation) is not int or type(index) is not int or generation < 0 or index < 0:
        raise ValueError("generation and index must be non-negative integers")
    return f"cg-p1-turn-planner-g{generation:02d}-c{index:02d}-{config.config_sha256()[:12]}"


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _base_source(source_path: Path | str | None = None) -> str:
    source = Path(source_path or BASE_SOURCE_PATH).resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    rendered = source.read_bytes()
    actual = _sha256_bytes(rendered)
    if actual != BASE_SOURCE_SHA256:
        raise ValueError(f"P1 source SHA mismatch: {actual} != {BASE_SOURCE_SHA256}")
    return rendered.decode("utf-8")


def _parameter_patch(config: TurnPlannerConfig, candidate_id: str) -> str:
    values = json.dumps(config.as_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f'''\n\n# RESEARCH_VARIANT: cg-p1-turn-planner-v1\n# Public-state-only turn planning overlay.\n_CG_TP_PARAMETERS = {values}\n_CG_TP_CONFIG_SHA256 = {config.config_sha256()!r}\n_CG_TP_CANDIDATE_ID = {candidate_id!r}\n\ndef _cg_tp_value(name: str) -> int:\n    return int(_CG_TP_PARAMETERS[name])\n\ndef _cg_tp_ready(card: object | None, obs: object) -> bool:\n    if card is None:\n        return False\n    try:\n        card_id = int(getattr(card, "id", -1))\n        energy = int(_energy_count(card))\n        if card_id == MEGA_LUCARIO:\n            return energy >= 2\n        if card_id == HARIYAMA:\n            return energy >= 3\n        if card_id == SOLROCK:\n            return energy >= 1 and _has(obs, LUNATONE)\n    except Exception:\n        return False\n    return False\n\ndef _cg_tp_ready_bench(obs) -> bool:\n    try:\n        return any(_cg_tp_ready(card, obs) for card in list(_mine(obs).bench or []))\n    except Exception:\n        return False\n\ndef _cg_tp_active_attack_exists(obs) -> bool:\n    try:\n        return any(\n            getattr(option, "type", None) == OptionType.ATTACK\n            for option in list(getattr(obs.select, "option", None) or [])\n        )\n    except Exception:\n        return False\n\ndef _cg_tp_active_damaged(obs) -> bool:\n    try:\n        active = _mine(obs).active[0] if _mine(obs).active else None\n        return active is not None and _damage(active) > 0\n    except Exception:\n        return False\n\ndef _cg_tp_attach_bonus(obs, option) -> int:\n    if getattr(option, "type", None) != OptionType.ATTACH:\n        return 0\n    try:\n        if _option_card_id(obs, option) != FIGHTING:\n            return 0\n        target = _target_for_option(obs, option)\n        if target is None:\n            return 0\n        target_id = getattr(target, "id", None)\n        energy = int(_energy_count(target))\n        if target_id == MEGA_LUCARIO and energy == 1:\n            return _cg_tp_value("ready_attach_bonus")\n        if target_id == HARIYAMA and energy == 2:\n            return _cg_tp_value("ready_attach_bonus")\n        if target_id == SOLROCK and energy == 0 and _has(obs, LUNATONE):\n            return _cg_tp_value("solrock_ready_bonus")\n    except Exception:\n        return 0\n    return 0\n\ndef _cg_tp_evolve_bonus(obs, option) -> int:\n    if getattr(option, "type", None) != OptionType.EVOLVE:\n        return 0\n    try:\n        card_id = _option_card_id(obs, option)\n        target = _target_for_option(obs, option)\n        target_id = getattr(target, "id", None)\n        if card_id == MEGA_LUCARIO and target_id == RIOLU and not getattr(target, "appearThisTurn", False):\n            return _cg_tp_value("ready_evolve_bonus")\n        if card_id == HARIYAMA and target_id == MAKUHITA and not getattr(target, "appearThisTurn", False):\n            return _cg_tp_value("ready_evolve_bonus") // 2\n    except Exception:\n        return 0\n    return 0\n\ndef _cg_tp_main_bonus(obs, option) -> int:\n    try:\n        ready_bench = _cg_tp_ready_bench(obs)\n        active_attack = _cg_tp_active_attack_exists(obs)\n        if getattr(option, "type", None) == OptionType.RETREAT and ready_bench:\n            bonus = _cg_tp_value("retreat_ready_bonus")\n            if _cg_tp_active_damaged(obs):\n                bonus += _cg_tp_value("damaged_retreat_bonus")\n            if active_attack:\n                bonus //= 3\n            return bonus\n        if getattr(option, "type", None) == OptionType.PLAY and _option_card_id(obs, option) == SWITCH and ready_bench:\n            bonus = _cg_tp_value("switch_ready_bonus")\n            if _cg_tp_active_damaged(obs):\n                bonus += _cg_tp_value("damaged_retreat_bonus") // 2\n            if active_attack:\n                bonus //= 3\n            return bonus\n        return _cg_tp_attach_bonus(obs, option) + _cg_tp_evolve_bonus(obs, option)\n    except Exception:\n        return 0\n\n_CG_TP_BASE_MAIN_SCORE = _main_score\ndef _main_score(obs, option: object) -> int:\n    if getattr(obs, "select", None) is None or getattr(option, "type", None) is None:\n        return 0\n    try:\n        return int(_CG_TP_BASE_MAIN_SCORE(obs, option)) + _cg_tp_main_bonus(obs, option)\n    except Exception:\n        return 0\n\n_CG_TP_BASE_SCORE = _score\ndef _score(obs, option: object) -> int:\n    if getattr(obs, "select", None) is None or getattr(option, "type", None) is None:\n        return 0\n    try:\n        return int(_CG_TP_BASE_SCORE(obs, option))\n    except Exception:\n        return 0\n\n_CG_TP_BASE_AGENT = agent\ndef agent(obs_dict: dict) -> list[int]:\n    return _CG_TP_BASE_AGENT(obs_dict)\n'''


def render_turn_planner_source(
    config: TurnPlannerConfig,
    *,
    candidate_id: str,
    source_path: Path | str | None = None,
) -> str:
    config.validate()
    if not isinstance(candidate_id, str) or not candidate_id:
        raise ValueError("candidate_id must be non-empty")
    return _base_source(source_path) + _parameter_patch(config, candidate_id)


def materialize_turn_planner_package(
    *,
    source_package: Path | str,
    output_package: Path | str,
    config: TurnPlannerConfig,
    candidate_id: str,
) -> dict[str, object]:
    """Materialize one isolated candidate without touching the parent."""

    source = Path(source_package).resolve()
    target = Path(output_package).resolve()
    source_main = source / "main.py"
    if not source.is_dir() or not source_main.is_file() or not (source / "deck.csv").is_file():
        raise ValueError(f"P1 source package is incomplete: {source}")
    if _sha256_bytes(source_main.read_bytes()) != BASE_SOURCE_SHA256:
        raise ValueError("P1 source package main SHA mismatch")
    if target.exists() or target.is_symlink():
        raise FileExistsError(f"turn planner output exists: {target}")
    config.validate()
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target)
    rendered = render_turn_planner_source(config, candidate_id=candidate_id).encode("utf-8")
    (target / "main.py").write_bytes(rendered)
    return {
        "schema_version": "cg-p1-turn-planner-candidate-v1",
        "candidate_id": candidate_id,
        "parent_policy_sha256": BASE_SOURCE_SHA256,
        "policy_sha256": _sha256_bytes(rendered),
        "deck_sha256": _sha256_bytes((target / "deck.csv").read_bytes()),
        "config": config.as_dict(),
        "config_sha256": config.config_sha256(),
        "actor_visible_only": True,
        "search_api_used": False,
        "research_only": True,
        "authority": {"training": False, "promotion": False, "submission": False},
    }


__all__ = [
    "BASE_SOURCE_SHA256",
    "PARAMETER_BOUNDS",
    "TurnPlannerConfig",
    "candidate_id_for_config",
    "materialize_turn_planner_package",
    "render_turn_planner_source",
]
