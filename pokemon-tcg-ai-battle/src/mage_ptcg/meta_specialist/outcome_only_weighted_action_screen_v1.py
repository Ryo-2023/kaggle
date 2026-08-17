"""Research-only 48-game weighted action screen.

This is a deliberately small child of the sealed iteration-1 outcome schedule.
It keeps the hard-negative quota proportions, samples exactly half of the
parent's 96 slots, and preserves a paired Rule-v0 control.  Only one bounded
``ATTACH`` or ``END`` action-type delta is accepted.  No game is executed by
this module; the separate runner is the explicit execution boundary.
"""

from __future__ import annotations

from dataclasses import replace
import hashlib
import itertools
import json
from pathlib import Path
from typing import Mapping

from mage_ptcg.meta_specialist.outcome_only_iteration1_action_screen_v1 import (
    OutcomeOnlyIteration1ActionScreenError,
    _file_sha,
    _inside,
    _load_inputs,
    _parent_seed_base,
    _slot_rows,
    build_outcome_only_iteration1_action_screen_v1,
    verify_outcome_only_iteration1_action_screen_v1,
)


SCHEMA_V1 = "meta-specialist-outcome-only-weighted-action-screen-v1"
PHASE_V1 = "WEIGHTED_ACTION_SCREEN_48"
MAX_DELTA_V1 = 120.0
_SHA_HEX = frozenset("0123456789abcdef")
_ALLOWED_ACTIONS = frozenset({"ATTACH", "END"})
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
        "train_ids", "heldout_ids", "zero_quota_ids", "source_slot_count",
        "source_schedule_quota", "parent_screen_sha256", "slots", "summary", "authority",
        "research_only", "execution_allowed", "ready_for_evaluation", "bridge_sha256",
        "screen_sha256",
    }
)


class OutcomeOnlyWeightedActionScreenError(ValueError):
    """Raised when a weighted action screen is not closed and reproducible."""


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(
            value, ensure_ascii=False, allow_nan=False, sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise OutcomeOnlyWeightedActionScreenError("value is not canonical JSON") from exc


def _semantic_sha(manifest: Mapping[str, object]) -> str:
    body = {
        key: value for key, value in manifest.items()
        if key not in {"screen_sha256", "bridge_sha256"}
    }
    return hashlib.sha256((SCHEMA_V1 + "\0").encode("ascii") + _canonical(body)).hexdigest()


def _sha(value: object, field: str) -> str:
    if type(value) is not str or len(value) != 64 or any(char not in _SHA_HEX for char in value):
        raise OutcomeOnlyWeightedActionScreenError(f"{field} must be a lowercase SHA-256")
    return value


def _action_deltas(value: Mapping[str, object] | None) -> dict[str, float]:
    if not isinstance(value, Mapping):
        raise OutcomeOnlyWeightedActionScreenError("action_deltas must be a mapping")
    if set(value) != set(value).intersection(_ALLOWED_ACTIONS):
        raise OutcomeOnlyWeightedActionScreenError("weighted screen accepts ATTACH/END only")
    if len(value) != 1:
        raise OutcomeOnlyWeightedActionScreenError("weighted screen accepts exactly one action delta")
    name, raw_delta = next(iter(value.items()))
    if type(name) is not str or name not in _ALLOWED_ACTIONS:
        raise OutcomeOnlyWeightedActionScreenError("weighted screen action type is not allowed")
    if isinstance(raw_delta, bool) or type(raw_delta) not in (int, float):
        raise OutcomeOnlyWeightedActionScreenError("weighted screen action delta must be numeric")
    delta = float(raw_delta)
    if delta == 0.0 or abs(delta) > MAX_DELTA_V1:
        raise OutcomeOnlyWeightedActionScreenError("weighted screen action delta must be nonzero and within ±120")
    return {name: delta}


def _candidate_id(value: object) -> str:
    if type(value) is not str or not value or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for char in value):
        raise OutcomeOnlyWeightedActionScreenError("candidate_id is invalid")
    return value


def _grouped_slots(slots: tuple[dict[str, object], ...]) -> list[tuple[str, tuple[dict[str, object], ...]]]:
    grouped: dict[str, list[dict[str, object]]] = {}
    for slot in slots:
        grouped.setdefault(str(slot["opponent_id"]), []).append(slot)
    return [(key, tuple(grouped[key])) for key in sorted(grouped)]


def _desired_counts(schedule: Mapping[str, object]) -> dict[str, int]:
    entries = schedule.get("entries")
    if type(entries) is not list:
        raise OutcomeOnlyWeightedActionScreenError("schedule entries are malformed")
    parsed: list[tuple[str, int, float]] = []
    for entry in entries:
        if type(entry) is not dict:
            raise OutcomeOnlyWeightedActionScreenError("schedule entry is malformed")
        opponent_id = entry.get("opponent_id")
        quota = entry.get("quota")
        weight = entry.get("weight")
        if type(opponent_id) is not str or type(quota) is not int or quota < 0:
            raise OutcomeOnlyWeightedActionScreenError("schedule entry quota is malformed")
        if type(weight) not in (int, float) or float(weight) < 0.0:
            raise OutcomeOnlyWeightedActionScreenError("schedule entry weight is malformed")
        if entry.get("split") != "META_TRAIN":
            raise OutcomeOnlyWeightedActionScreenError("weighted slots cannot include heldout entries")
        parsed.append((opponent_id, quota, float(weight)))
    total = sum(quota for _, quota, _ in parsed)
    target = total // 2
    if total != 96 or target != 48:
        raise OutcomeOnlyWeightedActionScreenError("weighted screen requires a 96-slot parent schedule")
    counts = {opponent_id: quota // 2 for opponent_id, quota, _ in parsed}
    remaining = target - sum(counts.values())
    odd = sorted(
        ((-weight, opponent_id) for opponent_id, quota, weight in parsed if quota % 2),
        key=lambda item: (item[0], item[1]),
    )
    if remaining < 0 or remaining > len(odd):
        raise OutcomeOnlyWeightedActionScreenError("weighted quota rounding cannot close to 48")
    for _, opponent_id in odd[:remaining]:
        counts[opponent_id] += 1
    if sum(counts.values()) != 48:
        raise OutcomeOnlyWeightedActionScreenError("weighted quota sum is not 48")
    return counts


def select_weighted_slots_v1(schedule: Mapping[str, object], *, base_seed: int) -> tuple[dict[str, object], ...]:
    """Select a deterministic quota-preserving 48-slot subset with 24 seats each."""
    if type(base_seed) is not int or base_seed < 0:
        raise OutcomeOnlyWeightedActionScreenError("base_seed must be a nonnegative integer")
    full_slots = _slot_rows(schedule, base_seed=base_seed)
    desired = _desired_counts(schedule)
    groups = dict(_grouped_slots(full_slots))
    # DP stores the lexicographically first local-index choice for each seat-0
    # count.  Each group has at most six slots, so the state is tiny and exact.
    states: dict[int, tuple[tuple[int, ...], ...]] = {0: ()}
    for opponent_id in sorted(desired):
        group = groups.get(opponent_id, ())
        need = desired[opponent_id]
        if need > len(group):
            raise OutcomeOnlyWeightedActionScreenError("weighted quota exceeds source quota")
        choices: dict[int, list[tuple[int, ...]]] = {}
        for indices in itertools.combinations(range(len(group)), need):
            seat0 = sum(int(group[index]["seat"] == 0) for index in indices)
            choices.setdefault(seat0, []).append(indices)
        next_states: dict[int, tuple[tuple[int, ...], ...]] = {}
        for prior_seat0, prior_choice in states.items():
            for seat0, options in choices.items():
                total_seat0 = prior_seat0 + seat0
                if total_seat0 > 24:
                    continue
                for option in options:
                    candidate = prior_choice + (option,)
                    old = next_states.get(total_seat0)
                    if old is None or candidate < old:
                        next_states[total_seat0] = candidate
        states = next_states
        if not states:
            raise OutcomeOnlyWeightedActionScreenError("cannot balance weighted slots by seat")
    choices = states.get(24)
    if choices is None:
        raise OutcomeOnlyWeightedActionScreenError("weighted slots cannot close to 24/24 seats")
    selected: list[dict[str, object]] = []
    for (opponent_id, _), indices in zip(
        ((opponent_id, groups.get(opponent_id, ())) for opponent_id in sorted(desired)),
        choices,
        strict=True,
    ):
        group = groups.get(opponent_id, ())
        selected.extend(group[index] for index in indices)
    selected.sort(key=lambda slot: int(slot["slot_index"]))
    if len(selected) != 48 or sum(int(slot["seat"] == 0) for slot in selected) != 24:
        raise OutcomeOnlyWeightedActionScreenError("weighted slot selection did not close")
    return tuple(dict(slot) for slot in selected)


def _load_manifest_json(path: Path, field: str) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise OutcomeOnlyWeightedActionScreenError(f"{field} JSON is invalid") from exc
    if type(value) is not dict:
        raise OutcomeOnlyWeightedActionScreenError(f"{field} must be an object")
    return value


def _update_game(game: object, *, manifest: Mapping[str, object], slot: Mapping[str, object], arm: str) -> object:
    metadata = dict(game.metadata)
    metadata.update(
        {
            "schema_version": SCHEMA_V1,
            "phase": PHASE_V1,
            "screen_sha256": manifest["screen_sha256"],
            "bridge_sha256": manifest["bridge_sha256"],
            "parent_screen_sha256": manifest["parent_screen_sha256"],
            "weighted_slot_count": 48,
            "source_slot_count": 96,
            "heldout_exposure": 0,
            "arm": arm,
            **dict(slot),
        }
    )
    return replace(
        game,
        game_id=f"{arm}-{SCHEMA_V1}-{slot['stratum_key']}",
        block_id=f"{SCHEMA_V1}-{arm}",
        metadata=metadata,
    )


def build_outcome_only_weighted_action_screen_v1(
    *, repo_root: Path | str, schedule_path: Path | str, candidate_id: str,
    action_deltas: Mapping[str, object],
) -> dict[str, object]:
    """Materialize one 48-slot weighted ATTACH/END candidate and its control."""
    root = Path(repo_root).resolve()
    candidate_id = _candidate_id(candidate_id)
    deltas = _action_deltas(action_deltas)
    schedule_file = _inside(root, schedule_path, "iteration schedule")
    parent = build_outcome_only_iteration1_action_screen_v1(
        repo_root=root, schedule_path=schedule_file,
        candidate_id=candidate_id, action_deltas=deltas,
    )
    parent_manifest = parent["manifest"]
    try:
        verify_outcome_only_iteration1_action_screen_v1(parent_manifest, repo_root=root)
    except (OutcomeOnlyIteration1ActionScreenError, ValueError) as exc:
        raise OutcomeOnlyWeightedActionScreenError(f"parent action screen verification failed: {exc}") from exc
    schedule, confirmation, *_ = _load_inputs(root, schedule_file)
    seed_base = _parent_seed_base(confirmation)
    slots = select_weighted_slots_v1(schedule, base_seed=seed_base)
    selected_keys = {str(slot["stratum_key"]) for slot in slots}
    source_games = {
        arm: tuple(game for game in parent[f"{arm}_games"] if game.metadata.get("stratum_key") in selected_keys)
        for arm in ("control", "candidate")
    }
    if any(len(games) != 48 for games in source_games.values()):
        raise OutcomeOnlyWeightedActionScreenError("parent games do not contain exactly 48 selected strata")
    manifest: dict[str, object] = dict(parent_manifest)
    manifest.update(
        {
            "schema_version": SCHEMA_V1,
            "phase": PHASE_V1,
            "source_slot_count": 96,
            "source_schedule_quota": int(schedule["quota"]),
            "parent_screen_sha256": parent_manifest["screen_sha256"],
            "slots": list(slots),
            "summary": {
                "source_schedule_iteration": 1,
                "source_games": 384,
                "source_slot_count": 96,
                "slot_count": 48,
                "weighted_quota_sum": 48,
                "heldout_exposure": 0,
                "seat_counts": {
                    "0": sum(int(slot["seat"] == 0) for slot in slots),
                    "1": sum(int(slot["seat"] == 1) for slot in slots),
                },
                "action_trace_used": False,
                "private_fields_used": False,
                "teacher_labels_used": False,
                "training_data": False,
                "hard_negative_support": len({str(slot["opponent_id"]) for slot in slots}),
            },
        }
    )
    # Re-seal after all material fields are present.  Game metadata is updated
    # below after the digest is known.
    manifest["screen_sha256"] = _semantic_sha(manifest)
    manifest["bridge_sha256"] = manifest["screen_sha256"]
    control_games = tuple(
        _update_game(game, manifest=manifest, slot=slot, arm="control")
        for game, slot in zip(sorted(source_games["control"], key=lambda item: int(item.metadata["slot_index"])), slots, strict=True)
    )
    candidate_games = tuple(
        _update_game(game, manifest=manifest, slot=slot, arm="candidate")
        for game, slot in zip(sorted(source_games["candidate"], key=lambda item: int(item.metadata["slot_index"])), slots, strict=True)
    )
    return {"manifest": manifest, "control_games": control_games, "candidate_games": candidate_games}


def verify_outcome_only_weighted_action_screen_v1(
    manifest: Mapping[str, object], *, repo_root: Path | str,
) -> dict[str, object]:
    if type(manifest) is not dict or set(manifest) != _MANIFEST_KEYS:
        raise OutcomeOnlyWeightedActionScreenError("weighted action screen manifest schema is not closed")
    if manifest.get("schema_version") != SCHEMA_V1 or manifest.get("phase") != PHASE_V1:
        raise OutcomeOnlyWeightedActionScreenError("weighted action screen schema/phase mismatch")
    if manifest.get("authority") != _AUTHORITY_FALSE or manifest.get("research_only") is not True or manifest.get("execution_allowed") is not False or manifest.get("ready_for_evaluation") is not True:
        raise OutcomeOnlyWeightedActionScreenError("weighted action screen authority/readiness is invalid")
    if manifest.get("screen_sha256") != _semantic_sha(manifest) or manifest.get("bridge_sha256") != manifest.get("screen_sha256"):
        raise OutcomeOnlyWeightedActionScreenError("weighted action screen semantic SHA mismatch")
    deltas = _action_deltas(manifest.get("action_deltas"))
    if manifest.get("candidate_config", {}).get("action_deltas") != deltas:
        raise OutcomeOnlyWeightedActionScreenError("weighted action screen candidate delta binding mismatch")
    root = Path(repo_root).resolve()
    schedule_path = _inside(root, str(manifest["schedule_path"]), "weighted schedule")
    if _file_sha(schedule_path) != manifest["schedule_file_sha256"]:
        raise OutcomeOnlyWeightedActionScreenError("weighted schedule file SHA mismatch")
    # Rebuild the parent with the exact candidate.  This rechecks the sealed
    # schedule, confirmation, deck/pool/evaluator identity and Rule-v0 SHA.
    parent = build_outcome_only_iteration1_action_screen_v1(
        repo_root=root, schedule_path=schedule_path,
        candidate_id=str(manifest["candidate_id"]), action_deltas=deltas,
    )
    verify_outcome_only_iteration1_action_screen_v1(parent["manifest"], repo_root=root)
    if manifest["parent_screen_sha256"] != parent["manifest"]["screen_sha256"]:
        raise OutcomeOnlyWeightedActionScreenError("weighted parent screen identity mismatch")
    schedule, confirmation, *_ = _load_inputs(root, schedule_path)
    expected_seed = _parent_seed_base(confirmation)
    if manifest.get("seed_base") != expected_seed or manifest.get("seed_source_confirmation_sha256") != confirmation["confirmation_sha256"]:
        raise OutcomeOnlyWeightedActionScreenError("weighted seed identity mismatch")
    expected_slots = select_weighted_slots_v1(schedule, base_seed=expected_seed)
    if manifest.get("slots") != list(expected_slots):
        raise OutcomeOnlyWeightedActionScreenError("weighted slot identity mismatch")
    if manifest.get("train_ids") != schedule.get("train_ids") or manifest.get("heldout_ids") != schedule.get("heldout_ids"):
        raise OutcomeOnlyWeightedActionScreenError("weighted population identity mismatch")
    summary = manifest.get("summary")
    if type(summary) is not dict or summary.get("slot_count") != 48 or summary.get("source_slot_count") != 96 or summary.get("weighted_quota_sum") != 48 or summary.get("heldout_exposure") != 0 or summary.get("seat_counts") != {"0": 24, "1": 24}:
        raise OutcomeOnlyWeightedActionScreenError("weighted slot summary mismatch")
    if manifest.get("source_schedule_quota") != 96:
        raise OutcomeOnlyWeightedActionScreenError("weighted source schedule quota mismatch")
    if manifest.get("candidate_policy_sha256") != parent["manifest"]["candidate_policy_sha256"] or manifest.get("control_policy_sha256") != parent["manifest"]["control_policy_sha256"]:
        raise OutcomeOnlyWeightedActionScreenError("weighted policy identity mismatch")
    return dict(manifest)


__all__ = [
    "OutcomeOnlyWeightedActionScreenError",
    "PHASE_V1",
    "SCHEMA_V1",
    "build_outcome_only_weighted_action_screen_v1",
    "select_weighted_slots_v1",
    "verify_outcome_only_weighted_action_screen_v1",
]
