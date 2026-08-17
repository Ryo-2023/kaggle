"""Design-only contract for native public-only self-rollout collection.

This module deliberately contains no evaluator, engine, subprocess, or
collector loop.  It validates a hash-bound common24 plan and public-only
decision records, then can materialize a dry-run manifest.  A native asset
whose source is ``local_eval_only`` and whose teacher behavior permission is
false is never promoted to a behavior source by this contract.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any, Mapping, Sequence


SCHEMA_V1 = "meta-specialist-native-public-rollout-collector-v1"
PLAN_SCHEMA_V1 = "meta-specialist-native-public-rollout-plan-v1"
RECORD_SCHEMA_V1 = "meta-specialist-native-public-rollout-record-v1"
_HEX = frozenset("0123456789abcdef")
_USAGE_BOUNDARIES = frozenset({"local_eval_only", "training_local", "training_local_and_eval"})
_ALLOWED_USAGES = frozenset({"audit-local", "training-local", "native-self-rollout-local"})
_OUTCOMES = frozenset({"win", "loss", "draw", "fault"})
_FAULT_STATUSES = frozenset({"ok", "fault", "step_limit", "unknown"})
_AUTHORITY_FALSE = {
    "training_authority": False,
    "promotion_authority": False,
    "submission_authority": False,
    "external_execution_authority": False,
}


class NativePublicRolloutCollectorError(ValueError):
    """Raised when a public self-rollout contract is malformed or unsafe."""


def _text(value: object, name: str) -> str:
    if type(value) is not str or not value.strip():
        raise NativePublicRolloutCollectorError(f"{name} must be a non-empty string")
    return value


def _sha(value: object, name: str) -> str:
    if type(value) is not str or len(value) != 64 or any(char not in _HEX for char in value):
        raise NativePublicRolloutCollectorError(f"{name} must be a lowercase SHA-256 hex string")
    return value


def _nonnegative_int(value: object, name: str) -> int:
    if type(value) is not int or value < 0:
        raise NativePublicRolloutCollectorError(f"{name} must be a nonnegative integer")
    return value


def _canonical_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise NativePublicRolloutCollectorError("value cannot be canonicalized") from exc


def _semantic_sha(domain: str, value: object) -> str:
    return hashlib.sha256(domain.encode("ascii") + b"\0" + _canonical_bytes(value)).hexdigest()


@dataclass(frozen=True, slots=True)
class NativePublicRolloutIdentityV1:
    candidate_id: str
    policy_sha256: str
    deck_sha256: str
    evaluator_sha256: str
    engine_sha256: str
    runner_sha256: str
    pool_manifest_sha256: str
    protocol_sha256: str
    projection_schema_sha256: str
    action_schema_sha256: str
    source_commit_sha256: str

    def __post_init__(self) -> None:
        _text(self.candidate_id, "identity.candidate_id")
        for field in (
            "policy_sha256",
            "deck_sha256",
            "evaluator_sha256",
            "engine_sha256",
            "runner_sha256",
            "pool_manifest_sha256",
            "protocol_sha256",
            "projection_schema_sha256",
            "action_schema_sha256",
            "source_commit_sha256",
        ):
            _sha(getattr(self, field), f"identity.{field}")

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class NativePublicRolloutAuthorizationV1:
    """Permission boundary for recording a native policy's own public actions.

    ``teacher_behavior_allowed`` is intentionally required to be false: this
    route is not allowed to relabel native actions as third-party teacher
    labels.  An owned research policy or an issuered self-rollout permission is
    required before a collection run may be considered admissible.
    """

    source_kind: str
    usage_boundary: str
    owned_policy: bool
    explicit_self_rollout_allowed: bool
    teacher_behavior_allowed: bool
    permission_manifest_id: str | None
    permission_content_hash: str | None
    decision_ref: str | None
    allowed_usages: tuple[str, ...]
    source_manifest_path: str | None = None
    source_manifest_sha256: str | None = None
    permission_manifest_path: str | None = None
    permission_manifest_sha256: str | None = None
    projection_audit_path: str | None = None
    projection_audit_sha256: str | None = None

    def __post_init__(self) -> None:
        _text(self.source_kind, "authorization.source_kind")
        if self.usage_boundary not in _USAGE_BOUNDARIES:
            raise NativePublicRolloutCollectorError("authorization.usage_boundary is unsupported")
        for field in ("owned_policy", "explicit_self_rollout_allowed", "teacher_behavior_allowed"):
            if type(getattr(self, field)) is not bool:
                raise NativePublicRolloutCollectorError(f"authorization.{field} must be bool")
        if self.teacher_behavior_allowed:
            raise NativePublicRolloutCollectorError(
                "native self-rollout cannot enable teacher behavior labels"
            )
        usages = tuple(self.allowed_usages)
        if usages != tuple(sorted(set(usages))) or any(item not in _ALLOWED_USAGES for item in usages):
            raise NativePublicRolloutCollectorError("authorization.allowed_usages is invalid")
        for field in ("permission_manifest_id", "permission_content_hash"):
            value = getattr(self, field)
            if value is not None:
                _sha(value, f"authorization.{field}")
        for field in (
            "source_manifest_sha256",
            "permission_manifest_sha256",
            "projection_audit_sha256",
        ):
            value = getattr(self, field)
            if value is not None:
                _sha(value, f"authorization.{field}")
        for field in (
            "source_manifest_path",
            "permission_manifest_path",
            "projection_audit_path",
        ):
            value = getattr(self, field)
            if value is not None:
                _text(value, f"authorization.{field}")
        if self.decision_ref is not None:
            _text(self.decision_ref, "authorization.decision_ref")
        permission_fields_present = any(
            value is not None
            for value in (self.permission_manifest_id, self.permission_content_hash, self.decision_ref)
        )
        if self.explicit_self_rollout_allowed:
            if not (
                self.permission_manifest_id
                and self.permission_content_hash
                and self.decision_ref
                and "native-self-rollout-local" in usages
            ):
                raise NativePublicRolloutCollectorError(
                    "explicit self-rollout permission requires permission binding and native-self-rollout-local"
                )
        elif permission_fields_present:
            raise NativePublicRolloutCollectorError(
                "permission fields cannot be supplied when self-rollout permission is false"
            )
        if self.owned_policy and self.source_kind != "owned_research_policy":
            raise NativePublicRolloutCollectorError(
                "owned_policy requires source_kind=owned_research_policy"
            )

    @property
    def collection_allowed(self) -> bool:
        return self.owned_policy or self.explicit_self_rollout_allowed

    def to_dict(self) -> dict[str, object]:
        result = asdict(self)
        result["allowed_usages"] = list(self.allowed_usages)
        result["collection_allowed"] = self.collection_allowed
        return result


@dataclass(frozen=True, slots=True)
class NativePublicRolloutGameV1:
    game_id: str
    opponent_id: str
    opponent_family: str
    seat: int
    repetition: int
    seed: int

    def __post_init__(self) -> None:
        for field in ("game_id", "opponent_id", "opponent_family"):
            _text(getattr(self, field), f"game.{field}")
        if self.seat not in (0, 1):
            raise NativePublicRolloutCollectorError("game.seat must be 0 or 1")
        if self.repetition not in (0, 1):
            raise NativePublicRolloutCollectorError("game.repetition must be 0 or 1")
        _nonnegative_int(self.seed, "game.seed")

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class NativePublicRolloutPlanV1:
    protocol: str
    base_seed: int
    opponent_ids: tuple[str, ...]
    opponent_families: tuple[tuple[str, str], ...]
    games: tuple[NativePublicRolloutGameV1, ...]
    pool_manifest_path: str | None = None
    pool_manifest_sha256: str | None = None
    pool_manifest_semantic_sha256: str | None = None

    def __post_init__(self) -> None:
        if self.protocol != "common24":
            raise NativePublicRolloutCollectorError("plan.protocol must be common24")
        _nonnegative_int(self.base_seed, "plan.base_seed")
        if self.pool_manifest_path is not None:
            _text(self.pool_manifest_path, "plan.pool_manifest_path")
        for field in ("pool_manifest_sha256", "pool_manifest_semantic_sha256"):
            value = getattr(self, field)
            if value is not None:
                _sha(value, f"plan.{field}")
        if (self.pool_manifest_path is None) != (self.pool_manifest_sha256 is None):
            raise NativePublicRolloutCollectorError(
                "plan pool manifest path and file SHA must be supplied together"
            )
        if self.pool_manifest_semantic_sha256 is not None and self.pool_manifest_sha256 is None:
            raise NativePublicRolloutCollectorError(
                "plan pool manifest semantic SHA requires a file SHA"
            )
        if len(self.opponent_ids) != 24 or len(set(self.opponent_ids)) != 24:
            raise NativePublicRolloutCollectorError("common24 plan requires exactly 24 unique opponents")
        if tuple(sorted(self.opponent_ids)) != self.opponent_ids:
            raise NativePublicRolloutCollectorError("plan.opponent_ids must be sorted")
        families = dict(self.opponent_families)
        if set(families) != set(self.opponent_ids) or any(not value for value in families.values()):
            raise NativePublicRolloutCollectorError("plan opponent families are incomplete")
        if len(self.games) != 96:
            raise NativePublicRolloutCollectorError("common24 plan must contain exactly 96 games")
        expected = {
            (opponent_id, seat, repetition)
            for opponent_id in self.opponent_ids
            for seat in (0, 1)
            for repetition in (0, 1)
        }
        actual = {(game.opponent_id, game.seat, game.repetition) for game in self.games}
        if (
            actual != expected
            or len(actual) != 96
            or len({game.game_id for game in self.games}) != 96
            or len({game.seed for game in self.games}) != 96
        ):
            raise NativePublicRolloutCollectorError("common24 game schedule is incomplete or duplicated")
        for game in self.games:
            if families[game.opponent_id] != game.opponent_family:
                raise NativePublicRolloutCollectorError("game opponent family does not match plan")

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "schema_version": PLAN_SCHEMA_V1,
            "protocol": self.protocol,
            "base_seed": self.base_seed,
            "opponent_ids": list(self.opponent_ids),
            "opponent_families": {key: value for key, value in self.opponent_families},
            "games": [game.to_dict() for game in self.games],
        }
        if self.pool_manifest_path is not None:
            result["pool_manifest_path"] = self.pool_manifest_path
            result["pool_manifest_sha256"] = self.pool_manifest_sha256
            result["pool_manifest_semantic_sha256"] = self.pool_manifest_semantic_sha256
        return result

    @property
    def plan_sha256(self) -> str:
        return _semantic_sha("mage_ptcg:native-public-rollout-plan:v1", self.to_dict())


def _derive_seed(base_seed: int, opponent_id: str, seat: int, repetition: int) -> int:
    digest = hashlib.sha256(
        f"native-public-rollout-v1\0{base_seed}\0{opponent_id}\0{seat}\0{repetition}".encode("utf-8")
    ).hexdigest()
    return int(digest[:15], 16)


def build_common24_plan_v1(
    *,
    opponent_ids: Sequence[str],
    opponent_families: Mapping[str, str],
    base_seed: int,
    pool_manifest_path: str | None = None,
    pool_manifest_sha256: str | None = None,
    pool_manifest_semantic_sha256: str | None = None,
) -> NativePublicRolloutPlanV1:
    ids = tuple(sorted(_text(item, "opponent_id") for item in opponent_ids))
    if len(ids) != 24 or len(set(ids)) != 24:
        raise NativePublicRolloutCollectorError("common24 requires 24 unique opponent IDs")
    families = tuple((item, _text(opponent_families.get(item), f"opponent_families[{item}]")) for item in ids)
    games = tuple(
        NativePublicRolloutGameV1(
            game_id=f"common24|{opponent_id}|seat={seat}|rep={repetition}",
            opponent_id=opponent_id,
            opponent_family=dict(families)[opponent_id],
            seat=seat,
            repetition=repetition,
            seed=_derive_seed(base_seed, opponent_id, seat, repetition),
        )
        for opponent_id in ids
        for seat in (0, 1)
        for repetition in (0, 1)
    )
    return NativePublicRolloutPlanV1(
        protocol="common24",
        base_seed=base_seed,
        opponent_ids=ids,
        opponent_families=families,
        games=games,
        pool_manifest_path=pool_manifest_path,
        pool_manifest_sha256=pool_manifest_sha256,
        pool_manifest_semantic_sha256=pool_manifest_semantic_sha256,
    )


def _read_bound_json(path_value: str, expected_sha: str, label: str) -> object:
    path = Path(path_value)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise NativePublicRolloutCollectorError(f"{label} cannot be read") from exc
    actual_sha = hashlib.sha256(raw).hexdigest()
    if actual_sha != expected_sha:
        raise NativePublicRolloutCollectorError(f"{label} file SHA does not verify")
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise NativePublicRolloutCollectorError(f"{label} is not valid JSON") from exc


def _manifest_rows(payload: object, label: str) -> list[Mapping[str, object]]:
    rows: object = payload
    if isinstance(payload, Mapping):
        rows = payload.get("rows", payload.get("opponents", payload.get("entries")))
    if not isinstance(rows, list) or not rows or any(not isinstance(row, Mapping) for row in rows):
        raise NativePublicRolloutCollectorError(f"{label} rows are missing or malformed")
    return [row for row in rows if isinstance(row, Mapping)]


def _validate_authorization_root(
    *, identity: NativePublicRolloutIdentityV1, authorization: NativePublicRolloutAuthorizationV1
) -> None:
    """Verify a local-evaluation self-rollout against immutable source artifacts.

    The authorization dataclass intentionally accepts only shape-valid values;
    this second gate binds those values to bytes on disk.  No caller-owned
    boolean can make a local-eval-only asset collection-ready without all three
    source, permission, and public-projection artifacts.
    """

    if not authorization.collection_allowed:
        return
    if authorization.usage_boundary == "local_eval_only" and authorization.owned_policy:
        raise NativePublicRolloutCollectorError(
            "local_eval_only owned_policy requires an issuered self-rollout binding"
        )
    required = (
        authorization.source_manifest_path,
        authorization.source_manifest_sha256,
        authorization.permission_manifest_path,
        authorization.permission_manifest_sha256,
        authorization.projection_audit_path,
        authorization.projection_audit_sha256,
    )
    if any(value is None for value in required):
        raise NativePublicRolloutCollectorError(
            "local_eval_only collection requires source, permission, and projection bindings"
        )

    source = _read_bound_json(
        authorization.source_manifest_path, authorization.source_manifest_sha256, "source manifest"  # type: ignore[arg-type]
    )
    source_rows = _manifest_rows(source, "source manifest")
    source_row = next(
        (
            row
            for row in source_rows
            if row.get("candidate_id", row.get("opponent_id", row.get("id"))) == identity.candidate_id
        ),
        None,
    )
    if source_row is None:
        raise NativePublicRolloutCollectorError("source manifest does not contain candidate")
    if source_row.get("policy_sha256", source_row.get("policy_hash")) != identity.policy_sha256:
        raise NativePublicRolloutCollectorError("source policy SHA does not match identity")
    if source_row.get("deck_sha256", source_row.get("canonical_deck_hash")) != identity.deck_sha256:
        raise NativePublicRolloutCollectorError("source deck SHA does not match identity")
    if source_row.get("usage_boundary") != authorization.usage_boundary:
        raise NativePublicRolloutCollectorError("source usage boundary does not match authorization")
    if source_row.get("behavior_allowed") is not False or source_row.get("submission_allowed") is not False:
        raise NativePublicRolloutCollectorError("source behavior/submission permission is unsafe")
    if isinstance(source, Mapping):
        for field in _AUTHORITY_FALSE:
            if source.get(field) is not False:
                raise NativePublicRolloutCollectorError("source manifest authority is unsafe")

    permission = _read_bound_json(
        authorization.permission_manifest_path,
        authorization.permission_manifest_sha256,  # type: ignore[arg-type]
        "permission manifest",
    )
    if not isinstance(permission, Mapping):
        raise NativePublicRolloutCollectorError("permission manifest is malformed")
    if permission.get("permission_manifest_id") != authorization.permission_manifest_id:
        raise NativePublicRolloutCollectorError("permission manifest ID does not match authorization")
    if permission.get("candidate_id") != identity.candidate_id:
        raise NativePublicRolloutCollectorError("permission candidate does not match identity")
    if permission.get("policy_sha256") != identity.policy_sha256 or permission.get("deck_sha256") != identity.deck_sha256:
        raise NativePublicRolloutCollectorError("permission policy/deck SHA does not match identity")
    if authorization.explicit_self_rollout_allowed:
        if permission.get("explicit_self_rollout_allowed") is not True:
            raise NativePublicRolloutCollectorError("permission does not authorize native self-rollout")
    elif permission.get("owned_policy") is not True:
        raise NativePublicRolloutCollectorError("owned policy lacks an issuered ownership binding")
    if permission.get("teacher_behavior_allowed") is not False:
        raise NativePublicRolloutCollectorError("permission enables teacher behavior")
    if permission.get("decision_ref") != authorization.decision_ref:
        raise NativePublicRolloutCollectorError("permission decision does not match authorization")
    if authorization.permission_content_hash != authorization.permission_manifest_sha256:
        raise NativePublicRolloutCollectorError("permission content SHA does not match bound manifest")
    authority = permission.get("authority")
    if not isinstance(authority, Mapping) or dict(authority) != _AUTHORITY_FALSE:
        raise NativePublicRolloutCollectorError("permission authority is unsafe")

    projection = _read_bound_json(
        authorization.projection_audit_path,
        authorization.projection_audit_sha256,  # type: ignore[arg-type]
        "projection audit",
    )
    if not isinstance(projection, Mapping):
        raise NativePublicRolloutCollectorError("projection audit is malformed")
    if projection.get("schema_version") != "meta-specialist-native-public-projection-audit-v1":
        raise NativePublicRolloutCollectorError("projection audit schema is unsupported")
    if projection.get("candidate_id") != identity.candidate_id or projection.get("public_only") is not True:
        raise NativePublicRolloutCollectorError("projection is not bound to public candidate identity")
    scan = projection.get("private_field_scan")
    if not isinstance(scan, Mapping) or scan.get("count") != 0 or scan.get("forbidden_fields") != []:
        raise NativePublicRolloutCollectorError("projection private-field scan is not clean")
    if projection.get("projection_schema_sha256") != identity.projection_schema_sha256:
        raise NativePublicRolloutCollectorError("projection schema SHA does not match identity")
    if projection.get("action_schema_sha256") != identity.action_schema_sha256:
        raise NativePublicRolloutCollectorError("action schema SHA does not match identity")
    if projection.get("authority") != _AUTHORITY_FALSE:
        raise NativePublicRolloutCollectorError("projection audit authority is unsafe")


def _validate_pool_binding(
    *, identity: NativePublicRolloutIdentityV1, authorization: NativePublicRolloutAuthorizationV1,
    plan: NativePublicRolloutPlanV1,
) -> None:
    if not authorization.collection_allowed:
        return
    if plan.pool_manifest_path is None or plan.pool_manifest_sha256 is None:
        raise NativePublicRolloutCollectorError("local_eval_only plan requires pool manifest binding")
    if plan.pool_manifest_sha256 != identity.pool_manifest_sha256:
        raise NativePublicRolloutCollectorError("pool manifest SHA does not match identity")
    if plan.pool_manifest_semantic_sha256 is None:
        raise NativePublicRolloutCollectorError("pool manifest semantic SHA is required")
    payload = _read_bound_json(plan.pool_manifest_path, plan.pool_manifest_sha256, "pool manifest")
    rows = _manifest_rows(payload, "pool manifest")
    normalized_rows = sorted(
        (dict(row) for row in rows),
        key=lambda row: str(row.get("id", row.get("opponent_id", ""))),
    )
    semantic = _semantic_sha("mage_ptcg:native-public-rollout-pool-manifest:v1", normalized_rows)
    if semantic != plan.pool_manifest_semantic_sha256:
        raise NativePublicRolloutCollectorError("pool manifest semantic SHA does not verify")
    by_id: dict[str, Mapping[str, object]] = {}
    for row in rows:
        raw_id = row.get("id", row.get("opponent_id"))
        if type(raw_id) is not str or not raw_id or raw_id in by_id:
            raise NativePublicRolloutCollectorError("pool manifest has invalid or duplicate opponent IDs")
        by_id[raw_id] = row
    families = dict(plan.opponent_families)
    if set(plan.opponent_ids) != set(by_id).intersection(plan.opponent_ids):
        raise NativePublicRolloutCollectorError("selected opponents are not fully present in pool manifest")
    for opponent_id in plan.opponent_ids:
        row = by_id.get(opponent_id)
        if row is None:
            raise NativePublicRolloutCollectorError("selected opponent is absent from pool manifest")
        if row.get("usage_boundary") != "local_eval_only":
            raise NativePublicRolloutCollectorError("selected opponent usage boundary is unsafe")
        row_family = row.get("family", row.get("opponent_family"))
        if row_family is None or row_family != families[opponent_id]:
            raise NativePublicRolloutCollectorError("selected opponent family is not bound to pool manifest")


_RECORD_KEYS = frozenset(
    {
        "game_id",
        "step_index",
        "seed",
        "seat",
        "opponent_id",
        "opponent_family",
        "state_digest",
        "action_key",
        "terminal_outcome",
    }
)
_RECORD_KEYS_WITH_FAULT = _RECORD_KEYS | {"fault_status"}
_RECORD_KEYS_WITH_TERMINAL = _RECORD_KEYS_WITH_FAULT | {"terminal"}


@dataclass(frozen=True, slots=True)
class PublicRolloutRecordV1:
    game_id: str
    step_index: int
    seed: int
    seat: int
    opponent_id: str
    opponent_family: str
    state_digest: str
    action_key: str
    terminal_outcome: str
    fault_status: str = "ok"
    terminal: bool = False

    def __post_init__(self) -> None:
        for field in ("game_id", "opponent_id", "opponent_family"):
            _text(getattr(self, field), f"record.{field}")
        _nonnegative_int(self.step_index, "record.step_index")
        _nonnegative_int(self.seed, "record.seed")
        if self.seat not in (0, 1):
            raise NativePublicRolloutCollectorError("record.seat must be 0 or 1")
        _sha(self.state_digest, "record.state_digest")
        _sha(self.action_key, "record.action_key")
        if self.terminal_outcome not in _OUTCOMES:
            raise NativePublicRolloutCollectorError("record.terminal_outcome is invalid")
        if type(self.terminal) is not bool:
            raise NativePublicRolloutCollectorError("record.terminal must be bool")
        if self.fault_status not in _FAULT_STATUSES:
            raise NativePublicRolloutCollectorError("record.fault_status is invalid")
        if self.terminal_outcome == "fault" and self.fault_status == "ok":
            raise NativePublicRolloutCollectorError("fault outcome requires a non-ok fault_status")
        if self.terminal_outcome != "fault" and self.fault_status != "ok":
            raise NativePublicRolloutCollectorError("non-fault outcome cannot carry fault_status")

    @classmethod
    def from_mapping(cls, payload: Mapping[str, object]) -> "PublicRolloutRecordV1":
        if not isinstance(payload, Mapping):
            raise NativePublicRolloutCollectorError("public rollout record must be an object")
        keys = set(payload)
        if keys not in (_RECORD_KEYS, _RECORD_KEYS_WITH_FAULT, _RECORD_KEYS_WITH_TERMINAL):
            forbidden = sorted(
                key
                for key in keys - _RECORD_KEYS_WITH_TERMINAL
                if any(token in str(key).lower() for token in ("private", "hidden", "teacher", "label", "logprob"))
            )
            if forbidden:
                raise NativePublicRolloutCollectorError(f"forbidden public rollout field: {forbidden[0]}")
            raise NativePublicRolloutCollectorError("unsupported or missing public rollout record fields")
        values = {key: payload[key] for key in _RECORD_KEYS}
        values["fault_status"] = payload.get(
            "fault_status", "fault" if values["terminal_outcome"] == "fault" else "ok"
        )
        values["terminal"] = payload.get("terminal", False)
        return cls(**values)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def validate_public_rollout_records_v1(
    *, records: Sequence[PublicRolloutRecordV1], plan: NativePublicRolloutPlanV1
) -> str | None:
    if not isinstance(plan, NativePublicRolloutPlanV1):
        raise NativePublicRolloutCollectorError("plan must be NativePublicRolloutPlanV1")
    by_game = {game.game_id: game for game in plan.games}
    seen: set[tuple[str, int]] = set()
    canonical: list[dict[str, object]] = []
    for record in records:
        if not isinstance(record, PublicRolloutRecordV1):
            raise NativePublicRolloutCollectorError("records must be PublicRolloutRecordV1")
        game = by_game.get(record.game_id)
        if game is None:
            raise NativePublicRolloutCollectorError("record game_id is outside the common24 plan")
        key = (record.game_id, record.step_index)
        if key in seen:
            raise NativePublicRolloutCollectorError("duplicate public rollout decision")
        seen.add(key)
        if (
            record.seed != game.seed
            or record.seat != game.seat
            or record.opponent_id != game.opponent_id
            or record.opponent_family != game.opponent_family
        ):
            raise NativePublicRolloutCollectorError("record identity does not match common24 plan")
        canonical.append(record.to_dict())
    if not canonical:
        return None
    canonical.sort(key=lambda row: (str(row["game_id"]), int(row["step_index"])))
    return _semantic_sha("mage_ptcg:native-public-rollout-records:v1", canonical)


def validate_complete_public_rollout_snapshot_v1(
    *, records: Sequence[PublicRolloutRecordV1], plan: NativePublicRolloutPlanV1
) -> dict[str, object]:
    """Fail closed unless a common24 collection is a complete 96-game snapshot.

    A record is a public decision row.  The terminal outcome is repeated on
    each row belonging to a game, so a game is complete only when its rows have
    contiguous step indexes and a single terminal outcome.  Fault games remain
    in the requested-game denominator rather than being silently dropped.
    """

    records_sha = validate_public_rollout_records_v1(records=records, plan=plan)
    by_game = {game.game_id: game for game in plan.games}
    grouped: dict[str, list[PublicRolloutRecordV1]] = {game_id: [] for game_id in by_game}
    for record in records:
        grouped[record.game_id].append(record)
    missing = [game_id for game_id, rows in grouped.items() if not rows]
    if missing:
        raise NativePublicRolloutCollectorError(
            f"complete common24 snapshot requires all 96 games; missing {len(missing)}"
        )
    fault_games = 0
    terminal_counts: dict[str, int] = {outcome: 0 for outcome in _OUTCOMES}
    seat_counts = {0: 0, 1: 0}
    opponent_counts: dict[str, int] = {game.opponent_id: 0 for game in plan.games}
    for game_id, rows in grouped.items():
        rows.sort(key=lambda row: row.step_index)
        expected_steps = list(range(len(rows)))
        actual_steps = [row.step_index for row in rows]
        if actual_steps != expected_steps:
            raise NativePublicRolloutCollectorError(
                f"game {game_id} has non-contiguous step indexes"
            )
        outcomes = {row.terminal_outcome for row in rows}
        if len(outcomes) != 1:
            raise NativePublicRolloutCollectorError(
                f"game {game_id} has inconsistent terminal outcomes"
            )
        outcome = rows[-1].terminal_outcome
        if outcome not in _OUTCOMES:
            raise NativePublicRolloutCollectorError("game terminal outcome is invalid")
        if any(row.terminal_outcome != outcome for row in rows):
            raise NativePublicRolloutCollectorError("terminal outcome appears after a terminal transition")
        terminal_rows = [row for row in rows if row.terminal]
        if len(terminal_rows) != 1 or terminal_rows[0] is not rows[-1]:
            raise NativePublicRolloutCollectorError(
                f"game {game_id} must have exactly one terminal record at the final step"
            )
        if outcome == "fault":
            if any(row.fault_status == "ok" for row in rows):
                raise NativePublicRolloutCollectorError("fault game has an ok fault status")
            if len({row.fault_status for row in rows}) != 1:
                raise NativePublicRolloutCollectorError("fault game has inconsistent fault status")
            fault_games += 1
        elif any(row.fault_status != "ok" for row in rows):
            raise NativePublicRolloutCollectorError("non-fault game has fault status")
        terminal_counts[outcome] += 1
        seat_counts[rows[0].seat] += 1
        opponent_counts[rows[0].opponent_id] += 1
    if sum(seat_counts.values()) != 96 or any(value != 4 for value in opponent_counts.values()):
        raise NativePublicRolloutCollectorError("common24 seat/opponent coverage is incomplete")
    return {
        "games": 96,
        "completed_games": 96,
        "records": len(records),
        "fault_games": fault_games,
        "fault_denominator": 96,
        "terminal_counts": terminal_counts,
        "seat_game_counts": {str(key): value for key, value in seat_counts.items()},
        "records_sha256": records_sha,
    }


def build_native_public_rollout_dry_run_v1(
    *,
    identity: NativePublicRolloutIdentityV1,
    authorization: NativePublicRolloutAuthorizationV1,
    plan: NativePublicRolloutPlanV1,
    records: Sequence[PublicRolloutRecordV1] = (),
) -> dict[str, object]:
    if not isinstance(identity, NativePublicRolloutIdentityV1):
        raise NativePublicRolloutCollectorError("identity must be NativePublicRolloutIdentityV1")
    if not isinstance(authorization, NativePublicRolloutAuthorizationV1):
        raise NativePublicRolloutCollectorError(
            "authorization must be NativePublicRolloutAuthorizationV1"
        )
    if not isinstance(plan, NativePublicRolloutPlanV1):
        raise NativePublicRolloutCollectorError("plan must be NativePublicRolloutPlanV1")
    if records:
        raise NativePublicRolloutCollectorError(
            "dry-run manifest cannot contain rollout records; validate a complete snapshot separately"
        )
    authorization_verified = authorization.collection_allowed
    try:
        _validate_authorization_root(identity=identity, authorization=authorization)
        _validate_pool_binding(identity=identity, authorization=authorization, plan=plan)
    except NativePublicRolloutCollectorError:
        # local_eval-only mismatches remain loud so an accidentally forged
        # permission cannot look like a valid run; other unverified ownership
        # claims are represented as a blocked dry-run rather than readiness.
        if authorization.usage_boundary == "local_eval_only":
            raise
        authorization_verified = False
    if not authorization.collection_allowed:
        authorization_verified = False
    records_sha = validate_public_rollout_records_v1(records=records, plan=plan)
    payload: dict[str, object] = {
        "schema_version": SCHEMA_V1,
        "status": "DRY_RUN" if authorization_verified else "BLOCKED",
        "dry_run": True,
        "collection_started": False,
        "processes_launched": False,
        "ready_for_collection": authorization_verified,
        "ready_for_evaluation": False,
        "teacher_labels_allowed": False,
        "records_present": bool(records),
        "records_sha256": records_sha,
        "identity": identity.to_dict(),
        "authorization": authorization.to_dict(),
        "plan": plan.to_dict(),
        "plan_sha256": plan.plan_sha256,
        "authority": dict(_AUTHORITY_FALSE),
        "authorization_verified": authorization_verified,
    }
    payload["manifest_sha256"] = _semantic_sha(
        "mage_ptcg:native-public-rollout-manifest:v1",
        {key: value for key, value in payload.items() if key != "manifest_sha256"},
    )
    return payload


def _authorization_from_dict(payload: object) -> NativePublicRolloutAuthorizationV1:
    if not isinstance(payload, Mapping):
        raise NativePublicRolloutCollectorError("manifest authorization must be an object")
    values = dict(payload)
    values.pop("collection_allowed", None)
    values["allowed_usages"] = tuple(values.get("allowed_usages", ()))
    return NativePublicRolloutAuthorizationV1(**values)


def _plan_from_dict(payload: object) -> NativePublicRolloutPlanV1:
    if not isinstance(payload, Mapping):
        raise NativePublicRolloutCollectorError("manifest plan must be an object")
    try:
        games = tuple(
            NativePublicRolloutGameV1(**dict(item))
            for item in payload["games"]  # type: ignore[index]
        )
        families = tuple(sorted(dict(payload["opponent_families"]).items()))  # type: ignore[index]
        return NativePublicRolloutPlanV1(
            protocol=payload["protocol"],  # type: ignore[index]
            base_seed=payload["base_seed"],  # type: ignore[index]
            opponent_ids=tuple(payload["opponent_ids"]),  # type: ignore[index]
            opponent_families=families,
            games=games,
            pool_manifest_path=payload.get("pool_manifest_path"),  # type: ignore[union-attr]
            pool_manifest_sha256=payload.get("pool_manifest_sha256"),  # type: ignore[union-attr]
            pool_manifest_semantic_sha256=payload.get("pool_manifest_semantic_sha256"),  # type: ignore[union-attr]
        )
    except (KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, NativePublicRolloutCollectorError):
            raise
        raise NativePublicRolloutCollectorError("manifest plan is malformed") from exc


def load_native_public_rollout_manifest_v1(path: Path | str) -> dict[str, object]:
    """Reload a dry-run manifest and re-derive its safety bindings.

    The loader is intentionally strict: it rejects authority mutations,
    readiness flips, plan/hash drift, and any attempt to turn a dry-run
    manifest into a collection result.  It does not launch any process.
    """

    source = Path(path)
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise NativePublicRolloutCollectorError("manifest cannot be read as JSON") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_V1:
        raise NativePublicRolloutCollectorError("manifest schema is invalid")
    stored = payload.get("manifest_sha256")
    if type(stored) is not str or stored != _semantic_sha(
        "mage_ptcg:native-public-rollout-manifest:v1",
        {key: value for key, value in payload.items() if key != "manifest_sha256"},
    ):
        raise NativePublicRolloutCollectorError("manifest hash does not verify")
    if payload.get("dry_run") is not True or payload.get("collection_started") is not False:
        raise NativePublicRolloutCollectorError("manifest is not a dry-run")
    if payload.get("ready_for_evaluation") is not False or payload.get("processes_launched") is not False:
        raise NativePublicRolloutCollectorError("dry-run readiness or execution flag is unsafe")
    if payload.get("authority") != _AUTHORITY_FALSE:
        raise NativePublicRolloutCollectorError("manifest authority must remain all false")
    identity_payload = payload.get("identity")
    if not isinstance(identity_payload, Mapping):
        raise NativePublicRolloutCollectorError("manifest identity is missing")
    identity = NativePublicRolloutIdentityV1(**dict(identity_payload))
    authorization = _authorization_from_dict(payload.get("authorization"))
    plan = _plan_from_dict(payload.get("plan"))
    if payload.get("plan_sha256") != plan.plan_sha256:
        raise NativePublicRolloutCollectorError("manifest plan SHA does not verify")
    verified = payload.get("authorization_verified")
    if type(verified) is not bool:
        raise NativePublicRolloutCollectorError("manifest authorization verification is missing")
    if verified:
        _validate_authorization_root(identity=identity, authorization=authorization)
        _validate_pool_binding(identity=identity, authorization=authorization, plan=plan)
    expected_status = "DRY_RUN" if verified else "BLOCKED"
    if payload.get("status") != expected_status or payload.get("ready_for_collection") is not verified:
        raise NativePublicRolloutCollectorError("manifest authorization/status binding is invalid")
    if payload.get("teacher_labels_allowed") is not False:
        raise NativePublicRolloutCollectorError("teacher labels are forbidden")
    if payload.get("records_present") is not False or payload.get("records_sha256") is not None:
        raise NativePublicRolloutCollectorError("dry-run manifest cannot claim collected records")
    # Re-serialize the typed identity/authorization/plan to ensure the loader
    # actually exercised each constructor; returning the original canonical
    # bytes keeps unknown future metadata visible to downstream audit tools.
    if identity.to_dict() != dict(identity_payload):
        raise NativePublicRolloutCollectorError("manifest identity normalization drift")
    if authorization.to_dict() != dict(payload["authorization"]):
        raise NativePublicRolloutCollectorError("manifest authorization normalization drift")
    if plan.to_dict() != dict(payload["plan"]):
        raise NativePublicRolloutCollectorError("manifest plan normalization drift")
    return payload


def _write_new_json(path: Path, payload: Mapping[str, object]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = _canonical_bytes(payload) + b"\n"
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    try:
        with temporary.open("xb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)
        temporary.unlink()
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return hashlib.sha256(raw).hexdigest()


def materialize_native_public_rollout_dry_run_v1(
    *,
    output_manifest: Path | str,
    identity: NativePublicRolloutIdentityV1,
    authorization: NativePublicRolloutAuthorizationV1,
    plan: NativePublicRolloutPlanV1,
    records: Sequence[PublicRolloutRecordV1] = (),
    repo_root: Path | str | None = None,
) -> dict[str, object]:
    """Write one new dry-run manifest without starting a collection process."""

    destination = Path(output_manifest)
    if repo_root is None:
        raise NativePublicRolloutCollectorError(
            "repo_root is required for a contained dry-run output"
        )
    root = Path(repo_root).resolve()
    resolved = destination.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise NativePublicRolloutCollectorError(
            "output manifest must be contained within repo_root"
        ) from exc
    if resolved == root:
        raise NativePublicRolloutCollectorError("output manifest cannot equal repo_root")
    payload = build_native_public_rollout_dry_run_v1(
        identity=identity, authorization=authorization, plan=plan, records=records
    )
    file_sha = _write_new_json(destination, payload)
    return {
        "status": payload["status"],
        "manifest_path": str(destination.resolve()),
        "manifest_file_sha256": file_sha,
        "ready_for_collection": payload["ready_for_collection"],
        "ready_for_evaluation": False,
        "processes_launched": False,
        "collection_started": False,
    }


__all__ = [
    "NativePublicRolloutAuthorizationV1",
    "NativePublicRolloutCollectorError",
    "NativePublicRolloutGameV1",
    "NativePublicRolloutIdentityV1",
    "NativePublicRolloutPlanV1",
    "PublicRolloutRecordV1",
    "build_common24_plan_v1",
    "build_native_public_rollout_dry_run_v1",
    "materialize_native_public_rollout_dry_run_v1",
    "load_native_public_rollout_manifest_v1",
    "validate_public_rollout_records_v1",
    "validate_complete_public_rollout_snapshot_v1",
]
