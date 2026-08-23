"""TDD contract tests for the GT-free Recipe C CSV/GEFF bridge."""

from __future__ import annotations

import csv
import json
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest
from zarr.storage import LocalStore

import biohub.recipe_c.geff_bridge as bridge_module
from biohub.recipe_c.geff_bridge import (
    CSV_HEADER,
    postprocessed_csv_to_geffs,
    validate_prediction_geff,
    write_prediction_manifest,
)
from biohub.reproducibility.gt_guard import mint_prediction_token, prediction_manifest_path
from biohub.submission.packaging import write_submission_csv

SAMPLE = "44b6_12dfb391"


def _write_csv(path: Path, rows: list[dict[str, object]], *, header: list[str] | None = None) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=header or CSV_HEADER)
        writer.writeheader()
        writer.writerows(rows)


def _node(row_id: int, node_id: int, t: int, *, z: int = 1, y: int = 2, x: int = 3) -> dict[str, object]:
    return {
        "id": row_id,
        "dataset": SAMPLE,
        "row_type": "node",
        "node_id": node_id,
        "t": t,
        "z": z,
        "y": y,
        "x": x,
        "source_id": -1,
        "target_id": -1,
    }


def _edge(row_id: int, source_id: int, target_id: int) -> dict[str, object]:
    return {
        "id": row_id,
        "dataset": SAMPLE,
        "row_type": "edge",
        "node_id": -1,
        "t": -1,
        "z": -1,
        "y": -1,
        "x": -1,
        "source_id": source_id,
        "target_id": target_id,
    }


def test_bridge_roundtrips_fork_and_uses_exact_sample_name(tmp_path: Path) -> None:
    csv_path = tmp_path / "submission.csv"
    _write_csv(
        csv_path,
        [_node(0, 0, 0), _node(1, 1, 1), _node(2, 2, 1), _edge(3, 0, 1), _edge(4, 0, 2)],
    )

    written = postprocessed_csv_to_geffs(
        csv_path,
        tmp_path / "predictions",
        sample_ids=(SAMPLE,),
        provenance={"recipe": "C"},
    )

    prediction = written[SAMPLE]
    assert prediction == tmp_path / "predictions" / f"{SAMPLE}.geff"
    assert prediction.is_dir()
    assert validate_prediction_geff(prediction, SAMPLE, expected_volume_shape_tzyx=(2, 16, 16, 16)) == {
        "nodes": 3,
        "edges": 2,
        "forks": 1,
    }


def test_bridge_serializes_to_absent_child_without_overwrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    csv_path = tmp_path / "submission.csv"
    _write_csv(csv_path, [_node(0, 0, 0)])
    calls: list[tuple[object, bool, bool, Path, int]] = []
    original = bridge_module._build_geff

    def observe_serializer(
        destination: LocalStore, bucket: dict[str, object], *, overwrite: bool = False
    ) -> None:
        assert isinstance(destination, LocalStore)
        store_root = Path(destination.root)
        owner_path = Path(os.readlink(store_root))
        calls.append(
            (destination, store_root.exists(), overwrite, owner_path, owner_path.parent.stat().st_mode & 0o777)
        )
        original(destination, bucket, overwrite=overwrite)

    monkeypatch.setattr(bridge_module, "_build_geff", observe_serializer)
    output_root = tmp_path / "predictions"
    postprocessed_csv_to_geffs(
        csv_path, output_root, sample_ids=(SAMPLE,), provenance={}
    )

    assert calls and calls[0][1:3] == (True, False)
    assert calls[0][4] == 0o700
    prediction = output_root / f"{SAMPLE}.geff"
    assert (prediction / ".zgroup").exists()
    assert not (prediction / "zarr.json").exists()
    assert not list(output_root.glob(".*.build-*.tmp"))


def test_bridge_does_not_remove_replaced_private_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    csv_path = tmp_path / "submission.csv"
    _write_csv(csv_path, [_node(0, 0, 0)])
    output_root = tmp_path / "predictions"

    def replace_parent_then_fail(
        source_parent_fd: int, source_name: str, destination_parent_fd: int, destination_name: str
    ) -> None:
        parent = Path(os.readlink(f"/proc/self/fd/{source_parent_fd}"))
        competitor = parent.with_name(parent.name + ".competitor")
        shutil.rmtree(parent)
        competitor.mkdir()
        (competitor / "keep").write_bytes(b"competitor")
        competitor.rename(parent)
        raise OSError("synthetic parent replacement")

    monkeypatch.setattr(bridge_module, "_rename_noreplace_at", replace_parent_then_fail)
    with pytest.raises(OSError, match="parent replacement"):
        postprocessed_csv_to_geffs(csv_path, output_root, sample_ids=(SAMPLE,), provenance={})

    parents = list(output_root.glob(".*.build-*.tmp"))
    assert len(parents) == 1
    assert (parents[0] / "keep").read_bytes() == b"competitor"


def test_bridge_does_not_remove_replaced_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    csv_path = tmp_path / "submission.csv"
    _write_csv(csv_path, [_node(0, 0, 0)])
    output_root = tmp_path / "predictions"
    original_rename = bridge_module._rename_noreplace_at

    def replace_child_then_publish(
        source_parent_fd: int, source_name: str, destination_parent_fd: int, destination_name: str
    ) -> None:
        source_parent = Path(os.readlink(f"/proc/self/fd/{source_parent_fd}"))
        source = source_parent / source_name
        competitor = source_parent / ".competitor.geff"
        competitor.mkdir()
        (competitor / "keep").write_bytes(b"competitor")
        shutil.rmtree(source)
        original_rename(source_parent_fd, competitor.name, destination_parent_fd, destination_name)

    monkeypatch.setattr(bridge_module, "_rename_noreplace_at", replace_child_then_publish)
    with pytest.raises(OSError, match=r"(?:temporary GEFF|final GEFF) identity"):
        postprocessed_csv_to_geffs(csv_path, output_root, sample_ids=(SAMPLE,), provenance={})

    final = output_root / f"{SAMPLE}.geff"
    assert (final / "keep").read_bytes() == b"competitor"
    assert not list(output_root.glob(".*.build-*.tmp"))


def test_bridge_does_not_restat_and_remove_child_replaced_before_serializer_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    csv_path = tmp_path / "submission.csv"
    _write_csv(csv_path, [_node(0, 0, 0)])
    output_root = tmp_path / "predictions"

    def replace_child_then_fail(
        destination: LocalStore, bucket: dict[str, object], *, overwrite: bool = False
    ) -> None:
        child = Path(os.readlink(Path(destination.root)))
        competitor = child.with_name(".competitor.geff")
        competitor.mkdir()
        (competitor / "keep").write_bytes(b"competitor")
        shutil.rmtree(child)
        competitor.rename(child)
        raise OSError("synthetic serializer failure")

    monkeypatch.setattr(bridge_module, "_build_geff", replace_child_then_fail)
    with pytest.raises(OSError, match="serializer failure"):
        postprocessed_csv_to_geffs(csv_path, output_root, sample_ids=(SAMPLE,), provenance={})

    parents = list(output_root.glob(".*.build-*.tmp"))
    assert len(parents) == 1
    assert (parents[0] / f"{SAMPLE}.geff" / "keep").read_bytes() == b"competitor"


@pytest.mark.parametrize("replacement", ["file", "symlink"])
def test_bridge_preserves_partial_child_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, replacement: str
) -> None:
    csv_path = tmp_path / "submission.csv"
    _write_csv(csv_path, [_node(0, 0, 0)])
    output_root = tmp_path / "predictions"

    def replace_child_then_fail(
        destination: LocalStore, bucket: dict[str, object], *, overwrite: bool = False
    ) -> None:
        child = Path(os.readlink(Path(destination.root)))
        if replacement == "file":
            shutil.rmtree(child)
            child.write_bytes(b"competitor-file")
        else:
            competitor = child.with_name(".competitor.geff")
            competitor.mkdir()
            (competitor / "keep").write_bytes(b"competitor-symlink")
            shutil.rmtree(child)
            child.symlink_to(competitor.name, target_is_directory=True)
        raise OSError("synthetic serializer failure")

    monkeypatch.setattr(bridge_module, "_build_geff", replace_child_then_fail)
    with pytest.raises(OSError, match="serializer failure"):
        postprocessed_csv_to_geffs(csv_path, output_root, sample_ids=(SAMPLE,), provenance={})

    parents = list(output_root.glob(".*.build-*.tmp"))
    assert len(parents) == 1
    child = parents[0] / f"{SAMPLE}.geff"
    if replacement == "file":
        assert child.read_bytes() == b"competitor-file"
    else:
        assert child.is_symlink()
        assert (parents[0] / ".competitor.geff" / "keep").read_bytes() == b"competitor-symlink"


def test_bridge_rejects_symlink_child_swap_after_identity_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    csv_path = tmp_path / "submission.csv"
    _write_csv(csv_path, [_node(0, 0, 0)])
    output_root = tmp_path / "predictions"
    original_publish = bridge_module._rename_noreplace_at

    def swap_symlink_then_publish(
        source_parent_fd: int, source_name: str, destination_parent_fd: int, destination_name: str
    ) -> None:
        source_parent = Path(os.readlink(f"/proc/self/fd/{source_parent_fd}"))
        source = source_parent / source_name
        competitor = source_parent / ".competitor.geff"
        competitor.mkdir()
        (competitor / "keep").write_bytes(b"competitor")
        shutil.rmtree(source)
        source.symlink_to(competitor.name, target_is_directory=True)
        original_publish(source_parent_fd, source_name, destination_parent_fd, destination_name)

    monkeypatch.setattr(bridge_module, "_rename_noreplace_at", swap_symlink_then_publish)
    with pytest.raises(OSError, match=r"(?:temporary GEFF|final GEFF) identity"):
        postprocessed_csv_to_geffs(csv_path, output_root, sample_ids=(SAMPLE,), provenance={})

    final = output_root / f"{SAMPLE}.geff"
    assert final.is_symlink()
    parents = list(output_root.glob(".*.build-*.tmp"))
    assert len(parents) == 1
    assert (parents[0] / ".competitor.geff" / "keep").read_bytes() == b"competitor"


def test_bridge_roundtrips_sparse_node_ids_from_raw_geff_via_source_csv(tmp_path: Path) -> None:
    raw_geff = tmp_path / "raw.geff"
    bridge_module._build_geff(
        raw_geff,
        {
            "nodes": {
                7: {"t": 0, "z": 1, "y": 2, "x": 3},
                42: {"t": 1, "z": 2, "y": 3, "x": 4},
                99: {"t": 2, "z": 3, "y": 4, "x": 5},
            },
            "edges": [(7, 42), (42, 99)],
        },
        overwrite=True,
    )
    source_csv = tmp_path / "submission.csv"
    write_submission_csv({SAMPLE: raw_geff}, source_csv)

    written = postprocessed_csv_to_geffs(
        source_csv,
        tmp_path / "predictions",
        sample_ids=(SAMPLE,),
        provenance={"recipe": "C"},
    )

    assert bridge_module._read_prediction_signature(written[SAMPLE]) == {
        "nodes": {
            7: (0, 1, 2, 3),
            42: (1, 2, 3, 4),
            99: (2, 3, 4, 5),
        },
        "edges": [(7, 42), (42, 99)],
    }


def test_bridge_rejects_invalid_sentinels_and_noncontiguous_rows(tmp_path: Path) -> None:
    csv_path = tmp_path / "bad.csv"
    bad_node = _node(0, 0, 0)
    bad_node["source_id"] = 7
    _write_csv(csv_path, [bad_node])
    with pytest.raises(ValueError, match="sentinel"):
        postprocessed_csv_to_geffs(csv_path, tmp_path / "predictions", sample_ids=(SAMPLE,), provenance={})

    bad_ids = [_node(1, 1, 0)]
    _write_csv(csv_path, bad_ids)
    with pytest.raises(ValueError, match=r"row id.*contiguous"):
        postprocessed_csv_to_geffs(csv_path, tmp_path / "predictions-2", sample_ids=(SAMPLE,), provenance={})


def test_bridge_rejects_duplicate_or_negative_sparse_node_ids(tmp_path: Path) -> None:
    csv_path = tmp_path / "bad.csv"
    _write_csv(csv_path, [_node(0, 7, 0), _node(1, 7, 1)])
    with pytest.raises(ValueError, match="unique"):
        postprocessed_csv_to_geffs(csv_path, tmp_path / "duplicate", sample_ids=(SAMPLE,), provenance={})

    _write_csv(csv_path, [_node(0, -7, 0)])
    with pytest.raises(ValueError, match="unique and non-negative"):
        postprocessed_csv_to_geffs(csv_path, tmp_path / "negative", sample_ids=(SAMPLE,), provenance={})


def test_bridge_rejects_dangling_sparse_edge_endpoint(tmp_path: Path) -> None:
    csv_path = tmp_path / "bad.csv"
    _write_csv(csv_path, [_node(0, 7, 0), _node(1, 42, 1), _edge(2, 7, 99)])
    with pytest.raises(ValueError, match="endpoint"):
        postprocessed_csv_to_geffs(csv_path, tmp_path / "predictions", sample_ids=(SAMPLE,), provenance={})


def test_bridge_rejects_whitespace_padded_integer_fields(tmp_path: Path) -> None:
    csv_path = tmp_path / "bad.csv"
    row = _node(0, 0, 0)
    row["id"] = " 0"
    _write_csv(csv_path, [row])
    with pytest.raises(ValueError, match="integer"):
        postprocessed_csv_to_geffs(csv_path, tmp_path / "predictions", sample_ids=(SAMPLE,), provenance={})


@pytest.mark.parametrize("field", ["id", "node_id", "t", "z", "y", "x", "source_id", "target_id"])
@pytest.mark.parametrize("padding", ["leading", "trailing", "tab"])
def test_csv_integer_fields_reject_surrounding_whitespace(
    tmp_path: Path, field: str, padding: str
) -> None:
    csv_path = tmp_path / "bad.csv"
    rows = [_node(0, 0, 0), _node(1, 1, 1), _edge(2, 0, 1)]
    row = rows[0] if field in {"id", "node_id", "t", "z", "y", "x"} else rows[2]
    value = str(row[field])
    row[field] = {"leading": f" {value}", "trailing": f"{value} ", "tab": f"\t{value}"}[padding]
    _write_csv(csv_path, rows)
    with pytest.raises(ValueError, match="integer"):
        postprocessed_csv_to_geffs(
            csv_path,
            tmp_path / f"predictions-{field}-{padding}",
            sample_ids=(SAMPLE,),
            provenance={},
        )


def test_manifest_rejects_reserved_provenance_collision(tmp_path: Path) -> None:
    csv_path = tmp_path / "submission.csv"
    _write_csv(csv_path, [_node(0, 0, 0)])
    prediction = postprocessed_csv_to_geffs(
        csv_path, tmp_path / "predictions", sample_ids=(SAMPLE,), provenance={}
    )[SAMPLE]
    with pytest.raises(ValueError, match="reserved"):
        write_prediction_manifest(prediction, selection_lock_id="a" * 64, provenance={"nodes": 99})


@pytest.mark.parametrize(
    "key",
    [
        "selection_lock_id",
        "ground_truth_included",
        "ground_truth_inputs",
        "directory_sha256",
        "files",
        "total_bytes",
        "hash_algorithm",
        "nodes",
        "edges",
        "forks",
        "manifest_created_at",
        "prediction_path",
        "prediction_name",
        "schema_version",
    ],
)
def test_manifest_rejects_each_reserved_provenance_key(tmp_path: Path, key: str) -> None:
    csv_path = tmp_path / f"submission-{key}.csv"
    _write_csv(csv_path, [_node(0, 0, 0)])
    prediction = postprocessed_csv_to_geffs(
        csv_path, tmp_path / f"predictions-{key}", sample_ids=(SAMPLE,), provenance={}
    )[SAMPLE]
    with pytest.raises(ValueError, match="reserved"):
        write_prediction_manifest(prediction, selection_lock_id="a" * 64, provenance={key: "forged"})
    assert not prediction_manifest_path(prediction).exists()


def test_json_exclusive_cleans_owned_temp_on_write_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "receipt.json"

    def fail_write(_fd: int, _payload: bytes) -> int:
        raise OSError("synthetic write failure")

    monkeypatch.setattr(bridge_module.os, "write", fail_write)
    with pytest.raises(OSError, match="synthetic write failure"):
        bridge_module.write_json_exclusive(target, {"status": "READY"})
    assert not target.exists()
    assert not list(tmp_path.glob(".receipt.json.*.tmp"))


def test_json_exclusive_rejects_zero_byte_write_without_looping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "receipt.json"

    monkeypatch.setattr(bridge_module.os, "write", lambda _fd, _payload: 0)
    with pytest.raises(OSError, match="short"):
        bridge_module.write_json_exclusive(target, {"status": "READY"})
    assert not target.exists()
    assert not list(tmp_path.glob(".receipt.json.*.tmp"))


def test_bridge_rejects_non_adjacent_edge_and_degree_violation(tmp_path: Path) -> None:
    csv_path = tmp_path / "bad.csv"
    rows = [_node(0, 0, 0), _node(1, 1, 2), _edge(2, 0, 1)]
    _write_csv(csv_path, rows)
    with pytest.raises(ValueError, match="adjacent"):
        postprocessed_csv_to_geffs(csv_path, tmp_path / "predictions", sample_ids=(SAMPLE,), provenance={})

    rows = [_node(0, 0, 0), _node(1, 1, 1), _node(2, 2, 1), _node(3, 3, 1)]
    rows.extend([_edge(4, 0, 1), _edge(5, 0, 2), _edge(6, 0, 3)])
    _write_csv(csv_path, rows)
    with pytest.raises(ValueError, match="outdegree"):
        postprocessed_csv_to_geffs(csv_path, tmp_path / "predictions-2", sample_ids=(SAMPLE,), provenance={})


def test_manifest_is_geff_sibling_and_contains_no_absolute_or_gt_path(tmp_path: Path) -> None:
    csv_path = tmp_path / "submission.csv"
    _write_csv(csv_path, [_node(0, 0, 0)])
    prediction = postprocessed_csv_to_geffs(
        csv_path,
        tmp_path / "predictions",
        sample_ids=(SAMPLE,),
        provenance={"recipe": "C"},
    )[SAMPLE]

    manifest = write_prediction_manifest(
        prediction,
        selection_lock_id="a" * 64,
        provenance={"role": "prediction"},
    )
    assert manifest == prediction_manifest_path(prediction)
    assert manifest.name == f"{prediction.name}.manifest.json"
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert payload["ground_truth_included"] is False
    assert payload["ground_truth_inputs"] == []
    assert "gt/" not in json.dumps(payload).lower()
    created = datetime.fromisoformat(payload["manifest_created_at"])
    assert created.tzinfo == UTC
    assert created <= datetime.now(UTC)
    assert str(tmp_path) not in json.dumps(payload)
    assert mint_prediction_token(prediction).directory_sha256 == payload["directory_sha256"]


def test_bridge_rejects_existing_or_symlink_output_root_without_writing(tmp_path: Path) -> None:
    csv_path = tmp_path / "submission.csv"
    _write_csv(csv_path, [_node(0, 0, 0)])
    output_root = tmp_path / "predictions"
    output_root.mkdir()
    sentinel = output_root / "sentinel.txt"
    sentinel.write_text("keep", encoding="utf-8")
    with pytest.raises((FileExistsError, ValueError), match=r"fresh|exist|directory"):
        postprocessed_csv_to_geffs(
            csv_path, output_root, sample_ids=(SAMPLE,), provenance={}
        )
    assert sentinel.read_text(encoding="utf-8") == "keep"

    target = tmp_path / "target"
    target.mkdir()
    linked = tmp_path / "linked-predictions"
    linked.symlink_to(target, target_is_directory=True)
    with pytest.raises((FileExistsError, ValueError), match=r"fresh|symlink|exist"):
        postprocessed_csv_to_geffs(
            csv_path, linked, sample_ids=(SAMPLE,), provenance={}
        )
    assert not (target / f"{SAMPLE}.geff").exists()


def test_bridge_open_root_anchor_failure_cleans_owned_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    csv_path = tmp_path / "submission.csv"
    _write_csv(csv_path, [_node(0, 0, 0)])
    output_root = tmp_path / "predictions"

    def fail_open(path: Path, *, expected_identity: tuple[int, int] | None = None) -> tuple[int, tuple[int, int]]:
        raise OSError("synthetic root anchor failure")

    monkeypatch.setattr(bridge_module, "_open_directory_fd", fail_open)
    with pytest.raises(OSError, match="root anchor failure"):
        postprocessed_csv_to_geffs(csv_path, output_root, sample_ids=(SAMPLE,), provenance={})
    assert not output_root.exists()


def test_bridge_rechecks_lossless_csv_topology_after_geff_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    csv_path = tmp_path / "submission.csv"
    _write_csv(csv_path, [_node(0, 0, 0), _node(1, 1, 1), _edge(2, 0, 1)])
    original = bridge_module._build_geff

    def tampered(
        destination: Path, bucket: dict[str, object], *, overwrite: bool = False
    ) -> None:
        altered = {
            "nodes": {int(key): dict(value) for key, value in bucket["nodes"].items()},
            "edges": list(bucket["edges"]),
        }
        altered["nodes"][0]["x"] = 99
        original(destination, altered, overwrite=overwrite)

    monkeypatch.setattr(bridge_module, "_build_geff", tampered)
    with pytest.raises(ValueError, match=r"lossless|topology|coordinate"):
        postprocessed_csv_to_geffs(
            csv_path, tmp_path / "predictions", sample_ids=(SAMPLE,), provenance={}
        )


def test_bridge_fsync_failure_does_not_remove_replaced_competitor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    csv_path = tmp_path / "submission.csv"
    _write_csv(csv_path, [_node(0, 0, 0)])
    output_root = tmp_path / "predictions"
    replaced = False

    def replace_then_fail(path: Path) -> None:
        nonlocal replaced
        if Path(path) == output_root and not replaced:
            final = output_root / f"{SAMPLE}.geff"
            competitor = output_root / ".competitor.geff"
            competitor.mkdir()
            (competitor / "competitor").write_bytes(b"keep")
            shutil.rmtree(final)
            competitor.rename(final)
            replaced = True
            raise OSError("synthetic bridge fsync failure")
        raise OSError("synthetic bridge fsync failure")

    monkeypatch.setattr(bridge_module, "_fsync_directory_fd", lambda _descriptor: replace_then_fail(output_root))
    with pytest.raises(OSError, match="fsync"):
        postprocessed_csv_to_geffs(
            csv_path, output_root, sample_ids=(SAMPLE,), provenance={}
        )
    assert replaced is True
    assert (output_root / f"{SAMPLE}.geff" / "competitor").read_bytes() == b"keep"


def test_bridge_callback_final_replacement_is_fail_closed(
    tmp_path: Path,
) -> None:
    csv_path = tmp_path / "submission.csv"
    _write_csv(csv_path, [_node(0, 0, 0)])
    output_root = tmp_path / "predictions"

    def replace_final(path: Path, identity: tuple[int, int]) -> None:
        final = output_root / f"{SAMPLE}.geff"
        if path != final:
            return
        competitor = output_root / ".competitor.geff"
        competitor.mkdir()
        (competitor / "keep").write_bytes(b"callback-competitor")
        shutil.rmtree(final)
        competitor.rename(final)

    with pytest.raises(OSError, match="after callback"):
        postprocessed_csv_to_geffs(
            csv_path,
            output_root,
            sample_ids=(SAMPLE,),
            provenance={},
            on_published=replace_final,
        )
    assert (output_root / f"{SAMPLE}.geff" / "keep").read_bytes() == b"callback-competitor"


def test_bridge_fsync_final_replacement_is_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    csv_path = tmp_path / "submission.csv"
    _write_csv(csv_path, [_node(0, 0, 0)])
    output_root = tmp_path / "predictions"
    replaced = False

    def replace_on_fsync(_descriptor: int) -> None:
        nonlocal replaced
        if not replaced:
            final = output_root / f"{SAMPLE}.geff"
            competitor = output_root / ".competitor.geff"
            competitor.mkdir()
            (competitor / "keep").write_bytes(b"fsync-competitor")
            shutil.rmtree(final)
            competitor.rename(final)
            replaced = True

    monkeypatch.setattr(bridge_module, "_fsync_directory_fd", replace_on_fsync)
    with pytest.raises(OSError, match="after fsync"):
        postprocessed_csv_to_geffs(csv_path, output_root, sample_ids=(SAMPLE,), provenance={})
    assert replaced is True
    assert (output_root / f"{SAMPLE}.geff" / "keep").read_bytes() == b"fsync-competitor"


def test_bridge_roundtrip_final_replacement_is_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    csv_path = tmp_path / "submission.csv"
    _write_csv(csv_path, [_node(0, 0, 0)])
    output_root = tmp_path / "predictions"
    original_read = bridge_module._read_prediction_signature

    def replace_after_read(store: object) -> dict[str, object]:
        signature = original_read(store)
        competitor = output_root / ".competitor.geff"
        competitor.mkdir()
        (competitor / "keep").write_bytes(b"roundtrip-competitor")
        final = output_root / f"{SAMPLE}.geff"
        shutil.rmtree(final)
        competitor.rename(final)
        return signature

    monkeypatch.setattr(bridge_module, "_read_prediction_signature", replace_after_read)
    with pytest.raises(OSError, match="after roundtrip"):
        postprocessed_csv_to_geffs(csv_path, output_root, sample_ids=(SAMPLE,), provenance={})
    assert (output_root / f"{SAMPLE}.geff" / "keep").read_bytes() == b"roundtrip-competitor"


def test_bridge_roundtrip_output_root_replacement_is_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    csv_path = tmp_path / "submission.csv"
    _write_csv(csv_path, [_node(0, 0, 0)])
    output_root = tmp_path / "predictions"
    original_read = bridge_module._read_prediction_signature

    def replace_root_after_read(store: object) -> dict[str, object]:
        signature = original_read(store)
        competitor = tmp_path / "competitor-root"
        competitor.mkdir()
        (competitor / "keep").write_bytes(b"root-competitor")
        shutil.rmtree(output_root)
        competitor.rename(output_root)
        return signature

    monkeypatch.setattr(bridge_module, "_read_prediction_signature", replace_root_after_read)
    with pytest.raises(OSError, match=r"(?:output root|final GEFF) identity changed"):
        postprocessed_csv_to_geffs(csv_path, output_root, sample_ids=(SAMPLE,), provenance={})
    assert (output_root / "keep").read_bytes() == b"root-competitor"


def test_bridge_parse_failure_removes_owned_root_without_restat(tmp_path: Path) -> None:
    csv_path = tmp_path / "empty.csv"
    _write_csv(csv_path, [])
    output_root = tmp_path / "predictions"
    with pytest.raises(ValueError, match="empty"):
        postprocessed_csv_to_geffs(
            csv_path, output_root, sample_ids=(SAMPLE,), provenance={}
        )
    assert not output_root.exists()


def test_bridge_parse_failure_with_callback_removes_owned_root(tmp_path: Path) -> None:
    csv_path = tmp_path / "empty.csv"
    _write_csv(csv_path, [])
    output_root = tmp_path / "predictions"
    callbacks: list[Path] = []

    def remember(path: Path, identity: tuple[int, int]) -> None:
        callbacks.append(path)

    with pytest.raises(ValueError, match="empty"):
        postprocessed_csv_to_geffs(
            csv_path,
            output_root,
            sample_ids=(SAMPLE,),
            provenance={},
            on_published=remember,
        )
    assert callbacks == [output_root]
    assert not output_root.exists()


def test_bridge_partial_build_failure_cleans_owned_temp_and_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    csv_path = tmp_path / "submission.csv"
    _write_csv(csv_path, [_node(0, 0, 0)])
    output_root = tmp_path / "predictions"

    def partial_build(destination: LocalStore, bucket: dict[str, object], *, overwrite: bool = False) -> None:
        (Path(destination.root) / "partial").write_bytes(b"partial")
        raise OSError("synthetic serializer failure")

    monkeypatch.setattr(bridge_module, "_build_geff", partial_build)
    with pytest.raises(OSError, match="serializer"):
        postprocessed_csv_to_geffs(
            csv_path, output_root, sample_ids=(SAMPLE,), provenance={}
        )
    assert not output_root.exists()
    assert not list(tmp_path.glob("predictions/.*.tmp"))


def test_bridge_partial_build_failure_with_callback_removes_owned_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    csv_path = tmp_path / "submission.csv"
    _write_csv(csv_path, [_node(0, 0, 0)])
    output_root = tmp_path / "predictions"

    def partial_build(destination: LocalStore, bucket: dict[str, object], *, overwrite: bool = False) -> None:
        (Path(destination.root) / "partial").write_bytes(b"partial")
        raise OSError("synthetic serializer failure")

    monkeypatch.setattr(bridge_module, "_build_geff", partial_build)
    with pytest.raises(OSError, match="serializer"):
        postprocessed_csv_to_geffs(
            csv_path,
            output_root,
            sample_ids=(SAMPLE,),
            provenance={},
            on_published=lambda path, identity: None,
        )
    assert not output_root.exists()


def test_bridge_callback_failure_removes_owned_root(tmp_path: Path) -> None:
    csv_path = tmp_path / "submission.csv"
    _write_csv(csv_path, [_node(0, 0, 0)])
    output_root = tmp_path / "predictions"

    def fail_callback(path: Path, identity: tuple[int, int]) -> None:
        raise RuntimeError("synthetic ownership callback failure")

    with pytest.raises(RuntimeError, match="ownership callback"):
        postprocessed_csv_to_geffs(
            csv_path,
            output_root,
            sample_ids=(SAMPLE,),
            provenance={},
            on_published=fail_callback,
        )
    assert not output_root.exists()


def test_bridge_fsync_failure_with_callback_removes_empty_owned_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    csv_path = tmp_path / "submission.csv"
    _write_csv(csv_path, [_node(0, 0, 0)])
    output_root = tmp_path / "predictions"

    monkeypatch.setattr(
        bridge_module,
        "_fsync_directory_fd",
        lambda path: (_ for _ in ()).throw(OSError("synthetic fsync failure")),
    )
    with pytest.raises(OSError, match="fsync"):
        postprocessed_csv_to_geffs(
            csv_path,
            output_root,
            sample_ids=(SAMPLE,),
            provenance={},
            on_published=lambda path, identity: None,
        )
    assert not output_root.exists()


def test_validate_prediction_geff_rejects_empty_graph(tmp_path: Path) -> None:
    td = pytest.importorskip("tracksdata")
    import polars as pl

    graph = td.graph.IndexedRXGraph()
    for key in ("z", "y", "x"):
        graph.add_node_attr_key(key, dtype=pl.Int64, default_value=0)
    path = tmp_path / f"{SAMPLE}.geff"
    graph.to_geff(path, overwrite=False)
    with pytest.raises(ValueError, match=r"empty|node"):
        validate_prediction_geff(path, SAMPLE, expected_volume_shape_tzyx=(2, 16, 16, 16))
