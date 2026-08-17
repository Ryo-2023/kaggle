"""Research-only four-block confirmation materializer for a sealed bridge.

The parent 96-game policy-fixed bridge remains immutable.  This module only
re-verifies that bridge and materializes four disjoint seed blocks with the
same candidate/control identities and META_TRAIN strata.  It does not run an
evaluator or grant any execution, training, promotion, or submission
authority.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
from typing import Mapping

from mage_ptcg.knowledge import load_pack
from mage_ptcg.meta_specialist.opponent_pool_v1 import (
    default_pool_root_v1,
    load_opponent_pool_v1,
)
from mage_ptcg.meta_specialist.outcome_only_policy_fixed_bridge_v1 import (
    OutcomeOnlyPolicyFixedBridgeError,
    _game as _build_game,
    _inside as _bridge_inside,
    _sha256 as _bridge_sha256_file,
    _validated_action_deltas,
    verify_policy_fixed_short_bridge_v1,
)


SCHEMA_V1 = "meta-specialist-outcome-only-policy-fixed-confirmation-v1"
PHASE_V1 = "POLICY_FIXED_CONFIRMATION"
BLOCK_COUNT_V1 = 4
BLOCK_QUOTA_V1 = 96
_AUTHORITY_FALSE = {
    "training_authority": False,
    "promotion_authority": False,
    "submission_authority": False,
    "external_execution_authority": False,
    "longrun_authority": False,
}
_SHA_HEX = frozenset("0123456789abcdef")
_MANIFEST_KEYS = frozenset(
    {
        "schema_version",
        "phase",
        "parent_bridge_path",
        "parent_bridge_file_sha256",
        "parent_bridge_sha256",
        "candidate_id",
        "control_id",
        "candidate_policy_sha256",
        "control_policy_sha256",
        "root_policy_sha256",
        "deck_path",
        "deck_sha256",
        "pool_manifest_path",
        "pool_manifest_sha256",
        "broad_config_path",
        "broad_config_sha256",
        "evaluator_sha256",
        "schedule_path",
        "schedule_file_sha256",
        "schedule_sha256",
        "runner_ref",
        "candidate_config",
        "control_config",
        "train_ids",
        "heldout_ids",
        "zero_quota_ids",
        "heldout_exposure",
        "seed_base",
        "seed_stride",
        "block_count",
        "block_quota",
        "blocks",
        "slots",
        "authority",
        "research_only",
        "execution_allowed",
        "ready_for_evaluation",
        "confirmation_sha256",
    }
)


class OutcomeOnlyPolicyFixedConfirmationError(ValueError):
    """Raised when a four-block confirmation cannot be verified safely."""


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise OutcomeOnlyPolicyFixedConfirmationError(f"value is not canonical JSON: {exc}") from exc


def _sha(value: object, field: str) -> str:
    if type(value) is not str or len(value) != 64 or any(char not in _SHA_HEX for char in value):
        raise OutcomeOnlyPolicyFixedConfirmationError(f"{field} must be a lowercase SHA-256")
    return value


def _inside(root: Path, value: object, field: str) -> Path:
    if not isinstance(value, (str, Path)) or not str(value):
        raise OutcomeOnlyPolicyFixedConfirmationError(f"{field} must be a non-empty path")
    try:
        path = _bridge_inside(root, str(value), field)
    except (OutcomeOnlyPolicyFixedBridgeError, OSError, ValueError) as exc:
        raise OutcomeOnlyPolicyFixedConfirmationError(str(exc)) from exc
    return path


def _confirmation_sha(manifest: Mapping[str, object]) -> str:
    body = {key: value for key, value in manifest.items() if key != "confirmation_sha256"}
    return hashlib.sha256((SCHEMA_V1 + "\0").encode("ascii") + _canonical(body)).hexdigest()


def _make_slots(parent_slots: list[Mapping[str, object]], *, seed_base: int, block_index: int) -> list[dict[str, object]]:
    if len(parent_slots) != BLOCK_QUOTA_V1:
        raise OutcomeOnlyPolicyFixedConfirmationError("parent bridge must contain exactly 96 slots")
    result: list[dict[str, object]] = []
    for local_index, parent_slot in enumerate(parent_slots):
        if type(parent_slot) is not dict:
            raise OutcomeOnlyPolicyFixedConfirmationError("parent bridge slot is malformed")
        if parent_slot.get("split") != "META_TRAIN" or int(parent_slot.get("seat", -1)) not in (0, 1):
            raise OutcomeOnlyPolicyFixedConfirmationError("parent bridge contains heldout or malformed slot")
        row = dict(parent_slot)
        row.update(
            {
                "block_index": block_index,
                "block_slot_index": local_index,
                "slot_index": block_index * BLOCK_QUOTA_V1 + local_index,
                "seed": seed_base + block_index * BLOCK_QUOTA_V1 + local_index,
                "stratum_key": f"block-{block_index:02d}:{parent_slot['stratum_key']}",
                "heldout_exposure": 0,
                "split": "META_TRAIN",
            }
        )
        result.append(row)
    if len({row["seed"] for row in result}) != BLOCK_QUOTA_V1:
        raise OutcomeOnlyPolicyFixedConfirmationError("confirmation block seeds are not unique")
    if sum(int(row["seat"] == 0) for row in result) != 48 or sum(int(row["seat"] == 1) for row in result) != 48:
        raise OutcomeOnlyPolicyFixedConfirmationError("confirmation block seat strata are unbalanced")
    return result


def _block_descriptor(block_index: int, slots: list[Mapping[str, object]]) -> dict[str, object]:
    seeds = [int(slot["seed"]) for slot in slots]
    return {
        "block_index": block_index,
        "slot_count": len(slots),
        "seed_start": min(seeds),
        "seed_end": max(seeds),
        "seat_counts": {
            "0": sum(int(slot["seat"] == 0) for slot in slots),
            "1": sum(int(slot["seat"] == 1) for slot in slots),
        },
        "slot_indices": [int(slot["slot_index"]) for slot in slots],
    }


def _candidate_runtime_config(root: Path, config: Mapping[str, object]) -> tuple[Path | None, dict[str, float]]:
    if type(config) is not dict:
        raise OutcomeOnlyPolicyFixedConfirmationError("parent candidate config is malformed")
    try:
        deltas = _validated_action_deltas(config.get("action_deltas"))
    except (TypeError, ValueError) as exc:
        raise OutcomeOnlyPolicyFixedConfirmationError(f"parent action config is not bounded/legal: {exc}") from exc
    raw_pack = config.get("pack_path")
    if raw_pack is None:
        pack_path = None
    else:
        pack_path = _inside(root, raw_pack, "parent KnowledgePack")
        try:
            load_pack(pack_path)
        except (OSError, TypeError, ValueError) as exc:
            raise OutcomeOnlyPolicyFixedConfirmationError("parent KnowledgePack cannot be loaded") from exc
    return pack_path, deltas


def build_policy_fixed_confirmation_v1(
    *,
    repo_root: Path | str,
    parent_bridge_path: Path | str,
    block_count: int = BLOCK_COUNT_V1,
) -> dict[str, object]:
    root = Path(repo_root).resolve()
    parent_file = _inside(root, parent_bridge_path, "parent bridge")
    try:
        parent = json.loads(parent_file.read_text(encoding="utf-8"))
        if type(parent) is not dict:
            raise ValueError("parent bridge must be an object")
        verify_policy_fixed_short_bridge_v1(parent, repo_root=root)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError, OutcomeOnlyPolicyFixedBridgeError) as exc:
        raise OutcomeOnlyPolicyFixedConfirmationError(f"parent bridge verification failed: {exc}") from exc
    if type(block_count) is not int or block_count != BLOCK_COUNT_V1:
        raise OutcomeOnlyPolicyFixedConfirmationError("confirmation requires exactly four blocks")
    parent_slots = parent.get("slots")
    if type(parent_slots) is not list or len(parent_slots) != BLOCK_QUOTA_V1:
        raise OutcomeOnlyPolicyFixedConfirmationError("parent bridge slot count is not 96")
    if parent.get("schedule_summary", {}).get("heldout_exposure") != 0:
        raise OutcomeOnlyPolicyFixedConfirmationError("parent bridge has heldout exposure")
    parent_seed_values = [int(slot["seed"]) for slot in parent_slots]
    if len(set(parent_seed_values)) != BLOCK_QUOTA_V1:
        raise OutcomeOnlyPolicyFixedConfirmationError("parent bridge seed universe is not unique")
    seed_base = max(parent_seed_values) + 1
    blocks: list[dict[str, object]] = []
    slots: list[dict[str, object]] = []
    for block_index in range(BLOCK_COUNT_V1):
        block_slots = _make_slots(parent_slots, seed_base=seed_base, block_index=block_index)
        blocks.append(_block_descriptor(block_index, block_slots))
        slots.extend(block_slots)
    if len({int(slot["seed"]) for slot in slots}) != BLOCK_COUNT_V1 * BLOCK_QUOTA_V1:
        raise OutcomeOnlyPolicyFixedConfirmationError("confirmation seed universe overlaps")
    authority = parent.get("authority")
    if authority != _AUTHORITY_FALSE:
        raise OutcomeOnlyPolicyFixedConfirmationError("parent bridge authority is not false")
    manifest: dict[str, object] = {
        "schema_version": SCHEMA_V1,
        "phase": PHASE_V1,
        "parent_bridge_path": str(parent_file.relative_to(root)),
        "parent_bridge_file_sha256": _bridge_sha256_file(parent_file),
        "parent_bridge_sha256": _sha(parent["bridge_sha256"], "parent_bridge_sha256"),
        "candidate_id": parent["candidate_id"],
        "control_id": parent["control_id"],
        "candidate_policy_sha256": parent["candidate_policy_sha256"],
        "control_policy_sha256": parent["control_policy_sha256"],
        "root_policy_sha256": parent["root_policy_sha256"],
        "deck_path": parent["deck_path"],
        "deck_sha256": parent["deck_sha256"],
        "pool_manifest_path": parent["pool_manifest_path"],
        "pool_manifest_sha256": parent["pool_manifest_sha256"],
        "broad_config_path": parent["broad_config_path"],
        "broad_config_sha256": parent["broad_config_sha256"],
        "evaluator_sha256": parent["evaluator_sha256"],
        "schedule_path": parent["schedule_path"],
        "schedule_file_sha256": parent["schedule_file_sha256"],
        "schedule_sha256": parent["schedule_sha256"],
        "runner_ref": parent["runner_ref"],
        "candidate_config": deepcopy(parent["candidate_config"]),
        "control_config": deepcopy(parent["control_config"]),
        "train_ids": list(parent["train_ids"]),
        "heldout_ids": list(parent["heldout_ids"]),
        "zero_quota_ids": list(parent["zero_quota_ids"]),
        "heldout_exposure": 0,
        "seed_base": seed_base,
        "seed_stride": BLOCK_QUOTA_V1,
        "block_count": BLOCK_COUNT_V1,
        "block_quota": BLOCK_QUOTA_V1,
        "blocks": blocks,
        "slots": slots,
        "authority": dict(_AUTHORITY_FALSE),
        "research_only": True,
        "execution_allowed": False,
        "ready_for_evaluation": True,
    }
    manifest["confirmation_sha256"] = _confirmation_sha(manifest)
    pool = load_opponent_pool_v1(default_pool_root_v1(root))
    deck_path = _inside(root, manifest["deck_path"], "confirmation deck")
    candidate_pack_path, candidate_deltas = _candidate_runtime_config(root, manifest["candidate_config"])
    control_pack_path, control_deltas = _candidate_runtime_config(root, manifest["control_config"])
    game_manifest = {**manifest, "bridge_sha256": manifest["parent_bridge_sha256"]}
    candidate_games = []
    control_games = []
    for slot in slots:
        block_index = int(slot["block_index"])
        candidate_game = _build_game(
            arm="candidate",
            arm_id=str(manifest["candidate_id"]),
            policy_sha=str(manifest["candidate_policy_sha256"]),
            root=root,
            deck_path=deck_path,
            pool=pool,
            slot=slot,
            manifest=game_manifest,
            config=manifest["candidate_config"],
            pack_path=candidate_pack_path,
            action_deltas=candidate_deltas,
        )
        control_game = _build_game(
            arm="control",
            arm_id=str(manifest["control_id"]),
            policy_sha=str(manifest["control_policy_sha256"]),
            root=root,
            deck_path=deck_path,
            pool=pool,
            slot=slot,
            manifest=game_manifest,
            config=manifest["control_config"],
            pack_path=control_pack_path,
            action_deltas=control_deltas,
        )
        candidate_metadata = dict(candidate_game.metadata)
        control_metadata = dict(control_game.metadata)
        for metadata in (candidate_metadata, control_metadata):
            metadata.update(
                {
                    "schema_version": SCHEMA_V1,
                    "confirmation_sha256": manifest["confirmation_sha256"],
                    "parent_bridge_sha256": manifest["parent_bridge_sha256"],
                    "block_index": block_index,
                    "heldout_exposure": 0,
                }
            )
        candidate_games.append(replace(candidate_game, game_id=f"candidate-{SCHEMA_V1}-block{block_index:02d}-{slot['stratum_key']}", block_id=f"{SCHEMA_V1}-candidate-block{block_index:02d}", metadata=candidate_metadata))
        control_games.append(replace(control_game, game_id=f"control-{SCHEMA_V1}-block{block_index:02d}-{slot['stratum_key']}", block_id=f"{SCHEMA_V1}-control-block{block_index:02d}", metadata=control_metadata))
    return {"manifest": manifest, "candidate_games": tuple(candidate_games), "control_games": tuple(control_games)}


def verify_policy_fixed_confirmation_v1(
    manifest: Mapping[str, object], *, repo_root: Path | str
) -> dict[str, object]:
    if type(manifest) is not dict or set(manifest) != _MANIFEST_KEYS:
        raise OutcomeOnlyPolicyFixedConfirmationError("confirmation manifest schema is not closed")
    if manifest.get("schema_version") != SCHEMA_V1 or manifest.get("phase") != PHASE_V1:
        raise OutcomeOnlyPolicyFixedConfirmationError("confirmation schema/phase mismatch")
    if manifest.get("authority") != _AUTHORITY_FALSE or manifest.get("research_only") is not True or manifest.get("execution_allowed") is not False:
        raise OutcomeOnlyPolicyFixedConfirmationError("confirmation authority is not research-only")
    if manifest.get("ready_for_evaluation") is not True or manifest.get("heldout_exposure") != 0:
        raise OutcomeOnlyPolicyFixedConfirmationError("confirmation gate is not closed")
    if manifest.get("confirmation_sha256") != _confirmation_sha(manifest):
        raise OutcomeOnlyPolicyFixedConfirmationError("confirmation semantic SHA mismatch")
    root = Path(repo_root).resolve()
    parent_file = _inside(root, manifest["parent_bridge_path"], "parent bridge")
    if _bridge_sha256_file(parent_file) != manifest["parent_bridge_file_sha256"]:
        raise OutcomeOnlyPolicyFixedConfirmationError("parent bridge file SHA mismatch")
    try:
        parent = json.loads(parent_file.read_text(encoding="utf-8"))
        verify_policy_fixed_short_bridge_v1(parent, repo_root=root)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError, OutcomeOnlyPolicyFixedBridgeError) as exc:
        raise OutcomeOnlyPolicyFixedConfirmationError(f"parent bridge re-verification failed: {exc}") from exc
    if manifest["parent_bridge_sha256"] != parent["bridge_sha256"]:
        raise OutcomeOnlyPolicyFixedConfirmationError("parent bridge semantic identity mismatch")
    inherited_fields = (
        "candidate_id", "control_id", "candidate_policy_sha256", "control_policy_sha256", "root_policy_sha256",
        "deck_path", "deck_sha256", "pool_manifest_path", "pool_manifest_sha256", "broad_config_path",
        "broad_config_sha256", "evaluator_sha256", "schedule_path", "schedule_file_sha256", "schedule_sha256",
        "runner_ref", "candidate_config", "control_config",
        "train_ids", "heldout_ids", "zero_quota_ids",
    )
    for field in inherited_fields:
        if manifest[field] != parent[field]:
            raise OutcomeOnlyPolicyFixedConfirmationError(f"confirmation inherited identity mismatch: {field}")
    if manifest["block_count"] != BLOCK_COUNT_V1 or manifest["block_quota"] != BLOCK_QUOTA_V1 or manifest["seed_stride"] != BLOCK_QUOTA_V1:
        raise OutcomeOnlyPolicyFixedConfirmationError("confirmation block contract mismatch")
    parent_slots = parent["slots"]
    if type(parent_slots) is not list or len(parent_slots) != BLOCK_QUOTA_V1:
        raise OutcomeOnlyPolicyFixedConfirmationError("parent bridge slots are not 96")
    parent_seeds = {int(slot["seed"]) for slot in parent_slots}
    if manifest["seed_base"] != max(parent_seeds) + 1:
        raise OutcomeOnlyPolicyFixedConfirmationError("confirmation seed base is not disjoint from parent")
    expected_slots: list[dict[str, object]] = []
    expected_blocks: list[dict[str, object]] = []
    for block_index in range(BLOCK_COUNT_V1):
        block = _make_slots(parent_slots, seed_base=int(manifest["seed_base"]), block_index=block_index)
        expected_slots.extend(block)
        expected_blocks.append(_block_descriptor(block_index, block))
    if manifest["blocks"] != expected_blocks or manifest["slots"] != expected_slots:
        raise OutcomeOnlyPolicyFixedConfirmationError("confirmation slots/blocks differ from parent strata")
    if parent_seeds.intersection({int(slot["seed"]) for slot in expected_slots}):
        raise OutcomeOnlyPolicyFixedConfirmationError("confirmation seeds overlap parent bridge")
    if len({int(slot["seed"]) for slot in expected_slots}) != BLOCK_COUNT_V1 * BLOCK_QUOTA_V1:
        raise OutcomeOnlyPolicyFixedConfirmationError("confirmation seed universe is not unique")
    return dict(manifest)


__all__ = [
    "OutcomeOnlyPolicyFixedConfirmationError",
    "SCHEMA_V1",
    "build_policy_fixed_confirmation_v1",
    "verify_policy_fixed_confirmation_v1",
]
