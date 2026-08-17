"""Evaluation-only actual-cabt agent inventory and privacy-safe counters.

This module is intentionally not imported by ``main.py``.  It adapts existing
factories for offline actual-cabt evaluation and persists only aggregate,
non-identity-bearing runtime measurements.
"""

from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import time
from typing import Any, Callable, Literal, Mapping, Sequence

from mage_ptcg.distillation.contracts import digest


Classification = Literal[
    "RUNNABLE",
    "RUNNABLE_WITH_MODEL",
    "RUNNABLE_WITH_FALLBACK",
    "FALLBACK_ONLY",
    "NOT_A_RUNTIME_AGENT",
    "BLOCKED_BY_MISSING_CAPABILITY",
    "BLOCKED_BY_MISSING_ARTIFACT",
    "BLOCKED_BY_INVALID_ARTIFACT",
    "UNSAFE",
]


@dataclass(frozen=True, slots=True)
class AgentAvailability:
    """Static, public-safe availability record for one evaluation candidate."""

    agent_id: str
    agent_version: str
    classification: Classification
    factory_name: str | None
    artifact_hash: str | None
    artifact_purpose: str | None
    effective_policy: str
    reason: str | None
    config_hash: str

    def to_public_dict(self) -> dict[str, object]:
        return {
            "agent_id": self.agent_id,
            "agent_version": self.agent_version,
            "classification": self.classification,
            "factory": self.factory_name,
            "artifact_hash": self.artifact_hash,
            "artifact_purpose": self.artifact_purpose,
            "effective_policy": self.effective_policy,
            "reason": self.reason,
            "config_hash": self.config_hash,
        }


def _sha256_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _availability(
    agent_id: str,
    *,
    classification: Classification,
    factory_name: str | None,
    artifact_hash: str | None = None,
    artifact_purpose: str | None = None,
    effective_policy: str,
    reason: str | None = None,
) -> AgentAvailability:
    config = {
        "agent_id": agent_id,
        "agent_version": "actual-viability-v0",
        "classification": classification,
        "factory": factory_name,
        "artifact_hash": artifact_hash,
        "artifact_purpose": artifact_purpose,
        "effective_policy": effective_policy,
        "reason": reason,
    }
    return AgentAvailability(
        agent_id=agent_id,
        agent_version="actual-viability-v0",
        classification=classification,
        factory_name=factory_name,
        artifact_hash=artifact_hash,
        artifact_purpose=artifact_purpose,
        effective_policy=effective_policy,
        reason=reason,
        config_hash=digest(config, domain="actual-agent-availability-v0"),
    )


def agent_inventory(
    *,
    student_model_path: str | Path | None = None,
    student_manifest_path: str | Path | None = None,
    neural_model_path: str | Path | None = None,
    package_path: str | Path | None = None,
) -> dict[str, AgentAvailability]:
    """Return the candidate inventory after a fail-closed Student load check."""
    model_hash: str | None = None
    artifact_purpose: str | None = None
    student_reason = "student_model_artifact_missing"
    if student_model_path is not None or student_manifest_path is not None:
        try:
            from mage_ptcg.student.artifact import load_validated_artifact

            _model, manifest = load_validated_artifact(student_model_path, student_manifest_path)
            model_hash = str(manifest["model_hash"])
            artifact_purpose = str(manifest["artifact_purpose"])
        except (ImportError, OSError, TypeError, ValueError):
            student_reason = "student_model_artifact_invalid"
    student = (
        _availability(
            "student",
            classification="RUNNABLE_WITH_MODEL",
            factory_name="main.make_student_agent",
            artifact_hash=model_hash,
            artifact_purpose=artifact_purpose,
            effective_policy="Student v0 with Rule Agent v0 fallback",
            reason=None,
        )
        if model_hash is not None
        else _availability(
            "student",
            classification="BLOCKED_BY_MISSING_ARTIFACT" if student_model_path is None and student_manifest_path is None else "BLOCKED_BY_INVALID_ARTIFACT",
            factory_name="main.make_student_agent",
            effective_policy="Rule Agent v0 fallback",
            reason=student_reason,
        )
    )
    neural_model_hash: str | None = None
    neural_artifact_purpose: str | None = None
    neural_reason = "neural_student_model_artifact_missing"
    if neural_model_path is not None:
        try:
            from mage_ptcg.offline_training.export import load_export

            document = load_export(neural_model_path)
            neural_model_hash = str(document["model_hash"])
            neural_artifact_purpose = str(document.get("model_purpose"))
        except (ImportError, OSError, TypeError, ValueError):
            neural_reason = "neural_student_model_artifact_invalid"
    neural_student = (
        _availability(
            "neural_student",
            classification="RUNNABLE_WITH_MODEL",
            factory_name="mage_ptcg.evaluation.actual_agents.make_neural_student_agent",
            artifact_hash=neural_model_hash,
            artifact_purpose=neural_artifact_purpose,
            effective_policy="Neural Student v1 with Rule Agent v0 fallback",
            reason=None,
        )
        if neural_model_hash is not None
        else _availability(
            "neural_student",
            classification="BLOCKED_BY_MISSING_ARTIFACT" if neural_model_path is None else "BLOCKED_BY_INVALID_ARTIFACT",
            factory_name="mage_ptcg.evaluation.actual_agents.make_neural_student_agent",
            effective_policy="Rule Agent v0 fallback",
            reason=neural_reason,
        )
    )
    neural_package_hash: str | None = None
    neural_package_purpose: str | None = None
    neural_package_reason = "neural_student_package_missing"
    if package_path is not None:
        try:
            p_dir = Path(package_path)
            p_manifest = json.loads((p_dir / "manifest.json").read_text(encoding="utf-8"))
            neural_package_hash = str(p_manifest["model_hash"])
            neural_package_purpose = str(p_manifest.get("model_purpose"))
        except (OSError, ValueError, KeyError):
            neural_package_reason = "neural_student_package_invalid"

    neural_student_package = (
        _availability(
            "neural_student_package",
            classification="RUNNABLE_WITH_MODEL",
            factory_name="mage_ptcg.evaluation.actual_agents.make_package_neural_student_agent",
            artifact_hash=neural_package_hash,
            artifact_purpose=neural_package_purpose,
            effective_policy="Neural Student v1 with Rule Agent v0 fallback (package runtime)",
            reason=None,
        )
        if neural_package_hash is not None
        else _availability(
            "neural_student_package",
            classification="BLOCKED_BY_MISSING_ARTIFACT" if package_path is None else "BLOCKED_BY_INVALID_ARTIFACT",
            factory_name="mage_ptcg.evaluation.actual_agents.make_package_neural_student_agent",
            effective_policy="Rule Agent v0 fallback",
            reason=neural_package_reason,
        )
    )
    return {
        "rule": _availability(
            "rule",
            classification="RUNNABLE",
            factory_name="main.make_rule_agent",
            effective_policy="Rule Agent v0",
        ),
        "deterministic": _availability(
            "deterministic",
            classification="RUNNABLE",
            factory_name="main.make_deterministic_agent",
            effective_policy="deterministic baseline",
        ),
        "bounded_search": _availability(
            "bounded_search",
            classification="RUNNABLE_WITH_FALLBACK",
            factory_name="main.make_bounded_search_agent",
            effective_policy="Rule Agent v0 fallback pending public EngineAdapter",
            reason="arbitrary_state_forward_unimplemented",
        ),
        "student": student,
        "neural_student": neural_student,
        "neural_student_package": neural_student_package,
        "c5": _availability(
            "c5",
            classification="NOT_A_RUNTIME_AGENT",
            factory_name=None,
            effective_policy="NOT_APPLICABLE",
            reason="C5_provides_attestation_binding_and_league_infrastructure_not_a_runtime_policy",
        ),
    }


def _percentile(values: Sequence[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    low, high = int(position), min(int(position) + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def _selection_is_legal(observation: object, selection: object) -> bool | None:
    if not isinstance(observation, Mapping):
        return None
    select = observation.get("select")
    if not isinstance(select, Mapping):
        return None
    options = select.get("option")
    minimum, maximum = select.get("minCount"), select.get("maxCount")
    if not isinstance(options, list) or type(minimum) is not int or type(maximum) is not int:
        return False
    if not isinstance(selection, list):
        return False
    return (
        minimum <= len(selection) <= maximum
        and len(selection) == len(set(selection))
        and all(type(index) is int and 0 <= index < len(options) for index in selection)
    )


class InstrumentedAgent:
    """Callable wrapper that retains aggregate counters only.

    No observation, selection, exception message, object repr, or filesystem
    path is exposed by :meth:`public_metrics`.
    """

    def __init__(self, *, availability: AgentAvailability, delegate: Callable[[dict], list[int]]):
        self.availability = availability
        self._delegate = delegate
        # kaggle-environments identifies Python callables by this attribute.
        self.__name__ = getattr(delegate, "__name__", "evaluation_agent")
        self._calls = 0
        self._decisions = 0
        self._legal_decisions = 0
        self._invalid = 0
        self._exceptions = 0
        self._fallbacks = 0
        self._fallback_reasons: Counter[str] = Counter()
        self._decision_latencies: deque[float] = deque(maxlen=20_000)
        self._search_requested = 0
        self._search_started = 0
        self._search_completed = 0
        self._search_blocked = 0
        self._search_block_reasons: Counter[str] = Counter()
        self._nodes_expanded = 0
        self._budget_exhausted = 0
        self._model_loaded = getattr(delegate, "student_policy", None) is not None
        self._inference_requested = 0
        self._inference_completed = 0
        self._inference_failed = 0
        self._feature_successes = 0
        self._feature_failures = 0
        self._student_selections = 0
        self._effective_policy_counts: Counter[str] = Counter()

    def __call__(self, observation: dict) -> list[int]:
        self._calls += 1
        is_decision = isinstance(observation.get("select") if isinstance(observation, dict) else None, Mapping)
        if is_decision:
            self._decisions += 1
        started = time.perf_counter()
        try:
            selection = self._delegate(observation)
        except Exception as exc:
            self._exceptions += 1
            self._fallback_reasons[f"agent_exception:{type(exc).__name__}"] += 1
            raise
        finally:
            if is_decision:
                self._decision_latencies.append((time.perf_counter() - started) * 1_000.0)
        if not is_decision:
            return selection
        legal = _selection_is_legal(observation, selection)
        if legal:
            self._legal_decisions += 1
        else:
            self._invalid += 1
        self._capture_search()
        self._capture_student()
        return selection

    def as_runtime_function(self) -> Callable[[dict], list[int]]:
        """Return a real function for environments that reject callable objects."""
        def runtime_agent(observation: dict) -> list[int]:
            return self(observation)

        runtime_agent.__name__ = self.__name__
        return runtime_agent

    def _capture_search(self) -> None:
        if self.availability.agent_id != "bounded_search":
            return
        self._search_requested += 1
        result = getattr(self._delegate, "last_search_result", None)
        if result is None:
            self._search_blocked += 1
            self._search_block_reasons["search_result_unavailable"] += 1
            return
        self._search_started += 1
        self._nodes_expanded += int(getattr(result, "expansions", 0))
        budget_reason = getattr(result, "budget_exhaustion_reason", "UNKNOWN")
        if budget_reason != "complete":
            self._budget_exhausted += 1
        fallback_reason = getattr(result, "fallback_reason", None)
        if isinstance(fallback_reason, str):
            self._fallbacks += 1
            self._fallback_reasons[fallback_reason] += 1
            if fallback_reason == "engine_adapter_unavailable":
                self._search_blocked += 1
                self._search_block_reasons[fallback_reason] += 1
            return
        self._search_completed += 1

    _MODEL_BACKED_AGENT_IDS = frozenset({"student", "neural_student", "neural_student_package"})
    _MODEL_SELECTED_LABEL = {"student": "Student v0", "neural_student": "Neural Student v1", "neural_student_package": "Neural Student v1"}

    def _capture_student(self) -> None:
        if self.availability.agent_id not in self._MODEL_BACKED_AGENT_IDS or not self._model_loaded:
            return
        policy = getattr(self._delegate, "student_policy", None)
        trace = getattr(policy, "last_decision_trace", None)
        if not isinstance(trace, Mapping):
            self._fallbacks += 1
            self._fallback_reasons["student_trace_unavailable"] += 1
            return
        student = trace.get("student")
        student_status = student.get("status") if isinstance(student, Mapping) else None
        if student_status == "not_requested":
            return
        self._inference_requested += 1
        if student_status == "selected":
            self._inference_completed += 1
            self._feature_successes += 1
            self._student_selections += 1
            self._effective_policy_counts[self._MODEL_SELECTED_LABEL[self.availability.agent_id]] += 1
            return
        self._inference_failed += 1
        reason = trace.get("reason")
        label = str(reason) if isinstance(reason, str) else "student_no_selection"
        self._fallbacks += 1
        self._effective_policy_counts["Rule Agent v0 fallback"] += 1
        self._fallback_reasons[f"student:{label}"] += 1
        if "DecisionState" in label or "selection" in label:
            self._feature_failures += 1

    def public_metrics(self) -> dict[str, object]:
        model_backed = self.availability.agent_id in self._MODEL_BACKED_AGENT_IDS
        effective = self.availability.effective_policy
        if self.availability.agent_id == "bounded_search" and self._decisions and self._fallbacks == self._decisions:
            effective = "Rule Agent v0 fallback only"
        elif model_backed and self._student_selections:
            effective = self.availability.effective_policy
        if not model_backed and self._decisions:
            self._effective_policy_counts[effective] = self._decisions
        return {
            "agent_id": self.availability.agent_id,
            "agent_version": self.availability.agent_version,
            "config_hash": self.availability.config_hash,
            "artifact_hash": self.availability.artifact_hash,
            "model_hash": self.availability.artifact_hash,
            "model_artifact_purpose": self.availability.artifact_purpose if model_backed else "NOT_APPLICABLE",
            "agent_calls": self._calls,
            "decisions": self._decisions,
            "legal_decisions": self._legal_decisions,
            "invalid_selections": self._invalid,
            "exception_count": self._exceptions,
            "timeout_count": "UNKNOWN",
            "fallback_count": self._fallbacks,
            "fallback_reason_counts": dict(sorted(self._fallback_reasons.items())),
            "latency_ms": {
                "p50": _percentile(self._decision_latencies, 0.50),
                "p95": _percentile(self._decision_latencies, 0.95),
                "max": max(self._decision_latencies) if self._decision_latencies else None,
            },
            "decision_latency_samples": len(self._decision_latencies),
            "legal_action_rate": self._legal_decisions / self._decisions if self._decisions else "UNKNOWN",
            "effective_policy": effective,
            "effective_policy_counts": dict(sorted(self._effective_policy_counts.items())),
            "runtime_features": {
                "search_requested": self._search_requested if self.availability.agent_id == "bounded_search" else "NOT_APPLICABLE",
                "search_started": self._search_started if self.availability.agent_id == "bounded_search" else "NOT_APPLICABLE",
                "search_completed": self._search_completed if self.availability.agent_id == "bounded_search" else "NOT_APPLICABLE",
                "search_blocked": self._search_blocked if self.availability.agent_id == "bounded_search" else "NOT_APPLICABLE",
                "search_block_reasons": dict(sorted(self._search_block_reasons.items())) if self.availability.agent_id == "bounded_search" else "NOT_APPLICABLE",
                "nodes_expanded": self._nodes_expanded if self.availability.agent_id == "bounded_search" else "NOT_APPLICABLE",
                "budget_exhausted": self._budget_exhausted if self.availability.agent_id == "bounded_search" else "NOT_APPLICABLE",
                "model_loaded": self._model_loaded if model_backed else "NOT_APPLICABLE",
                "inference_requested": self._inference_requested if model_backed else "NOT_APPLICABLE",
                "inference_completed": self._inference_completed if model_backed else "NOT_APPLICABLE",
                "inference_failed": self._inference_failed if model_backed else "NOT_APPLICABLE",
                "inference_count": self._inference_completed if model_backed else "NOT_APPLICABLE",
                "feature_success_count": self._feature_successes if model_backed else "NOT_APPLICABLE",
                "feature_failures": self._feature_failures if model_backed else "NOT_APPLICABLE",
                "feature_failure_count": self._feature_failures if model_backed else "NOT_APPLICABLE",
                "student_selection_count": self._student_selections if model_backed else "NOT_APPLICABLE",
                "teacher_rules_evaluated": "NOT_APPLICABLE",
                "teacher_rules_applied": "NOT_APPLICABLE",
            },
        }


class _NeuralPolicyTraceAdapter:
    """Normalize ``NeuralRuntimePolicy``'s flat trace into the nested
    ``{"student": {"status": ...}}`` shape :meth:`InstrumentedAgent._capture_student`
    already parses for the linear ``RuntimeStudentPolicy``.  The two runtimes
    use different trace shapes by design; this adapter avoids branching that
    shared telemetry code on runtime type.
    """

    def __init__(self, policy: "NeuralRuntimePolicy") -> None:
        self._policy = policy
        self.last_decision_trace: dict[str, object] | None = None

    def choose(self, observation: object) -> list[int] | None:
        selection = self._policy.choose(observation)
        trace = self._policy.last_decision_trace or {}
        status = trace.get("status")
        if status == "selected":
            self.last_decision_trace = {"student": {"status": "selected"}}
        elif status == "rule_optional_auxiliary":
            self.last_decision_trace = {"status": "rule_optional_auxiliary", "student": {"status": "not_requested"}}
        else:
            reason = trace.get("reason", status if status is not None else "unknown")
            self.last_decision_trace = {"status": "fallback", "reason": str(reason), "student": {"status": "failed"}}
        return selection


def make_package_neural_student_agent(
    *,
    deck: Sequence[int],
    package_path: str | Path,
) -> Callable[[dict], list[int]]:
    """Load the neural Student agent from the reproducible package directory."""
    import importlib.util
    import sys
    from mage_ptcg.offline_training.neural_runtime import NeuralRuntimePolicy

    package_dir = Path(package_path)
    model_path = package_dir / "models" / "neural-student-v1.json"

    # NeuralRuntimePolicy.load を一時的にフックして、作成されるインスタンスをキャプチャする
    original_load = NeuralRuntimePolicy.load
    captured_policy: _NeuralPolicyTraceAdapter | None = None

    class HookedPolicy(NeuralRuntimePolicy):
        @classmethod
        def load(cls, path):
            nonlocal captured_policy
            inst = original_load(path)
            captured_policy = _NeuralPolicyTraceAdapter(inst)
            return captured_policy

    # フックを適用
    NeuralRuntimePolicy.load = HookedPolicy.load

    # package_dir を sys.path に追加して、main.py をインポート
    original_path = sys.path[:]
    sys.path.insert(0, str(package_dir))
    
    # すでにインポートされている main や runtime_main があれば一時的に削除
    saved_modules = {}
    for mod_name in ("main", "runtime_main"):
        if mod_name in sys.modules:
            saved_modules[mod_name] = sys.modules[mod_name]
            del sys.modules[mod_name]

    try:
        # package_dir / "main.py" を直接ロード
        spec = importlib.util.spec_from_file_location("package_main", str(package_dir / "main.py"))
        package_main = importlib.util.module_from_spec(spec)
        sys.modules["package_main"] = package_main
        spec.loader.exec_module(package_main)
        
        # エージェント作成
        agent = package_main.make_neural_agent(deck=deck, model_path=model_path)
    finally:
        # フック解除とモジュール・pathの復元
        NeuralRuntimePolicy.load = original_load
        sys.path = original_path
        for mod_name in ("main", "runtime_main"):
            if mod_name in saved_modules:
                sys.modules[mod_name] = saved_modules[mod_name]
            elif mod_name in sys.modules:
                del sys.modules[mod_name]

    # InstrumentedAgent のために、キャプチャした policy を属性として付与する
    agent.student_policy = captured_policy
    return agent


def make_instrumented_agent(
    agent_id: str,
    *,
    deck: Sequence[int],
    seed: int,
    student_model_path: str | Path | None = None,
    student_manifest_path: str | Path | None = None,
    neural_model_path: str | Path | None = None,
    package_path: str | Path | None = None,
) -> InstrumentedAgent:
    """Build one explicitly selected evaluation agent; unknown IDs fail closed."""
    inventory = agent_inventory(
        student_model_path=student_model_path,
        student_manifest_path=student_manifest_path,
        neural_model_path=neural_model_path,
        package_path=package_path,
    )
    if agent_id not in inventory:
        raise ValueError("unknown_evaluation_agent")
    availability = inventory[agent_id]
    if availability.classification in {"NOT_A_RUNTIME_AGENT", "BLOCKED_BY_MISSING_ARTIFACT", "BLOCKED_BY_INVALID_ARTIFACT", "BLOCKED_BY_MISSING_CAPABILITY", "UNSAFE"}:
        raise ValueError(f"agent_not_runnable:{availability.classification}")
    from main import make_bounded_search_agent, make_deterministic_agent, make_rule_agent, make_student_agent

    if agent_id == "rule":
        delegate = make_rule_agent(deck=deck, seed=seed)
    elif agent_id == "deterministic":
        delegate = make_deterministic_agent(deck=deck)
    elif agent_id == "bounded_search":
        delegate = make_bounded_search_agent(deck=deck, seed=seed)
    elif agent_id == "student":
        delegate = make_student_agent(deck=deck, model_path=student_model_path)
    elif agent_id == "neural_student":
        delegate = make_neural_student_agent(deck=deck, model_path=neural_model_path)
    elif agent_id == "neural_student_package":
        delegate = make_package_neural_student_agent(deck=deck, package_path=package_path)
    else:  # protected above; retained for exhaustive safety
        raise ValueError("agent_not_runnable")
    return InstrumentedAgent(availability=availability, delegate=delegate)


# Add make_neural_student_agent back to keep public API
def make_neural_student_agent(
    *,
    deck: Sequence[int],
    model_path: str | Path | None = None,
) -> Callable[[dict], list[int]]:
    from main import _deck_supplier, _selection_contract, make_rule_agent
    from mage_ptcg.offline_training.neural_runtime import NeuralRuntimePolicy

    supply_deck = _deck_supplier(deck, None)
    fallback = make_rule_agent(deck=deck)
    policy: _NeuralPolicyTraceAdapter | None = None
    try:
        policy = _NeuralPolicyTraceAdapter(NeuralRuntimePolicy.load(model_path))
    except (ImportError, OSError, ValueError):
        policy = None

    def neural_student_agent(obs_dict: dict) -> list[int]:
        if _selection_contract(obs_dict) is None:
            return supply_deck()
        if policy is not None:
            selection = policy.choose(obs_dict)
            if selection is not None:
                return selection
        return fallback(obs_dict)

    neural_student_agent.__name__ = "neural_student_v1_with_rule_v0_fallback"
    neural_student_agent.student_policy = policy  # type: ignore[attr-defined]
    return neural_student_agent


__all__ = ["AgentAvailability", "Classification", "InstrumentedAgent", "agent_inventory", "make_instrumented_agent", "make_neural_student_agent", "make_package_neural_student_agent"]
