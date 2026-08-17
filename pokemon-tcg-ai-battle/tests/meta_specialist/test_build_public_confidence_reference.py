"""Contracts for the public bucket reference builder."""

from __future__ import annotations

import json
from pathlib import Path

from tests.meta_specialist.test_trajectory_v1 import _two_choice_forced_stop_transition


def _row(*, partition: str = "train", opponent_id: str = "secret", seat: int = 1) -> dict[str, object]:
    transition, _ = _two_choice_forced_stop_transition()
    return {
        "component_id": "a" * 64,
        "game_id": "b" * 64,
        "opponent_id": opponent_id,
        "seat": seat,
        "partition": partition,
        "schema": "meta-specialist-v4-dagger-transition-v1",
        "transition_index": 0,
        "transition": transition.to_dict(),
    }


def test_builder_counts_only_selected_partition_and_does_not_emit_identity_metadata(tmp_path: Path) -> None:
    from scripts.build_public_confidence_reference import build_public_bucket_reference

    source = tmp_path / "transitions.jsonl"
    source.write_text(
        "\n".join(
            [
                json.dumps(_row(partition="train", opponent_id="private-a", seat=0), sort_keys=True),
                json.dumps(_row(partition="validation", opponent_id="private-b", seat=1), sort_keys=True),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    result = build_public_bucket_reference(source, partition="train", rare_count_threshold=2)

    assert result["schema_version"] == "meta-specialist-public-bucket-reference-v1"
    assert result["source_sha256"]
    assert result["partition"] == "train"
    assert result["transition_count"] == 1
    assert result["prefix_count"] == 3
    assert result["bucket_counts"]
    serialized = json.dumps(result, sort_keys=True)
    assert result["privacy"]["uses_opponent_id"] is False
    assert result["privacy"]["uses_seat"] is False
    assert "private-a" not in serialized
    assert "private-b" not in serialized


def test_builder_is_deterministic_and_rejects_unknown_partition(tmp_path: Path) -> None:
    from scripts.build_public_confidence_reference import build_public_bucket_reference

    source = tmp_path / "transitions.jsonl"
    source.write_text(json.dumps(_row()) + "\n", encoding="utf-8")
    first = build_public_bucket_reference(source, partition="train", rare_count_threshold=2)
    second = build_public_bucket_reference(source, partition="train", rare_count_threshold=2)
    assert first == second

    try:
        build_public_bucket_reference(source, partition="test", rare_count_threshold=2)
    except ValueError as exc:
        assert "partition" in str(exc)
    else:  # pragma: no cover - explicit contract failure
        raise AssertionError("unknown partition must fail closed")
