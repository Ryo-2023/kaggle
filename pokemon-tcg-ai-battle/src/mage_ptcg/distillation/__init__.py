"""C5 targeted distillation contracts and selection utilities."""

from .actionkey_adapter import (
    ADAPTER_VERSION,
    RuleSupportClass,
    TeacherApplication,
    TeacherStatus,
    adapt_decision,
    adapt_records,
    adapt_teacher_rule,
    classify_teacher_rule,
)
from .contracts import DECISION_SCHEMA_VERSION, DecisionDatasetError
from .knowledge import CuratedKnowledgeError, apply_priors, load_curated_knowledge
from .registry import TeacherCapabilityError, default_teacher_registry, require_teacher
from .selection import SELECTION_POLICY_VERSION, SelectionConfig, select_targeted

__all__ = [
    "DECISION_SCHEMA_VERSION", "DecisionDatasetError", "SELECTION_POLICY_VERSION", "SelectionConfig",
    "TeacherCapabilityError", "default_teacher_registry", "require_teacher", "select_targeted",
    "CuratedKnowledgeError", "apply_priors", "load_curated_knowledge",
    "ADAPTER_VERSION", "RuleSupportClass", "TeacherApplication", "TeacherStatus",
    "adapt_decision", "adapt_records", "adapt_teacher_rule", "classify_teacher_rule",
]
