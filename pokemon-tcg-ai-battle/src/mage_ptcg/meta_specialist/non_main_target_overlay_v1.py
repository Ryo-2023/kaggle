"""Research-only Rule-v0 non-MAIN target score overlay.

The candidate changes only the public target ranking used by non-MAIN
selection prompts.  MAIN action selection, legal option construction, and the
Rule-v0 fallback remain untouched.  The materializer below binds every source
and configuration hash needed by a later weighted screen; it never executes a
game by itself.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
import hashlib
import json
from pathlib import Path
from typing import Any, Sequence

from agents.rule_agent import rank_rule_indices
from main import make_rule_agent
from mage_ptcg.meta_specialist.outcome_only_iteration1_action_screen_v1 import (
    _load_inputs,
    _parent_seed_base,
)
from mage_ptcg.meta_specialist.outcome_only_policy_fixed_bridge_v1 import (
    _game as _base_game,
    _inside,
    _sha256 as _file_sha,
    _slot_rows,
)
from mage_ptcg.meta_specialist.outcome_only_weighted_action_screen_v1 import (
    select_weighted_slots_v1,
)
from mage_ptcg.meta_specialist.opponent_pool_v1 import (
    build_opponent_agent_factory_v1,
    default_pool_root_v1,
    load_opponent_pool_v1,
    resolve_opponent_v1,
)
from scripts.parallel_cabt_evaluator_v1 import (
    EvaluationGameV1,
    evaluator_implementation_sha256_v1,
)
from scripts.run_performance_first_arena_v1 import root_policy_sha256
from scripts.run_rule_v0_knowledge_pool_screen_v1 import DEFAULT_POOL_MANIFEST
from scripts.test_sim import run_match


SCHEMA_V1 = "meta-specialist-rule-v0-non-main-target-overlay-v1"
PHASE_V1 = "WEIGHTED_TARGET_SCREEN_48"
RUNNER_REF_V1 = "mage_ptcg.meta_specialist.non_main_target_overlay_v1:run_non_main_target_overlay_game_v1"
TARGET_CANDIDATE_ID_V1 = "nonmain-target-lethal-d120-v1"

NON_MAIN_TARGET_CONFIG_V1: dict[str, object] = {
    "scope": "NON_MAIN_ONLY",
    "public_fields": ["damage", "hp", "playerIndex", "type"],
    "lethal_bonus_delta": 120.0,
    "main_action_deltas": {},
    "fallback": "RULE_V0_EXACT",
}

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
        "schema_version", "phase", "candidate_id", "control_id",
        "candidate_policy_sha256", "control_policy_sha256", "root_policy_sha256",
        "deck_path", "deck_sha256", "pool_manifest_path", "pool_manifest_sha256",
        "broad_config_path", "broad_config_sha256", "evaluator_sha256",
        "schedule_path", "schedule_file_sha256", "schedule_sha256",
        "confirmation_path", "confirmation_file_sha256", "confirmation_sha256",
        "seed_base", "seed_source_confirmation_sha256", "train_ids", "heldout_ids",
        "zero_quota_ids", "slots", "target_config", "target_config_sha256",
        "candidate_config", "control_config", "summary", "runner_ref", "authority",
        "research_only", "execution_allowed", "ready_for_evaluation",
        "bridge_sha256", "screen_sha256",
    }
)


class NonMainTargetOverlayError(ValueError):
    """Raised when the non-MAIN target overlay is malformed or unbound."""


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise NonMainTargetOverlayError("value is not canonical JSON") from exc


def _sha(value: object, field: str) -> str:
    if type(value) is not str or len(value) != 64 or any(char not in _SHA_HEX for char in value):
        raise NonMainTargetOverlayError(f"{field} must be a lowercase SHA-256")
    return value


def _candidate_id(value: object) -> str:
    if type(value) is not str or not value or any(
        char not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for char in value
    ):
        raise NonMainTargetOverlayError("candidate_id is invalid")
    if value != TARGET_CANDIDATE_ID_V1:
        raise NonMainTargetOverlayError("only the registered lethal-d120 candidate is allowed")
    return value


def _target_config(value: Mapping[str, object] | None = None) -> dict[str, object]:
    parsed = dict(NON_MAIN_TARGET_CONFIG_V1 if value is None else value)
    if parsed != NON_MAIN_TARGET_CONFIG_V1:
        raise NonMainTargetOverlayError("target config is not the registered public lethal overlay")
    return parsed


def _target_config_sha(value: Mapping[str, object] | None = None) -> str:
    return hashlib.sha256(_canonical(_target_config(value))).hexdigest()


def _semantic_sha(manifest: Mapping[str, object]) -> str:
    body = {
        key: value
        for key, value in manifest.items()
        if key not in {"screen_sha256", "bridge_sha256"}
    }
    return hashlib.sha256((SCHEMA_V1 + "\0").encode("ascii") + _canonical(body)).hexdigest()


def _policy_sha(root_sha: str, target_sha: str) -> str:
    _sha(root_sha, "root_policy_sha256")
    _sha(target_sha, "target_config_sha256")
    return hashlib.sha256(
        b"rule-v0-non-main-target\0" + _canonical(
            {"root_policy_sha256": root_sha, "target_config_sha256": target_sha}
        )
    ).hexdigest()


def _is_main_selection(select: Mapping[str, object]) -> bool:
    value = select.get("type")
    if value == 0:
        return True
    name = getattr(value, "name", None)
    if isinstance(name, str):
        return name.rsplit(".", 1)[-1].upper() == "MAIN"
    return isinstance(value, str) and value.rsplit(".", 1)[-1].upper() == "MAIN"


def _public_int(option: Mapping[str, object], field: str) -> int | None:
    value = option.get(field)
    if type(value) is int:
        return value
    return None


class NonMainTargetOverlayAgent:
    """Callable Rule-v0 wrapper with explicit public coverage counters."""

    def __init__(self, *, deck: Sequence[int] | None, seed: int | None) -> None:
        self._baseline = make_rule_agent(deck=deck, seed=seed)
        self.stats: dict[str, object] = {
            "decisions": 0,
            "eligible_non_main_decisions": 0,
            "changed_target_decisions": 0,
            "main_fallback_decisions": 0,
            "malformed_fallback_decisions": 0,
            "unsupported_fallback_decisions": 0,
            "exception_fallback_decisions": 0,
            "illegal_fallback_decisions": 0,
            "selection_type_counts": {},
            "changed_selection_type_counts": {},
        }

    def _increment(self, key: str) -> None:
        self.stats[key] = int(self.stats[key]) + 1

    def _selection_type(self, select: Mapping[str, object]) -> str:
        value = select.get("type")
        name = getattr(value, "name", None)
        if isinstance(name, str):
            return name.rsplit(".", 1)[-1].upper()
        return str(value)

    def __call__(self, observation: object) -> list[int] | None:
        fallback = self._baseline(observation)
        self._increment("decisions")
        try:
            if not isinstance(observation, Mapping):
                self._increment("unsupported_fallback_decisions")
                return fallback
            select = observation.get("select")
            if not isinstance(select, Mapping):
                self._increment("unsupported_fallback_decisions")
                return fallback
            selection_type = self._selection_type(select)
            type_counts = self.stats["selection_type_counts"]
            assert isinstance(type_counts, dict)
            type_counts[selection_type] = int(type_counts.get(selection_type, 0)) + 1
            if _is_main_selection(select):
                self._increment("main_fallback_decisions")
                return fallback
            options = select.get("option")
            raw_min = select.get("minCount")
            raw_max = select.get("maxCount")
            if (
                not isinstance(options, list)
                or type(raw_min) is not int
                or type(raw_max) is not int
                or raw_min < 1
                or raw_max < raw_min
                or raw_max > len(options)
            ):
                self._increment("malformed_fallback_decisions")
                return fallback
            ranked = rank_rule_indices(observation)
            if not ranked:
                self._increment("unsupported_fallback_decisions")
                return fallback
            adjusted: list[tuple[int, float, int]] = []
            for index, base_score in ranked:
                if type(index) is not int or index < 0 or index >= len(options):
                    self._increment("malformed_fallback_decisions")
                    return fallback
                option = options[index]
                if not isinstance(option, Mapping):
                    self._increment("malformed_fallback_decisions")
                    return fallback
                damage = _public_int(option, "damage")
                hp = _public_int(option, "hp")
                if damage is None or hp is None or damage < 0 or hp < 0:
                    self._increment("malformed_fallback_decisions")
                    return fallback
                delta = 120.0 if hp <= damage else 0.0
                adjusted.append((index, float(base_score) + delta, index))
            adjusted.sort(key=lambda row: (-row[1], row[2]))
            count = min(raw_min if raw_min else 1, raw_max)
            selected = [row[0] for row in adjusted[:count]]
            if (
                len(selected) != count
                or len(selected) != len(set(selected))
                or any(type(index) is not int or not 0 <= index < len(options) for index in selected)
            ):
                self._increment("illegal_fallback_decisions")
                return fallback
            self._increment("eligible_non_main_decisions")
            if selected != fallback:
                self._increment("changed_target_decisions")
                changed_counts = self.stats["changed_selection_type_counts"]
                assert isinstance(changed_counts, dict)
                changed_counts[selection_type] = int(changed_counts.get(selection_type, 0)) + 1
            return selected
        except Exception:
            self._increment("exception_fallback_decisions")
            return fallback


def build_non_main_target_agent(
    *, candidate_id: str, deck: Sequence[int] | None = None, seed: int | None = None,
) -> Callable[[object], list[int] | None]:
    _candidate_id(candidate_id)
    implementation = NonMainTargetOverlayAgent(deck=deck, seed=seed)
    # CABT's agent registry expects the same plain-function shape as
    # ``make_rule_agent``.  Keep counters attached for research telemetry while
    # exposing a function-compatible callable to the engine.
    def choose(observation: object) -> list[int] | None:
        return implementation(observation)

    choose.__name__ = f"{candidate_id}_research_only"
    choose.stats = implementation.stats  # type: ignore[attr-defined]
    choose.overlay_implementation = implementation  # type: ignore[attr-defined]
    return choose


def _manifest_summary(slots: Sequence[Mapping[str, object]]) -> dict[str, object]:
    return {
        "source_schedule_quota": 96,
        "slot_count": 48,
        "weighted_quota_sum": 48,
        "heldout_exposure": 0,
        "seat_counts": {
            "0": sum(int(slot["seat"] == 0) for slot in slots),
            "1": sum(int(slot["seat"] == 1) for slot in slots),
        },
        "hard_negative_support": len({str(slot["opponent_id"]) for slot in slots}),
        "action_trace_used": False,
        "private_fields_used": False,
        "teacher_labels_used": False,
        "training_data": False,
    }


def build_non_main_target_screen_v1(
    *, repo_root: Path | str, schedule_path: Path | str,
    candidate_id: str = TARGET_CANDIDATE_ID_V1,
) -> dict[str, object]:
    root = Path(repo_root).resolve()
    candidate_id = _candidate_id(candidate_id)
    target_config = _target_config()
    target_sha = _target_config_sha(target_config)
    schedule_file = _inside(root, schedule_path, "iteration schedule")
    try:
        schedule, confirmation, deck_path, pool_path, broad_path, confirmation_path = _load_inputs(root, schedule_file)
    except Exception as exc:
        raise NonMainTargetOverlayError(f"iteration input verification failed: {exc}") from exc
    root_sha = root_policy_sha256()
    evaluator_sha = evaluator_implementation_sha256_v1()
    if confirmation.get("root_policy_sha256") != root_sha or confirmation.get("control_policy_sha256") != root_sha:
        raise NonMainTargetOverlayError("current Rule v0 root/control identity differs from confirmation")
    if confirmation.get("evaluator_sha256") != evaluator_sha:
        raise NonMainTargetOverlayError("current evaluator identity differs from confirmation")
    seed_base = _parent_seed_base(confirmation)
    slots = select_weighted_slots_v1(schedule, base_seed=seed_base)
    if len(slots) != 48 or sum(int(slot["seat"] == 0) for slot in slots) != 24:
        raise NonMainTargetOverlayError("weighted target slots did not close to 48/24-24")
    train_ids = [str(item) for item in schedule.get("train_ids", [])]
    heldout_ids = [str(item) for item in schedule.get("heldout_ids", [])]
    zero_ids = [str(item["opponent_id"]) for item in schedule.get("entries", []) if int(item["quota"]) == 0]
    candidate_sha = _policy_sha(root_sha, target_sha)
    candidate_config = {
        "candidate_id": candidate_id,
        "target_config": target_config,
        "target_config_sha256": target_sha,
        "config_sha256": hashlib.sha256(_canonical({"candidate_id": candidate_id, "target_config_sha256": target_sha})).hexdigest(),
    }
    control_config = {
        "candidate_id": "baseline-no-pack",
        "target_config": None,
        "target_config_sha256": None,
        "config_sha256": hashlib.sha256(_canonical({"candidate_id": "baseline-no-pack", "target_config_sha256": None})).hexdigest(),
    }
    manifest: dict[str, object] = {
        "schema_version": SCHEMA_V1,
        "phase": PHASE_V1,
        "candidate_id": candidate_id,
        "control_id": "baseline-no-pack",
        "candidate_policy_sha256": candidate_sha,
        "control_policy_sha256": root_sha,
        "root_policy_sha256": root_sha,
        "deck_path": str(deck_path.relative_to(root)),
        "deck_sha256": _file_sha(deck_path),
        "pool_manifest_path": str(pool_path.relative_to(root)),
        "pool_manifest_sha256": _file_sha(pool_path),
        "broad_config_path": str(broad_path.relative_to(root)),
        "broad_config_sha256": _file_sha(broad_path),
        "evaluator_sha256": evaluator_sha,
        "schedule_path": str(schedule_file.relative_to(root)),
        "schedule_file_sha256": _file_sha(schedule_file),
        "schedule_sha256": str(schedule["schedule_sha256"]),
        "confirmation_path": str(confirmation_path.relative_to(root)),
        "confirmation_file_sha256": _file_sha(confirmation_path),
        "confirmation_sha256": str(confirmation["confirmation_sha256"]),
        "seed_base": seed_base,
        "seed_source_confirmation_sha256": str(confirmation["confirmation_sha256"]),
        "train_ids": train_ids,
        "heldout_ids": heldout_ids,
        "zero_quota_ids": zero_ids,
        "slots": list(slots),
        "target_config": target_config,
        "target_config_sha256": target_sha,
        "candidate_config": candidate_config,
        "control_config": control_config,
        "summary": _manifest_summary(slots),
        "runner_ref": RUNNER_REF_V1,
        "authority": dict(_AUTHORITY_FALSE),
        "research_only": True,
        "execution_allowed": False,
        "ready_for_evaluation": True,
    }
    manifest["screen_sha256"] = _semantic_sha(manifest)
    manifest["bridge_sha256"] = manifest["screen_sha256"]
    pool = load_opponent_pool_v1(default_pool_root_v1(root))
    control_games: list[EvaluationGameV1] = []
    candidate_games: list[EvaluationGameV1] = []
    for slot in slots:
        for arm, arm_id, policy_sha, config, enabled in (
            ("control", "baseline-no-pack", root_sha, control_config, False),
            ("candidate", candidate_id, candidate_sha, candidate_config, True),
        ):
            try:
                game = _base_game(
                    arm=arm,
                    arm_id=arm_id,
                    policy_sha=policy_sha,
                    root=root,
                    deck_path=deck_path,
                    pool=pool,
                    slot=slot,
                    manifest=manifest,
                    config=config,
                    pack_path=None,
                    action_deltas={},
                )
            except Exception as exc:
                raise NonMainTargetOverlayError(f"failed to materialize {arm} game: {exc}") from exc
            metadata = dict(game.metadata)
            metadata.update(
                {
                    "schema_version": SCHEMA_V1,
                    "runner_ref": RUNNER_REF_V1,
                    "target_overlay_enabled": enabled,
                    "target_config": target_config if enabled else None,
                    "target_config_sha256": target_sha if enabled else None,
                    "screen_sha256": manifest["screen_sha256"],
                    "bridge_sha256": manifest["bridge_sha256"],
                    "heldout_exposure": 0,
                }
            )
            game = replace(
                game,
                runner_ref=RUNNER_REF_V1,
                game_id=f"{arm}-{SCHEMA_V1}-{slot['stratum_key']}",
                block_id=f"{SCHEMA_V1}-{arm}",
                metadata=metadata,
            )
            (candidate_games if enabled else control_games).append(game)
    return {
        "manifest": manifest,
        "control_games": tuple(control_games),
        "candidate_games": tuple(candidate_games),
    }


def verify_non_main_target_screen_v1(
    manifest: Mapping[str, object], *, repo_root: Path | str,
) -> dict[str, object]:
    if type(manifest) is not dict or set(manifest) != _MANIFEST_KEYS:
        raise NonMainTargetOverlayError("target screen manifest schema is not closed")
    if manifest.get("schema_version") != SCHEMA_V1 or manifest.get("phase") != PHASE_V1:
        raise NonMainTargetOverlayError("target screen schema/phase mismatch")
    if (
        manifest.get("authority") != _AUTHORITY_FALSE
        or manifest.get("research_only") is not True
        or manifest.get("execution_allowed") is not False
        or manifest.get("ready_for_evaluation") is not True
    ):
        raise NonMainTargetOverlayError("target screen authority/readiness is invalid")
    if manifest.get("runner_ref") != RUNNER_REF_V1:
        raise NonMainTargetOverlayError("target screen runner identity mismatch")
    if manifest.get("screen_sha256") != _semantic_sha(manifest) or manifest.get("bridge_sha256") != manifest.get("screen_sha256"):
        raise NonMainTargetOverlayError("target screen semantic SHA mismatch")
    root = Path(repo_root).resolve()
    candidate_id = _candidate_id(manifest.get("candidate_id"))
    target = _target_config(manifest.get("target_config"))
    target_sha = _target_config_sha(target)
    if manifest.get("target_config_sha256") != target_sha:
        raise NonMainTargetOverlayError("target config SHA mismatch")
    schedule_path = _inside(root, str(manifest["schedule_path"]), "target schedule")
    try:
        schedule, confirmation, deck_path, pool_path, broad_path, confirmation_path = _load_inputs(root, schedule_path)
    except Exception as exc:
        raise NonMainTargetOverlayError(f"iteration input verification failed: {exc}") from exc
    if _file_sha(schedule_path) != manifest["schedule_file_sha256"] or schedule.get("schedule_sha256") != manifest["schedule_sha256"]:
        raise NonMainTargetOverlayError("target schedule identity mismatch")
    if _file_sha(confirmation_path) != manifest["confirmation_file_sha256"] or confirmation.get("confirmation_sha256") != manifest["confirmation_sha256"]:
        raise NonMainTargetOverlayError("target confirmation identity mismatch")
    if str(deck_path.relative_to(root)) != manifest["deck_path"] or _file_sha(deck_path) != manifest["deck_sha256"]:
        raise NonMainTargetOverlayError("target deck identity mismatch")
    if str(pool_path.relative_to(root)) != manifest["pool_manifest_path"] or _file_sha(pool_path) != manifest["pool_manifest_sha256"]:
        raise NonMainTargetOverlayError("target pool identity mismatch")
    if str(broad_path.relative_to(root)) != manifest["broad_config_path"] or _file_sha(broad_path) != manifest["broad_config_sha256"]:
        raise NonMainTargetOverlayError("target broad config identity mismatch")
    root_sha = root_policy_sha256()
    evaluator_sha = evaluator_implementation_sha256_v1()
    if manifest["root_policy_sha256"] != root_sha or manifest["control_policy_sha256"] != root_sha:
        raise NonMainTargetOverlayError("target root/control policy identity mismatch")
    if manifest["evaluator_sha256"] != evaluator_sha or confirmation.get("evaluator_sha256") != evaluator_sha:
        raise NonMainTargetOverlayError("target evaluator identity mismatch")
    if confirmation.get("root_policy_sha256") != root_sha or confirmation.get("control_policy_sha256") != root_sha:
        raise NonMainTargetOverlayError("target confirmation root identity mismatch")
    expected_seed = _parent_seed_base(confirmation)
    if manifest.get("seed_base") != expected_seed or manifest.get("seed_source_confirmation_sha256") != manifest.get("confirmation_sha256"):
        raise NonMainTargetOverlayError("target seed identity mismatch")
    expected_slots = select_weighted_slots_v1(schedule, base_seed=expected_seed)
    if manifest.get("slots") != list(expected_slots):
        raise NonMainTargetOverlayError("target weighted slots identity mismatch")
    if manifest.get("train_ids") != [str(item) for item in schedule.get("train_ids", [])] or manifest.get("heldout_ids") != [str(item) for item in schedule.get("heldout_ids", [])]:
        raise NonMainTargetOverlayError("target train/heldout identity mismatch")
    expected_zero = [str(item["opponent_id"]) for item in schedule.get("entries", []) if int(item["quota"]) == 0]
    if manifest.get("zero_quota_ids") != expected_zero:
        raise NonMainTargetOverlayError("target zero-quota identity mismatch")
    if any(slot.get("split") != "META_TRAIN" for slot in expected_slots):
        raise NonMainTargetOverlayError("target slots contain heldout exposure")
    expected_candidate_sha = _policy_sha(root_sha, target_sha)
    if manifest.get("candidate_policy_sha256") != expected_candidate_sha:
        raise NonMainTargetOverlayError("target candidate policy identity mismatch")
    if manifest.get("control_policy_sha256") != root_sha:
        raise NonMainTargetOverlayError("target control policy identity mismatch")
    expected_candidate_config = {
        "candidate_id": candidate_id,
        "target_config": target,
        "target_config_sha256": target_sha,
        "config_sha256": hashlib.sha256(_canonical({"candidate_id": candidate_id, "target_config_sha256": target_sha})).hexdigest(),
    }
    expected_control_config = {
        "candidate_id": "baseline-no-pack",
        "target_config": None,
        "target_config_sha256": None,
        "config_sha256": hashlib.sha256(_canonical({"candidate_id": "baseline-no-pack", "target_config_sha256": None})).hexdigest(),
    }
    if manifest.get("candidate_config") != expected_candidate_config or manifest.get("control_config") != expected_control_config:
        raise NonMainTargetOverlayError("target candidate/control config identity mismatch")
    if manifest.get("summary") != _manifest_summary(expected_slots):
        raise NonMainTargetOverlayError("target summary identity mismatch")
    return dict(manifest)


def run_non_main_target_overlay_game_v1(payload: Mapping[str, object]) -> Mapping[str, object]:
    """Spawn-safe worker entrypoint for one control/candidate game."""
    from scripts.parallel_cabt_evaluator_v1 import _game_from_payload

    game = _game_from_payload(payload)
    metadata = game.metadata
    if metadata.get("schema_version") != SCHEMA_V1:
        raise NonMainTargetOverlayError("target worker schema mismatch")
    if metadata.get("heldout_exposure") != 0 or metadata.get("opponent_usage_boundary") != "local_eval_only":
        raise NonMainTargetOverlayError("target worker permission/heldout boundary mismatch")
    root = Path(__file__).resolve().parents[3]
    pool = load_opponent_pool_v1(default_pool_root_v1(root))
    opponent = resolve_opponent_v1(pool, game.opponent_id, subject_deck_csv_path=game.subject_deck_path)
    if opponent.usage_boundary != "local_eval_only" or str(opponent.source).lower() == "synthetic":
        raise NonMainTargetOverlayError("target worker opponent is not permission-safe local evaluation")
    enabled = metadata.get("target_overlay_enabled") is True
    expected_target_sha = _target_config_sha() if enabled else None
    if metadata.get("target_config_sha256") != expected_target_sha:
        raise NonMainTargetOverlayError("target worker config SHA mismatch")
    subject_factory: Callable[[object, int], object]
    if enabled:
        subject_factory = lambda deck, seed: build_non_main_target_agent(
            candidate_id=TARGET_CANDIDATE_ID_V1, deck=deck, seed=seed
        )
        subject_name = TARGET_CANDIDATE_ID_V1
    else:
        subject_factory = lambda deck, seed: make_rule_agent(deck=deck, seed=seed)
        subject_name = "baseline-no-pack"
    opponent_factory = build_opponent_agent_factory_v1(opponent)
    subject_first = game.seat == 0
    return run_match(
        deck_a_path=game.subject_deck_path if subject_first else opponent.deck_csv_path,
        deck_b_path=opponent.deck_csv_path if subject_first else game.subject_deck_path,
        agent_a_name=subject_name if subject_first else game.opponent_id,
        agent_b_name=game.opponent_id if subject_first else subject_name,
        seed=game.seed,
        max_steps=game.max_steps,
        output_dir=str(root / "runs" / "non-main-target-overlay-worker" / game.game_id),
        save_html=False,
        save_result=False,
        agent_a_factory=subject_factory if subject_first else opponent_factory,
        agent_b_factory=opponent_factory if subject_first else subject_factory,
    )


__all__ = [
    "NON_MAIN_TARGET_CONFIG_V1",
    "NonMainTargetOverlayAgent",
    "NonMainTargetOverlayError",
    "RUNNER_REF_V1",
    "SCHEMA_V1",
    "build_non_main_target_agent",
    "build_non_main_target_screen_v1",
    "verify_non_main_target_screen_v1",
    "run_non_main_target_overlay_game_v1",
]
