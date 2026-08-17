"""Same-observation, public-only shadow telemetry for cg policy research."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import copy
import hashlib
import json
from pathlib import Path
import shutil
from typing import Any

from mage_ptcg.meta_specialist.cg_public_telemetry_v1 import (
    _assert_public_payload,
    append_public_telemetry_record_v1,
    build_public_telemetry_record_v1,
)


ROOT = Path(__file__).resolve().parents[3]
SCHEMA = "cg-p1-shadow-telemetry-v1"


def _canonical_hash(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _public_state(public: Mapping[str, object]) -> dict[str, object]:
    state = dict(public)
    for key in ("action", "candidate_id", "game_id"):
        state.pop(key, None)
    return state


def _legal_semantics(public: Mapping[str, object]) -> list[dict[str, object]]:
    attestations = public.get("public_candidate_attestations")
    if isinstance(attestations, list):
        result = []
        for item in attestations:
            if isinstance(item, Mapping):
                result.append(
                    {
                        "candidate_public_id": str(item.get("candidate_public_id", "")),
                        "semantic_operation": str(item.get("semantic_operation", "UNKNOWN")),
                    }
                )
        if result:
            return sorted(result, key=lambda value: (value["semantic_operation"], value["candidate_public_id"]))
    options = public.get("options")
    if isinstance(options, list):
        return [
            {
                "option_index": int(item.get("option_index", index)),
                "semantic_operation": str(item.get("type_name", "UNKNOWN")),
            }
            for index, item in enumerate(options)
            if isinstance(item, Mapping)
        ]
    return []


def build_shadow_record_v1(
    observation: Mapping[str, object],
    *,
    behavior_action: Sequence[int],
    shadow_action: Sequence[int],
    seat: int,
    game_id: str,
    behavior_policy_id: str,
    shadow_policy_id: str,
    decision_index: int,
    first_divergence_index: int | None,
    behavior_scores: Mapping[str, int] | None = None,
    shadow_scores: Mapping[str, int] | None = None,
    shadow_fault: str | None = None,
    active_rule_flags: Sequence[str] = (),
) -> dict[str, object]:
    """Build a safe record from one immutable observation and two action calls."""

    public = build_public_telemetry_record_v1(
        observation,
        list(behavior_action),
        seat=seat,
        game_id=game_id,
        candidate_id=behavior_policy_id,
    )
    legal = _legal_semantics(public)
    behavior = [int(value) for value in behavior_action]
    shadow = [int(value) for value in shadow_action]
    record: dict[str, object] = {
        "schema_version": SCHEMA,
        "record_type": "shadow_decision",
        "same_observation": True,
        "game_id": game_id,
        "seat": seat,
        "decision_index": int(decision_index),
        "behavior_policy_id": behavior_policy_id,
        "shadow_policy_id": shadow_policy_id,
        "behavior_action": behavior,
        "shadow_action": shadow,
        "actions_differ": behavior != shadow,
        "first_divergence_index": first_divergence_index,
        "public_state_digest": _canonical_hash(_public_state(public)),
        "legal_action_digest": _canonical_hash(legal),
        "legal_semantic_actions": legal,
        "score_breakdown": {
            "behavior": {str(key): int(value) for key, value in (behavior_scores or {}).items()},
            "shadow": {str(key): int(value) for key, value in (shadow_scores or {}).items()},
        },
        "active_rule_flags": sorted({str(value) for value in active_rule_flags}),
        "shadow_fault": shadow_fault,
        "terminal_outcome": public.get("observed_result"),
        "public": public,
    }
    _assert_public_payload(record)
    return record


def _wrapper_source(behavior_policy_id: str, shadow_policy_id: str) -> str:
    behavior_id = json.dumps(behavior_policy_id, ensure_ascii=False)
    shadow_id = json.dumps(shadow_policy_id, ensure_ascii=False)
    return f'''"""Research-only same-observation shadow wrapper."""

from __future__ import annotations

import copy
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cg_behavior as _behavior
import cg_shadow as _shadow
from mage_ptcg.meta_specialist.cg_p1_shadow_telemetry_v1 import (
    append_public_telemetry_record_v1,
    build_shadow_record_v1,
)


_BEHAVIOR_POLICY_ID = {behavior_id}
_SHADOW_POLICY_ID = {shadow_id}
_CURRENT_GAME_ID = None
_DECISION_INDEX = 0
_FIRST_DIVERGENCE_INDEX = None


def _reset_if_new_game(game_id):
    global _CURRENT_GAME_ID, _DECISION_INDEX, _FIRST_DIVERGENCE_INDEX
    if game_id != _CURRENT_GAME_ID:
        _CURRENT_GAME_ID = game_id
        _DECISION_INDEX = 0
        _FIRST_DIVERGENCE_INDEX = None


def _score_breakdown(module, observation):
    try:
        if observation.get("select") is None:
            return {{}}
        converted = module.to_observation_class(observation)
        options = list(converted.select.option or [])
        scorer = getattr(module, "_score", None)
        if scorer is None:
            return {{}}
        return {{f"option_{{index}}": int(scorer(converted, option)) for index, option in enumerate(options)}}
    except Exception:
        return {{}}


def _safe_fault_record(game_id, seat, decision_index, behavior_action, shadow_action, fault):
    return {{
        "schema_version": "cg-p1-shadow-telemetry-v1",
        "record_type": "shadow_fault",
        "same_observation": True,
        "game_id": game_id,
        "seat": seat,
        "decision_index": decision_index,
        "behavior_policy_id": _BEHAVIOR_POLICY_ID,
        "shadow_policy_id": _SHADOW_POLICY_ID,
        "behavior_action": list(behavior_action),
        "shadow_action": list(shadow_action),
        "actions_differ": list(behavior_action) != list(shadow_action),
        "first_divergence_index": _FIRST_DIVERGENCE_INDEX,
        "public_state_digest": None,
        "legal_action_digest": None,
        "legal_semantic_actions": [],
        "score_breakdown": {{"behavior": {{}}, "shadow": {{}}}},
        "active_rule_flags": [],
        "shadow_fault": fault,
        "terminal_outcome": None,
    }}


def agent(observation):
    global _DECISION_INDEX, _FIRST_DIVERGENCE_INDEX
    game_id = os.environ.get("CG_P1_SHADOW_TELEMETRY_GAME_ID", "unknown")
    seat = int(os.environ.get("CG_P1_SHADOW_TELEMETRY_SEAT", "0"))
    _reset_if_new_game(game_id)
    behavior_action = _behavior.agent(observation)
    shadow_action = []
    shadow_fault = None
    try:
        # A deep copy prevents a stateful shadow from mutating the behavior
        # call's input while preserving the exact public observation values.
        shadow_action = _shadow.agent(copy.deepcopy(observation))
    except Exception as exc:
        shadow_fault = f"{{type(exc).__name__}}: {{exc}}"
    decision_index = _DECISION_INDEX
    if list(behavior_action) != list(shadow_action) and _FIRST_DIVERGENCE_INDEX is None:
        _FIRST_DIVERGENCE_INDEX = decision_index
    path = os.environ.get("CG_P1_SHADOW_TELEMETRY_PATH")
    if path:
        try:
            record = build_shadow_record_v1(
                observation,
                behavior_action=behavior_action,
                shadow_action=shadow_action,
                seat=seat,
                game_id=game_id,
                behavior_policy_id=_BEHAVIOR_POLICY_ID,
                shadow_policy_id=_SHADOW_POLICY_ID,
                decision_index=decision_index,
                first_divergence_index=_FIRST_DIVERGENCE_INDEX,
                behavior_scores=_score_breakdown(_behavior, observation),
                shadow_scores=_score_breakdown(_shadow, observation),
                shadow_fault=shadow_fault,
            )
        except Exception as exc:
            record = _safe_fault_record(
                game_id,
                seat,
                decision_index,
                behavior_action,
                shadow_action,
                shadow_fault or f"public_projection: {{type(exc).__name__}}: {{exc}}",
            )
        append_public_telemetry_record_v1(path, record)
    _DECISION_INDEX += 1
    return behavior_action
'''


def materialize_shadow_package(
    *,
    behavior_package: Path | str,
    shadow_package: Path | str,
    output_package: Path | str,
    behavior_policy_id: str,
    shadow_policy_id: str,
) -> dict[str, object]:
    """Materialize a behavior package with a non-executed shadow policy."""

    behavior = Path(behavior_package).resolve()
    shadow = Path(shadow_package).resolve()
    target = Path(output_package).resolve()
    if not (behavior.is_dir() and (behavior / "main.py").is_file() and (behavior / "deck.csv").is_file()):
        raise ValueError(f"behavior package is incomplete: {behavior}")
    if not (shadow.is_dir() and (shadow / "main.py").is_file() and (shadow / "deck.csv").is_file()):
        raise ValueError(f"shadow package is incomplete: {shadow}")
    if (behavior / "deck.csv").read_bytes() != (shadow / "deck.csv").read_bytes():
        raise ValueError("behavior and shadow decks must match")
    if target.exists() or target.is_symlink():
        raise FileExistsError(f"shadow package output exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(behavior, target)
    (target / "main.py").rename(target / "cg_behavior.py")
    shutil.copy2(shadow / "main.py", target / "cg_shadow.py")
    (target / "main.py").write_text(
        _wrapper_source(behavior_policy_id, shadow_policy_id),
        encoding="utf-8",
    )
    manifest = {
        "schema_version": SCHEMA,
        "behavior_policy_id": behavior_policy_id,
        "shadow_policy_id": shadow_policy_id,
        "behavior_policy_sha256": hashlib.sha256((target / "cg_behavior.py").read_bytes()).hexdigest(),
        "shadow_policy_sha256": hashlib.sha256((target / "cg_shadow.py").read_bytes()).hexdigest(),
        "deck_sha256": hashlib.sha256((target / "deck.csv").read_bytes()).hexdigest(),
        "same_observation": True,
        "executed_actions": ["behavior_only"],
        "research_only": True,
    }
    (target / "cg_p1_shadow_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest
