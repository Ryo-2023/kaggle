from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.run_resource_aware_weighted_deck_halving_v1 import (
    ResourceAwareWeightedDeckError,
    load_meta_train_subset,
)


def _manifest(tmp_path: Path) -> Path:
    rows = [
        {
            "opponent_id": "aristophanivan_probabilistic",
            "split": "META_TRAIN",
            "weight": 0.4,
            "evaluation_allowed": True,
        },
        {
            "opponent_id": "harukiharada_crustle",
            "split": "META_TRAIN",
            "weight": 0.1,
            "evaluation_allowed": True,
        },
        {
            "opponent_id": "lucifer19_battlecore",
            "split": "META_FINAL",
            "weight": 0.9,
            "evaluation_allowed": True,
        },
        {
            "opponent_id": "other_train",
            "split": "META_TRAIN",
            "weight": 0.3,
            "evaluation_allowed": True,
        },
    ]
    path = tmp_path / "meta-manifest.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "fixture",
                "research_only": True,
                "promotion_authority": False,
                "training_authority": False,
                "submission_authority": False,
                "rows": rows,
            }
        ),
        encoding="utf-8",
    )
    return path


def test_subset_requires_targets_but_excludes_heldout(tmp_path: Path) -> None:
    selected = load_meta_train_subset(
        _manifest(tmp_path), top_k=3,
        required_ids=("aristophanivan_probabilistic", "harukiharada_crustle"),
    )

    assert selected["selected_ids"] == [
        "aristophanivan_probabilistic",
        "other_train",
        "harukiharada_crustle",
    ]
    assert "lucifer19_battlecore" in selected["heldout_ids"]
    assert "lucifer19_battlecore" not in selected["selected_ids"]
    assert selected["weight_update_excluded_heldout"] is True


def test_subset_rejects_non_meta_train_required_target(tmp_path: Path) -> None:
    with pytest.raises(ResourceAwareWeightedDeckError, match="required target"):
        load_meta_train_subset(
            _manifest(tmp_path), top_k=2,
            required_ids=("lucifer19_battlecore",),
        )


def test_subset_rejects_open_authority(tmp_path: Path) -> None:
    path = _manifest(tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["promotion_authority"] = True
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ResourceAwareWeightedDeckError, match="authority"):
        load_meta_train_subset(path, top_k=3)
