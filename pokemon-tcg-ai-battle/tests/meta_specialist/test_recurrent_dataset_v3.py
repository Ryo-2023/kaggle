"""Full-corpus authority for recurrent BC data must remain sealed end to end."""

from __future__ import annotations

import copy
from dataclasses import replace
import hashlib
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from mage_ptcg.meta_specialist.actor_visible_features_v1 import make_test_card_vocabulary_v1
from mage_ptcg.meta_specialist.actor_visible_v2 import build_actor_visible_decision_state_v2
from mage_ptcg.meta_specialist.local_dataset_v2 import (
    _record_content_hash,
    _record_id,
    build_local_record_v2,
    canonical_json_bytes_v2,
    derive_complete_action_id_v1,
    make_source_permission_manifest_v1,
)
from mage_ptcg.meta_specialist.recurrent_dataset_v3 import (
    build_recurrent_selection_manifest_v3,
    materialize_recurrent_selection_v3,
    prepare_sealed_recurrent_lane_v3,
    read_recurrent_selection_manifest_v3,
    stream_prepared_recurrent_selection_v3,
    stream_recurrent_selection_v3,
)
from mage_ptcg.meta_specialist import recurrent_dataset_v3 as recurrent_dataset


_QUALIFICATION_TIME = "2026-08-09T00:00:00Z"


def _observation() -> dict[str, object]:
    player = {
        "active": [], "asleep": False, "bench": [], "benchMax": 5,
        "burned": False, "confused": False, "deckCount": 60, "discard": [],
        "hand": [], "handCount": 0, "paralyzed": False, "poisoned": False,
        "prize": [],
    }
    return {
        "current": {
            "energyAttached": False, "firstPlayer": 0, "looking": None,
            "players": [player, {**player, "hand": None}], "result": -1,
            "retreated": False, "stadium": [], "stadiumPlayed": False,
            "supporterPlayed": False, "turn": 2, "turnActionCount": 0,
            "yourIndex": 0,
        },
        "select": {
            "context": 41, "contextCard": None, "deck": None, "effect": None,
            "maxCount": 1, "minCount": 1, "option": [{"type": 1}, {"type": 2}],
            "remainDamageCounter": 0, "remainEnergyCost": 0, "type": 9,
        },
        "step": 0,
    }


def _write_full_corpus_root(
    tmp_path: Path, *, untrusted: bool = False, paired_episodes: bool = False,
    snapshot_chunk_prefix: str = "", reopened_episode: bool = False,
) -> Path:
    """Write 36 genuine local records plus the independent snapshot authorities."""
    root = tmp_path / "sealed-root"
    root.mkdir()
    permission = make_source_permission_manifest_v1(
        artifact_sha256="a" * 64, source_kind="fixture-teacher",
        allowed_usages=("training-local",), revision="fixture", issuer="tests",
        valid_from_utc=None, expires_at_utc=None,
    )
    source = {
        "kind": "fixture-teacher", "artifact_sha256": "a" * 64,
        "synthetic": False, "synthetic_fields": [], "training_eligible": True,
        "usage_class": "qualified_training",
        "permission_manifest_id": permission["permission_manifest_id"],
    }
    vocabulary = make_test_card_vocabulary_v1(())
    state = build_actor_visible_decision_state_v2(_observation())
    selected = (state.legal_actions[0].local_action_id,)
    bootstrap = build_local_record_v2(
        state=state, vocabulary=vocabulary, episode_id_hash="e" * 64, decision_index=0,
        selection=selected, behavior={"status": "action_only", "selection": list(selected)},
        teacher={"status": "unavailable", "reason": "bootstrap"},
        student={"status": "fallback", "selection": [], "scores": [], "reason": "none"},
        source=source, provenance={"source_record_ordinal": 0},
    )
    complete = derive_complete_action_id_v1(
        decision_id=bootstrap["decision_id"], selection_type=9,
        selection_context=41, selection=selected,
    )
    base = build_local_record_v2(
        state=state, vocabulary=vocabulary, episode_id_hash="e" * 64, decision_index=0,
        selection=selected, behavior={"status": "action_only", "selection": list(selected)},
        teacher={
            "status": "available", "teacher_id": "fixture", "teacher_revision": "r1",
            "input_id": bootstrap["model_input_id"], "target_kind": "hard_selection",
            "quality_weight": 1.0, "value_target": None,
            "mass_rows": [{"complete_action_id": complete, "selection": list(selected), "weight": 1}],
        },
        student={"status": "fallback", "selection": [], "scores": [], "reason": "none"},
        source=source, provenance={"source_record_ordinal": 0},
    )
    records: list[dict[str, object]] = []
    for index in range(36):
        record = copy.deepcopy(base)
        if reopened_episode and index in (0, 2):
            episode_seed = "fixture-reopened-episode-a"
        elif reopened_episode and index == 1:
            episode_seed = "fixture-reopened-episode-b"
        else:
            episode_seed = f"fixture-episode-{index // 2 if paired_episodes else index}"
        episode = hashlib.sha256(episode_seed.encode()).hexdigest()
        record["episode_id_hash"] = episode
        record["decision_index"] = index
        record["provenance"] = {"source_record_ordinal": index}
        record["record_id"] = _record_id(
            decision_id=record["decision_id"], episode_id_hash=episode, decision_index=index,
        )
        record["content_hash"] = _record_content_hash(record)
        records.append(record)
    if untrusted:
        records[-1]["source"] = {**records[-1]["source"], "permission_manifest_id": "f" * 64}
        records[-1]["content_hash"] = _record_content_hash(records[-1])

    chunks: list[dict[str, object]] = []
    for shard_index, group in enumerate((records[:18], records[18:])):
        name = f"dataset-{shard_index:04d}.jsonl"
        raw = b"".join(canonical_json_bytes_v2(record) + b"\n" for record in group)
        (root / name).write_bytes(raw)
        declared_path = f"{snapshot_chunk_prefix}/{name}" if snapshot_chunk_prefix else name
        chunks.append({
            "path": declared_path, "dataset_snapshot_sha256": hashlib.sha256(raw).hexdigest(),
            "manifest_id": "b" * 64, "manifest_content_hash": "c" * 64,
        })
    snapshot = {
        "schema_version": "specialist-training-snapshot-index-v1",
        "dataset_snapshot_sha256": "d" * 64, "examples_total": len(records),
        "dataset_chunks": chunks,
        "duplicate_cap": {
            "ubiquitous_near_duplicate_ids": [base["near_duplicate_id"]],
            "ubiquity_min_episodes": 2,
        },
    }
    (root / "snapshot_index.json").write_bytes(canonical_json_bytes_v2(snapshot))
    (root / "teacher_dataset_manifest.json").write_bytes(canonical_json_bytes_v2({"permission_manifest": permission}))
    return root


def test_full_corpus_manifest_is_component_disjoint_and_reproducible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _write_full_corpus_root(tmp_path)
    output = tmp_path / "selection.json"
    def legacy_split(*_args, **_kwargs):
        raise AssertionError("legacy in-memory split authority was called")
    monkeypatch.setattr(recurrent_dataset, "build_split_manifest_v3", legacy_split, raising=False)

    manifest = build_recurrent_selection_manifest_v3(
        root, lane="alakazam", qualification_time_utc=_QUALIFICATION_TIME, output_path=output,
    )

    assert manifest["records_total"] > 32
    assert "selection" not in manifest
    assert (tmp_path / manifest["selection_index_path"]).is_file()
    assert manifest["split"]["overlap_counters"] == {
        "episode_overlap": 0, "near_duplicate_overlap": 0,
    }
    assert read_recurrent_selection_manifest_v3(output) == manifest


def test_recurrent_selection_rejects_unqualified_or_untrusted_record(tmp_path: Path) -> None:
    root = _write_full_corpus_root(tmp_path, untrusted=True)

    with pytest.raises(ValueError, match="qualified|permission"):
        build_recurrent_selection_manifest_v3(
            root, lane="alakazam", qualification_time_utc=_QUALIFICATION_TIME,
            output_path=tmp_path / "selection.json",
        )


def test_outer_teacher_manifest_allows_terminal_lf_but_pins_raw_bytes(tmp_path: Path) -> None:
    root = _write_full_corpus_root(tmp_path)
    teacher = root / "teacher_dataset_manifest.json"
    teacher.write_bytes(teacher.read_bytes() + b"\n")
    output = tmp_path / "selection.json"

    manifest = build_recurrent_selection_manifest_v3(
        root, lane="alakazam", qualification_time_utc=_QUALIFICATION_TIME, output_path=output,
    )

    assert manifest["teacher_manifest_sha256"] == hashlib.sha256(teacher.read_bytes()).hexdigest()
    assert materialize_recurrent_selection_v3(output, burn_in=0)


def test_snapshot_chunk_safe_relative_prefix_materializes_as_root_basename(tmp_path: Path) -> None:
    root = _write_full_corpus_root(tmp_path, snapshot_chunk_prefix="runs/fixture")
    output = tmp_path / "selection.json"

    manifest = build_recurrent_selection_manifest_v3(
        root, lane="alakazam", qualification_time_utc=_QUALIFICATION_TIME, output_path=output,
    )

    assert manifest["records_total"] == 36
    assert materialize_recurrent_selection_v3(output, burn_in=0)


@pytest.mark.parametrize("bad_path", ["/tmp/dataset-0000.jsonl", "../dataset-0000.jsonl", "runs/fixture/not-dataset.jsonl"])
def test_snapshot_chunk_path_escape_or_non_dataset_basename_is_rejected(tmp_path: Path, bad_path: str) -> None:
    root = _write_full_corpus_root(tmp_path)
    snapshot_path = root / "snapshot_index.json"
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    snapshot["dataset_chunks"][0]["path"] = bad_path
    snapshot_path.write_bytes(canonical_json_bytes_v2(snapshot))

    with pytest.raises(ValueError, match="chunk|strict|escape|snapshot"):
        build_recurrent_selection_manifest_v3(
            root, lane="alakazam", qualification_time_utc=_QUALIFICATION_TIME,
            output_path=tmp_path / "selection.json",
        )


def test_reader_rejects_rehashed_manifest_when_a_pinned_raw_line_changed(tmp_path: Path) -> None:
    root = _write_full_corpus_root(tmp_path)
    output = tmp_path / "selection.json"
    build_recurrent_selection_manifest_v3(
        root, lane="alakazam", qualification_time_utc=_QUALIFICATION_TIME, output_path=output,
    )
    shard = root / "dataset-0000.jsonl"
    original = shard.read_bytes()
    shard.write_bytes(original.replace(b'"fixture"', b'"changed"', 1))

    with pytest.raises(ValueError, match="raw line|snapshot"):
        materialize_recurrent_selection_v3(output, burn_in=0)


def test_root_manifest_rejects_a_tampered_selection_index(tmp_path: Path) -> None:
    root = _write_full_corpus_root(tmp_path)
    output = tmp_path / "selection.json"
    manifest = build_recurrent_selection_manifest_v3(
        root, lane="alakazam", qualification_time_utc=_QUALIFICATION_TIME, output_path=output,
    )
    index = tmp_path / manifest["selection_index_path"]
    index.write_bytes(index.read_bytes().replace(b'"partition":"train"', b'"partition":"validation"', 1))

    with pytest.raises(ValueError, match="index.*SHA|SHA.*index"):
        read_recurrent_selection_manifest_v3(output)


def test_disk_sort_uses_only_the_job_owned_tmpdir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "selection.json"
    parent = recurrent_dataset._job_scratch_parent(output)
    scratch = parent / "job"
    scratch.mkdir()
    source = tmp_path / "source.tsv"
    destination = tmp_path / "sorted.tsv"
    source.write_bytes(b"b\n")
    captured: dict[str, str] = {}
    monkeypatch.setenv("TMPDIR", str(tmp_path / "ambient-escape"))

    def fake_run(command, *, check, stdout, env):
        assert command[:4] == [command[0], "-s", "-t", "\t"]
        assert check is True
        captured.update(env)
        stdout.write(source.read_bytes())
        return None

    monkeypatch.setattr(recurrent_dataset.subprocess, "run", fake_run)
    recurrent_dataset._sort(source, destination, "-k1,1", scratch=scratch)

    assert captured["TMPDIR"] == str(scratch.resolve())
    assert captured["LC_ALL"] == "C"
    assert "ambient-escape" not in captured["TMPDIR"]


def test_job_scratch_parent_rejects_a_symlink_escape(tmp_path: Path) -> None:
    (tmp_path / "recurrent-spool").symlink_to(tmp_path.parent, target_is_directory=True)

    with pytest.raises(ValueError, match="scratch.*escape"):
        recurrent_dataset._job_scratch_parent(tmp_path / "selection.json")


def test_materializer_resets_only_between_full_corpus_episodes(tmp_path: Path) -> None:
    root = _write_full_corpus_root(tmp_path, paired_episodes=True)
    output = tmp_path / "selection.json"
    build_recurrent_selection_manifest_v3(
        root, lane="alakazam", qualification_time_utc=_QUALIFICATION_TIME, output_path=output,
    )

    sequences = materialize_recurrent_selection_v3(output, burn_in=1)

    assert len(sequences) == 18
    assert all(len(sequence.steps) == 2 for sequence in sequences)
    assert all(step.episode_start for sequence in sequences for step in sequence.steps[:1])
    assert all(not step.episode_start for sequence in sequences for step in sequence.steps[1:])


def test_stream_recurrent_selection_is_external_sha_anchored_and_partition_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The production stream cannot fall back to the eager fixture materializer."""
    root = _write_full_corpus_root(tmp_path, paired_episodes=True)
    output = tmp_path / "selection.json"
    build_recurrent_selection_manifest_v3(
        root, lane="alakazam", qualification_time_utc=_QUALIFICATION_TIME, output_path=output,
    )
    expected_sha = hashlib.sha256(output.read_bytes()).hexdigest()
    monkeypatch.setattr(
        recurrent_dataset, "materialize_recurrent_selection_v3",
        lambda *_args, **_kwargs: pytest.fail("stream must not eagerly materialize the corpus"),
    )

    stream = stream_recurrent_selection_v3(
        output, expected_manifest_file_sha256=expected_sha, burn_in=1, partition="train",
    )
    assert iter(stream) is stream
    first = next(stream)
    assert first.partition == "train"
    assert len(first.steps) == 2
    assert all(sequence.partition == "train" for sequence in stream)


def test_stream_recurrent_selection_rejects_manifest_anchor_index_and_raw_line_tamper(
    tmp_path: Path,
) -> None:
    """All authority failures occur before the stream may yield a sequence."""
    root = _write_full_corpus_root(tmp_path)
    output = tmp_path / "selection.json"
    manifest = build_recurrent_selection_manifest_v3(
        root, lane="alakazam", qualification_time_utc=_QUALIFICATION_TIME, output_path=output,
    )
    expected_sha = hashlib.sha256(output.read_bytes()).hexdigest()
    rehashed = dict(manifest)
    rehashed["lane"] = "tampered"
    rehashed["manifest_sha256"] = recurrent_dataset._hash(
        {key: value for key, value in rehashed.items() if key != "manifest_sha256"},
    )
    output.write_bytes(canonical_json_bytes_v2(rehashed))

    with pytest.raises(ValueError, match="external.*SHA|manifest.*SHA"):
        next(stream_recurrent_selection_v3(
            output, expected_manifest_file_sha256=expected_sha, burn_in=0, partition="train",
        ))

    expected_sha = hashlib.sha256(output.read_bytes()).hexdigest()
    index = tmp_path / manifest["selection_index_path"]
    index.write_bytes(index.read_bytes().replace(b'"partition":"train"', b'"partition":"validation"', 1))
    with pytest.raises(ValueError, match="index.*SHA|SHA.*index"):
        next(stream_recurrent_selection_v3(
            output, expected_manifest_file_sha256=expected_sha, burn_in=0, partition="train",
        ))

    build_recurrent_selection_manifest_v3(
        root, lane="alakazam", qualification_time_utc=_QUALIFICATION_TIME, output_path=output,
    )
    expected_sha = hashlib.sha256(output.read_bytes()).hexdigest()
    shard = root / "dataset-0000.jsonl"
    shard.write_bytes(shard.read_bytes().replace(b'"fixture"', b'"changed"', 1))
    with pytest.raises(ValueError, match="snapshot|raw line|reproduc"):
        next(stream_recurrent_selection_v3(
            output, expected_manifest_file_sha256=expected_sha, burn_in=0, partition="train",
        ))


def test_stream_uses_reconstructed_index_when_original_is_swapped_after_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A post-verify index swap cannot turn a validation component into train data."""
    root = _write_full_corpus_root(tmp_path)
    output = tmp_path / "selection.json"
    manifest = build_recurrent_selection_manifest_v3(
        root, lane="alakazam", qualification_time_utc=_QUALIFICATION_TIME, output_path=output,
    )
    index = tmp_path / manifest["selection_index_path"]
    original_files_equal = recurrent_dataset._files_equal

    def swap_original_after_byte_comparison(left: Path, right: Path) -> bool:
        assert right == index
        assert original_files_equal(left, right)
        entries = list(recurrent_dataset._read_index(index))
        changed = False
        for entry in entries:
            if entry["partition"] == "validation":
                entry["partition"] = "train"
                changed = True
                break
        assert changed
        index.write_bytes(b"".join(recurrent_dataset._canonical(entry) + b"\n" for entry in entries))
        return True

    monkeypatch.setattr(recurrent_dataset, "_files_equal", swap_original_after_byte_comparison)
    sequences = tuple(stream_recurrent_selection_v3(
        output, expected_manifest_file_sha256=hashlib.sha256(output.read_bytes()).hexdigest(),
        burn_in=0, partition="train",
    ))

    assert len(sequences) == manifest["split"]["counts"]["train"]
    assert all(sequence.partition == "train" for sequence in sequences)


def test_stream_keeps_reconstructed_index_until_early_close_then_cleans_scratch(tmp_path: Path) -> None:
    """The verified index survives yield, but its job-owned temporary directory does not leak."""
    root = _write_full_corpus_root(tmp_path, paired_episodes=True)
    output = tmp_path / "selection.json"
    build_recurrent_selection_manifest_v3(
        root, lane="alakazam", qualification_time_utc=_QUALIFICATION_TIME, output_path=output,
    )
    scratch_parent = recurrent_dataset._job_scratch_parent(output)
    stream = stream_recurrent_selection_v3(
        output, expected_manifest_file_sha256=hashlib.sha256(output.read_bytes()).hexdigest(),
        burn_in=0, partition="train",
    )

    assert next(stream).partition == "train"
    assert list(scratch_parent.glob("recurrent-selection-verify-*"))
    stream.close()
    assert not list(scratch_parent.glob("recurrent-selection-verify-*"))


def test_stream_recurrent_selection_rejects_invalid_partition_before_reading(tmp_path: Path) -> None:
    root = _write_full_corpus_root(tmp_path)
    output = tmp_path / "selection.json"
    build_recurrent_selection_manifest_v3(
        root, lane="alakazam", qualification_time_utc=_QUALIFICATION_TIME, output_path=output,
    )

    with pytest.raises(ValueError, match="partition"):
        next(stream_recurrent_selection_v3(
            output, expected_manifest_file_sha256=hashlib.sha256(output.read_bytes()).hexdigest(),
            burn_in=0, partition="all",
        ))


def test_preflight_freezes_reproduced_index_and_prepared_pass_never_recompiles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Component/split proof runs once; every pass still streams raw lockstep rows."""
    root = _write_full_corpus_root(tmp_path, paired_episodes=True)
    manifest_path = tmp_path / "selection.json"
    build_recurrent_selection_manifest_v3(
        root, lane="alakazam", qualification_time_utc=_QUALIFICATION_TIME, output_path=manifest_path,
    )
    prepared = prepare_sealed_recurrent_lane_v3(
        manifest_path, expected_manifest_file_sha256=hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        output_dir=tmp_path / "prepared", command_identity="fixture-command-v1",
    )
    receipt = recurrent_dataset._read_preflight_receipt_v3(prepared.receipt_path)
    assert receipt["frozen_index_sha256"] == receipt["original_index_sha256"]
    assert receipt["schema"] == "meta-specialist-recurrent-lane-preflight-v3"
    assert receipt["r3_projection"]["records_checked"] == receipt["records_total"]
    assert receipt["r3_projection"]["steps_checked"] >= receipt["records_total"]
    assert (prepared.receipt_path.parent / receipt["frozen_index_path"]).is_file()
    monkeypatch.setattr(
        recurrent_dataset, "_compile_selection_index",
        lambda *_args, **_kwargs: pytest.fail("prepared pass must not rebuild split/components"),
    )
    monkeypatch.setattr(
        recurrent_dataset, "_copy_closed_snapshot_to_scratch_v3",
        lambda *_args, **_kwargs: pytest.fail("prepared pass must not copy the sealed corpus"),
    )

    first = tuple(stream_prepared_recurrent_selection_v3(
        prepared.receipt_path, expected_receipt_file_sha256=prepared.expected_receipt_file_sha256,
        burn_in=1, partition="train",
    ))
    second = tuple(stream_prepared_recurrent_selection_v3(
        prepared.receipt_path, expected_receipt_file_sha256=prepared.expected_receipt_file_sha256,
        burn_in=1, partition="train",
    ))

    assert first and len(first) == len(second)
    assert all(sequence.partition == "train" and len(sequence.steps) == 2 for sequence in first)


def test_preflight_rejects_every_r3_projection_failure_before_writing_a_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fails if preflight can attest an index whose R3 projection later crashes."""
    root = _write_full_corpus_root(tmp_path)
    manifest_path = tmp_path / "selection.json"
    build_recurrent_selection_manifest_v3(
        root, lane="alakazam", qualification_time_utc=_QUALIFICATION_TIME,
        output_path=manifest_path,
    )
    original = recurrent_dataset._recurrent_steps_for_record
    calls: list[str] = []
    failing_ids: list[str] = []

    def reject_two_physical_records(record, **kwargs):
        record_id = record["record_id"]
        calls.append(record_id)
        if len(failing_ids) < 2:
            failing_ids.append(record_id)
            raise ValueError("ambiguous_public_locator")
        return original(record, **kwargs)

    monkeypatch.setattr(recurrent_dataset, "_recurrent_steps_for_record", reject_two_physical_records)

    with pytest.raises(ValueError, match="R3 projection rejected") as raised:
        prepare_sealed_recurrent_lane_v3(
            manifest_path,
            expected_manifest_file_sha256=hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
            output_dir=tmp_path / "prepared", command_identity="fixture-command-v1",
        )

    assert len(calls) == 36
    assert '"reason":"ValueError:ambiguous_public_locator"' in str(raised.value)
    assert '"count":2' in str(raised.value)
    assert failing_ids[0] in str(raised.value)
    assert not (tmp_path / "prepared" / "recurrent-lane-preflight-v3.json").exists()
    assert not (tmp_path / "prepared" / "sealed-run-index.jsonl").exists()
    assert not (tmp_path / "prepared" / "sealed-snapshot").exists()


def test_projection_preflight_closes_tracker_when_episode_advance_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The preflight verifier must release its disk tracker on an unexpected projection error."""
    root = _write_full_corpus_root(tmp_path)
    manifest_path = tmp_path / "selection.json"
    manifest = build_recurrent_selection_manifest_v3(
        root, lane="alakazam", qualification_time_utc=_QUALIFICATION_TIME,
        output_path=manifest_path,
    )
    payload = read_recurrent_selection_manifest_v3(manifest_path)
    authority = recurrent_dataset._assert_manifest_authorities(payload)
    trackers: list[object] = []

    class FailingTracker:
        def __init__(self, *_args: object) -> None:
            self.close_calls = 0
            trackers.append(self)

        def advance(self, _episode: str) -> bool:
            raise RuntimeError("forced projection tracker failure")

        def close(self) -> None:
            self.close_calls += 1

    monkeypatch.setattr(recurrent_dataset, "_PhysicalEpisodeTrackerV3", FailingTracker)

    with pytest.raises(RuntimeError, match="forced projection tracker failure"):
        recurrent_dataset._validate_r3_projection_preflight_v3(
            payload, root=authority[0], snapshot=authority[1], permission=authority[2],
            trusted=authority[3], vocabulary=authority[4],
            index=tmp_path / manifest["selection_index_path"],
        )

    assert len(trackers) == 1
    assert trackers[0].close_calls == 1


def test_legacy_v1_preflight_receipt_is_diagnostic_only_and_cannot_stream(
    tmp_path: Path,
) -> None:
    """Fails if an old receipt can bypass the newly required projection evidence."""
    root = _write_full_corpus_root(tmp_path)
    manifest_path = tmp_path / "selection.json"
    build_recurrent_selection_manifest_v3(
        root, lane="alakazam", qualification_time_utc=_QUALIFICATION_TIME,
        output_path=manifest_path,
    )
    prepared = prepare_sealed_recurrent_lane_v3(
        manifest_path,
        expected_manifest_file_sha256=hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        output_dir=tmp_path / "prepared", command_identity="fixture-command-v1",
    )
    legacy = recurrent_dataset._read_preflight_receipt_v3(prepared.receipt_path)
    legacy.pop("r3_projection")
    legacy.pop("frozen_snapshot_path")
    legacy["schema"] = "meta-specialist-recurrent-lane-preflight-v1"
    legacy["receipt_sha256"] = recurrent_dataset._hash(
        {key: value for key, value in legacy.items() if key != "receipt_sha256"},
    )
    legacy_path = tmp_path / "prepared" / "legacy-v1.json"
    legacy_path.write_bytes(canonical_json_bytes_v2(legacy))
    expected_sha = hashlib.sha256(legacy_path.read_bytes()).hexdigest()

    assert recurrent_dataset._read_preflight_receipt_v3(legacy_path) == legacy
    with pytest.raises(ValueError, match="sealed-snapshot v3 receipt"):
        next(stream_prepared_recurrent_selection_v3(
            legacy_path, expected_receipt_file_sha256=expected_sha,
            burn_in=0, partition="train",
        ))


def test_prepared_stream_rejects_projection_aggregate_drift_before_yield(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fails if a receipt can be reused after the projector changes its step sequence."""
    root = _write_full_corpus_root(tmp_path)
    manifest_path = tmp_path / "selection.json"
    build_recurrent_selection_manifest_v3(
        root, lane="alakazam", qualification_time_utc=_QUALIFICATION_TIME,
        output_path=manifest_path,
    )
    prepared = prepare_sealed_recurrent_lane_v3(
        manifest_path,
        expected_manifest_file_sha256=hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        output_dir=tmp_path / "prepared", command_identity="fixture-command-v1",
    )
    original = recurrent_dataset._recurrent_steps_for_record

    def duplicate_each_projected_step(record, **kwargs):
        steps = original(record, **kwargs)
        return [
            step
            for original_step in steps
            for step in (original_step, replace(original_step, episode_start=False))
        ]

    monkeypatch.setattr(
        recurrent_dataset, "_recurrent_steps_for_record", duplicate_each_projected_step,
    )
    stream = stream_prepared_recurrent_selection_v3(
        prepared.receipt_path,
        expected_receipt_file_sha256=prepared.expected_receipt_file_sha256,
        burn_in=0, partition="train",
    )

    with pytest.raises(ValueError, match="projection.*receipt|receipt.*projection|aggregate"):
        next(stream)


def test_failed_preflight_rerun_retires_the_previous_public_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fails if a rejected rerun leaves the previous receipt at its public entry path."""
    root = _write_full_corpus_root(tmp_path)
    manifest_path = tmp_path / "selection.json"
    build_recurrent_selection_manifest_v3(
        root, lane="alakazam", qualification_time_utc=_QUALIFICATION_TIME,
        output_path=manifest_path,
    )
    output_dir = tmp_path / "prepared"
    prepared = prepare_sealed_recurrent_lane_v3(
        manifest_path,
        expected_manifest_file_sha256=hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        output_dir=output_dir, command_identity="fixture-command-v1",
    )
    receipt = recurrent_dataset._read_preflight_receipt_v3(prepared.receipt_path)
    frozen = output_dir / receipt["frozen_index_path"]
    old_receipt_bytes = prepared.receipt_path.read_bytes()
    old_frozen_bytes = frozen.read_bytes()
    monkeypatch.setattr(
        recurrent_dataset, "_recurrent_steps_for_record",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("ambiguous_public_locator")),
    )

    with pytest.raises(ValueError, match="R3 projection rejected"):
        prepare_sealed_recurrent_lane_v3(
            manifest_path,
            expected_manifest_file_sha256=hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
            output_dir=output_dir, command_identity="fixture-command-v2",
        )

    assert not prepared.receipt_path.exists()
    assert not frozen.exists()
    retired = list((output_dir / ".retired-preflights").glob("retired-*"))
    assert len(retired) == 1
    assert (retired[0] / prepared.receipt_path.name).read_bytes() == old_receipt_bytes
    assert (retired[0] / frozen.name).read_bytes() == old_frozen_bytes
    assert (retired[0] / "sealed-snapshot").is_dir()
    assert not (output_dir / "sealed-snapshot").exists()


def test_prepared_stream_rejects_receipt_rehash_frozen_index_and_raw_snapshot_mutation(tmp_path: Path) -> None:
    """The external receipt anchor, frozen descriptor, and physical shard stay bound per pass."""
    root = _write_full_corpus_root(tmp_path)
    manifest_path = tmp_path / "selection.json"
    build_recurrent_selection_manifest_v3(
        root, lane="alakazam", qualification_time_utc=_QUALIFICATION_TIME, output_path=manifest_path,
    )
    prepared = prepare_sealed_recurrent_lane_v3(
        manifest_path, expected_manifest_file_sha256=hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        output_dir=tmp_path / "prepared", command_identity="fixture-command-v1",
    )
    receipt_path = prepared.receipt_path
    receipt = recurrent_dataset._read_preflight_receipt_v3(receipt_path)
    rehashed = dict(receipt)
    rehashed["command_identity"] = "self-rehashed-tamper"
    rehashed["receipt_sha256"] = recurrent_dataset._hash(
        {key: value for key, value in rehashed.items() if key != "receipt_sha256"},
    )
    receipt_path.write_bytes(canonical_json_bytes_v2(rehashed))
    with pytest.raises(ValueError, match="receipt external.*SHA|receipt.*file SHA"):
        next(stream_prepared_recurrent_selection_v3(
            receipt_path, expected_receipt_file_sha256=prepared.expected_receipt_file_sha256,
            burn_in=0, partition="train",
        ))

    prepared = prepare_sealed_recurrent_lane_v3(
        manifest_path, expected_manifest_file_sha256=hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        output_dir=tmp_path / "prepared", command_identity="fixture-command-v1",
    )
    receipt = recurrent_dataset._read_preflight_receipt_v3(prepared.receipt_path)
    frozen = prepared.receipt_path.parent / receipt["frozen_index_path"]
    frozen.write_bytes(frozen.read_bytes().replace(b'"partition":"train"', b'"partition":"validation"', 1))
    with pytest.raises(ValueError, match="frozen index.*SHA|index.*SHA"):
        next(stream_prepared_recurrent_selection_v3(
            prepared.receipt_path, expected_receipt_file_sha256=prepared.expected_receipt_file_sha256,
            burn_in=0, partition="train",
        ))

    prepared = prepare_sealed_recurrent_lane_v3(
        manifest_path, expected_manifest_file_sha256=hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        output_dir=tmp_path / "prepared", command_identity="fixture-command-v1",
    )
    receipt = recurrent_dataset._read_preflight_receipt_v3(prepared.receipt_path)
    shard = prepared.receipt_path.parent / receipt["frozen_snapshot_path"] / "dataset-0000.jsonl"
    shard.write_bytes(shard.read_bytes().replace(b'"fixture"', b'"changed"', 1))
    with pytest.raises(ValueError, match="snapshot|shard|raw line"):
        next(stream_prepared_recurrent_selection_v3(
            prepared.receipt_path, expected_receipt_file_sha256=prepared.expected_receipt_file_sha256,
            burn_in=0, partition="train",
        ))


def test_prepared_stream_rejects_a_frozen_shard_path_replacement_between_projection_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A replacement after aggregate scan cannot feed different bytes to yielded sequences."""
    root = _write_full_corpus_root(tmp_path, paired_episodes=True)
    manifest_path = tmp_path / "selection.json"
    build_recurrent_selection_manifest_v3(
        root, lane="alakazam", qualification_time_utc=_QUALIFICATION_TIME, output_path=manifest_path,
    )
    prepared = prepare_sealed_recurrent_lane_v3(
        manifest_path, expected_manifest_file_sha256=hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        output_dir=tmp_path / "prepared", command_identity="fixture-command-v1",
    )
    original_read = recurrent_dataset._regular_file_bytes_v3
    receipt = recurrent_dataset._read_preflight_receipt_v3(prepared.receipt_path)
    changed_receipt = dict(receipt)
    changed_receipt["command_identity"] = "replacement-receipt"
    changed_receipt["receipt_sha256"] = recurrent_dataset._hash(
        {key: value for key, value in changed_receipt.items() if key != "receipt_sha256"},
    )

    def read_then_replace(path: Path, *, expected_sha256, name):
        raw = original_read(path, expected_sha256=expected_sha256, name=name)
        if path == prepared.receipt_path:
            path.write_bytes(canonical_json_bytes_v2(changed_receipt))
        return raw

    monkeypatch.setattr(recurrent_dataset, "_regular_file_bytes_v3", read_then_replace)
    original_open = recurrent_dataset.os.open
    shard = prepared.receipt_path.parent / receipt["frozen_snapshot_path"] / "dataset-0000.jsonl"
    replacement = shard.read_bytes().replace(b'"fixture"', b'"changed"', 1)
    replacement_path = tmp_path / "replacement-shard.jsonl"
    replacement_path.write_bytes(replacement)
    swapped = False

    def open_then_replace(path, flags, *args, **kwargs):
        nonlocal swapped
        descriptor = original_open(path, flags, *args, **kwargs)
        if Path(path) == shard and not swapped:
            os.replace(replacement_path, shard)
            swapped = True
        return descriptor

    monkeypatch.setattr(recurrent_dataset.os, "open", open_then_replace)
    with pytest.raises(ValueError, match="snapshot shard physical SHA-256"):
        tuple(stream_prepared_recurrent_selection_v3(
            prepared.receipt_path, expected_receipt_file_sha256=prepared.expected_receipt_file_sha256,
            burn_in=0, partition="train",
        ))

    assert swapped


def test_frozen_index_uses_a_private_spool_after_sha_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A same-inode rewrite after hashing must not alter parsed frozen-index rows."""
    root = _write_full_corpus_root(tmp_path, paired_episodes=True)
    manifest_path = tmp_path / "selection.json"
    manifest = build_recurrent_selection_manifest_v3(
        root, lane="alakazam", qualification_time_utc=_QUALIFICATION_TIME,
        output_path=manifest_path,
    )
    index = tmp_path / manifest["selection_index_path"]
    original = index.read_bytes()
    if b'"partition":"train"' in original:
        changed = original.replace(b'"partition":"train"', b'"partition":"valid"', 1)
    else:
        changed = original.replace(b'"partition":"validation"', b'"partition":"validatioN"', 1)
    assert changed != original and len(changed) == len(original)
    before = index.stat()
    real_fdopen = recurrent_dataset.os.fdopen

    class MutatingReader:
        def __init__(self, handle):
            self._handle = handle
            self._mutated = False

        def __enter__(self):
            self._handle.__enter__()
            return self

        def __exit__(self, *args):
            return self._handle.__exit__(*args)

        def fileno(self):
            return self._handle.fileno()

        def read(self, *args):
            return self._handle.read(*args)

        def seek(self, *args):
            if not self._mutated:
                index.write_bytes(changed)
                os.utime(index, ns=(before.st_atime_ns, before.st_mtime_ns))
                self._mutated = True
            return self._handle.seek(*args)

        def __iter__(self):
            return self

        def __next__(self):
            return next(self._handle)

    monkeypatch.setattr(
        recurrent_dataset.os, "fdopen",
        lambda *args, **kwargs: MutatingReader(real_fdopen(*args, **kwargs)),
    )

    rows = list(recurrent_dataset._frozen_index_entries_v3(
        index, expected_sha=hashlib.sha256(original).hexdigest(),
    ))

    assert rows == list(recurrent_dataset._read_index_handle(io.BytesIO(original)))
    assert index.read_bytes() == original


@pytest.mark.parametrize("symlink_level", ["output-parent", "snapshot-root", "snapshot-child"])
def test_prepared_stream_rejects_symlinks_in_its_frozen_snapshot_path(
    tmp_path: Path, symlink_level: str,
) -> None:
    """Fails if resolve() erases a symlink before the production boundary checks it."""
    root = _write_full_corpus_root(tmp_path, paired_episodes=True)
    manifest_path = tmp_path / "selection.json"
    build_recurrent_selection_manifest_v3(
        root, lane="alakazam", qualification_time_utc=_QUALIFICATION_TIME,
        output_path=manifest_path,
    )
    output_dir = tmp_path / "prepared"
    prepared = prepare_sealed_recurrent_lane_v3(
        manifest_path,
        expected_manifest_file_sha256=hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        output_dir=output_dir, command_identity="fixture-command-v1",
    )
    receipt = recurrent_dataset._read_preflight_receipt_v3(prepared.receipt_path)
    snapshot = output_dir / receipt["frozen_snapshot_path"]

    if symlink_level == "output-parent":
        real = tmp_path / "prepared-real"
        output_dir.rename(real)
        output_dir.symlink_to(real, target_is_directory=True)
    elif symlink_level == "snapshot-root":
        real = output_dir / "sealed-snapshot-real"
        snapshot.rename(real)
        snapshot.symlink_to(real, target_is_directory=True)
    else:
        shard = snapshot / "dataset-0000.jsonl"
        real = snapshot / "dataset-0000-real.jsonl"
        shard.rename(real)
        shard.symlink_to(real)

    with pytest.raises(ValueError, match="symlink|snapshot authority"):
        next(stream_prepared_recurrent_selection_v3(
            prepared.receipt_path,
            expected_receipt_file_sha256=prepared.expected_receipt_file_sha256,
            burn_in=0, partition="train",
        ))


def test_verified_descriptor_accepts_a_read_side_effect_atime_change(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """atime is mutable read metadata, not evidence that the opened object was replaced."""
    target = tmp_path / "authority.json"
    target.write_bytes(b"fixture")
    original_fstat = recurrent_dataset.os.fstat
    calls = 0

    def fstat_with_atime_change(descriptor):
        nonlocal calls
        calls += 1
        value = original_fstat(descriptor)
        if calls == 2:
            return SimpleNamespace(
                st_dev=value.st_dev, st_ino=value.st_ino, st_mode=value.st_mode,
                st_size=value.st_size, st_mtime_ns=value.st_mtime_ns,
                st_ctime_ns=value.st_ctime_ns, st_atime_ns=value.st_atime_ns + 1,
            )
        return value

    monkeypatch.setattr(recurrent_dataset.os, "fstat", fstat_with_atime_change)
    assert recurrent_dataset._regular_file_bytes_v3(
        target, expected_sha256=hashlib.sha256(b"fixture").hexdigest(), name="fixture authority",
    ) == b"fixture"


def test_verified_descriptor_requires_o_nofollow_before_open(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Platforms without O_NOFOLLOW must block instead of following a possible symlink."""
    target = tmp_path / "authority.json"
    target.write_bytes(b"fixture")
    monkeypatch.delattr(recurrent_dataset.os, "O_NOFOLLOW", raising=False)
    monkeypatch.setattr(
        recurrent_dataset.os, "open",
        lambda *_args, **_kwargs: pytest.fail("must fail before attempting an unprotected open"),
    )

    with pytest.raises(ValueError, match="O_NOFOLLOW"):
        recurrent_dataset._regular_file_bytes_v3(
            target, expected_sha256=hashlib.sha256(b"fixture").hexdigest(), name="fixture authority",
        )


def test_disk_backed_episode_tracker_rejects_a_to_b_to_a_without_a_python_set(
    tmp_path: Path,
) -> None:
    tracker = recurrent_dataset._PhysicalEpisodeTrackerV3(tmp_path)
    try:
        assert tracker.advance("episode-a") is True
        assert tracker.advance("episode-a") is False
        assert tracker.advance("episode-b") is True
        with pytest.raises(ValueError, match="reopens an episode"):
            tracker.advance("episode-a")
        assert tracker.database_path.is_file()
    finally:
        tracker.close()


def test_materializer_rejects_a_reopened_physical_episode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _write_full_corpus_root(tmp_path)
    output = tmp_path / "selection.json"
    manifest = build_recurrent_selection_manifest_v3(
        root, lane="alakazam", qualification_time_utc=_QUALIFICATION_TIME, output_path=output,
    )
    payload = read_recurrent_selection_manifest_v3(output)
    authority = recurrent_dataset._assert_manifest_authorities(payload)
    qualified = list(recurrent_dataset._iter_requalified_records(
        authority[0], snapshot=authority[1], permission=authority[2], trusted=authority[3],
        qualification_time_utc=_QUALIFICATION_TIME, vocabulary=authority[4],
    ))
    index_entries = list(recurrent_dataset._read_index(tmp_path / manifest["selection_index_path"]))
    reopened_records = (qualified[0], qualified[1], qualified[0])
    reopened_entries = (index_entries[0], index_entries[1], index_entries[0])
    monkeypatch.setattr(
        recurrent_dataset, "_iter_requalified_records",
        lambda *_args, **_kwargs: iter(reopened_records),
    )
    monkeypatch.setattr(
        recurrent_dataset, "read_recurrent_selection_manifest_v3", lambda *_args, **_kwargs: payload,
    )
    monkeypatch.setattr(
        recurrent_dataset, "_read_index", lambda *_args, **_kwargs: iter(reopened_entries),
    )

    with pytest.raises(ValueError, match="reopens an episode"):
        materialize_recurrent_selection_v3(output, burn_in=0)


def test_full_corpus_rejects_a_to_b_to_a_episode_reappearance(tmp_path: Path) -> None:
    root = _write_full_corpus_root(tmp_path, reopened_episode=True)

    with pytest.raises(ValueError, match="reopens an episode"):
        build_recurrent_selection_manifest_v3(
            root, lane="alakazam", qualification_time_utc=_QUALIFICATION_TIME,
            output_path=tmp_path / "selection.json",
        )
