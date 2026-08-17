"""Public-seat-conditioned renderer over the sealed cg-lethal P1 policy.

This research-only surface adds four action-family offsets for each public
player index.  It never reads hidden opponent zones and does not alter action
legality, selection cardinality, deck binding, or fallback behavior.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
import hashlib
import json
from pathlib import Path
import shutil
from typing import Mapping

from .cg_p1_action_conditioned_renderer_v1 import (
    BASE_SOURCE_PATH,
    BASE_SOURCE_SHA256,
    _base_source,
)
from .self_owned_cg_deck_v1 import canonical_deck_sha256_v1
from .self_owned_cg_package_v1 import (
    PACKAGE_SCHEMA_VERSION_V1,
    SelfOwnedCgPackageV1Error,
    _canonical_json,
    _parse_deck_bytes,
    _patch_root_deck_constant,
    _prepare_empty_root,
    _regular_tree,
    _runtime_file_hashes,
    _semantic_sha,
    _sha256_file,
    _write_exclusive,
    verify_self_owned_cg_package_v1,
)


SCHEMA = "cg-p1-seat-conditioned-config-v1"


PARAMETER_BOUNDS: dict[str, tuple[int, int]] = {
    "seat0_attack_bonus": (-20000, 20000),
    "seat1_attack_bonus": (-20000, 20000),
    "seat0_retreat_bonus": (-20000, 20000),
    "seat1_retreat_bonus": (-20000, 20000),
    "seat0_attach_bonus": (-20000, 20000),
    "seat1_attach_bonus": (-20000, 20000),
    "seat0_evolve_bonus": (-20000, 20000),
    "seat1_evolve_bonus": (-20000, 20000),
}


@dataclass(frozen=True, slots=True)
class SeatConditionedConfig:
    """One bounded point on the public seat × action-family surface."""

    seat0_attack_bonus: int = 0
    seat1_attack_bonus: int = 0
    seat0_retreat_bonus: int = 0
    seat1_retreat_bonus: int = 0
    seat0_attach_bonus: int = 0
    seat1_attach_bonus: int = 0
    seat0_evolve_bonus: int = 0
    seat1_evolve_bonus: int = 0

    @classmethod
    def default(cls) -> "SeatConditionedConfig":
        return cls()

    @classmethod
    def from_mapping(cls, values: Mapping[str, object]) -> "SeatConditionedConfig":
        if not isinstance(values, Mapping):
            raise ValueError("seat-conditioned config must be a mapping")
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
    config: SeatConditionedConfig, *, generation: int, index: int
) -> str:
    config.validate()
    if type(generation) is not int or type(index) is not int or generation < 0 or index < 0:
        raise ValueError("generation and index must be non-negative integers")
    return f"cg-seat-conditioned-g{generation:02d}-c{index:02d}-{config.config_sha256()[:12]}"


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _parameter_patch(config: SeatConditionedConfig, candidate_id: str) -> str:
    values = json.dumps(config.as_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f'''

# RESEARCH_VARIANT: cg-p1-seat-conditioned-v1
# Public yourIndex is the only seat signal; hidden opponent zones are never read.
_CG_SEAT_CONDITIONED_PARAMETERS = {values}
_CG_SEAT_CONDITIONED_CONFIG_SHA256 = {config.config_sha256()!r}
_CG_SEAT_CONDITIONED_CANDIDATE_ID = {candidate_id!r}


def _cg_seat_value(name: str, seat: int) -> int:
    prefix = "seat1_" if int(seat) == 1 else "seat0_"
    return int(_CG_SEAT_CONDITIONED_PARAMETERS[prefix + name])


def _cg_seat_index(obs) -> int:
    try:
        value = int(getattr(getattr(obs, "current", None), "yourIndex", 0))
    except Exception:
        value = 0
    return 1 if value == 1 else 0


def _cg_seat_delta(obs, option: object) -> int:
    """Return only an action-family offset keyed by public yourIndex."""
    try:
        context = getattr(getattr(obs, "select", None), "context", None)
        option_type = getattr(option, "type", None)
        seat = _cg_seat_index(obs)
        family = None
        if context == SelectContext.MAIN:
            family = {{
                OptionType.ATTACK: "attack_bonus",
                OptionType.RETREAT: "retreat_bonus",
                OptionType.ATTACH: "attach_bonus",
                OptionType.EVOLVE: "evolve_bonus",
            }}.get(option_type)
        elif context == SelectContext.ATTACK:
            family = "attack_bonus"
        elif context in {{SelectContext.ATTACH_TO, SelectContext.ATTACH_FROM}}:
            family = "attach_bonus"
        elif context in {{SelectContext.SWITCH, SelectContext.TO_ACTIVE}}:
            family = "retreat_bonus"
        return _cg_seat_value(family, seat) if family is not None else 0
    except Exception:
        return 0


_CG_SEAT_CONDITIONED_BASE_MAIN_SCORE = _main_score


def _main_score(obs, option: object) -> int:
    if getattr(obs, "select", None) is None or getattr(option, "type", None) is None:
        return 0
    try:
        return int(_CG_SEAT_CONDITIONED_BASE_MAIN_SCORE(obs, option)) + _cg_seat_delta(obs, option)
    except Exception:
        return 0


_CG_SEAT_CONDITIONED_BASE_NON_MAIN_SCORE = _non_main_score


def _non_main_score(obs, option: object) -> int:
    if getattr(obs, "select", None) is None or getattr(option, "type", None) is None:
        return 0
    try:
        return int(_CG_SEAT_CONDITIONED_BASE_NON_MAIN_SCORE(obs, option)) + _cg_seat_delta(obs, option)
    except Exception:
        return 0


_CG_SEAT_CONDITIONED_BASE_AGENT = agent


def agent(obs_dict: dict) -> list[int]:
    return _CG_SEAT_CONDITIONED_BASE_AGENT(obs_dict)
'''


def render_seat_conditioned_source(
    config: SeatConditionedConfig,
    *,
    candidate_id: str,
    source_path: Path | str | None = None,
) -> str:
    config.validate()
    if not isinstance(candidate_id, str) or not candidate_id.strip():
        raise ValueError("candidate_id must be non-empty")
    source = Path(source_path or BASE_SOURCE_PATH).resolve()
    original = _base_source(source)
    if "CG_SEAT_CONDITIONED_PARAMETERS" in original:
        raise ValueError("source already contains seat-conditioned overlay")
    return original.rstrip() + "\n" + _parameter_patch(config, candidate_id)


def materialize_seat_conditioned_package(
    *,
    source_package: Path | str,
    self_owned_deck_package: Path | str,
    output_package: Path | str,
    config: SeatConditionedConfig,
    candidate_id: str,
) -> dict[str, object]:
    """Render one seat-conditioned policy and bind it to a self-owned deck."""

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

    rendered = render_seat_conditioned_source(
        config, candidate_id=candidate_id, source_path=source_main
    )
    patched = _patch_root_deck_constant(rendered, card_ids)
    if patched == rendered:
        raise SelfOwnedCgPackageV1Error("seat-conditioned policy was not deck-bound")

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
        "schema_version": "cg-p1-seat-conditioned-renderer-v1",
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
    _write_exclusive(target / "cg_seat_conditioned_manifest.json", _canonical_json(sidecar) + b"\n")
    return verify_self_owned_cg_package_v1(target)


__all__ = [
    "BASE_SOURCE_PATH",
    "BASE_SOURCE_SHA256",
    "PARAMETER_BOUNDS",
    "SCHEMA",
    "SeatConditionedConfig",
    "candidate_id_for_config",
    "materialize_seat_conditioned_package",
    "render_seat_conditioned_source",
]
