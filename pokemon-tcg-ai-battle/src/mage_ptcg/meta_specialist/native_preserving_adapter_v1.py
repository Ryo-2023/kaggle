"""Native-preserving policy wrapper used by research-only candidates."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import sys
from typing import Any, Callable, Sequence


class NativePreservingAdapterError(ValueError):
    """Raised when the wrapper contract or identity is malformed."""


@dataclass(frozen=True, slots=True)
class NativeScoreConfigV1:
    """Hash-bound, bounded score biases for a native policy adapter.

    The adapter is deliberately small: it can add a finite bias to a native
    option score, but it cannot invent a candidate outside the native option
    list or change non-main selections.  Keeping the mapping in a canonical
    object makes every research candidate reproducible and auditable.
    """

    biases: tuple[tuple[str, float], ...]

    @classmethod
    def from_mapping(cls, biases: object) -> "NativeScoreConfigV1":
        if not isinstance(biases, dict):
            raise NativePreservingAdapterError("biases must be an object")
        rows: list[tuple[str, float]] = []
        for key, value in biases.items():
            if type(key) is not str or not key.strip():
                raise NativePreservingAdapterError("bias keys must be non-empty strings")
            if type(value) not in (int, float) or not math.isfinite(float(value)):
                raise NativePreservingAdapterError("bias values must be finite numbers")
            if abs(float(value)) > 1000.0:
                raise NativePreservingAdapterError("bias values exceed bounded range")
            rows.append((key.strip().upper(), float(value)))
        rows.sort(key=lambda item: item[0])
        return cls(tuple(rows))

    def bias_for(self, option_type: object) -> float:
        name = _option_type_name(option_type)
        return dict(self.biases).get(name, 0.0)

    @property
    def sha256(self) -> str:
        payload = json.dumps(
            {"schema": "native-score-config-v1", "biases": list(self.biases)},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


def _option_type_name(value: object) -> str:
    """Normalize enum/string option type values without relying on a private API."""

    if hasattr(value, "name"):
        value = getattr(value, "name")
    text = str(value).strip().upper()
    if text.startswith("OPTIONTYPE."):
        text = text.split(".", 1)[1]
    return text


def _selection_context(observation: dict[str, Any], native_observation: object | None = None) -> str | None:
    selection = observation.get("select") if isinstance(observation, dict) else None
    context = selection.get("context") if isinstance(selection, dict) else None
    if context is None and native_observation is not None:
        native_select = getattr(native_observation, "select", None)
        context = getattr(native_select, "context", None)
    if context is None:
        return None
    return _option_type_name(context)


def load_native_module_v1(path: str | Path) -> object:
    """Load a native ``main.py``-like module without replacing repository modules.

    The loader is research-only.  It uses a source-hash module name, does not
    prepend the target directory to ``sys.path``, and restores common entrypoint
    aliases after execution.  A module must expose a callable ``agent``.
    """

    source = Path(path)
    if not source.is_file():
        raise NativePreservingAdapterError(f"native module is not a regular file: {source}")
    source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    name = f"_mage_native_candidate_{source_sha[:24]}"
    spec = importlib.util.spec_from_file_location(name, source)
    if spec is None or spec.loader is None:
        raise NativePreservingAdapterError("unable to create native module spec")
    module = importlib.util.module_from_spec(spec)
    old_aliases = {key: sys.modules.get(key) for key in ("main", "__main__", "agents")}
    try:
        sys.modules[name] = module
        spec.loader.exec_module(module)
    except Exception as exc:
        sys.modules.pop(name, None)
        raise NativePreservingAdapterError(f"native module import failed: {exc}") from exc
    finally:
        for key, value in old_aliases.items():
            if value is None:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = value
    agent = getattr(module, "agent", None)
    if not callable(agent):
        sys.modules.pop(name, None)
        raise NativePreservingAdapterError("native module must expose callable agent")
    setattr(module, "__native_source_sha256__", source_sha)
    return module


def _require_sha(value: object, name: str) -> str:
    if type(value) is not str or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise NativePreservingAdapterError(f"{name} must be a lowercase SHA-256 string")
    return value


@dataclass(frozen=True, slots=True)
class NativePolicyCoverageV1:
    native_calls: int
    override_attempts: int
    override_applied: int
    fallbacks: int
    skipped: int
    invalid_candidates: int
    eligibility_errors: int


class NativePreservingPolicyV1:
    """Call native first, and apply a bounded override only when eligible.

    ``override`` receives the raw observation and the native action. Returning
    ``None`` or an illegal action means exact native fallback. The wrapper does
    not repair a native action; it preserves the baseline's own behavior.
    """

    def __init__(
        self,
        *,
        native_agent: Callable[[dict[str, Any]], Sequence[int]],
        override: Callable[[dict[str, Any], Sequence[int]], Sequence[int] | None],
        eligibility: Callable[[dict[str, Any]], bool],
        baseline_policy_sha256: str,
        candidate_config_sha256: str,
    ) -> None:
        if not callable(native_agent) or not callable(override) or not callable(eligibility):
            raise NativePreservingAdapterError("native_agent, override, eligibility must be callable")
        self._native_agent = native_agent
        self._override = override
        self._eligibility = eligibility
        self.baseline_policy_sha256 = _require_sha(baseline_policy_sha256, "baseline_policy_sha256")
        self.candidate_config_sha256 = _require_sha(candidate_config_sha256, "candidate_config_sha256")
        self._native_calls = 0
        self._override_attempts = 0
        self._override_applied = 0
        self._fallbacks = 0
        self._skipped = 0
        self._invalid_candidates = 0
        self._eligibility_errors = 0

    def _selection_bounds(self, observation: dict[str, Any]) -> tuple[int, int, int] | None:
        selection = observation.get("select") if isinstance(observation, dict) else None
        if selection is None:
            return None
        if not isinstance(selection, dict):
            raise NativePreservingAdapterError("select must be an object or None")
        options = selection.get("option")
        if not isinstance(options, list):
            raise NativePreservingAdapterError("select.option must be a list")
        minimum = selection.get("minCount", 0)
        maximum = selection.get("maxCount", minimum or 1)
        if type(minimum) is not int or type(maximum) is not int or minimum < 0 or maximum < minimum:
            raise NativePreservingAdapterError("select minCount/maxCount are invalid")
        if maximum > len(options):
            maximum = len(options)
        return len(options), minimum, maximum

    @staticmethod
    def _is_legal_candidate(candidate: object, bounds: tuple[int, int, int]) -> bool:
        if not isinstance(candidate, (list, tuple)):
            return False
        option_count, minimum, maximum = bounds
        if any(type(index) is not int or index < 0 or index >= option_count for index in candidate):
            return False
        if len(set(candidate)) != len(candidate) or not minimum <= len(candidate) <= maximum:
            return False
        return True

    def __call__(self, observation: dict[str, Any]) -> list[int]:
        native_action = list(self._native_agent(observation))
        self._native_calls += 1
        bounds = self._selection_bounds(observation)
        if bounds is None:
            self._skipped += 1
            return native_action
        try:
            eligible = bool(self._eligibility(observation))
        except Exception:
            self._eligibility_errors += 1
            self._fallbacks += 1
            return native_action
        if not eligible:
            self._skipped += 1
            return native_action
        self._override_attempts += 1
        try:
            candidate = self._override(observation, list(native_action))
        except Exception:
            self._fallbacks += 1
            return native_action
        if candidate is None:
            self._fallbacks += 1
            return native_action
        if not self._is_legal_candidate(candidate, bounds):
            self._invalid_candidates += 1
            self._fallbacks += 1
            return native_action
        candidate_list = list(candidate)
        if candidate_list == native_action:
            self._fallbacks += 1
            return native_action
        self._override_applied += 1
        return candidate_list

    def snapshot(self) -> NativePolicyCoverageV1:
        return NativePolicyCoverageV1(
            native_calls=self._native_calls,
            override_attempts=self._override_attempts,
            override_applied=self._override_applied,
            fallbacks=self._fallbacks,
            skipped=self._skipped,
            invalid_candidates=self._invalid_candidates,
            eligibility_errors=self._eligibility_errors,
        )

    def reset_coverage(self) -> None:
        self._native_calls = 0
        self._override_attempts = 0
        self._override_applied = 0
        self._fallbacks = 0
        self._skipped = 0
        self._invalid_candidates = 0
        self._eligibility_errors = 0


def _native_observation_for(
    observation: dict[str, Any], native_module: object,
) -> object | None:
    """Convert an actor-visible observation through an explicitly public hook."""

    existing = observation.get("_native_object") if isinstance(observation, dict) else None
    if existing is not None:
        return existing
    converter = getattr(native_module, "to_observation_class", None)
    if not callable(converter):
        return None
    try:
        return converter(observation)
    except Exception:
        return None


def _score_value(raw: object) -> float | None:
    if type(raw) in (int, float) and math.isfinite(float(raw)):
        return float(raw)
    if isinstance(raw, (tuple, list)) and raw:
        value = raw[0]
        if type(value) in (int, float) and math.isfinite(float(value)):
            return float(value)
    if isinstance(raw, dict):
        for key in ("score", "priority", "value"):
            value = raw.get(key)
            if type(value) in (int, float) and math.isfinite(float(value)):
                return float(value)
    return None


def build_native_score_policy_v1(
    *,
    native_agent: Callable[[dict[str, Any]], Sequence[int]],
    native_module: object,
    config: NativeScoreConfigV1,
    baseline_policy_sha256: str,
) -> NativePreservingPolicyV1:
    """Build a conservative native score-bias candidate.

    Only ``MAIN`` selections are eligible.  The native module supplies the
    option score; this adapter adds the hash-bound bias, keeps deterministic
    index tie-breaking, and delegates all malformed/unsupported cases to the
    exact native action through :class:`NativePreservingPolicyV1`.
    """

    if not isinstance(config, NativeScoreConfigV1):
        raise NativePreservingAdapterError("config must be NativeScoreConfigV1")
    baseline = _require_sha(baseline_policy_sha256, "baseline_policy_sha256")

    def _converted(observation: dict[str, Any]) -> object | None:
        return _native_observation_for(observation, native_module)

    def eligible(observation: dict[str, Any]) -> bool:
        native_observation = _converted(observation)
        return _selection_context(observation, native_observation) == "MAIN" and native_observation is not None

    def override(observation: dict[str, Any], native_action: Sequence[int]) -> list[int] | None:
        native_observation = _converted(observation)
        if native_observation is None or _selection_context(observation, native_observation) != "MAIN":
            return None
        selection = observation.get("select") if isinstance(observation, dict) else None
        if not isinstance(selection, dict):
            return None
        options = selection.get("option")
        native_select = getattr(native_observation, "select", None)
        native_options = getattr(native_select, "option", None)
        if not isinstance(options, list) or not isinstance(native_options, (list, tuple)):
            return None
        if len(options) != len(native_options):
            return None
        scorer = getattr(native_module, "score_option", None)
        if not callable(scorer):
            return None
        scored: list[tuple[float, int]] = []
        for index, option in enumerate(native_options):
            try:
                score = _score_value(scorer(native_observation, option))
                if score is None:
                    return None
                score += config.bias_for(getattr(option, "type", None))
                scored.append((score, index))
            except Exception:
                return None
        if not scored:
            return None
        minimum = selection.get("minCount", 0)
        maximum = selection.get("maxCount", minimum or 1)
        if type(minimum) is not int or type(maximum) is not int or minimum < 1 or maximum < minimum:
            return None
        maximum = min(maximum, len(scored))
        if maximum < minimum:
            return None
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [index for _, index in scored[:minimum]]

    return NativePreservingPolicyV1(
        native_agent=native_agent,
        override=override,
        eligibility=eligible,
        baseline_policy_sha256=baseline,
        candidate_config_sha256=config.sha256,
    )


def build_native_guarded_score_policy_v1(
    *,
    native_agent: Callable[[dict[str, Any]], Sequence[int]],
    native_module: object,
    config: NativeScoreConfigV1,
    baseline_policy_sha256: str,
    min_score_gain: float = 1_000.0,
) -> NativePreservingPolicyV1:
    """Build a native-first score candidate with a strict improvement guard.

    The older score adapter ranked every eligible option from scratch.  That
    can replace native tie/negative-score handling too broadly.  This variant
    is intentionally narrower: only a single-choice ``MAIN`` selection is
    eligible, and the alternative must beat the exact native-selected option
    by ``min_score_gain`` after the bounded bias.  Multi-select, malformed
    native actions, unknown score outputs, and all non-MAIN contexts return the
    native action unchanged through :class:`NativePreservingPolicyV1`.
    """

    if not isinstance(config, NativeScoreConfigV1):
        raise NativePreservingAdapterError("config must be NativeScoreConfigV1")
    baseline = _require_sha(baseline_policy_sha256, "baseline_policy_sha256")
    if type(min_score_gain) not in (int, float) or not math.isfinite(float(min_score_gain)):
        raise NativePreservingAdapterError("min_score_gain must be a finite number")
    if float(min_score_gain) < 0.0 or float(min_score_gain) > 100_000.0:
        raise NativePreservingAdapterError("min_score_gain is outside bounded range")

    def _converted(observation: dict[str, Any]) -> object | None:
        return _native_observation_for(observation, native_module)

    def eligible(observation: dict[str, Any]) -> bool:
        native_observation = _converted(observation)
        selection = observation.get("select") if isinstance(observation, dict) else None
        if not isinstance(selection, dict):
            return False
        minimum = selection.get("minCount", 0)
        maximum = selection.get("maxCount", minimum or 1)
        return (
            _selection_context(observation, native_observation) == "MAIN"
            and native_observation is not None
            and type(minimum) is int
            and type(maximum) is int
            and minimum == maximum == 1
        )

    def override(observation: dict[str, Any], native_action: Sequence[int]) -> list[int] | None:
        native_observation = _converted(observation)
        if native_observation is None or _selection_context(observation, native_observation) != "MAIN":
            return None
        selection = observation.get("select") if isinstance(observation, dict) else None
        native_select = getattr(native_observation, "select", None)
        options = selection.get("option") if isinstance(selection, dict) else None
        native_options = getattr(native_select, "option", None)
        if not isinstance(options, list) or not isinstance(native_options, (list, tuple)):
            return None
        if len(options) != len(native_options) or len(native_action) != 1:
            return None
        native_index = native_action[0]
        if type(native_index) is not int or not 0 <= native_index < len(native_options):
            return None
        scorer = getattr(native_module, "score_option", None)
        if not callable(scorer):
            return None
        scored: list[tuple[float, int]] = []
        for index, option in enumerate(native_options):
            try:
                score = _score_value(scorer(native_observation, option))
                if score is None:
                    return None
                score += config.bias_for(getattr(option, "type", None))
                scored.append((score, index))
            except Exception:
                return None
        if not scored:
            return None
        native_score = next((score for score, index in scored if index == native_index), None)
        if native_score is None:
            return None
        scored.sort(key=lambda item: (-item[0], item[1]))
        best_score, best_index = scored[0]
        if best_index == native_index or best_score - native_score < float(min_score_gain):
            return None
        return [best_index]

    return NativePreservingPolicyV1(
        native_agent=native_agent,
        override=override,
        eligibility=eligible,
        baseline_policy_sha256=baseline,
        candidate_config_sha256=config.sha256,
    )
