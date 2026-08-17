from __future__ import annotations

from pathlib import Path

import pytest

from mage_ptcg.meta_specialist.cg_weekend_split_v1 import (
    SPLIT_NAMES,
    load_weekend_split,
)


ROOT = Path(__file__).resolve().parents[2]
SPLIT_PATH = ROOT / "configs/meta_specialist/cg_weekend_splits_v1.json"


def test_weekend_split_is_hash_bound_and_disjoint() -> None:
    split = load_weekend_split(SPLIT_PATH, verify_sources=True)
    assert set(split.rows_by_split) == set(SPLIT_NAMES)
    assert len(split.rows_by_split["META_TRAIN"]) == 12
    assert len(split.rows_by_split["META_DEV"]) == 6
    assert len(split.rows_by_split["META_FINAL"]) == 6
    assert not set(split.ids("META_TRAIN")) & set(split.ids("META_DEV"))
    assert not set(split.ids("META_TRAIN")) & set(split.ids("META_FINAL"))
    assert not set(split.ids("META_DEV")) & set(split.ids("META_FINAL"))
    assert "tomatomato_archaludon" not in split.ids("META_TRAIN")
    assert split.train_blocks
    assert set().union(*[set(block) for block in split.train_blocks]) == set(split.ids("META_TRAIN"))


def test_weekend_split_rejects_overlap_and_missing_binding(tmp_path: Path) -> None:
    raw = SPLIT_PATH.read_text(encoding="utf-8")
    broken = tmp_path / "broken.json"
    broken.write_text(raw.replace('"META_DEV": [', '"META_DEV": [\n      {"opponent_id": "aman_crustleaware_fighting",'), encoding="utf-8")
    with pytest.raises(ValueError):
        load_weekend_split(broken, verify_sources=False)
