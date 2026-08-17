from __future__ import annotations

from pathlib import Path

import pytest

from scripts.run_meta_weighted_deck_search_v1 import (
    DEFAULT_WORKER_RECYCLE_GAMES,
    DEFAULT_WORKERS,
    MetaWeightedDeckRunnerError,
)


def test_automatic_deck_search_defaults_use_parallel_workers() -> None:
    assert DEFAULT_WORKERS == 12
    assert DEFAULT_WORKER_RECYCLE_GAMES == 16


def test_automatic_deck_search_rejects_output_outside_final_sprint(tmp_path: Path) -> None:
    from scripts.run_meta_weighted_deck_search_v1 import materialize_manifest

    with pytest.raises(MetaWeightedDeckRunnerError, match="final-sprint"):
        materialize_manifest(output=tmp_path / "outside")
