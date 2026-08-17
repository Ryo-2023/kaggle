"""Research-only bounded action screen over an iteration-1 WDL schedule.

The schedule is verified before any game payload is built.  Candidate actions
are a small public Rule-v0 overlay and are never combined with a pack or
teacher labels.  This module only materializes paired game cells; its runner
is a separate, explicit entrypoint.
"""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
from typing import Mapping

from mage_ptcg.meta_specialist.outcome_only_hard_negative_v1 import _sha256_file as _schedule_sha_file
from mage_ptcg.meta_specialist.outcome_only_policy_fixed_bridge_v1 import (
    _game as _base_game,
    _slot_rows,
    _sha256 as _file_sha,
    _validated_action_deltas,
)
from mage_ptcg.meta_specialist.outcome_only_iteration1_schedule_v1 import (
    OutcomeOnlyIteration1ScheduleError,
    verify_outcome_only_iteration1_schedule_v1,
)
from mage_ptcg.meta_specialist.outcome_only_policy_fixed_confirmation_v1 import (
    OutcomeOnlyPolicyFixedConfirmationError,
    verify_policy_fixed_confirmation_v1,
)
from mage_ptcg.meta_specialist.opponent_pool_v1 import default_pool_root_v1, load_opponent_pool_v1
from scripts.parallel_cabt_evaluator_v1 import evaluator_implementation_sha256_v1
from scripts.run_performance_first_arena_v1 import root_policy_sha256
from scripts.run_rule_v0_knowledge_pool_screen_v1 import (
    DEFAULT_POOL_MANIFEST,
    RUNNER_REF_V1,
    _candidate_policy_sha,
    build_candidate_manifest,
)


SCHEMA_V1 = "meta-specialist-outcome-only-iteration1-action-screen-v1"
PHASE_V1 = "ACTION_SCREEN_96"
MAX_DELTA_V1 = 120.0
_SHA_HEX = frozenset("0123456789abcdef")
_AUTHORITY_FALSE = {
    "training_authority": False,
    "promotion_authority": False,
    "submission_authority": False,
    "external_execution_authority": False,
    "longrun_authority": False,
}
_MANIFEST_KEYS = frozenset(
    {
        "schema_version", "phase", "candidate_id", "control_id", "action_deltas",
        "candidate_policy_sha256", "control_policy_sha256", "root_policy_sha256",
        "deck_path", "deck_sha256", "pool_manifest_path", "pool_manifest_sha256",
        "broad_config_path", "broad_config_sha256", "evaluator_sha256", "runner_ref",
        "candidate_config", "control_config", "schedule_path", "schedule_file_sha256",
        "schedule_sha256", "confirmation_path", "confirmation_file_sha256",
        "confirmation_sha256", "seed_base", "seed_source_confirmation_sha256",
        "train_ids", "heldout_ids", "zero_quota_ids", "slots", "summary", "authority",
        "research_only", "execution_allowed", "ready_for_evaluation", "bridge_sha256",
        "screen_sha256",
    }
)


class OutcomeOnlyIteration1ActionScreenError(ValueError):
    """Raised when a bounded action screen is not closed and reproducible."""


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise OutcomeOnlyIteration1ActionScreenError(f"value is not canonical JSON: {exc}") from exc


def _sha(value: object, field: str) -> str:
    if type(value) is not str or len(value) != 64 or any(c not in _SHA_HEX for c in value):
        raise OutcomeOnlyIteration1ActionScreenError(f"{field} must be a lowercase SHA-256")
    return value


def _inside(root: Path, value: Path | str, field: str) -> Path:
    candidate = Path(value)
    path = (root / candidate).resolve() if not candidate.is_absolute() else candidate.resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise OutcomeOnlyIteration1ActionScreenError(f"{field} escapes repo_root") from exc
    if not path.is_file():
        raise OutcomeOnlyIteration1ActionScreenError(f"{field} is not a file: {path}")
    return path


def _semantic_sha(manifest: Mapping[str, object]) -> str:
    body = {key: value for key, value in manifest.items() if key not in {"screen_sha256", "bridge_sha256"}}
    return hashlib.sha256((SCHEMA_V1 + "\0").encode("ascii") + _canonical(body)).hexdigest()


def _load_json(path: Path, field: str) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise OutcomeOnlyIteration1ActionScreenError(f"{field} JSON is invalid") from exc
    if type(value) is not dict:
        raise OutcomeOnlyIteration1ActionScreenError(f"{field} must be an object")
    return value


def _load_inputs(root: Path, schedule_path: Path) -> tuple[dict[str, object], dict[str, object], Path, Path, Path, Path]:
    schedule = _load_json(schedule_path, "iteration schedule")
    try:
        verify_outcome_only_iteration1_schedule_v1(schedule, repo_root=root)
    except (OutcomeOnlyIteration1ScheduleError, ValueError) as exc:
        raise OutcomeOnlyIteration1ActionScreenError(f"iteration schedule verification failed: {exc}") from exc
    source_binding = schedule["sources"]["confirmation"]
    confirmation_path = _inside(root, str(source_binding["path"]), "confirmation source")
    confirmation = _load_json(confirmation_path, "confirmation")
    try:
        verify_policy_fixed_confirmation_v1(confirmation, repo_root=root)
    except (OutcomeOnlyPolicyFixedConfirmationError, ValueError) as exc:
        raise OutcomeOnlyIteration1ActionScreenError(f"confirmation verification failed: {exc}") from exc
    if _file_sha(confirmation_path) != source_binding["sha256"]:
        raise OutcomeOnlyIteration1ActionScreenError("confirmation source SHA differs from schedule")
    deck_path = _inside(root, str(confirmation["deck_path"]), "subject deck")
    pool_path = _inside(root, DEFAULT_POOL_MANIFEST, "pool manifest")
    broad_path = _inside(root, str(confirmation["broad_config_path"]), "broad config")
    if _file_sha(pool_path) != confirmation["pool_manifest_sha256"] or _file_sha(broad_path) != confirmation["broad_config_sha256"]:
        raise OutcomeOnlyIteration1ActionScreenError("pool/config source identity differs from confirmation")
    return schedule, confirmation, deck_path, pool_path, broad_path, confirmation_path


def _normalize_deltas(value: Mapping[str, object] | None) -> dict[str, float]:
    try:
        deltas = _validated_action_deltas(value)
    except (TypeError, ValueError) as exc:
        raise OutcomeOnlyIteration1ActionScreenError(f"action delta is not legal/bounded: {exc}") from exc
    if not deltas:
        raise OutcomeOnlyIteration1ActionScreenError("candidate must contain one bounded action delta")
    if "ATTACK" in deltas:
        raise OutcomeOnlyIteration1ActionScreenError("ATTACK candidate is excluded from iteration-1 screen")
    if len(deltas) > 2:
        raise OutcomeOnlyIteration1ActionScreenError("candidate may contain at most two action deltas")
    if any(abs(float(delta)) > MAX_DELTA_V1 for delta in deltas.values()):
        raise OutcomeOnlyIteration1ActionScreenError("action delta exceeds ±120 bound")
    return deltas


def _candidate_id(value: object) -> str:
    if type(value) is not str or not value or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for c in value):
        raise OutcomeOnlyIteration1ActionScreenError("candidate_id is invalid")
    return value


def _parent_seed_base(confirmation: Mapping[str, object]) -> int:
    slots = confirmation.get("slots")
    if type(slots) is not list or not slots:
        raise OutcomeOnlyIteration1ActionScreenError("confirmation seed slots are missing")
    seeds = [slot.get("seed") for slot in slots if type(slot) is dict]
    if len(seeds) != len(slots) or any(type(seed) is not int for seed in seeds) or len(set(seeds)) != len(seeds):
        raise OutcomeOnlyIteration1ActionScreenError("confirmation seed universe is malformed")
    return max(seeds) + 1


def _config_sha(candidate_id: str, deltas: Mapping[str, float]) -> str:
    return hashlib.sha256(_canonical({"candidate_id": candidate_id, "pack_sha256": None, "action_deltas": dict(deltas)})).hexdigest()


def _make_manifest(*, root: Path, schedule_path: Path, candidate_id: str, deltas: dict[str, float]) -> tuple[dict[str, object], dict[str, object], Path, Path, Path, Path]:
    schedule, confirmation, deck_path, pool_path, broad_path, confirmation_path = _load_inputs(root, schedule_path)
    candidate_id = _candidate_id(candidate_id)
    deltas = _normalize_deltas(deltas)
    root_sha = root_policy_sha256()
    evaluator_sha = evaluator_implementation_sha256_v1()
    for field, expected in (("deck_sha256", _file_sha(deck_path)), ("pool_manifest_sha256", _file_sha(pool_path)), ("broad_config_sha256", _file_sha(broad_path)), ("evaluator_sha256", evaluator_sha)):
        if confirmation[field] != expected:
            raise OutcomeOnlyIteration1ActionScreenError(f"confirmation {field} mismatch")
    if root_sha != confirmation["root_policy_sha256"] or confirmation["control_policy_sha256"] != root_sha:
        raise OutcomeOnlyIteration1ActionScreenError("current Rule v0 policy differs from sealed control")
    seed_base = _parent_seed_base(confirmation)
    slots = _slot_rows(schedule, base_seed=seed_base)
    if len(slots) != 96:
        raise OutcomeOnlyIteration1ActionScreenError("iteration schedule did not close to 96 slots")
    train_ids = [str(x) for x in schedule["train_ids"]]
    heldout_ids = [str(x) for x in schedule["heldout_ids"]]
    zero_ids = [str(x["opponent_id"]) for x in schedule["entries"] if int(x["quota"]) == 0]
    pool_sha = _file_sha(pool_path)
    broad_sha = _file_sha(broad_path)
    candidate_sha = _candidate_policy_sha(root_sha, None, deltas)
    candidate_identity = build_candidate_manifest(
        candidate_id=candidate_id, pack=None, action_deltas=deltas, root_policy_sha256=root_sha,
        deck_sha256=_file_sha(deck_path), pool_manifest_sha256=pool_sha, broad_config_sha256=broad_sha,
        evaluator_sha256=evaluator_sha, common24_ids=train_ids,
    )
    control_identity = build_candidate_manifest(
        candidate_id="baseline-no-pack", pack=None, action_deltas=None, root_policy_sha256=root_sha,
        deck_sha256=_file_sha(deck_path), pool_manifest_sha256=pool_sha, broad_config_sha256=broad_sha,
        evaluator_sha256=evaluator_sha, common24_ids=train_ids,
    )
    candidate_config = {"candidate_id": candidate_id, "pack_path": None, "pack_sha256": None, "action_deltas": deltas, "config_sha256": _config_sha(candidate_id, deltas), "identity": candidate_identity}
    control_config = {"candidate_id": "baseline-no-pack", "pack_path": None, "pack_sha256": None, "action_deltas": {}, "config_sha256": _config_sha("baseline-no-pack", {}), "identity": control_identity}
    summary = {
        "source_schedule_iteration": 1, "source_games": schedule["summary"]["source_games"], "slot_count": len(slots),
        "heldout_exposure": 0, "seat_counts": {"0": sum(int(x["seat"] == 0) for x in slots), "1": sum(int(x["seat"] == 1) for x in slots)},
        "action_trace_used": False, "private_fields_used": False, "teacher_labels_used": False, "training_data": False,
    }
    manifest: dict[str, object] = {
        "schema_version": SCHEMA_V1, "phase": PHASE_V1, "candidate_id": candidate_id, "control_id": "baseline-no-pack",
        "action_deltas": deltas, "candidate_policy_sha256": candidate_sha, "control_policy_sha256": root_sha, "root_policy_sha256": root_sha,
        "deck_path": str(deck_path.relative_to(root)), "deck_sha256": _file_sha(deck_path),
        "pool_manifest_path": str(pool_path.relative_to(root)), "pool_manifest_sha256": pool_sha,
        "broad_config_path": str(broad_path.relative_to(root)), "broad_config_sha256": broad_sha, "evaluator_sha256": evaluator_sha,
        "runner_ref": RUNNER_REF_V1, "candidate_config": candidate_config, "control_config": control_config,
        "schedule_path": str(schedule_path.relative_to(root)), "schedule_file_sha256": _file_sha(schedule_path), "schedule_sha256": schedule["schedule_sha256"],
        "confirmation_path": str(confirmation_path.relative_to(root)), "confirmation_file_sha256": _file_sha(confirmation_path), "confirmation_sha256": confirmation["confirmation_sha256"],
        "seed_base": seed_base, "seed_source_confirmation_sha256": confirmation["confirmation_sha256"],
        "train_ids": train_ids, "heldout_ids": heldout_ids, "zero_quota_ids": zero_ids, "slots": list(slots), "summary": summary,
        "authority": dict(_AUTHORITY_FALSE), "research_only": True, "execution_allowed": False, "ready_for_evaluation": True,
    }
    screen_sha = _semantic_sha(manifest)
    manifest["screen_sha256"] = screen_sha
    manifest["bridge_sha256"] = screen_sha
    return manifest, confirmation, deck_path, pool_path, broad_path, confirmation_path


def _games(*, root: Path, manifest: Mapping[str, object], deck_path: Path) -> tuple[tuple[object, ...], tuple[object, ...]]:
    pool = load_opponent_pool_v1(default_pool_root_v1(root))
    base_manifest = dict(manifest)
    base_manifest["bridge_sha256"] = manifest["screen_sha256"]
    candidate_games = []
    control_games = []
    for slot in manifest["slots"]:
        for arm, arm_id, policy_sha, config, deltas in (
            ("candidate", manifest["candidate_id"], manifest["candidate_policy_sha256"], manifest["candidate_config"], manifest["action_deltas"]),
            ("control", manifest["control_id"], manifest["control_policy_sha256"], manifest["control_config"], {}),
        ):
            game = _base_game(
                arm=arm, arm_id=str(arm_id), policy_sha=str(policy_sha), root=root, deck_path=deck_path,
                pool=pool, slot=slot, manifest=base_manifest, config=config, pack_path=None, action_deltas=deltas,
            )
            metadata = dict(game.metadata)
            metadata.update({"schema_version": SCHEMA_V1, "screen_sha256": manifest["screen_sha256"], "bridge_sha256": manifest["screen_sha256"], "confirmation_sha256": manifest["confirmation_sha256"], "schedule_iteration": 1, "heldout_exposure": 0})
            game = replace(game, game_id=f"{arm}-{SCHEMA_V1}-{slot['stratum_key']}", block_id=f"{SCHEMA_V1}-{arm}", metadata=metadata)
            (candidate_games if arm == "candidate" else control_games).append(game)
    return tuple(control_games), tuple(candidate_games)


def build_outcome_only_iteration1_action_screen_v1(*, repo_root: Path | str, schedule_path: Path | str, candidate_id: str, action_deltas: Mapping[str, object]) -> dict[str, object]:
    root = Path(repo_root).resolve()
    schedule_file = _inside(root, schedule_path, "iteration schedule")
    manifest, confirmation, deck_path, pool_path, broad_path, confirmation_path = _make_manifest(root=root, schedule_path=schedule_file, candidate_id=candidate_id, deltas=dict(action_deltas))
    control_games, candidate_games = _games(root=root, manifest=manifest, deck_path=deck_path)
    return {"manifest": manifest, "control_games": control_games, "candidate_games": candidate_games}


def verify_outcome_only_iteration1_action_screen_v1(manifest: Mapping[str, object], *, repo_root: Path | str) -> dict[str, object]:
    if type(manifest) is not dict or set(manifest) != _MANIFEST_KEYS:
        raise OutcomeOnlyIteration1ActionScreenError("action screen manifest schema is not closed")
    if manifest.get("schema_version") != SCHEMA_V1 or manifest.get("phase") != PHASE_V1:
        raise OutcomeOnlyIteration1ActionScreenError("action screen schema/phase mismatch")
    if manifest.get("authority") != _AUTHORITY_FALSE or manifest.get("research_only") is not True or manifest.get("execution_allowed") is not False or manifest.get("ready_for_evaluation") is not True:
        raise OutcomeOnlyIteration1ActionScreenError("action screen authority/readiness is invalid")
    if manifest.get("screen_sha256") != _semantic_sha(manifest) or manifest.get("bridge_sha256") != manifest.get("screen_sha256"):
        raise OutcomeOnlyIteration1ActionScreenError("action screen semantic SHA mismatch")
    root = Path(repo_root).resolve()
    schedule_path = _inside(root, str(manifest["schedule_path"]), "action screen schedule")
    if _file_sha(schedule_path) != manifest["schedule_file_sha256"]:
        raise OutcomeOnlyIteration1ActionScreenError("action screen schedule file SHA mismatch")
    schedule = _load_json(schedule_path, "action screen schedule")
    try:
        verify_outcome_only_iteration1_schedule_v1(schedule, repo_root=root)
    except (OutcomeOnlyIteration1ScheduleError, ValueError) as exc:
        raise OutcomeOnlyIteration1ActionScreenError(f"action screen schedule invalid: {exc}") from exc
    if schedule["schedule_sha256"] != manifest["schedule_sha256"]:
        raise OutcomeOnlyIteration1ActionScreenError("action screen schedule semantic identity mismatch")
    confirmation_path = _inside(root, str(manifest["confirmation_path"]), "action screen confirmation")
    if _file_sha(confirmation_path) != manifest["confirmation_file_sha256"]:
        raise OutcomeOnlyIteration1ActionScreenError("action screen confirmation file SHA mismatch")
    confirmation = _load_json(confirmation_path, "action screen confirmation")
    try:
        verify_policy_fixed_confirmation_v1(confirmation, repo_root=root)
    except (OutcomeOnlyPolicyFixedConfirmationError, ValueError) as exc:
        raise OutcomeOnlyIteration1ActionScreenError(f"action screen confirmation invalid: {exc}") from exc
    if confirmation["confirmation_sha256"] != manifest["confirmation_sha256"] or confirmation["confirmation_sha256"] != manifest["seed_source_confirmation_sha256"]:
        raise OutcomeOnlyIteration1ActionScreenError("action screen confirmation identity mismatch")
    expected_seed = _parent_seed_base(confirmation)
    if manifest["seed_base"] != expected_seed:
        raise OutcomeOnlyIteration1ActionScreenError("action screen seed base mismatch")
    deltas = _normalize_deltas(manifest["action_deltas"])
    if deltas != manifest["candidate_config"]["action_deltas"]:
        raise OutcomeOnlyIteration1ActionScreenError("action screen candidate config delta mismatch")
    root_sha = root_policy_sha256()
    if manifest["root_policy_sha256"] != root_sha or manifest["control_policy_sha256"] != root_sha:
        raise OutcomeOnlyIteration1ActionScreenError("action screen current root policy mismatch")
    expected_candidate = _candidate_policy_sha(root_sha, None, deltas)
    if manifest["candidate_policy_sha256"] != expected_candidate:
        raise OutcomeOnlyIteration1ActionScreenError("action screen candidate policy identity mismatch")
    if manifest["train_ids"] != schedule["train_ids"] or manifest["heldout_ids"] != schedule["heldout_ids"]:
        raise OutcomeOnlyIteration1ActionScreenError("action screen population identity mismatch")
    expected_slots = _slot_rows(schedule, base_seed=expected_seed)
    if manifest["slots"] != list(expected_slots):
        raise OutcomeOnlyIteration1ActionScreenError("action screen slot identity mismatch")
    if manifest["summary"]["slot_count"] != 96 or manifest["summary"]["heldout_exposure"] != 0 or manifest["summary"]["seat_counts"] != {"0": 48, "1": 48}:
        raise OutcomeOnlyIteration1ActionScreenError("action screen slot summary mismatch")
    return dict(manifest)


__all__ = ["OutcomeOnlyIteration1ActionScreenError", "SCHEMA_V1", "build_outcome_only_iteration1_action_screen_v1", "verify_outcome_only_iteration1_action_screen_v1"]
