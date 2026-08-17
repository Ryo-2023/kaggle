"""Primary teacher-vs-Rule-v0 performance evidence collector (v3).

This module deliberately stops at sealed performance evidence.  It neither
assigns a teacher-quality weight nor approves a trust rule.  CABT cannot yet
attest deterministic replay, so the two policy arms use independent seed
namespaces and fixed opponent-by-seat strata rather than claiming pairing.
"""

from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass, field
import hashlib
import json
import math
import os
from pathlib import Path
import random
import secrets
import stat
import statistics
import sys
import tempfile
import time
import traceback
from typing import Callable, Mapping, Sequence

from mage_ptcg.meta_specialist.opponent_pool_v1 import (
    OpponentInstanceV1,
    build_opponent_agent_factory_v1,
    load_opponent_pool_v1,
)
from mage_ptcg.meta_specialist.teacher_quality_worker_v3 import (
    SourceSnapshotV3,
    build_teacher_quality_worker_request_v3,
    read_source_snapshot_entry_v3,
    run_teacher_quality_attempt_worker_v3,
    seal_teacher_quality_source_snapshot_v3,
    validate_snapshot_policy_imports_v3,
    verify_source_snapshot_v3,
)


CAMPAIGN_SCHEMA_V3 = "meta-specialist-teacher-quality-campaign-v3"
ATTEMPT_SCHEMA_V3 = "meta-specialist-teacher-quality-attempt-v3"
RESULT_SCHEMA_V3 = "meta-specialist-teacher-quality-result-v3"
MANIFEST_SCHEMA_V3 = "meta-specialist-teacher-quality-evidence-manifest-v3"
BOOTSTRAP_SEED_V3 = 20260809
BOOTSTRAP_REPLICATES_V3 = 20_000
_ARMS = ("teacher", "rule-v0-baseline")
_PROFILES = frozenset({"calibration", "full"})
_OUTCOMES = frozenset({"win", "loss", "draw"})
_HEX = frozenset("0123456789abcdef")
_ATTEMPT_KEYS = frozenset({
    "schema", "campaign_id", "profile", "logical_game_id", "attempt_id",
    "lane", "arm", "policy", "subject_deck", "engine", "source",
    "opponent", "seat", "repetition", "environment_seed",
    "agent_sampling_seed", "retry_index", "outcome", "fault",
    "elapsed_seconds",
})
_FAULT_KEYS = frozenset({
    "kind", "exception_class", "message", "source_exception", "exit_code",
    "traceback_sha256",
})
_WORKER_PROVENANCE_KEYS = frozenset({
    "attempt_protocol", "engine_seed_capability", "source_snapshot_file_sha256",
    "source_snapshot_tree_sha256", "worker_timeout_seconds",
})


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _require_sha(value: object, field_name: str) -> str:
    if type(value) is not str or len(value) != 64 or any(char not in _HEX for char in value):
        raise ValueError(f"{field_name} must be a lowercase SHA-256 digest")
    return value


def _require_string(value: object, field_name: str) -> str:
    if type(value) is not str or not value:
        raise ValueError(f"{field_name} must be a nonempty string")
    return value


def _file_sha(path: str | Path, *, name: str) -> str:
    source = Path(path)
    if not hasattr(os, "O_NOFOLLOW"):
        raise ValueError("teacher-quality evidence requires O_NOFOLLOW")
    try:
        descriptor = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise ValueError(f"{name} cannot be opened without following a symlink") from exc
    with os.fdopen(descriptor, "rb", closefd=True) as handle:
        before = os.fstat(handle.fileno())
        if not __import__("stat").S_ISREG(before.st_mode):
            raise ValueError(f"{name} is not a regular file")
        digest = hashlib.sha256()
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
        after = os.fstat(handle.fileno())
        identity = lambda item: (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns, item.st_ctime_ns)
        if identity(before) != identity(after):
            raise ValueError(f"{name} changed while being hashed")
    return digest.hexdigest()


def _read_regular_file_no_follow(path: str | Path, *, name: str) -> bytes:
    """Read a stable regular file through one no-follow descriptor."""
    source = Path(path)
    if not hasattr(os, "O_NOFOLLOW"):
        raise ValueError("teacher-quality evidence requires O_NOFOLLOW")
    try:
        descriptor = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise ValueError(f"{name} cannot be opened without following a symlink") from exc
    with os.fdopen(descriptor, "rb", closefd=True) as handle:
        before = os.fstat(handle.fileno())
        if not __import__("stat").S_ISREG(before.st_mode):
            raise ValueError(f"{name} is not a regular file")
        raw = handle.read()
        after = os.fstat(handle.fileno())
        identity = lambda item: (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns, item.st_ctime_ns)
        if identity(before) != identity(after):
            raise ValueError(f"{name} changed while being read")
    return raw


def _read_verified_file(path: str | Path, expected: str, *, name: str) -> bytes:
    _require_sha(expected, f"expected {name}")
    raw = _read_regular_file_no_follow(path, name=name)
    if _sha(raw) != expected:
        raise ValueError(f"live {name} SHA-256 does not match the frozen campaign")
    return raw


def _verify_file(path: str | Path, expected: str, *, name: str) -> None:
    _read_verified_file(path, expected, name=name)


def _atomic_write(path: Path, raw: bytes) -> None:
    """Write private transient request files (evidence output uses FD authority)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    finally:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass


def _directory_identity_v3(value: os.stat_result) -> tuple[int, int, int]:
    # A valid collector changes the root's mtime/ctime as it publishes its
    # ledger.  Device/inode/mode still detect path replacement or type drift.
    return value.st_dev, value.st_ino, value.st_mode


def _regular_identity_v3(value: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return value.st_dev, value.st_ino, value.st_mode, value.st_size, value.st_mtime_ns, value.st_ctime_ns


def _open_directory_path_no_follow_v3(path: Path, *, name: str) -> int:
    """Open an absolute directory one no-follow component at a time."""
    if not hasattr(os, "O_NOFOLLOW"):
        raise ValueError("teacher-quality evidence requires O_NOFOLLOW")
    absolute = Path(os.path.abspath(os.fspath(path)))
    if not absolute.is_absolute():
        raise ValueError(f"{name} must be an absolute directory")
    descriptor = os.open(absolute.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for component in absolute.parts[1:]:
            try:
                before = os.stat(component, dir_fd=descriptor, follow_symlinks=False)
            except OSError as exc:
                raise ValueError(f"{name} cannot be inspected") from exc
            if stat.S_ISLNK(before.st_mode) or not stat.S_ISDIR(before.st_mode):
                raise ValueError(f"{name} contains a symlink or non-directory component")
            try:
                child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            except OSError as exc:
                raise ValueError(f"{name} cannot be opened without following a symlink") from exc
            try:
                if _directory_identity_v3(os.fstat(child)) != _directory_identity_v3(before):
                    raise ValueError(f"{name} changed while being opened")
            except BaseException:
                os.close(child)
                raise
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _open_or_create_directory_path_no_follow_v3(path: Path, *, name: str) -> int:
    """Create missing output components without ever traversing a symlink."""
    if not hasattr(os, "O_NOFOLLOW"):
        raise ValueError("teacher-quality evidence requires O_NOFOLLOW")
    absolute = Path(os.path.abspath(os.fspath(path)))
    if not absolute.is_absolute():
        raise ValueError(f"{name} must be an absolute directory")
    descriptor = os.open(absolute.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for component in absolute.parts[1:]:
            try:
                before = os.stat(component, dir_fd=descriptor, follow_symlinks=False)
            except FileNotFoundError:
                try:
                    os.mkdir(component, 0o700, dir_fd=descriptor)
                    before = os.stat(component, dir_fd=descriptor, follow_symlinks=False)
                except OSError as exc:
                    raise ValueError(f"{name} cannot create directory component") from exc
            except OSError as exc:
                raise ValueError(f"{name} cannot be inspected") from exc
            if stat.S_ISLNK(before.st_mode) or not stat.S_ISDIR(before.st_mode):
                raise ValueError(f"{name} contains a symlink or non-directory component")
            try:
                child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            except OSError as exc:
                raise ValueError(f"{name} cannot be opened without following a symlink") from exc
            try:
                if _directory_identity_v3(os.fstat(child)) != _directory_identity_v3(before):
                    raise ValueError(f"{name} changed while being opened")
            except BaseException:
                os.close(child)
                raise
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


@dataclass(slots=True)
class _OutputRootV3:
    """Pinned evidence-output directory; all campaign I/O is FD-relative."""

    path: Path
    descriptor: int
    identity: tuple[int, int, int]

    @classmethod
    def open(cls, path: Path) -> "_OutputRootV3":
        descriptor = _open_or_create_directory_path_no_follow_v3(path, name="evidence output root")
        try:
            actual = os.fstat(descriptor)
            if not stat.S_ISDIR(actual.st_mode):
                raise ValueError("evidence output root is not a directory")
            return cls(path=Path(path), descriptor=descriptor, identity=_directory_identity_v3(actual))
        except BaseException:
            os.close(descriptor)
            raise

    def close(self) -> None:
        os.close(self.descriptor)

    def assert_current(self) -> None:
        current = _open_directory_path_no_follow_v3(self.path, name="evidence output root")
        try:
            if _directory_identity_v3(os.fstat(current)) != self.identity:
                raise ValueError("evidence output root changed during collection")
        finally:
            os.close(current)
        if _directory_identity_v3(os.fstat(self.descriptor)) != self.identity:
            raise ValueError("pinned evidence output root changed during collection")

    def exists(self, name: str) -> bool:
        self.assert_current()
        try:
            item = os.stat(name, dir_fd=self.descriptor, follow_symlinks=False)
        except FileNotFoundError:
            return False
        if stat.S_ISLNK(item.st_mode):
            raise ValueError(f"evidence output entry {name} is a symlink")
        return True

    def read(self, name: str) -> bytes:
        self.assert_current()
        try:
            descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=self.descriptor)
        except FileNotFoundError:
            raise
        except OSError as exc:
            raise ValueError(f"evidence output entry {name} cannot be opened without following a symlink") from exc
        try:
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode):
                raise ValueError(f"evidence output entry {name} is not a regular file")
            chunks: list[bytes] = []
            while chunk := os.read(descriptor, 1024 * 1024):
                chunks.append(chunk)
            if (
                _directory_identity_v3(os.fstat(self.descriptor)) != self.identity
                or _regular_identity_v3(os.fstat(descriptor)) != _regular_identity_v3(before)
            ):
                raise ValueError(f"evidence output entry {name} changed while being read")
            return b"".join(chunks)
        finally:
            os.close(descriptor)

    def _write_destination_identity(self, name: str) -> tuple[int, int, int, int, int, int] | None:
        """Return the regular destination identity, rejecting every other leaf type."""
        try:
            item = os.lstat(name, dir_fd=self.descriptor)
        except FileNotFoundError:
            return None
        if not stat.S_ISREG(item.st_mode):
            raise ValueError(f"evidence output entry {name} is not a regular file")
        return _regular_identity_v3(item)

    def atomic_write(self, name: str, raw: bytes) -> None:
        if not name or "/" in name or "\\" in name:
            raise ValueError("evidence output entry name is invalid")
        self.assert_current()
        destination_before = self._write_destination_identity(name)
        temporary = f".{name}.{secrets.token_hex(16)}.tmp"
        try:
            descriptor = os.open(
                temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600, dir_fd=self.descriptor,
            )
            try:
                view = memoryview(raw)
                while view:
                    written = os.write(descriptor, view)
                    if written <= 0:
                        raise OSError("short evidence output write")
                    view = view[written:]
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            self.assert_current()
            if self._write_destination_identity(name) != destination_before:
                raise ValueError(f"evidence output entry {name} changed while being written")
            os.replace(temporary, name, src_dir_fd=self.descriptor, dst_dir_fd=self.descriptor)
            self.assert_current()
        finally:
            try:
                os.unlink(temporary, dir_fd=self.descriptor)
            except FileNotFoundError:
                pass


@dataclass(frozen=True, slots=True)
class LaneEvidenceInputV3:
    lane: str
    teacher_id: str
    teacher_revision: str
    subject_deck_path: str
    expected_subject_deck_sha256: str

    def __post_init__(self) -> None:
        for name in ("lane", "teacher_id", "teacher_revision", "subject_deck_path"):
            _require_string(getattr(self, name), name)
        _require_sha(self.expected_subject_deck_sha256, "expected_subject_deck_sha256")


@dataclass(frozen=True, slots=True)
class FrozenOpponentV3:
    opponent_id: str
    policy_sha256: str
    deck_sha256: str
    usage_boundary: str
    source: str
    _instance: OpponentInstanceV1 = field(repr=False, compare=False)

    def to_payload(self) -> dict[str, object]:
        return {
            "opponent_id": self.opponent_id,
            "policy_sha256": self.policy_sha256,
            "deck_sha256": self.deck_sha256,
            "usage_boundary": self.usage_boundary,
            "source": self.source,
        }


@dataclass(frozen=True, slots=True)
class LogicalGameV3:
    campaign_id: str
    profile: str
    logical_game_id: str
    lane: str
    arm: str
    policy: Mapping[str, object]
    subject_deck_path: str = field(repr=False, compare=False)
    subject_deck_sha256: str
    teacher_instance: OpponentInstanceV1 = field(repr=False, compare=False)
    opponent: FrozenOpponentV3
    engine_sha256: str
    source_commit: str
    source_commit_sha256: str
    seat: int
    repetition: int
    environment_seed: int
    agent_sampling_seed: int


@dataclass(frozen=True, slots=True)
class CampaignPlanV3:
    profile: str
    campaign_id: str
    lanes: tuple[LaneEvidenceInputV3, ...]
    panel: tuple[FrozenOpponentV3, ...]
    logical_games: tuple[LogicalGameV3, ...]
    schedule_path: str = field(repr=False, compare=False)
    schedule_sha256: str
    pool_root: str = field(repr=False, compare=False)
    pool_manifest_sha256: str
    engine_entry_point: str = field(repr=False, compare=False)
    engine_sha256: str
    baseline_entry_point: str = field(repr=False, compare=False)
    baseline_policy_sha256: str
    source_commit: str
    source_commit_sha256: str

    def to_payload(self) -> dict[str, object]:
        return {
            "schema": CAMPAIGN_SCHEMA_V3,
            "profile": self.profile,
            "campaign_id": self.campaign_id,
            "lanes": [
                {
                    "lane": item.lane,
                    "teacher_id": item.teacher_id,
                    "teacher_revision": item.teacher_revision,
                    "subject_deck_sha256": item.expected_subject_deck_sha256,
                }
                for item in self.lanes
            ],
            "panel": [item.to_payload() for item in self.panel],
            "source_closure": {
                "schedule_sha256": self.schedule_sha256,
                "pool_manifest_sha256": self.pool_manifest_sha256,
                "engine_sha256": self.engine_sha256,
                "baseline_policy_sha256": self.baseline_policy_sha256,
                "source_commit": self.source_commit,
                "source_commit_sha256": self.source_commit_sha256,
            },
            "matrix": {
                "arms": 2,
                "lanes": len(self.lanes),
                "opponents_per_lane": 3 if self.profile == "calibration" else 6,
                "seats": 2,
                "repetitions": 1 if self.profile == "calibration" else 8,
                "logical_games": len(self.logical_games),
                "max_retries": 1,
                "max_attempts": len(self.logical_games) * 2,
            },
            "independence": {
                "paired_claim": False,
                "engine_replay_attested": False,
                "seed_attestation": "unattested-until-live-runner-contract",
            },
            "hard_teacher_confidence": {"status": "unavailable"},
        }


@dataclass(frozen=True, slots=True)
class AttemptObservationV3:
    outcome: str | None
    fault: Mapping[str, object] | None
    elapsed_seconds: float

    def __post_init__(self) -> None:
        if type(self.elapsed_seconds) not in {int, float} or type(self.elapsed_seconds) is bool or not math.isfinite(float(self.elapsed_seconds)) or self.elapsed_seconds < 0:
            raise ValueError("elapsed_seconds must be finite and nonnegative")
        if (self.outcome is None) == (self.fault is None):
            raise ValueError("exactly one of outcome or fault is required")
        if self.outcome is not None and self.outcome not in _OUTCOMES:
            raise ValueError("outcome must be win, loss, or draw")
        if self.fault is not None and (type(self.fault) is not dict or frozenset(self.fault) != _FAULT_KEYS):
            raise ValueError("fault has an invalid closed key set")

    @classmethod
    def completed(cls, outcome: str, *, elapsed_seconds: float) -> "AttemptObservationV3":
        return cls(outcome=outcome, fault=None, elapsed_seconds=elapsed_seconds)

    @classmethod
    def faulted(
        cls, *, kind: str, exception_class: str, message: str,
        source_exception: str, exit_code: int | None, traceback_sha256: str,
        elapsed_seconds: float,
    ) -> "AttemptObservationV3":
        for name, value in (("kind", kind), ("exception_class", exception_class), ("message", message), ("source_exception", source_exception)):
            _require_string(value, name)
        if exit_code is not None and (type(exit_code) is not int or type(exit_code) is bool):
            raise ValueError("exit_code must be an integer or null")
        _require_sha(traceback_sha256, "traceback_sha256")
        return cls(
            outcome=None,
            fault={
                "kind": kind, "exception_class": exception_class, "message": message,
                "source_exception": source_exception, "exit_code": exit_code,
                "traceback_sha256": traceback_sha256,
            },
            elapsed_seconds=elapsed_seconds,
        )


def _read_schedule(path: Path, expected_sha256: str) -> dict[str, int]:
    raw = _read_verified_file(path, expected_sha256, name="opponent schedule")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("opponent schedule is not JSON") from exc
    if type(value) is not dict or not value:
        raise ValueError("opponent schedule must be a nonempty object")
    result: dict[str, int] = {}
    for key, weight in value.items():
        if type(key) is not str or not key or type(weight) is not int or type(weight) is bool or weight <= 0:
            raise ValueError("opponent schedule entries require nonempty ids and positive integer weights")
        result[key] = weight
    return result


def _frozen_opponent(instance: OpponentInstanceV1) -> FrozenOpponentV3:
    return FrozenOpponentV3(
        opponent_id=instance.opponent_id,
        policy_sha256=_require_sha(instance.policy_hash, f"{instance.opponent_id} policy hash"),
        deck_sha256=_file_sha(instance.deck_csv_path, name=f"{instance.opponent_id} deck"),
        usage_boundary=_require_string(instance.usage_boundary, "usage_boundary"),
        source=_require_string(instance.source, "source"),
        _instance=instance,
    )


def _seed(payload: Mapping[str, object]) -> int:
    # NumPy's portable process-global API accepts a 32-bit seed.  Keep the
    # campaign's three RNG namespaces identical rather than recording a value
    # that Python/Torch used but NumPy could not receive.
    return int(_sha(_canonical(payload))[:16], 16) & ((1 << 32) - 1)


def build_campaign_plan_v3(
    *, profile: str, lanes: Sequence[LaneEvidenceInputV3],
    schedule_path: str | Path, expected_schedule_sha256: str,
    pool_root: str | Path, expected_pool_manifest_sha256: str,
    engine_entry_point: str | Path, expected_engine_sha256: str,
    source_commit: str, expected_source_commit_sha256: str,
) -> CampaignPlanV3:
    """Freeze the entire result-independent campaign recipe before execution."""
    if profile not in _PROFILES:
        raise ValueError(f"profile must be one of {sorted(_PROFILES)}")
    if len(lanes) != 2 or len({item.lane for item in lanes}) != 2:
        raise ValueError("teacher-quality v3 requires exactly two distinct lanes")
    _require_string(source_commit, "source_commit")
    _require_sha(expected_source_commit_sha256, "expected_source_commit_sha256")
    if _sha(source_commit.encode()) != expected_source_commit_sha256:
        raise ValueError("external source commit SHA-256 does not match")
    _verify_file(engine_entry_point, expected_engine_sha256, name="engine entry point")
    schedule = _read_schedule(Path(schedule_path), expected_schedule_sha256)
    manifest_path = Path(pool_root) / "pool_manifest.json"
    _verify_file(manifest_path, expected_pool_manifest_sha256, name="opponent pool manifest")
    pool = load_opponent_pool_v1(pool_root)
    _verify_file(manifest_path, expected_pool_manifest_sha256, name="opponent pool manifest")

    lane_items = tuple(lanes)
    teacher_ids = {item.teacher_id for item in lane_items}
    teachers: dict[str, OpponentInstanceV1] = {}
    for lane in lane_items:
        instance = pool.get(lane.teacher_id)
        if instance is None:
            raise ValueError(f"lane teacher is not in the verified pool: {lane.teacher_id}")
        _verify_file(lane.subject_deck_path, lane.expected_subject_deck_sha256, name=f"{lane.lane} subject deck")
        teacher_deck_sha = _file_sha(instance.deck_csv_path, name=f"{lane.teacher_id} teacher deck")
        if teacher_deck_sha != lane.expected_subject_deck_sha256:
            raise ValueError(f"{lane.lane} subject deck bytes do not match its teacher deck")
        teachers[lane.lane] = instance

    eligible: list[tuple[str, FrozenOpponentV3]] = []
    for opponent_id in sorted(schedule):
        if opponent_id in teacher_ids:
            continue
        instance = pool.get(opponent_id)
        if instance is None:
            raise ValueError(f"scheduled opponent is absent from verified pool: {opponent_id}")
        frozen = _frozen_opponent(instance)
        score = _sha(_canonical({
            "rule": "teacher-quality-v3-panel-hash-v1",
            "schedule_sha256": expected_schedule_sha256,
            "pool_manifest_sha256": expected_pool_manifest_sha256,
            "opponent": frozen.to_payload(),
            "schedule_weight": schedule[opponent_id],
            "excluded_teacher_ids": sorted(teacher_ids),
        }))
        eligible.append((score, frozen))
    if len(eligible) < 6:
        raise ValueError("fewer than six eligible scheduled opponents remain")
    panel = tuple(item for _, item in sorted(eligible, key=lambda item: (item[0], item[1].opponent_id))[:6])

    baseline_path = Path(__file__).resolve().parents[3] / "main.py"
    if not baseline_path.is_file():
        raise ValueError("Rule v0 baseline entry point main.py is missing")
    baseline_sha = _file_sha(baseline_path, name="Rule v0 baseline entry point")
    campaign_body = {
        "schema": CAMPAIGN_SCHEMA_V3,
        "profile": profile,
        "lanes": [
            {"lane": item.lane, "teacher_id": item.teacher_id, "teacher_revision": item.teacher_revision, "subject_deck_sha256": item.expected_subject_deck_sha256}
            for item in lane_items
        ],
        "panel": [item.to_payload() for item in panel],
        "schedule_sha256": expected_schedule_sha256,
        "pool_manifest_sha256": expected_pool_manifest_sha256,
        "engine_sha256": expected_engine_sha256,
        "baseline_policy_sha256": baseline_sha,
        "source_commit": source_commit,
        "source_commit_sha256": expected_source_commit_sha256,
    }
    campaign_id = _sha(_canonical(campaign_body))
    used_panel = panel[:3] if profile == "calibration" else panel
    repetitions = range(1) if profile == "calibration" else range(8)
    games: list[LogicalGameV3] = []
    for lane in sorted(lane_items, key=lambda item: item.lane):
        teacher = teachers[lane.lane]
        for arm in _ARMS:
            policy = (
                {
                    "kind": "external-teacher", "policy_id": lane.teacher_id,
                    "revision": lane.teacher_revision,
                    "implementation_sha256": teacher.policy_hash,
                    "hard_confidence": "unavailable",
                }
                if arm == "teacher" else
                {
                    "kind": "rule-v0-baseline", "policy_id": "rule-v0",
                    "revision": baseline_sha[:16],
                    "implementation_sha256": baseline_sha,
                    "hard_confidence": "not-applicable",
                }
            )
            for opponent in used_panel:
                for seat in (0, 1):
                    for repetition in repetitions:
                        identity = {
                            "campaign_id": campaign_id, "lane": lane.lane, "arm": arm,
                            "opponent_id": opponent.opponent_id, "seat": seat,
                            "repetition": repetition,
                        }
                        logical_game_id = _sha(_canonical(identity))
                        games.append(LogicalGameV3(
                            campaign_id=campaign_id, profile=profile,
                            logical_game_id=logical_game_id, lane=lane.lane, arm=arm,
                            policy=policy, subject_deck_path=lane.subject_deck_path,
                            subject_deck_sha256=lane.expected_subject_deck_sha256,
                            teacher_instance=teacher, opponent=opponent,
                            engine_sha256=expected_engine_sha256,
                            source_commit=source_commit,
                            source_commit_sha256=expected_source_commit_sha256,
                            seat=seat, repetition=repetition,
                            environment_seed=_seed({**identity, "namespace": "environment"}),
                            agent_sampling_seed=_seed({**identity, "namespace": "agent-sampling"}),
                        ))
    plan = CampaignPlanV3(
        profile=profile, campaign_id=campaign_id, lanes=lane_items, panel=panel,
        logical_games=tuple(games), schedule_path=str(schedule_path),
        schedule_sha256=expected_schedule_sha256, pool_root=str(pool_root),
        pool_manifest_sha256=expected_pool_manifest_sha256,
        engine_entry_point=str(engine_entry_point), engine_sha256=expected_engine_sha256,
        baseline_entry_point=str(baseline_path), baseline_policy_sha256=baseline_sha,
        source_commit=source_commit, source_commit_sha256=expected_source_commit_sha256,
    )
    expected_count = 24 if profile == "calibration" else 384
    if len(plan.logical_games) != expected_count:
        raise AssertionError("campaign matrix construction produced an unexpected cardinality")
    return plan


def _attempt_base(game: LogicalGameV3, retry_index: int) -> dict[str, object]:
    return {
        "schema": ATTEMPT_SCHEMA_V3,
        "campaign_id": game.campaign_id,
        "profile": game.profile,
        "logical_game_id": game.logical_game_id,
        "attempt_id": _sha(_canonical({"logical_game_id": game.logical_game_id, "retry_index": retry_index})),
        "lane": game.lane,
        "arm": game.arm,
        "policy": dict(game.policy),
        "subject_deck": {"bytes_sha256": game.subject_deck_sha256},
        "engine": {"entry_point_sha256": game.engine_sha256},
        "source": {"commit": game.source_commit, "commit_sha256": game.source_commit_sha256},
        "opponent": game.opponent.to_payload(),
        "seat": game.seat,
        "repetition": game.repetition,
        "environment_seed": game.environment_seed,
        "agent_sampling_seed": game.agent_sampling_seed,
        "retry_index": retry_index,
    }


def _attempt_row(game: LogicalGameV3, retry_index: int, observation: AttemptObservationV3) -> dict[str, object]:
    return {
        **_attempt_base(game, retry_index),
        "outcome": observation.outcome,
        "fault": None if observation.fault is None else dict(observation.fault),
        "elapsed_seconds": float(observation.elapsed_seconds),
    }


def _strict_line(raw: bytes, line_number: int) -> dict[str, object]:
    if not raw.endswith(b"\n") or b"\r" in raw:
        raise ValueError(f"attempt ledger line {line_number} must end in exactly LF")
    try:
        row = json.loads(raw[:-1].decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"attempt ledger line {line_number} is not strict JSON") from exc
    if type(row) is not dict or frozenset(row) != _ATTEMPT_KEYS:
        raise ValueError(f"attempt ledger line {line_number} has an invalid closed key set")
    if _canonical(row) + b"\n" != raw:
        raise ValueError(f"attempt ledger line {line_number} is not canonical JSON")
    return row


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _validate_row(row: dict[str, object], game: LogicalGameV3, retry_index: int) -> None:
    base = _attempt_base(game, retry_index)
    for key, expected in base.items():
        if row[key] != expected:
            raise ValueError(f"attempt ledger identity drift at {game.logical_game_id}: {key}")
    elapsed = row["elapsed_seconds"]
    if type(elapsed) not in {int, float} or type(elapsed) is bool or not math.isfinite(float(elapsed)) or elapsed < 0:
        raise ValueError("attempt elapsed_seconds is invalid")
    outcome, fault = row["outcome"], row["fault"]
    if (outcome is None) == (fault is None):
        raise ValueError("attempt must have exactly one of outcome or fault")
    if outcome is not None and outcome not in _OUTCOMES:
        raise ValueError("attempt outcome is invalid")
    if fault is not None:
        if type(fault) is not dict or frozenset(fault) != _FAULT_KEYS:
            raise ValueError("attempt fault has an invalid closed key set")
        for name in ("kind", "exception_class", "message", "source_exception"):
            _require_string(fault[name], f"fault {name}")
        if fault["exit_code"] is not None and (type(fault["exit_code"]) is not int or type(fault["exit_code"]) is bool):
            raise ValueError("fault exit_code is invalid")
        _require_sha(fault["traceback_sha256"], "fault traceback_sha256")


def _parse_attempt_ledger_v3(raw: bytes, *, plan: CampaignPlanV3) -> list[dict[str, object]]:
    rows = [_strict_line(line, index) for index, line in enumerate(raw.splitlines(keepends=True), 1)]
    cursor = 0
    for game in plan.logical_games:
        if cursor == len(rows):
            break
        _validate_row(rows[cursor], game, 0)
        first = rows[cursor]
        cursor += 1
        if first["fault"] is not None and cursor < len(rows):
            _validate_row(rows[cursor], game, 1)
            cursor += 1
    if cursor != len(rows):
        raise ValueError("attempt ledger has duplicate, extra, or out-of-order rows")
    return rows


def read_attempt_ledger_v3(path: str | Path, *, plan: CampaignPlanV3) -> list[dict[str, object]]:
    source = Path(path)
    try:
        raw = _read_regular_file_no_follow(source, name="attempt ledger")
    except FileNotFoundError:
        return []
    return _parse_attempt_ledger_v3(raw, plan=plan)


def _require_production_subject_smoke(plan: CampaignPlanV3) -> None:
    """Require current, hash-pinned smoke evidence for both subject policies."""
    raw = _read_verified_file(
        Path(plan.pool_root) / "pool_manifest.json", plan.pool_manifest_sha256,
        name="opponent pool manifest",
    )
    try:
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError("opponent pool manifest is not strict JSON for smoke preflight") from exc
    rows = payload if type(payload) is list else payload.get("opponents") if type(payload) is dict else None
    if type(rows) is not list:
        raise ValueError("opponent pool manifest does not expose smoke_ok subject rows")
    by_id: dict[str, dict[str, object]] = {}
    for row in rows:
        if type(row) is not dict or type(row.get("id")) is not str or row["id"] in by_id:
            raise ValueError("opponent pool manifest has invalid subject smoke identity")
        by_id[row["id"]] = row
    for lane in plan.lanes:
        subject = by_id.get(lane.teacher_id)
        if subject is None or subject.get("smoke_ok") is not True:
            raise ValueError(
                f"production subject {lane.teacher_id!r} for lane {lane.lane!r} must have smoke_ok=true"
            )


def _snapshot_campaign_id_v3(snapshot: SourceSnapshotV3) -> str:
    verify_source_snapshot_v3(snapshot)
    raw = read_source_snapshot_entry_v3(snapshot, "source-manifest.json")
    try:
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError("source snapshot manifest is not strict JSON") from exc
    if type(payload) is not dict or type(payload.get("campaign_id")) is not str:
        raise ValueError("source snapshot manifest has no campaign identity")
    return payload["campaign_id"]


def _subject_policy_path_v3(game: LogicalGameV3, snapshot: SourceSnapshotV3) -> str:
    if game.arm == "teacher":
        component = _sha(game.teacher_instance.opponent_id.encode())[:16]
        path = f"inputs/teachers/{component}/policy.py"
        expected = game.teacher_instance.policy_hash
    else:
        # Rule v0's entry point resolves its sealed ``agents/`` package next
        # to the snapshot-root main.py.  The duplicate input copy is evidence
        # material only and must not become an import root without that closure.
        path = "main.py"
        expected = game.baseline_policy_sha256 if hasattr(game, "baseline_policy_sha256") else game.policy["implementation_sha256"]
    raw = read_source_snapshot_entry_v3(snapshot, path)
    if _sha(raw) != expected:
        raise ValueError("snapshot subject policy does not match frozen policy identity")
    return path


def _snapshot_engine_path_v3(game: LogicalGameV3, snapshot: SourceSnapshotV3) -> str:
    """Resolve the one sealed engine entry without retaining its host path."""
    raw = read_source_snapshot_entry_v3(snapshot, "source-manifest.json")
    try:
        manifest = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError("source snapshot manifest is not strict JSON for engine bridge") from exc
    entries = manifest.get("entries") if type(manifest) is dict else None
    if type(entries) is not list:
        raise ValueError("source snapshot manifest has no engine entries")
    matches = [
        row.get("path") for row in entries
        if type(row) is dict
        and type(row.get("path")) is str
        and str(row["path"]).startswith("inputs/engine/")
        and row.get("sha256") == game.engine_sha256
    ]
    if len(matches) != 1:
        raise ValueError("source snapshot has no unique frozen engine entry")
    engine_path = str(matches[0])
    read_source_snapshot_entry_v3(snapshot, engine_path)
    return engine_path


def _game_bridge_request_v3(
    game: LogicalGameV3, snapshot: SourceSnapshotV3, *, max_steps: int,
) -> dict[str, object]:
    subject_deck = f"inputs/lanes/{_sha(game.lane.encode())[:16]}/subject-deck.csv"
    opponent_component = _sha(game.opponent.opponent_id.encode())[:16]
    request = {
        "engine_path": _snapshot_engine_path_v3(game, snapshot),
        "opponent_policy_path": f"inputs/panel/{opponent_component}/policy.py",
        "subject_deck_path": subject_deck,
        "opponent_deck_path": f"inputs/panel/{opponent_component}/deck.csv",
        "environment_seed": game.environment_seed,
        "max_steps": max_steps,
    }
    # The worker request builder re-reads all paths via the snapshot FD.  These
    # reads make an absent/misaddressed subject or opponent a parent preflight
    # failure before a worker is spawned.
    for path in (
        request["opponent_policy_path"], request["subject_deck_path"], request["opponent_deck_path"],
    ):
        read_source_snapshot_entry_v3(snapshot, str(path))
    return request


def _snapshot_policy_import_paths_v3(
    plan: CampaignPlanV3, snapshot: SourceSnapshotV3,
) -> tuple[str, ...]:
    """Return the complete frozen policy import panel in stable order."""
    paths = ["main.py"]
    for lane in sorted(plan.lanes, key=lambda item: item.lane):
        game = next(
            item for item in plan.logical_games
            if item.lane == lane.lane and item.arm == "teacher"
        )
        paths.append(_subject_policy_path_v3(game, snapshot))
    for opponent in sorted(plan.panel, key=lambda item: item.opponent_id):
        component = _sha(opponent.opponent_id.encode())[:16]
        path = f"inputs/panel/{component}/policy.py"
        read_source_snapshot_entry_v3(snapshot, path)
        paths.append(path)
    if len(paths) != len(set(paths)):
        raise ValueError("frozen policy import panel has duplicate paths")
    return tuple(paths)


def _require_worker_provenance_v3(value: object) -> dict[str, object]:
    if type(value) is not dict or frozenset(value) != _WORKER_PROVENANCE_KEYS:
        raise ValueError("worker provenance has an invalid closed key set")
    if value["attempt_protocol"] != "fresh-worker-v3" or value["engine_seed_capability"] != "unattested":
        raise ValueError("worker provenance claims an unsupported execution capability")
    _require_sha(value["source_snapshot_file_sha256"], "source_snapshot_file_sha256")
    _require_sha(value["source_snapshot_tree_sha256"], "source_snapshot_tree_sha256")
    timeout = value["worker_timeout_seconds"]
    if type(timeout) not in {int, float} or type(timeout) is bool or not math.isfinite(float(timeout)) or timeout <= 0:
        raise ValueError("worker provenance timeout is invalid")
    return dict(value)


def build_live_attempt_runner_v3(
    *, plan: CampaignPlanV3, source_snapshot: SourceSnapshotV3 | None = None,
    transient_root: str | Path | None = None, max_steps: int = 10_000,
    worker_timeout_seconds: float = 30.0,
) -> Callable[[LogicalGameV3, int], AttemptObservationV3]:
    """Return a sealed-snapshot, one-fresh-worker-per-attempt runner.

    Each attempt supplies only sealed engine, subject, opponent, and deck
    entries to a fresh worker.  The engine's seed contract remains explicitly
    ``unattested`` even though the logical environment seed is transported.
    """
    if type(max_steps) is not int or type(max_steps) is bool or max_steps <= 0:
        raise ValueError("max_steps must be a positive integer")
    if type(worker_timeout_seconds) not in {int, float} or type(worker_timeout_seconds) is bool or worker_timeout_seconds <= 0:
        raise ValueError("worker_timeout_seconds must be positive")
    _require_production_subject_smoke(plan)
    owns_snapshot = source_snapshot is None
    if source_snapshot is None:
        snapshot_root = (
            Path(transient_root) if transient_root is not None
            else Path(plan.engine_entry_point).parent / ".teacher-quality-v3-snapshots"
        )
        snapshot = seal_teacher_quality_source_snapshot_v3(plan=plan, staging_root=snapshot_root)
    else:
        if type(source_snapshot) is not SourceSnapshotV3:
            raise ValueError("source_snapshot must be a SourceSnapshotV3")
        snapshot = source_snapshot
    try:
        if _snapshot_campaign_id_v3(snapshot) != plan.campaign_id:
            raise ValueError("source snapshot does not belong to the frozen campaign")
        validate_snapshot_policy_imports_v3(
            snapshot=snapshot, policy_paths=_snapshot_policy_import_paths_v3(plan, snapshot),
        )
    except BaseException:
        if owns_snapshot:
            snapshot.close()
        raise
    provenance = _require_worker_provenance_v3({
        "attempt_protocol": "fresh-worker-v3",
        "engine_seed_capability": "unattested",
        "source_snapshot_file_sha256": snapshot.file_sha256,
        "source_snapshot_tree_sha256": snapshot.tree_sha256,
        "worker_timeout_seconds": float(worker_timeout_seconds),
    })

    def runner(game: LogicalGameV3, retry_index: int) -> AttemptObservationV3:
        if game.campaign_id != plan.campaign_id or retry_index not in (0, 1):
            raise ValueError("live runner received a game outside its frozen campaign")
        policy_path = _subject_policy_path_v3(game, snapshot)
        request = build_teacher_quality_worker_request_v3(
            snapshot=snapshot, campaign_id=plan.campaign_id,
            logical_game_id=game.logical_game_id, retry_index=retry_index,
            subject_seat=game.seat, agent_sampling_seed=game.agent_sampling_seed,
            policy_path=policy_path,
            game=_game_bridge_request_v3(game, snapshot, max_steps=max_steps),
        )
        with tempfile.TemporaryDirectory(prefix=".teacher-quality-v3-worker-request-") as request_root:
            request_path = Path(request_root) / "request.json"
            _atomic_write(request_path, _canonical(request))
            response = run_teacher_quality_attempt_worker_v3(
                request_path, timeout_seconds=float(worker_timeout_seconds),
            )
        elapsed = response.get("elapsed_seconds")
        if type(elapsed) not in {int, float} or type(elapsed) is bool or not math.isfinite(float(elapsed)) or elapsed < 0:
            raise ValueError("fresh worker response elapsed_seconds is invalid")
        if response.get("outcome") in _OUTCOMES:
            bridge = response.get("game")
            if (
                type(bridge) is not dict
                or bridge.get("subject_seat") != game.seat
                or bridge.get("subject_outcome") != response["outcome"]
            ):
                raise ValueError("fresh worker response lacks matching bridge outcome")
            return AttemptObservationV3.completed(str(response["outcome"]), elapsed_seconds=float(elapsed))
        fault = response.get("fault")
        if type(fault) is not dict or frozenset(fault) != _FAULT_KEYS:
            raise ValueError("fresh worker response has no closed outcome or fault")
        return AttemptObservationV3.faulted(
            kind=str(fault["kind"]), exception_class=str(fault["exception_class"]),
            message=str(fault["message"]), source_exception=str(fault["source_exception"]),
            exit_code=fault["exit_code"] if type(fault["exit_code"]) is int else None,
            traceback_sha256=str(fault["traceback_sha256"]), elapsed_seconds=float(elapsed),
        )

    def close() -> None:
        if owns_snapshot:
            snapshot.close()

    setattr(runner, "worker_provenance_v3", provenance)
    setattr(runner, "close", close)
    return runner


class _ProgressV3:
    def __init__(self, total: int, stream: object) -> None:
        self.total = total
        self.stream = stream
        self.completed = 0
        self.started = time.monotonic()
        self.last_snapshot = self.started
        self.bar = None
        if bool(getattr(stream, "isatty", lambda: False)()):
            try:
                from tqdm import tqdm
                self.bar = tqdm(total=total, desc="teacher-quality-v3", unit="attempt", file=stream)
            except ImportError:
                self.bar = None

    def update(self, faults: int) -> None:
        self.completed += 1
        now = time.monotonic()
        if self.bar is not None:
            self.bar.set_postfix(faults=faults)
            self.bar.update(1)
        elif now - self.last_snapshot >= 10:
            elapsed = max(now - self.started, 1e-9)
            print(json.dumps({"stage": "collect", "completed": self.completed, "total": self.total, "attempts_per_second": self.completed / elapsed, "faults": faults}, sort_keys=True), file=self.stream)
            self.last_snapshot = now

    def close(self) -> None:
        if self.bar is not None:
            self.bar.close()


@contextmanager
def _output_lock_v3(destination: Path):
    """Hold a non-blocking per-output lock for the whole resume transaction."""
    output = _OutputRootV3.open(destination)
    try:
        descriptor = os.open(
            ".teacher-quality-evidence-v3.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,
            0o600, dir_fd=output.descriptor,
        )
    except OSError as exc:
        output.close()
        raise ValueError("cannot safely open evidence output lock") from exc
    try:
        import fcntl
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("teacher-quality evidence output is already in use") from exc
        output.assert_current()
        yield output
    finally:
        try:
            import fcntl
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)
            output.close()


def _final_rows(plan: CampaignPlanV3, rows: list[dict[str, object]]) -> list[tuple[LogicalGameV3, dict[str, object]]]:
    grouped: dict[str, list[dict[str, object]]] = {}
    for row in rows:
        grouped.setdefault(str(row["logical_game_id"]), []).append(row)
    result = []
    for game in plan.logical_games:
        attempts = grouped.get(game.logical_game_id, [])
        if not attempts or attempts[-1]["fault"] is not None:
            if not attempts or len(attempts) < 2:
                raise ValueError("attempt ledger is incomplete")
        result.append((game, attempts[-1]))
    return result


def _runtime(rows: list[dict[str, object]], plan: CampaignPlanV3) -> dict[str, object]:
    values = sorted(float(row["elapsed_seconds"]) for row in rows)
    p50 = statistics.median(values) if values else None
    p95 = values[max(0, math.ceil(0.95 * len(values)) - 1)] if values else None
    faults = sum(row["fault"] is not None for row in rows)
    retries = sum(row["retry_index"] == 1 for row in rows)
    return {
        "p50_attempt_seconds": p50,
        "p95_attempt_seconds": p95,
        "attempt_fault_rate": faults / len(rows) if rows else None,
        "retry_rate_per_logical_game": retries / len(plan.logical_games),
        "estimated_full_max_attempt_seconds": None if p95 is None else p95 * 768,
    }


def _lane_result(finals: list[tuple[LogicalGameV3, dict[str, object]]]) -> dict[str, object]:
    strata: dict[tuple[str, int], dict[str, list[dict[str, object]]]] = {}
    for game, row in finals:
        strata.setdefault((game.opponent.opponent_id, game.seat), {arm: [] for arm in _ARMS})[game.arm].append(row)
    if len(strata) != 12:
        raise ValueError("full evidence lane must contain exactly 12 opponent-by-seat strata")
    output_strata: dict[str, object] = {}
    teacher_samples = []
    delta_samples = []
    generator = random.Random(BOOTSTRAP_SEED_V3)
    observed_teacher = []
    observed_baseline = []
    stratum_values: list[tuple[list[int], list[int]]] = []
    for (opponent_id, seat), arms in sorted(strata.items()):
        if any(len(arms[arm]) != 8 for arm in _ARMS):
            raise ValueError("full evidence stratum must contain eight logical games per arm")
        arm_summary: dict[str, object] = {}
        values: dict[str, list[int]] = {}
        for arm in _ARMS:
            counts = Counter("fault" if row["fault"] is not None else row["outcome"] for row in arms[arm])
            values[arm] = [int(row["fault"] is None and row["outcome"] == "win") for row in arms[arm]]
            arm_summary[arm] = {
                "games": 8, "wins": counts["win"], "draws": counts["draw"],
                "losses": counts["loss"], "faults": counts["fault"],
                "win_rate": sum(values[arm]) / 8,
            }
        observed_teacher.append(sum(values["teacher"]) / 8)
        observed_baseline.append(sum(values["rule-v0-baseline"]) / 8)
        stratum_values.append((values["teacher"], values["rule-v0-baseline"]))
        output_strata[f"opponent={opponent_id}|seat={seat}"] = arm_summary
    for _ in range(BOOTSTRAP_REPLICATES_V3):
        teacher_rates = []
        baseline_rates = []
        for teacher_values, baseline_values in stratum_values:
            teacher_rates.append(sum(generator.choice(teacher_values) for _ in range(8)) / 8)
            baseline_rates.append(sum(generator.choice(baseline_values) for _ in range(8)) / 8)
        teacher_macro = sum(teacher_rates) / 12
        teacher_samples.append(teacher_macro)
        delta_samples.append(teacher_macro - sum(baseline_rates) / 12)
    teacher_samples.sort()
    delta_samples.sort()
    lower_index = math.ceil(0.05 * BOOTSTRAP_REPLICATES_V3) - 1
    return {
        "macro_delta": sum(observed_teacher) / 12 - sum(observed_baseline) / 12,
        "teacher_absolute_macro_win_rate": sum(observed_teacher) / 12,
        "teacher_absolute_one_sided_95_lower": teacher_samples[lower_index],
        "delta_one_sided_95_lower": delta_samples[lower_index],
        "strata": output_strata,
    }


def _result_payload(
    plan: CampaignPlanV3, rows: list[dict[str, object]], *,
    worker_provenance: Mapping[str, object] | None,
) -> dict[str, object]:
    finals = _final_rows(plan, rows)
    body: dict[str, object] = {
        "schema": RESULT_SCHEMA_V3,
        "status": "PERFORMANCE_EVIDENCE_ONLY" if plan.profile == "full" else "CALIBRATION_ONLY",
        "campaign_id": plan.campaign_id,
        "profile": plan.profile,
        "logical_games": len(finals),
        "attempts": len(rows),
        "bootstrap": {"seed": BOOTSTRAP_SEED_V3, "replicates": BOOTSTRAP_REPLICATES_V3},
        "runtime": _runtime(rows, plan),
        "hard_teacher_confidence": {"status": "unavailable"},
        "lanes": {},
    }
    if worker_provenance is not None:
        body["worker_provenance"] = _require_worker_provenance_v3(worker_provenance)
    if plan.profile == "full":
        by_lane = {lane.lane: [(game, row) for game, row in finals if game.lane == lane.lane] for lane in plan.lanes}
        body["lanes"] = {lane: _lane_result(items) for lane, items in sorted(by_lane.items())}
    body["result_sha256"] = _sha(_canonical(body))
    return body


def _runner_worker_provenance_v3(
    runner: Callable[[LogicalGameV3, int], AttemptObservationV3],
) -> dict[str, object] | None:
    value = getattr(runner, "worker_provenance_v3", None)
    return None if value is None else _require_worker_provenance_v3(value)


def _campaign_payload_v3(
    plan: CampaignPlanV3, worker_provenance: Mapping[str, object] | None,
) -> dict[str, object]:
    payload = plan.to_payload()
    if worker_provenance is not None:
        provenance = _require_worker_provenance_v3(worker_provenance)
        payload.update({
            "source_snapshot_file_sha256": provenance["source_snapshot_file_sha256"],
            "source_snapshot_tree_sha256": provenance["source_snapshot_tree_sha256"],
            "engine_seed_capability": provenance["engine_seed_capability"],
            "worker_provenance": provenance,
        })
    return payload


def _collect_teacher_quality_evidence_locked_v3(
    *, plan: CampaignPlanV3, output: _OutputRootV3,
    runner: Callable[[LogicalGameV3, int], AttemptObservationV3],
    progress_stream: object = sys.stderr,
) -> dict[str, object]:
    """Execute/resume a frozen serial campaign and atomically seal its evidence."""
    worker_provenance = _runner_worker_provenance_v3(runner)
    campaign_raw = _canonical(_campaign_payload_v3(plan, worker_provenance))
    if output.exists("campaign.json") and output.read("campaign.json") != campaign_raw:
        raise ValueError("existing output belongs to a different frozen campaign")
    if not output.exists("campaign.json"):
        output.atomic_write("campaign.json", campaign_raw)
    try:
        rows = _parse_attempt_ledger_v3(output.read("attempts.jsonl"), plan=plan)
    except FileNotFoundError:
        rows = []
    grouped: dict[str, list[dict[str, object]]] = {}
    for row in rows:
        grouped.setdefault(str(row["logical_game_id"]), []).append(row)
    progress = _ProgressV3(len(plan.logical_games) * 2, progress_stream)
    try:
        for game in plan.logical_games:
            attempts = grouped.setdefault(game.logical_game_id, [])
            if not attempts:
                observation = runner(game, 0)
                if type(observation) is not AttemptObservationV3:
                    raise ValueError("runner must return AttemptObservationV3")
                row = _attempt_row(game, 0, observation)
                attempts.append(row)
                rows.append(row)
                output.atomic_write("attempts.jsonl", b"".join(_canonical(item) + b"\n" for item in rows))
                progress.update(sum(item["fault"] is not None for item in rows))
            if attempts[-1]["fault"] is not None and len(attempts) == 1:
                observation = runner(game, 1)
                if type(observation) is not AttemptObservationV3:
                    raise ValueError("runner must return AttemptObservationV3")
                row = _attempt_row(game, 1, observation)
                attempts.append(row)
                rows.append(row)
                output.atomic_write("attempts.jsonl", b"".join(_canonical(item) + b"\n" for item in rows))
                progress.update(sum(item["fault"] is not None for item in rows))
    finally:
        progress.close()
    rows = _parse_attempt_ledger_v3(output.read("attempts.jsonl"), plan=plan)
    result = _result_payload(plan, rows, worker_provenance=worker_provenance)
    result_raw = _canonical(result)
    output.atomic_write("result.json", result_raw)
    runtime = _runtime(rows, plan)
    manifest: dict[str, object] = {
        "schema": MANIFEST_SCHEMA_V3,
        "campaign_id": plan.campaign_id,
        "profile": plan.profile,
        "campaign_file_sha256": _sha(campaign_raw),
        "ledger_sha256": _sha(output.read("attempts.jsonl")),
        "result_file_sha256": _sha(result_raw),
        "logical_games": len(plan.logical_games),
        "attempts": len(rows),
        "attempt_faults": sum(row["fault"] is not None for row in rows),
        "final_faults": sum(row["fault"] is not None for _, row in _final_rows(plan, rows)),
        "strata_complete": plan.profile == "full",
        "external_sha256": {
            "schedule": plan.schedule_sha256,
            "pool_manifest": plan.pool_manifest_sha256,
            "engine": plan.engine_sha256,
            "baseline_policy": plan.baseline_policy_sha256,
            "source_commit": plan.source_commit_sha256,
        },
        "runtime": runtime,
        "hard_teacher_confidence": {"status": "unavailable"},
    }
    if worker_provenance is not None:
        manifest.update({
            "source_snapshot_file_sha256": worker_provenance["source_snapshot_file_sha256"],
            "source_snapshot_tree_sha256": worker_provenance["source_snapshot_tree_sha256"],
            "engine_seed_capability": worker_provenance["engine_seed_capability"],
            "worker_provenance": worker_provenance,
        })
    manifest["manifest_sha256"] = _sha(_canonical(manifest))
    output.atomic_write("manifest.json", _canonical(manifest))
    return manifest


def collect_teacher_quality_evidence_v3(
    *, plan: CampaignPlanV3, output_dir: str | Path,
    runner: Callable[[LogicalGameV3, int], AttemptObservationV3],
    progress_stream: object = sys.stderr,
) -> dict[str, object]:
    """Execute/resume one campaign while excluding concurrent writers."""
    destination = Path(output_dir)
    with _output_lock_v3(destination) as output:
        return _collect_teacher_quality_evidence_locked_v3(
            plan=plan, output=output, runner=runner,
            progress_stream=progress_stream,
        )


def read_ready_teacher_quality_manifest_v3(
    path: str | Path, *, expected_manifest_file_sha256: str,
    expected_manifest_sha256: str,
) -> dict[str, object]:
    """Read only complete fresh-worker performance evidence, never calibration.

    This is an evidence-consumption guard, not a trust/weight or θ0 authority.
    Those decisions remain in the separate teacher-quality authority modules.
    """
    raw = _read_verified_file(path, expected_manifest_file_sha256, name="evidence manifest")
    try:
        manifest = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError("full performance evidence manifest is not strict JSON") from exc
    if type(manifest) is not dict or raw != _canonical(manifest):
        raise ValueError("full performance evidence manifest is not canonical")
    if manifest.get("schema") != MANIFEST_SCHEMA_V3 or manifest.get("manifest_sha256") != expected_manifest_sha256:
        raise ValueError("full performance evidence manifest identity is invalid")
    body = dict(manifest)
    supplied = body.pop("manifest_sha256", None)
    if type(supplied) is not str or _sha(_canonical(body)) != supplied:
        raise ValueError("full performance evidence manifest self hash is invalid")
    if (
        manifest.get("profile") != "full" or manifest.get("strata_complete") is not True
        or manifest.get("logical_games") != 384
    ):
        raise ValueError("full performance evidence is required; calibration is non-authoritative")
    provenance = manifest.get("worker_provenance")
    _require_worker_provenance_v3(provenance)
    if (
        manifest.get("source_snapshot_file_sha256") != provenance["source_snapshot_file_sha256"]
        or manifest.get("source_snapshot_tree_sha256") != provenance["source_snapshot_tree_sha256"]
        or manifest.get("engine_seed_capability") != provenance["engine_seed_capability"]
    ):
        raise ValueError("full performance evidence worker provenance is incomplete")
    return manifest


__all__ = [
    "AttemptObservationV3", "CampaignPlanV3", "FrozenOpponentV3",
    "LaneEvidenceInputV3", "LogicalGameV3", "build_campaign_plan_v3",
    "build_live_attempt_runner_v3", "collect_teacher_quality_evidence_v3",
    "read_attempt_ledger_v3", "read_ready_teacher_quality_manifest_v3", "SourceSnapshotV3",
    "read_source_snapshot_entry_v3",
    "seal_teacher_quality_source_snapshot_v3", "verify_source_snapshot_v3",
]
