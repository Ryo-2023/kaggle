"""Public-state action-conditioned renderer over the sealed cg-lethal P1 policy.

The renderer is a research-only policy surface.  It adds bounded interactions
between a legal action family and coarse actor-visible state buckets while
leaving the P1 option legality, selection cardinality, fallback, and runtime
package untouched.  Hidden opponent zones are never inspected.
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
SCHEMA = "cg-p1-action-conditioned-config-v1"


PARAMETER_BOUNDS: dict[str, tuple[int, int]] = {
    "attack_lethal_behind_bonus": (0, 30000),
    "attack_lethal_ahead_bonus": (0, 30000),
    "attack_nonlethal_behind_penalty": (-30000, 0),
    "attack_nonlethal_ahead_bonus": (0, 30000),
    "attack_early_bonus": (-15000, 15000),
    "attack_late_bonus": (-15000, 15000),
    "retreat_damaged_bonus": (0, 30000),
    "retreat_behind_bonus": (0, 25000),
    "retreat_powered_bench_bonus": (0, 25000),
    "attach_reserve_penalty": (-30000, 0),
    "evolve_early_bonus": (0, 30000),
    "switch_damaged_bonus": (0, 25000),
}


@dataclass(frozen=True, slots=True)
class ActionConditionedConfig:
    """One bounded point on the action-family × public-state surface."""

    attack_lethal_behind_bonus: int = 0
    attack_lethal_ahead_bonus: int = 0
    attack_nonlethal_behind_penalty: int = 0
    attack_nonlethal_ahead_bonus: int = 0
    attack_early_bonus: int = 0
    attack_late_bonus: int = 0
    retreat_damaged_bonus: int = 0
    retreat_behind_bonus: int = 0
    retreat_powered_bench_bonus: int = 0
    attach_reserve_penalty: int = 0
    evolve_early_bonus: int = 0
    switch_damaged_bonus: int = 0

    @classmethod
    def default(cls) -> "ActionConditionedConfig":
        return cls()

    @classmethod
    def from_mapping(cls, values: Mapping[str, object]) -> "ActionConditionedConfig":
        if not isinstance(values, Mapping):
            raise ValueError("action-conditioned config must be a mapping")
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
    config: ActionConditionedConfig, *, generation: int, index: int
) -> str:
    config.validate()
    if type(generation) is not int or type(index) is not int or generation < 0 or index < 0:
        raise ValueError("generation and index must be non-negative integers")
    return f"cg-action-conditioned-g{generation:02d}-c{index:02d}-{config.config_sha256()[:12]}"


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


def _parameter_patch(config: ActionConditionedConfig, candidate_id: str) -> str:
    values = json.dumps(config.as_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f'''

# RESEARCH_VARIANT: cg-p1-action-conditioned-v1
# Public-state-only action-family overlay; hidden opponent zones are never read.
_CG_ACTION_CONDITIONED_PARAMETERS = {values}
_CG_ACTION_CONDITIONED_CONFIG_SHA256 = {config.config_sha256()!r}
_CG_ACTION_CONDITIONED_CANDIDATE_ID = {candidate_id!r}


def _cg_ac_value(name: str) -> int:
    return int(_CG_ACTION_CONDITIONED_PARAMETERS[name])


def _cg_ac_active(player: object | None) -> object | None:
    try:
        active = getattr(player, "active", None) or []
        return active[0] if active else None
    except Exception:
        return None


def _cg_ac_damage(card: object | None) -> int:
    if card is None:
        return 0
    try:
        maximum = int(getattr(card, "maxHp", getattr(card, "hp", 0)))
        current = int(getattr(card, "hp", 0))
        return max(0, maximum - current)
    except Exception:
        return 0


def _cg_ac_energy(card: object | None) -> int:
    try:
        attached = getattr(card, "energyCards", None)
        return len(attached) if attached is not None else len(getattr(card, "energies", []) or [])
    except Exception:
        return 0


def _cg_ac_prize_count(player: object | None) -> int:
    # Only the public count is read; card identities are never inspected.
    try:
        return len(getattr(player, "prize", None) or [])
    except Exception:
        return 0


def _cg_ac_phase(obs) -> int:
    try:
        turn = int(getattr(getattr(obs, "current", None), "turn", 0))
    except Exception:
        turn = 0
    if turn <= 2:
        return 0
    if turn <= 5:
        return 1
    return 2


def _cg_ac_delta(obs, option: object) -> int:
    """Return only the action-conditioned public-state interaction delta."""
    try:
        mine = _mine(obs)
        opponent = _opponent(obs)
        mine_active = _cg_ac_active(mine)
        opponent_active = _cg_ac_active(opponent)
        behind = _cg_ac_prize_count(mine) > _cg_ac_prize_count(opponent)
        ahead = _cg_ac_prize_count(mine) < _cg_ac_prize_count(opponent)
        phase = _cg_ac_phase(obs)
        context = getattr(getattr(obs, "select", None), "context", None)
        option_type = getattr(option, "type", None)
        delta = 0

        if context == SelectContext.MAIN:
            if option_type == OptionType.ATTACK:
                damage = _available_attack_damage(option)
                hp = int(getattr(opponent_active, "hp", 0)) if opponent_active is not None else 0
                lethal = hp > 0 and damage >= hp
                if lethal:
                    delta += _cg_ac_value("attack_lethal_behind_bonus" if behind else "attack_lethal_ahead_bonus")
                elif hp > 0:
                    delta += _cg_ac_value("attack_nonlethal_behind_penalty" if behind else "attack_nonlethal_ahead_bonus")
                if phase == 0:
                    delta += _cg_ac_value("attack_early_bonus")
                elif phase == 2:
                    delta += _cg_ac_value("attack_late_bonus")
            elif option_type == OptionType.RETREAT:
                if _cg_ac_damage(mine_active) > 0:
                    delta += _cg_ac_value("retreat_damaged_bonus")
                if behind:
                    delta += _cg_ac_value("retreat_behind_bonus")
                if any(_cg_ac_energy(card) >= 2 for card in (getattr(mine, "bench", None) or [])):
                    delta += _cg_ac_value("retreat_powered_bench_bonus")
            elif option_type == OptionType.ATTACH:
                target = _target_for_option(obs, option)
                if _cg_ac_energy(target) >= 2:
                    delta += _cg_ac_value("attach_reserve_penalty")
            elif option_type == OptionType.EVOLVE and phase == 0:
                delta += _cg_ac_value("evolve_early_bonus")
            elif option_type == OptionType.PLAY:
                if _option_card_id(obs, option) == SWITCH and _cg_ac_damage(mine_active) > 0:
                    delta += _cg_ac_value("switch_damaged_bonus")
            return delta

        if context in {{SelectContext.ATTACH_TO, SelectContext.ATTACH_FROM}}:
            target = _target_for_option(obs, option)
            if _cg_ac_energy(target) >= 2:
                delta += _cg_ac_value("attach_reserve_penalty")
        elif context in {{SelectContext.SWITCH, SelectContext.TO_ACTIVE}}:
            if _cg_ac_damage(_card_for_option(obs, option)) > 0:
                delta += _cg_ac_value("switch_damaged_bonus")
        elif context == SelectContext.ATTACK and phase == 0:
            delta += _cg_ac_value("attack_early_bonus")
        return delta
    except Exception:
        return 0


_CG_ACTION_CONDITIONED_BASE_MAIN_SCORE = _main_score


def _main_score(obs, option: object) -> int:
    if getattr(obs, "select", None) is None or getattr(option, "type", None) is None:
        return 0
    try:
        return int(_CG_ACTION_CONDITIONED_BASE_MAIN_SCORE(obs, option)) + _cg_ac_delta(obs, option)
    except Exception:
        return 0


_CG_ACTION_CONDITIONED_BASE_NON_MAIN_SCORE = _non_main_score


def _non_main_score(obs, option: object) -> int:
    if getattr(obs, "select", None) is None or getattr(option, "type", None) is None:
        return 0
    try:
        return int(_CG_ACTION_CONDITIONED_BASE_NON_MAIN_SCORE(obs, option)) + _cg_ac_delta(obs, option)
    except Exception:
        return 0


_CG_ACTION_CONDITIONED_BASE_SCORE = _score


def _score(obs, option: object) -> int:
    if getattr(obs, "select", None) is None or getattr(option, "type", None) is None:
        return 0
    try:
        return int(_CG_ACTION_CONDITIONED_BASE_SCORE(obs, option))
    except Exception:
        return 0


_CG_ACTION_CONDITIONED_BASE_AGENT = agent


def agent(obs_dict: dict) -> list[int]:
    return _CG_ACTION_CONDITIONED_BASE_AGENT(obs_dict)
'''


def render_action_conditioned_source(
    config: ActionConditionedConfig,
    *,
    candidate_id: str,
    source_path: Path | str | None = None,
) -> str:
    config.validate()
    if not isinstance(candidate_id, str) or not candidate_id.strip():
        raise ValueError("candidate_id must be non-empty")
    source = Path(source_path or BASE_SOURCE_PATH).resolve()
    original = _base_source(source)
    if "CG_ACTION_CONDITIONED_PARAMETERS" in original:
        raise ValueError("source already contains action-conditioned overlay")
    return original.rstrip() + "\n" + _parameter_patch(config, candidate_id)


def materialize_action_conditioned_package(
    *,
    source_package: Path | str,
    self_owned_deck_package: Path | str,
    output_package: Path | str,
    config: ActionConditionedConfig,
    candidate_id: str,
) -> dict[str, object]:
    """Render one action-conditioned policy and bind it to a self-owned deck."""

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

    rendered = render_action_conditioned_source(
        config, candidate_id=candidate_id, source_path=source_main
    )
    patched = _patch_root_deck_constant(rendered, card_ids)
    if patched == rendered:
        raise SelfOwnedCgPackageV1Error("action-conditioned policy was not deck-bound")

    target = Path(output_package).resolve()
    _prepare_empty_root(target)
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
    sidecar = {
        "schema_version": "cg-p1-action-conditioned-renderer-v1",
        "candidate_id": candidate_id,
        "config": config.as_dict(),
        "config_sha256": config.config_sha256(),
        "parent_policy_sha256": BASE_SOURCE_SHA256,
        "policy_sha256": payload["policy_sha256"],
        "canonical_deck_sha256": canonical_deck_sha,
        "actor_visible_only": True,
        "hidden_opponent_zones_used": False,
        "research_only": True,
    }
    _write_exclusive(target / "cg_action_conditioned_manifest.json", _canonical_json(sidecar) + b"\n")
    return verify_self_owned_cg_package_v1(target)


__all__ = [
    "BASE_SOURCE_PATH",
    "BASE_SOURCE_SHA256",
    "PARAMETER_BOUNDS",
    "SCHEMA",
    "ActionConditionedConfig",
    "candidate_id_for_config",
    "materialize_action_conditioned_package",
    "render_action_conditioned_source",
]
