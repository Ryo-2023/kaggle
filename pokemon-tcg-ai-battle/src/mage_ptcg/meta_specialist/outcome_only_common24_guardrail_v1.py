"""Research-only full-common24 guardrail for one bounded action overlay.

The preceding weighted screen is META_TRAIN-only.  This guardrail uses the
same candidate/control on all 24 broad-pool IDs for evaluation only: the four
sealed heldout IDs are present in the denominator but are never admitted to
the hard-negative schedule, weighting, or any training artifact.
"""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
from typing import Mapping

from mage_ptcg.meta_specialist.outcome_only_iteration1_schedule_v1 import (
    OutcomeOnlyIteration1ScheduleError,
    verify_outcome_only_iteration1_schedule_v1,
)
from mage_ptcg.meta_specialist.outcome_only_iteration1_action_screen_v1 import _inside
from scripts.parallel_cabt_evaluator_v1 import evaluator_implementation_sha256_v1
from scripts.run_performance_first_arena_v1 import ROOT_DECK, root_policy_sha256
from scripts.run_rule_v0_knowledge_pool_screen_v1 import (
    DEFAULT_BROAD_CONFIG,
    DEFAULT_POOL_MANIFEST,
    RUNNER_REF_V1,
    _candidate_policy_sha,
    _sha256,
    build_candidate_manifest,
    build_screen_games,
)


SCHEMA_V1 = "meta-specialist-outcome-only-common24-guardrail-v1"
PHASE_V1 = "COMMON24_GUARDRAIL_96"
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
        "schema_version", "phase", "candidate_id", "control_id", "action_deltas",
        "candidate_policy_sha256", "control_policy_sha256", "root_policy_sha256",
        "deck_path", "deck_sha256", "pool_manifest_path", "pool_manifest_sha256",
        "broad_config_path", "broad_config_sha256", "evaluator_sha256", "runner_ref",
        "candidate_config", "control_config", "schedule_path", "schedule_file_sha256",
        "schedule_sha256", "base_seed", "opponent_ids", "train_ids", "heldout_ids",
        "heldout_training_exposure", "slots", "summary", "authority", "research_only",
        "execution_allowed", "ready_for_evaluation", "screen_sha256", "bridge_sha256",
    }
)


class OutcomeOnlyCommon24GuardrailError(ValueError):
    """Raised when the full-common24 evaluation guardrail is not closed."""


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise OutcomeOnlyCommon24GuardrailError("value is not canonical JSON") from exc


def _semantic_sha(manifest: Mapping[str, object]) -> str:
    body = {key: value for key, value in manifest.items() if key not in {"screen_sha256", "bridge_sha256"}}
    return hashlib.sha256((SCHEMA_V1 + "\0").encode("ascii") + _canonical(body)).hexdigest()


def _sha(value: object, field: str) -> str:
    if type(value) is not str or len(value) != 64 or any(char not in _SHA_HEX for char in value):
        raise OutcomeOnlyCommon24GuardrailError(f"{field} must be a lowercase SHA-256")
    return value


def _inside_file(root: Path, value: str | Path, field: str) -> Path:
    try:
        return _inside(root, value, field)
    except (ValueError, OSError) as exc:
        raise OutcomeOnlyCommon24GuardrailError(str(exc)) from exc


def _load_json(path: Path, field: str) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise OutcomeOnlyCommon24GuardrailError(f"{field} JSON is invalid") from exc
    if type(payload) is not dict:
        raise OutcomeOnlyCommon24GuardrailError(f"{field} must be an object")
    return payload


def _action_delta(value: Mapping[str, object]) -> dict[str, float]:
    if not isinstance(value, Mapping) or set(value) != {"ATTACH"}:
        raise OutcomeOnlyCommon24GuardrailError("common24 guardrail accepts ATTACH only")
    raw = value["ATTACH"]
    if isinstance(raw, bool) or type(raw) not in (int, float) or not (-120.0 <= float(raw) <= 120.0) or float(raw) == 0.0:
        raise OutcomeOnlyCommon24GuardrailError("ATTACH delta must be finite, nonzero, and within ±120")
    return {"ATTACH": float(raw)}


def _load_sources(root: Path, schedule_path: Path, broad_path: Path) -> tuple[dict[str, object], dict[str, object]]:
    schedule = _load_json(schedule_path, "schedule")
    try:
        verify_outcome_only_iteration1_schedule_v1(schedule, repo_root=root)
    except (OutcomeOnlyIteration1ScheduleError, ValueError) as exc:
        raise OutcomeOnlyCommon24GuardrailError(f"schedule verification failed: {exc}") from exc
    broad = _load_json(broad_path, "broad config")
    opponent_ids = broad.get("opponent_ids")
    if type(opponent_ids) is not list or len(opponent_ids) != 24 or len(set(opponent_ids)) != 24 or any(type(item) is not str or not item for item in opponent_ids):
        raise OutcomeOnlyCommon24GuardrailError("broad config must contain exactly 24 unique opponent IDs")
    train_ids = schedule.get("train_ids")
    heldout_ids = schedule.get("heldout_ids")
    if type(train_ids) is not list or type(heldout_ids) is not list or set(train_ids) | set(heldout_ids) != set(opponent_ids) or set(train_ids) & set(heldout_ids):
        raise OutcomeOnlyCommon24GuardrailError("broad config and schedule split IDs do not close")
    return schedule, broad


def _slots(opponent_ids: list[str], train_ids: list[str], heldout_ids: list[str], *, base_seed: int) -> tuple[dict[str, object], ...]:
    if type(base_seed) is not int or base_seed < 0:
        raise OutcomeOnlyCommon24GuardrailError("base_seed must be a nonnegative integer")
    heldout = set(heldout_ids)
    slots: list[dict[str, object]] = []
    ordinal = 0
    for opponent_id in opponent_ids:
        split = "META_FINAL" if opponent_id in heldout else "META_TRAIN"
        for seat in (0, 1):
            for repetition in range(2):
                slots.append(
                    {
                        "slot_index": ordinal,
                        "stratum_key": f"{opponent_id}-seat{seat}-rep{repetition:02d}",
                        "opponent_id": opponent_id,
                        "seat": seat,
                        "repetition": repetition,
                        "seed": base_seed + ordinal,
                        "split": split,
                        "training_exposure": 0,
                    }
                )
                ordinal += 1
    return tuple(slots)


def _bind_games(games: tuple[object, ...], *, manifest: Mapping[str, object], slots: tuple[dict[str, object], ...], arm: str) -> tuple[object, ...]:
    ordered = sorted(games, key=lambda game: int(game.seed))
    if len(ordered) != len(slots):
        raise OutcomeOnlyCommon24GuardrailError("common24 game count does not close")
    bound = []
    for game, slot in zip(ordered, slots, strict=True):
        if (game.opponent_id, game.seat, game.seed, game.metadata.get("repetition")) != (slot["opponent_id"], slot["seat"], slot["seed"], slot["repetition"]):
            raise OutcomeOnlyCommon24GuardrailError("common24 game slot identity differs")
        metadata = dict(game.metadata)
        metadata.update(
            {
                "schema_version": SCHEMA_V1,
                "phase": PHASE_V1,
                "screen_sha256": manifest["screen_sha256"],
                "bridge_sha256": manifest["bridge_sha256"],
                "arm": arm,
                "evaluation_only": True,
                "heldout_training_exposure": 0,
                "synthetic_opponent": False,
                "stratum_key": slot["stratum_key"],
                **slot,
            }
        )
        bound.append(replace(game, game_id=f"{arm}-{SCHEMA_V1}-{slot['stratum_key']}", block_id=f"{SCHEMA_V1}-{arm}", metadata=metadata))
    return tuple(bound)


def build_outcome_only_common24_guardrail_v1(
    *, repo_root: Path | str, schedule_path: Path | str, broad_config_path: Path | str,
    candidate_id: str, action_deltas: Mapping[str, object], base_seed: int,
) -> dict[str, object]:
    root = Path(repo_root).resolve()
    schedule_file = _inside_file(root, schedule_path, "schedule")
    broad_file = _inside_file(root, broad_config_path, "broad config")
    schedule, broad = _load_sources(root, schedule_file, broad_file)
    deltas = _action_delta(action_deltas)
    opponent_ids = [str(item) for item in broad["opponent_ids"]]
    train_ids = [str(item) for item in schedule["train_ids"]]
    heldout_ids = [str(item) for item in schedule["heldout_ids"]]
    deck = _inside_file(root, ROOT_DECK, "subject deck")
    pool = _inside_file(root, DEFAULT_POOL_MANIFEST, "pool manifest")
    broad_sha = _sha256(broad_file)
    pool_sha = _sha256(pool)
    deck_sha = _sha256(deck)
    root_sha = root_policy_sha256()
    evaluator_sha = evaluator_implementation_sha256_v1()
    if candidate_id != "attach-plus-120":
        raise OutcomeOnlyCommon24GuardrailError("common24 guardrail is reserved for attach-plus-120")
    slots = _slots(opponent_ids, train_ids, heldout_ids, base_seed=base_seed)
    candidate_games_raw = build_screen_games(
        candidate_id=candidate_id, pack_path=None, pack=None, action_deltas=deltas,
        opponent_ids=opponent_ids, games_per_seat=2, base_seed=base_seed,
        subject_deck=deck, pool_manifest_sha256=pool_sha, broad_config_sha256=broad_sha,
        evaluator_sha256=evaluator_sha, root_policy_sha256=root_sha,
        block_id=f"{SCHEMA_V1}-candidate-96",
    )
    control_games_raw = build_screen_games(
        candidate_id="baseline-no-pack", pack_path=None, pack=None, action_deltas={},
        opponent_ids=opponent_ids, games_per_seat=2, base_seed=base_seed,
        subject_deck=deck, pool_manifest_sha256=pool_sha, broad_config_sha256=broad_sha,
        evaluator_sha256=evaluator_sha, root_policy_sha256=root_sha,
        block_id=f"{SCHEMA_V1}-control-96",
    )
    candidate_identity = build_candidate_manifest(
        candidate_id=candidate_id, pack=None, action_deltas=deltas,
        root_policy_sha256=root_sha, deck_sha256=deck_sha, pool_manifest_sha256=pool_sha,
        broad_config_sha256=broad_sha, evaluator_sha256=evaluator_sha, common24_ids=opponent_ids,
    )
    control_identity = build_candidate_manifest(
        candidate_id="baseline-no-pack", pack=None, action_deltas={},
        root_policy_sha256=root_sha, deck_sha256=deck_sha, pool_manifest_sha256=pool_sha,
        broad_config_sha256=broad_sha, evaluator_sha256=evaluator_sha, common24_ids=opponent_ids,
    )
    manifest: dict[str, object] = {
        "schema_version": SCHEMA_V1, "phase": PHASE_V1, "candidate_id": candidate_id,
        "control_id": "baseline-no-pack", "action_deltas": deltas,
        "candidate_policy_sha256": _candidate_policy_sha(root_sha, None, deltas),
        "control_policy_sha256": root_sha, "root_policy_sha256": root_sha,
        "deck_path": str(deck.relative_to(root)), "deck_sha256": deck_sha,
        "pool_manifest_path": str(pool.relative_to(root)), "pool_manifest_sha256": pool_sha,
        "broad_config_path": str(broad_file.relative_to(root)), "broad_config_sha256": broad_sha,
        "evaluator_sha256": evaluator_sha, "runner_ref": RUNNER_REF_V1,
        "candidate_config": {"identity": candidate_identity, "action_deltas": deltas},
        "control_config": {"identity": control_identity, "action_deltas": {}},
        "schedule_path": str(schedule_file.relative_to(root)), "schedule_file_sha256": _sha256(schedule_file),
        "schedule_sha256": schedule["schedule_sha256"], "base_seed": base_seed,
        "opponent_ids": opponent_ids, "train_ids": train_ids, "heldout_ids": heldout_ids,
        "heldout_training_exposure": 0, "slots": list(slots),
        "summary": {
            "slot_count": 96, "seat_counts": {"0": 48, "1": 48},
            "train_evaluation_games": 80, "heldout_evaluation_games": 16,
            "heldout_training_exposure": 0, "training_exposure": 0,
            "hard_negative_weight_update": False, "action_trace_used": False,
            "teacher_labels_used": False, "private_fields_used": False,
            "faults": 0,
        },
        "authority": dict(_AUTHORITY_FALSE), "research_only": True,
        "execution_allowed": False, "ready_for_evaluation": True,
    }
    manifest["screen_sha256"] = _semantic_sha(manifest)
    manifest["bridge_sha256"] = manifest["screen_sha256"]
    control_games = _bind_games(control_games_raw, manifest=manifest, slots=slots, arm="control")
    candidate_games = _bind_games(candidate_games_raw, manifest=manifest, slots=slots, arm="candidate")
    return {"manifest": manifest, "control_games": control_games, "candidate_games": candidate_games}


def verify_outcome_only_common24_guardrail_v1(manifest: Mapping[str, object], *, repo_root: Path | str) -> dict[str, object]:
    if type(manifest) is not dict or set(manifest) != _MANIFEST_KEYS:
        raise OutcomeOnlyCommon24GuardrailError("common24 guardrail manifest schema is not closed")
    if manifest.get("schema_version") != SCHEMA_V1 or manifest.get("phase") != PHASE_V1:
        raise OutcomeOnlyCommon24GuardrailError("common24 guardrail schema/phase mismatch")
    if manifest.get("authority") != _AUTHORITY_FALSE or manifest.get("research_only") is not True or manifest.get("execution_allowed") is not False or manifest.get("ready_for_evaluation") is not True:
        raise OutcomeOnlyCommon24GuardrailError("common24 guardrail authority/readiness is invalid")
    if manifest.get("screen_sha256") != _semantic_sha(manifest) or manifest.get("bridge_sha256") != manifest.get("screen_sha256"):
        raise OutcomeOnlyCommon24GuardrailError("common24 guardrail semantic SHA mismatch")
    root = Path(repo_root).resolve()
    schedule_path = _inside_file(root, str(manifest["schedule_path"]), "schedule")
    broad_path = _inside_file(root, str(manifest["broad_config_path"]), "broad config")
    if _sha256(schedule_path) != manifest["schedule_file_sha256"] or _sha256(broad_path) != manifest["broad_config_sha256"]:
        raise OutcomeOnlyCommon24GuardrailError("common24 source file SHA mismatch")
    schedule, broad = _load_sources(root, schedule_path, broad_path)
    if manifest["schedule_sha256"] != schedule["schedule_sha256"] or manifest["opponent_ids"] != broad["opponent_ids"] or manifest["train_ids"] != schedule["train_ids"] or manifest["heldout_ids"] != schedule["heldout_ids"]:
        raise OutcomeOnlyCommon24GuardrailError("common24 population identity mismatch")
    expected_slots = _slots(list(broad["opponent_ids"]), list(schedule["train_ids"]), list(schedule["heldout_ids"]), base_seed=int(manifest["base_seed"]))
    if manifest["slots"] != list(expected_slots) or len(expected_slots) != 96:
        raise OutcomeOnlyCommon24GuardrailError("common24 slot identity mismatch")
    summary = manifest["summary"]
    if summary.get("slot_count") != 96 or summary.get("seat_counts") != {"0": 48, "1": 48} or summary.get("train_evaluation_games") != 80 or summary.get("heldout_evaluation_games") != 16 or summary.get("heldout_training_exposure") != 0:
        raise OutcomeOnlyCommon24GuardrailError("common24 summary mismatch")
    deltas = _action_delta(manifest["action_deltas"])
    root_sha = root_policy_sha256()
    if manifest["root_policy_sha256"] != root_sha or manifest["control_policy_sha256"] != root_sha or manifest["candidate_policy_sha256"] != _candidate_policy_sha(root_sha, None, deltas):
        raise OutcomeOnlyCommon24GuardrailError("common24 policy identity mismatch")
    return dict(manifest)


__all__ = [
    "OutcomeOnlyCommon24GuardrailError",
    "PHASE_V1",
    "SCHEMA_V1",
    "build_outcome_only_common24_guardrail_v1",
    "verify_outcome_only_common24_guardrail_v1",
]
