"""Research-only public-state advantage lookup with an immutable native fallback.

The module deliberately has no evaluator, trainer, or submission entrypoint.  It
turns a strict ``META_TRAIN`` JSONL ledger into a canonical, hash-bound table
and exposes a native-first policy wrapper for the smallest safe action surface:
single-choice ``MAIN`` selections.  Unknown state/action keys, malformed
observations, multi-select/ordered selections, and non-finite values all return
the native action unchanged.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import math
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Mapping, Sequence

from .meta_distribution_v1 import MetaDistributionError, load_meta_distribution_manifest_v1
from .native_preserving_adapter_v1 import (
    NativePolicyCoverageV1,
    NativePreservingAdapterError,
    NativePreservingPolicyV1,
)


SCHEMA_V1 = "meta-specialist-native-public-advantage-v1"
ROW_SCHEMA_V1 = "meta-specialist-native-public-advantage-row-v1"
_ROW_KEYS = frozenset(
    {"state_digest", "action_key", "opponent_id", "seat", "split", "outcome", "weight"}
)
_OUTCOME_VALUE = {"win": 1.0, "draw": 0.5, "loss": 0.0, "fault": 0.0}
_SHA_CHARS = frozenset("0123456789abcdef")
_OVERRIDE_MARGIN_V1 = 0.05


class NativePublicAdvantageError(ValueError):
    """Raised when an advantage source or candidate violates the closed contract."""


def _require_sha(value: object, name: str) -> str:
    if type(value) is not str or len(value) != 64 or any(char not in _SHA_CHARS for char in value):
        raise NativePublicAdvantageError(f"{name} must be a lowercase SHA-256 hex string")
    return value


def _finite(value: object, name: str, *, positive: bool = False) -> float:
    if type(value) not in (int, float) or isinstance(value, bool):
        raise NativePublicAdvantageError(f"{name} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise NativePublicAdvantageError(f"{name} must be finite")
    if positive and result <= 0.0:
        raise NativePublicAdvantageError(f"{name} must be positive")
    return result


def _digest(value: object, name: str) -> str:
    if type(value) is not str or len(value) != 64 or any(char not in _SHA_CHARS for char in value):
        raise NativePublicAdvantageError(f"{name} must be a lowercase SHA-256 digest")
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
        raise NativePublicAdvantageError("value cannot be canonicalized") from exc


def _semantic_sha(domain: str, value: object) -> str:
    # Domain and canonical JSON are both newline-free.  The separator prevents
    # a table hash from being confused with a hash of a different artifact.
    return hashlib.sha256(domain.encode("ascii") + b"\0" + _canonical_bytes(value)).hexdigest()


def _freeze(value: object) -> object:
    """Return a recursively immutable, deterministic copy for table metadata."""

    if isinstance(value, Mapping):
        if any(type(key) is not str for key in value):
            raise NativePublicAdvantageError("coverage mapping keys must be strings")
        return MappingProxyType(
            {key: _freeze(value[key]) for key in sorted(value)}
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, (set, frozenset)):
        raise NativePublicAdvantageError("coverage metadata must not contain sets")
    if type(value) in (float, int) and not isinstance(value, bool) and not math.isfinite(float(value)):
        raise NativePublicAdvantageError("coverage metadata must be finite")
    return value


def _thaw(value: object) -> object:
    """Convert internal immutable metadata back to JSON-compatible values."""

    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise NativePublicAdvantageError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_nonfinite(token: str) -> object:
    raise NativePublicAdvantageError(f"non-finite JSON number: {token}")


def _read_rows(path: Path | str) -> list[dict[str, object]]:
    source = Path(path)
    try:
        lines = source.read_bytes().splitlines()
    except OSError as exc:
        raise NativePublicAdvantageError(f"cannot read source rows: {source}") from exc
    if not lines:
        raise NativePublicAdvantageError("source rows must not be empty")
    rows: list[dict[str, object]] = []
    for line_number, raw_line in enumerate(lines, 1):
        if not raw_line.strip():
            raise NativePublicAdvantageError(f"blank JSONL row at line {line_number}")
        try:
            value = json.loads(
                raw_line.decode("utf-8"),
                object_pairs_hook=_reject_duplicate_pairs,
                parse_constant=_reject_nonfinite,
            )
        except NativePublicAdvantageError:
            raise
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise NativePublicAdvantageError(f"invalid JSONL row at line {line_number}") from exc
        if type(value) is not dict:
            raise NativePublicAdvantageError(f"JSONL row {line_number} must be an object")
        rows.append(value)
    return rows


def _manifest_binding(path: Path | str):
    manifest_path = Path(path)
    try:
        manifest = load_meta_distribution_manifest_v1(manifest_path, verify_sources=True)
        manifest_sha = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    except (MetaDistributionError, OSError, ValueError) as exc:
        raise NativePublicAdvantageError(f"verified meta manifest rejected: {manifest_path}") from exc
    authority_fields = (
        "training_authority",
        "promotion_authority",
        "submission_authority",
    )
    if not bool(getattr(manifest, "research_only", False)) or any(
        getattr(manifest, field, None) is not False for field in authority_fields
    ):
        raise NativePublicAdvantageError("meta manifest grants authority")
    return manifest, manifest_sha


def _validate_row(
    raw: Mapping[str, object],
    *,
    manifest_rows: Mapping[str, object],
    seen: set[tuple[object, ...]],
) -> dict[str, object]:
    keys = set(raw)
    if keys != _ROW_KEYS:
        private = sorted(
            key for key in keys - _ROW_KEYS if any(token in str(key).lower() for token in ("private", "hidden", "hand"))
        )
        if private:
            raise NativePublicAdvantageError(f"private row fields are forbidden: {private[0]}")
        unknown = sorted(keys - _ROW_KEYS)
        missing = sorted(_ROW_KEYS - keys)
        raise NativePublicAdvantageError(f"unsupported or missing row fields: {unknown or missing}")
    state_digest = _digest(raw["state_digest"], "state_digest")
    action_key = _digest(raw["action_key"], "action_key")
    opponent_id = raw["opponent_id"]
    if type(opponent_id) is not str or not opponent_id.strip():
        raise NativePublicAdvantageError("opponent_id must be a non-empty string")
    seat = raw["seat"]
    if type(seat) is not int or seat not in (0, 1):
        raise NativePublicAdvantageError("seat must be 0 or 1")
    split = raw["split"]
    if split != "META_TRAIN":
        raise NativePublicAdvantageError("source rows must use exact split META_TRAIN")
    outcome = raw["outcome"]
    if outcome not in _OUTCOME_VALUE:
        raise NativePublicAdvantageError("outcome must be win, draw, loss, or fault")
    weight = _finite(raw["weight"], "weight", positive=True)
    manifest_row = manifest_rows.get(opponent_id)
    if manifest_row is None:
        raise NativePublicAdvantageError("source opponent is absent from verified meta manifest")
    if getattr(manifest_row, "split", None) != "META_TRAIN":
        raise NativePublicAdvantageError("META_DEV/META_FINAL opponent cannot supply advantage rows")
    if getattr(manifest_row, "usage_boundary", None) not in {
        "training_local",
        "training_local_and_eval",
    }:
        raise NativePublicAdvantageError("opponent usage boundary is not training-eligible")
    if getattr(manifest_row, "training_allowed", None) is not True:
        raise NativePublicAdvantageError("opponent does not have training permission")
    if getattr(manifest_row, "behavior_allowed", None) is not True:
        raise NativePublicAdvantageError("opponent does not have behavior permission")
    if getattr(manifest_row, "submission_allowed", None) is not False:
        raise NativePublicAdvantageError("submission authority is forbidden for source opponent")
    # The source schema has no game id by design.  Reject an exact canonical
    # duplicate, while allowing the same public state/action to recur in
    # separate episodes (different outcome, seat, or weight).
    identity = (state_digest, action_key, opponent_id, seat, split, outcome, weight)
    if identity in seen:
        raise NativePublicAdvantageError("duplicate source record")
    seen.add(identity)
    return {
        "state_digest": state_digest,
        "action_key": action_key,
        "opponent_id": opponent_id,
        "seat": seat,
        "split": "META_TRAIN",
        "outcome": outcome,
        "weight": weight,
        "score": _OUTCOME_VALUE[outcome],
    }


@dataclass(frozen=True, slots=True)
class PublicAdvantageEntryV1:
    state_digest: str
    action_key: str
    support: int
    total_weight: float
    mean_outcome: float
    state_mean_outcome: float
    raw_delta: float
    delta: float

    def __post_init__(self) -> None:
        _digest(self.state_digest, "entry.state_digest")
        _digest(self.action_key, "entry.action_key")
        if type(self.support) is not int or self.support <= 0:
            raise NativePublicAdvantageError("entry.support must be positive")
        _finite(self.total_weight, "entry.total_weight", positive=True)
        for name in ("mean_outcome", "state_mean_outcome", "raw_delta", "delta"):
            _finite(getattr(self, name), f"entry.{name}")

    @property
    def outcome_mean(self) -> float:
        return self.mean_outcome

    @property
    def baseline_outcome(self) -> float:
        return self.state_mean_outcome

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class PublicAdvantageTableV1:
    schema_version: str
    iteration: int
    baseline_policy_sha256: str
    meta_manifest_sha256: str
    delta_cap: float
    min_support: int
    entries: tuple[PublicAdvantageEntryV1, ...]
    coverage_summary: Mapping[str, object]
    table_sha256: str
    training_authority: bool = False
    promotion_authority: bool = False
    submission_authority: bool = False
    execution_authority: bool = False
    research_only: bool = True

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_V1:
            raise NativePublicAdvantageError("wrong public advantage schema")
        if type(self.iteration) is not int or self.iteration < 0:
            raise NativePublicAdvantageError("iteration must be a nonnegative integer")
        _require_sha(self.baseline_policy_sha256, "baseline_policy_sha256")
        _require_sha(self.meta_manifest_sha256, "meta_manifest_sha256")
        _finite(self.delta_cap, "delta_cap", positive=True)
        if type(self.min_support) is not int or self.min_support <= 0:
            raise NativePublicAdvantageError("min_support must be a positive integer")
        _require_sha(self.table_sha256, "table_sha256")
        authority_values = tuple(
            getattr(self, name)
            for name in ("training_authority", "promotion_authority", "submission_authority", "execution_authority")
        )
        if type(self.research_only) is not bool or not self.research_only or any(
            type(value) is not bool or value for value in authority_values
        ):
            raise NativePublicAdvantageError("public advantage table must remain research-only")
        entries = tuple(self.entries)
        if any(type(entry) is not PublicAdvantageEntryV1 for entry in entries):
            raise NativePublicAdvantageError("table entries must be PublicAdvantageEntryV1")
        object.__setattr__(self, "entries", entries)
        coverage = _freeze(self.coverage_summary)
        if not isinstance(coverage, Mapping):
            raise NativePublicAdvantageError("coverage_summary must be a mapping")
        object.__setattr__(self, "coverage_summary", coverage)
        keys = [(entry.state_digest, entry.action_key) for entry in entries]
        if keys != sorted(keys) or len(keys) != len(set(keys)):
            raise NativePublicAdvantageError("table entries must be sorted and unique")
        expected_sha = _semantic_sha(
            "mage-ptcg:native-public-advantage-table:v1",
            _table_payload(
                iteration=self.iteration,
                baseline_policy_sha256=self.baseline_policy_sha256,
                meta_manifest_sha256=self.meta_manifest_sha256,
                delta_cap=self.delta_cap,
                min_support=self.min_support,
                entries=entries,
                coverage_summary=coverage,
            ),
        )
        if self.table_sha256 != expected_sha:
            raise NativePublicAdvantageError("table_sha256 does not match canonical table payload")

    @property
    def authority_false(self) -> bool:
        return not any(
            bool(getattr(self, name))
            for name in ("training_authority", "promotion_authority", "submission_authority", "execution_authority")
        )

    @property
    def canonical_sha256(self) -> str:
        return self.table_sha256

    @property
    def sha256(self) -> str:
        return self.table_sha256

    def entry(self, state_digest: object, action_key: object) -> PublicAdvantageEntryV1 | None:
        if type(state_digest) is not str or type(action_key) is not str:
            return None
        target = (state_digest, action_key)
        for entry in self.entries:
            current = (entry.state_digest, entry.action_key)
            if current == target:
                return entry
            if current > target:
                break
        return None

    def to_dict(self, *, include_sha: bool = True) -> dict[str, object]:
        payload: dict[str, object] = {
            "schema_version": self.schema_version,
            "iteration": self.iteration,
            "baseline_policy_sha256": self.baseline_policy_sha256,
            "meta_manifest_sha256": self.meta_manifest_sha256,
            "delta_cap": self.delta_cap,
            "min_support": self.min_support,
            "entries": [entry.to_dict() for entry in self.entries],
            "coverage_summary": _thaw(self.coverage_summary),
            "training_authority": self.training_authority,
            "promotion_authority": self.promotion_authority,
            "submission_authority": self.submission_authority,
            "execution_authority": self.execution_authority,
            "research_only": self.research_only,
        }
        if include_sha:
            payload["table_sha256"] = self.table_sha256
        return payload

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> "PublicAdvantageTableV1":
        """Reload and self-verify a canonical table payload."""

        if not isinstance(raw, Mapping):
            raise NativePublicAdvantageError("table payload must be an object")
        entries_raw = raw.get("entries")
        if not isinstance(entries_raw, (list, tuple)):
            raise NativePublicAdvantageError("table entries must be a list")
        entries: list[PublicAdvantageEntryV1] = []
        for item in entries_raw:
            if not isinstance(item, Mapping):
                raise NativePublicAdvantageError("table entry must be an object")
            try:
                entries.append(PublicAdvantageEntryV1(**dict(item)))
            except TypeError as exc:
                raise NativePublicAdvantageError("table entry fields are invalid") from exc
        required = {
            "schema_version",
            "iteration",
            "baseline_policy_sha256",
            "meta_manifest_sha256",
            "delta_cap",
            "min_support",
            "coverage_summary",
            "table_sha256",
        }
        if not required.issubset(raw):
            raise NativePublicAdvantageError("table payload is missing required fields")
        return cls(
            schema_version=raw["schema_version"],
            iteration=raw["iteration"],
            baseline_policy_sha256=raw["baseline_policy_sha256"],
            meta_manifest_sha256=raw["meta_manifest_sha256"],
            delta_cap=raw["delta_cap"],
            min_support=raw["min_support"],
            entries=tuple(entries),
            coverage_summary=raw["coverage_summary"],
            table_sha256=raw["table_sha256"],
            training_authority=raw.get("training_authority", False),
            promotion_authority=raw.get("promotion_authority", False),
            submission_authority=raw.get("submission_authority", False),
            execution_authority=raw.get("execution_authority", False),
            research_only=raw.get("research_only", True),
        )


def _table_payload(
    *,
    iteration: int,
    baseline_policy_sha256: str,
    meta_manifest_sha256: str,
    delta_cap: float,
    min_support: int,
    entries: Sequence[PublicAdvantageEntryV1],
    coverage_summary: Mapping[str, object],
) -> dict[str, object]:
    return {
        "schema_version": SCHEMA_V1,
        "iteration": iteration,
        "baseline_policy_sha256": baseline_policy_sha256,
        "meta_manifest_sha256": meta_manifest_sha256,
        "delta_cap": delta_cap,
        "min_support": min_support,
        "entries": [entry.to_dict() for entry in entries],
        "coverage_summary": _thaw(coverage_summary),
        "training_authority": False,
        "promotion_authority": False,
        "submission_authority": False,
        "execution_authority": False,
        "research_only": True,
    }


def build_public_advantage_table_v1(
    *,
    source_rows_path: str | Path,
    meta_manifest_path: str | Path,
    baseline_policy_sha256: str,
    iteration: int,
    delta_cap: float = 0.25,
    min_support: int = 4,
) -> PublicAdvantageTableV1:
    """Build a deterministic, META_TRAIN-only public advantage table."""

    baseline_policy_sha256 = _require_sha(baseline_policy_sha256, "baseline_policy_sha256")
    if type(iteration) is not int or iteration < 0:
        raise NativePublicAdvantageError("iteration must be a nonnegative integer")
    delta_cap = _finite(delta_cap, "delta_cap", positive=True)
    if type(min_support) is not int or min_support <= 0:
        raise NativePublicAdvantageError("min_support must be a positive integer")
    manifest, manifest_sha = _manifest_binding(meta_manifest_path)
    raw_rows = _read_rows(source_rows_path)
    seen: set[tuple[object, ...]] = set()
    rows = [
        _validate_row(raw, manifest_rows={row.opponent_id: row for row in manifest.rows}, seen=seen)
        for raw in raw_rows
    ]

    by_state: dict[str, list[dict[str, object]]] = {}
    by_action: dict[tuple[str, str], list[dict[str, object]]] = {}
    for row in rows:
        state = str(row["state_digest"])
        action = str(row["action_key"])
        by_state.setdefault(state, []).append(row)
        by_action.setdefault((state, action), []).append(row)

    entries: list[PublicAdvantageEntryV1] = []
    insufficient_support = 0
    for (state, action), group in sorted(by_action.items()):
        support = len(group)
        if support < min_support:
            insufficient_support += 1
            continue
        total_weight = sum(float(row["weight"]) for row in group)
        action_mean = sum(float(row["weight"]) * float(row["score"]) for row in group) / total_weight
        state_rows = by_state[state]
        state_weight = sum(float(row["weight"]) for row in state_rows)
        state_mean = sum(float(row["weight"]) * float(row["score"]) for row in state_rows) / state_weight
        raw_delta = action_mean - state_mean
        # A one-pseudocount shrinkage keeps a barely-supported signal close to
        # the state baseline while preserving the exact raw value as support
        # grows.  The cap is applied after shrinkage.
        shrinkage = support / (support + 1.0)
        delta = max(-delta_cap, min(delta_cap, raw_delta * shrinkage))
        entries.append(
            PublicAdvantageEntryV1(
                state_digest=state,
                action_key=action,
                support=support,
                total_weight=total_weight,
                mean_outcome=action_mean,
                state_mean_outcome=state_mean,
                raw_delta=raw_delta,
                delta=delta,
            )
        )
    outcomes = {key: sum(1 for row in rows if row["outcome"] == key) for key in _OUTCOME_VALUE}
    coverage = {
        "input_rows": len(raw_rows),
        "accepted_rows": len(rows),
        "meta_train_rows": len(rows),
        "state_count": len(by_state),
        "action_pairs": len(by_action),
        "supported_action_pairs": len(entries),
        "insufficient_support": insufficient_support,
        "opponent_count": len({str(row["opponent_id"]) for row in rows}),
        "seat_counts": {str(seat): sum(1 for row in rows if row["seat"] == seat) for seat in (0, 1)},
        "outcome_counts": outcomes,
        "private_state_features": False,
        "heldout_rows": 0,
    }
    payload = _table_payload(
        iteration=iteration,
        baseline_policy_sha256=baseline_policy_sha256,
        meta_manifest_sha256=manifest_sha,
        delta_cap=delta_cap,
        min_support=min_support,
        entries=entries,
        coverage_summary=coverage,
    )
    table_sha = _semantic_sha("mage-ptcg:native-public-advantage-table:v1", payload)
    return PublicAdvantageTableV1(
        schema_version=SCHEMA_V1,
        iteration=iteration,
        baseline_policy_sha256=baseline_policy_sha256,
        meta_manifest_sha256=manifest_sha,
        delta_cap=delta_cap,
        min_support=min_support,
        entries=tuple(entries),
        coverage_summary=coverage,
        table_sha256=table_sha,
    )


def _context_name(value: object) -> str:
    if hasattr(value, "name"):
        value = getattr(value, "name")
    text = str(value).strip().upper()
    if text.startswith("OPTIONTYPE."):
        text = text.split(".", 1)[1]
    return text


def _option_action_key(option: object) -> str | None:
    if isinstance(option, str):
        value = option
    elif isinstance(option, Mapping):
        value = option.get("action_key", option.get("digest"))
    else:
        return None
    if type(value) is not str or len(value) != 64 or any(char not in _SHA_CHARS for char in value):
        return None
    return value


class NativePublicAdvantagePolicyV1(NativePreservingPolicyV1):
    """Native-first adapter for bounded public-state single-choice overrides."""

    override_margin = _OVERRIDE_MARGIN_V1

    def __init__(
        self,
        *,
        native_agent: Callable[[dict[str, Any]], Sequence[int]],
        table: PublicAdvantageTableV1,
        baseline_policy_sha256: str,
        candidate_config_sha256: str,
    ) -> None:
        if type(table) is not PublicAdvantageTableV1:
            raise NativePublicAdvantageError("table must be PublicAdvantageTableV1")
        baseline_policy_sha256 = _require_sha(baseline_policy_sha256, "baseline_policy_sha256")
        candidate_config_sha256 = _require_sha(candidate_config_sha256, "candidate_config_sha256")
        if baseline_policy_sha256 != table.baseline_policy_sha256:
            raise NativePublicAdvantageError("baseline policy SHA differs from table binding")
        super().__init__(
            native_agent=native_agent,
            override=lambda _observation, native_action: native_action,
            eligibility=lambda _observation: False,
            baseline_policy_sha256=baseline_policy_sha256,
            candidate_config_sha256=candidate_config_sha256,
        )
        self.table = table
        self.table_sha256 = table.table_sha256
        self.research_only = True
        self.training_authority = False
        self.promotion_authority = False
        self.submission_authority = False
        self.execution_authority = False

    def __call__(self, observation: dict[str, Any]) -> list[int]:
        # Call the native policy before inspecting candidate fields.  This is
        # the baseline action returned for every fail-closed path below.
        native_action = list(self._native_agent(observation))
        self._native_calls += 1
        if not isinstance(observation, dict):
            self._skipped += 1
            return native_action
        selection = observation.get("select")
        if not isinstance(selection, Mapping):
            self._skipped += 1
            return native_action
        if _context_name(selection.get("context")) != "MAIN":
            self._skipped += 1
            return native_action
        options = selection.get("option")
        minimum = selection.get("minCount")
        maximum = selection.get("maxCount")
        if (
            not isinstance(options, list)
            or type(minimum) is not int
            or type(maximum) is not int
            or minimum != 1
            or maximum != 1
            or len(options) == 0
        ):
            self._skipped += 1
            return native_action
        action_keys = [_option_action_key(option) for option in options]
        if any(key is None for key in action_keys) or len(set(action_keys)) != len(action_keys):
            self._skipped += 1
            return native_action
        state_digest = observation.get("state_digest")
        if type(state_digest) is not str or any(char not in _SHA_CHARS for char in state_digest) or len(state_digest) != 64:
            self._fallbacks += 1
            return native_action
        if (
            not isinstance(native_action, list)
            or len(native_action) != 1
            or type(native_action[0]) is not int
            or not 0 <= native_action[0] < len(options)
        ):
            self._fallbacks += 1
            return native_action
        self._override_attempts += 1
        best: tuple[float, int] | None = None
        for index, action_key in enumerate(action_keys):
            assert action_key is not None
            entry = self.table.entry(state_digest, action_key)
            if entry is None or not math.isfinite(entry.delta) or entry.delta <= self.override_margin:
                continue
            candidate = (entry.delta, index)
            if best is None or candidate[0] > best[0] or (candidate[0] == best[0] and candidate[1] < best[1]):
                best = candidate
        if best is None or best[1] == native_action[0]:
            self._fallbacks += 1
            return native_action
        self._override_applied += 1
        return [best[1]]


def build_native_public_advantage_policy_v1(
    *,
    native_agent: Callable[[dict[str, Any]], Sequence[int]],
    table: PublicAdvantageTableV1,
    baseline_policy_sha256: str,
    candidate_config_sha256: str,
) -> NativePreservingPolicyV1:
    """Build a native-first, research-only public advantage policy."""

    if not callable(native_agent):
        raise NativePublicAdvantageError("native_agent must be callable")
    try:
        return NativePublicAdvantagePolicyV1(
            native_agent=native_agent,
            table=table,
            baseline_policy_sha256=baseline_policy_sha256,
            candidate_config_sha256=candidate_config_sha256,
        )
    except (NativePublicAdvantageError, NativePreservingAdapterError) as exc:
        if isinstance(exc, NativePublicAdvantageError):
            raise
        raise NativePublicAdvantageError(str(exc)) from exc


__all__ = [
    "NativePublicAdvantageError",
    "PublicAdvantageEntryV1",
    "PublicAdvantageTableV1",
    "NativePublicAdvantagePolicyV1",
    "build_public_advantage_table_v1",
    "build_native_public_advantage_policy_v1",
]
