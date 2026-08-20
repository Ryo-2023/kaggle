import json
from pathlib import Path

import polars as pl
import tracksdata as td
import zarr

from biohub.detector_fixed_race.panel import freeze_validation_panel


def _write_image(path: Path) -> None:
    root = zarr.open_group(path, mode="w")
    root.create_array("0", shape=(2, 2, 2, 2), dtype="uint16")
    root.attrs.put(
        {
            "multiscales": [
                {
                    "datasets": [
                        {
                            "path": "0",
                            "coordinateTransformations": [
                                {"type": "scale", "scale": [1.0, 1.625, 0.40625, 0.40625]}
                            ],
                        }
                    ]
                }
            ],
            "image_statistics": {"quantiles": {"0.001": 1.0, "0.999": 2.0}},
        }
    )


def _write_gt(path: Path, division: bool = False) -> None:
    graph = td.graph.IndexedRXGraph()
    for key in ("z", "y", "x"):
        graph.add_node_attr_key(key, dtype=pl.Float64, default_value=0.0)
    graph.add_node({"t": 0, "z": 0.0, "y": 0.0, "x": 0.0})
    graph.add_node({"t": 1, "z": 0.0, "y": 0.0, "x": 0.0})
    graph.add_edge(0, 1, {})
    if division:
        graph.add_node({"t": 1, "z": 0.0, "y": 1.0, "x": 0.0})
        graph.add_edge(0, 2, {})
    graph.to_geff(path)


def test_panel_selection_is_deterministic_and_score_free(tmp_path: Path) -> None:
    train = tmp_path / "train"
    gt = tmp_path / "gt"
    train.mkdir()
    gt.mkdir()
    for sample_id in ("a", "b", "dev", "division"):
        _write_image(train / f"{sample_id}.zarr")
        _write_gt(gt / f"{sample_id}.geff", division=sample_id == "division")

    first = freeze_validation_panel(train, gt, "dev", minimum=3, maximum=3)
    second = freeze_validation_panel(train, gt, "dev", minimum=3, maximum=3)
    assert first == second
    assert len(first["samples"]) == 3
    assert "score" not in json.dumps(first)
    assert "dev" in {sample["sample_id"] for sample in first["samples"]}
    assert any(sample["division_source_count"] > 0 for sample in first["samples"])
