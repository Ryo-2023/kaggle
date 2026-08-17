"""Render the Student-only public entrypoint used inside its package.

The repository ``main.py`` remains the approved Rule-v0 entrypoint.  This
template is copied only into an explicitly requested Student candidate package.
"""

from __future__ import annotations


def render_student_entrypoint() -> str:
    """Return the deterministic, package-relative Student entrypoint source."""
    return '''"""Standalone ACTUAL_TRAINED Student candidate entrypoint."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import runtime_main as _runtime_main

_RUNTIME_MAIN_FILE = getattr(_runtime_main, "__file__", None)
if not isinstance(_RUNTIME_MAIN_FILE, str):
    raise RuntimeError("runtime_main.__file__ is unavailable")

PACKAGE_ROOT = Path(_RUNTIME_MAIN_FILE).resolve().parent
SRC_ROOT = PACKAGE_ROOT / "src"
for _path in (PACKAGE_ROOT, SRC_ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from mage_ptcg.student.model import StudentV0Model

make_rule_agent = _runtime_main.make_rule_agent
make_student_agent = _runtime_main.make_student_agent

MODEL_PATH = PACKAGE_ROOT / "models" / "student-v0.json"
MODEL_MANIFEST_PATH = PACKAGE_ROOT / "student-model-manifest.json"
PACKAGE_MANIFEST_PATH = PACKAGE_ROOT / "student-package-manifest.json"
PACKAGE_TELEMETRY = {
    "model_loaded": False,
    "model_hash": None,
    "inference_requested": 0,
    "inference_completed": 0,
    "student_selection_count": 0,
    "fallback_count": 0,
    "legal_decision_count": 0,
    "legal_action_count": 0,
    "invalid_count": 0,
    "crash_count": 0,
    "timeout_count": 0,
}


def _selection(obs):
    select = obs.get("select") if isinstance(obs, dict) else None
    return select if isinstance(select, dict) else None


def _is_legal(choice, select):
    options = select.get("option")
    minimum, maximum = select.get("minCount"), select.get("maxCount")
    return (
        isinstance(choice, list)
        and isinstance(options, list)
        and type(minimum) is int
        and type(maximum) is int
        and minimum <= len(choice) <= maximum
        and len(choice) == len(set(choice))
        and all(type(index) is int and 0 <= index < len(options) for index in choice)
    )


def _load_student():
    manifest = json.loads(MODEL_MANIFEST_PATH.read_text(encoding="utf-8"))
    package = json.loads(PACKAGE_MANIFEST_PATH.read_text(encoding="utf-8"))
    expected_hash = package.get("model_hash")
    actual_hash = hashlib.sha256(MODEL_PATH.read_bytes()).hexdigest()
    if (
        manifest.get("artifact_purpose") != "ACTUAL_TRAINED"
        or manifest.get("performance_eligible") is not True
        or manifest.get("privacy_scan_executed") is not True
        or manifest.get("privacy_violations") != 0
        or not isinstance(expected_hash, str)
        or expected_hash != actual_hash
        or manifest.get("model_hash") != actual_hash
    ):
        raise ValueError("Student package model manifest is incompatible")
    StudentV0Model.load(MODEL_PATH)
    policy_agent = make_student_agent(model_path=MODEL_PATH)
    if getattr(policy_agent, "student_policy", None) is None:
        raise ValueError("Student runtime policy was not loaded")
    PACKAGE_TELEMETRY["model_loaded"] = True
    PACKAGE_TELEMETRY["model_hash"] = actual_hash
    return policy_agent


try:
    _DEFAULT_AGENT = _load_student()
    _USING_FALLBACK = False
except (OSError, TypeError, ValueError, json.JSONDecodeError):
    _DEFAULT_AGENT = make_rule_agent()
    _USING_FALLBACK = True


def package_telemetry():
    """Return scalar runtime evidence without retaining observations."""
    return dict(PACKAGE_TELEMETRY)


def agent(obs_dict):
    """Competition callable: Student selection or deterministic Rule-v0 fallback."""
    select = _selection(obs_dict)
    if select is not None:
        PACKAGE_TELEMETRY["inference_requested"] += 1
    try:
        choice = _DEFAULT_AGENT(obs_dict)
    except Exception:
        PACKAGE_TELEMETRY["crash_count"] += 1
        PACKAGE_TELEMETRY["fallback_count"] += 1
        choice = make_rule_agent()(obs_dict)
    if select is None:
        return choice
    if _is_legal(choice, select):
        PACKAGE_TELEMETRY["legal_decision_count"] += 1
        PACKAGE_TELEMETRY["legal_action_count"] += len(choice)
    else:
        PACKAGE_TELEMETRY["invalid_count"] += 1
    policy = getattr(_DEFAULT_AGENT, "student_policy", None)
    trace = getattr(policy, "last_decision_trace", None)
    selected = isinstance(trace, dict) and isinstance(trace.get("student"), dict) and trace["student"].get("status") == "selected"
    if not _USING_FALLBACK and selected:
        PACKAGE_TELEMETRY["inference_completed"] += 1
        PACKAGE_TELEMETRY["student_selection_count"] += 1
    elif _USING_FALLBACK or (isinstance(trace, dict) and trace.get("status") == "fallback"):
        PACKAGE_TELEMETRY["fallback_count"] += 1
    return choice
'''


def render_student_package_init() -> str:
    """Return the runtime-only package initializer with no dataset imports."""
    return '''"""Runtime-only Student package surface for a public candidate."""
from .model import MODEL_SCHEMA_VERSION, StudentV0Model
from .runtime import RuntimeStudentPolicy, StudentModelError

__all__ = [
    "MODEL_SCHEMA_VERSION",
    "RuntimeStudentPolicy",
    "StudentModelError",
    "StudentV0Model",
]
'''


def render_student_runtime_model(source: str) -> str:
    """Remove the offline-only dataset import from the packaged model module."""
    target = "from .dataset import RuleBCExample\n"
    if source.count(target) != 1:
        raise ValueError("unexpected Student model import surface")
    return source.replace(target, "from typing import TYPE_CHECKING\n\nif TYPE_CHECKING:\n    from .dataset import RuleBCExample\n")


def render_student_cabt_trace(source: str) -> str:
    """Remove the offline-only actor-visible attestation feature from the packaged trace module.

    Student inference never supplies ``actor_visible_attestation_writer`` or
    ``actor_visible_card_classifier`` to :func:`make_traced_agent`, so the
    deferred import of ``mage_ptcg.distillation.actor_visible_attestation`` is
    unreachable at runtime. It is still removed from the packaged copy so the
    Student submission's local-import closure never references a module the
    tar does not ship.
    """
    attestation_writer_class = (
        'class ActorVisibleAttestationWriter:\n'
        '    """Separate writer for redacted offline teacher-binding outcomes.\n'
        '\n'
        '    It accepts only the bounded redacted payload defined by the binder.  It is\n'
        '    deliberately not interchangeable with :class:`TraceWriter`.\n'
        '    """\n'
        '\n'
        '    _REQUIRED = frozenset({\n'
        '        "teacher_id", "canonical_rule_id", "candidate_public_id",\n'
        '        "condition_evaluated", "condition_result", "binding_status",\n'
        '        "binding_reason", "binder_version", "provenance_category",\n'
        '    })\n'
        '\n'
        '    def __init__(self, path: str | Path) -> None:\n'
        '        self._path = Path(path)\n'
        '        self._handle = self._path.open("a", encoding="utf-8")\n'
        '\n'
        '    def write(self, record: Mapping[str, Any]) -> None:\n'
        '        if set(record) != self._REQUIRED:\n'
        '            raise PrivacyInvariantError("invalid actor-visible redacted attestation payload")\n'
        '        if find_forbidden_keys(record):\n'
        '            raise PrivacyInvariantError("attestation contains forbidden observation field")\n'
        '        self._handle.write(json.dumps(dict(record), ensure_ascii=False, sort_keys=True) + "\\n")\n'
        '        self._handle.flush()\n'
        '\n'
        '    def close(self) -> None:\n'
        '        self._handle.close()\n'
        '\n'
        '    def __enter__(self) -> "ActorVisibleAttestationWriter":\n'
        '        return self\n'
        '\n'
        '    def __exit__(self, *exc_info: object) -> None:\n'
        '        self.close()\n'
        '\n'
        '\n'
    )
    if source.count(attestation_writer_class) != 1:
        raise ValueError("unexpected cabt_trace ActorVisibleAttestationWriter surface")
    result = source.replace(attestation_writer_class, "")

    attestation_params = (
        '    actor_visible_attestation_writer: ActorVisibleAttestationWriter | None = None,\n'
        '    actor_visible_card_classifier: Callable[[int], str | None] | None = None,\n'
    )
    if result.count(attestation_params) != 1:
        raise ValueError("unexpected make_traced_agent attestation parameter surface")
    result = result.replace(attestation_params, "")

    attestation_call_site = (
        '        if (\n'
        '            actor_visible_attestation_writer is not None\n'
        '            and actor_visible_card_classifier is not None\n'
        '            and isinstance(observation, Mapping)\n'
        '        ):\n'
        '            from mage_ptcg.distillation.actor_visible_attestation import bind_tr000010\n'
        '\n'
        '            for attestation in bind_tr000010(\n'
        '                observation, card_classifier=actor_visible_card_classifier\n'
        '            ):\n'
        '                actor_visible_attestation_writer.write(attestation.to_private_artifact())\n'
    )
    if result.count(attestation_call_site) != 1:
        raise ValueError("unexpected make_traced_agent attestation call site")
    result = result.replace(attestation_call_site, "")

    if "actor_visible_attestation" in result or "ActorVisibleAttestationWriter" in result:
        raise ValueError("actor-visible attestation surface was not fully removed")
    return result
