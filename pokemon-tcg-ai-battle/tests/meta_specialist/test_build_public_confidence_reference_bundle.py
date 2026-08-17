"""Contracts for the multi-source public confidence reference bundle."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tests.meta_specialist.test_build_public_confidence_reference import _row


def _write_source(path: Path, *, opponent_id: str, partition: str = "train") -> None:
    path.write_text(
        json.dumps(_row(partition=partition, opponent_id=opponent_id, seat=0)) + "\n",
        encoding="utf-8",
    )


def _source_list_sha256(payload: dict[str, object]) -> str:
    source_manifest = {
        "partition": payload["partition"],
        "source_list": payload["source_list"],
    }
    canonical = json.dumps(source_manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def test_bundle_aggregates_fixed_source_order_and_hash_binds_each_source(tmp_path: Path) -> None:
    from scripts.build_public_confidence_reference_bundle import (
        REFERENCE_BUNDLE_SCHEMA_V1,
        build_public_bucket_reference_bundle,
    )

    seed0 = tmp_path / "seed0.jsonl"
    seed1 = tmp_path / "seed1.jsonl"
    _write_source(seed0, opponent_id="private-seed0")
    _write_source(seed1, opponent_id="private-seed1")

    result = build_public_bucket_reference_bundle([seed0, seed1], partition="train")

    assert result["schema_version"] == REFERENCE_BUNDLE_SCHEMA_V1
    assert result["partition"] == "train"
    assert result["source_count"] == 2
    source_list = result["source_list"]
    assert isinstance(source_list, list)
    assert [item["ordinal"] for item in source_list] == [0, 1]
    assert source_list[0]["source_sha256"] == hashlib.sha256(seed0.read_bytes()).hexdigest()
    assert source_list[1]["source_sha256"] == hashlib.sha256(seed1.read_bytes()).hexdigest()
    assert result["source_list_sha256"] == _source_list_sha256(result)
    assert result["transition_count"] == 2
    assert result["prefix_count"] == 6
    assert result["bucket_counts"]
    serialized = json.dumps(result, sort_keys=True)
    assert "private-seed0" not in serialized
    assert "private-seed1" not in serialized
    assert result["privacy"] == {
        "uses_opponent_id": False,
        "uses_seat": False,
        "uses_policy_identity": False,
        "uses_hidden_fields": False,
    }


def test_bundle_order_is_fixed_and_reversing_sources_changes_only_source_binding(tmp_path: Path) -> None:
    from scripts.build_public_confidence_reference_bundle import build_public_bucket_reference_bundle

    seed0 = tmp_path / "seed0.jsonl"
    seed1 = tmp_path / "seed1.jsonl"
    _write_source(seed0, opponent_id="private-seed0")
    _write_source(seed1, opponent_id="private-seed1")

    forward = build_public_bucket_reference_bundle([seed0, seed1])
    reverse = build_public_bucket_reference_bundle([seed1, seed0])

    assert forward["source_list"] != reverse["source_list"]
    assert forward["source_list_sha256"] != reverse["source_list_sha256"]
    assert forward["bucket_counts"] == reverse["bucket_counts"]
    assert forward["transition_count"] == reverse["transition_count"] == 2


def test_bundle_requires_two_distinct_sources_and_rejects_empty_partition(tmp_path: Path) -> None:
    from scripts.build_public_confidence_reference_bundle import build_public_bucket_reference_bundle

    source = tmp_path / "source.jsonl"
    _write_source(source, opponent_id="private")

    with pytest.raises(ValueError, match="at least two"):
        build_public_bucket_reference_bundle([source])
    with pytest.raises(ValueError, match="distinct"):
        build_public_bucket_reference_bundle([source, source])

    validation_only = tmp_path / "validation.jsonl"
    _write_source(validation_only, opponent_id="private-validation", partition="validation")
    with pytest.raises(ValueError, match="selected partition contains no"):
        build_public_bucket_reference_bundle([source, validation_only])
