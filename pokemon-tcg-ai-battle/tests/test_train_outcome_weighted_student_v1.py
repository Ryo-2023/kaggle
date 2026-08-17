"""Planner-level checks for the outcome-weighted Student trainer."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.train_outcome_weighted_student_v1 import (
    _partition_student_examples,
    train_bundle_v1,
)


def test_training_rejects_existing_output(tmp_path: Path) -> None:
    output = tmp_path / "out"
    output.mkdir()
    with pytest.raises(FileExistsError):
        train_bundle_v1(collection_root=tmp_path / "missing", output_root=output)


def test_partition_excludes_only_ordered_skill_examples() -> None:
    supported = SimpleNamespace(selection_type=0, selection_context=0, example_id="supported")
    ordered = SimpleNamespace(selection_type=5, selection_context=34, example_id="ordered")

    kept, excluded = _partition_student_examples([supported, ordered])

    assert [example.example_id for example in kept] == ["supported"]
    assert excluded == {"ordered": "ordered_selection_not_representable"}
