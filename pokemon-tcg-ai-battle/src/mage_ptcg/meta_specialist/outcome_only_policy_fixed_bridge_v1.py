"""Research-only policy-fixed bridge for outcome-only hard-negative schedules.

The bridge turns a verified outcome schedule into paired ``EvaluationGameV1``
cells for a Rule v0 control and one bounded KnowledgePack/action overlay.  It
never executes a game and never changes the production agent or evaluator.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from mage_ptcg.knowledge import KnowledgePack, load_pack
from mage_ptcg.meta_specialist.outcome_only_hard_negative_v1 import (
    OutcomeOnlyHardNegativeError,
    verify_outcome_only_hard_negative_schedule_v1,
)
from scripts.parallel_cabt_evaluator_v1 import EvaluationGameV1, evaluator_implementation_sha256_v1
from scripts.run_performance_first_arena_v1 import root_policy_sha256
from scripts.run_rule_v0_knowledge_pool_screen_v1 import (
    DEFAULT_POOL_MANIFEST,
    RUNNER_REF_V1,
    _candidate_policy_sha,
    _sha256,
    _validated_action_deltas,
    build_candidate_manifest,
    pack_bytes_sha256,
)
from mage_ptcg.meta_specialist.opponent_pool_v1 import (
    default_pool_root_v1,
    load_opponent_pool_v1,
    resolve_opponent_v1,
)


SCHEMA_V1 = "meta-specialist-outcome-only-policy-fixed-short-bridge-v1"
PHASE_V1 = "POLICY_FIXED_SHORT"
_SHA_HEX = frozenset("0123456789abcdef")
_MANIFEST_KEYS = frozenset(
    {
        "schema_version",
        "phase",
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
        "slots",
        "schedule_summary",
        "outcome_subject_identity",
        "authority",
        "research_only",
        "execution_allowed",
        "ready_for_evaluation",
        "bridge_sha256",
    }
)
_AUTHORITY_FALSE = {
    "training_authority": False,
    "promotion_authority": False,
    "submission_authority": False,
    "external_execution_authority": False,
    "longrun_authority": False,
}


class OutcomeOnlyPolicyFixedBridgeError(ValueError):
    """Raised when a policy-fixed bridge cannot be closed safely."""


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise OutcomeOnlyPolicyFixedBridgeError(f"value is not canonical JSON: {exc}") from exc


def _sha(value: object, field: str) -> str:
    if type(value) is not str or len(value) != 64 or any(char not in _SHA_HEX for char in value):
        raise OutcomeOnlyPolicyFixedBridgeError(f"{field} must be a lowercase SHA-256")
    return value


def _text(value: object, field: str) -> str:
    if type(value) is not str or not value:
        raise OutcomeOnlyPolicyFixedBridgeError(f"{field} must be a non-empty string")
    return value


def _bridge_sha(payload: Mapping[str, object]) -> str:
    body = {key: value for key, value in payload.items() if key != "bridge_sha256"}
    return hashlib.sha256((SCHEMA_V1 + "\0").encode("ascii") + _canonical(body)).hexdigest()


def _inside(root: Path, value: str | Path, field: str) -> Path:
    candidate = Path(value)
    path = (root / candidate).resolve() if not candidate.is_absolute() else candidate.resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise OutcomeOnlyPolicyFixedBridgeError(f"{field} escapes repo_root") from exc
    if not path.is_file():
        raise OutcomeOnlyPolicyFixedBridgeError(f"{field} is not a file: {path}")
    return path


def _pack_and_deltas(
    *, pack_path: Path | str | None, action_deltas: Mapping[str, object] | None
) -> tuple[KnowledgePack | None, dict[str, float], str | None]:
    try:
        deltas = _validated_action_deltas(action_deltas)
    except (TypeError, ValueError) as exc:
        raise OutcomeOnlyPolicyFixedBridgeError(f"action deltas are not bounded/legal: {exc}") from exc
    if pack_path is not None and deltas:
        raise OutcomeOnlyPolicyFixedBridgeError("pack and action_deltas cannot be combined")
    pack = None
    pack_sha = None
    if pack_path is not None:
        try:
            pack = load_pack(pack_path)
        except (OSError, ValueError, TypeError) as exc:
            raise OutcomeOnlyPolicyFixedBridgeError(f"KnowledgePack cannot be loaded: {pack_path}") from exc
        pack_sha = pack_bytes_sha256(pack)
    return pack, deltas, pack_sha


def _load_schedule_sources(root: Path, schedule: Mapping[str, object]) -> dict[str, Path]:
    sources = schedule.get("sources")
    if type(sources) is not dict:
        raise OutcomeOnlyPolicyFixedBridgeError("schedule sources are malformed")
    resolved: dict[str, Path] = {}
    for role, binding in sources.items():
        if type(binding) is not dict or type(binding.get("path")) is not str:
            raise OutcomeOnlyPolicyFixedBridgeError("schedule source binding is malformed")
        resolved[role] = _inside(root, str(binding["path"]), f"schedule source:{role}")
    return resolved


def _slot_rows(schedule: Mapping[str, object], *, base_seed: int) -> tuple[dict[str, object], ...]:
    entries = schedule.get("entries")
    if type(entries) is not list:
        raise OutcomeOnlyPolicyFixedBridgeError("schedule entries are malformed")
    slots: list[dict[str, object]] = []
    seat_count: dict[str, dict[str, int]] = {}
    ordinal = 0
    for entry in sorted(entries, key=lambda item: str(item.get("opponent_id"))):
        if type(entry) is not dict:
            raise OutcomeOnlyPolicyFixedBridgeError("schedule entry is malformed")
        opponent_id = _text(entry.get("opponent_id"), "schedule entry opponent_id")
        if entry.get("split") != "META_TRAIN":
            raise OutcomeOnlyPolicyFixedBridgeError("heldout entry reached policy-fixed bridge")
        quota = entry.get("quota")
        weight = entry.get("weight")
        if type(quota) is not int or quota < 0 or type(weight) not in (int, float):
            raise OutcomeOnlyPolicyFixedBridgeError("schedule entry quota/weight is malformed")
        seat_count[opponent_id] = {"0": 0, "1": 0}
        for local_index in range(quota):
            seat = ordinal % 2
            repetition = seat_count[opponent_id][str(seat)]
            seat_count[opponent_id][str(seat)] += 1
            slots.append(
                {
                    "slot_index": ordinal,
                    "stratum_key": f"{opponent_id}-slot-{local_index:03d}",
                    "opponent_id": opponent_id,
                    "seat": seat,
                    "repetition": repetition,
                    "seed": base_seed + ordinal,
                    "schedule_weight": float(weight),
                    "schedule_quota": quota,
                    "split": "META_TRAIN",
                }
            )
            ordinal += 1
    if ordinal != int(schedule.get("quota", -1)):
        raise OutcomeOnlyPolicyFixedBridgeError("schedule slot count differs from quota")
    if sum(int(row["seat"] == 0) for row in slots) != sum(int(row["seat"] == 1) for row in slots):
        raise OutcomeOnlyPolicyFixedBridgeError("policy-fixed bridge seat strata are unbalanced")
    return tuple(slots)


def _game(
    *, arm: str, arm_id: str, policy_sha: str, root: Path, deck_path: Path,
    pool: Mapping[str, object], slot: Mapping[str, object], manifest: Mapping[str, object],
    config: Mapping[str, object], pack_path: Path | None, action_deltas: Mapping[str, float],
) -> EvaluationGameV1:
    opponent_id = str(slot["opponent_id"])
    opponent = resolve_opponent_v1(pool, opponent_id, subject_deck_csv_path=str(deck_path))
    if opponent.usage_boundary != "local_eval_only" or str(opponent.source).lower() == "synthetic":
        raise OutcomeOnlyPolicyFixedBridgeError("bridge requires permission-safe non-synthetic local evaluation opponents")
    opponent_policy_sha = _sha256(opponent.policy_path)
    opponent_deck_sha = _sha256(opponent.deck_csv_path)
    metadata = {
        "schema_version": SCHEMA_V1,
        "bridge_sha256": manifest["bridge_sha256"],
        "schedule_sha256": manifest["schedule_sha256"],
        "schedule_file_sha256": manifest["schedule_file_sha256"],
        "candidate_id": arm_id,
        "root_policy_sha256": manifest["root_policy_sha256"],
        "candidate_policy_sha256": policy_sha,
        "candidate_config_sha256": config["config_sha256"],
        "pack_path": str(pack_path) if pack_path else None,
        "action_deltas": dict(action_deltas),
        "candidate_env": {},
        "candidate_min_score_gain": 0.0,
        "pool_manifest_sha256": manifest["pool_manifest_sha256"],
        "broad_config_sha256": manifest["broad_config_sha256"],
        "evaluator_sha256": manifest["evaluator_sha256"],
        "opponent_usage_boundary": opponent.usage_boundary,
        "opponent_source": opponent.source,
        "synthetic_opponent": False,
        "heldout_exposure": 0,
        "arm": arm,
        **dict(slot),
    }
    return EvaluationGameV1(
        game_id=f"{arm}-{SCHEMA_V1}-{slot['stratum_key']}",
        block_id=f"{SCHEMA_V1}-{arm}",
        policy_id=f"rule-v0-{arm_id}",
        policy_sha256=policy_sha,
        deck_id="root-deck-current-worktree",
        deck_sha256=_sha256(deck_path),
        opponent_id=opponent_id,
        opponent_identity={
            "policy_sha256": opponent_policy_sha,
            "deck_sha256": opponent_deck_sha,
            "usage_boundary": opponent.usage_boundary,
            "source": opponent.source,
            "meta_split": "META_TRAIN",
        },
        opponent_deck_sha256=opponent_deck_sha,
        seat=int(slot["seat"]),
        seed=int(slot["seed"]),
        max_steps=2000,
        subject_deck_path=str(deck_path),
        opponent_deck_path=str(opponent.deck_csv_path),
        policy_agent_name=f"rule-v0-{arm_id}",
        opponent_agent_name=opponent_id,
        runner_ref=RUNNER_REF_V1,
        metadata=metadata,
    )


def build_policy_fixed_short_bridge_v1(
    *,
    repo_root: Path | str,
    schedule_path: Path | str,
    subject_deck_path: Path | str,
    candidate_id: str,
    action_deltas: Mapping[str, object] | None = None,
    pack_path: Path | str | None = None,
) -> dict[str, object]:
    root = Path(repo_root).resolve()
    schedule_file = _inside(root, schedule_path, "schedule")
    try:
        schedule = verify_outcome_only_hard_negative_schedule_v1(schedule_file, root)
    except (OutcomeOnlyHardNegativeError, OSError, ValueError) as exc:
        raise OutcomeOnlyPolicyFixedBridgeError(f"schedule verification failed: {exc}") from exc
    sources = _load_schedule_sources(root, schedule)
    summary = json.loads(sources["summary"].read_text(encoding="utf-8"))
    deck_path = _inside(root, subject_deck_path, "subject deck")
    deck_sha = _sha256(deck_path)
    root_sha = root_policy_sha256()
    evaluator_sha = evaluator_implementation_sha256_v1()
    if evaluator_sha != schedule["subject_identity"]["evaluator_sha256"]:
        raise OutcomeOnlyPolicyFixedBridgeError("current evaluator SHA differs from schedule source")
    pool_manifest = _inside(root, DEFAULT_POOL_MANIFEST, "opponent pool manifest")
    pool_sha = _sha256(pool_manifest)
    if pool_sha != schedule["sources"]["pool_manifest"]["sha256"]:
        raise OutcomeOnlyPolicyFixedBridgeError("opponent pool SHA differs from schedule source")
    broad_path = sources["config"]
    broad_sha = _sha256(broad_path)
    pack_file = _inside(root, pack_path, "KnowledgePack") if pack_path is not None else None
    pack, deltas, pack_sha = _pack_and_deltas(pack_path=pack_file, action_deltas=action_deltas)
    try:
        candidate_policy_sha = _candidate_policy_sha(root_sha, pack, deltas)
    except (ValueError, TypeError) as exc:
        raise OutcomeOnlyPolicyFixedBridgeError(f"candidate policy identity failed: {exc}") from exc
    train_ids = tuple(str(item["opponent_id"]) for item in schedule["entries"])
    heldout_ids = tuple(str(item["opponent_id"]) for item in schedule["excluded_heldout"])
    zero_quota_ids = tuple(str(item["opponent_id"]) for item in schedule["entries"] if int(item["quota"]) == 0)
    base_seed = int(summary["base_seed"])
    slots = _slot_rows(schedule, base_seed=base_seed)
    pool = load_opponent_pool_v1(default_pool_root_v1(root))
    candidate_config = {
        "candidate_id": candidate_id,
        "pack_sha256": pack_sha,
        "pack_path": str(pack_file.relative_to(root)) if pack_file else None,
        "action_deltas": dict(deltas),
        "config_sha256": hashlib.sha256(_canonical({"pack_sha256": pack_sha, "action_deltas": dict(deltas), "candidate_id": candidate_id})).hexdigest(),
    }
    control_config = {
        "candidate_id": "baseline-no-pack",
        "pack_sha256": None,
        "pack_path": None,
        "action_deltas": {},
        "config_sha256": hashlib.sha256(_canonical({"pack_sha256": None, "action_deltas": {}, "candidate_id": "baseline-no-pack"})).hexdigest(),
    }
    candidate_identity = build_candidate_manifest(
        candidate_id=candidate_id,
        pack=pack,
        action_deltas=deltas,
        root_policy_sha256=root_sha,
        deck_sha256=deck_sha,
        pool_manifest_sha256=pool_sha,
        broad_config_sha256=broad_sha,
        evaluator_sha256=evaluator_sha,
        common24_ids=train_ids,
    )
    control_identity = build_candidate_manifest(
        candidate_id="baseline-no-pack",
        pack=None,
        action_deltas=None,
        root_policy_sha256=root_sha,
        deck_sha256=deck_sha,
        pool_manifest_sha256=pool_sha,
        broad_config_sha256=broad_sha,
        evaluator_sha256=evaluator_sha,
        common24_ids=train_ids,
    )
    manifest: dict[str, object] = {
        "schema_version": SCHEMA_V1,
        "phase": PHASE_V1,
        "candidate_id": _text(candidate_id, "candidate_id"),
        "control_id": "baseline-no-pack",
        "candidate_policy_sha256": candidate_policy_sha,
        "control_policy_sha256": root_sha,
        "root_policy_sha256": root_sha,
        "deck_path": str(deck_path.relative_to(root)),
        "deck_sha256": deck_sha,
        "pool_manifest_path": str(pool_manifest.relative_to(root)),
        "pool_manifest_sha256": pool_sha,
        "broad_config_path": str(broad_path.relative_to(root)),
        "broad_config_sha256": broad_sha,
        "evaluator_sha256": evaluator_sha,
        "schedule_path": str(schedule_file.relative_to(root)),
        "schedule_file_sha256": _sha256(schedule_file),
        "schedule_sha256": schedule["schedule_sha256"],
        "runner_ref": RUNNER_REF_V1,
        "candidate_config": {**candidate_config, "identity": candidate_identity},
        "control_config": {**control_config, "identity": control_identity},
        "train_ids": list(train_ids),
        "heldout_ids": list(heldout_ids),
        "zero_quota_ids": list(zero_quota_ids),
        "slots": list(slots),
        "schedule_summary": {
            "source_games": schedule["summary"]["source_games"],
            "included_games": schedule["summary"]["included_games"],
            "heldout_exposure": 0,
            "slot_count": len(slots),
            "seat_counts": {str(seat): sum(int(slot["seat"] == seat) for slot in slots) for seat in (0, 1)},
            "weight_sum": sum(float(item["weight"]) for item in schedule["entries"]),
            "quota_sum": sum(int(item["quota"]) for item in schedule["entries"]),
        },
        "outcome_subject_identity": dict(schedule["subject_identity"]),
        "authority": dict(_AUTHORITY_FALSE),
        "research_only": True,
        "execution_allowed": False,
        "ready_for_evaluation": True,
    }
    manifest["bridge_sha256"] = _bridge_sha(manifest)
    candidate_games = tuple(
        _game(
            arm="candidate",
            arm_id=candidate_id,
            policy_sha=candidate_policy_sha,
            root=root,
            deck_path=deck_path,
            pool=pool,
            slot=slot,
            manifest=manifest,
            config=candidate_config,
            pack_path=pack_file,
            action_deltas=deltas,
        )
        for slot in slots
    )
    control_games = tuple(
        _game(
            arm="control",
            arm_id="baseline-no-pack",
            policy_sha=root_sha,
            root=root,
            deck_path=deck_path,
            pool=pool,
            slot=slot,
            manifest=manifest,
            config=control_config,
            pack_path=None,
            action_deltas={},
        )
        for slot in slots
    )
    return {"manifest": manifest, "candidate_games": candidate_games, "control_games": control_games}


def verify_policy_fixed_short_bridge_v1(
    manifest: Mapping[str, object], *, repo_root: Path | str
) -> dict[str, object]:
    if type(manifest) is not dict or set(manifest) != _MANIFEST_KEYS:
        raise OutcomeOnlyPolicyFixedBridgeError("bridge manifest schema is not closed")
    if manifest.get("schema_version") != SCHEMA_V1 or manifest.get("phase") != PHASE_V1:
        raise OutcomeOnlyPolicyFixedBridgeError("bridge schema/phase mismatch")
    if manifest.get("authority") != _AUTHORITY_FALSE or manifest.get("research_only") is not True or manifest.get("execution_allowed") is not False:
        raise OutcomeOnlyPolicyFixedBridgeError("bridge authority is not research-only")
    if manifest.get("ready_for_evaluation") is not True:
        raise OutcomeOnlyPolicyFixedBridgeError("bridge is not ready_for_evaluation")
    if manifest.get("bridge_sha256") != _bridge_sha(manifest):
        raise OutcomeOnlyPolicyFixedBridgeError("bridge semantic SHA mismatch")
    root = Path(repo_root).resolve()
    schedule_file = _inside(root, str(manifest["schedule_path"]), "bridge schedule")
    schedule = verify_outcome_only_hard_negative_schedule_v1(schedule_file, root)
    if schedule["schedule_sha256"] != manifest["schedule_sha256"] or _sha256(schedule_file) != manifest["schedule_file_sha256"]:
        raise OutcomeOnlyPolicyFixedBridgeError("bridge schedule identity mismatch")
    if manifest["evaluator_sha256"] != evaluator_implementation_sha256_v1():
        raise OutcomeOnlyPolicyFixedBridgeError("bridge evaluator SHA mismatch")
    sources = _load_schedule_sources(root, schedule)
    summary = json.loads(sources["summary"].read_text(encoding="utf-8"))
    if type(summary) is not dict or type(summary.get("base_seed")) is not int:
        raise OutcomeOnlyPolicyFixedBridgeError("bridge source summary seed is malformed")
    expected_deck = _inside(root, str(manifest["deck_path"]), "bridge subject deck")
    if _sha256(expected_deck) != manifest["deck_sha256"]:
        raise OutcomeOnlyPolicyFixedBridgeError("bridge subject deck identity mismatch")
    expected_pool = _inside(root, str(manifest["pool_manifest_path"]), "bridge pool manifest")
    if expected_pool != _inside(root, DEFAULT_POOL_MANIFEST, "opponent pool manifest"):
        raise OutcomeOnlyPolicyFixedBridgeError("bridge pool manifest path mismatch")
    if _sha256(expected_pool) != manifest["pool_manifest_sha256"]:
        raise OutcomeOnlyPolicyFixedBridgeError("bridge pool manifest identity mismatch")
    expected_broad = _inside(root, str(manifest["broad_config_path"]), "bridge broad config")
    if expected_broad != sources["config"] or _sha256(expected_broad) != manifest["broad_config_sha256"]:
        raise OutcomeOnlyPolicyFixedBridgeError("bridge broad config identity mismatch")
    current_root_sha = root_policy_sha256()
    if manifest["root_policy_sha256"] != current_root_sha or manifest["control_policy_sha256"] != current_root_sha:
        raise OutcomeOnlyPolicyFixedBridgeError("bridge root/control policy identity mismatch")
    expected_train = [str(item["opponent_id"]) for item in schedule["entries"]]
    expected_heldout = [str(item["opponent_id"]) for item in schedule["excluded_heldout"]]
    expected_zero = [str(item["opponent_id"]) for item in schedule["entries"] if int(item["quota"]) == 0]
    if manifest["train_ids"] != expected_train or manifest["heldout_ids"] != expected_heldout or manifest["zero_quota_ids"] != expected_zero:
        raise OutcomeOnlyPolicyFixedBridgeError("bridge train/heldout identity mismatch")
    if manifest["outcome_subject_identity"] != schedule["subject_identity"]:
        raise OutcomeOnlyPolicyFixedBridgeError("bridge outcome subject identity mismatch")
    candidate_config = manifest["candidate_config"]
    control_config = manifest["control_config"]
    config_keys = {"candidate_id", "pack_sha256", "pack_path", "action_deltas", "config_sha256", "identity"}
    if type(candidate_config) is not dict or set(candidate_config) != config_keys:
        raise OutcomeOnlyPolicyFixedBridgeError("bridge candidate config is not closed")
    control_keys = config_keys
    if type(control_config) is not dict or set(control_config) != control_keys:
        raise OutcomeOnlyPolicyFixedBridgeError("bridge control config is not closed")
    if candidate_config["candidate_id"] != manifest["candidate_id"]:
        raise OutcomeOnlyPolicyFixedBridgeError("bridge candidate identity mismatch")
    if control_config["candidate_id"] != manifest["control_id"] or manifest["control_id"] != "baseline-no-pack":
        raise OutcomeOnlyPolicyFixedBridgeError("bridge control identity mismatch")
    try:
        candidate_deltas = _validated_action_deltas(candidate_config["action_deltas"])
        control_deltas = _validated_action_deltas(control_config["action_deltas"])
    except (TypeError, ValueError) as exc:
        raise OutcomeOnlyPolicyFixedBridgeError(f"bridge action config is not bounded/legal: {exc}") from exc
    if control_deltas or control_config["pack_sha256"] is not None or control_config["pack_path"] is not None:
        raise OutcomeOnlyPolicyFixedBridgeError("bridge control config is not baseline")
    candidate_pack = None
    candidate_pack_path = candidate_config["pack_path"]
    if candidate_pack_path is not None:
        candidate_pack_file = _inside(root, str(candidate_pack_path), "bridge candidate KnowledgePack")
        try:
            candidate_pack = load_pack(candidate_pack_file)
        except (OSError, ValueError, TypeError) as exc:
            raise OutcomeOnlyPolicyFixedBridgeError("bridge candidate KnowledgePack cannot be loaded") from exc
        if candidate_config["pack_sha256"] != pack_bytes_sha256(candidate_pack):
            raise OutcomeOnlyPolicyFixedBridgeError("bridge candidate KnowledgePack identity mismatch")
    elif candidate_config["pack_sha256"] is not None:
        raise OutcomeOnlyPolicyFixedBridgeError("bridge candidate KnowledgePack path is missing")
    expected_candidate_sha = _candidate_policy_sha(current_root_sha, candidate_pack, candidate_deltas)
    if manifest["candidate_policy_sha256"] != expected_candidate_sha:
        raise OutcomeOnlyPolicyFixedBridgeError("bridge candidate policy identity mismatch")
    expected_candidate_config_sha = hashlib.sha256(
        _canonical({"pack_sha256": candidate_config["pack_sha256"], "action_deltas": candidate_deltas, "candidate_id": manifest["candidate_id"]})
    ).hexdigest()
    expected_control_config_sha = hashlib.sha256(
        _canonical({"pack_sha256": None, "action_deltas": {}, "candidate_id": "baseline-no-pack"})
    ).hexdigest()
    if candidate_config["config_sha256"] != expected_candidate_config_sha or control_config["config_sha256"] != expected_control_config_sha:
        raise OutcomeOnlyPolicyFixedBridgeError("bridge candidate/control config SHA mismatch")
    expected_candidate_identity = build_candidate_manifest(
        candidate_id=str(manifest["candidate_id"]),
        pack=candidate_pack,
        action_deltas=candidate_deltas,
        root_policy_sha256=current_root_sha,
        deck_sha256=str(manifest["deck_sha256"]),
        pool_manifest_sha256=str(manifest["pool_manifest_sha256"]),
        broad_config_sha256=str(manifest["broad_config_sha256"]),
        evaluator_sha256=str(manifest["evaluator_sha256"]),
        common24_ids=expected_train,
    )
    expected_control_identity = build_candidate_manifest(
        candidate_id="baseline-no-pack",
        pack=None,
        action_deltas=None,
        root_policy_sha256=current_root_sha,
        deck_sha256=str(manifest["deck_sha256"]),
        pool_manifest_sha256=str(manifest["pool_manifest_sha256"]),
        broad_config_sha256=str(manifest["broad_config_sha256"]),
        evaluator_sha256=str(manifest["evaluator_sha256"]),
        common24_ids=expected_train,
    )
    if candidate_config["identity"] != expected_candidate_identity or control_config["identity"] != expected_control_identity:
        raise OutcomeOnlyPolicyFixedBridgeError("bridge candidate/control manifest identity mismatch")
    expected_summary = {
        "source_games": schedule["summary"]["source_games"],
        "included_games": schedule["summary"]["included_games"],
        "heldout_exposure": 0,
        "slot_count": int(schedule["quota"]),
        "seat_counts": {"0": int(schedule["quota"]) // 2, "1": int(schedule["quota"]) // 2},
        "weight_sum": sum(float(item["weight"]) for item in schedule["entries"]),
        "quota_sum": sum(int(item["quota"]) for item in schedule["entries"]),
    }
    if manifest["schedule_summary"] != expected_summary:
        raise OutcomeOnlyPolicyFixedBridgeError("bridge schedule summary identity mismatch")
    slots = _slot_rows(schedule, base_seed=int(summary["base_seed"]))
    if list(slots) != manifest["slots"]:
        raise OutcomeOnlyPolicyFixedBridgeError("bridge slots differ from schedule")
    if set(manifest["heldout_ids"]) != {str(item["opponent_id"]) for item in schedule["excluded_heldout"]}:
        raise OutcomeOnlyPolicyFixedBridgeError("bridge heldout IDs mismatch")
    if any(slot["split"] != "META_TRAIN" for slot in slots) or int(manifest["schedule_summary"]["heldout_exposure"]) != 0:
        raise OutcomeOnlyPolicyFixedBridgeError("bridge has heldout exposure")
    return dict(manifest)


__all__ = [
    "OutcomeOnlyPolicyFixedBridgeError",
    "SCHEMA_V1",
    "build_policy_fixed_short_bridge_v1",
    "verify_policy_fixed_short_bridge_v1",
]
