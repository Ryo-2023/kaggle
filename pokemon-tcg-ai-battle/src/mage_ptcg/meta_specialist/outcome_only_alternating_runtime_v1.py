"""Outcome-only, research-only deck/policy alternating runtime.

This module is the executable layer missing from the older alternating state
contracts.  It deliberately consumes only candidate identities and terminal
WDL rows.  It does not infer teacher actions, read private observations, or
grant promotion/training/submission authority.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

from scripts.parallel_cabt_evaluator_v1 import (
    DEFAULT_MAX_WORKERS_V1,
    DEFAULT_WORKER_RECYCLE_GAMES_V1,
    EvaluationGameV1,
    aggregate_ledger_v1,
    evaluation_implementation_sha256_v1,
    run_parallel_cabt_evaluation,
)
from scripts.run_native_policy_candidate_pilot_v1 import (
    RUNNER_REF_V1,
    build_native_candidate_games_v1,
)
from mage_ptcg.meta_specialist.opponent_pool_v1 import load_opponent_pool_v1


OUTCOME_ONLY_ALTERNATING_RUNTIME_SCHEMA_V1 = (
    "meta-specialist-outcome-only-alternating-runtime-v1"
)
OUTCOME_ONLY_ALTERNATING_STAGE_SCHEMA_V1 = (
    "meta-specialist-outcome-only-alternating-stage-v1"
)
ALTERNATING_STAGE_GAMES_V1 = (96, 384, 768, 1536)
POLICY_FIXED_SHORT_V1 = "POLICY_FIXED_SHORT"
DECK_FIXED_LONG_V1 = "DECK_FIXED_LONG"
PHASES_V1 = frozenset({POLICY_FIXED_SHORT_V1, DECK_FIXED_LONG_V1})
AUTHORITY_FALSE_V1 = {
    "execute_allowed": False,
    "training_allowed": False,
    "promotion_allowed": False,
    "submission_allowed": False,
    "longrun_allowed": False,
}
_SHA_CHARS = frozenset("0123456789abcdef")
_WDL = {"win": 1.0, "draw": 0.5, "loss": 0.0}


class OutcomeOnlyAlternatingRuntimeError(ValueError):
    """Raised when the runtime cannot prove a closed stage contract."""


def _sha(value: object, name: str) -> str:
    if type(value) is not str or len(value) != 64 or any(c not in _SHA_CHARS for c in value):
        raise OutcomeOnlyAlternatingRuntimeError(f"{name} must be a lowercase SHA-256")
    return value


def _text(value: object, name: str) -> str:
    if type(value) is not str or not value.strip():
        raise OutcomeOnlyAlternatingRuntimeError(f"{name} must be a non-empty string")
    return value


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(
            value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise OutcomeOnlyAlternatingRuntimeError("value is not canonical JSON") from exc


def _sha_file(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise OutcomeOnlyAlternatingRuntimeError(f"cannot hash source: {path}") from exc


def _config_sha(env: Mapping[str, str], biases: Mapping[str, float], min_score_gain: float) -> str:
    payload = {
        "env": dict(sorted((str(key), str(value)) for key, value in env.items())),
        "biases": dict(sorted((str(key), float(value)) for key, value in biases.items())),
        "min_score_gain": float(min_score_gain),
    }
    return hashlib.sha256(
        b"mage-ptcg:outcome-only-alternating-candidate-config:v1\0" + _canonical(payload)
    ).hexdigest()


def _validate_env(value: object) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise OutcomeOnlyAlternatingRuntimeError("candidate env must be an object")
    allowed = {"USE_SEARCH", "SP_BUDGET", "BEAM_CAND", "BEAM_MAXD", "BEAM_MARGIN", "POKE_SEARCH_BUDGET"}
    result: dict[str, str] = {}
    for key, item in value.items():
        if type(key) is not str or key not in allowed:
            raise OutcomeOnlyAlternatingRuntimeError(f"unsupported candidate env key: {key!r}")
        if type(item) not in (str, int, float) or isinstance(item, bool):
            raise OutcomeOnlyAlternatingRuntimeError(f"candidate env[{key}] is not scalar")
        text = str(item)
        if not text or len(text) > 32:
            raise OutcomeOnlyAlternatingRuntimeError(f"candidate env[{key}] is malformed")
        result[key] = text
    return dict(sorted(result.items()))


def _validate_biases(value: object) -> dict[str, float]:
    if not isinstance(value, Mapping):
        raise OutcomeOnlyAlternatingRuntimeError("candidate biases must be an object")
    result: dict[str, float] = {}
    for key, item in value.items():
        if type(key) is not str or not key.strip():
            raise OutcomeOnlyAlternatingRuntimeError("candidate bias keys must be non-empty")
        if type(item) not in (int, float) or isinstance(item, bool) or not math.isfinite(float(item)):
            raise OutcomeOnlyAlternatingRuntimeError("candidate bias values must be finite")
        if abs(float(item)) > 1000.0:
            raise OutcomeOnlyAlternatingRuntimeError("candidate bias exceeds bounded range")
        result[key.strip().upper()] = float(item)
    return dict(sorted(result.items()))


@dataclass(frozen=True, slots=True)
class OutcomeOnlyCandidateSpecV1:
    """Hash-bound policy/deck identity for one alternating arm."""

    candidate_id: str
    main_path: Path
    deck_path: Path
    policy_sha256: str
    deck_sha256: str
    config_sha256: str
    env: Mapping[str, str]
    biases: Mapping[str, float]
    min_score_gain: float = 0.0

    def __post_init__(self) -> None:
        _text(self.candidate_id, "candidate_id")
        object.__setattr__(self, "main_path", Path(self.main_path).resolve())
        object.__setattr__(self, "deck_path", Path(self.deck_path).resolve())
        _sha(self.policy_sha256, "policy_sha256")
        _sha(self.deck_sha256, "deck_sha256")
        _sha(self.config_sha256, "config_sha256")
        env = _validate_env(self.env)
        biases = _validate_biases(self.biases)
        object.__setattr__(self, "env", env)
        object.__setattr__(self, "biases", biases)
        if type(self.min_score_gain) not in (int, float) or isinstance(self.min_score_gain, bool):
            raise OutcomeOnlyAlternatingRuntimeError("min_score_gain must be numeric")
        if not math.isfinite(float(self.min_score_gain)) or not 0.0 <= float(self.min_score_gain) <= 100_000.0:
            raise OutcomeOnlyAlternatingRuntimeError("min_score_gain is outside bounded range")
        if _config_sha(env, biases, float(self.min_score_gain)) != self.config_sha256:
            raise OutcomeOnlyAlternatingRuntimeError("config SHA does not reproduce candidate config")

    def verify_sources(self) -> None:
        if not self.main_path.is_file():
            raise OutcomeOnlyAlternatingRuntimeError(f"candidate policy is missing: {self.main_path}")
        if not self.deck_path.is_file():
            raise OutcomeOnlyAlternatingRuntimeError(f"candidate deck is missing: {self.deck_path}")
        if _sha_file(self.main_path) != self.policy_sha256:
            raise OutcomeOnlyAlternatingRuntimeError("candidate policy SHA changed")
        if _sha_file(self.deck_path) != self.deck_sha256:
            raise OutcomeOnlyAlternatingRuntimeError("candidate deck SHA changed")

    @classmethod
    def from_mapping(cls, payload: Mapping[str, object]) -> "OutcomeOnlyCandidateSpecV1":
        if not isinstance(payload, Mapping):
            raise OutcomeOnlyAlternatingRuntimeError("candidate spec must be an object")
        return cls(
            candidate_id=str(payload.get("candidate_id", "")),
            main_path=Path(str(payload.get("main_path", ""))),
            deck_path=Path(str(payload.get("deck_path", ""))),
            policy_sha256=str(payload.get("policy_sha256", "")),
            deck_sha256=str(payload.get("deck_sha256", "")),
            config_sha256=str(payload.get("config_sha256", "")),
            env=payload.get("env", {}),
            biases=payload.get("biases", {}),
            min_score_gain=payload.get("min_score_gain", 0.0),
        )

    def to_mapping(self, *, pool_root: Path) -> dict[str, object]:
        self.verify_sources()
        return {
            "main_path": str(self.main_path),
            "deck_path": str(self.deck_path),
            "policy_sha256": self.policy_sha256,
            "deck_sha256": self.deck_sha256,
            "config_sha256": self.config_sha256,
            "env": dict(self.env),
            "biases": dict(self.biases),
            "min_score_gain": float(self.min_score_gain),
            "pool_root": str(pool_root.resolve()),
        }

    def to_dict(self) -> dict[str, object]:
        return {
            "candidate_id": self.candidate_id,
            "main_path": str(self.main_path),
            "deck_path": str(self.deck_path),
            "policy_sha256": self.policy_sha256,
            "deck_sha256": self.deck_sha256,
            "config_sha256": self.config_sha256,
            "env": dict(self.env),
            "biases": dict(self.biases),
            "min_score_gain": float(self.min_score_gain),
            "research_only": True,
        }


def _stage_repetitions(stage_games: int, reference_ids: Sequence[str]) -> int:
    if type(stage_games) is not int or stage_games <= 0:
        raise OutcomeOnlyAlternatingRuntimeError("stage_games must be a positive integer")
    if not reference_ids or len(set(reference_ids)) != len(reference_ids):
        raise OutcomeOnlyAlternatingRuntimeError("reference_ids must be unique and non-empty")
    denominator = 2 * len(reference_ids)
    if stage_games % denominator:
        raise OutcomeOnlyAlternatingRuntimeError(
            "stage_games must equal opponent_count * 2 seats * repetitions"
        )
    return stage_games // denominator


def build_candidate_control_games_v1(
    *,
    candidate: OutcomeOnlyCandidateSpecV1,
    native_control: OutcomeOnlyCandidateSpecV1,
    pool_root: Path,
    reference_ids: Sequence[str],
    stage_games: int,
    base_seed: int,
    block_id: str,
    runner_ref: str = RUNNER_REF_V1,
    max_steps: int = 2_000,
    timeout_seconds: float = 600.0,
) -> tuple[EvaluationGameV1, ...]:
    """Build candidate and control cells with identical paired strata."""

    if candidate.candidate_id == native_control.candidate_id:
        raise OutcomeOnlyAlternatingRuntimeError("candidate and native control IDs must differ")
    if type(base_seed) is not int or isinstance(base_seed, bool) or base_seed < 0:
        raise OutcomeOnlyAlternatingRuntimeError("base_seed must be nonnegative")
    repetitions = _stage_repetitions(stage_games, reference_ids)
    candidate.verify_sources()
    native_control.verify_sources()
    pool = load_opponent_pool_v1(Path(pool_root).resolve())
    for item in (candidate, native_control):
        if item.candidate_id in set(reference_ids):
            raise OutcomeOnlyAlternatingRuntimeError(
                f"subject id cannot be an opponent reference: {item.candidate_id}"
            )
    built: list[EvaluationGameV1] = []
    for arm, spec in (("candidate", candidate), ("native_control", native_control)):
        raw_games = build_native_candidate_games_v1(
            candidate_id=spec.candidate_id,
            candidate=spec.to_mapping(pool_root=Path(pool_root)),
            pool=pool,
            reference_ids=tuple(reference_ids),
            games_per_opponent_seat=repetitions,
            base_seed=base_seed,
            block_id=f"{block_id}:{arm}",
            max_steps=max_steps,
            timeout_seconds=timeout_seconds,
        )
        if len(raw_games) != stage_games:
            raise OutcomeOnlyAlternatingRuntimeError(
                f"{arm} produced {len(raw_games)} games; expected {stage_games}"
            )
        for game in raw_games:
            repetition = game.metadata.get("repetition")
            pair_key = f"{game.opponent_id}|seat{game.seat}|rep{repetition}"
            built.append(
                replace(
                    game,
                    runner_ref=runner_ref,
                    metadata={
                        **dict(game.metadata),
                        "alternating_arm": arm,
                        "pair_key": pair_key,
                        "research_only": True,
                        "execution_allowed": False,
                    },
                )
            )
    candidate_keys = {
        (str(game.metadata["pair_key"]), game.seed)
        for game in built
        if game.metadata["alternating_arm"] == "candidate"
    }
    control_keys = {
        (str(game.metadata["pair_key"]), game.seed)
        for game in built
        if game.metadata["alternating_arm"] == "native_control"
    }
    if candidate_keys != control_keys:
        raise OutcomeOnlyAlternatingRuntimeError("candidate/control strata differ")
    return tuple(built)


def _family_for(opponent_id: str) -> str:
    return opponent_id.split("_", 1)[0] or opponent_id


def _project_record(row: Mapping[str, object]) -> dict[str, object]:
    metadata = row.get("metadata")
    if not isinstance(metadata, Mapping):
        raise OutcomeOnlyAlternatingRuntimeError("evaluation row metadata is missing")
    arm = metadata.get("alternating_arm")
    pair_key = metadata.get("pair_key")
    if arm not in {"candidate", "native_control"} or type(pair_key) is not str or not pair_key:
        raise OutcomeOnlyAlternatingRuntimeError("evaluation row arm/pair key is invalid")
    opponent_id = row.get("opponent_id")
    seat = row.get("seat")
    seed = row.get("seed")
    outcome = row.get("outcome")
    if type(opponent_id) is not str or not opponent_id or seat not in (0, 1):
        raise OutcomeOnlyAlternatingRuntimeError("evaluation row opponent/seat is invalid")
    if type(seed) not in (int, str) or isinstance(seed, bool):
        raise OutcomeOnlyAlternatingRuntimeError("evaluation row seed is invalid")
    if outcome not in {"win", "draw", "loss", "fault"}:
        raise OutcomeOnlyAlternatingRuntimeError("evaluation row outcome is invalid")
    pair_digest = hashlib.sha256(
        b"mage-ptcg:outcome-only-alternating-pair:v1\0" + pair_key.encode()
    ).hexdigest()[:32]
    return {
        "game_id": f"pair-{pair_digest}",
        "seed": str(seed),
        "opponent_id": opponent_id,
        "family": _family_for(opponent_id),
        "seat": int(seat),
        "outcome": outcome,
        "fault": outcome == "fault" or row.get("status") == "FAULT",
        "arm": str(arm),
        "raw_game_id": str(row.get("game_id", "")),
    }


def _aggregate_projected(rows: Sequence[Mapping[str, object]]) -> dict[str, object]:
    outcomes = [str(row["outcome"]) for row in rows]
    wins = outcomes.count("win")
    draws = outcomes.count("draw")
    losses = outcomes.count("loss")
    faults = outcomes.count("fault")
    requested = len(rows)
    seat = {
        str(index): {
            "games": sum(1 for row in rows if row["seat"] == index),
            "score_rate": (
                sum(_WDL.get(str(row["outcome"]), 0.0) for row in rows if row["seat"] == index)
                / max(1, sum(1 for row in rows if row["seat"] == index))
            ),
        }
        for index in (0, 1)
    }
    return {
        "requested_games": requested,
        "completed_games": wins + draws + losses,
        "wins": wins,
        "draws": draws,
        "losses": losses,
        "faults": faults,
        "fault_rate": faults / requested if requested else None,
        "score_rate": (wins + 0.5 * draws) / requested if requested else None,
        "score_denominator_games": requested,
        "seat": seat,
        "research_only": True,
    }


def _semantic_sha(domain: str, value: object) -> str:
    return hashlib.sha256(domain.encode("ascii") + b"\0" + _canonical(value)).hexdigest()


def summarize_candidate_control_rows_v1(
    rows: Sequence[Mapping[str, object]],
    *,
    candidate: OutcomeOnlyCandidateSpecV1,
    native_control: OutcomeOnlyCandidateSpecV1,
    stage_games: int,
    protocol_sha256: str,
) -> dict[str, object]:
    """Project raw evaluator rows to a paired, outcome-only stage summary."""

    _sha(protocol_sha256, "protocol_sha256")
    projected = [_project_record(row) for row in rows]
    by_arm = {
        arm: sorted((item for item in projected if item["arm"] == arm), key=lambda x: str(x["game_id"]))
        for arm in ("candidate", "native_control")
    }
    if len(by_arm["candidate"]) != stage_games or len(by_arm["native_control"]) != stage_games:
        raise OutcomeOnlyAlternatingRuntimeError("both arms must cover the exact stage games")
    candidate_keys = {(row["game_id"], row["seed"], row["opponent_id"], row["seat"]) for row in by_arm["candidate"]}
    control_keys = {(row["game_id"], row["seed"], row["opponent_id"], row["seat"]) for row in by_arm["native_control"]}
    if candidate_keys != control_keys:
        raise OutcomeOnlyAlternatingRuntimeError("candidate/control projected strata differ")
    # The internal arm marker is not part of the public outcome projection.
    candidate_records = [{key: row[key] for key in ("game_id", "seed", "opponent_id", "family", "seat", "outcome", "fault")} for row in by_arm["candidate"]]
    native_records = [{key: row[key] for key in ("game_id", "seed", "opponent_id", "family", "seat", "outcome", "fault")} for row in by_arm["native_control"]]
    game_ids = sorted(str(row["game_id"]) for row in candidate_records)
    seeds = sorted(str(row["seed"]) for row in candidate_records)
    strata = [
        {key: row[key] for key in ("game_id", "seed", "opponent_id", "family", "seat")}
        for row in candidate_records
    ]
    candidate_summary = _aggregate_projected(candidate_records)
    native_summary = _aggregate_projected(native_records)
    candidate.verify_sources()
    native_control.verify_sources()
    payload = {
        "schema_version": OUTCOME_ONLY_ALTERNATING_RUNTIME_SCHEMA_V1,
        "candidate_id": candidate.candidate_id,
        "native_control_id": native_control.candidate_id,
        "stage_games": stage_games,
        "protocol_sha256": protocol_sha256,
        "candidate": candidate_summary,
        "native_control": native_summary,
        "candidate_delta": float(candidate_summary["score_rate"]) - float(native_summary["score_rate"]),
        "candidate_policy_sha256": candidate.policy_sha256,
        "candidate_deck_sha256": candidate.deck_sha256,
        "candidate_config_sha256": candidate.config_sha256,
        "native_policy_sha256": native_control.policy_sha256,
        "native_deck_sha256": native_control.deck_sha256,
        "native_config_sha256": native_control.config_sha256,
        "game_id_universe_sha256": _semantic_sha("mage-ptcg:outcome-only-game-id-universe:v1", {"game_ids": game_ids}),
        "seed_universe_sha256": _semantic_sha("mage-ptcg:outcome-only-seed-universe:v1", {"seeds": seeds}),
        "strata_sha256": _semantic_sha("mage-ptcg:outcome-only-strata:v1", {"strata": strata}),
        "candidate_records": candidate_records,
        "native_control_records": native_records,
        "authority": dict(AUTHORITY_FALSE_V1),
        "research_only": True,
    }
    payload["summary_sha256"] = _semantic_sha(
        "mage-ptcg:outcome-only-alternating-summary:v1",
        {key: value for key, value in payload.items() if key != "summary_sha256"},
    )
    return payload


def next_stage_games_v1(stage_games: int, *, positive: bool) -> int | None:
    if stage_games not in ALTERNATING_STAGE_GAMES_V1:
        raise OutcomeOnlyAlternatingRuntimeError("stage_games is outside successive-halving sequence")
    if not positive or stage_games == ALTERNATING_STAGE_GAMES_V1[-1]:
        return None
    return ALTERNATING_STAGE_GAMES_V1[ALTERNATING_STAGE_GAMES_V1.index(stage_games) + 1]


def _write_new_json(path: Path, payload: Mapping[str, object]) -> str:
    """Publish one canonical JSON file without replacing a competing writer."""

    path.parent.mkdir(parents=True, exist_ok=True)
    raw = _canonical(payload)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        with temporary.open("xb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)
        temporary.unlink(missing_ok=True)
    finally:
        temporary.unlink(missing_ok=True)
    return hashlib.sha256(raw).hexdigest()


def _read_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OutcomeOnlyAlternatingRuntimeError(f"cannot read stage JSON: {path}") from exc
    if type(value) is not dict:
        raise OutcomeOnlyAlternatingRuntimeError(f"stage JSON must be an object: {path}")
    return value


def _protocol_sha(
    *,
    pool_root: Path,
    reference_ids: Sequence[str],
    stage_games: int,
    base_seed: int,
    max_steps: int,
    timeout_seconds: float,
    runner_ref: str,
) -> str:
    manifest = pool_root / "pool_manifest.json"
    if not manifest.is_file():
        raise OutcomeOnlyAlternatingRuntimeError(f"pool manifest is missing: {manifest}")
    payload = {
        "schema": OUTCOME_ONLY_ALTERNATING_STAGE_SCHEMA_V1,
        "pool_manifest_sha256": _sha_file(manifest),
        "reference_ids": list(reference_ids),
        "stage_games": stage_games,
        "base_seed": base_seed,
        "max_steps": max_steps,
        "timeout_seconds": float(timeout_seconds),
        "runner_ref": runner_ref,
        "evaluator_sha256": evaluation_implementation_sha256_v1(),
    }
    return _semantic_sha("mage-ptcg:outcome-only-alternating-protocol:v1", payload)


def _stage_decision(summary: Mapping[str, object]) -> tuple[str, int | None]:
    candidate = summary.get("candidate")
    native = summary.get("native_control")
    if not isinstance(candidate, Mapping) or not isinstance(native, Mapping):
        raise OutcomeOnlyAlternatingRuntimeError("stage summary arm aggregates are missing")
    if int(candidate.get("faults", 0)) or int(native.get("faults", 0)):
        return "INVALID_FAULT", None
    candidate_seat = candidate.get("seat")
    native_seat = native.get("seat")
    if not isinstance(candidate_seat, Mapping) or not isinstance(native_seat, Mapping):
        raise OutcomeOnlyAlternatingRuntimeError("stage summary seat aggregates are missing")
    candidate_gap = abs(float(candidate_seat["0"]["score_rate"]) - float(candidate_seat["1"]["score_rate"]))
    native_gap = abs(float(native_seat["0"]["score_rate"]) - float(native_seat["1"]["score_rate"]))
    positive = (
        float(summary["candidate_delta"]) > 0.0
        and candidate_gap <= 0.05
        and native_gap <= 0.05
    )
    if not positive:
        return "NOT_PROMOTABLE", None
    return "POSITIVE_CONTINUE", next_stage_games_v1(int(summary["stage_games"]), positive=True)


def run_alternating_stage_v1(
    *,
    candidate: OutcomeOnlyCandidateSpecV1,
    native_control: OutcomeOnlyCandidateSpecV1,
    pool_root: Path,
    reference_ids: Sequence[str],
    stage_games: int,
    base_seed: int,
    block_id: str,
    output_root: Path,
    execute: bool = False,
    phase: str = POLICY_FIXED_SHORT_V1,
    runner_ref: str = RUNNER_REF_V1,
    max_steps: int = 2_000,
    timeout_seconds: float = 600.0,
    workers: int = DEFAULT_MAX_WORKERS_V1,
    worker_recycle_games: int = DEFAULT_WORKER_RECYCLE_GAMES_V1,
) -> dict[str, object]:
    """Materialize or execute one candidate/control stage.

    The two arms are submitted in one evaluator block so the requested worker
    budget is shared rather than multiplying process pools per candidate.
    """

    if phase not in PHASES_V1:
        raise OutcomeOnlyAlternatingRuntimeError(f"unsupported phase: {phase}")
    if stage_games not in ALTERNATING_STAGE_GAMES_V1:
        raise OutcomeOnlyAlternatingRuntimeError(
            "stage_games must be one of 96, 384, 768, or 1536"
        )
    if type(workers) is not int or workers <= 0:
        raise OutcomeOnlyAlternatingRuntimeError("workers must be a positive integer")
    if type(worker_recycle_games) is not int or worker_recycle_games <= 0:
        raise OutcomeOnlyAlternatingRuntimeError("worker_recycle_games must be a positive integer")
    root = Path(output_root).resolve()
    if root.exists():
        if any(root.iterdir()):
            raise FileExistsError(f"stage output root is not empty: {root}")
        raise FileExistsError(f"stage output root already exists: {root}")
    # Build/verify before claiming the destination so an invalid candidate does
    # not leave a misleading empty run root behind.
    games = build_candidate_control_games_v1(
        candidate=candidate,
        native_control=native_control,
        pool_root=Path(pool_root),
        reference_ids=tuple(reference_ids),
        stage_games=stage_games,
        base_seed=base_seed,
        block_id=block_id,
        runner_ref=runner_ref,
        max_steps=max_steps,
        timeout_seconds=timeout_seconds,
    )
    protocol_sha256 = _protocol_sha(
        pool_root=Path(pool_root).resolve(),
        reference_ids=tuple(reference_ids),
        stage_games=stage_games,
        base_seed=base_seed,
        max_steps=max_steps,
        timeout_seconds=timeout_seconds,
        runner_ref=runner_ref,
    )
    root.mkdir(parents=True, exist_ok=False)
    spec_payload = {
        "schema_version": OUTCOME_ONLY_ALTERNATING_RUNTIME_SCHEMA_V1,
        "candidate": candidate.to_dict(),
        "native_control": native_control.to_dict(),
        "reference_ids": list(reference_ids),
        "stage_games": stage_games,
        "base_seed": base_seed,
        "block_id": block_id,
        "phase": phase,
        "protocol_sha256": protocol_sha256,
        "runner_ref": runner_ref,
        "max_steps": max_steps,
        "timeout_seconds": float(timeout_seconds),
        "research_only": True,
        "authority": dict(AUTHORITY_FALSE_V1),
    }
    spec_sha = _write_new_json(root / "stage-spec.json", spec_payload)
    manifest: dict[str, object] = {
        "schema_version": OUTCOME_ONLY_ALTERNATING_STAGE_SCHEMA_V1,
        "status": "EXECUTING" if execute else "DRY_RUN",
        "execution_started": bool(execute),
        "phase": phase,
        "stage_games": stage_games,
        "workers": workers,
        "worker_recycle_games": worker_recycle_games,
        "protocol_sha256": protocol_sha256,
        "stage_spec_sha256": spec_sha,
        "evaluator_implementation_sha256": evaluation_implementation_sha256_v1(),
        "authority": dict(AUTHORITY_FALSE_V1),
        "research_only": True,
        "candidate_id": candidate.candidate_id,
        "native_control_id": native_control.candidate_id,
        "candidate_policy_sha256": candidate.policy_sha256,
        "candidate_deck_sha256": candidate.deck_sha256,
        "candidate_config_sha256": candidate.config_sha256,
        "native_policy_sha256": native_control.policy_sha256,
        "native_deck_sha256": native_control.deck_sha256,
        "native_config_sha256": native_control.config_sha256,
        "reference_ids": list(reference_ids),
    }
    if not execute:
        manifest["status"] = "DRY_RUN"
        manifest["execution_started"] = False
        manifest["requested_games"] = len(games)
        manifest_sha = _write_new_json(root / "manifest.json", manifest)
        return {
            "status": "DRY_RUN",
            "execution_started": False,
            "manifest_sha256": manifest_sha,
            "output_root": str(root),
            "authority": dict(AUTHORITY_FALSE_V1),
            "research_only": True,
        }
    evaluation = run_parallel_cabt_evaluation(
        games,
        output_dir=root / "evaluation",
        max_workers=workers,
        worker_recycle_games=worker_recycle_games,
        overwrite=False,
    )
    summary = summarize_candidate_control_rows_v1(
        evaluation["rows"],
        candidate=candidate,
        native_control=native_control,
        stage_games=stage_games,
        protocol_sha256=protocol_sha256,
    )
    decision, next_stage = _stage_decision(summary)
    summary_sha = _write_new_json(root / "summary.json", summary)
    manifest.update(
        {
            "status": "COMPLETE",
            "requested_games": len(games),
            "completed_games": evaluation["summary"].get("completed_games"),
            "faults": evaluation["summary"].get("faults"),
            "summary_sha256": summary_sha,
            "decision": decision,
            "next_stage_games": next_stage,
            "evaluation_output": "evaluation",
        }
    )
    manifest_sha = _write_new_json(root / "manifest.json", manifest)
    return {
        "status": "COMPLETE",
        "execution_started": True,
        "manifest_sha256": manifest_sha,
        "summary_sha256": summary_sha,
        "output_root": str(root),
        "summary": summary,
        "decision": decision,
        "next_stage_games": next_stage,
        "authority": dict(AUTHORITY_FALSE_V1),
        "research_only": True,
    }


def load_alternating_stage_v1(run_root: Path | str) -> dict[str, object]:
    """Reload and verify a stage manifest and its optional summary."""

    root = Path(run_root).resolve()
    manifest_path = root / "manifest.json"
    manifest = _read_json(manifest_path)
    if manifest.get("schema_version") != OUTCOME_ONLY_ALTERNATING_STAGE_SCHEMA_V1:
        raise OutcomeOnlyAlternatingRuntimeError("stage manifest schema is invalid")
    if manifest.get("authority") != AUTHORITY_FALSE_V1 or manifest.get("research_only") is not True:
        raise OutcomeOnlyAlternatingRuntimeError("stage manifest grants authority")
    summary_path = root / "summary.json"
    result = dict(manifest)
    if manifest.get("status") == "COMPLETE":
        if not summary_path.is_file():
            raise OutcomeOnlyAlternatingRuntimeError("completed stage summary is missing")
        actual = _sha_file(summary_path)
        if actual != manifest.get("summary_sha256"):
            raise OutcomeOnlyAlternatingRuntimeError("stage summary SHA changed")
        result["summary"] = _read_json(summary_path)
    return result


__all__ = [
    "ALTERNATING_STAGE_GAMES_V1",
    "AUTHORITY_FALSE_V1",
    "DECK_FIXED_LONG_V1",
    "DEFAULT_MAX_WORKERS_V1",
    "DEFAULT_WORKER_RECYCLE_GAMES_V1",
    "OutcomeOnlyAlternatingRuntimeError",
    "OutcomeOnlyCandidateSpecV1",
    "OUTCOME_ONLY_ALTERNATING_RUNTIME_SCHEMA_V1",
    "POLICY_FIXED_SHORT_V1",
    "build_candidate_control_games_v1",
    "load_alternating_stage_v1",
    "next_stage_games_v1",
    "run_alternating_stage_v1",
    "summarize_candidate_control_rows_v1",
]
