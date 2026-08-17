"""Research-only Rule v0 ``ABILITY+120`` weighted screen bridge.

The bridge is deliberately narrower than the production agent: it wraps the
existing public-state Rule v0 action ranking, changes only the score of legal
``ABILITY`` MAIN options, and otherwise returns the exact Rule v0 answer.  It
materializes a 48-cell META_TRAIN schedule paired with an unchanged control;
it never executes a game itself and carries no training, promotion, or
submission authority.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from mage_ptcg.meta_specialist.opponent_pool_v1 import (
    build_opponent_agent_factory_v1,
    default_pool_root_v1,
    load_opponent_pool_v1,
    resolve_opponent_v1,
)
from main import make_rule_agent  # research-only adapter; production main is unchanged
from scripts.parallel_cabt_evaluator_v1 import EvaluationGameV1, evaluator_implementation_sha256_v1
from scripts.run_performance_first_arena_v1 import root_policy_sha256
from scripts.run_rule_v0_knowledge_pool_screen_v1 import (
    DEFAULT_POOL_MANIFEST,
    RUNNER_REF_V1,
    _candidate_policy_sha,
    _sha256,
)
from scripts.test_sim import run_match


ABILITY_RUNNER_REF_V1 = "scripts.run_rule_v0_main_ability_weighted48_v1:run_rule_v0_main_ability_weighted48_game_v1"
_ACTION_TYPE_NAMES = {7: "PLAY", 8: "ATTACH", 9: "EVOLVE", 10: "ABILITY", 13: "ATTACK", 14: "END"}


SCHEMA_V1 = "meta-specialist-rule-v0-main-ability-weighted48-v1"
PHASE_V1 = "MAIN_ABILITY_WEIGHTED48"
CANDIDATE_ID_V1 = "rule-v0-main-ability-plus-120-v1"
CONTROL_ID_V1 = "rule-v0-main-baseline-v1"
ROOT_DECK = Path(__file__).resolve().parents[3] / "deck.csv"
DEFAULT_SCHEDULE = (
    Path(__file__).resolve().parents[3]
    / "runs/final-sprint-autonomous/v4-seed1-outcome-only-hard-negative-iteration1-20260813/schedule.json"
)
DEFAULT_BROAD_CONFIG = Path(__file__).resolve().parents[3] / "configs/meta_specialist/performance_first_broad_pool_v1.json"
SEED_BASE_V1 = 14910000
_SHA_HEX = frozenset("0123456789abcdef")
_AUTHORITY_FALSE = {
    "training_authority": False,
    "promotion_authority": False,
    "submission_authority": False,
    "external_execution_authority": False,
    "longrun_authority": False,
}


class RuleV0MainAbilityWeighted48Error(ValueError):
    """Raised when the closed ABILITY screen contract is invalid."""


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise RuleV0MainAbilityWeighted48Error(f"value is not canonical JSON: {exc}") from exc


def _sha(value: object, field: str) -> str:
    if type(value) is not str or len(value) != 64 or any(c not in _SHA_HEX for c in value):
        raise RuleV0MainAbilityWeighted48Error(f"{field} must be a lowercase SHA-256")
    return value


def _inside(root: Path, value: Path | str, field: str) -> Path:
    candidate = Path(value)
    path = (root / candidate).resolve() if not candidate.is_absolute() else candidate.resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise RuleV0MainAbilityWeighted48Error(f"{field} escapes repo root") from exc
    if not path.is_file():
        raise RuleV0MainAbilityWeighted48Error(f"{field} is not a file: {path}")
    return path


def _schedule_sha(schedule: Mapping[str, object]) -> str:
    value = schedule.get("schedule_sha256")
    return _sha(value, "schedule_sha256")


def _validate_schedule(schedule: Mapping[str, object], *, schedule_file: Path) -> dict[str, object]:
    """Verify the source's sealed invariants without trusting its weights."""
    if not isinstance(schedule, Mapping):
        raise RuleV0MainAbilityWeighted48Error("schedule must be an object")
    if not bool(schedule.get("research_only")) or schedule.get("ready_for_evaluation") is not True:
        raise RuleV0MainAbilityWeighted48Error("schedule is not research-only evaluation-ready")
    authority = schedule.get("authority")
    if not isinstance(authority, Mapping) or any(value is not False for value in authority.values()):
        raise RuleV0MainAbilityWeighted48Error("schedule authority is not false")
    if int(schedule.get("quota", -1)) != 96:
        raise RuleV0MainAbilityWeighted48Error("schedule quota must be exactly 96")
    entries = schedule.get("entries")
    if not isinstance(entries, list) or not entries:
        raise RuleV0MainAbilityWeighted48Error("schedule entries are malformed")
    entry_ids: list[str] = []
    quota_sum = 0
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise RuleV0MainAbilityWeighted48Error("schedule entry is malformed")
        oid = entry.get("opponent_id")
        quota = entry.get("quota")
        weight = entry.get("weight")
        if type(oid) is not str or not oid or type(quota) is not int or quota < 0:
            raise RuleV0MainAbilityWeighted48Error("schedule entry identity/quota is malformed")
        if type(weight) not in (int, float) or isinstance(weight, bool):
            raise RuleV0MainAbilityWeighted48Error("schedule entry weight is malformed")
        if entry.get("split") != "META_TRAIN":
            raise RuleV0MainAbilityWeighted48Error("heldout entry reached ABILITY bridge")
        if entry.get("usage_boundary") != "local_eval_only":
            raise RuleV0MainAbilityWeighted48Error("schedule opponent boundary is not local_eval_only")
        if entry.get("training_exposure_allowed") is not False or entry.get("teacher_behavior_allowed") is not False:
            raise RuleV0MainAbilityWeighted48Error("schedule exposes training or teacher behavior")
        if entry.get("evaluation_allowed") is not True:
            raise RuleV0MainAbilityWeighted48Error("schedule entry is not evaluation-allowed")
        entry_ids.append(oid)
        quota_sum += quota
    if len(set(entry_ids)) != len(entry_ids) or quota_sum != 96:
        raise RuleV0MainAbilityWeighted48Error("schedule entry IDs or quota sum mismatch")
    train_ids = schedule.get("train_ids")
    heldout_ids = schedule.get("heldout_ids")
    if not isinstance(train_ids, list) or set(train_ids) != set(entry_ids) or len(train_ids) != len(entry_ids) or not isinstance(heldout_ids, list) or set(train_ids) & set(heldout_ids):
        raise RuleV0MainAbilityWeighted48Error("schedule train/heldout identity mismatch")
    summary = schedule.get("summary")
    if not isinstance(summary, Mapping) or summary.get("heldout_exposure") != 0:
        raise RuleV0MainAbilityWeighted48Error("schedule summary has heldout exposure")
    for key in ("action_trace_used", "private_fields_used", "teacher_labels_used", "training_data"):
        if summary.get(key) is not False:
            raise RuleV0MainAbilityWeighted48Error(f"schedule summary uses forbidden field: {key}")
    if not schedule_file.is_file():
        raise RuleV0MainAbilityWeighted48Error("schedule file is missing")
    return dict(schedule)


def build_rule_v0_main_ability_agent_v1(*, deck: list[int] | None = None, seed: int | None = None):
    """Return the public-state-only ABILITY overlay with exact Rule fallback."""
    from agents.rule_agent import rank_rule_indices

    baseline = make_rule_agent(deck=deck, seed=seed)
    telemetry: dict[str, int] = {
        "observations": 0,
        "main_observations": 0,
        "eligible_main_observations": 0,
        "override_attempts": 0,
        "override_applied": 0,
        "fallback_count": 0,
    }

    def choose(observation: dict) -> list[int]:
        telemetry["observations"] += 1
        fallback = baseline(observation)
        try:
            select = observation.get("select")
            if not isinstance(select, Mapping):
                return fallback
            selection_type = select.get("type")
            if not (selection_type == 0 or str(selection_type).rsplit(".", 1)[-1].upper() == "MAIN"):
                return fallback
            telemetry["main_observations"] += 1
            options = select.get("option")
            if not isinstance(options, list) or not options:
                telemetry["fallback_count"] += 1
                return fallback
            legal_ability = any(isinstance(option, Mapping) and option.get("type") == 10 for option in options)
            if legal_ability:
                telemetry["eligible_main_observations"] += 1
            ranked = rank_rule_indices(observation)
            raw_min = select.get("minCount", 0)
            raw_max = select.get("maxCount", 0)
            if not ranked or type(raw_min) is not int or type(raw_max) is not int or raw_min < 0 or raw_max < raw_min:
                telemetry["fallback_count"] += 1
                return fallback
            count = raw_min if raw_min else 1
            if count > raw_max or count > len(options):
                telemetry["fallback_count"] += 1
                return fallback
            adjusted: list[tuple[int, float, int]] = []
            for index, base_score in ranked:
                if type(index) is not int or index < 0 or index >= len(options):
                    telemetry["fallback_count"] += 1
                    return fallback
                option = options[index]
                if not isinstance(option, Mapping) or type(option.get("type")) is not int:
                    telemetry["fallback_count"] += 1
                    return fallback
                name = _ACTION_TYPE_NAMES.get(option["type"])
                adjusted.append((index, float(base_score) + (120.0 if name == "ABILITY" else 0.0), index))
            adjusted.sort(key=lambda item: (-item[1], item[2]))
            selected = [item[0] for item in adjusted[:count]]
            if len(selected) != count or len(set(selected)) != count:
                telemetry["fallback_count"] += 1
                return fallback
            telemetry["override_attempts"] += 1
            if selected != fallback:
                telemetry["override_applied"] += 1
            return selected
        except (AttributeError, KeyError, TypeError, ValueError, IndexError):
            telemetry["fallback_count"] += 1
            return fallback

    choose.telemetry = telemetry  # type: ignore[attr-defined]
    choose.telemetry_schema = "rule-v0-main-ability-telemetry-v1"  # type: ignore[attr-defined]
    return choose


def _telemetry_snapshot(agent: object) -> dict[str, object]:
    raw = getattr(agent, "telemetry", None)
    if not isinstance(raw, Mapping):
        return {
            "schema_version": "rule-v0-main-ability-telemetry-v1",
            "available": False,
            "reason": "baseline_agent_has_no_overlay_telemetry",
        }
    return {"schema_version": str(getattr(agent, "telemetry_schema", "rule-v0-main-ability-telemetry-v1")), "available": True, **{str(k): int(v) for k, v in raw.items()}}


def run_rule_v0_main_ability_weighted48_game_v1(payload: Mapping[str, object]) -> Mapping[str, object]:
    """Run one game and return bounded candidate telemetry in the raw result."""
    from scripts.parallel_cabt_evaluator_v1 import _game_from_payload

    game = _game_from_payload(payload)
    pool = load_opponent_pool_v1(default_pool_root_v1(Path(__file__).resolve().parents[3]))
    opponent = resolve_opponent_v1(pool, game.opponent_id, subject_deck_csv_path=game.subject_deck_path)
    opponent_factory = build_opponent_agent_factory_v1(opponent)
    created: list[object] = []
    candidate = game.metadata.get("arm") == "candidate"

    def subject_factory(deck: object, game_seed: int):
        agent = build_rule_v0_main_ability_agent_v1(deck=deck, seed=game_seed) if candidate else make_rule_agent(deck=deck, seed=game_seed)
        created.append(agent)
        return agent

    subject_first = game.seat == 0
    result = run_match(
        deck_a_path=game.subject_deck_path if subject_first else opponent.deck_csv_path,
        deck_b_path=opponent.deck_csv_path if subject_first else game.subject_deck_path,
        agent_a_name=f"rule-v0-{game.metadata.get('candidate_id', 'baseline')}" if subject_first else game.opponent_id,
        agent_b_name=game.opponent_id if subject_first else f"rule-v0-{game.metadata.get('candidate_id', 'baseline')}",
        seed=game.seed,
        max_steps=game.max_steps,
        output_dir=str(Path(__file__).resolve().parents[3] / "runs" / "rule-v0-main-ability-worker" / game.game_id),
        save_html=False,
        save_result=False,
        agent_a_factory=subject_factory if subject_first else opponent_factory,
        agent_b_factory=opponent_factory if subject_first else subject_factory,
    )
    if not isinstance(result, Mapping):
        raise RuleV0MainAbilityWeighted48Error("run_match returned a non-mapping result")
    output = dict(result)
    snapshots = [_telemetry_snapshot(agent) for agent in created]
    aggregate: dict[str, object] = {"schema_version": "rule-v0-main-ability-telemetry-v1", "available": bool(snapshots and snapshots[0].get("available")), "agent_count": len(snapshots), "agents": snapshots}
    output["ability_telemetry"] = aggregate
    # The shared research evaluator preserves the bounded ``rewards`` field
    # but intentionally drops arbitrary runner keys.  Carry the same
    # telemetry there so each ledger row retains the per-game counters without
    # editing the evaluator or production runtime.
    output["rewards"] = {"ability_telemetry": aggregate, "raw_rewards": output.get("rewards")}
    return output


def _all_slots(schedule: Mapping[str, object]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    ordinal = 0
    for entry in sorted(schedule["entries"], key=lambda e: str(e["opponent_id"])):
        oid = str(entry["opponent_id"])
        for local in range(int(entry["quota"])):
            rows.append({
                "slot_index": ordinal,
                "stratum_key": f"{oid}-slot-{local:03d}",
                "opponent_id": oid,
                "seat": ordinal % 2,
                "repetition": local // 2,
                "seed": SEED_BASE_V1 + ordinal,
                "schedule_weight": float(entry["weight"]),
                "schedule_quota": int(entry["quota"]),
                "split": "META_TRAIN",
            })
            ordinal += 1
    return rows


def _selected_slots(schedule: Mapping[str, object], *, base_seed: int = SEED_BASE_V1) -> list[dict[str, object]]:
    if type(base_seed) is not int or base_seed < 0:
        raise RuleV0MainAbilityWeighted48Error("base_seed must be a nonnegative integer")
    all_rows = _all_slots(schedule)
    # Preserve each opponent's deterministic floor, then allocate the 4
    # remaining cells by descending sealed weight.  The final global order
    # keeps the two subject seats exactly balanced.
    quotas = {str(e["opponent_id"]): int(e["quota"]) for e in schedule["entries"]}
    keep = {oid: q // 2 for oid, q in quotas.items()}
    remaining = 48 - sum(keep.values())
    ranked = sorted(schedule["entries"], key=lambda e: (-float(e["weight"]), str(e["opponent_id"])))
    for entry in ranked[:remaining]:
        keep[str(entry["opponent_id"])] += 1
    seen = {oid: 0 for oid in keep}
    rows = []
    for row in all_rows:
        oid = row["opponent_id"]
        if seen[oid] < keep[oid]:
            rows.append(row)
            seen[oid] += 1
    rows.sort(key=lambda row: (int(row["slot_index"])))
    # Re-number the selected slots/seeds so candidate/control are the exact
    # same 48-cell universe and no accidental gap is treated as a game.
    seat_repetitions = {oid: {0: 0, 1: 0} for oid in keep}
    for index, row in enumerate(rows):
        row["slot_index"] = index
        row["seed"] = base_seed + index
        row["seat"] = index % 2
        row["repetition"] = seat_repetitions[row["opponent_id"]][row["seat"]]
        seat_repetitions[row["opponent_id"]][row["seat"]] += 1
    if len(rows) != 48 or sum(int(r["seat"] == 0) for r in rows) != 24 or sum(int(r["seat"] == 1) for r in rows) != 24:
        raise RuleV0MainAbilityWeighted48Error("selected weighted48 slots are not balanced")
    return rows


def _config(candidate_id: str, deltas: Mapping[str, float], *, scope: str, fallback: str) -> dict[str, object]:
    body = {
        "candidate_id": candidate_id,
        "scope": scope,
        "fallback": fallback,
        "action_deltas": dict(deltas),
        "public_fields": ["select.type", "select.option[].type", "select.option[].index"],
    }
    return {**body, "config_sha256": hashlib.sha256(_canonical(body)).hexdigest()}


def _game(*, arm: str, arm_id: str, policy_sha: str, root: Path, deck: Path, pool: Mapping[str, object], slot: Mapping[str, object], manifest: Mapping[str, object], config: Mapping[str, object], deltas: Mapping[str, float]) -> EvaluationGameV1:
    opponent_id = str(slot["opponent_id"])
    opponent = resolve_opponent_v1(pool, opponent_id, subject_deck_csv_path=str(deck))
    if opponent.usage_boundary != "local_eval_only" or str(opponent.source).lower() == "synthetic":
        raise RuleV0MainAbilityWeighted48Error("opponent is not a permission-safe local evaluation asset")
    opponent_deck_sha = _sha256(opponent.deck_csv_path)
    metadata = {
        "schema_version": SCHEMA_V1,
        "phase": PHASE_V1,
        "arm": arm,
        "candidate_id": arm_id,
        "candidate_policy_sha256": policy_sha,
        "root_policy_sha256": manifest["root_policy_sha256"],
        "candidate_config_sha256": config["config_sha256"],
        "action_deltas": dict(deltas),
        "scope": "MAIN_ONLY",
        "fallback": "RULE_V0_EXACT",
        "screen_sha256": manifest["screen_sha256"],
        "schedule_file_sha256": manifest["schedule_file_sha256"],
        "schedule_sha256": manifest["schedule_sha256"],
        "pool_manifest_sha256": manifest["pool_manifest_sha256"],
        "broad_config_sha256": manifest["broad_config_sha256"],
        "evaluator_sha256": manifest["evaluator_sha256"],
        "opponent_usage_boundary": opponent.usage_boundary,
        "opponent_source": opponent.source,
        "synthetic_opponent": False,
        "heldout_exposure": 0,
        **dict(slot),
    }
    return EvaluationGameV1(
        game_id=f"{arm}-{SCHEMA_V1}-{slot['stratum_key']}",
        block_id=f"{SCHEMA_V1}-{arm}",
        policy_id=f"rule-v0-{arm_id}",
        policy_sha256=policy_sha,
        deck_id="root-deck-current-worktree",
        deck_sha256=_sha256(deck),
        opponent_id=opponent_id,
        opponent_identity={"policy_sha256": _sha256(opponent.policy_path), "deck_sha256": opponent_deck_sha, "usage_boundary": opponent.usage_boundary, "source": opponent.source, "meta_split": "META_TRAIN"},
        opponent_deck_sha256=opponent_deck_sha,
        seat=int(slot["seat"]),
        seed=int(slot["seed"]),
        max_steps=2000,
        subject_deck_path=str(deck),
        opponent_deck_path=str(opponent.deck_csv_path),
        policy_agent_name=f"rule-v0-{arm_id}",
        opponent_agent_name=opponent_id,
        runner_ref=ABILITY_RUNNER_REF_V1,
        metadata=metadata,
    )


def _screen_sha(manifest: Mapping[str, object]) -> str:
    body = {k: v for k, v in manifest.items() if k != "screen_sha256"}
    return hashlib.sha256((SCHEMA_V1 + "\0").encode("ascii") + _canonical(body)).hexdigest()


def build_rule_v0_main_ability_weighted48_v1(*, repo_root: Path | str, schedule_path: Path | str = DEFAULT_SCHEDULE, candidate_id: str = CANDIDATE_ID_V1, base_seed: int = SEED_BASE_V1) -> dict[str, object]:
    root = Path(repo_root).resolve()
    if candidate_id != CANDIDATE_ID_V1:
        raise RuleV0MainAbilityWeighted48Error("candidate_id is not the sealed ABILITY+120 identity")
    schedule_file = _inside(root, schedule_path, "schedule")
    try:
        schedule = _validate_schedule(json.loads(schedule_file.read_text(encoding="utf-8")), schedule_file=schedule_file)
    except (OSError, json.JSONDecodeError) as exc:
        raise RuleV0MainAbilityWeighted48Error(f"schedule cannot be loaded: {exc}") from exc
    deck = _inside(root, ROOT_DECK, "subject deck")
    pool_path = _inside(root, DEFAULT_POOL_MANIFEST, "opponent pool")
    broad = _inside(root, DEFAULT_BROAD_CONFIG, "broad config")
    root_sha = root_policy_sha256()
    evaluator_sha = evaluator_implementation_sha256_v1()
    pool = load_opponent_pool_v1(default_pool_root_v1(root))
    slots = _selected_slots(schedule, base_seed=base_seed)
    deltas = {"ABILITY": 120.0}
    candidate_sha = _candidate_policy_sha(root_sha, None, deltas)
    candidate_config = _config(CANDIDATE_ID_V1, deltas, scope="MAIN_ONLY", fallback="RULE_V0_EXACT")
    control_config = _config(CONTROL_ID_V1, {}, scope="RULE_V0_BASELINE", fallback="RULE_V0_EXACT")
    manifest: dict[str, object] = {
        "schema_version": SCHEMA_V1,
        "phase": PHASE_V1,
        "candidate_id": CANDIDATE_ID_V1,
        "control_id": CONTROL_ID_V1,
        "action_deltas": deltas,
        "candidate_policy_sha256": candidate_sha,
        "control_policy_sha256": root_sha,
        "root_policy_sha256": root_sha,
        "deck_path": str(deck.relative_to(root)),
        "deck_sha256": _sha256(deck),
        "pool_manifest_path": str(pool_path.relative_to(root)),
        "pool_manifest_sha256": _sha256(pool_path),
        "broad_config_path": str(broad.relative_to(root)),
        "broad_config_sha256": _sha256(broad),
        "evaluator_sha256": evaluator_sha,
        "schedule_path": str(schedule_file.relative_to(root)),
        "schedule_file_sha256": _sha256(schedule_file),
        "schedule_sha256": _schedule_sha(schedule),
        "runner_ref": ABILITY_RUNNER_REF_V1,
        "candidate_config": candidate_config,
        "control_config": control_config,
        "train_ids": list(schedule["train_ids"]),
        "heldout_ids": list(schedule["heldout_ids"]),
        "slots": slots,
        "seed_base": base_seed,
        "summary": {"slot_count": 48, "seat_counts": {"0": 24, "1": 24}, "heldout_exposure": 0, "family_count": len({str(x["opponent_id"]) for x in slots}), "coverage_gate": "telemetry_not_available_in_existing_runner"},
        "authority": dict(_AUTHORITY_FALSE),
        "research_only": True,
        "execution_allowed": False,
        "ready_for_evaluation": True,
    }
    manifest["screen_sha256"] = _screen_sha(manifest)
    candidate_games = tuple(_game(arm="candidate", arm_id=CANDIDATE_ID_V1, policy_sha=candidate_sha, root=root, deck=deck, pool=pool, slot=slot, manifest=manifest, config=candidate_config, deltas=deltas) for slot in slots)
    control_games = tuple(_game(arm="control", arm_id=CONTROL_ID_V1, policy_sha=root_sha, root=root, deck=deck, pool=pool, slot=slot, manifest=manifest, config=control_config, deltas={}) for slot in slots)
    return {"manifest": manifest, "candidate_games": candidate_games, "control_games": control_games}


def verify_rule_v0_main_ability_weighted48_v1(manifest: Mapping[str, object], *, repo_root: Path | str) -> dict[str, object]:
    if not isinstance(manifest, Mapping):
        raise RuleV0MainAbilityWeighted48Error("manifest must be an object")
    if manifest.get("schema_version") != SCHEMA_V1 or manifest.get("phase") != PHASE_V1:
        raise RuleV0MainAbilityWeighted48Error("manifest schema/phase mismatch")
    if manifest.get("authority") != _AUTHORITY_FALSE or manifest.get("research_only") is not True or manifest.get("execution_allowed") is not False:
        raise RuleV0MainAbilityWeighted48Error("manifest authority is not research-only")
    if manifest.get("screen_sha256") != _screen_sha(manifest):
        raise RuleV0MainAbilityWeighted48Error("manifest semantic SHA mismatch")
    root = Path(repo_root).resolve()
    schedule_file = _inside(root, str(manifest.get("schedule_path")), "schedule")
    schedule = _validate_schedule(json.loads(schedule_file.read_text(encoding="utf-8")), schedule_file=schedule_file)
    if _sha256(schedule_file) != manifest.get("schedule_file_sha256") or _schedule_sha(schedule) != manifest.get("schedule_sha256"):
        raise RuleV0MainAbilityWeighted48Error("schedule identity mismatch")
    deck = _inside(root, str(manifest.get("deck_path")), "subject deck")
    pool = _inside(root, str(manifest.get("pool_manifest_path")), "opponent pool")
    broad = _inside(root, str(manifest.get("broad_config_path")), "broad config")
    checks = (("deck_sha256", deck), ("pool_manifest_sha256", pool), ("broad_config_sha256", broad))
    for field, path in checks:
        if _sha256(path) != manifest.get(field):
            raise RuleV0MainAbilityWeighted48Error(f"{field} identity mismatch")
    if manifest.get("root_policy_sha256") != root_policy_sha256() or manifest.get("control_policy_sha256") != root_policy_sha256():
        raise RuleV0MainAbilityWeighted48Error("root/control policy identity mismatch")
    if manifest.get("candidate_id") != CANDIDATE_ID_V1 or manifest.get("action_deltas") != {"ABILITY": 120.0}:
        raise RuleV0MainAbilityWeighted48Error("ABILITY candidate identity mismatch")
    if manifest.get("candidate_policy_sha256") != _candidate_policy_sha(root_policy_sha256(), None, {"ABILITY": 120.0}):
        raise RuleV0MainAbilityWeighted48Error("candidate policy identity mismatch")
    seed_base = manifest.get("seed_base", SEED_BASE_V1)
    slots = _selected_slots(schedule, base_seed=seed_base)
    if manifest.get("slots") != slots or manifest.get("summary", {}).get("slot_count") != 48:
        raise RuleV0MainAbilityWeighted48Error("weighted48 slot identity mismatch")
    if manifest.get("train_ids") != schedule.get("train_ids") or manifest.get("heldout_ids") != schedule.get("heldout_ids"):
        raise RuleV0MainAbilityWeighted48Error("train/heldout identity mismatch")
    return dict(manifest)


__all__ = [
    "CANDIDATE_ID_V1",
    "PHASE_V1",
    "SCHEMA_V1",
    "RuleV0MainAbilityWeighted48Error",
    "build_rule_v0_main_ability_agent_v1",
    "build_rule_v0_main_ability_weighted48_v1",
    "verify_rule_v0_main_ability_weighted48_v1",
]
