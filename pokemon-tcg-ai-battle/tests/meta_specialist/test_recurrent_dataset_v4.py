"""V4 recurrent source must bind representation identity and READY quality evidence."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from mage_ptcg.meta_specialist import recurrent_dataset_v4 as recurrent_v4
from mage_ptcg.meta_specialist.recurrent_dataset_v3 import (
    build_recurrent_selection_manifest_v3,
    stream_recurrent_record_authority_v3,
    verify_recurrent_record_authority_v3,
)
from mage_ptcg.meta_specialist.recurrent_dataset_v4 import (
    RecurrentBCStepV4,
    _join_quality_overlay_v4,
    prepare_sealed_recurrent_lane_v4,
    stream_prepared_recurrent_selection_v4,
)
from mage_ptcg.meta_specialist.representation_v4 import RelationalStateV4
from mage_ptcg.meta_specialist.teacher_quality_v2 import TeacherQualityOverlayRowV2
from tests.meta_specialist.test_recurrent_dataset_v3 import (
    _QUALIFICATION_TIME,
    _write_full_corpus_root,
)


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_generic_record_authority_rebuilds_once_then_streams_physical_identity(
    tmp_path: Path,
) -> None:
    """V4 must not reach through v3's representation-specific receipt/projection."""
    root = _write_full_corpus_root(tmp_path, paired_episodes=True)
    manifest_path = tmp_path / "selection.json"
    build_recurrent_selection_manifest_v3(
        root, lane="alakazam", qualification_time_utc=_QUALIFICATION_TIME,
        output_path=manifest_path,
    )

    authority = verify_recurrent_record_authority_v3(
        manifest_path, expected_manifest_file_sha256=_file_sha(manifest_path),
    )
    rows = stream_recurrent_record_authority_v3(
        manifest_path,
        expected_manifest_file_sha256=_file_sha(manifest_path),
        expected_manifest_sha256=authority.manifest_sha256,
        expected_selection_index_sha256=authority.selection_index_sha256,
        expected_records_total=authority.records_total, expected_split=authority.split,
        expected_chunks=authority.chunks,
    )

    first = next(rows)
    assert first.record_id == first.record["record_id"]
    assert first.content_hash == first.record["content_hash"]
    assert first.partition in {"train", "validation"}
    assert first.component_id
    assert 1 + sum(1 for _ in rows) == authority.records_total == 36


def test_v4_step_rejects_default_or_zero_weight_and_keeps_record_identity() -> None:
    state = RelationalStateV4((0.0,), (), ())
    kwargs = dict(
        state=state, target_index=0, episode_group="episode-a", model_input=object(),
        step_input=object(), target_masses=(1.0,), reach_mass=1.0, episode_start=True,
        component_id="component-a", partition="train", record_id="a" * 64,
        content_hash="b" * 64,
    )

    step = RecurrentBCStepV4(quality_weight=0.7, **kwargs)

    assert step.record_id == "a" * 64
    assert step.content_hash == "b" * 64
    with pytest.raises(TypeError, match="reach_mass"):
        RecurrentBCStepV4(quality_weight=0.7, **{key: value for key, value in kwargs.items() if key != "reach_mass"})
    with pytest.raises(ValueError, match="quality_weight"):
        RecurrentBCStepV4(quality_weight=0.0, **kwargs)
    with pytest.raises(ValueError, match="default.*1.0"):
        RecurrentBCStepV4(quality_weight=1.0, **kwargs)
    with pytest.raises(ValueError, match="reach_mass"):
        RecurrentBCStepV4(quality_weight=0.7, **(kwargs | {"reach_mass": 0.0}))


def _overlay(record_id: str, content_hash: str, weight: float = 0.7) -> TeacherQualityOverlayRowV2:
    return TeacherQualityOverlayRowV2(
        record_id, content_hash, "teacher-a", "c" * 64, "d" * 64, weight, None,
    )


@pytest.mark.parametrize("mutation,match", [
    ("missing", "missing"), ("extra", "extra"), ("mismatch", "content_hash"),
    ("duplicate", "duplicate"), ("zero", "positive"), ("default", "default.*1.0"),
])
def test_quality_join_rejects_nonexact_or_unauthorized_overlay(
    tmp_path: Path, mutation: str, match: str,
) -> None:
    source = tmp_path / "source.tsv"
    source.write_text(f"0\t{'a' * 64}\t{'b' * 64}\n", encoding="ascii")
    rows = [_overlay("a" * 64, "b" * 64)]
    if mutation == "missing":
        rows = []
    elif mutation == "extra":
        rows.append(_overlay("f" * 64, "e" * 64))
    elif mutation == "mismatch":
        rows = [_overlay("a" * 64, "e" * 64)]
    elif mutation == "duplicate":
        rows.append(rows[0])
    elif mutation == "zero":
        rows = [_overlay("a" * 64, "b" * 64, 0.0)]
    elif mutation == "default":
        rows = [_overlay("a" * 64, "b" * 64, 1.0)]

    with pytest.raises(ValueError, match=match):
        _join_quality_overlay_v4(
            source, iter(rows), destination=tmp_path / "quality.jsonl",
            scratch=tmp_path,
        )


def test_v4_preflight_and_stream_keep_identity_quality_and_episode_boundaries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _write_full_corpus_root(tmp_path, paired_episodes=True)
    selection = tmp_path / "selection.json"
    build_recurrent_selection_manifest_v3(
        root, lane="alakazam", qualification_time_utc=_QUALIFICATION_TIME,
        output_path=selection,
    )
    selection_sha = _file_sha(selection)
    authority = verify_recurrent_record_authority_v3(
        selection, expected_manifest_file_sha256=selection_sha,
    )
    physical = list(stream_recurrent_record_authority_v3(
        selection, expected_manifest_file_sha256=selection_sha,
        expected_manifest_sha256=authority.manifest_sha256,
        expected_selection_index_sha256=authority.selection_index_sha256,
        expected_records_total=authority.records_total, expected_split=authority.split,
        expected_chunks=authority.chunks,
    ))
    overlays = sorted(
        (_overlay(row.record_id, row.content_hash) for row in physical),
        key=lambda row: row.record_id,
    )
    teacher_path = tmp_path / "teacher-quality-manifest-v2.json"
    teacher_path.write_bytes(b"{}")
    teacher_file_sha = _file_sha(teacher_path)
    teacher_self_sha = "e" * 64
    teacher = {
        "status": "READY", "theta0_allowed": True, "authority_gap": None,
        "manifest_sha256": teacher_self_sha, "row_count": len(overlays),
        "weight_histogram": {"0.7": len(overlays)},
        "overlay": {"basename": "overlay.jsonl", "file_sha256": "f" * 64, "row_count": len(overlays)},
    }
    monkeypatch.setattr(recurrent_v4, "read_teacher_quality_manifest_v2", lambda *_args, **_kwargs: teacher)
    monkeypatch.setattr(
        recurrent_v4, "stream_ready_teacher_quality_overlay_v2",
        lambda *_args, **_kwargs: iter(overlays),
    )

    prepared = prepare_sealed_recurrent_lane_v4(
        selection, expected_selection_manifest_file_sha256=selection_sha,
        teacher_quality_manifest_path=teacher_path,
        expected_teacher_quality_manifest_file_sha256=teacher_file_sha,
        expected_teacher_quality_manifest_sha256=teacher_self_sha,
        output_dir=tmp_path / "prepared", command_identity="fixture-command",
    )
    sequences = list(stream_prepared_recurrent_selection_v4(
        prepared.receipt_path,
        expected_receipt_file_sha256=prepared.expected_receipt_file_sha256,
        burn_in=1, partition="train",
    ))

    assert sequences
    assert all(sequence.burn_in == 1 and sequence.partition == "train" for sequence in sequences)
    assert all(step.quality_weight == 0.7 for sequence in sequences for step in sequence.steps)
    assert all(step.record_id and step.content_hash for sequence in sequences for step in sequence.steps)
    assert all(sequence.steps[0].episode_start for sequence in sequences)


def _prepare_fixture_v4(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    root = _write_full_corpus_root(tmp_path, paired_episodes=True)
    selection = tmp_path / "selection.json"
    build_recurrent_selection_manifest_v3(
        root, lane="alakazam", qualification_time_utc=_QUALIFICATION_TIME,
        output_path=selection,
    )
    selection_sha = _file_sha(selection)
    authority = verify_recurrent_record_authority_v3(
        selection, expected_manifest_file_sha256=selection_sha,
    )
    physical = list(stream_recurrent_record_authority_v3(
        selection, expected_manifest_file_sha256=selection_sha,
        expected_manifest_sha256=authority.manifest_sha256,
        expected_selection_index_sha256=authority.selection_index_sha256,
        expected_records_total=authority.records_total, expected_split=authority.split,
        expected_chunks=authority.chunks,
    ))
    overlays = sorted(
        (_overlay(row.record_id, row.content_hash) for row in physical),
        key=lambda row: row.record_id,
    )
    teacher_path = tmp_path / "teacher-quality-manifest-v2.json"
    teacher_path.write_bytes(b"{}")
    teacher_file_sha = _file_sha(teacher_path)
    teacher_self_sha = "e" * 64
    teacher = {
        "status": "READY", "theta0_allowed": True, "authority_gap": None,
        "manifest_sha256": teacher_self_sha, "row_count": len(overlays),
        "weight_histogram": {"0.7": len(overlays)},
        "overlay": {"basename": "overlay.jsonl", "file_sha256": "f" * 64, "row_count": len(overlays)},
    }
    monkeypatch.setattr(recurrent_v4, "read_teacher_quality_manifest_v2", lambda *_args, **_kwargs: teacher)
    monkeypatch.setattr(
        recurrent_v4, "stream_ready_teacher_quality_overlay_v2",
        lambda *_args, **_kwargs: iter(overlays),
    )
    output = tmp_path / "prepared"
    prepared = prepare_sealed_recurrent_lane_v4(
        selection, expected_selection_manifest_file_sha256=selection_sha,
        teacher_quality_manifest_path=teacher_path,
        expected_teacher_quality_manifest_file_sha256=teacher_file_sha,
        expected_teacher_quality_manifest_sha256=teacher_self_sha,
        output_dir=output, command_identity="fixture-command",
    )
    return prepared, output, selection, selection_sha, teacher_path, teacher_file_sha, teacher_self_sha


def test_failed_rerun_retires_old_v4_receipt_before_new_preflight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared, output, selection, selection_sha, teacher_path, teacher_file_sha, teacher_self_sha = \
        _prepare_fixture_v4(tmp_path, monkeypatch)
    assert prepared.receipt_path.is_file()

    def rejected_overlay(*_args, **_kwargs):
        raise ValueError("fixture overlay rejected")

    monkeypatch.setattr(recurrent_v4, "stream_ready_teacher_quality_overlay_v2", rejected_overlay)
    with pytest.raises(ValueError, match="fixture overlay rejected"):
        prepare_sealed_recurrent_lane_v4(
            selection, expected_selection_manifest_file_sha256=selection_sha,
            teacher_quality_manifest_path=teacher_path,
            expected_teacher_quality_manifest_file_sha256=teacher_file_sha,
            expected_teacher_quality_manifest_sha256=teacher_self_sha,
            output_dir=output, command_identity="failed-rerun",
        )

    assert not prepared.receipt_path.exists()
    assert list((output / ".retired-v4").glob("retired-*/recurrent-lane-preflight-v4.json"))


def test_v4_preflight_closes_tracker_when_projection_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The v4 preflight must release its disk tracker when projection exits exceptionally."""
    _prepared, output, selection, selection_sha, teacher_path, teacher_file_sha, teacher_self_sha = \
        _prepare_fixture_v4(tmp_path, monkeypatch)
    trackers: list[object] = []

    class FailingTracker:
        def __init__(self, *_args: object) -> None:
            self.close_calls = 0
            trackers.append(self)

        def advance(self, _episode: str) -> bool:
            raise RuntimeError("forced v4 preflight tracker failure")

        def close(self) -> None:
            self.close_calls += 1

    monkeypatch.setattr(recurrent_v4, "_PhysicalEpisodeTrackerV3", FailingTracker)

    with pytest.raises(RuntimeError, match="forced v4 preflight tracker failure"):
        prepare_sealed_recurrent_lane_v4(
            selection, expected_selection_manifest_file_sha256=selection_sha,
            teacher_quality_manifest_path=teacher_path,
            expected_teacher_quality_manifest_file_sha256=teacher_file_sha,
            expected_teacher_quality_manifest_sha256=teacher_self_sha,
            output_dir=output, command_identity="forced-projection-failure",
        )

    assert len(trackers) == 1
    assert trackers[0].close_calls == 1


def test_prepared_v4_stream_closes_tracker_when_generator_closes_early(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Closing a partially consumed prepared stream must release its disk tracker once."""
    prepared, _output, *_ = _prepare_fixture_v4(tmp_path, monkeypatch)
    trackers: list[object] = []

    class TrackingEpisode:
        def __init__(self, *_args: object) -> None:
            self._current: str | None = None
            self.close_calls = 0
            trackers.append(self)

        def advance(self, episode: str) -> bool:
            started = episode != self._current
            self._current = episode
            return started

        def close(self) -> None:
            self.close_calls += 1

    monkeypatch.setattr(recurrent_v4, "_PhysicalEpisodeTrackerV3", TrackingEpisode)
    stream = stream_prepared_recurrent_selection_v4(
        prepared.receipt_path,
        expected_receipt_file_sha256=prepared.expected_receipt_file_sha256,
        burn_in=0, partition="train",
    )

    assert next(stream).partition == "train"
    stream.close()

    assert len(trackers) == 1
    assert trackers[0].close_calls == 1


def test_prepared_v4_rejects_symlink_path_and_quality_sidecar_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared, output, *_ = _prepare_fixture_v4(tmp_path, monkeypatch)
    alias = tmp_path / "prepared-alias"
    alias.symlink_to(output, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        next(stream_prepared_recurrent_selection_v4(
            alias / prepared.receipt_path.name,
            expected_receipt_file_sha256=prepared.expected_receipt_file_sha256,
            burn_in=0, partition="train",
        ))

    quality = output / "recurrent-quality-v4.jsonl"
    quality.write_bytes(quality.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="quality sidecar.*SHA|SHA-256"):
        next(stream_prepared_recurrent_selection_v4(
            prepared.receipt_path,
            expected_receipt_file_sha256=prepared.expected_receipt_file_sha256,
            burn_in=0, partition="train",
        ))


def test_quality_sidecar_uses_a_private_spool_after_sha_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A same-inode rewrite after hashing must not alter rows subsequently consumed."""
    prepared, output, *_ = _prepare_fixture_v4(tmp_path, monkeypatch)
    quality = output / "recurrent-quality-v4.jsonl"
    original = quality.read_bytes()
    changed = original.replace(b'"quality_weight":0.7', b'"quality_weight":0.8', 1)
    assert changed != original and len(changed) == len(original)
    before = quality.stat()
    real_fdopen = recurrent_v4.os.fdopen

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
                quality.write_bytes(changed)
                os.utime(quality, ns=(before.st_atime_ns, before.st_mtime_ns))
                self._mutated = True
            return self._handle.seek(*args)

        def __iter__(self):
            return self

        def __next__(self):
            return next(self._handle)

    monkeypatch.setattr(recurrent_v4.os, "fdopen", lambda *args, **kwargs: MutatingReader(real_fdopen(*args, **kwargs)))

    rows = list(recurrent_v4._quality_rows_v4(
        quality, expected_sha=hashlib.sha256(original).hexdigest(),
    ))

    assert rows[0]["quality_weight"] == 0.7
    assert quality.read_bytes() == original


def test_prepared_v4_never_accepts_a_v3_receipt(tmp_path: Path) -> None:
    legacy = tmp_path / "recurrent-lane-preflight-v3.json"
    legacy.write_bytes(b'{"schema":"meta-specialist-recurrent-lane-preflight-v3"}')

    with pytest.raises(ValueError, match="closed schema|schema"):
        next(stream_prepared_recurrent_selection_v4(
            legacy, expected_receipt_file_sha256=_file_sha(legacy),
            burn_in=0, partition="train",
        ))
