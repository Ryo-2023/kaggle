"""C4 Student v0: offline Rule-v0 distillation and safe runtime policy."""

from .dataset import (
    DATASET_SCHEMA_VERSION,
    RuleBCExample,
    build_rule_bc_example,
    load_dataset,
    split_examples,
    validate_example,
    write_dataset,
)
from .model import MODEL_SCHEMA_VERSION, StudentV0Model, train_model
from .runtime import RuntimeStudentPolicy, StudentModelError

__all__ = [
    "DATASET_SCHEMA_VERSION",
    "MODEL_SCHEMA_VERSION",
    "RuleBCExample",
    "RuntimeStudentPolicy",
    "StudentModelError",
    "StudentV0Model",
    "build_rule_bc_example",
    "load_dataset",
    "split_examples",
    "train_model",
    "validate_example",
    "write_dataset",
]
