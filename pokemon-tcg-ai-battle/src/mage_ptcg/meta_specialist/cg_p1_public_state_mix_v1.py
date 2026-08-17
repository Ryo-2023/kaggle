"""Public-state-only policy surface over the sealed cg-lethal P1 policy.

This renderer is a separate research surface from the existing P1 parameter
and turn-planner surfaces.  It only uses visible board state, visible prize
counts, and the legal option being scored.  It never reads an opponent hand,
deck, discard contents, or card identities hidden behind the engine boundary.
The renderer is intentionally package-oriented: callers must provide a
verified self-owned deck package and receive an isolated research candidate.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
import hashlib
import json
from pathlib import Path
import shutil
from typing import Mapping

from .self_owned_cg_deck_v1 import canonical_deck_sha256_v1
from .self_owned_cg_package_v1 import (
    PACKAGE_SCHEMA_VERSION_V1,
    SelfOwnedCgPackageV1Error,
    _canonical_json,
    _patch_root_deck_constant,
    _prepare_empty_root,
    _regular_tree,
    _runtime_file_hashes,
    _semantic_sha,
    _sha256_file,
    _write_exclusive,
    _parse_deck_bytes,
    verify_self_owned_cg_package_v1,
)


ROOT = Path(__file__).resolve().parents[3]
BASE_SOURCE_PATH = (
    ROOT
    / "runs/final-sprint-autonomous/cg-policy-screen-v1-retry-safe4-20260814"
    / "candidates/cg-lethal-target-v1/package/main.py"
)
BASE_SOURCE_SHA256 = "1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9"
SCHEMA = "cg-p1-public-state-mix-config-v1"


# The surface is deliberately small and bounded.  Zero is the identity
# overlay; positive/negative values are sampled by a later source generator.
PARAMETER_BOUNDS: dict[str, tuple[int, int]] = {
    "behind_attack_bonus": (0, 30000),
    "ahead_lethal_bonus": (0, 30000),
    "overkill_penalty": (-30000, 0),
    "damaged_active_retreat_bonus": (0, 30000),
    "bench_ready_switch_bonus": (0, 30000),
    "target_low_hp_bonus": (0, 25000),
    "target_high_damage_bonus": (0, 25000),
    "prize_gap_threshold": (1, 3),
}


@dataclass(frozen=True, slots=True)
class PublicStateMixConfig:
    """Bounded integer weights for one public-state-only policy point."""

    behind_attack_bonus: int = 0
    ahead_lethal_bonus: int = 0
    overkill_penalty: int = 0
    damaged_active_retreat_bonus: int = 0
    bench_ready_switch_bonus: int = 0
    target_low_hp_bonus: int = 0
    target_high_damage_bonus: int = 0
    prize_gap_threshold: int = 1

    @classmethod
    def default(cls) -> "PublicStateMixConfig":
        return cls()

    @classmethod
    def from_mapping(cls, values: Mapping[str, object]) -> "PublicStateMixConfig":
        if not isinstance(values, Mapping):
            raise ValueError("public-state mix config must be a mapping")
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


def candidate_id_for_config(
    config: PublicStateMixConfig, *, generation: int, index: int
) -> str:
    config.validate()
    if type(generation) is not int or type(index) is not int or generation < 0 or index < 0:
        raise ValueError("generation and index must be non-negative integers")
    return (
        f"cg-p1-public-state-mix-g{generation:02d}-c{index:02d}-"
        f"{config.config_sha256()[:12]}"
    )


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


def _parameter_patch(config: PublicStateMixConfig, candidate_id: str) -> str:
    values = json.dumps(config.as_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f'''

# RESEARCH_VARIANT: cg-p1-public-state-mix-v1
# Public-state-only overlay: prize race, visible board stability, and target pressure.
_CG_PSM_PARAMETERS = {values}
_CG_PSM_CONFIG_SHA256 = {config.config_sha256()!r}
_CG_PSM_CANDIDATE_ID = {candidate_id!r}

def _cg_psm_value(name: str) -> int:
    return int(_CG_PSM_PARAMETERS[name])

def _cg_psm_hp(card: object | None) -> int:
    if card is None:
        return 0
    try:
        return max(0, int(getattr(card, "hp", 0)))
    except Exception:
        return 0

def _cg_psm_max_hp(card: object | None) -> int:
    if card is None:
        return 0
    try:
        return max(_cg_psm_hp(card), int(getattr(card, "maxHp", 0)))
    except Exception:
        return _cg_psm_hp(card)

def _cg_psm_damage(card: object | None) -> int:
    return max(0, _cg_psm_max_hp(card) - _cg_psm_hp(card))

def _cg_psm_prize_count(player: object | None) -> int:
    # Only the public count is used; hidden prize card identities are ignored.
    if player is None:
        return 0
    try:
        prize = getattr(player, "prize", None)
        return len(prize or [])
    except Exception:
        return 0

def _cg_psm_active(player: object | None) -> object | None:
    try:
        active = getattr(player, "active", None) or []
        return active[0] if active else None
    except Exception:
        return None

def _cg_psm_ready_bench(obs) -> bool:
    try:
        return any(_energy_count(card) >= 2 for card in (_mine(obs).bench or []))
    except Exception:
        return False

def _cg_psm_main_bonus(obs, option: object) -> int:
    option_type = getattr(option, "type", None)
    if option_type == OptionType.ATTACK:
        try:
            active = _cg_psm_active(_opponent(obs))
            hp = _cg_psm_hp(active)
            damage = _available_attack_damage(option)
            gap = _cg_psm_prize_count(_mine(obs)) - _cg_psm_prize_count(_opponent(obs))
            threshold = _cg_psm_value("prize_gap_threshold")
            bonus = 0
            if gap >= threshold and hp > 0 and damage < hp:
                bonus += _cg_psm_value("behind_attack_bonus")
            if gap <= -threshold and hp > 0 and damage >= hp:
                bonus += _cg_psm_value("ahead_lethal_bonus")
            if hp > 0 and damage >= hp + 100:
                bonus += _cg_psm_value("overkill_penalty")
            return bonus
        except Exception:
            return 0
    if option_type == OptionType.RETREAT:
        try:
            active = _cg_psm_active(_mine(obs))
            if _cg_psm_damage(active) > 0 and _cg_psm_ready_bench(obs):
                return _cg_psm_value("damaged_active_retreat_bonus")
        except Exception:
            return 0
        return 0
    if option_type == OptionType.PLAY:
        try:
            if _option_card_id(obs, option) == SWITCH:
                active = _cg_psm_active(_mine(obs))
                if _cg_psm_damage(active) > 0 and _cg_psm_ready_bench(obs):
                    return _cg_psm_value("bench_ready_switch_bonus")
        except Exception:
            return 0
    return 0

_CG_PSM_BASE_MAIN_SCORE = _main_score
def _main_score(obs, option: object) -> int:
    if getattr(obs, "select", None) is None or getattr(option, "type", None) is None:
        return 0
    try:
        return int(_CG_PSM_BASE_MAIN_SCORE(obs, option)) + _cg_psm_main_bonus(obs, option)
    except Exception:
        return 0

_CG_PSM_BASE_NON_MAIN_SCORE = _non_main_score
def _non_main_score(obs, option: object) -> int:
    if getattr(obs, "select", None) is None or getattr(option, "type", None) is None:
        return 0
    try:
        score = int(_CG_PSM_BASE_NON_MAIN_SCORE(obs, option))
        context = getattr(obs.select, "context", None)
        if context in {{SelectContext.DAMAGE, SelectContext.DAMAGE_COUNTER, SelectContext.DAMAGE_COUNTER_ANY}}:
            target = _card_for_option(obs, option)
            hp = _cg_psm_hp(target)
            if 0 < hp <= 80:
                score += _cg_psm_value("target_low_hp_bonus")
            if _cg_psm_damage(target) >= 100:
                score += _cg_psm_value("target_high_damage_bonus")
        return score
    except Exception:
        return 0

_CG_PSM_BASE_SCORE = _score
def _score(obs, option: object) -> int:
    if getattr(obs, "select", None) is None or getattr(option, "type", None) is None:
        return 0
    try:
        return int(_CG_PSM_BASE_SCORE(obs, option))
    except Exception:
        return 0

_CG_PSM_BASE_AGENT = agent
def agent(obs_dict: dict) -> list[int]:
    return _CG_PSM_BASE_AGENT(obs_dict)
'''


def render_public_state_mix_source(
    config: PublicStateMixConfig,
    *,
    candidate_id: str,
    source_path: Path | str | None = None,
) -> str:
    config.validate()
    if not isinstance(candidate_id, str) or not candidate_id:
        raise ValueError("candidate_id must be non-empty")
    return _base_source(source_path) + _parameter_patch(config, candidate_id)


def materialize_public_state_mix_package(
    *,
    source_package: Path | str,
    self_owned_deck_package: Path | str,
    output_package: Path | str,
    config: PublicStateMixConfig,
    candidate_id: str,
) -> dict[str, object]:
    """Create one isolated public-state candidate bound to a self-owned deck."""

    config.validate()
    if not isinstance(candidate_id, str) or not candidate_id.strip():
        raise SelfOwnedCgPackageV1Error("candidate_id must be non-empty")
    source = Path(source_package).resolve()
    source_main = source / "main.py"
    source_cg = source / "cg"
    if source_main.is_symlink() or not source_main.is_file():
        raise SelfOwnedCgPackageV1Error("P1 source package main.py is not regular")
    if _sha256_file(source_main) != BASE_SOURCE_SHA256:
        raise SelfOwnedCgPackageV1Error("P1 source policy SHA mismatch")
    _regular_tree(source_cg)

    deck_package = Path(self_owned_deck_package).resolve()
    deck_manifest = verify_self_owned_cg_package_v1(deck_package)
    if deck_manifest.get("parent_policy_sha256") != BASE_SOURCE_SHA256:
        raise SelfOwnedCgPackageV1Error("self-owned deck package is not P1-bound")
    deck_path = deck_package / "deck.csv"
    deck_bytes = deck_path.read_bytes()
    card_ids = _parse_deck_bytes(deck_bytes)
    canonical_deck_sha = canonical_deck_sha256_v1(card_ids)
    if canonical_deck_sha != deck_manifest.get("canonical_deck_sha256"):
        raise SelfOwnedCgPackageV1Error("self-owned deck manifest does not bind canonical deck")

    rendered = render_public_state_mix_source(
        config,
        candidate_id=candidate_id,
        source_path=source_main,
    )
    patched = _patch_root_deck_constant(rendered, card_ids)
    if patched == rendered:
        raise SelfOwnedCgPackageV1Error("public-state policy was not deck-bound")

    target = Path(output_package).resolve()
    _prepare_empty_root(target)
    _regular_tree(source_cg)
    shutil.copytree(
        source_cg,
        target / "cg",
        dirs_exist_ok=True,
        symlinks=False,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    _write_exclusive(target / "main.py", patched.encode("utf-8"))
    _write_exclusive(target / "deck.csv", deck_bytes)
    runtime_files = {
        f"cg/{relative_path}": digest
        for relative_path, digest in _runtime_file_hashes(target / "cg").items()
    }
    payload: dict[str, object] = {
        "schema_version": PACKAGE_SCHEMA_VERSION_V1,
        "candidate_id": candidate_id,
        "archetype_id": deck_manifest.get("archetype_id", "self-owned-cg"),
        "parent_deck": None,
        "public_parent_read": False,
        "parent_policy_sha256": BASE_SOURCE_SHA256,
        "policy_sha256": _sha256_file(target / "main.py"),
        "deck_file_sha256": _sha256_file(target / "deck.csv"),
        "canonical_deck_sha256": canonical_deck_sha,
        "root_deck_replaced": True,
        "runtime_files": runtime_files,
        "research_only": True,
        "authority": {
            "training_allowed": False,
            "promotion_allowed": False,
            "submission_allowed": False,
        },
    }
    payload["manifest_sha256"] = _semantic_sha(payload)
    _write_exclusive(target / "self_owned_cg_package_manifest.json", _canonical_json(payload))
    parameter_manifest = {
        "schema_version": "cg-p1-public-state-mix-candidate-v1",
        "candidate_id": candidate_id,
        "config": config.as_dict(),
        "config_sha256": config.config_sha256(),
        "parent_policy_sha256": BASE_SOURCE_SHA256,
        "policy_sha256": payload["policy_sha256"],
        "canonical_deck_sha256": canonical_deck_sha,
        "actor_visible_only": True,
        "search_api_used": False,
        "research_only": True,
    }
    _write_exclusive(
        target / "cg_p1_public_state_mix_manifest.json",
        _canonical_json(parameter_manifest) + b"\n",
    )
    return verify_self_owned_cg_package_v1(target)


__all__ = [
    "BASE_SOURCE_SHA256",
    "PARAMETER_BOUNDS",
    "PublicStateMixConfig",
    "candidate_id_for_config",
    "materialize_public_state_mix_package",
    "render_public_state_mix_source",
]
