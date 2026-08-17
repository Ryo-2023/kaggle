"""TDD coverage for the pinned C1v2 telemetry-audit closure."""

from __future__ import annotations

import builtins
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from typing import Callable

import pytest

from mage_ptcg.meta_specialist.actor_visible_features_v1 import CardVocabularyV1


def _fixture_row() -> dict[str, object]:
    """Small checked-in telemetry fixture; it intentionally has no outer step."""
    player = {
        "active": [], "asleep": False, "bench": [], "benchMax": 5,
        "burned": False, "confused": False, "deckCount": 60, "discard": [],
        "hand": [], "handCount": 0, "paralyzed": False, "poisoned": False,
        "prize": [],
    }
    opponent = {**player, "hand": None}
    return {
        "game_id": "fixture-game-id-must-not-reach-report",
        "selected_action": [0],
        "public_observation": {
            "current": {
                "energyAttached": False, "firstPlayer": -1, "looking": None,
                "players": [player, opponent], "result": -1, "retreated": False,
                "stadium": [], "stadiumPlayed": False, "supporterPlayed": False,
                "turn": 0, "turnActionCount": 1, "yourIndex": 0,
            },
            "select": {
                "context": 41, "contextCard": None, "deck": None, "effect": None,
                "maxCount": 1, "minCount": 1,
                "option": [{"type": 1}, {"type": 2}],
                "remainDamageCounter": 0, "remainEnergyCost": 0, "type": 9,
            },
        },
    }


def _fixture_vocabulary() -> CardVocabularyV1:
    """An explicitly supplied, already-validated local audit vocabulary."""
    return CardVocabularyV1(
        recognized_card_ids=frozenset(), source_sha256="1" * 64,
        environment_version="fixture", usage_decision="unqualified",
        test_only=False, permission_decision="unqualified",
    )


@pytest.mark.parametrize("overflow", ("1e9999", "-1e9999"))
def test_audit_rejects_nonfinite_exponent_overflow_in_ignored_nested_data(
    tmp_path: Path, overflow: str,
) -> None:
    """An ignored JSON member cannot bypass the finite-number input boundary."""
    from scripts.audit_actor_visible_v2_corpus import CorpusAuditError, audit_telemetry_corpus_v2

    base = json.dumps(_fixture_row(), separators=(",", ":"))
    raw = (base[:-1] + ',"ignored":{"nested":[' + overflow + ']}}' + "\n").encode()
    source = tmp_path / "overflow.jsonl"
    source.write_bytes(raw)

    with pytest.raises(CorpusAuditError) as raised:
        audit_telemetry_corpus_v2(
            source, expected_sha256=hashlib.sha256(raw).hexdigest(),
            expected_nonblank_records=1, enforce_pinned_hard_gates=False,
            vocabulary=_fixture_vocabulary(),
        )

    assert str(raised.value) == "telemetry line 1 is invalid"


@pytest.mark.parametrize("finite_number", ("1.25", "-1.25e3"))
def test_audit_keeps_accepting_finite_json_numbers_in_ignored_nested_data(
    tmp_path: Path, finite_number: str,
) -> None:
    """Finite decimal and exponent JSON syntax retains ordinary parse behavior."""
    from scripts.audit_actor_visible_v2_corpus import audit_telemetry_corpus_v2

    base = json.dumps(_fixture_row(), separators=(",", ":"))
    raw = (base[:-1] + ',"ignored":{"nested":[' + finite_number + ']}}' + "\n").encode()
    source = tmp_path / "finite.jsonl"
    source.write_bytes(raw)

    outcome = audit_telemetry_corpus_v2(
        source, expected_sha256=hashlib.sha256(raw).hexdigest(),
        expected_nonblank_records=1, enforce_pinned_hard_gates=False,
        vocabulary=_fixture_vocabulary(),
    )

    assert outcome.summary["local_records_valid"] == 1


def test_audit_cli_bootstraps_only_its_repo_local_src_from_an_arbitrary_cwd(
    tmp_path: Path,
) -> None:
    """Direct checkout CLI use requires neither the caller cwd nor PYTHONPATH."""
    repository_root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, str(repository_root / "scripts" / "audit_actor_visible_v2_corpus.py"), "--help"],
        cwd=tmp_path,
        env={"PATH": os.environ.get("PATH", os.defpath)},
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "--corpus" in result.stdout


def test_audit_cli_prefers_its_repo_src_over_a_poisoned_pythonpath(tmp_path: Path) -> None:
    """A preexisting repo src entry cannot leave an attacker package ahead of it."""
    repository_root = Path(__file__).resolve().parents[2]
    attacker = tmp_path / "attacker"
    attacker_package = attacker / "mage_ptcg"
    attacker_package.mkdir(parents=True)
    (attacker_package / "__init__.py").write_text("", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(repository_root / "scripts" / "audit_actor_visible_v2_corpus.py"), "--help"],
        cwd=tmp_path,
        env={
            "PATH": os.environ.get("PATH", os.defpath),
            "PYTHONPATH": os.pathsep.join((str(attacker), str(repository_root / "src"))),
        },
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "--card-data" in result.stdout


def _mutate_bytes(path: Path, replacement: bytes, *, attack: str) -> None:
    if attack == "same-size-in-place":
        assert path.stat().st_size == len(replacement)
        with builtins.open(path, "r+b") as handle:
            handle.write(replacement)
            handle.truncate()
    elif attack == "path-replace":
        alternate = path.with_name(f"{path.name}.replacement")
        with builtins.open(alternate, "wb") as handle:
            handle.write(replacement)
        os.replace(alternate, path)
    elif attack == "growth":
        with builtins.open(path, "ab") as handle:
            handle.write(replacement)
    elif attack == "truncation":
        with builtins.open(path, "r+b") as handle:
            handle.truncate(max(1, path.stat().st_size // 2))
    else:  # pragma: no cover - closed test parameter domain.
        raise AssertionError(attack)


def _install_mutation_after_first_read_close(
    monkeypatch: pytest.MonkeyPatch, *, target: Path, mutation: Callable[[], None],
) -> dict[str, object]:
    """Instrument the descriptor open/close without reopening the target path."""
    original_open = os.open
    original_fdopen = os.fdopen
    state: dict[str, object] = {"opens": 0, "mutated": False, "handles": []}
    target_descriptors: set[int] = set()

    class RacingHandle:
        def __init__(self, raw: object) -> None:
            self.raw = raw

        def __enter__(self) -> "RacingHandle":
            self.raw.__enter__()  # type: ignore[attr-defined]
            return self

        def __exit__(self, *args: object) -> object:
            try:
                return self.raw.__exit__(*args)  # type: ignore[attr-defined]
            finally:
                if not state["mutated"]:
                    state["mutated"] = True
                    mutation()

        def __iter__(self):
            return iter(self.raw)  # type: ignore[arg-type]

        def __getattr__(self, name: str) -> object:
            return getattr(self.raw, name)

    def racing_open(path: object, flags: int, mode: int = 0o777, *, dir_fd: int | None = None) -> int:
        descriptor = original_open(path, flags, mode, dir_fd=dir_fd)
        if Path(path) == target:  # type: ignore[arg-type]
            state["opens"] = int(state["opens"]) + 1
            target_descriptors.add(descriptor)
        return descriptor

    def racing_fdopen(descriptor: int, *args: object, **kwargs: object):
        raw = original_fdopen(descriptor, *args, **kwargs)
        if descriptor in target_descriptors:
            target_descriptors.remove(descriptor)
            state["handles"].append(raw)  # type: ignore[union-attr]
            return RacingHandle(raw)
        return raw

    monkeypatch.setattr(os, "open", racing_open)
    monkeypatch.setattr(os, "fdopen", racing_fdopen)
    return state


@pytest.mark.parametrize("source_kind", ("telemetry", "card-data"))
def test_audit_rejects_symlink_sources_even_when_the_target_has_the_expected_hash(
    tmp_path: Path, source_kind: str,
) -> None:
    """A valid target cannot lend its bytes/provenance through a symlink path."""
    import scripts.audit_actor_visible_v2_corpus as audit

    telemetry = tmp_path / "telemetry.jsonl"
    telemetry.write_text(json.dumps(_fixture_row(), separators=(",", ":")) + "\n", encoding="utf-8")
    telemetry_sha = hashlib.sha256(telemetry.read_bytes()).hexdigest()
    card_data = tmp_path / "EN_Card_Data.csv"
    card_data.write_text("Card ID,Card Name\n1,Known\n", encoding="utf-8")
    card_sha = hashlib.sha256(card_data.read_bytes()).hexdigest()
    link = tmp_path / "source-link"
    link.symlink_to(telemetry if source_kind == "telemetry" else card_data)

    with pytest.raises(audit.CorpusAuditError, match="symlink|no-follow|regular"):
        audit.audit_telemetry_corpus_v2(
            link if source_kind == "telemetry" else telemetry,
            expected_sha256=telemetry_sha, expected_nonblank_records=1,
            enforce_pinned_hard_gates=False,
            vocabulary=_fixture_vocabulary() if source_kind == "telemetry" else None,
            card_data_path=link if source_kind == "card-data" else None,
            expected_card_data_sha256=card_sha,
        )


def test_descriptor_open_rejects_non_regular_sources_without_blocking_or_leaking_fds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Devices, directories, FIFOs, and sockets fail before any blocking read."""
    import scripts.audit_actor_visible_v2_corpus as audit

    directory = tmp_path / "directory"
    directory.mkdir()
    fifo = tmp_path / "source.fifo"
    os.mkfifo(fifo)
    socket_path = tmp_path / "source.socket"
    socket_reader, socket_writer = socket.socketpair()
    opened: list[int] = []
    original_open = os.open

    def capturing_open(path: object, *args: object, **kwargs: object) -> int:
        descriptor = (
            os.dup(socket_reader.fileno()) if Path(path) == socket_path  # type: ignore[arg-type]
            else original_open(path, *args, **kwargs)  # type: ignore[arg-type]
        )
        opened.append(descriptor)
        return descriptor

    monkeypatch.setattr(os, "open", capturing_open)
    started = time.monotonic()
    try:
        for source in (Path("/dev/null"), directory, fifo, socket_path):
            with pytest.raises(audit.CorpusAuditError, match="regular|source"):
                audit._open_regular_source_fd_v2(
                    source, maximum_bytes=1024, source_name="test-source",
                )
    finally:
        socket_reader.close()
        socket_writer.close()
    assert time.monotonic() - started < 1.0
    for descriptor in opened:
        with pytest.raises(OSError):
            os.fstat(descriptor)


def test_descriptor_open_fails_closed_when_required_linux_flags_are_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No-follow/close-on-exec/nonblocking semantics are mandatory, not optional."""
    import scripts.audit_actor_visible_v2_corpus as audit

    source = tmp_path / "regular.bin"
    source.write_bytes(b"fixture")
    monkeypatch.delattr(os, "O_NOFOLLOW", raising=False)
    with pytest.raises(audit.CorpusAuditError, match="O_NOFOLLOW|unavailable"):
        audit._open_regular_source_fd_v2(
            source, maximum_bytes=len(b"fixture"), source_name="test-source",
        )


@pytest.mark.parametrize("source_kind", ("telemetry", "card-data"))
def test_path_replacement_immediately_after_descriptor_open_uses_the_open_regular_inode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source_kind: str,
) -> None:
    """Replacing the pathname after atomic open cannot redirect snapshot bytes."""
    import scripts.audit_actor_visible_v2_corpus as audit

    telemetry = tmp_path / "telemetry.jsonl"
    telemetry_bytes = (json.dumps(_fixture_row(), separators=(",", ":")) + "\n").encode()
    telemetry.write_bytes(telemetry_bytes)
    telemetry_sha = hashlib.sha256(telemetry_bytes).hexdigest()
    card_data = tmp_path / "EN_Card_Data.csv"
    card_bytes = b"Card ID,Card Name\n1,Known\n"
    card_data.write_bytes(card_bytes)
    card_sha = hashlib.sha256(card_bytes).hexdigest()
    target = telemetry if source_kind == "telemetry" else card_data
    alternate = target.with_name(f"{target.name}.new")
    alternate.write_bytes(b"not the pinned source")
    original_open = os.open
    opens = 0

    def replacing_open(path: object, flags: int, mode: int = 0o777, *, dir_fd: int | None = None) -> int:
        nonlocal opens
        descriptor = original_open(path, flags, mode, dir_fd=dir_fd)
        if Path(path) == target:  # type: ignore[arg-type]
            opens += 1
            os.replace(alternate, target)
        return descriptor

    monkeypatch.setattr(os, "open", replacing_open)
    outcome = audit.audit_telemetry_corpus_v2(
        telemetry, expected_sha256=telemetry_sha, expected_nonblank_records=1,
        enforce_pinned_hard_gates=False,
        vocabulary=_fixture_vocabulary() if source_kind == "telemetry" else None,
        card_data_path=card_data if source_kind == "card-data" else None,
        expected_card_data_sha256=card_sha,
    )

    assert opens == 1
    assert outcome.summary["local_records_valid"] == 1


def test_descriptor_snapshot_accepts_exact_size_and_rejects_one_byte_over(tmp_path: Path) -> None:
    """The fstat preflight and EOF sentinel agree on the exact byte boundary."""
    import scripts.audit_actor_visible_v2_corpus as audit

    source = tmp_path / "regular.bin"
    source.write_bytes(b"12345678")
    expected = hashlib.sha256(b"12345678").hexdigest()
    snapshot = audit._read_verified_source_snapshot_v2(
        source, expected_sha256=expected, maximum_bytes=8, source_name="test-source",
    )
    assert snapshot.raw_bytes == b"12345678"
    with pytest.raises(audit.CorpusAuditError, match="bound|limit|large"):
        audit._read_verified_source_snapshot_v2(
            source, expected_sha256=expected, maximum_bytes=7, source_name="test-source",
        )


@pytest.mark.parametrize("failure", ("normal", "reader", "fdopen", "enter", "exit"))
def test_snapshot_descriptor_ownership_closes_the_exact_fd_once_on_every_transition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    """FD ownership survives fdopen/enter/read/exit exceptions without leaks or double-close."""
    import scripts.audit_actor_visible_v2_corpus as audit

    source = tmp_path / "regular.bin"
    source.write_bytes(b"descriptor-ownership")
    expected = hashlib.sha256(source.read_bytes()).hexdigest()
    original_fdopen = os.fdopen
    captured_descriptors: list[int] = []
    wrappers: list[object] = []

    class OwnershipProbe:
        def __init__(self, raw: object) -> None:
            self.raw = raw
            self.close_calls = 0

        @property
        def closed(self) -> bool:
            return self.raw.closed  # type: ignore[no-any-return,union-attr]

        def close(self) -> None:
            self.close_calls += 1
            if self.close_calls != 1:
                raise AssertionError("source handle was closed more than once")
            self.raw.close()  # type: ignore[union-attr]

        def read(self, size: int = -1) -> bytes:
            if failure == "reader":
                raise RuntimeError("reader boom")
            return self.raw.read(size)  # type: ignore[no-any-return,union-attr]

        def __enter__(self) -> "OwnershipProbe":
            if failure == "enter":
                raise RuntimeError("enter boom")
            return self

        def __exit__(self, *_args: object) -> bool:
            if failure == "exit":
                raise RuntimeError("exit boom")
            self.close()
            return False

    def probing_fdopen(descriptor: int, *args: object, **kwargs: object):
        captured_descriptors.append(descriptor)
        if failure == "fdopen":
            raise RuntimeError("fdopen boom")
        wrapper = OwnershipProbe(original_fdopen(descriptor, *args, **kwargs))
        wrappers.append(wrapper)
        return wrapper

    monkeypatch.setattr(os, "fdopen", probing_fdopen)
    if failure == "normal":
        snapshot = audit._read_verified_source_snapshot_v2(
            source, expected_sha256=expected, maximum_bytes=1024, source_name="test-source",
        )
        assert snapshot.raw_bytes == b"descriptor-ownership"
    else:
        with pytest.raises(RuntimeError, match=f"{failure} boom"):
            audit._read_verified_source_snapshot_v2(
                source, expected_sha256=expected, maximum_bytes=1024, source_name="test-source",
            )

    assert len(captured_descriptors) == 1
    descriptor = captured_descriptors[0]
    assert not Path(f"/proc/self/fd/{descriptor}").exists()
    with pytest.raises(OSError):
        os.fstat(descriptor)
    if wrappers:
        assert wrappers[0].close_calls == 1  # type: ignore[union-attr]


def test_small_audit_injects_only_synthetic_step_and_maps_selection_to_local_action_ids(tmp_path: Path) -> None:
    """Fails if raw game IDs/outer indices leak or action indices bypass C1 bindings."""
    from scripts.audit_actor_visible_v2_corpus import audit_telemetry_corpus_v2

    source = tmp_path / "fixture.jsonl"
    source.write_text(json.dumps(_fixture_row(), separators=(",", ":")) + "\n", encoding="utf-8")
    source_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()

    outcome = audit_telemetry_corpus_v2(
        source, expected_sha256=source_sha256, expected_nonblank_records=1,
        enforce_pinned_hard_gates=False, vocabulary=_fixture_vocabulary(),
    )

    assert outcome.summary["records"] == 1
    assert outcome.summary["c1_valid"] == 1
    assert outcome.summary["local_records_valid"] == 1
    assert outcome.summary["default_training_examples"] == 0
    assert outcome.records[0]["source"] == {
        "kind": "pinned-telemetry-audit", "artifact_sha256": source_sha256,
        "synthetic": True, "synthetic_fields": ["step"], "training_eligible": False,
        "usage_class": "audit_only_unqualified", "permission_manifest_id": None,
    }
    assert outcome.records[0]["behavior"]["status"] == "action_only"
    assert outcome.records[0]["teacher"]["status"] == "unavailable"
    assert outcome.records[0]["student"]["status"] == "fallback"
    assert outcome.records[0]["selection"] == [outcome.records[0]["legal_actions"][0]["local_action_id"]]
    rendered = json.dumps(outcome.summary, ensure_ascii=False, sort_keys=True)
    assert "fixture-game-id" not in rendered
    assert "game_id" not in rendered


@pytest.mark.parametrize("attack", ("same-size-in-place", "path-replace", "growth", "truncation"))
def test_telemetry_hash_and_parse_share_one_bounded_immutable_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, attack: str,
) -> None:
    """Path/inode mutations after the one read cannot rebind pinned provenance."""
    from scripts.audit_actor_visible_v2_corpus import _episode_id_hash, audit_telemetry_corpus_v2

    original = _fixture_row()
    original["game_id"] = "fixture-game-a"
    replacement = _fixture_row()
    replacement["game_id"] = "fixture-game-b"
    original_bytes = (json.dumps(original, separators=(",", ":")) + "\n").encode()
    replacement_bytes = (json.dumps(replacement, separators=(",", ":")) + "\n").encode()
    assert len(original_bytes) == len(replacement_bytes)
    source = tmp_path / "telemetry.jsonl"
    source.write_bytes(original_bytes)
    source_sha256 = hashlib.sha256(original_bytes).hexdigest()
    attack_bytes = replacement_bytes if attack != "growth" else b"\n"
    state = _install_mutation_after_first_read_close(
        monkeypatch, target=source,
        mutation=lambda: _mutate_bytes(source, attack_bytes, attack=attack),
    )

    outcome = audit_telemetry_corpus_v2(
        source, expected_sha256=source_sha256, expected_nonblank_records=1,
        enforce_pinned_hard_gates=False, vocabulary=_fixture_vocabulary(),
    )

    assert state["opens"] == 1
    assert all(handle.closed for handle in state["handles"])  # type: ignore[union-attr]
    assert outcome.records[0]["episode_id_hash"] == _episode_id_hash(
        source_sha256=source_sha256, game_id="fixture-game-a",
    )


@pytest.mark.parametrize("attack", ("same-size-in-place", "path-replace", "growth", "truncation"))
def test_card_data_hash_and_parse_share_one_bounded_immutable_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, attack: str,
) -> None:
    """Vocabulary IDs and source SHA always derive from the same single read."""
    from scripts.audit_actor_visible_v2_corpus import audit_telemetry_corpus_v2

    card_bytes = b"Card ID,Card Name\n1,Known\n"
    replacement = b"Card ID,Card Name\n2,Other\n"
    assert len(card_bytes) == len(replacement)
    card_data = tmp_path / "EN_Card_Data.csv"
    card_data.write_bytes(card_bytes)
    card_data_sha256 = hashlib.sha256(card_bytes).hexdigest()
    attack_bytes = replacement if attack != "growth" else b"2,Other\n"
    state = _install_mutation_after_first_read_close(
        monkeypatch, target=card_data,
        mutation=lambda: _mutate_bytes(card_data, attack_bytes, attack=attack),
    )
    row = _fixture_row()
    row["public_observation"]["current"]["stadium"] = [  # type: ignore[index]
        {"id": 1, "serial": 1, "playerIndex": 0},
    ]
    source = tmp_path / "telemetry.jsonl"
    source.write_text(json.dumps(row, separators=(",", ":")) + "\n", encoding="utf-8")
    source_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()

    outcome = audit_telemetry_corpus_v2(
        source, expected_sha256=source_sha256, expected_nonblank_records=1,
        enforce_pinned_hard_gates=False, card_data_path=card_data,
        expected_card_data_sha256=card_data_sha256,
    )

    assert state["opens"] == 1
    assert all(handle.closed for handle in state["handles"])  # type: ignore[union-attr]
    assert outcome.summary["c1_valid"] == outcome.summary["local_records_valid"] == 1
    assert outcome.summary["card_vocabulary"] == {
        "source_sha256": card_data_sha256, "recognized_card_ids": 1,
    }


def test_telemetry_snapshot_has_a_fail_closed_byte_bound(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The one-shot source snapshot cannot grow without bound before SHA trust."""
    import scripts.audit_actor_visible_v2_corpus as audit

    source = tmp_path / "telemetry.jsonl"
    source.write_text(json.dumps(_fixture_row(), separators=(",", ":")) + "\n", encoding="utf-8")
    monkeypatch.setattr(audit, "MAX_TELEMETRY_SNAPSHOT_BYTES_V2", source.stat().st_size - 1, raising=False)
    with pytest.raises(audit.CorpusAuditError, match="bound|limit|large"):
        audit.audit_telemetry_corpus_v2(
            source, expected_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
            expected_nonblank_records=1, enforce_pinned_hard_gates=False,
            vocabulary=_fixture_vocabulary(),
        )


def test_card_data_snapshot_has_a_fail_closed_byte_bound(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The pinned vocabulary source has its own independent exact-read bound."""
    import scripts.audit_actor_visible_v2_corpus as audit

    card_data = tmp_path / "EN_Card_Data.csv"
    card_data.write_text("Card ID,Card Name\n1,Known\n", encoding="utf-8")
    source = tmp_path / "telemetry.jsonl"
    source.write_text(json.dumps(_fixture_row(), separators=(",", ":")) + "\n", encoding="utf-8")
    monkeypatch.setattr(audit, "MAX_CARD_DATA_SNAPSHOT_BYTES_V2", card_data.stat().st_size - 1, raising=False)
    with pytest.raises(audit.CorpusAuditError, match="bound|limit|large"):
        audit.audit_telemetry_corpus_v2(
            source, expected_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
            expected_nonblank_records=1, enforce_pinned_hard_gates=False,
            card_data_path=card_data,
            expected_card_data_sha256=hashlib.sha256(card_data.read_bytes()).hexdigest(),
        )


def test_audit_binds_the_card_vocabulary_to_the_exact_card_database_and_rejects_unknown_ids(
    tmp_path: Path,
) -> None:
    """A made-up card cannot silently become a known token from a numeric range."""
    from scripts.audit_actor_visible_v2_corpus import audit_telemetry_corpus_v2

    card_data = tmp_path / "EN_Card_Data.csv"
    card_data.write_text("Card ID,Card Name\n1,Known Card\n", encoding="utf-8")
    card_data_sha256 = hashlib.sha256(card_data.read_bytes()).hexdigest()
    row = _fixture_row()
    row["public_observation"]["current"]["stadium"] = [  # type: ignore[index]
        {"id": 2, "serial": 2, "playerIndex": 0},
    ]
    source = tmp_path / "unknown-card.jsonl"
    source.write_text(json.dumps(row, separators=(",", ":")) + "\n", encoding="utf-8")
    source_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()

    outcome = audit_telemetry_corpus_v2(
        source, expected_sha256=source_sha256, expected_nonblank_records=1,
        enforce_pinned_hard_gates=False, card_data_path=card_data,
        expected_card_data_sha256=card_data_sha256,
    )

    assert outcome.summary["records"] == 1
    assert outcome.summary["c1_valid"] == 1
    assert outcome.summary["local_records_valid"] == 0
    assert outcome.summary["validation_errors"] == {"local_record:CorpusAuditError": 1}
    assert outcome.summary["card_vocabulary"] == {
        "source_sha256": card_data_sha256, "recognized_card_ids": 1,
    }


def test_audit_checks_sha_before_parsing_any_telemetry(tmp_path: Path) -> None:
    """Fails if a substituted corpus can be parsed before its pinned SHA-256 gate."""
    from scripts.audit_actor_visible_v2_corpus import CorpusAuditError, audit_telemetry_corpus_v2

    source = tmp_path / "bad.jsonl"
    source.write_text("not JSON\n", encoding="utf-8")
    with pytest.raises(CorpusAuditError, match="SHA-256"):
        audit_telemetry_corpus_v2(
            source, expected_sha256="0" * 64, expected_nonblank_records=1,
            enforce_pinned_hard_gates=False, vocabulary=_fixture_vocabulary(),
        )


def test_local_record_failure_does_not_decrement_the_c1_boundary_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """C1 and Task-5 conversion evidence remain independently attributable."""
    import scripts.audit_actor_visible_v2_corpus as audit
    from mage_ptcg.meta_specialist.local_dataset_v2 import LocalDatasetV2Error

    source = tmp_path / "telemetry.jsonl"
    source.write_text(json.dumps(_fixture_row(), separators=(",", ":")) + "\n", encoding="utf-8")

    def fail_local_record(**_kwargs: object) -> dict[str, object]:
        raise LocalDatasetV2Error("forced local-record failure")

    monkeypatch.setattr(audit, "build_local_record_v2", fail_local_record)
    outcome = audit.audit_telemetry_corpus_v2(
        source, expected_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        expected_nonblank_records=1, enforce_pinned_hard_gates=False,
        vocabulary=_fixture_vocabulary(),
    )

    assert outcome.summary["c1_valid"] == 1
    assert outcome.summary["local_records_valid"] == 0
    assert outcome.summary["validation_errors"] == {
        "local_record:LocalDatasetV2Error": 1,
    }


def test_audit_outcome_returns_fresh_deep_copies_of_hard_gated_evidence(tmp_path: Path) -> None:
    """Caller mutation cannot rewrite the outcome's sealed internal evidence."""
    from scripts.audit_actor_visible_v2_corpus import audit_telemetry_corpus_v2

    source = tmp_path / "telemetry.jsonl"
    source.write_text(json.dumps(_fixture_row(), separators=(",", ":")) + "\n", encoding="utf-8")
    outcome = audit_telemetry_corpus_v2(
        source, expected_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        expected_nonblank_records=1, enforce_pinned_hard_gates=False,
        vocabulary=_fixture_vocabulary(),
    )
    summary = outcome.summary
    records = outcome.records
    summary["records"] = 999
    summary["card_vocabulary"]["recognized_card_ids"] = 999  # type: ignore[index]
    records[0]["source"]["artifact_sha256"] = "f" * 64  # type: ignore[index]

    assert outcome.summary["records"] == 1
    assert outcome.summary["card_vocabulary"]["recognized_card_ids"] == 0  # type: ignore[index]
    assert outcome.records[0]["source"]["artifact_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()  # type: ignore[index]
    assert outcome.summary is not summary
    assert outcome.records is not records


_PINNED_CORPUS = Path(
    "/home/bfe-lab-ono/kaggle/handoff-artifacts/"
    "family-agent-activation-remediation-v1/artifacts/turn_telemetry.jsonl"
)


@pytest.mark.skipif(not _PINNED_CORPUS.is_file(), reason="external pinned telemetry is not present")
def test_external_pinned_936_corpus_hard_gate() -> None:
    """Explicit external gate: exact collision/shape/tail counts and zero training rows."""
    from scripts.audit_actor_visible_v2_corpus import (
        PINNED_TELEMETRY_SHA256_V2,
        audit_telemetry_corpus_v2,
    )

    outcome = audit_telemetry_corpus_v2(_PINNED_CORPUS, expected_sha256=PINNED_TELEMETRY_SHA256_V2)
    assert outcome.summary["records"] == 936
    assert outcome.summary["public_identity"] == {
        "representable": 339, "duplicate-public-identity": 597,
    }
    assert outcome.summary["validation_errors"] == {}
    assert outcome.summary["default_training_examples"] == 0
    assert outcome.summary["runtime_tail_valid"] == {"61": 1, "64": 1, "67": 1}
    assert outcome.summary["max_visible_card_collection"] <= 60
    assert outcome.summary["card_vocabulary"] == {
        "source_sha256": "a0ea63cf7adcb65d35436ce0eb390de6e2e35654a7c67c065a45f4abaa00f373",
        "recognized_card_ids": 1267,
    }
